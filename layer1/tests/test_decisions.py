"""Constraint and decision-band tests (architecture §7; CLAUDE.md rules 1 and 2). Synthetic records only."""
import datetime as dt

import duckdb
import pytest

from layer1.identity import decisions

D = dt.date(1980, 5, 17)
BASE = dict(first_name="QUILLA", first_initial="Q", middle_initial=None, last_name="ZARNOW", name_suffix=None,
            preferred_name=None, dob=D, dob_conflict=False, phone_e164="+15552010199", phone_first6="555201",
            phone_last2="99", phone_masked=False, postal_code="K1A 0B1", fsa="K1A", email=None,
            national_id_hash=None)
GAMMA = dict(gamma_first_name=5, gamma_last_name=3, gamma_dob=3, gamma_phone=3, gamma_postal_code=2, gamma_email=-1,
             gamma_national_id_hash=-1, gamma_pay_accounts=-1)
BF = dict(bf_first_name=50.0, bf_last_name=100.0, bf_dob=200.0, bf_phone=500.0, bf_postal_code=20.0, bf_email=1.0,
          bf_national_id_hash=1.0, bf_pay_accounts=1.0, bf_tf_adj_last_name=1.0)


def insert_many(con, sql, rows):
    if rows:
        con.executemany(sql, list(rows))


def make_db(pairs, records, households=(), human=(), deterministic=()):
    con = duckdb.connect()
    con.execute("CREATE SCHEMA identity; CREATE SCHEMA silver;")
    cols = ["unique_id", "source_system"] + list(BASE)
    rows = []
    for key, over in records.items():
        r = {**BASE, **over, "unique_id": key, "source_system": key.split(":")[0]}
        rows.append([r[c] for c in cols])
    con.execute("""CREATE TABLE identity.splink_input (unique_id VARCHAR, source_system VARCHAR, first_name VARCHAR,
        first_initial VARCHAR, middle_initial VARCHAR, last_name VARCHAR, name_suffix VARCHAR, preferred_name VARCHAR,
        dob DATE, dob_conflict BOOLEAN, phone_e164 VARCHAR, phone_first6 VARCHAR, phone_last2 VARCHAR,
        phone_masked BOOLEAN, postal_code VARCHAR, fsa VARCHAR, email VARCHAR, national_id_hash VARCHAR)""")
    con.executemany(f"INSERT INTO identity.splink_input VALUES ({','.join('?' * len(cols))})", rows)
    pcols = ["unique_id_l", "unique_id_r", "match_probability", "match_weight"] + list(GAMMA) + list(BF)
    con.execute(f"CREATE TABLE identity.splink_predictions ({', '.join(c + ' DOUBLE' if c not in ('unique_id_l','unique_id_r') else c + ' VARCHAR' for c in pcols)})")
    for p in pairs:
        r = {**GAMMA, **BF, "match_weight": 10.0, **p}
        con.execute(f"INSERT INTO identity.splink_predictions VALUES ({','.join('?' * len(pcols))})", [r[c] for c in pcols])
    con.execute("""CREATE TABLE identity.deterministic_pairs (key_l VARCHAR, key_r VARCHAR, rule VARCHAR,
        dob_identical BOOLEAN, decision VARCHAR, reason VARCHAR)""")
    insert_many(con, "INSERT INTO identity.deterministic_pairs VALUES (?, ?, ?, ?, ?, ?)", deterministic)
    con.execute("""CREATE TABLE silver.household_refs (source_system VARCHAR, source_id VARCHAR,
        related_source_system VARCHAR, related_source_id VARCHAR, relation VARCHAR, account_id VARCHAR, resolves BOOLEAN)""")
    insert_many(con, "INSERT INTO silver.household_refs VALUES (?, ?, ?, ?, ?, ?, ?)", households)
    con.execute(decisions.PERSISTENT_DDL)
    for h in human:
        con.execute("INSERT INTO identity.review_decisions VALUES (?, ?, ?, ?, ?, now())", list(h))
    con.execute(decisions.MATCH_PAIRS_SQL)
    return con


def decision_of(con, a, b):
    return con.execute("SELECT decision, constraint_flags FROM identity.match_pairs WHERE key_l = ? AND key_r = ?",
                       [min(a, b), max(a, b)]).fetchone()


A, B = "CRM:AAA-0001", "LENDING:B00000001"


def test_high_probability_identical_dob_links():
    con = make_db([dict(unique_id_l=A, unique_id_r=B, match_probability=0.99)], {A: {}, B: {}})
    assert decision_of(con, A, B)[0] == "link"


@pytest.mark.parametrize("dob_r", [dt.date(1980, 5, 18), None])
def test_non_identical_or_missing_dob_never_auto_links(dob_r):
    con = make_db([dict(unique_id_l=A, unique_id_r=B, match_probability=0.999)], {A: {}, B: {"dob": dob_r}})
    assert decision_of(con, A, B)[0] == "review"


def test_dob_swap_with_strong_other_evidence_goes_to_review_even_below_band():
    con = make_db([dict(unique_id_l=A, unique_id_r=B, match_probability=0.5, gamma_dob=2, bf_dob=0.01,
                        match_weight=10.0)], {A: {}, B: {"dob": dt.date(1980, 5, 17)}})
    assert decision_of(con, A, B)[0] == "review"


def test_band_boundaries():
    recs = {A: {}, B: {}, "CARDS:CC0000001": {}}
    con = make_db([dict(unique_id_l=A, unique_id_r=B, match_probability=0.80),
                   dict(unique_id_l=A, unique_id_r="CARDS:CC0000001", match_probability=0.60)], recs)
    assert decision_of(con, A, B)[0] == "review"
    assert decision_of(con, A, "CARDS:CC0000001")[0] == "no_link"


def test_household_pair_is_never_linked():
    a, b = "CORE_BANKING:0000000001", "CORE_BANKING:0000000002"
    con = make_db([dict(unique_id_l=a, unique_id_r=b, match_probability=0.999)], {a: {}, b: {}},
                  households=[("CORE_BANKING", "0000000001", "CORE_BANKING", "0000000002", "joint_holder", "CHQ-1", True)])
    d, flags = decision_of(con, a, b)
    assert d == "no_link" and "household" in flags


@pytest.mark.parametrize("over_r, flag", [
    ({"name_suffix": "SR"}, "suffix_conflict"),
    ({"first_name": "ROBERT", "first_initial": "R"}, "lookalike"),
    ({"dob_conflict": True}, "dob_conflict"),
])
def test_constraints_block_auto_link(over_r, flag):
    over_l = {"name_suffix": "JR"} if flag == "suffix_conflict" else {}
    con = make_db([dict(unique_id_l=A, unique_id_r=B, match_probability=0.999)], {A: over_l, B: over_r})
    d, flags = decision_of(con, A, B)
    assert d == "review" and flag in flags


def test_possible_twins_go_to_review():
    con = make_db([dict(unique_id_l=A, unique_id_r=B, match_probability=0.999)],
                  {A: {}, B: {"first_name": "MARGOT", "first_initial": "M"}})
    d, flags = decision_of(con, A, B)
    assert d == "review" and "possible_twins" in flags


def test_human_decision_overrides_score():
    con = make_db([dict(unique_id_l=A, unique_id_r=B, match_probability=0.999)], {A: {}, B: {}},
                  human=[(A, B, "no_link", "reviewer1", "different people")])
    assert decision_of(con, A, B)[0] == "no_link"


def test_deterministic_pair_without_splink_score():
    a, b = "CORE_BANKING:0000000001", "BUREAU:0000000001"
    con = make_db([], {a: {}, b: {}}, deterministic=[(min(a, b), max(a, b), "D2", True, "link", None)])
    assert decision_of(con, a, b)[0] == "link"


def test_deterministic_sql_orders_keys():
    from layer1.identity import deterministic
    con = duckdb.connect()
    con.execute("""CREATE SCHEMA silver; CREATE TABLE silver.identity_input AS
        SELECT * FROM (VALUES ('CORE_BANKING:0000000001', 'CORE_BANKING', '0000000001', DATE '1980-05-17', NULL),
                              ('BUREAU:0000000001', 'BUREAU', '0000000001', DATE '1980-05-17', NULL),
                              ('CRM:AAA-0002', 'CRM', 'AAA-0002', DATE '1970-01-01', 'h1'),
                              ('CRM:AAA-0001', 'CRM', 'AAA-0001', DATE '1970-01-02', 'h1'))
        t(source_key, source_system, source_id, dob, national_id_hash)""")
    deterministic.run(con)
    rows = con.execute("SELECT key_l, key_r, rule, decision FROM identity.deterministic_pairs ORDER BY rule").fetchall()
    assert rows == [("CRM:AAA-0001", "CRM:AAA-0002", "D1", "review"),          # same ID hash, DOBs differ
                    ("BUREAU:0000000001", "CORE_BANKING:0000000001", "D2", "link")]


def test_gamma_constants_match_comparison_levels():
    from layer1.identity.comparisons import LEVEL_LABELS
    assert LEVEL_LABELS["dob"][decisions.GAMMA_DOB_SWAP] == "day and month swapped"
    assert LEVEL_LABELS["dob"][decisions.GAMMA_DOB_ONE_PART] == "one part differs"
    assert LEVEL_LABELS["phone"][decisions.GAMMA_PHONE_MASK] == "consistent with card mask"
    assert LEVEL_LABELS["first_name"][decisions.GAMMA_FIRST_INITIAL_ONLY].startswith("initial matches")


def test_swapped_first_and_last_name_is_not_a_lookalike():
    con = make_db([dict(unique_id_l=A, unique_id_r=B, match_probability=0.999, gamma_first_name=2, gamma_last_name=1)],
                  {A: {}, B: {"first_name": "ZARNOW", "first_initial": "Z", "last_name": "QUILLA"}})
    d, flags = decision_of(con, A, B)
    assert d == "link" and "lookalike" not in flags
