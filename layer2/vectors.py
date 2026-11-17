"""Embeddings and the vector store for agent-notes RAG (layer2/docs/rag_plan.md §3). Light by construction:

- model intfloat/multilingual-e5-small (384 dims, English and French) on CPU with the already-installed
  transformers + torch; loaded lazily, on the first notes question that needs it, once per process;
- store: DuckDB in its own file lake/layer2_vectors.duckdb, table note_vec, similarity with DuckDB's built-in
  array_cosine_similarity (no extension). Only the notes of cases someone asked about are embedded, then reused;
  there is no index of all notes. Writes are best-effort: a locked file means vectors stay in memory;
- L2_VECTORS=0 switches it off; without the model (offline) available() is False and callers use keyword search.

    uv run python -m layer2.vectors --download     # fetch the model once into ~/.cache/huggingface
"""
from __future__ import annotations

import os
import sys
import threading

from layer2.adapters import REPO

MODEL = os.environ.get("L2_EMBED_MODEL", "intfloat/multilingual-e5-small")
DIM = 384
STORE = REPO / "lake" / "layer2_vectors.duckdb"
_state: dict = {"model": None, "tokenizer": None, "failed": None}
_lock = threading.Lock()
_memory: dict[str, list[float]] = {}          # note_id -> vector, when the store cannot be written


def enabled() -> bool:
    return os.environ.get("L2_VECTORS", "1") != "0"


def _load(download: bool = False) -> bool:
    if _state["model"] is not None:
        return True
    if _state["failed"] is not None and not download:
        return False
    with _lock:
        if _state["model"] is not None:
            return True
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
            kw = {} if download else {"local_files_only": True}
            _state["tokenizer"] = AutoTokenizer.from_pretrained(MODEL, **kw)
            # float32: bfloat16 was measured worse on this CPU (more memory in use, up to 2x slower)
            model = AutoModel.from_pretrained(MODEL, **kw)
            model.eval()
            torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
            _state["model"] = model
            return True
        except Exception as e:  # noqa: BLE001 - offline or not downloaded: keyword search only
            _state["failed"] = f"{type(e).__name__}: {str(e)[:200]}"
            return False


def available() -> bool:
    """True when vector search can run now (switched on and the model is on disk). Loads the model on first call."""
    return enabled() and _load()


def embed(texts: list[str], kind: str = "passage"):
    """L2-normalised e5 embeddings (numpy float32, one row per text). kind: "query" or "passage"."""
    import numpy as np
    import torch
    if not _load():
        raise RuntimeError(f"embedding model unavailable: {_state['failed']}")
    tok, model = _state["tokenizer"], _state["model"]
    out = []
    with torch.no_grad():
        for i in range(0, len(texts), 32):
            batch = [f"{kind}: {t or ''}" for t in texts[i:i + 32]]
            enc = tok(batch, padding=True, truncation=True, max_length=128, return_tensors="pt")
            hidden = model(**enc).last_hidden_state
            mask = enc["attention_mask"].unsqueeze(-1).to(hidden.dtype)
            vec = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
            out.append(torch.nn.functional.normalize(vec, dim=-1).numpy().astype(np.float32))
    return np.vstack(out) if out else np.zeros((0, DIM), dtype=np.float32)


def _connect(read_only: bool):
    import duckdb
    STORE.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(STORE), read_only=read_only and STORE.exists())
    if not read_only:
        con.execute(f"CREATE TABLE IF NOT EXISTS note_vec (note_id VARCHAR PRIMARY KEY, model VARCHAR, "
                    f"vec FLOAT[{DIM}], created_at TIMESTAMP DEFAULT current_timestamp)")
    return con


def note_scores(question: str, rows) -> dict[str, float]:
    """note_id -> cosine similarity between the question and each note (rows: (note_id, case_id, date, type, text)).
    Notes not yet in the store are embedded and stored; similarity is computed by DuckDB when the store is usable."""
    ids = [r[0] for r in rows]
    if not ids:
        return {}
    qvec = embed([question], "query")[0].tolist()
    try:
        con = _connect(read_only=False)
    except Exception:  # noqa: BLE001 - locked by another process: compute in memory
        con = None
    try:
        known = set()
        if con is not None:
            known = {i for (i,) in con.execute(
                "SELECT note_id FROM note_vec WHERE model = ? AND note_id IN (SELECT unnest(?))", [MODEL, ids]).fetchall()}
        missing = [r for r in rows if r[0] not in known and r[0] not in _memory]
        if missing:
            vecs = embed([r[4] for r in missing], "passage")
            if con is not None:
                con.executemany("INSERT OR IGNORE INTO note_vec (note_id, model, vec) VALUES (?, ?, ?)",
                                [(r[0], MODEL, v.tolist()) for r, v in zip(missing, vecs)])
            else:
                _memory.update({r[0]: v.tolist() for r, v in zip(missing, vecs)})
        if con is not None and all(i in known or i not in _memory for i in ids):
            return dict(con.execute(
                f"SELECT note_id, array_cosine_similarity(vec, ?::FLOAT[{DIM}]) FROM note_vec "
                f"WHERE model = ? AND note_id IN (SELECT unnest(?))", [qvec, MODEL, ids]).fetchall())
        import numpy as np
        q = np.asarray(qvec, dtype=np.float32)
        return {i: float(np.dot(q, np.asarray(_memory[i], dtype=np.float32))) for i in ids if i in _memory}
    finally:
        if con is not None:
            con.close()


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--download" in args:
        ok = _load(download=True)
        print(f"{MODEL}: {'ready' if ok else 'failed: ' + str(_state['failed'])}")
        return 0 if ok else 1
    print(f"{MODEL}: {'available' if available() else 'not available: ' + str(_state['failed'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
