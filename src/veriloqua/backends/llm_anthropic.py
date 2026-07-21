"""Anthropic LLM backend (reference default for medium/high + judge/back-translation).

Uses adaptive thinking + ``output_config.effort`` on current models, and degrades
gracefully on older SDKs/models that reject those parameters. Lazily imports the
``anthropic`` SDK so it is never a core dependency.
"""

from __future__ import annotations

from veriloqua.backends.base import LLMResponse
from veriloqua.errors import BackendNotConfigured

# input/output USD per 1M tokens — for the --max-cost job budget estimate.
_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def _cost(model: str, in_tok: int, out_tok: int) -> float:
    pin, pout = _PRICES.get(model, (0.0, 0.0))
    return (in_tok / 1_000_000) * pin + (out_tok / 1_000_000) * pout


class AnthropicBackend:
    name = "anthropic"

    def __init__(self, *, api_key: str | None = None, model: str = "claude-sonnet-5") -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise BackendNotConfigured(
                "the anthropic backend needs `pip install veriloqua[anthropic]`"
            ) from exc
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model = model

    def complete(self, system: str, user: str, *, model: str | None = None,
                 effort: str = "high", max_tokens: int = 4096) -> LLMResponse:
        mdl = model or self.model
        base = dict(
            model=mdl,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        resp = self._create(base, effort)
        text = "".join(
            getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text"
        )
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) if usage else 0
        out_tok = getattr(usage, "output_tokens", 0) if usage else 0
        return LLMResponse(
            text=text, input_tokens=in_tok, output_tokens=out_tok, model=mdl,
            cost_usd=_cost(mdl, in_tok, out_tok),
        )

    def _create(self, base: dict, effort: str):
        # Preferred path: adaptive thinking + effort (current models).
        try:
            return self._client.messages.create(
                **base,
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
            )
        except TypeError:
            pass  # SDK too old for these kwargs
        except Exception as exc:  # model rejected thinking/output_config (400) — retry plain
            if not _is_param_error(exc):
                raise
        return self._client.messages.create(**base)


def _is_param_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(t in msg for t in ("thinking", "output_config", "effort", "budget_tokens", "400"))
