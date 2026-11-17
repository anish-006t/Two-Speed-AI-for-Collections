"""Typed decisions: one fixed question about one piece of (redacted) text, answered with a probability and the quote
that supports it. Layer 3's extractor and Layer 4's live prompts and QA checklist are built on this.

    from shared.decide import decide
    q = {"id": "hardship", "version": 1, "type": "choice", "options": ["none", "possible", "clear"],
         "wording": "Does the customer describe financial hardship?"}
    decide(turn_text, q)  # {"answer": "clear", "prob": 0.93, "evidence": "I lost my job", "model_version": "..."}

Question types: yes_no (answer bool), choice (one of options), score (float in [min, max], default 0-1), extract (str).
A plain string question means yes_no. Unknown answers come back as None with empty evidence.
Backend: Jev when JEV_API_KEY is set (not wired yet), otherwise the LLM with structured output via shared/llm.py.
"""
import re

from shared import config
from shared.llm import complete, resolve_model

TYPES = ("yes_no", "choice", "score", "extract")
UNKNOWN = "unknown"
UNVERIFIED_EVIDENCE_PROB_CAP = 0.5  # a quote we can't find in the text is not evidence

SYSTEM = (
    "You answer one typed question about a text from a bank collections team: a call transcript, one call turn, or "
    "an agent note. Identifiers in the text are replaced by tags such as <PERSON>; never guess them. Use only what "
    "the text says. If the text does not answer the question, answer \"unknown\". For evidence, copy the shortest "
    "exact span of the text that supports your answer, character for character, or an empty string when the answer "
    "is unknown. confidence is your probability (0 to 1) that the answer is correct."
)


def normalise_question(question: dict | str) -> dict:
    q = {"id": "adhoc", "version": 1, "type": "yes_no", "wording": question} if isinstance(question, str) \
        else dict(question)
    q.setdefault("version", 1)
    q.setdefault("type", "yes_no")
    q["wording"] = q.get("wording") or q.get("question")
    if not q.get("id") or not q["wording"]:
        raise ValueError("a question needs an id and wording")
    if q["type"] not in TYPES:
        raise ValueError(f"question type must be one of {TYPES}, got {q['type']!r}")
    if q["type"] == "choice" and not q.get("options"):
        raise ValueError(f"choice question {q['id']} needs options")
    return q


def decide(text: str, question: dict | str, model: str | None = None) -> dict:
    """Returns {"answer", "prob", "evidence", "model_version"}."""
    q = normalise_question(question)
    if config.jev_key():
        return _jev_decide(text, q)
    return _llm_decide(text, q, resolve_model(model))


def _jev_decide(text: str, q: dict) -> dict:
    raise NotImplementedError("JEV_API_KEY is set, but the Jev client is not wired into shared/decide.py yet. "
                              "Unset JEV_API_KEY to use the LLM path.")


def _schema(q: dict) -> dict:
    if q["type"] == "yes_no":
        answer = {"type": "string", "enum": ["yes", "no", UNKNOWN]}
    elif q["type"] == "choice":
        answer = {"type": "string", "enum": [str(o) for o in q["options"]] + [UNKNOWN]}
    else:  # score as a number in a string, extract as free text; "unknown" for both
        answer = {"type": "string"}
    return {"type": "object", "additionalProperties": False, "required": ["answer", "confidence", "evidence"],
            "properties": {"answer": answer, "confidence": {"type": "number"}, "evidence": {"type": "string"}}}


def _prompt(text: str, q: dict) -> str:
    hint = {"yes_no": "Answer yes, no or unknown.",
            "choice": f"Answer with exactly one of: {', '.join(map(str, q.get('options', [])))}, or unknown.",
            "score": f"Answer with one number from {q.get('min', 0)} to {q.get('max', 1)}, or unknown.",
            "extract": "Answer with the value as it appears in the text, or unknown."}[q["type"]]
    return f"Question ({q['id']}): {q['wording']}\n{hint}\n\nText:\n<<<\n{text}\n>>>"


def decide_many(text: str, questions: list, model: str | None = None, tag: str = "decide.many") -> dict:
    """Answer several typed questions about one text in a single model call (17 questions cost one call, not 17).
    Returns {question_id: {"answer", "prob", "evidence", "model_version"}}, each checked exactly like decide()."""
    qs = [normalise_question(q) for q in questions]
    if not qs:
        return {}
    if config.jev_key():
        return {q["id"]: _jev_decide(text, q) for q in qs}
    model = resolve_model(model)
    schema = {"type": "object", "additionalProperties": False, "required": [q["id"] for q in qs],
              "properties": {q["id"]: _schema(q) for q in qs}}
    lines = [f"- {q['id']}: {q['wording']} " + _hint(q) for q in qs]
    prompt = ("Answer every question below about the text. For each, give answer, confidence and evidence.\n\n"
              + "\n".join(lines) + f"\n\nText:\n<<<\n{text}\n>>>")
    raw = complete(prompt, model=model, system=SYSTEM, json_schema=schema, max_tokens=180 * len(qs) + 200, tag=tag)
    return {q["id"]: _finish(raw.get(q["id"]) or {}, q, text, f"llm:{model}|q:{q['id']}@{q['version']}") for q in qs}


def _hint(q: dict) -> str:
    return {"yes_no": "Answer yes, no or unknown.",
            "choice": f"Answer with exactly one of: {', '.join(map(str, q.get('options', [])))}, or unknown.",
            "score": f"Answer with one number from {q.get('min', 0)} to {q.get('max', 1)}, or unknown.",
            "extract": "Answer with the value as it appears in the text, or unknown."}[q["type"]]


def _llm_decide(text: str, q: dict, model: str) -> dict:
    raw = complete(_prompt(text, q), model=model, system=SYSTEM, json_schema=_schema(q), max_tokens=512,
                   tag=f"decide.{q['id']}")
    return _finish(raw, q, text, f"llm:{model}|q:{q['id']}@{q['version']}")


def _finish(raw: dict, q: dict, text: str, model_version: str) -> dict:
    """Validate one raw answer: allowed values only, verbatim evidence or a capped probability."""
    answer = _coerce(raw.get("answer"), q)
    prob = min(max(float(raw.get("confidence") or 0.0), 0.0), 1.0)
    evidence = (raw.get("evidence") or "").strip()
    if answer is None:
        evidence = ""
    elif evidence and not _found_in(evidence, text):
        evidence, prob = "", min(prob, UNVERIFIED_EVIDENCE_PROB_CAP)
    return {"answer": answer, "prob": round(prob, 4), "evidence": evidence, "model_version": model_version}


def _coerce(value, q: dict):
    if value is None or str(value).strip().lower() in (UNKNOWN, ""):
        return None
    value = str(value).strip()
    if q["type"] == "yes_no":
        return {"yes": True, "no": False}.get(value.lower())
    if q["type"] == "choice":
        options = {str(o).lower(): o for o in q["options"]}
        return options.get(value.lower())
    if q["type"] == "score":
        try:
            return min(max(float(value), float(q.get("min", 0))), float(q.get("max", 1)))
        except ValueError:
            return None
    return value


def _found_in(quote: str, text: str) -> bool:
    squash = lambda s: re.sub(r"\s+", " ", s).strip().lower()  # noqa: E731
    return squash(quote) in squash(text)
