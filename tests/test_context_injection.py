"""A domain/app context brief must reach the prompts so translation is app-aware. The
brief is named by `domain` (loads `<context_dir>/<domain>.md`) or passed inline via
`context=`; it is injected as UNTRUSTED data into triage, translate, and deep — never
obeyed. Absent brief → zero injection, unchanged behavior."""

from __future__ import annotations

from veriloqua import Translator
from veriloqua.backends.fakes import FakeLLMBackend, FakeMTBackend
from veriloqua.config import load_config
from veriloqua.engine import prompt_assembly as pa

_BRIEF = "Lumashot is an AI photo/video enhancer. 'watermark' means the paywall watermark."


class _PromptCapturingLLM(FakeLLMBackend):
    """Records every user prompt so a test can assert what context each tier received.
    Routes to a hard case so all three tiers (triage/translate/deep) fire."""

    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[str] = []

    def _route(self, system: str, user: str) -> dict:
        self.prompts.append(user)
        if pa.TASK_TRIAGE in user:
            return {
                "detected_src": "es", "src_confidence": 0.95, "domain": "app_review",
                "complexity": "hard", "is_slang": False, "has_ambiguity": True,
                "has_cultural": False, "src_wellformed": True, "proper_nouns": [],
                "translation": "",
            }
        return super()._route(system, user)


def test_inline_context_reaches_all_three_tiers(tmp_config):
    llm = _PromptCapturingLLM()
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(), llm_backend=llm)
    r = tr.translate("no me quita la marca de agua", to="zh", mode="auto", context=_BRIEF)
    tr.close()

    assert r.trace.get("context_injected") is True
    triage = [p for p in llm.prompts if pa.TASK_TRIAGE in p]
    translate = [p for p in llm.prompts if pa.TASK_TRANSLATE in p]
    deep = [p for p in llm.prompts if pa.TASK_DEEP in p]
    assert triage and translate and deep                      # all three tiers ran
    for bucket in (triage, translate, deep):
        assert any(_BRIEF in p for p in bucket)               # brief present in each
        # and it is wrapped as untrusted data, not injected as bare instructions
        assert any("domain_context" in p and pa.UNTRUSTED_OPEN in p for p in bucket)


def test_domain_pack_is_loaded_by_domain_name(tmp_config, tmp_path):
    ctx_dir = tmp_path / "context"
    ctx_dir.mkdir()
    (ctx_dir / "lumashot_review.md").write_text(_BRIEF, encoding="utf-8")
    tmp_config.context_dir = ctx_dir

    llm = _PromptCapturingLLM()
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(), llm_backend=llm)
    r = tr.translate("borrosa", to="zh", mode="auto", domain="lumashot_review")
    tr.close()

    assert r.trace.get("context_injected") is True
    assert any(_BRIEF in p for p in llm.prompts)


def test_no_pack_means_no_injection(tmp_config):
    llm = _PromptCapturingLLM()
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(), llm_backend=llm)
    r = tr.translate("borrosa", to="zh", mode="auto", domain="does_not_exist")
    tr.close()

    assert r.trace.get("context_injected") is False
    assert not any("domain_context" in p for p in llm.prompts)


def test_domain_name_cannot_escape_the_context_dir(tmp_config):
    # path-traversal guard: a crafted domain never reads outside the context dir
    cfg = load_config()
    assert cfg.domain_context("../../etc/passwd") == ""
    assert cfg.domain_context("..") == ""
