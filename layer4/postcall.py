"""Post-call summary (L4-5): draft the fields and a one-line narrative, the agent edits and approves or rejects, and
approved fields flow into layer3.text_features (source_type 'approved_summary') for the next pre-call brief.

Fields come from Layer 3's extract() over the whole redacted call, with the live replay's promise (amount and date
read back by the agent) taking precedence. The narrative follows the gold label style
("Customer reports job loss; promised $345 on 18 Sep; next step: hardship plan review.") and is written by rules from
the fields, not by an LLM, so it never says anything the fields don't. Every field keeps its probability, evidence and
confidence gate; nothing is saved without a person approving it.

Disability is a protected ground (POL-COLL-004 §4.6): a hardship for that reason is written as
"a health-related hardship" and stored as reason "health", never as disability.
"""
import json
import uuid
from datetime import date, datetime, timezone

from layer3.text.extract import extract
from layer4 import live_replay
from layer4.gates import gate
from shared import audit, db

NEXT_STEPS = ("send_payment_link", "hardship_plan_review", "escalate_supervisor", "dispute_investigation",
              "stop_contact", "call_back")
REASON_PHRASE = {
    "reduced_income": "reduced hours / income", "job_loss": "job loss", "separation": "separation",
    "illness": "illness", "medical": "illness", "rate_reset": "payment increase after rate reset",
    "disaster_evacuation": "evacuation (disaster)", "natural_disaster": "evacuation (disaster)",
    "ei_waiting": "waiting for EI", "bereavement": "bereavement", "strike_lockout": "strike or lockout",
    "health": "a health-related hardship", "cost_of_living": "cost of living", "family_emergency": "a family emergency",
}
PROTECTED_REASONS = {"disability": "health"}
DISPUTE_PHRASE = {"late_fee": "late fee", "payment_not_applied": "payment not applied", "billing_error": "billing error",
                  "balance": "billing error", "fraud": "fraud", "identity": "identity", "service": "service"}
SUMMARY_MODEL = "postcall-rules-1"

DDL = """
CREATE SCHEMA IF NOT EXISTS layer4;
CREATE TABLE IF NOT EXISTS layer4.summaries (
    summary_id VARCHAR PRIMARY KEY, transcript_id VARCHAR, case_id VARCHAR, golden_customer_id VARCHAR,
    call_ts TIMESTAMP, status VARCHAR NOT NULL, draft VARCHAR, final VARCHAR, narrative_draft VARCHAR,
    narrative VARCHAR, edits VARCHAR, model_version VARCHAR, created_by VARCHAR, created_at TIMESTAMP,
    approved_by VARCHAR, approved_at TIMESTAMP, reject_reason VARCHAR
);
"""


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _field(value, prob=None, evidence="", source=""):
    return {"value": value, "prob": None if prob is None else round(float(prob), 4), "evidence": evidence or "",
            "source": source, "gate": gate(prob)}


# --------------------------------------------------------------------------------------------- fields and narrative

def next_step(f: dict) -> str:
    """Rule order fitted to the public labels' own fields (494/500 agree)."""
    v = {k: (f[k]["value"] if isinstance(f.get(k), dict) else f.get(k)) for k in f}
    if v.get("cease_request") or v.get("insolvency_mention"):
        return "stop_contact"
    if v.get("complaint") or v.get("sentiment") is not None and v["sentiment"] <= -0.7:
        return "escalate_supervisor"
    if v.get("dispute"):
        return "dispute_investigation"
    if v.get("hardship") == "possible" and v.get("promise_amount"):
        return "send_payment_link"
    if v.get("hardship") in ("possible", "clear"):
        return "hardship_plan_review"
    if v.get("promise_amount"):
        return "send_payment_link"
    return "call_back"


def narrative(f: dict) -> str:
    """One line in the gold label style, built only from the fields."""
    v = {k: (f[k]["value"] if isinstance(f.get(k), dict) else f.get(k)) for k in f}
    parts = []
    if v.get("hardship") == "clear":
        reason = PROTECTED_REASONS.get(v.get("hardship_reason"), v.get("hardship_reason"))
        parts.append(f"Customer reports {REASON_PHRASE.get(reason, 'financial hardship')}")
    elif v.get("hardship") == "possible":
        parts.append("Customer indicates finances are tight")
    if v.get("dispute"):
        parts.append(f"disputes {DISPUTE_PHRASE.get(v.get('dispute_type'), 'the balance')}")
    if v.get("promise_amount") and v.get("promise_date"):
        d = date.fromisoformat(str(v["promise_date"])[:10])
        parts.append(f"promised ${float(v['promise_amount']):,.0f} on {d.day} {d:%b}")
    if v.get("complaint"):
        parts.append("wants to complain")
    if v.get("insolvency_mention"):
        parts.append("mentions consumer proposal / bankruptcy")
    if v.get("cease_request"):
        parts.append("asked to stop contact")
    if not parts:
        parts.append("No commitment made")
    step = v.get("next_step") or next_step(f)
    parts.append(f"next step: {step.replace('_', ' ')}")
    return "; ".join(parts) + "."


def draft(transcript_id: str, con=None, replay_result: dict | None = None, call: dict | None = None) -> dict:
    """Draft summary for one call: fields with probability, evidence and gate, plus the narrative. Not saved.
    call: an already loaded (or, in governance tests, altered) call; loaded from the database when omitted."""
    con = con or db.connect()
    call = call or live_replay.load_call(transcript_id, con)
    text = "\n".join(f"{t['speaker'].capitalize()}: {t['text']}" for t in call["turns"] if t["text"].strip())
    a = extract(text, "transcript")
    pick = lambda q: a.get(q, {})  # noqa: E731
    f = {}
    for q in ("hardship", "dispute", "complaint", "cease_request", "insolvency_mention", "third_party_present"):
        f[q] = _field(pick(q).get("answer"), pick(q).get("prob"), pick(q).get("evidence"), "extract")
    reason = pick("hardship_reason").get("answer")
    if reason in PROTECTED_REASONS:
        reason = PROTECTED_REASONS[reason]
    f["hardship_reason"] = _field(reason if f["hardship"]["value"] in ("possible", "clear") else None,
                                  pick("hardship_reason").get("prob"), pick("hardship_reason").get("evidence"),
                                  "extract")
    f["dispute_type"] = _field(pick("dispute_type").get("answer") if f["dispute"]["value"] else None,
                               pick("dispute_type").get("prob"), pick("dispute_type").get("evidence"), "extract")
    s = pick("sentiment")
    f["sentiment"] = _field(s.get("answer"), s.get("prob"), s.get("evidence"), "extract")
    f["sentiment"]["gate"] = gate(s.get("prob"), "sentiment")      # tone for context: never sent to review

    # Promise: the replay's read-back is the most reliable source; then the whole-call answers.
    result = replay_result or live_replay.replay(transcript_id, call=call)
    heard = [st["promise"] for st in result["steps"] if st["promise"]]
    if heard and heard[-1].get("amount") and heard[-1].get("due_date"):
        p = heard[-1]
        f["promise_amount"] = _field(p["amount"], p.get("prob") or 0.9, p.get("evidence"), "live replay")
        f["promise_date"] = _field(p["due_date"], p.get("prob") or 0.9, p.get("evidence"), "live replay")
    else:
        amt = live_replay.parse_amount(pick("promise_amount").get("answer"))
        due = live_replay.resolve_date(pick("promise_date").get("answer"), call["call_date"])
        f["promise_amount"] = _field(amt, pick("promise_amount").get("prob"), pick("promise_amount").get("evidence"),
                                     "extract")
        f["promise_date"] = _field(due, pick("promise_date").get("prob"), pick("promise_date").get("evidence"),
                                   "extract")
    f["hardship"]["gate"] = gate(f["hardship"]["prob"], "hardship")
    f["next_step"] = _field(next_step(f), None, "", "rules from the fields")
    f["next_step"]["gate"] = "confirm"
    return {"transcript_id": transcript_id, "case_id": call["case_id"],
            "golden_customer_id": call["golden_customer_id"], "call_ts": call["call_start_ts"],
            "fields": f, "narrative": narrative(f),
            "model_version": f"{SUMMARY_MODEL}|{pick('hardship').get('model_version', '')}"}


# ---------------------------------------------------------------------------------------------- saving and approval

def ensure_tables(con) -> None:
    con.execute(DDL)


def save_draft(d: dict, user: str, con=None) -> str:
    con = con or db.connect(read_only=False)
    ensure_tables(con)
    sid = str(uuid.uuid4())
    con.execute("""INSERT INTO layer4.summaries (summary_id, transcript_id, case_id, golden_customer_id, call_ts, status,
                   draft, narrative_draft, model_version, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)""",
                [sid, d["transcript_id"], d["case_id"], d["golden_customer_id"], d["call_ts"],
                 json.dumps(d["fields"], default=str), d["narrative"], d["model_version"], user, _now()])
    audit.log({"action": "summary_drafted", "user": user, "case_id": d["case_id"],
               "golden_customer_id": d["golden_customer_id"], "input_ref": f"transcript:{d['transcript_id']}",
               "output": {"summary_id": sid, "narrative": d["narrative"]}}, con=con)
    return sid


def _get(con, sid):
    cur = con.execute("SELECT * FROM layer4.summaries WHERE summary_id = ?", [sid])
    row = cur.fetchone()
    if row is None:
        raise LookupError(f"No summary {sid}")
    return dict(zip([c[0] for c in cur.description], row))


def approve(sid: str, final_values: dict, final_narrative: str, user: str, role: str = "agent", con=None,
            reason: str = "") -> dict:
    """Approve with the agent's edits. Returns the edits. Writes the approved fields to layer3.text_features.
    Changing a drafted field overrides the model, so it needs a reason (the narrative alone does not)."""
    con = con or db.connect(read_only=False)
    ensure_tables(con)
    row = _get(con, sid)
    if row["status"] != "draft":
        raise ValueError(f"Summary {sid} is already {row['status']}")
    drafted = json.loads(row["draft"])
    edits = {k: {"from": drafted[k]["value"], "to": v} for k, v in final_values.items()
             if k in drafted and str(drafted[k]["value"]) != str(v)}
    if edits and not (reason or "").strip():
        raise ValueError(f"Changing {', '.join(sorted(edits))} overrides the draft: give a reason")
    if final_narrative != row["narrative_draft"]:
        edits["narrative"] = {"from": row["narrative_draft"], "to": final_narrative}
    now = _now()
    con.execute("""UPDATE layer4.summaries SET status = 'approved', final = ?, narrative = ?, edits = ?,
                   approved_by = ?, approved_at = ? WHERE summary_id = ?""",
                [json.dumps(final_values, default=str), final_narrative, json.dumps(edits, default=str), user, now,
                 sid])
    write_back(row, final_values, final_narrative, user, con)
    audit.log({"action": "summary_approved", "user": user, "role": role, "case_id": row["case_id"],
               "golden_customer_id": row["golden_customer_id"], "input_ref": f"summary:{sid}", "approved_by": user,
               "output": {"edited_fields": sorted(edits)},
               "reason": (reason or "").strip() or ("approved with edits" if edits else "approved as drafted")},
              con=con)
    return edits


def reject(sid: str, reason: str, user: str, role: str = "agent", con=None) -> None:
    if not (reason or "").strip():
        raise ValueError("A rejection needs a reason")
    con = con or db.connect(read_only=False)
    ensure_tables(con)
    row = _get(con, sid)
    con.execute("UPDATE layer4.summaries SET status = 'rejected', reject_reason = ?, approved_by = ?, approved_at = ? "
                "WHERE summary_id = ?", [reason.strip(), user, _now(), sid])
    audit.log({"action": "summary_rejected", "user": user, "role": role, "case_id": row["case_id"],
               "golden_customer_id": row["golden_customer_id"], "input_ref": f"summary:{sid}",
               "reason": reason.strip()}, con=con)


WRITE_BACK = ("hardship", "hardship_reason", "promise_amount", "promise_date", "dispute", "dispute_type", "complaint",
              "cease_request", "insolvency_mention", "third_party_present", "sentiment", "next_step")


def write_back(row: dict, final_values: dict, final_narrative: str, user: str, con) -> None:
    """Append the approved fields to layer3.text_features in Layer 3's own row layout."""
    from layer3.features.batch_text import _flatten
    import pandas as pd
    answers = {q: {"answer": final_values.get(q), "prob": 1.0, "evidence": f"approved by {user}",
                   "model_version": "approved_summary"} for q in WRITE_BACK}
    answers["promise_mentioned"] = {"answer": bool(final_values.get("promise_amount")), "prob": 1.0,
                                    "evidence": f"approved by {user}", "model_version": "approved_summary"}
    meta = {"source_type": "approved_summary", "source_id": row["summary_id"],
            "golden_customer_id": row["golden_customer_id"], "case_id": row["case_id"], "event_ts": row["call_ts"],
            "text": final_narrative}
    flat = _flatten(meta, answers)
    # Known the day after the call, Layer 3's rule for every call it reads. The real clock time of approval would
    # sit after the data snapshot, so point-in-time features (and the next brief's risk score) would never see it.
    call_ts = pd.to_datetime(row["call_ts"]) if row.get("call_ts") is not None else None
    flat["available_at"] = (call_ts + pd.Timedelta(days=1)).to_pydatetime() if call_ts is not None else _now()
    frame = pd.DataFrame([flat])
    for col in frame.columns:  # one row of mixed answers: store as text so it fits any existing column type
        if col.endswith("__prob"):
            frame[col] = frame[col].astype(float)
        elif col not in ("event_ts", "extracted_at", "available_at"):
            frame[col] = frame[col].map(lambda x: None if x is None else str(x))
    con.register("l4_summary_frame", frame)
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS layer3")
        con.execute("CREATE TABLE IF NOT EXISTS layer3.text_features AS SELECT * FROM l4_summary_frame WHERE false")
        existing = {r[0] for r in con.execute("""SELECT column_name FROM information_schema.columns
                                                 WHERE table_schema = 'layer3' AND table_name = 'text_features'""")
                    .fetchall()}
        cols = [c for c in frame.columns if c in existing]
        quoted = ", ".join(f'"{c}"' for c in cols)
        con.execute(f"INSERT INTO layer3.text_features ({quoted}) SELECT {quoted} FROM l4_summary_frame")
    finally:
        con.unregister("l4_summary_frame")


def summaries_for(con, case_id: str | None = None, golden_customer_id: str | None = None, limit: int = 20):
    if not db.table_exists(con, "layer4", "summaries"):
        return []
    where, params = [], []
    if case_id:
        where.append("case_id = ?")
        params.append(case_id)
    if golden_customer_id:
        where.append("golden_customer_id = ?")
        params.append(golden_customer_id)
    cur = con.execute(f"""SELECT summary_id, transcript_id, case_id, status, narrative_draft, narrative, edits,
                                 created_at, approved_by, approved_at, reject_reason FROM layer4.summaries
                          {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY created_at DESC LIMIT ?""",
                      params + [limit])
    return [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]
