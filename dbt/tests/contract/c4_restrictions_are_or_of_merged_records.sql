-- Contract rule 4: each restriction flag equals the OR of the same flag across the merged CRM records.
WITH expected AS (
    SELECT x.golden_customer_id,
           bool_or(c.cease_communication_flag = 'true') AS cease, bool_or(c.internal_do_not_call_flag = 'true') AS dnc,
           bool_or(c.deceased_flag = 'true') AS deceased, bool_or(c.bankruptcy_flag = 'true') AS bankrupt
    FROM {{ source('identity', 'crosswalk') }} x
    JOIN {{ source('bronze', 'customers') }} c ON x.source_system = 'CRM' AND c.crm_customer_id = x.source_id
    WHERE x.valid_to IS NULL GROUP BY 1
)
SELECT g.golden_customer_id FROM {{ ref('c360_customer') }} g JOIN expected e USING (golden_customer_id)
WHERE g.cease_communication_flag <> e.cease OR g.internal_do_not_call_flag <> e.dnc
   OR g.deceased_flag <> e.deceased OR g.bankruptcy_flag <> e.bankrupt
