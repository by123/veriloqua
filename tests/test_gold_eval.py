"""The gold suite ACTUALLY evaluates translation accuracy: it translates every gold
segment through the engine and scores it with sentence chrF. The report says exactly
what was computed (and what wasn't) — no phantom "is tracked" claims."""

from __future__ import annotations

import json

import pytest

from veriloqua import cli
from veriloqua.eval.gold import chrf, run_gold
from veriloqua.eval.replay import run_eval

GOLD = [
    {"source": "Please confirm your booking.", "reference": "请确认您的预订。", "domain": "hospitality"},
    {"source": "This may cause drowsiness.", "reference": "此药可能引起嗜睡。", "domain": "medical"},
]


def test_chrf_perfect_match_scores_100():
    assert chrf("请确认您的预订。", "请确认您的预订。") == pytest.approx(100.0)


def test_chrf_orders_quality():
    ref = "请在周五前确认您的预订。"
    close = chrf("请在星期五前确认您的预订。", ref)
    far = chrf("完全无关的文本内容啊", ref)
    echo = chrf("Please confirm your booking by Friday.", ref)
    assert close > far
    assert far >= echo or far < 15          # both junk hypotheses score very low
    assert close > 50


def test_run_gold_translates_and_scores_every_segment():
    lookup = {c["source"]: c["reference"] for c in GOLD}
    report = run_gold(translate_fn=lambda s, d: lookup[s], cases=GOLD)

    assert report["ran"] is True
    assert report["scored"] == len(GOLD)
    assert report["chrf_mean"] == pytest.approx(100.0)
    assert len(report["per_segment"]) == len(GOLD)
    assert all(seg["hypothesis"] == seg["reference"] for seg in report["per_segment"])
    assert "not implemented" in report["note"]       # honest about COMET/judge


def test_run_gold_records_failures_instead_of_hiding_them():
    def boom(source, domain):
        if "drowsiness" in source:
            raise RuntimeError("backend down")
        return "请确认您的预订。"

    report = run_gold(translate_fn=boom, cases=GOLD)
    assert report["scored"] == 1
    assert len(report["errors"]) == 1
    assert "backend down" in report["errors"][0]


def test_suite_gold_runs_translations_via_run_eval():
    calls: list[str] = []

    def fake_translate(source, domain):
        calls.append(source)
        return "某个译文"

    report = run_eval(suite="gold", translate_fn=fake_translate)
    assert report["tier2_gold"]["ran"] is True
    assert len(calls) == report["tier2_gold"]["segments"] > 0
    assert report["tier2_gold"]["chrf_mean"] is not None


def test_suite_all_does_not_run_translations():
    def must_not_be_called(source, domain):  # pragma: no cover - the point is it never runs
        raise AssertionError("suite 'all' must not translate")

    report = run_eval(suite="all", translate_fn=must_not_be_called)
    assert report["tier2_gold"]["ran"] is False
    assert "is tracked" not in json.dumps(report)    # the phantom claim is gone


def test_cli_eval_gold_end_to_end(tmp_path, monkeypatch, capsys):
    """`vq eval --suite gold` really pushes segments through Translator.translate."""
    from veriloqua.result import FastResult, Status

    class _StubTranslator:
        def __init__(self, **_kw):
            from veriloqua.config import load_config
            self.config = load_config()
            self.calls = 0

        def translate(self, text, **kw):
            assert kw.get("mode") == "fast"
            _StubTranslator.total_calls += 1
            return FastResult(text="占位译文", engine="stub", detected_src="en",
                              confidence=0.9, status=Status.OK, request_id="x")

        def close(self):
            pass

    _StubTranslator.total_calls = 0
    import veriloqua
    monkeypatch.setenv("VERILOQUA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(veriloqua, "Translator", _StubTranslator)

    rc = cli.main(["eval", "--suite", "gold", "--mode", "fast"])
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert out["tier2_gold"]["ran"] is True
    assert _StubTranslator.total_calls == out["tier2_gold"]["segments"] > 0
