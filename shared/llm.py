"""The only way to call an LLM in this repo (team rule 1). Same input, same output: every answer is cached on disk, so
reruns are free and repeatable, and every call (cache hit or not) is logged with model, tokens and latency.

    from shared.llm import complete
    text = complete("Summarise this redacted note: ...", model="fast", tag="l2.policy")
    data = complete(prompt, model="smart", json_schema=SCHEMA, tag="l4.summary")   # -> dict matching SCHEMA

Send redacted text only (silver.*_redacted), never raw PII or whole customer tables. The call log holds no prompt
text, only a hash of it. `json_schema` uses the API's structured outputs: every object in it needs
"additionalProperties": false and a "required" list.
"""
import hashlib
import json
import os
import tempfile
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone

from shared import config


class LLMError(RuntimeError):
    pass


class LLMCacheMiss(LLMError):
    """LLM_OFFLINE=1 and the answer is not in the cache."""


class LLMRefusal(LLMError):
    """The model declined (stop_reason 'refusal'). Not cached."""


# Models that accept the server-side refusal fallback ("fallbacks": "default"); others are called plainly.
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-1"}
_client = None
_client_lock = threading.Lock()
_log_lock = threading.Lock()


def resolve_model(model: str | None) -> str:
    if model in (None, "", "fast"):
        return config.model_fast()
    if model == "smart":
        return config.model_smart()
    return model


def cache_key(request: dict) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def complete(prompt: str, model: str | None = None, system: str | None = None, json_schema: dict | None = None,
             max_tokens: int = 1024, tag: str = "") -> str | dict:
    """Text answer, or a dict when json_schema is given. model: None/'fast', 'smart', or an exact model ID."""
    model = resolve_model(model)
    request = {"model": model, "system": system, "prompt": prompt, "json_schema": json_schema,
               "max_tokens": max_tokens}
    if config.llm_provider() != "anthropic":  # keeps existing Anthropic cache keys valid
        request["provider"] = config.llm_provider()
    key = cache_key(request)
    path = config.cache_dir() / key[:2] / f"{key}.json"
    started = time.perf_counter()

    if path.exists():
        record = json.loads(path.read_text(encoding="utf-8"))
        _log(tag, model, key, cache_hit=True, started=started)
        return record["output"]
    if config.offline():
        _log(tag, model, key, cache_hit=False, started=started, status="offline_miss")
        raise LLMCacheMiss(f"LLM_OFFLINE=1 and no cached answer for {tag or 'this call'} ({key[:12]})")

    result = _call_api(model=model, system=system, prompt=prompt, json_schema=json_schema, max_tokens=max_tokens)
    usage = {"input_tokens": result["input_tokens"], "output_tokens": result["output_tokens"]}
    if result["stop_reason"] == "refusal":
        _log(tag, model, key, cache_hit=False, started=started, status="refusal", **usage)
        raise LLMRefusal(f"{model} declined the request ({tag or 'untagged'})")
    if json_schema is not None and result["stop_reason"] == "max_tokens":
        _log(tag, model, key, cache_hit=False, started=started, status="truncated", **usage)
        raise LLMError(f"Structured answer cut off at max_tokens={max_tokens}; raise it ({tag or 'untagged'})")

    try:
        output = parse_json(result["text"], json_schema) if json_schema is not None else result["text"]
    except ValueError as e:  # not cached, so a rerun tries again
        _log(tag, model, key, cache_hit=False, started=started, status="bad_json", **usage)
        raise LLMError(f"{model} returned an answer that doesn't match the schema ({tag or 'untagged'}): {e}") from e
    _write_atomic(path, {"key": key, "model": result["model"], "output": output, "usage": usage,
                         "stop_reason": result["stop_reason"], "created_at": _now()})
    _log(tag, model, key, cache_hit=False, started=started, **usage)
    return output


NO_CREDENTIALS = "No Anthropic credentials: set ANTHROPIC_API_KEY in .env (see .env.example)"


def _get_client():
    global _client
    with _client_lock:
        if _client is None:
            import anthropic
            try:
                _client = anthropic.Anthropic()
            except (anthropic.AnthropicError, TypeError) as e:
                raise LLMError(NO_CREDENTIALS) from e
        return _client


def _call_api(model: str, system: str | None, prompt: str, json_schema: dict | None, max_tokens: int) -> dict:
    """One API call to the configured provider. Tests replace this function, so it must return plain data. Every
    failure (no credentials, rate limit after retries, network, bad request) surfaces as LLMError."""
    if config.llm_provider() == "anthropic":
        return _call_anthropic(model, system, prompt, json_schema, max_tokens)
    return _call_openai_compatible(model, system, prompt, json_schema, max_tokens)


def parse_json(text: str, schema: dict | None = None):
    """The JSON object in a model's answer (tolerates code fences or a sentence around it), checked against the
    schema's required keys and enums."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[s.find("{"):] if "{" in s else s
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the answer")
    data = json.loads(s[start:end + 1])
    if schema:
        _check(data, schema, "$")
    return data


def _check(value, schema: dict, path: str) -> None:
    if schema.get("type") == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path}: expected an object")
        for k in schema.get("required", []):
            if k not in value:
                raise ValueError(f"{path}: missing {k}")
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                _check(value[k], sub, f"{path}.{k}")
    elif "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: {value!r} is not one of {schema['enum']}")


# Cloudflare in front of some providers (Groq) rejects Python's default urllib user agent with "error code: 1010".
USER_AGENT = "dhandho-shared-llm/1.0"
_pace_lock = threading.Lock()
_last_call = [0.0]


def _call_openai_compatible(model, system, prompt, json_schema, max_tokens) -> dict:
    """Groq, Cerebras, Ollama or any OpenAI-compatible chat API. Structured output uses JSON mode with the schema in
    the system prompt (supported by every one of them); the answer is checked against the schema afterwards.
    Calls are paced (LLM_MIN_INTERVAL_S) and retried on rate limits, so free tiers are not exceeded."""
    import urllib.error
    import urllib.request
    s = config.provider_settings()
    if not s["base_url"]:
        raise LLMError("LLM_BASE_URL is not set for LLM_PROVIDER=openai_compatible")
    if not s["api_key"]:
        raise LLMError(f"No API key for LLM_PROVIDER={config.llm_provider()}: set it in .env (see .env.example)")
    sys_text = system or ""
    body = {"model": model, "max_tokens": max_tokens, "temperature": 0}
    if "gpt-oss" in model:  # reasoning models: thinking counts against max_tokens, so keep it short and leave room
        body["reasoning_effort"] = "low"
        body["max_tokens"] = max_tokens + 1024
    if json_schema is not None:
        sys_text = (sys_text + "\n\nReply with one JSON object only, no other text, matching this JSON schema:\n"
                    + json.dumps(json_schema)).strip()
        body["response_format"] = {"type": "json_object"}
    body["messages"] = ([{"role": "system", "content": sys_text}] if sys_text else []) + \
        [{"role": "user", "content": prompt}]
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {s['api_key']}", "User-Agent": USER_AGENT}
    waits = [2, 5, 10, 20, 30, 45, 60, 60, 60, 60]  # free tiers cap tokens per minute: be patient, never fail fast
    for attempt in range(len(waits) + 1):
        with _pace_lock:  # at most one call per min_interval_s across threads
            gap = s["min_interval_s"] - (time.monotonic() - _last_call[0])
            if gap > 0:
                time.sleep(gap)
            _last_call[0] = time.monotonic()
        req = urllib.request.Request(f"{s['base_url']}/chat/completions", data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                out = json.load(r)
            break
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode(errors="ignore")
            if e.code == 429 and ("per-day" in detail or "per day" in detail.lower()):
                # a provider's daily quota (OpenRouter free tier: 50 a day per account) only resets the next day:
                # retrying would freeze the app for minutes, so fail now and let callers use their rules
                raise LLMError(f"{model}: daily limit reached (resets at 05:30 IST): {detail}") from e
            if e.code in (429, 500, 502, 503) and attempt < len(waits):
                retry_after = e.headers.get("retry-after")
                time.sleep(float(retry_after) if retry_after and retry_after.replace(".", "").isdigit()
                           else waits[attempt])
                continue
            raise LLMError(f"{model}: API error {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < len(waits):
                time.sleep(waits[attempt])
                continue
            raise LLMError(f"{model}: connection error: {e}") from e
    choice = out["choices"][0]
    usage = out.get("usage") or {}
    finish = choice.get("finish_reason")
    return {"text": choice["message"].get("content") or "", "model": out.get("model", model),
            "stop_reason": "max_tokens" if finish == "length" else finish or "stop",
            "input_tokens": usage.get("prompt_tokens") or 0, "output_tokens": usage.get("completion_tokens") or 0}


def _call_anthropic(model: str, system: str | None, prompt: str, json_schema: dict | None, max_tokens: int) -> dict:
    import anthropic
    client = _get_client()
    kwargs = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    if system:
        kwargs["system"] = system
    if json_schema is not None:
        kwargs["output_config"] = {"format": {"type": "json_schema", "schema": json_schema}}
    try:
        if model in _FALLBACK_MODELS:
            response = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                                                   **kwargs)
        else:
            response = client.messages.create(**kwargs)
    except anthropic.APIStatusError as e:
        raise LLMError(f"{model}: API error {e.status_code}: {e.message} (request {e.request_id})") from e
    except anthropic.APIConnectionError as e:
        raise LLMError(f"{model}: connection error: {e}") from e
    except TypeError as e:  # the SDK resolves credentials lazily and reports a missing key as a TypeError
        if "authentication" not in str(e).lower():
            raise
        raise LLMError(NO_CREDENTIALS) from e
    text = "".join(block.text for block in response.content if block.type == "text")
    return {"text": text, "model": response.model, "stop_reason": response.stop_reason,
            "input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}


def _write_atomic(path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False)
    try:
        os.replace(tmp, path)
    except PermissionError:  # Windows: another thread is writing the same answer right now; keep theirs
        os.remove(tmp)


def _log(tag, model, key, cache_hit, started, status="ok", input_tokens=0, output_tokens=0) -> None:
    line = {"ts": _now(), "tag": tag, "model": model, "key": key[:16], "cache_hit": cache_hit, "status": status,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
    path = config.call_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _log_lock, open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def usage_summary() -> list[dict]:
    """Calls, cache hits and tokens per tag, from the call log (for the app's status panel and cost checks)."""
    path = config.call_log_path()
    if not path.exists():
        return []
    totals = defaultdict(lambda: {"calls": 0, "cache_hits": 0, "input_tokens": 0, "output_tokens": 0})
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        line = json.loads(raw)
        t = totals[(line["tag"] or "untagged", line["model"])]
        t["calls"] += 1
        t["cache_hits"] += line["cache_hit"]
        t["input_tokens"] += line["input_tokens"]
        t["output_tokens"] += line["output_tokens"]
    return [{"tag": tag, "model": model, **t} for (tag, model), t in sorted(totals.items())]


def check() -> None:
    """`uv run python -m shared.llm`: confirm the configured provider works (one tiny call) and list its models."""
    import urllib.request
    p = config.llm_provider()
    print(f"provider {p}; fast model {config.model_fast()}; smart model {config.model_smart()}")
    if p != "anthropic":
        s = config.provider_settings()
        try:
            req = urllib.request.Request(f"{s['base_url']}/models",
                                         headers={"Authorization": f"Bearer {s['api_key']}", "User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as r:
                ids = sorted(m["id"] for m in json.load(r).get("data", []))
            print(f"{len(ids)} models available: {', '.join(ids)}")
            for name in (config.model_fast(), config.model_smart()):
                print(f"  {name}: {'available' if name in ids else 'NOT AVAILABLE, set LLM_MODEL_FAST/SMART in .env'}")
        except Exception as e:  # noqa: BLE001 - this is a diagnostic
            print(f"could not list models: {e}")
    started = time.perf_counter()
    reply = complete(f"Reply with the single word ok. ({_now()})", max_tokens=8, tag="shared.check")
    print(f"test call answered {reply!r} in {time.perf_counter() - started:.1f}s")


if __name__ == "__main__":
    check()
