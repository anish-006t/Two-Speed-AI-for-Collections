"""shared/llm.py: disk cache, structured output, call log, offline mode. No network: _call_api is faked."""
import json
import os

import pytest

from shared import config, llm


@pytest.fixture
def fake_api(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "llm_cache"))
    monkeypatch.setenv("LLM_OFFLINE", "0")
    calls = []

    def fake(model, system, prompt, json_schema, max_tokens):
        calls.append(prompt)
        text = json.dumps({"answer": "yes"}) if json_schema else f"echo: {prompt}"
        return {"text": text, "model": model, "stop_reason": "end_turn", "input_tokens": 10, "output_tokens": 3}

    monkeypatch.setattr(llm, "_call_api", fake)
    return calls


def log_lines():
    return [json.loads(x) for x in config.call_log_path().read_text().splitlines()]


def test_second_identical_call_is_a_cache_hit(fake_api):
    assert llm.complete("hello", tag="t") == "echo: hello"
    assert llm.complete("hello", tag="t") == "echo: hello"
    assert len(fake_api) == 1
    assert [x["cache_hit"] for x in log_lines()] == [False, True]


@pytest.mark.parametrize("change", [{"model": "smart"}, {"system": "be brief"}, {"max_tokens": 99},
                                    {"json_schema": {"type": "object"}}])
def test_any_input_change_misses_the_cache(fake_api, change):
    llm.complete("hello")
    llm.complete("hello", **change)
    assert len(fake_api) == 2


def test_json_schema_returns_a_dict(fake_api):
    out = llm.complete("q", json_schema={"type": "object", "properties": {"answer": {"type": "string"}},
                                         "required": ["answer"], "additionalProperties": False})
    assert out == {"answer": "yes"}


def test_call_log_has_tokens_and_no_prompt_text(fake_api):
    llm.complete("SECRET-TEXT-123", tag="l4.test")
    raw = config.call_log_path().read_text()
    assert "SECRET-TEXT-123" not in raw
    line = log_lines()[0]
    assert {"ts", "tag", "model", "cache_hit", "input_tokens", "output_tokens", "latency_ms"} <= set(line)
    assert line["tag"] == "l4.test" and line["input_tokens"] == 10


def test_offline_mode_raises_on_a_miss_and_serves_hits(fake_api, monkeypatch):
    llm.complete("cached")
    monkeypatch.setenv("LLM_OFFLINE", "1")
    assert llm.complete("cached") == "echo: cached"
    with pytest.raises(llm.LLMCacheMiss):
        llm.complete("never seen")
    assert len(fake_api) == 1


def test_refusal_is_raised_and_not_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "c"))
    monkeypatch.setattr(llm, "_call_api", lambda **k: {"text": "", "model": k["model"], "stop_reason": "refusal",
                                                        "input_tokens": 5, "output_tokens": 0})
    with pytest.raises(llm.LLMRefusal):
        llm.complete("x")
    assert not any(p.suffix == ".json" for p in (tmp_path / "c").rglob("*"))


def test_model_aliases(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_FAST", "fast-model")
    monkeypatch.setenv("LLM_MODEL_SMART", "smart-model")
    assert llm.resolve_model(None) == "fast-model"
    assert llm.resolve_model("smart") == "smart-model"
    assert llm.resolve_model("claude-opus-5-5") == "claude-opus-5-5"


def test_usage_summary_groups_by_tag(fake_api):
    llm.complete("a", tag="x")
    llm.complete("a", tag="x")
    llm.complete("b", tag="y")
    rows = {r["tag"]: r for r in llm.usage_summary()}
    assert rows["x"]["calls"] == 2 and rows["x"]["cache_hits"] == 1 and rows["y"]["calls"] == 1


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("RUN_LIVE_LLM") != "1",
                    reason="live call: set ANTHROPIC_API_KEY and RUN_LIVE_LLM=1")
def test_live_smoke(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "live"))
    assert "ok" in llm.complete("Reply with the word ok and nothing else.", max_tokens=16).lower()


def test_missing_credentials_is_a_clear_llm_error(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "c"))
    monkeypatch.setattr(llm, "_client", None)
    with pytest.raises(llm.LLMError, match="ANTHROPIC_API_KEY"):
        llm.complete("no key here")


# ------------------------------------------------------------------ OpenAI-compatible providers (Groq and others)

class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        import io
        return io.BytesIO(json.dumps(self.payload).encode())

    def __exit__(self, *a):
        return False


@pytest.fixture
def groq(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MIN_INTERVAL_S", "0")
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "c"))
    monkeypatch.delenv("LLM_MODEL_FAST", raising=False)
    monkeypatch.delenv("LLM_MODEL_SMART", raising=False)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    sent = []

    def install(*responses):
        queue = list(responses)

        def urlopen(req, timeout=None):
            sent.append(json.loads(req.data))
            r = queue.pop(0)
            if isinstance(r, Exception):
                raise r
            return _Resp(r)
        monkeypatch.setattr("urllib.request.urlopen", urlopen)
        return sent
    return install


def _ok(content, finish="stop"):
    return {"model": "llama", "choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4}}


def test_groq_text_call_uses_provider_models_and_is_cached(groq):
    sent = groq(_ok("hello"))
    assert llm.complete("hi") == "hello" and llm.complete("hi") == "hello"     # second is a cache hit
    assert len(sent) == 1 and sent[0]["model"] == "openai/gpt-oss-20b" and sent[0]["reasoning_effort"] == "low"
    assert llm.resolve_model("smart") == "openai/gpt-oss-120b"


def test_groq_json_mode_checks_the_schema(groq):
    schema = {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string",
                                                                                 "enum": ["yes", "no"]}}}
    sent = groq(_ok('Sure! {"answer": "yes"}'), _ok('{"answer": "maybe"}'))
    assert llm.complete("q1", json_schema=schema) == {"answer": "yes"}
    assert sent[0]["response_format"] == {"type": "json_object"} and "JSON schema" in sent[0]["messages"][0]["content"]
    with pytest.raises(llm.LLMError, match="doesn't match the schema"):
        llm.complete("q2", json_schema=schema)


def test_groq_rate_limit_is_retried(groq):
    import urllib.error
    limited = urllib.error.HTTPError("u", 429, "rate", {"retry-after": "1"}, None)
    limited.read = lambda: b"slow down"
    sent = groq(limited, _ok("done"))
    assert llm.complete("x") == "done" and len(sent) == 2


def test_a_daily_limit_fails_at_once_instead_of_retrying_for_minutes(groq):
    """OpenRouter's free tier answers 429 "free-models-per-day" until 05:30 IST: retrying only freezes the app."""
    import urllib.error
    daily = urllib.error.HTTPError("u", 429, "rate", {}, None)
    daily.read = lambda: b'{"error":{"message":"Rate limit exceeded: free-models-per-day. Add 10 credits"}}'
    sent = groq(daily, _ok("never reached"))
    with pytest.raises(llm.LLMError, match="daily limit"):
        llm.complete("x")
    assert len(sent) == 1


def test_groq_without_a_key_is_a_clear_error(groq, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY")
    with pytest.raises(llm.LLMError, match="No API key for LLM_PROVIDER=groq"):
        llm.complete("x")


def test_parse_json_tolerates_fences():
    assert llm.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        llm.parse_json("no json here")
