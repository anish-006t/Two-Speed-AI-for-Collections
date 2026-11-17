"""Review queue: low-confidence items are queued once, decisions are logged, overrides need a reason."""
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from layer4 import review
from shared import db

CALL = {"transcript_id": "T-1", "case_id": "CS-1", "golden_customer_id": "G-1"}


def prompt(rule, gate, prob):
    return {"rule": rule, "gate": gate, "prob": prob, "answer": True, "evidence": "my son is here",
            "title": rule, "model_version": "m"}


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setenv("DHANDHO_DB", str(tmp_path / "r.duckdb"))
    db.close_all()
    c = db.connect(read_only=False)
    yield c
    db.close_all()


def test_only_review_gated_items_are_queued_and_only_once(con):
    steps = [{"prompts": [prompt("R-THIRD-PARTY", "review", 0.55), prompt("R-DISPUTE", "confirm", 0.8)],
              "promise": {"gate": "review", "prob": 0.5, "amount": 100.0, "due_date": "2026-09-20", "evidence": "x"}}]
    assert review.queue_from_replay(con, CALL, steps) == 2
    assert review.queue_from_replay(con, CALL, steps) == 0
    assert sorted(i["subject"] for i in review.queue(con)) == ["R-THIRD-PARTY", "promise"]


def test_accept_and_override_are_logged_and_override_needs_a_reason(con):
    review.queue_from_replay(con, CALL, [{"prompts": [prompt("R-THIRD-PARTY", "review", 0.55)], "promise": None}])
    item = review.queue(con)[0]["item_id"]
    with pytest.raises(ValueError, match="reason"):
        review.decide(con, item, "override", "lead-1", final_value=False)
    review.decide(con, item, "override", "lead-1", final_value=False, reason="TV in the background, no third party")
    row = review.queue(con, "overridden")[0]
    assert row["decided_by"] == "lead-1" and json.loads(row["final_value"]) is False
    with pytest.raises(ValueError, match="already"):
        review.decide(con, item, "accept", "lead-1")
    actions = [r[0] for r in con.execute("SELECT action FROM audit.events ORDER BY ts").fetchall()]
    assert actions == ["review_queued", "review_override"]
    assert review.stats(con)["overridden"] == 1


def test_summary_fields_below_threshold_are_queued(con):
    draft = {"case_id": "CS-1", "golden_customer_id": "G-1", "model_version": "m",
             "fields": {"dispute": {"value": True, "prob": 0.4, "gate": "review", "evidence": "not my fee"},
                        "complaint": {"value": False, "prob": 0.3, "gate": "review"},
                        "hardship": {"value": "clear", "prob": 0.95, "gate": "confirm"}}}
    assert review.queue_from_summary(con, "S-1", draft) == 1
    assert review.queue(con)[0]["subject"] == "dispute"


def stat(at, title: str) -> str:
    """The value on a stat card (app.shell.stat_cards renders them as HTML)."""
    import re
    page = " ".join(e.proto.body for e in at.get("html"))
    m = re.search(rf'ds-stat-title">{re.escape(title)}</span>.*?ds-stat-value">([^<]*)<', page)
    return m.group(1) if m else ""


def test_team_lead_page_shows_the_queue_and_accepts(con):
    review.queue_from_replay(con, CALL, [{"prompts": [prompt("R-THIRD-PARTY", "review", 0.55)], "promise": None}])
    db.close_all()
    page = str(Path(__file__).resolve().parents[2] / "app" / "pages" / "5_Review_and_Governance.py")
    at = AppTest.from_file(page, default_timeout=60)
    at.session_state["role"] = "team_lead"
    at.run()
    assert not at.exception
    assert stat(at, "Waiting for review") == "1"
    next(b for b in at.button if b.label == "Accept").click().run()
    assert not at.exception
    assert stat(at, "Accepted") == "1"
    db.close_all()


def test_summaries_tab_shows_drafts_and_rejections_without_edits(con):
    """A draft or rejected summary has no edits (NULL); the tab must still render (it crashed on NaN)."""
    from layer4 import postcall
    postcall.ensure_tables(con)
    con.execute("""INSERT INTO layer4.summaries (summary_id, case_id, status, narrative_draft, edits, created_at)
                   VALUES ('s1', 'CS-1', 'rejected', 'No commitment made; next step: call back.', NULL, now()),
                          ('s2', 'CS-1', 'approved', 'Customer reports job loss; next step: hardship plan review.',
                           '{"hardship": {"from": "possible", "to": "clear"}}', now())""")
    db.close_all()
    page = str(Path(__file__).resolve().parents[2] / "app" / "pages" / "5_Review_and_Governance.py")
    at = AppTest.from_file(page, default_timeout=60)
    at.run()
    assert not at.exception
    table = next(d.value for d in at.dataframe if "edited" in d.value.columns)
    assert sorted(table["edited"]) == ["", "hardship"]
    db.close_all()
