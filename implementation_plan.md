# Implementation Plan: Student Comprehension Assessment (Jev + LLM)

**Problem ID:** PRAYAS-INFO-26-S-007
**Goal:** A web app where a student reads a short technical article for a fixed time, the article is hidden, the student writes a summary in their own words, and the backend returns a 1-10 comprehension rating plus targeted feedback instantly.

---

## 1. Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | FastAPI + Pydantic | Python, async, auto docs at `/docs` |
| Frontend | Jinja2 templates + HTMX + vanilla JS + Tailwind (CDN) | No build step. The only client logic is a timer and swapping panels. Responsive, so it works on mobile |
| Database | SQLite via SQLModel | Zero setup, enough for a prototype |
| Jev | OpenRouter Decisions API via `httpx` | One key, documented JSON, full control of timeouts |
| LLM | Any OpenRouter chat model (small, cheap, supports JSON output) via the `openai` SDK pointed at OpenRouter | Same key as Jev |
| Tooling | `uv`, `pytest`, `python-dotenv`, `ruff` | |

**Config (`.env`):**
```
OPENROUTER_API_KEY=sk-or-...
LLM_MODEL=<pick a small OpenRouter model>
JEV_MODEL=typesafe/jev-1.13
READ_SECONDS=75
```
The key stays server-side only. Never expose it to the browser.

---

## 2. How Jev and the LLM split the work

Jev is a decision model, not a text generator. It returns typed answers with probabilities and gives no explanation. The LLM generates text. Use each for what it is good at.

1. **Rubric generation (LLM, offline, once per article, cached).** Output JSON: `purpose` (one sentence), `key_points` (3-5), `misconceptions` (1-2).
2. **Grading (Jev, at submit, one request).** Several independent questions on the same state, answered in parallel:
   - `comprehension`: `score`, 10 levels. Level index 0-9 maps to rating 1-10.
   - `states_purpose`: `noul`.
   - `contradicts`: `noul`, true if the summary contradicts the article.
   - `style`: `choice` of `paraphrase`, `near_copy`, `off_topic`.
   - `point_0 ... point_N`: one `noul` per key point.
3. **Feedback (LLM, after Jev).** Input: Jev's structured results plus the rubric. Output: 2-3 sentences naming what was covered, what was missed and any error, without quoting the article. Stream it to the UI.
4. **Hybrid scoring (plain Python).** Combine Jev's score, confidence and guards into the final rating.

### Jev facts (from OpenRouter docs)
- Endpoint: `POST https://openrouter.ai/api/alpha/decisions` (alpha, so wrap it in one adapter).
- Model: pin `typesafe/jev-1.13`. Avoid `~typesafe/jev-latest`, which would shift your tuned thresholds.
- Request: `{model, state, questions: {name: {type, instructions, criteria}}}`.
- `score` criteria is an ordered list of 2-10 level descriptions. The response `score` is 0-based and fractional (probability-weighted), so `rating = clamp(round(score + 1), 1, 10)`.
- `noul` returns P(yes) from 0 to 1. Criteria is `{"true": "...", "false": "..."}`. OpenRouter requires non-null instructions and descriptions. The PDF spells it "null", but the real type name is `noul`.
- `choice` returns `choice`, `probabilities` and `confidence`.
- Questions in one request cannot see each other's answers.
- Billed per input token, output free. Each response has `usage.cost`. Log it.
- The Decisions API is not chat completions, so OpenAI chat SDKs will not work for Jev. Use `httpx`.
- Check the Decisions API reference on OpenRouter if any field shape differs from this document.

---

## 3. Scoring logic (`scoring.py`)

Run cheap guards first, then Jev, then combine.

**Pre-guards (no API cost):**
- Minimum length (e.g. under 15 words: reject with a prompt to write more).
- N-gram overlap with the source (e.g. 5-gram overlap above ~50%: flag as `near_copy`, cap the rating).
- Injection phrases such as "give me 10", "ignore instructions": reject or ignore the text.

**Post-Jev:**
- `rating = clamp(round(comp.score + 1), 1, 10)`
- If `contradicts` P(yes) > 0.6: subtract 1-2 points.
- If `style.choice == near_copy` or `off_topic`: cap the rating (e.g. max 4).
- If `comp.confidence` < threshold (tune on labeled data): mark `low_confidence` and optionally ask the LLM judge for a second opinion, then average.
- Covered points: `noul > 0.6` counts as covered. Tune the threshold on the gold set.
- If the Jev call fails or times out (20 s, one retry): fall back to an LLM-only judge and mark `source: "llm_fallback"`.

---

## 4. Data model (SQLModel)

```
Article   id, title, text, rubric_json (nullable until generated)
Session   id (uuid), article_id, started_at, read_seconds, submitted_at (nullable)
Attempt   id, session_id, summary, rating, jev_score_raw, confidence,
          covered_points_json, missed_points_json, feedback,
          flags_json, source ("jev" | "llm_fallback"),
          jev_cost, llm_cost, latency_ms
```

---

## 5. API endpoints

| Method | Path | Behavior |
|---|---|---|
| GET | `/` | Article list page |
| POST | `/sessions` | Body `{article_id}`. Creates a session, stores `started_at`, returns article text and `read_seconds`. Generates and caches the rubric if missing |
| POST | `/sessions/{id}/submit` | Body `{summary}`. Rejects if the reading window has not elapsed (small grace) or a submission already exists. Runs the pipeline and returns `{rating, confidence, covered, missed, feedback, flags}` |
| GET | `/sessions/{id}` | Fetch a past result |
| GET | `/health` | Liveness check |

**Hide-text enforcement:** the client hides the article with JS, but the server only serves the text once per session (at `POST /sessions`) and never again. There is no endpoint that returns article text afterward.

---

## 6. Frontend flow

Three panels on one page, swapped with JS or HTMX:

1. **Read:** article text, countdown bar, optional "I'm done" button (not before a minimum time).
2. **Write:** article removed from the DOM. Textarea with word counter and a submit button.
3. **Result:** large 1-10 rating, covered and missed points, streamed feedback, "Try another article" button.

Mobile checks: textarea does not get covered by the keyboard, timer is visible, buttons are tap-sized.

---

## 7. Project layout

```
prayas-comprehension/
  app/
    main.py               # FastAPI app, routes
    config.py             # settings from .env
    db.py                 # engine, session helper
    models.py             # SQLModel tables + Pydantic schemas
    services/
      llm.py              # OpenRouter chat client (rubric, feedback, fallback judge)
      rubric.py           # rubric generation + caching
      jev_grader.py       # build request, call Decisions API, parse answers
      feedback.py         # feedback prompt + streaming
      scoring.py          # guards + hybrid rating logic
    templates/            # base.html, index.html, session.html
    static/app.js         # countdown, panel swap
  data/articles/          # 8-10 short technical articles (~300 words, .md)
  scripts/seed.py         # load articles into the DB
  eval/
    gold.jsonl            # labeled summaries
    run_eval.py           # compare Jev-only / LLM-only / hybrid
  tests/
  .env.example
  pyproject.toml
  README.md
```

---

## 8. Build order

### Phase 1: Skeleton
- [ ] `uv init`, install: `fastapi uvicorn[standard] sqlmodel httpx openai jinja2 python-dotenv pytest ruff`
- [ ] `config.py`, `db.py`, `models.py`
- [ ] Seed 8-10 articles (`scripts/seed.py`)
- [ ] `POST /sessions` and `POST /submit` with a **stubbed** score
- [ ] Read, hide and write flow working in the browser with the countdown
- [ ] Server rejects early or duplicate submits

**Done when:** you can read an article, watch it disappear, submit text and see a fake rating.

### Phase 2: Jev grader
- [ ] Run one raw `httpx` call against the Decisions API to confirm the key and response shape
- [ ] `jev_grader.py`: `build_request(rubric, summary)` and `grade(...)` with timeout and one retry
- [ ] Parse `answers` and `usage.cost` into a typed result object
- [ ] Hand-write a rubric for one article and test good, partial and bad summaries
- [ ] Tests with a mocked HTTP layer (fixed JSON in, expected rating out)

**Done when:** three hand-written summaries of one article get clearly ordered ratings.

### Phase 3: LLM, scoring and guards
- [ ] `rubric.py`: LLM generates rubric JSON (structured output), validate with Pydantic, cache in DB
- [ ] `feedback.py`: feedback from Jev results, no verbatim quotes from the article
- [ ] `scoring.py`: guards, contradiction penalty, near-copy cap, low-confidence handling, LLM fallback
- [ ] Wire everything into `/submit`, log `jev_cost`, `llm_cost`, `latency_ms`

**Done when:** `/submit` returns a real rating and useful feedback end to end.

### Phase 4: UI polish
- [ ] Streamed feedback on the result panel
- [ ] Covered and missed points display
- [ ] Mobile layout pass
- [ ] Error states (network failure, API timeout, empty summary)
- [ ] Past results page (optional)

### Phase 5: Evaluation (the research contribution)
- [ ] Pick 4-5 articles. Write 6-8 summaries per article spanning bad, partial and strong (~30 total)
- [ ] Two team members label each independently on 1-10, then reconcile into `gold.jsonl`
- [ ] `run_eval.py` scores every summary three ways: **Jev only**, **LLM judge only**, **hybrid**
- [ ] Report for each method: Spearman correlation with human labels, mean absolute error, p50/p95 latency, cost per attempt
- [ ] Tune thresholds (covered-point P(yes), confidence cutoff) on the gold set, then report results
- [ ] Include adversarial cases: copy-paste, off-topic, prompt injection, confident-but-wrong summaries

**Done when:** you have a table comparing the three methods and a short written conclusion.

### Phase 6: Wrap-up
- [ ] README with setup, run and eval instructions
- [ ] 2-3 minute demo recording or script
- [ ] Short report: problem, design, results, limitations, SDG 4 link

---

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Decisions API is alpha and may change | Single adapter file, mocked tests, LLM fallback |
| Score rubric wording drives quality | Write levels carefully, iterate against the gold set |
| Jev gives no reasons | LLM writes feedback from structured outputs |
| Students paste the article or game the grader | Server-side single serve, n-gram guard, `style` choice, injection check |
| Low-confidence or noisy scores | Confidence threshold plus second opinion, report variance across repeated runs |
| API cost or rate limits | Cache rubrics, small LLM, log `usage.cost` |
| Scope creep | Core flow first. Past-results page and extras are optional |

---

## 10. Definition of done

- [ ] Article shown for the reading window, then removed, and not retrievable afterward
- [ ] Summary input handled with validation
- [ ] Backend uses Jev (score, noul, choice) plus the LLM together
- [ ] 1-10 rating and targeted feedback returned in a few seconds
- [ ] Evaluation table comparing Jev-only, LLM-only and hybrid
- [ ] README and demo
