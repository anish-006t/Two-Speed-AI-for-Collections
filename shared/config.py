"""Team settings from `.env` (see `.env.example`). Read at call time, so tests can change the environment."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]  # repo root (this file is shared/config.py)
load_dotenv(ROOT / ".env", override=False)  # real environment variables win over .env


def _path(name: str, default: str) -> Path:
    p = Path(os.environ.get(name) or default)
    return p if p.is_absolute() else ROOT / p


def model_fast() -> str:
    p = llm_provider()
    return os.environ.get("LLM_MODEL_FAST") or (PROVIDERS[p]["fast"] if p != "anthropic" else "claude-haiku-4-5-20251001")


def model_smart() -> str:
    p = llm_provider()
    return os.environ.get("LLM_MODEL_SMART") or (PROVIDERS[p]["smart"] if p != "anthropic" else "claude-sonnet-5-5")


def db_path() -> Path:
    return _path("DHANDHO_DB", "lake/maple.duckdb")


def cache_dir() -> Path:
    return _path("LLM_CACHE_DIR", "lake/llm_cache")


def call_log_path() -> Path:
    return cache_dir().parent / "llm_calls.jsonl"


def offline() -> bool:
    return os.environ.get("LLM_OFFLINE", "0").strip().lower() in ("1", "true", "yes")


def jev_key() -> str | None:
    return os.environ.get("JEV_API_KEY") or None


def default_user() -> str:
    return os.environ.get("DHANDHO_USER") or "unknown"


# ------------------------------------------------------------------------------------------------- LLM provider
# LLM_PROVIDER picks where shared/llm.py sends calls: anthropic (default), or any OpenAI-compatible endpoint
# (groq, cerebras, ollama, or "openai_compatible" with LLM_BASE_URL and LLM_API_KEY set by hand).
PROVIDERS = {
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY",
             "fast": "openai/gpt-oss-20b", "smart": "openai/gpt-oss-120b", "min_interval_s": 2.1},
    "cerebras": {"base_url": "https://api.cerebras.ai/v1", "key_env": "CEREBRAS_API_KEY",
                 "fast": "llama3.1-8b", "smart": "llama-3.3-70b", "min_interval_s": 2.1},
    "ollama": {"base_url": "http://localhost:11434/v1", "key_env": "", "fast": "qwen2.5:7b", "smart": "qwen2.5:7b",
               "min_interval_s": 0.0},
    "openai_compatible": {"base_url": "", "key_env": "LLM_API_KEY", "fast": "", "smart": "", "min_interval_s": 0.0},
}


def llm_provider() -> str:
    p = (os.environ.get("LLM_PROVIDER") or "anthropic").strip().lower()
    if p != "anthropic" and p not in PROVIDERS:
        raise ValueError(f"LLM_PROVIDER must be anthropic or one of {sorted(PROVIDERS)}, got {p!r}")
    return p


def provider_settings() -> dict:
    """Base URL, API key and pacing for an OpenAI-compatible provider."""
    p = PROVIDERS[llm_provider()]
    return {"base_url": (os.environ.get("LLM_BASE_URL") or p["base_url"]).rstrip("/"),
            "api_key": os.environ.get(p["key_env"]) if p["key_env"] else "ollama",
            "min_interval_s": float(os.environ.get("LLM_MIN_INTERVAL_S") or p["min_interval_s"])}


def llm_available() -> bool:
    """True when the configured provider has what it needs to make a call (a key, or a local Ollama)."""
    p = llm_provider()
    if p == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    return p == "ollama" or bool(provider_settings()["api_key"])
