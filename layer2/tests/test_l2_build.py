"""layer2.build picks the best source available and reads Layer 1's own trap fixes when they exist."""
import duckdb

from layer2.build import ACCOUNT_MONTH_FROM_GOLD, SQL_DIR, substitutions


def _con():
    con = duckdb.connect()
    con.execute("CREATE SCHEMA bronze; CREATE SCHEMA gold; CREATE SCHEMA layer2; CREATE SCHEMA access_analyst")
    con.execute((SQL_DIR / "00_macros.sql").read_text())
    return con


def test_bronze_only():
    con = _con()
    con.execute("CREATE TABLE bronze.collections_cases (case_id VARCHAR, current_dpd VARCHAR)")
    con.execute("CREATE TABLE bronze.contact_history (contact_id VARCHAR)")
    con.execute("CREATE TABLE bronze.promises_to_pay (ptp_id VARCHAR, ptp_status VARCHAR)")
    s = substitutions(con)
    assert s["10_f_cases.sql"]["__CASES__"] == "bronze.collections_cases"
    assert s["10_f_cases.sql"]["__GOLDEN__"] == "NULL::VARCHAR"
    assert "bucket_from_dpd" in s["10_f_cases.sql"]["__CASE_BUCKET__"]
    assert "_replace" not in s


def test_prefers_analyst_view_and_layer1_fixes():
    con = _con()
    con.execute("CREATE TABLE gold.cases (case_id VARCHAR, golden_customer_id VARCHAR, current_dpd VARCHAR, "
                "current_bucket_recomputed VARCHAR)")
    con.execute("CREATE VIEW access_analyst.analyst_cases AS SELECT * FROM gold.cases")
    con.execute("CREATE TABLE gold.promises_to_pay (ptp_id VARCHAR, golden_customer_id VARCHAR, ptp_status VARCHAR, "
                "ptp_status_adjusted VARCHAR)")
    con.execute("CREATE TABLE gold.contact_timeline (contact_id VARCHAR, golden_customer_id VARCHAR)")
    con.execute("CREATE TABLE gold.account_monthly_snapshot (account_id VARCHAR, product_type VARCHAR, account_status VARCHAR, "
                "month_end_date DATE, month_end_date_format VARCHAR, dpd INTEGER, dpd_bucket VARCHAR, "
                "dpd_bucket_recomputed VARCHAR, write_off_flag BOOLEAN, balance DOUBLE)")
    con.execute("INSERT INTO gold.account_monthly_snapshot VALUES ('C-1', 'card', 'active', DATE '2026-03-31', "
                "'MM/DD/YYYY', 10, '1-30', '1-30', false, 5.0), ('C-2', 'card', 'active', DATE '2026-03-31', "
                "'DD/MM/YYYY', 40, '1-30', '31-60', false, 7.0)")
    s = substitutions(con)
    assert s["10_f_cases.sql"]["__CASES__"] == "access_analyst.analyst_cases"
    assert s["10_f_cases.sql"]["__CASE_BUCKET__"] == "c.current_bucket_recomputed"
    assert s["30_f_promises.sql"]["__ADJUSTED__"].startswith("nullif(trim(p.ptp_status_adjusted)")
    con.execute(s["_replace"]["50_f_account_month.sql"])
    rows = con.execute("SELECT account_id, month_end, month_end_lenient, bucket_from_dpd FROM layer2.f_account_month "
                       "ORDER BY 1").fetchall()
    assert rows[0][1] is None and str(rows[0][2]) == "2026-03-31"     # month-first: kept only in the lenient date
    assert str(rows[1][1]) == "2026-03-31" and rows[1][3] == "31-60"   # Layer 1's recomputed bucket
    assert "__SOURCE__" not in ACCOUNT_MONTH_FROM_GOLD.replace("__SOURCE__", "x")


def test_governance_check(tmp_path, monkeypatch):
    import pytest

    import layer2.build as b
    (tmp_path / "tags_v1.yml").write_text(
        "entries:\n"
        "- {table: customers, column: gender_code, tag: Protected attribute}\n"
        "- {table: geo_reference, column: province_code, tag: Direct PII}\n")
    monkeypatch.setattr(b, "TAGS_DIR", tmp_path)
    con = _con()
    con.execute("CREATE TABLE layer2.f_cases (case_id VARCHAR, province_code VARCHAR)")
    notes = b.governance_check(con)
    assert any("province_code" in n for n in notes)                  # listed for review, not blocked
    con.execute("CREATE TABLE layer2.bad (gender_code VARCHAR)")
    with pytest.raises(RuntimeError):
        b.governance_check(con)
