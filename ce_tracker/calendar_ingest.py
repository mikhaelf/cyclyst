"""Scores Google Calendar events as candidate CE activities and stages matches into
pending_activities. Pure-Python, stdlib only — no Google API client, no OAuth. This module
never talks to Google directly: whoever is driving ingestion (a live Claude session with the
Calendar MCP connection already attached) fetches events, writes them to a JSON file, and hands
them to this module via the CLI entry point below. See ce-cpe-tracker-design.md §5.

Never guesses `category` or `applies` on a candidate — there's no reliable signal mapping a
calendar title to a specific credential or CE category, and every candidate goes through the
mandatory review-before-approve form (routes.review_pending_form) before it can become a real
activities row, so the human fills those in rather than the matcher fabricating them.
"""

import argparse
import json
import re
from datetime import datetime
from difflib import SequenceMatcher

from .db import get_db

# Tier 1 — providers already seen in this user's real activity history. A hit here (title or
# organizer domain) is unflagged; everything else that stages is flagged for review.
KNOWN_PROVIDERS = ["Northbridge Learning", "ISACA", "IAPP", "PMI", "ISC2", "Brightline Events", "Fieldstone Ventures", "Coursera", "SANS"]

# Tier 3 — CE-intent terms drawn from the actual qualifying-activity categories in the PMI CCR
# Handbook and ISC2 Certification Maintenance Handbook (Courses/Training, Organization
# Meetings, Give a Presentation, Share Knowledge, Vendor presentation, etc.) — not invented
# generic English. A hit here without a Tier 1 provider match is always flagged.
CE_KEYWORDS = [
    "conference", "webinar", "workshop", "seminar", "training", "course",
    "chapter meeting", "panel", "lunch and learn", "lunch-and-learn", "mentoring",
    "presentation", "lecture", "guest speaker", "demo", "vendor demo", "briefing",
    "symposium", "bootcamp", "summit", "roundtable", "certificate", "cpe", "continuing education",
]

# Common calendar noise that should never stage, even if it happens to contain a CE keyword
# (e.g. a "team meeting" invite). Directly generalizes the project's own "Book Club" example.
DENYLIST = [
    "1:1", "1-1", "standup", "stand-up", "team meeting", "family", "personal",
    "lunch", "interview", "performance review", "book club", "pto", "ooo",
]

MAX_REASONABLE_HOURS = 8
DUPLICATE_TITLE_THRESHOLD = 0.6


def _word_in(term, text):
    return re.search(r"\b" + re.escape(term.lower()) + r"\b", text.lower()) is not None


def _contains_any(text, terms):
    return any(_word_in(term, text) for term in terms)


def _event_date_and_hours(event):
    """Returns (date_iso, hours_or_None, is_all_day). Uses the event's own offset-aware
    dateTime directly rather than normalizing to system-local — a late-evening event could
    otherwise land on the wrong calendar day, which matters for calendar-year deadlines like
    ISACA's Jan 15."""
    start = event.get("start", {})
    end = event.get("end", {})
    if "date" in start:  # all-day event — no real wall-clock hours signal
        return start["date"], None, True

    start_dt = datetime.fromisoformat(start["dateTime"])
    end_dt = datetime.fromisoformat(end["dateTime"])
    hours = round((end_dt - start_dt).total_seconds() / 3600 * 4) / 4  # nearest quarter hour
    return start_dt.date().isoformat(), hours, False


def _organizer_domain(event):
    organizer_email = event.get("organizer", {}).get("email", "")
    return organizer_email.split("@")[-1].lower() if "@" in organizer_email else ""


def score_event(event, known_providers=KNOWN_PROVIDERS, ce_keywords=CE_KEYWORDS, denylist=DENYLIST):
    """Returns a candidate dict ready for stage_candidates(), or None if this event shouldn't
    be staged at all. `event` is a plain dict shaped like a Google Calendar API event resource."""
    if event.get("status") == "cancelled":
        return None

    title = event.get("summary") or ""
    if not title or _contains_any(title, denylist):
        return None

    # Title match ("Northbridge Learning Security Briefing") is a strong signal — the event is literally named
    # after a known CE provider. Organizer-domain match alone is weaker: any event organized
    # through a vendor's calendar system matches, including purely social ones (a real example
    # caught in dry-run testing: an "Fieldstone Ventures"-organized "Networking Reception" matched on domain
    # with zero CE content in the title). Domain match alone still stages as a candidate, but
    # never earns the unflagged trust tier on its own — only a title hit does that.
    domain = _organizer_domain(event)
    provider_title_hit = next((p for p in known_providers if _word_in(p, title)), None)
    provider_domain_hit = next((p for p in known_providers if p.lower() in domain), None)
    keyword_hit = _contains_any(title, ce_keywords)
    if not provider_title_hit and not provider_domain_hit and not keyword_hit:
        return None

    date_iso, hours, is_all_day = _event_date_and_hours(event)
    flagged = is_all_day or not provider_title_hit
    if hours is not None and hours > MAX_REASONABLE_HOURS:
        flagged = True

    provider = provider_title_hit or provider_domain_hit or event.get("organizer", {}).get("displayName") or None

    return {
        "id": f"cal:{event['id']}",
        "date": date_iso,
        "provider": provider,
        "title": title,
        "hours_guess": hours,
        "source": "calendar",
        "flagged": flagged,
    }


def _title_similarity(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _score_duplicate(candidate, row):
    score = _title_similarity(candidate["title"], row["title"])
    cand_provider, row_provider = candidate.get("provider"), row["provider"]
    if cand_provider and row_provider:
        if cand_provider.lower() in row_provider.lower() or row_provider.lower() in cand_provider.lower():
            score = min(1.0, score + 0.1)

    # Hours is a secondary signal, not a gate: candidates that never carry an hours guess
    # (most calendar events) skip this untouched, same score as before. When both sides do
    # have one, an exact match nudges score up; a large mismatch (>50% apart) pulls it back
    # down even if the title matched well, so a same-day near-duplicate with wildly different
    # hours doesn't get silently treated as the same activity on title alone.
    cand_hours = candidate.get("hours_guess")
    row_hours = row["hours_guess"] if "hours_guess" in row.keys() else None
    if cand_hours is not None and row_hours is not None:
        if cand_hours == row_hours:
            score = min(1.0, score + 0.1)
        elif row_hours and abs(cand_hours - row_hours) / row_hours > 0.5:
            score = max(0.0, score - 0.15)

    return score


def _find_duplicate(conn, candidate):
    """Same-date is a required gate (checked via the WHERE clause); title similarity is the
    primary signal, with a small bonus if provider also matches and if hours also line up.
    Checked against both real activities and other already-staged pending rows, since a
    calendar event might duplicate something logged manually from a certificate, not just
    another calendar scan. `activities.hours` is aliased to `hours_guess` so both queries
    hand `_score_duplicate` the same field name regardless of which table a row came from."""
    best = None

    for row in conn.execute(
        "SELECT id, title, provider, hours AS hours_guess FROM activities WHERE date = ?", (candidate["date"],)
    ):
        score = _score_duplicate(candidate, row)
        if score >= DUPLICATE_TITLE_THRESHOLD and (best is None or score > best[1]):
            best = (f"activity:{row['id']}", score)

    for row in conn.execute(
        "SELECT id, title, provider, hours_guess FROM pending_activities WHERE date = ? AND id != ?",
        (candidate["date"], candidate["id"]),
    ):
        score = _score_duplicate(candidate, row)
        if score >= DUPLICATE_TITLE_THRESHOLD and (best is None or score > best[1]):
            best = (row["id"], score)

    return best


def stage_candidates(conn, candidates, window_start, window_end, events_seen):
    """Inserts candidates with INSERT OR IGNORE, keyed on the calendar-derived id — an event
    already staged in *any* status (pending/approved/dismissed) from a prior overlapping run is
    silently skipped, which also makes a dismissal sticky (it never resurfaces). Runs duplicate
    detection only on rows that were actually newly inserted, and logs one calendar_ingest_runs
    row per call. Returns a summary dict for the caller to report back to the user."""
    staged = 0
    for c in candidates:
        cur = conn.execute(
            "INSERT OR IGNORE INTO pending_activities "
            "(id, date, provider, title, hours_guess, applies, source, flagged, status) "
            "VALUES (?,?,?,?,?,?,?,?,'pending')",
            (c["id"], c["date"], c["provider"], c["title"], c["hours_guess"], "", c["source"], int(c["flagged"])),
        )
        if cur.rowcount == 0:
            continue  # already seen under this id in a prior run

        staged += 1
        dup = _find_duplicate(conn, c)
        if dup:
            ref, score = dup
            conn.execute(
                "UPDATE pending_activities SET duplicate_of = ?, duplicate_confidence = ? WHERE id = ?",
                (ref, round(score, 2), c["id"]),
            )

    conn.execute(
        "INSERT INTO calendar_ingest_runs (run_at, window_start, window_end, events_seen, candidates_staged) "
        "VALUES (?,?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"), window_start, window_end, events_seen, staged),
    )
    conn.commit()
    return {"events_seen": events_seen, "candidates_scored": len(candidates), "candidates_staged": staged}


def default_window_start(conn):
    """First run (no prior calendar_ingest_runs row): the earliest cycle_start_date among
    active cpe_hours credentials — nothing before any current cycle's start could count toward
    it anyway, so it's a real bound, not a guess. Every later run defaults to the window_end of
    the most recent run (the 'since last run' watermark)."""
    last_run = conn.execute(
        "SELECT window_end FROM calendar_ingest_runs ORDER BY run_at DESC LIMIT 1"
    ).fetchone()
    if last_run:
        return last_run["window_end"]

    earliest = conn.execute(
        "SELECT MIN(cycle_start_date) AS d FROM credentials WHERE archived = 0 AND cycle_start_date IS NOT NULL"
    ).fetchone()
    return earliest["d"] if earliest and earliest["d"] else None


def main():
    parser = argparse.ArgumentParser(description="Stage Google Calendar events as candidate CE activities.")
    parser.add_argument("--events-file", required=True, help="JSON file: array of Calendar API event resources")
    parser.add_argument("--since", help="Override window start (ISO date); defaults to since-last-run")
    parser.add_argument("--until", help="Window end (ISO date); defaults to today")
    args = parser.parse_args()

    with open(args.events_file) as f:
        events = json.load(f)

    conn = get_db()
    window_start = args.since or default_window_start(conn) or ""
    window_end = args.until or datetime.now().date().isoformat()

    candidates = [c for c in (score_event(e) for e in events) if c]
    summary = stage_candidates(conn, candidates, window_start, window_end, events_seen=len(events))
    conn.close()

    print(
        f"Scanned {summary['events_seen']} events ({window_start} to {window_end}): "
        f"{summary['candidates_scored']} matched, {summary['candidates_staged']} newly staged."
    )


if __name__ == "__main__":
    main()
