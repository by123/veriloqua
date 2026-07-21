"""User content must never be written into the packaged fixtures tree; user replay
cases land in the XDG user data dir."""

from __future__ import annotations

from pathlib import Path

import veriloqua.eval as ev
from veriloqua import Translator
from veriloqua.backends.fakes import FakeMTBackend


def test_user_replay_isolated_from_package(tmp_config):
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend())
    tr.correct_text(source="my private phrase alpha", our_output="x", corrected="y",
                    src="en", tgt="zh", domain="general")
    tr.close()

    user_file = tmp_config.replay_dir / "corrections.jsonl"
    assert user_file.exists()
    assert "my private phrase alpha" in user_file.read_text(encoding="utf-8")

    pkg_corrections = Path(ev.__file__).parent / "fixtures" / "replay" / "corrections.jsonl"
    assert "my private phrase alpha" not in pkg_corrections.read_text(encoding="utf-8")


def test_packaged_fixtures_present():
    base = Path(ev.__file__).parent / "fixtures"
    assert (base / "replay" / "corrections.jsonl").is_file()
    assert (base / "en_zh_gold.jsonl").is_file()
    assert (base / "seed_termbase.jsonl").is_file()
