"""Bronze ingest: store every source file exactly as received and record it in bronze._load_manifest.

CSV tables are copied once to Parquet with every column kept as text (all_varchar), so leading zeros, `NA` codes and
malformed values survive unchanged. Parquet history tables are not copied; bronze views read them in place.
A file whose SHA-256 is already in the manifest is skipped, so reloading is safe.

Usage:  python -m layer1.pipeline.ingest.load_bronze [--tables customers,card_accounts] [--db lake/maple.duckdb]
"""
import argparse
import csv
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from layer1.pipeline import config
from layer1.pipeline.db import connect

MANIFEST_DDL = """
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE TABLE IF NOT EXISTS bronze._load_manifest (
    table_name       VARCHAR,
    source_file      VARCHAR,   -- path relative to the release folder
    bronze_path      VARCHAR,   -- Parquet copy for CSV sources; the source file itself for Parquet sources
    row_count        BIGINT,
    sha256           VARCHAR,
    sha256_expected  VARCHAR,   -- from the release MANIFEST.csv
    sha256_ok        BOOLEAN,
    batch_id         VARCHAR,
    loaded_at        TIMESTAMP,
    pipeline_run_id  VARCHAR
);
"""


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_release_manifest(raw: Path) -> dict:
    with open(raw / "MANIFEST.csv", newline="") as f:
        return {row["path"]: row["sha256"] for row in csv.DictReader(f)}


def already_loaded(con, table: str, sha: str) -> bool:
    return con.execute(
        "SELECT count(*) FROM bronze._load_manifest WHERE table_name = ? AND sha256 = ?", [table, sha]
    ).fetchone()[0] > 0


def record(con, table, source_file, bronze_path, rows, sha, expected, batch_id, loaded_at, run_id):
    con.execute(
        "INSERT INTO bronze._load_manifest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [table, source_file, str(bronze_path), rows, sha, expected, sha == expected if expected else None,
         batch_id, loaded_at, run_id],
    )


def load_csv_table(con, table, meta, raw, expected, batch_id, loaded_at, run_id, bronze_root):
    src = raw / meta["path"]
    sha = sha256_of(src)
    if already_loaded(con, table, sha):
        print(f"  {table}: hash already loaded, skipped")
        return
    out_dir = bronze_root / table / f"load_date={config.SNAPSHOT_DATE}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "part-000.parquet"
    # all_varchar keeps values as received; only an empty field becomes null (release README convention).
    con.execute(
        f"""
        COPY (
            SELECT *,
                   '{meta["path"]}'          AS _source_file,
                   '{batch_id}'              AS _batch_id,
                   TIMESTAMP '{loaded_at:%Y-%m-%d %H:%M:%S}' AS _loaded_at
            FROM read_csv('{src}', header = true, all_varchar = true, delim = ',', quote = '"', escape = '"',
                          sample_size = -1)
        ) TO '{out}' (FORMAT parquet, COMPRESSION zstd)
        """
    )
    rows = con.execute(f"SELECT count(*) FROM read_parquet('{out}')").fetchone()[0]
    record(con, table, meta["path"], out, rows, sha, expected.get(meta["path"]), batch_id, loaded_at, run_id)
    flag = "" if rows == meta.get("rows") else f"  (schema.json says {meta.get('rows')})"
    print(f"  {table}: {rows:,} rows -> {out.relative_to(bronze_root.parent)}{flag}")


def load_parquet_table(con, table, meta, raw, expected, batch_id, loaded_at, run_id):
    folder = raw / meta["path"]
    files = sorted(folder.glob("year=*/*.parquet"))
    new = 0
    for f in files:
        rel = f.relative_to(raw).as_posix()   # forward slashes on every OS (lineage is compared across laptops)
        sha = sha256_of(f)
        if already_loaded(con, table, sha):
            continue
        rows = con.execute(f"SELECT sum(num_rows) FROM parquet_file_metadata('{f}')").fetchone()[0]
        record(con, table, rel, f, rows, sha, expected.get(rel), batch_id, loaded_at, run_id)
        new += 1
    total = con.execute(
        "SELECT sum(row_count) FROM bronze._load_manifest WHERE table_name = ?", [table]
    ).fetchone()[0]
    print(f"  {table}: {len(files)} files ({new} new), {total:,} rows read in place")


def create_views(con, schema: dict, raw: Path, bronze_root: Path):
    """bronze.<table> views. CSV sources read the Parquet copy; history tables read the release files in place."""
    for table, meta in schema.items():
        if meta["format"] == "csv":
            glob = bronze_root / table / "load_date=*" / "*.parquet"
            if not list(bronze_root.glob(f"{table}/load_date=*/*.parquet")):
                continue
            con.execute(
                f"CREATE OR REPLACE VIEW bronze.{table} AS "
                f"SELECT * EXCLUDE (load_date) FROM read_parquet('{glob}', hive_partitioning = true)"
            )
        elif meta["format"].startswith("parquet"):
            glob = raw / meta["path"] / "year=*" / "*.parquet"
            con.execute(
                f"""
                CREATE OR REPLACE VIEW bronze.{table} AS
                SELECT p.*, m.source_file AS _source_file, m.batch_id AS _batch_id, m.loaded_at AS _loaded_at
                FROM read_parquet('{glob}', hive_partitioning = true, filename = '_bronze_path') p
                LEFT JOIN bronze._load_manifest m
                  ON m.table_name = '{table}' AND m.bronze_path = p._bronze_path
                """
            )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tables", help="comma-separated subset of tables (default: all)")
    ap.add_argument("--db", default=str(config.FULL_DB))
    args = ap.parse_args(argv)

    raw = config.RAW
    schema = json.loads((raw / "schema" / "schema.json").read_text())
    if args.tables:
        wanted = set(args.tables.split(","))
        unknown = wanted - set(schema)
        if unknown:
            raise SystemExit(f"unknown tables: {sorted(unknown)}")
        schema = {t: m for t, m in schema.items() if t in wanted}
    expected = load_release_manifest(raw)

    run_id = str(uuid.uuid4())
    loaded_at = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    batch_id = f"B{config.SNAPSHOT_DATE.replace('-', '')}"

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    con = connect(args.db)
    con.execute(MANIFEST_DDL)
    print(f"run {run_id}, batch {batch_id}")
    for table, meta in schema.items():
        if meta["format"] == "csv":
            load_csv_table(con, table, meta, raw, expected, batch_id, loaded_at, run_id, config.BRONZE)
        elif meta["format"].startswith("parquet"):
            load_parquet_table(con, table, meta, raw, expected, batch_id, loaded_at, run_id)
    create_views(con, json.loads((raw / "schema" / "schema.json").read_text()), raw, config.BRONZE)

    bad = con.execute("SELECT count(*) FROM bronze._load_manifest WHERE sha256_ok = false").fetchone()[0]
    if bad:
        raise SystemExit(f"{bad} files do not match the release MANIFEST.csv checksum")
    con.close()


if __name__ == "__main__":
    main()
