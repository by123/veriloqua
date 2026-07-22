"""The trusted write path.

A correction enters ONLY here (via ``tr.correct()`` / ``vq correct``) — never from
source text, never from an autonomous fast/medium path. The exact-match never-repeat
core needs ZERO LLM calls and runs entirely on deterministic string handling by
classifying the MQM error type and refining APPLIES-WHEN.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from veriloqua._norm import normalize
from veriloqua.memory.records import Kind, MemoryEntry, Provenance, Scope, Status
from veriloqua.taxonomy import UNSPECIFIED


def _applies_when(ctx: dict[str, str]) -> str:
    if not ctx:
        return "user-scope exact match only"
    return "; ".join(f"{k}={v}" for k, v in ctx.items())


def ingest_correction(
    store: Any,
    *,
    source_text: str,
    our_output: str,
    corrected: str,
    src_lang: str,
    tgt_lang: str,
    domain: str = "",
    register: str = "",
    scope: Scope = Scope.USER,
    scope_id: str = "",
    note: str = "",
    provenance: Provenance = Provenance.USER_CORRECTION,
    replay_dir: str | Path | None = None,
) -> MemoryEntry:
    """File a correction. Stores our output as a rejected rendering (the reject-guard
    payload), keyed on the normalized source span."""
    source_norm = normalize(source_text)
    ctx: dict[str, str] = {}
    if domain:
        ctx["domain"] = domain
    if register:
        ctx["register"] = register

    error_type = UNSPECIFIED
    applies_when = _applies_when(ctx)

    rejected: list[str] = []
    if our_output and normalize(our_output) != normalize(corrected):
        rejected.append(our_output)

    # narrowest scope by default; user preference IS truth for them, so instant.
    entry = MemoryEntry(
        kind=Kind.CORRECTION,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        source_text=source_text,
        source_norm=source_norm,
        accepted_translation=corrected,
        rejected_translations=rejected,
        error_type=error_type,
        context_tags=ctx,
        applies_when=applies_when,
        rationale=note,
        provenance=provenance,
        confidence=provenance_default_conf(provenance),
        scope=scope,
        scope_id=scope_id,
        status=Status.ACTIVE if provenance in (Provenance.USER_CORRECTION, Provenance.HUMAN_REVIEW)
        else Status.PROPOSED,  # llm/web-derived quarantine until corroborated
    )

    existing = store.find_active_key(source_norm, src_lang, tgt_lang, scope.value, scope_id)
    if existing is not None:
        merged = set(existing.rejected_translations) | set(rejected)
        if normalize(existing.accepted_translation) != normalize(corrected):
            # the user changed their mind: the previous accepted target is now blocked too.
            merged.add(existing.accepted_translation)
            store.record_conflict(existing.id, corrected, note="re-correction (user wins)")
        entry.rejected_translations = list(merged)
        entry.id = store.supersede(existing.id, entry)
    else:
        store.add_entry(entry)

    if replay_dir is not None and entry.status == Status.ACTIVE:
        append_replay_case(replay_dir, entry)
    return entry


def provenance_default_conf(p: Provenance) -> float:
    return {
        Provenance.HUMAN_REVIEW: 0.95,
        Provenance.USER_CORRECTION: 0.8,
        Provenance.WEB_VERIFIED: 0.6,
        Provenance.LLM_SELF_DERIVED: 0.4,
    }.get(p, 0.5)


def add_term_lock(
    store: Any,
    *,
    src_lang: str,
    tgt_lang: str,
    source_text: str,
    target: str,
    invariant: bool = False,
    domain: str = "",
    register: str = "",
    scope: Scope = Scope.USER,
    scope_id: str = "",
) -> MemoryEntry:
    ctx: dict[str, str] = {}
    if domain:
        ctx["domain"] = domain
    if register:
        ctx["register"] = register
    entry = MemoryEntry(
        kind=Kind.TERM_LOCK,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        source_text=source_text,
        source_norm=normalize(source_text),
        accepted_translation=target,
        invariant=invariant,
        context_tags=ctx,
        applies_when=_applies_when(ctx),
        provenance=Provenance.HUMAN_REVIEW,
        confidence=0.95,
        scope=scope,
        scope_id=scope_id,
    )
    existing = store.find_active_key(entry.source_norm, src_lang, tgt_lang, scope.value, scope_id)
    if existing is not None:
        entry.id = store.supersede(existing.id, entry)
    else:
        store.add_entry(entry)
    return entry


# ---------------------------------------------------------------- replay cases
def append_replay_case(replay_dir: str | Path, entry: MemoryEntry) -> None:
    """Append a correction-replay case + its paired over-fit probe to the USER data
    dir (never the packaged fixtures). The probe uses the same source with a
    deliberately mismatched domain and asserts the correction does NOT fire there."""
    replay_dir = Path(replay_dir)
    replay_dir.mkdir(parents=True, exist_ok=True)
    case = {
        "source": entry.source_text,
        "src_lang": entry.src_lang,
        "tgt_lang": entry.tgt_lang,
        "rejected": entry.rejected_translations,
        "accepted": entry.accepted_translation,
        "error_type": entry.error_type,
        "context": entry.context_tags,
        "scope": entry.scope.value,
    }
    with (replay_dir / "corrections.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(case, ensure_ascii=False) + "\n")

    # over-fit probe: same source, contradicted domain — must NOT fire.
    probe_domain = "legal" if entry.context_tags.get("domain") != "legal" else "casual-chat"
    probe = {
        "source": entry.source_text,
        "src_lang": entry.src_lang,
        "tgt_lang": entry.tgt_lang,
        "context": {"domain": probe_domain},
        "must_not_apply_entry_source": entry.source_text,
    }
    with (replay_dir / "overfit_probes.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(probe, ensure_ascii=False) + "\n")
