"""Layered semantic-equality checker (Tier-2, advisory).

Order: rejected-string blocklist → (optional) NLI entailment → judge tiebreak.
Only the blocklist ships in core; the richer tiers are boosters. This is never a
build gate — the deterministic reject-guard is."""

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
