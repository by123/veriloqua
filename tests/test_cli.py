"""CLI behavior end-to-end through ``cli.main(argv)``: default-subcommand injection,
glossary/memory round-trips against an isolated data dir, translate output shape,
and the removed-mode error surface."""

from __future__ import annotations

import pytest

from veriloqua import cli
from veriloqua.result import FastResult, Status


@pytest.fixture
def iso_env(tmp_path, monkeypatch):
    """Isolate the CLI from the real user: own data dir, no third-party calls."""
    monkeypatch.setenv("VERILOQUA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VERILOQUA_QUIET", "1")
    return tmp_path


class _StubTranslator:
    """Stands in for Translator so translate CLI tests never touch the network."""

    def __init__(self, **_overrides):
        from veriloqua.config import load_config
        self.config = load_config()

    def translate(self, text, **_kw):
        return FastResult(text=f"[译]{text}", engine="stub", detected_src="en",
                          confidence=0.9, status=Status.OK, request_id="stubreq")

    def close(self):
        pass


def test_bare_text_defaults_to_translate_subcommand(iso_env, monkeypatch, capsys):
    import veriloqua
    monkeypatch.setattr(veriloqua, "Translator", _StubTranslator)
    rc = cli.main(["hello", "world", "--to", "zh", "--mode", "fast"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[译]hello world" in out


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "veriloqua" in capsys.readouterr().out


def test_removed_mode_is_rejected_by_the_parser(iso_env, capsys):
    with pytest.raises(SystemExit):
        cli.main(["hello", "--mode", "high"])
    assert "invalid choice" in capsys.readouterr().err


def test_glossary_add_list_rm_roundtrip(iso_env, capsys):
    rc = cli.main(["glossary", "add", "New York", "纽约",
                   "--from", "en", "--to", "zh", "--invariant"])
    assert rc == 0
    first = capsys.readouterr().out
    assert "纽约" in first and "#" in first

    rc = cli.main(["glossary", "list"])
    assert rc == 0
    listed = capsys.readouterr().out
    assert "New York" in listed and "[invariant]" in listed

    entry_id = listed.split("#", 1)[1].split()[0]
    rc = cli.main(["glossary", "rm", entry_id])
    assert rc == 0
    assert "removed" in capsys.readouterr().out


def test_memory_stats_reports_db(iso_env, capsys):
    rc = cli.main(["memory", "stats"])
    assert rc == 0
    assert "total_entries" in capsys.readouterr().out


def test_backends_listing_never_crashes(capsys):
    assert cli.main(["backends"]) == 0
    assert "translation/google_free" in capsys.readouterr().out


def test_errors_are_one_clean_line(iso_env, capsys):
    rc = cli.main(["correct", "no-such-request-id", "译文"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("vq: error:")
    assert "Traceback" not in err
