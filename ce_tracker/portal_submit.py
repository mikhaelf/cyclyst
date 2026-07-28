"""Tracks which locally-approved CE activities have been submitted to an issuer's CPE portal,
per (activity, credential) — see ce-cpe-tracker-design.md §7a. CISSP/ISC2 only for v1.

This module never drives a browser and never submits anything itself. `record_submission` is
the single write path for the ledger, and it must only be called after a human has explicitly
approved that specific record for submission — there is no batch/loop command here by design,
matching the hard "explicit go per record, no batch auto-submit" rule in §7. The actual
portal interaction (navigating cpe.isc2.org, filling the form, and — before that — re-checking
the live CPE List for a duplicate that beat this ledger to it) is done by a live Claude-in-Chrome
session, which calls `list-ready` to find candidates and `record-submission` to log the result.

Category/domain/auto-reporting-provider notes below come from the first live run against the
real ISC2 form (2026-05-11) — see CLAUDE.local.md for the full account.
"""

import argparse
from datetime import date, datetime, timedelta

from .db import get_db

# Providers observed auto-reporting CPEs to ISC2 on the member's behalf (via ISC2's own
# provider partnerships — the "On Your Behalf" column on the live CPE List). An activity from
# one of these is NOT automatically a duplicate, but it's a strong enough signal that the live
# CPE List re-check (mandatory either way, per §7a) deserves extra scrutiny before submitting —
# ISC2 has twice turned out to already have the entry, once under a different title and hours.
AUTO_REPORTING_PROVIDERS = {"northbridge learning"}

# Suggests ISC2's Category > CPE Type, keyed by keywords found in the local `category` field
# or, failing that, the activity title. Always a suggestion for human review before typing
# into the live form — never auto-applied. Patterns below are what actually got accepted on
# the real form for six real submissions; expect to extend this as new activity shapes show up.
_CONFERENCE_KEYWORDS = ("summit", "conference", " con", "con -", "symposium", "forum")
_WEBINAR_KEYWORDS = ("webinar", "briefing", "online")


def suggest_isc2_mapping(activity):
    """Best-effort suggestion for ISC2's Category, CPE Type, and Domain fields. Returns a dict
    with any of the three keys set to None where no pattern matched — an unmapped case must be
    picked manually on the live form, never guessed silently (same posture as certificate
    parsing, §11)."""
    title = (activity["title"] or "").lower()
    local_category = (activity["category"] or "").lower()
    haystack = f"{local_category} {title}"

    category = cpe_type = None
    if any(k in haystack for k in _CONFERENCE_KEYWORDS):
        category, cpe_type = "Education", "Industry Conference"
    elif any(k in haystack for k in _WEBINAR_KEYWORDS):
        category, cpe_type = "Education", "Online webinars, podcasts and other online materials"

    domain = None
    for keywords, candidate in (
        (("secops", "security operations", " soc "), "Security Operations"),
        (("architecture", "engineering", "tech and strategy", "technical"), "Security Architecture and Engineering"),
        (("risk", "governance", "ciso", "summit", "executive"), "Security and Risk Management"),
        (("identity", "iam", "access management"), "Identity and Access Management (IAM)"),
        (("network", "communication"), "Communication and Network Security"),
        (("assessment", "testing", "audit"), "Security Assessment and Testing"),
        (("software", "development", "devsecops"), "Software Development Security"),
        (("asset",), "Asset Security"),
    ):
        if any(k in haystack for k in keywords):
            domain = candidate
            break

    return {"category": category, "cpe_type": cpe_type, "domain": domain}


def is_auto_reporting_provider(activity):
    return (activity["provider"] or "").strip().lower() in AUTO_REPORTING_PROVIDERS


def ready_to_submit(conn, credential_id="cissp"):
    """Activities that apply to credential_id and have no ledger row marking them already
    submitted or portal-confirmed for that credential. Same comma-list `applies` matching as
    views._fetch_applicable_activities. A ledger row recorded for a *different* credential on
    a multi-applies activity (e.g. "cissp,aigp") has no effect here."""
    return conn.execute(
        "SELECT * FROM activities WHERE ',' || applies || ',' LIKE '%,' || ? || ',%' "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM activity_portal_submissions "
        "  WHERE activity_id = activities.id AND credential_id = ? "
        "  AND status IN ('submitted', 'portal_confirmed')"
        ") ORDER BY date",
        (credential_id, credential_id),
    ).fetchall()


def record_submission(conn, activity_id, credential_id, source, confirmation_id=None, submitted_date=None,
                       isc2_category=None, isc2_cpe_type=None, isc2_domain=None):
    """Upserts the ledger row for one (activity, credential) pair. Idempotent — calling this
    twice for the same pair updates in place rather than creating a second row. `status` is
    derived from `source`: a live-portal duplicate finding ('portal-existing') means the
    activity was never actually submitted by us, so it's recorded as 'portal_confirmed'
    rather than 'submitted'. `recorded_at` is a full timestamp (not just a date) so
    `pending_reconciliation` can apply ISC2's own "updated within 60 minutes" window."""
    status = "portal_confirmed" if source == "portal-existing" else "submitted"
    submitted_date = submitted_date or date.today().isoformat()
    conn.execute(
        "INSERT INTO activity_portal_submissions "
        "(activity_id, credential_id, status, submitted_date, confirmation_id, source, "
        "isc2_category, isc2_cpe_type, isc2_domain, recorded_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(activity_id, credential_id) DO UPDATE SET "
        "status=excluded.status, submitted_date=excluded.submitted_date, "
        "confirmation_id=excluded.confirmation_id, source=excluded.source, "
        "isc2_category=excluded.isc2_category, isc2_cpe_type=excluded.isc2_cpe_type, "
        "isc2_domain=excluded.isc2_domain, recorded_at=excluded.recorded_at",
        (activity_id, credential_id, status, submitted_date, confirmation_id, source,
         isc2_category, isc2_cpe_type, isc2_domain, datetime.now().isoformat()),
    )
    conn.commit()


RECONCILE_WAIT_MINUTES = 60


def pending_reconciliation(conn, credential_id="cissp"):
    """Ledger rows this automation submitted that are old enough for ISC2's own "record will
    be updated within the next 60 minutes" window to have elapsed, and haven't been confirmed
    against a live re-check of the CPE List yet. Only 'automation'-sourced rows apply — a
    'portal-existing' row was already confirmed at record time, and 'manual' rows aren't ours
    to reconcile. See ce-cpe-tracker-design.md §7a "Confirmation semantics"."""
    cutoff = (datetime.now() - timedelta(minutes=RECONCILE_WAIT_MINUTES)).isoformat()
    return conn.execute(
        "SELECT aps.*, a.date, a.hours, a.provider, a.title FROM activity_portal_submissions aps "
        "JOIN activities a ON a.id = aps.activity_id "
        "WHERE aps.credential_id = ? AND aps.status = 'submitted' AND aps.source = 'automation' "
        "AND aps.recorded_at <= ? ORDER BY aps.recorded_at",
        (credential_id, cutoff),
    ).fetchall()


def confirm_submission(conn, activity_id, credential_id, isc2_hours=None, isc2_title=None):
    """Marks a submitted ledger row 'portal_confirmed' after a live re-check found it on ISC2's
    CPE List. If ISC2's recorded hours/title differ from the local activity — as happened for
    two auto-reported Northbridge Learning entries the first time through, see CLAUDE.local.md — pass
    isc2_hours/isc2_title to correct the local record: ISC2's accepted number is the real one
    once something's live there, not what we originally logged."""
    conn.execute(
        "UPDATE activity_portal_submissions SET status='portal_confirmed' WHERE activity_id=? AND credential_id=?",
        (activity_id, credential_id),
    )
    if isc2_hours is not None:
        conn.execute("UPDATE activities SET hours=? WHERE id=?", (isc2_hours, activity_id))
    if isc2_title is not None:
        conn.execute("UPDATE activities SET title=? WHERE id=?", (isc2_title, activity_id))
    conn.commit()


def main():
    parser = argparse.ArgumentParser(description="Track ISC2 CPE portal submission status for locally-approved activities.")
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list-ready", help="List activities not yet submitted to the given credential's portal.")
    list_p.add_argument("--credential", default="cissp")

    rec_p = sub.add_parser("record-submission", help="Record that one activity was submitted (or already present).")
    rec_p.add_argument("--activity-id", type=int, required=True)
    rec_p.add_argument("--credential", default="cissp")
    rec_p.add_argument("--source", required=True, choices=["automation", "portal-existing", "manual"])
    rec_p.add_argument("--confirmation", help="Confirmation ID/reference the portal returned, if any.")
    rec_p.add_argument("--date", help="Submission date (ISO); defaults to today.")
    rec_p.add_argument("--isc2-category", help="ISC2 form Category actually picked, e.g. 'Education'.")
    rec_p.add_argument("--isc2-cpe-type", help="ISC2 form CPE Type actually picked, e.g. 'Industry Conference'.")
    rec_p.add_argument("--isc2-domain", help="ISC2 domain(s) actually checked, comma-separated if more than one.")

    pending_p = sub.add_parser("list-pending-reconcile",
                                help="List automation submissions old enough (60+ min) to re-check against the live CPE List.")
    pending_p.add_argument("--credential", default="cissp")

    confirm_p = sub.add_parser("confirm-submission",
                                help="Mark a submitted activity portal_confirmed after finding it on the live CPE List.")
    confirm_p.add_argument("--activity-id", type=int, required=True)
    confirm_p.add_argument("--credential", default="cissp")
    confirm_p.add_argument("--isc2-hours", type=float, help="Correct local hours to match what ISC2 actually recorded, if different.")
    confirm_p.add_argument("--isc2-title", help="Correct local title to match what ISC2 actually recorded, if different.")

    args = parser.parse_args()
    conn = get_db()

    if args.command == "list-ready":
        rows = ready_to_submit(conn, args.credential)
        if not rows:
            print(f"Nothing ready to submit to {args.credential}.")
        for r in rows:
            flag = " [AUTO-REPORTING PROVIDER — check ISC2's live CPE List first; it may already be there under a different title/hours]" \
                if is_auto_reporting_provider(r) else ""
            print(f"[{r['id']}] {r['date']}  {r['hours']:g}h  {r['provider'] or '-'} — {r['title']}  (category: {r['category'] or '-'}){flag}")
            mapping = suggest_isc2_mapping(r)
            if mapping["category"] or mapping["domain"]:
                print(f"      suggested ISC2 mapping (confirm before submitting): "
                      f"{mapping['category'] or '?'} > {mapping['cpe_type'] or '?'} | domain: {mapping['domain'] or '?'}")

    elif args.command == "record-submission":
        record_submission(conn, args.activity_id, args.credential, args.source, args.confirmation, args.date,
                           args.isc2_category, args.isc2_cpe_type, args.isc2_domain)
        print(f"Recorded activity {args.activity_id} as {args.source} for {args.credential}.")

    elif args.command == "list-pending-reconcile":
        rows = pending_reconciliation(conn, args.credential)
        if not rows:
            print(f"Nothing pending reconciliation for {args.credential}.")
        for r in rows:
            print(f"[{r['activity_id']}] {r['date']}  {r['hours']:g}h  {r['provider'] or '-'} — {r['title']}  "
                  f"(submitted {r['submitted_date']}, recorded {r['recorded_at']})")

    elif args.command == "confirm-submission":
        confirm_submission(conn, args.activity_id, args.credential, args.isc2_hours, args.isc2_title)
        print(f"Confirmed activity {args.activity_id} for {args.credential}.")

    conn.close()


if __name__ == "__main__":
    main()
