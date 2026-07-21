"""Shared, deterministic text normalization.

The non-repeat guarantee (see ``memory/guard.py``) depends on *stable* string
comparison. All exact-match keys and reject-guard checks route through the
functions here so the verdict is byte-for-byte reproducible on any machine,
independent of which optional fuzzy backend happens to be installed.
"""

from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"\s+")
# Strip punctuation that never carries lexical identity for matching purposes.
# We keep CJK/letters/digits and collapse everything else to a space.
_PUNCT = re.compile(r"[^\w一-鿿぀-ヿ가-힣]+", re.UNICODE)


def normalize(text: str) -> str:
    """Normalize for exact-match keying: NFKC, casefold, punctuation → space, collapse.

    Deterministic and locale-independent. Two strings that a human would call
    "the same term" should normalize identically; two genuinely different terms
    should not collide.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.casefold()
    text = _PUNCT.sub(" ", text)
    text = _WS.sub(" ", text)
    return text.strip()


def tokens(text: str) -> list[str]:
    """Normalized whitespace tokens."""
    n = normalize(text)
    return n.split(" ") if n else []


def contains_span(haystack: str, needle: str) -> bool:
    """True if ``needle`` occurs in ``haystack`` as a contiguous *token* subsequence.

    Token-boundary aware, so ``"cat"`` does not match inside ``"category"`` while
    ``"new york"`` matches inside ``"i love new york city"``. This is the primitive
    the deterministic span-scan is built on.
    """
    h = tokens(haystack)
    n = tokens(needle)
    if not n or len(n) > len(h):
        return False
    first = n[0]
    for i in range(len(h) - len(n) + 1):
        if h[i] == first and h[i : i + len(n)] == n:
            return True
    return False
