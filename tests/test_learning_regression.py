"""The flagship acceptance suite: the engine never repeats a corrected mistake in
its context, never over-applies it out of context, and learns with zero LLM keys."""

from __future__ import annotations

from veriloqua import Translator
from veriloqua.backends.fakes import FakeLLMBackend, FakeMTBackend
from veriloqua.memory.base import MemoryStore
from veriloqua.memory.store_sqlite import SqliteMemoryStore

BAD, GOOD = "打断一条腿", "祝你好运"


def _tr(cfg, respond=None):
    return Translator(config=cfg, mt_backend=FakeMTBackend(),
                      llm_backend=FakeLLMBackend(respond=respond))


def test_never_repeat_after_correction(tmp_config):
    # the fake model stubbornly keeps producing the bad rendering
    respond = lambda s: BAD if "break a leg" in s else f"TR::{s}"
    tr = _tr(tmp_config, respond)
    r1 = tr.translate("break a leg", to="zh", mode="medium", domain="casual-chat")
    tr.correct(r1.request_id, GOOD, scope="user")
    r2 = tr.translate("break a leg", to="zh", mode="medium", domain="casual-chat")
    assert BAD not in r2.text          # the corrected mistake is impossible now
    assert GOOD in r2.text             # deterministic guard supplied the accepted rendering
    tr.close()


def test_never_repeat_embedded_in_larger_text(tmp_config):
    respond = lambda s: f"prefix {BAD} suffix"
    tr = _tr(tmp_config, respond)
    tr.correct_text(source="break a leg", our_output=BAD, corrected=GOOD,
                    src="en", tgt="zh", domain="casual-chat")
    r = tr.translate("please break a leg tonight", to="zh", mode="medium", domain="casual-chat")
    assert BAD not in r.text
    tr.close()


def test_overfit_guard_out_of_scope(tmp_config):
    # a fix learned for casual-chat must NOT fire in a contradicted (legal) domain
    respond = lambda s: BAD
    tr = _tr(tmp_config, respond)
    tr.correct_text(source="break a leg", our_output=BAD, corrected=GOOD,
                    src="en", tgt="zh", domain="casual-chat")
    r = tr.translate("break a leg", to="zh", mode="medium", domain="legal")
    assert r.text == BAD               # unchanged: the correction did not apply here
    tr.close()


def test_zero_llm_correction_path(tmp_config):
    # no LLM configured — correction + guarantee still work
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend())
    e = tr.correct_text(source="the cloud", our_output="云朵", corrected="云端",
                        src="en", tgt="zh", domain="tech")
    assert e.error_type == "unspecified"          # no classifier ran
    assert "云朵" in e.rejected_translations       # our output blocked
    assert e.status.value == "active"
    tr.close()


def test_recorrection_supersedes_one_active_row(tmp_config):
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend())
    tr.correct_text(source="foo widget", our_output="bar", corrected="baz",
                    src="en", tgt="zh")
    tr.correct_text(source="foo widget", our_output="baz", corrected="qux",
                    src="en", tgt="zh")
    active = [e for e in tr._store.all_entries()
              if e.source_norm == "foo widget" and e.status.value == "active"]
    assert len(active) == 1
    assert active[0].accepted_translation == "qux"
    assert "baz" in active[0].rejected_translations  # the superseded preference is blocked
    tr.close()


def test_store_satisfies_protocol():
    store = SqliteMemoryStore(":memory:")
    assert isinstance(store, MemoryStore)
    store.close()
