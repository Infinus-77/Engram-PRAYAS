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


def test_create_and_start_custom_article(client):
    """Test Feature 6: Creating a custom article and launching a reading session."""
    payload = {
        "title": "Quantum Error Correction Primer",
        "text": (
            "Quantum error correction protects quantum information from errors caused by decoherence "
            "and other quantum noise. Quantum error correction is essential to achieve fault-tolerant "
            "quantum computation that can reduce the addition of noise on stored quantum information, "
            "fault-tolerant quantum gates, and fault-tolerant state preparation. Classical error correcting "
            "codes use redundancy. The simplest way is to store the information multiple times, and if "
            "these copies are later found to disagree, take a majority vote."
        ),
    }
    response = client.post("/articles/create-and-start", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "session_id" in data
    assert data["redirect_url"].startswith("/sessions/")

    # Check session page
    view_res = client.get(data["redirect_url"])
    assert view_res.status_code == 200
    assert "Quantum Error Correction Primer" in view_res.text


def test_certificate_download_and_socratic_followup(client):
    """Test Features 5 & 7: Certificate PDF download and Socratic follow-up interrogation."""
    # 1. Create a session and submit a scored attempt
    articles = list_articles()
    article = articles[0]
    session_res = client.post("/sessions", data={"article_id": article.id}, follow_redirects=False)
    session_id = session_res.headers["location"].split("/")[-1]

    # Advance timer past reading window
    session = ReadingSession(
        id=session_id,
        article_id=article.id,
        started_at=datetime.now(UTC) - timedelta(seconds=80),
        read_seconds=75,
        text_served=True,
    )
    save_session(session)

    mock_scoring = {
        "rating": 7,
        "jev_score_raw": 6.8,
        "confidence": 0.88,
        "covered": ["Key concept 1"],
        "missed": ["Omitted concept 2", "Incomplete mechanism 3"],
        "feedback": "Solid comprehension with some missing nuances.",
        "flags": [],
        "source": "jev",
        "jev_cost": 0.0015,
        "llm_cost": 0.0005,
        "latency_ms": 720,
    }

    with patch("app.main.score_summary", new_callable=AsyncMock) as mock_score:
        mock_score.return_value = mock_scoring
        sub_res = client.post(
            f"/sessions/{session_id}/submit",
            json={"summary": "An accurate review of the key concept 1 with solid technical detail."},
        )
        assert sub_res.status_code == 200

    # 2. Test Feature 7: Download Certificate PDF
    cert_res = client.get(f"/sessions/{session_id}/certificate")
    assert cert_res.status_code == 200
    assert cert_res.headers["content-type"] == "application/pdf"
    assert len(cert_res.content) > 1000
    assert cert_res.content[:4] == b"%PDF"

    # 3. Test Feature 5: Socratic Follow-Up Generation
    mock_questions = [
        {
            "question": "Can you explain how omitted concept 2 operates?",
            "hint": "Recall the underlying mechanism.",
            "target_concept": "Omitted concept 2",
        }
    ]
    with patch("app.main.generate_followup_questions", new_callable=AsyncMock) as mock_fq:
        mock_fq.return_value = mock_questions
        fq_res = client.get(f"/sessions/{session_id}/followup")
        assert fq_res.status_code == 200
        fq_data = fq_res.json()
        assert len(fq_data["questions"]) == 1
        assert fq_data["questions"][0]["target_concept"] == "Omitted concept 2"

    # 4. Test Feature 5: Evaluate Socratic Follow-Up Answer
    mock_eval = {
        "score": 4,
        "correct": True,
        "explanation": "Great explanation demonstrating clear understanding.",
    }
    with patch("app.main.evaluate_followup_answer", new_callable=AsyncMock) as mock_ea:
        mock_ea.return_value = mock_eval
        ans_res = client.post(
            f"/sessions/{session_id}/followup/answer",
            json={
                "question": "Can you explain how omitted concept 2 operates?",
                "target_concept": "Omitted concept 2",
                "answer": "It operates through deterministic collision resolution chaining.",
            },
        )
        assert ans_res.status_code == 200
        ans_data = ans_res.json()
        assert ans_data["score"] == 4
        assert ans_data["correct"] is True

