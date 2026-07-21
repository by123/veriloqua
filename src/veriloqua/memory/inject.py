"""Render retrieved entries into the structured USE/NEVER/BECAUSE/APPLIES-WHEN
memory block. The block is later wrapped as UNTRUSTED DATA by prompt_assembly —
the rationale text is read by the model, never obeyed."""

from __future__ import annotations

from veriloqua.memory.records import MemoryEntry
from veriloqua.memory.retrieval import Surfaced


def _lock_line(e: MemoryEntry) -> str:
    inv = "invariant: yes" if e.invariant else "invariant: no"
    return f'LOCK: "{e.source_text}" -> "{e.accepted_translation}" [{e.scope.value}] [{inv}]'


def _correction_line(e: MemoryEntry) -> str:
    parts = [f'SRC "{e.source_text}" — USE:"{e.accepted_translation}"']
    if e.rejected_translations:
        parts.append("NEVER:" + "; ".join(f'"{r}"' for r in e.rejected_translations))
    if e.rationale:
        parts.append(f"BECAUSE:{e.rationale}")
    if e.applies_when:
        parts.append(f"APPLIES-WHEN:{e.applies_when}")
    return "; ".join(parts)


def render_memory_block(locks: list[MemoryEntry], corrections: list[MemoryEntry],
                        surfaced: list[Surfaced] | None = None) -> str:
    if not (locks or corrections or surfaced):
        return ""
    lines: list[str] = []
    if locks:
        lines.append("# TERM LOCKS (render the concept, correctly inflected)")
        lines += [_lock_line(e) for e in locks]
    if corrections:
        lines.append("# PAST CORRECTIONS (apply only when APPLIES-WHEN matches this segment)")
        lines += [_correction_line(e) for e in corrections]
    if surfaced:
        lines.append("# POSSIBLY RELEVANT (candidates only — verify before use)")
        lines += [_correction_line(s.entry) for s in surfaced]
    return "\n".join(lines)
