"""Deterministic, offline fakes for the test suite and for demos without keys.

FakeLLMBackend routes on the ``[[TASK:*]]`` markers that ``prompt_assembly`` emits,
so a test can drive the whole medium/high pipeline with zero network and fully
predictable output — including deliberately returning a *rejected* rendering to
prove the deterministic reject-guard rewrites it.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from veriloqua.backends.base import LLMResponse, TranslationResult
from veriloqua.engine import prompt_assembly as pa


class FakeMTBackend:
    name = "fake_mt"
    third_party = False

    def __init__(self, table: dict[str, str] | None = None) -> None:
        self.table = table or {}

    def translate(self, text: str, src_lang: str, tgt_lang: str) -> TranslationResult:
        out = self.table.get(text, f"[{tgt_lang}] {text}")
        return TranslationResult(text=out, detected_src=src_lang or "en", engine=self.name)


class FakeLLMBackend:
    name = "fake_llm"  # noqa: F811  (class attribute for the LLMBackend protocol)

    def __init__(
        self,
        model: str = "fake-model",
        *,
        respond: Callable[[str], str] | None = None,
    ) -> None:
        self.model = model
        # default: deterministic, marks the target so tests can assert on it
        self.respond = respond or (lambda s: f"TR::{s}")
        self.calls = 0

    def complete(self, system: str, user: str, *, model: str | None = None,
                 effort: str = "high", max_tokens: int = 4096) -> LLMResponse:
        self.calls += 1
        payload = self._route(system, user)
        text = json.dumps(payload, ensure_ascii=False)
        return LLMResponse(
            text=text,
            input_tokens=len(user) // 4,
            output_tokens=len(text) // 4,
            model=model or self.model,
        )

    def _route(self, system: str, user: str) -> dict:
        if pa.TASK_TRIAGE in user:
            src = pa.extract_source(user)
            # complexity "standard" → tests deterministically go through the Sonnet tier
            return {
                "detected_src": "en", "src_confidence": 0.99, "domain": "",
                "complexity": "standard", "is_slang": False, "has_ambiguity": False,
                "has_cultural": False, "src_wellformed": True, "proper_nouns": [],
                "translation": self.respond(src),
            }
        if pa.TASK_DEEP in user:
            src = pa.extract_source(user)
            return {
                "translation": self.respond(src), "chosen_reading": None, "alt_readings": [],
                "source_repair": "", "confidence": 0.85, "notes": [], "learned": None,
            }
        if pa.TASK_CROSSCHECK in user:
            # default fake: the audit agrees (tests that need a divergence subclass this)
            return {"source_reading": "", "translation_reading": "", "source_intelligible": True,
                    "agree": True, "divergence": ""}
        if pa.TASK_JUDGE in user:
            # score five indices; candidate 0 wins by min-dimension
            return {
                "scores": [
                    {"candidate_index": i, "adequacy": 5 - i, "fluency": 5,
                     "register": 5 - min(i, 1), "terminology": 5, "culture": 5, "issues": []}
                    for i in range(5)
                ]
            }
        if pa.TASK_BACKTRANSLATE in user:
            body = _between(user, pa.UNTRUSTED_OPEN, pa.UNTRUSTED_CLOSE)
            return {"back_translation": body.replace("[to_backtranslate]", "").strip()}
        src = pa.extract_source(user)
        primary = self.respond(src)
        if pa.TASK_CANDIDATES in user:
            return {
                "candidates": [
                    {"text": primary, "strategy": "faithful"},
                    {"text": primary, "strategy": "localized"},
                ]
            }
        # translate (+ self-review shape; extra keys are harmless for legacy callers)
        return {
            "translation": primary,
            "self_confidence": 0.72,
            "ambiguity": False,
            "cultural": False,
            "risk_flags": [],
            "chosen_reading": None,
            "notes": [],
        }


def _between(text: str, open_tok: str, close_tok: str) -> str:
    if open_tok in text and close_tok in text:
        return text.split(open_tok, 1)[1].split(close_tok, 1)[0]
    return text
