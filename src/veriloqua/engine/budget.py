"""Budget accounting — per-segment AND per-job.

A large file can never silently become a five-figure bill: the tracker refuses the
next LLM/MT call once any job ceiling is reached, and the pipeline fails *closed* to
the best result so far with a 'budget exhausted at segment K' summary.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from veriloqua.backends.base import LLMResponse
from veriloqua.errors import BudgetExhausted
from veriloqua.modes import Budget


@dataclass(slots=True)
class BudgetTracker:
    budget: Budget
    job_calls: int = 0
    job_tokens: int = 0
    job_cost: float = 0.0
    started: float = field(default_factory=time.monotonic)
    # per-segment (reset each segment)
    seg_llm_calls: int = 0
    seg_mt_calls: int = 0
    exhausted_at_segment: int | None = None

    def start_segment(self) -> None:
        self.seg_llm_calls = 0
        self.seg_mt_calls = 0

    def job_elapsed(self) -> float:
        return time.monotonic() - self.started

    def _check_job(self, segment_index: int) -> None:
        b = self.budget
        if b.job_max_calls and self.job_calls >= b.job_max_calls:
            self._raise("calls", segment_index)
        if b.job_max_tokens and self.job_tokens >= b.job_max_tokens:
            self._raise("tokens", segment_index)
        if b.job_max_cost_usd and self.job_cost >= b.job_max_cost_usd:
            self._raise("cost", segment_index)
        if b.job_max_wall_clock_s and self.job_elapsed() >= b.job_max_wall_clock_s:
            self._raise("wall-clock", segment_index)

    def _raise(self, kind: str, segment_index: int) -> None:
        self.exhausted_at_segment = segment_index
        raise BudgetExhausted(f"job budget exhausted ({kind}) at segment {segment_index}")

    def can_llm(self, segment_index: int) -> bool:
        """True if another per-segment LLM call is allowed (and job budget is intact)."""
        self._check_job(segment_index)
        if self.budget.max_llm_calls and self.seg_llm_calls >= self.budget.max_llm_calls:
            return False
        return True

    def record_llm(self, resp: LLMResponse) -> None:
        self.seg_llm_calls += 1
        self.job_calls += 1
        self.job_tokens += resp.input_tokens + resp.output_tokens
        self.job_cost += resp.cost_usd

    def can_mt(self, segment_index: int) -> bool:
        self._check_job(segment_index)
        if self.budget.max_mt_calls and self.seg_mt_calls >= self.budget.max_mt_calls:
            return False
        return True

    def record_mt(self) -> None:
        self.seg_mt_calls += 1
        self.job_calls += 1

    def summary(self) -> dict:
        return {
            "job_llm_and_mt_calls": self.job_calls,
            "job_tokens": self.job_tokens,
            "job_cost_usd": round(self.job_cost, 6),
            "job_wall_clock_s": round(self.job_elapsed(), 3),
            "exhausted_at_segment": self.exhausted_at_segment,
        }
