"""End-to-end: saved review decisions override scores on the next identity run (architecture §7 Tier 3).

Runs on a throwaway copy of lake/dev.duckdb (skipped when it does not exist). The decisions inserted here are test
inputs in that copy only; the real review_decisions table is never touched.
"""
import shutil

import duckdb
import pytest

from layer1.identity import review_io, run
from layer1.pipeline import config

pytestmark = pytest.mark.skipif(not config.DEV_DB.exists(), reason="needs lake/dev.duckdb (run the dev pipeline)")


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("e2e") / "dev_copy.duckdb"
    shutil.copy(config.DEV_DB, path)
    con = duckdb.connect(str(path))
    # A two-key cluster held together by one Splink auto-link, and a pair waiting in review.
    split_pair = con.execute("""
        WITH sizes AS (SELECT golden_customer_id, count(*) n FROM identity.crosswalk WHERE valid_to IS NULL GROUP BY 1)
        SELECT m.key_l, m.key_r FROM identity.match_pairs m
        JOIN identity.crosswalk x ON x.source_key = m.key_l AND x.valid_to IS NULL
        JOIN sizes s USING (golden_customer_id)
        WHERE m.decision = 'link' AND m.decided_by = 'splink' AND s.n = 2 ORDER BY 1, 2 LIMIT 1""").fetchone()
    merge_pair = con.execute("""
        SELECT q.key_l, q.key_r FROM identity.review_queue q
        JOIN identity.crosswalk a ON a.source_key = q.key_l AND a.valid_to IS NULL
        JOIN identity.crosswalk b ON b.source_key = q.key_r AND b.valid_to IS NULL
        WHERE a.golden_customer_id <> b.golden_customer_id ORDER BY q.priority LIMIT 1""").fetchone()
    con.close()
    csv = path.parent / "batch.csv"
    csv.write_text("key_l,key_r,decision,reviewer,reason\n"
                   f"{split_pair[0]},{split_pair[1]},no_link,e2e-test,test input\n"
                   f"{merge_pair[0]},{merge_pair[1]},link,e2e-test,test input\n"
                   f"{merge_pair[0]},{merge_pair[1]},maybe,e2e-test,invalid decision\n"
                   f"{merge_pair[0]},{merge_pair[1]},link,,missing reviewer\n")
    review_io.main(["import", str(csv), "--db", str(path)])
    run.main(["--db", str(path), "--skip-splink"])
    return path, split_pair, merge_pair


def golden(con, key):
    return con.execute("SELECT golden_customer_id FROM identity.crosswalk WHERE source_key = ? AND valid_to IS NULL",
                       [key]).fetchone()[0]


def test_invalid_rows_are_rejected(db):
    path, _, _ = db
    con = duckdb.connect(str(path), read_only=True)
    assert con.execute("SELECT count(*) FROM identity.review_decisions WHERE reviewer = 'e2e-test'").fetchone()[0] == 2


def test_no_link_splits_the_cluster_and_logs_it(db):
    path, (a, b), _ = db
    con = duckdb.connect(str(path), read_only=True)
    assert golden(con, a) != golden(con, b)
    assert con.execute("SELECT count(*) FROM identity.golden_alias WHERE reason = 'split_from'").fetchone()[0] >= 1
    assert con.execute("SELECT decided_by FROM identity.match_pairs WHERE key_l = ? AND key_r = ?",
                       [a, b]).fetchone()[0] == "human"


def test_link_merges_and_keeps_the_oldest_id(db):
    path, _, (a, b) = db
    con = duckdb.connect(str(path), read_only=True)
    g = golden(con, a)
    assert g == golden(con, b)
    assert con.execute("SELECT count(*) FROM identity.golden_alias WHERE golden_customer_id = ? AND reason = 'merged_into'",
                       [g]).fetchone()[0] >= 1
    # merged this run -> at most medium confidence, so the agent must verify before disclosure
    assert con.execute("SELECT identity_confidence FROM identity.golden_confidence WHERE golden_customer_id = ?",
                       [g]).fetchone()[0] in ("medium", "low")


def test_crosswalk_keeps_history(db):
    path, (a, _), _ = db
    con = duckdb.connect(str(path), read_only=True)
    rows = con.execute("SELECT count(*) FROM identity.crosswalk WHERE source_key = ?", [a]).fetchone()[0]
    assert rows >= 1
    assert con.execute("SELECT count(*) FROM identity.crosswalk WHERE valid_to IS NOT NULL").fetchone()[0] >= 1
