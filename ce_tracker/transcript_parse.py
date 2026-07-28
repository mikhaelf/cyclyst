"""Bulk multi-record PDF transcript parsing and staging (design doc §11a). Distinct from
certificate_parse.py (one PDF -> one CE record, pre-filling the manual Add Continuing Education form):
this handles an issuer-exported transcript PDF holding many CE rows at once (e.g. ISC2's
"Official CPE Transcript" export), extracting every row and staging each one into
pending_activities the same way §5/§5a do -- never a direct write to `activities`.
"""
import re
from datetime import datetime

from .calendar_ingest import _find_duplicate

# CPE Type strings confirmed from a real ISC2 transcript export. Extend only with types
# actually observed in a real transcript -- never guessed, same posture as certificate_parse's
# KNOWN_PROVIDERS list. A type that isn't recognized just stays attached to the title rather
# than being mis-split, so the row still stages (with a slightly noisier title) instead of
# being silently dropped.
KNOWN_CPE_TYPES = [
    "Online webinars, podcasts and other online materials",
    "Industry Conference",
    "CPE Rollover",
]

ROLLOVER_TYPE = "CPE Rollover"

_DATE_RE = re.compile(r"(\d{1,2}-[A-Za-z]{3}-\d{4})")
_TABLE_HEADER_RE = re.compile(r"Activity Date\s+Activity Description\s+CPE Type\s+Credit", re.I)
_TABLE_END_RE = re.compile(r"Total\b.*?\d+\.\d{1,2}", re.I)
_GROUP_HEADING_RE = re.compile(r"Group\s+([A-Za-z0-9]+)\s+Credits", re.I)
_CREDIT_RE = re.compile(r"(\d+\.\d{1,2})\s*$")

# A near-certain match -- same date, essentially identical title, matching hours -- against
# something already sitting in pending_activities (any status) or already logged in
# activities isn't worth a second card in the review queue for the same real-world event, so
# it's skipped rather than staged-and-flagged. Anything below this still stages, flagged for
# a human to look at, via the existing calendar_ingest.DUPLICATE_TITLE_THRESHOLD band.
AUTO_SKIP_THRESHOLD = 0.98


def _slug(title):
    return "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")[:40]


def _normalize_ws(text):
    return re.sub(r"\s+", " ", text).strip()


def _split_title_and_type(desc_and_type):
    for cpe_type in sorted(KNOWN_CPE_TYPES, key=len, reverse=True):
        if desc_and_type.endswith(cpe_type):
            title = desc_and_type[: -len(cpe_type)].strip()
            if title:
                return title, cpe_type
    return desc_and_type, ""


def _parse_row(date_token, body):
    body = _normalize_ws(body)
    credit_match = _CREDIT_RE.search(body)
    if not credit_match:
        return None
    desc_and_type = body[: credit_match.start()].strip()
    title, cpe_type = _split_title_and_type(desc_and_type)
    if not title:
        return None
    try:
        date_iso = datetime.strptime(date_token, "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None
    return {"date": date_iso, "title": title, "cpe_type": cpe_type, "hours": float(credit_match.group(1))}


def _extract_table_chunks(text):
    """Yields (group_label, chunk_text) for every 'Activity Date ...' table header found --
    group_label is read from the nearest 'Group X Credits' heading above that header, and the
    chunk runs from just after the header to the next 'Total ... credit-total' marker (or the
    end of the text, if a table runs off the end without one). A transcript covering more than
    one credential prints one such table per credential; when the same real activity counts
    toward more than one credential's pool (see §2 -- ISC2 concentrations share the base
    credential's Group A pool), it's printed once per table, which the caller's date+title+
    hours dedup collapses back into a single candidate."""
    headers = list(_TABLE_HEADER_RE.finditer(text))
    for header_match in headers:
        preceding = text[: header_match.start()]
        group_label = None
        for group_match in _GROUP_HEADING_RE.finditer(preceding):
            group_label = f"Group {group_match.group(1)}"  # last match before this header wins

        body_start = header_match.end()
        end_match = _TABLE_END_RE.search(text, pos=body_start)
        body_end = end_match.start() if end_match else len(text)
        yield group_label, text[body_start:body_end]


def extract_rows(text):
    """Parses every CE row out of a transcript's already-extracted text. Returns
    (candidates, rollover_rows): rollover_rows are CPE Rollover-type entries, excluded from
    candidacy (they're a carried-over credit balance, not CE evidence) but returned separately
    so a caller can surface the amount as a suggested manual_adjustment_hours value instead of
    fabricating an itemized activity to justify it.

    `applies` is deliberately left blank on every candidate, same as calendar-sourced ones
    (calendar_ingest.py): a real ISC2 transcript prints no credential-name text next to each
    table (only a "Certification awarded on <date>" + "Group A Credits (minimum N)" pair,
    neither of which reliably maps to one specific tracked credential without guessing) --
    so which credential(s) a row applies to is left for the human to fill in at review, not
    inferred from PDF layout.
    """
    seen = {}  # (date, title, hours) -> candidate, for dedup across tables in this one document
    rollover_rows = []

    for group_label, chunk in _extract_table_chunks(text):
        tokens = _DATE_RE.split(chunk)  # ['', date1, body1, date2, body2, ...]
        for date_token, body in zip(tokens[1::2], tokens[2::2]):
            row = _parse_row(date_token, body)
            if not row:
                continue
            if row["cpe_type"] == ROLLOVER_TYPE:
                rollover_rows.append({**row, "group": group_label})
                continue
            key = (row["date"], row["title"], row["hours"])
            if key in seen:
                continue
            seen[key] = {
                "id": f"pdf:{row['date']}:{_slug(row['title'])}",
                "date": row["date"],
                "provider": None,
                "title": row["title"],
                "hours_guess": row["hours"],
                "applies": "",
                "category": group_label,
                "source": "transcript_pdf",
            }

    return list(seen.values()), rollover_rows


def extract_text(file_storage):
    from pypdf import PdfReader
    reader = PdfReader(file_storage.stream)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def parse(file_storage):
    return extract_rows(extract_text(file_storage))


def stage_batch(conn, files):
    """Parses every uploaded file, merges candidates across the whole batch on the same
    date+title+hours key used for intra-document dedup (so two files that both happen to
    describe the same activity only stage once), then stages each surviving candidate.
    A per-file parse failure is recorded but doesn't stop the rest of the batch -- one
    corrupt/unsupported upload shouldn't block everything else in it."""
    merged = {}
    all_rollovers = []
    file_errors = []

    for file_storage in files:
        if not file_storage or not file_storage.filename:
            continue
        try:
            candidates, rollovers = parse(file_storage)
        except Exception:
            file_errors.append(file_storage.filename)
            continue
        for c in candidates:
            key = (c["date"], c["title"], c["hours_guess"])
            merged.setdefault(key, c)
        all_rollovers.extend(rollovers)

    staged, skipped_exact, flagged = [], [], []
    for c in merged.values():
        dup = _find_duplicate(conn, c)
        if dup and dup[1] >= AUTO_SKIP_THRESHOLD:
            skipped_exact.append((c, dup))
            continue

        cur = conn.execute(
            "INSERT OR IGNORE INTO pending_activities "
            "(id, date, provider, title, hours_guess, applies, source, category, flagged, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,'pending')",
            (c["id"], c["date"], c["provider"], c["title"], c["hours_guess"],
             c["applies"], c["source"], c["category"], 0),
        )
        if cur.rowcount == 0:
            continue  # identical id already staged from a prior upload of this same file

        if dup:
            ref, score = dup
            conn.execute(
                "UPDATE pending_activities SET duplicate_of = ?, duplicate_confidence = ?, flagged = 1 WHERE id = ?",
                (ref, round(score, 2), c["id"]),
            )
            flagged.append((c, ref, score))
        staged.append(c)

    conn.commit()
    return {
        "files_seen": len(files),
        "file_errors": file_errors,
        "candidates_found": len(merged),
        "staged": staged,
        "skipped_exact_duplicates": skipped_exact,
        "flagged_possible_duplicates": flagged,
        "rollover_rows": all_rollovers,
    }
