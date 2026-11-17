"""SQL guard (architecture §3c): every query Layer 2 runs, compiled or model-written, passes here first.

One read-only SELECT; only Layer 2's safe views and the bronze reference tables; no personal or protected columns;
no file or system functions; a row limit. Uses sqlglot (DuckDB dialect) to see the real tables and columns behind
aliases and CTEs; a conservative regex check is used if sqlglot is missing. Queries also run on a read-only
connection, so even a missed write could not change data.
"""
from __future__ import annotations

import re

ALLOWED_SCHEMAS = {"layer2", "access_analyst"}
ALLOWED_BRONZE = {"metric_definitions", "hardship_programs", "reference_documents", "ops_events", "code_lookups",
                  "qa_checklist", "agents_public"}
DENY_COLUMNS = {
    # direct identifiers and free text
    "first_name", "last_name", "middle_name", "preferred_name", "full_name_raw", "email", "primary_phone_e164",
    "primary_phone_raw", "secondary_phone_raw", "work_phone_raw", "best_reachable_phone", "phone_number_hash",
    "address_line1", "address_line2", "postal_code", "postal_code_raw", "date_of_birth", "dob_raw",
    "national_id_hash", "note_text", "sms_reply_text", "sms_text", "cardholder_name_raw", "borrower_name_raw",
    "holder_name_raw", "subject_name_reported", "subject_dob_reported", "subject_address_reported", "geo_lat", "geo_lon",
    # protected attributes and close proxies (root CLAUDE.md rule 2)
    "gender_code", "marital_status", "citizenship_status", "household_size", "dependants_count",
    "newcomer_program_flag", "newcomer_arrival_date", "accessibility_needs_flag", "accessibility_type",
    "vulnerability_type", "vulnerability_flag", "ccb_deposit_flag", "customer_accent_region", "age", "age_band",
    "aml_risk_rating", "pep_flag", "fsa_income_index", "salutation",
}


def _tagged_protected() -> set:
    """Column names Layer 1's sensitivity tags mark protected or AML (unambiguous names), when the file exists."""
    from pathlib import Path
    files = sorted((Path(__file__).resolve().parents[1] / "layer1" / "config" / "sensitivity").glob("tags_v*.yml"))
    if not files:
        return set()
    try:
        import yaml
        entries = yaml.safe_load(files[-1].read_text()).get("entries", [])
    except Exception:  # noqa: BLE001 - the built-in list still applies
        return set()
    return {e["column"] for e in entries if e.get("tag") in ("Protected attribute", "Restricted (AML)")}


DENY_COLUMNS |= _tagged_protected()
DENY_FUNCTIONS = {"read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto", "read_text", "read_blob",
                  "glob", "getenv", "parquet_scan", "csv_scan", "sniff_csv", "query_table", "query"}
ROW_LIMIT = 1000


def _regex_check(sql: str) -> tuple[bool, str]:
    s = re.sub(r"'[^']*'", "''", sql.lower())
    if ";" in s.strip().rstrip(";"):
        return False, "more than one statement"
    if not re.match(r"\s*(with|select)\b", s):
        return False, "only SELECT queries are allowed"
    if re.search(r"\b(insert|update|delete|drop|create|alter|attach|copy|pragma|install|load|set|export)\b", s):
        return False, "write or system statement"
    for t in re.findall(r"\b(?:from|join)\s+([a-z_][\w.]*)", s):
        schema = t.split(".")[0] if "." in t else None
        if schema and schema not in ALLOWED_SCHEMAS and not (schema == "bronze" and t.split(".")[1] in ALLOWED_BRONZE):
            return False, f"table {t} is outside the allowed views"
    for c in DENY_COLUMNS:
        if re.search(rf"\b{c}\b", s):
            return False, f"column {c} is personal or protected"
    for f in DENY_FUNCTIONS:
        if re.search(rf"\b{f}\s*\(", s):
            return False, f"function {f} is not allowed"
    return True, ""


def check(sql: str) -> tuple[bool, str, str]:
    """(ok, problem, sql with a LIMIT added when the query has none)."""
    sql = sql.strip().rstrip(";")
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        ok, problem = _regex_check(sql)
        if ok and not re.search(r"\blimit\s+\d+\s*$", sql, re.I):
            sql += f"\nLIMIT {ROW_LIMIT}"
        return ok, problem, sql
    try:
        trees = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.SqlglotError as e:   # parse and tokenizer errors (e.g. prose inside the SQL)
        return False, f"SQL does not parse: {str(e).splitlines()[0]}", sql
    if len(trees) != 1 or trees[0] is None:
        return False, "exactly one statement is allowed", sql
    tree = trees[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        return False, "only SELECT queries are allowed", sql
    for bad in (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command, exp.Copy, exp.Set):
        if tree.find(bad):
            return False, "write or system statement", sql
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for t in tree.find_all(exp.Table):
        name, schema = t.name.lower(), (t.db or "").lower()
        if not schema and name in ctes:
            continue
        if schema in ALLOWED_SCHEMAS or (schema == "bronze" and name in ALLOWED_BRONZE):
            continue
        return False, f"table {schema + '.' if schema else ''}{name} is outside the allowed views", sql
    for c in tree.find_all(exp.Column):
        if c.name.lower() in DENY_COLUMNS:
            return False, f"column {c.name} is personal or protected", sql
    for f in tree.find_all(exp.Func):
        fname = (f.sql_name() if hasattr(f, "sql_name") else type(f).__name__).lower()
        if fname in DENY_FUNCTIONS or (isinstance(f, exp.Anonymous) and f.name.lower() in DENY_FUNCTIONS):
            return False, f"function {fname} is not allowed", sql
    if not tree.args.get("limit"):
        sql += f"\nLIMIT {ROW_LIMIT}"
    return True, "", sql
