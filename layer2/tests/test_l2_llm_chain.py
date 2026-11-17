"""The LLM fallback chain with a fake provider: no network, no budget used."""
import json

import pytest

from layer2 import adapters

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["a"],
          "properties": {"a": {"type": "string", "enum": ["x", "y"]}}}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setattr(adapters, "CACHE", tmp_path)
    monkeypatch.setattr(adapters, "llm_provider", lambda: "openrouter")
    monkeypatch.setattr(adapters, "audit", lambda event: None)
    monkeypatch.setattr(adapters.time, "sleep", lambda s: None)
    monkeypatch.setenv("L2_LLM_MODEL", "m1")
    monkeypatch.setenv("L2_LLM_FALLBACKS", "m2,m3")
    monkeypatch.setenv("L2_LLM_DAILY_CALLS", "10")
    monkeypatch.setitem(adapters._breaker, "failures", 0)
    monkeypatch.setitem(adapters._breaker, "open", False)
    calls = []

    def install(behaviour):   # behaviour: model -> list of results (exception or text), consumed in order
        def _call(prompt, system, schema, model, max_tokens):
            calls.append(model)
            r = behaviour[model].pop(0)
            if isinstance(r, Exception):
                raise r
            return r, 10, 5, model
        monkeypatch.setattr(adapters, "_openrouter", _call)
        return calls
    return install


def test_busy_model_is_retried_then_succeeds(fake):
    calls = fake({"m1": [adapters.LLMUnavailable("OpenRouter 429: busy"), json.dumps({"a": "x"})]})
    assert adapters.complete("q1", json_schema=SCHEMA) == {"a": "x"}
    assert calls == ["m1", "m1"]
    assert adapters._budget_left() == 9                    # one call counted, the 429 was not


def test_busy_then_next_model(fake):
    busy = adapters.LLMUnavailable("OpenRouter 429: busy")
    calls = fake({"m1": [busy, busy, busy], "m2": [json.dumps({"a": "y"})]})
    assert adapters.complete("q2", json_schema=SCHEMA) == {"a": "y"}
    assert calls == ["m1", "m1", "m1", "m2"]


def test_bad_json_moves_to_next_model_and_answer_is_cached(fake):
    calls = fake({"m1": ['{"a": "not-allowed"}'], "m2": ["no json at all"], "m3": [json.dumps({"a": "x"})]})
    assert adapters.complete("q3", json_schema=SCHEMA) == {"a": "x"}
    assert calls == ["m1", "m2", "m3"]
    assert adapters.complete("q3", json_schema=SCHEMA) == {"a": "x"}   # from the cache
    assert calls == ["m1", "m2", "m3"]


def test_budget_and_breaker(fake, monkeypatch):
    down = adapters.LLMUnavailable("OpenRouter 503: down")
    fake({m: [down] * 10 for m in ("m1", "m2", "m3")})
    for i in range(adapters.MAX_FAILURES):
        with pytest.raises(adapters.LLMUnavailable):
            adapters.complete(f"q4-{i}")
    assert adapters._breaker["open"]
    with pytest.raises(adapters.LLMUnavailable, match="switched off"):
        adapters.complete("q5")
    monkeypatch.setitem(adapters._breaker, "open", False)
    monkeypatch.setenv("L2_LLM_DAILY_CALLS", "0")
    with pytest.raises(adapters.LLMUnavailable, match="budget"):
        adapters.complete("q6")


def test_provider_daily_limit_stops_every_model_for_the_day(fake):
    day = adapters.LLMUnavailable('OpenRouter 429: {"message":"Rate limit exceeded: free-models-per-day"}')
    calls = fake({"m1": [day], "m2": [json.dumps({"a": "x"})]})
    with pytest.raises(adapters.LLMUnavailable, match="daily limit"):
        adapters.complete("q-day", json_schema=SCHEMA)
    assert calls == ["m1"]                       # no retry, no other model: they share the account's limit
    assert adapters._budget_left() == 0 and not adapters.llm_can_call()
    with pytest.raises(adapters.LLMUnavailable):                # a new prompt cannot be sent today…
        adapters.complete("q-new", json_schema=SCHEMA)


def test_cached_answer_survives_a_used_up_budget(fake):
    calls = fake({"m1": [json.dumps({"a": "x"})]})
    assert adapters.complete("q-cache", json_schema=SCHEMA) == {"a": "x"}
    state = adapters._budget()
    state["provider_limit"] = True
    adapters._save_budget(state)
    assert adapters.llm_available() and not adapters.llm_can_call()
    assert adapters.complete("q-cache", json_schema=SCHEMA) == {"a": "x"}   # …but a cached answer is still served
    assert calls == ["m1"]


def test_shared_gateway_daily_limit_shows_zero_calls_left(fake, monkeypatch):
    """Through the shared gateway the provider's daily limit arrives as an LLMError; the counter must learn it too,
    so the app says 0 calls left instead of 45."""
    import shared.llm
    monkeypatch.setattr(adapters, "llm_provider", lambda: "shared")

    def limited(*a, **k):
        raise shared.llm.LLMError("m1: daily limit reached (resets at 05:30 IST): free-models-per-day")
    monkeypatch.setattr(shared.llm, "complete", limited)
    with pytest.raises(adapters.LLMUnavailable):
        adapters.complete("q")
    assert adapters._budget_left() == 0 and "free daily limit" in adapters.llm_status()


def test_dotenv_references_are_expanded(tmp_path, monkeypatch):
    """LLM_API_KEY=${OPENROUTER_API_KEY} must give the key, not the literal text (OpenRouter then said
    "Missing Authentication header")."""
    (tmp_path / ".env").write_text("L2T_KEY=sk-or-abc\nL2T_COPY=${L2T_KEY}\nL2T_QUOTED=\"q v\"\n")
    for k in ("L2T_KEY", "L2T_COPY", "L2T_QUOTED"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(adapters, "REPO", tmp_path)
    adapters._load_dotenv()
    import os
    assert os.environ["L2T_COPY"] == "sk-or-abc" and os.environ["L2T_QUOTED"] == "q v"
    for k in ("L2T_KEY", "L2T_COPY", "L2T_QUOTED"):
        monkeypatch.delenv(k, raising=False)


def test_budget_follows_the_key_in_use(fake, monkeypatch):
    """Switching the gateway to another provider (OpenRouter used up -> Groq) must not inherit the old limit."""
    monkeypatch.setattr(adapters, "llm_provider", lambda: "shared")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-used-up")
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-or-used-up")
    adapters._provider_day_limit("free-models-per-day")
    assert adapters._budget_left() == 0
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_fresh")
    monkeypatch.delenv("LLM_BASE_URL")
    assert adapters._budget_left() == 10                    # L2_LLM_DAILY_CALLS in the fixture
