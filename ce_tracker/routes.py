import csv
import io
import re
from datetime import date

from flask import Blueprint, Response, render_template, request

from . import progress, reports, views
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
        "category_options": [
            r["name"] for r in conn.execute(
                "SELECT DISTINCT name FROM categories WHERE credential_id IN "
                "(SELECT id FROM credentials WHERE archived = 0) ORDER BY name"
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
        "credential_form_view": False,
        "credential_form_mode": None,
        "credential_form_id": None,
        "credential_form_values": {},
        "credential_form_errors": {},
        "credential_form_categories": [],
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


def _derive_badge_visibility(platform, url):
    if url:
        return "public"
    if platform:
        return "private"
    return None


def _insert_category_rows(conn, credential_id, category_rows):
    """Inserts category rows, then links parent_id by name in a second pass (new rows don't
    have DB-assigned ids until after insert, so nesting can't be set in one shot)."""
    name_to_id = {}
    for cat in category_rows:
        cur = conn.execute(
            "INSERT INTO categories (credential_id, name, earned, target, rule_type) VALUES (?,?,0,?,?)",
            (credential_id, cat["name"], cat["target"], cat["rule_type"]),
        )
        name_to_id[cat["name"]] = cur.lastrowid
    for cat in category_rows:
        parent_name = cat.get("parent_name")
        if parent_name and parent_name in name_to_id:
            conn.execute(
                "UPDATE categories SET parent_id = ? WHERE id = ?",
                (name_to_id[parent_name], name_to_id[cat["name"]]),
            )


def _slugify(name, existing_ids):
    base = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-") or "credential"
    slug = base
    n = 2
    while slug in existing_ids:
        slug = f"{base}-{n}"
        n += 1
    return slug


def _parse_credential_form(form):
    """Validate the Add/Edit Credential form. Returns (values_for_redisplay, parsed_db_values, errors).
    `parsed_db_values` has renewal_type-appropriate fields resolved to their DB-ready types
    (dates as ISO strings, numbers as float) and unused fields set to None."""
    errors = {}

    name = (form.get("name") or "").strip()
    if not name:
        errors["name"] = "Name is required."
    issuer = (form.get("issuer") or "").strip()
    if not issuer:
        errors["issuer"] = "Issuer is required."

    renewal_type = form.get("renewal_type") or "cpe_hours"
    if renewal_type not in ("cpe_hours", "exam"):
        renewal_type = "cpe_hours"

    values = {
        "name": name,
        "issuer": issuer,
        "member_id": (form.get("member_id") or "").strip(),
        "renewal_type": renewal_type,
        "cycle_start_date": (form.get("cycle_start_date") or "").strip(),
        "cycle_years": (form.get("cycle_years") or "").strip(),
        "unit": (form.get("unit") or "").strip() or "hours",
        "total": (form.get("total") or "").strip(),
        "reporting_calendar": form.get("reporting_calendar") or "cycle_anniversary",
        "annual_minimum_type": form.get("annual_minimum_type") or "",
        "annual_minimum": (form.get("annual_minimum") or "").strip(),
        "cycle_end_date": (form.get("cycle_end_date") or "").strip(),
        "grace_period_days": (form.get("grace_period_days") or "").strip(),
        "note": (form.get("note") or "").strip(),
        "badge_platform": (form.get("badge_platform") or "").strip(),
        "badge_url": (form.get("badge_url") or "").strip(),
    }

    cycle_start_date = None
    cycle_end_date = None
    total = None
    annual_minimum = None
    grace_period_days = None
    annual_minimum_type = values["annual_minimum_type"] if values["annual_minimum_type"] in ("suggested", "mandatory") else None

    if renewal_type == "cpe_hours":
        if not values["cycle_start_date"]:
            errors["cycle_start_date"] = "Cycle start date is required."
        else:
            try:
                cycle_start_date = date.fromisoformat(values["cycle_start_date"])
            except ValueError:
                errors["cycle_start_date"] = "Enter a valid date."

        cycle_years = None
        try:
            cycle_years = int(values["cycle_years"])
            if cycle_years <= 0:
                errors["cycle_years"] = "Cycle length must be a positive whole number of years."
        except ValueError:
            errors["cycle_years"] = "Enter the cycle length in years (e.g. 3)."

        if cycle_start_date and cycle_years and "cycle_years" not in errors:
            try:
                cycle_end_date = cycle_start_date.replace(year=cycle_start_date.year + cycle_years)
            except ValueError:  # Feb 29 start, non-leap target year
                cycle_end_date = cycle_start_date.replace(year=cycle_start_date.year + cycle_years, day=28)

        try:
            total = float(values["total"])
            if total <= 0:
                errors["total"] = "Hours/points required must be greater than 0."
        except ValueError:
            errors["total"] = "Enter the hours/points required for a full cycle."

        if annual_minimum_type:
            try:
                annual_minimum = float(values["annual_minimum"])
                if annual_minimum <= 0:
                    errors["annual_minimum"] = "Enter a positive number, or set the requirement to None."
            except ValueError:
                errors["annual_minimum"] = "Enter an annual minimum, or set the requirement to None."

        if values["grace_period_days"]:
            try:
                grace_period_days = int(values["grace_period_days"])
                if grace_period_days <= 0:
                    errors["grace_period_days"] = "Enter a positive number of days, or leave blank for no grace period."
            except ValueError:
                errors["grace_period_days"] = "Enter a whole number of days, or leave blank for no grace period."

    else:  # exam
        if not values["cycle_end_date"]:
            errors["cycle_end_date"] = "Expiration date is required."
        else:
            try:
                cycle_end_date = date.fromisoformat(values["cycle_end_date"])
            except ValueError:
                errors["cycle_end_date"] = "Enter a valid date."

    category_rows = []
    names = form.getlist("category_name")
    rules = form.getlist("category_rule")
    targets = form.getlist("category_target")
    parents = form.getlist("category_parent")
    submitted_names = {(n or "").strip() for n in names if (n or "").strip()}
    for i, cat_name in enumerate(names):
        cat_name = (cat_name or "").strip()
        if not cat_name:
            continue
        rule = rules[i] if i < len(rules) else "min"
        target_raw = targets[i] if i < len(targets) else ""
        try:
            target = float(target_raw)
        except ValueError:
            errors["categories"] = f'Enter a target number for category "{cat_name}".'
            continue
        # Nesting is optional and forgiving: a parent name that doesn't match another row in
        # this same submission (typo, removed row, self-reference) just falls back to
        # top-level rather than blocking the whole form — same generic mechanism any credential
        # can use (design doc §3's category_rules/sub_requirements), not PMI-specific.
        parent_name = (parents[i] if i < len(parents) else "").strip()
        if parent_name not in submitted_names or parent_name == cat_name:
            parent_name = None
        category_rows.append({
            "name": cat_name, "rule_type": rule if rule in ("min", "max") else "min",
            "target": target, "parent_name": parent_name,
        })

    parsed = {
        "renewal_type": renewal_type,
        "cycle_start_date": cycle_start_date.isoformat() if cycle_start_date else None,
        "cycle_end_date": cycle_end_date.isoformat() if cycle_end_date else None,
        "reporting_calendar": values["reporting_calendar"] if renewal_type == "cpe_hours" else None,
        "unit": values["unit"] if renewal_type == "cpe_hours" else None,
        "total": total,
        "annual_minimum": annual_minimum,
        "annual_minimum_type": annual_minimum_type if renewal_type == "cpe_hours" else None,
        "grace_period_days": grace_period_days if renewal_type == "cpe_hours" else None,
        "category_rows": category_rows if renewal_type == "cpe_hours" else [],
    }
    return values, parsed, errors


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


@bp.route("/activities/<int:activity_id>", methods=["DELETE"])
def delete_activity(activity_id):
    selected_id = request.args.get("selected")
    tab = request.args.get("tab", "activity")

    conn = get_db()
    conn.execute("DELETE FROM activities WHERE id = ?", (activity_id,))
    conn.commit()
    conn.close()

    return render_template("partials/body.html", **_body_context(selected_id, tab))


@bp.route("/credentials/new")
def new_credential_form():
    context = _body_context(request.args.get("selected"), request.args.get("tab", "overview"))
    context["credential_form_view"] = True
    context["credential_form_mode"] = "add"
    context["credential_form_values"] = {
        "renewal_type": "cpe_hours",
        "reporting_calendar": "cycle_anniversary",
        "annual_minimum_type": "",
        "cycle_years": "3",
        "unit": "hours",
    }
    context["credential_form_categories"] = []
    return render_template("partials/body.html", **context)


@bp.route("/credentials", methods=["POST"])
def create_credential():
    values, parsed, errors = _parse_credential_form(request.form)

    if errors:
        context = _body_context(request.args.get("selected"), request.args.get("tab", "overview"))
        context["credential_form_view"] = True
        context["credential_form_mode"] = "add"
        context["credential_form_values"] = values
        context["credential_form_categories"] = parsed["category_rows"]
        context["credential_form_errors"] = errors
        return render_template("partials/body.html", **context)

    badge_platform = values["badge_platform"] or None
    badge_url = values["badge_url"] or None

    conn = get_db()
    existing_ids = {r["id"] for r in conn.execute("SELECT id FROM credentials").fetchall()}
    new_id = _slugify(values["name"], existing_ids)

    conn.execute(
        "INSERT INTO credentials (id, name, issuer, member_id, status, renewal_type, cycle_start_date, cycle_end_date, "
        "reporting_calendar, unit, total, annual_minimum, annual_minimum_type, grace_period_days, note, "
        "badge_platform, badge_url, badge_visibility) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            new_id, values["name"], values["issuer"], values["member_id"] or None, "on-track", parsed["renewal_type"],
            parsed["cycle_start_date"], parsed["cycle_end_date"], parsed["reporting_calendar"],
            parsed["unit"], parsed["total"], parsed["annual_minimum"], parsed["annual_minimum_type"],
            parsed["grace_period_days"], values["note"] or None, badge_platform, badge_url,
            _derive_badge_visibility(badge_platform, badge_url),
        ),
    )
    _insert_category_rows(conn, new_id, parsed["category_rows"])
    conn.commit()
    conn.close()

    return render_template("partials/body.html", **_body_context(new_id, "overview"))


@bp.route("/credentials/<credential_id>/edit")
def edit_credential_form(credential_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM credentials WHERE id = ?", (credential_id,)).fetchone()
    if not row:
        conn.close()
        return render_template("partials/body.html", **_body_context(None, "overview"))

    categories = conn.execute(
        "SELECT id, name, target, rule_type, parent_id FROM categories WHERE credential_id = ?", (credential_id,)
    ).fetchall()
    conn.close()

    id_to_name = {c["id"]: c["name"] for c in categories}
    category_view_rows = [
        {
            "name": c["name"], "target": c["target"], "rule_type": c["rule_type"],
            "parent_name": id_to_name.get(c["parent_id"]),
        }
        for c in categories
    ]

    cycle_years = ""
    if row["cycle_start_date"] and row["cycle_end_date"]:
        years = date.fromisoformat(row["cycle_end_date"]).year - date.fromisoformat(row["cycle_start_date"]).year
        cycle_years = str(years) if years else ""

    selected_id = credential_id if not row["archived"] else request.args.get("selected")
    context = _body_context(selected_id, request.args.get("tab", "overview"))
    context["credential_form_view"] = True
    context["credential_form_mode"] = "edit"
    context["credential_form_id"] = credential_id
    context["credential_form_values"] = {
        "name": row["name"],
        "issuer": row["issuer"],
        "member_id": row["member_id"] or "",
        "renewal_type": row["renewal_type"],
        "cycle_start_date": row["cycle_start_date"] or "",
        "cycle_years": cycle_years,
        "unit": row["unit"] or "hours",
        "total": "" if row["total"] is None else f"{row['total']:g}",
        "reporting_calendar": row["reporting_calendar"] or "cycle_anniversary",
        "annual_minimum_type": row["annual_minimum_type"] or "",
        "annual_minimum": "" if row["annual_minimum"] is None else f"{row['annual_minimum']:g}",
        "cycle_end_date": row["cycle_end_date"] or "",
        "grace_period_days": "" if row["grace_period_days"] is None else str(row["grace_period_days"]),
        "note": row["note"] or "",
        "badge_platform": row["badge_platform"] or "",
        "badge_url": row["badge_url"] or "",
    }
    context["credential_form_categories"] = category_view_rows
    return render_template("partials/body.html", **context)


@bp.route("/credentials/<credential_id>/edit", methods=["POST"])
def update_credential(credential_id):
    tab = request.args.get("tab", "overview")
    values, parsed, errors = _parse_credential_form(request.form)

    if errors:
        context = _body_context(credential_id, tab)
        context["credential_form_view"] = True
        context["credential_form_mode"] = "edit"
        context["credential_form_id"] = credential_id
        context["credential_form_values"] = values
        context["credential_form_categories"] = parsed["category_rows"]
        context["credential_form_errors"] = errors
        return render_template("partials/body.html", **context)

    badge_platform = values["badge_platform"] or None
    badge_url = values["badge_url"] or None

    conn = get_db()
    conn.execute(
        "UPDATE credentials SET name=?, issuer=?, member_id=?, renewal_type=?, cycle_start_date=?, cycle_end_date=?, "
        "reporting_calendar=?, unit=?, total=?, annual_minimum=?, annual_minimum_type=?, grace_period_days=?, note=?, "
        "badge_platform=?, badge_url=?, badge_visibility=? WHERE id=?",
        (
            values["name"], values["issuer"], values["member_id"] or None, parsed["renewal_type"], parsed["cycle_start_date"],
            parsed["cycle_end_date"], parsed["reporting_calendar"], parsed["unit"], parsed["total"],
            parsed["annual_minimum"], parsed["annual_minimum_type"], parsed["grace_period_days"], values["note"] or None,
            badge_platform, badge_url, _derive_badge_visibility(badge_platform, badge_url),
            credential_id,
        ),
    )
    conn.execute("DELETE FROM categories WHERE credential_id = ?", (credential_id,))
    _insert_category_rows(conn, credential_id, parsed["category_rows"])
    conn.commit()
    conn.close()

    return render_template("partials/body.html", **_body_context(credential_id, tab))


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
    visibility = _derive_badge_visibility(platform, url)

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


def _credential_and_in_cycle_activities(conn, credential_id):
    """Returns (row, categories, all_applicable_activities, in_cycle_activities) or
    (None, None, None, None) if the credential doesn't exist. Shared by both export routes."""
    row = conn.execute("SELECT * FROM credentials WHERE id = ?", (credential_id,)).fetchone()
    if not row:
        return None, None, None, None

    categories = conn.execute(
        "SELECT id, name, target, rule_type, parent_id FROM categories WHERE credential_id = ?", (credential_id,)
    ).fetchall()
    activities = conn.execute(
        "SELECT * FROM activities WHERE ',' || applies || ',' LIKE '%,' || ? || ',%' ORDER BY date", (credential_id,)
    ).fetchall()

    start, end = row["cycle_start_date"], row["cycle_end_date"]
    in_cycle = [a for a in activities if (not start or a["date"] >= start) and (not end or a["date"] <= end)]
    return row, categories, activities, in_cycle


@bp.route("/credentials/<credential_id>/export.csv")
def export_activities_csv(credential_id):
    conn = get_db()
    row, _categories, _activities, in_cycle = _credential_and_in_cycle_activities(conn, credential_id)
    conn.close()
    if not row:
        return render_template("partials/body.html", **_body_context(None, "overview"))

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Date", "Provider", "Title", "Hours", "Category", "Applies To", "Status"])
    for a in in_cycle:
        writer.writerow([a["date"], a["provider"] or "", a["title"], a["hours"], a["category"] or "", a["applies"], a["status"]])

    filename = f"{row['id']}-activities-{date.today().isoformat()}.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@bp.route("/credentials/<credential_id>/export.pdf")
def export_activities_pdf(credential_id):
    conn = get_db()
    row, categories, activities, in_cycle = _credential_and_in_cycle_activities(conn, credential_id)
    conn.close()
    if not row:
        return render_template("partials/body.html", **_body_context(None, "overview"))

    prog = progress.compute_progress(row, activities, categories)
    pdf_bytes = reports.build_activity_report_pdf(row, in_cycle, prog["category_results"], prog["earned"] or 0)

    filename = f"{row['id']}-report-{date.today().isoformat()}.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
