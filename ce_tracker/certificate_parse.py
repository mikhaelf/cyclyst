"""Local-only certificate parsing for the "Add Training" upload assist.

First-pass, no network calls (see ce-cpe-tracker-design.md §11): extracts text from an
uploaded PDF and applies heuristics to guess date/provider/hours/title. Anything not
confidently found comes back blank for manual entry — this never guesses a value, since a
wrong auto-filled field is worse than an empty one someone has to type in themselves.
"""
import re
from datetime import datetime

from pypdf import PdfReader

KNOWN_PROVIDERS = [
    "ISACA", "ISC2", "(ISC)²", "PMI", "IAPP", "Credly", "SANS", "GIAC", "CompTIA",
    "Salesforce", "AWS", "Amazon Web Services", "Google Cloud", "Microsoft", "Coursera",
    "Udemy", "LinkedIn Learning", "Northbridge Learning", "Pluralsight", "edX",
]

_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"

_DATE_PATTERNS = [
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "%Y-%m-%d"),
    (re.compile(rf"\b(?:{_MONTHS})\s+\d{{1,2}},?\s+\d{{4}}\b", re.I), "%B %d %Y"),
    (re.compile(rf"\b\d{{1,2}}\s+(?:{_MONTHS})\s+\d{{4}}\b", re.I), "%d %B %Y"),
    (re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b"), "%m/%d/%Y"),
]

_HOURS_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:CPE|CEU|PDU)s?\b", re.I)
_HOURS_FALLBACK_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*hours?\b", re.I)

_TITLE_CUES = [
    re.compile(r"certificate of completion", re.I),
    re.compile(r"has (?:successfully )?completed", re.I),
    re.compile(r"for (?:successfully )?completing", re.I),
    re.compile(r"certifies that", re.I),
]


class UnsupportedFileType(Exception):
    pass


def extract_text(file_storage) -> str:
    filename = (file_storage.filename or "").lower()
    if not filename.endswith(".pdf"):
        raise UnsupportedFileType("Only PDF certificates are supported right now.")
    reader = PdfReader(file_storage.stream)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _find_date(text):
    for pattern, fmt in _DATE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        cleaned = m.group(0).replace(",", "")
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _find_hours(text):
    m = _HOURS_PATTERN.search(text) or _HOURS_FALLBACK_PATTERN.search(text)
    return m.group(1) if m else ""


def _find_provider(text):
    for name in KNOWN_PROVIDERS:
        if re.search(rf"\b{re.escape(name)}\b", text, re.I):
            return name
    return ""


def _find_title(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for i, line in enumerate(lines):
        if any(cue.search(line) for cue in _TITLE_CUES) and i + 1 < len(lines):
            candidate = lines[i + 1]
            if 3 <= len(candidate) <= 120:
                return candidate
    return ""


def parse(file_storage):
    """Best-effort 'date'/'hours'/'provider'/'title' from an uploaded PDF certificate.

    Each value is '' when not confidently found — callers should treat that as "leave for
    manual entry", not retry or guess further.
    """
    text = extract_text(file_storage)
    return {
        "date": _find_date(text),
        "hours": _find_hours(text),
        "provider": _find_provider(text),
        "title": _find_title(text),
    }
