"""Keyless Google Translate endpoint — the zero-config fast default.

Unofficial and IP-rate-limited. It is wrapped so it (a) never returns garbage or
echoes the input as a fake translation, and (b) fails once with an actionable error
under rate-limiting instead of a per-segment wall of uncertainty. It works with no
setup and no key; a one-time transparent notice is printed on first use, and
``no_third_party`` hard-disables it. One-line swap to the keyed Cloud Translation /
DeepL backends for volume/production use.
"""

from __future__ import annotations

import os
import sys
import time

import httpx

from veriloqua.backends.base import TranslationResult
from veriloqua.errors import (
    NetworkUnavailable,
    RateLimited,
    ThirdPartyConsentRequired,
)

_ENDPOINT = "https://translate.googleapis.com/translate_a/single"
_NOTICE = (
    "veriloqua: fast mode uses the free public Google Translate endpoint (your text is "
    "sent to Google). Set VERILOQUA_NO_THIRD_PARTY=1 to disable, or use a local agent "
    "CLI / keyed backend. (VERILOQUA_QUIET=1 silences this notice.)"
)
_notified = False  # module-level: emit the notice at most once per process


class GoogleFreeBackend:
    name = "google_free"
    third_party = True

    def __init__(
        self,
        *,
        allow_third_party: bool = True,
        no_third_party: bool = False,
        interactive: bool | None = None,
        timeout: float = 10.0,
        max_retries: int = 3,
        client: httpx.Client | None = None,
    ) -> None:
        self.no_third_party = no_third_party
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = client
        self._breaker_tripped = False

    def _ensure_consent(self) -> None:
        global _notified
        if self.no_third_party:
            raise ThirdPartyConsentRequired(
                "no_third_party guard is set — the free Google path is disabled. "
                "Use a local agent CLI (mode medium/high) or a keyed backend."
            )
        # zero-config: allowed by default, with a one-time transparent notice.
        if not _notified and not os.environ.get("VERILOQUA_QUIET"):
            sys.stderr.write(_NOTICE + "\n")
            _notified = True

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout, headers={"User-Agent": "veriloqua"})
        return self._client

    def translate(self, text: str, src_lang: str, tgt_lang: str) -> TranslationResult:
        if not text.strip():
            return TranslationResult(text="", detected_src=src_lang, engine=self.name)
        if self._breaker_tripped:
            raise RateLimited(
                "google_free is rate-limited (circuit open) — configure a keyed backend."
            )
        self._ensure_consent()

        params = {
            "client": "gtx",
            "sl": src_lang or "auto",
            "tl": tgt_lang,
            "dt": "t",
            "q": text,
        }
        delay = 0.5
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = self._http().get(_ENDPOINT, params=params)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                raise NetworkUnavailable("cannot reach the translation endpoint") from exc
            except httpx.HTTPError as exc:
                last_exc = exc
                time.sleep(_jittered(delay, attempt))
                continue

            if resp.status_code == 429 or _looks_like_captcha(resp.text):
                # bounded jittered backoff, then trip the breaker
                if attempt < self.max_retries - 1:
                    time.sleep(_jittered(delay, attempt))
                    continue
                self._breaker_tripped = True
                raise RateLimited(
                    "google_free rate-limited/captcha after retries — configure a keyed backend."
                )
            if resp.status_code >= 500:
                last_exc = httpx.HTTPStatusError("server error", request=resp.request, response=resp)
                time.sleep(_jittered(delay, attempt))
                continue
            if resp.status_code != 200:
                raise NetworkUnavailable(f"unexpected status {resp.status_code} from endpoint")

            translated, detected = _parse(resp)
            return TranslationResult(text=translated, detected_src=detected, engine=self.name)

        raise NetworkUnavailable(f"translation endpoint failed after retries: {last_exc}")


def _jittered(base: float, attempt: int) -> float:
    # deterministic-ish backoff without RNG dependency
    return min(base * (2**attempt), 8.0)


def _looks_like_captcha(body: str) -> bool:
    low = body[:400].lower()
    return "captcha" in low or "unusual traffic" in low or low.lstrip().startswith("<!doctype html")


def _parse(resp: httpx.Response) -> tuple[str, str]:
    try:
        data = resp.json()
    except Exception as exc:  # HTML/garbage body
        raise NetworkUnavailable("non-JSON response from translation endpoint") from exc
    if not isinstance(data, list) or not data or not isinstance(data[0], list):
        raise NetworkUnavailable("malformed response from translation endpoint")
    parts = [seg[0] for seg in data[0] if isinstance(seg, list) and seg and isinstance(seg[0], str)]
    translated = "".join(parts)
    detected = "auto"
    if len(data) > 2 and isinstance(data[2], str):
        detected = data[2]
    return translated, detected
