"""MCP tool logic (offline). The memory tools make no model/network call, so the
full add_term → lookup → correct → lookup loop is deterministic."""

from __future__ import annotations

import pytest

import veriloqua.mcp_server as m


def test_mcp_memory_tools_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("VERILOQUA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VERILOQUA_CONFIG", "/dev/null")

    added = m._tool_add_term(source="the cloud", target="云端", src="en", tgt="zh", domain="tech")
    assert added["added"] and added["entry_id"]

    look = m._tool_lookup_glossary("please deploy to the cloud", src="en", tgt="zh", domain="tech")
    assert look["found"]
    assert any(t["target"] == "云端" for t in look["term_locks"])

    corrected = m._tool_correct(corrected="祝你好运", source="break a leg",
                                our_output="打断一条腿", src="en", tgt="zh", domain="casual-chat")
    assert corrected["learned"]
    assert "打断一条腿" in corrected["will_never_repeat"]

    look2 = m._tool_lookup_glossary("break a leg", src="en", tgt="zh", domain="casual-chat")
    assert any("打断一条腿" in c["never"] for c in look2["corrections"])


def test_mcp_correct_requires_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("VERILOQUA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VERILOQUA_CONFIG", "/dev/null")
    r = m._tool_correct(corrected="x")  # no request_id, no source/our_output
    assert "error" in r


def test_build_server_registers_tools():
    pytest.importorskip("mcp")  # skip if the MCP SDK isn't installed
    server = m.build_server()
    assert server is not None  # registration of all four vq_* tools succeeded without error
