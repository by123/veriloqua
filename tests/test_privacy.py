"""Privacy guarantees are implemented, not just documented: the request log stores
scrubbed text, the DB is owner-only, third-party consent is enforced, and the
request log can be switched off entirely."""

from __future__ import annotations

import os
import stat
import sys

import pytest

from veriloqua import Translator
from veriloqua.backends.fakes import FakeLLMBackend, FakeMTBackend
from veriloqua.backends.google_free import GoogleFreeBackend
from veriloqua.errors import ThirdPartyConsentRequired

SENSITIVE = ("write to jane.doe@example.com and use key sk-abcdef123456789012 "
             "to pay card 4111 1111 1111 1111 today")


def test_request_log_stores_scrubbed_text(tmp_config):
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(),
                    llm_backend=FakeLLMBackend())
    r = tr.translate(SENSITIVE, to="zh", mode="auto")
    rec = tr._store.load_request(r.request_id)
    tr.close()

    assert rec is not None
    assert "jane.doe@example.com" not in rec.source_text
    assert "sk-abcdef123456789012" not in rec.source_text
    assert "4111 1111 1111 1111" not in rec.source_text
    assert "[REDACTED_EMAIL]" in rec.source_text


def test_request_log_can_be_fully_disabled(tmp_config):
    tmp_config.request_log_enabled = False
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(),
                    llm_backend=FakeLLMBackend())
    r = tr.translate("log nothing about this request please", to="zh", mode="auto")
    assert tr._store.load_request(r.request_id) is None
    tr.close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_memory_db_is_owner_only(tmp_config):
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend(),
                    llm_backend=FakeLLMBackend())
    tr.translate("hello there my friend how are you", to="zh", mode="auto")
    mode = stat.S_IMODE(os.stat(tmp_config.db_path).st_mode)
    tr.close()

    assert mode == 0o600


def test_withheld_consent_blocks_the_free_endpoint():
    b = GoogleFreeBackend(allow_third_party=False)
    with pytest.raises(ThirdPartyConsentRequired):
        b.translate("hello", "en", "zh")


def test_no_third_party_guard_blocks_the_free_endpoint():
    b = GoogleFreeBackend(no_third_party=True)
    with pytest.raises(ThirdPartyConsentRequired):
        b.translate("hello", "en", "zh")


def test_free_mt_text_travels_in_post_body_not_url():
    """The source text must never appear in the request URL (proxy/server logs)."""
    import httpx

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json=[[["你好", "hello", None, None]], None, "en"])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    b = GoogleFreeBackend(client=client)
    res = b.translate("hello", "en", "zh")

    assert res.text == "你好"
    assert seen["method"] == "POST"
    assert "hello" not in seen["url"]          # text is not in the URL
    assert "hello" in seen["body"]             # it rides in the body


def test_free_mt_refuses_oversized_text():
    from veriloqua.backends.google_free import MAX_FREE_CHARS
    from veriloqua.errors import VeriloquaError

    b = GoogleFreeBackend()
    with pytest.raises(VeriloquaError, match="at most"):
        b.translate("x" * (MAX_FREE_CHARS + 1), "en", "zh")
