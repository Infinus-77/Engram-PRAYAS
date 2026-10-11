"""
Seed script: load benchmark technical articles into Firebase Cloud Firestore.

Usage:
    python scripts/seed.py
"""

import json
import pathlib
import sys

# Ensure the project root is on the Python path
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import get_article, get_db, init_db, list_articles, save_article
from app.models import Article


def seed():
    """Load benchmark articles into Firebase Cloud Firestore."""
    init_db()
    db = get_db()

    articles_file = ROOT / "data" / "articles.json"
    backup_file = ROOT / "data_articles_backup.json"
    source_file = articles_file if articles_file.exists() else backup_file

    if not source_file.exists():
        print("[!] No article data source found at data/articles.json.")
        return

    articles_data = json.loads(source_file.read_text(encoding="utf-8"))
    print(f"[*] Found {len(articles_data)} benchmark article(s) in {source_file.name}\n")

    added = 0
    updated = 0

    for item in articles_data:
        article_id = str(item.get("id"))
        existing = get_article(article_id)

        article = Article(
            id=article_id,
            title=item.get("title", ""),
            text=item.get("text", ""),
            rubric_json=item.get("rubric_json"),
        )
        save_article(article)

        if existing:
            print(f"  [~] Updated: #{article_id} - {article.title}")
            updated += 1
        else:
            print(f"  [+] Added:   #{article_id} - {article.title}")
            added += 1

    total_articles = len(list_articles())
    print(f"\n[OK] Firestore seeding complete: {added} added, {updated} updated. Total active: {total_articles}")


if __name__ == "__main__":
    seed()
