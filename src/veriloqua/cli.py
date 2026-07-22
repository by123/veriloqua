"""The ``vq`` command-line interface (stdlib argparse core; ``[cli]`` adds rich).

Reachable identically as ``python -m veriloqua``. ``vq "text" -t es`` translates;
``correct`` / ``glossary`` / ``memory`` / ``lock`` / ``eval`` / ``backends`` / ``config``
manage learning and configuration.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from veriloqua import __version__

_SUBCOMMANDS = {"translate", "correct", "glossary", "memory", "lock", "eval", "backends", "config"}


def _entry_to_dict(e: Any) -> dict:
    return {
        "id": e.id, "kind": e.kind.value, "src_lang": e.src_lang, "tgt_lang": e.tgt_lang,
        "source_text": e.source_text, "accepted_translation": e.accepted_translation,
        "rejected_translations": e.rejected_translations, "scope": e.scope.value,
        "status": e.status.value, "provenance": e.provenance.value, "invariant": e.invariant,
        "error_type": e.error_type, "context_tags": e.context_tags, "applies_when": e.applies_when,
        "confidence": e.confidence, "created_at": e.created_at,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vq", description="Veriloqua translation engine")
    p.add_argument("--version", action="version", version=f"veriloqua {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("translate", help="translate text (or stdin); target defaults to Chinese")
    t.add_argument("text", nargs="*")   # any number of words; joined (quote for exact spacing)
    t.add_argument("--to", "-t", default="zh",
                   help="target language (default: zh — any language → Chinese)")
    t.add_argument("--from", "-f", dest="src", default="auto", help="source language (default auto)")
    t.add_argument("--mode", "-m", choices=["fast", "auto"], default=None,
                   help="fast = keyless Google (instant); auto = Haiku→Sonnet→Opus cascade (default)")
    t.add_argument("--domain", "-d", default="",
                   help="domain label; also loads a <domain>.md context brief if one exists")
    t.add_argument("--context-file", default=None,
                   help="path to a domain/app context brief to inject (overrides the --domain pack)")
    t.add_argument("--register", "-r", default=None, help="e.g. formal, casual:playful")
    t.add_argument("--max-cost", type=float, default=None, help="per-job USD ceiling")
    t.add_argument("--max-calls", type=int, default=None, help="per-job LLM/MT call ceiling")
    t.add_argument("--json", action="store_true", help="emit full structured result")

    c = sub.add_parser("correct", help="file a correction (the trusted learning path)")
    c.add_argument("request_id")
    c.add_argument("corrected")
    c.add_argument("--note", default="")
    c.add_argument("--scope", choices=["user", "project", "global"], default="user")

    g = sub.add_parser("glossary", help="manage deterministic term locks")
    gsub = g.add_subparsers(dest="action", required=True)
    ga = gsub.add_parser("add")
    ga.add_argument("source")
    ga.add_argument("target")
    ga.add_argument("--from", "-f", dest="src", required=True)
    ga.add_argument("--to", "-t", dest="tgt", required=True)
    ga.add_argument("--domain", "-d", default="")
    ga.add_argument("--invariant", action="store_true", help="whole-token safe (proper noun/code)")
    gsub.add_parser("list")
    grm = gsub.add_parser("rm")
    grm.add_argument("id", type=int)

    m = sub.add_parser("memory", help="inspect / back up / clean the memory store")
    msub = m.add_subparsers(dest="action", required=True)
    msub.add_parser("stats")
    mex = msub.add_parser("export")
    mex.add_argument("file")
    mim = msub.add_parser("import")
    mim.add_argument("file")
    mf = msub.add_parser("forget")
    mf.add_argument("id", type=int)
    msub.add_parser("conflicts")
    msub.add_parser("purge-log")

    lk = sub.add_parser("lock", help="human promotion across the global boundary")
    lk.add_argument("entry_id", type=int)
    lk.add_argument("--scope", default="global")

    ev = sub.add_parser("eval", help="run the deterministic replay / over-fit gate; "
                                     "--suite gold translates the gold set and reports chrF")
    ev.add_argument("--suite", choices=["replay", "gold", "all"], default="all")
    ev.add_argument("--mode", "-m", choices=["fast", "auto"], default="auto",
                    help="engine mode used when --suite gold translates the gold set")

    sub.add_parser("backends", help="list registered/available backends")

    cf = sub.add_parser("config", help="get/set configuration")
    cfsub = cf.add_subparsers(dest="action", required=True)
    cfg = cfsub.add_parser("get")
    cfg.add_argument("key", nargs="?")
    cfs = cfsub.add_parser("set")
    cfs.add_argument("key")
    cfs.add_argument("value")

    return p


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] not in _SUBCOMMANDS and raw[0] not in ("--version", "-h", "--help"):
        raw = ["translate"] + raw
    args = build_parser().parse_args(raw)

    try:
        return _dispatch(args)
    except Exception as exc:  # user-facing: one clean line, not a traceback
        print(f"vq: error: {exc}", file=sys.stderr)
        return 1


def _make_translator(args: argparse.Namespace):
    from veriloqua import Translator

    overrides: dict[str, Any] = {}
    if getattr(args, "max_cost", None) is not None:
        overrides["max_cost_usd"] = args.max_cost
    if getattr(args, "max_calls", None) is not None:
        overrides["max_calls"] = args.max_calls
    return Translator(**overrides)


def _dispatch(args: argparse.Namespace) -> int:
    if args.cmd == "translate":
        return _cmd_translate(args)
    if args.cmd == "correct":
        return _cmd_correct(args)
    if args.cmd == "glossary":
        return _cmd_glossary(args)
    if args.cmd == "memory":
        return _cmd_memory(args)
    if args.cmd == "lock":
        return _cmd_lock(args)
    if args.cmd == "eval":
        return _cmd_eval(args)
    if args.cmd == "backends":
        return _cmd_backends(args)
    if args.cmd == "config":
        return _cmd_config(args)
    return 1


def _cmd_translate(args: argparse.Namespace) -> int:
    text = " ".join(args.text) if args.text else sys.stdin.read()
    if not text.strip():
        print("vq: error: no text to translate", file=sys.stderr)
        return 1
    tr = _make_translator(args)
    try:
        mode = args.mode or tr.config.default_mode
        if mode != "fast" and sys.stderr.isatty() and not os.environ.get("VERILOQUA_QUIET"):
            print("vq: auto mode — trivial short texts return instantly (keyless MT); "
                  "otherwise Haiku triages, Sonnet translates, hard cases escalate to Opus. "
                  "LLM tiers over a local agent CLI can take 10-40s. "
                  "(--mode fast = instant keyless result; --json for the full trace)",
                  file=sys.stderr)
        context = None
        if getattr(args, "context_file", None):
            try:
                with open(args.context_file, encoding="utf-8") as fh:
                    context = fh.read()
            except OSError as exc:
                print(f"vq: warning: could not read --context-file ({exc}); ignoring",
                      file=sys.stderr)
        result = tr.translate(text, to=args.to, source=args.src, mode=mode,
                              domain=args.domain, register=args.register, context=context)
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(result.text)
            _print_caveats(result)
            if getattr(result, "request_id", ""):
                print(f"[request_id: {result.request_id}  mode: {getattr(result, 'mode', 'fast')}]",
                      file=sys.stderr)
    finally:
        tr.close()
    return 0


def _print_caveats(result: Any) -> None:
    """Plain-mode honesty: stdout carries only the translation, but warnings/notes and any
    reconstructed-meaning alternatives go to stderr (like the request_id line) so a flagged
    result never looks confident — even when stdout is piped. Silent for a clean OK result."""
    status = getattr(getattr(result, "status", None), "value", "ok")
    if status == "ok":
        return
    warnings = list(getattr(result, "warnings", []) or [])
    notes = list(getattr(result, "notes", []) or [])
    alternatives = list(getattr(result, "alternatives", []) or [])
    for w in warnings[:3]:
        print(f"⚠️  {w}", file=sys.stderr)
    for n in notes[:2]:
        print(f"·  {n}", file=sys.stderr)
    for alt in alternatives[:3]:
        print(f"↷  另一种可能的理解:{alt}", file=sys.stderr)
    if not warnings and not notes:
        print("⚠️  结果标记为存疑(--json 查看完整依据)", file=sys.stderr)


def _cmd_correct(args: argparse.Namespace) -> int:
    tr = _make_translator(args)
    try:
        entry = tr.correct(args.request_id, args.corrected, note=args.note, scope=args.scope)
        print(f"learned correction #{entry.id}: \"{entry.source_text[:60]}\" -> "
              f"\"{entry.accepted_translation}\" [{entry.scope.value}]")
        if entry.rejected_translations:
            print(f"  blocked renderings: {entry.rejected_translations}", file=sys.stderr)
    finally:
        tr.close()
    return 0


def _cmd_glossary(args: argparse.Namespace) -> int:
    tr = _make_translator(args)
    try:
        if args.action == "add":
            e = tr.add_term(source=args.source, target=args.target, src=args.src, tgt=args.tgt,
                            invariant=args.invariant, domain=args.domain)
            inv = " (invariant)" if e.invariant else ""
            print(f"term lock #{e.id}: \"{e.source_text}\" -> \"{e.accepted_translation}\"{inv}")
        elif args.action == "list":
            from veriloqua.memory.records import Kind

            entries = [e for e in tr._store.all_entries() if e.kind == Kind.TERM_LOCK]
            if not entries:
                print("(no term locks)")
            for e in entries:
                inv = " [invariant]" if e.invariant else ""
                print(f"#{e.id}  {e.src_lang}->{e.tgt_lang}  \"{e.source_text}\" -> "
                      f"\"{e.accepted_translation}\"{inv}  [{e.scope.value}]")
        elif args.action == "rm":
            ok = tr.forget(args.id)
            print("removed" if ok else "not found")
    finally:
        tr.close()
    return 0


def _cmd_memory(args: argparse.Namespace) -> int:
    tr = _make_translator(args)
    try:
        if args.action == "stats":
            print(json.dumps(tr.memory_stats(), ensure_ascii=False, indent=2))
        elif args.action == "export":
            data = [_entry_to_dict(e) for e in tr._store.all_entries(include_deleted=True)]
            with open(args.file, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            print(f"exported {len(data)} entries -> {args.file}")
        elif args.action == "import":
            with open(args.file, encoding="utf-8") as fh:
                data = json.load(fh)
            n = _import_entries(tr, data)
            print(f"imported {n} entries")
        elif args.action == "forget":
            print("removed" if tr.forget(args.id) else "not found")
        elif args.action == "conflicts":
            conflicts = tr.conflicts()
            print(json.dumps(conflicts, ensure_ascii=False, indent=2) if conflicts
                  else "(no open conflicts)")
        elif args.action == "purge-log":
            n = tr.purge_log()
            print(f"purged {n} request-log records")
    finally:
        tr.close()
    return 0


def _import_entries(tr: Any, data: list[dict]) -> int:
    from veriloqua._norm import normalize
    from veriloqua.memory.records import Kind, MemoryEntry, Provenance, Scope, Status

    n = 0
    for d in data:
        entry = MemoryEntry(
            kind=Kind(d.get("kind", "correction")),
            src_lang=d["src_lang"], tgt_lang=d["tgt_lang"], source_text=d["source_text"],
            source_norm=normalize(d["source_text"]), accepted_translation=d["accepted_translation"],
            rejected_translations=d.get("rejected_translations", []),
            error_type=d.get("error_type", "unspecified"), context_tags=d.get("context_tags", {}),
            applies_when=d.get("applies_when", ""), provenance=Provenance(d.get("provenance", "user_correction")),
            confidence=d.get("confidence", 0.8), scope=Scope(d.get("scope", "user")),
            status=Status(d.get("status", "active")), invariant=d.get("invariant", False),
        )
        existing = tr._store.find_active_key(entry.source_norm, entry.src_lang, entry.tgt_lang,
                                             entry.scope.value, entry.scope_id)
        if existing is None:
            tr._store.add_entry(entry)
            n += 1
    return n


def _cmd_lock(args: argparse.Namespace) -> int:
    tr = _make_translator(args)
    try:
        ok = tr.lock(args.entry_id, scope=args.scope)
        print(f"entry #{args.entry_id} promoted to scope={args.scope}" if ok else "not found")
    finally:
        tr.close()
    return 0


def _cmd_eval(args: argparse.Namespace) -> int:
    from veriloqua.eval.replay import run_eval

    tr = _make_translator(args)
    try:
        translate_fn = None
        if args.suite == "gold":
            # the gold fixtures are en→zh; each segment really runs through the engine
            def translate_fn(text: str, domain: str) -> str:
                r = tr.translate(text, to="zh", source="en", mode=args.mode, domain=domain)
                return r.text

        report = run_eval(replay_dir=tr.config.replay_dir, suite=args.suite,
                          translate_fn=translate_fn)
    finally:
        tr.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # the deterministic tier gates; the gold quality score is advisory by design
    return 0 if report.get("tier1_passed", True) else 1


def _cmd_backends(args: argparse.Namespace) -> int:
    def available(mod: str) -> bool:
        import importlib.util

        try:
            return importlib.util.find_spec(mod) is not None
        except (ImportError, ValueError):
            return False

    import shutil

    rows = [
        ("llm/claude_cli", "Claude Code `claude -p`", "yes" if shutil.which("claude") else "install Claude Code + log in"),
        ("translation/google_free", "keyless Google (built-in)", "yes"),
        ("translation/deepl", "DeepL", "yes" if available("deepl") else "pip install veriloqua[deepl]"),
        ("translation/google_cloud", "Google Cloud", "yes" if available("google.cloud.translate") else "pip install veriloqua[google]"),
        ("llm/anthropic", "Anthropic", "yes" if available("anthropic") else "pip install veriloqua[anthropic]"),
        ("llm/openai", "OpenAI", "yes" if available("openai") else "pip install veriloqua[openai]"),
        ("embedding/sentence_transformers", "local embeddings", "yes" if available("sentence_transformers") else "pip install veriloqua[embeddings]"),
        ("fuzzy/rapidfuzz", "lexical surfacing", "rapidfuzz" if available("rapidfuzz") else "difflib (stdlib fallback)"),
    ]
    for name, desc, status in rows:
        print(f"{name:38}  {desc:22}  {status}")
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    from veriloqua.config import load_config

    cfg = load_config()
    if args.action == "get":
        if args.key:
            print(getattr(cfg, args.key, "(unknown key)"))
        else:
            print(repr(cfg))
    elif args.action == "set":
        print("Set VERILOQUA_* env vars or edit the TOML at "
              f"{cfg.config_dir / 'config.toml'} — e.g.:\n  {args.key} = \"{args.value}\"",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
