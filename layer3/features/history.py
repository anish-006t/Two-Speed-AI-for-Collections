"""Prepare the dated history tables the feature definitions read; only schema layer3 is written.

Every row carries `available_at`, the moment the fact became known, so a feature computed "as of" a date never sees
later data:
  account_history     quarter-end snapshots (known the day after month end) and the current account row
  salary_history      monthly salary credits per deposit account, from bronze (known after the month closes)
  bureau_history      bureau scores per pull, from bronze (known the day after the pull)
  contact_responses   per-contact response by channel (known the day after the contact)
Golden IDs come from Layer 1: deposit accounts through gold.c360_accounts (falling back to the CRM key), bureau pulls
through the BUREAU key in identity.crosswalk.
"""
from layer3.features.batch_text import ensure_table
from layer3.features.defs import publish_registry

HISTORY_TABLES = ("account_history", "salary_history", "bureau_history", "contact_responses")


def prepare_history(con):
    con.execute("CREATE SCHEMA IF NOT EXISTS layer3")
    con.execute("""CREATE OR REPLACE TABLE layer3.account_history AS
        SELECT s.golden_customer_id,s.account_id,a.product,
               cast(s.month_end_date AS TIMESTAMP)+INTERVAL 1 DAY available_at,
               s.balance,CASE WHEN a.product='deposit' THEN NULL ELSE s.balance END credit_exposure,
               CASE WHEN a.product='card' THEN s.limit_or_original_amount END credit_limit,s.dpd,0 priority
        FROM gold.account_monthly_snapshot s JOIN gold.c360_accounts a USING(account_id)
        WHERE s.golden_customer_id IS NOT NULL AND s.month_end_date IS NOT NULL
        UNION ALL
        SELECT golden_customer_id,account_id,product,cast(snapshot_date AS TIMESTAMP)+INTERVAL 1 DAY,
               balance,credit_exposure,credit_limit,dpd,1
        FROM gold.c360_accounts WHERE golden_customer_id IS NOT NULL AND snapshot_date IS NOT NULL""")
    con.execute("CREATE INDEX IF NOT EXISTS account_lookup ON layer3.account_history(golden_customer_id,available_at)")
    con.execute("""CREATE OR REPLACE TABLE layer3.salary_history AS
        SELECT coalesce(a.golden_customer_id, x.golden_customer_id) golden_customer_id,
               s.deposit_account_id account_id, s.credit_month,
               try_cast(s.last_credit_date AS DATE) last_credit_date,
               try_cast(s.credit_count AS INTEGER) credit_count,
               coalesce(s.employer_flag = 'Y', false) is_payroll,
               cast(last_day(try_cast(s.credit_month || '-01' AS DATE)) AS TIMESTAMP) + INTERVAL 1 DAY available_at
        FROM bronze.salary_credit_history s
        LEFT JOIN gold.c360_accounts a ON a.account_id = s.deposit_account_id
        LEFT JOIN identity.crosswalk x ON x.source_key = 'CRM:' || s.crm_customer_id AND x.valid_to IS NULL
        WHERE coalesce(a.golden_customer_id, x.golden_customer_id) IS NOT NULL
          AND try_cast(s.credit_month || '-01' AS DATE) IS NOT NULL""")
    con.execute("""CREATE OR REPLACE TABLE layer3.bureau_history AS
        SELECT x.golden_customer_id, cast(try_cast(b.pull_date AS DATE) AS TIMESTAMP) + INTERVAL 1 DAY available_at,
               avg(try_cast(b.risk_score AS DOUBLE)) risk_score
        FROM bronze.bureau_history b
        JOIN identity.crosswalk x ON x.source_key = 'BUREAU:' || b.bank_subject_ref AND x.valid_to IS NULL
        WHERE try_cast(b.pull_date AS DATE) IS NOT NULL
        GROUP BY 1, 2""")
    con.execute("""CREATE OR REPLACE TABLE layer3.contact_responses AS
        SELECT golden_customer_id,channel_resolved channel,
               try_cast(contact_ts_local AS TIMESTAMP) event_ts,
               try_cast(contact_ts_local AS TIMESTAMP)+INTERVAL 1 DAY available_at,
               CASE channel_resolved WHEN 'call' THEN try_cast(rpc_flag AS BOOLEAN)::INTEGER
                    WHEN 'sms' THEN try_cast(sms_reply_flag AS BOOLEAN)::INTEGER
                    WHEN 'email' THEN try_cast(email_clicked_flag AS BOOLEAN)::INTEGER
                    WHEN 'push' THEN try_cast(push_opened_flag AS BOOLEAN)::INTEGER END::DOUBLE response
        FROM gold.contact_timeline WHERE golden_customer_id IS NOT NULL
          AND channel_resolved IN ('call','sms','email','push')""")
    for table in ("salary_history", "bureau_history", "contact_responses"):
        con.execute(f"CREATE INDEX IF NOT EXISTS {table}_lookup ON layer3.{table}(golden_customer_id, available_at)")
    ensure_table(con)       # text features may be built later; lookups must still work without them
    publish_registry(con)
    return {t: con.execute(f"SELECT count(*) FROM layer3.{t}").fetchone()[0] for t in HISTORY_TABLES}
