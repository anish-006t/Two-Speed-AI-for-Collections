"""App shell helpers every page uses: the user from the sidebar, the page registry, one DB connection.

One role (4 Oct, owner decision): every user is an **agent** with access to every page and feature. Privacy rules
still apply inside the pages (masked contact details, identity gate, protected traits masked and refused), and every
view and decision is audited with the user's name.

    from app.shell import get_con, get_user
    con = get_con()                          # None until the lake is built
"""
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from shared import config, db

APP = Path(__file__).resolve().parent
ROLES = {"agent": "Agent"}          # one role with access to everything (was agent / team lead / analyst)


@dataclass(frozen=True)
class PageSlot:
    file: str            # under app/pages/
    title: str
    owner: str
    deliverable: str
    roles: tuple
    icon: str


PAGES = (
    PageSlot("1_Customer_360.py", "Collections 360", "Anish (Layer 1)", "L1-8", ("agent",),
             ":material/person_search:"),
    PageSlot("2_Ask_Collections.py", "Ask Collections", "Het (Layer 2)", "L2", ("agent",),
             ":material/forum:"),
    PageSlot("3_Feature_Store.py", "Feature Store", "Ayush (Layer 3)", "L3-8", ("agent",),
             ":material/dataset:"),
    PageSlot("4_Agent_Assist.py", "Agent Assist", "Madhur (Layer 4)", "L4-3 to L4-5", ("agent",),
             ":material/support_agent:"),
    PageSlot("5_Review_and_Governance.py", "Review and Governance", "Madhur (Layer 4)", "L4-6, L4-7",
             ("agent",), ":material/fact_check:"),
)


def pages_for(role: str) -> list[PageSlot]:
    return [p for p in PAGES if role in p.roles]


def get_role() -> str:
    return "agent"


def get_user() -> str:
    return st.session_state.get("user") or config.default_user()


def require_role(*roles: str) -> None:
    """Kept for page code that still calls it: the single agent role may open every page, so it never stops."""
    return None


@st.cache_resource(show_spinner=False)
def _connection(path: str):
    return db.connect(read_only=False, path=path)


def get_con():
    """This app's read-write connection (audit, Layer 4 tables), or None if the database isn't built yet.
    While the app runs it holds the file: close it before running a pipeline step."""
    path = db.db_path()
    if not path.exists():  # never create an empty database by connecting
        return None
    return _connection(str(path))


# ---------------------------------------------------------------------------------------- how text is understood
AI_MODES = {
    "model": "Model (live, free plan)",
    "cached": "Cached model answers only",
    "rules": "Offline rules",
}


# Look and feel from design-system-starter (tokens in .streamlit/config.toml): Inter, its type scale and its dark
# palette. Only what the theme cannot express lives here: stat cards like its StatCard, card surfaces, the sidebar
# navigation, and the browser surfaces (selection, focus ring, scrollbars, tabular numbers).
STYLE = """
<style>
:root { --ds-page: #141414; --ds-wash: #191919; --ds-elevated: #1F1F1F; --ds-border: rgba(255,255,255,.10);
        --ds-border-soft: rgba(255,255,255,.06); --ds-text: rgba(255,255,255,.92); --ds-text-2: rgba(255,255,255,.55);
        --ds-text-3: rgba(255,255,255,.38); --ds-accent: #4489FF; --ds-accent-text: #49B0FF;
        --ds-accent-tint: rgba(68,137,255,.12); --ds-tint: rgba(255,255,255,.05);
        --ds-shadow: 0 1px 2px rgba(0,0,0,.40), 0 2px 6px rgba(0,0,0,.18);
        --ds-ease: cubic-bezier(.16,1,.3,1); }

/* browser surfaces */
::selection { background: rgba(68,137,255,.35); color: #fff; }
* { scrollbar-width: thin; scrollbar-color: rgba(255,255,255,.18) transparent; }
:focus-visible { outline: 2px solid var(--ds-accent) !important; outline-offset: 2px; }
[data-testid="stMetricValue"], [data-testid="stDataFrame"], .ds-stat-value, code { font-variant-numeric: tabular-nums; }
button, a, [data-baseweb="tab"], [data-testid="stSidebarNav"] a {
  transition: background-color 180ms var(--ds-ease), color 180ms var(--ds-ease), border-color 180ms var(--ds-ease),
              box-shadow 180ms var(--ds-ease); }

/* page frame and type */
.block-container { padding-top: 4rem; padding-bottom: 3rem; max-width: 1440px; }
h1 { letter-spacing: -.02em; }
h2, h3, h4 { letter-spacing: -.01em; }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p { color: var(--ds-text-2); font-size: .8125rem; }

/* metrics as the design's StatCard */
[data-testid="stMetric"] { background: var(--ds-elevated); border: 1px solid var(--ds-border); border-radius: .75rem;
                           padding: 1rem; box-shadow: var(--ds-shadow); }
[data-testid="stMetricLabel"] p { font-size: .75rem; color: var(--ds-text-2); font-weight: 500; }
[data-testid="stMetricValue"] { font-size: 1.5rem; font-weight: 600; letter-spacing: -.02em; color: #fff; }

/* cards, expanders, alerts, tables */
[data-testid="stVerticalBlockBorderWrapper"] { box-shadow: var(--ds-shadow); background: var(--ds-elevated); }
[data-testid="stExpander"] details { border-color: var(--ds-border); border-radius: .75rem; background: var(--ds-wash); }
[data-testid="stAlert"] { border-radius: .625rem; }
[data-testid="stAlert"] p, [data-testid="stAlert"] li { font-size: .875rem; line-height: 1.375rem; }
[data-testid="stAlertContainer"] { border: 1px solid var(--ds-border-soft); }
[data-testid="stDataFrame"] { border-radius: .75rem; overflow: hidden; }

/* tabs and segmented controls */
[data-baseweb="tab-list"] { gap: 1.25rem; border-bottom: 1px solid var(--ds-border-soft); }
[data-baseweb="tab"] p { font-size: .8125rem; font-weight: 500; }

/* buttons: the design's secondary and primary */
[data-testid="stBaseButton-secondary"] { background: var(--ds-tint); border-color: var(--ds-border); }
[data-testid="stBaseButton-secondary"]:hover { background: rgba(255,255,255,.08); border-color: rgba(255,255,255,.2); }
[data-testid="stBaseButton-primary"] { box-shadow: 0 1px 2px rgba(0,0,0,.35); }

/* sidebar: page background, quiet nav, active item as the design's selected tab */
[data-testid="stSidebar"] { border-right: 1px solid var(--ds-border-soft); }
[data-testid="stSidebarNav"] a { border-radius: .5rem; margin: 1px 0; border: 1px solid transparent; }
[data-testid="stSidebarNav"] a span { font-size: .8125rem; font-weight: 500; color: var(--ds-text-2); }
[data-testid="stSidebarNav"] a:hover { background: var(--ds-tint); }
[data-testid="stSidebarNav"] a:hover span { color: var(--ds-text); }
[data-testid="stSidebarNav"] a[aria-current="page"] { background: var(--ds-tint); border-color: rgba(255,255,255,.08); }
[data-testid="stSidebarNav"] a[aria-current="page"] span { color: #fff; }
[data-testid="stSidebarNavSeparator"] { border-color: var(--ds-border-soft); }
.ds-brand { display: flex; align-items: center; gap: .625rem; padding: .25rem 0 .75rem 0; }
.ds-brand-mark { width: 1.75rem; height: 1.75rem; border-radius: .5rem; background: var(--ds-accent); display: flex;
                 align-items: center; justify-content: center; color: #fff; font-weight: 700; font-size: .875rem;
                 box-shadow: var(--ds-shadow); }
.ds-brand-name { font-size: .8125rem; font-weight: 600; color: #fff; line-height: 1.1rem; }
.ds-brand-sub { font-size: .625rem; color: var(--ds-text-3); }

/* page header */
.ds-header { display: flex; flex-direction: column; gap: .25rem; margin: 0 0 1.5rem 0; }
.ds-title { font-size: 1.25rem; font-weight: 700; letter-spacing: -.02em; color: #fff; line-height: 1.875rem; }
.ds-sub { font-size: .8125rem; color: var(--ds-text-2); max-width: 72ch; }

/* stat cards (StatCard) */
.ds-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }
.ds-stat { background: var(--ds-elevated); border: 1px solid var(--ds-border); border-radius: .75rem; padding: 1rem;
           box-shadow: var(--ds-shadow); display: flex; flex-direction: column; gap: .75rem;
           transition: border-color 180ms var(--ds-ease); }
.ds-stat:hover { border-color: rgba(255,255,255,.2); }
.ds-stat-top { display: flex; align-items: center; justify-content: space-between; }
.ds-stat-title { font-size: .75rem; font-weight: 500; color: var(--ds-text-2); }
.ds-stat-icon { width: 1.75rem; height: 1.75rem; border-radius: .5rem; background: var(--ds-tint); display: flex;
                align-items: center; justify-content: center; color: var(--ds-text); }
.ds-stat-icon img { display: block; opacity: .9; }
.ds-list { display: flex; flex-direction: column; gap: .75rem; font-size: .8125rem; line-height: 1.25rem;
           color: var(--ds-text); }
.ds-li { display: flex; align-items: flex-start; gap: .125rem; }
.ds-li b { color: #fff; font-weight: 600; }
.ds-check { display: inline-flex; margin: .125rem .5rem 0 0; }
.ds-check img { width: 14px; height: 14px; opacity: .7; }
[data-testid="stVerticalBlockBorderWrapper"] hr { margin: .375rem 0 !important; }
.ds-stat-value { font-size: 1.5rem; font-weight: 600; letter-spacing: -.02em; color: #fff; line-height: 2rem; }
.ds-stat-desc { font-size: .6875rem; color: var(--ds-text-3); }
.ds-badge { display: inline-flex; align-items: center; gap: .375rem; font-size: .6875rem; font-weight: 500;
            padding: .125rem .5rem; border-radius: 999px; border: 1px solid; }
.ds-badge-dot { width: .375rem; height: .375rem; border-radius: 999px; background: currentColor; }
.ds-badge.ok { color: #00EC7E; background: rgba(0,236,126,.10); border-color: rgba(0,236,126,.20); }
.ds-badge.warn { color: #F58E3A; background: rgba(245,142,58,.10); border-color: rgba(245,142,58,.20); }
.ds-badge.info { color: #337DF4; background: rgba(51,125,244,.10); border-color: rgba(51,125,244,.20); }

/* rows (SettingRow) */
.ds-row-title { font-size: .875rem; font-weight: 500; color: #fff; display: flex; align-items: center; gap: .5rem; }
.ds-row-desc { font-size: .75rem; color: var(--ds-text-2); line-height: 1.125rem; margin-top: .125rem; }
.ds-section-title { font-size: .9375rem; font-weight: 600; color: #fff; margin: .5rem 0 .125rem 0; }
.ds-section-desc { font-size: .75rem; color: var(--ds-text-2); margin-bottom: .75rem; }
.ds-step { flex: none; display: inline-flex; align-items: center; justify-content: center; width: 1.25rem; height: 1.25rem;
           border-radius: 999px; background: var(--ds-accent-tint); color: var(--ds-accent-text); font-size: .6875rem;
           font-weight: 600; margin-right: .5rem; font-variant-numeric: tabular-nums; }
</style>
"""

# Lucide-style strokes (the design system's icon set: 24px grid, 2px stroke, round caps)
ICONS = {
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "folder": '<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/>',
    "phone": '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"/>',
    "alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
    "check": '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="m9 11 3 3L22 4"/>',
    "x": '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
    "edit": '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
}


def _icon(name: str) -> str:
    """The icon as an <img> data URI: st.html strips inline SVG."""
    from urllib.parse import quote
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#EBEBEB" '
           f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{ICONS.get(name, "")}</svg>')
    return f'<img alt="" width="16" height="16" src="data:image/svg+xml,{quote(svg)}">'


def html_steps(steps: list[tuple[str, str]]) -> None:
    """A numbered list in the design's type (13px), for sequences where the order matters."""
    import html
    rows = "".join(f'<div class="ds-li"><span class="ds-step">{n}</span><div><b>{html.escape(h)}</b>: '
                   f'{html.escape(t)}</div></div>' for n, (h, t) in enumerate(steps, 1))
    st.html(f'<div class="ds-list">{rows}</div>')


def html_checks(lines: list[str]) -> None:
    import html
    rows = "".join(f'<div class="ds-li"><span class="ds-check">{_icon("check")}</span><div>{html.escape(t)}</div>'
                   f'</div>' for t in lines)
    st.html(f'<div class="ds-list">{rows}</div>')


def apply_style() -> None:
    st.html(STYLE)


def brand() -> None:
    """The design's sidebar brand block (accent mark and product name), at the top of the sidebar."""
    st.logo(str(APP / "assets" / "logo.svg"), size="large", icon_image=str(APP / "assets" / "logo_icon.svg"))


def page_header(title: str, subtitle: str = "") -> None:
    """Title block in the design system's style (page title, one-line description)."""
    import html
    st.html(f'<div class="ds-header"><div class="ds-title">{html.escape(title)}</div>'
            + (f'<div class="ds-sub">{html.escape(subtitle)}</div>' if subtitle else "") + "</div>")


def stat_cards(items: list[tuple]) -> None:
    """A row of the design's StatCards: (title, value, description, icon name[, badge text, badge kind])."""
    import html
    cards = []
    for it in items:
        title, value, desc, icon = it[:4]
        badge = (f'<span class="ds-badge {it[5] if len(it) > 5 else "info"}"><span class="ds-badge-dot"></span>'
                 f'{html.escape(str(it[4]))}</span>') if len(it) > 4 and it[4] else ""
        shown = "—" if value is None else (f"{value:,}" if isinstance(value, (int, float)) else html.escape(str(value)))
        cards.append(f'<div class="ds-stat"><div class="ds-stat-top"><span class="ds-stat-title">{html.escape(title)}'
                     f'</span><span class="ds-stat-icon">{_icon(icon)}</span></div><div style="display:flex;'
                     f'align-items:baseline;justify-content:space-between;gap:.5rem"><span class="ds-stat-value">'
                     f'{shown}</span>{badge}</div><div class="ds-stat-desc">{html.escape(desc)}</div></div>')
    st.html(f'<div class="ds-stats">{"".join(cards)}</div>')


def ai_mode_control() -> str:
    """Sidebar switch that decides whether Layer 3's extractor may call the model. 'cached' never makes an API call:
    a question the model has not answered before falls back to the offline rules."""
    import os
    available = config.llm_available()
    options = list(AI_MODES) if available else ["rules"]
    default = st.session_state.get("ai_mode", "cached" if available else "rules")
    mode = st.radio("Understanding call text", options, index=options.index(default) if default in options else 0,
                    format_func=AI_MODES.get, key="ai_mode",
                    help="Cached and Offline make no API calls at all. Model calls the free provider; answers are "
                         "cached, so a repeated question is free.")
    os.environ["L3_EXTRACTOR_MODE"] = "rules" if mode == "rules" else "auto"
    os.environ["LLM_OFFLINE"] = "1" if mode == "cached" else "0"
    if available:
        st.caption(f"{config.llm_provider()} · {config.model_fast()}")
    else:
        st.caption("No model key in .env: offline rules only.")
    return mode
