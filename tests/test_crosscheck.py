"""Hard-case English triangulation. The direct translation stays the output; the
cross-check reads source and rendering independently into English and flags when they
assert different propositions (polarity/mood/subject drift) — e.g. a statement rendered
as a command. It only audits; it never rewrites."""

from __future__ import annotations

from veriloqua import Translator
from veriloqua.backends.fakes import FakeLLMBackend, FakeMTBackend
from veriloqua.engine import prompt_assembly as pa

_HARD_TRIAGE = {
    "detected_src": "es", "src_confidence": 0.95, "domain": "", "complexity": "hard",
    "is_slang": False, "has_ambiguity": True, "has_cultural": False,
    "proper_nouns": [], "translation": "",
}


class _DivergentLLM(FakeLLMBackend):
    """A hard case whose cross-check reports the source and translation disagree."""

    def _route(self, system: str, user: str) -> dict:
        if pa.TASK_TRIAGE in user:
            return dict(_HARD_TRIAGE)
        if pa.TASK_CROSSCHECK in user:
            return {"source_reading": "the files won't download",
                    "translation_reading": "don't download the files",
                    "agree": False, "divergence": "polarity/mood: statement vs. command"}
        return super()._route(system, user)


class _HardButConsistentLLM(FakeLLMBackend):
    """A hard case whose cross-check agrees (uses the default fake crosscheck route)."""

    def _route(self, system: str, user: str) -> dict:
        if pa.TASK_TRIAGE in user:
            return dict(_HARD_TRIAGE)
        return super()._route(system, user)


def test_crosscheck_flags_meaning_drift(tmp_config):
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(), llm_backend=_DivergentLLM())
    r = tr.translate("No descarga los archivos", to="zh", mode="auto")
    assert r.status.value == "uncertain"
    assert r.trace["crosscheck"]["agree"] is False
    assert any("英文交叉核对" in n for n in r.notes)
    tr.close()


class _FakeSearch:
    def search(self, query: str, max_results: int = 5) -> list[dict]:
        return [{"snippet": "meaning attested in source A"},
                {"snippet": "meaning attested in source B"}]


def test_flagged_case_without_search_backend_is_uncertain(tmp_config):
    # a flagged (ambiguous) case with NO search backend cannot be web-verified —
    # crosscheck agreement alone must not read as a clean pass
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(),
                    llm_backend=_HardButConsistentLLM())
    r = tr.translate("una frase difícil", to="zh", mode="auto")
    assert r.trace["crosscheck"]["agree"] is True
    assert r.trace["research_status"] == "unavailable"
    assert r.status.value == "uncertain"
    assert any("未经外部验证" in n for n in r.notes)
    tr.close()


def test_crosscheck_passes_when_consistent_and_researched(tmp_config):
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(),
                    llm_backend=_HardButConsistentLLM(), search_backend=_FakeSearch())
    r = tr.translate("una frase difícil", to="zh", mode="auto")
    assert r.trace["crosscheck"]["agree"] is True
    assert r.trace["research_status"] == "web_verified"
    assert r.status.value == "ok"
    tr.close()


def test_crosscheck_reports_independence_honestly(tmp_config):
    # same-backend crosscheck must NOT claim independence; a separate judge backend does
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(),
                    llm_backend=_HardButConsistentLLM(), search_backend=_FakeSearch())
    r = tr.translate("una frase difícil", to="zh", mode="auto")
    assert r.trace["crosscheck"]["independent"] is False
    tr.close()

    judge = FakeLLMBackend()
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(),
                    llm_backend=_HardButConsistentLLM(), judge_backend=judge,
                    search_backend=_FakeSearch())
    r = tr.translate("una frase difícil", to="zh", mode="auto")
    assert r.trace["crosscheck"]["independent"] is True
    assert judge.calls > 0                      # the judge backend actually ran
    tr.close()


def test_crosscheck_can_be_disabled(tmp_config):
    tmp_config.auto_crosscheck = False
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(), llm_backend=_DivergentLLM())
    r = tr.translate("No descarga los archivos", to="zh", mode="auto")
    assert r.trace["crosscheck"] is None          # the audit never ran
    tr.close()


def test_crosscheck_skipped_on_easy_path(tmp_config):
    # a non-hard sentence never reaches the deep tier, so no cross-check call is made
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(),
                    llm_backend=FakeLLMBackend(respond=lambda s: f"TR::{s}"))
    r = tr.translate("hello world", to="zh", mode="auto")
    assert r.trace["crosscheck"] is None
    tr.close()
