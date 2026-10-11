"""
Rubric generation and caching.

Generates a structured rubric for an article via the LLM, validates
the shape, and persists it in the Firestore article document so it is only
created once per article.
"""

import json
import logging

from app.db import get_article, save_article
from app.services.llm import generate_rubric as _llm_generate_rubric

logger = logging.getLogger(__name__)


async def get_or_create_rubric(
    article_id: str | int,
    article_title: str,
    article_text: str,
    db=None,
) -> dict:
    """
    Return the cached rubric for *article_id*, or generate a new one.

    Parameters
    ----------
    article_id : str | int
    article_title : str
    article_text : str
    db : optional database handle (ignored or uses Firestore)

    Returns
    -------
    dict  {purpose, key_points, misconceptions}
    """
    article = get_article(str(article_id))
    if not article:
        raise ValueError(f"Article {article_id} not found")

    # ── Return cached rubric if available ──
    if article.rubric_json:
        try:
            rubric = json.loads(article.rubric_json)
            logger.debug(
                "Using cached rubric for article %s", article_id
            )
            return rubric
        except json.JSONDecodeError:
            logger.warning(
                "Corrupt rubric_json for article %s — regenerating",
                article_id,
            )

    # ── Generate new rubric via LLM ──
    logger.info(
        "Generating rubric for article %s: '%s'",
        article_id,
        article_title,
    )

    result = await _llm_generate_rubric(article_title, article_text)
    rubric = result["rubric"]

    # Persist in Firestore
    article.rubric_json = json.dumps(rubric)
    save_article(article)

    logger.info(
        "Rubric cached for article %s (%d key points, cost=%s)",
        article_id,
        len(rubric.get("key_points", [])),
        result.get("cost", "N/A"),
    )

    return rubric
