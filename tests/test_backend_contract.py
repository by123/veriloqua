"""Backend contract tests: every shipped backend (and the test fakes) must expose the
same shapes the pipeline relies on — without needing optional SDKs installed or any
network. Signature drift in any adapter fails here, not in production."""

from __future__ import annotations

import inspect

import pytest

from veriloqua.backends import fakes, google_free

MT_CLASSES = [google_free.GoogleFreeBackend, fakes.FakeMTBackend]


def _optional(module: str, cls: str):
    try:
        mod = __import__(module, fromlist=[cls])
        return getattr(mod, cls)
    except Exception:
        return None


LLM_CLASSES = [c for c in (
    fakes.FakeLLMBackend,
    _optional("veriloqua.backends.llm_cli", "CliLLMBackend"),
) if c is not None]


@pytest.mark.parametrize("cls", MT_CLASSES)
def test_mt_backend_contract(cls):
    assert isinstance(getattr(cls, "name", None), str) and cls.name
    assert isinstance(getattr(cls, "third_party", None), bool)
    sig = inspect.signature(cls.translate)
    assert list(sig.parameters)[:4] == ["self", "text", "src_lang", "tgt_lang"]


@pytest.mark.parametrize("cls", LLM_CLASSES)
def test_llm_backend_contract(cls):
    assert getattr(cls, "name", None), cls
    sig = inspect.signature(cls.complete)
    params = sig.parameters
    assert list(params)[:3] == ["self", "system", "user"]
    for kw in ("model", "effort", "max_tokens"):
        assert kw in params, f"{cls.__name__}.complete missing keyword '{kw}'"
        assert params[kw].kind is inspect.Parameter.KEYWORD_ONLY


def test_mt_result_shape():
    res = fakes.FakeMTBackend({"hi": "hola"}).translate("hi", "en", "es")
    for attr in ("text", "detected_src", "engine"):
        assert hasattr(res, attr)


def test_llm_response_shape():
    resp = fakes.FakeLLMBackend().complete("system", "user")
    for attr in ("text", "input_tokens", "output_tokens", "model"):
        assert hasattr(resp, attr)
