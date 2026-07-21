"""Public API: the one-shot ``translate()`` and the ``Translator`` class.

    import veriloqua
    print(veriloqua.translate("Hello", to="es"))            # fast, keyless
    tr = veriloqua.Translator()
    r = tr.translate("The spirit is willing", to="ru", mode="auto")
    tr.correct(r.request_id, "лучший перевод")               # learns the correction
"""

from __future__ import annotations

import dataclasses
from typing import Any

from veriloqua.backends.base import LLMBackend, SearchBackend, TranslationBackend
from veriloqua.config import Config, load_config
from veriloqua.engine import pipeline as pl
from veriloqua.engine.budget import BudgetTracker
from veriloqua.errors import BackendNotConfigured, BudgetExhausted
from veriloqua.lang.register import RegisterProfile
from veriloqua.memory import learn
from veriloqua.memory.records import MemoryEntry, RequestLogRecord, Scope
from veriloqua.memory.scrub import scrub_text, source_hash
from veriloqua.memory.store_sqlite import SqliteMemoryStore
from veriloqua.modes import Mode, policy_for
from veriloqua.result import FastResult, Status, TranslationResult


class Translator:
    def __init__(
        self,
        *,
        config: Config | None = None,
        mt_backend: TranslationBackend | None = None,
        llm_backend: LLMBackend | None = None,
        judge_backend: LLMBackend | None = None,
        search_backend: SearchBackend | None = None,
        store: Any | None = None,
        register: RegisterProfile | None = None,
        **overrides: Any,
    ) -> None:
        self.config = config or load_config(overrides)
        self.config.ensure_dirs()
        self.register = register or RegisterProfile()
        self._store = store if store is not None else SqliteMemoryStore(
            self.config.db_path,
            request_log_max=self.config.request_log_max,
            request_log_days=self.config.request_log_days,
        )
        self._explicit_mt = mt_backend
        self._explicit_llm = llm_backend
        self._explicit_judge = judge_backend
        self._search = search_backend

    # ------------------------------------------------------------ backends
    def _mt(self) -> TranslationBackend | None:
        if self._explicit_mt is not None:
            return self._explicit_mt
        if self.config.no_third_party:
            return None
        from veriloqua.backends.google_free import GoogleFreeBackend

        return GoogleFreeBackend(
            allow_third_party=self.config.allow_third_party,
            no_third_party=self.config.no_third_party,
        )

    def _llm(self) -> LLMBackend | None:
        """Resolve an LLM backend with ZERO config where possible.

        Order: explicit backend → explicitly-selected provider → auto-detect a local
        agent CLI (`claude`/`codex`, no API key) → API-key SDK backend → None.
        """
        if self._explicit_llm is not None:
            return self._explicit_llm

        provider = self.config.llm_provider
        if provider in ("claude_cli", "codex_cli"):
            from veriloqua.backends.llm_cli import CliLLMBackend

            try:
                return CliLLMBackend(provider, model=self.config.cli_model)
            except BackendNotConfigured:
                return None
        if provider in ("anthropic", "openai"):
            return self._sdk_llm(provider)

        # auto-detect (zero config): a logged-in agent CLI beats needing an API key.
        from veriloqua.backends.llm_cli import CliLLMBackend

        cli = CliLLMBackend.detect(model=self.config.cli_model)
        if cli is not None:
            return cli
        return self._sdk_llm(None)

    def _sdk_llm(self, provider: str | None) -> LLMBackend | None:
        key = self.config.resolved_api_key()
        if provider is None:
            from veriloqua.config import _detect_provider

            provider = _detect_provider()
        if not (provider and key):
            return None
        try:
            if provider == "anthropic":
                from veriloqua.backends.llm_anthropic import AnthropicBackend

                return AnthropicBackend(api_key=key, model=self.config.sdk_model)
            if provider == "openai":
                from veriloqua.backends.llm_openai import OpenAIBackend

                return OpenAIBackend(api_key=key, model=self.config.sdk_model)
        except BackendNotConfigured:
            return None
        return None

    def _context(self, mode: Mode, domain: str, llm: LLMBackend | None) -> pl.PipelineContext:
        return pl.PipelineContext(
            config=self.config,
            mt=self._mt(),
            llm=llm,
            triage_model=self.config.triage_model,
            translate_model=self.config.translate_model,
            deep_model=self.config.deep_model,
            store=self._store,
            search=self._search,
            register=self.register,
        )

    # ------------------------------------------------------------ translate
    def translate(
        self,
        text: str,
        *,
        to: str,
        source: str = "auto",
        mode: str | Mode = "auto",
        domain: str = "",
        register: str | None = None,
        context: str | None = None,
    ) -> TranslationResult | FastResult:
        if not isinstance(mode, Mode):
            try:
                mode = Mode(mode)
            except ValueError:
                raise ValueError(
                    f"unknown mode {mode!r}: valid modes are 'fast' and 'auto'"
                ) from None
        if register is not None:
            self.register = RegisterProfile.parse(register)
        # Domain/app brief: an explicit string wins; otherwise the `domain` names a pack file.
        context_brief = context if context is not None else self.config.domain_context(domain)

        llm = self._llm()
        if mode is Mode.AUTO and llm is None:
            mode = Mode.FAST  # graceful: no LLM → keyless fast path

        ctx = self._context(mode, domain, llm)
        policy = policy_for(mode)
        budget = dataclasses.replace(
            policy.budget,
            job_max_calls=self.config.max_calls,
            job_max_cost_usd=self.config.max_cost_usd,
        )
        tracker = BudgetTracker(budget=budget)
        tracker.start_segment()
        rid = pl.new_request_id()

        try:
            result = pl.run_segment(ctx, text=text, src=source, tgt=to, domain=domain,
                                    mode=mode, tracker=tracker, seg_idx=0, request_id=rid,
                                    context_brief=context_brief)
        except BudgetExhausted as exc:
            if ctx.mt is not None:
                result = self._fast_fallback(ctx, text, source, to, rid,
                                             f"budget exhausted ({exc})")
            else:
                raise
        except BackendNotConfigured as exc:
            # zero-config resilience: if the agent CLI isn't usable and we were in
            # auto mode, degrade to the keyless fast path instead of erroring.
            if mode is Mode.AUTO and ctx.mt is not None:
                result = self._fast_fallback(ctx, text, source, to, rid,
                                             f"LLM unavailable ({exc})")
            else:
                raise

        self._log_request(result, text=text, src=source, tgt=to, mode=mode, domain=domain)
        return result

    # ------------------------------------------------------------ learning
    def correct(
        self,
        request_id: str,
        corrected: str,
        *,
        note: str = "",
        scope: str | Scope = Scope.USER,
    ) -> MemoryEntry:
        rec = self._store.load_request(request_id)
        if rec is None:
            raise ValueError(
                f"no logged request '{request_id}' (it may have aged out of the ring buffer, "
                "or request logging is disabled). Use correct_text() with the original text."
            )
        scope = Scope(scope) if not isinstance(scope, Scope) else scope
        return learn.ingest_correction(
            self._store, source_text=rec.source_text, our_output=rec.output_text,
            corrected=corrected, src_lang=rec.src_lang, tgt_lang=rec.tgt_lang,
            domain=rec.domain, register=rec.register, scope=scope, note=note,
            scope_id=self._scope_id_for(scope), replay_dir=self.config.replay_dir,
        )

    def correct_text(
        self,
        *,
        source: str,
        our_output: str,
        corrected: str,
        src: str,
        tgt: str,
        domain: str = "",
        register: str = "",
        note: str = "",
        scope: str | Scope = Scope.USER,
    ) -> MemoryEntry:
        scope = Scope(scope) if not isinstance(scope, Scope) else scope
        return learn.ingest_correction(
            self._store, source_text=source, our_output=our_output, corrected=corrected,
            src_lang=src, tgt_lang=tgt, domain=domain, register=register, scope=scope,
            note=note, scope_id=self._scope_id_for(scope), replay_dir=self.config.replay_dir,
        )

    def add_term(self, *, source: str, target: str, src: str, tgt: str,
                 invariant: bool = False, domain: str = "", register: str = "",
                 scope: str | Scope = Scope.USER) -> MemoryEntry:
        scope = Scope(scope) if not isinstance(scope, Scope) else scope
        return learn.add_term_lock(
            self._store, src_lang=src, tgt_lang=tgt, source_text=source, target=target,
            invariant=invariant, domain=domain, register=register, scope=scope,
            scope_id=self._scope_id_for(scope),
        )

    def _scope_id_for(self, scope: Scope) -> str:
        """The real identity a scoped write is stamped with: user rows carry the user
        id, project rows the project id, global rows none."""
        if scope is Scope.USER:
            return self.config.user_id
        if scope is Scope.PROJECT:
            return self.config.project_id
        return ""

    def lock(self, entry_id: int, *, scope: str = "global") -> bool:
        return self._store.promote_scope(entry_id, scope)

    def forget(self, entry_id: int) -> bool:
        return self._store.soft_delete(entry_id)

    def memory_stats(self) -> dict:
        return self._store.stats()

    def conflicts(self) -> list[dict]:
        return self._store.conflicts()

    def purge_log(self) -> int:
        return self._store.purge_log()

    def close(self) -> None:
        self._store.close()

    # ------------------------------------------------------------ internal
    def _fast_fallback(self, ctx: pl.PipelineContext, text: str, source: str, to: str,
                       rid: str, reason: str) -> FastResult:
        fr = pl.fast_run(ctx, text=text, src=source, tgt=to,
                         tracker=BudgetTracker(policy_for(Mode.FAST).budget),
                         seg_idx=0, request_id=rid)
        fr.warnings.append(f"{reason}; returned keyless fast fallback")
        if fr.status == Status.OK:
            fr.status = Status.DEGRADED
        return fr

    def _log_request(self, result: Any, *, text: str, src: str, tgt: str, mode: Mode,
                     domain: str) -> None:
        if not self.config.request_log_enabled:
            return
        register = self.register.register.value if self.register else ""
        detected = getattr(result, "detected_src", src) or src
        # the ring buffer stores SCRUBBED text: obviously-sensitive tokens (emails,
        # keys, long digit runs) never persist; the hash stays over the original so
        # correct-by-text can still find the request
        rec = RequestLogRecord(
            request_id=result.request_id, src_lang=detected, tgt_lang=tgt,
            source_text=scrub_text(text), output_text=scrub_text(result.text),
            mode=mode.value, domain=domain, register=register, source_hash=source_hash(text),
        )
        try:
            self._store.log_request(rec)
        except Exception:
            pass


def translate(text: str, *, to: str, source: str = "auto", mode: str | Mode = "auto",
              domain: str = "", register: str | None = None, context: str | None = None,
              **overrides: Any) -> TranslationResult | FastResult:
    """One-shot translation. Defaults to ``auto`` (zero config): uses a locally
    logged-in agent CLI (`claude`/`codex`) when present, otherwise the keyless Google
    fast path. Force the keyless path with ``mode='fast'``."""
    tr = Translator(**overrides)
    try:
        return tr.translate(text, to=to, source=source, mode=mode, domain=domain,
                            register=register, context=context)
    finally:
        tr.close()
