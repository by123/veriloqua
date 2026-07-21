"""Tolerant JSON extraction from LLM output.

Models occasionally wrap JSON in prose or code fences despite instructions; this
pulls the first balanced ``{...}`` object and parses it, returning ``{}`` on failure
so the pipeline degrades instead of crashing.
"""

from __future__ import annotations

import json
from typing import Any


def parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if not text:
        return {}
    # strip code fences
    if text.startswith("```"):
        text = text.strip("`")
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1 :]
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        pass
    # find first balanced object
    start = text.find("{")
    if start == -1:
        return {}
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start : i + 1])
                    return obj if isinstance(obj, dict) else {}
                except json.JSONDecodeError:
                    return {}
    return {}
