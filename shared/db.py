"""DuckDB connection with the team settings (memory limit, threads, spill directory from layer1/pipeline/config.py).

DuckDB allows one writer per file, and one process cannot open the same file both read-only and read-write. So this
module keeps one connection per file per process and hands out cursors on it: in the app, the first page to ask for a
read-write connection makes every later read-only request share it instead of failing.

    from shared.db import connect
    con = connect()                    # read-only, lake/maple.duckdb unless DHANDHO_DB says otherwise
    con = connect(read_only=False)     # Layer 4 tables, audit.events
"""
import threading
from pathlib import Path

import duckdb

from layer1.pipeline import config as l1
from shared import config

_lock = threading.Lock()
_open: dict[str, tuple[duckdb.DuckDBPyConnection, bool]] = {}  # resolved path -> (connection, read_only)


def db_path() -> Path:
    return config.db_path()


def _open_connection(path: Path, read_only: bool) -> duckdb.DuckDBPyConnection:
    if read_only and not path.exists():
        raise FileNotFoundError(f"{path} does not exist. Build Layer 1 first (commands in CLAUDE.md) "
                                f"or point DHANDHO_DB at lake/dev.duckdb.")
    path.parent.mkdir(parents=True, exist_ok=True)
    l1.TMP.mkdir(parents=True, exist_ok=True)
    try:
        con = duckdb.connect(str(path), read_only=read_only)
    except duckdb.IOException as e:
        raise RuntimeError(f"Cannot open {path}: another process holds it. DuckDB allows one writer, so close "
                           f"Streamlit, notebooks or the pipeline and retry. ({e})") from e
    con.execute(f"SET memory_limit = '{l1.DUCKDB_MEMORY_LIMIT}'")
    con.execute(f"SET threads = {l1.DUCKDB_THREADS}")
    con.execute(f"SET temp_directory = '{l1.TMP.as_posix()}'")
    return con


def connect(read_only: bool = True, path=None) -> duckdb.DuckDBPyConnection:
    """A cursor on this process's connection to the database. Safe to `.close()`; the shared connection stays."""
    p = Path(path) if path else db_path()
    key = str(p.resolve())
    with _lock:
        held = _open.get(key)
        if held and not read_only and held[1]:
            raise RuntimeError(f"{p} is already open read-only in this process; call shared.db.close_all() first, "
                               f"or ask for read_only=False before any read-only connection.")
        if not held:
            held = (_open_connection(p, read_only), read_only)
            _open[key] = held
        return held[0].cursor()


def close_all() -> None:
    """Close every connection this process holds (tests; scripts that hand the file to a pipeline step)."""
    with _lock:
        for con, _ in _open.values():
            con.close()
        _open.clear()


def table_exists(con, schema: str, table: str) -> bool:
    return con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
                       [schema, table]).fetchone()[0] > 0
