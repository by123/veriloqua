"""Verification steps.

- ``roundtrip_adequacy``: MEDIUM's cheap, pure-stdlib back-translation overlap flag
  (difflib, no sacrebleu on the runtime path). A SOFT note only — never a gate.
- ``blind_backtranslate``: HIGH's blind back-translation on the independent model;
  an adequacy routing flag (credits D1 only, never register/culture).
- ``cove_revise``: HIGH's conditional Chain-of-Verification self-check + revise.
"""

from __future__ import annotations

import difflib

from veriloqua._norm import normalize
from veriloqua.backends.base import LLMBackend, TranslationBackend
from veriloqua.engine._parse import parse_json_object
from veriloqua.engine.budget import BudgetTracker
from veriloqua.engine.prompt_assembly import build_backtranslate, build_translator
from veriloqua.lang.register import RegisterProfile


def _overlap(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def roundtrip_adequacy(
    mt: TranslationBackend | None,
    *,
    source_text: str,
    output_text: str,
    src_lang: str,
    tgt_lang: str,
    tracker: BudgetTracker,
    seg_idx: int,
) -> float | None:
    """Back-translate the output with the MT backend, then compare to the source via
    pure-stdlib overlap. Returns None if unavailable or it errors — never raises."""
    if mt is None or not tracker.can_mt(seg_idx):
        return None
    try:
        bt = mt.translate(output_text, tgt_lang, src_lang)
        tracker.record_mt()
    except Exception:
        return None
    return _overlap(source_text, bt.text)


def blind_backtranslate(
    llm: LLMBackend,
    *,
    model: str,
    effort: str,
    source_text: str,
    output_text: str,
    src_lang: str,
    tgt_lang: str,
    tracker: BudgetTracker,
    seg_idx: int,
) -> float | None:
    if not tracker.can_llm(seg_idx):
        return None
    system, user = build_backtranslate(target_text=output_text, tgt_lang=tgt_lang, src_lang=src_lang)
    resp = llm.complete(system, user, model=model, effort=effort, max_tokens=2048)
    tracker.record_llm(resp)
    bt = parse_json_object(resp.text).get("back_translation", "")
    if not bt:
        return None
    return _overlap(source_text, bt)


def cove_revise(
    llm: LLMBackend,
    *,
    model: str,
    effort: str,
    source_text: str,
    draft: str,
    lang_pair: str,
    register: RegisterProfile,
    memory_block: str,
    domain: str,
    tracker: BudgetTracker,
    seg_idx: int,
) -> str:
    """One conditional self-check pass: entities preserved? omissions? register?
    placeholders intact? Returns the (possibly revised) translation, or the draft
    unchanged if budget/parse fails."""
    if not tracker.can_llm(seg_idx):
        return draft
    system, user = build_translator(
        source_text=source_text, lang_pair=lang_pair, register=register,
        memory_block=memory_block, mt_draft=draft, domain=domain, mode="high",
    )
    user += (
        "\n\nSELF-CHECK before answering: are all named entities preserved? any omission "
        "or added information? does register/tone match the profile? are all placeholders "
        "intact? Return the corrected final translation in the same JSON contract."
    )
    resp = llm.complete(system, user, model=model, effort=effort, max_tokens=4096)
    tracker.record_llm(resp)
    revised = parse_json_object(resp.text).get("translation", "")
    return revised or draft
