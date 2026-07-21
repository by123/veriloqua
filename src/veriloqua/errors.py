"""Typed exceptions for Veriloqua.

Callers can catch precisely the failure they care about instead of string-matching
messages. Every raised error in the package is one of these.
"""

from __future__ import annotations


class VeriloquaError(Exception):
    """Base class for all Veriloqua errors."""


class NetworkUnavailable(VeriloquaError):
    """A translation backend could not reach the network. Fast mode never echoes
    the input as a fake "translation" — it raises this instead."""


class FastTranslateUncertain(VeriloquaError):
    """Fast mode produced output that failed the anomaly validator and the caller
    asked to fail rather than return an unverified result."""


class BudgetExhausted(VeriloquaError):
    """A per-segment or per-job budget (calls / tokens / wall-clock / cost) was hit.
    The pipeline fails closed to the best result so far and attaches this reason."""


class BackendNotConfigured(VeriloquaError):
    """A required backend is missing for the requested mode — e.g. medium/high
    with no LLM backend, or high independence with no configured second model."""


class MemoryConflict(VeriloquaError):
    """A write would contradict an existing active entry for the same key; the
    conflicting entry is surfaced for review instead of being silently overwritten."""


class ThirdPartyConsentRequired(VeriloquaError):
    """A backend would ship source text to a third-party service, but no consent
    was given. In non-interactive runs, set ``VERILOQUA_ALLOW_THIRD_PARTY=1``."""


class RateLimited(VeriloquaError):
    """A backend tripped its circuit breaker after repeated rate-limit / captcha
    responses. Configure a keyed backend for volume use."""
