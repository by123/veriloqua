"""The MQM-style error taxonomy used by the judge, the correction classifier,
and per-type regression tracking. Terminology is scored by concept + inflection,
never by string equality."""

from __future__ import annotations

ERROR_TAXONOMY: dict[str, str] = {
    "accuracy:mistranslation": "the target states something the source does not",
    "accuracy:omission": "meaning-bearing source content dropped",
    "accuracy:addition": "information not entailed by the source added",
    "accuracy:untranslated": "source left untranslated when it should be translated",
    "fluency": "grammar, spelling, punctuation, or translationese",
    "terminology": "wrong/inconsistent domain term or proper noun (concept + inflection)",
    "register-tone": "formality/politeness (您/你, keigo, T/V), emotion, humor, sarcasm mismatch",
    "locale-convention": "number/date/currency/unit or placeholder ({name}/%s/ICU/HTML) wrong for locale",
    "culture-pragmatic": "idiom/meme/culture-bound expression not localized to equivalent effect",
    "ambiguity-misresolution": "a polysemous/underspecified source span resolved to the wrong reading",
}

SEVERITIES = ("neutral", "minor", "major", "critical")

#: error_type stored on a correction when no LLM classifier ran (the keyless path).
UNSPECIFIED = "unspecified"
