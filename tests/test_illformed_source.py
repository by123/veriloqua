"""A malformed source (typos, dropped words, transcription noise) must not be laundered
into a confident, fluent-looking translation. Language ID can be certain while the string
itself is broken — a faithful rendering of garbage is still garbage. The engine forfeits
the fast path, escalates to the repair-capable deep tier, marks the result uncertain, warns
that the SOURCE is the problem, and surfaces the most likely intended meaning.

This is the `vq "Ese que no pasaste fotos bodas a él ..."` case: fluent Chinese word-salad
that read as a confident answer, with a cross-check that falsely reported "语义一致"."""

from __future__ import annotations

from veriloqua import Translator
from veriloqua.backends.fakes import FakeLLMBackend, FakeMTBackend
from veriloqua.engine import prompt_assembly as pa

_ILLFORMED = "Ese que no pasaste fotos bodas a él tuvo dos fotos sin nada de tengo miedo"


class _IllFormedLLM(FakeLLMBackend):
    """Triage is confident the language is Spanish but reports the source is not well-formed;
    the deep tier reconstructs a likely intended reading; the cross-check reports the source
    is unintelligible (so its 'agree' must not read as a clean pass)."""

    def _route(self, system: str, user: str) -> dict:
        if pa.TASK_TRIAGE in user:
            return {
                "detected_src": "es", "src_confidence": 0.96, "domain": "casual_messaging",
                "complexity": "hard", "is_slang": False, "has_ambiguity": True,
                "has_cultural": False, "src_wellformed": False, "proper_nouns": [],
                "translation": "",
            }
        if pa.TASK_DEEP in user:
            return {
                "translation": "（大意）你没把婚礼照片发给他……",
                "chosen_reading": "fragmented, likely a voice-to-text transcript",
                "alt_readings": ["你没给他发婚礼的照片", "关于婚礼照片你没转发给他"],
                "source_repair": "No le pasaste las fotos de la boda a él.",
                "confidence": 0.4, "notes": [], "learned": None,
            }
        if pa.TASK_CROSSCHECK in user:
            return {"source_reading": "(incoherent fragment)", "translation_reading": "...",
                    "source_intelligible": False, "agree": True, "divergence": ""}
        return super()._route(system, user)


def test_illformed_source_is_flagged_and_reconstructed(tmp_config):
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(), llm_backend=_IllFormedLLM())
    r = tr.translate(_ILLFORMED, to="zh", mode="auto")
    assert r.status.value == "uncertain"
    assert r.trace.get("src_wellformed") is False
    assert r.trace.get("tier") == "opus"                      # escalated, never short-circuited
    assert r.trace.get("source_repair")                       # a repaired reading was recorded
    assert any("拼写/漏词" in w or "语法不通" in w for w in r.warnings)
    assert r.alternatives                                     # intended-meaning options surfaced
    tr.close()


def test_illformed_crosscheck_never_reports_clean_pass(tmp_config):
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(), llm_backend=_IllFormedLLM())
    r = tr.translate(_ILLFORMED, to="zh", mode="auto")
    # cross-check 'agree' was True, but on an unintelligible source that must not read as a pass
    assert r.trace["crosscheck"]["source_intelligible"] is False
    assert r.status.value == "uncertain"
    assert not any("语义一致" in n for n in r.notes)
    assert any("无法核实" in n or "不构成完整语义" in n for n in r.notes)
    tr.close()
