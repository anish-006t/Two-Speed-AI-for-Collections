"""Layer 2 development database without building Layer 1 (seconds, not 15 minutes).

    uv run python -m layer2.devdb                  # creates lake/l2dev.duckdb: schema bronze = views over the release
    uv run python -m layer2.build --db lake/l2dev.duckdb
    DHANDHO_DB=lake/l2dev.duckdb uv run python -m layer2.benchmark.eval_dev

`bronze` here is read in place from data/raw/ with every CSV column as text, the same shape Layer 1's bronze has.
There is no gold (no golden IDs) and no silver (no redacted transcripts), so call summaries need the real
lake/maple.duckdb; every metric and policy answer works. Writes only lake/l2dev.duckdb.
"""
from __future__ import annotations

import argparse
import sys

from layer2.adapters import REPO

RELEASE = REPO / "data" / "raw" / "maple_collections_release" / "data"
CSV_TABLES = ["agent_notes", "collections_cases", "contact_history", "promises_to_pay", "agents", "case_assignment_history",
              "card_accounts", "loan_accounts", "deposit_accounts", "call_transcripts", "metric_definitions",
              "reference_documents", "ops_events", "code_lookups", "qa_checklist", "hardship_programs"]
PARQUET_TABLES = ["account_monthly_snapshot"]


def create(path) -> None:
    import duckdb
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
    for t in CSV_TABLES:
        con.execute(f"CREATE OR REPLACE VIEW bronze.{t} AS SELECT * FROM "
                    f"read_csv('{RELEASE / (t + '.csv')}', all_varchar = true, header = true)")
    for t in PARQUET_TABLES:
        con.execute(f"CREATE OR REPLACE VIEW bronze.{t} AS SELECT * FROM "
                    f"read_parquet('{RELEASE / t}/*/*.parquet', hive_partitioning = true)")
    n = con.execute("SELECT count(*) FROM bronze.collections_cases").fetchone()[0]
    con.close()
    print(f"{path.relative_to(REPO)}: bronze views over the release ({n:,} cases)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "lake" / "l2dev.duckdb"))
    args = ap.parse_args(argv)
    from pathlib import Path
    create(Path(args.db))
    return 0


if __name__ == "__main__":
    sys.exit(main())
