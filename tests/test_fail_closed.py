"""Fail-closed semantics: malformed model output, source echo, dropped placeholders,
and unenforceable term locks / corrections must surface as DEGRADED or UNCERTAIN —
never as status=ok — and a source echo is never passed off as a translation."""

from __future__ import annotations

from veriloqua import Translator
from veriloqua.backends.fakes import FakeLLMBackend, FakeMTBackend
from veriloqua.engine import prompt_assembly as pa
from veriloqua.memory import guard
from veriloqua.memory.records import Kind, MemoryEntry
from veriloqua.result import Status

LONG = "this sentence is long enough to bypass the trivial short-circuit entirely"


class _GarbageLLM(FakeLLMBackend):
    """Returns non-JSON prose from every tier."""

    def complete(self, system, user, *, model=None, effort="high", max_tokens=4096):
        resp = super().complete(system, user, model=model, effort=effort,
                                max_tokens=max_tokens)
        resp = type(resp)(text="Sorry, I cannot produce JSON today.",
                          input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
                          model=resp.model)
        return resp


class _SonnetGarbageLLM(FakeLLMBackend):
    """Translate tier emits garbage; the deep tier answers properly."""

    def _route(self, system, user):
        if pa.TASK_TRANSLATE in user and pa.TASK_DEEP not in user:
            return {}  # serialized as '{}' → no "translation" key → tier failed
        return super()._route(system, user)


def test_all_tiers_malformed_degrades_to_mt(tmp_config):
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend({LONG: "机器直译"}),
                    llm_backend=_GarbageLLM())
    r = tr.translate(LONG, to="zh", mode="auto")
    tr.close()

    assert r.status is Status.DEGRADED
    assert r.text == "机器直译"                      # MT fallback, not a source echo
    assert any("无法解析" in w for w in r.warnings)


def test_all_tiers_malformed_without_mt_returns_empty_degraded(tmp_config):
    tmp_config.no_third_party = True
    tr = Translator(config=tmp_config, mt_backend=None, llm_backend=_GarbageLLM())
    r = tr.translate(LONG, to="zh", mode="auto")
    tr.close()

    assert r.status is Status.DEGRADED
    assert r.text == ""                              # explicit failure, never an echo
    assert any("未产出译文" in w for w in r.warnings)


def test_sonnet_malformed_escalates_and_deep_recovers(tmp_config):
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(),
                    llm_backend=_SonnetGarbageLLM())
    r = tr.translate(LONG, to="zh", mode="auto")
    tr.close()

    assert r.trace["tier"] == "opus"                 # escalated instead of echoing
    assert r.text.startswith("TR::")                 # deep tier's answer was used
    assert any("升级处理" in w for w in r.warnings)


def test_source_echo_is_flagged_not_ok(tmp_config):
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(),
                    llm_backend=FakeLLMBackend(respond=lambda s: s))  # echoes the source
    r = tr.translate(LONG, to="zh", mode="auto")
    tr.close()

    assert r.status is not Status.OK
    assert any("完全相同" in w for w in r.warnings)


def test_dropped_placeholder_degrades(tmp_config):
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(),
                    llm_backend=FakeLLMBackend(respond=lambda s: "占位符被吃掉的很长的译文内容"))
    r = tr.translate("Hello {name}, your export of {file} finished", to="zh", mode="auto")
    tr.close()

    assert r.status is Status.DEGRADED
    assert any("占位符" in w for w in r.warnings)


def test_unsatisfied_invariant_lock_degrades(tmp_config):
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(),
                    llm_backend=FakeLLMBackend(respond=lambda s: "这个译文把品牌名意译丢了"))
    tr.add_term(source="Veriloqua", target="Veriloqua", src="en", tgt="zh", invariant=True)
    r = tr.translate("Veriloqua is fast and reliable software", to="zh", source="en",
                     mode="auto")
    tr.close()

    assert r.status is Status.DEGRADED
    assert any("术语锁" in w for w in r.warnings)


# ---------------------------------------------------------------- guard spans
def _corr(rejected: str, accepted: str) -> MemoryEntry:
    return MemoryEntry(kind=Kind.CORRECTION, src_lang="en", tgt_lang="zh",
                       source_text="x", source_norm="x", accepted_translation=accepted,
                       rejected_translations=[rejected])


def test_guard_replaces_case_variant():
    out, fixes = guard.enforce("Please go Touch  Grass now", [_corr("touch grass", "出去走走")])
    assert "出去走走" in out and "Touch" not in out
    assert fixes


def test_guard_replaces_fullwidth_nfkc_variant():
    # detection normalizes NFKC; replacement must land on the ORIGINAL fullwidth span
    out, _ = guard.enforce("结果是 ｔｏｕｃｈ ｇｒａｓｓ 哦", [_corr("touch grass", "出去走走")])
    assert "出去走走" in out and "ｔｏｕｃｈ" not in out


def test_guard_detection_and_replacement_agree():
    # whatever find_violations can see, enforce must be able to rewrite
    tricky = ["Touch Grass", "touch, grass", "ｔｏｕｃｈ ｇｒａｓｓ", "TOUCH GRASS!"]
    for variant in tricky:
        text = f"prefix {variant} suffix"
        corr = [_corr("touch grass", "OK")]
        assert guard.find_violations(text, corr), variant
        fixed, _ = guard.enforce(text, corr)
        assert not guard.find_violations(fixed, corr), variant


def test_guard_bounded_when_replacement_contains_needle():
    # replacement itself still contains the needle: one pass, no infinite loop
    out, _ = guard.enforce("touch grass", [_corr("touch grass", "touch grass (idiom)")])
    assert out == "touch grass (idiom)"
