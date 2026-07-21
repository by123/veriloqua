"""Backend protocols. All I/O crosses one of these; the engine never talks to a
vendor SDK directly."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class TranslationResult:
    text: str
    detected_src: str = "auto"
    engine: str = ""


@dataclass(slots=True)
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    cost_usd: float = 0.0


@runtime_checkable
class TranslationBackend(Protocol):
    name: str
    #: True if using this backend sends source text to a third party.
    third_party: bool

    def translate(self, text: str, src_lang: str, tgt_lang: str) -> TranslationResult: ...


@runtime_checkable
class LLMBackend(Protocol):
    name: str
    model: str

    def complete(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        effort: str = "high",
        max_tokens: int = 4096,
    ) -> LLMResponse: ...


@runtime_checkable
class EmbeddingBackend(Protocol):
    name: str
    model: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class SearchBackend(Protocol):
    name: str

    def search(self, query: str, *, max_results: int = 5) -> list[dict]: ...


@dataclass(slots=True)
class NullSearchBackend:
    """Default: no web research. High mode marks status=uncertain rather than
    fabricating a citation."""

    name: str = "null"
    results: list[dict] = field(default_factory=list)

    def search(self, query: str, *, max_results: int = 5) -> list[dict]:
        return []
