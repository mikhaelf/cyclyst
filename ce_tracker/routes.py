from datetime import date

from flask import Blueprint, render_template, request

from . import views
from .db import get_db
from .docs_content import DOCS_SECTIONS

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
        "docs_view": False,
        "docs_sections": [],
        "show_badge_form": False,
        "badge_form_values": {},
        "badge_form_errors": {},
        "status_legend": views.STATUS_LEGEND,
        "show_archive_form": False,
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


def _parse_badge_form(form):
    """Validate the optional public-credential-link form. Returns (values_for_redisplay, errors)."""
    errors = {}

    platform = (form.get("badge_platform") or "").strip()
    url = (form.get("badge_url") or "").strip()

    if url and not (url.startswith("http://") or url.startswith("https://")):
        errors["badge_url"] = "Enter a full URL starting with http:// or https://."
    if url and not platform:
        errors["badge_platform"] = "Add a platform name (e.g. Credly) so the link can be labeled."

    return {"badge_platform": platform, "badge_url": url}, errors


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


@bp.route("/docs")
def docs_fragment():
    selected_id = request.args.get("selected")
    tab = request.args.get("tab", "overview")
    context = _body_context(selected_id, tab)
    context["docs_view"] = True
    context["docs_sections"] = DOCS_SECTIONS
    return render_template("partials/body.html", **context)


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


@bp.route("/credentials/<credential_id>/badge/edit")
def edit_badge_form(credential_id):
    tab = request.args.get("tab", "overview")
    context = _body_context(credential_id, tab)
    if not context["credential"]:
        return render_template("partials/body.html", **context)

    context["show_badge_form"] = True
    context["badge_form_values"] = {
        "badge_platform": context["credential"]["badge_platform"] or "",
        "badge_url": context["credential"]["badge_url"] or "",
    }
    return render_template("partials/body.html", **context)


@bp.route("/credentials/<credential_id>/badge", methods=["POST"])
def update_badge(credential_id):
    tab = request.args.get("tab", "overview")
    values, errors = _parse_badge_form(request.form)

    if errors:
        context = _body_context(credential_id, tab)
        context["show_badge_form"] = True
        context["badge_form_values"] = values
        context["badge_form_errors"] = errors
        return render_template("partials/body.html", **context)

    platform = values["badge_platform"] or None
    url = values["badge_url"] or None
    visibility = "public" if url else ("private" if platform else None)

    conn = get_db()
    conn.execute(
        "UPDATE credentials SET badge_platform = ?, badge_url = ?, badge_visibility = ? WHERE id = ?",
        (platform, url, visibility, credential_id),
    )
    conn.commit()
    conn.close()

    return render_template("partials/body.html", **_body_context(credential_id, tab))


@bp.route("/credentials/<credential_id>/archive/edit")
def archive_form(credential_id):
    tab = request.args.get("tab", "overview")
    context = _body_context(credential_id, tab)
    if not context["credential"]:
        return render_template("partials/body.html", **context)

    context["show_archive_form"] = True
    return render_template("partials/body.html", **context)


@bp.route("/credentials/<credential_id>/archive", methods=["POST"])
def archive_credential(credential_id):
    retired_label = (request.form.get("retired_label") or "").strip()
    if not retired_label:
        retired_label = f"Archived {date.today().isoformat()}"

    conn = get_db()
    conn.execute(
        "UPDATE credentials SET archived = 1, retired_label = ? WHERE id = ?",
        (retired_label, credential_id),
    )
    conn.commit()
    conn.close()

    # deselect the now-archived credential so the sidebar falls back to the next active one
    return render_template("partials/body.html", **_body_context(None, "overview"))


@bp.route("/credentials/<credential_id>/unarchive", methods=["POST"])
def unarchive_credential(credential_id):
    conn = get_db()
    conn.execute(
        "UPDATE credentials SET archived = 0, retired_label = NULL WHERE id = ?",
        (credential_id,),
    )
    conn.commit()
    conn.close()

    return render_template("partials/body.html", **_body_context(credential_id, "overview"))


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
