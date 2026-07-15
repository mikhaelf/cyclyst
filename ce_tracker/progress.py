"""Computes credential progress (status, pct earned, days left, category pass/fail) from
stored cycle dates and activity data — nothing here is persisted, it's recomputed on every
view. Implements design doc §4 for the 'cpe_hours' and 'exam' renewal types; 'mandatory_module'
and the renews/depends_on cascade are schema-only for now (see plan/CLAUDE.md scope note).
"""

from datetime import date


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
            "on_track": None, "annual_earned": None, "category_results": [],
        }

    if renewal_type != "cpe_hours":
        # mandatory_module / other future types — no computation branch yet, surface neutrally
        return {
            "status": "reminder", "pct": None, "earned": None, "days_left": None,
            "on_track": None, "annual_earned": None, "category_results": [],
        }

    cycle_start = credential_row["cycle_start_date"]
    cycle_end = credential_row["cycle_end_date"]
    total = credential_row["total"] or 0

    earned = _sum_hours(activities, start=cycle_start, end=cycle_end)
    pct = min(100, round(earned / total * 100)) if total else 0

    start_d = date.fromisoformat(cycle_start) if cycle_start else today
    end_d = date.fromisoformat(cycle_end) if cycle_end else today
    days_left = (end_d - today).days
    days_total = (end_d - start_d).days
    days_elapsed = max(0, (today - start_d).days)
    on_track = (earned / total) >= (days_elapsed / days_total) if total and days_total > 0 else True

    if today > end_d:
        status = "on-track" if earned >= total else "expired"
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

    category_results = []
    for cat in categories:
        cat_earned = _sum_hours(activities, start=cycle_start, end=cycle_end, category=cat["name"])
        ok = cat_earned >= cat["target"] if cat["rule_type"] == "min" else cat_earned <= cat["target"]
        category_results.append({
            "name": cat["name"], "earned": cat_earned, "target": cat["target"],
            "rule_type": cat["rule_type"], "ok": ok,
        })

    return {
        "status": status, "pct": pct, "earned": earned, "days_left": days_left,
        "on_track": on_track, "annual_earned": annual_earned, "category_results": category_results,
    }
