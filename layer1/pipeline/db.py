"""DuckDB connection helper: one place that applies the memory, thread and spill settings."""
import duckdb

from layer1.pipeline import config


def connect(path=config.FULL_DB, read_only=False):
    config.TMP.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only)
    con.execute(f"SET memory_limit = '{config.DUCKDB_MEMORY_LIMIT}'")
    con.execute(f"SET threads = {config.DUCKDB_THREADS}")
    con.execute(f"SET temp_directory = '{config.TMP}'")
    con.execute("SET preserve_insertion_order = false")  # lets large COPYs stream instead of buffering
    return con
