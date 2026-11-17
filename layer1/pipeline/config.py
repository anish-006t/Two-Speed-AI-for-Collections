"""Paths and run settings shared by every pipeline step."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # repo root (this file is layer1/pipeline/config.py)
RAW = ROOT / "data" / "raw" / "maple_collections_release"
LAKE = ROOT / "lake"
BRONZE = LAKE / "bronze"
TMP = LAKE / "tmp"

FULL_DB = LAKE / "maple.duckdb"
DEV_DB = LAKE / "dev.duckdb"

SNAPSHOT_DATE = "2026-09-28"  # source snapshot date of the release; also the bronze load_date partition
SOURCE_SYSTEM_BY_TABLE = {
    "customers": "CRM",
    "card_accounts": "CARDS",
    "loan_accounts": "LENDING",
    "deposit_accounts": "CORE_BANKING",
    "external": "BUREAU",
}

# Machine budget (decisions.md D-17): about 4-6 GB free RAM, 10 threads.
DUCKDB_MEMORY_LIMIT = "4GB"
DUCKDB_THREADS = 8
