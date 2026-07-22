"""Backend protocols. All I/O crosses one of these; the engine never talks to a
vendor SDK directly. Alternate backends are wired in explicitly via the
``Translator(...)`` constructor arguments."""

from __future__ import annotations

from veriloqua.backends.base import (
    LLMBackend,
    LLMResponse,
    SearchBackend,
    TranslationBackend,
    TranslationResult,
)

__all__ = [
    "TranslationBackend",
    "TranslationResult",
    "LLMBackend",
    "LLMResponse",
    "SearchBackend",
]
