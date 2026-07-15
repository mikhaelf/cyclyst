"""Builds view-model dicts for templates, mirroring the mockup's buildCredentialsView/buildCategoriesView logic."""

from . import progress
from .db import get_db

STATUS_COLOR = {"on-track": "#1f9d7c", "at-risk": "#d68f1f", "reminder": "#667085", "expired": "#d64545"}
STATUS_LABEL = {"on-track": "On track", "at-risk": "At risk", "reminder": "Reminder only", "expired": "Expired"}
ACT_STATUS_COLOR = {"submitted": "#1f9d7c", "logged": "#98a2b3"}
ACT_STATUS_LABEL = {"submitted": "Submitted", "logged": "Logged, not yet filed"}

# Explains the sidebar status dots. Excludes "expired" — that status only appears on archived
# credentials, which render as plain rows without a dot, so it never shows up in the active list.
STATUS_LEGEND = [
    {"color": STATUS_COLOR["on-track"], "label": "On track — earning at a healthy pace this cycle"},
    {"color": STATUS_COLOR["at-risk"], "label": "At risk — behind pace or a deadline is coming up"},
    {"color": STATUS_COLOR["reminder"], "label": "Reminder only — no CE hours tracked for this one"},
]


def _category_view(result):
    """result is one of progress.compute_progress()'s category_results entries — earned/ok
    are already computed from summed activity hours, this just adds display formatting."""
    pct = min(100, round((result["earned"] / result["target"]) * 100)) if result["target"] else 0
    return {
        "name": result["name"],
        "earned": result["earned"],
        "target": result["target"],
        "ok": result["ok"],
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
    }


def _fetch_categories(conn, credential_id):
    return conn.execute(
        "SELECT name, target, rule_type FROM categories WHERE credential_id = ?", (credential_id,)
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


def _days_label(status, days_left):
    if days_left is None:
        return ""
    if status == "reminder":
        return f"renew by ~{days_left}d"
    if days_left < 0:
        return f"{-days_left} days overdue"
    return f"{days_left} days left"


def credential_view(row, conn):
    categories = _fetch_categories(conn, row["id"])
    activities = _fetch_applicable_activities(conn, row["id"])
    prog = progress.compute_progress(row, activities, categories)

    status = prog["status"]
    status_color = STATUS_COLOR[status]
    pct = prog["pct"]
    pct_label = "—" if pct is None else f"{pct}%"
    earned_label = (
        "No CE hours tracked for this credential."
        if row["total"] is None
        else f"{prog['earned']:g} of {row['total']:g} {row['unit']} earned this cycle"
    )
    annual_label = _annual_label(row, prog["annual_earned"]) if prog["annual_earned"] is not None else None

    return {
        "id": row["id"],
        "name": row["name"],
        "issuer": row["issuer"],
        "member_id": row["member_id"],
        "cycle_label": row["cycle_label"],
        "days_left": prog["days_left"],
        "days_label": _days_label(status, prog["days_left"]),
        "pct": pct,
        "pct_label": pct_label,
        "earned_label": earned_label,
        "annual_label": annual_label,
        "note": row["note"],
        "status": status,
        "status_color": status_color,
        "status_label": STATUS_LABEL[status],
        "ring_gradient": f"conic-gradient({status_color} {(pct or 0) * 3.6}deg, #eaecf0 0deg)",
        "categories": [_category_view(c) for c in prog["category_results"]],
        "activities": [_activity_view(a) for a in activities],
        "badge_platform": row["badge_platform"],
        "badge_visibility": row["badge_visibility"],
        "badge_url": row["badge_url"],
        "badge_is_public": row["badge_visibility"] == "public",
        "badge_label": (
            f"{row['badge_platform']} · Public badge ↗"
            if row["badge_visibility"] == "public"
            else f"{row['badge_platform']} · Private"
        ),
    }


def sidebar_credentials(conn):
    rows = conn.execute(
        "SELECT * FROM credentials WHERE archived = 0 ORDER BY rowid"
    ).fetchall()
    result = []
    for row in rows:
        categories = _fetch_categories(conn, row["id"])
        activities = _fetch_applicable_activities(conn, row["id"])
        prog = progress.compute_progress(row, activities, categories)
        pct = prog["pct"]
        result.append(
            {
                "id": row["id"],
                "name": row["name"],
                "issuer": row["issuer"],
                "pct": pct,
                "pct_label": "—" if pct is None else f"{pct}%",
                "status_color": STATUS_COLOR[prog["status"]],
                "bar_width": pct or 0,
            }
        )
    return result


def archived_credentials(conn):
    rows = conn.execute(
        "SELECT id, name, issuer, retired_label FROM credentials WHERE archived = 1 ORDER BY rowid"
    ).fetchall()
    return [dict(r) for r in rows]


def pending_view(conn):
    rows = conn.execute(
        "SELECT * FROM pending_activities WHERE status = 'pending' ORDER BY date DESC"
    ).fetchall()
    return [
        {
            "id": row["id"],
            "date": row["date"],
            "provider": row["provider"],
            "title": row["title"],
            "hours_guess": row["hours_guess"],
            "source": row["source"],
            "flagged": bool(row["flagged"]),
        }
        for row in rows
    ]


def get_credential_or_404(conn, credential_id):
    row = conn.execute("SELECT * FROM credentials WHERE id = ? AND archived = 0", (credential_id,)).fetchone()
    return credential_view(row, conn) if row else None
