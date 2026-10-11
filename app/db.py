"""
Firebase Cloud Firestore database module for PRAYAS.

Collections:
  - articles
  - reading_sessions
  - attempts
"""

import json
import logging
import os
import pathlib
from datetime import UTC, datetime
from typing import Any

import firebase_admin
from firebase_admin import credentials, firestore

from app.config import settings
from app.models import Article, Attempt, ReadingSession

logger = logging.getLogger(__name__)
ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent

# ── Firebase Initialization ──────────────────────────────────────────

_firestore_client = None
_is_live_firestore = False


class InMemoryFirestore:
    """
    Fallback in-memory Firestore-compatible store used if Firebase credentials
    have not yet been provided, or for fast unit testing.
    """

    def __init__(self):
        self._collections: dict[str, dict[str, dict[str, Any]]] = {
            "articles": {},
            "reading_sessions": {},
            "attempts": {},
        }

    def collection(self, name: str):
        return InMemoryCollection(self, name)


class InMemoryCollection:
    def __init__(self, store: InMemoryFirestore, name: str):
        self.store = store
        self.name = name

    def _get_coll(self):
        if self.name not in self.store._collections:
            self.store._collections[self.name] = {}
        return self.store._collections[self.name]

    def document(self, doc_id: str):
        return InMemoryDocumentRef(self, doc_id)

    def stream(self):
        docs = []
        for doc_id, data in self._get_coll().items():
            docs.append(InMemoryDocSnapshot(doc_id, data))
        return docs

    def where(self, field_path: str, op_string: str, value: Any):
        coll = self._get_coll()
        results = []
        for doc_id, data in coll.items():
            field_val = data.get(field_path)
            if op_string in ("==", "=") and field_val == value:
                results.append(InMemoryDocSnapshot(doc_id, data))
        return InMemoryQuery(results)


class InMemoryQuery:
    def __init__(self, results: list):
        self.results = results

    def stream(self):
        return self.results


class InMemoryDocSnapshot:
    def __init__(self, doc_id: str, data: dict[str, Any] | None):
        self.id = doc_id
        self._data = dict(data) if data else None
        self.exists = data is not None

    def to_dict(self):
        return dict(self._data) if self._data else {}


class InMemoryDocumentRef:
    def __init__(self, collection: InMemoryCollection, doc_id: str):
        self.collection = collection
        self.id = doc_id

    def get(self):
        coll = self.collection._get_coll()
        data = coll.get(self.id)
        return InMemoryDocSnapshot(self.id, data)

    def set(self, data: dict[str, Any], merge: bool = False):
        coll = self.collection._get_coll()
        if merge and self.id in coll:
            coll[self.id].update(data)
        else:
            coll[self.id] = dict(data)

    def update(self, data: dict[str, Any]):
        coll = self.collection._get_coll()
        if self.id in coll:
            coll[self.id].update(data)
        else:
            coll[self.id] = dict(data)


def init_db():
    """Initialize Firebase Admin SDK and Firestore client."""
    global _firestore_client, _is_live_firestore

    if _firestore_client is not None:
        return _firestore_client

    # Check for credentials in settings, environment, or default local files
    cred_path = None
    possible_paths = [
        settings.FIREBASE_CREDENTIALS_PATH,
        os.environ.get("FIREBASE_CREDENTIALS_PATH"),
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
        str(ROOT_DIR / "serviceAccountKey.json"),
        str(ROOT_DIR / "firebase-credentials.json"),
    ]

    for p in possible_paths:
        if p and pathlib.Path(p).is_file():
            cred_path = str(pathlib.Path(p).resolve())
            break

    try:
        if not firebase_admin._apps:
            if cred_path:
                logger.info("Initializing Firebase with credentials from: %s", cred_path)
                cred = credentials.Certificate(cred_path)
                firebase_admin.initialize_app(cred)
                _is_live_firestore = True
            elif settings.FIREBASE_PROJECT_ID or os.environ.get("FIRESTORE_EMULATOR_HOST"):
                project_id = settings.FIREBASE_PROJECT_ID or "prayas-comprehension"
                logger.info("Initializing Firebase with project ID: %s", project_id)
                firebase_admin.initialize_app(options={"projectId": project_id})
                _is_live_firestore = True
            else:
                logger.info("No Firebase credentials found. Falling back to local Firestore storage adapter.")
                _firestore_client = InMemoryFirestore()
                _is_live_firestore = False
                seed_articles_if_empty()
                return _firestore_client

        _firestore_client = firestore.client()
        logger.info("Connected to Cloud Firestore successfully.")
        seed_articles_if_empty()
        return _firestore_client

    except Exception as exc:
        logger.warning(
            "Could not connect to live Cloud Firestore (%s). Using Firestore local adapter.",
            exc,
        )
        _firestore_client = InMemoryFirestore()
        _is_live_firestore = False
        seed_articles_if_empty()
        return _firestore_client


def get_db():
    """Return the active Firestore database client."""
    global _firestore_client
    if _firestore_client is None:
        init_db()
    return _firestore_client


# ── High-Level Firestore Entity Helpers ──────────────────────────────

def list_articles() -> list[Article]:
    """Fetch all benchmark articles from the Firestore 'articles' collection."""
    db = get_db()
    docs = db.collection("articles").stream()
    articles = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = str(doc.id)
        articles.append(Article(**data))
    # Sort deterministically by id
    try:
        articles.sort(key=lambda a: int(a.id) if a.id.isdigit() else a.id)
    except Exception:
        articles.sort(key=lambda a: a.id)
    return articles


def get_article(article_id: str | int) -> Article | None:
    """Fetch an article by its ID from Firestore."""
    db = get_db()
    str_id = str(article_id)
    doc = db.collection("articles").document(str_id).get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    data["id"] = str(doc.id)
    return Article(**data)


def save_article(article: Article) -> None:
    """Save or update an article in Firestore."""
    db = get_db()
    db.collection("articles").document(str(article.id)).set(article.model_dump())


def get_session(session_id: str) -> ReadingSession | None:
    """Fetch a reading session document by ID."""
    db = get_db()
    doc = db.collection("reading_sessions").document(str(session_id)).get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    data["id"] = str(doc.id)
    # Ensure datetime parsing if needed
    if isinstance(data.get("started_at"), str):
        data["started_at"] = datetime.fromisoformat(data["started_at"])
    if isinstance(data.get("submitted_at"), str):
        data["submitted_at"] = datetime.fromisoformat(data["submitted_at"])
    return ReadingSession(**data)


def save_session(session: ReadingSession) -> None:
    """Persist or update a reading session in Firestore."""
    db = get_db()
    payload = session.model_dump()
    # Serialize datetime to ISO string for Firestore compatibility
    if isinstance(payload.get("started_at"), datetime):
        payload["started_at"] = payload["started_at"].isoformat()
    if isinstance(payload.get("submitted_at"), datetime):
        payload["submitted_at"] = payload["submitted_at"].isoformat()
    db.collection("reading_sessions").document(str(session.id)).set(payload)


def get_attempt_by_session(session_id: str) -> Attempt | None:
    """Find the attempt associated with a session ID."""
    db = get_db()
    docs = list(
        db.collection("attempts")
        .where("session_id", "==", str(session_id))
        .stream()
    )
    if not docs:
        return None
    doc = docs[0]
    data = doc.to_dict()
    data["id"] = str(doc.id)
    return Attempt(**data)


def save_attempt(attempt: Attempt) -> Attempt:
    """Save a scored attempt in Firestore."""
    db = get_db()
    payload = attempt.model_dump()
    doc_id = str(attempt.id) if attempt.id else f"attempt_{attempt.session_id}"
    payload["id"] = doc_id
    db.collection("attempts").document(doc_id).set(payload)
    attempt.id = doc_id
    return attempt


def seed_articles_if_empty():
    """Ensure benchmark articles are seeded into Firestore."""
    db = get_db()
    existing = list(db.collection("articles").stream())
    if existing:
        return

    articles_file = ROOT_DIR / "data" / "articles.json"
    backup_file = ROOT_DIR / "data_articles_backup.json"
    source_file = articles_file if articles_file.exists() else backup_file

    if source_file.exists():
        try:
            articles_data = json.loads(source_file.read_text(encoding="utf-8"))
            for item in articles_data:
                doc_id = str(item.get("id"))
                db.collection("articles").document(doc_id).set(item)
            logger.info("Seeded %d benchmark articles into Firestore", len(articles_data))
        except Exception as exc:
            logger.error("Failed to seed articles from %s: %s", source_file, exc)
