import os
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

# Overridable so tests and non-default setups can point at their own database file.
# The default lives in the gitignored data/ directory — see README "Where your data lives".
DB_PATH = Path(os.environ.get("CE_TRACKER_DB") or Path(__file__).resolve().parent.parent / "data" / "tracker.db")

# New columns added to `credentials` for computed (not stored) progress tracking.
# See ce-cpe-tracker-design.md §3/§9 for the source schema this maps to.
CREDENTIAL_COLUMNS = [
    ("renewal_type", "TEXT NOT NULL DEFAULT 'cpe_hours'"),  # 'cpe_hours' | 'exam' | 'mandatory_module'
    ("cycle_start_date", "TEXT"),   # ISO date; NULL for 'exam' type
    ("cycle_end_date", "TEXT"),     # ISO date; the renewal/expiration deadline
    ("reporting_calendar", "TEXT"),  # 'calendar_year' | 'cycle_anniversary'; NULL for 'exam'
    ("annual_minimum", "INTEGER"),
    ("annual_minimum_type", "TEXT"),  # 'suggested' | 'mandatory'
    ("grace_period_days", "INTEGER"),  # post-cycle-end window to finish earning/submitting; NULL = hard cutoff
    ("depends_on", "TEXT"),  # cascade — see progress.apply_dependency_cascade
    ("renews", "TEXT"),  # schema-only for now (design doc §9) — no computation branch reads this yet
]

# The issuer's own portal is the real system of record for CPE/PDU totals — this tracker is a
# secondary aggregator. `manual_adjustment_hours` (credentials) holds the portal's own
# last-confirmed total for the cycle; progress.py fills in only the gap versus what's actually
# itemized in `activities`, so it never double-counts hours that later get logged for real (see
# progress.py's compute_progress). `manual_adjustment` (categories, added in the same migration)
# is still a flat add-on, not gap-based — fine today since no tracked category currently sets it,
# but worth revisiting the same way if one starts to.
MANUAL_ADJUSTMENT_COLUMN = ("manual_adjustment_hours", "REAL")

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
    rule_type TEXT NOT NULL, -- 'min' or 'max'
    parent_id INTEGER REFERENCES categories(id), -- optional nesting, e.g. PMI's Talent Triangle legs inside "Education"
    manual_adjustment REAL -- hours the issuer's portal already confirms, not backed by a local activity
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
    id TEXT PRIMARY KEY, -- calendar-sourced rows use 'cal:{google_event_id}' so re-scans dedup for free
    date TEXT NOT NULL,
    provider TEXT,
    title TEXT NOT NULL,
    hours_guess REAL,
    applies TEXT NOT NULL DEFAULT '', -- comma-separated credential ids
    source TEXT,
    flagged INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending', -- 'pending' | 'approved' | 'dismissed'
    category TEXT,
    duplicate_of TEXT, -- e.g. 'activity:15' or 'cal:...' — a likely-duplicate match found at staging time
    duplicate_confidence REAL -- 0-1 score; NULL = no duplicate check matched anything
);

CREATE TABLE IF NOT EXISTS calendar_ingest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    events_seen INTEGER,
    candidates_staged INTEGER
);

-- Per (activity, credential) issuer-portal submission ledger — see ce_tracker/portal_submit.py
-- and ce-cpe-tracker-design.md §7a. A flat activities.status can't express "submitted to
-- ISC2 but not IAPP" for an activity whose `applies` names multiple credentials; this tracks
-- that per credential instead. Absence of a row means not yet submitted for that pair.
CREATE TABLE IF NOT EXISTS activity_portal_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL REFERENCES activities(id),
    credential_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'not_submitted', -- 'not_submitted' | 'submitted' | 'portal_confirmed'
    submitted_date TEXT,
    confirmation_id TEXT,
    source TEXT, -- 'automation' | 'portal-existing' | 'manual'
    isc2_category TEXT,   -- e.g. "Education" — what was actually picked on ISC2's form
    isc2_cpe_type TEXT,   -- e.g. "Industry Conference" — ISC2's activity-type dropdown value
    isc2_domain TEXT,     -- e.g. "Security Operations" — ISC2's domain checkbox(es), comma-separated if multiple
    recorded_at TEXT NOT NULL,  -- full timestamp (not just a date) — pending_reconciliation needs minute-level precision
    UNIQUE(activity_id, credential_id)
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


def _migrate_dependency_any_of_column(conn):
    """`depends_on` (existing, design doc §3/§9) expresses a single hard AND-dependency —
    Salesforce-style: this credential is invalid if that one lapses. Some real credentials
    (e.g. IAPP's Fellow of Information Privacy) need a second, OR-shaped dependency: valid
    as long as *at least one* of a group stays active. `depends_on_any_of` adds that without
    changing the meaning of the existing column."""
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(credentials)")}
    if "depends_on_any_of" in existing_cols:
        return
    conn.execute("ALTER TABLE credentials ADD COLUMN depends_on_any_of TEXT")  # comma-separated credential ids


def _migrate_category_parent_column(conn):
    """Adds nesting to category rules via a self-referential parent_id — the generic shape
    design doc §3 already sketched (`category_rules` + `sub_requirements`), e.g. PMI's Talent
    Triangle legs living inside a "Education" bucket, or ISC2's ISSEP sub-quota inside Group A.
    This is deliberately generic (any credential can nest any category under any other) rather
    than a PMI-specific column, so it backfills PMP's own Education/Giving Back split as data,
    not as special-cased code."""
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(categories)")}
    if "parent_id" in existing_cols:
        return
    conn.execute("ALTER TABLE categories ADD COLUMN parent_id INTEGER REFERENCES categories(id)")

    pmp_rows = conn.execute("SELECT id FROM categories WHERE credential_id = 'pmp'").fetchall()
    if pmp_rows:
        talent_triangle_ids = [r["id"] for r in pmp_rows]
        education_id = conn.execute(
            "INSERT INTO categories (credential_id, name, earned, target, rule_type) "
            "VALUES ('pmp', 'Education', 0, 35, 'min')"
        ).lastrowid
        conn.execute(
            "INSERT INTO categories (credential_id, name, earned, target, rule_type) "
            "VALUES ('pmp', 'Giving Back', 0, 25, 'max')"
        )
        placeholders = ",".join("?" * len(talent_triangle_ids))
        conn.execute(
            f"UPDATE categories SET parent_id = ? WHERE id IN ({placeholders})",
            (education_id, *talent_triangle_ids),
        )


def _migrate_grace_period_backfill(conn):
    """`grace_period_days` (design doc §3/§9) has existed as a schema column since the first
    computed-progress migration but was never backfilled or read by progress.py — this wires
    it up with the one real, confirmed value we have: ISC2's Certification Maintenance
    Handbook documents a 90-day post-cycle-end grace period to finish earning/submitting CPEs
    before suspension. Only setting it for the one ISC2-issued credential actually tracked
    (CISSP) — other issuers' grace periods aren't confirmed, so they stay NULL (no hard
    guess) rather than assuming a number. Guarded on NULL, not on first-run, so it's safe to
    run every init_db() without clobbering a value later corrected via the Edit Credential form."""
    conn.execute(
        "UPDATE credentials SET grace_period_days = 90 WHERE id = 'cissp' AND grace_period_days IS NULL"
    )


def _migrate_manual_adjustment_columns(conn):
    """See MANUAL_ADJUSTMENT_COLUMN above — lets a human record hours the issuer's own portal
    already confirms without backing them with a locally-itemized activity. No backfill here:
    unlike grace_period_days (a stable issuer policy), these totals are personal and change
    constantly as the user submits more to the issuer directly, so they're set once via the
    Edit Credential form (or a one-off script), not baked into a migration that would go stale
    immediately."""
    cred_cols = {row["name"] for row in conn.execute("PRAGMA table_info(credentials)")}
    if MANUAL_ADJUSTMENT_COLUMN[0] not in cred_cols:
        conn.execute(f"ALTER TABLE credentials ADD COLUMN {MANUAL_ADJUSTMENT_COLUMN[0]} {MANUAL_ADJUSTMENT_COLUMN[1]}")

    cat_cols = {row["name"] for row in conn.execute("PRAGMA table_info(categories)")}
    if "manual_adjustment" not in cat_cols:
        conn.execute("ALTER TABLE categories ADD COLUMN manual_adjustment REAL")


def _migrate_pending_activity_columns(conn):
    """Adds the fields calendar (and any future) ingestion needs: `category` so a staged row
    can carry a real category through the review-before-approve form (see routes.py's
    review_pending_form), and `duplicate_of`/`duplicate_confidence` for cross-source duplicate
    detection (e.g. a calendar event that looks like the same real-world activity as one
    already manually logged) — see ce_tracker/calendar_ingest.py."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(pending_activities)")}
    if "category" not in cols:
        conn.execute("ALTER TABLE pending_activities ADD COLUMN category TEXT")
    if "duplicate_of" not in cols:
        conn.execute("ALTER TABLE pending_activities ADD COLUMN duplicate_of TEXT")
    if "duplicate_confidence" not in cols:
        conn.execute("ALTER TABLE pending_activities ADD COLUMN duplicate_confidence REAL")


def _migrate_pending_activity_breakdown_column(conn):
    """Adds the itemized included/excluded/unresolved-session breakdown agenda_parse.py
    produces for a single aggregate candidate -- shown alongside the suggested total hours
    so a human can see what was counted before approving, not just the number."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(pending_activities)")}
    if "breakdown" not in cols:
        conn.execute("ALTER TABLE pending_activities ADD COLUMN breakdown TEXT")


def _migrate_portal_submission_isc2_columns(conn):
    """Records what was actually picked on ISC2's own form (category/CPE-type/domain) per
    ledger row, not just that a submission happened — otherwise that detail only ever
    existed in a chat transcript. See ce-cpe-tracker-design.md §7a."""
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(activity_portal_submissions)")}
    if "isc2_category" not in existing_cols:
        conn.execute("ALTER TABLE activity_portal_submissions ADD COLUMN isc2_category TEXT")
    if "isc2_cpe_type" not in existing_cols:
        conn.execute("ALTER TABLE activity_portal_submissions ADD COLUMN isc2_cpe_type TEXT")
    if "isc2_domain" not in existing_cols:
        conn.execute("ALTER TABLE activity_portal_submissions ADD COLUMN isc2_domain TEXT")


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    conn.executescript(SCHEMA)
    _migrate_credential_columns(conn)
    _migrate_member_id_column(conn)
    _migrate_dependency_any_of_column(conn)
    _migrate_category_parent_column(conn)
    _migrate_grace_period_backfill(conn)
    _migrate_manual_adjustment_columns(conn)
    _migrate_pending_activity_columns(conn)
    _migrate_pending_activity_breakdown_column(conn)
    _migrate_portal_submission_isc2_columns(conn)
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
    ]
    conn.executemany(
        "INSERT INTO categories (credential_id, name, earned, target, rule_type) VALUES (?,?,?,?,?)",
        categories,
    )

    # PMP's nested shape — a top-level Education/Giving Back split with the Talent Triangle
    # legs living inside Education — demonstrates the generic parent_id nesting mechanism
    # (not PMI-specific code; any credential's category rows can nest this way).
    education_id = conn.execute(
        "INSERT INTO categories (credential_id, name, earned, target, rule_type) VALUES ('pmp', 'Education', 23, 35, 'min')"
    ).lastrowid
    conn.execute(
        "INSERT INTO categories (credential_id, name, earned, target, rule_type) VALUES ('pmp', 'Giving Back', 0, 25, 'max')"
    )
    conn.executemany(
        "INSERT INTO categories (credential_id, name, earned, target, rule_type, parent_id) VALUES (?,?,?,?,?,?)",
        [
            ("pmp", "Ways of Working", 8, 8, "min", education_id),
            ("pmp", "Power Skills", 5, 8, "min", education_id),
            ("pmp", "Business Acumen", 10, 8, "min", education_id),
        ],
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
        ("p1", "2026-07-08", "Coursera", "Responsible AI Certificate", 5, "aigp,cissp", "calendar", 0),
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
