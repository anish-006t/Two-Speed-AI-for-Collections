"""Versioned typed-question extraction over redacted collection text.

The normal path delegates every question to :func:`shared.decide.decide`.  A
deterministic local backend is used when no API key is configured (and can be
selected explicitly with ``L3_EXTRACTOR_MODE=rules``), which keeps live replay
and tests useful offline without changing the public interface.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

from shared import config
from shared.decide import decide, decide_many  # noqa: F401 - decide kept for callers
from shared.llm import LLMError

QUESTION_PATH = Path(__file__).with_name("questions.yaml")
SOURCE_TYPES = {"transcript", "note", "turn"}
RULE_MODEL = "rules:l3-text-1"


@lru_cache(maxsize=1)
def load_questions() -> tuple[dict, ...]:
    """Load and validate the registry (JSON is deliberately valid YAML)."""
    questions = json.loads(QUESTION_PATH.read_text(encoding="utf-8"))
    required = {"id", "version", "wording", "type", "options", "applies_to", "label_column"}
    seen: set[str] = set()
    for question in questions:
        missing = required - question.keys()
        if missing:
            raise ValueError(f"question is missing {sorted(missing)}: {question!r}")
        if question["id"] in seen:
            raise ValueError(f"duplicate question id {question['id']!r}")
        if not set(question["applies_to"]) <= SOURCE_TYPES:
            raise ValueError(f"invalid applies_to for {question['id']!r}")
        seen.add(question["id"])
    return tuple(questions)


def extract(text: str, source_type: str) -> dict:
    """Return all applicable typed answers for a transcript, note, or turn."""
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"source_type must be transcript, note or turn, got {source_type!r}")
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    mode = os.environ.get("L3_EXTRACTOR_MODE", "auto").strip().lower()
    if mode not in {"auto", "llm", "rules"}:
        raise ValueError("L3_EXTRACTOR_MODE must be auto, llm, or rules")
    has_key = config.llm_available() or bool(os.environ.get("JEV_API_KEY"))
    use_rules = mode == "rules" or (mode == "auto" and not has_key)
    questions = [q for q in load_questions() if source_type in q["applies_to"]]
    if use_rules:
        output = {q["id"]: _rule_decide(text, q) for q in questions}
    else:
        try:  # one model call answers every question for this text (Layer 4 change, 4 Oct: was one call per question)
            output = decide_many(text, questions, tag=f"l3.extract.{source_type}")
        except LLMError:
            if mode == "llm":
                raise
            output = {q["id"]: _rule_decide(text, q) for q in questions}
    for q in questions:  # same shape as the LLM's llm:<model>|q:<id>@<v>
        answer = output[q["id"]]
        if answer.get("model_version", "").startswith("rules:"):
            answer["model_version"] = f"{RULE_MODEL}|q:{q['id']}@{q['version']}"
    return output


def _hit(pattern: str, text: str):
    return re.search(pattern, text, flags=re.IGNORECASE)


def _answer(value, probability: float, match=None) -> dict:
    return {"answer": value, "prob": round(probability, 4),
            "evidence": match.group(0) if match else "", "model_version": RULE_MODEL}


def _rule_decide(text: str, question: dict) -> dict:  # noqa: C901 - auditable fallback rules are intentionally flat
    qid = question["id"]
    hardship_patterns = {   # label vocabulary (questions.yaml v2); specific causes before general ones
        "ei_waiting": r"\bEI\b|employment insurance|waiting (?:for|on) (?:my )?(?:EI|benefits?|claim)",
        "strike_lockout": r"\bon strike\b|\bstrike\b|lock-?out|picket",
        "disaster_evacuation": r"evacuat|wildfire|forest fire|flood",
        "rate_reset": r"rate (?:reset|went up|increase|jumped)|mortgage renewal|renew(?:ed|al) at|payment went up",
        "separation": r"separat|divorc|split up|broke up|my ex\b",
        "bereavement": r"passed away|bereavement|funeral|lost my (?:husband|wife|partner|mother|father)",
        "illness": r"in (?:the )?hospital|surgery|medical|sick(?:ness| leave)?|illness|diagnos|treatment",
        "job_loss": r"lost my job|laid off|been let go|unemployed|out of work|plant clos",
        "reduced_income": r"hours (?:got |were )?cut|reduced (?:my )?hours|pay cut|less work|fewer shifts|cut back",
    }
    reason, reason_match = None, None
    for candidate, pattern in hardship_patterns.items():
        if match := _hit(pattern, text):
            reason, reason_match = candidate, match
            break
    possible = _hit(r"struggl|money is tight|hard time|can'?t afford|cannot afford|behind on", text)
    promise = _hit(r"\bi (?:can|will|'ll) pay\b|\bi promise\b|\bmake a payment\b|\bpay (?:it|this|\$)", text)
    amount = _hit(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)", text)
    promised_date = _hit(r"\b(\d{4}-\d{2}-\d{2}|(?:mon|tues|wednes|thurs|fri|satur|sun)day|tomorrow|next week|the \d{1,2}(?:st|nd|rd|th))\b", text)
    patterns = {
        "dispute": r"not my (?:debt|account)|\bdisput|fraud|wrong (?:amount|balance)|payment (?:was )?not applied",
        "complaint": r"\bcomplain|unacceptable|ridiculous|poor service|harass|manager",
        "cease_request": r"stop calling|don'?t call|do not call|cease (?:calling|communication)",
        "insolvency_mention": r"bankrupt|consumer proposal|insolven|licensed trustee",
        "third_party_present": r"on speaker|speakerphone|someone else (?:is )?(?:here|listening)|my lawyer|my spouse is here",
        "legal_threat_by_agent": r"(?:agent|collector).{0,50}(?:arrest|sue|seize|garnish)|(?:we|i) will (?:have you arrested|seize|sue)",
        "protected_attribute_mentioned": r"\b(?:gender|marital status|citizenship|newcomer|disability|disabled|ethnicity|religion)\b",
    }
    if qid == "hardship":
        return _answer("clear" if reason_match else "possible" if possible else "none",
                       .92 if reason_match else .72 if possible else .82, reason_match or possible)
    if qid == "hardship_reason":
        return _answer(reason, .9 if reason else .65, reason_match)
    if qid == "promise_mentioned":
        return _answer(bool(promise), .9 if promise else .78, promise)
    if qid == "promise_amount":
        value = float(amount.group(1).replace(",", "")) if amount and promise else None
        return _answer(value, .94 if value is not None else .7, amount if value is not None else None)
    if qid == "promise_date":
        return _answer(promised_date.group(1) if promised_date and promise else None,
                       .88 if promised_date and promise else .7, promised_date if promise else None)
    if qid == "promise_intent_strength":
        conditional = _hit(r"if i can|try to|might|hopefully|should be able", text)
        firm = _hit(r"i promise|i will pay|i'll pay|definitely", text)
        value, evidence = ("firm", firm) if firm else ("conditional", conditional) if conditional else ("weak", promise)
        return _answer(value if promise else None, .82 if promise else .65, evidence)
    if qid == "dispute_type":   # label vocabulary (questions.yaml v2)
        kinds = (("late_fee", r"late fee|late charge|fee was charged|charged (?:a |me )?fee"),
                 ("payment_not_applied", r"payment (?:was |wasn'?t |has not been |hasn'?t been )?(?:not )?applied|"
                                         r"paid (?:it )?already|already paid|payment (?:is )?missing"),
                 ("fraud", r"fraud|stolen|unauthori[sz]ed|not my (?:debt|account|card)"),
                 ("billing_error", r"wrong (?:amount|balance)|billing (?:error|mistake)|overcharg|error on (?:my|the) "
                                   r"(?:bill|statement)|interest (?:is )?wrong"),
                 ("other", r"\bdisput"))
        for value, pattern in kinds:
            if match := _hit(pattern, text):
                return _answer(value, .87, match)
        return _answer(None, .7)
    if qid in patterns:
        match = _hit(patterns[qid], text)
        return _answer(bool(match), .9 if match else .76, match)
    if qid == "sentiment":
        negative = _hit(r"angry|ridiculous|harass|unacceptable|frustrat|upset", text)
        positive = _hit(r"thank you|thanks|appreciate|that helps|great", text)
        return _answer(-.7 if negative else .5 if positive else 0.0, .7, negative or positive)
    if qid == "stated_delay_reason":   # choice over the label vocabulary (questions.yaml v2)
        delays = (("refuses", r"won'?t pay|not (?:going to|gonna) pay|refuse"),
                  ("bank_error", r"bank (?:error|mistake)|bank (?:messed|screwed)|transfer (?:failed|bounced)|"
                                 r"payment (?:bounced|was returned)"),
                  ("dispute", r"\bdisput|not my (?:debt|account)|wrong (?:amount|balance)|late fee"),
                  ("payroll_timing", r"pay ?day|paycheque|paycheck|get paid|salary (?:comes|is late|hasn'?t)|"
                                     r"payroll|next (?:pay|cheque)"),
                  ("forgot", r"forgot|slipped my mind|didn'?t realize|wasn'?t aware|missed the (?:date|due)"),
                  ("travel", r"travel|out of (?:town|the country)|vacation|away (?:for|on)"),
                  ("reduced_income", r"hours (?:got |were )?cut|reduced (?:my )?hours|pay cut|laid off|lost my job|"
                                     r"less work|income (?:dropped|went down)|\bEI\b"))
        for value, pattern in delays:
            if match := _hit(pattern, text):
                return _answer(value, .76, match)
        return _answer("other" if reason_match or possible else None, .62 if reason_match or possible else .55,
                       reason_match or possible)
    if qid == "next_step":   # choice over the label vocabulary (questions.yaml v2); strongest instruction first
        steps = (("stop_contact", r"stop calling|don'?t call|do not call|cease|no more calls"),
                 ("dispute_investigation", r"investigat|\bdisput|look into (?:the|this|that)"),
                 ("escalate_supervisor", r"supervisor|manager|escalat"),
                 ("hardship_plan_review", r"hardship (?:plan|program|programme|review)|review (?:my |your |the )?options|"
                                          r"payment plan|reduced payment"),
                 ("send_payment_link", r"payment link|send (?:you |me )?(?:a |the )?link|link to pay|pay online"),
                 ("call_back", r"call (?:you |me )?back|call again|follow[- ]up|callback|\bcb\b"))
        for value, pattern in steps:
            if match := _hit(pattern, text):
                return _answer(value, .78, match)
        return _answer("none", .6)
    return _answer(None, .5)
