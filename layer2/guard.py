"""Step 1: decide whether to refuse (architecture §8).

Rules first (precise, explainable), then a typed decision for what the rules don't catch. A refusal always says
which rule fired and offers questions we can answer. Questions *about* a policy on protected grounds or vulnerable
customers are answerable; questions that *use* protected attributes or their proxies on data are refused.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from layer2 import adapters

PROTECTED = (r"gender|\bsex\b|\bmale\b|\bfemale\b|\bmen\b|\bwomen\b|\bman\b|\bwoman\b|marital|married|divorced|widow|spouse status|citizenship|citizen\b|"
             r"immigra|newcomer|refugee|ethnic|\brace\b|racial|religio|\bchurch|\bmosque|\btemple|\bsynagogue|"
             r"\bgurdwara|\bpray|\bfaith\b|\bpastor|\bpriest|\bimam\b|\brabbi|église|mosquée|disabilit|accessibility|\baccent|"
             r"(?:customer|client|debtor|borrower|people|person)s?'?\s+ages?\b|\bby age\b|\bage (?:band|group|bracket|range)s?|\bages? of (?:the )?(?:customers?|clients?|debtors?|borrowers?|people)|\baged\b|how old|elderly|senior citizen|\b(?:older|younger|oldest|youngest)\b|"
             r"household size|dependan|dependen|children|pregnan|sexual orientation|national origin|"
             r"vulnerability type|\bvulnerab|vulnérab|aml|politically exposed|\bpep\b")
PROXY = (r"\bfsa|postal (?:area|code)|postcode|zip|neighbou?rhood|\bareas?\b|language|french|english-speaking|"
         r"-speaking|accent|branch|surname|first name|ethnic")
STRONG_PROXY = (r"\bfsas?\b|forward sortation|postal (?:area|code|prefix)|postcodes?|zip codes?|neighbou?rhoods?|"
                r"\bby (?:preferred |spoken |home |first |customers?'? )?language|\bby langue")   # language as a breakdown
TARGETING = (r"priorit|lower (?:the |their )?(?:priority|treatment)|downgrade|stop offering|stop giving|deny|denied|exclude|excluding|not offer|avoid|restrict|refuse|withhold|"
             r"target|prioriti[sz]e|call(?:ed)? first|deprioriti|rank|penali[sz]|charge more|less help|fewer|"
             r"should we (?:stop|not)|default more|\bless\b|more often|because they (?:pay|default|break|are)")
DATA_USE = (r"\bcustomers\b|\bclients\b|\bdebtors\b|\bborrowers\b|\blist\b|\bshow\b|\brank|\bby\b|group|segment|break ?down|compare|which customers|who are|\bwhich\b|"
            r"\bmost\b|\bmore\b|\bless\b|\bthan\b|"
            r"prioriti|first|filter|count|how many|share|rate|average|\btop\b|export|give me")
BULK_VERB = (r"export|download|dump|extract|send me|give me|list|share|pull|copy|every|each|all (?:the )?customers|"
             r"\bwrite\b|\bprint\b|\bshow\b|\bput\b|\binclude\b")
PII = (r"\bnames?\b|phone|mobile number|telephone|e-?mail|address|date of birth|\bdob\b|\bsin\b|social insurance|"
       r"national id|card number|account number|contact details")
OFF_TOPIC = (r"weather|forecast|temperature|stock price|share price|bitcoin|crypto|recipe|movie|song|football|"
             r"cricket|hockey|nhl|nba|game tonight|who will win|super bowl|joke|poem|horoscope|lottery|"
             r"translate this|write (?:me )?a story|capital of")
DOMAIN = (r"case|customer|account|promise|ptp|dpd|bucket|queue|agent|call|contact|sms|email|policy|hardship|"
          r"payment|collect|card|loan|deposit|rate|cure|roll|dispute|complaint|consent|transcript|strategy|"
          r"champion|challenger|charge|write-?off|delinquen|incident|outage|dialer|script|programme|program|"
          r"insolvency|proposal|bankrupt|grace|fee|balance|channel|province|site|benchmark|metric")
POLICY_FRAME = (r"\bpolicy\b|procedure|what should|what must|must the agent|can we|may we|are we allowed|"
                r"is it allowed|what does .* say|how should|guidance")


@dataclass
class GuardResult:
    refused: bool
    category: str = "answerable"       # answerable | off_topic | protected_attribute | proxy_targeting | bulk_pii_export | outside_role
    reason: str = ""
    alternatives: list = field(default_factory=list)
    engine: str = "rules"
    prob: float | None = None


ALTERNATIVES = {
    "protected_attribute": ["Broken-promise rate by number of previously broken promises",
                            "Hardship rate of cases by primary product"],
    "proxy_targeting": ["Hardship rate by primary product",
                        "What POL-COLL-004 says about offering hardship options"],
    "bulk_pii_export": ["How many open cases there are by current bucket",
                        "Average days past due of open cases by queue"],
    "off_topic": ["Promise-kept rate for a month", "What the contact policy allows per week"],
    "prompt_injection": ["Ask a question about collections data or policy"],
    "outside_role": ["The same figure as an aggregate across customers"],
}
REASONS = {
    "protected_attribute": "Refuse: protected ground. The question uses protected attributes (for example gender, "
                           "marital status, citizenship, age), which Maple Bank policy POL-COLL-004 §4.6 forbids in "
                           "any decision or ranking.",
    "proxy_targeting": "Refuse: protected ground. Targeting treatment by postal area, language or similar fields acts "
                       "as a proxy for protected grounds and income (POL-COLL-004 §4.6); hardship help is decided "
                       "per customer, not by area.",
    "bulk_pii_export": "Refuse: personal data. Bulk export of customer names, phone numbers or other identifiers is not "
                       "allowed through question answering.",
    "off_topic": "Refuse: outside scope. This assistant answers questions about Maple Bank collections data and "
                 "policies only.",
    "outside_role": "Refuse: outside your role. Your role can see aggregates, not individual customers' details.",
    "prompt_injection": "Refuse: the request tries to change the assistant's instructions or reveal its configuration.",
}


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.I) is not None


# Refusals that are final: never sent to the AI for a second opinion, and a misspelling cannot hide them (ask.py
# checks the typed and the corrected question). Off-topic is soft: a garbled question may only look off-topic.
HARD = {"protected_attribute", "proxy_targeting", "bulk_pii_export", "prompt_injection", "outside_role"}


def _refuse(category: str, engine: str = "rules", prob: float | None = None, detail: str = "") -> GuardResult:
    reason = REASONS[category] + (f" ({detail})" if detail else "")
    return GuardResult(True, category, reason, ALTERNATIVES.get(category, []), engine, prob)


def rule_check(question: str, role: str = "analyst") -> GuardResult | None:
    """Refusal by rule, or None when no rule fires."""
    q = question.lower()
    from layer2.llm_guard import looks_like_injection
    if looks_like_injection(question):
        return _refuse("prompt_injection")
    policy_frame = _has(POLICY_FRAME, q)
    if _has(OFF_TOPIC, q) and not _has(DOMAIN, q):
        return _refuse("off_topic")
    if _has(BULK_VERB, q) and _has(PII, q) and not policy_frame and not _has(r"how many|count|share|rate|number of", q):
        return _refuse("bulk_pii_export")
    staff_trait = re.search(r"(?:certif|train|special|dedicated|team)\w*\s+(?:\w+\s+)?(?:for|in|on|with)\s+"
                            r"vuln[ée]rab", q)                  # agents certified for vulnerable customers (BQ-014)
    if _has(PROTECTED, q) and _has(DATA_USE, q) and not policy_frame and not (
            staff_trait and not _has(PROTECTED, re.sub(r"vuln[ée]rab\w*", " ", q))):
        return _refuse("protected_attribute")
    if _has(PROXY, q) and _has(TARGETING, q):
        return _refuse("proxy_targeting")
    if _has(STRONG_PROXY, q) and _has(DATA_USE, q) and not policy_frame:   # postal area as a breakdown is a proxy
        return _refuse("proxy_targeting")
    if role == "analyst" and _has(PII, q) and _has(r"\bwhat is\b|\bshow\b|\bgive\b", q) and _has(r"customer\s+\S*\d", q):
        return _refuse("outside_role")
    return None


def check(question: str, role: str = "analyst", use_model: bool = True, threshold: float = 0.85) -> GuardResult:
    hit = rule_check(question, role)
    if hit:
        return hit
    if not use_model or _has(DOMAIN, question.lower()):
        # questions about collections data or policy are judged by the rules (LLM refusals are uncalibrated and each
        # call spends the daily budget); the model only looks at questions with no collections words at all
        return GuardResult(False)
    criteria = {
        "answerable": "a question about collections data, metrics, cases, calls, policies or procedures",
        "off_topic": "not about the bank's collections work at all",
        "protected_attribute": "asks to list, rank or group people by gender, marital status, citizenship, age, "
                               "disability or ethnicity",
        "proxy_targeting": "asks to deny or target help by postal area, language or another proxy for a protected group",
        "bulk_pii_export": "asks to export or list customers' names, phone numbers, emails or addresses",
    }
    try:   # LLM only: zero-shot Laya refused valid questions at 0.98 confidence (outputs/layer2_laya_check.md)
        d = adapters.decide(question, {"category": {"type": "choice", "instructions":
                                                    "What kind of request is this?", "criteria": criteria}},
                            engines=("llm",))["category"]
    except adapters.DecisionUnavailable:
        return GuardResult(False, engine="none")
    if d["answer"] != "answerable" and (d["prob"] or 0) >= threshold:
        return _refuse(d["answer"], engine=d["engine"], prob=d["prob"])
    return GuardResult(False, engine=d["engine"], prob=d["prob"])
