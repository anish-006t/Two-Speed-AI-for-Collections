"""Build Layer 3 in one command: history tables, batch text features, measured accuracy, feature registry.

    uv run python -m layer3.build                      # DHANDHO_DB, default lake/maple.duckdb
    uv run python -m layer3.build --db lake/dev.duckdb
    uv run python -m layer3.build --skip-text          # history and registry only (minutes)

Runs after Layer 1 (`python -m layer1.build`). Writes only schema layer3. Train the promise-break model afterwards
with `python -m layer4.models.promise_break.train`. Close the app first: DuckDB allows one writer.
"""
import argparse
import time

from layer3.features import batch_text, history
from layer3.features.defs import publish_registry
from shared.db import connect


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db")
    ap.add_argument("--skip-text", action="store_true", help="skip the batch text features")
    ap.add_argument("--note-months", type=int, default=12)
    ap.add_argument("--limit", type=int, help="only the first N transcripts and notes (development)")
    ap.add_argument("--extractor", choices=["auto", "llm", "rules"], default="auto",
                    help="auto: LLM when an API key is set, else rules; llm: require the LLM (fail without a key)")
    args = ap.parse_args(argv)
    import os
    os.environ["L3_EXTRACTOR_MODE"] = args.extractor
    con = connect(read_only=False, path=args.db) if args.db else connect(read_only=False)
    t = time.time()
    counts = history.prepare_history(con)
    print(f"history built in {time.time() - t:.0f} s: " + ", ".join(f"{k} {v:,}" for k, v in counts.items()))
    if not args.skip_text:
        t = time.time()
        n = batch_text.build_text_features(con, note_months=args.note_months, limit=args.limit)
        print(f"text features: {n:,} rows in {time.time() - t:.0f} s")
    print(f"feature registry: {publish_registry(con)} features")


if __name__ == "__main__":
    main()
