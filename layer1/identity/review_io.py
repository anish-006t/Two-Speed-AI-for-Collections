"""Batch review without the app (architecture §7 Tier 3): export a prioritised slice, import decisions, report.

export  writes the top N pairs of identity.review_queue to lake/review/<name>.csv with both records side by side,
        the comparison results and the rule-based explanation. The file holds customer PII: it stays in lake/
        (git-ignored) and must not be emailed or printed.
import  reads a reviewed CSV (columns key_l, key_r, decision, reviewer, reason) and appends valid rows to
        identity.review_decisions. Decisions must be link, no_link or unsure; every row needs a reviewer name;
        unknown pairs are rejected. They apply from the next `python -m layer1.identity.run`.
status  prints counts only.

Usage:
  python -m layer1.identity.review_io export --db lake/dev.duckdb --top 200 [--only-open-cases]
  python -m layer1.identity.review_io import --db lake/dev.duckdb lake/review/batch.csv
  python -m layer1.identity.review_io status --db lake/dev.duckdb
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from layer1.identity import explain
from layer1.pipeline import config
from layer1.pipeline.db import connect

FIELDS = ["first_name", "middle_initial", "last_name", "name_suffix", "dob", "phone_e164", "phone_first6",
          "phone_last2", "postal_code", "email"]
DECISIONS = {"link", "no_link", "unsure"}


def export(con, top, only_open, out_dir):
    q = con.execute(f"""
        SELECT q.priority, m.*
        FROM identity.review_queue q JOIN identity.match_pairs m USING (key_l, key_r)
        WHERE NOT EXISTS (SELECT 1 FROM identity.review_decisions d
                          WHERE d.key_l = q.key_l AND d.key_r = q.key_r AND d.decision IN ('link', 'no_link'))
          {"AND q.has_open_case" if only_open else ""}
        ORDER BY q.priority LIMIT {int(top)}""").df()
    recs = con.execute("SELECT * FROM identity.splink_input WHERE unique_id IN (SELECT unnest(?))",
                       [list(set(q.key_l) | set(q.key_r))]).df().set_index("unique_id")
    rows = []
    for pair in q.to_dict("records"):
        l, r = recs.loc[pair["key_l"]], recs.loc[pair["key_r"]]
        row = {"priority": pair["priority"], "key_l": pair["key_l"], "key_r": pair["key_r"],
               "match_probability": pair["match_probability"], "constraint_flags": pair["constraint_flags"],
               "explanation": explain.explain(pair)[0]}
        for f in FIELDS:
            row[f"{f}_l"], row[f"{f}_r"] = l.get(f), r.get(f)
        row.update({"decision": "", "reviewer": "", "reason": ""})
        rows.append(row)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"review_batch_{datetime.now():%Y%m%d_%H%M%S}.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    print(f"wrote {len(rows)} pairs to {path} (contains PII: keep it inside lake/)")
    return path


def import_decisions(con, path):
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df = df[df["decision"].str.strip() != ""]
    problems = []
    if not df.empty:
        bad_decision = ~df["decision"].isin(DECISIONS)
        no_reviewer = df["reviewer"].str.strip() == ""
        known = con.execute("SELECT key_l, key_r FROM identity.match_pairs").df()
        merged = df.merge(known.assign(found=True), on=["key_l", "key_r"], how="left")
        unknown = merged["found"].isna().to_numpy()
        for name, mask in [("decision not link/no_link/unsure", bad_decision.to_numpy()),
                           ("missing reviewer name", no_reviewer.to_numpy()), ("pair not found", unknown)]:
            if mask.any():
                problems.append(f"{int(mask.sum())} rows: {name}")
        df = df[~(bad_decision.to_numpy() | no_reviewer.to_numpy() | unknown)]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for r in df.itertuples():
        con.execute("INSERT INTO identity.review_decisions VALUES (?, ?, ?, ?, ?, ?)",
                    [r.key_l, r.key_r, r.decision, r.reviewer.strip(), r.reason or None, now])
    print(f"imported {len(df)} decisions" + (f"; rejected: {'; '.join(problems)}" if problems else ""))
    return len(df), problems


def status(con):
    rows = con.execute("""
        SELECT (SELECT count(*) FROM identity.review_queue) AS queue,
               (SELECT count(*) FROM identity.review_queue WHERE has_open_case) AS queue_open_case,
               (SELECT count(*) FROM identity.review_decisions) AS decisions,
               (SELECT count(*) FROM identity.review_decisions WHERE decision = 'link') AS links,
               (SELECT count(*) FROM identity.review_decisions WHERE decision = 'no_link') AS kept_apart,
               (SELECT count(DISTINCT reviewer) FROM identity.review_decisions) AS reviewers""").df()
    print(rows.to_string(index=False))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["export", "import", "status"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--db", default=str(config.DEV_DB))
    ap.add_argument("--top", type=int, default=200)
    ap.add_argument("--only-open-cases", action="store_true")
    args = ap.parse_args(argv)
    con = connect(args.db)
    con.execute("CREATE SCHEMA IF NOT EXISTS identity")
    from layer1.identity.decisions import PERSISTENT_DDL
    con.execute(PERSISTENT_DDL)
    if args.action == "export":
        export(con, args.top, args.only_open_cases, config.LAKE / "review")
    elif args.action == "import":
        if not args.file:
            raise SystemExit("import needs the reviewed CSV path")
        import_decisions(con, Path(args.file))
    else:
        status(con)
    con.close()


if __name__ == "__main__":
    main()
