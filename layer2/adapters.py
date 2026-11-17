"""Layer 2's only door to other layers and to AI models (architecture §10).

Each function uses the team version when it exists (shared/*, layer3, access views) and a Layer 2 fallback until
then, so Layer 2 never waits for, or changes, another layer. Every result says which engine produced it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "lake" / "layer2_cache"
AUDIT_LOG = REPO / "lake" / "layer2_audit.jsonl"


class LLMUnavailable(RuntimeError):
    """No LLM is configured (no shared gateway, no API key, or the SDK is missing)."""


class DecisionUnavailable(RuntimeError):
    """No typed-decision engine could answer (no Laya and no LLM)."""


def _load_dotenv() -> None:
    """Same reader as shared/config.py (python-dotenv), so ${VAR} references and quotes are handled the same way;
    real environment variables win."""
    env = REPO / ".env"
    if not env.exists():
        return
    from dotenv import load_dotenv
    load_dotenv(env, override=False)


_load_dotenv()


def _hash(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def _cache_get(kind: str, key: str):
    path = CACHE / kind / f"{key}.json"
    return json.loads(path.read_text()) if path.exists() else None


def _cache_put(kind: str, key: str, value) -> None:
    path = CACHE / kind / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, default=str))


# --------------------------------------------------------------------------------------------- database

def db_path() -> Path:
    """DHANDHO_DB if set, else the full database, else the 50K-customer sample."""
    if os.environ.get("DHANDHO_DB"):
        p = Path(os.environ["DHANDHO_DB"])
        return p if p.is_absolute() else REPO / p
    full, dev = REPO / "lake" / "maple.duckdb", REPO / "lake" / "dev.duckdb"
    return full if full.exists() or not dev.exists() else dev


def connect(read_only: bool = True, path: str | Path | None = None):
    """DuckDB connection with the team settings (shared.db when it exists)."""
    if path is None:
        try:
            from shared.db import connect as shared_connect  # type: ignore
            return shared_connect(read_only=read_only)
        except (ImportError, AttributeError):
            pass
    import duckdb
    target = Path(path) if path else db_path()
    con = duckdb.connect(str(target), read_only=read_only)
    con.execute(f"SET memory_limit = '{os.environ.get('DUCKDB_MEMORY_LIMIT', '4GB')}'")
    con.execute(f"SET threads = {int(os.environ.get('DUCKDB_THREADS', '8'))}")
    return con


# --------------------------------------------------------------------------------------------- LLM

def llm_provider() -> str | None:
    """shared (team gateway) > openrouter (OPENROUTER_API_KEY) > anthropic (ANTHROPIC_API_KEY) > None."""
    try:
        import shared.llm  # type: ignore # noqa: F401
        return "shared"
    except ImportError:
        pass
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return None
    return "anthropic" if (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")) else None


def llm_status() -> str:
    """Why the LLM is or is not usable, in plain words (for the app)."""
    if llm_provider() is None:
        return "no API key configured"
    if _breaker["open"]:
        return "switched off after repeated failures; restart the app to retry"
    if _budget().get("provider_limit"):
        return "the AI provider's free daily limit (50 requests per account) is used up; it resets at 05:30 IST"
    if _budget_left() <= 0:
        return (f"today's call cap L2_LLM_DAILY_CALLS={os.environ.get('L2_LLM_DAILY_CALLS', '45')} is used; "
                "it resets at 05:30 IST")
    return f"{_budget_left()} calls left today"


def llm_available() -> bool:
    """An LLM is configured and not switched off. The daily budget is enforced inside complete(), after the cache
    lookup, so cached answers keep working when the budget is used up (the 19:00 rerun of released questions must
    not lose them); an uncached call then raises LLMUnavailable and the lane falls back to its rules."""
    return llm_provider() is not None and not _breaker["open"]


def llm_can_call() -> bool:
    """A NEW (uncached) call is possible right now: configured, not switched off and budget left."""
    return llm_available() and _budget_left() > 0


# Guardrails on availability: a daily call budget (OpenRouter's free tier allows ~50 requests a day) and a
# circuit breaker that stops calling after repeated failures in one process, so Layer 2 falls back to rules
# instead of hammering a provider that is down or out of quota.
_breaker = {"failures": 0, "open": False}
MAX_FAILURES = int(os.environ.get("L2_LLM_MAX_FAILURES", "3"))


def _active_key() -> str:
    """The key the next call will use: through the shared gateway, its provider's key (Groq, OpenRouter, ...), so a
    used-up limit on one provider is not carried over to another."""
    if llm_provider() == "shared":
        try:
            from shared import config as shared_config  # type: ignore
            if shared_config.llm_provider() == "anthropic":
                return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or ""
            return shared_config.provider_settings()["api_key"] or ""
        except Exception:
            pass
    return os.environ.get("OPENROUTER_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or ""


def _budget_file() -> Path:
    # UTC day: OpenRouter's free limit resets at 00:00 UTC (05:30 IST), so the counter must too
    # one counter per API key (a short fingerprint, never the key) so a new key starts fresh
    key = _active_key()
    tag = hashlib.sha256(key.encode()).hexdigest()[:8] if key else "nokey"
    return CACHE / "llm_budget" / f"{time.strftime('%Y-%m-%d', time.gmtime())}-{tag}.json"


def _budget() -> dict:
    f = _budget_file()
    return json.loads(f.read_text()) if f.exists() else {"calls": 0}


def _save_budget(state: dict) -> None:
    f = _budget_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(state))


def _budget_left() -> int:
    """Calls left today: 0 once the provider has said its own daily limit is reached (it is per account, so a new
    key on the same account does not reset it)."""
    state = _budget()
    if state.get("provider_limit"):
        return 0
    return int(os.environ.get("L2_LLM_DAILY_CALLS", "45")) - state["calls"]


def _spend_call() -> None:
    state = _budget()
    state["calls"] += 1
    _save_budget(state)


def _refund_call() -> None:
    state = _budget()
    state["calls"] = max(0, state["calls"] - 1)
    _save_budget(state)


def _provider_day_limit(error: str) -> bool:
    """OpenRouter's free tier: 50 requests a day per account ("free-models-per-day"); every free model shares it."""
    if "per-day" in error or "per day" in error.lower():
        state = _budget()
        state["provider_limit"] = True
        _save_budget(state)
        return True
    return False


def _validate(value, schema: dict, path: str = "$") -> None:
    """Minimal JSON-schema check (types, required, enum, additionalProperties) so a malformed answer never flows on."""
    t = schema.get("type")
    types = t if isinstance(t, list) else [t] if t else []
    ok = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}
    if types:
        good = any((tt == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
                   or (tt == "integer" and isinstance(value, int) and not isinstance(value, bool))
                   or (tt in ok and isinstance(value, ok[tt])) for tt in types)
        if not good:
            raise ValueError(f"{path}: expected {types}, got {type(value).__name__}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: {value!r} not in the allowed values")
    if isinstance(value, dict):
        for r in schema.get("required", []):
            if r not in value:
                raise ValueError(f"{path}: missing {r}")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(props)
            if extra:
                raise ValueError(f"{path}: unexpected {sorted(extra)}")
        for k, v in value.items():
            if k in props:
                _validate(v, props[k], f"{path}.{k}")
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            _validate(v, schema["items"], f"{path}[{i}]")


def _parse_json(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1] if start >= 0 and end > start else text)


def _openrouter(prompt, system, json_schema, model, max_tokens):
    import urllib.error
    import urllib.request
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0}
    if json_schema:
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": "answer", "strict": True, "schema": json_schema}}
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                                          "Content-Type": "application/json",
                                          "X-Title": "dhandho Layer 2"})
    try:
        with urllib.request.urlopen(req, timeout=int(os.environ.get("L2_LLM_TIMEOUT", "90"))) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise LLMUnavailable(f"OpenRouter {e.code}: {e.read()[:200]!r}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise LLMUnavailable(f"OpenRouter connection error: {e}") from e
    if "error" in data:
        raise LLMUnavailable(f"OpenRouter error: {str(data['error'])[:200]}")
    msg = data["choices"][0]["message"]
    text = msg.get("content") or ""
    if not text.strip():
        raise LLMUnavailable("empty answer")
    usage = data.get("usage", {})
    return text, usage.get("prompt_tokens"), usage.get("completion_tokens"), data.get("model", model)


def _anthropic(prompt, system, json_schema, model, max_tokens):
    import anthropic
    client = anthropic.Anthropic()
    kwargs = dict(model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
    if system:
        kwargs["system"] = system
    if json_schema:
        kwargs["output_config"] = {"format": {"type": "json_schema", "schema": json_schema}}
    try:
        response = client.messages.create(**kwargs)
    except anthropic.APIConnectionError as e:
        raise LLMUnavailable(f"connection error: {e}") from e
    except anthropic.APIStatusError as e:
        raise LLMUnavailable(f"API error {e.status_code}: {e.message}") from e
    if response.stop_reason == "refusal":
        raise LLMUnavailable("model declined the request")
    text = next((b.text for b in response.content if b.type == "text"), "")
    return text, response.usage.input_tokens, response.usage.output_tokens, model


def complete(prompt: str, system: str | None = None, json_schema: dict | None = None, model: str | None = None,
             max_tokens: int = 4000, fast: bool = False):
    """Text (or a schema-checked dict when json_schema is given). Cached on disk: same input, same output.

    Provider: shared/llm.py > OpenRouter > Anthropic. On OpenRouter the model comes from L2_LLM_MODEL (or
    L2_LLM_MODEL_FAST when fast=True), with L2_LLM_FALLBACKS tried in order when a model is busy or fails.
    Raises LLMUnavailable when nothing can answer; callers then use their rule-based path."""
    provider = llm_provider()
    if provider == "shared":
        from shared.llm import LLMError  # type: ignore
        from shared.llm import complete as shared_complete  # type: ignore
        try:  # any gateway failure (no key, cache-only miss, rate limit) means "use the rule-based path"
            return shared_complete(prompt, model=model or ("fast" if fast else "smart"), system=system,
                                   json_schema=json_schema, max_tokens=max_tokens, tag="l2.ask")
        except LLMError as e:
            _provider_day_limit(str(e))   # a daily-quota error: the app then shows 0 calls left, not 45
            raise LLMUnavailable(str(e)) from e
    if provider == "openrouter":
        primary = (os.environ.get("L2_LLM_MODEL_FAST") if fast else None) or os.environ.get(
            "L2_LLM_MODEL", "qwen/qwen3.8-27b:free")
        models = [primary] + [m.strip() for m in os.environ.get(
            "L2_LLM_FALLBACKS", "nvidia/nemotron-3-super-120b-a12b:free,google/gemma-4-31b-it:free").split(",")
            if m.strip() and m.strip() != primary]
    else:
        models = [model or os.environ.get("LLM_MODEL_FAST" if fast else "LLM_MODEL_SMART", "claude-sonnet-5-5")]
    for m in models:   # a cached answer from any model in the chain is reused
        cached = _cache_get("llm", _hash("llm", m, system, prompt, json_schema))
        if cached is not None:
            return cached["output"]
    if provider is None:
        raise LLMUnavailable("no LLM configured (shared/llm.py, OPENROUTER_API_KEY or ANTHROPIC_API_KEY)")
    if _breaker["open"]:
        raise LLMUnavailable("LLM switched off after repeated failures in this run")
    errors = []
    for m in models:
        if _budget_left() <= 0:
            raise LLMUnavailable("daily LLM call budget used (L2_LLM_DAILY_CALLS)")
        _spend_call()
        start = time.time()
        try:
            call = _openrouter if provider == "openrouter" else _anthropic
            waits = [float(x) for x in os.environ.get("L2_LLM_RETRY_429", "2,5").split(",") if x.strip()]
            for attempt in range(len(waits) + 1):   # a busy free model often frees up within seconds
                try:
                    text, tin, tout, served = call(prompt, system, json_schema, m, max_tokens)
                    break
                except LLMUnavailable as e:
                    if " 429" not in str(e) or attempt == len(waits) or _provider_day_limit(str(e)):
                        raise
                    time.sleep(waits[attempt])
            output = text
            if json_schema:
                output = _parse_json(text)
                _validate(output, json_schema)
        except (LLMUnavailable, ValueError, json.JSONDecodeError) as e:
            if " 429" in str(e):   # rate-limited upstream: nothing was answered, so it does not use the budget
                _refund_call()
            errors.append(f"{m}: {e}")
            audit({"action": "llm_call", "model": m, "ok": False, "error": str(e)[:300]})
            if _budget().get("provider_limit"):   # the account's daily limit: no other free model will answer today
                raise LLMUnavailable("the AI provider's free daily limit is reached; rules answer until it resets")
            continue
        _breaker["failures"] = 0
        _cache_put("llm", _hash("llm", m, system, prompt, json_schema),
                   {"output": output, "model": served, "seconds": round(time.time() - start, 2),
                    "input_tokens": tin, "output_tokens": tout})
        audit({"action": "llm_call", "model": served, "ok": True, "seconds": round(time.time() - start, 2),
               "input_tokens": tin, "output_tokens": tout})
        return output
    _breaker["failures"] += 1
    if _breaker["failures"] >= MAX_FAILURES:
        _breaker["open"] = True
    raise LLMUnavailable("; ".join(errors)[:600])


# --------------------------------------------------------------------------------------------- typed decisions

def decide(state, questions: dict, engines: tuple = ("laya", "llm")) -> dict:
    """Typed decisions (choice / noul) in Laya's question format.

    questions: {name: {"type": "choice", "instructions": str, "criteria": {option: description}}
                       | {"type": "noul", "instructions": str}}
    Returns {name: {"answer": option or bool, "prob": float, "probs": {option: p} | None, "engine": str}}.
    Order: Laya (local, calibrated) then LLM structured output (uncalibrated). Raises DecisionUnavailable.
    """
    errors = []
    for engine in engines:
        try:
            if engine == "laya":
                from layer2 import decide_laya
                return decide_laya.predict(state, questions)
            if engine == "llm":
                return _decide_llm(state, questions)
        except Exception as e:  # noqa: BLE001 - try the next engine, keep the reason
            errors.append(f"{engine}: {type(e).__name__}: {e}")
    raise DecisionUnavailable("; ".join(errors))


def _decide_llm(state, questions: dict) -> dict:
    props, required = {}, []
    lines = []
    for name, q in questions.items():
        if q["type"] == "choice":
            options = list(q["criteria"])
            props[name] = {"type": "object", "additionalProperties": False, "required": ["answer", "confidence"],
                           "properties": {"answer": {"type": "string", "enum": options},
                                          "confidence": {"type": "number"}}}
            opts = "; ".join(f"{k}: {v}" for k, v in q["criteria"].items())
            lines.append(f"- {name}: {q['instructions']} Options: {opts}")
        else:
            props[name] = {"type": "object", "additionalProperties": False, "required": ["answer", "confidence"],
                           "properties": {"answer": {"type": "boolean"}, "confidence": {"type": "number"}}}
            lines.append(f"- {name}: {q['instructions']} (true or false)")
        required.append(name)
    schema = {"type": "object", "properties": props, "required": required, "additionalProperties": False}
    state_text = state if isinstance(state, str) else json.dumps(state, default=str)
    prompt = ("Answer each question about the text. Give confidence between 0 and 1.\n\nText:\n" + state_text
              + "\n\nQuestions:\n" + "\n".join(lines))
    out = complete(prompt, json_schema=schema, max_tokens=1000, fast=True)
    return {name: {"answer": v["answer"], "prob": float(v["confidence"]), "probs": None, "engine": "llm"}
            for name, v in out.items()}


# --------------------------------------------------------------------------------------------- audit

def audit(event: dict) -> None:
    """Append to the team audit log (shared.audit) or Layer 2's JSONL fallback. Never raises."""
    event = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "layer": 2, **event}
    try:
        from shared.audit import log  # type: ignore
        log(event)
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG.open("a") as f:
            f.write(json.dumps(event, default=str) + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------------------------- other layers

def layer3_extract():
    """Layer 3's extract(text, source_type) when it exists, else None."""
    try:
        from layer3.text.extract import extract  # type: ignore
        return extract
    except ImportError:
        return None


def relation_exists(con, schema: str, table: str) -> bool:
    return con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
                       [schema, table]).fetchone()[0] > 0


def column_exists(con, schema: str, table: str, column: str) -> bool:
    return con.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = ? AND table_name = ? "
                       "AND column_name = ?", [schema, table, column]).fetchone()[0] > 0
