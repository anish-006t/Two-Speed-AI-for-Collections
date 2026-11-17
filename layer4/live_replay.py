"""Live call replay (L4-4): step through a real, redacted transcript as if the call were happening.

Every customer turn (with the two turns before it for context) goes through Layer 3's extract(). Rules here turn the
typed answers into prompts for the agent, each with the customer's words that triggered it, the rule ID and the
policy it comes from. A spoken promise is re-scored with score_promise() and a safer date is suggested when it is
risky. A QA checklist ticks as the agent makes the required statements.

Text comes only from silver.transcript_turns_redacted. Prompts are suggestions to the agent, gated by confidence
(layer4/gates.py); nothing is sent to the customer.
"""
import re
from datetime import date, timedelta

from layer3.text.extract import extract
from layer4.gates import gate
from layer4.models.promise_break.score import score_promise
from shared import db

RISKY_PROMISE = 0.5
CONTEXT_TURNS = 2

# Rule: question, when it fires, what the agent should do, and the policy. "speaker" is whose turn it applies to.
RULES = (
    {"id": "R-HARDSHIP", "question": "hardship", "fires": lambda a: a in ("possible", "clear"), "level": "caution",
     "title": "Hardship mentioned",
     "prompt": "Acknowledge it and record the hardship with the stated reason. Offer an eligible option before asking "
               "for payment, and do not ask for a payment the customer says they cannot afford.",
     "policy": "POL-COLL-004 §4.2", "speaker": "customer"},
    {"id": "R-INSOLVENCY", "question": "insolvency_mention", "fires": lambda a: a is True, "level": "stop",
     "title": "Insolvency mentioned",
     "prompt": "Stop collection activity: ask for the trustee's name and filing details; the case moves to "
               "insolvency hold.", "policy": "POL-COLL-001 §7.3", "speaker": "customer"},
    {"id": "R-CEASE", "question": "cease_request", "fires": lambda a: a is True, "level": "stop",
     "title": "Customer asked to stop contact",
     "prompt": "Confirm the request, explain that required letters continue, and record it; no further calls, texts "
               "or emails.", "policy": "POL-COLL-001 §5.4", "speaker": "customer"},
    {"id": "R-DISPUTE", "question": "dispute", "fires": lambda a: a is True, "level": "caution",
     "title": "Dispute raised",
     "prompt": "Open a dispute and pause payment requests for the disputed amount; send fraud claims to the fraud "
               "team.", "policy": "PRC-COLL-012 §6.1", "speaker": "customer"},
    {"id": "R-THIRD-PARTY", "question": "third_party_present", "fires": lambda a: a is True, "level": "stop",
     "title": "Someone else is on the call",
     "prompt": "Do not discuss the debt with a third party; speak only with the customer or their authorised "
               "representative.", "policy": "POL-COLL-001 §5.4", "speaker": "customer"},
    {"id": "R-COMPLAINT", "question": "complaint", "fires": lambda a: a is True, "level": "caution",
     "title": "Complaint", "prompt": "Acknowledge the complaint and log it in the complaints process.",
     "policy": "POL-COLL-007 §8.1", "speaker": "customer"},
    {"id": "R-PROTECTED", "question": "protected_attribute_mentioned", "fires": lambda a: a is True, "level": "info",
     "title": "Personal detail shared",
     "prompt": "Do not use it in any decision and do not write it in the notes.", "policy": "POL-COLL-004 §4.6",
     "speaker": "customer"},
    {"id": "R-AGENT-THREAT", "question": "legal_threat_by_agent", "fires": lambda a: a is True, "level": "stop",
     "title": "Agent wording: possible threat",
     "prompt": "Use only approved factual wording about next steps; no threats of legal action or seizure.",
     "policy": "QA item 4 (no threatening language)", "speaker": "agent"},
)

WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
                "eighty": 80, "ninety": 90}
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], start=1)}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTH_NAME = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"


# ------------------------------------------------------------------------------------------------ loading a call

def load_call(transcript_id: str, con=None) -> dict:
    con = con or db.connect()
    meta = con.execute("""SELECT t.transcript_id, t.case_id, t.agent_id, t.call_start_ts, t.duration_sec, t.direction,
                                 x.golden_customer_id
                          FROM bronze.call_transcripts t
                          LEFT JOIN identity.crosswalk x
                            ON x.source_key = 'CRM:' || t.crm_customer_id AND x.valid_to IS NULL
                          WHERE t.transcript_id = ?""", [transcript_id]).fetchone()
    if meta is None:
        raise LookupError(f"No transcript {transcript_id}")
    turns = con.execute("""SELECT turn_index, speaker, start_sec, end_sec, text_redacted
                           FROM silver.transcript_turns_redacted WHERE transcript_id = ?
                           ORDER BY turn_index""", [transcript_id]).fetchall()
    keys = ("transcript_id", "case_id", "agent_id", "call_start_ts", "duration_sec", "direction", "golden_customer_id")
    call = dict(zip(keys, meta))
    call["call_date"] = str(call["call_start_ts"])[:10]
    call["turns"] = [{"index": int(t[0]), "speaker": t[1], "start_sec": float(t[2] or 0), "end_sec": float(t[3] or 0),
                      "text": t[4] or ""} for t in turns]
    return call


# ------------------------------------------------------------------------------------------- amounts and dates

def parse_amount(value) -> float | None:
    """A number from an extract() answer: 400, '$400.00', '1,250', 'four hundred', 'two hundred fifty'."""
    if value is None or value is False:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    s = str(value).lower().replace(",", "")
    m = re.search(r"\$?\s*(\d+(?:\.\d{1,2})?)", s)
    if m:
        return float(m.group(1))
    total, current, seen = 0, 0, False
    for word in re.findall(r"[a-z]+", s):
        if word in WORD_NUMBERS:
            current += WORD_NUMBERS[word]
            seen = True
        elif word == "hundred" and seen:
            current = max(current, 1) * 100
        elif word == "thousand" and seen:
            total += max(current, 1) * 1000
            current = 0
    return float(total + current) if seen else None


def resolve_date(value, call_date: str) -> str | None:
    """An ISO date from an extract() answer, read relative to the call date: '2026-09-18', 'September 18',
    'the 18th', 'Friday', 'tomorrow', 'next week'. Returns None rather than guessing."""
    if not value:
        return None
    s = str(value).strip().lower()
    base = date.fromisoformat(call_date)
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", s)
    if m:
        return date(*map(int, m.groups())).isoformat()
    if "today" in s:
        return base.isoformat()
    if "tomorrow" in s:
        return (base + timedelta(days=1)).isoformat()
    if "next week" in s:
        return (base + timedelta(days=7)).isoformat()
    for i, name in enumerate(WEEKDAYS):
        if name in s:
            ahead = (i - base.weekday()) % 7 or 7
            return (base + timedelta(days=ahead)).isoformat()
    words = re.findall(r"[a-z]+", s)
    month = next((n for name, n in MONTHS.items() if name in s or name[:3] in words), None)
    if month:  # the number next to the month name: "September 18", "18th of Sept"
        day = (re.search(MONTH_NAME + r"\s+(\d{1,2})(?!\d)", s)
               or re.search(r"(?<![\d$.,])(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + MONTH_NAME, s))
    else:      # a bare day ("the 18th"), never part of money ($400.00) or a time (10:30)
        day = (re.search(r"(?<![\d$.,:])(\d{1,2})(?:st|nd|rd|th)(?![\d:])", s)
               or re.search(r"\bthe\s+(\d{1,2})(?![\d:.])", s))
    if day:
        d = int(day.group(1))
        y, mo = base.year, month or base.month
        for _ in range(2):
            try:
                cand = date(y, mo, d)
            except ValueError:
                return None
            if month:
                # A month name is a date ahead of the call: "January 5" said in September is next January.
                return (cand if cand >= base - timedelta(days=31) else date(y + 1, mo, d)).isoformat()
            if cand >= base:
                return cand.isoformat()
            mo, y = (1, y + 1) if mo == 12 else (mo + 1, y)
    return None


# ------------------------------------------------------------------------------------------------- QA checklist

# Each item: how the agent's words tick it, in order. A deterministic first pass; QA scoring is first on the cut list.
QA_ITEMS = (
    ("recording_notice_given", "Recording notice given", r"\brecorded\b|\brecording\b"),
    ("agent_identified_self_and_purpose", "Agent identified self and purpose", r"calling (from|on behalf of) maple"),
    ("identity_verified_before_disclosure", "Identity verified before disclosure",
     r"date of birth|confirm your (identity|address|postal)|security question"),
    ("hardship_handled_per_policy", "Hardship handled per policy",
     r"hardship (option|program|plan|team)|reduced payment|skip a payment|defer|payment plan"),
    ("ptp_details_confirmed_back", "Promise confirmed back", r"(just to confirm|to confirm).*\$?\d"),
    ("respectful_close_and_next_steps", "Respectful close and next steps",
     r"thank you for your time|have a (good|great|nice)|you(?:'ll| will) (receive|hear)"),
)
DEBT_WORDS = r"past due|overdue|balance|amount owing|\$\d"


def qa_tick(state: dict, turn: dict, flags: set) -> list[str]:
    """Update the checklist with one agent turn; returns the items ticked or failed by this turn."""
    changed = []
    if turn["speaker"] != "agent":
        return changed
    text = turn["text"].lower()
    if re.search(DEBT_WORDS, text) and state["identity_verified_before_disclosure"]["status"] == "pending":
        state["identity_verified_before_disclosure"].update(status="failed", turn=turn["index"],
                                                            evidence=turn["text"][:160])
        changed.append("identity_verified_before_disclosure")
    for code, _, pattern in QA_ITEMS:
        item = state[code]
        if item["status"] != "pending" or not re.search(pattern, text):
            continue
        if code == "hardship_handled_per_policy" and "R-HARDSHIP" not in flags:
            continue
        if code == "ptp_details_confirmed_back" and "PROMISE" not in flags:
            continue
        item.update(status="done", turn=turn["index"], evidence=turn["text"][:160])
        changed.append(code)
    return changed


def new_qa_state() -> dict:
    return {code: {"name": name, "status": "pending", "turn": None, "evidence": None} for code, name, _ in QA_ITEMS}


# ------------------------------------------------------------------------------------------------------- replay

def _window(turns: list[dict], i: int) -> str:
    lo = max(0, i - CONTEXT_TURNS)
    return "\n".join(f"{t['speaker'].capitalize()}: {t['text']}" for t in turns[lo:i + 1])


def promise_check(answers: dict, call: dict, window: str) -> dict | None:
    """A spoken promise: amount and date from the typed answers (or the agent's confirmation in the window), then the
    break risk and, when risky, a safer date."""
    pm = answers.get("promise_mentioned", {})
    amount = parse_amount(answers.get("promise_amount", {}).get("answer"))
    due = resolve_date(answers.get("promise_date", {}).get("answer"), call["call_date"])
    current = window.splitlines()[-1] if window else ""
    # The agent reading the promise back ("Agent: Just to confirm, $400 on September 18") counts as hearing it.
    read_back = current.lower().startswith("agent:") and "confirm" in current.lower() and "$" in current
    if not (pm.get("answer") is True or amount or due or read_back):
        return None
    confirmations = [line.split(":", 1)[-1] for line in window.splitlines() if "confirm" in line.lower()]
    if amount is None:
        amount = parse_amount(confirmations[-1] if confirmations and "$" in confirmations[-1]
                              else (window if "$" in window else None))
    if due is None and confirmations:
        due = resolve_date(confirmations[-1], call["call_date"])
    out = {"amount": amount, "due_date": due, "prob": pm.get("prob"),
           "evidence": pm.get("evidence") or answers.get("promise_amount", {}).get("evidence"),
           "intent": answers.get("promise_intent_strength", {}).get("answer")}
    if not (amount and due and call.get("golden_customer_id")):
        out["status"] = "incomplete"
        out["missing"] = [n for n, v in (("amount", amount), ("date", due),
                                          ("customer record", call.get("golden_customer_id"))) if not v]
        return out
    try:
        score = score_promise(call["golden_customer_id"], amount, due, call["call_date"])
    except Exception as e:  # noqa: BLE001 - the replay continues without a score
        out.update(status="unscored", missing=[f"risk model ({type(e).__name__})"])
        return out
    out.update(status="risky" if score["risk"] >= RISKY_PROMISE else "ok", **score)
    if out["status"] == "risky" and score.get("safer_date") and score["safer_date"] != due:
        out["suggestion"] = (f"Risk {score['risk']:.0%}. Suggest {score['safer_date']} instead (the day after the "
                             f"customer's usual income date), or a smaller first payment.")
    return out


def iter_replay(call: dict):
    """Yield one step per turn, computing each only when asked for (each turn is one extract() call)."""
    turns, fired, qa, last_promise = call["turns"], set(), new_qa_state(), None
    for i, turn in enumerate(turns):
        step = {"turn": turn, "prompts": [], "promise": None, "qa_changed": []}
        if turn["text"].strip():
            window = _window(turns, i)
            answers = extract(window, "turn")
            for rule in RULES:
                if rule["id"] in fired or rule["speaker"] != turn["speaker"]:
                    continue
                a = answers.get(rule["question"])
                if a and rule["fires"](a["answer"]):
                    fired.add(rule["id"])
                    step["prompts"].append({
                        "rule": rule["id"], "level": rule["level"], "title": rule["title"], "prompt": rule["prompt"],
                        "policy": rule["policy"], "answer": a["answer"], "prob": a["prob"],
                        "gate": gate(a["prob"], rule["question"]),
                        "evidence": a.get("evidence") or turn["text"], "said_at_sec": turn["start_sec"],
                        "model_version": a.get("model_version")})
            if turn["speaker"] == "customer" or "confirm" in turn["text"].lower():
                p = promise_check(answers, call, window)
                key = p and (p.get("amount"), p.get("due_date"))
                if p and key != last_promise and (p.get("amount") or p.get("due_date")):
                    last_promise = key
                    fired.add("PROMISE")
                    p["gate"] = gate(p.get("prob"), "promise")
                    step["promise"] = p
        step["qa_changed"] = qa_tick(qa, turn, fired)
        step["qa"] = {k: dict(v) for k, v in qa.items()}
        yield step


def replay(transcript_id: str, con=None, call: dict | None = None) -> dict:
    """Run the whole call and return every step (evaluation, tests, warming the LLM cache before a demo)."""
    call = call or load_call(transcript_id, con)
    steps = list(iter_replay(call))
    fired = sorted({p["rule"] for s in steps for p in s["prompts"]} | ({"PROMISE"} if any(s["promise"] for s in steps)
                                                                       else set()))
    return {"call": {k: v for k, v in call.items() if k != "turns"}, "steps": steps, "fired": fired}


def time_to_flag(result: dict, rule: str = "R-HARDSHIP") -> float | None:
    """Seconds into the call when a rule first fired (for comparing with hardship_first_mention_sec)."""
    for step in result["steps"]:
        for p in step["prompts"]:
            if p["rule"] == rule:
                return p["said_at_sec"]
    return None


def demo_calls(con=None, limit: int = 20) -> list[dict]:
    """Labelled calls with clear hardship and a promise: good for a demo and for measuring time-to-flag."""
    from layer1.pipeline import config
    con = con or db.connect()
    path = (config.RAW / "labels" / "call_transcripts_labels_public_500.csv").as_posix()
    rows = con.execute(f"""SELECT l.transcript_id, l.hardship_first_mention_sec, l.label_ptp_amount, l.label_ptp_date
                           FROM read_csv_auto('{path}', all_varchar=true) l
                           WHERE l.label_hardship = 'clear' AND l.label_ptp_mentioned = 'true'
                           ORDER BY l.transcript_id LIMIT ?""", [limit]).fetchall()
    return [{"transcript_id": r[0], "hardship_first_mention_sec": float(r[1]) if r[1] else None,
             "label_ptp_amount": r[2], "label_ptp_date": r[3]} for r in rows]

