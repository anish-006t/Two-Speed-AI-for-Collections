"""Redact free text with Microsoft Presidio before anything else reads it (architecture §6, §12; CLAUDE.md rule 4).

Inputs: bronze.agent_notes.note_text and the transcript turns in files/transcripts/**.json.
Outputs: silver.agent_notes_redacted, silver.transcript_turns_redacted, silver.redaction_spot_check.
Entities replaced with <ENTITY_TYPE>: people, phones, emails, card and bank numbers, locations, nationality/religion
(NRP), plus Canadian postal codes, SINs, and the bank's own account and CRM ID formats. Dates and amounts stay:
they are not identifying on their own and Layer 3 needs them (promise dates, amounts).

French text runs through the same English pipeline: pattern recognizers still catch phones, emails, postal codes
and IDs, but name detection is weaker (decisions D-25). The spot check counts any pattern left behind.

Usage:  python -m layer1.pipeline.redact [--db lake/dev.duckdb] [--limit 20000] [--processes 3]
"""
import argparse
import json
import re
import time
from pathlib import Path

import pandas as pd

from layer1.pipeline import config
from layer1.pipeline.db import connect

ENTITIES = ["PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "CREDIT_CARD", "IBAN_CODE", "US_BANK_NUMBER", "LOCATION",
            "NRP", "IP_ADDRESS", "CA_POSTAL_CODE", "CA_SIN", "BANK_ACCOUNT_ID", "CRM_ID", "LONG_NUMBER"]

CUSTOM_PATTERNS = {
    "CA_POSTAL_CODE": [r"\b[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z][ -]?\d[ABCEGHJ-NPRSTV-Z]\d\b"],
    "CA_SIN": [r"\b\d{3}[ -]\d{3}[ -]\d{3}\b"],
    "BANK_ACCOUNT_ID": [r"\b(?:C|L|CHQ|SAV|TFS|USD)-\d{4,}\b", r"\b\d{10}\b"],
    "CRM_ID": [r"\b[A-Z]{3}-\d{4}\b"],
    # Card or account numbers written in groups; synthetic ones fail Presidio's Luhn check, so match the shape.
    "LONG_NUMBER": [r"\b\d(?:[ -]?\d){11,18}\b"],
}
# What the spot check looks for in redacted text: anything here means something slipped through.
LEAK_PATTERNS = {
    "phone": re.compile(r"(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
    "postal_code": re.compile(CUSTOM_PATTERNS["CA_POSTAL_CODE"][0]),
    "crm_id": re.compile(CUSTOM_PATTERNS["CRM_ID"][0]),
    "long_number": re.compile(r"\b\d(?:[ -]?\d){11,18}\b"),
}


def build_engines():
    from presidio_analyzer import AnalyzerEngine, BatchAnalyzerEngine, Pattern, PatternRecognizer
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_anonymizer import AnonymizerEngine

    nlp = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy", "models": [{"lang_code": "en", "model_name": "en_core_web_md"}]}).create_engine()
    analyzer = AnalyzerEngine(nlp_engine=nlp, supported_languages=["en"])
    for entity, patterns in CUSTOM_PATTERNS.items():
        analyzer.registry.add_recognizer(PatternRecognizer(
            supported_entity=entity, patterns=[Pattern(f"{entity}_{i}", p, 0.6) for i, p in enumerate(patterns)]))
    return BatchAnalyzerEngine(analyzer_engine=analyzer), AnonymizerEngine()


def redact_texts(batch_engine, anonymizer, texts, processes, batch_size=256):
    """Returns (redacted_texts, entity_counts) in input order. Empty or null texts pass through."""
    clean = [t if isinstance(t, str) else "" for t in texts]
    results = batch_engine.analyze_iterator(clean, language="en", entities=ENTITIES,
                                            batch_size=batch_size, n_process=processes)
    out, counts = [], []
    for text, found in zip(clean, results):
        if not text:
            out.append(None)
            counts.append("{}")
            continue
        out.append(anonymizer.anonymize(text=text, analyzer_results=found).text)
        c = {}
        for r in found:
            c[r.entity_type] = c.get(r.entity_type, 0) + 1
        counts.append(json.dumps(c, sort_keys=True))
    return out, counts


def leak_counts(texts):
    return {name: sum(1 for t in texts if isinstance(t, str) and rx.search(t)) for name, rx in LEAK_PATTERNS.items()}


def redact_notes(con, engines, processes, limit, chunk=20_000):
    con.execute("DROP TABLE IF EXISTS silver.agent_notes_redacted")
    total = con.execute("SELECT count(*) FROM bronze.agent_notes").fetchone()[0]
    total = min(total, limit) if limit else total
    leaks, done, start = {k: 0 for k in LEAK_PATTERNS}, 0, time.time()
    for offset in range(0, total, chunk):
        n = min(chunk, total - offset)
        df = con.execute(f"""
            SELECT note_id, contact_id, case_id, crm_customer_id, account_id, agent_id, note_ts, note_type,
                   note_language, note_text, _source_file, _batch_id, _loaded_at
            FROM bronze.agent_notes ORDER BY note_id LIMIT {n} OFFSET {offset}""").df()
        df["note_text_redacted"], df["entities_found"] = redact_texts(*engines, df["note_text"].tolist(), processes)
        for k, v in leak_counts(df["note_text_redacted"]).items():
            leaks[k] += v
        df = df.drop(columns=["note_text"])
        con.register("chunk_df", df)
        if offset == 0:
            con.execute("CREATE TABLE silver.agent_notes_redacted AS SELECT * FROM chunk_df")
        else:
            con.execute("INSERT INTO silver.agent_notes_redacted SELECT * FROM chunk_df")
        con.unregister("chunk_df")
        done += n
        rate = done / (time.time() - start)
        print(f"  notes {done:,}/{total:,}  {rate:.0f}/s  eta {(total - done) / rate / 60:.1f} min", flush=True)
    return {"notes": done, "residual_matches": leaks}


def redact_transcripts(con, engines, processes, limit):
    paths = con.execute("SELECT transcript_id, crm_customer_id, language, file_path FROM bronze.call_transcripts "
                        "ORDER BY transcript_id" + (f" LIMIT {limit}" if limit else "")).fetchall()
    rows = []
    for tid, crm, lang, rel in paths:
        f = config.RAW / "files" / rel if not (config.RAW / rel).exists() else config.RAW / rel
        if not f.exists():
            continue
        for turn in json.loads(f.read_text()).get("turns", []):
            rows.append((tid, crm, lang, turn.get("turn_index"), turn.get("speaker"), turn.get("start_sec"),
                         turn.get("end_sec"), turn.get("text"), turn.get("sentiment_score")))
    df = pd.DataFrame(rows, columns=["transcript_id", "crm_customer_id", "language", "turn_index", "speaker",
                                     "start_sec", "end_sec", "text", "sentiment_score"])
    df["text_redacted"], df["entities_found"] = redact_texts(*engines, df["text"].tolist(), processes)
    leaks = leak_counts(df["text_redacted"])
    df = df.drop(columns=["text"])
    df["_source_file"] = "files/transcripts"
    con.register("turns_df", df)
    con.execute("CREATE OR REPLACE TABLE silver.transcript_turns_redacted AS SELECT * FROM turns_df")
    con.unregister("turns_df")
    return {"transcripts": len(paths), "turns": len(df), "residual_matches": leaks}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(config.FULL_DB))
    ap.add_argument("--limit", type=int, default=None, help="only the first N notes / transcripts (development)")
    ap.add_argument("--processes", type=int, default=3, help="spaCy processes (~1 GB RAM each)")
    ap.add_argument("--skip-notes", action="store_true")
    args = ap.parse_args(argv)

    con = connect(args.db)
    con.execute("CREATE SCHEMA IF NOT EXISTS silver")
    engines = build_engines()
    report = {}
    if not args.skip_notes:
        report["notes"] = redact_notes(con, engines, args.processes, args.limit)
    report["transcripts"] = redact_transcripts(con, engines, args.processes, args.limit)
    con.execute("CREATE OR REPLACE TABLE silver.redaction_spot_check AS SELECT now() AS at, ?::JSON AS report",
                [json.dumps(report)])
    print(json.dumps(report, indent=2))
    con.close()


if __name__ == "__main__":
    main()
