"""Step 2: pick the lane (architecture §3). Rules first; a typed decision only when the rules disagree or say nothing.

Lanes: metric_sql (numbers from data), policy (policies, procedures, scripts, FAQ, incident reports, release notes),
call_summary (a specific recorded call), lookup (facts about one case or customer).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from layer2 import adapters
from layer2.parse import Parsed

ROUTES = ("metric_sql", "policy", "call_summary", "lookup")

POLICY_CUES = (r"\bpolicy\b|procedure|\bscript\b|\bfaq\b|what should|what must|must the agent|should the agent|"
               r"what do(?:es)? (?:the|our) |can we|may we|are we allowed|allowed to|is it allowed|"
               r"\bneeded\b|\brequired\b|do we need|consent|grace period|how long does|programme last|program last|"
               r"\bwhy did\b|\bwhy was\b|\bwhy were\b|what happened|incident|outage|release note|"
               r"do not call|national do not call|stop collection calls|considered broken|what changed|renamed|"
               r"\bcolumn\b|release note|data contract|"
               r"a customer (?:tells|says|said|told|asks|reports)|before asking for payment|how many .* can we|"
               r"how should|what happens (?:when|if)|how long (?:do|does|is|can|should)|how do (?:we|i|agents?)|"
               r"\bhandle\b|files? for bankruptcy|what (?:is|are) the rules?|\bpermitted\b")
METRIC_CUES = (r"\brate\b|\bshare\b|\baverage\b|\bmean\b|how many|how much|\bcount\b|\btotal\b|\bsum\b|"
               r"\bby (?:agent|product|queue|channel|province|hour|bucket|site|month|week|day|card|loan|primary|current)|"
               r"\bper\b|percentage|\bratio\b|compare|trend|change with|\bopen today\b|roll(?:ed)?\b|cured|charged off|"
               r"\bfraction\b|\bproportion\b|\bpercent\b|\bmedian\b")
SUMMARY_CUES = r"summar|what did the customer|what was said|recorded call|last call|recent call|transcript|conversation"
LOOKUP_CUES = r"\bcase\b|\bcustomer\b|\bptp-|\bpromise\b"


@dataclass
class Route:
    route: str
    engine: str = "rules"
    prob: float | None = None
    probs: dict | None = None


def _n(pattern: str, text: str) -> int:
    return len(re.findall(pattern, text, re.I))


def vocabulary_lean(question: str) -> tuple[int, int]:
    """(words that name data, words only the bank's documents use). Measured on 20 cue-less questions written for
    this (10 data, 10 policy): "policy when documents >= data, unless a period is named" routed 19 right; the BM25
    score of the best section did not separate them."""
    from layer2.policy import _vocab
    from layer2.semantic import data_words
    from layer2.semantic.coverage import ENTITY, STOP, words
    dw = data_words(None)
    global _DOC_ONLY
    if _DOC_ONLY is None:
        _DOC_ONLY = words(" ".join(_vocab())) - dw
    ws = [w for w in words(question) if w not in STOP and w not in ENTITY and len(w) > 2]
    return sum(w in dw for w in ws), sum(w in _DOC_ONLY for w in ws)


_DOC_ONLY = None


def route(p: Parsed, use_model: bool = True) -> Route:
    q = p.question.lower()
    if (p.case_ids or p.golden_ids) and re.search(r"\bnotes?\b|\bstory\b|\bhistory\b", q):
        return Route("lookup")
    if p.case_ids and re.search(SUMMARY_CUES, q):
        return Route("call_summary")
    if p.transcript_ids:
        return Route("call_summary")
    if p.doc_ids:
        return Route("policy")
    policy, metric = _n(POLICY_CUES, q), _n(METRIC_CUES, q)
    # "how many call attempts can we make" is a rule, "how many attempts did cases receive" is data
    if re.search(r"\b(can|may|should|must|do|does) (?:we|i|you|they|he|she|the agent|agents|an agent|a customer|"
                 r"the customer|customers)\b.*\b(?:have to|need to|required to|allowed|call|contact|provide|offer|say|do)\b", q) or \
            re.search(r"\b(can|may|should|must) (?:we|i|the agent|agents|an agent)\b", q):
        policy += 2
    if re.search(r"\b(did|were|was) .*\b(cases|accounts|promises|calls|contacts|customers|disputes)\b", q) \
            and not re.search(r"\bwhy\b", q):
        metric += 1
    from layer2.semantic.generic import LIST_INTENT
    if re.search(LIST_INTENT, q) and not re.search(r"\bwhy\b", q):   # "which customers are most likely to…"
        metric += 2
    if (p.case_ids or p.ptp_ids or p.golden_ids) and not metric:
        return Route("lookup")
    if policy and not metric:
        return Route("policy")
    if metric and not policy:
        return Route("metric_sql")
    if not policy and not metric:   # no cue either way: weigh data words against words only documents use
        d, w = vocabulary_lean(p.question)
        if not p.periods and w >= d:
            return Route("policy", f"rules-vocabulary (data {d}, documents {w})")
        return Route("metric_sql", f"rules-vocabulary (data {d}, documents {w})")
    # No model here: rules were right on all 35 benchmark and 29 own questions, while an LLM tie-breaker sent a
    # policy question to lookup at 0.95 confidence (layer2/CHANGELOG.md, 3 Oct).
    return Route("metric_sql" if metric >= policy else "policy", "rules-tiebreak")
