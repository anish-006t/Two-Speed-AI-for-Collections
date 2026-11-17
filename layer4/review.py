"""Human review (L4-6): the review queue for low-confidence model output, and logged overrides.

Gates (layer4/gates.py): 0.9 or more is a suggestion, 0.75 to 0.9 needs the agent's confirmation, below 0.75 comes
here. Hardship always needs confirmation. Nothing reaches a customer automatically. An override needs a reason, and
every decision is written to audit.events.
"""
import json
import uuid
from datetime import datetime, timezone

from layer4.gates import CONFIRM_AT
from shared import audit, db

DDL = """
CREATE SCHEMA IF NOT EXISTS layer4;
CREATE TABLE IF NOT EXISTS layer4.review_queue (
    item_id VARCHAR PRIMARY KEY, created_at TIMESTAMP, kind VARCHAR, case_id VARCHAR, golden_customer_id VARCHAR,
    source_ref VARCHAR, subject VARCHAR, answer VARCHAR, prob DOUBLE, evidence VARCHAR, why VARCHAR,
    model_version VARCHAR, status VARCHAR NOT NULL, decided_by VARCHAR, decided_at TIMESTAMP, decision VARCHAR,
    final_value VARCHAR, reason VARCHAR
);
"""
KINDS = ("live_prompt", "promise", "summary_field")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ensure_table(con) -> None:
    con.execute(DDL)


def enqueue(con, kind: str, case_id, golden_customer_id, source_ref: str, subject: str, answer, prob, evidence="",
            why="", model_version="") -> str | None:
    """Add one item; returns its ID, or None if the same subject for the same source is already queued."""
    ensure_table(con)
    if con.execute("SELECT 1 FROM layer4.review_queue WHERE source_ref = ? AND subject = ?",
                   [source_ref, subject]).fetchone():
        return None
    item = str(uuid.uuid4())
    con.execute("""INSERT INTO layer4.review_queue (item_id, created_at, kind, case_id, golden_customer_id, source_ref,
                   subject, answer, prob, evidence, why, model_version, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')""",
                [item, _now(), kind, case_id, golden_customer_id, source_ref, subject,
                 json.dumps(answer, default=str), prob, (evidence or "")[:500], why, model_version])
    audit.log({"action": "review_queued", "user": "system", "role": "system", "case_id": case_id,
               "golden_customer_id": golden_customer_id, "input_ref": source_ref, "prob": prob,
               "output": {"item_id": item, "subject": subject}, "reason": why}, con=con)
    return item


def decide(con, item_id: str, decision: str, user: str, role: str = "team_lead", final_value=None,
           reason: str = "") -> None:
    """decision: 'accept' (the model was right) or 'override' (needs final_value and a reason)."""
    if decision not in ("accept", "override"):
        raise ValueError("decision must be accept or override")
    if decision == "override" and not (reason or "").strip():
        raise ValueError("An override needs a reason")
    ensure_table(con)
    row = con.execute("SELECT status, case_id, golden_customer_id, source_ref, subject, answer, prob "
                      "FROM layer4.review_queue WHERE item_id = ?", [item_id]).fetchone()
    if row is None:
        raise LookupError(f"No review item {item_id}")
    if row[0] != "open":
        raise ValueError(f"Item {item_id} is already {row[0]}")
    final = row[5] if decision == "accept" else json.dumps(final_value, default=str)
    con.execute("""UPDATE layer4.review_queue SET status = ?, decided_by = ?, decided_at = ?, decision = ?,
                   final_value = ?, reason = ? WHERE item_id = ?""",
                ["accepted" if decision == "accept" else "overridden", user, _now(), decision, final,
                 (reason or "").strip() or None, item_id])
    audit.log({"action": f"review_{decision}", "user": user, "role": role, "case_id": row[1],
               "golden_customer_id": row[2], "input_ref": row[3], "prob": row[6], "approved_by": user,
               "output": {"item_id": item_id, "subject": row[4], "model_answer": json.loads(row[5]),
                          "final": json.loads(final)},
               "reason": (reason or "").strip() or "accepted as the model suggested"}, con=con)


def queue(con, status: str = "open", limit: int = 200) -> list[dict]:
    if not db.table_exists(con, "layer4", "review_queue"):
        return []
    cur = con.execute("SELECT * FROM layer4.review_queue WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                      [status, limit]) if status != "all" else \
        con.execute("SELECT * FROM layer4.review_queue ORDER BY created_at DESC LIMIT ?", [limit])
    names = [c[0] for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def stats(con) -> dict:
    out = {"open": 0, "accepted": 0, "overridden": 0}
    if db.table_exists(con, "layer4", "review_queue"):
        out.update(dict(con.execute("SELECT status, count(*) FROM layer4.review_queue GROUP BY 1").fetchall()))
    if db.table_exists(con, "layer4", "summaries"):
        out["summaries"] = dict(con.execute("SELECT status, count(*) FROM layer4.summaries GROUP BY 1").fetchall())
    return out


def queue_from_replay(con, call: dict, steps: list[dict]) -> int:
    """Queue every live prompt or promise below the confirm threshold. Returns how many were added."""
    added = 0
    ref = f"transcript:{call['transcript_id']}"
    for s in steps:
        for p in s["prompts"]:
            if p["gate"] == "review":
                added += bool(enqueue(con, "live_prompt", call["case_id"], call["golden_customer_id"], ref, p["rule"],
                                      p["answer"], p["prob"], p["evidence"],
                                      f"{p['title']}: confidence below {CONFIRM_AT}", p.get("model_version") or ""))
        pr = s.get("promise")
        if pr and pr.get("gate") == "review":
            added += bool(enqueue(con, "promise", call["case_id"], call["golden_customer_id"], ref, "promise",
                                  {"amount": pr.get("amount"), "due_date": pr.get("due_date")}, pr.get("prob"),
                                  pr.get("evidence"), f"promise heard with confidence below {CONFIRM_AT}",
                                  pr.get("model_version") or ""))
    return added


def queue_from_summary(con, summary_id: str, draft: dict) -> int:
    added = 0
    for name, f in draft["fields"].items():
        if f.get("gate") == "review" and f.get("value") not in (None, False, "none"):
            added += bool(enqueue(con, "summary_field", draft["case_id"], draft["golden_customer_id"],
                                  f"summary:{summary_id}", name, f["value"], f.get("prob"), f.get("evidence"),
                                  f"summary field drafted with confidence below {CONFIRM_AT}", draft.get("model_version", "")))
    return added
