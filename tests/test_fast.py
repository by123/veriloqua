from __future__ import annotations

from veriloqua import Translator
from veriloqua.backends.fakes import FakeMTBackend


def test_fast_basic(tmp_config):
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend({"Hello": "Hola"}))
    r = tr.translate("Hello", to="es", mode="fast")
    assert r.text == "Hola"
    assert r.confidence == 0.35
    assert r.request_id
    tr.close()


def test_fast_placeholders_preserved(tmp_config):
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend())
    r = tr.translate("Hi {name}, %s items and <b>bold</b>", to="es", mode="fast")
    assert "{name}" in r.text
    assert "%s" in r.text
    assert "<b>" in r.text
    tr.close()


def test_fast_invariant_lock_applied(tmp_config):
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend())
    tr.add_term(source="New York", target="Nueva York", src="en", tgt="es", invariant=True)
    r = tr.translate("I love New York", to="es", mode="fast")
    assert "Nueva York" in r.text
    tr.close()


def test_fast_never_echoes_via_validator(tmp_config):
    # MT that returns the input unchanged across a script boundary → flagged, not passed silently
    tr = Translator(config=tmp_config, mt_backend=FakeMTBackend({"你好": "你好"}))
    r = tr.translate("你好", to="en", source="zh", mode="fast")
    assert r.status.value == "uncertain"
    tr.close()
