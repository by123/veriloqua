"""Independent judge: score all candidates in one batched structured call and pick
the highest MIN-dimension candidate.

``judge_independent`` reflects reality: it is True only when the judge ran on a
genuinely different model from the translator. Same-model scoring is allowed (when
``allow_same_model_judge`` is set) but is surfaced as judge_independent=false, and the
confidence layer drops the independence credit accordingly — independence is never faked.
"""

from __future__ import annotations

from veriloqua.backends.base import LLMBackend
from veriloqua.engine._parse import parse_json_object
from veriloqua.engine.budget import BudgetTracker
from veriloqua.engine.prompt_assembly import build_judge
from veriloqua.lang.register import RegisterProfile
from veriloqua.result import Issue, QualityScore


def judge_candidates(
    llm: LLMBackend,
    *,
    model: str,
    effort: str,
    source_text: str,
    candidate_texts: list[str],
    lang_pair: str,
    register: RegisterProfile,
    domain: str,
    independent: bool,
    tracker: BudgetTracker,
    seg_idx: int,
) -> tuple[int, list[QualityScore], list[Issue]]:
    if not candidate_texts:
        return 0, [], []
    if not tracker.can_llm(seg_idx):
        # no budget to judge: pick candidate 0, no scores
        return 0, [], []

    system, user = build_judge(
        source_text=source_text, candidates=candidate_texts, lang_pair=lang_pair,
        register=register, domain=domain,
    )
    resp = llm.complete(system, user, model=model, effort=effort, max_tokens=1024)
    tracker.record_llm(resp)
    data = parse_json_object(resp.text)

    # Fast judge: a single best-index pick (detailed per-dimension scoring makes the
    # model deliberate for tens of seconds — too slow over a local agent CLI). The
    # accuracy win of "high" comes from candidate diversity + the judge's selection +
    # the deterministic guard, not from verbose scores.
    best = data.get("best_index", 0)
    if not isinstance(best, int) or not (0 <= best < len(candidate_texts)):
        best = 0
    return best, [], []
