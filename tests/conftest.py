from __future__ import annotations

import pytest

from veriloqua.backends.fakes import FakeLLMBackend, FakeMTBackend
from veriloqua.config import Config


@pytest.fixture
def tmp_config(tmp_path) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "cfg",
        request_log_max=1000,
        request_log_days=30,
        allow_same_model_judge=True,
    )


@pytest.fixture
def fake_mt() -> FakeMTBackend:
    return FakeMTBackend()


def make_llm(respond=None) -> FakeLLMBackend:
    return FakeLLMBackend(respond=respond)
