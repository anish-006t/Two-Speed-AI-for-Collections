"""Rules for the extra benchmark questions (4 Oct, 19:00 release), checked without a model.

Each one was answered wrongly by the rules before: the date column of a range ("cured between", "due between"), a
plain aggregate instead of an unrelated certified metric ("total overdue on open cases" is not "amount collected"),
and the share of payment links clicked."""
from pathlib import Path

import pytest

from layer2.semantic import time_key_for

DB = Path(__file__).resolve().parents[2] / "lake" / "maple.duckdb"
needs_db = pytest.mark.skipif(not DB.exists(), reason="needs the built database")

PROMISES = {"time_cues": {"paid": ["paid"], "created": ["made", "taken", "created"], "due": ["due"]}}
CASES = {"time_cues": {"opened": ["opened", "open in", "new cases"], "cured": ["cured in", "cured"]}}


@pytest.mark.parametrize("q,view,key", [
    ("promise-kept rate for promises due between 1 and 20 september 2026, by the channel the promise was taken on",
     PROMISES, "due"),
    ("promises created in september 2026", PROMISES, "created"),
    ("for cases that cured between 1 july and 28 september 2026, average days to cure", CASES, "cured"),
    ("cases opened in july 2026 that cured", CASES, "opened"),
    ("promise-kept rate by queue", PROMISES, None),
])
def test_the_cue_before_a_range_names_the_date_column(q, view, key):
    assert time_key_for(q, view) == key


@needs_db
@pytest.mark.parametrize("q,sql_bits", [
    ("What is the total amount overdue on open collections cases, by primary product?",
     ["sum(total_overdue)", "is_open", "primary_product"]),
    ("In September 2026, what share of payment links sent by SMS were clicked?",
     ["link_clicked", "payment_link", "channel = 'sms'"]),
    ("How many open collections cases have a hardship flag, by queue?", ["count(*)", "is_open", "hardship_flag"]),
    ("For cases that cured between 1 July and 28 September 2026, what was the average number of days to cure, "
     "by primary product?", ["avg(days_to_cure)", "cure_date >= DATE '2026-07-01'"]),
    ("What was the promise-kept rate for promises due between 1 and 20 September 2026, by the channel the promise "
     "was taken on?", ["ptp_due_date >= DATE '2026-09-01'", "ptp_channel"]),
    ("How many open cases are on insolvency hold, and how much is overdue on them?",
     ["sum(total_overdue)", "insolvency_hold", "count(*)"]),
])
def test_extra_questions_by_rules(q, sql_bits):
    from layer2.ask import ask
    out = ask(q, role="analyst", as_of="2026-09-28", use_model=False)
    assert not out["refused"] and out["route"] == "metric_sql"
    for bit in sql_bits:
        assert bit in out["sql_or_sources"], (bit, out["sql_or_sources"])
