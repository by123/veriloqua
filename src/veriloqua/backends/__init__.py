"""Backend protocols + a tiny registry with entry-point discovery.

Third parties register adapters under these entry-point groups:
``veriloqua.translation_backends``, ``veriloqua.llm_backends``,
``veriloqua.embedding_backends``, ``veriloqua.search_backends``,
``veriloqua.memory_stores``.
"""

from __future__ import annotations

from veriloqua.backends.base import (
    EmbeddingBackend,
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
    "EmbeddingBackend",
    "SearchBackend",
]
