"""Tests for ce_tracker.portal_submit — the issuer-portal net-new/duplicate-prevention ledger
(design doc §7a/§7b), covering both ISC2 (CISSP) and PMI (PMP). Pure in-memory-SQLite checks,
no real data/tracker.db involved. Run with:
    python -m unittest discover -s tests
"""

import sqlite3
import unittest
from datetime import datetime, timedelta

from ce_tracker.db import SCHEMA
from ce_tracker.portal_submit import (
    confirm_submission,
    is_auto_reporting_provider,
    isc2_credits,
    pending_reconciliation,
    ready_to_submit,
    record_submission,
    suggest_isc2_mapping,
    suggest_pmi_mapping,
)


class PortalSubmitTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def tearDown(self):
        self.conn.close()

    def _insert_activity(self, applies, date="2026-06-15", title="Executive Risk & Governance Summit", provider="Brightline Events", category=None):
        cur = self.conn.execute(
            "INSERT INTO activities (date, provider, title, hours, category, applies, status) "
            "VALUES (?, ?, ?, 6, ?, ?, 'logged')",
            (date, provider, title, category, applies),
        )
        return cur.lastrowid

    def test_excludes_activity_not_applying_to_credential(self):
        self._insert_activity("aigp")
        self.assertEqual(ready_to_submit(self.conn, "cissp"), [])

    def test_includes_not_yet_submitted_cissp_activity(self):
        activity_id = self._insert_activity("cissp")
        ready = ready_to_submit(self.conn, "cissp")
        self.assertEqual([r["id"] for r in ready], [activity_id])

    def test_excludes_activity_already_submitted(self):
        activity_id = self._insert_activity("cissp")
        record_submission(self.conn, activity_id, "cissp", source="automation", confirmation_id="ABC123")
        self.assertEqual(ready_to_submit(self.conn, "cissp"), [])

    def test_excludes_activity_already_portal_confirmed(self):
        activity_id = self._insert_activity("cissp")
        record_submission(self.conn, activity_id, "cissp", source="portal-existing")
        self.assertEqual(ready_to_submit(self.conn, "cissp"), [])

    def test_ledger_row_for_other_credential_does_not_affect_this_one(self):
        # Multi-applies activity: submitted to aigp only, still net-new for cissp.
        activity_id = self._insert_activity("cissp,aigp")
        record_submission(self.conn, activity_id, "aigp", source="manual")
        ready = ready_to_submit(self.conn, "cissp")
        self.assertEqual([r["id"] for r in ready], [activity_id])

    def test_pmp_ledger_isolated_from_cissp_on_same_multi_applies_activity(self):
        # Same shape the real DB exercises: one activity applying to both cissp and pmp,
        # already submitted to ISC2 — must still be net-new for PMP.
        activity_id = self._insert_activity("cissp,pmp")
        record_submission(self.conn, activity_id, "cissp", source="automation")
        self.assertEqual(ready_to_submit(self.conn, "cissp"), [])
        ready = ready_to_submit(self.conn, "pmp")
        self.assertEqual([r["id"] for r in ready], [activity_id])

    def test_record_submission_is_idempotent(self):
        activity_id = self._insert_activity("cissp")
        record_submission(self.conn, activity_id, "cissp", source="automation", confirmation_id="ABC123")
        record_submission(self.conn, activity_id, "cissp", source="automation", confirmation_id="ABC123")
        count = self.conn.execute(
            "SELECT COUNT(*) FROM activity_portal_submissions WHERE activity_id = ? AND credential_id = 'cissp'",
            (activity_id,),
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_portal_existing_source_records_portal_confirmed_status(self):
        activity_id = self._insert_activity("cissp")
        record_submission(self.conn, activity_id, "cissp", source="portal-existing")
        row = self.conn.execute(
            "SELECT status FROM activity_portal_submissions WHERE activity_id = ? AND credential_id = 'cissp'",
            (activity_id,),
        ).fetchone()
        self.assertEqual(row["status"], "portal_confirmed")

    def test_record_submission_stores_submitted_fields(self):
        activity_id = self._insert_activity("cissp")
        record_submission(self.conn, activity_id, "cissp", source="automation",
                           category="Education", cpe_type="Industry Conference",
                           domain="Security and Risk Management")
        row = self.conn.execute(
            "SELECT submitted_category, submitted_cpe_type, submitted_domain FROM activity_portal_submissions "
            "WHERE activity_id = ? AND credential_id = 'cissp'",
            (activity_id,),
        ).fetchone()
        self.assertEqual(row["submitted_category"], "Education")
        self.assertEqual(row["submitted_cpe_type"], "Industry Conference")
        self.assertEqual(row["submitted_domain"], "Security and Risk Management")

    def test_suggest_isc2_mapping_conference_title(self):
        activity = {"title": "Fieldstone Ventures Con - Tech and strategy", "category": None}
        mapping = suggest_isc2_mapping(activity)
        self.assertEqual(mapping["category"], "Education")
        self.assertEqual(mapping["cpe_type"], "Industry Conference")
        self.assertEqual(mapping["domain"], "Security Architecture and Engineering")

    def test_suggest_isc2_mapping_webinar_title(self):
        activity = {"title": "February Privacy Live Briefing: Law Updates", "category": None}
        mapping = suggest_isc2_mapping(activity)
        self.assertEqual(mapping["category"], "Education")
        self.assertEqual(mapping["cpe_type"], "Online webinars, podcasts and other online materials")

    def test_suggest_isc2_mapping_unmatched_returns_none(self):
        activity = {"title": "Some Unlabeled Activity", "category": None}
        mapping = suggest_isc2_mapping(activity)
        self.assertIsNone(mapping["category"])
        self.assertIsNone(mapping["cpe_type"])
        self.assertIsNone(mapping["domain"])

    def test_isc2_credits_rounds_down_to_quarter_hour(self):
        self.assertEqual(isc2_credits(6.33), 6.25)

    def test_isc2_credits_leaves_exact_quarter_hour_unchanged(self):
        self.assertEqual(isc2_credits(6.25), 6.25)
        self.assertEqual(isc2_credits(6.0), 6.0)

    def test_suggest_pmi_mapping_conference_title(self):
        activity = {"title": "PMI Chapter Leadership Summit", "category": None}
        mapping = suggest_pmi_mapping(activity)
        self.assertEqual(mapping["category"], "Education")
        self.assertEqual(mapping["cpe_type"], "Organization Meetings")
        self.assertEqual(mapping["domain"], "Power Skills")

    def test_suggest_pmi_mapping_webinar_title(self):
        activity = {"title": "On-Demand Webinar: Agile Roadmapping", "category": None}
        mapping = suggest_pmi_mapping(activity)
        self.assertEqual(mapping["category"], "Education")
        self.assertEqual(mapping["cpe_type"], "Online or Digital Media")
        self.assertEqual(mapping["domain"], "Business Acumen")

    def test_suggest_pmi_mapping_unmatched_returns_none(self):
        activity = {"title": "Some Unlabeled Activity", "category": None}
        mapping = suggest_pmi_mapping(activity)
        self.assertIsNone(mapping["category"])
        self.assertIsNone(mapping["cpe_type"])
        self.assertIsNone(mapping["domain"])

    def test_is_auto_reporting_provider_case_insensitive(self):
        self.assertTrue(is_auto_reporting_provider({"provider": "Northbridge Learning"}, "cissp"))
        self.assertTrue(is_auto_reporting_provider({"provider": "northbridge learning"}, "cissp"))
        self.assertFalse(is_auto_reporting_provider({"provider": "Fieldstone Ventures"}, "cissp"))
        self.assertFalse(is_auto_reporting_provider({"provider": None}, "cissp"))

    def test_is_auto_reporting_provider_scoped_per_credential(self):
        # ProjectManagement.com auto-reports to PMI, not ISC2 — same provider name must not
        # trip the ISC2 flag, and Northbridge Learning (an ISC2 auto-reporter) must not trip the PMP flag.
        self.assertTrue(is_auto_reporting_provider({"provider": "ProjectManagement.com"}, "pmp"))
        self.assertFalse(is_auto_reporting_provider({"provider": "ProjectManagement.com"}, "cissp"))
        self.assertFalse(is_auto_reporting_provider({"provider": "Northbridge Learning"}, "pmp"))

    def test_pending_reconciliation_excludes_recent_automation_submission(self):
        activity_id = self._insert_activity("cissp")
        record_submission(self.conn, activity_id, "cissp", source="automation")
        self.assertEqual(pending_reconciliation(self.conn, "cissp"), [])

    def test_pending_reconciliation_includes_old_automation_submission(self):
        activity_id = self._insert_activity("cissp")
        record_submission(self.conn, activity_id, "cissp", source="automation")
        old_timestamp = (datetime.now() - timedelta(minutes=90)).isoformat()
        self.conn.execute(
            "UPDATE activity_portal_submissions SET recorded_at = ? WHERE activity_id = ? AND credential_id = 'cissp'",
            (old_timestamp, activity_id),
        )
        self.conn.commit()
        due = pending_reconciliation(self.conn, "cissp")
        self.assertEqual([r["activity_id"] for r in due], [activity_id])

    def test_pending_reconciliation_excludes_portal_existing_source(self):
        activity_id = self._insert_activity("cissp")
        record_submission(self.conn, activity_id, "cissp", source="portal-existing")
        old_timestamp = (datetime.now() - timedelta(minutes=90)).isoformat()
        self.conn.execute(
            "UPDATE activity_portal_submissions SET recorded_at = ? WHERE activity_id = ? AND credential_id = 'cissp'",
            (old_timestamp, activity_id),
        )
        self.conn.commit()
        self.assertEqual(pending_reconciliation(self.conn, "cissp"), [])

    def test_confirm_submission_marks_portal_confirmed(self):
        activity_id = self._insert_activity("cissp")
        record_submission(self.conn, activity_id, "cissp", source="automation")
        confirm_submission(self.conn, activity_id, "cissp")
        row = self.conn.execute(
            "SELECT status FROM activity_portal_submissions WHERE activity_id = ? AND credential_id = 'cissp'",
            (activity_id,),
        ).fetchone()
        self.assertEqual(row["status"], "portal_confirmed")

    def test_confirm_submission_corrects_hours_and_title_on_mismatch(self):
        activity_id = self._insert_activity("cissp", title="AI Governance Symposium, Session")
        record_submission(self.conn, activity_id, "cissp", source="automation")
        confirm_submission(self.conn, activity_id, "cissp", portal_hours=4.0, portal_title="Cedar Valley Symposium")
        row = self.conn.execute("SELECT hours, title FROM activities WHERE id = ?", (activity_id,)).fetchone()
        self.assertEqual(row["hours"], 4.0)
        self.assertEqual(row["title"], "Cedar Valley Symposium")


if __name__ == "__main__":
    unittest.main()
