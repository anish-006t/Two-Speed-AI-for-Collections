"""Tests for the silver parser macros in dbt/macros/parsers.sql.

The macros are loaded from the dbt file itself, so the tests run the exact SQL the pipeline runs.
All values here are invented; none come from the release.
"""
import datetime as dt
import re
from pathlib import Path

import duckdb
import pytest

PARSERS = Path(__file__).resolve().parents[2] / "dbt" / "macros" / "parsers.sql"


def parser_sql() -> str:
    text = PARSERS.read_text()
    text = re.sub(r"\{#.*?#\}", "", text, flags=re.S)
    body = re.search(r"\{%\s*macro create_parsers\(\)\s*%\}(.*)\{%\s*endmacro\s*%\}", text, flags=re.S)
    assert body, "create_parsers macro not found"
    assert "{{" not in body.group(1) and "{%" not in body.group(1), "parser SQL must stay free of Jinja"
    return body.group(1)


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect()
    c.execute(parser_sql())
    yield c
    c.close()


def one(con, expr, *params):
    return con.execute(f"SELECT {expr}", list(params)).fetchone()[0]


# ---------- names ----------

@pytest.mark.parametrize("raw, expected", [
    ("Zoë O'Neil-Brandt", "ZOE ONEIL BRANDT"),
    ("  jean   luc ", "JEAN LUC"),
    ("St. Pierre", "ST PIERRE"),
    ("...", None),
    (None, None),
])
def test_norm_name(con, raw, expected):
    assert one(con, "norm_name(?)", raw) == expected


@pytest.mark.parametrize("raw, expected", [("Jr.", "JR"), ("sr", "SR"), ("III", "III"), ("", None), (None, None), ("Dr", None)])
def test_norm_suffix(con, raw, expected):
    assert one(con, "norm_suffix(?)", raw) == expected


def name(con, fn, raw):
    r = one(con, f"{fn}(?)", raw)
    return None if r is None else (r["first_name"], r["first_initial"], r["middle_initial"], r["last_name"], r["name_suffix"])


@pytest.mark.parametrize("raw, expected", [
    ("Q. ZARNOW", (None, "Q", None, "ZARNOW", None)),            # initial + last: thin record, no first name
    ("Q.ZARNOW", (None, "Q", None, "ZARNOW", None)),
    ("QUILLA ZARNOW", ("QUILLA", "Q", None, "ZARNOW", None)),      # first + last
    ("ZARNOW/QUILLA", ("QUILLA", "Q", None, "ZARNOW", None)),      # last/first
    ("ZARNOW-OBI/QUILLA", ("QUILLA", "Q", None, "ZARNOW OBI", None)),
    ("Q. ZARNOW JR", (None, "Q", None, "ZARNOW", "JR")),
    ("QUILLA", None),                                              # one token: unparseable, goes to quarantine
    (None, None),
])
def test_parse_card_name(con, raw, expected):
    assert name(con, "parse_card_name", raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("Quilla Zarnow", ("QUILLA", "Q", None, "ZARNOW", None)),
    ("Andrée Zarnow", ("ANDREE", "A", None, "ZARNOW", None)),      # accent stripped
    ("QUILLA VAN ZARNOW", ("QUILLA", "Q", None, "VAN ZARNOW", None)),
    ("Quilla Zarnow Sr.", ("QUILLA", "Q", None, "ZARNOW", "SR")),
    ("Quilla", None),
])
def test_parse_loan_name(con, raw, expected):
    assert name(con, "parse_loan_name", raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("ZARNOW Q J", (None, "Q", "J", "ZARNOW", None)),
    ("ZARNOW Q", (None, "Q", None, "ZARNOW", None)),
    ("VAN ZARNOW Q J", (None, "Q", "J", "VAN ZARNOW", None)),
    ("O'ZARNOW Q", (None, "Q", None, "OZARNOW", None)),
    ("ZARNOW-OBI Q J", (None, "Q", "J", "ZARNOW OBI", None)),
    ("ZARNOW", None),
    ("ZARNOW QUILLA", None),                                       # not the deposit layout: quarantine, don't guess
])
def test_parse_deposit_name(con, raw, expected):
    assert name(con, "parse_deposit_name", raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("ZARNOW, QUILLA JUNE", ("QUILLA", "Q", "J", "ZARNOW", None)),
    ("ZARNOW, QUILLA", ("QUILLA", "Q", None, "ZARNOW", None)),
    ("DE ZARNOW, QUILLA-MAE JUNE", ("QUILLA MAE", "Q", "J", "DE ZARNOW", None)),
    ("ZARNOW QUILLA", None),
])
def test_parse_bureau_name(con, raw, expected):
    assert name(con, "parse_bureau_name", raw) == expected


# ---------- dates of birth: each source has one format; anything else is NULL (quarantine) ----------

D = dt.date


@pytest.mark.parametrize("fn, raw, expected", [
    ("parse_card_dob", "07-03-1981", D(1981, 3, 7)),       # DD-MM-YYYY
    ("parse_card_dob", "25-12-1990", D(1990, 12, 25)),
    ("parse_card_dob", "12-25-1990", None),                # month 25: wrong format, never swapped
    ("parse_card_dob", "1990-12-25", None),
    ("parse_card_dob", "7-3-1981", None),
    ("parse_card_dob", "31-02-1981", None),                # impossible date
    ("parse_loan_dob", "1981/03/07", D(1981, 3, 7)),       # YYYY/MM/DD
    ("parse_loan_dob", "1981/25/12", None),
    ("parse_loan_dob", "07/03/1981", None),
    ("parse_deposit_dob", "19810307", D(1981, 3, 7)),      # YYYYMMDD
    ("parse_deposit_dob", "19812503", None),
    ("parse_deposit_dob", "1981037", None),
    ("parse_iso_dob", "1981-03-07", D(1981, 3, 7)),        # CRM date_of_birth and bureau
    ("parse_iso_dob", "03/07/1981", None),                 # CRM dob_raw style is never accepted (D-09)
    ("parse_iso_dob", "Mar 07 1981", None),
    ("parse_iso_dob", "1890-01-01", None),                 # before 1900: out of range
    ("parse_iso_dob", "2027-01-01", None),                 # after the snapshot date
    ("parse_iso_dob", None, None),
])
def test_dob_parsers(con, fn, raw, expected):
    assert one(con, f"{fn}(?)", raw) == expected


# ---------- phones ----------

@pytest.mark.parametrize("raw, expected", [
    ("+1 (555) 201 0199", "+15552010199"),
    ("555-201-0199", "+15552010199"),
    ("(555) 201-0199", "+15552010199"),
    ("555.201.0199", "+15552010199"),
    ("5552010199", "+15552010199"),
    ("+1 555 201 0199", "+15552010199"),
    ("555-201-0199 x412", "+15552010199"),               # work phone extension dropped
    ("555-201-019", None),                               # 9 digits: invalid
    ("25552010199", None),                               # 11 digits not starting with 1
    (None, None),
])
def test_to_e164(con, raw, expected):
    assert one(con, "to_e164(?)", raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("555201xxx99", (None, "555201", "99", True)),
    ("555201XXX99", (None, "555201", "99", True)),
    ("5552010199", ("+15552010199", "555201", "99", False)),
    ("555201xx99", None),
    (None, None),
])
def test_card_phone(con, raw, expected):
    r = one(con, "card_phone(?)", raw)
    assert (None if r is None else (r["phone_e164"], r["phone_first6"], r["phone_last2"], r["phone_masked"])) == expected


def test_phone_hash_is_md5_of_ten_digits(con):
    import hashlib
    assert one(con, "phone_hash('+15552010199')") == hashlib.md5(b"5552010199").hexdigest()


# ---------- postal codes ----------

@pytest.mark.parametrize("raw, expected", [
    ("K1A0B1", "K1A 0B1"),
    ("k1a 0b1", "K1A 0B1"),
    (" K1A 0B1 ", "K1A 0B1"),
    ("KIA 0B1", None),        # letter where a digit belongs: never auto-corrected in silver
    ("K1A 0B", None),
    ("12345", None),
    (None, None),
])
def test_norm_postal(con, raw, expected):
    assert one(con, "norm_postal(?)", raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("12 Elm St, Ottawa ON CA K1A0B1", "K1A 0B1"),
    ("12 Elm St, Ottawa ON K1A 0B1", "K1A 0B1"),
    ("12 Elm St, Ottawa ON", None),
    (None, None),
])
def test_bureau_postal(con, raw, expected):
    assert one(con, "bureau_postal(?)", raw) == expected


# ---------- keys ----------

@pytest.mark.parametrize("raw, expected", [
    ("1234567890", "1234567890"),
    ("34567890", "0034567890"),      # 8 digits: lost two leading zeros (D-02)
    ("4567890", "0004567890"),
    ("12345678901", None),
    ("12A4567890", None),
    (None, None),
])
def test_pad_cif(con, raw, expected):
    assert one(con, "pad_cif(?)", raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ('["C-1001","L-2002"]', ["C-1001", "L-2002"]),
    ('["CHQ-3003"]', ["CHQ-3003"]),
    ("C-1001", ["C-1001"]),          # bare ID (D-13)
    ("[]", []),
    ("not an id", None),
    ("", None),
    (None, None),
])
def test_parse_account_ids(con, raw, expected):
    assert one(con, "parse_account_ids(?)", raw) == expected


# ---------- delinquency buckets and month-end dates (D-38) ----------

@pytest.mark.parametrize("dpd, expected", [
    (0, "current"), (1, "1-30"), (30, "1-30"), (31, "31-60"), (90, "61-90"), (91, "91-120"),
    (150, "121-150"), (180, "151-180"), (181, "180+"), (None, None), (-1, None),
])
def test_dpd_to_bucket(con, dpd, expected):
    assert one(con, "dpd_to_bucket(?::INTEGER)", dpd) == expected


@pytest.mark.parametrize("raw, expected, fmt", [
    ("2026-03-31", D(2026, 3, 31), "YYYY-MM-DD"),
    ("31/03/2026", D(2026, 3, 31), "DD/MM/YYYY"),
    ("20260331", D(2026, 3, 31), "YYYYMMDD"),
    ("28/02/2025", D(2025, 2, 28), "DD/MM/YYYY"),
    ("29/02/2024", D(2024, 2, 29), "DD/MM/YYYY"),       # leap year month end
    ("03/31/2026", D(2026, 3, 31), "MM/DD/YYYY"),        # month-first also occurs; only one order is a month end
    ("06/30/2026", D(2026, 6, 30), "MM/DD/YYYY"),
    ("01/02/2026", None, "unknown"),                     # neither order is a month end: rejected, not guessed
    ("2026-03-30", None, "YYYY-MM-DD"),                  # not a month end
    ("30/06/26", None, "unknown"),
    (None, None, "unknown"),
])
def test_parse_month_end(con, raw, expected, fmt):
    assert one(con, "parse_month_end(?)", raw) == expected
    assert one(con, "month_end_format(?)", raw) == fmt
