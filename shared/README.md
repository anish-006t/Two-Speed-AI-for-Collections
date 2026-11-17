# shared/: code every layer uses (owner: Madhur; changes by reviewed pull request)

Setup: `cp .env.example .env`, pick `LLM_PROVIDER` (default in the example: `groq`, free tier) and paste its key
(`GROQ_API_KEY`, or `ANTHROPIC_API_KEY` for `anthropic`), then `uv sync --all-groups`. Check it with
`uv run python -m shared.llm`: it lists the provider's models and makes one tiny call.
Tests: `uv run pytest layer4/tests` (no key or database needed).

| Module | Use it for | Main functions |
|---|---|---|
| `config.py` | Settings from `.env` | `model_fast()`, `model_smart()`, `db_path()`, `cache_dir()`, `offline()` |
| `llm.py` | **Every** LLM call (team rule 1) | `complete(prompt, model=None, system=None, json_schema=None, max_tokens=1024, tag="")`, `usage_summary()` |
| `decide.py` | Typed questions on text | `decide(text, question) -> {"answer", "prob", "evidence", "model_version"}`; `decide_many(text, questions)` answers them all in one call |
| `db.py` | The DuckDB connection | `connect(read_only=True, path=None)`, `table_exists(con, schema, table)`, `close_all()` |
| `audit.py` | Logging what people and models did | `log(event) -> event_id`, `recent(n=100, case_id=None)` |

## `llm.complete`

- **Providers:** `LLM_PROVIDER=anthropic | groq | cerebras | ollama | openai_compatible`.
  - The non-Anthropic ones use an OpenAI-compatible API: JSON mode, with the schema in the prompt and the answer
    checked afterwards.
  - Calls are paced (`LLM_MIN_INTERVAL_S`; Groq defaults to 2.1 s) and retried on rate limits.
  - The provider is part of the cache key, so answers from different providers never mix.

- **Models:** `model` is `None` or `"fast"` (`LLM_MODEL_FAST`), `"smart"` (`LLM_MODEL_SMART`), or an exact model ID.
- **Tags:** give every call a `tag` such as `l2.policy` or `l3.extract`. Usage is reported per tag.
- **Cache:** same inputs, same answer, read from `lake/llm_cache/`. Reruns cost nothing.
- **Offline:** `LLM_OFFLINE=1` answers only from the cache and raises `LLMCacheMiss` otherwise. Use it for tests
  and demo reruns.
- **Structured output:** `json_schema` gives a dict back. Every object in the schema needs
  `"additionalProperties": false` and a `"required"` list.
- **Call log:** `lake/llm_calls.jsonl` has one line per call (tag, model, tokens, latency, cache hit). It never
  holds prompt text.
- **Errors:** a model refusal raises `LLMRefusal` and is not cached.
- **What you may send:** redacted text only (`silver.agent_notes_redacted`, `silver.transcript_turns_redacted`).
  Never send raw PII or whole customer tables.

## `decide.decide`

```python
q = {"id": "hardship", "version": 1, "type": "choice", "options": ["none", "possible", "clear"],
     "wording": "Does the customer describe financial hardship?"}
decide(turn_text, q)   # {"answer": "clear", "prob": 0.93, "evidence": "I lost my job", "model_version": "llm:...|q:hardship@1"}
```

- **Question types:**

  | Type | Answer |
  |---|---|
  | `yes_no` | bool |
  | `choice` | one of `options` |
  | `score` | float in [`min`, `max`], default 0 to 1 |
  | `extract` | str |

  A plain string question is treated as `yes_no`.
- **Unknown:** an unknown answer comes back as `None`.
- **Evidence:** if the quote is not found in the text, it is dropped and `prob` is capped at 0.5.
- **Backend:** Jev when `JEV_API_KEY` is set (not wired yet: it raises), otherwise the LLM.

## `db.connect`

- **Path:** `DHANDHO_DB`, default `lake/maple.duckdb`. Settings are a 4 GB memory limit, 8 threads, and spill to
  `lake/tmp`.
- **One connection per process:** there is one connection per file per process, and you get cursors on it. This
  means a Streamlit app that writes (audit, summaries) can still serve read-only callers.
- **One writer:** DuckDB allows one writer per file across processes. Close the app before running a pipeline step.

## `audit.log`

```python
audit.log({"action": "summary_approved", "role": "agent", "case_id": "CS-...", "prob": 0.91, "approved_by": "lead",
           "output": {"hardship": "clear"}, "reason": "agent edited next step"})
```

- **Columns:** event_id, ts, user, role, action, case_id, golden_customer_id, input_ref, output, prob,
  approved_by, reason.
- **Append-only.**
- **Refused events:** keys naming PII or protected attributes, emails and phone numbers in `output` or `reason`,
  and unknown fields. Each raises `AuditRejected`.
