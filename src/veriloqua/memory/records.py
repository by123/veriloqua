"""Record types + enums for the memory store."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Kind(str, Enum):
    TERM_LOCK = "term_lock"      # deterministic glossary; applied in ALL modes
    CORRECTION = "correction"    # gated contextual fix; medium/high only
    IDIOM = "idiom"
    DONT_USE = "dont_use"
    STYLE_PREF = "style_pref"


class Scope(str, Enum):
    GLOBAL = "global"
    PROJECT = "project"
    USER = "user"                # narrowest — the default write scope


class Status(str, Enum):
    ACTIVE = "active"            # only active rows auto-apply
    PROPOSED = "proposed"        # quarantined until corroborated
    NEEDS_REVIEW = "needs_review"
    RETIRED = "retired"
    SUPERSEDED = "superseded"


class Provenance(str, Enum):
    HUMAN_REVIEW = "human_review"
    USER_CORRECTION = "user_correction"
    WEB_VERIFIED = "web_verified"
    LLM_SELF_DERIVED = "llm_self_derived"


#: trust weight per provenance, feeds the retrieval gate.
PROVENANCE_WEIGHT: dict[Provenance, float] = {
    Provenance.HUMAN_REVIEW: 1.0,
    Provenance.USER_CORRECTION: 0.8,
    Provenance.WEB_VERIFIED: 0.7,
    Provenance.LLM_SELF_DERIVED: 0.4,
}

#: per-kind freshness horizon in days; None = never expires (term locks).
TTL_DAYS: dict[Kind, int | None] = {
    Kind.TERM_LOCK: None,
    Kind.CORRECTION: 365,
    Kind.IDIOM: 365,
    Kind.DONT_USE: None,
    Kind.STYLE_PREF: 365,
}

# Scope retrieval precedence: user beats project beats global.
SCOPE_PRECEDENCE: dict[Scope, int] = {Scope.USER: 3, Scope.PROJECT: 2, Scope.GLOBAL: 1}


@dataclass(slots=True)
class MemoryEntry:
    """One glossary/correction row. ``rejected_translations`` is the never-repeat
    payload: it always includes our own past wrong output for a correction."""

    kind: Kind
    src_lang: str
    tgt_lang: str
    source_text: str
    source_norm: str
    accepted_translation: str
    id: int | None = None
    literal_gloss: str = ""
    rejected_translations: list[str] = field(default_factory=list)
    error_type: str = "unspecified"
    context_tags: dict[str, str] = field(default_factory=dict)   # domain/register/locale/audience/project_id
    applies_when: str = ""
    rationale: str = ""
    examples: list[dict[str, str]] = field(default_factory=list)
    provenance: Provenance = Provenance.USER_CORRECTION
    source_urls: list[str] = field(default_factory=list)
    confidence: float = 0.8
    scope: Scope = Scope.USER
    scope_id: str = ""
    status: Status = Status.ACTIVE
    invariant: bool = False       # whole-token safe substitution (proper noun/code/unit)
    hit_count: int = 0
    apply_count: int = 0
    positive_signals: int = 0
    negative_signals: int = 0
    ttl_days: int | None = None
    created_at: str = ""
    last_verified_at: str = ""
    last_applied_at: str = ""
    superseded_by: int | None = None
    deleted_at: str | None = None

    def weight(self) -> float:
        return PROVENANCE_WEIGHT.get(self.provenance, 0.4)


@dataclass(slots=True)
class RequestLogRecord:
    """PII-minimized ring-buffer entry. Powers correct-by-id WITHOUT persisting
    every source forever: unfiled records age out; a filed correction promotes
    this record's payload into a durable correction row."""

    request_id: str
    src_lang: str
    tgt_lang: str
    source_text: str        # may be scrubbed; minimal-span where possible
    output_text: str
    mode: str
    domain: str = ""
    register: str = ""
    created_at: str = ""
    source_hash: str = ""
