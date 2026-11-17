import duckdb
import pandas as pd

from layer3.features import defs
from layer3.tests._history_fixture import history_tables


def _database():
    con = duckdb.connect(":memory:")
    con.execute("CREATE SCHEMA gold")
    con.execute("""CREATE TABLE gold.l3_customer_features (
        golden_customer_id VARCHAR, product_count INTEGER, total_credit_exposure DOUBLE,
        max_dpd_current INTEGER, current_bucket VARCHAR, hardship_flag BOOLEAN,
        incident_affected BOOLEAN, _loaded_at TIMESTAMP)""")
    con.execute("""INSERT INTO gold.l3_customer_features VALUES
        ('G-1', 1, 100, 5, 'early', false, false, '2026-01-01'),
        ('G-1', 2, 300, 20, 'mid', true, false, '2026-06-01')""")
    con.execute("""CREATE TABLE gold.promises_to_pay (ptp_id VARCHAR,
        golden_customer_id VARCHAR, ptp_created_ts TIMESTAMP, ptp_status_adjusted VARCHAR, ptp_amount DOUBLE,
        status_ts TIMESTAMP)""")
    con.execute("""INSERT INTO gold.promises_to_pay VALUES
        ('P-1', 'G-1', '2026-02-01', 'broken', 100, '2026-02-10'), ('P-2', 'G-1', '2026-07-01', 'kept', 200, '2026-07-10')""")
    # one card known from January, a loan only from June: product_count is 1 before June and 2 after
    history_tables(con, accounts=[("G-1", "C-1", "card", "2026-01-01", 100, 5),
                                  ("G-1", "L-1", "loan", "2026-06-01", 200, 20)])
    return con


def test_online_and_training_use_same_point_in_time_values(monkeypatch):
    con = _database()
    monkeypatch.setattr(defs, "connect", lambda **kwargs: con.cursor())
    online = defs.get_features("G-1", "2026-03-01")
    training = defs.build_training_frame(pd.DataFrame([{"golden_customer_id": "G-1", "as_of": "2026-03-01"}]))
    # identical values; a missing value is None online and NaN in the training frame (both mean "unknown")
    trained = {k: (None if pd.isna(v) else v) for k, v in training.iloc[0][list(online)].to_dict().items()}
    assert trained == online
    assert online["product_count"] == 1
    assert online["prior_resolved_ptp_count"] == 1
    assert online["prior_broken_ptp_count"] == 1
    assert online["prior_kept_ptp_rate"] == 0


def test_future_records_do_not_leak(monkeypatch):
    con = _database()
    monkeypatch.setattr(defs, "connect", lambda **kwargs: con.cursor())
    before = defs.get_features("G-1", "2026-03-01")
    after = defs.get_features("G-1", "2026-08-01")
    assert before["product_count"] == 1 and after["product_count"] == 2
    assert before["prior_resolved_ptp_count"] == 1 and after["prior_resolved_ptp_count"] == 2


def test_registry_contains_no_protected_or_proxy_fields():
    # whole words, as the registry's own validator does ("age" must not match "agent" or "message")
    import re
    words = set()
    for record in defs.registry_records():
        for field in ("name", "definition", "source", "prompt"):
            words |= set(re.split(r"[^a-z]+", str(record.get(field) or "").lower()))
    assert not words & set(defs.PROHIBITED_TOKENS)


def test_publish_registry_writes_every_definition():
    con = duckdb.connect(":memory:")
    assert defs.publish_registry(con) == len(defs.FEATURES)
    assert con.execute("SELECT count(*) FROM layer3.feature_registry").fetchone()[0] == len(defs.FEATURES)


def test_promise_history_excludes_the_scored_promise_and_unknown_outcomes(monkeypatch):
    con = _database()
    monkeypatch.setattr(defs, "connect", lambda **kwargs: con.cursor())
    at_creation = defs.get_features("G-1", "2026-02-01")        # scoring the 1 Feb promise itself
    before_outcome = defs.get_features("G-1", "2026-02-05")     # created, outcome not known until 10 Feb
    after_outcome = defs.get_features("G-1", "2026-02-11")
    assert at_creation["prior_resolved_ptp_count"] == 0 and at_creation["prior_avg_promised_amount"] is None
    assert before_outcome["prior_resolved_ptp_count"] == 0 and before_outcome["prior_broken_ptp_count"] == 0
    assert after_outcome["prior_broken_ptp_count"] == 1


def test_salary_and_bureau_history_respect_available_at(monkeypatch):
    con = _database()
    con.execute("""INSERT INTO layer3.salary_history VALUES
        ('G-1', 'CHQ-1', '2026-01', '2026-01-15', 1, true, '2026-02-01'),
        ('G-1', 'CHQ-1', '2026-02', '2026-02-15', 1, true, '2026-03-01')""")
    con.execute("""INSERT INTO layer3.bureau_history VALUES
        ('G-1', '2026-01-10', 700), ('G-1', '2026-04-10', 650)""")
    monkeypatch.setattr(defs, "connect", lambda **kwargs: con.cursor())
    march = defs.get_features("G-1", "2026-03-15")
    assert march["usual_payday"] == 15                      # both months known by mid-March
    assert march["bureau_score_change"] is None             # only one pull known yet
    may = defs.get_features("G-1", "2026-05-01")
    assert may["bureau_score_change"] == -50                # second pull known from 11 April


def test_hardship_and_incident_flags_are_point_in_time(monkeypatch):
    con = _database()
    con.execute("INSERT INTO gold.cases VALUES ('CS-1', 'G-1', '2026-05-01', 'true')")
    con.execute("INSERT INTO gold.incident_effects VALUES ('G-1', 'INC-0914')")
    con.execute("INSERT INTO bronze.ops_events VALUES ('INC-0914', '2026-09-14 15:40:00')")
    monkeypatch.setattr(defs, "connect", lambda **kwargs: con.cursor())
    before, after = defs.get_features("G-1", "2026-04-01"), defs.get_features("G-1", "2026-09-28")
    assert not before["hardship_flag"] and after["hardship_flag"]
    assert not before["incident_affected"] and after["incident_affected"]
