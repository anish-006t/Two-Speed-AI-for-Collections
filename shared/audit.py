"""Append-only audit log in `audit.events`: who did what to which case, with what model output and probability, and who
approved it. This module only ever inserts and reads rows.

    from shared import audit
    audit.log({"user": "madhur", "role": "agent", "action": "identity_verified", "case_id": "CS-2024-000123"})

Events carry IDs and decisions, never customer PII or protected attributes: keys that name them are refused, and
string values that look like an email address or phone number are refused too.
"""
import json
import re
import uuid
from datetime import datetime, timezone

from shared import config
from shared.db import connect

COLUMNS = ("event_id", "ts", "user", "role", "action", "case_id", "golden_customer_id", "input_ref", "output",
           "prob", "approved_by", "reason")
MAX_STR = 2000

# Key tokens that mean PII (team rule 3) or a protected attribute (team rule 2). Keys are split on non-letters.
BLOCKED_TOKENS = {
    "phone", "email", "dob", "birth", "address", "postal", "sin", "ssn", "passport", "licence", "license",
    "gender", "sex", "marital", "citizenship", "household", "newcomer", "accessibility", "disability",
    "vulnerability", "vulnerable", "accent", "age", "aml", "ethnicity", "religion",
}
BLOCKED_KEYS = {"name", "first_name", "last_name", "full_name", "customer_name", "given_name", "surname",
                "national_id", "national_id_hash", "card_number", "account_number", "phone_number_hash"}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")

DDL = """
CREATE SCHEMA IF NOT EXISTS audit;
CREATE TABLE IF NOT EXISTS audit.events (
    event_id VARCHAR PRIMARY KEY, ts TIMESTAMP NOT NULL, "user" VARCHAR NOT NULL, role VARCHAR,
    action VARCHAR NOT NULL, case_id VARCHAR, golden_customer_id VARCHAR, input_ref VARCHAR, output VARCHAR,
    prob DOUBLE, approved_by VARCHAR, reason VARCHAR
);
"""


class AuditRejected(ValueError):
    pass


FREE_TEXT = ("output", "reason")  # values scanned for contact details; ID columns are not (IDs can be 10 digits)


def check(obj, where: str = "event", scan_values: bool = False) -> None:
    """Raise AuditRejected if any key names PII or a protected attribute, or (in free text) a string looks like an
    email address or phone number."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k).lower()
            if key in BLOCKED_KEYS or BLOCKED_TOKENS & set(re.split(r"[^a-z]+", key)):
                raise AuditRejected(f"{where}.{k}: PII or protected attributes never go in the audit log")
            is_id = key == "id" or key.endswith("_id")  # nested IDs (random UUIDs can hold 10 digits in a row)
            check(v, f"{where}.{k}", (scan_values and not is_id) or (where == "event" and key in FREE_TEXT))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            check(v, f"{where}[{i}]", scan_values)
    elif scan_values and isinstance(obj, str) and (EMAIL.search(obj) or PHONE.search(obj)):
        raise AuditRejected(f"{where}: looks like an email address or phone number")


def _clip(value):
    if isinstance(value, str) and len(value) > MAX_STR:
        return value[:MAX_STR] + "…"
    return value


def ensure_table(con) -> None:
    con.execute(DDL)


def log(event: dict, con=None) -> str:
    """Append one event. Required: action. user defaults to DHANDHO_USER. Returns the event_id."""
    unknown = set(event) - set(COLUMNS)
    if unknown:
        raise AuditRejected(f"unknown audit fields {sorted(unknown)}; allowed: {COLUMNS}")
    if not event.get("action"):
        raise AuditRejected("an audit event needs an action")
    check(event)
    row = {c: event.get(c) for c in COLUMNS}
    row["event_id"] = row["event_id"] or str(uuid.uuid4())
    row["ts"] = row["ts"] or datetime.now(timezone.utc).replace(tzinfo=None)
    row["user"] = row["user"] or config.default_user()
    if row["output"] is not None and not isinstance(row["output"], str):
        row["output"] = json.dumps(row["output"], default=str, ensure_ascii=False)
    row = {c: _clip(v) for c, v in row.items()}
    con = con or connect(read_only=False)
    ensure_table(con)
    con.execute(f"INSERT INTO audit.events ({', '.join(chr(34) + c + chr(34) for c in COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in COLUMNS)})", [row[c] for c in COLUMNS])
    return row["event_id"]


def recent(n: int = 100, case_id: str | None = None, con=None) -> list[dict]:
    """Newest events first, optionally for one case."""
    con = con or connect(read_only=False)
    ensure_table(con)
    where, params = ("WHERE case_id = ?", [case_id]) if case_id else ("", [])
    cur = con.execute(f"SELECT * FROM audit.events {where} ORDER BY ts DESC LIMIT ?", params + [n])
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]
