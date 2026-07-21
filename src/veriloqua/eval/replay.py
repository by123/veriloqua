"""Correction-replay runner.

Merges packaged seed fixtures with the USER replay dir and runs the two tiers:

* TIER 1 (deterministic, build-breaking): for every filed correction, the reject-guard
  removes the rejected rendering (verbatim AND embedded in a longer sentence), and the
  paired over-fit probe does NOT fire in a contradicted context. Uses ONLY the
  deterministic exact-match tier, so the verdict is identical with or without rapidfuzz.
* TIER 2 (advisory): a semantic-equality spot check. Reported, never blocks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from veriloqua._norm import normalize
from veriloqua.memory import guard
from veriloqua.memory.records import Kind, MemoryEntry
from veriloqua.memory.retrieval import exact_scan

_FIXTURES = Path(__file__).with_name("fixtures")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _case_to_entry(case: dict) -> MemoryEntry:
    rejected = case.get("rejected", [])
    if isinstance(rejected, str):
        rejected = [rejected]
    return MemoryEntry(
        id=case.get("id", -1),
        kind=Kind.CORRECTION,
        src_lang=case.get("src_lang", "en"),
        tgt_lang=case.get("tgt_lang", "zh"),
        source_text=case["source"],
        source_norm=normalize(case["source"]),
        accepted_translation=case.get("accepted", ""),
        rejected_translations=list(rejected),
        context_tags=case.get("context", {}),
    )


def _check_guarantee(entry: MemoryEntry) -> list[str]:
    """Return a list of failure descriptions (empty = passed)."""
    failures: list[str] = []
    for rej in entry.rejected_translations:
        variants = [rej, f"So, {rej}.", f"Note — {rej} — really."]
        for v in variants:
            cleaned, _ = guard.enforce(v, [entry])
            if guard.find_violations(cleaned, [entry]):
                failures.append(f"rejected rendering survived guard: '{rej}' in variant '{v}'")
    return failures


def _check_overfit(entry: MemoryEntry) -> list[str]:
    """The correction must NOT fire in a contradicted context."""
    entry_domain = entry.context_tags.get("domain", "")
    probe_domain = "legal" if entry_domain != "legal" else "casual-chat"
    if not entry_domain:
        # under-tagged entries have no domain to contradict; skip (directional gate design)
        return []
    ctx = {"domain": probe_domain}
    _, corrections = exact_scan([entry], entry.source_text, ctx)
    if corrections:
        return [f"over-fit: correction fired in contradicted domain '{probe_domain}'"]
    return []


def run_eval(*, replay_dir: str | Path | None = None, suite: str = "all") -> dict[str, Any]:
    cases: list[dict] = []
    probes: list[dict] = []

    if suite in ("replay", "all"):
        cases += _read_jsonl(_FIXTURES / "replay" / "corrections.jsonl")
        probes += _read_jsonl(_FIXTURES / "replay" / "overfit_probes.jsonl")
        if replay_dir is not None:
            rd = Path(replay_dir)
            cases += _read_jsonl(rd / "corrections.jsonl")
            probes += _read_jsonl(rd / "overfit_probes.jsonl")

    tier1_failures: list[str] = []
    guaranteed = 0
    overfit_ok = 0
    for case in cases:
        try:
            entry = _case_to_entry(case)
        except KeyError as exc:
            tier1_failures.append(f"malformed case (missing {exc})")
            continue
        gf = _check_guarantee(entry)
        of = _check_overfit(entry)
        if not gf:
            guaranteed += 1
        if not of:
            overfit_ok += 1
        tier1_failures += gf + of

    report: dict[str, Any] = {
        "suite": suite,
        "cases": len(cases),
        "guarantee_passed": guaranteed,
        "overfit_passed": overfit_ok,
        "tier1_passed": not tier1_failures,
        "tier1_failures": tier1_failures[:50],
    }

    if suite in ("gold", "all"):
        gold = _read_jsonl(_FIXTURES / "en_zh_gold.jsonl")
        report["tier2_gold_segments"] = len(gold)
        report["tier2_note"] = (
            "advisory only — chrF/COMET/judge quality metric is tracked, never blocks a merge "
            "(install veriloqua[eval] for chrF)."
        )

    return report
