"""The Ask Collections page end to end (Streamlit AppTest, rules only so it needs no API key).

Skipped when there is no Layer 2 database (lake/maple.duckdb or DHANDHO_DB) with schema layer2 built.
"""
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DB = Path(os.environ.get("DHANDHO_DB", REPO / "lake" / "maple.duckdb"))
if not DB.is_absolute():
    DB = REPO / DB


def _ready() -> bool:
    if not DB.exists():
        return False
    try:
        import duckdb
        con = duckdb.connect(str(DB), read_only=True)
        ok = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'layer2'").fetchone()[0] > 0
        con.close()
        return ok
    except Exception:  # noqa: BLE001 - locked or not built
        return False


pytestmark = pytest.mark.skipif(not _ready(), reason="no built Layer 2 database")


def _ask(at, question):
    at.text_input[0].set_value(question)
    [b for b in at.button if b.label == "Ask"][0].click().run()
    assert not at.exception, at.exception


def test_page(monkeypatch):
    monkeypatch.setenv("DHANDHO_DB", str(DB))
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(REPO / "app" / "pages" / "2_Ask_Collections.py"), default_timeout=180)
    at.run()
    assert not at.exception, at.exception
    at.sidebar.toggle[0].set_value(False)                       # rules only: no API calls in tests
    # the reference example: a ranked list with a headline, its SQL, data products and checked follow-ups
    _ask(at, "Which customers are most likely to break a promise to pay this week?")
    md = " ".join(m.value for m in at.markdown)
    assert "open promises most likely to break" in md and "at risk" in md and "Understood as:" in md
    assert len(at.dataframe) == 1 and "break_prob" in at.code[0].value
    assert any("model_scores" in c.value for c in at.caption)
    # a certified metric, then edit the breakdown and re-run (no model)
    _ask(at, "What is the average days past due of open cases by queue?")
    assert len(at.dataframe) == 1 and len(at.code) == 1
    [m for m in at.multiselect if m.label == "Break down by"][0].set_value(["current_bucket"])
    [b for b in at.button if b.label == "Re-run with these choices"][0].click().run()   # edited plan, no model
    assert not at.exception, at.exception
    assert list(at.dataframe[0].value.columns)[0] == "current_bucket"
    _ask(at, "Show the hardship notes for case CS-2026-817819")       # notes: header, cited notes table
    assert at.info and "notes for CS-2026-817819" in at.info[0].value and "note_id" in at.dataframe[0].value.columns
    _ask(at, "List the gender and marital status of customers who broke a promise last month.")
    assert at.error and at.error[0].value.startswith("Refuse")
    _ask(at, "How many call attempts can we make on a mid-stage account in a week?")
    assert at.success and "POL-COLL-001" in at.success[0].value
