"""
Socratic follow-up interrogation service.

After scoring a student's summary, this service generates targeted
follow-up questions specifically aimed at the concepts the student missed.
The student answers these to demonstrate deeper retention.
"""

import json
import logging

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not settings.OPENROUTER_API_KEY:
            raise RuntimeError("OPENROUTER_API_KEY is not set.")
        _client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.OPENROUTER_API_KEY,
        )
    return _client


async def generate_followup_questions(
    article_title: str,
    article_text: str,
    missed_points: list[str],
    covered_points: list[str],
    student_summary: str,
    num_questions: int = 3,
) -> list[dict]:
    """
    Generate Socratic follow-up questions targeting specifically the concepts
    the student missed in their initial summary.

    Parameters
    ----------
    article_title : str
    article_text : str
    missed_points : list[str]  — rubric points the student failed to cover
    covered_points : list[str] — rubric points the student covered
    student_summary : str      — the student's original submission
    num_questions : int        — how many questions to generate (default 3)

    Returns
    -------
    list[dict]  — each item: {"question": str, "hint": str, "target_concept": str}
    """
    client = _get_client()

    missed_str = "\n".join(f"- {p}" for p in missed_points) if missed_points else "None"
    covered_str = "\n".join(f"- {p}" for p in covered_points) if covered_points else "None"

    prompt = f"""You are a Socratic tutor helping a student deepen their comprehension.

Article Title: {article_title}

Article Text (first 1500 chars):
{article_text[:1500]}

Student's Summary:
{student_summary}

Concepts the student COVERED well:
{covered_str}

Concepts the student MISSED or explained poorly:
{missed_str}

Generate exactly {num_questions} targeted follow-up questions that:
1. Focus specifically on the MISSED concepts (not the covered ones)
2. Are open-ended and require explanation (not yes/no)
3. Progress from easier recall to deeper application
4. Each question includes a short hint to guide thinking without giving away the answer

Return ONLY a JSON array with {num_questions} objects, each with:
- "question": the follow-up question string
- "hint": a one-sentence hint that guides thinking without revealing the answer
- "target_concept": the missed point this question addresses

Example format:
[
  {{
    "question": "Why does TCP use a randomly chosen sequence number rather than starting at zero?",
    "hint": "Think about what an attacker could do if they could predict the next sequence number.",
    "target_concept": "Security rationale for random ISN"
  }}
]
"""

    try:
        response = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert Socratic tutor. "
                        "Return ONLY a valid JSON array, no other text."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )
        raw = response.choices[0].message.content or "[]"
        # Strip markdown fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        questions = json.loads(raw)
        if not isinstance(questions, list):
            questions = []
        return questions[:num_questions]
    except Exception as exc:
        logger.warning("Follow-up question generation failed: %s", exc)
        # Fallback: generate simple missed-concept questions without LLM
        return [
            {
                "question": f"Can you explain the concept: '{point}'?",
                "hint": "Try to explain it in your own words without referring back to the text.",
                "target_concept": point,
            }
            for point in missed_points[:num_questions]
        ]


async def evaluate_followup_answer(
    question: str,
    target_concept: str,
    article_text: str,
    student_answer: str,
) -> dict:
    """
    Evaluate a student's answer to a Socratic follow-up question.

    Returns
    -------
    dict  {"score": int (1-5), "correct": bool, "explanation": str}
    """
    client = _get_client()

    prompt = f"""Evaluate this student's answer to a follow-up question.

Target Concept: {target_concept}
Question Asked: {question}
Student's Answer: {student_answer}

Reference (article excerpt, first 1000 chars):
{article_text[:1000]}

Rate the answer on a 1-5 scale:
5 = Demonstrates full, accurate understanding of the concept
4 = Mostly correct, minor gaps
3 = Partially correct, significant gap in understanding
2 = Shows basic awareness but major misunderstanding
1 = Incorrect or no understanding demonstrated

Return ONLY a JSON object with:
- "score": integer 1-5
- "correct": boolean (true if score >= 3)
- "explanation": one sentence explaining the score and what was right/wrong
"""

    try:
        response = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert educational assessor. Return only valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        raw = response.choices[0].message.content or "{}"
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        result = json.loads(raw)
        score = int(result.get("score", 1))
        return {
            "score": score,
            "correct": score >= 3,
            "explanation": result.get("explanation", ""),
        }
    except Exception as exc:
        logger.warning("Follow-up answer evaluation failed: %s", exc)
        return {"score": 1, "correct": False, "explanation": "Could not evaluate answer."}
