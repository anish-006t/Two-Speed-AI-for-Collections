"""Layer 4 tests never call a real LLM: no provider key is visible, Layer 3's extractor runs its offline rules, and
tests that exercise the gateway fake the network themselves. Settings the app changes are restored after each test."""
import pytest


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    for key in ("GROQ_API_KEY", "CEREBRAS_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "LLM_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("L3_EXTRACTOR_MODE", "rules")
    monkeypatch.setenv("LLM_OFFLINE", "0")
