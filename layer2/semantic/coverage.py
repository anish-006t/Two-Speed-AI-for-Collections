"""Does a rule-built plan cover the whole question? Lists every part it does not express.

A data question is made of a closed set of parts: what to compute, breakdowns, filters, a period, and operations
(a threshold, top N, a change between periods, a distinct count, a "per X" ratio, a list of records, an exclusion).
The rules handle some of them. Answering with a part silently dropped is worse than not answering, so this check
names what the plan leaves out. The caller then asks the LLM to extend the rule-built SQL, or, without a model,
answers with a note saying what was not applied.

Two kinds of evidence, both general (grammar and the plan's own vocabulary, never particular questions):
  1. operations, found from grammar words ("more than 5", "top 10", "versus" with two periods, "distinct",
     "per case", "excluding"), unless the plan declares it handles them;
  2. content words: after removing the period, ordinary English and analytics words, and every word the plan
     explains (its metric's cues, chosen breakdowns, filters, values, column), whatever is left was not used.
"""
from __future__ import annotations

import re

MONTHS = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|" \
         r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
UNIT = r"(?:days?|weeks?|months?|quarters?|years?)"
PERIOD = [
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{MONTHS}\b\.?(?:,?\s+\d{{4}})?",
    rf"\b{MONTHS}\b\.?(?:\s+\d{{1,2}}(?:st|nd|rd|th)?)?(?:,?\s+\d{{4}})?",
    r"\b\d{4}-\d{2}(?:-\d{2})?\b", r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", r"\b(?:19|20)\d{2}\b", r"\bq[1-4]\b",
    rf"\b(?:this|last|next|previous|past|prior|current|same)\s+(?:\d+\s+)?{UNIT}\b",
    rf"\b(?:the\s+)?(?:\d+|one|two|three|six|twelve)\s+{UNIT}\s+(?:to|before|after|ending|ago)\b",
    rf"\bwithin\s+\d+\s+{UNIT}\b", rf"\b\d+-{UNIT}\b", r"\b(?:to|year)[- ]date\b", r"\bytd\b", r"\bso far\b",
    r"\btoday\b", r"\byesterday\b", r"\bto ?day\b", r"\b(?:end|start|beginning|middle)\s+of\b", r"\bmonth[- ]end\b",
]
# Ordinary English and analytics words: they never change which rows or columns a query needs.
STOP = set("""
a an the of to in on at for by with from into onto over under per and or as is are was were be been being do does did
done have has had what whats which who whom whose when where why how many much show shows showing me give list tell find
get see our we us you your their them they it its this that these those there here all any each every some than then so
if please can could would should will may might just also about across between during among etc i my can't cant
s t let lets need want know look looking break broken-down down up out split grouped group groups grouping
rate rates ratio share shares percent percentage percentages proportion fraction number numbers count counts total
totals sum average averages mean median avg value values amount amounts level levels overall breakdown trend trends
data figure figures metric metrics stat stats statistics distribution summary summarise summarize
time period periods day days week weeks weekly month months monthly year years yearly quarter quarters quarterly daily
date dates now currently today snapshot recent recently latest ever far end start
across among across separately respectively each every respective
compare compared comparing comparison versus vs against relative difference differ
along together whole entire full overall typical typically usually generally roughly approximately around
""".split())
ENTITY = set("""case cases customer customers account accounts promise promises ptp ptps contact contacts call calls
attempt attempts debtor debtors borrower borrowers client clients record records portfolio book collections
collection bank maple""".split())

# Operations: name -> pattern. Numbers inside periods are blanked first, so "last 3 months" is not "top 3".
CMP = r"(?:more|greater|higher|bigger|larger|less|fewer|lower|smaller)\s+than|over|above|under|below|at\s+least|" \
      r"at\s+most|exceed(?:s|ing)?|>=?|<=?|or\s+more|or\s+less|or\s+fewer|plus"
OPS = {
    "threshold": rf"\b(?:{CMP})\s+\$?\d[\d,.]*|\$?\d[\d,.]*\s*(?:\+|(?:or more|or less|or fewer|plus)\b)",
    "top_n": r"\b(?:top|bottom|first|last|best|worst)\s+\d+\b|\bwhich\s+\d+\b|"
             r"\b\d+\s+(?:highest|lowest|largest|biggest|smallest|most|least|best|worst)\b",
    "change": r"\b(?:change[sd]?|chang(?:ing)|grow(?:th|n)?|grew|increase[sd]?|decrease[sd]?|drop(?:ped|s)?|rise|rose|"
              r"risen|fell|fall(?:en)?|decline[sd]?|jump(?:ed)?|went (?:up|down)|difference|delta|"
              r"improv(?:e|ed|es|ing|ement|ements)|worsen(?:ed|ing|s)?|deteriorat\w*|gain(?:ed|s)?|slipp?(?:ed)?|"
              r"month[- ]over[- ]month|year[- ]over[- ]year|mom|yoy)\b",
    "compare": r"\b(?:vs\.?|versus|compared? (?:to|with)|relative to|against)\b",
    "distinct": r"\b(?:distinct|unique|different)\b",
    "own_definition": r"\b(?:i mean|i\.e\.?|ie,|meaning|defined as|by which i mean|in other words|counting only|"
                      r"only counting|where .{1,40}\bmeans)\b",
    "per_entity": r"\bper\s+(?:case|customer|account|promise|agent|debtor|borrower|client)\b",
    "exclusion": r"\b(?:not|without|excluding|exclude[sd]?|except|other than|apart from|non[- ]\w+|never|no longer)\b",
    "list_records": r"^\s*(?:please\s+)?(?:list|show|give|display|find|get)(?:\s+me)?(?:\s+(?:the|all|a list of))?"
                    r"(?:\s+(?:top|bottom|first)\s+\d+)?\s+(?:cases|accounts|promises|contacts|customers|calls)\b",
}
LABELS = {"threshold": "a threshold ({})", "top_n": "top/bottom N ({})", "change": "a change between periods ({})",
          "compare": "a comparison of periods ({})", "own_definition": "the question's own definition ({}…)",
          "distinct": "a distinct count ({})", "per_entity": "a per-entity ratio ({})", "exclusion": "an exclusion ({})",
          "list_records": "a list of individual records ({})"}


def blank_periods(q: str) -> str:
    q = q.lower()
    for rx in PERIOD:
        q = re.sub(rx, " ", q)
    return q


def _stem(w: str) -> str:
    w = w.strip("'-")
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and re.search(r"(?:ss|x|ch|sh)es$", w):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def words(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z][a-z0-9_'-]*", (text or "").lower().replace("_", " "))}


def operations(question: str, periods: int = 0) -> dict[str, str]:
    """Operations the question asks for -> the words that ask for it. A change needs two periods (or a "vs" between
    periods); "champion versus challenger" is a breakdown, which the plan covers."""
    q = blank_periods(question)
    found = {}
    for name, rx in OPS.items():
        m = re.search(rx, q)
        if m:
            found[name] = m.group(0).strip()
    ql = question.lower()
    mentions = len(re.findall(r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|june?|july?|aug(?:ust)?|"
                              r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b", ql))
    mentions += len(re.findall(r"\b(?:this|last|previous|prior)\s+(?:month|week|quarter|year)\b|\bq[1-4]\b", ql))
    for name in ("change", "compare"):   # about periods only; "champion versus challenger" is a breakdown
        if name in found and max(periods, mentions) < 2:
            found.pop(name)
    return found


def gaps(question: str, explained: str | list[str], handled: set[str] = frozenset(), periods: int = 0,
         data_words: set[str] | None = None, definition: str = "") -> list[str]:
    """Parts of the question the plan does not express.
    explained   the plan's vocabulary (cues, phrases, values, column names, its SQL's identifiers);
    handled     operations the plan implements;
    data_words  every word that refers to data (columns, category values, catalog phrases): a leftover word is
                reported only if it is one of these or a proper noun ("Ontario"); ordinary words ("raised",
                "vary", "receive") cannot change the query, so they are not;
    definition  the metric's own formula and filters: an operation already inside it ("1 or more days past due" in
                the delinquency rate, "per case" in attempts per case) is covered."""
    vocab = words(" ".join(explained) if isinstance(explained, list) else explained)
    defn = (definition or "").lower()
    out = []
    for n, w in operations(question, periods).items():
        if n in handled:
            continue
        nums = re.findall(r"\d+(?:\.\d+)?", w)
        if n == "threshold" and nums and all(re.search(rf"(?<![\d.]){re.escape(x)}(?![\d.])", defn) for x in nums):
            continue
        if n == "per_entity" and w.lower() in defn:
            continue
        out.append(LABELS[n].format(w))
    proper = {_stem(m.lower()) for m in re.findall(r"(?<=[a-z,] )[A-Z][a-zA-Z]+", question)}
    q = re.sub(r"['’](?:s|re|ve|ll|d|t)\b", " ", blank_periods(question))
    for rx in OPS.values():          # operation words are reported above, not as leftovers
        q = re.sub(rx, " ", q)
    left = []
    for w in re.findall(r"[a-z][a-z0-9_'-]*", q.replace("_", " ")):
        s = _stem(w)
        if s in STOP or w in STOP or s in vocab or s in ENTITY or len(s) < 3:
            continue
        if data_words is not None and s not in data_words and w not in data_words and s not in proper:
            continue
        if s not in left:
            left.append(s)
    if left:
        out.append("words not used: " + ", ".join(left))
    return out
