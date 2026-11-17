"""shared/db.py and shared/audit.py on a temporary database."""
from pathlib import Path

import pytest

from shared import audit, db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    path = tmp_path / "t.duckdb"
    monkeypatch.setenv("DHANDHO_DB", str(path))
    db.close_all()
    yield path
    db.close_all()


def test_settings_applied(tmp_db):
    con = db.connect(read_only=False)
    assert con.execute("SELECT current_setting('threads')").fetchone()[0] == 8
    assert db.db_path() == tmp_db


def test_read_only_after_read_write_shares_the_connection(tmp_db):
    rw = db.connect(read_only=False)
    rw.execute("CREATE TABLE t AS SELECT 1 AS x")
    ro = db.connect()  # would raise in DuckDB if opened as a second, read-only connection
    assert ro.execute("SELECT x FROM t").fetchone()[0] == 1
    ro.close()
    assert rw.execute("SELECT count(*) FROM t").fetchone()[0] == 1  # closing a cursor keeps the connection
    assert db.table_exists(rw, "main", "t") and not db.table_exists(rw, "main", "nope")


def test_read_write_after_read_only_fails_clearly(tmp_db):
    db.connect(read_only=False).execute("CREATE TABLE t AS SELECT 1 AS x")
    db.close_all()
    db.connect()
    with pytest.raises(RuntimeError, match="read-only"):
        db.connect(read_only=False)


def test_missing_file_read_only_says_how_to_build(tmp_db):
    with pytest.raises(FileNotFoundError, match="Build Layer 1"):
        db.connect()


def test_audit_appends_and_fills_defaults(tmp_db, monkeypatch):
    monkeypatch.setenv("DHANDHO_USER", "tester")
    first = audit.log({"action": "identity_verified", "role": "agent", "case_id": "CS-1"})
    audit.log({"action": "summary_approved", "case_id": "CS-1", "prob": 0.93, "approved_by": "lead",
               "output": {"hardship": "clear", "reasons": ["job loss"]}})
    audit.log({"action": "other", "case_id": "CS-2"})
    rows = audit.recent(case_id="CS-1")
    assert [r["action"] for r in rows] == ["summary_approved", "identity_verified"]
    assert rows[1]["event_id"] == first and rows[1]["user"] == "tester" and rows[1]["ts"] is not None
    assert '"hardship": "clear"' in rows[0]["output"]


@pytest.mark.parametrize("event", [
    {"action": "x", "output": {"customer_name": "A"}},
    {"action": "x", "output": {"details": {"gender": "F"}}},
    {"action": "x", "output": {"age_band": "30-39"}},
    {"action": "x", "output": [{"vulnerability_flag": True}]},
    {"action": "x", "reason": "call back on 416-555-0199"},
    {"action": "x", "output": {"note": "mail me at a.b@example.com"}},
    {"action": "x", "phone": "1"},
    {"action": "x", "not_a_column": 1},
    {"role": "agent"},
])
def test_audit_rejects_pii_protected_and_unknown_fields(tmp_db, event):
    with pytest.raises(audit.AuditRejected):
        audit.log(event)


def test_ids_that_look_numeric_are_allowed(tmp_db):
    audit.log({"action": "x", "golden_customer_id": "4165550199", "case_id": "CS-2022-821260"})


def test_nested_ids_in_free_text_are_not_read_as_phone_numbers(tmp_db):
    """A random UUID can hold 10 digits in a row; it is an ID, not a phone number (this made two tests flaky)."""
    audit.log({"action": "x", "output": {"summary_id": "6a1b2c3d-0f00-4e5a-9b7c-416555019901", "item_id": "4165550199"}})
    with pytest.raises(audit.AuditRejected):
        audit.log({"action": "x", "output": {"summary_id": "s1", "summary": "call back on 416-555-0199"}})


def test_audit_module_never_updates_or_deletes():
    src = (Path(audit.__file__)).read_text().upper()
    assert "UPDATE " not in src and "DELETE " not in src and "DROP " not in src
