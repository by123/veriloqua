"""OpenAI LLM backend (alternate translator + candidate judge/back-translation model).

Lazily imports the ``openai`` SDK so it is never a core dependency. Included so the
independent judge / blind back-translation can run on a genuinely different vendor
model when the user has one configured.
"""

from __future__ import annotations

from veriloqua.backends.base import LLMResponse
from veriloqua.errors import BackendNotConfigured


class OpenAIBackend:
    name = "openai"

    def __init__(self, *, api_key: str | None = None, model: str = "gpt-4o-mini") -> None:
        try:
            import openai
        except ImportError as exc:  # pragma: no cover
            raise BackendNotConfigured(
                "the openai backend needs `pip install veriloqua[openai]`"
            ) from exc
        self._client = openai.OpenAI(api_key=api_key) if api_key else openai.OpenAI()
        self.model = model

    def complete(self, system: str, user: str, *, model: str | None = None,
                 effort: str = "high", max_tokens: int = 4096) -> LLMResponse:
        mdl = model or self.model
        resp = self._client.chat.completions.create(
            model=mdl,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
        out_tok = getattr(usage, "completion_tokens", 0) if usage else 0
        return LLMResponse(text=text, input_tokens=in_tok, output_tokens=out_tok, model=mdl)
