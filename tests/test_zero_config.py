"""Zero-config behavior: the agent-CLI LLM backend (no API key) and the auto-mode
fast fallback when no LLM is usable."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from veriloqua import Translator
from veriloqua.backends.fakes import FakeMTBackend
from veriloqua.backends.llm_cli import CliLLMBackend
from veriloqua.errors import BackendNotConfigured
from veriloqua.result import FastResult


def _fake_proc(returncode: int, stdout: str = "", stderr: str = ""):
    class R:
        pass

    r = R()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def test_cli_backend_parses_stdout(monkeypatch):
    payload = '{"translation":"hola","self_confidence":0.9,"risk_flags":[],"notes":[]}'
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_proc(0, stdout=payload))
    b = CliLLMBackend("claude_cli", binary="/fake/claude")
    r = b.complete("SYSTEM PROMPT", "USER PROMPT")
    assert "hola" in r.text


def test_cli_backend_feeds_prompt_on_stdin(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        return _fake_proc(0, stdout="{}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    CliLLMBackend("claude_cli", binary="/fake/claude").complete("SYS", "USR")
    assert captured["cmd"][:2] == ["/fake/claude", "-p"]     # print mode
    assert "--system-prompt" in captured["cmd"]              # system on its own channel
    assert "SYS" in captured["cmd"]                          # ...carrying the system prompt
    assert captured["input"] == "USR"                        # only the user text on stdin
    assert "--strict-mcp-config" in captured["cmd"]          # MCP disabled for speed


def test_cli_backend_error_surfaces(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_proc(1, stderr="not logged in"))
    b = CliLLMBackend("claude_cli", binary="/fake/claude")
    with pytest.raises(BackendNotConfigured):
        b.complete("s", "u")


def test_cli_backend_missing_binary(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(BackendNotConfigured):
        CliLLMBackend("claude_cli")


class _BoomLLM:
    name = "boom"
    model = "boom"

    def complete(self, *a, **k):
        raise BackendNotConfigured("agent CLI not logged in")


def test_auto_falls_back_to_fast_when_llm_unusable(tmp_config):
    # sentence-length input so the tier-0 MT short-circuit doesn't answer first —
    # this test is about the LLM-unavailable degradation path
    src = "how is the weather looking today"
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend({src: "qué tiempo hace hoy"}),
                    llm_backend=_BoomLLM())
    r = tr.translate(src, to="es", mode="auto")
    assert isinstance(r, FastResult)         # degraded gracefully, no crash
    assert r.text == "qué tiempo hace hoy"
    assert any("fast fallback" in w for w in r.warnings)
    tr.close()


def test_explicit_medium_surfaces_llm_error(tmp_config):
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(), llm_backend=_BoomLLM())
    with pytest.raises(BackendNotConfigured):
        # explicit mode: no silent downgrade (sentence-length input skips tier 0)
        tr.translate("how is the weather looking today", to="es", mode="medium")
    tr.close()
