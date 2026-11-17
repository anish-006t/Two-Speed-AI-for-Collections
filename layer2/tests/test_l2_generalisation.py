"""Our own questions (tests/questions_own.yaml): lane, metric and breakdown chosen by rules alone.

Needs no database for routing; the metric check uses the planner with menus off (con=None), so it runs anywhere.
"""
from pathlib import Path

import pytest
import yaml

from layer2.guard import rule_check
from layer2.parse import parse
from layer2.router import route
from layer2.semantic import compile_plan, plan
from layer2.sql_guard import check

CASES = yaml.safe_load((Path(__file__).parent / "questions_own.yaml").read_text())


@pytest.mark.parametrize("case", CASES, ids=[c["q"][:50] for c in CASES])
def test_own_question(case):
    q = case["q"]
    refused = rule_check(q) is not None
    if case["route"] == "refuse":
        assert refused, "should be refused"
        return
    assert not refused, "must not be refused"
    p = parse(q, "2026-09-28")
    assert route(p, use_model=False).route == case["route"]
    if case["route"] == "metric_sql" and "generic" in case:
        assert plan(p, con=None, use_model=False) is None, "a certified metric should not have matched"
        return   # the general-aggregate build needs the database: see test_generic_on_db
    if case["route"] == "metric_sql":
        qp = plan(p, con=None, use_model=False)
        assert qp is not None and qp.measure == case["measure"]
        assert qp.dimensions == case.get("dims", [])
        ok, problem, _ = check(compile_plan(qp, p))
        assert ok, problem


GENERIC = [c for c in CASES if "generic" in c]
DB = Path(__file__).resolve().parents[2] / "lake" / "maple.duckdb"


@pytest.mark.skipif(not DB.exists(), reason="needs the built database for column names and menus")
@pytest.mark.parametrize("case", GENERIC, ids=[c["q"][:50] for c in GENERIC])
def test_generic_on_db(case):
    import duckdb

    from layer2.semantic import generic
    try:
        con = duckdb.connect(str(DB), read_only=True)
    except duckdb.Error:
        pytest.skip("database locked")
    gp = generic.build(parse(case["q"], "2026-09-28"), con)
    exp = case["generic"]
    assert gp is not None and gp.view == exp["view"] and gp.agg == exp["agg"]
    if "column" in exp:
        assert gp.column == exp["column"]
    ok, problem, sql = check(generic.to_sql(gp))
    assert ok, problem
    assert con.execute(sql).fetchall()
