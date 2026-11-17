-- Contract rule 7: when identity confidence is not high, verify_before_disclosure is true.
SELECT golden_customer_id FROM {{ ref('c360_customer') }}
WHERE identity_confidence <> 'high' AND NOT verify_before_disclosure
