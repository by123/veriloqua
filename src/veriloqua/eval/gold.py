"""Gold-set quality evaluation: ACTUALLY translate the gold segments and score them.

Scoring is sentence chrF (Popović 2015). When ``veriloqua[eval]`` (sacrebleu) is
installed, the reference implementation is used; otherwise a small built-in
approximation (character n-grams up to order 6, β=2, whitespace removed) — the
report labels which one produced the numbers. COMET and LLM-judge scoring are NOT
implemented; the report never claims otherwise. Advisory only: this never gates
a build — the deterministic replay tier does.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable

try:  # reference implementation, optional
    from sacrebleu.metrics import CHRF as _SacreCHRF

    _sacre_chrf: _SacreCHRF | None = _SacreCHRF()
    CHRF_IMPL = "sacrebleu"
except ImportError:  # pragma: no cover - exercised only without the [eval] extra
    _sacre_chrf = None
    CHRF_IMPL = "builtin (approximate chrF; install veriloqua[eval] for sacrebleu)"


def _char_ngrams(text: str, n: int) -> Counter:
    return Counter(text[i : i + n] for i in range(len(text) - n + 1))


def _builtin_chrf(hypothesis: str, reference: str, *, char_order: int = 6,
                  beta: float = 2.0) -> float:
    """Sentence chrF over whitespace-stripped character n-grams (order 1..6, β=2).
    An approximation of sacrebleu's chrF, close enough for an advisory signal."""
    hyp = "".join(hypothesis.split())
    ref = "".join(reference.split())
    precisions: list[float] = []
    recalls: list[float] = []
    for n in range(1, char_order + 1):
        h, r = _char_ngrams(hyp, n), _char_ngrams(ref, n)
        if not h and not r:
            continue
        overlap = sum((h & r).values())
        precisions.append(overlap / max(1, sum(h.values())))
        recalls.append(overlap / max(1, sum(r.values())))
    if not precisions:
        return 0.0
    p = sum(precisions) / len(precisions)
    r = sum(recalls) / len(recalls)
    if p + r == 0.0:
        return 0.0
    b2 = beta * beta
    return 100.0 * (1 + b2) * p * r / (b2 * p + r)


def chrf(hypothesis: str, reference: str) -> float:
    """Sentence chrF in [0, 100] via sacrebleu when installed, else the built-in."""
    if _sacre_chrf is not None:
        return float(_sacre_chrf.sentence_score(hypothesis, [reference]).score)
    return _builtin_chrf(hypothesis, reference)


def run_gold(*, translate_fn: Callable[[str, str], str],
             cases: list[dict]) -> dict[str, Any]:
    """Translate every gold case through ``translate_fn(source, domain)`` and score
    each hypothesis against the reference with sentence chrF. Returns per-segment
    results plus the mean; translation errors are recorded, never hidden."""
    per_segment: list[dict[str, Any]] = []
    errors: list[str] = []
    for case in cases:
        source = str(case.get("source") or "")
        reference = str(case.get("reference") or "")
        if not source or not reference:
            errors.append(f"malformed gold case (needs source+reference): {case!r:.80}")
            continue
        try:
            hypothesis = translate_fn(source, str(case.get("domain") or ""))
        except Exception as exc:
            errors.append(f"translation failed for '{source[:40]}': {exc}")
            continue
        per_segment.append({
            "source": source,
            "hypothesis": hypothesis,
            "reference": reference,
            "chrf": round(chrf(hypothesis, reference), 1),
        })

    scores = [seg["chrf"] for seg in per_segment]
    return {
        "ran": True,
        "segments": len(cases),
        "scored": len(scores),
        "chrf_mean": round(sum(scores) / len(scores), 1) if scores else None,
        "chrf_impl": CHRF_IMPL,
        "per_segment": per_segment,
        "errors": errors,
        "note": ("advisory quality signal: sentence chrF against the gold set. "
                 "COMET and LLM-judge scoring are not implemented. Never gates a build."),
    }
