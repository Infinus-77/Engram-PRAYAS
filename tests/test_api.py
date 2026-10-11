from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.db import (
    get_attempt_by_session,
    init_db,
    list_articles,
    save_session,
)
from app.main import app
from app.models import ReadingSession


@pytest.fixture
def client():
    init_db()
    return TestClient(app)


def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == "firestore"


def test_article_list_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "ENGRAM" in response.text
    assert "Available articles" in response.text
    assert "start reading" in response.text.lower()


def test_create_session_and_view(client):
    articles = list_articles()
    assert len(articles) > 0
    article = articles[0]
    article_id = article.id

    with patch("app.main.get_or_create_rubric", new=AsyncMock(return_value={"purpose": "Test", "key_points": []})):
        res = client.post("/sessions", data={"article_id": article_id}, follow_redirects=False)
        assert res.status_code == 303
        redirect_url = res.headers["location"]
        session_id = redirect_url.split("/")[-1]

    # First view: article text should be served
    res_view = client.get(f"/sessions/{session_id}")
    assert res_view.status_code == 200
    assert article.title in res_view.text

    # Second view: single-serve enforcement (article text is NOT served again)
    res_view_second = client.get(f"/sessions/{session_id}")
    assert res_view_second.status_code == 200
    assert "The article text has already been served for this session" in res_view_second.text


def test_submit_before_reading_window_rejects(client):
    articles = list_articles()
    article = articles[0]
    session = ReadingSession(article_id=article.id, read_seconds=75)
    save_session(session)
    session_id = session.id

    # Immediate submit without waiting
    res = client.post(
        f"/sessions/{session_id}/submit",
        json={"summary": "This is a summary submitted way too early before reading window elapsed."},
    )
    assert res.status_code == 422
    assert "Reading time has not elapsed" in res.json()["detail"]


def test_submit_valid_scored_with_api_pipeline(client):
    articles = list_articles()
    article = articles[0]
    past_time = datetime.now(UTC) - timedelta(seconds=120)
    session = ReadingSession(article_id=article.id, read_seconds=75, started_at=past_time)
    save_session(session)
    session_id = session.id

    mock_scoring_result = {
        "rating": 9,
        "feedback": "Outstanding summary covering all key aspects with clear insight.",
        "covered": ["Key Concept 1", "Key Concept 2"],
        "missed": [],
        "flags": [],
        "source": "jev",
        "jev_score_raw": 8.4,
        "confidence": 0.92,
        "jev_cost": 0.0018,
        "llm_cost": 0.0006,
        "latency_ms": 1450,
    }

    with patch("app.main.score_summary", new=AsyncMock(return_value=mock_scoring_result)):
        res = client.post(
            f"/sessions/{session_id}/submit",
            json={"summary": "A comprehensive and thorough explanation of the technical principles from the article."},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["rating"] == 9
        assert data["source"] == "jev"
        assert len(data["covered"]) == 2
        assert "Outstanding summary" in data["feedback"]

    # Verify attempt saved in Firestore with all scoring metadata
    attempt = get_attempt_by_session(session_id)
    assert attempt is not None
    assert attempt.rating == 9
    assert attempt.source == "jev"
    assert attempt.jev_score_raw == 8.4
    assert attempt.confidence == 0.92
    assert attempt.jev_cost == 0.0018
    assert attempt.llm_cost == 0.0006
