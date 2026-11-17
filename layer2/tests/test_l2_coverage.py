"""Coverage check (never silently wrong) and the LLM extension of rule-built SQL, with fake model output."""
from pathlib import Path

import pytest

from layer2.parse import parse
from layer2.semantic.coverage import gaps, operations

DB = Path(__file__).resolve().parents[2] / "lake" / "maple.duckdb"
needs_db = pytest.mark.skipif(not DB.exists(), reason="needs the built database")


@pytest.mark.parametrize("q, op", [
    ("How many open cases have more than 5 contact attempts?", "threshold"),
    ("accounts 90+ days past due", "threshold"),
    ("Which 3 queues have the most cases?", "top_n"),
    ("List the top 10 cases by total overdue", "list_records"),
    ("How many distinct customers have a broken promise?", "distinct"),
    ("Average contact cost per case by queue", "per_entity"),
    ("Promise kept rate excluding vendor sites", "exclusion"),
    ("Growth in disputes from August to September 2026", "change"),
    ("Average DPD this month versus last month", "compare"),
])
def test_operations_found(q, op):
    assert op in operations(q, periods=0)


def test_comparison_words_need_two_periods():
    # "champion versus challenger" is a breakdown, not a change between periods
    assert "compare" not in operations("Compare the kept rate of champion versus challenger_A", periods=0)
    assert "change" not in operations("Did the kept rate drop by queue in September 2026?", periods=1)


def test_numbers_inside_periods_are_not_operations():
    assert operations("Broken promise rate over the last 3 months by queue", periods=1) == {}
    assert operations("Cure rate within 30 days for cases opened in July 2026", periods=1) == {}


def test_definition_covers_its_own_operations():
    d = "delinquency rate 100.0 * avg(CASE WHEN dpd >= 1 THEN 1 ELSE 0 END)"
    assert gaps("Share of active card accounts 1 or more days past due", ["delinquency", "card", "active"],
                definition=d, data_words=set()) == []
    assert gaps("Contact attempts per case by queue", ["attempts"], definition="attempts per case",
                data_words=set()) == []


def test_only_data_words_and_proper_nouns_are_leftovers():
    data = {"hardship", "flag", "queue", "province"}
    out = gaps("How many disputes were raised with a hardship flag in Ontario?", ["dispute"], data_words=data)
    assert out and "hardship" in out[-1] and "flag" in out[-1] and "ontario" in out[-1] and "raised" not in out[-1]


# ---------------------------------------------------------------------------------------------- on the database

def _con():
    import duckdb
    try:
        return duckdb.connect(str(DB), read_only=True)
    except duckdb.Error:
        pytest.skip("database locked")


@needs_db
@pytest.mark.parametrize("q, sql_bits", [
    ("Which 5 queues have the highest total overdue?", ["GROUP BY 1", "DESC", "LIMIT 5"]),
    ("average balance of accounts that are more than 90 days past due", ["avg(balance)", "dpd > 90"]),
    ("How many distinct customers have an open case?", ["count(DISTINCT golden_customer_id)", "is_open"]),
])
def test_generic_operations(q, sql_bits):
    from layer2.semantic import generic
    gp = generic.build(parse(q, "2026-09-28"), _con())
    sql = generic.to_sql(gp)
    for bit in sql_bits:
        assert bit in sql


@needs_db
def test_without_a_model_the_gap_is_stated():
    from layer2.semantic import answer_metric
    out = answer_metric(parse("How many open cases have more than 5 contact attempts?", "2026-09-28"),
                        use_model=False)
    assert "Not applied: a threshold (more than 5)" in out["answer"]


@needs_db
@pytest.mark.parametrize("verdict, expect", [("answer", "extended by the AI"), ("already_correct", None),
                                             ("cannot", "Not applied")])
def test_llm_extends_or_confirms(monkeypatch, verdict, expect):
    from layer2 import adapters
    from layer2.semantic import answer_metric
    sql = ("SELECT count(*) AS cases FROM layer2.f_cases WHERE is_open AND contact_attempts_total > 5"
           if verdict == "answer" else "")
    monkeypatch.setattr(adapters, "llm_available", lambda: True)
    monkeypatch.setattr(adapters, "complete", lambda *a, **k: {"verdict": verdict, "sql": sql, "explanation": "x"})
    out = answer_metric(parse("How many open cases have more than 5 contact attempts?", "2026-09-28"))
    if expect:
        assert expect in out["answer"]
    else:
        assert "Not applied" not in out["answer"] and out["engines"]["coverage"].startswith("llm checked")
    if verdict == "answer":
        assert "contact_attempts_total > 5" in out["sql_or_sources"]


@needs_db
def test_unsafe_llm_sql_is_retried_then_dropped(monkeypatch):
    from layer2 import adapters, freeform
    replies = iter([{"verdict": "answer", "sql": "DELETE FROM layer2.f_cases", "explanation": ""},
                    {"verdict": "answer", "sql": "SELECT full_name FROM bronze.crm_customers", "explanation": ""}])
    monkeypatch.setattr(adapters, "complete", lambda *a, **k: next(replies))
    r = freeform.run(parse("Ratio of SMS to calls by queue", "2026-09-28"), _con())
    assert r["status"] == "failed"


@needs_db
def test_no_false_alarms_on_known_questions():
    """Questions the rules answer fully must not be flagged (each flag costs an LLM call or adds a wrong note)."""
    import csv

    import yaml

    from layer2.ask import ask
    release = Path(__file__).resolve().parents[2] / "data" / "raw" / "maple_collections_release" / "data"
    qs = [c["q"] for c in yaml.safe_load((Path(__file__).parent / "questions_own.yaml").read_text())]
    if (release / "benchmark_questions.csv").exists():
        qs += [r["question_text"] for r in csv.DictReader((release / "benchmark_questions.csv").open())]
    flagged = []
    for q in qs:
        out = ask(q, role="analyst", as_of="2026-09-28", use_model=False)
        if out["route"] == "metric_sql" and "Not applied" in out["answer"]:
            flagged.append(q)
    assert flagged == []


@pytest.mark.parametrize("q, labels", [
    ("broken promise rate for promises due in the first half of 2026 (Jan-Jun)", ["H1 2026"]),
    ("cost per contact over the second quarter of 2026 (April to June)", ["Q2 2026"]),
    ("Q2 2026 (promises due 1 April to 30 June)", ["2026-04-01 to 2026-06-30"]),
    ("charge-off date in calendar year 2025", ["2025"]),
    ("biggest drop between July and August 2026", ["August 2026", "July 2026"]),
    ("disputes between 1 July and 28 September 2026", ["2026-07-01 to 2026-09-28"]),
    ("How many promises may we take in September 2026", ["September 2026"]),
])
def test_periods_restated_or_without_year(q, labels):
    assert [x.label for x in parse(q, "2026-09-28").periods] == labels


def test_this_week_includes_the_coming_days():
    per = parse("promises due this week", "2026-09-28").periods[0]       # 28 Sep 2026 is a Monday
    assert (per.start.isoformat(), per.end.isoformat()) == ("2026-09-28", "2026-10-04")


@needs_db
@pytest.mark.parametrize("q, bits", [
    ("Which customers are most likely to break a promise to pay this week?",
     ["golden_customer_id", "break_prob", "ptp_status = 'open'", "ORDER BY break_prob DESC", "LIMIT 10"]),
    ("List the top 10 cases by total overdue", ["case_id", "ORDER BY total_overdue DESC", "LIMIT 10"]),
])
def test_ranked_lists_by_rules(q, bits):
    from layer2.ask import ask
    out = ask(q, use_model=False)
    assert out["route"] == "metric_sql" and "Not applied" not in out["answer"]
    for b in bits:
        assert b in out["sql_or_sources"]
    assert not any(c in out["sql_or_sources"] for c in ("first_name", "last_name", "phone", "email"))


@needs_db
def test_risk_card_explains_with_the_models_reasons():
    from layer2.ask import ask
    from layer2.present import checked_follow_ups
    q = "Which customers are most likely to break a promise to pay this week?"
    ups = checked_follow_ups(ask(q, use_model=False), q)
    assert ups and ups[0].startswith("Why is GC")
    out = ask(ups[0], use_model=False)
    assert out["route"] == "lookup" and "break probability" in out["answer"] and "protected" in out["answer"]


def test_improvement_between_two_months_is_a_change():
    q = "Which 3 agent sites improved their right-party contact rate the most from August to September 2026?"
    assert "change" in operations(q, periods=1)        # one parsed range, but two months named
