"""The deterministic post-generation reject-guard — Layer B of the guarantee.

Fed ONLY by the exact-match span-scan set (never fuzzy-surfaced entries), so its
verdict is identical on every machine. If a generated output reproduces a rejected
rendering for an in-context correction, the guard first asks the caller to
regenerate with an explicit NEVER constraint; if that still fails (or there is no
model, as in a pure-deterministic test), it hard-substitutes the accepted rendering.
An exact repeat of a corrected mistake in its own context is therefore impossible,
regardless of model temperature.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from veriloqua._norm import contains_span
from veriloqua.memory.records import MemoryEntry


@dataclass(slots=True)
class Violation:
    entry: MemoryEntry
    rejected: str


def find_violations(output: str, corrections: list[MemoryEntry]) -> list[Violation]:
    viols: list[Violation] = []
    for e in corrections:
        for rej in e.rejected_translations:
            if rej and contains_span(output, rej):
                viols.append(Violation(entry=e, rejected=rej))
    return viols


def _replace_ci(text: str, old: str, new: str) -> str:
    if not old:
        return text
    return re.sub(re.escape(old), lambda _m: new, text, flags=re.IGNORECASE)


def enforce(output: str, corrections: list[MemoryEntry]) -> tuple[str, list[str]]:
    """Deterministically remove any rejected rendering that survived generation.

    Returns (possibly-rewritten output, human-readable fix notes). This is the
    last-resort hard enforcement; the pipeline prefers regeneration first.
    """
    fixes: list[str] = []
    out = output
    for v in find_violations(out, corrections):
        replacement = v.entry.accepted_translation
        new_out = _replace_ci(out, v.rejected, replacement)
        if new_out != out:
            out = new_out
            fixes.append(
                f"blocked repeat of rejected rendering "
                f"'{v.rejected}' → '{replacement}' (entry #{v.entry.id})"
            )
    return out, fixes


def apply_invariant_locks(text: str, locks: list[MemoryEntry]) -> tuple[str, list[int]]:
    """Whole-token, case-insensitive substitution of invariant term locks (fast mode
    and as a medium/high safety net). Longest source first to avoid partial shadowing."""
    applied: list[int] = []
    out = text
    for e in sorted(locks, key=lambda e: len(e.source_text), reverse=True):
        pattern = re.escape(e.source_text)
        # word boundary where the token is alphanumeric-bounded; plain otherwise (CJK)
        if e.source_text[:1].isascii() and e.source_text.strip().replace(" ", "").isalnum():
            pattern = rf"\b{pattern}\b"
        repl = e.accepted_translation
        new_out = re.sub(pattern, lambda _m, _r=repl: _r, out, flags=re.IGNORECASE)
        if new_out != out:
            out = new_out
            if e.id is not None:
                applied.append(e.id)
    return out, applied
