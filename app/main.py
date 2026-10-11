"""
FastAPI application — routes, template rendering, and API endpoints.
Powered by Firebase Cloud Firestore for real-time document persistence.

Routes:
  GET  /                              → Article list page
  POST /sessions                      → Create reading session, redirect
  GET  /sessions/{id}                 → Session page (read / write / result panels)
  POST /sessions/{id}/submit          → Score summary, return JSON result
  GET  /sessions/{id}/followup        → Generate Socratic follow-up questions
  POST /sessions/{id}/followup/answer → Evaluate a follow-up answer
  GET  /sessions/{id}/certificate     → Download PDF diagnostic certificate
  POST /articles/import/pdf           → Import article from uploaded PDF
  POST /articles/import/url           → Import article from a web URL
  GET  /health                        → Liveness check
"""

import json
import logging
import pathlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.db import (
    get_article,
    get_attempt_by_session,
    get_session,
    init_db,
    list_articles,
    save_article,
    save_attempt,
    save_session,
)
from app.models import Article, Attempt, ReadingSession
from app.services.certificate import generate_certificate_pdf
from app.services.importer import extract_text_from_pdf, extract_text_from_url
from app.services.rubric import get_or_create_rubric
from app.services.scoring import score_summary
from app.services.socratic import evaluate_followup_answer, generate_followup_questions

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


# ── Feature 5: Socratic Follow-Up Interrogation ──────────────────

@app.get("/sessions/{session_id}/followup")
async def get_followup_questions(session_id: str):
    """
    Generate Socratic follow-up questions targeting the student's missed rubric points.
    Only available after a session has been submitted and scored.
    """
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    attempt = get_attempt_by_session(session_id)
    if not attempt:
        raise HTTPException(
            status_code=409,
            detail="No attempt found for this session. Submit a summary first.",
        )

    article = get_article(session.article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")

    missed = json.loads(attempt.missed_points_json) if attempt.missed_points_json else []
    covered = json.loads(attempt.covered_points_json) if attempt.covered_points_json else []

    if not missed:
        return JSONResponse({"questions": [], "message": "All rubric points were covered. No follow-up needed."})

    questions = await generate_followup_questions(
        article_title=article.title,
        article_text=article.text,
        missed_points=missed,
        covered_points=covered,
        student_summary=attempt.summary,
        num_questions=min(3, len(missed)),
    )

    return JSONResponse({"questions": questions, "missed_count": len(missed)})


@app.post("/sessions/{session_id}/followup/answer")
async def submit_followup_answer(session_id: str, request: Request):
    """
    Evaluate a student's answer to a single Socratic follow-up question.
    """
    body = await request.json()
    question = body.get("question", "").strip()
    target_concept = body.get("target_concept", "").strip()
    student_answer = body.get("answer", "").strip()

    if not student_answer:
        raise HTTPException(status_code=422, detail="Answer cannot be empty")

    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    article = get_article(session.article_id)
    article_text = article.text if article else ""

    result = await evaluate_followup_answer(
        question=question,
        target_concept=target_concept,
        article_text=article_text,
        student_answer=student_answer,
    )

    return JSONResponse(result)


# ── Feature 6: PDF & URL Article Importer ────────────────────────

@app.post("/articles/import/pdf")
async def import_article_pdf(file: UploadFile = File(...)):
    """
    Import an article from an uploaded PDF file.
    Returns the extracted title and text for the user to review before starting a session.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Only PDF files are supported")

    max_size = 10 * 1024 * 1024  # 10 MB
    content = await file.read()
    if len(content) > max_size:
        raise HTTPException(status_code=413, detail="PDF file is too large (max 10 MB)")

    try:
        result = extract_text_from_pdf(content)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("PDF import failed")
        raise HTTPException(status_code=500, detail=f"Failed to process PDF: {e}")

    return JSONResponse({
        "title": result["title"],
        "text": result["text"],
        "word_count": len(result["text"].split()),
        "page_count": result["page_count"],
        "source": "pdf",
    })


@app.post("/articles/import/url")
async def import_article_url(request: Request):
    """
    Import an article from a web URL.
    Returns the extracted title and text for the user to review before starting a session.
    """
    body = await request.json()
    url = body.get("url", "").strip()

    if not url:
        raise HTTPException(status_code=422, detail="URL is required")

    try:
        result = await extract_text_from_url(url)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("URL import failed")
        raise HTTPException(status_code=500, detail=f"Failed to fetch URL: {e}")

    return JSONResponse({
        "title": result["title"],
        "text": result["text"],
        "word_count": len(result["text"].split()),
        "source": "url",
        "source_url": result["url"],
    })


@app.post("/articles/create-and-start")
async def create_and_start_custom_article(request: Request):
    """
    Save a user-imported custom article (from PDF or URL) to Firestore
    and immediately launch a new timed reading session for it.
    """
    import uuid

    body = await request.json()
    title = body.get("title", "").strip()
    text = body.get("text", "").strip()

    if not title:
        title = "Imported Document"
    if not text or len(text.split()) < 50:
        raise HTTPException(
            status_code=422,
            detail="Article text is too short. Minimum 50 words required.",
        )

    # Deterministic custom ID
    article_id = f"custom-{uuid.uuid4().hex[:8]}"
    article = Article(id=article_id, title=title, text=text)
    save_article(article)

    # Pre-generate rubric in background / best-effort
    try:
        await get_or_create_rubric(
            article_id=article.id,
            article_title=article.title,
            article_text=article.text,
        )
    except Exception as exc:
        logger.warning("Custom article rubric generation warning: %s", exc)

    # Create reading session
    session = ReadingSession(
        article_id=article.id,
        read_seconds=settings.READ_SECONDS,
    )
    save_session(session)

    return JSONResponse({
        "status": "ok",
        "session_id": session.id,
        "redirect_url": f"/sessions/{session.id}",
    })


# ── Feature 7: PDF Diagnostic Certificate ────────────────────────

@app.get("/sessions/{session_id}/certificate")
async def download_certificate(session_id: str):
    """
    Generate and download a PDF diagnostic certificate for a completed session.
    Only available after a summary has been submitted and scored.
    """
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    attempt = get_attempt_by_session(session_id)
    if not attempt:
        raise HTTPException(
            status_code=409,
            detail="No attempt found. Submit a summary first to generate a certificate.",
        )

    article = get_article(session.article_id)
    article_title = article.title if article else "Unknown Article"

    covered = json.loads(attempt.covered_points_json) if attempt.covered_points_json else []
    missed = json.loads(attempt.missed_points_json) if attempt.missed_points_json else []

    try:
        pdf_bytes = generate_certificate_pdf(
            session_id=session_id,
            article_title=article_title,
            student_summary=attempt.summary,
            rating=attempt.rating,
            feedback=attempt.feedback or "",
            covered_points=covered,
            missed_points=missed,
            source=attempt.source,
        )
    except Exception as e:
        logger.exception("Certificate generation failed")
        raise HTTPException(status_code=500, detail=f"Could not generate certificate: {e}")

    filename = f"engram-report-{session_id[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )
