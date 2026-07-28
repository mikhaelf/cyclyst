"""Tests for ce_tracker.portal_submit — the ISC2 net-new/duplicate-prevention ledger
(design doc §7a). Pure in-memory-SQLite checks, no real data/tracker.db involved. Run with:
    python -m unittest discover -s tests
"""

import sqlite3
import unittest
from datetime import datetime, timedelta

from ce_tracker.db import SCHEMA
from ce_tracker.portal_submit import (
    confirm_submission,
    is_auto_reporting_provider,
    pending_reconciliation,
    ready_to_submit,
    record_submission,
    suggest_isc2_mapping,
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

    def test_record_submission_stores_isc2_fields(self):
        activity_id = self._insert_activity("cissp")
        record_submission(self.conn, activity_id, "cissp", source="automation",
                           isc2_category="Education", isc2_cpe_type="Industry Conference",
                           isc2_domain="Security and Risk Management")
        row = self.conn.execute(
            "SELECT isc2_category, isc2_cpe_type, isc2_domain FROM activity_portal_submissions "
            "WHERE activity_id = ? AND credential_id = 'cissp'",
            (activity_id,),
        ).fetchone()
        self.assertEqual(row["isc2_category"], "Education")
        self.assertEqual(row["isc2_cpe_type"], "Industry Conference")
        self.assertEqual(row["isc2_domain"], "Security and Risk Management")

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

    def test_is_auto_reporting_provider_case_insensitive(self):
        self.assertTrue(is_auto_reporting_provider({"provider": "Northbridge Learning"}))
        self.assertTrue(is_auto_reporting_provider({"provider": "northbridge learning"}))
        self.assertFalse(is_auto_reporting_provider({"provider": "Fieldstone Ventures"}))
        self.assertFalse(is_auto_reporting_provider({"provider": None}))

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
        confirm_submission(self.conn, activity_id, "cissp", isc2_hours=4.0, isc2_title="Cedar Valley Symposium")
        row = self.conn.execute("SELECT hours, title FROM activities WHERE id = ?", (activity_id,)).fetchone()
        self.assertEqual(row["hours"], 4.0)
        self.assertEqual(row["title"], "Cedar Valley Symposium")


if __name__ == "__main__":
    unittest.main()
