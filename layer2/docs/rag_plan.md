# Layer 2 RAG plan: agent notes (and a measured decision on transcripts)

Status: built and measured (4 Oct 2026). Notes use RAG; transcripts keep the regex rules (experiment below). Owner: Het. Read with `architecture.md` and `../CHANGELOG.md`.

## 1. Why

The brief lists "RAG over agent notes and transcripts" under where AI should help. RAG means two steps:
**retrieve** the relevant passages, then **generate** an answer from only those passages, citing them.

| Lane | Retrieval today | Generation today | Gap |
|---|---|---|---|
| Agent notes (`story.py`) | BM25 keyword search inside one case or customer, 5 English topic lists | none: a ranked list of notes | misses French notes and paraphrases, ignores note types, never answers the question |
| Call summaries (`call_summary.py`) | the case's most recent transcript | regex rules extract the facts (96.2% exact on 500 public labels); LLM only if rules find nothing | not RAG, but measurably good; tuned on those 500 labels |
| Policy (`policy.py`) | BM25 over the bank's documents | LLM answer with checked citations | none: **out of scope, unchanged** |

The notes data (`silver.agent_notes_redacted`): 760,594 notes over 235,528 cases (about 3 per case), 47 characters
on average (agent shorthand). Languages: EN 652,052, FR 103,249, mixed 5,293. Note types: call_note 605,093,
system_note 58,166, skip_trace 43,519, hardship_assessment 28,604, supervisor_note 14,579, dispute_note 6,590,
payment_note 4,043.

## 2. Decisions (agreed with the owner)

1. Embedding model: **`intfloat/multilingual-e5-small`** (384 dimensions, English and French, ~470 MB, CPU).
2. RAG for **agent notes**. **Transcripts** switch to RAG **only if** a measured experiment shows it beats the
   regex rules. **Policy** stays as it is.
3. **Lightweight and only where necessary**: no new service, no new packages, no global index, nothing loaded or
   called for questions that do not need it.

## 3. Design

### 3.1 Vector store
- DuckDB, in its own file `lake/layer2_vectors.duckdb` (git-ignored, like all of `lake/`):
  `note_vec(note_id VARCHAR PRIMARY KEY, model VARCHAR, vec FLOAT[384], created_at TIMESTAMP)`.
- Similarity with DuckDB's built-in `array_cosine_similarity` (checked in DuckDB 1.5.6; no `vss` extension).
- A separate file, so it never competes with the single writer of `lake/maple.duckdb`. Writes are best-effort:
  if another process holds the file, vectors are computed in memory and not stored.
- **No index of all 760K notes.** That would take 30-60 minutes of CPU and ~1 GB. A question is about one case or
  customer, so only those notes are embedded, the first time they are asked about, then reused from the store.

### 3.2 Model loading
- `transformers` + `torch` (CPU) are already installed for Laya: **no new dependency**.
- Loaded lazily on the first question that passes gate 2 (below), once per process; never for metric, policy or
  call-summary questions.
- Downloaded once into `~/.cache/huggingface` (outside the repo): `python -m layer2.vectors --download`.
  Without it (offline, or the download failed) everything falls back to keyword search, exactly as today.
- e5 conventions: prefix `"query: "` for questions and `"passage: "` for notes; mean pooling; L2 normalisation;
  max 128 tokens (notes are short); `torch.no_grad()`; batch 32.

### 3.3 Gates: when each part runs

| Gate | Runs when | Cost |
|---|---|---|
| 1. Keyword search (BM25 + note types + French words) | every notes question | free, milliseconds |
| 2. Vector search (hybrid with keyword) | the question asks something ("what / why / did / has / any / is / was …", not "show / list the notes"), **or** keyword search finds fewer than 3 matching notes, **or** the case has French notes | embedding a few notes, ~tens of ms once warm |
| 3. AI-written answer | gate 2 conditions **and** an asking question **and** use_model is on **and** `adapters.llm_available()` | one LLM call |

"Show the notes for case X" never loads the model and never calls the AI.

Kill switches: `L2_VECTORS=0` (keyword only; gates 2 and 3 off). Existing: `L2_NORMALIZE=0`.

### 3.4 Ranking
- Keyword: today's BM25, plus
  - a boost for notes whose `note_type` matches the question's topic: hardship / income / job / sick ->
    `hardship_assessment`; dispute / fee / charge -> `dispute_note`; pay / payment / promise -> `payment_note`;
    complaint / escalate / supervisor -> `supervisor_note`;
  - French topic words reused from `call_summary.REASON_RULES` and `TIGHT` (they already cover French);
  - `system_note` left out unless the question asks for system events;
  - `skip_trace` (locating contact details) only for the agent role, never for analysts.
- Hybrid (gate 2): reciprocal-rank fusion, `score = 1/(60 + rank_keyword) + 1/(60 + rank_vector)`.

### 3.5 The answer (gate 3)
- Top 5 notes (date, ID, type, redacted text) and the wrapped question (`llm_guard.wrap_question`) go to
  `adapters.complete` with schema `{answer: string, cited: [note_id], covered: boolean}`.
- Accepted only if:
  - every cited ID is one of the 5 notes;
  - every number in the answer appears in the cited notes (`llm_guard.ungrounded_numbers`);
  - no personal data (`llm_guard.pii_leaks`);
  - `covered` is true, else the answer is "the notes don't say".
- On any failure, the ranked list as today; the audit log records why.
- The page shows the answer first and the cited notes underneath, always.

### 3.6 Interfaces
```python
# layer2/vectors.py (new)
def available() -> bool                                     # model loadable and L2_VECTORS != "0"
def embed(texts: list[str], kind: str) -> "np.ndarray"      # kind: "query" | "passage"; rows L2-normalised
def note_scores(question: str, rows: list[tuple]) -> dict   # note_id -> cosine; embeds and stores missing notes
# layer2/story.py (changed)
def answer_story(p, role="analyst", use_model=True) -> dict  # same output shape as today, plus engines["story"]
                                                            # = "bm25" | "bm25+vectors" | "bm25+vectors+llm"
```

## 4. Transcripts: an experiment, not a change

`layer2/benchmark/transcript_rag_eval.py`:
- 40 labelled calls, sampled with a fixed seed from the 500 public labels.
- Method A: today's rules. Method B: embed the call's turns, retrieve the top 8 turns for the summary fields, one
  LLM call extracts the same fields (same schema as `call_summary.fields_llm`), same sentence builder.
- Scores: exact sentence, per-field accuracy, AI calls, seconds per call.
- **Switch to B only if it beats A by at least 5 points on exact sentences with no field worse.**
- Caveat: the rules were tuned on these 500 labels and no other labels exist, so the test favours A.
- Cost: 40 LLM calls. Run after the free limit resets (05:30 IST); OpenRouter's free tier is 50 requests a day
  per account. Expected outcome: keep the rules.

## 5. How we measure (no overfitting)

`layer2/benchmark/notes_eval.py`:
- **Sample:** 300 random cases (fixed seed), each with at least 3 notes and at least one hardship_assessment,
  dispute_note, payment_note or supervisor_note.
- **Questions:** fixed generic templates, written before any result is seen, in plain words that avoid the type
  names:
  - hardship: "What has the customer said about money problems in the notes for case {case}?"
  - dispute: "Did the customer disagree with a charge in the notes for case {case}?"
  - payment: "What do the notes for case {case} say about paying?"
  - supervisor: "Was anything escalated in the notes for case {case}?"
- **Label:** the note type. A hit means a note of that type is in the top 3.
- **Metrics:** hit@3 and MRR, for (a) today's keyword search, (b) + note types and French words, (c) + vectors.
  Also: a French-only slice; latency p50/p95, cold (first load) and warm; peak memory.
- **Rule:** templates and thresholds are not changed after seeing results on this sample. A later change is
  judged on a new sample (different seed).

## 6. Acceptance criteria

| Gate | Must hold |
|---|---|
| Value | (b) beats (a) on hit@3; vectors are kept only if (c) beats (b), overall or on the French slice |
| Overhead | no added time for non-notes questions; warm notes question with vectors < 300 ms extra (p95); process memory increase < 700 MB; first model load reported separately |
| Safety | AI answers pass every guardrail or fall back; skip-trace notes never shown to analysts; no personal data in answers (spot check plus `pii_leaks`) |
| Regression | `pytest layer2/tests` green (unit tests use a fake embedder: no download, no network); 35 benchmark answers identical; dev 21/21; typo test 239/242 unchanged; holdout v3 unchanged |
| Generation | on 10 sampled asking questions (10 AI calls), report the share that pass every guardrail; failures shown as the list |

## 7. Tasks

| # | Task | Needs | Status |
|---|---|---|---|
| 1 | Free fixes in `story.py` (note types, French words, system/skip-trace rules) | nothing | done |
| 2 | `notes_eval.py`; measure (a) and (b) | nothing | done |
| 3 | `vectors.py` + `lake/layer2_vectors.duckdb` + hybrid ranking; measure (c) | model download (once) | done |
| 4 | Cited AI answer (gate 3) + guardrails + tests with a fake LLM | nothing | done |
| 5 | App: answer above the cited notes; checker status "AI answer from notes (cited)" | nothing | done |
| 6 | Generation check, 10 AI calls | | done |
| 7 | Transcript experiment, **20** AI calls (owner's budget decision); decide by the rule in section 4 | | done: keep rules |
| 8 | CHANGELOG, README, this file's status; commit on `l2/ask-runner` | | done |

## 8. Risks

| Risk | Mitigation |
|---|---|
| Model download fails or is slow on a teammate's laptop | keyword fallback; `--download` step in the README; `L2_VECTORS=0` |
| First notes question is slow (model load, a few seconds) | load once per process; the Streamlit page caches it; benchmark questions do not touch notes |
| The AI answer misses something in short, messy notes | the cited notes are always shown underneath; guardrails block invented facts, not omissions |
| Vector file locked by another process | in-memory fallback, store skipped |
| Hackathon deadline (final run 19:00, commits by 21:00 IST, 4 Oct) | tasks 1-2 are useful alone; stop after any task and the system still works |

## 9. Results (4 Oct 2026)

Notes retrieval (`outputs/layer2_notes_eval*.md`; label = note type; hit = right type in the top 3):

| Method | seed 7: hit@3 / MRR / French | seed 11 (fresh): hit@3 / MRR / French |
|---|---|---|
| before (frozen copy) | 9.2% / 0.077 / 19.4% | 7.7% / 0.060 / 25.0% |
| keyword, text only | 75.4% / 0.647 / 47.2% | 75.1% / 0.649 / 56.8% |
| keyword (note types + French words) | 94.6% / 0.930 / 91.7% | 93.7% / 0.911 / 88.6% |
| + vectors, equal-weight fusion (rrf) | 95.4% / 0.843 / 97.2% | 94.5% / 0.836 / 93.2% |
| **+ vectors, tie-break (chosen on seed 11)** | — | **96.4% / 0.941 / 93.2%** |

- On text alone, vectors add +11 points overall and +24 to +33 on French. Equal-weight fusion lowered MRR, so the
  tie-break (keyword first, vectors order ties and the notes keyword missed) was declared and judged on a fresh seed.
- Because the full ranking boosts by note type and the label is the note type, the keyword number partly measures
  topic-to-type mapping; the "text only" rows measure the note text.
- Overhead: listings and every non-notes question never load the model; warm notes question with vectors 24 ms p50,
  31 ms p95; first model load about 4-5 s; process memory +726 to +741 MB (6% over the plan's 700 MB, accepted by the
  owner). bfloat16 was tried and measured worse on this CPU (more memory, up to 2x slower), so float32 stays.
- Cited AI answers (`outputs/layer2_notes_answers_check.md`): 6/10 passed every guardrail on the first run; the 4
  fallbacks were two notes-search bugs (only the last 12 months searched when no period was asked; "Please
  summarize…" not seen as a question), both fixed; the same 4 then passed.
- Gates: 35 benchmark answers identical, dev 21/21, holdout v3 unchanged, 241 tests (fake embedder and fake LLM).

Transcripts (`outputs/layer2_transcript_rag_eval.md`, 20 labelled calls, seed 5, 20 AI calls): exact sentence
**rules 90% vs RAG 60%**; RAG was worse on hardship (90 vs 95), promise amount and date (85 vs 95), next step (70 vs
90), equal elsewhere; 17.7 s per call vs milliseconds. Decision by the fixed rule: **keep the regex rules.** The test
favours the rules (tuned on these labels) and 20 calls is a small sample, but a 30-point gap is far beyond both.
Likely cause: 8 retrieved turns of a ~20-turn call drop context the summary needs (who confirmed the promise, the
agent's closing next step); the rules read the whole call.

Protected information (added after a review of real notes): notes can mention religion or vulnerability. The notes
shown and the text the AI reads mask them as [protected] (vulnerability and mental health for analysts only), and
the AI answer is dropped if it mentions any (`story.mask`, `story.protected_leak`). Fresh transcript test set and
the rules-vs-hybrid comparison: `outputs/layer2_transcript_compare.md`.
