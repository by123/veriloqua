"""Veriloqua MCP server — exposes the engine to any MCP client (Claude Code,
Claude Desktop, Cursor, Zed, …).

Two ways to use it, matching the "who is the translator?" split:

* **You translate, Veriloqua remembers** — call ``vq_lookup_glossary`` to get the
  project's term locks + past corrections for a text, translate it yourself (you are
  the LLM), then call ``vq_correct`` if a human fixes it. No extra model call, instant.
* **Veriloqua translates** — call ``vq_translate`` to run the full engine
  (fast = keyless Google; auto = the tiered LLM cascade).

Run it with ``veriloqua-mcp`` (stdio). Install:  pip install "veriloqua[mcp]"
Add to a client, e.g.:  claude mcp add veriloqua -- veriloqua-mcp
"""

from __future__ import annotations

from typing import Any


def _translator():
    # Fresh per call: reads config (incl. VERILOQUA_DATA_DIR) from the environment and
    # shares the same on-disk memory DB, so request_ids/corrections persist across calls.
    from veriloqua import Translator

    return Translator()


def _hit_dicts(result: Any) -> list[dict]:
    out = []
    for h in getattr(result, "memory_hits", []) or []:
        out.append({"source": h.source, "accepted": h.accepted, "kind": h.kind,
                    "scope": h.scope, "applied": h.applied})
    return out


# --------------------------------------------------------------- tool bodies
def _tool_translate(text: str, to: str = "zh", source: str = "auto",
                    mode: str = "fast", domain: str = "") -> dict:
    """Translate text end-to-end with Veriloqua, applying the project glossary and the
    correction memory. `to` defaults to Chinese. `mode`: "fast" (keyless Google,
    instant — the default) or "auto" (tiered LLM cascade, needs a configured backend).
    Returns `request_id` — pass it to vq_correct to teach a fix. Prefer
    vq_lookup_glossary + your own translation when you are already an LLM agent."""
    tr = _translator()
    try:
        r = tr.translate(text, to=to, source=source, mode=mode, domain=domain)
        return {
            "translation": r.text,
            "request_id": getattr(r, "request_id", ""),
            "mode": getattr(r, "mode", "fast"),
            "status": r.status.value,
            "confidence": getattr(r, "confidence", None),
            "memory_applied": _hit_dicts(r),
            "warnings": list(getattr(r, "warnings", [])),
        }
    finally:
        tr.close()


def _tool_lookup_glossary(text: str, src: str = "auto", tgt: str = "zh",
                          domain: str = "") -> dict:
    """Return the term locks and past corrections relevant to `text` so YOU (the calling
    agent) can translate it consistently — no model call is made here. Apply an entry
    only when its context matches; use each USE rendering; NEVER output a rendering
    listed under `never`. Call vq_correct afterward if a human fixes your output."""
    from veriloqua.memory import inject, retrieval

    tr = _translator()
    try:
        entries = tr._store.active_entries(
            src, tgt, ["user", "project", "global"],
            user_id=tr.config.user_id, project_id=tr.config.project_id,
        )
        ret = retrieval.retrieve(entries, text, {"domain": domain})
        locks = [{"source": e.source_text, "target": e.accepted_translation,
                  "invariant": e.invariant, "scope": e.scope.value} for e in ret.exact_locks]
        corrections = [{"source": e.source_text, "use": e.accepted_translation,
                        "never": e.rejected_translations, "applies_when": e.applies_when,
                        "scope": e.scope.value} for e in ret.exact_corrections]
        return {
            "term_locks": locks,
            "corrections": corrections,
            "memory_block": inject.render_memory_block(
                ret.exact_locks, ret.exact_corrections, ret.surfaced),
            "guidance": ("Translate the text yourself using these. Apply an entry only if its "
                         "context matches; render each USE term; never output a NEVER rendering. "
                         "If a human corrects your result, call vq_correct."),
            "found": bool(locks or corrections),
        }
    finally:
        tr.close()


def _tool_correct(corrected: str, request_id: str = "", source: str = "",
                  our_output: str = "", src: str = "auto", tgt: str = "zh",
                  domain: str = "") -> dict:
    """Teach Veriloqua a correction; the deterministic guard then blocks that rendering
    on exact-span matches in this context.
    Provide EITHER `request_id` (from a prior vq_translate) OR `source` + `our_output`
    (the wrong translation you produced) + `tgt`. The rejected rendering is stored and
    will be blocked deterministically next time."""
    tr = _translator()
    try:
        if request_id:
            entry = tr.correct(request_id, corrected, note="via mcp")
        elif source and our_output:
            entry = tr.correct_text(source=source, our_output=our_output, corrected=corrected,
                                    src=src, tgt=tgt, domain=domain, note="via mcp")
        else:
            return {"error": "provide request_id, or source + our_output (+ tgt)"}
        return {
            "learned": True,
            "entry_id": entry.id,
            "accepted": entry.accepted_translation,
            "will_never_repeat": entry.rejected_translations,
            "scope": entry.scope.value,
        }
    finally:
        tr.close()


def _tool_add_term(source: str, target: str, src: str = "en", tgt: str = "zh",
                   invariant: bool = False, domain: str = "") -> dict:
    """Add a glossary term lock: `source` must always render as `target`. Applied in all
    modes. Mark `invariant=true` for proper nouns / product names / codes that must be
    substituted verbatim (also used by fast mode)."""
    tr = _translator()
    try:
        entry = tr.add_term(source=source, target=target, src=src, tgt=tgt,
                            invariant=invariant, domain=domain)
        return {"added": True, "entry_id": entry.id, "source": entry.source_text,
                "target": entry.accepted_translation, "invariant": entry.invariant}
    finally:
        tr.close()


def build_server():
    """Construct the FastMCP server with the four vq_* tools registered."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "The Veriloqua MCP server needs the MCP SDK: pip install \"veriloqua[mcp]\""
        ) from exc

    server = FastMCP("veriloqua")
    server.tool(name="vq_translate")(_tool_translate)
    server.tool(name="vq_lookup_glossary")(_tool_lookup_glossary)
    server.tool(name="vq_correct")(_tool_correct)
    server.tool(name="vq_add_term")(_tool_add_term)
    return server


def main() -> int:
    build_server().run()  # stdio transport
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
