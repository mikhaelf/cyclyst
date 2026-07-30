"""Builds view-model dicts for templates, mirroring the mockup's buildCredentialsView/buildCategoriesView logic."""

from datetime import date

from . import portal_submit, progress
from .db import get_db

STATUS_COLOR = {
    "on-track": "#1f9d7c", "at-risk": "#d68f1f", "reminder": "#667085",
    "grace-period": "#c1440e", "expired": "#d64545",
}
STATUS_LABEL = {
    "on-track": "On track", "at-risk": "At risk", "reminder": "Reminder only",
    "grace-period": "In grace period", "expired": "Expired",
}
ACT_STATUS_COLOR = {"submitted": "#1f9d7c", "logged": "#98a2b3"}
ACT_STATUS_LABEL = {"submitted": "Submitted", "logged": "Logged, not yet filed"}

# Explains the sidebar status dots. Excludes "expired" — that status only appears on archived
# credentials, which render as plain rows without a dot, so it never shows up in the active list.
STATUS_LEGEND = [
    {"color": STATUS_COLOR["on-track"], "label": "On track — earning at a healthy pace this cycle"},
    {"color": STATUS_COLOR["at-risk"], "label": "At risk — behind pace or a deadline is coming up"},
    {"color": STATUS_COLOR["grace-period"], "label": "In grace period — past the deadline, still time to submit"},
    {"color": STATUS_COLOR["reminder"], "label": "Reminder only — no CE hours tracked for this one"},
]


def _category_view(result):
    """result is one of progress.compute_progress()'s category_results entries — earned/ok
    are already computed from summed activity hours (rolled up from any nested sub-categories),
    this just adds display formatting. `depth` indents nested rows in the Overview tab."""
    pct = min(100, round((result["earned"] / result["target"]) * 100)) if result["target"] else 0
    return {
        "name": result["name"],
        "earned": result["earned"],
        "target": result["target"],
        "ok": result["ok"],
        "depth": result.get("depth", 0),
        "label": f"{result['name']}: {result['earned']:g}/{result['target']:g} {'✓' if result['ok'] else '✗'}",
        "bar_color": "#1f9d7c" if result["ok"] else "#d68f1f",
        "pct": pct,
    }


def _activity_view(row):
    return {
        "id": row["id"],
        "date": row["date"],
        "provider": row["provider"],
        "title": row["title"],
        "category": row["category"],
        "hours_label": f"{row['hours']:g}h",
        "applies": row["applies"].split(",") if row["applies"] else [],
        "status_color": ACT_STATUS_COLOR[row["status"]],
        "status_label": ACT_STATUS_LABEL[row["status"]],
        "archived": bool(row["archived"]),
    }


def _fetch_categories(conn, credential_id):
    return conn.execute(
        "SELECT id, name, target, rule_type, parent_id, manual_adjustment FROM categories WHERE credential_id = ?",
        (credential_id,),
    ).fetchall()


def _fetch_applicable_activities(conn, credential_id):
    return conn.execute(
        "SELECT * FROM activities WHERE ',' || applies || ',' LIKE '%,' || ? || ',%' ORDER BY date DESC",
        (credential_id,),
    ).fetchall()


def _annual_label(row, annual_earned):
    minimum = row["annual_minimum"]
    minimum_type = row["annual_minimum_type"]
    if minimum_type == "mandatory":
        return f"Mandatory {minimum:g}/yr — {annual_earned:g}/{minimum:g} earned this window."
    if minimum_type == "suggested":
        return f"No mandatory floor — suggested pace {minimum:g}/yr — {annual_earned:g} earned this window."
    return None


def _mask_member_id(value):
    """Member ID is a real issuer-account identifier (see README/DISCLAIMER) — mask all but
    the last 4 characters for the default on-screen display; the dashboard reveals the full
    value on click, and exports/the edit form still use the unmasked value from the row."""
    if not value:
        return value
    if len(value) <= 4:
        return "•" * len(value)
    return "•" * (len(value) - 4) + value[-4:]


def _cycle_label(row):
    """cycle_label was a hand-typed display column from the original static seed data — once a
    credential's real cycle_start_date/cycle_end_date are known (cpe_hours type), derive the
    label from them so editing the dates through the Add/Edit Credential form can't leave a
    stale label behind. Exam-type credentials keep their curated stored label (e.g. "Re-exam
    required") since there's no natural start/end pair to derive from."""
    if row["renewal_type"] == "cpe_hours" and row["cycle_start_date"] and row["cycle_end_date"]:
        start = date.fromisoformat(row["cycle_start_date"])
        end = date.fromisoformat(row["cycle_end_date"])
        return f"{start:%b %Y} – {end:%b %Y}"
    return row["cycle_label"]


def _earned_label(row, prog):
    if row["total"] is None:
        return "No CE hours tracked for this credential."
    earned, total, unit = prog["earned"], row["total"], row["unit"]
    adjustment = prog.get("earned_adjustment") or 0
    carryover = prog.get("carryover_earned") or 0
    logged = prog["earned_logged"]
    if adjustment and carryover:
        return (
            f"{earned:g} of {total:g} {unit} earned this cycle "
            f"({logged:g} logged here + {adjustment:g} confirmed on {row['issuer']}'s platform "
            f"+ {carryover:g} carried over from last cycle)"
        )
    if carryover:
        # Transparency: distinguish hours logged in this cycle's own window from hours the
        # issuer let carry forward from the prior cycle's final window (carryover_max_hours).
        return (
            f"{earned:g} of {total:g} {unit} earned this cycle "
            f"({logged:g} logged here + {carryover:g} carried over from last cycle)"
        )
    if adjustment:
        # Transparency: distinguish hours backed by a local activity from hours the issuer's
        # portal already confirms but this tracker never itemized (see manual_adjustment_hours).
        return (
            f"{earned:g} of {total:g} {unit} earned this cycle "
            f"({logged:g} logged here + {adjustment:g} confirmed on {row['issuer']}'s platform)"
        )
    return f"{earned:g} of {total:g} {unit} earned this cycle"


def _days_label(status, days_left, grace_days_left=None):
    if status == "grace-period":
        return f"{grace_days_left} days left in grace period to submit"
    if days_left is None:
        return ""
    if status == "reminder":
        return f"renew by ~{days_left}d"
    if days_left < 0:
        return f"{-days_left} days overdue"
    return f"{days_left} days left"


def _status_by_id(conn):
    """Every non-archived credential's own intrinsic (pre-cascade) status, keyed by id —
    lets progress.apply_dependency_cascade check a credential's prerequisites without
    those prerequisites needing to be independently re-derived by the caller."""
    rows = conn.execute("SELECT * FROM credentials WHERE archived = 0").fetchall()
    statuses = {}
    for row in rows:
        categories = _fetch_categories(conn, row["id"])
        activities = _fetch_applicable_activities(conn, row["id"])
        statuses[row["id"]] = progress.compute_progress(row, activities, categories)["status"]
    return statuses


def credential_view(row, conn, show_archived=False):
    categories = _fetch_categories(conn, row["id"])
    # Unfiltered -- progress totals, category rollups, and the submission queue must keep
    # counting archived activities exactly like non-archived ones (see retention.py). Only the
    # displayed "activities" list below filters on show_archived.
    activities = _fetch_applicable_activities(conn, row["id"])
    prog = progress.compute_progress(row, activities, categories)

    status, cascade_note = progress.apply_dependency_cascade(row, prog["status"], _status_by_id(conn))
    status_color = STATUS_COLOR[status]
    pct = prog["pct"]
    pct_label = "—" if pct is None else f"{pct}%"
    earned_label = _earned_label(row, prog)
    annual_label = _annual_label(row, prog["annual_earned"]) if prog["annual_earned"] is not None else None

    return {
        "id": row["id"],
        "name": row["name"],
        "issuer": row["issuer"],
        "member_id": row["member_id"],
        "member_id_masked": _mask_member_id(row["member_id"]),
        "cycle_label": _cycle_label(row),
        "days_left": prog["days_left"],
        "days_label": _days_label(status, prog["days_left"], prog["grace_days_left"]),
        "pct": pct,
        "pct_label": pct_label,
        "earned_label": earned_label,
        "annual_label": annual_label,
        "note": row["note"],
        "cascade_note": cascade_note,
        "status": status,
        "status_color": status_color,
        "status_label": STATUS_LABEL[status],
        "ring_gradient": f"conic-gradient({status_color} {(pct or 0) * 3.6}deg, #eaecf0 0deg)",
        "categories": [_category_view(c) for c in prog["category_results"]],
        "activities": [
            _activity_view(a) for a in activities if show_archived or not a["archived"]
        ],
        "badge_platform": row["badge_platform"],
        "badge_visibility": row["badge_visibility"],
        "badge_url": row["badge_url"],
        "badge_is_public": row["badge_visibility"] == "public",
        "badge_label": (
            f"{row['badge_platform']} · Public badge ↗"
            if row["badge_visibility"] == "public"
            else f"{row['badge_platform']} · Private"
        ),
        # Issuer-portal submission automation (design doc §7a/§7b) — CISSP and PMP for now,
        # see portal_submit.MAPPING_SUGGESTERS for which credentials are supported. Read-only:
        # this dashboard never submits anything itself, it just surfaces what a live
        # Claude-in-Chrome session (via ce_tracker/portal_submit.py) would find with `list-ready`.
        "submission_queue": (
            [_submission_queue_row(a) for a in portal_submit.ready_to_submit(conn, row["id"])]
            if row["id"] in portal_submit.MAPPING_SUGGESTERS
            else None
        ),
    }


def _submission_queue_row(row):
    return {"date": row["date"], "title": row["title"], "hours_label": f"{row['hours']:g}h"}


def sidebar_credentials(conn):
    rows = conn.execute(
        "SELECT * FROM credentials WHERE archived = 0 ORDER BY rowid"
    ).fetchall()
    status_by_id = _status_by_id(conn)
    result = []
    for row in rows:
        categories = _fetch_categories(conn, row["id"])
        activities = _fetch_applicable_activities(conn, row["id"])
        prog = progress.compute_progress(row, activities, categories)
        status, _ = progress.apply_dependency_cascade(row, prog["status"], status_by_id)
        pct = prog["pct"]
        result.append(
            {
                "id": row["id"],
                "name": row["name"],
                "issuer": row["issuer"],
                "pct": pct,
                "pct_label": "—" if pct is None else f"{pct}%",
                "status_color": STATUS_COLOR[status],
                "bar_width": pct or 0,
            }
        )
    return result


def archived_credentials(conn):
    rows = conn.execute(
        "SELECT id, name, issuer, retired_label FROM credentials WHERE archived = 1 ORDER BY rowid"
    ).fetchall()
    return [dict(r) for r in rows]


def _duplicate_label(conn, duplicate_of):
    """duplicate_of is 'activity:<id>' or 'cal:<pending_activities.id>' — resolve it to a short
    human-readable description for the "possible duplicate of X" callout."""
    if not duplicate_of or ":" not in duplicate_of:
        return None
    kind, ref_id = duplicate_of.split(":", 1)
    if kind == "activity":
        row = conn.execute("SELECT title, date, provider FROM activities WHERE id = ?", (ref_id,)).fetchone()
    else:
        row = conn.execute(
            "SELECT title, date, provider FROM pending_activities WHERE id = ?", (duplicate_of,)
        ).fetchone()
    if not row:
        return None
    bits = [row["title"], row["date"]]
    if row["provider"]:
        bits.append(row["provider"])
    return " · ".join(bits)


def pending_view(conn):
    rows = conn.execute(
        "SELECT * FROM pending_activities WHERE status = 'pending' ORDER BY date DESC"
    ).fetchall()
    result = []
    for row in rows:
        duplicate_confidence = row["duplicate_confidence"]
        result.append({
            "id": row["id"],
            "date": row["date"],
            "provider": row["provider"],
            "title": row["title"],
            "hours_guess": row["hours_guess"],
            "category": row["category"],
            "applies": row["applies"].split(",") if row["applies"] else [],
            "source": row["source"],
            "breakdown": row["breakdown"] if "breakdown" in row.keys() else None,
            "flagged": bool(row["flagged"]),
            "duplicate_pct": round(duplicate_confidence * 100) if duplicate_confidence else None,
            "duplicate_label": _duplicate_label(conn, row["duplicate_of"]) if duplicate_confidence else None,
        })
    return result


def get_credential_or_404(conn, credential_id, show_archived=False):
    row = conn.execute("SELECT * FROM credentials WHERE id = ? AND archived = 0", (credential_id,)).fetchone()
    return credential_view(row, conn, show_archived=show_archived) if row else None
