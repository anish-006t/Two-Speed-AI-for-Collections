{{ config(severity='warn') }}
-- D-38: every quarter-end snapshot date parses to a month end; any that do not are listed for the source owner.
SELECT month_end_date_format, count(*) AS n FROM {{ ref('account_monthly_snapshot') }}
WHERE month_end_date IS NULL GROUP BY 1
