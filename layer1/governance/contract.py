"""Generate the data contract for gold.c360_customer: deliverables/data_contract_c360.yaml (brief L1-6, D-42).

Starts from the bank's own contract format (files/docs/data_contracts/DC-COLL-001: owner, refresh, quality rules,
allowed and not-allowed uses) and architecture §11. The schema is read from the built table, sensitivity from
layer1/config/sensitivity/tags_v1.yml, source and survivorship from layer1/config/mappings/mappings_v1.yml, and every
quality rule names the dbt test that enforces it, with its severity read from that test. Regenerate after any change
to gold; layer1/tests/test_contract.py fails if the file and the build disagree.

Usage:  python -m layer1.governance.contract [--db lake/maple.duckdb]
"""
import argparse
import re
from datetime import date

import duckdb
import yaml

from layer1.governance import propose
from layer1.pipeline import config

OUT = config.ROOT / "deliverables" / "data_contract_c360.yaml"
TESTS = config.ROOT / "dbt" / "tests" / "contract"

# (id, rule, enforced by) in the order of architecture §11; severity is read from each test file.
QUALITY_RULES = [
    ("Q1", "golden_customer_id is unique and not null", "schema.yml: unique / not_null on golden_customer_id"),
    ("Q2", "each source_system + source_id maps to exactly one current golden ID", "c1_one_current_golden_per_source_key"),
    ("Q3", "every current CRM row and every card, loan, deposit and bureau key is in the crosswalk",
     "c2_every_key_in_crosswalk"),
    ("Q4", "no automatic link joins records whose dates of birth are not identical; differing DOBs are linked only "
           "through a logged human approval", "c3_no_auto_link_without_identical_dob"),
    ("Q5", "each restriction flag equals the OR of the same flag across the merged CRM records",
     "c4_restrictions_are_or_of_merged_records"),
    ("Q6", "precision per source on its truth set is at or above the floor (0.98, provisional)", "c5_precision_floor"),
    ("Q7", "no automatic link joins a joint holder, co-borrower, Jr/Sr or look-alike pair",
     "c6_no_auto_link_breaks_constraint"),
    ("Q8", "every golden ID has an identity_confidence; when it is not high, verify_before_disclosure is true",
     "c7_verify_unless_high"),
    ("Q9", "postal_code matches A1A 1A1 with a known FSA; phones are valid E.164", "c8_formats"),
    ("Q10", "_loaded_at is within one day of the source snapshot date", "c9_loaded_recently"),
    ("Q11", "protected attributes (including every vulnerability field), age, age_band and AML fields never appear "
            "in gold", "no_protected_columns_in_gold"),
    ("Q12", "every gold table carries the five lineage columns", "gold_lineage_columns"),
    ("Q13", "the analyst views hold no direct identifiers, postal code, FSA or protected attribute",
     "access_analyst_has_no_pii"),
    ("Q14", "the agent view never shows a full phone, full email or date of birth", "access_agent_masked"),
]


def severity(test):
    if test.startswith("schema.yml"):
        return "blocking"
    text = (TESTS / f"{test}.sql").read_text()
    return "warning" if re.search(r"severity\s*=\s*['\"]warn['\"]", text) else "blocking"


def build(db):
    con = duckdb.connect(str(db), read_only=True)
    cols = con.execute("DESCRIBE gold.c360_customer").fetchall()
    rows = con.execute("SELECT count(*), max(_loaded_at) FROM gold.c360_customer").fetchone()
    mappings = {e["target"].split(".")[-1]: e for e in yaml.safe_load(propose.MAP_OUT.read_text())["entries"]}
    tags = {e["column"]: e["tag"] for e in yaml.safe_load(propose.TAGS_OUT.read_text())["entries"]
            if e["table"] == "customers"}
    derived_sensitivity = {"best_reachable_phone": "Direct PII", "golden_customer_id": "Internal",
                           "crm_customer_ids": "Internal", "source_keys": "Internal"}
    schema = []
    for name, dtype, *_ in cols:
        m = mappings.get(name, {})
        schema.append({
            "name": name, "type": dtype,
            "sensitivity": tags.get(name) or derived_sensitivity.get(name, "Internal"),
            "source": f"{m.get('source_table', 'pipeline')}.{m.get('source_column', name)}".rstrip("."),
            "rule": m.get("rule", ""),
            **({"primary_key": True} if name == "golden_customer_id" else {}),
        })
    return {
        "data_contract": {
            "id": "DC-C360-001", "name": "Customer 360 golden record", "version": "1.0.0", "status": "active",
            "generated_on": str(date.today()), "dataset": "gold.c360_customer",
            "format_follows": "DC-COLL-001 (bank data contract: owner, refresh, quality rules, allowed uses)",
        },
        "owner": {"business_owner": "Head of Collections Operations (name to confirm)",
                  "technical_steward": "Layer 1, Data Product Factory (team dhandho)",
                  "contact": "layer1/CHANGELOG.md; changes that affect consumers are marked 'Affects layer N'"},
        "description": "One row per real customer, keyed by golden_customer_id, with identity confidence, contact "
                       "details, consent, contact rules, restrictions, the hardship care flag, incident impact and "
                       "lineage. Built from CRM, cards, loans, deposits and bureau by identity resolution "
                       "(architecture §7) and survivorship (§8).",
        "grain": "one row per golden_customer_id; history in gold.c360_customer_history (dbt_valid_from / dbt_valid_to)",
        "refresh": {"schedule": "daily batch after the nightly source extracts (05:00 ET), Dagster schedule 06:00",
                    "build_time_full_data": "about 16 minutes end to end",
                    "freshness_rule": "Q10: _loaded_at within one day of the source snapshot date (warning)"},
        "volume": {"rows_at_last_build": rows[0], "last_loaded_at": str(rows[1]),
                   "expected": "about 1.06M rows; a change over 50% against the previous run blocks the release "
                               "(matching-health checks in gold.dq_report)"},
        "schema": schema,
        "identity": {
            "keys": "identity.crosswalk maps every source key to its golden_customer_id, with probability, rule and "
                    "reviewer, and keeps history (valid_from / valid_to)",
            "confidence": {"high": "deterministic, human-approved, or probability >= 0.95 with no constraint",
                           "medium": "linked on thin evidence (card initial + masked phone) or merged/split this run",
                           "low": "pair waiting for review, or no CRM record"},
            "verify_before_disclosure": "true unless identity_confidence is high; agents must verify identity "
                                        "before discussing any debt",
            "match_quality_at_last_build": "precision 99.8% and recall 97.7-98.9% per source on the collections-case "
                                           "truth set; 0 joint holders or co-borrowers merged",
        },
        "quality_rules": [{"id": i, "rule": r, "enforced_by": t, "severity": severity(t)} for i, r, t in QUALITY_RULES],
        "quality_on_failure": "blocking rules stop the release (dbt build fails, Dagster run fails); warnings are "
                              "reported in gold.dq_report and deliverables/data_quality_report.md",
        "allowed_uses": [
            "collections servicing and contact planning",
            "the agent's customer view, through access_agent.case_brief",
            "aggregate analysis in Layer 2, through access_analyst views",
            "Layer 3 features, through gold.l3_customer_features only",
        ],
        "not_allowed_uses": [
            "marketing (national_dncl_flag and casl_express_consent_email are marketing fields and never gate "
            "collections contact)",
            "credit underwriting",
            "any decision, feature, rule or answer that uses protected attributes or their proxies "
            "(see layer1/config/sensitivity/tags_v1.yml)",
            "using the hardship flag to justify adverse action (protective handling only)",
            "contacting a customer automatically: every customer-facing action is reviewed by a person",
        ],
        "access": {
            "collections_agent": "access_agent.case_brief: own cases, masked phone and email, no DOB, verify gate",
            "analyst": "access_analyst.*: no names, phones, email, DOB, postal code or FSA",
            "data_engineer": "pipeline tables, access_engineer.quarantine (values masked), gold.dq_report",
            "identity_reviewer": "the two records of one pair, in the review app",
            "fairness_reviewer": "restricted.fairness_link_rates (aggregates only)",
        },
        "excluded_from_this_dataset": "protected attributes (gender, marital status, citizenship, household, "
                                      "dependants, newcomer, accessibility, vulnerability, accent), age, age_band, "
                                      "aml_risk_rating, pep_flag: only in the restricted schema",
        "lineage": {"columns": ["_source_system", "_source_file", "_batch_id", "_loaded_at", "_pipeline_run_id"],
                    "path": "data/raw -> bronze (as received, checksummed) -> silver (parsed, quarantine) -> "
                            "identity (Splink, clustering, crosswalk) -> gold.c360_customer"},
        "change_policy": "schema changes are additive by default; a removed or renamed column is a new minor or "
                         "major version, announced in layer1/CHANGELOG.md with 'Affects layer N' before release. "
                         "Source values are never overwritten; corrections are new columns with a reason.",
        "decisions_log": "layer1/docs/decisions.md (D-01 to D-42)",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(config.FULL_DB))
    args = ap.parse_args(argv)
    doc = build(args.db)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    header = ("# Data contract for gold.c360_customer. Generated by `python -m layer1.governance.contract` from the\n"
              "# built table, the sensitivity tags and the C360 mappings; do not edit by hand, regenerate.\n")
    OUT.write_text(header + yaml.safe_dump(doc, sort_keys=False, width=118, allow_unicode=True))
    print(f"{OUT.relative_to(config.ROOT)}: {len(doc['schema'])} columns, {len(doc['quality_rules'])} quality rules")


if __name__ == "__main__":
    main()
