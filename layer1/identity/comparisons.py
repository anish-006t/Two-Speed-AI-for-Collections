"""Splink comparison definitions (architecture §7 comparison table), with no Splink import.

Level order matters: Splink's gamma value for a pair is the position of the first matching level counted from the
bottom (ELSE = 0, null = -1). LEVEL_LABELS turns a gamma back into a plain label for review explanations.
"""


def lvl(sql, label, **kw):
    return {"sql_condition": sql, "label_for_charts": label, **kw}


def null_lvl(sql):
    return lvl(sql, "null", is_null_level=True)


ELSE = lvl("ELSE", "all other")

COMPARISONS = [
    {
        "output_column_name": "first_name",
        "comparison_description": "first name, initial or preferred name",
        "comparison_levels": [
            null_lvl("first_initial_l IS NULL OR first_initial_r IS NULL"),
            lvl("first_name_l = first_name_r", "exact"),
            lvl("preferred_name_l = first_name_r OR preferred_name_r = first_name_l", "matches preferred name"),
            lvl("jaro_winkler_similarity(first_name_l, first_name_r) >= 0.92", "jaro-winkler >= 0.92"),
            lvl("first_name_l = last_name_r AND last_name_l = first_name_r", "first and last swapped"),
            lvl("(first_name_l IS NULL OR first_name_r IS NULL) AND first_initial_l = first_initial_r",
                "initial matches (one side has an initial only)"),
            ELSE,
        ],
    },
    {
        "output_column_name": "last_name",
        "comparison_description": "surname, with term-frequency adjustment for common surnames",
        "comparison_levels": [
            null_lvl("last_name_l IS NULL OR last_name_r IS NULL"),
            lvl("last_name_l = last_name_r", "exact", tf_adjustment_column="last_name", tf_adjustment_weight=1.0),
            lvl("jaro_winkler_similarity(last_name_l, last_name_r) >= 0.92", "jaro-winkler >= 0.92"),
            lvl("first_name_l = last_name_r AND last_name_l = first_name_r", "first and last swapped"),
            ELSE,
        ],
    },
    {
        "output_column_name": "dob",
        "comparison_description": "date of birth",
        "comparison_levels": [
            null_lvl("dob_l IS NULL OR dob_r IS NULL"),
            lvl("dob_l = dob_r", "exact"),
            lvl("year(dob_l) = year(dob_r) AND month(dob_l) = day(dob_r) AND day(dob_l) = month(dob_r)",
                "day and month swapped"),
            lvl("(year(dob_l) = year(dob_r))::INT + (month(dob_l) = month(dob_r))::INT"
                " + (day(dob_l) = day(dob_r))::INT = 2", "one part differs"),
            ELSE,
        ],
    },
    {
        "output_column_name": "phone",
        "comparison_description": "phone: exact, last 7 digits, or consistent with a card mask",
        "comparison_levels": [
            null_lvl("phone_first6_l IS NULL OR phone_first6_r IS NULL"),
            lvl("phone_e164_l = phone_e164_r", "exact"),
            lvl("phone_last7_l = phone_last7_r", "last 7 digits equal"),
            lvl("(phone_masked_l OR phone_masked_r) AND phone_first6_l = phone_first6_r"
                " AND phone_last2_l = phone_last2_r", "consistent with card mask"),
            ELSE,
        ],
    },
    {
        "output_column_name": "postal_code",
        "comparison_description": "postal code",
        "comparison_levels": [
            null_lvl("postal_code_l IS NULL OR postal_code_r IS NULL"),
            lvl("postal_code_l = postal_code_r", "exact"),
            lvl("fsa_l = fsa_r", "same FSA"),
            ELSE,
        ],
    },
    {
        "output_column_name": "email",
        "comparison_description": "email (CRM pairs only)",
        "comparison_levels": [null_lvl("email_l IS NULL OR email_r IS NULL"), lvl("email_l = email_r", "exact"), ELSE],
    },
    {
        "output_column_name": "national_id_hash",
        "comparison_description": "national ID hash (CRM pairs only)",
        "comparison_levels": [
            null_lvl("national_id_hash_l IS NULL OR national_id_hash_r IS NULL"),
            lvl("national_id_hash_l = national_id_hash_r", "exact"),
            ELSE,
        ],
    },
    {
        "output_column_name": "pay_accounts",
        "comparison_description": "D3: loan PAD or card autopay from the other key's deposit account",
        "comparison_levels": [
            null_lvl("pay_accounts_l IS NULL OR pay_accounts_r IS NULL"),
            lvl("list_has_any(pay_accounts_l, pay_accounts_r)", "shares a payment account"),
            ELSE,
        ],
    },
]



def level_labels():
    """{comparison: {gamma: label}} for every comparison."""
    out = {}
    for c in COMPARISONS:
        levels = [lv for lv in c["comparison_levels"] if not lv.get("is_null_level")]
        n = len(levels)
        out[c["output_column_name"]] = {-1: "missing on one side",
                                        **{n - 1 - i: lv["label_for_charts"] for i, lv in enumerate(levels)}}
    return out


LEVEL_LABELS = level_labels()
