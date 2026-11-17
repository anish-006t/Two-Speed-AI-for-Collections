-- Helpers for the safe views. Bronze keeps every column as text, so booleans and buckets are derived here.
CREATE OR REPLACE MACRO layer2.l2_bool(x) AS lower(trim(CAST(x AS VARCHAR))) IN ('true', 't', '1', 'yes', 'y');
-- Delinquency bucket from days past due (README: current, 1-30, ..., 151-180, 180+).
CREATE OR REPLACE MACRO layer2.bucket_from_dpd(d) AS CASE
    WHEN d IS NULL THEN NULL WHEN d <= 0 THEN 'current' WHEN d <= 30 THEN '1-30' WHEN d <= 60 THEN '31-60'
    WHEN d <= 90 THEN '61-90' WHEN d <= 120 THEN '91-120' WHEN d <= 150 THEN '121-150' WHEN d <= 180 THEN '151-180'
    ELSE '180+' END;
