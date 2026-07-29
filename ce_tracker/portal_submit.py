"""Tracks which locally-approved CE activities have been submitted to an issuer's CPE portal,
per (activity, credential) — see ce-cpe-tracker-design.md §7a/§7b. ISC2 (CISSP) and PMI (PMP)
for now; the ledger and query functions are issuer-generic, only the mapping heuristics below
are per-issuer.

This module never drives a browser and never submits anything itself. `record_submission` is
the single write path for the ledger, and it must only be called after a human has explicitly
approved that specific record for submission — there is no batch/loop command here by design,
matching the hard "explicit go per record, no batch auto-submit" rule in §7. The actual
portal interaction (navigating cpe.isc2.org or ccrs.pmi.org, filling the form, and — before
that — re-checking the live CPE List/transcript for a duplicate that beat this ledger to it)
is done by a live Claude-in-Chrome session, which calls `list-ready` to find candidates and
`record-submission` to log the result.

Category/domain/auto-reporting-provider notes below come from the first live run against the
real ISC2 form (2026-05-11) — see CLAUDE.local.md for the full account. PMI mapping is built
from the CCR Handbook (`Credential Providers/PMI/ccr-certification-requirements-handbook.pdf`)
but hasn't had a live CCRS run yet — treat its suggestions as less trustworthy than ISC2's
until one happens.
"""

import argparse
from datetime import date, datetime, timedelta

from .db import get_db

# Providers observed auto-reporting CPEs/PDUs to an issuer on the member's behalf, keyed by
# credential_id. An activity from one of these is NOT automatically a duplicate, but it's a
# strong enough signal that the live portal re-check (mandatory either way, per §7a/§7b)
# deserves extra scrutiny before submitting:
# - ISC2/CISSP: Northbridge Learning reports via ISC2's own provider partnerships (the "On Your Behalf" column
#   on the live CPE List) — confirmed twice turning out to already have the entry, once under
#   a different title and hours.
# - PMI/PMP: ProjectManagement.com reports automatically if that account is linked to the
#   PMI.org account holding the certification (per the CCR Handbook) — an account-link
#   mechanism rather than a provider partnership, but the same "check before submitting" signal.
AUTO_REPORTING_PROVIDERS = {
    "cissp": {"northbridge learning"},
    "pmp": {"projectmanagement.com"},
}

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


# Suggests PMI's Education/Giving Back "Ways to Earn PDUs" category and a Talent Triangle leg,
# from the CCR Handbook's real vocabulary (Credential Providers/PMI/ccr-certification-
# requirements-handbook.pdf). Not yet validated against a live CCRS run — treat as a rougher
# starting point than suggest_isc2_mapping until one happens.
_PMI_COURSE_KEYWORDS = ("course", "training", "bootcamp", "class")
_PMI_MEETING_KEYWORDS = ("summit", "conference", " con", "con -", "symposium", "forum", "meeting", "chapter")
_PMI_DIGITAL_KEYWORDS = ("webinar", "podcast", "on-demand", "online", "briefing")
_PMI_READING_KEYWORDS = ("book", "article", "whitepaper", "white paper", "blog", "reading")
_PMI_INFORMAL_KEYWORDS = ("mentoring", "mentor", "panel", "lunch and learn", "lunch-and-learn")
_PMI_VOLUNTEER_KEYWORDS = ("volunteer",)
_PMI_PRESENTATION_KEYWORDS = ("presentation", "speaking", "speaker", "present")


def suggest_pmi_mapping(activity):
    """Best-effort suggestion for PMI's Education/Giving Back category, CCRS activity type, and
    Talent Triangle leg (Ways of Working / Power Skills / Business Acumen). Returns a dict with
    any of the three keys set to None where no pattern matched — an unmapped case must be
    picked manually on the live CCRS form, never guessed silently (same posture as
    suggest_isc2_mapping and certificate parsing, §11)."""
    title = (activity["title"] or "").lower()
    local_category = (activity["category"] or "").lower()
    haystack = f"{local_category} {title}"

    category = cpe_type = None
    if any(k in haystack for k in _PMI_COURSE_KEYWORDS):
        category, cpe_type = "Education", "Courses or Training"
    elif any(k in haystack for k in _PMI_MEETING_KEYWORDS):
        category, cpe_type = "Education", "Organization Meetings"
    elif any(k in haystack for k in _PMI_DIGITAL_KEYWORDS):
        category, cpe_type = "Education", "Online or Digital Media"
    elif any(k in haystack for k in _PMI_READING_KEYWORDS):
        category, cpe_type = "Education", "Reading Materials"
    elif any(k in haystack for k in _PMI_INFORMAL_KEYWORDS):
        category, cpe_type = "Education", "Informal Learning"
    elif any(k in haystack for k in _PMI_VOLUNTEER_KEYWORDS):
        category, cpe_type = "Giving Back", "Volunteer"
    elif any(k in haystack for k in _PMI_PRESENTATION_KEYWORDS):
        category, cpe_type = "Giving Back", "Give a Presentation"

    domain = None
    for keywords, candidate in (
        (("leadership", "communication", "stakeholder", "collaborat", "empathy"), "Power Skills"),
        (("strategy", "business", "portfolio", "roadmap", "governance", "executive"), "Business Acumen"),
        (("agile", "scrum", "framework", "methodology", "technique", "waterfall"), "Ways of Working"),
    ):
        if any(k in haystack for k in keywords):
            domain = candidate
            break

    return {"category": category, "cpe_type": cpe_type, "domain": domain}


MAPPING_SUGGESTERS = {"cissp": suggest_isc2_mapping, "pmp": suggest_pmi_mapping}


def is_auto_reporting_provider(activity, credential_id):
    provider = (activity["provider"] or "").strip().lower()
    return provider in AUTO_REPORTING_PROVIDERS.get(credential_id, set())


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
                       category=None, cpe_type=None, domain=None):
    """Upserts the ledger row for one (activity, credential) pair. Idempotent — calling this
    twice for the same pair updates in place rather than creating a second row. `status` is
    derived from `source`: a live-portal duplicate finding ('portal-existing') means the
    activity was never actually submitted by us, so it's recorded as 'portal_confirmed'
    rather than 'submitted'. `recorded_at` is a full timestamp (not just a date) so
    `pending_reconciliation` can apply an issuer's own "updated within N minutes" window."""
    status = "portal_confirmed" if source == "portal-existing" else "submitted"
    submitted_date = submitted_date or date.today().isoformat()
    conn.execute(
        "INSERT INTO activity_portal_submissions "
        "(activity_id, credential_id, status, submitted_date, confirmation_id, source, "
        "submitted_category, submitted_cpe_type, submitted_domain, recorded_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(activity_id, credential_id) DO UPDATE SET "
        "status=excluded.status, submitted_date=excluded.submitted_date, "
        "confirmation_id=excluded.confirmation_id, source=excluded.source, "
        "submitted_category=excluded.submitted_category, submitted_cpe_type=excluded.submitted_cpe_type, "
        "submitted_domain=excluded.submitted_domain, recorded_at=excluded.recorded_at",
        (activity_id, credential_id, status, submitted_date, confirmation_id, source,
         category, cpe_type, domain, datetime.now().isoformat()),
    )
    conn.commit()


RECONCILE_WAIT_MINUTES = 60


def pending_reconciliation(conn, credential_id="cissp"):
    """Ledger rows this automation submitted that are old enough for the issuer's own "record
    will update within N minutes" window to have elapsed, and haven't been confirmed against a
    live re-check of the portal yet. Only 'automation'-sourced rows apply — a 'portal-existing'
    row was already confirmed at record time, and 'manual' rows aren't ours to reconcile. See
    ce-cpe-tracker-design.md §7a "Confirmation semantics"."""
    cutoff = (datetime.now() - timedelta(minutes=RECONCILE_WAIT_MINUTES)).isoformat()
    return conn.execute(
        "SELECT aps.*, a.date, a.hours, a.provider, a.title FROM activity_portal_submissions aps "
        "JOIN activities a ON a.id = aps.activity_id "
        "WHERE aps.credential_id = ? AND aps.status = 'submitted' AND aps.source = 'automation' "
        "AND aps.recorded_at <= ? ORDER BY aps.recorded_at",
        (credential_id, cutoff),
    ).fetchall()


def confirm_submission(conn, activity_id, credential_id, portal_hours=None, portal_title=None):
    """Marks a submitted ledger row 'portal_confirmed' after a live re-check found it on the
    issuer's portal. If the portal's recorded hours/title differ from the local activity — as
    happened for two auto-reported Northbridge Learning entries the first time through, see CLAUDE.local.md —
    pass portal_hours/portal_title to correct the local record: the portal's accepted number is
    the real one once something's live there, not what we originally logged."""
    conn.execute(
        "UPDATE activity_portal_submissions SET status='portal_confirmed' WHERE activity_id=? AND credential_id=?",
        (activity_id, credential_id),
    )
    if portal_hours is not None:
        conn.execute("UPDATE activities SET hours=? WHERE id=?", (portal_hours, activity_id))
    if portal_title is not None:
        conn.execute("UPDATE activities SET title=? WHERE id=?", (portal_title, activity_id))
    conn.commit()


def main():
    parser = argparse.ArgumentParser(description="Track issuer CPE/PDU portal submission status for locally-approved activities.")
    sub = parser.add_subparsers(dest="command", required=True)

    credential_help = "Credential id to check against, e.g. 'cissp' or 'pmp' (default: %(default)s)."
    activity_id_help = "The activities.id of the locally-logged activity, shown in list-ready's output."

    list_p = sub.add_parser("list-ready", help="List activities not yet submitted to the given credential's portal.")
    list_p.add_argument("--credential", default="cissp", help=credential_help)

    rec_p = sub.add_parser("record-submission", help="Record that one activity was submitted (or already present).")
    rec_p.add_argument("--activity-id", type=int, required=True, help=activity_id_help)
    rec_p.add_argument("--credential", default="cissp", help=credential_help)
    rec_p.add_argument("--source", required=True, choices=["automation", "portal-existing", "manual"],
                        help="'automation' = submitted live via a supervised browser session; "
                             "'portal-existing' = found already on the portal (e.g. auto-reported by the "
                             "provider), not submitted by us; 'manual' = submitted some other way.")
    rec_p.add_argument("--confirmation", help="Confirmation ID/reference the portal returned, if any.")
    rec_p.add_argument("--date", help="Submission date (ISO); defaults to today.")
    rec_p.add_argument("--category", help="Portal form Category actually picked, e.g. 'Education'.")
    rec_p.add_argument("--cpe-type", help="Portal form CPE/PDU Type actually picked, e.g. 'Industry Conference'.")
    rec_p.add_argument("--domain", help="Portal domain/talent-triangle pick(s) actually checked, comma-separated if more than one.")

    pending_p = sub.add_parser("list-pending-reconcile",
                                help="List automation submissions old enough (60+ min) to re-check against the live portal.")
    pending_p.add_argument("--credential", default="cissp", help=credential_help)

    confirm_p = sub.add_parser("confirm-submission",
                                help="Mark a submitted activity portal_confirmed after finding it on the live portal.")
    confirm_p.add_argument("--activity-id", type=int, required=True, help=activity_id_help)
    confirm_p.add_argument("--credential", default="cissp", help=credential_help)
    confirm_p.add_argument("--portal-hours", type=float, help="Correct local hours to match what the portal actually recorded, if different.")
    confirm_p.add_argument("--portal-title", help="Correct local title to match what the portal actually recorded, if different.")

    args = parser.parse_args()
    conn = get_db()

    if args.command == "list-ready":
        rows = ready_to_submit(conn, args.credential)
        if not rows:
            print(f"Nothing ready to submit to {args.credential}.")
        suggest_mapping = MAPPING_SUGGESTERS.get(args.credential)
        for r in rows:
            flag = " [AUTO-REPORTING PROVIDER — check the live portal first; it may already be there under a different title/hours]" \
                if is_auto_reporting_provider(r, args.credential) else ""
            print(f"[{r['id']}] {r['date']}  {r['hours']:g}h  {r['provider'] or '-'} — {r['title']}  (category: {r['category'] or '-'}){flag}")
            if suggest_mapping:
                mapping = suggest_mapping(r)
                if mapping["category"] or mapping["domain"]:
                    print(f"      suggested mapping (confirm before submitting): "
                          f"{mapping['category'] or '?'} > {mapping['cpe_type'] or '?'} | domain: {mapping['domain'] or '?'}")

    elif args.command == "record-submission":
        record_submission(conn, args.activity_id, args.credential, args.source, args.confirmation, args.date,
                           args.category, args.cpe_type, args.domain)
        print(f"Recorded activity {args.activity_id} as {args.source} for {args.credential}.")

    elif args.command == "list-pending-reconcile":
        rows = pending_reconciliation(conn, args.credential)
        if not rows:
            print(f"Nothing pending reconciliation for {args.credential}.")
        for r in rows:
            print(f"[{r['activity_id']}] {r['date']}  {r['hours']:g}h  {r['provider'] or '-'} — {r['title']}  "
                  f"(submitted {r['submitted_date']}, recorded {r['recorded_at']})")

    elif args.command == "confirm-submission":
        confirm_submission(conn, args.activity_id, args.credential, args.portal_hours, args.portal_title)
        print(f"Confirmed activity {args.activity_id} for {args.credential}.")

    conn.close()


if __name__ == "__main__":
    main()
