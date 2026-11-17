"""Propose sensitivity tags for all 1,417 dictionary columns and source-to-target mappings for gold.c360_customer
(architecture §12, hand-off task 1, decisions D-40).

Proposals are made by rules (no model call): the dictionary's own tags are kept, the release README's protected
list is applied, and column names and descriptions suggest Direct PII, quasi-identifiers and possible proxies. Every
entry is written with status "proposed", a reason and a confidence. A person approves entries by setting
status: approved, approved_by and approved_on (`python -m layer1.governance.propose approve ...` does this for a
list of entries). A new version of either file is a new file (_v2), so git history is the audit trail.

Usage:
  python -m layer1.governance.propose tags        -> layer1/config/sensitivity/tags_v1.yml
  python -m layer1.governance.propose mappings    -> layer1/config/mappings/mappings_v1.yml  (needs lake/dev.duckdb)
  python -m layer1.governance.propose approve FILE --by "<name>" [--where tag=Proxy] [--ids a,b]
"""
import argparse
import re
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from layer1.pipeline import config

ROOT = config.ROOT
DICT = ROOT / "docs" / "data_dictionary" / "data_dictionary_columns.csv"
TAGS_OUT = ROOT / "layer1" / "config" / "sensitivity" / "tags_v1.yml"
MAP_OUT = ROOT / "layer1" / "config" / "mappings" / "mappings_v1.yml"

# Order matters: the first matching rule wins. (tag, confidence, reason, test on (table, column, description))
PROTECTED_RELEASE = {"gender", "marital", "citizenship", "household", "dependants", "newcomer", "accessibility",
                     "vulnerab", "accent"}
AGENT_TABLES = {"agents", "agent_shifts", "case_assignment_history"}   # agent skills, not customer attributes
RULES = [
    ("Internal", "medium", "agent skill or certification, not a customer attribute",
     lambda t, c, d: t in AGENT_TABLES and ("vulnerab" in c or "certified" in c)),
    ("Protected attribute", "high", "release README lists this among protected attributes (fairness tests only)",
     lambda t, c, d: any(k in c for k in PROTECTED_RELEASE) or c == "ccb_deposit_flag"),
    ("Protected attribute", "high", "age is derived from DOB and excluded from gold and features (architecture §11)",
     lambda t, c, d: c in ("age", "age_band")),
    ("Restricted (AML)", "high", "AML field: restricted schema only (architecture §11)",
     lambda t, c, d: c in ("aml_risk_rating", "pep_flag")),
    ("Direct PII", "high", "free text can contain names, phones and addresses; redact before use",
     lambda t, c, d: c.endswith("_text") or c in ("note_text", "turns", "turn.text", "raw_description", "sms_text")),
    ("Direct PII", "high", "identifies a person directly (name, contact detail, ID or date of birth)",
     lambda t, c, d: re.search(r"(^|_)(name|first_name|last_name|full_name)(_|$)", c) is not None
     and "file" not in c and "product" not in c and "model" not in c and "template" not in c and "bureau" not in c
     and "agency" not in c and "program" not in c and "trustee" not in c and "employer" not in c and "cma" not in c
     and "region" not in c),
    ("Direct PII", "high", "contact detail or identifier",
     lambda t, c, d: re.search(r"(phone_(raw|e164|number)|^email$|email_address|address_line|_address_reported|dob|"
                               r"date_of_birth|vin|national_id_hash|ip_address)", c) is not None),
    ("Quasi-identifier", "medium", "location, employer or date that narrows a person down when combined",
     lambda t, c, d: re.search(r"(postal|^fsa$|geo_lat|geo_lon|^city$|employer_name|counterparty_name|"
                               r"payroll_descriptor|address_since)", c) is not None),
    ("Proxy (review)", "medium", "may stand in for origin, language or ethnicity (protected grounds); review before "
     "any use in decisions", lambda t, c, d: "language" in c or c in ("interpreter_used_flag", "salutation")),
    ("Proxy (review)", "medium", "may stand in for income, family or health status; review before any use in decisions",
     lambda t, c, d: c in ("fsa_income_index", "urban_rural", "home_branch_city", "provincial_benefit_flag",
                           "cpp_oas_deposit_flag", "ei_deposit_flag", "military_or_reserve_flag",
                           "third_party_relationship", "power_of_attorney_flag")),
]


def propose_tags():
    d = pd.read_csv(DICT, dtype=str, keep_default_na=False)
    entries = []
    for r in d.itertuples(index=False):
        t, c, desc, given = r.Table, r.Column, r.Description, r.Sensitivity
        tag = conf = reason = None
        for rt, rc, rr, test in RULES:
            if test(t, c.lower(), desc.lower()):
                tag, conf, reason = rt, rc, rr
                break
        if given:   # the dictionary's own tag is kept; a rule may only make it stricter
            stricter = tag in ("Protected attribute", "Restricted (AML)") and given != tag
            if not stricter:
                tag, conf, reason = given, "high", "tag from the data dictionary"
        if tag is None:
            tag, conf, reason = "Internal", "low", "no personal or protected signal found by the rules"
        entries.append({"id": f"{t}.{c}", "table": t, "column": c, "tag": tag, "dictionary_tag": given or None,
                        "confidence": conf, "reason": reason, "status": "proposed", "approved_by": None,
                        "approved_on": None})
    return entries


# Survivorship and derivation of each gold.c360_customer column (architecture §8, dbt/models/gold/c360_customer.sql).
DERIVED = {
    "golden_customer_id": ("identity.crosswalk", "golden_customer_id", "assigned by clustering; stable across runs"),
    "crm_customer_ids": ("identity.crosswalk", "source_id where source_system = 'CRM'", "all merged CRM records"),
    "source_keys": ("identity.crosswalk", "source_key", "every source key in the cluster"),
    "match_probability": ("identity.crosswalk", "match_probability", "lowest Splink probability among the links"),
    "identity_confidence": ("identity.golden_confidence", "identity_confidence", "high / medium / low (architecture §7)"),
    "verify_before_disclosure": ("identity.golden_confidence", "identity_confidence", "true unless confidence is high"),
    "best_reachable_phone": ("contact_history + customers", "phone_number_hash, rpc_flag",
                             "latest right-party contact on a consented CRM phone (D-06)"),
    "product_count": ("gold.c360_accounts", "account_id", "count of linked accounts"),
    "has_card": ("gold.c360_accounts", "product", "any linked card"),
    "has_loan": ("gold.c360_accounts", "product", "any linked loan"),
    "has_deposit": ("gold.c360_accounts", "product", "any linked deposit account"),
    "total_credit_exposure": ("gold.c360_accounts", "credit_exposure", "card balance + loan outstanding + overdraft used"),
    "max_dpd_current": ("gold.c360_accounts", "dpd", "maximum over linked accounts"),
    "open_case_id": ("collections_cases", "case_id", "latest open case (outcome is null)"),
    "current_bucket": ("collections_cases", "current_bucket", "bucket of the latest open case"),
    "hardship_flag": ("collections_cases + account tables", "hardship_flag, hardship_program_flag",
                      "true if any case or account is true; protective use only"),
    "incident_affected": ("gold.incident_effects", "golden_customer_id", "any INC-0914 signal (D-24)"),
    "source_systems": ("identity.crosswalk", "source_system", "systems in the cluster"),
    "survivorship_source": ("pipeline", "", "which record supplied each group of fields"),
    "date_of_birth": ("customers", "date_of_birth", "CRM value; product DOB only when no CRM record (identity only)"),
    "primary_phone_e164": ("customers", "primary_phone_e164", "most recently updated CRM record; else latest product"),
    "postal_code": ("customers", "postal_code", "most recently updated CRM record; else latest product"),
    "first_name": ("customers", "first_name", "most recently updated CRM record; else latest product"),
    "last_name": ("customers", "last_name", "most recently updated CRM record; else latest product"),
    "email": ("customers", "email", "most recently updated CRM record (CRM only)"),
    "province_code": ("customers", "province_code", "most recently updated CRM record"),
    "time_zone": ("customers", "time_zone", "most recently updated CRM record"),
    "preferred_language": ("customers", "preferred_language", "most recently updated CRM record; servicing only, "
                           "never a feature (Proxy, D-37)"),
    "permitted_call_start_local": ("customers", "permitted_call_start_local", "most recently updated CRM record"),
    "permitted_call_end_local": ("customers", "permitted_call_end_local", "most recently updated CRM record"),
    "sunday_contact_permitted": ("customers", "sunday_contact_permitted", "false if any merged CRM record is false"),
    "voicemail_permitted": ("customers", "voicemail_permitted", "false if any merged CRM record is false"),
    "contact_at_work_permitted": ("customers", "contact_at_work_permitted", "false if any merged CRM record is false"),
}
CONSENT = "most recent consent_last_updated_ts when identity confidence is high, else most restrictive"
RESTRICTION = "true if any merged CRM record is true"


def propose_mappings(db):
    import duckdb
    con = duckdb.connect(str(db), read_only=True)
    cols = [r[0] for r in con.execute("DESCRIBE gold.c360_customer").fetchall()]
    crm_cols = {r[0] for r in con.execute("DESCRIBE bronze.customers").fetchall()}
    entries = []
    for c in cols:
        if c.startswith("_"):
            src, scol, rule, conf = "pipeline", c, "lineage column (CLAUDE.md rule 7)", "high"
        elif c in DERIVED:
            src, scol, rule = DERIVED[c]
            conf = "high"
        elif c.startswith("consent_"):
            src, scol, rule, conf = "customers", c, CONSENT, "high"
        elif c.endswith("_flag") and c in crm_cols:
            src, scol, rule, conf = "customers", c, RESTRICTION, "high"
        elif c in crm_cols:
            src, scol, rule, conf = "customers", c, "most recently updated CRM record", "medium"
        else:
            src, scol, rule, conf = "unknown", "", "no rule matched: needs a person", "low"
        entries.append({"id": f"gold.c360_customer.{c}", "target": f"gold.c360_customer.{c}", "source_table": src,
                        "source_column": scol, "rule": rule, "confidence": conf, "status": "proposed",
                        "approved_by": None, "approved_on": None})
    return entries


def write(path, kind, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"kind": kind, "version": 1, "generated_on": str(date.today()),
           "how_to_approve": "set status: approved, approved_by and approved_on; changes go into a new _vN file",
           "entries": entries}
    path.write_text(yaml.safe_dump(doc, sort_keys=False, width=120))
    counts = pd.Series([e.get("tag") or e["confidence"] for e in entries]).value_counts().to_dict()
    print(f"{path.relative_to(ROOT)}: {len(entries)} entries {counts}")


def approve(path, by, where=None, ids=None):
    doc = yaml.safe_load(Path(path).read_text())
    n = 0
    for e in doc["entries"]:
        ok = (ids and e["id"] in ids) or (where and all(str(e.get(k)) == v for k, v in where.items()))
        if ok and e["status"] != "approved":
            e.update(status="approved", approved_by=by, approved_on=str(date.today()))
            n += 1
    Path(path).write_text(yaml.safe_dump(doc, sort_keys=False, width=120))
    print(f"approved {n} entries in {path} as {by}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["tags", "mappings", "approve"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--db", default=str(config.DEV_DB))
    ap.add_argument("--by", help="name of the person approving (required for approve)")
    ap.add_argument("--where", help="approve entries matching key=value[,key=value]")
    ap.add_argument("--ids", help="approve these entry ids (comma-separated)")
    args = ap.parse_args(argv)
    if args.action == "tags":
        write(TAGS_OUT, "sensitivity_tags", propose_tags())
    elif args.action == "mappings":
        write(MAP_OUT, "source_to_target_mappings", propose_mappings(args.db))
    else:
        if not (args.file and args.by and args.by.strip()):
            raise SystemExit("approve needs the file and --by with the approver's name")
        where = dict(kv.split("=", 1) for kv in args.where.split(",")) if args.where else None
        approve(args.file, args.by.strip(), where, set(args.ids.split(",")) if args.ids else None)


if __name__ == "__main__":
    main()
