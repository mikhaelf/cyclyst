"""Tests for ce_tracker.portal_submit — the ISC2 net-new/duplicate-prevention ledger
(design doc §7a). Pure in-memory-SQLite checks, no real data/tracker.db involved. Run with:
    python -m unittest discover -s tests
"""

import sqlite3
import unittest

from ce_tracker.db import SCHEMA
from ce_tracker.portal_submit import ready_to_submit, record_submission


class PortalSubmitTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def tearDown(self):
        self.conn.close()

    def _insert_activity(self, applies, date="2026-06-15", title="Executive Risk & Governance Summit"):
        cur = self.conn.execute(
            "INSERT INTO activities (date, provider, title, hours, category, applies, status) "
            "VALUES (?, 'Brightline Events', ?, 6, NULL, ?, 'logged')",
            (date, title, applies),
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


if __name__ == "__main__":
    unittest.main()
