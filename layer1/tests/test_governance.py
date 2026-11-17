"""Sensitivity tags and source-to-target mappings (architecture §12, decisions D-40)."""
import duckdb
import pandas as pd
import pytest
import yaml

from layer1.governance import propose
from layer1.pipeline import config

TAGS = yaml.safe_load(propose.TAGS_OUT.read_text())["entries"]
MAPS = yaml.safe_load(propose.MAP_OUT.read_text())["entries"]
ALLOWED = {"Direct PII", "Quasi-identifier", "Protected attribute", "Restricted (AML)", "Proxy (review)", "Internal"}
NEVER_IN_GOLD = {"Protected attribute", "Restricted (AML)"}


def test_every_dictionary_column_has_one_tag():
    d = pd.read_csv(propose.DICT, dtype=str, keep_default_na=False)
    ids = [e["id"] for e in TAGS]
    assert len(ids) == len(set(ids)) == len(d)
    assert {e["tag"] for e in TAGS} <= ALLOWED
    assert all(e["status"] in ("proposed", "approved") and e["reason"] for e in TAGS)


def test_dictionary_tags_are_kept_or_made_stricter():
    for e in TAGS:
        if e["dictionary_tag"]:
            assert e["tag"] == e["dictionary_tag"] or e["tag"] in NEVER_IN_GOLD, e["id"]


@pytest.mark.parametrize("col, tag", [
    ("customers.vulnerability_flag", "Protected attribute"), ("customers.age_band", "Protected attribute"),
    ("customers.pep_flag", "Restricted (AML)"), ("customers.preferred_language", "Proxy (review)"),
    ("customers.email", "Direct PII"), ("agents.vulnerable_customer_certified", "Internal"),
])
def test_key_tags(col, tag):
    assert next(e["tag"] for e in TAGS if e["id"] == col) == tag


def test_approved_entries_name_a_person():
    for e in TAGS + MAPS:
        if e["status"] == "approved":
            assert e["approved_by"] and e["approved_on"], e["id"]


@pytest.mark.skipif(not config.DEV_DB.exists(), reason="needs lake/dev.duckdb")
def test_every_c360_column_is_mapped_and_no_unknown_source():
    con = duckdb.connect(str(config.DEV_DB), read_only=True)
    cols = {r[0] for r in con.execute("DESCRIBE gold.c360_customer").fetchall()}
    mapped = {e["target"].split(".")[-1] for e in MAPS}
    assert cols == mapped
    assert not [e["id"] for e in MAPS if e["source_table"] == "unknown"]


@pytest.mark.skipif(not config.DEV_DB.exists(), reason="needs lake/dev.duckdb")
def test_protected_columns_never_reach_gold_access_or_layer_schemas():
    banned = {e["column"] for e in TAGS if e["tag"] in NEVER_IN_GOLD}
    proxies = {e["column"] for e in TAGS if e["tag"] == "Proxy (review)" and e["status"] == "approved"}
    con = duckdb.connect(str(config.DEV_DB), read_only=True)
    cols = con.execute("""SELECT table_schema, table_name, column_name FROM information_schema.columns
                          WHERE table_schema IN ('gold', 'access_analyst', 'access_agent', 'layer2', 'layer3')""").fetchall()
    assert not [c for c in cols if c[2] in banned]
    feature_tables = {("gold", "l3_customer_features"), ("access_analyst", "customer_features")}
    assert not [c for c in cols if c[2] in proxies and ((c[0], c[1]) in feature_tables or c[0] == "layer3")]
