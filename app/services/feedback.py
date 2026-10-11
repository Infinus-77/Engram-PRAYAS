"""
Feedback generation service.

Wraps the LLM feedback call and provides a convenience entry point
that the scoring pipeline can call after Jev grading.
"""

import logging

from app.services.llm import generate_feedback as _llm_generate_feedback

logger = logging.getLogger(__name__)


async def create_feedback(
    jev_results: dict,
    rubric: dict,
    summary: str,
) -> dict:
    """
    Generate personalised feedback for a graded summary.

    Parameters
    ----------
    jev_results : dict
        Normalised grading results (from Jev or LLM fallback).
    rubric : dict
        The article's rubric ({purpose, key_points, misconceptions}).
    summary : str
        The student's original summary text.

    Returns
    -------
    dict  {"feedback": str, "cost": float|None}
    """
    try:
        result = await _llm_generate_feedback(jev_results, rubric, summary)
        logger.info("Feedback generated (cost=%s)", result.get("cost"))
        return result
    except Exception as exc:
        logger.error("Feedback generation failed: %s", exc)
        # Graceful degradation — return a generic message rather than
        # failing the whole submission.
        return {
            "feedback": (
                "We were unable to generate detailed feedback at this time. "
                "Your comprehension score is shown above."
            ),
            "cost": None,
        }
