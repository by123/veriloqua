"""Memory retrieval scoping: detect-then-retrieve, real user/project identities on
scoped rows, and one-row-per-key dedup across scopes (user > project > global)."""

from __future__ import annotations

from veriloqua import Translator
from veriloqua.backends.fakes import FakeLLMBackend, FakeMTBackend
from veriloqua.memory import retrieval
from veriloqua.memory.records import Kind, MemoryEntry, Scope

LONG = "please break a leg tonight before the big show starts"


def _entry(source: str, accepted: str, scope: Scope, *, src="en", tgt="zh") -> MemoryEntry:
    return MemoryEntry(kind=Kind.CORRECTION, src_lang=src, tgt_lang=tgt,
                       source_text=source, source_norm=source.lower(),
                       accepted_translation=accepted, scope=scope)


def test_scope_dedup_keeps_only_the_winning_row():
    entries = [
        _entry("break a leg", "全局版", Scope.GLOBAL),
        _entry("break a leg", "项目版", Scope.PROJECT),
        _entry("break a leg", "用户版", Scope.USER),
    ]
    ret = retrieval.retrieve(entries, "go break a leg tonight", {"domain": ""})
    assert len(ret.exact_corrections) == 1
    assert ret.exact_corrections[0].accepted_translation == "用户版"


def test_user_scoped_rows_are_isolated_by_user_id(tmp_config):
    tmp_config.user_id = "alice"
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(),
                    llm_backend=FakeLLMBackend())
    tr.correct_text(source="break a leg", our_output="断腿", corrected="祝你演出成功",
                    src="en", tgt="zh")
    store = tr._store

    alice = store.active_entries("en", "zh", ["user"], user_id="alice", project_id="")
    bob = store.active_entries("en", "zh", ["user"], user_id="bob", project_id="")
    assert len(alice) == 1 and alice[0].scope_id == "alice"
    assert bob == []                     # another user never sees alice's rows
    tr.close()


def test_legacy_rows_without_scope_id_still_load(tmp_config):
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(),
                    llm_backend=FakeLLMBackend())
    store = tr._store
    store.add_entry(_entry("old term", "旧译", Scope.USER))   # scope_id="" (legacy)
    got = store.active_entries("en", "zh", ["user"], user_id="anyone", project_id="")
    assert len(got) == 1
    tr.close()


def test_detected_language_gates_which_entries_inject(tmp_config):
    # A Russian-pair correction whose span happens to appear in an English text must
    # NOT inject once triage has detected the text is English (src was 'auto').
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(),
                    llm_backend=FakeLLMBackend())   # fake triage detects "en"
    tr.correct_text(source="break a leg", our_output="x", corrected="ru 专用译法",
                    src="ru", tgt="zh")
    r = tr.translate(LONG, to="zh", mode="auto")    # source="auto"
    tr.close()

    assert not any(h.source == "break a leg" for h in r.memory_hits)


def test_same_language_entry_still_injects_after_detection(tmp_config):
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(),
                    llm_backend=FakeLLMBackend())
    tr.correct_text(source="break a leg", our_output="断腿", corrected="祝你演出成功",
                    src="en", tgt="zh")
    r = tr.translate(LONG, to="zh", mode="auto")
    tr.close()

    assert any(h.source == "break a leg" for h in r.memory_hits)
