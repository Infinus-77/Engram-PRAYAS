"""
Scoring pipeline for student summaries.

Orchestrates the full grading flow:

1. **Pre-guards** (no API cost) — injection detection, minimum length,
   n-gram overlap.
2. **Jev Decisions API** — structured comprehension scoring, per-point
   coverage, style classification, contradiction detection.
3. **Hybrid scoring** — combines Jev's raw score with guard results,
   contradiction penalties, near-copy caps, and confidence checks.
4. **LLM feedback** — personalised 2–3 sentence feedback from the
   grading results.
5. **LLM fallback** — if Jev fails/times out, the LLM judges alone
   and the result is marked ``source: "llm_fallback"``.
"""

import json
import logging
import re
import time

from app.services import jev_grader
from app.services.feedback import create_feedback
from app.services.llm import llm_fallback_judge

logger = logging.getLogger(__name__)


# ── Pre-guards (no API cost) ─────────────────────────────────────


def _ngram_overlap(summary: str, article: str, n: int = 5) -> float:
    """
    Calculate n-gram overlap ratio between the summary and article.
    High overlap suggests copy-paste rather than genuine comprehension.
    """
    def ngrams(text: str, n: int) -> list[tuple[str, ...]]:
        words = text.lower().split()
        return [tuple(words[i : i + n]) for i in range(len(words) - n + 1)]

    summary_ngrams = ngrams(summary, n)
    article_ngrams_set = set(ngrams(article, n))

    if not summary_ngrams:
        return 0.0

    matches = sum(1 for ng in summary_ngrams if ng in article_ngrams_set)
    return matches / len(summary_ngrams)


def _check_injection(text: str) -> bool:
    """Detect common prompt-injection phrases that try to game the grader."""
    patterns = [
        r"give\s+me\s+(a\s+)?10",
        r"ignore\s+(previous\s+)?instructions",
        r"score\s+(me\s+)?(a\s+)?10",
        r"maximum\s+score",
        r"perfect\s+rating",
        r"you\s+are\s+a\s+helpful",
        r"disregard\s+(all|previous)",
    ]
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in patterns)


# ── Hybrid scoring logic ─────────────────────────────────────────


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _compute_hybrid_rating(jev_result: dict, flags: list[str]) -> int:
    """
    Combine Jev's raw score with guards into the final 1–10 rating.

    Rules (from implementation plan §3):
      - rating = clamp(round(score + 1), 1, 10)
      - contradicts P(yes) > 0.6  → subtract 2 points
      - style == near_copy or off_topic → cap at 4
      - near_copy flag from n-gram guard → cap at 4
    """
    raw = jev_result.get("raw_score", 4.0)
    rating = _clamp(round(raw + 1), 1, 10)

    # Contradiction penalty
    if jev_result.get("contradicts", False):
        rating = _clamp(rating - 2, 1, 10)
        if "contradiction" not in flags:
            flags.append("contradiction")

    # Style-based cap
    style = jev_result.get("style", "paraphrase")
    if style in ("near_copy", "off_topic"):
        rating = min(rating, 4)
        if style not in flags:
            flags.append(style)

    # N-gram near-copy cap (from pre-guard)
    if "near_copy" in flags:
        rating = min(rating, 4)

    return rating


# ── Main pipeline ────────────────────────────────────────────────


async def score_summary(
    summary: str,
    article_text: str,
    rubric_json: str | None,
) -> dict:
    """
    Score a student summary against the source article.

    This is the main entry point called by the submit route.  It runs
    pre-guards first (free), then the Jev + LLM pipeline.

    Returns
    -------
    dict with keys:
        rating, feedback, covered, missed, flags, source,
        jev_score_raw, confidence, jev_cost, llm_cost, latency_ms
    """
    start = time.time()
    words = summary.split()
    word_count = len(words)
    flags: list[str] = []
    jev_cost: float = 0.0
    llm_cost: float = 0.0

    def _result(
        rating, feedback, covered, missed, source,
        jev_score_raw=None, confidence=None,
    ):
        return {
            "rating": rating,
            "feedback": feedback,
            "covered": covered,
            "missed": missed,
            "flags": flags,
            "source": source,
            "jev_score_raw": jev_score_raw,
            "confidence": confidence,
            "jev_cost": jev_cost,
            "llm_cost": llm_cost,
            "latency_ms": int((time.time() - start) * 1000),
        }

    # ── Pre-guards (no API cost) ─────────────────────────────────

    # Guard: prompt injection
    if _check_injection(summary):
        flags.append("injection_detected")
        return _result(
            1,
            "Your submission was flagged for containing manipulation phrases. "
            "Please write a genuine summary of the article in your own words.",
            [],
            ["All key points"],
            "guard",
        )

    # Guard: minimum length
    if word_count < 15:
        flags.append("too_short")
        return _result(
            2,
            "Your summary is too short to evaluate properly. "
            "Try to write at least a few sentences covering the main ideas.",
            [],
            ["Insufficient content to assess"],
            "guard",
        )

    # Guard: near-copy detection (5-gram overlap)
    overlap = _ngram_overlap(summary, article_text, n=5)
    if overlap > 0.5:
        flags.append("near_copy")

    # ── Parse rubric ─────────────────────────────────────────────

    rubric: dict = {}
    if rubric_json:
        try:
            rubric = json.loads(rubric_json)
        except json.JSONDecodeError:
            logger.warning("Invalid rubric_json, proceeding without rubric")

    if not rubric.get("key_points"):
        logger.warning("No rubric available — results may be less accurate")

    # ── Jev grading (primary) ────────────────────────────────────

    source = "jev"
    try:
        jev_result = await jev_grader.grade(summary, article_text, rubric)

        jev_cost = jev_result.get("cost", 0.0)
        rating = _compute_hybrid_rating(jev_result, flags)
        covered = jev_result.get("covered", [])
        missed = jev_result.get("missed", [])
        jev_score_raw = jev_result.get("raw_score")
        confidence = jev_result.get("confidence")

        # Low-confidence warning
        if confidence is not None and confidence < 0.4:
            flags.append("low_confidence")

        # Generate LLM feedback from Jev results
        feedback_input = {
            "rating": rating,
            "covered": covered,
            "missed": missed,
            "style": jev_result.get("style", "paraphrase"),
            "contradicts": jev_result.get("contradicts", False),
        }
        fb = await create_feedback(feedback_input, rubric, summary)
        feedback = fb["feedback"]
        llm_cost = fb.get("cost") or 0.0

        return _result(
            rating, feedback, covered, missed, source,
            jev_score_raw=jev_score_raw, confidence=confidence,
        )

    except Exception as exc:
        logger.warning("Jev grading failed, falling back to LLM: %s", exc)
        source = "llm_fallback"

    # ── LLM fallback judge ───────────────────────────────────────

    try:
        fallback = await llm_fallback_judge(summary, article_text, rubric)
        llm_cost = fallback.get("cost") or 0.0

        rating = fallback["rating"]
        covered = fallback.get("covered", [])
        missed = fallback.get("missed", [])
        feedback = fallback.get("feedback", "")

        # Apply n-gram near-copy cap even for LLM fallback
        if "near_copy" in flags:
            rating = min(rating, 4)
            feedback = (
                "Your summary appears to closely mirror the original text. "
                "Comprehension is best demonstrated by restating concepts in "
                "your own words and explaining the relationships between ideas."
            )

        flags.append("llm_fallback")

        return _result(rating, feedback, covered, missed, source)

    except Exception as exc:
        logger.error("LLM fallback also failed: %s", exc)
        flags.append("scoring_error")

        return _result(
            5,
            "We encountered an error while scoring your summary. "
            "Your response has been saved and can be re-evaluated later.",
            [],
            [],
            "error",
        )
