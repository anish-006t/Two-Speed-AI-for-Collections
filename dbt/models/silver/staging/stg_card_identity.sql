-- Card accounts, one row per account. Three name layouts (D-07), DD-MM-YYYY DOB, masked or full phone (D-04).
SELECT
    c.src_customer_ref                         AS source_id,
    c.card_account_id                          AS account_id,
    c.snapshot_date,
    c.cardholder_name_raw                      AS name_raw,
    {{ name_cols("parse_card_name(c.cardholder_name_raw)") }},
    c.cardholder_dob_raw                       AS dob_raw,
    parse_card_dob(c.cardholder_dob_raw)       AS dob,
    c.cardholder_phone_raw                     AS phone_raw,
    {{ phone_cols("card_phone(c.cardholder_phone_raw)") }},
    c.cardholder_postal_raw                    AS postal_raw,
    {{ postal_cols("norm_postal(c.cardholder_postal_raw)") }},
    c.autopay_source_account,
    {{ lineage_cols('CARDS', 'c') }}
FROM {{ source('bronze', 'card_accounts') }} c
{{ geo_join("norm_postal(c.cardholder_postal_raw)") }}
