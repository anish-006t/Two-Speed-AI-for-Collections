-- Loan accounts, one row per loan. 'Ryan Mitchell' names (accents stripped, D-08), YYYY/MM/DD DOB.
SELECT
    l.src_customer_ref                         AS source_id,
    l.loan_id                                  AS account_id,
    l.snapshot_date,
    l.borrower_name_raw                        AS name_raw,
    {{ name_cols("parse_loan_name(l.borrower_name_raw)") }},
    l.borrower_dob_raw                         AS dob_raw,
    parse_loan_dob(l.borrower_dob_raw)         AS dob,
    l.borrower_phone_raw                       AS phone_raw,
    {{ phone_cols("full_phone(l.borrower_phone_raw)") }},
    l.borrower_postal_raw                      AS postal_raw,
    {{ postal_cols("norm_postal(l.borrower_postal_raw)") }},
    l.co_borrower_flag,
    l.co_borrower_ref,
    l.pad_source_account,
    {{ lineage_cols('LENDING', 'l') }}
FROM {{ source('bronze', 'loan_accounts') }} l
{{ geo_join("norm_postal(l.borrower_postal_raw)") }}
