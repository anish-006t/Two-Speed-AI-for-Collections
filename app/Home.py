"""The team app: `uv run streamlit run app/Home.py`. Navigation, the story of the system and its status. One role: every
user is an agent with access to every page (4 Oct, owner decision).
Each layer owns its page file in app/pages/ (see app/README.md); a slot whose file doesn't exist shows a placeholder."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # streamlit puts app/ on the path, not the repo root
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from app import shell  # noqa: E402
from shared import config, db, llm  # noqa: E402

st.set_page_config(page_title="dhandho.exe · Maple Collections", page_icon=":material/account_balance:",
                   layout="wide", initial_sidebar_state="expanded")

STATUS_TABLES = ("gold.c360_customer", "gold.cases", "silver.transcript_turns_redacted", "layer3.text_features",
                 "layer4.summaries", "layer4.review_queue", "audit.events")
DEMO_CASE, DEMO_AGENT, DEMO_CALL = "CS-2026-971262", "AG-7816", "1010325"

USE_CASES = [
    ("Collections 360", ":material/person_search:",
     "One trusted record per real customer, built from 3.5 million messy source records. Every match carries a "
     "confidence; below high, the agent must verify identity before talking about money. Personal details are "
     "masked, and notes and transcripts are redacted before anyone, or any model, reads them."),
    ("Ask Collections", ":material/forum:",
     "Plain-English questions answered with the SQL shown, on the bank's 15 certified metric definitions. Policy "
     "questions are answered with citations. Questions about protected groups are refused."),
    ("Feature Store", ":material/dataset:",
     "Each customer fact is defined once and used both to train the promise-break model and to score live, using "
     "only what was known at the time. Typed questions read calls and notes with a probability and the quote."),
    ("Agent Assist", ":material/support_agent:",
     "Before the call, a brief with red flags, allowed hardship options and promise risk. During it, live prompts "
     "on the turn the customer speaks, a promise check and a QA checklist. After it, a summary the agent approves."),
]


USE_CASE_PAGES = {"Collections 360": "pages/1_Customer_360.py", "Ask Collections": "pages/2_Ask_Collections.py",
                  "Feature Store": "pages/3_Feature_Store.py", "Agent Assist": "pages/4_Agent_Assist.py"}
DEMO_STEPS = [
    ("Open the demo case", f"Agent Assist → **Demo case** (case `{DEMO_CASE}`, agent `{DEMO_AGENT}`)."),
    ("Read the brief", "Red flags with policies, allowed hardship options, the debt and the promise-break risk "
                       "with three reasons and a safer date. Case `CS-2026-200193` shows the identity lock."),
    ("Replay the call", f"Call `{DEMO_CALL}` turn by turn: hardship is flagged on the turn it is said, the $400 "
                        "promise is checked, and the QA checklist ticks."),
    ("Approve the summary", "Check the drafted fields and approve. Back in the brief it is under History."),
    ("Review and audit", "Low-confidence items wait in the review queue: accept, or override with a reason. "
                         "Every action is in the audit trail."),
]


def kpis(con) -> list[tuple]:
    """Headline numbers for the overview (counts only, no customer data)."""
    def one(sql):
        try:
            return con.execute(sql).fetchone()[0]
        except Exception:  # noqa: BLE001 - a table not built yet shows a dash
            return None
    review_open = one("SELECT count(*) FROM layer4.review_queue WHERE status = 'open'") \
        if db.table_exists(con, "layer4", "review_queue") else 0
    approved = one("SELECT count(*) FROM layer4.summaries WHERE status = 'approved'") \
        if db.table_exists(con, "layer4", "summaries") else 0
    with_call = one("""SELECT count(*) FROM gold.cases k WHERE k.outcome IS NULL AND EXISTS
                       (SELECT 1 FROM bronze.call_transcripts t WHERE t.case_id = k.case_id)""")
    return [
        ("Customers", one("SELECT count(*) FROM gold.c360_customer"), "One golden record per real person", "users"),
        ("Open cases", one("SELECT count(*) FROM gold.cases WHERE outcome IS NULL"), "In collections today", "folder"),
        ("With a recorded call", with_call, "Open cases you can replay in Agent Assist", "phone"),
        ("Waiting for review", review_open, "AI output below 0.75 confidence", "alert",
         "Queue clear" if not review_open else "Needs a person", "ok" if not review_open else "warn"),
        ("Summaries approved", approved, "Approved by agents and written back", "check"),
    ]


def home():
    shell.page_header("Collections overview",
                      "Fast typed decisions on every call, with a person in charge of every outcome.")
    con = shell.get_con()
    if con is None:
        st.error(f"No database at `{db.db_path()}`. Build Layer 1 first: `uv run python -m layer1.build --target "
                 "full` (or set `DHANDHO_DB=lake/dev.duckdb` in `.env`).")
    else:
        shell.stat_cards(kpis(con))

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.html('<div class="ds-section-title">Workflows</div><div class="ds-section-desc">Every page is open to '
                'you as an agent.</div>')
        with st.container(border=True):
            for title, icon, text in USE_CASES:
                a, b = st.columns([5, 1.3], vertical_alignment="center")
                a.html(f'<div class="ds-row-title">{title}</div><div class="ds-row-desc">{text}</div>')
                b.page_link(USE_CASE_PAGES[title], label="Open", icon=icon)
    with right:
        st.html('<div class="ds-section-title">Demo path</div><div class="ds-section-desc">Five steps, about three '
                'minutes.</div>')
        with st.container(border=True):
            shell.html_steps([(h, t.replace("**", "").replace("`", "")) for h, t in DEMO_STEPS])
            st.page_link("pages/4_Agent_Assist.py", label="Start in Agent Assist", icon=":material/play_circle:")
        st.html('<div class="ds-section-title">Safeguards</div>')
        with st.container(border=True):
            shell.html_checks(["Nothing in this app contacts a customer.",
                               "Every suggestion has a plain-English reason; a person approves, confirms or "
                               "overrides it.",
                               "Names, phones and emails are masked; debt stays hidden until identity is verified.",
                               "Questions about protected groups are refused, and every action is logged."])

    with st.expander("System status", icon=":material/monitor_heart:"):
        if con is not None:
            rows = []
            for name in STATUS_TABLES:
                schema, table = name.split(".")
                n = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0] \
                    if db.table_exists(con, schema, table) else None
                rows.append({"table": name, "rows": "not built yet" if n is None else f"{n:,}"})
            st.dataframe(pd.DataFrame(rows), hide_index=True)
        st.dataframe(pd.DataFrame(model_status()), hide_index=True)
        usage = llm.usage_summary()
        st.caption("Model calls so far (cache hits are free):")
        if usage:
            st.dataframe(pd.DataFrame(usage), hide_index=True)
        else:
            st.caption("None yet.")


def model_status() -> list[dict]:
    """What runs where, read from settings and files only: this page never calls a model."""
    meta_path = ROOT / "layer4" / "models" / "promise_break" / "metadata.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        promise = f"{meta['model_version']} · test AUC {meta['metrics']['auc']:.3f}"
    except (OSError, ValueError, KeyError):
        promise = "baseline (no trained model file)"
    text = (f"{config.llm_provider()} · {config.model_fast()}" if config.llm_available()
            else "offline rules (no model key in .env)")
    return [{"component": "Call and note understanding (Layer 3 extract)", "running": text},
            {"component": "Promise-break risk", "running": promise},
            {"component": "Summary line", "running": "rules from the approved fields (no model)"},
            {"component": "Hardship options", "running": "policy rules from hardship_programs (no model)"}]


def placeholder(slot: shell.PageSlot):
    def page():
        st.title(slot.title)
        st.info(f"This page is not built yet ({slot.deliverable}). Add `app/pages/{slot.file}` to fill this slot.")
    return page


shell.apply_style()
with st.sidebar:
    shell.brand()
    st.caption("Signed in as **Agent** · access to every page")
    if "user" not in st.session_state:
        st.session_state["user"] = config.default_user() if config.default_user() != "unknown" else "demo-user"
    st.text_input("Your name (for the audit log)", key="user")
    shell.ai_mode_control()
    st.caption(f"Database: `{db.db_path().name}`")

pages = []
for slot in shell.pages_for(shell.get_role()):
    file = shell.APP / "pages" / slot.file
    if file.exists():
        pages.append(st.Page(str(file), title=slot.title, icon=slot.icon))
    else:
        pages.append(st.Page(placeholder(slot), title=slot.title, icon=slot.icon,
                             url_path=slot.file.removesuffix(".py").split("_", 1)[1].lower()))

st.navigation({"": [st.Page(home, title="Home", icon=":material/home:", default=True)], "Pages": pages}).run()
