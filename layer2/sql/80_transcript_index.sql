-- Which recorded calls (transcripts) belong to which case, newest first. Text itself stays in the redacted silver table.
-- No call language: Layer 1's tags flag it as a possible proxy for origin, and Layer 2 does not need it.
CREATE OR REPLACE TABLE layer2.transcript_index AS
SELECT CAST(transcript_id AS VARCHAR) AS transcript_id, case_id, contact_id,
       try_cast(call_start_ts AS TIMESTAMP) AS call_start_ts, nullif(trim(direction), '') AS direction,
       try_cast(duration_sec AS INTEGER) AS duration_sec
FROM bronze.call_transcripts;
