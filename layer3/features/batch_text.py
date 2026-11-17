"""Batch typed text extraction into ``layer3.text_features``.

Only redacted silver text is read.  Transcript turns are grouped in order;
notes are limited to open cases and the preceding 12 months by default.
"""
from __future__ import annotations

import argparse
from datetime import datetime

import pandas as pd

from layer3.text.extract import extract, load_questions
from shared.db import connect


META_COLUMNS = {"source_type": "VARCHAR", "source_id": "VARCHAR", "golden_customer_id": "VARCHAR",
                "case_id": "VARCHAR", "event_ts": "TIMESTAMP", "available_at": "TIMESTAMP",
                "model_version": "VARCHAR", "extracted_at": "TIMESTAMP"}
NUMERIC_EXTRACTS = {"promise_amount"}


def text_schema() -> dict:
    """Column -> DuckDB type for layer3.text_features, derived from the question registry (one place)."""
    cols = dict(META_COLUMNS)
    for q in load_questions():
        qid = q["id"]
        if q["type"] == "yes_no":
            kind = "BOOLEAN"
        elif q["type"] == "score" or qid in NUMERIC_EXTRACTS:
            kind = "DOUBLE"
        else:
            kind = "VARCHAR"
        cols.update({qid: kind, f"{qid}__prob": "DOUBLE", f"{qid}__evidence": "VARCHAR",
                     f"{qid}__model_version": "VARCHAR"})
    return cols


def ensure_table(con) -> None:
    """Create layer3.text_features with the registry's typed schema, adding any column a newer registry needs."""
    con.execute("CREATE SCHEMA IF NOT EXISTS layer3")
    schema = text_schema()
    con.execute("CREATE TABLE IF NOT EXISTS layer3.text_features ("
                + ", ".join(f'"{c}" {t}' for c, t in schema.items()) + ")")
    have = {r[0] for r in con.execute("SELECT column_name FROM information_schema.columns "
                                      "WHERE table_schema = 'layer3' AND table_name = 'text_features'").fetchall()}
    for c, t in schema.items():
        if c not in have:
            con.execute(f'ALTER TABLE layer3.text_features ADD COLUMN "{c}" {t}')


def _flatten(meta: dict, answers: dict) -> dict:
    row = dict(meta)
    versions = set()
    for qid, answer in answers.items():
        row[qid] = answer["answer"]
        row[f"{qid}__prob"] = answer["prob"]
        row[f"{qid}__evidence"] = answer["evidence"]
        row[f"{qid}__model_version"] = answer["model_version"]
        versions.add(answer["model_version"])
    row["model_version"] = ";".join(sorted(versions))
    # Known the day after the call or note, the same convention as the other history tables (point in time).
    if row.get("event_ts") is not None:
        row["available_at"] = pd.Timestamp(row["event_ts"]) + pd.Timedelta(days=1)
    else:
        row["available_at"] = None
    row["extracted_at"] = datetime.utcnow()
    return row


def source_rows(con, note_months: int = 12, limit: int | None = None):
    suffix = f" LIMIT {int(limit)}" if limit else ""
    transcripts = con.execute("""
        SELECT 'transcript' source_type, t.transcript_id source_id, x.golden_customer_id,
               c.case_id, try_cast(c.call_start_ts AS TIMESTAMP) event_ts,
               string_agg(t.text_redacted, '\n' ORDER BY t.turn_index) source_text
        FROM silver.transcript_turns_redacted t
        LEFT JOIN bronze.call_transcripts c ON c.transcript_id = t.transcript_id
        LEFT JOIN identity.crosswalk x ON x.source_key = 'CRM:' || t.crm_customer_id AND x.valid_to IS NULL
        GROUP BY t.transcript_id, x.golden_customer_id, c.case_id, c.call_start_ts
        ORDER BY t.transcript_id""" + suffix).fetchall()
    notes = con.execute(f"""
        SELECT 'note' source_type, n.note_id source_id, x.golden_customer_id, n.case_id,
               try_cast(n.note_ts AS TIMESTAMP) event_ts, n.note_text_redacted source_text
        FROM silver.agent_notes_redacted n
        JOIN gold.cases c ON c.case_id = n.case_id AND c.outcome IS NULL
        LEFT JOIN identity.crosswalk x ON x.source_key = 'CRM:' || n.crm_customer_id AND x.valid_to IS NULL
        WHERE try_cast(n.note_ts AS TIMESTAMP) >= (SELECT max(try_cast(note_ts AS TIMESTAMP)) FROM silver.agent_notes_redacted)
                                                   - INTERVAL '{int(note_months)} months'
        ORDER BY n.note_id""" + suffix).fetchall()
    columns = ["source_type", "source_id", "golden_customer_id", "case_id", "event_ts", "text"]
    return (dict(zip(columns, row)) for row in [*transcripts, *notes])


def build_text_features(con=None, note_months: int = 12, limit: int | None = None) -> int:
    con = con or connect(read_only=False)
    rows = []
    for source in source_rows(con, note_months=note_months, limit=limit):
        text = source.pop("text") or ""
        rows.append(_flatten(source, extract(text, source["source_type"])))
    if not rows:
        return 0
    frame = pd.DataFrame(rows)
    schema = text_schema()
    for column in schema:                       # every row has every column, even if a question didn't apply
        if column not in frame:
            frame[column] = None
    frame = frame[list(schema)].astype(object).where(frame[list(schema)].notna(), None)
    con.register("l3_text_frame", frame)
    try:
        ensure_table(con)
        con.execute("DELETE FROM layer3.text_features WHERE (source_type, source_id) IN "
                    "(SELECT source_type, source_id FROM l3_text_frame)")
        casts = ", ".join(f'TRY_CAST("{c}" AS {t}) AS "{c}"' for c, t in schema.items())
        con.execute(f"INSERT INTO layer3.text_features BY NAME SELECT {casts} FROM l3_text_frame")
    finally:
        con.unregister("l3_text_frame")
    return len(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db")
    parser.add_argument("--note-months", type=int, default=12)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    con = connect(read_only=False, path=args.db) if args.db else connect(read_only=False)
    print(f"wrote {build_text_features(con, args.note_months, args.limit):,} text feature rows")


if __name__ == "__main__":
    main()
