"""Register and tone as a controlled vocabulary.

Uses Joos's five styles plus a small tone-tag set, and carries per-language-pair
politeness overrides (T/V, keigo, 您/你). The profile is injected into the
translator/judge prompts so "match the register" is a checkable instruction rather
than a vibe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Register(str, Enum):
    FROZEN = "frozen"
    FORMAL = "formal"
    CONSULTATIVE = "consultative"
    CASUAL = "casual"
    INTIMATE = "intimate"


TONE_TAGS = ("neutral", "warm", "curt", "playful", "sarcastic", "urgent", "deferential")


@dataclass(slots=True)
class RegisterProfile:
    register: Register = Register.CONSULTATIVE
    tone: str = "neutral"
    audience: str = ""
    # per-pair politeness override, e.g. {"zh": "您", "ja": "teineigo", "de": "Sie"}
    politeness: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, spec: str | None) -> RegisterProfile:
        """Parse a compact CLI spec like ``formal`` or ``formal:warm`` or
        ``casual:playful`` into a profile. Unknown values fall back to defaults."""
        if not spec:
            return cls()
        parts = spec.split(":", 1)
        reg_raw = parts[0].strip().lower()
        try:
            reg = Register(reg_raw)
        except ValueError:
            reg = Register.CONSULTATIVE
        tone = "neutral"
        if len(parts) == 2 and parts[1].strip().lower() in TONE_TAGS:
            tone = parts[1].strip().lower()
        return cls(register=reg, tone=tone)

    def describe(self) -> str:
        bits = [f"register={self.register.value}", f"tone={self.tone}"]
        if self.audience:
            bits.append(f"audience={self.audience}")
        if self.politeness:
            bits.append("politeness=" + ",".join(f"{k}:{v}" for k, v in self.politeness.items()))
        return "; ".join(bits)
