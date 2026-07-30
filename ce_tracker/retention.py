"""Activity Log retention: archives (never deletes) activities older than a chosen cutoff,
after writing a CSV backup of exactly what's being archived.

Archiving only hides an activity from the default Activity Log view. It is not a second,
lesser copy of the data and it never removes anything from the database: progress.py's
computed totals, category rollups, duplicate detection (calendar_ingest._find_duplicate),
and CSV/PDF report exports all keep reading archived activities exactly like non-archived
ones -- only the Activity Log's own listing filters them out by default. Every archived
activity can be unarchived individually; nothing here is a one-way trip.
"""
import csv
from datetime import date, datetime

from .db import DB_PATH

ARCHIVE_DIR = DB_PATH.parent / "archives"

CSV_FIELDS = ["id", "date", "provider", "title", "hours", "category", "applies", "status"]


def cutoff_date(years, today=None):
    """The ISO date `years` years before today. Feb 29 falls back to Feb 28 on a non-leap
    target year, same handling as db.py's cycle-date backfill."""
    today = today or date.today()
    try:
        return today.replace(year=today.year - years).isoformat()
    except ValueError:
        return today.replace(year=today.year - years, day=28).isoformat()


def rows_to_archive(conn, cutoff_iso):
    return conn.execute(
        "SELECT * FROM activities WHERE date < ? AND archived = 0 ORDER BY date", (cutoff_iso,)
    ).fetchall()


def write_csv_backup(rows, now=None):
    """Writes a timestamped CSV of exactly the given rows to data/archives/ and returns its
    path -- a durable, human-readable record of what got archived that survives independently
    of the database itself."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%dT%H%M%S")
    path = ARCHIVE_DIR / f"activities-archived-{stamp}.csv"
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_FIELDS)
        for row in rows:
            writer.writerow([row[field] for field in CSV_FIELDS])
    return path


def archive_activities(conn, years):
    """Writes a CSV backup of every non-archived activity dated before `years` years ago,
    then marks exactly those rows archived. Returns (cutoff_iso, backup_path_or_None, count).
    backup_path is None when there was nothing to archive -- no empty file is written."""
    cutoff_iso = cutoff_date(years)
    rows = rows_to_archive(conn, cutoff_iso)
    if not rows:
        return cutoff_iso, None, 0

    backup_path = write_csv_backup(rows)
    conn.execute("UPDATE activities SET archived = 1 WHERE date < ? AND archived = 0", (cutoff_iso,))
    conn.commit()
    return cutoff_iso, backup_path, len(rows)


def unarchive_activity(conn, activity_id):
    conn.execute("UPDATE activities SET archived = 0 WHERE id = ?", (activity_id,))
    conn.commit()
