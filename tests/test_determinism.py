"""The hard guarantee and its CI gate must not depend on which fuzzy backend is
installed. The exact-match tier uses no fuzzy library at all, so a Tier-1 verdict is
invariant by construction; we assert it, and assert the partial-unique-index invariant."""

from __future__ import annotations

import sqlite3

import pytest

from veriloqua.eval.replay import run_eval
from veriloqua.memory.records import Kind, MemoryEntry
from veriloqua.memory.store_sqlite import SqliteMemoryStore


def test_tier1_gate_passes_on_seed_fixtures():
    report = run_eval(suite="replay")
    assert report["tier1_passed"], report["tier1_failures"]
    assert report["cases"] >= 5


def test_tier1_verdict_independent_of_fuzzy_backend(monkeypatch):
    # exact_scan never calls the fuzzy backend; forcing difflib must not change the verdict.
    import veriloqua.memory.retrieval as retr

    monkeypatch.setattr(retr, "FUZZY_BACKEND", "difflib-forced")
    report_a = run_eval(suite="replay")
    monkeypatch.setattr(retr, "FUZZY_BACKEND", "rapidfuzz-forced")
    report_b = run_eval(suite="replay")
    assert report_a["tier1_passed"] == report_b["tier1_passed"] is True
    assert report_a["guarantee_passed"] == report_b["guarantee_passed"]


def test_partial_unique_index_one_active_row():
    store = SqliteMemoryStore(":memory:")
    e1 = MemoryEntry(kind=Kind.TERM_LOCK, src_lang="en", tgt_lang="zh",
                     source_text="x", source_norm="x", accepted_translation="a")
    store.add_entry(e1)
    e2 = MemoryEntry(kind=Kind.TERM_LOCK, src_lang="en", tgt_lang="zh",
                     source_text="x", source_norm="x", accepted_translation="b")
    with pytest.raises(sqlite3.IntegrityError):
        store.add_entry(e2)  # a second ACTIVE row for the same key is rejected
    store.close()
