"""Semantic-equality spot check (Tier-2, advisory).

Two deterministic layers: a rejected-string blocklist, then a normalized similarity
ratio against the accepted rendering. Nothing heavier ships or is claimed. This is
never a build gate — the deterministic reject-guard is."""

from __future__ import annotations

import difflib

from veriloqua._norm import contains_span, normalize


def conveys_accepted(output: str, accepted: str, rejected: list[str], *, cutoff: float = 0.6) -> bool:
    for r in rejected:
        if r and contains_span(output, r):
            return False
    if not accepted:
        return True
    return difflib.SequenceMatcher(None, normalize(output), normalize(accepted)).ratio() >= cutoff
