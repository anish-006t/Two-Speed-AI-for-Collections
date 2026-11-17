"""Local Laya typed decisions (architecture §5). Loaded once per process; every result cached on disk.

Laya never generates text: each question is a `choice` (one option from a list) or a `noul` (probability of yes).
We pin the multilingual checkpoint: smaller than the English one (322M vs 421M), reads 1,024 tokens instead of 512,
and handles the French documents and questions.
"""
from __future__ import annotations

import os
import threading

from layer2.adapters import _cache_get, _cache_put, _hash

MODEL = os.environ.get("LAYA_MODEL", "multilingual")
_router = None
_lock = threading.Lock()


def _get_router():
    global _router
    with _lock:
        if _router is None:
            from laya import Router  # heavy import (torch); only when a decision is needed
            _router = Router()
    return _router


def _normalise(name: str, q: dict, raw: dict) -> dict:
    """Map Laya's answer for one question to {answer, prob, probs, engine}."""
    probs = None
    for key in ("probabilities", "probs", "distribution", "scores"):
        if isinstance(raw.get(key), dict):
            probs = {str(k): float(v) for k, v in raw[key].items()}
            break
    if q["type"] == "choice":
        answer = raw.get("choice")
        if answer is None and probs:
            answer = max(probs, key=probs.get)
        prob = probs.get(answer) if probs and answer in probs else raw.get("confidence")
        return {"answer": answer, "prob": float(prob) if prob is not None else None, "probs": probs, "engine": "laya"}
    p_yes = raw.get("noul", raw.get("probability", raw.get("p_true")))
    p_yes = float(p_yes) if p_yes is not None else None
    return {"answer": bool(p_yes is not None and p_yes >= 0.5), "prob": p_yes,
            "probs": {"yes": p_yes, "no": 1 - p_yes} if p_yes is not None else None, "engine": "laya"}


def predict(state, questions: dict) -> dict:
    key = _hash("laya", MODEL, state, questions)
    cached = _cache_get("laya", key)
    if cached is not None:
        return cached
    result = _get_router().predict(state, questions, model=MODEL)
    answers = result.get("answers", result)
    out = {name: _normalise(name, q, answers.get(name, {})) for name, q in questions.items()}
    _cache_put("laya", key, out)
    return out
