"""Mode policies and budgets.

A mode is a *policy* over the pipeline: which capabilities are allowed, how much
verification runs, and how much may be spent. Budgets are enforced per-segment AND
per-job so a large file can never silently become a five-figure bill.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Mode(str, Enum):
    FAST = "fast"
    MEDIUM = "medium"
    HIGH = "high"
    AUTO = "auto"


class MemoryWrite(str, Enum):
    NONE = "none"                    # fast: never writes
    EXPLICIT_ONLY = "explicit_only"  # medium: only via correct()
    AUTONOMOUS = "autonomous"        # high: auto-writes through quarantine


@dataclass(slots=True, frozen=True)
class Budget:
    """Per-segment AND per-job ceilings. The pipeline fails *closed* to the best
    result so far when any ceiling is hit — never loops, never silently overspends."""

    # per-segment
    max_llm_calls: int = 0
    max_mt_calls: int = 0
    wall_clock_s: float = 0.0
    # per-job (aggregate across all segments)
    job_max_calls: int = 0            # 0 = unlimited
    job_max_tokens: int = 0           # 0 = unlimited
    job_max_wall_clock_s: float = 0.0 # 0 = unlimited
    job_max_cost_usd: float = 0.0     # 0 = unlimited


@dataclass(slots=True, frozen=True)
class ModePolicy:
    mode: Mode
    use_llm: bool
    glossary_apply: bool             # deterministic term locks applied
    apply_corrections: bool          # exact corrections auto-applied (never in fast)
    memory_read: bool                # semantic/fuzzy surfacing tiers read
    memory_write: MemoryWrite
    verify: bool                     # CoVe / back-translation / judge
    web_research: bool
    candidates: int                  # number of candidates generated
    budget: Budget


FAST = ModePolicy(
    mode=Mode.FAST,
    use_llm=False,
    glossary_apply=True,             # invariant whole-token locks only
    apply_corrections=False,
    memory_read=False,
    memory_write=MemoryWrite.NONE,
    verify=False,
    web_research=False,
    candidates=0,
    budget=Budget(max_mt_calls=1, wall_clock_s=5.0),
)

MEDIUM = ModePolicy(
    mode=Mode.MEDIUM,
    use_llm=True,
    glossary_apply=True,
    apply_corrections=True,
    memory_read=True,
    memory_write=MemoryWrite.EXPLICIT_ONLY,
    verify=True,                     # cheap stdlib round-trip flag only
    web_research=False,
    candidates=1,
    budget=Budget(max_llm_calls=2, max_mt_calls=2, wall_clock_s=15.0),
)

HIGH = ModePolicy(
    mode=Mode.HIGH,
    use_llm=True,
    glossary_apply=True,
    apply_corrections=True,
    memory_read=True,
    memory_write=MemoryWrite.AUTONOMOUS,
    verify=True,
    web_research=True,               # only fires if a SearchBackend is configured
    candidates=2,                    # 2 strategies (faithful / localized), one batched call
    # Tuned for latency over a local agent CLI: the default high path is 2 calls
    # (candidates + batched judge). Blind back-translation is opt-in (config
    # high_backtranslate) and CoVe is conditional (landmine only), so a fresh
    # segment stays ~2 calls. Cap kept high as a safety backstop for regenerations.
    budget=Budget(max_llm_calls=12, max_mt_calls=1, wall_clock_s=30.0),
)

# The one non-fast mode: a tiered cascade (Haiku triage → Sonnet translate+review →
# Opus deep, only when flagged). Budget covers triage + translate + deep + 1 slack.
AUTO = ModePolicy(
    mode=Mode.AUTO,
    use_llm=True,
    glossary_apply=True,
    apply_corrections=True,
    memory_read=True,
    memory_write=MemoryWrite.AUTONOMOUS,   # Opus deep pass may write QUARANTINED entries
    verify=True,
    web_research=True,                     # only fires if a SearchBackend is configured
    candidates=0,
    budget=Budget(max_llm_calls=4, max_mt_calls=0, wall_clock_s=60.0),
)

PRESETS: dict[Mode, ModePolicy] = {
    Mode.FAST: FAST, Mode.AUTO: AUTO, Mode.MEDIUM: MEDIUM, Mode.HIGH: HIGH,
}


def policy_for(mode: Mode) -> ModePolicy:
    """Everything except fast now runs the tiered ``auto`` pipeline; medium/high are
    kept only as back-compat aliases and resolve to the same policy."""
    if mode is Mode.FAST:
        return FAST
    return AUTO
