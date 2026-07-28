"""Tracks which locally-approved CE activities have been submitted to an issuer's CPE portal,
per (activity, credential) — see ce-cpe-tracker-design.md §7a. CISSP/ISC2 only for v1.

This module never drives a browser and never submits anything itself. `record_submission` is
the single write path for the ledger, and it must only be called after a human has explicitly
approved that specific record for submission — there is no batch/loop command here by design,
matching the hard "explicit go per record, no batch auto-submit" rule in §7. The actual
portal interaction (navigating cpe.isc2.org, filling the form, and — before that — re-checking
the live CPE List for a duplicate that beat this ledger to it) is done by a live Claude-in-Chrome
session, which calls `list-ready` to find candidates and `record-submission` to log the result.
"""

import argparse
from datetime import date

from .db import get_db


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


def record_submission(conn, activity_id, credential_id, source, confirmation_id=None, submitted_date=None):
    """Upserts the ledger row for one (activity, credential) pair. Idempotent — calling this
    twice for the same pair updates in place rather than creating a second row. `status` is
    derived from `source`: a live-portal duplicate finding ('portal-existing') means the
    activity was never actually submitted by us, so it's recorded as 'portal_confirmed'
    rather than 'submitted'."""
    status = "portal_confirmed" if source == "portal-existing" else "submitted"
    submitted_date = submitted_date or date.today().isoformat()
    conn.execute(
        "INSERT INTO activity_portal_submissions "
        "(activity_id, credential_id, status, submitted_date, confirmation_id, source, recorded_at) "
        "VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(activity_id, credential_id) DO UPDATE SET "
        "status=excluded.status, submitted_date=excluded.submitted_date, "
        "confirmation_id=excluded.confirmation_id, source=excluded.source, recorded_at=excluded.recorded_at",
        (activity_id, credential_id, status, submitted_date, confirmation_id, source, date.today().isoformat()),
    )
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

    args = parser.parse_args()
    conn = get_db()

    if args.command == "list-ready":
        rows = ready_to_submit(conn, args.credential)
        if not rows:
            print(f"Nothing ready to submit to {args.credential}.")
        for r in rows:
            print(f"[{r['id']}] {r['date']}  {r['hours']:g}h  {r['provider'] or '-'} — {r['title']}  (category: {r['category'] or '-'})")

    elif args.command == "record-submission":
        record_submission(conn, args.activity_id, args.credential, args.source, args.confirmation, args.date)
        print(f"Recorded activity {args.activity_id} as {args.source} for {args.credential}.")

    conn.close()


if __name__ == "__main__":
    main()
