"""Tiny layer3 history tables for tests, with the same columns layer3.features.history builds on real data."""

TEXT_COLUMNS = ("hardship VARCHAR, hardship_reason VARCHAR, stated_delay_reason VARCHAR, promise_mentioned BOOLEAN, "
                "promise_amount DOUBLE, promise_date VARCHAR, promise_intent_strength VARCHAR, dispute BOOLEAN, "
                "complaint BOOLEAN, cease_request BOOLEAN, sentiment DOUBLE")


def history_tables(con, accounts=()):
    """Create empty layer3 history tables; `accounts` = (golden_id, account_id, product, available_at, balance, dpd)."""
    con.execute("CREATE SCHEMA IF NOT EXISTS layer3")
    for schema in ("gold", "bronze"):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    con.execute("""CREATE TABLE IF NOT EXISTS gold.cases (case_id VARCHAR, golden_customer_id VARCHAR,
        case_open_date VARCHAR, hardship_flag VARCHAR)""")
    con.execute("CREATE TABLE IF NOT EXISTS gold.incident_effects (golden_customer_id VARCHAR, incident_id VARCHAR)")
    con.execute("CREATE TABLE IF NOT EXISTS bronze.ops_events (event_id VARCHAR, end_ts VARCHAR)")
    con.execute("""CREATE TABLE layer3.account_history (golden_customer_id VARCHAR, account_id VARCHAR,
        product VARCHAR, available_at TIMESTAMP, balance DOUBLE, credit_exposure DOUBLE, credit_limit DOUBLE,
        dpd INTEGER, priority INTEGER)""")
    for g, a, product, at, balance, dpd in accounts:
        con.execute("INSERT INTO layer3.account_history VALUES (?, ?, ?, ?, ?, ?, NULL, ?, 0)",
                    [g, a, product, at, balance, balance, dpd])
    con.execute("""CREATE TABLE layer3.salary_history (golden_customer_id VARCHAR, account_id VARCHAR,
        credit_month VARCHAR, last_credit_date DATE, credit_count INTEGER, is_payroll BOOLEAN, available_at TIMESTAMP)""")
    con.execute("CREATE TABLE layer3.bureau_history (golden_customer_id VARCHAR, available_at TIMESTAMP, risk_score DOUBLE)")
    con.execute("""CREATE TABLE layer3.contact_responses (golden_customer_id VARCHAR, channel VARCHAR,
        event_ts TIMESTAMP, available_at TIMESTAMP, response DOUBLE)""")
    con.execute(f"""CREATE TABLE layer3.text_features (golden_customer_id VARCHAR, source_type VARCHAR,
        source_id VARCHAR, case_id VARCHAR, event_ts TIMESTAMP, available_at TIMESTAMP, {TEXT_COLUMNS})""")
