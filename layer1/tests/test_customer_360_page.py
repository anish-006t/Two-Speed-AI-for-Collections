"""Customer 360 page (brief L1-8): what the one agent role sees (4 Oct, owner decision), the verify gate, the audit trail.

Runs the Streamlit page headlessly on a throwaway copy of lake/dev.duckdb (skipped when it does not exist); the
audit events it writes stay in the copy. Assertions look at masks and section labels, never at customer values.
"""
import re
import shutil

import duckdb
import pytest

from layer1.pipeline import config

pytestmark = pytest.mark.skipif(not config.DEV_DB.exists(), reason="needs lake/dev.duckdb")
st_testing = pytest.importorskip("streamlit.testing.v1")
PAGE = str(config.ROOT / "app" / "pages" / "1_Customer_360.py")


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    # Same file name as the original: dbt-built views name their catalog after the file ("dev"), decisions D-45.
    path = tmp_path_factory.mktemp("c360") / "dev.duckdb"
    shutil.copy(config.DEV_DB, path)
    con = duckdb.connect(str(path), read_only=True)
    verify_case, verify_gid = con.execute("""
        SELECT k.case_id, k.golden_customer_id FROM gold.cases k JOIN gold.c360_customer c USING (golden_customer_id)
        WHERE c.verify_before_disclosure AND k.outcome IS NULL ORDER BY 1 LIMIT 1""").fetchone()
    high_gid = con.execute("""SELECT golden_customer_id FROM gold.c360_customer
                              WHERE identity_confidence = 'high' AND product_count > 1 ORDER BY 1 LIMIT 1""").fetchone()[0]
    con.close()
    return path, verify_case, verify_gid, high_gid


def run_page(monkeypatch, path, query, click=None):
    monkeypatch.setenv("DHANDHO_DB", str(path))
    from app import shell
    shell._connection.clear()
    at = st_testing.AppTest.from_file(PAGE, default_timeout=120)
    at.session_state["role"] = "agent"
    at.session_state["user"] = "pytest"
    at.run()
    at.text_input[0].set_value(query).run()
    if click:
        next(b for b in at.button if b.label == click).click().run()
    assert not at.exception, [e.message for e in at.exception]
    return at


def page_text(at):
    parts = [m.value for m in at.markdown] + [w.value for w in at.warning] + [i.value for i in at.info]
    parts += [str(d.value) for d in at.dataframe]
    return "\n".join(parts)


def debt_table_shown(at):
    return any("overdue (CAD)" in d.value.columns for d in at.dataframe)


def test_agent_sees_masked_contact_and_no_debt_until_verified(monkeypatch, db):
    path, case_id, gid, _ = db
    at = run_page(monkeypatch, path, case_id)
    text = page_text(at)
    assert "Verify before disclosure" in text and "Debt details are hidden" in text
    assert not debt_table_shown(at)                            # no case details before verification
    assert not re.search(r"\+1\d{10}", text)                   # never a full phone number
    at = run_page(monkeypatch, path, case_id, click="I have verified the customer's identity")
    assert debt_table_shown(at)                                # case details shown after verification
    from app import shell        # the page's own connection: DuckDB allows one configuration per file in a process
    actions = {r[0] for r in shell.get_con().execute("SELECT action FROM audit.events WHERE case_id = ?",
                                                     [case_id]).fetchall()}
    assert {"c360_viewed", "identity_verified"} <= actions


def test_golden_id_opens_the_customer_with_a_case_picker(monkeypatch, db):
    path, _, gid, _ = db
    at = run_page(monkeypatch, path, gid)
    assert not at.error
    assert any(s.label == "Case" for s in at.selectbox)        # the customer's cases to pick from


def test_linked_records_are_counts_and_no_dob_or_raw_contacts(monkeypatch, db):
    path, _, _, gid = db
    at = run_page(monkeypatch, path, gid)
    links = next(d.value for d in at.dataframe if "source_system" in d.value.columns)
    assert set(links.columns) == {"source_system", "link_status", "records"}   # merged-record counts, no source keys
    text = page_text(at)
    assert "Date of birth" not in text
    assert not re.search(r"\+1\d{10}", text) and not re.search(r"[\w.]+@[\w.]+\.\w+", text)


def test_unknown_id_shows_an_error(monkeypatch, db):
    path, *_ = db
    at = run_page(monkeypatch, path, "XX-123")
    assert any("No customer found" in e.value for e in at.error)
