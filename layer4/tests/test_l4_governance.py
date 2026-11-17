"""Governance checks: word swaps, schema and output scans, report layout."""
import duckdb

from layer4 import governance as g


def test_swap_words_changes_gender_and_names_only():
    text = "Hi <PERSON>, is your husband with him? He said his son pays."
    male, female = g.swap_words(text, 0), g.swap_words(text, 1)
    assert male == "Hi James Miller, is your husband with him? He said his son pays."
    assert female == "Hi Wei Chen, is your wife with her? She said her daughter pays."
    assert g.swap_words("The amount is $400 by Friday.", 1) == "The amount is $400 by Friday."


def test_schema_scan_flags_protected_columns_and_allows_listed_ones():
    con = duckdb.connect()
    con.execute("CREATE SCHEMA layer3; CREATE SCHEMA layer2")
    con.execute("CREATE TABLE layer3.bad (golden_customer_id VARCHAR, gender_code VARCHAR, age_band VARCHAR, "
                "first_name VARCHAR, protected_attribute_mentioned BOOLEAN, province_code VARCHAR)")
    con.execute("CREATE TABLE layer2.ok (email_opened_flag BOOLEAN, agent_certified_vulnerable BOOLEAN)")
    hits = {h["column"] for h in g.schema_scan(con)}
    assert hits == {"layer3.bad.gender_code", "layer3.bad.age_band", "layer3.bad.first_name"}


def test_output_scan_finds_contact_details(tmp_path):
    (tmp_path / "a.csv").write_text("id,answer\n1,call 416-555-0199\n2,x@y.com\n")
    (tmp_path / "b.md").write_text("no contact details, case CS-2022-821260")
    assert g.output_scan(tmp_path) == [{"file": "a.csv", "kind": "email", "count": 1},
                                       {"file": "a.csv", "kind": "phone", "count": 1}]


def test_report_marks_wrong_refusals_and_rules_floor():
    cf = {"calls": 2, "changed": 0, "with_swaps": 2, "examples": [], "extractor": ["postcall-rules-1|rules:x"]}
    refused = [{"question": "By gender?", "kind": "protected", "refused": True, "route": "refuse"},
               {"question": "By FSA?", "kind": "proxy", "refused": False, "route": "metric_sql"},
               {"question": "How many?", "kind": "control", "refused": False, "route": "metric_sql"}]
    text = g.report(cf, [], [], refused, {"available": False, "reason": "no model"})
    assert "| By FSA? | proxy | no **(wrong)** |" in text and "| How many? | control | no |" in text
    assert "this run is a floor" in text and "1 of 2 protected or proxy questions refused" in text
