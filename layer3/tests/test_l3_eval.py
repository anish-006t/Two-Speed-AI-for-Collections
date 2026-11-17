import pandas as pd

from layer3.text.eval_labels import evaluate_frame


def test_eval_reports_accuracy_f1_and_calibration(monkeypatch):
    monkeypatch.setenv("L3_EXTRACTOR_MODE", "rules")
    frame = pd.DataFrame([
        {"text": "I lost my job", "label_hardship": "clear", "label_promise_mentioned": False},
        {"text": "I will pay $20 tomorrow", "label_hardship": "none", "label_promise_mentioned": True},
    ])
    results = evaluate_frame(frame, "note")
    indexed = {row["question"]: row for row in results}
    assert indexed["hardship"]["accuracy"] == 1
    assert indexed["promise_mentioned"]["macro_f1"] == 1
    assert 0 <= indexed["hardship"]["brier"] <= 1
