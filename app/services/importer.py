"""
PDF and URL article importer service.

Allows importing articles from:
 1. Uploaded PDF files (parsed with pypdf)
 2. Web URLs (fetched with httpx, HTML cleaned to text)

Returns a normalized {title, text} dict ready for article creation.
"""

import io
import logging
import re

import httpx
import pypdf

logger = logging.getLogger(__name__)

# Minimum word count for an importable article
MIN_WORDS = 150
MAX_WORDS = 3000


def _clean_text(raw: str) -> str:
    """Normalize whitespace and strip control characters from extracted text."""
    # Replace form feeds and multiple newlines
    text = re.sub(r"\f", "\n", raw)
    # Collapse 3+ newlines into 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Remove non-printable chars except newlines and tabs
    text = re.sub(r"[^\x09\x0A\x20-\x7E\u00A0-\uFFFF]", " ", text)
    # Collapse multiple spaces
    text = re.sub(r"  +", " ", text)
    return text.strip()


def extract_text_from_pdf(pdf_bytes: bytes) -> dict:
    """
    Extract text content from a PDF file's bytes.

    Parameters
    ----------
    pdf_bytes : bytes  — raw PDF file content

    Returns
    -------
    dict  {"title": str, "text": str, "page_count": int}

    Raises
    ------
    ValueError  if the PDF has too little or too much text
    """
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    page_count = len(reader.pages)

    all_text_parts = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        all_text_parts.append(page_text)

    raw_text = "\n\n".join(all_text_parts)
    text = _clean_text(raw_text)

    word_count = len(text.split())
    if word_count < MIN_WORDS:
        raise ValueError(
            f"Extracted text is too short ({word_count} words). "
            f"Minimum required: {MIN_WORDS} words. "
            "Please upload a text-based (not scanned) PDF."
        )
    if word_count > MAX_WORDS:
        # Truncate to MAX_WORDS words to keep within reasonable session length
        text = " ".join(text.split()[:MAX_WORDS])
        logger.info("PDF text truncated from %d to %d words", word_count, MAX_WORDS)

    # Attempt to extract title from PDF metadata or first line
    title = ""
    meta = reader.metadata
    if meta and meta.get("/Title"):
        title = str(meta["/Title"]).strip()
    if not title:
        # Try using the first non-empty line as the title
        for line in text.split("\n"):
            line = line.strip()
            if line and len(line) < 200:
                title = line
                break
    if not title:
        title = "Imported PDF Article"

    return {"title": title, "text": text, "page_count": page_count}


async def extract_text_from_url(url: str) -> dict:
    """
    Fetch a web URL and extract readable article text.

    Parameters
    ----------
    url : str  — the HTTP/HTTPS URL to fetch

    Returns
    -------
    dict  {"title": str, "text": str, "url": str}

    Raises
    ------
    ValueError  if the URL cannot be fetched or yields too little text
    """
    if not url.startswith(("http://", "https://")):
        raise ValueError("URL must start with http:// or https://")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; ENGRAM-Importer/1.0; "
            "+https://engram.academic)"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain",
    }

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
    except httpx.TimeoutException:
        raise ValueError(f"Request timed out when fetching: {url}")
    except httpx.HTTPStatusError as e:
        raise ValueError(f"HTTP {e.response.status_code} when fetching: {url}")
    except Exception as e:
        raise ValueError(f"Could not fetch URL: {e}")

    content_type = response.headers.get("content-type", "")
    raw = response.text

    # Extract title from <title> tag
    title = ""
    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw, re.IGNORECASE | re.DOTALL)
    if title_match:
        title = title_match.group(1).strip()
        title = re.sub(r"<[^>]+>", "", title).strip()  # strip nested tags

    # Remove scripts, styles, nav, footer, header blocks
    for tag in ["script", "style", "nav", "footer", "header", "aside", "noscript", "iframe"]:
        raw = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", " ", raw, flags=re.DOTALL | re.IGNORECASE)

    # Strip remaining HTML tags
    text = re.sub(r"<[^>]+>", " ", raw)

    # Decode common HTML entities
    entities = {
        "&amp;": "&", "&lt;": "<", "&gt;": ">",
        "&nbsp;": " ", "&quot;": '"', "&#39;": "'",
        "&ldquo;": '"', "&rdquo;": '"', "&ndash;": "-", "&mdash;": "-",
    }
    for entity, replacement in entities.items():
        text = text.replace(entity, replacement)

    text = _clean_text(text)

    word_count = len(text.split())
    if word_count < MIN_WORDS:
        raise ValueError(
            f"Extracted text is too short ({word_count} words). "
            "The page may require JavaScript or be behind a paywall."
        )
    if word_count > MAX_WORDS:
        text = " ".join(text.split()[:MAX_WORDS])

    if not title:
        title = "Imported Web Article"

    return {"title": title, "text": text, "url": url}
