"""Composite confidence with components exposed in the trace.

Self-report is weighted near-zero (models are poor at their own calibration). The
independence term is included ONLY when the judge genuinely ran on a different model
(judge_independent=true); same-model scoring never earns the independence credit.
"""

from __future__ import annotations

from veriloqua.result import QualityScore

_BASE = {"medium": 0.60, "high": 0.70, "fast": 0.35}


def composite(
    *,
    mode: str,
    self_confidence: float = 0.5,
    quality: QualityScore | None = None,
    roundtrip_adequacy: float | None = None,
    exact_hits: int = 0,
) -> tuple[float, dict[str, float]]:
    base = _BASE.get(mode, 0.5)
    components: dict[str, float] = {"base": base}
    score = base

    if quality is not None:
        qnorm = quality.min_dim() / 5.0
        components["judge_min_dim_norm"] = round(qnorm, 3)
        # independence credit only when real
        weight = 0.35 if quality.judge_independent else 0.15
        components["judge_weight"] = weight
        score = (1 - weight) * score + weight * qnorm

    if roundtrip_adequacy is not None:
        components["roundtrip_adequacy"] = round(roundtrip_adequacy, 3)
        score += 0.05 * (roundtrip_adequacy - 0.5)  # adequacy flag only, small nudge

    if exact_hits:
        components["exact_memory_hits"] = float(exact_hits)
        score += 0.05  # deterministic anchor: exact term/correction applied

    # self-report: deliberately tiny weight
    components["self_report"] = round(self_confidence, 3)
    score += 0.03 * (self_confidence - 0.5)

    score = max(0.0, min(1.0, score))
    return round(score, 3), components
