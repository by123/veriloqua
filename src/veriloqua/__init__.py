"""Veriloqua — a self-improving translation engine that understands what people
really mean.

Three modes: ``fast`` (keyless Google, no setup), ``medium`` (LLM + memory), and
``high`` (LLM + independent verification). Corrections are learned durably so the
same mistake is never repeated in the same context.
"""

from __future__ import annotations

from veriloqua.api import Translator, translate
from veriloqua.config import Config, load_config
from veriloqua.errors import (
    BackendNotConfigured,
    BudgetExhausted,
    FastTranslateUncertain,
    MemoryConflict,
    NetworkUnavailable,
    RateLimited,
    ThirdPartyConsentRequired,
    VeriloquaError,
)
from veriloqua.lang.register import Register, RegisterProfile
from veriloqua.modes import Mode
from veriloqua.result import FastResult, TranslationResult

__version__ = "0.1.0"

__all__ = [
    "translate",
    "Translator",
    "TranslationResult",
    "FastResult",
    "Mode",
    "RegisterProfile",
    "Register",
    "Config",
    "load_config",
    "VeriloquaError",
    "NetworkUnavailable",
    "FastTranslateUncertain",
    "BudgetExhausted",
    "BackendNotConfigured",
    "MemoryConflict",
    "ThirdPartyConsentRequired",
    "RateLimited",
    "__version__",
]
