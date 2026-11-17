-- Deposit accounts, one row per account. CIF padded to 10 digits (D-02); 'MITCHELL R J' names; YYYYMMDD DOB.
SELECT
    pad_cif(d.src_customer_ref)                AS source_id,
    d.src_customer_ref                         AS source_id_raw,
    d.deposit_account_id                       AS account_id,
    d.snapshot_date,
    d.holder_name_raw                          AS name_raw,
    {{ name_cols("parse_deposit_name(d.holder_name_raw)") }},
    d.holder_dob_raw                           AS dob_raw,
    parse_deposit_dob(d.holder_dob_raw)        AS dob,
    d.holder_phone_raw                         AS phone_raw,
    {{ phone_cols("full_phone(d.holder_phone_raw)") }},
    d.holder_postal_raw                        AS postal_raw,
    {{ postal_cols("norm_postal(d.holder_postal_raw)") }},
    d.joint_account_flag,
    pad_cif(d.joint_holder_ref)                AS joint_holder_cif,
    d.joint_holder_ref                         AS joint_holder_ref_raw,
    {{ lineage_cols('CORE_BANKING', 'd') }}
FROM {{ source('bronze', 'deposit_accounts') }} d
{{ geo_join("norm_postal(d.holder_postal_raw)") }}
