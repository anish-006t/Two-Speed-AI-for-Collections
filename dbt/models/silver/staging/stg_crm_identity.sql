-- CRM rows, current only (decisions D-05: one version per ID, so in practice only is_deleted removes rows).
-- DOB comes from date_of_birth only; dob_raw mixes day-first and month-first dates and is never parsed (D-09).
SELECT
    c.crm_customer_id                                   AS source_id,
    NULL::VARCHAR                                       AS account_id,
    '{{ var("snapshot_date") }}'                          AS snapshot_date,
    concat_ws(' ', c.first_name, c.middle_name, c.last_name) AS name_raw,
    norm_name(c.first_name)                             AS first_name,
    left(norm_name(c.first_name), 1)                    AS first_initial,
    left(norm_name(c.middle_name), 1)                   AS middle_initial,
    norm_name(c.last_name)                              AS last_name,
    norm_suffix(c.name_suffix)                          AS name_suffix,
    norm_name(c.preferred_name)                         AS preferred_name,
    c.date_of_birth                                     AS dob_raw,
    parse_iso_dob(c.date_of_birth)                      AS dob,
    coalesce(c.primary_phone_e164, c.primary_phone_raw) AS phone_raw,
    {{ phone_cols("full_phone(coalesce(c.primary_phone_e164, c.primary_phone_raw))") }},
    to_e164(c.secondary_phone_raw)                      AS secondary_phone_e164,
    to_e164(c.work_phone_raw)                           AS work_phone_e164,
    c.postal_code                                       AS postal_raw,
    {{ postal_cols("norm_postal(c.postal_code)") }},
    nullif(lower(trim(c.email)), '')                    AS email,
    c.national_id_hash,
    c.duplicate_of_crm_id,
    c.last_profile_update_ts,
    {{ lineage_cols('CRM', 'c') }}
FROM {{ source('bronze', 'customers') }} c
{{ geo_join("norm_postal(c.postal_code)") }}
WHERE c.effective_to_ts LIKE '9999-12-31%' AND c.is_deleted = 'false'
