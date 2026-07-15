"""Builds view-model dicts for templates, mirroring the mockup's buildCredentialsView/buildCategoriesView logic."""

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


def _category_view(row):
    ok = row["earned"] >= row["target"] if row["rule_type"] == "min" else row["earned"] <= row["target"]
    pct = min(100, round((row["earned"] / row["target"]) * 100)) if row["target"] else 0
    return {
        "name": row["name"],
        "earned": row["earned"],
        "target": row["target"],
        "ok": ok,
        "label": f"{row['name']}: {row['earned']:g}/{row['target']:g} {'✓' if ok else '✗'}",
        "bar_color": "#1f9d7c" if ok else "#d68f1f",
        "pct": pct,
    }


def _activity_view(row):
    return {
        "date": row["date"],
        "provider": row["provider"],
        "title": row["title"],
        "category": row["category"],
        "hours_label": f"{row['hours']:g}h",
        "applies": row["applies"].split(",") if row["applies"] else [],
        "status_color": ACT_STATUS_COLOR[row["status"]],
        "status_label": ACT_STATUS_LABEL[row["status"]],
    }


def credential_view(row, conn):
    status_color = STATUS_COLOR[row["status"]]
    pct = row["pct"]
    pct_label = "—" if pct is None else f"{pct}%"
    days_label = f"renew by ~{row['days_left']}d" if row["status"] == "reminder" else f"{row['days_left']} days left"
    earned_label = (
        "No CE hours tracked for this credential."
        if row["total"] is None
        else f"{row['earned']:g} of {row['total']:g} {row['unit']} earned this cycle"
    )
    categories = conn.execute(
        "SELECT name, earned, target, rule_type FROM categories WHERE credential_id = ?", (row["id"],)
    ).fetchall()
    activities = conn.execute(
        "SELECT * FROM activities WHERE ',' || applies || ',' LIKE '%,' || ? || ',%' ORDER BY date DESC",
        (row["id"],),
    ).fetchall()

    return {
        "id": row["id"],
        "name": row["name"],
        "issuer": row["issuer"],
        "cycle_label": row["cycle_label"],
        "days_left": row["days_left"],
        "days_label": days_label,
        "pct": pct,
        "pct_label": pct_label,
        "earned_label": earned_label,
        "annual_label": row["annual_label"],
        "note": row["note"],
        "status": row["status"],
        "status_color": status_color,
        "status_label": STATUS_LABEL[row["status"]],
        "ring_gradient": f"conic-gradient({status_color} {(pct or 0) * 3.6}deg, #eaecf0 0deg)",
        "categories": [_category_view(c) for c in categories],
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
        status_color = STATUS_COLOR[row["status"]]
        result.append(
            {
                "id": row["id"],
                "name": row["name"],
                "issuer": row["issuer"],
                "pct": row["pct"],
                "pct_label": "—" if row["pct"] is None else f"{row['pct']}%",
                "status_color": status_color,
                "bar_width": row["pct"] or 0,
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
