from __future__ import annotations

from veriloqua import Translator
from veriloqua.backends.fakes import FakeLLMBackend, FakeMTBackend
from veriloqua.result import FastResult


def test_high_mode_llm_call_cap(tmp_config):
    llm = FakeLLMBackend(respond=lambda s: f"TR::{s}")
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(), llm_backend=llm)
    tr.translate("hello world", to="zh", mode="auto", domain="general")
    assert llm.calls <= 12  # re-derived per-segment ceiling
    tr.close()


def test_job_budget_fail_closed_to_fast(tmp_config):
    tmp_config.max_calls = 1  # job aggregate ceiling → high can't finish
    llm = FakeLLMBackend(respond=lambda s: f"TR::{s}")
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(), llm_backend=llm)
    r = tr.translate("hello world", to="zh", mode="auto")
    # fell back to the keyless fast path rather than crashing or overspending
    assert isinstance(r, FastResult)
    assert any("budget exhausted" in w for w in r.warnings)
    tr.close()
