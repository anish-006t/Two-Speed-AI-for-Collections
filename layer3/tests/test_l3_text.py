import json

import pytest

from layer3.text.extract import QUESTION_PATH, extract, load_questions


def test_question_registry_is_complete_and_mapped():
    raw = json.loads(QUESTION_PATH.read_text(encoding="utf-8"))
    ids = {item["id"] for item in raw}
    required = {"hardship", "hardship_reason", "stated_delay_reason", "promise_mentioned", "promise_amount",
                "promise_date", "promise_intent_strength", "dispute", "dispute_type", "complaint",
                "cease_request", "insolvency_mention", "third_party_present", "legal_threat_by_agent",
                "sentiment", "next_step"}
    assert required <= ids
    assert "vulnerability" not in ids
    assert len(ids) == len(raw)
    assert all(q["version"] >= 1 and q["label_column"] and q["applies_to"] for q in load_questions())


def test_offline_extractor_all_applicable_questions(monkeypatch):
    monkeypatch.setenv("L3_EXTRACTOR_MODE", "rules")
    text = "I lost my job, but I will pay $1,250.50 on Friday. Thank you."
    output = extract(text, "turn")
    expected = {q["id"] for q in load_questions() if "turn" in q["applies_to"]}
    assert set(output) == expected
    assert output["hardship"]["answer"] == "clear"
    assert output["hardship_reason"]["answer"] == "job_loss"
    assert output["promise_mentioned"]["answer"] is True
    assert output["promise_amount"]["answer"] == 1250.50
    for answer in output.values():
        assert set(answer) == {"answer", "prob", "evidence", "model_version"}
        assert 0 <= answer["prob"] <= 1
        assert not answer["evidence"] or answer["evidence"].lower() in text.lower()


def test_extractor_rejects_bad_input():
    with pytest.raises(ValueError):
        extract("text", "email")
    with pytest.raises(TypeError):
        extract(None, "note")
