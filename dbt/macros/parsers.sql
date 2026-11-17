{#
  Per-source parsers for silver (architecture §6, decisions.md D-02, D-04, D-07 to D-10).
  The body between the macro tags is plain DuckDB SQL with no Jinja, so tests/test_parsers.py runs this exact text.
  Every parser returns NULL when a value does not fit its source's known format; models send those rows to
  silver.quarantine. Day and month are never guessed (CLAUDE.md rule 8).
#}
{% macro create_parsers() %}
-- ---------- names ----------
-- Upper-case, strip accents, drop apostrophes and periods, turn other punctuation into spaces, collapse spaces.
CREATE OR REPLACE MACRO norm_name(s) AS
    nullif(trim(regexp_replace(
        regexp_replace(regexp_replace(upper(strip_accents(s)), '[''’.]', '', 'g'), '[^A-Z ]', ' ', 'g'),
        ' +', ' ', 'g')), '');

-- Generational suffix as a separate column (JR, SR, II, III, IV).
CREATE OR REPLACE MACRO norm_suffix(s) AS
    CASE upper(regexp_replace(coalesce(s, ''), '[^A-Za-z]', '', 'g'))
        WHEN 'JR' THEN 'JR' WHEN 'SR' THEN 'SR' WHEN 'II' THEN 'II' WHEN 'III' THEN 'III' WHEN 'IV' THEN 'IV'
        ELSE NULL END;
CREATE OR REPLACE MACRO trailing_suffix(n) AS
    CASE WHEN regexp_matches(n, ' (JR|SR|II|III|IV)$') THEN regexp_extract(n, ' (JR|SR|II|III|IV)$', 1) END;
CREATE OR REPLACE MACRO strip_suffix(n) AS
    nullif(trim(regexp_replace(n, ' (JR|SR|II|III|IV)$', '')), '');

CREATE OR REPLACE MACRO name_struct(first_raw, last_raw, middle_raw, initial_only) AS
    struct_pack(
        first_name     := CASE WHEN initial_only THEN NULL ELSE norm_name(first_raw) END,
        first_initial  := left(norm_name(first_raw), 1),
        middle_initial := left(norm_name(middle_raw), 1),
        last_name      := strip_suffix(norm_name(last_raw)),
        name_suffix    := trailing_suffix(norm_name(last_raw))
    );

-- Cards (D-07): 'R. MITCHELL' (initial + last), 'MITCHELL/RYAN' (last/first), 'RYAN MITCHELL' (first + last).
CREATE OR REPLACE MACRO parse_card_name(s) AS
    CASE
        WHEN regexp_full_match(trim(s), '[A-Za-z]\. ?\S.*')
            THEN name_struct(regexp_extract(trim(s), '^([A-Za-z])\.', 1),
                             regexp_extract(trim(s), '^[A-Za-z]\. ?(.+)$', 1), NULL, true)
        WHEN regexp_full_match(trim(s), '[^/]+/[^/]+')
            THEN name_struct(regexp_extract(trim(s), '/(.+)$', 1), regexp_extract(trim(s), '^([^/]+)/', 1), NULL, false)
        WHEN regexp_full_match(trim(s), '\S+ \S.*')
            THEN name_struct(regexp_extract(trim(s), '^(\S+) ', 1), regexp_extract(trim(s), '^\S+ (.+)$', 1), NULL, false)
        ELSE NULL END;

-- Loans: 'Ryan Mitchell' (first + rest). Reversed order is left to Splink's swap level (D-08), never guessed here.
CREATE OR REPLACE MACRO parse_loan_name(s) AS
    CASE WHEN regexp_full_match(trim(s), '\S+ \S.*')
        THEN name_struct(regexp_extract(trim(s), '^(\S+) ', 1), regexp_extract(trim(s), '^\S+ (.+)$', 1), NULL, false)
    END;

-- Deposits: 'MITCHELL R J' (last, first initial, optional middle initial).
CREATE OR REPLACE MACRO parse_deposit_name(s) AS
    CASE WHEN regexp_full_match(trim(s), '.+ [A-Za-z]( [A-Za-z])?')
        THEN name_struct(regexp_extract(trim(s), '^.+? ([A-Za-z])( [A-Za-z])?$', 1),
                         regexp_extract(trim(s), '^(.+?) [A-Za-z]( [A-Za-z])?$', 1),
                         nullif(trim(regexp_extract(trim(s), '^.+? [A-Za-z]( [A-Za-z])?$', 1)), ''), true)
    END;

-- Bureau: 'MITCHELL, RYAN JAMES' (last, first, middle).
CREATE OR REPLACE MACRO parse_bureau_name(s) AS
    CASE WHEN regexp_full_match(trim(s), '[^,]+, ?\S.*')
        THEN name_struct(regexp_extract(trim(s), ',\s*(\S+)', 1), regexp_extract(trim(s), '^([^,]+),', 1),
                         nullif(regexp_extract(trim(s), ',\s*\S+\s+(\S+)', 1), ''), false)
    END;

-- ---------- dates of birth (one fixed format per source, D-09 / D-10) ----------
CREATE OR REPLACE MACRO dob_in_range(d) AS
    CASE WHEN d BETWEEN DATE '1900-01-01' AND DATE '2026-09-28' THEN d END;
CREATE OR REPLACE MACRO parse_iso_dob(s) AS
    CASE WHEN regexp_full_match(trim(s), '\d{4}-\d{2}-\d{2}')
        THEN dob_in_range(try_strptime(trim(s), '%Y-%m-%d')::DATE) END;
CREATE OR REPLACE MACRO parse_card_dob(s) AS          -- DD-MM-YYYY
    CASE WHEN regexp_full_match(trim(s), '\d{2}-\d{2}-\d{4}')
        THEN dob_in_range(try_strptime(trim(s), '%d-%m-%Y')::DATE) END;
CREATE OR REPLACE MACRO parse_loan_dob(s) AS          -- YYYY/MM/DD
    CASE WHEN regexp_full_match(trim(s), '\d{4}/\d{2}/\d{2}')
        THEN dob_in_range(try_strptime(trim(s), '%Y/%m/%d')::DATE) END;
CREATE OR REPLACE MACRO parse_deposit_dob(s) AS       -- YYYYMMDD
    CASE WHEN regexp_full_match(trim(s), '\d{8}')
        THEN dob_in_range(try_strptime(trim(s), '%Y%m%d')::DATE) END;

-- ---------- phones ----------
-- Any North American number to E.164. An extension ('x123', 'ext 4') is dropped; 10 digits, or 11 starting with 1.
CREATE OR REPLACE MACRO phone_digits(s) AS
    regexp_replace(regexp_replace(lower(trim(s)), '\s*(x|ext\.?)\s*\d+$', ''), '[^0-9]', '', 'g');
CREATE OR REPLACE MACRO to_e164(s) AS
    CASE
        WHEN length(phone_digits(s)) = 10 THEN '+1' || phone_digits(s)
        WHEN length(phone_digits(s)) = 11 AND left(phone_digits(s), 1) = '1' THEN '+' || phone_digits(s)
        ELSE NULL END;
-- Card phones (D-04): '519555xxx42' is masked (first 6 and last 2 digits visible); 10 digits is a full number.
CREATE OR REPLACE MACRO card_phone(s) AS
    CASE
        WHEN regexp_full_match(trim(s), '\d{6}[xX*#]{3}\d{2}')
            THEN struct_pack(phone_e164 := NULL::VARCHAR, phone_first6 := left(trim(s), 6),
                             phone_last2 := right(trim(s), 2), phone_masked := true)
        WHEN to_e164(s) IS NOT NULL
            THEN struct_pack(phone_e164 := to_e164(s), phone_first6 := substr(to_e164(s), 3, 6),
                             phone_last2 := right(to_e164(s), 2), phone_masked := false)
        ELSE NULL END;
-- Same shape as card_phone for sources whose phones are never masked.
CREATE OR REPLACE MACRO full_phone(s) AS
    CASE WHEN to_e164(s) IS NOT NULL
        THEN struct_pack(phone_e164 := to_e164(s), phone_first6 := substr(to_e164(s), 3, 6),
                         phone_last2 := right(to_e164(s), 2), phone_masked := false)
    END;
-- md5 of the 10-digit national number: the method behind contact_history.phone_number_hash (D-06).
CREATE OR REPLACE MACRO phone_hash(e164) AS md5(right(e164, 10));

-- ---------- postal codes ----------
CREATE OR REPLACE MACRO norm_postal(s) AS
    CASE WHEN regexp_full_match(upper(replace(trim(s), ' ', '')), '[A-Z]\d[A-Z]\d[A-Z]\d')
        THEN left(upper(replace(trim(s), ' ', '')), 3) || ' ' || right(upper(replace(trim(s), ' ', '')), 3) END;
-- Bureau address string ends with the postal code, with or without a space.
CREATE OR REPLACE MACRO bureau_postal(addr) AS
    norm_postal(regexp_extract(upper(trim(addr)), '([A-Z]\d[A-Z] ?\d[A-Z]\d)$', 1));

-- ---------- keys ----------
-- Deposit CIF lost its leading zeros (D-02): pad to 10 digits; anything that is not 1-10 digits is invalid.
CREATE OR REPLACE MACRO pad_cif(s) AS
    CASE WHEN regexp_full_match(trim(s), '\d{1,10}') THEN lpad(trim(s), 10, '0') END;
-- collections_cases.account_ids: a JSON array, or (about 5%) one bare ID (D-13).
CREATE OR REPLACE MACRO parse_account_ids(s) AS
    CASE
        WHEN s IS NULL OR trim(s) = '' THEN NULL
        WHEN json_valid(trim(s)) AND left(trim(s), 1) = '['
            THEN list_filter(list_transform(from_json(trim(s), '["VARCHAR"]'), x -> trim(x)), x -> x <> '')
        WHEN regexp_full_match(trim(s), '[A-Z]+-\d+') THEN [trim(s)]
        ELSE NULL END;

-- ---------- delinquency and snapshot dates (benchmark traps, decisions D-38) ----------
-- Bucket from days past due, using the release's buckets; NULL when DPD is missing (never inferred from cycles).
CREATE OR REPLACE MACRO dpd_to_bucket(d) AS
    CASE WHEN d IS NULL OR d < 0 THEN NULL
         WHEN d = 0 THEN 'current' WHEN d <= 30 THEN '1-30' WHEN d <= 60 THEN '31-60' WHEN d <= 90 THEN '61-90'
         WHEN d <= 120 THEN '91-120' WHEN d <= 150 THEN '121-150' WHEN d <= 180 THEN '151-180' ELSE '180+' END;
-- account_monthly_snapshot.month_end_date arrives as YYYY-MM-DD, YYYYMMDD, DD/MM/YYYY or MM/DD/YYYY (both slash
-- orders occur). Every value must be the last day of its month. A month-end day is 28-31 and can never be a month, so
-- exactly one slash reading is a valid month end: the order is read from the value, not guessed (rule 8).
CREATE OR REPLACE MACRO me_if_month_end(d) AS CASE WHEN d = last_day(d) THEN d END;
CREATE OR REPLACE MACRO parse_month_end(s) AS
    CASE
        WHEN regexp_full_match(trim(s), '\d{4}-\d{2}-\d{2}') THEN me_if_month_end(try_strptime(trim(s), '%Y-%m-%d')::DATE)
        WHEN regexp_full_match(trim(s), '\d{8}') THEN me_if_month_end(try_strptime(trim(s), '%Y%m%d')::DATE)
        WHEN regexp_full_match(trim(s), '\d{2}/\d{2}/\d{4}') THEN coalesce(
            me_if_month_end(try_strptime(trim(s), '%d/%m/%Y')::DATE),
            me_if_month_end(try_strptime(trim(s), '%m/%d/%Y')::DATE))
    END;
CREATE OR REPLACE MACRO month_end_format(s) AS
    CASE WHEN regexp_full_match(trim(s), '\d{4}-\d{2}-\d{2}') THEN 'YYYY-MM-DD'
         WHEN regexp_full_match(trim(s), '\d{8}') THEN 'YYYYMMDD'
         WHEN regexp_full_match(trim(s), '\d{2}/\d{2}/\d{4}') THEN
             CASE WHEN me_if_month_end(try_strptime(trim(s), '%d/%m/%Y')::DATE) IS NOT NULL THEN 'DD/MM/YYYY'
                  WHEN me_if_month_end(try_strptime(trim(s), '%m/%d/%Y')::DATE) IS NOT NULL THEN 'MM/DD/YYYY'
                  ELSE 'unknown' END
         ELSE 'unknown' END;
{% endmacro %}
