from datetime import date

from flask import Blueprint, render_template, request

from . import views
from .db import get_db

bp = Blueprint("main", __name__)


def _body_context(selected_id, tab):
    conn = get_db()
    sidebar = views.sidebar_credentials(conn)
    if not selected_id and sidebar:
        selected_id = sidebar[0]["id"]
    context = {
        "sidebar": sidebar,
        "archived": views.archived_credentials(conn),
        "credential": views.get_credential_or_404(conn, selected_id) if selected_id else None,
        "pending": views.pending_view(conn),
        "tab": tab,
        "all_credentials": [
            dict(r) for r in conn.execute(
                "SELECT id, name FROM credentials WHERE archived = 0 ORDER BY rowid"
            ).fetchall()
        ],
        "show_add_form": False,
        "form_values": {},
        "form_errors": {},
    }
    conn.close()
    return context


def _parse_activity_form(form, credential_ids):
    """Validate the manual-add-activity form. Returns (values_for_redisplay, errors, hours_as_float_or_None)."""
    errors = {}

    title = (form.get("title") or "").strip()
    if not title:
        errors["title"] = "Title is required."

    date_value = (form.get("date") or "").strip()
    if not date_value:
        errors["date"] = "Date is required."

    hours_raw = (form.get("hours") or "").strip()
    hours = None
    try:
        hours = float(hours_raw)
        if hours <= 0:
            errors["hours"] = "Hours must be greater than 0."
    except ValueError:
        errors["hours"] = "Enter a number for hours."

    applies = [cid for cid in form.getlist("applies") if cid in credential_ids]
    if not applies:
        errors["applies"] = "Select at least one credential this activity applies to."

    values = {
        "date": date_value or date.today().isoformat(),
        "provider": (form.get("provider") or "").strip(),
        "title": title,
        "hours": hours_raw,
        "category": (form.get("category") or "").strip(),
        "applies": applies,
    }
    return values, errors, hours


@bp.route("/")
def index():
    selected_id = request.args.get("selected")
    tab = request.args.get("tab", "overview")
    return render_template("index.html", **_body_context(selected_id, tab))


@bp.route("/view")
def view_fragment():
    selected_id = request.args.get("selected")
    tab = request.args.get("tab", "overview")
    return render_template("partials/body.html", **_body_context(selected_id, tab))


@bp.route("/activities/new")
def new_activity_form():
    selected_id = request.args.get("selected")
    context = _body_context(selected_id, "activity")
    context["show_add_form"] = True
    context["form_values"] = {
        "date": date.today().isoformat(),
        "applies": [context["credential"]["id"]] if context["credential"] else [],
    }
    return render_template("partials/body.html", **context)


@bp.route("/activities", methods=["POST"])
def create_activity():
    selected_id = request.args.get("selected")
    context = _body_context(selected_id, "activity")
    credential_ids = {c["id"] for c in context["all_credentials"]}

    values, errors, hours = _parse_activity_form(request.form, credential_ids)

    if errors:
        context["show_add_form"] = True
        context["form_values"] = values
        context["form_errors"] = errors
        return render_template("partials/body.html", **context)

    conn = get_db()
    conn.execute(
        "INSERT INTO activities (date, provider, title, hours, category, applies, status) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            values["date"],
            values["provider"] or None,
            values["title"],
            hours,
            values["category"] or None,
            ",".join(values["applies"]),
            "logged",
        ),
    )
    conn.commit()
    conn.close()

    # rebuild context so the new row shows up in the (freshly queried) activity list
    return render_template("partials/body.html", **_body_context(selected_id, "activity"))


@bp.route("/pending/<pending_id>/approve", methods=["POST"])
def approve_pending(pending_id):
    selected_id = request.args.get("selected")
    tab = request.args.get("tab", "pending")

    conn = get_db()
    item = conn.execute(
        "SELECT * FROM pending_activities WHERE id = ? AND status = 'pending'", (pending_id,)
    ).fetchone()
    if item:
        conn.execute(
            "INSERT INTO activities (date, provider, title, hours, category, applies, status) "
            "VALUES (?,?,?,?,?,?,?)",
            (item["date"], item["provider"], item["title"], item["hours_guess"] or 0, None, item["applies"], "logged"),
        )
        conn.execute("UPDATE pending_activities SET status = 'approved' WHERE id = ?", (pending_id,))
        conn.commit()
    conn.close()

    return render_template("partials/body.html", **_body_context(selected_id, tab))


@bp.route("/pending/<pending_id>/dismiss", methods=["POST"])
def dismiss_pending(pending_id):
    selected_id = request.args.get("selected")
    tab = request.args.get("tab", "pending")

    conn = get_db()
    conn.execute(
        "UPDATE pending_activities SET status = 'dismissed' WHERE id = ? AND status = 'pending'", (pending_id,)
    )
    conn.commit()
    conn.close()

    return render_template("partials/body.html", **_body_context(selected_id, tab))
