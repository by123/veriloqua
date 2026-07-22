"""PII minimization.

Honest scope: local-only storage, minimal spans, and hashing are the real
mechanisms. The regex scrubber removes obviously-sensitive tokens (emails, long
digit runs, key-shaped strings). We do NOT claim regex removes PII from free
prose; there is no NER scrubbing.
"""

from __future__ import annotations

import hashlib
import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_LONG_NUM = re.compile(r"\b\d[\d\s-]{7,}\d\b")          # phone / card / account-ish
_KEYISH = re.compile(r"\b(?:sk|pk|api|key|token|ghp|xox[a-z])[-_][A-Za-z0-9]{12,}\b", re.I)


def scrub_text(text: str) -> str:
    """Redact clearly-sensitive tokens. Deterministic; leaves prose intact."""
    text = _KEYISH.sub("[REDACTED_KEY]", text)
    text = _EMAIL.sub("[REDACTED_EMAIL]", text)
    text = _LONG_NUM.sub("[REDACTED_NUM]", text)
    return text


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
