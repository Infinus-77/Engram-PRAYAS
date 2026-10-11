# PRAYAS — Student Comprehension Assessment

**Problem ID:** PRAYAS-INFO-26-S-007

An AI-powered web application where students read a short technical article, write a summary from memory, and receive instant comprehension feedback with a 1–10 rating.

## Stack

- **Backend:** FastAPI + SQLModel (SQLite) + Pydantic
- **Frontend:** Jinja2 templates + vanilla JS
- **Grading:** Jev (OpenRouter Decisions API) + LLM (OpenRouter chat) — hybrid scoring
- **Tooling:** uv, pytest, ruff

## Quick Start

### 1. Install dependencies

```bash
uv venv
uv pip install -e ".[dev]"
```

Or with pip:

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -e ".[dev]"
```

### 2. Set up environment

```bash
copy .env.example .env
# Edit .env with your OpenRouter API key
```

### 3. Seed articles

```bash
python scripts/seed.py
```

### 4. Run the dev server

```bash
uvicorn app.main:app --reload --port 8000
```

Visit **http://localhost:8000** to start.

## Project Structure

```
PRAYAS/
  app/
    main.py               # FastAPI app, routes
    config.py             # Settings from .env
    db.py                 # SQLite engine + session helper
    models.py             # SQLModel tables
    services/
      scoring.py          # Guards + scoring pipeline
      jev_grader.py       # Jev Decisions API adapter (Phase 2)
      rubric.py           # LLM rubric generation (Phase 3)
      feedback.py         # LLM feedback generation (Phase 3)
    templates/            # Jinja2 HTML templates
    static/               # CSS + JS
  data/articles/          # Technical articles (~300 words each)
  scripts/seed.py         # Load articles into DB
  eval/                   # Evaluation scripts (Phase 5)
  tests/                  # Test suite
```

## API Endpoints

| Method | Path                     | Description                     |
|--------|--------------------------|---------------------------------|
| GET    | `/`                      | Article list page               |
| POST   | `/sessions`              | Create reading session          |
| GET    | `/sessions/{id}`         | Session page (read/write/result)|
| POST   | `/sessions/{id}/submit`  | Submit summary for scoring      |
| GET    | `/health`                | Liveness check                  |

## SDG 4 — Quality Education

This tool supports SDG 4 by providing instant, personalised feedback on reading comprehension, helping students identify gaps in their understanding and improve their learning outcomes.
