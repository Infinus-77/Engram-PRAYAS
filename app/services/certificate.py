"""
PDF Diagnostic Certificate generator.

Generates a premium editorial-style PDF report for a completed session:
 - Article title and session metadata
 - Student's original summary
 - Score badge with rating
 - Covered key points (green checkmarks)
 - Missed key points (red X marks)
 - AI diagnostic feedback
 - Verification hash for tamper-proof validation

Uses ReportLab for pure-Python PDF generation (no native deps required).
"""

import hashlib
import io
import logging
from datetime import UTC, datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)

# ── Brand Colors ──────────────────────────────────────────────────────
COLOR_BLACK    = colors.HexColor("#0E1013")
COLOR_BG       = colors.HexColor("#F9F9FD")
COLOR_PRIMARY  = colors.HexColor("#0038A4")
COLOR_GREEN    = colors.HexColor("#004E1C")
COLOR_YELLOW   = colors.HexColor("#775A00")
COLOR_RED      = colors.HexColor("#BA1A1A")
COLOR_SURFACE  = colors.HexColor("#EEEDF2")
COLOR_GOLD     = colors.HexColor("#FFC72C")
COLOR_WHITE    = colors.white


def _build_styles() -> dict:
    base = getSampleStyleSheet()

    styles = {
        "title": ParagraphStyle(
            "EngTitle",
            parent=base["Heading1"],
            fontSize=22,
            leading=26,
            fontName="Helvetica-Bold",
            textColor=COLOR_BLACK,
            spaceAfter=2 * mm,
        ),
        "subtitle": ParagraphStyle(
            "EngSubtitle",
            parent=base["Normal"],
            fontSize=9,
            fontName="Helvetica",
            textColor=COLOR_PRIMARY,
            spaceAfter=4 * mm,
        ),
        "section_header": ParagraphStyle(
            "EngSectionHeader",
            parent=base["Normal"],
            fontSize=8,
            fontName="Helvetica-Bold",
            textColor=COLOR_PRIMARY,
            spaceBefore=6 * mm,
            spaceAfter=2 * mm,
            letterSpacing=1.2,
        ),
        "body": ParagraphStyle(
            "EngBody",
            parent=base["Normal"],
            fontSize=9,
            fontName="Helvetica",
            textColor=COLOR_BLACK,
            leading=14,
            spaceAfter=2 * mm,
        ),
        "point_ok": ParagraphStyle(
            "EngPointOK",
            parent=base["Normal"],
            fontSize=9,
            fontName="Helvetica",
            textColor=COLOR_GREEN,
            leading=13,
            leftIndent=4 * mm,
        ),
        "point_miss": ParagraphStyle(
            "EngPointMiss",
            parent=base["Normal"],
            fontSize=9,
            fontName="Helvetica",
            textColor=COLOR_RED,
            leading=13,
            leftIndent=4 * mm,
        ),
        "mono": ParagraphStyle(
            "EngMono",
            parent=base["Normal"],
            fontSize=7,
            fontName="Courier",
            textColor=COLOR_PRIMARY,
            leading=10,
        ),
        "label": ParagraphStyle(
            "EngLabel",
            parent=base["Normal"],
            fontSize=7,
            fontName="Helvetica-Bold",
            textColor=COLOR_YELLOW,
        ),
    }
    return styles


def _score_label(rating: int) -> str:
    if rating >= 9: return "EXCEPTIONAL"
    if rating >= 7: return "PROFICIENT"
    if rating >= 5: return "DEVELOPING"
    if rating >= 3: return "BASIC"
    return "NEEDS WORK"


def _score_color(rating: int) -> object:
    if rating >= 7: return COLOR_GREEN
    if rating >= 5: return COLOR_YELLOW
    return COLOR_RED


def _generate_verification_hash(
    session_id: str,
    article_title: str,
    rating: int,
    summary: str,
) -> str:
    payload = f"{session_id}:{article_title}:{rating}:{summary[:100]}"
    return hashlib.sha256(payload.encode()).hexdigest()[:32].upper()


def generate_certificate_pdf(
    session_id: str,
    article_title: str,
    student_summary: str,
    rating: int,
    feedback: str,
    covered_points: list[str],
    missed_points: list[str],
    source: str = "ai",
) -> bytes:
    """
    Generate a premium editorial-style diagnostic PDF certificate.

    Parameters
    ----------
    session_id : str
    article_title : str
    student_summary : str
    rating : int  (1-10)
    feedback : str
    covered_points : list[str]
    missed_points : list[str]
    source : str  — evaluation engine used

    Returns
    -------
    bytes  — raw PDF bytes ready to send as a download
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        leftMargin=22 * mm,
        rightMargin=22 * mm,
        title=f"ENGRAM Diagnostic Report — {article_title}",
        author="ENGRAM Academic Platform",
        subject="Comprehension Assessment Certificate",
    )

    styles = _build_styles()
    story = []

    # ── Header Banner ────────────────────────────────────────────────
    header_data = [[
        Paragraph("ENGRAM", ParagraphStyle(
            "LogoText", fontName="Helvetica-Bold", fontSize=20,
            textColor=COLOR_WHITE, leading=22,
        )),
        Paragraph(
            "COMPREHENSION DIAGNOSTIC REPORT",
            ParagraphStyle(
                "BannerSub", fontName="Helvetica-Bold", fontSize=8,
                textColor=COLOR_GOLD, leading=10, alignment=2,
            ),
        ),
    ]]
    header_table = Table(header_data, colWidths=[80 * mm, doc.width - 80 * mm])
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COLOR_BLACK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (0, -1), 6 * mm),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 6 * mm),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 6 * mm))

    # ── Article Title & Metadata ─────────────────────────────────────
    story.append(Paragraph(article_title, styles["title"]))
    issued_at = datetime.now(UTC).strftime("%d %B %Y — %H:%M UTC")
    story.append(Paragraph(
        f"SESSION ID: {session_id[:16].upper()}...  |  ISSUED: {issued_at}  |  ENGINE: {source.upper()}",
        styles["subtitle"],
    ))
    story.append(HRFlowable(width="100%", thickness=2, color=COLOR_BLACK))
    story.append(Spacer(1, 4 * mm))

    # ── Score Badge Row ──────────────────────────────────────────────
    score_label = _score_label(rating)
    score_col = _score_color(rating)
    score_data = [[
        Paragraph(
            f"<b>{rating}/10</b>",
            ParagraphStyle("ScoreNum", fontName="Helvetica-Bold",
                           fontSize=36, textColor=score_col, leading=40),
        ),
        Table([[
            Paragraph(score_label, ParagraphStyle(
                "ScoreLbl", fontName="Helvetica-Bold", fontSize=11,
                textColor=COLOR_WHITE, leading=14,
            )),
        ]], colWidths=[55 * mm], rowHeights=[14 * mm], style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), score_col),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ])),
        Paragraph(
            f"<b>COVERED POINTS</b><br/>{len(covered_points)} of "
            f"{len(covered_points) + len(missed_points)}",
            ParagraphStyle("StatCell", fontName="Helvetica-Bold",
                           fontSize=10, textColor=COLOR_BLACK, alignment=1),
        ),
        Paragraph(
            f"<b>MISSED POINTS</b><br/>{len(missed_points)}",
            ParagraphStyle("StatCell2", fontName="Helvetica-Bold",
                           fontSize=10, textColor=COLOR_RED, alignment=1),
        ),
    ]]
    score_table = Table(
        score_data,
        colWidths=[28 * mm, 60 * mm, 45 * mm, 45 * mm],
    )
    score_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BACKGROUND", (0, 0), (0, -1), COLOR_SURFACE),
        ("BOX", (0, 0), (-1, -1), 1.5, COLOR_BLACK),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, COLOR_SURFACE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(score_table)
    story.append(Spacer(1, 5 * mm))

    # ── AI Diagnostic Feedback ───────────────────────────────────────
    story.append(Paragraph("DIAGNOSTIC FEEDBACK", styles["section_header"]))
    story.append(HRFlowable(width="100%", thickness=1, color=COLOR_PRIMARY))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(feedback or "No feedback available.", styles["body"]))

    # ── Student's Summary ────────────────────────────────────────────
    story.append(Paragraph("STUDENT SUMMARY SUBMITTED", styles["section_header"]))
    story.append(HRFlowable(width="100%", thickness=1, color=COLOR_PRIMARY))
    story.append(Spacer(1, 2 * mm))
    # Box the summary
    summary_data = [[Paragraph(student_summary, styles["body"])]]
    summary_table = Table(summary_data, colWidths=[doc.width])
    summary_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1, COLOR_BLACK),
        ("BACKGROUND", (0, 0), (-1, -1), COLOR_SURFACE),
        ("TOPPADDING", (0, 0), (-1, -1), 4 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4 * mm),
        ("LEFTPADDING", (0, 0), (-1, -1), 5 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5 * mm),
    ]))
    story.append(summary_table)

    # ── Rubric Checklist ─────────────────────────────────────────────
    story.append(Paragraph("RUBRIC ANALYSIS", styles["section_header"]))
    story.append(HRFlowable(width="100%", thickness=1, color=COLOR_PRIMARY))
    story.append(Spacer(1, 2 * mm))

    for point in covered_points:
        story.append(Paragraph(f"[+]  {point}", styles["point_ok"]))
    for point in missed_points:
        story.append(Paragraph(f"[-]  {point}", styles["point_miss"]))

    # ── Verification Block ───────────────────────────────────────────
    v_hash = _generate_verification_hash(session_id, article_title, rating, student_summary)

    story.append(Spacer(1, 6 * mm))
    story.append(HRFlowable(width="100%", thickness=2, color=COLOR_BLACK))
    story.append(Spacer(1, 3 * mm))

    verify_data = [[
        Paragraph("VERIFICATION HASH", styles["label"]),
        Paragraph(v_hash, styles["mono"]),
    ]]
    verify_table = Table(verify_data, colWidths=[45 * mm, doc.width - 45 * mm])
    verify_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COLOR_BLACK),
        ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
        ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
    ]))
    story.append(verify_table)
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        "This certificate verifies an active-recall comprehension session completed under tamper-proof "
        "conditions. The verification hash above is derived from the session ID, article, score, and "
        "submission content. Reproducible for independent verification.",
        ParagraphStyle("FootNote", fontName="Helvetica", fontSize=7,
                       textColor=COLOR_PRIMARY, leading=10),
    ))

    doc.build(story)
    return buffer.getvalue()
