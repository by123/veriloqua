"""Tier 0 of auto mode: a trivial short input ("你好", "thank you") answers via keyless MT
with ZERO LLM calls. Anything doubtful — negation, memory involvement, a domain brief,
placeholders, sentence-shaped text, or a dubious MT output (echo / wrong script /
validator warning) — falls through to the LLM cascade unchanged."""

from __future__ import annotations

from veriloqua import Translator
from veriloqua.backends.fakes import FakeLLMBackend, FakeMTBackend


def _tr(tmp_config, table: dict[str, str] | None = None) -> tuple[Translator, FakeLLMBackend]:
    llm = FakeLLMBackend()
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(table or {}), llm_backend=llm)
    return tr, llm


def test_trivial_short_input_answers_via_mt_with_zero_llm_calls(tmp_config):
    tr, llm = _tr(tmp_config, {"你好": "Hello"})
    r = tr.translate("你好", to="en", mode="auto")
    tr.close()

    assert r.text == "Hello"
    assert r.trace["tier"] == "mt"
    assert r.trace["need_deep"] is False
    assert llm.calls == 0                      # the whole point: no tokens spent


def test_negation_is_never_shortcircuited(tmp_config):
    tr, llm = _tr(tmp_config, {"不行": "No way"})
    r = tr.translate("不行", to="en", mode="auto")
    tr.close()

    assert r.trace["tier"] != "mt"
    assert llm.calls > 0


def test_sentence_shaped_input_goes_to_cascade(tmp_config):
    tr, llm = _tr(tmp_config)
    r = tr.translate("这句话比较长,应当走完整的级联流程", to="en", mode="auto")
    tr.close()

    assert r.trace["tier"] != "mt"
    assert llm.calls > 0


def test_domain_brief_disables_the_shortcircuit(tmp_config):
    # an inline context brief signals the caller wants app-aware nuance — MT can't use it
    tr, llm = _tr(tmp_config, {"你好": "Hello"})
    r = tr.translate("你好", to="en", mode="auto", context="app brief")
    tr.close()

    assert r.trace["tier"] != "mt"
    assert llm.calls > 0


def test_echoed_mt_output_falls_through(tmp_config):
    # MT returning the input unchanged (coined word / untranslatable) is not an answer
    tr, llm = _tr(tmp_config, {"hola": "hola"})
    r = tr.translate("hola", to="en", source="es", mode="auto")
    tr.close()

    assert r.trace["tier"] != "mt"
    assert llm.calls > 0


def test_wrong_script_mt_output_falls_through(tmp_config):
    # target is Chinese but MT produced Latin text → not trusted, cascade decides
    tr, llm = _tr(tmp_config, {"hi": "hi there"})
    r = tr.translate("hi", to="zh", source="en", mode="auto")
    tr.close()

    assert r.trace["tier"] != "mt"
    assert llm.calls > 0


def test_exact_memory_involvement_goes_to_cascade(tmp_config):
    tr, llm = _tr(tmp_config, {"hola": "hi"})
    tr.add_term(source="hola", target="你好", src="es", tgt="en")
    r = tr.translate("hola", to="en", source="es", mode="auto")
    tr.close()

    assert r.trace["tier"] != "mt"
    assert llm.calls > 0


def test_config_flag_disables_tier0(tmp_config):
    tmp_config.auto_mt_shortcircuit = False
    tr, llm = _tr(tmp_config, {"你好": "Hello"})
    r = tr.translate("你好", to="en", mode="auto")
    tr.close()

    assert r.trace["tier"] != "mt"
    assert llm.calls > 0
