# app/: the team Streamlit app (shell owned by Madhur)

## Run it

```bash
uv sync --all-groups
uv run python -m layer1.build --target full      # once: Layer 1 (about 16 min), skip if lake/maple.duckdb exists
uv run python -m layer3.build --extractor rules  # once: Layer 3 history and text features (about 6 min)
uv run streamlit run app/Home.py                 # opens http://localhost:8501
```

- **Close the app before running any build step:** DuckDB allows one writer, and the app holds the file.
- **One role: Agent** (owner decision, 4 Oct). The agent sees every page; the sidebar only asks your name for the
  audit log. Privacy rules still apply to the agent: names, phones and emails masked, debt hidden until identity is
  verified, protected traits masked and refused, no bulk personal-data export.
- **Human review** is unchanged: confidence gates on the call (Agent Assist), summary approval with a reason for any
  change, and the review queue (Review and Governance). Any agent may review any item, including their own; every
  decision is audited with the reviewer's name. There is no four-eyes rule (no code enforced it before either).

## How call text is understood (sidebar)

| Setting | What it does | Cost |
|---|---|---|
| **Cached model answers only** (default) | Uses model answers already in `lake/llm_cache/`. Anything not cached falls back to the offline rules. **Never calls an API.** | free |
| **Model (live, free plan)** | Calls the provider in `.env` (`LLM_PROVIDER=groq`, gpt-oss-20b) for anything not cached, then caches it | free plan; limits return errors, not charges |
| **Offline rules** | Layer 3's keyword rules only | free |

The demo call `1010325` is fully cached, so it plays with the model's answers in the default setting.

## Demo path

1. **Agent Assist → Demo case** (case `CS-2026-971262`, agent `AG-7816`). The **Agent** picker defaults to **All
   agents**, so any open case can be picked or typed.
2. **1 · Brief:**
   - red flags with their policies;
   - hardship options from the programme rules;
   - the debt, with the promise-break risk, three reasons and a safer date;
   - to see the identity lock, pick case `CS-2026-200193` (low confidence): the debt stays hidden until you press
     **Identity verified**.
3. **Calls on this case → Replay** (or **2 · Call**). Press **Next turn** or **Play to end**:
   - hardship is flagged at 43.9 s, exactly when the customer says it;
   - the $400 promise is scored at 88% risk, with a safer date;
   - the QA checklist ticks as the agent goes.
4. **Draft the summary** (3 · Summary). It matches the gold label. Change a field if you like (a reason is then
   required), then **Approve**. **Back to the brief** shows it under History.
5. **Review and Governance:** the review queue (accept, or override with a reason), approved summaries with their
   edits, and the audit trail.
6. **Customer 360** (`CS-2026-922626` or `GC00778254`), **Ask Collections** (Use AI on or off in its sidebar) and
   **Feature Store** (`GC00386898`) are open to the same agent.

## Pages

| Page | Built by | What the agent does there |
|---|---|---|
| `Home.py` (name, AI mode, navigation, demo path, status) | Madhur | start here |
| `pages/1_Customer_360.py` | Anish | case or golden ID; masked case brief behind the identity gate |
| `pages/2_Ask_Collections.py` | Het | plain-English questions with SQL and sources; Use AI toggle |
| `pages/3_Feature_Store.py` | Ayush | features for a customer, registry |
| `pages/4_Agent_Assist.py` | Madhur | brief, live call replay, summary approval |
| `pages/5_Review_and_Governance.py` | Madhur | review queue, approved summaries, audit trail |

## Adding or changing a page

- **Start your page with:**
  ```python
  from app.shell import get_con, get_role, get_user
  con = get_con()                         # the app's one connection; None until the lake is built
  ```
- **Writing:** pages write only their own layer's schema. Log actions with `shared.audit.log(...)`.
- **Pages** are listed in `app/shell.py` (`PAGES`); every page is open to the one agent role.
- **Theme:** `.streamlit/config.toml`.
