"""Bronze ingest on a tiny fake release: values kept as received, reloads skipped, checksums verified."""
import csv
import hashlib
import json

import duckdb
import pytest

from layer1.pipeline import config
from layer1.pipeline.ingest import load_bronze


@pytest.fixture
def release(tmp_path, monkeypatch):
    raw = tmp_path / "release"
    (raw / "data" / "history" / "year=2025").mkdir(parents=True)
    (raw / "schema").mkdir()
    # Leading zeros, the 'NA' outcome code and an empty field (null) must survive bronze unchanged.
    (raw / "data" / "people.csv").write_text('ref,code,note\n0012345678,NA,"a, quoted"\n0000000001,,plain\n')
    con = duckdb.connect()
    con.execute(f"COPY (SELECT 1 AS id, 2.5 AS amount) TO '{raw}/data/history/year=2025/part-000.parquet' (FORMAT parquet)")
    con.close()
    schema = {
        "people": {"format": "csv", "path": "data/people.csv", "rows": 2, "columns": []},
        "history": {"format": "parquet (hive-partitioned by year)", "path": "data/history/", "rows": 1, "columns": []},
    }
    (raw / "schema" / "schema.json").write_text(json.dumps(schema))
    with open(raw / "MANIFEST.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["path", "bytes", "sha256"])
        for rel in ["data/people.csv", "data/history/year=2025/part-000.parquet"]:
            p = raw / rel
            w.writerow([rel, p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest()])
    monkeypatch.setattr(config, "RAW", raw)
    monkeypatch.setattr(config, "BRONZE", tmp_path / "lake" / "bronze")
    monkeypatch.setattr(config, "TMP", tmp_path / "lake" / "tmp")
    return tmp_path / "lake" / "test.duckdb"


def test_values_kept_as_received(release):
    load_bronze.main(["--db", str(release)])
    con = duckdb.connect(str(release), read_only=True)
    rows = con.execute("SELECT ref, code, note FROM bronze.people ORDER BY ref").fetchall()
    assert rows == [("0000000001", None, "plain"), ("0012345678", "NA", "a, quoted")]
    types = {r[0]: r[1] for r in con.execute("DESCRIBE bronze.people").fetchall()}
    assert types["ref"] == "VARCHAR" and types["code"] == "VARCHAR"
    lineage = con.execute("SELECT DISTINCT _source_file, _batch_id FROM bronze.people").fetchall()
    assert lineage == [("data/people.csv", "B20260928")]
    assert con.execute("SELECT _source_file FROM bronze.history").fetchone()[0] == "data/history/year=2025/part-000.parquet"


def test_manifest_counts_and_checksums(release):
    load_bronze.main(["--db", str(release)])
    con = duckdb.connect(str(release), read_only=True)
    m = dict(con.execute("SELECT table_name, row_count FROM bronze._load_manifest").fetchall())
    assert m == {"people": 2, "history": 1}
    assert con.execute("SELECT bool_and(sha256_ok) FROM bronze._load_manifest").fetchone()[0] is True


def test_reload_is_skipped(release):
    load_bronze.main(["--db", str(release)])
    load_bronze.main(["--db", str(release)])
    con = duckdb.connect(str(release), read_only=True)
    assert con.execute("SELECT count(*) FROM bronze._load_manifest").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM bronze.people").fetchone()[0] == 2


def test_checksum_mismatch_stops_the_load(release):
    people = config.RAW / "data" / "people.csv"
    people.write_text(people.read_text() + "0000000002,X,changed\n")
    with pytest.raises(SystemExit, match="checksum"):
        load_bronze.main(["--db", str(release)])
