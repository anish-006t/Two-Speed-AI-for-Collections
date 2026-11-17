"""Build Layer 1 in one command, then check that every interface table is there (brief L1-1).

  uv run python -m layer1.build --target dev     # 50K-customer sample, about 3 minutes (needs the full bronze first)
  uv run python -m layer1.build --target full    # everything, about 16 minutes, about 6 GB free RAM
  uv run python -m layer1.build --verify-only    # just the checks

Steps run one at a time (DuckDB allows one writer): bronze -> [sample] -> silver -> identity -> gold, restricted
and access views -> redaction -> data quality. A step that fails stops the build and says which one. Close Streamlit,
notebooks and any other DuckDB connection first.
"""
import argparse
import os
import subprocess
import sys
import time

from layer1.pipeline import config

# Interface tables (docs/INTERFACES.md) and the smallest row count that makes sense on the full data and the sample.
INTERFACE_TABLES = {
    "gold.c360_customer": (1_000_000, 40_000), "gold.c360_accounts": (1_800_000, 80_000),
    "gold.account_monthly_snapshot": (12_000_000, 12_000_000), "gold.cases": (360_000, 15_000),
    "gold.contact_timeline": (2_600_000, 100_000), "gold.promises_to_pay": (230_000, 10_000),
    "gold.incident_effects": (800, 20), "gold.l3_customer_features": (1_000_000, 40_000),
    "gold.dq_report": (1, 1), "identity.crosswalk": (3_400_000, 150_000),
    "silver.agent_notes_redacted": (760_000, 30_000), "silver.transcript_turns_redacted": (380_000, 10_000),
    "access_analyst.customers": (1_000_000, 40_000), "access_agent.case_brief": (360_000, 15_000),
    "bronze.metric_definitions": (15, 15), "bronze.reference_documents": (19, 19),
}


def run(step, cmd, env=None):
    print(f"\n=== {step}: {' '.join(cmd)}", flush=True)
    t = time.time()
    result = subprocess.run(cmd, env={**os.environ, **(env or {})})
    if result.returncode != 0:
        raise SystemExit(f"\nBuild stopped: step '{step}' failed (exit {result.returncode}). Fix it and rerun; finished "
                         f"steps are safe to repeat.")
    print(f"=== {step} done in {time.time() - t:.0f} s", flush=True)


def verify(target):
    import duckdb
    db = config.DEV_DB if target == "dev" else config.FULL_DB
    if not db.exists():
        raise SystemExit(f"{db} does not exist: run the build first")
    con = duckdb.connect(str(db), read_only=True)
    col = 0 if target == "full" else 1
    problems = []
    print(f"\nChecking {db.relative_to(config.ROOT)}")
    for table, minimum in INTERFACE_TABLES.items():
        schema, name = table.split(".")
        exists = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
                             [schema, name]).fetchone()[0]
        n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] if exists else None
        ok = n is not None and n >= minimum[col]
        print(f"  {'ok ' if ok else 'BAD'} {table:38s} {('missing' if n is None else f'{n:,} rows'):>18s}")
        if not ok:
            problems.append(table)
    blocking = con.execute("""SELECT count(*) FROM gold.dq_report WHERE status = 'fail' AND severity = 'block'
                              AND run_id = (SELECT arg_max(run_id, recorded_at) FROM gold.dq_report)""").fetchone()[0]
    print(f"  {'ok ' if blocking == 0 else 'BAD'} latest data-quality run: {blocking} blocking failures")
    if problems or blocking:
        raise SystemExit(f"\nNot ready: {', '.join(problems) or 'blocking data-quality failures'}")
    print("\nLayer 1 is ready: every interface table is there. Read it with shared.db.connect(read_only=True).")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", choices=["dev", "full"], default="dev")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--skip-redaction", action="store_true", help="skip the 11-minute redaction (text tables stay as before)")
    args = ap.parse_args(argv)
    if not args.verify_only:
        if not (config.RAW / "schema" / "schema.json").exists():
            raise SystemExit("data/raw/maple_collections_release/ is missing: unzip the release first (see README.md)")
        py = [sys.executable, "-m"]
        dbt = [sys.executable, "-m", "dbt.cli.main"]
        db = str(config.DEV_DB if args.target == "dev" else config.FULL_DB)
        dbt_args = ["--project-dir", "dbt", "--profiles-dir", "dbt", "--target", args.target]
        env = {"DHANDHO_LAKE": str(config.LAKE)}
        start = time.time()
        run("bronze", py + ["layer1.pipeline.ingest.load_bronze"])
        if args.target == "dev":
            run("dev sample", py + ["layer1.pipeline.ingest.sample"])
        run("silver", dbt + ["build", "--select", "silver", "--indirect-selection", "cautious"] + dbt_args, env)
        run("identity", py + ["layer1.identity.run", "--db", db])
        run("gold", dbt + ["build", "--exclude", "silver"] + dbt_args, env)
        if not args.skip_redaction:
            run("redaction", py + ["layer1.pipeline.redact", "--db", db])
        run("data quality", py + ["layer1.quality.checks", "--db", db])
        print(f"\nBuilt in {(time.time() - start) / 60:.1f} minutes.")
    verify(args.target)


if __name__ == "__main__":
    main()
