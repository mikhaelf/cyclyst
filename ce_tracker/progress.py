"""Computes credential progress (status, pct earned, days left, category pass/fail) from
stored cycle dates and activity data — nothing here is persisted, it's recomputed on every
view. Implements design doc §4 for the 'cpe_hours' and 'exam' renewal types, plus the
depends_on cascade (branch 4); 'mandatory_module' and the 'renews' stacking relationship
are still schema-only.
"""

from datetime import date, timedelta


def _in_window(activity_date, start, end):
    if start and activity_date < start:
        return False
    if end and activity_date > end:
        return False
    return True


def _sum_hours(activities, start=None, end=None, category=None):
    total = 0.0
    for a in activities:
        if category is not None and a["category"] != category:
            continue
        if not _in_window(a["date"], start, end):
            continue
        total += a["hours"]
    return total


def _anniversary_window(cycle_start_date, today):
    """The [anniversary, anniversary+1yr) window of cycle_start_date's month/day that contains today."""
    start = date.fromisoformat(cycle_start_date)
    try:
        anniv = date(today.year, start.month, start.day)
    except ValueError:
        anniv = date(today.year, start.month, 28)  # Feb 29 anchor on a non-leap year
    if anniv > today:
        anniv = anniv.replace(year=anniv.year - 1)
    window_end = anniv.replace(year=anniv.year + 1)
    return anniv.isoformat(), window_end.isoformat()


def _compute_category_results(activities, categories, cycle_start, cycle_end):
    """Category rules can nest (a category's `parent_id` points at another category row for
    the same credential) — e.g. PMI's Talent Triangle legs living inside an "Education"
    bucket, or ISC2's ISSEP sub-quota inside Group A (design doc §3's `category_rules` +
    `sub_requirements`). A parent's earned total rolls up its own directly-tagged activities
    plus everything earned by its descendants, so activities can be logged against whichever
    level is most specific without double-entry. Flat (non-nested) categories — the common
    case — behave exactly as before: no parent, no children, earned = direct match only.
    This is entirely data-driven per credential; nothing here is specific to any one issuer.

    A category's `manual_adjustment` folds in on top of its direct activity sum — the issuer's
    own portal is the real system of record, and hours it already confirms don't need a
    locally-itemized activity to back them (see credential-level manual_adjustment_hours below
    for the same idea applied to the whole cycle total).
    """
    children_by_parent = {}
    for cat in categories:
        parent_id = cat["parent_id"] if "parent_id" in cat.keys() else None
        if parent_id:
            children_by_parent.setdefault(parent_id, []).append(cat)

    def direct_earned(cat):
        logged = _sum_hours(activities, start=cycle_start, end=cycle_end, category=cat["name"])
        adjustment = cat["manual_adjustment"] if "manual_adjustment" in cat.keys() else None
        return logged + (adjustment or 0)

    def rollup_earned(cat):
        total = direct_earned(cat)
        cat_id = cat["id"] if "id" in cat.keys() else None
        for child in children_by_parent.get(cat_id, []):
            total += rollup_earned(child)
        return total

    def build(cat, depth):
        earned = rollup_earned(cat)
        ok = earned >= cat["target"] if cat["rule_type"] == "min" else earned <= cat["target"]
        results = [{
            "name": cat["name"], "earned": earned, "target": cat["target"],
            "rule_type": cat["rule_type"], "ok": ok, "depth": depth,
        }]
        cat_id = cat["id"] if "id" in cat.keys() else None
        for child in children_by_parent.get(cat_id, []):
            results.extend(build(child, depth + 1))
        return results

    top_level = [c for c in categories if not (("parent_id" in c.keys()) and c["parent_id"])]
    category_results = []
    for cat in top_level:
        category_results.extend(build(cat, 0))
    return category_results


def compute_progress(credential_row, activities, categories, today=None):
    """activities: rows already filtered to ones whose `applies` includes this credential.
    categories: this credential's category-rule rows. Returns a dict of computed values —
    callers (views.py) turn these into display strings/colors."""
    today = today or date.today()
    renewal_type = credential_row["renewal_type"]

    if renewal_type == "exam":
        cycle_end = credential_row["cycle_end_date"]
        days_left = (date.fromisoformat(cycle_end) - today).days if cycle_end else None
        status = "reminder" if (days_left is None or days_left >= 0) else "expired"
        return {
            "status": status, "pct": None, "earned": None, "days_left": days_left,
            "on_track": None, "annual_earned": None, "category_results": [], "grace_days_left": None,
            "earned_logged": None, "earned_adjustment": None,
        }

    if renewal_type != "cpe_hours":
        # mandatory_module / other future types — no computation branch yet, surface neutrally
        return {
            "status": "reminder", "pct": None, "earned": None, "days_left": None,
            "on_track": None, "annual_earned": None, "category_results": [], "grace_days_left": None,
            "earned_logged": None, "earned_adjustment": None,
        }

    cycle_start = credential_row["cycle_start_date"]
    cycle_end = credential_row["cycle_end_date"]
    total = credential_row["total"] or 0

    # This tracker aggregates across issuers; it isn't itself the system of record — the
    # issuer's own portal is. `manual_adjustment_hours` holds the portal's own last-confirmed
    # total for this credential (refreshed whenever you re-check the portal), not a flat
    # add-on — the gap between that total and what's actually itemized here fills in
    # automatically. That makes it self-correcting as activities get logged: approving a
    # previously-adjustment-covered activity into the log shrinks the gap by the same amount,
    # so the same portal hours are never counted twice.
    earned_logged = _sum_hours(activities, start=cycle_start, end=cycle_end)
    portal_confirmed = credential_row["manual_adjustment_hours"] if "manual_adjustment_hours" in credential_row.keys() else None
    earned_adjustment = max(0, (portal_confirmed or 0) - earned_logged)
    earned = earned_logged + earned_adjustment
    pct = min(100, round(earned / total * 100)) if total else 0

    start_d = date.fromisoformat(cycle_start) if cycle_start else today
    end_d = date.fromisoformat(cycle_end) if cycle_end else today
    days_left = (end_d - today).days
    days_total = (end_d - start_d).days
    days_elapsed = max(0, (today - start_d).days)
    on_track = (earned / total) >= (days_elapsed / days_total) if total and days_total > 0 else True

    # Grace period (design doc §4 branch 2): past cycle_end with hours still short doesn't mean
    # expired outright if this issuer gives a post-deadline window to finish earning/submitting
    # (e.g. ISC2's 90 days) — `grace_period_days` is generic per credential, NULL means "no
    # grace, hard cutoff" (matches issuers like GIAC per the design doc).
    grace_period_days = credential_row["grace_period_days"] if "grace_period_days" in credential_row.keys() else None
    grace_days_left = None

    if today > end_d:
        if earned >= total:
            status = "on-track"
        elif grace_period_days:
            grace_end_d = end_d + timedelta(days=grace_period_days)
            if today <= grace_end_d:
                status = "grace-period"
                grace_days_left = (grace_end_d - today).days
            else:
                status = "expired"
        else:
            status = "expired"
    elif on_track:
        status = "on-track"
    else:
        status = "at-risk"

    annual_earned = None
    if credential_row["annual_minimum_type"] and cycle_start:
        if credential_row["reporting_calendar"] == "calendar_year":
            window_start = date(today.year, 1, 1).isoformat()
            window_end = date(today.year, 12, 31).isoformat()
        else:
            window_start, window_end = _anniversary_window(cycle_start, today)
        annual_earned = _sum_hours(activities, start=window_start, end=window_end)

    category_results = _compute_category_results(activities, categories, cycle_start, cycle_end)

    return {
        "status": status, "pct": pct, "earned": earned, "days_left": days_left,
        "on_track": on_track, "annual_earned": annual_earned, "category_results": category_results,
        "grace_days_left": grace_days_left, "earned_logged": earned_logged, "earned_adjustment": earned_adjustment,
    }


def apply_dependency_cascade(credential_row, status, status_by_id):
    """Design doc §4 branch 4: a credential can be individually on track but still be
    invalid because a prerequisite lapsed. `status_by_id` is every OTHER credential's own
    intrinsic status (id -> status), computed independently — this only ever escalates
    `status` to 'at-risk', never de-escalates it, and never itself produces 'expired'
    (matching the design doc, which treats cascade failure as a warning, not an outage).

    depends_on: single credential id that must not be 'expired' (AND).
    depends_on_any_of: comma-separated credential ids where at least one must not be
    'expired' (OR) — for compound cases like IAPP's FIP (requires CIPP + one of CIPM/CIPT/AIGP)
    that a single depends_on can't express.

    Returns (final_status, cascade_note_or_None).
    """
    depends_on = credential_row["depends_on"]
    if depends_on and status_by_id.get(depends_on) == "expired":
        return "at-risk", f"Prerequisite credential '{depends_on}' has expired."

    any_of_raw = credential_row["depends_on_any_of"] if "depends_on_any_of" in credential_row.keys() else None
    any_of = [c.strip() for c in any_of_raw.split(",") if c.strip()] if any_of_raw else []
    if any_of and all(status_by_id.get(c) == "expired" for c in any_of):
        return "at-risk", "None of the qualifying alternate credentials (" + ", ".join(any_of) + ") are currently active."

    return status, None
