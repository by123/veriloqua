"""Fast-mode output validation + lightweight language/script detection.

Never echoes the input as a fake translation; flags anomalies (empty, output==input
across a different-script pair, length/script-ratio outliers, detected_src==tgt)
rather than silently passing them.
"""

from __future__ import annotations

import re

from veriloqua._norm import normalize

_CJK = re.compile(r"[㐀-鿿豈-﫿぀-ヿ가-힯]")
_LATIN = re.compile(r"[A-Za-zÀ-ÿ]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_ARABIC = re.compile(r"[؀-ۿ]")


def dominant_script(text: str) -> str:
    counts = {
        "cjk": len(_CJK.findall(text)),
        "latin": len(_LATIN.findall(text)),
        "cyrillic": len(_CYRILLIC.findall(text)),
        "arabic": len(_ARABIC.findall(text)),
    }
    best = max(counts, key=lambda k: counts[k])
    return best if counts[best] > 0 else "unknown"


def validate_fast(
    *, source_text: str, output_text: str, detected_src: str, tgt_lang: str
) -> tuple[list[str], bool]:
    """Return (warnings, hard_reject)."""
    warnings: list[str] = []
    if not output_text.strip():
        return (["empty translation from backend"], True)

    src_script = dominant_script(source_text)
    out_script = dominant_script(output_text)

    if normalize(source_text) == normalize(output_text) and src_script != out_script:
        warnings.append("output identical to input across differing scripts (possible echo)")
    if src_script == out_script and src_script != "unknown" and _expect_script_change(detected_src, tgt_lang):
        warnings.append(f"output script ({out_script}) unchanged from source; expected a change")

    # CJK is 3-4x denser than alphabetic scripts, so cross-script pairs get wider bounds
    # ("thank you" → "谢谢" is 9→2 chars and perfectly normal).
    lo, hi = 0.35, 3.0
    if src_script != out_script and "cjk" in (src_script, out_script):
        lo, hi = (0.12, 3.0) if out_script == "cjk" else (0.35, 8.0)
    slen, olen = len(source_text), len(output_text)
    if slen >= 8 and (olen < slen * lo or olen > slen * hi):
        warnings.append(f"length ratio outlier (src={slen}, out={olen})")

    if detected_src and tgt_lang and detected_src.split("-")[0] == tgt_lang.split("-")[0]:
        warnings.append(f"detected source ({detected_src}) equals target ({tgt_lang})")

    return warnings, False


# language → expected dominant script, used only to flag "no change" heuristically.
_LANG_SCRIPT = {
    "zh": "cjk", "ja": "cjk", "ko": "cjk", "ru": "cyrillic", "uk": "cyrillic",
    "ar": "arabic", "fa": "arabic",
}


def _expect_script_change(src: str, tgt: str) -> bool:
    s = _LANG_SCRIPT.get((src or "").split("-")[0], "latin")
    t = _LANG_SCRIPT.get((tgt or "").split("-")[0], "latin")
    return s != t
