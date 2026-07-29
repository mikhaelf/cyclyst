"""Agenda-style event schedule parsing (multi-session-day conference/retreat agendas, as
distinct from certificates or issuer transcripts -- see certificate_parse.py and
transcript_parse.py for those).

Stages ONE aggregate candidate per uploaded agenda -- a suggested total hours (summed from
non-denied sessions) plus an itemized breakdown for human review -- rather than one row per
session: a conference is logged as a single activity (see e.g. "Fieldstone Ventures Con - Tech and
strategy" in real usage), and there's no existing "merge several approved rows into one
activity" step that per-session staging would need.

Deliberately supports exactly two formats, each validated against exactly one real sample --
same honestly-narrow scope as transcript_parse.py's ISC2-only caveat, just two branches
instead of one. A third company's agenda is not guaranteed to fit either:

- "Meridian Security-style": day headers with no year ("Monday | June 22"), explicit start-end time
  ranges ("8:40 AM - 9:30 AM Description"). The missing year must come from the caller
  (see `default_year`) -- never guessed.
- "Fieldstone Ventures-style": day headers with a year ("Thursday, June 4, 2026"), bulleted single start
  times with no explicit end ("9:00 a.m. - Description") -- duration is inferred from the
  next timestamped line's start time, which is only knowable for a session that isn't the
  last one of its day.
"""
import re
from datetime import date

from .calendar_ingest import _find_duplicate

# Matches transcript_parse.py's threshold for "don't stage a second card for the same
# real-world event" -- a near-certain duplicate is skipped rather than flagged.
AUTO_SKIP_THRESHOLD = 0.98

_WEEKDAYS = "Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday"
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_MONTH_INDEX = {name: i for i, name in enumerate(
    "January February March April May June July August September October November December".split(), 1
)}

_DAY_HEADER_NO_YEAR_RE = re.compile(
    rf"^\s*(?:{_WEEKDAYS})\s*\|\s*({_MONTHS})\s+(\d{{1,2}})\s*$", re.I
)
_DAY_HEADER_WITH_YEAR_RE = re.compile(
    rf"^\s*(?:{_WEEKDAYS}),?\s+({_MONTHS})\s+(\d{{1,2}}),\s*(\d{{4}})\s*$", re.I
)

_TIME_TOKEN = r"(\d{1,2}):(\d{2})\s*([AaPp])\.?[Mm]\.?"
_TIME_RANGE_RE = re.compile(rf"^\s*{_TIME_TOKEN}\s*[-–]\s*{_TIME_TOKEN}\s*(.*)$")
_TIME_ONLY_RE = re.compile(rf"^\s*[•\-*]?\s*{_TIME_TOKEN}\s*[-–]?\s*(.*)$")

_NON_CONTENT_PATTERNS = [
    re.compile(r"\bbreakfast\b", re.I),
    re.compile(r"\bbrunch\b", re.I),
    re.compile(r"\blunch\b", re.I),
    re.compile(r"\bdinner\b", re.I),
    re.compile(r"\breception\b", re.I),
    re.compile(r"\bmixer\b", re.I),
    re.compile(r"\bcocktail\b", re.I),
    re.compile(r"\barrivals?\b", re.I),
    re.compile(r"\bcheck[\s-]?in\b", re.I),
    re.compile(r"\bcheck[\s-]?out\b", re.I),
    re.compile(r"\bdeparture\b", re.I),
    re.compile(r"\bbreak\b", re.I),  # standalone "Break" -- word boundary excludes "breakout"
    re.compile(r"\bworkout\b", re.I),
    re.compile(r"\byoga\b", re.I),
    re.compile(r"\bpilates\b", re.I),
    re.compile(r"\bconcludes?\b", re.I),
    re.compile(r"\bfarewell\b", re.I),
    re.compile(r"\bafternoon activities\b", re.I),
    re.compile(r"\bat your leisure\b", re.I),
]


def is_non_content(description):
    """True if this agenda line item is hospitality/logistics rather than CE content.

    A denylist, not a content classifier: False means "not recognized as logistics," not
    "confirmed to be real CE content" -- a human still decides which surviving lines
    actually count and for how long.
    """
    return any(pattern.search(description) for pattern in _NON_CONTENT_PATTERNS)


def _to_24h(hour, minute, meridiem):
    hour = int(hour)
    if meridiem.upper() == "P" and hour != 12:
        hour += 12
    if meridiem.upper() == "A" and hour == 12:
        hour = 0
    return hour, int(minute)


def _parse_day_header(line, default_year):
    """Returns (date_or_None, year_was_missing). year_was_missing is True only for a
    no-year-format header when no default_year was supplied -- lets the caller distinguish
    "not a day header at all" from "was a day header but couldn't resolve without a year"."""
    m = _DAY_HEADER_WITH_YEAR_RE.match(line)
    if m:
        month_name, day, year = m.groups()
        return date(int(year), _MONTH_INDEX[month_name.title()], int(day)), False

    m = _DAY_HEADER_NO_YEAR_RE.match(line)
    if m:
        month_name, day = m.groups()
        if default_year is None:
            return None, True
        return date(int(default_year), _MONTH_INDEX[month_name.title()], int(day)), False

    return None, False


def _parse_time_line(line):
    """Returns (start_minutes, end_minutes_or_None, description) for a recognized
    timestamped line, or None if the line isn't one. Times are minutes-since-midnight so
    duration is a plain subtraction. Range form is tried first -- the single-time pattern
    would otherwise swallow a range's second timestamp as part of the description."""
    m = _TIME_RANGE_RE.match(line)
    if m:
        sh, sm, smer, eh, em, emer, desc = m.groups()
        start_h, start_m = _to_24h(sh, sm, smer)
        end_h, end_m = _to_24h(eh, em, emer)
        return start_h * 60 + start_m, end_h * 60 + end_m, desc.strip()

    m = _TIME_ONLY_RE.match(line)
    if m:
        sh, sm, smer, desc = m.groups()
        start_h, start_m = _to_24h(sh, sm, smer)
        return start_h * 60 + start_m, None, desc.strip()

    return None


def extract_sessions(text, default_year=None):
    """Walks extracted PDF text line by line, tracking the current day from headers and
    building one entry per timestamped line -- merging soft-wrapped continuation lines
    (no leading timestamp) into the previous entry's description, same wrapping pypdf
    produces for a description that spans more than one physical line.

    Returns (sessions, year_needed): year_needed is True if a no-year-format day header was
    found without a default_year to resolve it against -- surfaced separately so the caller
    can ask for a year specifically, rather than reporting a generic "nothing found"."""
    sessions = []
    current_day = None
    year_needed = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        day, missing_year = _parse_day_header(line, default_year)
        if missing_year:
            year_needed = True
            current_day = None
            continue
        if day is not None:
            current_day = day
            continue

        parsed = _parse_time_line(line)
        if parsed is not None:
            if current_day is None:
                continue  # timestamped line before any resolvable day header -- can't date it
            start_minutes, end_minutes, description = parsed
            sessions.append({
                "date": current_day,
                "start_minutes": start_minutes,
                "end_minutes": end_minutes,
                "description": description,
            })
            continue

        if sessions and sessions[-1]["date"] == current_day:
            sessions[-1]["description"] = (sessions[-1]["description"] + " " + line).strip()

    return sessions, year_needed


def _resolve_durations(sessions):
    """Fills in end_minutes for sessions that only have a start time, inferred from the
    next session's start time on the same day. The last session of a day (nothing to infer
    an end from) is left with end_minutes=None -- not guessed."""
    for i, session in enumerate(sessions):
        if session["end_minutes"] is not None:
            continue
        nxt = sessions[i + 1] if i + 1 < len(sessions) else None
        if nxt is not None and nxt["date"] == session["date"]:
            session["end_minutes"] = nxt["start_minutes"]
    return sessions


def summarize(sessions):
    """Classifies each session (denied vs. content, resolvable duration vs. not) and sums
    the countable ones into a suggested total, with an itemized breakdown string covering
    every session either way -- included, excluded, or skipped for an unresolvable
    duration -- so nothing is silently dropped from human view."""
    sessions = _resolve_durations(sessions)

    included, excluded, unresolved = [], [], []
    for s in sessions:
        if is_non_content(s["description"]):
            excluded.append(s)
        elif s["end_minutes"] is None:
            unresolved.append(s)
        else:
            included.append(s)

    total_hours = round(sum((s["end_minutes"] - s["start_minutes"]) / 60 for s in included), 2)
    last_date = max((s["date"] for s in included), default=None)

    lines = []
    for s in included:
        hours = round((s["end_minutes"] - s["start_minutes"]) / 60, 2)
        lines.append(f"Included: {s['description']} ({hours:g}h)")
    for s in unresolved:
        lines.append(f"Skipped (duration unknown): {s['description']}")
    for s in excluded:
        lines.append(f"Excluded: {s['description']}")

    return {
        "date": last_date,
        "hours": total_hours,
        "breakdown": "\n".join(lines),
        "included_count": len(included),
        "excluded_count": len(excluded),
        "unresolved_count": len(unresolved),
    }


def _slug(title):
    return "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")[:40]


def extract_text(file_storage):
    from pypdf import PdfReader
    reader = PdfReader(file_storage.stream)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def parse(file_storage, default_year=None):
    """Returns (candidate, error). candidate is None with an error message when nothing
    countable was found -- including the specific case where a no-year-format agenda needs
    a default_year that wasn't supplied. Provider is deliberately left blank: unlike a
    certificate's issuer, an agenda's host isn't reliably extractable across formats (no
    shared positive signal, same reasoning as the denylist-only classifier above), so it's
    left for the human to fill in at review, same as calendar_ingest.py already leaves
    `applies` blank rather than guessing."""
    text = extract_text(file_storage)
    sessions, year_needed = extract_sessions(text, default_year=default_year)
    summary = summarize(sessions)

    if summary["date"] is None:
        if year_needed:
            return None, "This agenda's dates don't include a year — enter one and try again."
        return None, "No countable sessions found in this agenda."

    filename = (file_storage.filename or "agenda").rsplit(".", 1)[0]
    title = re.sub(r"[_-]+", " ", filename).strip() or "Imported Agenda"

    candidate = {
        "id": f"agenda:{_slug(title)}:{summary['date'].isoformat()}",
        "date": summary["date"].isoformat(),
        "provider": None,
        "title": title,
        "hours_guess": summary["hours"],
        "applies": "",
        "source": "agenda_pdf",
        "category": None,
        "breakdown": summary["breakdown"],
    }
    return candidate, None


def stage(conn, candidate):
    """Inserts the aggregate candidate into pending_activities -- never a direct write to
    activities, same posture as every other candidate source. A near-certain duplicate (by
    calendar_ingest._find_duplicate's score, e.g. a conference already logged manually or
    already staged from a prior upload of the same agenda) is skipped rather than staged a
    second time for the same real-world event; anything less certain still stages, flagged."""
    dup = _find_duplicate(conn, candidate)
    if dup and dup[1] >= AUTO_SKIP_THRESHOLD:
        conn.commit()
        return {"staged": False, "skipped_exact_duplicate": True, "duplicate": dup}

    cur = conn.execute(
        "INSERT OR IGNORE INTO pending_activities "
        "(id, date, provider, title, hours_guess, applies, source, category, breakdown, flagged, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,'pending')",
        (candidate["id"], candidate["date"], candidate["provider"], candidate["title"],
         candidate["hours_guess"], candidate["applies"], candidate["source"],
         candidate["category"], candidate["breakdown"], 0),
    )
    if cur.rowcount == 0:
        conn.commit()
        return {"staged": False, "skipped_exact_duplicate": False, "duplicate": None, "already_staged": True}

    if dup:
        ref, score = dup
        conn.execute(
            "UPDATE pending_activities SET duplicate_of = ?, duplicate_confidence = ?, flagged = 1 WHERE id = ?",
            (ref, round(score, 2), candidate["id"]),
        )

    conn.commit()
    return {"staged": True, "skipped_exact_duplicate": False, "duplicate": dup, "already_staged": False}
