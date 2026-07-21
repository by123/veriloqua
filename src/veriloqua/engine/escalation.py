"""medium → high escalation rules.

A matched past correction is a landmine: a past mistake permanently raises the
verification bar for that context. Risk flags and (in auto mode) a low confidence
floor also trigger escalation."""

from __future__ import annotations

RISK_TRIGGERS = {
    "slang", "neologism", "ambiguity", "ambiguous", "cultural_reference",
    "cultural", "culture", "legal", "medical", "safety", "idiom", "pun",
}

CONFIDENCE_FLOOR = 0.55  # calibrated default; auto-mode only


def should_escalate(
    *,
    risk_flags: list[str],
    landmine: bool,
    confidence: float,
    auto: bool,
    floor: float = CONFIDENCE_FLOOR,
) -> tuple[bool, str]:
    if landmine:
        return True, "landmine: a past correction matched this context"
    hits = {f.lower() for f in risk_flags} & RISK_TRIGGERS
    if hits:
        return True, f"risk_flags: {sorted(hits)}"
    if auto and confidence < floor:
        return True, f"confidence {confidence:.2f} < floor {floor:.2f}"
    return False, ""
