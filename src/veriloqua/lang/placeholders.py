"""Extract and restore structural placeholders so the engine never translates or
reorders them: ``{name}``, ``{{var}}``, ``%s``/``%(x)s``, ICU ``{n, plural, ...}``
(outer braces), and HTML/XML tags.

The strategy is masking: replace each placeholder with an opaque sentinel the model
will pass through verbatim, then swap the originals back in afterwards.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Order matters: HTML tags first, then ICU/brace forms, then printf.
_PATTERNS = [
    re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^<>]*?)?/?>"),   # HTML/XML tags
    re.compile(r"\{\{[^{}]+\}\}"),                              # {{var}}
    re.compile(r"\{[^{}]+\}"),                                  # {name} / ICU {n,plural,...}
    re.compile(r"%(?:\([^)]+\))?[-+ #0]*\d*(?:\.\d+)?[a-zA-Z%]"),  # %s %(name)s %.2f
]

_SENTINEL = "VQPH{n}"  # private-use codepoints: highly unlikely in real text


@dataclass(slots=True)
class Masked:
    text: str
    mapping: dict[str, str]  # sentinel -> original placeholder


def extract_placeholders(text: str) -> Masked:
    mapping: dict[str, str] = {}
    counter = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal counter
        token = _SENTINEL.format(n=counter)
        mapping[token] = m.group(0)
        counter += 1
        return token

    out = text
    for pat in _PATTERNS:
        out = pat.sub(repl, out)
    return Masked(text=out, mapping=mapping)


def restore_placeholders(text: str, mapping: dict[str, str]) -> str:
    out = text
    for token, original in mapping.items():
        out = out.replace(token, original)
    return out


def placeholders_preserved(masked: Masked, output: str) -> bool:
    """True if every masked sentinel survived into ``output`` (none dropped)."""
    return all(token in output for token in masked.mapping)
