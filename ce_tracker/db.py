import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "tracker.db"

# New columns added to `credentials` for computed (not stored) progress tracking.
# See ce-cpe-tracker-design.md §3/§9 for the source schema this maps to.
CREDENTIAL_COLUMNS = [
    ("renewal_type", "TEXT NOT NULL DEFAULT 'cpe_hours'"),  # 'cpe_hours' | 'exam' | 'mandatory_module'
    ("cycle_start_date", "TEXT"),   # ISO date; NULL for 'exam' type
    ("cycle_end_date", "TEXT"),     # ISO date; the renewal/expiration deadline
    ("reporting_calendar", "TEXT"),  # 'calendar_year' | 'cycle_anniversary'; NULL for 'exam'
    ("annual_minimum", "INTEGER"),
    ("annual_minimum_type", "TEXT"),  # 'suggested' | 'mandatory'
    # Schema-only for now (design doc §9) — no computation branch reads these yet.
    ("grace_period_days", "INTEGER"),
    ("renews", "TEXT"),
    ("depends_on", "TEXT"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS credentials (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    issuer TEXT NOT NULL,
    cycle_label TEXT,
    days_left INTEGER,
    pct INTEGER,
    earned REAL,
    total REAL,
    unit TEXT,
    annual_label TEXT,
    note TEXT,
    status TEXT NOT NULL,
    badge_platform TEXT,
    badge_visibility TEXT,
    badge_url TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    retired_label TEXT
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    credential_id TEXT NOT NULL REFERENCES credentials(id),
    name TEXT NOT NULL,
    earned REAL NOT NULL,
    target REAL NOT NULL,
    rule_type TEXT NOT NULL -- 'min' or 'max'
);

CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    provider TEXT,
    title TEXT NOT NULL,
    hours REAL NOT NULL,
    category TEXT,
    applies TEXT NOT NULL, -- comma-separated credential ids
    status TEXT NOT NULL   -- 'submitted' or 'logged'
);

CREATE TABLE IF NOT EXISTS pending_activities (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    provider TEXT,
    title TEXT NOT NULL,
    hours_guess REAL,
    applies TEXT NOT NULL DEFAULT '', -- comma-separated credential ids
    source TEXT,
    flagged INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending' -- 'pending' | 'approved' | 'dismissed'
);
"""


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate_credential_columns(conn):
    """Add the computed-progress columns if this is an older database, then backfill
    the originally-seeded sample credentials so the app keeps working. Real dates for
    your own certs are still needed (see CLAUDE.local.md) — these are placeholders
    until corrected via the Edit Credential form."""
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(credentials)")}
    if "cycle_end_date" in existing_cols:
        return  # already migrated

    for col_name, col_def in CREDENTIAL_COLUMNS:
        if col_name not in existing_cols:
            conn.execute(f"ALTER TABLE credentials ADD COLUMN {col_name} {col_def}")

    today = date.today()

    def years_from(iso_date, years):
        d = date.fromisoformat(iso_date)
        try:
            return d.replace(year=d.year + years).isoformat()
        except ValueError:  # Feb 29 on a non-leap target year
            return d.replace(year=d.year + years, day=28).isoformat()

    # Every seeded credential's cycle_label TEXT ("Jun 2023 – Jun 2026" etc.) turned out to be
    # stale flavor text inconsistent with its own days_left number (e.g. cissp's label implies
    # a 2026 end date, but days_left=322 implies ~2027) — days_left is what actually drove the
    # displayed status/pct before this change, so every date below is derived from days_left
    # relative to *migration run time*, not from the label text, to guarantee the backfill
    # doesn't start out already past its own deadline.
    cissp_end = (today + timedelta(days=349)).isoformat()
    cisa_end = (today + timedelta(days=605)).isoformat()
    pmp_end = (today + timedelta(days=74)).isoformat()
    aigp_end = (today + timedelta(days=265)).isoformat()
    gcp_end = (today + timedelta(days=855)).isoformat()

    backfill = {
        "cissp": dict(renewal_type="cpe_hours", cycle_start_date=years_from(cissp_end, -3), cycle_end_date=cissp_end,
                      reporting_calendar="cycle_anniversary", annual_minimum=40, annual_minimum_type="suggested"),
        "cisa": dict(renewal_type="cpe_hours", cycle_start_date=years_from(cisa_end, -3), cycle_end_date=cisa_end,
                     reporting_calendar="calendar_year", annual_minimum=20, annual_minimum_type="mandatory"),
        "crisc": dict(renewal_type="cpe_hours", cycle_start_date=years_from(cisa_end, -3), cycle_end_date=cisa_end,
                      reporting_calendar="calendar_year", annual_minimum=20, annual_minimum_type="mandatory"),
        "pmp": dict(renewal_type="cpe_hours", cycle_start_date=years_from(pmp_end, -3), cycle_end_date=pmp_end,
                    reporting_calendar="cycle_anniversary", annual_minimum=None, annual_minimum_type=None),
        "aigp": dict(renewal_type="cpe_hours", cycle_start_date=years_from(aigp_end, -2), cycle_end_date=aigp_end,
                     reporting_calendar="cycle_anniversary", annual_minimum=None, annual_minimum_type=None),
        "gcp": dict(renewal_type="exam", cycle_start_date=None, cycle_end_date=gcp_end,
                    reporting_calendar=None, annual_minimum=None, annual_minimum_type=None),
    }
    for cred_id, values in backfill.items():
        conn.execute(
            "UPDATE credentials SET renewal_type=:renewal_type, cycle_start_date=:cycle_start_date, "
            "cycle_end_date=:cycle_end_date, reporting_calendar=:reporting_calendar, "
            "annual_minimum=:annual_minimum, annual_minimum_type=:annual_minimum_type WHERE id=:id",
            {**values, "id": cred_id},
        )


def _migrate_member_id_column(conn):
    """Add a dedicated member/cert ID field, and pull IDs back out of `note` where an
    earlier data-entry pass had appended them as free text (rendered oddly as a warning
    callout). Extraction is pattern-based, not hardcoded to specific IDs, since this file
    is tracked in git and personal ID numbers shouldn't live in source."""
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(credentials)")}
    if "member_id" in existing_cols:
        return  # already migrated

    conn.execute("ALTER TABLE credentials ADD COLUMN member_id TEXT")

    pattern = re.compile(r"^\S+(?: member)? ID (\S+?)\.\s*")
    rows = conn.execute("SELECT id, note FROM credentials WHERE note IS NOT NULL").fetchall()
    for row in rows:
        match = pattern.match(row["note"])
        if not match:
            continue
        conn.execute(
            "UPDATE credentials SET member_id=?, note=? WHERE id=?",
            (match.group(1), row["note"][match.end():].strip() or None, row["id"]),
        )


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    conn.executescript(SCHEMA)
    _migrate_credential_columns(conn)
    _migrate_member_id_column(conn)
    conn.commit()
    conn.close()


def seed_db():
    """Populate sample data mirroring the Claude Design mockup, for test-driving the UI.
    Skips seeding if credentials already exist, so re-running the app doesn't reset your data."""
    conn = get_db()
    existing = conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0]
    if existing:
        conn.close()
        return

    credentials = [
        ("cissp", "CISSP", "ISC2", "Jun 2023 – Jun 2026", 349, 65, 78, 120, "hrs",
         "No mandatory annual floor — suggested pace 40/yr toward the 120/3-yr total.", None,
         "on-track", "Credly", "public", "https://www.credly.com/badges/example-cissp"),
        ("cisa", "CISA", "ISACA", "Calendar-year annual · 3-yr cycle", 605, 79, 95, 120, "hrs",
         "Mandatory 20/yr on calendar year — 14/20 earned so far this year. Report to ISACA by Jan 15.", None,
         "on-track", "Credly", "public", "https://www.credly.com/badges/example-cisa"),
        ("crisc", "CRISC", "ISACA", "Calendar-year annual · 3-yr cycle", 605, 79, 95, 120, "hrs",
         "Shares its CE pool with CISA — same activities count toward both.", None,
         "on-track", "Credly", "private", None),
        ("pmp", "PMP", "PMI", "Sep 2022 – Sep 2025", 74, 70, 42, 60, "PDU",
         '≥35 PDU "Education" floor, ≤25 "Giving Back" cap.', None,
         "at-risk", "Credential.net", "public", "https://credential.net/example-pmp"),
        ("aigp", "AIGP", "IAPP", "2-yr term · ends Apr 2027", 265, 55, 11, 20, "credits",
         "Flat 20-credit pool, no category split. Up to 10 surplus credits carry over if earned in the final 6 months.",
         "2 pending items must be self-reported within 90 days of the event.",
         "on-track", "Credly", "private", None),
        ("gcp", "Cloud GenAI Leader", "Google Cloud", "Re-exam required", 855, None, None, None, None,
         "Not CE-based — renews by re-exam only. Visibility reminder, no hours tracked.", None,
         "reminder", "Credly", "public", "https://www.credly.com/badges/example-gcp"),
    ]
    conn.executemany(
        """INSERT INTO credentials
           (id, name, issuer, cycle_label, days_left, pct, earned, total, unit,
            annual_label, note, status, badge_platform, badge_visibility, badge_url)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        credentials,
    )

    categories = [
        ("cissp", "Group A", 65, 90, "min"),
        ("cissp", "Group B", 13, 30, "max"),
        ("pmp", "Ways of Working", 8, 8, "min"),
        ("pmp", "Power Skills", 5, 8, "min"),
        ("pmp", "Business Acumen", 10, 8, "min"),
    ]
    conn.executemany(
        "INSERT INTO categories (credential_id, name, earned, target, rule_type) VALUES (?,?,?,?,?)",
        categories,
    )

    activities = [
        ("2026-06-30", "Northbridge Learning", "AI Governance Symposium", 3, "Technical", "cissp,aigp", "submitted"),
        ("2026-06-12", "ISACA", "Audit Committee Webinar", 2, "Audit", "cisa,crisc", "logged"),
        ("2026-05-28", "PMI", "Agile Leadership Course", 6, "Ways of Working", "pmp", "submitted"),
        ("2026-05-14", "IAPP", "AI Governance Roundtable", 4, "Privacy Tech", "aigp", "logged"),
        ("2026-04-30", "SANS", "Cloud Security Bootcamp", 8, "Technical", "cissp", "submitted"),
        ("2026-04-02", "PMI", "Stakeholder Comms Workshop", 3, "Power Skills", "pmp", "logged"),
    ]
    conn.executemany(
        "INSERT INTO activities (date, provider, title, hours, category, applies, status) VALUES (?,?,?,?,?,?,?)",
        activities,
    )

    pending = [
        ("p1", "2026-07-08", "Coursera", "Responsible AI Certificate", 5, "aigp,cissp", "gmail", 0),
        ("p2", "2026-07-05", "ISC2", "Security Congress registration", 12, "cissp", "calendar", 0),
        ("p3", "2026-06-29", "Local Meetup", '"Book Club" calendar event', 1, "", "calendar", 1),
    ]
    conn.executemany(
        "INSERT INTO pending_activities (id, date, provider, title, hours_guess, applies, source, flagged) VALUES (?,?,?,?,?,?,?,?)",
        pending,
    )

    archived = [
        ("ceh", "CEH", "EC-Council", "Retired 2022 — not renewed"),
        ("itil", "ITIL Foundation", "Axelos", "Lapsed 2021 — no longer pursuing"),
    ]
    conn.executemany(
        "INSERT INTO credentials (id, name, issuer, status, archived, retired_label) VALUES (?,?,?,?,1,?)",
        [(a[0], a[1], a[2], "expired", a[3]) for a in archived],
    )

    conn.commit()
    conn.close()
