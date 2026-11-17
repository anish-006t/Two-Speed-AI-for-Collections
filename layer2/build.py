"""Build schema `layer2` (safe views + filter menus) after Layer 1 (architecture §7).

    uv run python -m layer2.build                     # DHANDHO_DB or lake/maple.duckdb
    uv run python -m layer2.build --db lake/dev.duckdb

Adaptive: each view reads Layer 1's analyst view when it exists (access_analyst.*), else its gold table (golden IDs,
approved fixes), else bronze, and uses Layer 1's own trap fixes (recomputed buckets, cleaned quarter-end dates) when
present, so Layer 2 works whatever state Layer 1 is in and picks up its fixes on the next build. A governance check
compares every layer2 column with Layer 1's sensitivity tags. Writes only to schema `layer2`. Close the app and
notebooks first: DuckDB allows one writer.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from layer2.adapters import column_exists, connect, db_path, relation_exists

SQL_DIR = Path(__file__).parent / "sql"
CERTIFIED_MONTH_END_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%Y%m%d"]          # the organisers' reference parsing
LENIENT_MONTH_END_FORMATS = CERTIFIED_MONTH_END_FORMATS + ["%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y"]
# Columns users may filter on, with their menus (distinct values) cached for the planner and the app.
MENU_FIELDS = {
    "f_cases": ["queue", "primary_product", "province_code", "test_cell", "current_bucket", "assignment_method",
                "current_treatment", "strategy_code", "agent_site", "hardship_reason"],
    "f_contacts": ["channel", "direction", "outcome_code", "agent_site", "test_cell", "province_code", "queue"],
    "f_promises": ["ptp_status", "product", "ptp_channel", "agent_site", "queue", "primary_product", "test_cell"],
    "f_accounts": ["product", "product_type", "product_code", "account_status"],
    "f_account_month": ["product_type", "account_status"],
}


def _first(con, *candidates) -> str | None:
    """The first relation that exists: Layer 1's analyst view, then gold, then bronze."""
    for name in candidates:
        schema, table = name.split(".")
        if relation_exists(con, schema, table):
            return name
    return None


def _has(con, relation: str | None, column: str) -> bool:
    return bool(relation) and column_exists(con, *relation.split("."), column)


# When Layer 1's cleaned quarter-end history exists (gold.account_monthly_snapshot, decisions D-38), read it instead
# of parsing bronze. Its month_end_date accepts month-first dates too; month_end keeps the certified parsing.
ACCOUNT_MONTH_FROM_GOLD = """CREATE OR REPLACE TABLE layer2.f_account_month AS
SELECT account_id, product_type, account_status,
       CASE WHEN month_end_date_format = 'MM/DD/YYYY' THEN NULL ELSE month_end_date END AS month_end,
       month_end_date AS month_end_lenient,
       dpd, dpd_bucket, dpd_bucket_recomputed AS bucket_from_dpd,
       layer2.l2_bool(write_off_flag) AS write_off, balance
FROM __SOURCE__;"""


def substitutions(con) -> dict:
    cases = _first(con, "access_analyst.cases", "access_analyst.analyst_cases", "gold.cases", "bronze.collections_cases")
    contacts = _first(con, "access_analyst.contact_timeline", "access_analyst.analyst_contact_timeline", "gold.contact_timeline", "bronze.contact_history")
    promises = _first(con, "access_analyst.promises_to_pay", "access_analyst.analyst_promises_to_pay", "gold.promises_to_pay", "bronze.promises_to_pay")
    accounts = _first(con, "access_analyst.accounts", "access_analyst.analyst_accounts", "gold.c360_accounts")
    history = _first(con, "access_analyst.account_monthly_snapshot", "access_analyst.analyst_account_monthly_snapshot", "gold.account_monthly_snapshot")

    def month_end(formats):
        return "coalesce(" + ", ".join(f"try_strptime(trim(month_end_date), '{f}')" for f in formats) + ")::DATE"

    def golden(alias, rel):
        return f"{alias}.golden_customer_id" if _has(con, rel, "golden_customer_id") else "NULL::VARCHAR"
    adjusted = "nullif(trim(p.ptp_status_adjusted), '')" if _has(con, promises, "ptp_status_adjusted") \
        else "nullif(trim(p.ptp_status), '')"
    bucket = "c.current_bucket_recomputed" if _has(con, cases, "current_bucket_recomputed") \
        else "layer2.bucket_from_dpd(try_cast(c.current_dpd AS INTEGER))"
    subs = {
        "10_f_cases.sql": {"__CASES__": cases, "__GOLDEN__": golden("c", cases), "__CASE_BUCKET__": bucket},
        "20_f_contacts.sql": {"__CONTACTS__": contacts, "__GOLDEN__": golden("h", contacts)},
        "30_f_promises.sql": {"__PROMISES__": promises, "__GOLDEN__": golden("p", promises), "__ADJUSTED__": adjusted},
        "40_f_accounts.sql": {"__ACCOUNT_GOLDEN__": "g.golden_customer_id" if accounts else "NULL::VARCHAR",
                              "__ACCOUNT_JOIN__": ("LEFT JOIN (SELECT account_id, any_value(golden_customer_id) AS "
                                                   f"golden_customer_id FROM {accounts} GROUP BY 1) g USING (account_id)")
                              if accounts else ""},
        "50_f_account_month.sql": {"__MONTH_END_LENIENT__": month_end(LENIENT_MONTH_END_FORMATS),
                                   "__MONTH_END__": month_end(CERTIFIED_MONTH_END_FORMATS)},
        "_sources": {"cases": cases, "contacts": contacts, "promises": promises, "accounts": accounts,
                     "account_history": history if _has(con, history, "month_end_date_format") else "bronze (parsed here)",
                     "case_bucket": "Layer 1 current_bucket_recomputed" if "recomputed" in bucket else "computed here"},
    }
    if _has(con, history, "month_end_date_format") and _has(con, history, "dpd_bucket_recomputed"):
        subs["_replace"] = {"50_f_account_month.sql": ACCOUNT_MONTH_FROM_GOLD.replace("__SOURCE__", history)}
    return subs


TAGS_DIR = Path(__file__).resolve().parents[1] / "layer1" / "config" / "sensitivity"


def governance_check(con) -> list[str]:
    """Compare every layer2 column with Layer 1's sensitivity tags (when the tags file exists).

    Fails the build on a column tagged protected or AML; lists direct-PII and proxy names for review (the tags are
    per source table, and names such as province_code are quasi-identifiers on cases but mis-tagged elsewhere)."""
    files = sorted(TAGS_DIR.glob("tags_v*.yml"))
    if not files:
        return ["no Layer 1 sensitivity tags yet (layer1/config/sensitivity/tags_v*.yml)"]
    import yaml
    entries = yaml.safe_load(files[-1].read_text()).get("entries", [])
    by_tag: dict[str, set] = {}
    for e in entries:
        by_tag.setdefault(e["tag"], set()).add(e["column"])
    cols = {c for (c,) in con.execute("SELECT DISTINCT column_name FROM information_schema.columns "
                                      "WHERE table_schema = 'layer2'").fetchall()}
    blocked = cols & (by_tag.get("Protected attribute", set()) | by_tag.get("Restricted (AML)", set()))
    if blocked:
        raise RuntimeError(f"layer2 exposes protected or AML columns: {sorted(blocked)}")
    notes = [f"tags: {files[-1].name}; no protected or AML column in layer2"]
    for tag in ("Direct PII", "Proxy (review)"):
        hit = sorted(cols & by_tag.get(tag, set()))
        if hit:
            notes.append(f"review: {tag} name match {hit}")
    return notes


def build(db: str | Path | None = None, verbose: bool = True) -> dict:
    con = connect(read_only=False, path=db or db_path())
    con.execute("CREATE SCHEMA IF NOT EXISTS layer2")
    subs = substitutions(con)
    timings = {}
    for path in sorted(SQL_DIR.glob("*.sql")):
        sql = subs.get("_replace", {}).get(path.name) or path.read_text()
        for token, value in subs.get(path.name, {}).items():
            sql = sql.replace(token, value)
        start = time.time()
        con.execute(sql)
        timings[path.stem] = round(time.time() - start, 1)
        if verbose:
            print(f"{path.stem:<22} {timings[path.stem]:>6}s", flush=True)
    rows = []
    for table, fields in MENU_FIELDS.items():
        for field in fields:
            rows += con.execute(f"SELECT '{table}', '{field}', CAST({field} AS VARCHAR), count(*) FROM layer2.{table} "
                                f"WHERE {field} IS NOT NULL GROUP BY 3 HAVING count(*) > 0 ORDER BY 4 DESC LIMIT 60").fetchall()
    con.execute("CREATE OR REPLACE TABLE layer2.menu_values (view_name VARCHAR, field VARCHAR, value VARCHAR, n BIGINT)")
    con.executemany("INSERT INTO layer2.menu_values VALUES (?, ?, ?, ?)", rows)
    counts = {t: con.execute(f"SELECT count(*) FROM layer2.{t}").fetchone()[0] for t in
              ("f_cases", "f_contacts", "f_promises", "f_accounts", "f_account_month", "f_assignments", "f_deposits",
               "transcript_index")}
    governance = governance_check(con)
    con.execute("CREATE OR REPLACE TABLE layer2.build_info AS SELECT now() AS built_at, ? AS sources, ? AS row_counts, "
                "? AS governance", [str(subs["_sources"]), str(counts), "; ".join(governance)])
    con.close()
    if verbose:
        print("sources:", subs["_sources"])
        print("rows:", counts)
        print("governance:", "; ".join(governance))
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", help="DuckDB file (default: DHANDHO_DB or lake/maple.duckdb)")
    args = ap.parse_args(argv)
    build(args.db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
