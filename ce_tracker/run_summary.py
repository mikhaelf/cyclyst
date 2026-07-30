"""Tracks web-app process starts (app_runs) and summarizes issuer-portal submissions
recorded since the previous run -- the dashboard's "since your last run" banner.

Portal submissions happen out-of-band, via a separate ce_tracker.portal_submit CLI session
(see README) -- this module never submits anything itself. It only reads the same
activity_portal_submissions ledger the Overview tab's submission queue already reads
(views.py's portal_submit.ready_to_submit), grouped by what changed since the app was last
started.
"""
from datetime import datetime


def record_app_start(conn, now=None):
    """Call exactly once per process start (see ce_tracker/__init__.py:create_app).
    Unconditionally inserts a new row -- previous_run_started_at() finds the run before this
    one, which is the cutoff for this run's summary."""
    now = now or datetime.now()
    conn.execute("INSERT INTO app_runs (started_at) VALUES (?)", (now.isoformat(timespec="seconds"),))
    conn.commit()


def previous_run_started_at(conn):
    """The start time of the run before the current (most recent) one -- None on the very
    first run ever, since there's nothing yet to compare against."""
    row = conn.execute("SELECT started_at FROM app_runs ORDER BY id DESC LIMIT 1 OFFSET 1").fetchone()
    return row["started_at"] if row else None


def is_current_run_dismissed(conn):
    row = conn.execute("SELECT dismissed FROM app_runs ORDER BY id DESC LIMIT 1").fetchone()
    return bool(row["dismissed"]) if row else False


def dismiss_current_run(conn):
    """Hides the banner for the rest of this run only -- the next process start gets a fresh
    (undismissed) summary. Permanent opt-out is a separate flag, see set_summary_enabled."""
    conn.execute("UPDATE app_runs SET dismissed = 1 WHERE id = (SELECT MAX(id) FROM app_runs)")
    conn.commit()


def summary_enabled(conn):
    row = conn.execute("SELECT run_summary_enabled FROM app_settings WHERE id = 1").fetchone()
    return True if row is None else bool(row["run_summary_enabled"])


def set_summary_enabled(conn, enabled):
    conn.execute(
        "INSERT INTO app_settings (id, run_summary_enabled) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET run_summary_enabled = excluded.run_summary_enabled",
        (1 if enabled else 0,),
    )
    conn.commit()


def submissions_since(conn, cutoff_iso):
    """Portal submissions recorded since cutoff_iso, grouped by credential. Returns [] when
    cutoff_iso is None (first run ever) or nothing new was recorded. Each row:
    {'credential_id', 'name', 'issuer', 'count'}."""
    if cutoff_iso is None:
        return []
    rows = conn.execute(
        "SELECT c.id AS credential_id, c.name AS name, c.issuer AS issuer, COUNT(*) AS count "
        "FROM activity_portal_submissions s JOIN credentials c ON c.id = s.credential_id "
        "WHERE s.recorded_at > ? GROUP BY s.credential_id, c.name, c.issuer ORDER BY count DESC",
        (cutoff_iso,),
    ).fetchall()
    return [dict(r) for r in rows]
