"""
OpenRouter chat client for rubric generation, feedback, and fallback judging.

Uses the ``openai`` SDK pointed at OpenRouter's ``/api/v1`` endpoint.
All functions are async so they integrate naturally with FastAPI handlers.
"""

import json
import logging
import re

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

# ── Lazy singleton client ────────────────────────────────────────

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not settings.OPENROUTER_API_KEY:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. "
                "Add it to .env to enable LLM features."
            )
        _client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.OPENROUTER_API_KEY,
        )
    return _client


def _extract_json(text: str) -> dict:
    """
    Best-effort JSON extraction from LLM output.

    Handles responses wrapped in ```json ... ``` fences or with leading prose.
    """
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find a fenced JSON block
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try to find { ... } substring
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract JSON from LLM response: {text[:200]}")


# ── Rubric generation ────────────────────────────────────────────


async def generate_rubric(article_title: str, article_text: str) -> dict:
    """
    Generate a structured grading rubric for an article.

    Returns
    -------
    dict  {"rubric": {purpose, key_points, misconceptions}, "cost": float|None}
    """
    client = _get_client()

    prompt = (
        "Analyze the following technical article and produce a grading rubric "
        "as a JSON object.\n\n"
        f"Article Title: {article_title}\n\n"
        f"Article Text:\n{article_text}\n\n"
        "Return a JSON object with exactly these keys:\n"
        '- "purpose": A single sentence describing the article\'s main '
        "purpose or thesis.\n"
        '- "key_points": A list of 3–5 key points a good summary should '
        "cover. Each must be a concise sentence.\n"
        '- "misconceptions": A list of 1–2 common misconceptions a student '
        "might have after reading this article.\n\n"
        "Return ONLY the JSON object, no other text."
    )

    try:
        response = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert educational assessor. "
                        "Return only valid JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
    except Exception:
        # Some models don't support response_format — retry without it.
        logger.warning("response_format unsupported, retrying without it")
        response = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert educational assessor. "
                        "Return only valid JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )

    content = response.choices[0].message.content or ""
    rubric = _extract_json(content)

    # Validate required fields
    if "purpose" not in rubric or not isinstance(rubric.get("purpose"), str):
        rubric["purpose"] = "Unable to determine article purpose."
    if "key_points" not in rubric or not isinstance(
        rubric.get("key_points"), list
    ):
        rubric["key_points"] = []
    if "misconceptions" not in rubric or not isinstance(
        rubric.get("misconceptions"), list
    ):
        rubric["misconceptions"] = []

    cost = None
    if response.usage and hasattr(response.usage, "cost"):
        cost = response.usage.cost

    logger.info(
        "Rubric generated for '%s': %d key points, cost=%s",
        article_title,
        len(rubric["key_points"]),
        cost,
    )

    return {"rubric": rubric, "cost": cost}


# ── Feedback generation ──────────────────────────────────────────


async def generate_feedback(
    jev_results: dict,
    rubric: dict,
    summary: str,
) -> dict:
    """
    Generate 2–3 sentences of personalised feedback from Jev grading results.

    Returns
    -------
    dict  {"feedback": str, "cost": float|None}
    """
    client = _get_client()

    prompt = (
        "Based on the grading results below, write 2–3 sentences of "
        "constructive feedback for a student's article summary.\n\n"
        f"Article Purpose: {rubric.get('purpose', 'N/A')}\n"
        f"Key Points: {json.dumps(rubric.get('key_points', []))}\n\n"
        "Grading Results:\n"
        f"- Comprehension Score: {jev_results.get('rating', 'N/A')}/10\n"
        f"- Covered Points: {json.dumps(jev_results.get('covered', []))}\n"
        f"- Missed Points: {json.dumps(jev_results.get('missed', []))}\n"
        f"- Style: {jev_results.get('style', 'paraphrase')}\n"
        f"- Contains Contradictions: {jev_results.get('contradicts', False)}\n\n"
        f"Student Summary:\n{summary}\n\n"
        "Rules:\n"
        "- Name what was covered well and what was missed.\n"
        "- If there are contradictions, mention the error without quoting "
        "the article.\n"
        "- Do NOT quote the original article verbatim.\n"
        "- Be encouraging but honest.\n"
        "- Keep to 2–3 sentences."
    )

    response = await client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a supportive but honest educational tutor "
                    "giving brief feedback on a student's comprehension "
                    "summary."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.5,
        max_tokens=250,
    )

    feedback = (response.choices[0].message.content or "").strip()
    cost = None
    if response.usage and hasattr(response.usage, "cost"):
        cost = response.usage.cost

    return {"feedback": feedback, "cost": cost}


# ── LLM-only fallback judge ─────────────────────────────────────


async def llm_fallback_judge(
    summary: str,
    article_text: str,
    rubric: dict,
) -> dict:
    """
    Score a summary using the LLM alone (no Jev).

    Used when the Jev Decisions API is unavailable or times out.

    Returns
    -------
    dict  {rating, covered, missed, contradicts, style, feedback, cost}
    """
    client = _get_client()
    key_points = rubric.get("key_points", [])

    prompt = (
        "You are grading a student's comprehension summary of a technical "
        "article.\n\n"
        f"Article Text:\n{article_text}\n\n"
        f"Student Summary:\n{summary}\n\n"
        "Rubric:\n"
        f"- Purpose: {rubric.get('purpose', 'N/A')}\n"
        f"- Key Points: {json.dumps(key_points)}\n\n"
        "Return a JSON object with:\n"
        '- "rating": integer 1–10 comprehension score\n'
        '- "covered": list of key points the student covered '
        "(use the exact rubric wording)\n"
        '- "missed": list of key points the student missed '
        "(use the exact rubric wording)\n"
        '- "contradicts": boolean, true if the summary contradicts the '
        "article\n"
        '- "style": one of "paraphrase", "near_copy", "off_topic"\n'
        '- "feedback": 2–3 sentences of constructive feedback\n\n'
        "Return ONLY the JSON object."
    )

    try:
        response = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert educational assessor. "
                        "Return only valid JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
    except Exception:
        logger.warning("response_format unsupported, retrying without it")
        response = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert educational assessor. "
                        "Return only valid JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )

    content = response.choices[0].message.content or ""
    result = _extract_json(content)

    cost = None
    if response.usage and hasattr(response.usage, "cost"):
        cost = response.usage.cost

    return {
        "rating": max(1, min(10, int(result.get("rating", 5)))),
        "covered": result.get("covered", []),
        "missed": result.get("missed", []),
        "contradicts": bool(result.get("contradicts", False)),
        "style": result.get("style", "paraphrase"),
        "feedback": result.get("feedback", ""),
        "cost": cost,
    }
