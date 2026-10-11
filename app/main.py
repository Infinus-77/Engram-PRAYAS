"""
FastAPI application — routes, template rendering, and API endpoints.
Powered by Firebase Cloud Firestore for real-time document persistence.

Routes:
  GET  /                        → Article list page
  POST /sessions                → Create reading session, redirect
  GET  /sessions/{id}           → Session page (read / write / result panels)
  POST /sessions/{id}/submit    → Score summary, return JSON result
  GET  /health                  → Liveness check
"""

import json
import logging
import pathlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.db import (
    get_article,
    get_attempt_by_session,
    get_session,
    init_db,
    list_articles,
    save_attempt,
    save_session,
)
from app.models import Attempt, ReadingSession
from app.services.rubric import get_or_create_rubric
from app.services.scoring import score_summary

logger = logging.getLogger(__name__)

BASE_DIR = pathlib.Path(__file__).resolve().parent


# ── Lifespan ─────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


# ── App ──────────────────────────────────────────────────────────

app = FastAPI(title="ENGRAM Comprehension", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ── Routes ───────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render the article list page from Firestore articles."""
    articles = list_articles()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"articles": articles},
    )


@app.post("/sessions")
async def create_session(article_id: str = Form(...)):
    """
    Create a new reading session for the given article in Firestore.
    Generates and caches the rubric if missing.
    Redirects to the session page.
    """
    article = get_article(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    # Generate and cache the rubric if it doesn't exist yet.
    try:
        await get_or_create_rubric(
            article_id=article.id,
            article_title=article.title,
            article_text=article.text,
        )
    except Exception as exc:
        logger.warning("Rubric generation failed: %s", exc)

    session = ReadingSession(
        article_id=str(article_id),
        read_seconds=settings.READ_SECONDS,
    )
    save_session(session)

    return RedirectResponse(f"/sessions/{session.id}", status_code=303)


@app.get("/sessions/{session_id}", response_class=HTMLResponse)
async def get_session_page(request: Request, session_id: str):
    """
    Render the session page.

    Single-serve enforcement: article text is embedded in the template
    only on the first visit. Subsequent visits (refresh, back-button)
    will not include the article text.
    """
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    article = get_article(session.article_id)

    # Check for existing attempt in Firestore
    attempt = get_attempt_by_session(session_id)

    # Serve article text only once (single-serve)
    article_text = None
    article_title = article.title if article else "Unknown"

    if not session.text_served and not attempt:
        article_text = article.text if article else None
        session.text_served = True
        save_session(session)

    # Build attempt data for result panel
    attempt_data = None
    if attempt:
        attempt_data = {
            "rating": attempt.rating,
            "feedback": attempt.feedback or "",
            "covered": json.loads(attempt.covered_points_json)
            if attempt.covered_points_json
            else [],
            "missed": json.loads(attempt.missed_points_json)
            if attempt.missed_points_json
            else [],
            "flags": json.loads(attempt.flags_json)
            if attempt.flags_json
            else [],
            "source": attempt.source,
            "summary": attempt.summary,
        }

    read_seconds = session.read_seconds
    started_at_iso = (
        session.started_at.isoformat() if session.started_at else None
    )

    # Nav is locked during active read/write phases; unlocked once result is available
    is_session_active = attempt is None

    return templates.TemplateResponse(
        request=request,
        name="session.html",
        context={
            "session_id": session_id,
            "article_title": article_title,
            "article_text": article_text,
            "read_seconds": read_seconds,
            "started_at": started_at_iso,
            "has_attempt": attempt is not None,
            "attempt": attempt_data,
            "session_active": is_session_active,
        },
    )


@app.post("/sessions/{session_id}/submit")
async def submit_summary(session_id: str, request: Request):
    """
    Accept a student summary, run the scoring pipeline, and persist the result in Firestore.

    Rejects:
      - Empty summaries
      - Duplicate submissions (one per session)
      - Submissions before the reading window has elapsed (5s grace)
    """
    body = await request.json()
    summary = body.get("summary", "").strip()

    if not summary:
        raise HTTPException(status_code=422, detail="Summary cannot be empty")

    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Reject duplicate submissions
    existing = get_attempt_by_session(session_id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail="A submission already exists for this session",
        )

    # Check reading window elapsed (5s grace period)
    now = datetime.now(UTC)
    elapsed = (now - session.started_at).total_seconds()
    min_read_time = max(session.read_seconds - 5, 10)
    if elapsed < min_read_time:
        remaining = int(min_read_time - elapsed)
        raise HTTPException(
            status_code=422,
            detail=f"Reading time has not elapsed. Please wait {remaining} more seconds.",
        )

    # Get article for scoring
    article = get_article(session.article_id)
    article_text = article.text if article else ""
    rubric_json = article.rubric_json if article else None

    # Score the summary (async — calls scoring pipeline)
    result = await score_summary(summary, article_text, rubric_json)

    # Persist the attempt with full grading metadata in Firestore
    attempt = Attempt(
        session_id=session_id,
        summary=summary,
        rating=result["rating"],
        jev_score_raw=result.get("jev_score_raw"),
        confidence=result.get("confidence"),
        feedback=result.get("feedback", ""),
        covered_points_json=json.dumps(result.get("covered", [])),
        missed_points_json=json.dumps(result.get("missed", [])),
        flags_json=json.dumps(result.get("flags", [])),
        source=result.get("source", "jev"),
        jev_cost=result.get("jev_cost"),
        llm_cost=result.get("llm_cost"),
        latency_ms=result.get("latency_ms", 0),
    )
    session.submitted_at = now
    save_attempt(attempt)
    save_session(session)

    return JSONResponse(
        {
            "rating": result["rating"],
            "feedback": result.get("feedback", ""),
            "covered": result.get("covered", []),
            "missed": result.get("missed", []),
            "flags": result.get("flags", []),
            "source": result.get("source", "jev"),
        }
    )


@app.get("/health")
async def health():
    """Liveness check."""
    return {"status": "ok", "version": "0.1.0", "database": "firestore"}
