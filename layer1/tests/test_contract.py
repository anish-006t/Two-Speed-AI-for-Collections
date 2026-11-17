"""The data contract (deliverables/data_contract_c360.yaml) matches the build and the dbt tests (brief L1-6, D-42)."""
import duckdb
import pytest
import yaml

from layer1.governance import contract
from layer1.pipeline import config

DOC = yaml.safe_load(contract.OUT.read_text())


def test_required_sections():
    for key in ["data_contract", "owner", "grain", "refresh", "schema", "quality_rules", "allowed_uses",
                "not_allowed_uses", "access", "lineage", "change_policy"]:
        assert DOC.get(key), key


def test_every_quality_rule_points_at_an_existing_test_with_the_right_severity():
    for q in DOC["quality_rules"]:
        assert q["severity"] in ("blocking", "warning")
        t = q["enforced_by"]
        if t.startswith("schema.yml"):
            assert "golden_customer_id" in (config.ROOT / "dbt" / "models" / "gold" / "schema.yml").read_text()
            continue
        f = contract.TESTS / f"{t}.sql"
        assert f.exists(), t
        assert q["severity"] == contract.severity(t)


def test_no_protected_or_aml_column_in_the_schema():
    assert not [c["name"] for c in DOC["schema"] if c["sensitivity"] in ("Protected attribute", "Restricted (AML)")]
    assert not [c["name"] for c in DOC["schema"] if "vulnerab" in c["name"] or c["name"] in ("age", "age_band")]


@pytest.mark.skipif(not config.DEV_DB.exists(), reason="needs a built database")
def test_schema_matches_the_built_table():
    con = duckdb.connect(str(config.DEV_DB), read_only=True)
    built = [(n, t) for n, t, *_ in con.execute("DESCRIBE gold.c360_customer").fetchall()]
    assert [(c["name"], c["type"]) for c in DOC["schema"]] == built


def test_dq_report_every_issue_has_a_count_and_a_handling_line():
    from layer1.quality import report
    text = report.OUT.read_text()
    rows = [line for line in text.splitlines() if line.startswith("| ") and line.split("|")[1].strip().isdigit()]
    assert len(rows) >= 25
    for line in rows:
        cells = [c.strip() for c in line.split("|")[1:-1]]
        assert cells[3].replace(",", "").isdigit(), line[:80]     # records affected
        assert len(cells[4]) > 10, line[:80]                      # how it was handled
    assert "ops_events does not list" in text
