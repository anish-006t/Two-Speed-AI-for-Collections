"""The query checker: status of each question (rules only, no API calls) and its page (Streamlit AppTest)."""
from pathlib import Path

import pytest

from layer2.checker import classify

DB = Path(__file__).resolve().parents[2] / "lake" / "maple.duckdb"


@pytest.mark.parametrize("out, status", [
    ({"refused": True, "answer": "Refuse: protected ground."}, "refused"),
    ({"refused": False, "answer": "x\n\nNote: Not applied: a threshold (more than 5).", "engines": {}}, "partial"),
    ({"refused": False, "answer": "a,b\n1,2", "engines": {"extended": "llm-sql"}}, "ai_extended"),
    ({"refused": False, "answer": "a\n1", "engines": {"lookup": "llm-sql"}}, "ai_written"),
    ({"refused": False, "answer": "This question does not match any certified metric or rule-built aggregate"},
     "cannot"),
    ({"refused": False, "answer": "a\n1", "engines": {"metric": "rules (general aggregate)"}}, "aggregate"),
    ({"refused": False, "answer": "Per POL-COLL-001 §5.2 …", "engines": {"policy": "llm"}}, "ai_text"),
    ({"refused": False, "answer": "a\n1", "engines": {"metric": "rules"}}, "rules"),
])
def test_classify(out, status):
    assert classify(out) == status


@pytest.mark.skipif(not DB.exists(), reason="needs the built database")
def test_checker_page_rules_only():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "checker_app.py"), default_timeout=120)
    at.run()
    at.text_area[0].set_value("How many open cases have more than 5 contact attempts?\n"
                              "Which 5 queues have the highest total overdue?\n"
                              "Which FSAs have the worst broken promise rates?")
    at.button[0].click()
    at.run()
    assert not at.exception
    statuses = list(at.dataframe[0].value["Status"])
    assert statuses == ["⚠️ Partly answered", "✅ Rule-built aggregate", "🛑 Refused"]
