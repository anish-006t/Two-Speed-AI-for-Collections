"""Dagster orchestration for Layer 1 (architecture §4): one asset per table, daily batch.

bronze (Python) -> silver (dbt) -> identity (Python: Splink, clustering, crosswalk) -> gold + restricted (dbt)
-> gold.dq_report (Python). Redaction of notes and transcripts runs off bronze.
dbt models keep their own schema in the asset key (silver/..., gold/...), so Dagster wires the Python assets to
them through the dbt sources (bronze/..., identity/...).

Run:  DHANDHO_TARGET=dev uv run dagster dev -m orchestration.definitions     (default target: dev)
"""
import json
import os
import sys
from pathlib import Path

from dagster import (AssetExecutionContext, AssetKey, AssetSpec, Definitions, ScheduleDefinition, define_asset_job,
                     in_process_executor, multi_asset)
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, DbtProject, dbt_assets

from layer1.pipeline import config

TARGET = os.environ.get("DHANDHO_TARGET", "dev")
# dagster-dbt runs dbt from inside dbt/, so the profile needs the lake as an absolute path (decisions D-41).
os.environ.setdefault("DHANDHO_LAKE", str(config.LAKE))
DB = str(config.DEV_DB if TARGET == "dev" else config.FULL_DB)

dbt_project = DbtProject(project_dir=config.ROOT / "dbt", profiles_dir=config.ROOT / "dbt", target=TARGET)
dbt_project.prepare_if_dev()


class SchemaKeyTranslator(DagsterDbtTranslator):
    """Asset keys are [schema, table] for both dbt models and sources."""

    def get_asset_key(self, dbt_resource_props):
        if dbt_resource_props["resource_type"] == "source":
            return AssetKey([dbt_resource_props["source_name"], dbt_resource_props["name"]])
        schema = dbt_resource_props["config"].get("schema") or dbt_resource_props.get("schema") or "main"
        return AssetKey([schema, dbt_resource_props["name"]])


TRANSLATOR = SchemaKeyTranslator()
SCHEMA_JSON = config.RAW / "schema" / "schema.json"
BRONZE_TABLES = sorted(json.loads(SCHEMA_JSON.read_text())) if SCHEMA_JSON.exists() else []


@multi_asset(specs=[AssetSpec(["bronze", t], group_name="bronze") for t in BRONZE_TABLES], name="bronze_ingest")
def bronze_ingest(context: AssetExecutionContext):
    """Files as received + manifest; skips files whose hash is already loaded. The dev DB is then re-sampled."""
    from layer1.pipeline.ingest import load_bronze, sample
    load_bronze.main(["--db", str(config.FULL_DB)])
    if TARGET == "dev":
        sample.main([])


@dbt_assets(manifest=dbt_project.manifest_path, select="silver", dagster_dbt_translator=TRANSLATOR,
            name="silver_dbt", project=dbt_project)
def silver_dbt(context: AssetExecutionContext, dbt: DbtCliResource):
    yield from dbt.cli(["build", "--indirect-selection", "cautious"], context=context).stream()


IDENTITY_TABLES = ["crosswalk", "golden_confidence", "golden_alias", "match_pairs", "review_queue", "match_quality"]


@multi_asset(
    specs=[AssetSpec(["identity", t], group_name="identity",
                     deps=[AssetKey(["silver", "identity_input"]), AssetKey(["silver", "household_refs"]),
                           AssetKey(["silver", "account_links"])]) for t in IDENTITY_TABLES],
    name="identity_resolution")
def identity_resolution(context: AssetExecutionContext):
    """Deterministic rules, Splink, constraints, review decisions, clustering, crosswalk, confidence, evaluation."""
    from layer1.identity import run
    run.main(["--db", DB])


@dbt_assets(manifest=dbt_project.manifest_path, select="gold restricted access c360_customer_history",
            dagster_dbt_translator=TRANSLATOR, name="gold_dbt", project=dbt_project)
def gold_dbt(context: AssetExecutionContext, dbt: DbtCliResource):
    yield from dbt.cli(["build"], context=context).stream()


@multi_asset(specs=[AssetSpec(["silver", "agent_notes_redacted"], group_name="text",
                              deps=[AssetKey(["bronze", "agent_notes"])]),
                    AssetSpec(["silver", "transcript_turns_redacted"], group_name="text",
                              deps=[AssetKey(["bronze", "call_transcripts"])])],
             name="redaction")
def redaction(context: AssetExecutionContext):
    """Presidio redaction; must finish before any LLM or Layer 2/3 reader sees the text."""
    from layer1.pipeline import redact
    redact.main(["--db", DB])


@multi_asset(specs=[AssetSpec(["gold", "dq_narrative"], group_name="quality"),
                    AssetSpec(["gold", "dq_report"], group_name="quality",
                              deps=[AssetKey(["gold", "c360_customer"]), AssetKey(["gold", "c360_accounts"]),
                                    AssetKey(["identity", "match_quality"])])],
             name="dq_checks")
def dq_checks(context: AssetExecutionContext):
    """Six families of checks; raises (stops the release) on an unlabelled blocking failure. Then the alert text."""
    from layer1.quality import checks, narrative
    checks.main(["--db", DB])
    narrative.main(["--db", DB])


daily = define_asset_job("layer1_daily", selection="*")

defs = Definitions(
    assets=[bronze_ingest, silver_dbt, identity_resolution, gold_dbt, redaction, dq_checks],
    jobs=[daily],
    schedules=[ScheduleDefinition(job=daily, cron_schedule="0 6 * * *")],   # after the nightly source extracts
    # One step at a time: DuckDB allows a single writer per database, and serial steps keep memory within ~6 GB
    # (decisions D-41).
    executor=in_process_executor,
    resources={"dbt": DbtCliResource(project_dir=dbt_project,
                                     dbt_executable=str(Path(sys.executable).parent / "dbt"))},  # the venv's dbt
)
