"""Call-summary lane (architecture §8): case -> most recent RECORDED call -> typed facts -> one gold-style line.

Lookup, not search: the transcript is found by SQL, so the right call is summarised. The facts are typed (reason,
dispute, promise amount and date, complaint, insolvency, stop-contact, next step) and code writes the sentence in the
labelled style, e.g. "Customer reports illness; promised $95 on 14 Oct; next step: hardship plan review."
Text comes only from silver.transcript_turns_redacted (or the transcript file with Layer 2's local redaction).
Engines: Layer 3 extract() if present, else the LLM on redacted text, else keyword rules learnt from the 500 public
labels (88% exact sentences). Laya is not used here: refining with it dropped exact matches from 90% to 67%.
"""
from __future__ import annotations

import os

import calendar
import json
import re
from datetime import date

from layer2 import adapters, llm_guard
from layer2.adapters import REPO
from layer2.parse import Parsed

REASONS = {
    "reduced_income": "Customer reports reduced hours / income", "job_loss": "Customer reports job loss",
    "separation": "Customer reports separation", "illness": "Customer reports illness",
    "rate_reset": "Customer reports payment increase after rate reset", "disability": "Customer reports disability",
    "disaster_evacuation": "Customer reports evacuation (disaster)", "ei_waiting": "Customer reports waiting for EI",
}
DISPUTES = {"late_fee": "disputes late fee", "billing_error": "disputes billing error",
            "payment_not_applied": "disputes payment not applied", "fraud": "disputes fraud"}
NEXT_STEPS = {"send_payment_link": "send payment link", "hardship_plan_review": "hardship plan review",
              "escalate_supervisor": "escalate supervisor", "dispute_investigation": "dispute investigation",
              "stop_contact": "stop contact", "call_back": "call back"}

FIELDS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["reason", "hardship", "dispute", "complaint", "insolvency", "cease_request", "promise_amount",
                 "promise_date", "next_step"],
    "properties": {
        "reason": {"type": "string", "enum": list(REASONS) + ["none"]},
        "hardship": {"type": "string", "enum": ["none", "possible", "clear"]},
        "dispute": {"type": "string", "enum": list(DISPUTES) + ["none"]},
        "complaint": {"type": "boolean"},
        "insolvency": {"type": "boolean"},
        "cease_request": {"type": "boolean"},
        "promise_amount": {"type": ["number", "null"]},
        "promise_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "next_step": {"type": "string", "enum": list(NEXT_STEPS)},
    },
}
SYSTEM = """You read a redacted collections call transcript and return typed facts only.
- reason: the hardship cause the customer states (none if no cause is stated).
- hardship: clear if a hardship cause is stated, possible if the customer only says money is tight, else none.
- dispute: what the customer disputes, if anything. complaint: the customer wants to complain.
- insolvency: the customer mentions a consumer proposal or bankruptcy. cease_request: asks the bank to stop contact.
- promise_amount / promise_date: the payment the customer agreed to make (amount in dollars, date as YYYY-MM-DD using
  the call date to pick the year); null when no payment was agreed.
- next_step: the agent's next action."""


def find_transcript(con, case_id: str) -> dict | None:
    row = con.execute("SELECT transcript_id, call_start_ts FROM layer2.transcript_index WHERE case_id = ? "
                      "ORDER BY call_start_ts DESC NULLS LAST LIMIT 1", [case_id]).fetchone()
    return {"transcript_id": row[0], "call_start_ts": row[1]} if row else None


# Same shapes as Layer 1's redaction (layer1/pipeline/redact.py), used only when its redacted table is missing.
LOCAL_REDACTIONS = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "<EMAIL_ADDRESS>"),
    (re.compile(r"\b\d(?:[ -]?\d){11,18}\b"), "<LONG_NUMBER>"),
    (re.compile(r"(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}\b"), "<PHONE_NUMBER>"),
    (re.compile(r"\b\d{3}[ -]\d{3}[ -]\d{3}\b"), "<CA_SIN>"),
    (re.compile(r"\b[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z][ -]?\d[ABCEGHJ-NPRSTV-Z]\d\b", re.I), "<CA_POSTAL_CODE>"),
    (re.compile(r"\b(?:C|L|CHQ|SAV|TFS|USD)-\d{4,}\b|\b\d{10}\b"), "<BANK_ACCOUNT_ID>"),
    (re.compile(r"\b[A-Z]{3}-\d{4}\b"), "<CRM_ID>"),
    (re.compile(r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|(?:19|20)\d\d-\d\d-\d\d)\b"), "<DATE_OF_BIRTH?>"),
    (re.compile(r"\b((?i:my name is|this is|speaking with|am i speaking (?:with|to)|je m'appelle|ici)|Mr\.?|Mrs\.?|Ms\.?|Miss)\s+"
                r"([A-ZÀ-Ý][a-zà-ÿ'-]+)(\s+[A-ZÀ-Ý][a-zà-ÿ'-]+)?"), r"\1 <PERSON>"),
]


def local_redact(text: str) -> str:
    for rx, tag in LOCAL_REDACTIONS:
        text = rx.sub(tag, text)
    return text


def load_turns(con, transcript_id: str) -> tuple[list[tuple[str, str]] | None, str]:
    """Redacted turns: Layer 1's silver table, else the transcript file with Layer 2's local redaction."""
    if adapters.relation_exists(con, "silver", "transcript_turns_redacted"):
        rows = con.execute("SELECT speaker, text_redacted FROM silver.transcript_turns_redacted "
                           "WHERE CAST(transcript_id AS VARCHAR) = ? ORDER BY try_cast(turn_index AS INTEGER)",
                           [str(transcript_id)]).fetchall()
        if rows:
            return [(s or "", t or "") for s, t in rows], "silver.transcript_turns_redacted"
    try:
        row = con.execute("SELECT file_path FROM bronze.call_transcripts WHERE CAST(transcript_id AS VARCHAR) = ?",
                          [str(transcript_id)]).fetchone()
    except Exception:  # noqa: BLE001
        row = None
    path = REPO / "data" / "raw" / "maple_collections_release" / "files" / row[0] if row and row[0] else None
    if not path or not path.exists():
        return None, "none"
    turns = json.loads(path.read_text(encoding="utf-8")).get("turns", [])
    turns = sorted(turns, key=lambda t: t.get("turn_index") or 0)
    return [(t.get("speaker") or "", local_redact(t.get("text") or "")) for t in turns], "transcript file (local redaction)"


def _money(v: float) -> str:
    return f"${round(v):,}"


def _short_date(d: date) -> str:
    return f"{d.day} {calendar.month_abbr[d.month]}"


def render(f: dict) -> str:
    parts = []
    if f.get("reason") in REASONS:
        parts.append(REASONS[f["reason"]])
    elif f.get("hardship") == "possible":
        parts.append("Customer indicates finances are tight")
    if f.get("dispute") in DISPUTES:
        parts.append(DISPUTES[f["dispute"]])
    if f.get("promise_amount"):
        d = f.get("promise_date")
        when = ""
        if d:
            try:
                when = " on " + _short_date(date.fromisoformat(str(d)[:10]))
            except ValueError:
                when = f" on {d}"
        parts.append(f"promised {_money(float(f['promise_amount']))}{when}")
    if f.get("complaint"):
        parts.append("wants to complain")
    if f.get("insolvency"):
        parts.append("mentions consumer proposal / bankruptcy")
    if f.get("cease_request"):
        parts.append("asked to stop contact")
    if not parts:
        parts.append("No commitment made")
    parts.append(f"next step: {NEXT_STEPS.get(f.get('next_step'), 'call back')}")
    return "; ".join(parts) + "."


# --------------------------------------------------------------------------------------------- engines

_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_MOIS = {"janv": 1, "janvier": 1, "févr": 2, "fevr": 2, "février": 2, "mars": 3, "avr": 4, "avril": 4, "mai": 5,
         "juin": 6, "juil": 7, "juillet": 7, "août": 8, "aout": 8, "sept": 9, "septembre": 9, "oct": 10,
         "octobre": 10, "nov": 11, "novembre": 11, "déc": 12, "dec": 12, "décembre": 12}
_AMOUNT = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)|(\d[\d,]*(?:[.,]\d{1,2})?)\s?\$|(\d[\d,]*(?:\.\d{1,2})?)\s+dollars|"
                     r"\bpa(?:y|yer)\s+(\d[\d,]*(?:\.\d{1,2})?)\b")
_DATE_EN = [re.compile(rf"\b{_MON}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I),
            re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MON}\b", re.I)]
_DATE_FR = re.compile(r"\ble\s+(\d{1,2})(?:er)?\s+(" + "|".join(sorted(_MOIS, key=len, reverse=True)) + r")\b", re.I)


def _find_date(text: str):
    from layer2.parse import _month
    m = _DATE_FR.search(text)
    if m:
        return _MOIS[m.group(2).lower()], int(m.group(1))
    for rx in _DATE_EN:
        m = rx.search(text)
        if m:
            a, b = m.groups()
            mon, day = (a, b) if not a.isdigit() else (b, a)
            return _month(mon), int(day)
    return None


def _find_amount(text: str):
    for m in _AMOUNT.finditer(text):
        raw = next(g for g in m.groups() if g)
        value = float(raw.replace(",", "") if raw.count(",") != 1 or "." in raw or len(raw.split(",")[1]) == 3
                      else raw.replace(",", "."))
        return value
    return None


def regex_promise(turns, call_day: date | None) -> tuple[float | None, str | None]:
    """The agent's read-back ("Just to confirm, a payment of $595.00 on October 5" / "Pour confirmer, un paiement
    de 405.00 $ le 3 juil.") is the most reliable; otherwise the last turn with both an amount and a date."""
    confirm = [t for s, t in turns if re.search(r"confirm|pour confirmer|read (?:that|it) back", t, re.I)]
    others = [t for _, t in turns]
    for text in list(reversed(confirm)) + list(reversed(others)):
        if re.search(r"past due|en souffrance|balance|solde", text, re.I):
            continue
        amount, md = _find_amount(text), _find_date(text)
        if amount is not None and md:
            mo, day = md
            year = call_day.year if call_day else 2026
            try:
                d = date(year, mo, day)
                if call_day and (d - call_day).days < -60:   # a date months before the call means next year
                    d = date(year + 1, mo, day)
            except ValueError:
                continue
            return amount, d.isoformat()
    for text in reversed(others):   # "I can pay $505 the 16th": the next 16th after the call
        m = re.search(r"\bthe (\d{1,2})(?:st|nd|rd|th)\b|\ble (\d{1,2})(?:er)?\b(?! ?\$)", text, re.I)
        amount = _find_amount(text)
        if m and amount is not None and call_day:
            day = int(m.group(1) or m.group(2))
            y, mo = call_day.year, call_day.month
            for _ in range(2):
                try:
                    d = date(y, mo, day)
                    if d > call_day:
                        return amount, d.isoformat()
                except ValueError:
                    pass
                y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    return None, None


# Keyword rules: the always-available engine (no model). Measured on the 500 public labels by
# `python -m layer2.benchmark.eval_summaries`; Laya / the LLM / Layer 3 replace it when present.
REASON_RULES = [   # most specific first; reduced_income is the most generic, so it is tried last
    ("rate_reset", r"rate (?:went up|reset|increase|jumped)|after the rate|payment (?:went up|increased|jumped)|"
                   r"renewal|renewed|higher rate|variable rate|taux|renouvellement"),
    ("disaster_evacuation", r"evacuat|wildfire|forest fire|flood|hurricane|lost (?:our|my) (?:home|house)|évacu|inond|feux? de forêt"),
    ("separation", r"separat|divorc|split (?:up|with)|broke up|my ex\b|left me|moved out|séparation|séparé"),
    ("disability", r"disab|injur|accident|can't walk|wheelchair|long[- ]term disability|invalidit|bless[ée]"),
    ("illness", r"\bsick\b|\bill\b|illness|medical|hospital|surgery|cancer|chemo|treatment|"
                r"malade|maladie|hôpital|chirurgie|congé de maladie"),
    ("ei_waiting", r"\bei\b|employment insurance|waiting (?:on|for) (?:my )?(?:ei|benefits|claim)|assurance[- ]emploi|\bae\b"),
    ("job_loss", r"lost my job|laid off|lay ?offs?\b|let go|unemployed|position was eliminated|job was eliminated|"
                 r"plant closed|mis à pied|perdu mon (?:emploi|travail)|congédié|licencié"),
    ("reduced_income", r"(?:shifts?|hours?)\b[^.]{0,25}\b(?:cut|reduced)|cut (?:my|our) hours|fewer hours|reduced hours|"
                       r"(?:only|just) (?:getting|working) \d+ (?:days|shifts|hours)|pay (?:got |was )?cut|cut back|"
                       r"less work|part[- ]time now|income (?:dropped|went down)|only bringing home|lost (?:my|the) overtime|"
                       r"making less|earning less|"
                       r"heures (?:ont été )?(?:coupées|réduites)|quarts? (?:ont été )?(?:coupés|réduits)|salaire (?:a été )?réduit"),
]
TIGHT = (r"\btight\b|struggl|hard time|tough (?:month|time)|short this month|money is short|behind on bills|"
         r"(?:things|it|money) (?:are|is) hard|hard this month|budget is|difficile financièrement|difficile ce mois")
DISPUTE_RULES = [
    ("fraud", r"fraud|didn't make (?:this|that|those)|not my (?:transaction|purchase)|stolen card|someone used my|"
              r"pas fait cet achat|fraude"),
    ("late_fee", r"late fee|late charge|charged a (?:late )?fee|frais de retard"),
    ("payment_not_applied", r"(?:payment|paid)[^.]{0,20}(?:wasn't|was not|isn't|not|never) (?:applied|posted|showing|credited)|"
                            r"already (?:made a )?pa(?:id|yment)|paiement[^.]{0,30}pas été appliqué"),
    ("billing_error", r"billing error|charged twice|double charged|wrong amount|statement is wrong|overcharg|erreur de facturation"),
]


def fields_rules(turns, call_day) -> dict:
    cust = " ".join(t for s, t in turns if s.lower().startswith(("cust", "client"))).lower()
    agent = " ".join(t for s, t in turns if s.lower().startswith("agent")).lower()
    reason = next((k for k, rx in REASON_RULES if re.search(rx, cust)), "none")
    hardship = "clear" if reason != "none" else ("possible" if re.search(TIGHT, cust) else "none")
    dispute = next((k for k, rx in DISPUTE_RULES if re.search(rx, cust)), "none")
    complaint = bool(re.search(r"complain|complaint|ombudsman|plainte", cust))
    insolvency = bool(re.search(r"consumer proposal|bankrupt|insolvency|trustee|proposition de consommateur|faillite", cust))
    # a formal request ("Please stop calling me."), not frustration ("Stop calling me every day!")
    cease = bool(re.search(r"please stop (?:calling|contacting) me|i want you to stop (?:calling|contacting)|"
                           r"veuillez cesser de m'appeler|arrêtez de m'appeler, s'il vous plaît", cust))
    amount, when = regex_promise(turns, call_day)
    # next step: the rule the 500 public labels follow
    if cease or insolvency:
        step = "stop_contact"
    elif complaint or re.search(r"transfer(?:ring)? you|my supervisor|team lead|je vous transf[eè]re|superviseur", agent):
        step = "escalate_supervisor"
    elif dispute != "none":
        step = "dispute_investigation"
    elif hardship == "clear":
        step = "hardship_plan_review"
    elif amount is not None:
        step = "send_payment_link"
    else:
        step = "call_back"
    return {"reason": reason, "hardship": hardship, "dispute": dispute, "complaint": complaint,
            "insolvency": insolvency, "cease_request": cease, "promise_amount": amount, "promise_date": when,
            "next_step": step}


def refine_with_laya(turns, fields: dict, threshold: float = 0.6) -> dict:
    """Keep the rules' answers; let Laya fill the reason and next step only where the rules found nothing."""
    text = " ".join(f"{s}: {t}" for s, t in turns)[-6000:]
    qs = {}
    if fields.get("reason") == "none":
        qs["reason"] = {"type": "choice", "instructions": "What hardship cause does the customer state?",
                        "criteria": {**{k: v.replace("Customer reports ", "") for k, v in REASONS.items()},
                                     "none": "no hardship cause stated"}}
    if not qs:
        return fields
    try:
        d = adapters.decide(text, qs, engines=("laya",))
    except adapters.DecisionUnavailable:
        return fields
    out = dict(fields)
    for k, v in d.items():
        if v["answer"] and (v["prob"] or 0) >= threshold:
            out[k] = v["answer"]
            if k == "reason" and v["answer"] != "none":
                out["hardship"] = "clear"
    return out


def fields_llm(turns, call_day) -> dict:
    text = "\n".join(f"{s}: {t}" for s, t in turns)
    return adapters.complete(f"Call date: {call_day}\n\nTRANSCRIPT\n{text}", system=SYSTEM, json_schema=FIELDS_SCHEMA,
                             model=None, max_tokens=800)


def rules_found_nothing(fields: dict) -> bool:
    return (fields["reason"] == "none" and fields["dispute"] == "none" and not fields["promise_amount"]
            and fields["hardship"] == "none" and not fields["complaint"])


def fields_hybrid(turns, call_day, use_model: bool = True) -> tuple[dict, str]:
    """Rules first (96% exact sentences on the 500 public labels). Only when the rules found nothing, the LLM reads
    the call and its facts are checked against it (llm_guard.summary_fields_ok). Returns (fields, engine)."""
    fields, engine = fields_rules(turns, call_day), "rules+regex"
    if rules_found_nothing(fields) and use_model and adapters.llm_available():
        try:
            llm_fields = fields_llm(turns, call_day)
            ok, problem = llm_guard.summary_fields_ok(llm_fields, turns, call_day)
            if ok:
                fields, engine = llm_fields, "llm (checked against the call)"
            else:
                adapters.audit({"action": "guardrail", "lane": "call_summary", "problem": problem})
        except adapters.LLMUnavailable:
            pass
    return fields, engine


def answer_call_summary(p: Parsed, role: str = "analyst", use_model: bool = True) -> dict:
    if not p.case_ids:
        return {"answer": "Please give a case ID (for example CS-2026-614370) to summarise its most recent recorded call.",
                "sql_or_sources": "", "refused": False, "reason": "no case ID", "route": "call_summary", "table": None}
    case_id = p.case_ids[0]
    con = adapters.connect(read_only=True)
    try:
        t = find_transcript(con, case_id)
        if not t:
            return {"answer": f"Case {case_id} has no recorded call (no transcript).", "refused": False,
                    "sql_or_sources": f"layer2.transcript_index: no transcript for {case_id}", "reason": "no transcript",
                    "route": "call_summary", "table": None}
        turns, text_source = load_turns(con, t["transcript_id"])
        banner = ""
        if role == "agent" and adapters.relation_exists(con, "gold", "c360_customer"):
            row = con.execute("SELECT g.verify_before_disclosure FROM layer2.f_cases c JOIN gold.c360_customer g "
                              "USING (golden_customer_id) WHERE c.case_id = ?", [case_id]).fetchone()
            if row and row[0]:
                banner = "Verify the customer's identity before discussing the debt (identity confidence is not high). "
    finally:
        con.close()
    if not turns:
        return {"answer": f"The transcript {t['transcript_id']} for case {case_id} could not be read.",
                "sql_or_sources": f"transcript {t['transcript_id']}", "refused": False, "reason": "no redacted text",
                "route": "call_summary", "table": None}
    call_day = t["call_start_ts"].date() if t["call_start_ts"] else None
    engine, fields = None, None
    # Layer 3's extract() returns amounts and dates as text ("$195", "the 5th"), which does not fit the official
    # summary format, and it is not measured on the summary labels; Layer 2's rules are (96% exact). Opt in with
    # L2_USE_LAYER3_EXTRACT=1.
    extract = adapters.layer3_extract() if os.environ.get("L2_USE_LAYER3_EXTRACT") == "1" else None
    if extract is not None:
        try:
            raw = extract("\n".join(f"{s}: {x}" for s, x in turns), "transcript")
            fields = {k: (v.get("answer") if isinstance(v, dict) else v) for k, v in raw.items()}
            engine = "layer3"
        except Exception:  # noqa: BLE001 - fall through to Layer 2's own engines
            fields = None
    if fields is None:
        fields, engine = fields_hybrid(turns, call_day, use_model)
    summary = render(fields)
    return {"answer": banner + summary, "sql_or_sources": f"transcript {t['transcript_id']} (case {case_id}, call of "
                                                          f"{call_day}; most recent recorded call; text from "
                                                          f"{text_source})",
            "refused": False, "reason": f"summarised the most recent recorded call with {engine}", "route": "call_summary",
            "table": [fields], "engines": {"call_summary": engine}}
