"""The deterministic post-generation reject-guard — Layer B of the correction memory.

Fed ONLY by the exact-match span-scan set (never fuzzy-surfaced entries), so its
verdict is identical on every machine. If a generated output reproduces a rejected
rendering for an in-context correction, the guard substitutes the accepted rendering.

Detection and replacement use the SAME normalized-token machinery: a violation is
found via ``contains_span`` (normalized), and the replacement locates that same
normalized token span in the ORIGINAL string and splices there — so a rejected
rendering that differs from the output only in case, width (NFKC), or punctuation
is still replaced. Anything the guard cannot rewrite is reported back via
``find_violations`` so the pipeline can fail closed (degrade the status) instead
of shipping a silently unenforced result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from veriloqua._norm import contains_span, normalize, tokens
from veriloqua.memory.records import MemoryEntry

# Same char class the normalizer keeps: word chars incl. CJK. Tokenizing the ORIGINAL
# string with this and normalizing each token yields the same token stream that
# ``_norm.tokens`` produces on the whole string, but with original [start, end) spans.
_TOKEN_RE = re.compile(r"[\w一-鿿぀-ヿ가-힣]+", re.UNICODE)

_MAX_REPLACEMENTS = 20  # hard cap: a replacement can never loop forever


@dataclass(slots=True)
class Violation:
    entry: MemoryEntry
    rejected: str


def _tokens_with_spans(text: str) -> list[tuple[str, int, int]]:
    """Normalized tokens of ``text``, each carrying the original [start, end) span.
    One original token may expand to several normalized tokens (NFKC); they all
    share the original span, which is what the splice needs."""
    out: list[tuple[str, int, int]] = []
    for m in _TOKEN_RE.finditer(text):
        for sub in normalize(m.group(0)).split():
            out.append((sub, m.start(), m.end()))
    return out


def find_span(text: str, needle: str) -> tuple[int, int] | None:
    """Locate ``needle`` in ``text`` under normalized-token comparison and return the
    ORIGINAL character span covering it, or None. This is the replacement-side twin
    of ``_norm.contains_span`` — same tokenization, same verdict."""
    hay = _tokens_with_spans(text)
    ndl = tokens(needle)
    if not ndl or len(ndl) > len(hay):
        return None
    for i in range(len(hay) - len(ndl) + 1):
        if [t[0] for t in hay[i : i + len(ndl)]] == ndl:
            return hay[i][1], hay[i + len(ndl) - 1][2]
    return None


def replace_span(text: str, needle: str, replacement: str) -> str:
    """Replace every normalized-token occurrence of ``needle`` in ``text`` with
    ``replacement``, splicing at the original character offsets. Bounded, so a
    replacement that still normalizes to the needle cannot loop forever."""
    if not needle:
        return text
    out = text
    for _ in range(_MAX_REPLACEMENTS):
        span = find_span(out, needle)
        if span is None:
            return out
        start, end = span
        out = out[:start] + replacement + out[end:]
        if contains_span(replacement, needle):
            return out  # replacement contains the needle: one pass, then stop
    return out


def find_violations(output: str, corrections: list[MemoryEntry]) -> list[Violation]:
    viols: list[Violation] = []
    for e in corrections:
        for rej in e.rejected_translations:
            if rej and contains_span(output, rej):
                viols.append(Violation(entry=e, rejected=rej))
    return viols


def enforce(output: str, corrections: list[MemoryEntry]) -> tuple[str, list[str]]:
    """Deterministically remove any rejected rendering that survived generation.

    Returns (possibly-rewritten output, human-readable fix notes). Callers should
    re-run ``find_violations`` on the result: anything still present could not be
    rewritten and must degrade the result status instead of passing silently.
    """
    fixes: list[str] = []
    out = output
    for v in find_violations(out, corrections):
        replacement = v.entry.accepted_translation
        new_out = replace_span(out, v.rejected, replacement)
        if new_out != out:
            out = new_out
            fixes.append(
                f"blocked repeat of rejected rendering "
                f"'{v.rejected}' → '{replacement}' (entry #{v.entry.id})"
            )
    return out, fixes


def apply_invariant_locks(text: str, locks: list[MemoryEntry]) -> tuple[str, list[int]]:
    """Substitute invariant term locks (proper nouns, product names, codes) in ``text``
    using the same normalized-span machinery as the reject-guard. Longest source first
    to avoid partial shadowing."""
    applied: list[int] = []
    out = text
    for e in sorted(locks, key=lambda e: len(e.source_text), reverse=True):
        new_out = replace_span(out, e.source_text, e.accepted_translation)
        if new_out != out:
            out = new_out
            if e.id is not None:
                applied.append(e.id)
    return out, applied


def unsatisfied_locks(source: str, output: str,
                      locks: list[MemoryEntry]) -> list[MemoryEntry]:
    """Invariant locks whose source term occurs in ``source`` but whose accepted
    rendering is missing from ``output`` — i.e. the lock failed to take effect
    (the model paraphrased the term and no substitution point existed). The
    pipeline degrades the result instead of shipping the miss silently."""
    missed: list[MemoryEntry] = []
    for e in locks:
        if not e.invariant or not e.source_text or not e.accepted_translation:
            continue
        if not contains_span(source, e.source_text):
            continue
        if normalize(e.accepted_translation) not in normalize(output):
            missed.append(e)
    return missed
