"""Tests for ce_tracker.progress — compute_progress(), _anniversary_window(), and
apply_dependency_cascade() are pure functions over plain data (dicts stand in for
sqlite3.Row, which the code only touches via row["key"] and "key" in row.keys()), so no
database or Flask app is involved. `today` is always pinned so results never drift as real
time passes. Run with:
    python -m unittest discover -s tests
"""

import unittest
from datetime import date

from ce_tracker.progress import _anniversary_window, apply_dependency_cascade, compute_progress

TODAY = date(2026, 7, 15)


def _credential(**overrides):
    """A cpe_hours credential mid-cycle relative to TODAY; tests override what differs."""
    row = {
        "renewal_type": "cpe_hours",
        "cycle_start_date": "2024-05-01",
        "cycle_end_date": "2027-04-30",
        "reporting_calendar": "cycle_anniversary",
        "total": 120,
        "annual_minimum_type": None,
        "manual_adjustment_hours": None,
        "grace_period_days": None,
        "depends_on": None,
        "depends_on_any_of": None,
    }
    row.update(overrides)
    return row


def _activity(activity_date, hours=1.0, category=None):
    # Dates stay ISO strings, matching storage — _in_window compares them lexicographically.
    return {"date": activity_date, "hours": hours, "category": category}


def _category(cat_id, name, target, rule_type="min", parent_id=None, manual_adjustment=None):
    return {
        "id": cat_id, "name": name, "target": target, "rule_type": rule_type,
        "parent_id": parent_id, "manual_adjustment": manual_adjustment,
    }


class ExamRenewalTests(unittest.TestCase):
    def test_future_expiration_is_reminder_with_days_left(self):
        result = compute_progress(
            _credential(renewal_type="exam", cycle_start_date=None, cycle_end_date="2026-08-14"),
            [], [], today=TODAY,
        )
        self.assertEqual(result["status"], "reminder")
        self.assertEqual(result["days_left"], 30)
        self.assertIsNone(result["pct"])
        self.assertIsNone(result["earned"])

    def test_expiring_today_is_still_reminder_not_expired(self):
        result = compute_progress(
            _credential(renewal_type="exam", cycle_end_date=TODAY.isoformat()),
            [], [], today=TODAY,
        )
        self.assertEqual(result["days_left"], 0)
        self.assertEqual(result["status"], "reminder")

    def test_past_expiration_is_expired(self):
        result = compute_progress(
            _credential(renewal_type="exam", cycle_end_date="2026-07-01"),
            [], [], today=TODAY,
        )
        self.assertEqual(result["status"], "expired")
        self.assertEqual(result["days_left"], -14)

    def test_missing_expiration_date_is_reminder_with_no_countdown(self):
        result = compute_progress(
            _credential(renewal_type="exam", cycle_end_date=None),
            [], [], today=TODAY,
        )
        self.assertEqual(result["status"], "reminder")
        self.assertIsNone(result["days_left"])


class UnimplementedRenewalTypeTests(unittest.TestCase):
    def test_mandatory_module_surfaces_neutrally(self):
        # Schema-only renewal type (design doc §9): no computation branch yet, so it must
        # come back neutral rather than crash or pretend to know a status.
        result = compute_progress(_credential(renewal_type="mandatory_module"), [], [], today=TODAY)
        self.assertEqual(result["status"], "reminder")
        self.assertIsNone(result["pct"])
        self.assertIsNone(result["days_left"])


class EarningMathTests(unittest.TestCase):
    def test_sums_hours_inside_cycle_window_only(self):
        activities = [
            _activity("2024-04-30", 5.0),   # day before cycle start — excluded
            _activity("2025-06-01", 3.0),
            _activity("2026-01-15", 4.5),
            _activity("2027-05-01", 8.0),   # day after cycle end — excluded
        ]
        result = compute_progress(_credential(), activities, [], today=TODAY)
        self.assertEqual(result["earned"], 7.5)
        self.assertEqual(result["earned_logged"], 7.5)
        self.assertEqual(result["earned_adjustment"], 0)

    def test_cycle_boundary_days_are_inclusive(self):
        activities = [
            _activity("2024-05-01", 2.0),  # exactly cycle_start
            _activity("2027-04-30", 3.0),  # exactly cycle_end
        ]
        result = compute_progress(_credential(), activities, [], today=TODAY)
        self.assertEqual(result["earned"], 5.0)

    def test_portal_confirmed_total_fills_gap_without_double_counting(self):
        # manual_adjustment_hours is the portal's own last-confirmed total, not a flat
        # add-on: only the gap versus what's actually logged fills in, so it never pushes
        # the total past what the portal itself confirms.
        result = compute_progress(
            _credential(manual_adjustment_hours=10.0),
            [_activity("2025-06-01", 5.0)], [], today=TODAY,
        )
        self.assertEqual(result["earned_logged"], 5.0)
        self.assertEqual(result["earned_adjustment"], 5.0)  # gap: 10 confirmed - 5 logged
        self.assertEqual(result["earned"], 10.0)

    def test_portal_confirmed_total_adjustment_shrinks_to_zero_once_fully_logged(self):
        # Once locally-itemized activities catch up to (or pass) the portal's confirmed
        # total, the adjustment contributes nothing further — no double count, no negative gap.
        result = compute_progress(
            _credential(manual_adjustment_hours=10.0),
            [_activity("2025-06-01", 12.0)], [], today=TODAY,
        )
        self.assertEqual(result["earned_logged"], 12.0)
        self.assertEqual(result["earned_adjustment"], 0.0)
        self.assertEqual(result["earned"], 12.0)

    def test_pct_rounds_and_caps_at_100(self):
        result = compute_progress(_credential(total=3), [_activity("2025-06-01", 2.0)], [], today=TODAY)
        self.assertEqual(result["pct"], 67)  # 66.67 rounds up

        over = compute_progress(_credential(total=100), [_activity("2025-06-01", 250.0)], [], today=TODAY)
        self.assertEqual(over["pct"], 100)

    def test_pct_uses_bankers_rounding_on_exact_halves(self):
        # Pins current behavior: Python's round() takes .5 to the nearest EVEN number, so
        # exactly 12.5% displays as 12, not 13. (1/8 is exact in binary floats, so this
        # assertion is deterministic.) If display rounding should always round half up,
        # that's a deliberate change to make in progress.py, not a test to loosen.
        result = compute_progress(_credential(total=8), [_activity("2025-06-01", 1.0)], [], today=TODAY)
        self.assertEqual(result["pct"], 12)

    def test_zero_total_yields_zero_pct_and_on_track(self):
        result = compute_progress(_credential(total=0), [_activity("2025-06-01", 5.0)], [], today=TODAY)
        self.assertEqual(result["pct"], 0)
        self.assertTrue(result["on_track"])
        self.assertEqual(result["status"], "on-track")


class OnTrackFormulaTests(unittest.TestCase):
    # 3-year cycle 2024-05-01 → 2027-04-30 is 1094 days; TODAY is day 805 (~73.6% elapsed),
    # so with total=120 the pace threshold is ~88.3 hours.

    def test_ahead_of_pace_is_on_track(self):
        result = compute_progress(_credential(), [_activity("2025-06-01", 100.0)], [], today=TODAY)
        self.assertTrue(result["on_track"])
        self.assertEqual(result["status"], "on-track")

    def test_behind_pace_is_at_risk(self):
        result = compute_progress(_credential(), [_activity("2025-06-01", 60.0)], [], today=TODAY)
        self.assertFalse(result["on_track"])
        self.assertEqual(result["status"], "at-risk")

    def test_exactly_at_pace_counts_as_on_track(self):
        # 1-year cycle, exactly halfway through in both time and hours: >= means on-track.
        cred = _credential(cycle_start_date="2026-01-01", cycle_end_date="2026-12-31", total=100)
        halfway = date(2026, 7, 2)  # day 182 of 364
        result = compute_progress(cred, [_activity("2026-03-01", 50.0)], [], today=halfway)
        self.assertTrue(result["on_track"])

    def test_before_cycle_start_is_on_track_with_zero_hours(self):
        # days_elapsed clamps to 0, so a cycle that hasn't begun can't be behind pace.
        cred = _credential(cycle_start_date="2026-09-01", cycle_end_date="2029-08-31")
        result = compute_progress(cred, [], [], today=TODAY)
        self.assertTrue(result["on_track"])
        self.assertEqual(result["status"], "on-track")


class PostDeadlineTests(unittest.TestCase):
    ENDED = _credential(cycle_start_date="2023-06-01", cycle_end_date="2026-05-31", total=120)

    def test_complete_hours_past_deadline_is_still_on_track(self):
        result = compute_progress(self.ENDED, [_activity("2025-01-15", 120.0)], [], today=TODAY)
        self.assertEqual(result["status"], "on-track")
        self.assertEqual(result["days_left"], -45)

    def test_short_hours_with_no_grace_period_is_expired_immediately(self):
        # NULL grace_period_days = hard cutoff (the GIAC-style case in the design doc).
        result = compute_progress(self.ENDED, [_activity("2025-01-15", 100.0)], [], today=TODAY)
        self.assertEqual(result["status"], "expired")

    def test_short_hours_inside_grace_window_is_grace_period(self):
        cred = dict(self.ENDED, grace_period_days=90)  # grace ends 2026-08-29
        result = compute_progress(cred, [_activity("2025-01-15", 100.0)], [], today=TODAY)
        self.assertEqual(result["status"], "grace-period")
        self.assertEqual(result["grace_days_left"], 45)

    def test_last_day_of_grace_still_counts_as_grace_period(self):
        cred = dict(self.ENDED, grace_period_days=45)  # grace ends exactly TODAY
        result = compute_progress(cred, [_activity("2025-01-15", 100.0)], [], today=TODAY)
        self.assertEqual(result["status"], "grace-period")
        self.assertEqual(result["grace_days_left"], 0)

    def test_past_grace_window_is_expired(self):
        cred = dict(self.ENDED, grace_period_days=30)  # grace ended 2026-06-30
        result = compute_progress(cred, [_activity("2025-01-15", 100.0)], [], today=TODAY)
        self.assertEqual(result["status"], "expired")
        self.assertIsNone(result["grace_days_left"])


class AnnualWindowTests(unittest.TestCase):
    def test_no_annual_minimum_type_means_no_annual_earned(self):
        result = compute_progress(_credential(), [_activity("2026-06-01", 5.0)], [], today=TODAY)
        self.assertIsNone(result["annual_earned"])

    def test_calendar_year_window_counts_current_year_only(self):
        cred = _credential(annual_minimum_type="mandatory", reporting_calendar="calendar_year")
        activities = [
            _activity("2025-12-31", 10.0),  # prior year — excluded from annual
            _activity("2026-01-01", 3.0),
            _activity("2026-07-01", 4.0),
        ]
        result = compute_progress(cred, activities, [], today=TODAY)
        self.assertEqual(result["annual_earned"], 7.0)
        self.assertEqual(result["earned"], 17.0)  # cycle total still counts all three

    def test_anniversary_window_counts_since_most_recent_anniversary(self):
        # cycle_start 2024-05-01 → the window containing TODAY is [2026-05-01, 2027-05-01).
        cred = _credential(annual_minimum_type="suggested")
        activities = [
            _activity("2026-04-30", 10.0),  # last day of the PREVIOUS anniversary year
            _activity("2026-05-01", 2.0),
            _activity("2026-07-01", 6.0),
        ]
        result = compute_progress(cred, activities, [], today=TODAY)
        self.assertEqual(result["annual_earned"], 8.0)


class AnniversaryWindowTests(unittest.TestCase):
    def test_anniversary_already_passed_this_year(self):
        self.assertEqual(
            _anniversary_window("2024-05-01", date(2026, 7, 15)),
            ("2026-05-01", "2027-05-01"),
        )

    def test_anniversary_still_ahead_uses_previous_year(self):
        self.assertEqual(
            _anniversary_window("2024-05-01", date(2026, 3, 1)),
            ("2025-05-01", "2026-05-01"),
        )

    def test_today_exactly_on_anniversary_starts_window_today(self):
        self.assertEqual(
            _anniversary_window("2024-05-01", date(2026, 5, 1)),
            ("2026-05-01", "2027-05-01"),
        )

    def test_feb_29_start_anchors_to_feb_28_in_non_leap_years(self):
        self.assertEqual(
            _anniversary_window("2024-02-29", date(2026, 3, 15)),
            ("2026-02-28", "2027-02-28"),
        )


class CategoryRuleTests(unittest.TestCase):
    def test_flat_min_rule_pass_and_fail(self):
        categories = [_category(1, "Group A", target=10)]
        passing = compute_progress(
            _credential(), [_activity("2025-06-01", 12.0, category="Group A")], categories, today=TODAY,
        )
        self.assertEqual(passing["category_results"], [
            {"name": "Group A", "earned": 12.0, "target": 10, "rule_type": "min", "ok": True, "depth": 0},
        ])
        failing = compute_progress(
            _credential(), [_activity("2025-06-01", 6.0, category="Group A")], categories, today=TODAY,
        )
        self.assertFalse(failing["category_results"][0]["ok"])

    def test_max_rule_caps_instead_of_requiring(self):
        categories = [_category(1, "Self-study", target=30, rule_type="max")]
        result = compute_progress(
            _credential(), [_activity("2025-06-01", 40.0, category="Self-study")], categories, today=TODAY,
        )
        self.assertFalse(result["category_results"][0]["ok"])

    def test_category_sums_respect_cycle_window(self):
        categories = [_category(1, "Group A", target=10)]
        result = compute_progress(
            _credential(),
            [_activity("2024-01-01", 50.0, category="Group A")],  # before cycle start
            categories, today=TODAY,
        )
        self.assertEqual(result["category_results"][0]["earned"], 0.0)

    def test_category_manual_adjustment_adds_to_direct_hours(self):
        categories = [_category(1, "Group A", target=10, manual_adjustment=4.0)]
        result = compute_progress(
            _credential(), [_activity("2025-06-01", 7.0, category="Group A")], categories, today=TODAY,
        )
        self.assertEqual(result["category_results"][0]["earned"], 11.0)
        self.assertTrue(result["category_results"][0]["ok"])

    def test_nested_categories_roll_up_into_parent(self):
        # PMI-style: Talent Triangle legs nested inside "Education". Parent earned = its own
        # directly-tagged hours plus everything its children earned; children keep their own
        # pass/fail and render at depth 1, in order, after the parent.
        categories = [
            _category(1, "Education", target=35),
            _category(2, "Ways of Working", target=8, parent_id=1),
            _category(3, "Power Skills", target=8, parent_id=1),
        ]
        activities = [
            _activity("2025-06-01", 20.0, category="Education"),
            _activity("2025-07-01", 10.0, category="Ways of Working"),
            _activity("2025-08-01", 6.0, category="Power Skills"),
        ]
        result = compute_progress(_credential(), activities, categories, today=TODAY)
        by_name = {c["name"]: c for c in result["category_results"]}
        self.assertEqual(by_name["Education"]["earned"], 36.0)  # 20 direct + 10 + 6 rolled up
        self.assertTrue(by_name["Education"]["ok"])
        self.assertEqual(by_name["Ways of Working"]["earned"], 10.0)
        self.assertFalse(by_name["Power Skills"]["ok"])  # 6 < 8 even though parent passes
        self.assertEqual(
            [(c["name"], c["depth"]) for c in result["category_results"]],
            [("Education", 0), ("Ways of Working", 1), ("Power Skills", 1)],
        )

    def test_grandchild_rolls_up_through_two_levels(self):
        categories = [
            _category(1, "Group A", target=30),
            _category(2, "Domain", target=10, parent_id=1),
            _category(3, "Sub-domain", target=5, parent_id=2),
        ]
        result = compute_progress(
            _credential(), [_activity("2025-06-01", 5.0, category="Sub-domain")], categories, today=TODAY,
        )
        by_name = {c["name"]: c for c in result["category_results"]}
        self.assertEqual(by_name["Group A"]["earned"], 5.0)
        self.assertEqual(by_name["Domain"]["earned"], 5.0)
        self.assertEqual(by_name["Sub-domain"]["depth"], 2)


class DependencyCascadeTests(unittest.TestCase):
    def test_expired_prerequisite_escalates_to_at_risk_with_note(self):
        status, note = apply_dependency_cascade(
            _credential(depends_on="cissp"), "on-track", {"cissp": "expired"},
        )
        self.assertEqual(status, "at-risk")
        self.assertIn("cissp", note)

    def test_healthy_prerequisite_leaves_status_untouched(self):
        status, note = apply_dependency_cascade(
            _credential(depends_on="cissp"), "on-track", {"cissp": "at-risk"},
        )
        self.assertEqual((status, note), ("on-track", None))

    def test_unknown_prerequisite_id_does_not_escalate(self):
        # A dangling depends_on (credential deleted/renamed) must not fabricate a warning.
        status, note = apply_dependency_cascade(
            _credential(depends_on="ghost"), "on-track", {},
        )
        self.assertEqual((status, note), ("on-track", None))

    def test_cascade_never_deescalates_expired(self):
        status, note = apply_dependency_cascade(
            _credential(depends_on="cissp"), "expired", {"cissp": "on-track"},
        )
        self.assertEqual(status, "expired")

    def test_any_of_with_one_survivor_is_fine(self):
        cred = _credential(depends_on_any_of="cipp-us, cipm, cipt")
        status, note = apply_dependency_cascade(
            cred, "on-track", {"cipp-us": "expired", "cipm": "expired", "cipt": "on-track"},
        )
        self.assertEqual((status, note), ("on-track", None))

    def test_any_of_with_all_expired_escalates(self):
        cred = _credential(depends_on_any_of="cipp-us, cipm")
        status, note = apply_dependency_cascade(
            cred, "on-track", {"cipp-us": "expired", "cipm": "expired"},
        )
        self.assertEqual(status, "at-risk")
        self.assertIn("cipp-us", note)


if __name__ == "__main__":
    unittest.main()
