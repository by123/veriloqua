"""Mode orchestration.

Two modes only:

* ``fast`` (``fast_run``) — keyless Google, no LLM, read-only term locks.
* ``auto`` (``auto_run``) — a tiered LLM cascade:

      text
       ↓  tier 0  : trivial short input (greeting-sized, no negation/memory/brief)?
       ↓            → keyless MT answers in <1s, zero LLM calls; any doubt falls through
       ↓  Haiku   : detect language, classify domain, flag slang/ambiguity/culture,
       ↓            judge complexity (and translate genuinely simple sentences directly)
       ↓  Sonnet  : translate + a first self-review          ← handles ~80-90%
       ↓  low confidence / ambiguity / cultural / dialect / meme / landmine?
       ├─ no  → output
       └─ yes → web search (if configured) + Opus deep judgment → write to knowledge base

Everything ends at the deterministic reject-guard, which is the hard backstop for the
never-repeat guarantee. ``medium``/``high`` are kept only as aliases that route to ``auto``.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from veriloqua import progress
from veriloqua._norm import normalize
from veriloqua.backends.base import LLMBackend, SearchBackend, TranslationBackend
from veriloqua.config import Config
from veriloqua.engine._parse import parse_json_object
from veriloqua.engine.budget import BudgetTracker
from veriloqua.engine.prompt_assembly import (
    build_crosscheck,
    build_deep,
    build_translate_review,
    build_triage,
)
from veriloqua.engine.validators import dominant_script, validate_fast
from veriloqua.errors import BackendNotConfigured, BudgetExhausted
from veriloqua.lang.placeholders import extract_placeholders, restore_placeholders
from veriloqua.lang.register import RegisterProfile
from veriloqua.memory import guard, inject, retrieval
from veriloqua.memory.records import MemoryEntry, Provenance, Scope
from veriloqua.modes import Mode
from veriloqua.result import FastResult, MemoryHit, Status, TranslationResult

_SCOPES = [Scope.USER.value, Scope.PROJECT.value, Scope.GLOBAL.value]
AUTO_FLOOR = 0.6                 # below this, Sonnet's result escalates to the Opus deep pass
SRC_CONF_FLOOR = 0.5             # below this, the detected source language is treated as uncertain


@dataclass(slots=True)
class PipelineContext:
    config: Config
    mt: TranslationBackend | None = None
    llm: LLMBackend | None = None
    triage_model: str = ""
    translate_model: str = ""
    deep_model: str = ""
    store: object | None = None                 # MemoryStore | None
    search: SearchBackend | None = None
    register: RegisterProfile = field(default_factory=RegisterProfile)


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


# Meaning-flip landmines that the weak triage model must NOT be trusted to resolve in its
# one-shot "simple sentence" translation — these force the fuller translator pass instead.
_NEG_WORDS = re.compile(
    r"\bno\b|\bnot\b|n't|\bnever\b|\bnone\b|\bnon\b|\bne\b|\bpas\b|\bnada\b|\bnunca\b|"
    r"\bn[aã]o\b|\bnem\b|\bnicht\b|\bkeine?\b|\bnein\b|\bniet\b|\bgeen\b|\bне\b|\bнет\b|\bни\b",
    re.IGNORECASE,
)
_NEG_CHARS = ("不", "沒", "没", "無", "无", "別", "别", "勿", "非", "未", "ない", "ません", "لا", "ما")


def _has_negation(text: str) -> bool:
    """True if the source carries a negator (across several languages). Negation is the
    classic mood/scope flip (statement vs. command); over-triggering only costs latency."""
    return bool(_NEG_WORDS.search(text)) or any(c in text for c in _NEG_CHARS)


_TRIVIAL_EDGE = " \t\r\n。．.!！?？,，、;；:：~～"
_TRIVIAL_BREAKS = "。.!?！？;；,，:："


def _mt_trivial(text: str) -> bool:
    """Deterministic gate for the tier-0 MT short-circuit: greeting-sized text where plain
    MT is as good as the cascade ("你好", "thank you"). Conservative on purpose — negation,
    internal clause breaks, or anything sentence-shaped falls through to triage.
    Over-rejecting only costs latency; over-accepting ships an unreviewed literal MT line."""
    core = text.strip(_TRIVIAL_EDGE)
    if not core or "\n" in text:
        return False
    if _has_negation(core):
        return False
    if any(ch in core for ch in _TRIVIAL_BREAKS):
        return False
    if dominant_script(core) == "cjk":
        return len(core) <= 6
    return len(core) <= 16 and len(core.split()) <= 2


def _wrong_script_for_target(candidate: str, tgt: str, source: str) -> bool:
    """Cheap guard on the weak-model short-circuit: if the target is a CJK language but the
    quick translation has no CJK characters — and isn't just the source kept verbatim (a
    proper noun/code legitimately stays Latin) — it was translated to the wrong language."""
    if not any(tgt.lower().startswith(p) for p in ("zh", "ja", "ko", "yue", "cmn")):
        return False
    if candidate.strip() == source.strip():
        return False
    return not any(
        "㐀" <= ch <= "鿿" or "぀" <= ch <= "ヿ" or "가" <= ch <= "힣"
        for ch in candidate
    )


# ------------------------------------------------------------------- helpers
def _load_entries(ctx: PipelineContext, src: str, tgt: str) -> list[MemoryEntry]:
    if ctx.store is None:
        return []
    return ctx.store.active_entries(src, tgt, _SCOPES)  # type: ignore[union-attr]


def _mask_invariant_locks(text: str, locks: list[MemoryEntry]) -> tuple[str, dict[str, str]]:
    """Protect invariant term locks by masking source spans to sentinels that restore
    to the *target* form — morphologically safe and guarantees the locked term."""
    restore: dict[str, str] = {}
    work = text
    k = 0
    for e in sorted(locks, key=lambda e: len(e.source_text), reverse=True):
        pattern = re.escape(e.source_text)
        if e.source_text[:1].isascii() and e.source_text.strip().replace(" ", "").isalnum():
            pattern = rf"\b{pattern}\b"
        token = f"VQLK{k}Z"
        new_work, n = re.subn(pattern, token, work, flags=re.IGNORECASE)
        if n:
            work = new_work
            restore[token] = e.accepted_translation
            k += 1
    return work, restore


def _hits(locks: list[MemoryEntry], corrections: list[MemoryEntry],
          surfaced: list, applied: bool) -> list[MemoryHit]:
    out: list[MemoryHit] = []
    for e in locks:
        out.append(MemoryHit(entry_id=e.id or -1, kind=e.kind.value, source=e.source_text,
                             accepted=e.accepted_translation, scope=e.scope.value,
                             applied=applied, tier="exact"))
    for e in corrections:
        out.append(MemoryHit(entry_id=e.id or -1, kind=e.kind.value, source=e.source_text,
                             accepted=e.accepted_translation, scope=e.scope.value,
                             applied=applied, tier="exact"))
    for s in surfaced:
        out.append(MemoryHit(entry_id=s.entry.id or -1, kind=s.entry.kind.value,
                             source=s.entry.source_text, accepted=s.entry.accepted_translation,
                             scope=s.entry.scope.value, applied=False,
                             similarity=s.similarity, tier=s.tier))
    return out


def _record_applications(ctx: PipelineContext, entries: list[MemoryEntry], request_id: str) -> None:
    if ctx.store is None:
        return
    for e in entries:
        if e.id is not None:
            try:
                ctx.store.record_application(e.id, request_id)  # type: ignore[union-attr]
            except Exception:
                pass


# --------------------------------------------------------------------- fast
def fast_run(ctx: PipelineContext, *, text: str, src: str, tgt: str,
             tracker: BudgetTracker, seg_idx: int, request_id: str) -> FastResult:
    if ctx.mt is None:
        raise BackendNotConfigured("fast mode needs a translation backend")
    warnings: list[str] = []
    masked = extract_placeholders(text)

    locks = retrieval.fast_locks(_load_entries(ctx, src, tgt), text)
    work, lock_restore = _mask_invariant_locks(masked.text, locks)

    if not tracker.can_mt(seg_idx):
        raise BudgetExhausted(f"budget exhausted before fast MT at segment {seg_idx}")
    res = ctx.mt.translate(work, src, tgt)
    tracker.record_mt()

    out = restore_placeholders(res.text, {**lock_restore, **masked.mapping})

    warns, hard = validate_fast(source_text=text, output_text=out,
                                detected_src=res.detected_src, tgt_lang=tgt)
    warnings += warns
    status = Status.UNCERTAIN if (warns or hard) else Status.OK
    if locks:
        warnings.append(f"applied {len(locks)} invariant term lock(s)")

    return FastResult(text=out, engine=res.engine, detected_src=res.detected_src,
                      confidence=0.35, status=status, warnings=warnings, request_id=request_id)


# ------------------------------------------------------------- auto tiers
def _triage(ctx: PipelineContext, source: str, lang_pair: str, domain: str,
            tracker: BudgetTracker, seg_idx: int, context_brief: str = "") -> dict:
    if not tracker.can_llm(seg_idx):
        raise BudgetExhausted(f"budget exhausted before triage at segment {seg_idx}")
    system, user = build_triage(source_text=source, lang_pair=lang_pair, domain=domain,
                                context_brief=context_brief)
    with progress.working([
        "开始检测源语言…",
        "提取专有名词与术语…",
        "判断是否包含俚语或网络梗…",
        "初步判断文本领域与难度…",
    ]):
        resp = ctx.llm.complete(system, user, model=ctx.triage_model, effort="low", max_tokens=1024)
    tracker.record_llm(resp)
    return parse_json_object(resp.text)


def _translate_review(ctx: PipelineContext, source: str, lang_pair: str, memory_block: str,
                      domain: str, tracker: BudgetTracker, seg_idx: int,
                      context_brief: str = "") -> dict:
    if not tracker.can_llm(seg_idx):
        raise BudgetExhausted(f"budget exhausted before translate at segment {seg_idx}")
    system, user = build_translate_review(source_text=source, lang_pair=lang_pair,
                                          register=ctx.register, memory_block=memory_block,
                                          domain=domain, context_brief=context_brief)
    with progress.working([
        "正在翻译并自查译文…",
        "核对语气、语域与用词…",
        "比对术语库与历史更正…",
    ]):
        resp = ctx.llm.complete(system, user, model=ctx.translate_model, effort="medium",
                                max_tokens=2048)
    tracker.record_llm(resp)
    return parse_json_object(resp.text)


def _deep(ctx: PipelineContext, source: str, draft: str, memory_block: str, research: str,
          lang_pair: str, domain: str, tracker: BudgetTracker, seg_idx: int,
          context_brief: str = "") -> dict:
    if not tracker.can_llm(seg_idx):
        return {}  # no budget for the deep pass; keep the Sonnet result
    system, user = build_deep(source_text=source, draft=draft, lang_pair=lang_pair,
                              register=ctx.register, memory_block=memory_block,
                              research=research, domain=domain, context_brief=context_brief)
    with progress.working([
        "疑难点深度推敲中…",
        "综合多方释义、权衡语境…",
        "定稿并沉淀到知识库…",
    ]):
        resp = ctx.llm.complete(system, user, model=ctx.deep_model, effort="high", max_tokens=4096)
    tracker.record_llm(resp)
    return parse_json_object(resp.text)


def _crosscheck(ctx: PipelineContext, source: str, translation: str, lang_pair: str,
                tracker: BudgetTracker, seg_idx: int) -> dict:
    """Independent English triangulation of a hard-case result. Uses the translate model
    (Sonnet) — a different tier than the Opus deep pass that produced the text — so the
    audit is genuinely independent, not the author grading itself."""
    if not tracker.can_llm(seg_idx):
        return {}
    system, user = build_crosscheck(source_text=source, translation=translation, lang_pair=lang_pair)
    with progress.working([
        "英文回译交叉核对语义…",
        "比对极性、语气与主语是否一致…",
    ]):
        resp = ctx.llm.complete(system, user, model=ctx.translate_model, effort="low",
                                max_tokens=1024)
    tracker.record_llm(resp)
    return parse_json_object(resp.text)


def _maybe_learn(ctx: PipelineContext, deep_data: dict, src: str, tgt: str, domain: str,
                 research_backed: bool) -> int | None:
    """Write to the knowledge base what the Opus deep pass resolved — as a QUARANTINED
    (status=proposed) entry that is never auto-injected until corroborated. This is the
    only autonomous write, and it can never be triggered by source text alone."""
    learned = deep_data.get("learned")
    if not isinstance(learned, dict) or ctx.store is None:
        return None
    s = str(learned.get("source") or "").strip()
    t = str(learned.get("target") or "").strip()
    if not s or not t or len(s) > 120:
        return None
    from veriloqua.memory import learn as learnmod

    prov = Provenance.WEB_VERIFIED if research_backed else Provenance.LLM_SELF_DERIVED
    try:
        entry = learnmod.ingest_correction(
            ctx.store, source_text=s, our_output="", corrected=t, src_lang=src, tgt_lang=tgt,
            domain=domain, note=str(learned.get("rationale") or "")[:200], provenance=prov,
        )
        return entry.id
    except Exception:
        return None


def auto_run(ctx: PipelineContext, *, text: str, src: str, tgt: str, domain: str,
             tracker: BudgetTracker, seg_idx: int, request_id: str,
             context_brief: str = "") -> TranslationResult:
    if ctx.llm is None:
        raise BackendNotConfigured(
            "auto mode needs an LLM backend — a logged-in claude/codex CLI or an API key "
            "(pip install veriloqua[anthropic] + ANTHROPIC_API_KEY); or use --mode fast"
        )
    notes: list[str] = []
    warnings: list[str] = []
    alternatives: list[str] = []
    source_repair = ""
    masked = extract_placeholders(text)
    entries = _load_entries(ctx, src, tgt)
    ret = retrieval.retrieve(entries, text, {"domain": domain, "register": ctx.register.tone})
    memory_block = inject.render_memory_block(ret.exact_locks, ret.exact_corrections, ret.surfaced)
    lang_pair = f"{src}->{tgt}"

    # ── tier 0: deterministic MT short-circuit for trivial short inputs ──
    # A greeting-sized source with no negation, no memory involvement, no placeholders and
    # no domain brief gains nothing from the LLM cascade — keyless MT answers in <1s and
    # spends zero tokens. Every check is deterministic; the MT output must additionally
    # survive validate_fast, the wrong-script guard, and an echo check, otherwise the
    # request falls through to Haiku triage unchanged.
    if (ctx.config.auto_mt_shortcircuit and ctx.mt is not None and not context_brief
            and not ret.exact_locks and not ret.exact_corrections and not ret.landmine()
            and not masked.mapping and _mt_trivial(masked.text) and tracker.can_mt(seg_idx)):
        res = ctx.mt.translate(masked.text, src, tgt)
        tracker.record_mt()
        warns, hard = validate_fast(source_text=masked.text, output_text=res.text,
                                    detected_src=res.detected_src, tgt_lang=tgt)
        echoed = normalize(res.text) == normalize(masked.text)
        if not warns and not hard and not echoed \
                and not _wrong_script_for_target(res.text, tgt, masked.text):
            progress.note("琐碎短句,机器直译秒出")
            return TranslationResult(
                text=res.text, mode="auto", confidence=0.7, status=Status.OK,
                detected_src=res.detected_src, backend=res.engine, request_id=request_id,
                notes=["琐碎短句由机器翻译直出,未动用 LLM"],
                memory_hits=_hits(ret.exact_locks, ret.exact_corrections, ret.surfaced,
                                  applied=False),
                trace={
                    "tier": "mt", "detected_src": res.detected_src, "src_confidence": None,
                    "src_wellformed": True, "source_repair": None,
                    "domain": domain or "general", "context_injected": False,
                    "complexity": "trivial", "need_deep": False, "research_status": None,
                    "crosscheck": None, "learned_entry_id": None, "confidence": 0.7,
                    "fuzzy_backend": ret.fuzzy_backend, "budget": tracker.summary(),
                },
            )
        # MT result looked doubtful → continue into the LLM cascade below

    # ── tier 1: Haiku triage ──────────────────────────────────────────────
    tri = _triage(ctx, masked.text, lang_pair, domain, tracker, seg_idx, context_brief)
    detected = str(tri.get("detected_src") or src)
    domain = domain or str(tri.get("domain") or "")
    complexity = str(tri.get("complexity") or "standard")
    tri_flagged = bool(tri.get("is_slang") or tri.get("has_ambiguity") or tri.get("has_cultural"))
    # Language ID is only trusted when the source was 'auto'; an explicit source is the caller's.
    src_conf = float(tri.get("src_confidence", 1.0) or 1.0)
    low_src_conf = src == "auto" and src_conf < SRC_CONF_FLOOR
    # Source well-formedness is orthogonal to language ID: the input can be confidently the
    # detected language yet be a broken string (typos, dropped words, ASR/OCR noise) with no
    # recoverable meaning. A faithful translation of garbage is itself garbage, so this forfeits
    # the fast path, forces the deep (repair-capable) tier, and marks the result uncertain.
    ill_formed = tri.get("src_wellformed", True) is False

    if low_src_conf:
        pn = ", ".join(str(p) for p in (tri.get("proper_nouns") or [])[:3])
        progress.note(f"「{pn or masked.text[:20]}」难以确定语言,可能是专有名词或生造词")
    else:
        progress.note(f"识别为 {detected} 文本,领域:{domain or '通用'}")
    if ill_formed:
        progress.note("源文本疑似有拼写/漏词或不通顺,将推断最可能的原意后翻译")
    if tri.get("is_slang"):
        progress.note("检测到俚语或网络梗,需额外核实")
    if tri.get("has_ambiguity"):
        progress.note("源文存在歧义,需谨慎处理")
    if tri.get("has_cultural"):
        progress.note("涉及文化专有概念")

    tier = "haiku"
    tri_translation = str(tri.get("translation") or "")
    # The fast short-circuit trusts Haiku's OWN one-shot translation, so take it only for a
    # genuinely trivial, low-risk case. A shaky language ID, a negation (the classic
    # statement-vs-command / scope landmine), or a rendering that landed in the wrong script
    # all forfeit the short-circuit and go to the grammar-aware translator tier.
    if (complexity == "simple" and not tri_flagged and not low_src_conf and not ill_formed
            and not ret.landmine() and tri_translation
            and not _has_negation(masked.text)
            and not _wrong_script_for_target(tri_translation, tgt, masked.text)):
        progress.note("句子简单直接,快速给出译文")
        translation = tri_translation
        conf = 0.70
        need_deep = False
    else:
        # ── tier 2: Sonnet translate + self-review ────────────────────────
        tier = "sonnet"
        sv = _translate_review(ctx, masked.text, lang_pair, memory_block, domain, tracker,
                               seg_idx, context_brief)
        translation = str(sv.get("translation") or masked.text)
        conf = float(sv.get("self_confidence", 0.6) or 0.6)
        for n in sv.get("notes", []) or []:
            notes.append(str(n))
        need_deep = (
            bool(sv.get("ambiguity") or sv.get("cultural") or (sv.get("risk_flags") or []))
            or ret.landmine()
            or conf < AUTO_FLOOR
            or complexity == "hard"
            or tri_flagged
            or low_src_conf
            or ill_formed
        )

    # ── tier 3: Opus deep judgment (only when flagged) ────────────────────
    research_status: str | None = None
    learned_id: int | None = None
    if need_deep and ctx.config.auto_deep:
        tier = "opus"
        progress.note("译文有疑点,升级到深度校验")
        research_text = ""
        if ctx.search is not None:
            progress.note("开始联网搜索,查证释义与用例…")
            try:
                results = ctx.search.search(text, max_results=5)
            except Exception:
                results = []
            if results:
                research_status = "web_verified" if len(results) >= 2 else "insufficient"
                if len(results) >= 2:
                    progress.note(f"获取 {len(results)} 条来源,正在交叉比对…")
                else:
                    progress.note("可用来源不足,结论标记为待核实")
                research_text = "\n".join(
                    str(r.get("snippet") or r.get("text") or r)[:500] for r in results[:5]
                )
            else:
                research_status = "none"
                progress.note("未找到可靠来源,结论未经外部验证")
        dv = _deep(ctx, masked.text, translation, memory_block, research_text,
                   lang_pair, domain, tracker, seg_idx, context_brief)
        if dv.get("translation"):
            translation = str(dv["translation"])
        conf = float(dv.get("confidence", conf) or conf)
        for n in dv.get("notes", []) or []:
            notes.append(str(n))
        source_repair = str(dv.get("source_repair") or "").strip()
        alternatives = [str(a).strip() for a in (dv.get("alt_readings") or []) if str(a).strip()]
        learned_id = _maybe_learn(ctx, dv, src, tgt, domain,
                                  research_backed=(research_status == "web_verified"))
    elif need_deep and not ctx.config.auto_deep:
        notes.append("detected a hard case but the deep (Opus) tier is disabled")

    # ── finalize: placeholders, term locks, deterministic reject-guard ────
    out = restore_placeholders(translation, masked.mapping)
    out, _ = guard.apply_invariant_locks(out, [e for e in ret.exact_locks if e.invariant])
    out, guard_fixes = guard.enforce(out, ret.exact_corrections)
    notes += guard_fixes

    exact_n = len(ret.exact_locks) + len(ret.exact_corrections)
    final_conf = min(1.0, conf + (0.05 if exact_n else 0.0))
    status = Status.OK if final_conf >= 0.5 else Status.UNCERTAIN
    if research_status in ("none", "insufficient") and tri_flagged:
        status = Status.UNCERTAIN
        notes.append("需要联网核查但无搜索后端/结果不足,结果未经外部验证")
    if low_src_conf:
        status = Status.UNCERTAIN
        notes.append(
            f"源语言判断存疑(detected={detected}, 置信度≈{src_conf:.2f});"
            "可能是专有名词、生造词或非自然语言,已按原样保留处理"
        )
    if ill_formed:
        status = Status.UNCERTAIN
        msg = "源文本本身疑似有拼写/漏词或语法不通(可能是语音转写、不完整或非母语文本),原意无法确定;"
        if source_repair:
            msg += f"已按最可能的意思翻译。原文可能想表达:「{source_repair}」"
        else:
            msg += "以下为在此前提下的最佳直译,仅供参考"
        warnings.append(msg)

    # ── hard-case cross-check: triangulate meaning through English ────────
    # Direct translation stays the output; this only AUDITS it. On a hard case we read the
    # source and our rendering independently into English and compare propositions — catching
    # polarity/mood/subject drift (the "statement rendered as a command" class of bug).
    crosscheck: dict = {}
    if need_deep and ctx.config.auto_crosscheck:
        crosscheck = _crosscheck(ctx, text, out, lang_pair, tracker, seg_idx)
        src_unintelligible = crosscheck.get("source_intelligible") is False
        if src_unintelligible:
            # Agreement on an unintelligible source only means both readings share the same
            # hallucinated repair — never let it read as a clean pass.
            status = Status.UNCERTAIN
            progress.note("英文交叉核对:源文本不通,无法据此确认语义")
            notes.append("英文交叉核对判定源文本不构成完整语义,译文的语义正确性无法核实")
        elif crosscheck.get("agree") is False:
            status = Status.UNCERTAIN
            div = str(crosscheck.get("divergence") or "").strip()
            progress.note("英文交叉核对发现语义偏差,已标记待确认")
            notes.append(
                "英文交叉核对发现源文与译文可能表达了不同的命题" + (f":{div}" if div else "")
                + "(核对仅提示风险,未改动译文)"
            )
        elif crosscheck.get("agree") is True:
            progress.note("英文交叉核对通过,语义一致")

    _record_applications(ctx, ret.exact_locks + ret.exact_corrections, request_id)
    trace = {
        "tier": tier,
        "detected_src": detected,
        "src_confidence": src_conf,
        "src_wellformed": not ill_formed,
        "source_repair": source_repair or None,
        "domain": domain or "general",
        "context_injected": bool(context_brief),
        "complexity": complexity,
        "need_deep": need_deep,
        "research_status": research_status,
        "crosscheck": {"agree": crosscheck.get("agree"),
                       "source_intelligible": crosscheck.get("source_intelligible"),
                       "divergence": crosscheck.get("divergence")} if crosscheck else None,
        "learned_entry_id": learned_id,
        "confidence": final_conf,
        "fuzzy_backend": ret.fuzzy_backend,
        "budget": tracker.summary(),
    }
    return TranslationResult(
        text=out, mode="auto", confidence=final_conf, status=status, detected_src=detected,
        backend=getattr(ctx.llm, "name", ""), request_id=request_id,
        alternatives=alternatives, notes=notes, warnings=warnings,
        memory_hits=_hits(ret.exact_locks, ret.exact_corrections, ret.surfaced, applied=True),
        trace=trace,
    )


# ----------------------------------------------------------------- dispatch
def run_segment(ctx: PipelineContext, *, text: str, src: str, tgt: str, domain: str,
                mode: Mode, tracker: BudgetTracker, seg_idx: int,
                request_id: str | None = None, context_brief: str = ""):
    rid = request_id or new_request_id()
    if mode is Mode.FAST:
        return fast_run(ctx, text=text, src=src, tgt=tgt, tracker=tracker,
                        seg_idx=seg_idx, request_id=rid)
    # auto (and the medium/high aliases) all run the tiered pipeline
    return auto_run(ctx, text=text, src=src, tgt=tgt, domain=domain,
                    tracker=tracker, seg_idx=seg_idx, request_id=rid, context_brief=context_brief)
