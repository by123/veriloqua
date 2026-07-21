"""A shaky source-language identification must not be laundered into a confident
translation. When triage reports low `src_confidence` for an `auto` source — a bare
name, brand, or coined word like "Veriloqua" — the engine refuses the simple
short-circuit, escalates, and marks the result uncertain instead of guessing."""

from __future__ import annotations

from veriloqua import Translator
from veriloqua.backends.fakes import FakeLLMBackend, FakeMTBackend
from veriloqua.engine import prompt_assembly as pa


class _ShakyTriageLLM(FakeLLMBackend):
    """Triage is *sure* it's a simple English word (and even offers a bogus literal),
    but not sure of the language — exactly the trap `vq "Veriloqua"` hit."""

    def __init__(self, src_confidence: float) -> None:
        super().__init__()
        self._src_confidence = src_confidence

    def _route(self, system: str, user: str) -> dict:
        if pa.TASK_TRIAGE in user:
            return {
                "detected_src": "en", "src_confidence": self._src_confidence, "domain": "",
                "complexity": "simple", "is_slang": False, "has_ambiguity": False,
                "has_cultural": False, "proper_nouns": ["Veriloqua"],
                "translation": "薇瑞洛夸",  # the short-circuit would have committed to this
            }
        return super()._route(system, user)


def test_low_confidence_language_is_not_short_circuited(tmp_config):
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(),
                    llm_backend=_ShakyTriageLLM(src_confidence=0.2))
    r = tr.translate("Veriloqua", to="zh", mode="auto")   # source defaults to "auto"
    assert r.status.value == "uncertain"
    assert r.trace.get("src_confidence") == 0.2
    assert r.trace.get("tier") != "haiku"                 # refused the simple short-circuit
    assert "薇瑞洛夸" not in r.text                          # never committed the bogus literal
    assert any("源语言判断存疑" in n for n in r.notes)
    tr.close()


def test_confident_language_still_short_circuits(tmp_config):
    # same shape, but high confidence — the fast path is allowed and the note is absent.
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(),
                    llm_backend=_ShakyTriageLLM(src_confidence=0.95))
    r = tr.translate("Veriloqua", to="zh", mode="auto")
    assert r.trace.get("tier") == "haiku"
    assert r.text == "薇瑞洛夸"
    assert not any("源语言判断存疑" in n for n in r.notes)
    tr.close()


def test_explicit_source_is_trusted_even_if_confidence_low(tmp_config):
    # the caller pinned the source; a low triage confidence must not override their choice.
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(),
                    llm_backend=_ShakyTriageLLM(src_confidence=0.1))
    r = tr.translate("Veriloqua", to="zh", source="en", mode="auto")
    assert not any("源语言判断存疑" in n for n in r.notes)
    tr.close()


class _SimpleImperativeLookalikeLLM(FakeLLMBackend):
    """Haiku is confident the sentence is 'simple' and hands back the WRONG imperative
    reading — the trap `vq "No descarga los archivos"` hit when it short-circuited."""

    def _route(self, system: str, user: str) -> dict:
        if pa.TASK_TRIAGE in user:
            return {
                "detected_src": "es", "src_confidence": 0.98, "domain": "",
                "complexity": "simple", "is_slang": False, "has_ambiguity": False,
                "has_cultural": False, "proper_nouns": [],
                "translation": "不要下载这些文件",  # wrong: this is the imperative, not indicative
            }
        return super()._route(system, user)


def test_negation_forbids_the_fast_short_circuit(tmp_config):
    # a negated source is a mood/scope landmine — it must reach the grammar-aware translator,
    # never be committed straight from Haiku's one-shot triage translation.
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(),
                    llm_backend=_SimpleImperativeLookalikeLLM())
    r = tr.translate("No descarga los archivos", to="zh", mode="auto")
    assert r.trace.get("tier") != "haiku"          # did NOT take the short-circuit
    assert r.text != "不要下载这些文件"               # Haiku's bad imperative was not committed
    tr.close()


class _WrongLanguageShortCircuitLLM(FakeLLMBackend):
    """Haiku calls it simple but translates to the WRONG language (English, not the
    requested Chinese) — the reason `vq` printed 'Do not download the files'."""

    def _route(self, system: str, user: str) -> dict:
        if pa.TASK_TRIAGE in user:
            return {
                "detected_src": "es", "src_confidence": 0.98, "domain": "",
                "complexity": "simple", "is_slang": False, "has_ambiguity": False,
                "has_cultural": False, "proper_nouns": [],
                "translation": "Download the files",  # English, but target is zh
            }
        return super()._route(system, user)


def test_wrong_target_script_forbids_the_fast_short_circuit(tmp_config):
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(),
                    llm_backend=_WrongLanguageShortCircuitLLM())
    r = tr.translate("Descarga los archivos", to="zh", mode="auto")   # no negation here
    assert r.trace.get("tier") != "haiku"          # rejected: output wasn't in the CJK target
    assert r.text != "Download the files"
    tr.close()
