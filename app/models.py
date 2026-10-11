"""
Pydantic schemas and models for Firebase Cloud Firestore collections.

Firestore Collections:
  - articles:         source material with cached rubric
  - reading_sessions: reading attempt tracking timing and single-serve text
  - attempts:         scored submission with all grading metadata
"""

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field


class Article(BaseModel):
    id: str
    title: str
    text: str
    rubric_json: str | None = None


class ReadingSession(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    article_id: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    read_seconds: int = 75
    submitted_at: datetime | None = None
    text_served: bool = False


class Attempt(BaseModel):
    id: str | None = None
    session_id: str
    summary: str
    rating: int
    jev_score_raw: float | None = None
    confidence: float | None = None
    covered_points_json: str | None = None
    missed_points_json: str | None = None
    feedback: str | None = None
    flags_json: str | None = None
    source: str = "jev"
    jev_cost: float | None = None
    llm_cost: float | None = None
    latency_ms: int | None = None


class FollowUpQuestion(BaseModel):
    """A single Socratic follow-up question targeting a missed concept."""
    question: str
    hint: str
    target_concept: str


class FollowUpSession(BaseModel):
    """Stores the follow-up questions and student answers for a session."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    questions: list[dict] = Field(default_factory=list)
    answers: list[dict] = Field(default_factory=list)   # [{answer, score, correct, explanation}]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ImportedArticle(BaseModel):
    """Temporary model for a user-imported article (not yet persisted)."""
    title: str
    text: str
    word_count: int = 0
    source: str = "upload"   # "upload" | "url"
    source_url: str | None = None
    page_count: int | None = None

