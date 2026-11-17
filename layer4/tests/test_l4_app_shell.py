"""The app shell opens with and without a database; the one agent role (4 Oct, owner decision) sees every page."""
from pathlib import Path

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from app import shell
from shared import db

HOME = str(Path(__file__).resolve().parents[2] / "app" / "Home.py")


@pytest.fixture
def no_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DHANDHO_DB", str(tmp_path / "missing.duckdb"))
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "llm_cache"))
    db.close_all()
    yield tmp_path
    db.close_all()


@pytest.fixture
def small_db(no_db, monkeypatch):
    path = no_db / "small.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE SCHEMA gold; CREATE TABLE gold.cases AS SELECT 'CS-1' AS case_id")
    con.close()
    monkeypatch.setenv("DHANDHO_DB", str(path))
    return path


def test_one_agent_role_sees_every_page():
    assert list(shell.ROLES) == ["agent"]
    assert [p.title for p in shell.pages_for("agent")] == ["Collections 360", "Ask Collections", "Feature Store",
                                                           "Agent Assist", "Review and Governance"]


@pytest.mark.parametrize("role", list(shell.ROLES))
def test_home_runs_without_a_database(no_db, role):
    at = AppTest.from_file(HOME, default_timeout=30)
    at.session_state["role"] = role
    at.run()
    assert not at.exception
    assert any("No database" in e.value for e in at.error)
    assert not (no_db / "missing.duckdb").exists()  # opening the app must not create an empty database


def test_home_shows_table_counts_with_a_database(small_db):
    at = AppTest.from_file(HOME, default_timeout=30)
    at.run()
    assert not at.exception and not at.error
    table = at.dataframe[0].value
    assert table.set_index("table").loc["gold.cases", "rows"] == "1"
    assert table.set_index("table").loc["layer4.summaries", "rows"] == "not built yet"


@pytest.mark.parametrize("page", ["4_Agent_Assist.py", "5_Review_and_Governance.py"])
def test_layer4_pages_render_for_the_agent(small_db, page):
    at = AppTest.from_file(str(Path(HOME).parent / "pages" / page), default_timeout=30)
    at.session_state["role"] = "agent"
    at.run()
    assert not at.exception and not any("This page is for" in w.value for w in at.warning)
