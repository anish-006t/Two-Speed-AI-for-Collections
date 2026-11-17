from layer4.models.promise_break import score
from layer4.models.promise_break.train import train

import duckdb


def test_untrained_baseline_preserves_interface(monkeypatch, tmp_path):
    monkeypatch.setattr(score, "MODEL_PATH", tmp_path / "missing.txt")
    monkeypatch.setattr(score, "METADATA_PATH", tmp_path / "missing.json")
    result = score.score_promise("G-1", 500, "2026-10-20", "2026-10-03")
    assert set(result) == {"risk", "reasons", "safer_date", "model_version"}
    assert 0 <= result["risk"] <= 1 and len(result["reasons"]) == 3
    assert not result["model_version"].startswith("stub")


def test_baseline_validates_amount(monkeypatch, tmp_path):
    monkeypatch.setattr(score, "MODEL_PATH", tmp_path / "missing.txt")
    monkeypatch.setattr(score, "METADATA_PATH", tmp_path / "missing.json")
    try:
        score.score_promise("G-1", -1, "2026-10-20", "2026-10-03")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_lightgbm_training_writes_artifact_and_report(tmp_path):
    con = duckdb.connect(":memory:")
    con.execute("CREATE SCHEMA gold")
    con.execute("""CREATE TABLE gold.l3_customer_features (
        golden_customer_id VARCHAR, product_count INTEGER, total_credit_exposure DOUBLE,
        max_dpd_current INTEGER, current_bucket VARCHAR, hardship_flag BOOLEAN,
        incident_affected BOOLEAN, _loaded_at TIMESTAMP)""")
    con.execute("""INSERT INTO gold.l3_customer_features
        SELECT 'G-' || i, 2, 1000 + i, 10 + i, 'mid', false, false, '2025-01-01'
        FROM range(10) r(i)""")
    con.execute("""CREATE TABLE gold.promises_to_pay (
        ptp_id VARCHAR, golden_customer_id VARCHAR, ptp_created_ts TIMESTAMP, ptp_due_date DATE,
        ptp_amount DOUBLE, ptp_status_adjusted VARCHAR, status_ts TIMESTAMP)""")
    con.execute("""INSERT INTO gold.promises_to_pay
        SELECT 'P-' || i, 'G-' || (i % 10), DATE '2026-01-01' + i * INTERVAL 1 DAY,
               DATE '2026-01-03' + i * INTERVAL 1 DAY, 50 + i,
               CASE WHEN i % 3 = 0 THEN 'broken' ELSE 'kept' END, DATE '2026-01-04' + i * INTERVAL 1 DAY
        FROM range(120) r(i)""")
    from layer3.tests._history_fixture import history_tables
    history_tables(con, accounts=[(f"G-{i}", f"C-{i}", "card", "2025-01-01", 1000 + i, 10 + i) for i in range(10)])
    metrics = train(con, tmp_path / "model.txt", tmp_path / "metadata.json", tmp_path / "report.md")
    assert metrics["rows"] == 120 and 0 <= metrics["brier"] <= 1
    assert (tmp_path / "model.txt").exists() and (tmp_path / "metadata.json").exists()
    assert "Test AUC" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_scoring_falls_back_to_baseline_when_features_cannot_be_built(monkeypatch, tmp_path):
    from layer4.models.promise_break import score
    model, meta = tmp_path / "model.txt", tmp_path / "metadata.json"
    model.write_text("placeholder"); meta.write_text("{}")
    monkeypatch.setattr(score, "MODEL_PATH", model)
    monkeypatch.setattr(score, "METADATA_PATH", meta)
    monkeypatch.setattr(score, "get_features", lambda *a: (_ for _ in ()).throw(RuntimeError("no history")))
    out = score.score_promise("G-1", 100, "2026-10-10", "2026-10-03")
    assert out["model_version"] == score.BASELINE_VERSION and "layer3.build" in out["reasons"][2]
