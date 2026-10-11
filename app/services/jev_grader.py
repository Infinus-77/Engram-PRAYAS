"""
Jev Decisions API adapter for structured grading.

Calls ``POST https://openrouter.ai/api/alpha/decisions`` via httpx.
This is NOT a chat-completions endpoint — the OpenAI SDK does not work here.

Reference (from OpenRouter docs / implementation plan):
  Request:  {model, state, questions: {name: {type, instructions, criteria}}}
  Response: {answers: {name: {...}}, usage: {cost, ...}}

Question types used:
  score  – ordered level descriptions → fractional 0-based score + confidence
  noul   – binary yes/no → probability 0–1
  choice – labelled options → selected choice, probabilities, confidence
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

JEV_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
JEV_TIMEOUT = 20.0  # seconds per attempt
JEV_RETRIES = 1  # one retry after the first failure


# ── Request builder ──────────────────────────────────────────────


def _build_request(
    rubric: dict,
    summary: str,
    article_text: str,
) -> dict:
    """
    Build the Jev Decisions API request payload.

    Creates one ``score`` question for overall comprehension, one ``noul``
    for purpose coverage, one ``noul`` for contradiction, one ``choice``
    for style, and one ``noul`` per key point from the rubric.
    """
    key_points = rubric.get("key_points", [])
    purpose = rubric.get("purpose", "")

    # State is the full context Jev evaluates against
    state = (
        f"Article purpose: {purpose}\n\n"
        f"Article text:\n{article_text}\n\n"
        f"Student summary:\n{summary}"
    )

    # ── 10-level comprehension score ──
    score_levels = [
        "Completely off-topic or nonsensical; demonstrates no understanding "
        "of the article.",
        "Mentions the topic area but no specific content from the article.",
        "Identifies the broad subject but misses all key points and "
        "contains errors.",
        "Captures one key point vaguely; significant gaps in understanding.",
        "Covers one or two key points partially; misses most important "
        "details.",
        "Covers about half the key points with some accuracy; basic "
        "understanding shown.",
        "Covers most key points but with some inaccuracies or missing "
        "relationships.",
        "Covers most key points accurately; shows good understanding of "
        "main ideas.",
        "Covers all key points accurately with clear explanations of "
        "relationships.",
        "Comprehensive, accurate, and insightful; demonstrates deep "
        "understanding with connections between ideas.",
    ]

    questions: dict = {
        "comprehension": {
            "type": "score",
            "instructions": (
                "Rate how well the student summary demonstrates "
                "comprehension of the article's key concepts."
            ),
            "criteria": score_levels,
        },
        "states_purpose": {
            "type": "noul",
            "instructions": (
                "Does the student summary correctly state or paraphrase "
                "the main purpose of the article?"
            ),
            "criteria": {
                "true": (
                    "The summary clearly states or accurately paraphrases "
                    "the article's main purpose or thesis."
                ),
                "false": (
                    "The summary does not mention or misrepresents the "
                    "article's main purpose."
                ),
            },
        },
        "contradicts": {
            "type": "noul",
            "instructions": (
                "Does the student summary contain any statements that "
                "contradict facts presented in the article?"
            ),
            "criteria": {
                "true": (
                    "The summary contains one or more statements that "
                    "directly contradict information in the article."
                ),
                "false": (
                    "The summary does not contain contradictions with "
                    "the article content."
                ),
            },
        },
        "style": {
            "type": "choice",
            "instructions": (
                "Classify the writing style of the student summary."
            ),
            "criteria": {
                "paraphrase": (
                    "The summary restates concepts in the student's own "
                    "words, demonstrating genuine understanding."
                ),
                "near_copy": (
                    "The summary closely mirrors the original article's "
                    "wording with minimal rephrasing."
                ),
                "off_topic": (
                    "The summary discusses topics unrelated to the article."
                ),
            },
        },
    }

    # One noul per key point
    for i, point in enumerate(key_points):
        questions[f"point_{i}"] = {
            "type": "noul",
            "instructions": (
                f"Does the student summary cover this key point: {point}"
            ),
            "criteria": {
                "true": f"The summary addresses this point: {point}",
                "false": f"The summary does not address this point: {point}",
            },
        }

    return {
        "model": settings.JEV_MODEL,
        "state": state,
        "questions": questions,
    }


# ── Response parser ──────────────────────────────────────────────


def _parse_response(response_data: dict, rubric: dict) -> dict:
    """
    Parse a Jev Decisions API response into a normalised result dict.

    Returns
    -------
    dict with keys:
        raw_score, confidence, states_purpose, contradicts, contradicts_prob,
        style, style_confidence, covered, missed, cost
    """
    answers = response_data.get("answers", {})
    usage = response_data.get("usage", {})
    key_points = rubric.get("key_points", [])

    # ── Comprehension score ──
    comp = answers.get("comprehension", {})
    raw_score = comp.get("score", 4.0)
    confidence = comp.get("confidence", 0.0)

    # ── Purpose ──
    states_purpose_prob = answers.get("states_purpose", {}).get(
        "probability", 0.5
    )

    # ── Contradiction ──
    contradicts_prob = answers.get("contradicts", {}).get("probability", 0.0)

    # ── Style ──
    style_answer = answers.get("style", {})
    style_choice = style_answer.get("choice", "paraphrase")
    style_confidence = style_answer.get("confidence", 0.0)

    # ── Per-point coverage ──
    covered: list[str] = []
    missed: list[str] = []
    coverage_threshold = 0.6

    for i, point in enumerate(key_points):
        prob = answers.get(f"point_{i}", {}).get("probability", 0.0)
        if prob > coverage_threshold:
            covered.append(point)
        else:
            missed.append(point)

    return {
        "raw_score": raw_score,
        "confidence": confidence,
        "states_purpose": states_purpose_prob > 0.6,
        "contradicts": contradicts_prob > 0.6,
        "contradicts_prob": contradicts_prob,
        "style": style_choice,
        "style_confidence": style_confidence,
        "covered": covered,
        "missed": missed,
        "cost": usage.get("cost", 0.0),
    }


# ── Public grading function ──────────────────────────────────────


async def grade(
    summary: str,
    article_text: str,
    rubric: dict,
) -> dict:
    """
    Call the Jev Decisions API to grade a student summary.

    Implements a 20-second timeout and one retry.  Raises ``RuntimeError``
    if both attempts fail (callers should fall back to the LLM judge).

    Returns
    -------
    dict  — the normalised result from ``_parse_response``.
    """
    payload = _build_request(rubric, summary, article_text)
    headers = {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    last_error: Exception | None = None

    for attempt in range(1 + JEV_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=JEV_TIMEOUT) as client:
                response = await client.post(
                    JEV_ENDPOINT,
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

            logger.info(
                "Jev grading succeeded (attempt %d/%d): cost=%.6f",
                attempt + 1,
                1 + JEV_RETRIES,
                data.get("usage", {}).get("cost", 0),
            )
            return _parse_response(data, rubric)

        except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            last_error = exc
            logger.warning(
                "Jev grading attempt %d/%d failed: %s",
                attempt + 1,
                1 + JEV_RETRIES,
                exc,
            )
        except Exception as exc:
            last_error = exc
            logger.error(
                "Jev grading attempt %d/%d unexpected error: %s",
                attempt + 1,
                1 + JEV_RETRIES,
                exc,
            )

    raise RuntimeError(
        f"Jev grading failed after {1 + JEV_RETRIES} attempts: {last_error}"
    )
