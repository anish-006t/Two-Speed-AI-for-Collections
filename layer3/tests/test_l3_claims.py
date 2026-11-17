"""The three Layer 3 claims, checked on the code and (when built) on the real sample database.

1. SQL + LLM features: numeric features come from SQL; text and voice (call transcript) features are extracted by
   the typed-question extractor (LLM through shared.decide, rules fallback without a key).
2. LLM features are tested: each ships with its prompt, version and accuracy against human labels.
3. Same values everywhere: one definition for training (offline) and live scoring (online).
"""
import duckdb
import pandas as pd
import pytest

from layer1.pipeline import config
from layer3.features import batch_text, defs
from layer3.text.extract import extract, load_questions

DEV = config.DEV_DB


def test_claim1_every_feature_is_sql_or_extracted_text():
    for r in defs.registry_records():
        if r["ai_derived"]:
            assert r["name"].startswith("text_") and r["source"] == "layer3.text_features"
            assert "voice (call transcripts)" in r["modality"]
        else:
            assert r["modality"] == "structured (SQL)" and r["definition"].lstrip().upper().startswith("SELECT")


def test_claim2_each_ai_feature_ships_prompt_version_and_accuracy():
    questions = {q["id"]: q for q in load_questions()}
    for r in defs.registry_records():
        if not r["ai_derived"]:
            continue
        q = questions[r["name"][len("text_"):]]
        assert r["prompt"] == q["wording"] and r["question_version"] == q["version"]
        assert r["extractor"]
        if defs.EVAL_JSON.exists():
            assert r["accuracy"] is not None and r["accuracy_n"], r["name"]


def test_claim2_answers_record_question_and_extractor_version():
    for qid, answer in extract("I lost my job, I will pay $50 on Friday", "transcript").items():
        version = next(q["version"] for q in load_questions() if q["id"] == qid)
        assert answer["model_version"].endswith(f"|q:{qid}@{version}")


@pytest.mark.skipif(not DEV.exists(), reason="needs lake/dev.duckdb with Layer 3 built")
def test_claim3_training_and_live_values_match_on_real_customers(monkeypatch):
    con = duckdb.connect(str(DEV), read_only=True)
    if not con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='layer3' "
                       "AND table_name='account_history'").fetchone()[0]:
        pytest.skip("Layer 3 not built on the sample")
    ids = [r[0] for r in con.execute("""SELECT golden_customer_id FROM layer3.text_features
                                        WHERE golden_customer_id IS NOT NULL GROUP BY 1 ORDER BY 1 LIMIT 15""").fetchall()]
    monkeypatch.setattr(defs, "connect", lambda **kw: con.cursor())
    as_of = "2026-09-28"
    batch = defs.build_training_frame(pd.DataFrame({"golden_customer_id": ids, "as_of": as_of}))
    for i, gid in enumerate(ids):
        live = defs.get_features(gid, as_of)
        offline = {k: (None if pd.isna(v) else v) for k, v in batch.iloc[i][list(live)].to_dict().items()}
        assert offline == live, gid
    assert any(v is not None for v in live.values())


@pytest.mark.skipif(not DEV.exists(), reason="needs lake/dev.duckdb with Layer 3 built")
def test_claim3_stored_text_answers_equal_a_live_extraction():
    con = duckdb.connect(str(DEV), read_only=True)
    if not con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='layer3' "
                       "AND table_name='text_features'").fetchone()[0]:
        pytest.skip("Layer 3 not built on the sample")
    sources = [s for s in batch_text.source_rows(con, limit=10) if s["source_type"] == "transcript"][:10]
    assert sources
    compared = 0
    for s in sources:
        stored = con.execute("SELECT * FROM layer3.text_features WHERE source_type = ? AND source_id = ?",
                             [s["source_type"], s["source_id"]]).df()
        if stored.empty:
            continue
        live = extract(s["text"] or "", s["source_type"])
        for qid, answer in live.items():
            if stored.iloc[0][f"{qid}__model_version"] != answer["model_version"]:
                continue        # stored by another extractor version: not comparable
            compared += 1
            value = stored.iloc[0][qid]
            value = None if pd.isna(value) else value
            expected = answer["answer"]
            assert (value == expected) or (value is not None and expected is not None
                                           and str(value).lower() == str(expected).lower()), (s["source_id"], qid)
    assert compared >= 50, f"only {compared} answers were comparable"
