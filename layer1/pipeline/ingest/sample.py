"""Build the development sample (about 50K customers) in lake/dev.duckdb from the full bronze layer.

The sample is geographic: whole FSAs (first three postal characters) are taken, in a fixed hash order, until about
50K current CRM rows are covered, and every identity source is filtered to the same FSAs. A person's CRM, card, loan,
deposit and bureau records then mostly land in the sample together, which a random sample of CRM IDs would not give.
Records whose postal code is missing or differs between systems (people who moved) can fall outside the sample;
that is acceptable for development and does not affect full runs.

Usage:  python -m layer1.pipeline.ingest.sample [--target 50000]
"""
import argparse

from layer1.pipeline import config
from layer1.pipeline.db import connect

FSA_OF = {  # how to read the FSA from each bronze table's raw postal value
    "customers": "left(upper(replace(postal_code, ' ', '')), 3)",
    "card_accounts": "left(upper(replace(cardholder_postal_raw, ' ', '')), 3)",
    "loan_accounts": "left(upper(replace(borrower_postal_raw, ' ', '')), 3)",
    "deposit_accounts": "left(upper(replace(holder_postal_raw, ' ', '')), 3)",
    "external": "left(regexp_extract(upper(trim(subject_address_reported)), '([A-Z]\\d[A-Z]) ?\\d[A-Z]\\d$', 1), 3)",
}
COPY_WHOLE = ["geo_reference", "code_lookups", "ops_events", "_load_manifest", "reference_documents"]
BY_CRM_ID = ["contact_history", "promises_to_pay", "agent_notes", "call_transcripts", "model_scores"]
HISTORY = ["loan_instalments", "transactions", "card_statements", "account_monthly_snapshot", "bureau_history",
           "salary_credit_history"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", type=int, default=50_000, help="approximate number of current CRM rows")
    args = ap.parse_args(argv)

    if not config.FULL_DB.exists():
        raise SystemExit("lake/maple.duckdb not found: run python -m layer1.pipeline.ingest.load_bronze first")
    config.DEV_DB.unlink(missing_ok=True)
    con = connect(config.DEV_DB)
    con.execute(f"ATTACH '{config.FULL_DB}' AS full_db (READ_ONLY)")
    con.execute("CREATE SCHEMA bronze")

    con.execute(f"""
        CREATE TABLE bronze._sample_fsas AS
        WITH counts AS (
            SELECT {FSA_OF['customers']} AS fsa, count(*) AS n
            FROM full_db.bronze.customers
            WHERE is_deleted = 'false' AND postal_code IS NOT NULL
            GROUP BY 1
        ), ordered AS (
            SELECT fsa, n, sum(n) OVER (ORDER BY md5(fsa), fsa) AS running FROM counts
        )
        SELECT fsa, n FROM ordered WHERE running - n < {args.target}
    """)
    n_fsa, n_crm = con.execute("SELECT count(*), sum(n) FROM bronze._sample_fsas").fetchone()
    print(f"sample: {n_fsa} FSAs covering {n_crm:,} current CRM rows")

    for table, fsa_expr in FSA_OF.items():
        con.execute(f"""
            CREATE TABLE bronze.{table} AS
            SELECT * FROM full_db.bronze.{table}
            WHERE {fsa_expr} IN (SELECT fsa FROM bronze._sample_fsas)
        """)
    con.execute("""
        CREATE TABLE bronze.collections_cases AS
        SELECT * FROM full_db.bronze.collections_cases
        WHERE coll_customer_ref IN (SELECT crm_customer_id FROM bronze.customers)
    """)
    for table in COPY_WHOLE:
        con.execute(f"CREATE TABLE bronze.{table} AS SELECT * FROM full_db.bronze.{table}")
    # Collections-side tables carry crm_customer_id: keep the sampled customers' rows.
    for table in BY_CRM_ID:
        con.execute(f"""
            CREATE TABLE bronze.{table} AS SELECT * FROM full_db.bronze.{table}
            WHERE crm_customer_id IN (SELECT crm_customer_id FROM bronze.customers)""")
    # Every other table (reference, policy, operations; all small) is copied whole, so Layers 2-4 can develop on the
    # sample too (brief L1-1).
    import json
    handled = set(FSA_OF) | set(BY_CRM_ID) | set(HISTORY) | set(COPY_WHOLE) | {"collections_cases"}
    for table in sorted(json.loads((config.RAW / "schema" / "schema.json").read_text())):
        if table not in handled:
            con.execute(f"CREATE TABLE bronze.{table} AS SELECT * FROM full_db.bronze.{table}")
    # History tables stay where they are: views over the release Parquet, like the full database.
    for table, sql in con.execute("""
            SELECT view_name, sql FROM duckdb_views() WHERE database_name = 'full_db' AND schema_name = 'bronze'
            AND view_name IN (SELECT unnest(?))""", [HISTORY]).fetchall():
        con.execute(sql.replace("CREATE VIEW", "CREATE OR REPLACE VIEW", 1))   # joins this DB's _load_manifest copy

    for table in list(FSA_OF) + ["collections_cases"] + BY_CRM_ID:
        n = con.execute(f"SELECT count(*) FROM bronze.{table}").fetchone()[0]
        print(f"  bronze.{table}: {n:,} rows")
    con.close()


if __name__ == "__main__":
    main()
