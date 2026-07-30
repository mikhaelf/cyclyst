"""Tests for ce_tracker.run_summary -- the "since your last run" banner. In-memory SQLite,
no real data/tracker.db involved. Run with:
    python -m unittest discover -s tests
"""

import sqlite3
import unittest
from datetime import datetime

from ce_tracker import run_summary
from ce_tracker.db import SCHEMA


def _insert_credential(conn, credential_id, name, issuer):
    conn.execute(
        "INSERT INTO credentials (id, name, issuer, status) VALUES (?, ?, ?, 'on-track')",
        (credential_id, name, issuer),
    )


def _insert_activity(conn, applies="cissp"):
    conn.execute(
        "INSERT INTO activities (date, provider, title, hours, category, applies, status) "
        "VALUES ('2026-01-01', 'Provider', 'Title', 2.0, NULL, ?, 'logged')",
        (applies,),
    )
    return conn.execute("SELECT id FROM activities ORDER BY id DESC LIMIT 1").fetchone()["id"]


def _insert_submission(conn, activity_id, credential_id, recorded_at):
    conn.execute(
        "INSERT INTO activity_portal_submissions (activity_id, credential_id, status, recorded_at) "
        "VALUES (?, ?, 'submitted', ?)",
        (activity_id, credential_id, recorded_at),
    )


class RunSummaryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def tearDown(self):
        self.conn.close()

    def test_previous_run_is_none_on_first_run_ever(self):
        run_summary.record_app_start(self.conn, now=datetime(2026, 7, 1, 9, 0, 0))
        self.assertIsNone(run_summary.previous_run_started_at(self.conn))

    def test_previous_run_is_the_run_before_the_current_one(self):
        run_summary.record_app_start(self.conn, now=datetime(2026, 7, 1, 9, 0, 0))
        run_summary.record_app_start(self.conn, now=datetime(2026, 7, 2, 9, 0, 0))
        run_summary.record_app_start(self.conn, now=datetime(2026, 7, 3, 9, 0, 0))
        self.assertEqual(run_summary.previous_run_started_at(self.conn), "2026-07-02T09:00:00")

    def test_submissions_since_none_cutoff_returns_nothing(self):
        self.assertEqual(run_summary.submissions_since(self.conn, None), [])

    def test_submissions_since_filters_and_groups_by_credential(self):
        _insert_credential(self.conn, "cissp", "CISSP", "ISC2")
        _insert_credential(self.conn, "pmp", "PMP", "PMI")
        a1 = _insert_activity(self.conn)
        a2 = _insert_activity(self.conn)
        a3 = _insert_activity(self.conn, applies="pmp")
        self.conn.commit()

        _insert_submission(self.conn, a1, "cissp", "2026-07-01T10:00:00")  # before cutoff
        _insert_submission(self.conn, a2, "cissp", "2026-07-05T10:00:00")  # after cutoff
        _insert_submission(self.conn, a3, "pmp", "2026-07-06T10:00:00")    # after cutoff
        self.conn.commit()

        result = run_summary.submissions_since(self.conn, "2026-07-02T00:00:00")
        by_id = {r["credential_id"]: r for r in result}
        self.assertEqual(set(by_id), {"cissp", "pmp"})
        self.assertEqual(by_id["cissp"]["count"], 1)
        self.assertEqual(by_id["cissp"]["issuer"], "ISC2")
        self.assertEqual(by_id["pmp"]["count"], 1)

    def test_dismiss_only_affects_the_latest_run(self):
        run_summary.record_app_start(self.conn, now=datetime(2026, 7, 1, 9, 0, 0))
        run_summary.record_app_start(self.conn, now=datetime(2026, 7, 2, 9, 0, 0))
        self.assertFalse(run_summary.is_current_run_dismissed(self.conn))

        run_summary.dismiss_current_run(self.conn)
        self.assertTrue(run_summary.is_current_run_dismissed(self.conn))

        # A fresh run is not dismissed by default.
        run_summary.record_app_start(self.conn, now=datetime(2026, 7, 3, 9, 0, 0))
        self.assertFalse(run_summary.is_current_run_dismissed(self.conn))

    def test_summary_enabled_defaults_true_and_can_be_toggled(self):
        self.assertTrue(run_summary.summary_enabled(self.conn))

        run_summary.set_summary_enabled(self.conn, False)
        self.assertFalse(run_summary.summary_enabled(self.conn))

        # Toggling again exercises the upsert's UPDATE path, not just INSERT.
        run_summary.set_summary_enabled(self.conn, True)
        self.assertTrue(run_summary.summary_enabled(self.conn))


if __name__ == "__main__":
    unittest.main()
