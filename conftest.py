"""Repo-wide test guard: tests never call a real LLM API and never touch the real LLM cache or call log.

With a key in .env, any test that reached the extractor in "auto" mode used to call the provider for real (and could
send redacted customer text from the sample database). Every test now runs with the LLM credentials and provider
settings removed from the environment, and with its own empty cache directory: a call that a test does not fake
fails with a "no credentials" LLMError (the Layer 3 extractor then uses its rules), and the developer's .env cannot
change test results. To run live LLM checks on purpose:
    PYTEST_ALLOW_LIVE_LLM=1 uv run pytest ...
"""
import os
from pathlib import Path

import pytest


def pytest_configure(config):
    # pyproject.toml sets --basetemp=lake/tmp/pytest; lake/ is git-ignored, so a fresh clone needs its parent created
    if config.option.basetemp:
        Path(config.option.basetemp).parent.mkdir(parents=True, exist_ok=True)

LLM_SETTINGS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "ANTHROPIC_BASE_URL",
                "LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY", "JEV_API_KEY",
                "LLM_MIN_INTERVAL_S",
                "LLM_MODEL_FAST", "LLM_MODEL_SMART", "LLM_OFFLINE")


@pytest.fixture(autouse=True)
def _no_live_llm_calls(monkeypatch, tmp_path):
    if os.environ.get("PYTEST_ALLOW_LIVE_LLM") != "1":
        for name in LLM_SETTINGS:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "llm_cache"))   # call log sits beside it, also per test
        monkeypatch.setattr("shared.llm._client", None, raising=False)
    yield
