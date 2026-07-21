"""Result types returned by the engine.

``TranslationResult`` (medium/high) and ``FastResult`` (fast) both stringify to
their ``.text`` so the one-line common case stays trivial:

    >>> str(veriloqua.translate("Hello", to="es"))   # doctest: +SKIP
    'Hola'
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    OK = "ok"
    UNCERTAIN = "uncertain"  # produced a result but flagged low confidence / anomalies
    DEGRADED = "degraded"    # ran a weaker path than requested (e.g. budget/consent)


class ResultKind(str, Enum):
    FAST = "fast"
    TRANSLATION = "translation"


@dataclass(slots=True)
class MemoryHit:
    """A glossary/correction entry that participated in a translation."""

    entry_id: int
    kind: str
    source: str
    accepted: str
    scope: str
    applied: bool           # exact-match auto-applied vs merely surfaced/injected
    similarity: float = 1.0
    tier: str = "exact"     # exact | lexical | semantic


@dataclass(slots=True)
class Issue:
    """One typed MQM-style error found during verification/judging."""

    category: str           # see veriloqua.taxonomy.ERROR_TAXONOMY
    severity: str           # neutral | minor | major | critical
    source_span: str = ""
    target_span: str = ""
    comment: str = ""


@dataclass(slots=True)
class QualityScore:
    """Five-dimension 1–5 rubric scores (see the judge prompt)."""

    adequacy: float = 0.0            # D1 meaning fidelity
    fluency: float = 0.0             # D2 naturalness
    register: float = 0.0            # D3 tone/politeness match
    terminology: float = 0.0         # D4 term adherence (concept + inflection)
    culture: float = 0.0             # D5 pragmatic equivalence
    judge_independent: bool = False  # scored by a genuinely different model?

    def min_dim(self) -> float:
        return min(self.adequacy, self.fluency, self.register, self.terminology, self.culture)

    def mean(self) -> float:
        return (self.adequacy + self.fluency + self.register + self.terminology + self.culture) / 5.0


@dataclass(slots=True)
class Candidate:
    """One generated translation candidate (high mode)."""

    text: str
    strategy: str = "faithful"       # faithful | localized | register_matched
    quality: QualityScore | None = None


@dataclass(slots=True)
class FastResult:
    """Fast-mode output. No LLM, no memory writes, low fixed confidence."""

    text: str
    engine: str
    detected_src: str = "auto"
    confidence: float = 0.35          # fixed-low: signals "unverified"
    status: Status = Status.OK
    warnings: list[str] = field(default_factory=list)
    request_id: str = ""
    kind: ResultKind = ResultKind.FAST

    def __str__(self) -> str:
        return self.text

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "text": self.text,
            "engine": self.engine,
            "detected_src": self.detected_src,
            "confidence": self.confidence,
            "status": self.status.value,
            "warnings": list(self.warnings),
            "request_id": self.request_id,
        }


@dataclass(slots=True)
class TranslationResult:
    """Medium/high output with the full verification + memory trace."""

    text: str
    mode: str
    confidence: float = 0.0
    status: Status = Status.OK
    detected_src: str = "auto"
    backend: str = ""
    request_id: str = ""
    alternatives: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    memory_hits: list[MemoryHit] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    quality: QualityScore | None = None
    trace: dict[str, Any] = field(default_factory=dict)
    kind: ResultKind = ResultKind.TRANSLATION

    def __str__(self) -> str:
        return self.text

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "text": self.text,
            "mode": self.mode,
            "confidence": self.confidence,
            "status": self.status.value,
            "detected_src": self.detected_src,
            "backend": self.backend,
            "request_id": self.request_id,
            "alternatives": list(self.alternatives),
            "notes": list(self.notes),
            "warnings": list(self.warnings),
            "memory_hits": [_hit_dict(h) for h in self.memory_hits],
            "issues": [_issue_dict(i) for i in self.issues],
            "quality": _quality_dict(self.quality) if self.quality else None,
            "trace": self.trace,
        }


def _hit_dict(h: MemoryHit) -> dict[str, Any]:
    return {
        "entry_id": h.entry_id, "kind": h.kind, "source": h.source, "accepted": h.accepted,
        "scope": h.scope, "applied": h.applied, "similarity": h.similarity, "tier": h.tier,
    }


def _issue_dict(i: Issue) -> dict[str, Any]:
    return {
        "category": i.category, "severity": i.severity,
        "source_span": i.source_span, "target_span": i.target_span, "comment": i.comment,
    }


def _quality_dict(q: QualityScore) -> dict[str, Any]:
    return {
        "adequacy": q.adequacy, "fluency": q.fluency, "register": q.register,
        "terminology": q.terminology, "culture": q.culture,
        "judge_independent": q.judge_independent,
        "min_dim": q.min_dim(), "mean": q.mean(),
    }
