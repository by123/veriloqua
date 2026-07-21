"""Multi-candidate generation for high mode (faithful / localized / register-matched),
batched into a single structured LLM call."""

from __future__ import annotations

from veriloqua.backends.base import LLMBackend
from veriloqua.engine._parse import parse_json_object
from veriloqua.engine.budget import BudgetTracker
from veriloqua.engine.prompt_assembly import build_candidates
from veriloqua.lang.register import RegisterProfile
from veriloqua.result import Candidate


def generate(
    llm: LLMBackend,
    *,
    model: str,
    effort: str,
    source_text: str,
    lang_pair: str,
    register: RegisterProfile,
    memory_block: str,
    domain: str,
    n: int,
    tracker: BudgetTracker,
    seg_idx: int,
) -> list[Candidate]:
    if not tracker.can_llm(seg_idx):
        return []
    system, user = build_candidates(
        source_text=source_text, lang_pair=lang_pair, register=register,
        memory_block=memory_block, domain=domain, n=n,
    )
    resp = llm.complete(system, user, model=model, effort=effort, max_tokens=4096)
    tracker.record_llm(resp)
    data = parse_json_object(resp.text)
    out: list[Candidate] = []
    for c in data.get("candidates", []):
        if isinstance(c, dict) and c.get("text"):
            out.append(Candidate(text=str(c["text"]), strategy=str(c.get("strategy", "faithful"))))
    return out
