"""Write column descriptions for every gold model into dbt/models/gold/schema.yml (hand-off task 4, decisions D-41).

Descriptions come from what already exists, so they stay in step with the build: the C360 mappings
(layer1/config/mappings/mappings_v1.yml), the data dictionary for columns passed through from a source table, and a
short list for columns Layer 1 derives. Existing tests in schema.yml are kept.

Usage:  python -m layer1.governance.dbt_docs [--db lake/dev.duckdb]
then:   uv run dbt docs generate --project-dir dbt --profiles-dir dbt --target dev
"""
import argparse

import duckdb
import pandas as pd
import yaml

from layer1.governance import propose
from layer1.pipeline import config

SCHEMA = config.ROOT / "dbt" / "models" / "gold" / "schema.yml"
SOURCE_OF = {   # gold model -> source table whose columns it passes through
    "cases": "collections_cases", "contact_timeline": "contact_history", "promises_to_pay": "promises_to_pay",
    "account_monthly_snapshot": "account_monthly_snapshot", "c360_accounts": None, "incident_effects": None,
    "l3_customer_features": None, "c360_customer": None,
}
MODEL_DESCRIPTIONS = {
    "c360_customer": "One row per golden customer: identity confidence, contact details, consent, contact rules, "
                     "restrictions, hardship and incident flags, with lineage (data contract, architecture §11).",
    "c360_accounts": "Every card, loan and deposit account with its golden customer ID and recomputed DPD bucket.",
    "account_monthly_snapshot": "Quarter-end account history with parsed month_end_date and recomputed buckets (D-38).",
    "cases": "Collections cases with the case's golden customer ID, case-link alarm and recomputed bucket.",
    "contact_timeline": "Contact history keyed by golden customer ID; channel_resolved fixes the REL-0926 rename.",
    "promises_to_pay": "Promises to pay keyed by golden customer ID, with the approved INC-0914 adjustment columns.",
    "incident_effects": "Which customers each known incident affected, and through which signal (D-24).",
    "l3_customer_features": "Feature view for Layer 3: no identifiers, protected attributes or proxies (D-37).",
}
DERIVED = {
    "golden_customer_id": "Golden customer ID from identity resolution (identity.crosswalk).",
    "dpd_missing": "True when dpd is empty in the source (about 8% of card and loan rows); never filled from cycles.",
    "dpd_bucket_recomputed": "Bucket derived from dpd with the release's bucket edges; NULL when dpd is missing (D-38).",
    "dpd_bucket_mismatch": "True when the source bucket disagrees with dpd_bucket_recomputed.",
    "current_bucket_recomputed": "Bucket derived from current_dpd (D-38).",
    "current_bucket_mismatch": "True when the source current_bucket disagrees with current_dpd.",
    "case_link_alarm": "True when an account in the case belongs to a different golden customer than the case (§7).",
    "customer_ref_unresolved": "True when coll_customer_ref matches no current CRM record (D-13).",
    "account_golden_ids": "Golden IDs of the accounts listed in the case.",
    "account_id_list": "account_ids parsed from JSON or a bare ID (D-13).",
    "channel_resolved": "coalesce(channel_v2, channel) after the approved REL-0926 fix; channel as received before.",
    "channel_fix_applied": "Whether the REL-0926 channel fix was approved when this table was built.",
    "incident_candidate": "Promise matches the INC-0914 rule (broken on the outage day, paid the next day).",
    "ptp_status_adjusted": "ptp_status with the approved INC-0914 adjustment; ptp_status stays as received.",
    "adjustment_reason": "Why ptp_status_adjusted differs from ptp_status.",
    "incident_id": "Incident behind an adjustment or effect.",
    "signal": "How the incident showed up: contact_tagged, ptp_broken_paid_next_day or late_fee_in_window.",
    "month_end_date": "month_end_date parsed from four formats; NULL unless it is a month end (D-38).",
    "month_end_date_raw": "month_end_date as received.",
    "month_end_date_format": "Which of the four formats the raw value used.",
    "crm_customer_id_as_received": "CRM ID from the snapshot as received; noisy, so not used for linking (D-03).",
    "source_key": "source_system:source_id key of the record in identity resolution.",
    "account_id": "Account ID as in the source system (card_account_id, loan_id or deposit_account_id).",
    "product_type": "Product type or code from the source account table.",
    "balance": "Current balance (card), total outstanding (loan) or current balance (deposit), CAD.",
    "ptp_id": "Promise to pay affected by the incident, when the signal is a promise.",
    "product": "card, loan or deposit.",
    "credit_exposure": "Card balance, loan outstanding or overdraft used, counted in total_credit_exposure.",
    "_source_system": "Lineage: source system of the row.", "_source_file": "Lineage: file the row came from.",
    "_batch_id": "Lineage: load batch.", "_loaded_at": "Lineage: when the source file was loaded.",
    "_pipeline_run_id": "Lineage: dbt invocation that built the row.",
}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(config.DEV_DB))
    args = ap.parse_args(argv)
    con = duckdb.connect(args.db, read_only=True)
    dictionary = pd.read_csv(propose.DICT, dtype=str, keep_default_na=False)
    dict_desc = {(r.Table, r.Column): r.Description for r in dictionary.itertuples(index=False)}
    mappings = {e["target"].split(".")[-1]: e for e in yaml.safe_load(propose.MAP_OUT.read_text())["entries"]}
    tags = {(e["table"], e["column"]): e["tag"] for e in yaml.safe_load(propose.TAGS_OUT.read_text())["entries"]}

    doc = yaml.safe_load(SCHEMA.read_text())
    existing = {m["name"]: m for m in doc["models"]}
    missing = []
    for model, source in SOURCE_OF.items():
        cols = [r[0] for r in con.execute(f"DESCRIBE gold.{model}").fetchall()]
        m = existing.setdefault(model, {"name": model})
        m["description"] = MODEL_DESCRIPTIONS[model]
        by_name = {c["name"]: c for c in m.get("columns", [])}
        out = []
        for c in cols:
            entry = by_name.get(c, {"name": c})
            if model in ("c360_customer", "l3_customer_features") and c in mappings:
                mp = mappings[c]
                text = f"{mp['rule'][0].upper()}{mp['rule'][1:]}. Source: {mp['source_table']}.{mp['source_column']}".rstrip(".")
                tag = tags.get(("customers", c))
            elif c in DERIVED:
                text, tag = DERIVED[c], None
            elif source and (source, c) in dict_desc:
                text, tag = f"{dict_desc[(source, c)]} (as received from {source}).", tags.get((source, c))
            elif (("card_accounts", c) in dict_desc) or (("customers", c) in dict_desc):
                key = ("customers", c) if ("customers", c) in dict_desc else ("card_accounts", c)
                text, tag = f"{dict_desc[key]} (from {key[0]}).", tags.get(key)
            else:
                text, tag = None, None
                missing.append(f"{model}.{c}")
            if text:
                entry["description"] = text + (f" Sensitivity: {tag}." if tag and tag != "Internal" else "")
            out.append(entry)
        m["columns"] = out
    doc["models"] = list(existing.values())
    SCHEMA.write_text(yaml.safe_dump(doc, sort_keys=False, width=120))
    print(f"described {sum(len(m['columns']) for m in doc['models'])} columns; missing: {missing or 'none'}")
    return missing


if __name__ == "__main__":
    main()
