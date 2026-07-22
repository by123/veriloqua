"""Mode policies and budgets.

Two modes only: ``fast`` (keyless MT) and ``auto`` (the tiered LLM cascade).
A mode is a *policy* over the pipeline: which capabilities are allowed, how much
verification runs, and how much may be spent. Budgets are enforced per-segment AND
per-job to bound the cost of a large file.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Mode(str, Enum):
    FAST = "fast"
    AUTO = "auto"


class MemoryWrite(str, Enum):
    NONE = "none"                    # fast: never writes
    AUTONOMOUS = "autonomous"        # auto: the deep tier may write through quarantine


@dataclass(slots=True, frozen=True)
class Budget:
    """Per-segment AND per-job ceilings. When a ceiling is hit the pipeline stops and
    returns the best result so far (marked degraded) instead of looping or overspending."""

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
    memory_read: bool                # lexical-fuzzy surfacing tier read
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

PRESETS: dict[Mode, ModePolicy] = {Mode.FAST: FAST, Mode.AUTO: AUTO}


def policy_for(mode: Mode) -> ModePolicy:
    return FAST if mode is Mode.FAST else AUTO
