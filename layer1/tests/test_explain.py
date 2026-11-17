"""The LLM input for a review explanation must carry comparison results only, never record values (rule 4)."""
from layer1.identity import explain

PAIR = {
    "key_l": "CRM:AAA-0001", "key_r": "CARDS:CC0000001", "system_l": "CRM", "system_r": "CARDS",
    "first_name_l": "QUILLA", "last_name_l": "ZARNOW", "dob_l": "1980-05-17", "phone_e164_l": "+15552010199",
    "postal_code_l": "K1A 0B1",
    "gamma_first_name": 1, "gamma_last_name": 3, "gamma_dob": 3, "gamma_phone": 1, "gamma_postal_code": 2,
    "bf_first_name": 2.0, "bf_last_name": 64.0, "bf_dob": 128.0, "bf_phone": 4.0, "bf_postal_code": 30.0,
    "match_probability": 0.91, "constraint_flags": "",
}


def test_summary_has_no_values_or_keys():
    text = "\n".join(explain.comparison_summary(PAIR))
    for value in ["QUILLA", "ZARNOW", "1980", "5552010199", "K1A", "AAA-0001", "CC0000001"]:
        assert value not in text
    assert "dob: exact (weight +7.0)" in text
    assert "first name: initial matches" in text
    assert "phone: consistent with card mask" in text


def test_explanation_is_rule_based_and_value_free():
    text, source = explain.explain(PAIR)
    assert source == "rules"
    assert "last name exact" in text and "dob exact" in text
    for value in ["QUILLA", "ZARNOW", "1980", "5552010199", "K1A"]:
        assert value not in text


def test_household_pair_suggests_keep_apart():
    text, _ = explain.explain({**PAIR, "constraint_flags": "household"})
    assert "keep apart" in text
