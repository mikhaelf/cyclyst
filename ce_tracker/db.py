import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "tracker.db"

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


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    conn.executescript(SCHEMA)
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
