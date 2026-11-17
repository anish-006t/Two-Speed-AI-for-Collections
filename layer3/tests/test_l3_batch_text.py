import duckdb

from layer3.features.batch_text import build_text_features
from layer3.text.extract import load_questions


def test_batch_reads_only_redacted_text_and_is_idempotent(monkeypatch):
    monkeypatch.setenv("L3_EXTRACTOR_MODE", "rules")
    con = duckdb.connect(":memory:")
    for schema in ("silver", "bronze", "identity", "gold"):
        con.execute(f"CREATE SCHEMA {schema}")
    con.execute("""CREATE TABLE silver.transcript_turns_redacted (
        transcript_id VARCHAR, crm_customer_id VARCHAR, turn_index INTEGER, text_redacted VARCHAR)""")
    con.execute("INSERT INTO silver.transcript_turns_redacted VALUES ('T-1','C-1',1,'I lost my job'),"
                "('T-1','C-1',2,'I will pay $50 Friday')")
    con.execute("""CREATE TABLE bronze.call_transcripts (
        transcript_id VARCHAR, case_id VARCHAR, call_start_ts TIMESTAMP)""")
    con.execute("INSERT INTO bronze.call_transcripts VALUES ('T-1','CASE-1',current_timestamp)")
    con.execute("""CREATE TABLE silver.agent_notes_redacted (
        note_id VARCHAR, crm_customer_id VARCHAR, case_id VARCHAR, note_ts TIMESTAMP, note_text_redacted VARCHAR)""")
    con.execute("INSERT INTO silver.agent_notes_redacted VALUES ('N-1','C-1','CASE-1',current_timestamp,'Will pay tomorrow')")
    con.execute("CREATE TABLE identity.crosswalk (source_key VARCHAR, golden_customer_id VARCHAR, valid_to TIMESTAMP)")
    con.execute("INSERT INTO identity.crosswalk VALUES ('CRM:C-1','G-1',NULL)")
    con.execute("CREATE TABLE gold.cases (case_id VARCHAR, outcome VARCHAR)")
    con.execute("INSERT INTO gold.cases VALUES ('CASE-1',NULL)")
    assert build_text_features(con) == 2
    assert build_text_features(con) == 2
    assert con.execute("SELECT count(*) FROM layer3.text_features").fetchone()[0] == 2
    columns = {row[1] for row in con.execute("PRAGMA table_info('layer3.text_features')").fetchall()}
    expected = {q["id"] for q in load_questions() if "transcript" in q["applies_to"] or "note" in q["applies_to"]}
    assert expected <= columns
    assert "text" not in columns and "note_text_redacted" not in columns
