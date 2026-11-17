-- Bureau subjects (external), one row per pull. 'MITCHELL, RYAN JAMES' names, ISO DOB, postal at the end of the
-- address string. bank_subject_ref is the 10-digit CIF (D-02).
SELECT
    pad_cif(e.bank_subject_ref)                AS source_id,
    e.bureau_request_id                        AS account_id,
    e.pull_date                                AS snapshot_date,
    e.subject_name_reported                    AS name_raw,
    {{ name_cols("parse_bureau_name(e.subject_name_reported)") }},
    e.subject_dob_reported                     AS dob_raw,
    parse_iso_dob(e.subject_dob_reported)      AS dob,
    NULL::VARCHAR                              AS phone_raw,
    NULL::VARCHAR                              AS phone_e164,
    NULL::VARCHAR                              AS phone_first6,
    NULL::VARCHAR                              AS phone_last2,
    NULL::BOOLEAN                              AS phone_masked,
    e.subject_address_reported                 AS postal_raw,
    {{ postal_cols("bureau_postal(e.subject_address_reported)") }},
    e.hit_status,
    {{ lineage_cols('BUREAU', 'e') }}
FROM {{ source('bronze', 'external') }} e
{{ geo_join("bureau_postal(e.subject_address_reported)") }}
