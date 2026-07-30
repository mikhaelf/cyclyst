"""Tests for ce_tracker.retention -- archiving (never deleting) old activities. In-memory
SQLite, no real data/tracker.db involved, and CSV backups are redirected to a temp directory
so tests never write into this repo's data/archives/. Run with:
    python -m unittest discover -s tests
"""

import csv
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from ce_tracker import retention
from ce_tracker.db import SCHEMA


def _insert_activity(conn, activity_date, title="Test Activity", archived=0):
    conn.execute(
        "INSERT INTO activities (date, provider, title, hours, category, applies, status, archived) "
        "VALUES (?, 'Provider', ?, 2.0, NULL, 'cissp', 'logged', ?)",
        (activity_date, title, archived),
    )


class CutoffDateTests(unittest.TestCase):
    def test_cutoff_is_n_years_before_today(self):
        self.assertEqual(retention.cutoff_date(5, today=date(2026, 7, 30)), "2021-07-30")

    def test_leap_day_falls_back_to_feb_28_on_non_leap_target_year(self):
        # 2024 is a leap year, 2023 (one year back) is not -- same edge case db.py's cycle
        # backfill handles.
        self.assertEqual(retention.cutoff_date(1, today=date(2024, 2, 29)), "2023-02-28")


class ArchiveActivitiesTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.tmpdir = tempfile.TemporaryDirectory()
        self.patcher = patch.object(retention, "ARCHIVE_DIR", Path(self.tmpdir.name))
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmpdir.cleanup()
        self.conn.close()

    def test_archives_only_rows_older_than_cutoff(self):
        _insert_activity(self.conn, "2015-01-01", title="Old")
        _insert_activity(self.conn, "2026-01-01", title="Recent")
        self.conn.commit()

        # today defaults to date.today() inside cutoff_date -- 5 years back is well before
        # 2026-01-01 regardless of when this test runs, and well after 2015-01-01.
        cutoff_iso, backup_path, count = retention.archive_activities(self.conn, 5)
        self.assertEqual(count, 1)
        self.assertIsNotNone(backup_path)

        rows = {r["title"]: bool(r["archived"]) for r in self.conn.execute("SELECT title, archived FROM activities")}
        self.assertTrue(rows["Old"])
        self.assertFalse(rows["Recent"])

    def test_cutoff_boundary_is_exclusive_of_the_cutoff_date_itself(self):
        cutoff_iso = retention.cutoff_date(5)
        _insert_activity(self.conn, cutoff_iso, title="OnCutoff")
        self.conn.commit()

        _, _, count = retention.archive_activities(self.conn, 5)
        self.assertEqual(count, 0)
        row = self.conn.execute("SELECT archived FROM activities WHERE title = 'OnCutoff'").fetchone()
        self.assertFalse(bool(row["archived"]))

    def test_already_archived_rows_are_not_recounted(self):
        _insert_activity(self.conn, "2015-01-01", title="Old", archived=1)
        self.conn.commit()

        _, backup_path, count = retention.archive_activities(self.conn, 5)
        self.assertEqual(count, 0)
        self.assertIsNone(backup_path)

    def test_nothing_to_archive_writes_no_backup_file(self):
        _insert_activity(self.conn, "2026-01-01", title="Recent")
        self.conn.commit()

        _, backup_path, count = retention.archive_activities(self.conn, 5)
        self.assertEqual(count, 0)
        self.assertIsNone(backup_path)
        self.assertEqual(list(Path(self.tmpdir.name).iterdir()), [])

    def test_backup_csv_contains_the_archived_rows(self):
        _insert_activity(self.conn, "2015-01-01", title="Old One")
        self.conn.commit()

        _, backup_path, count = retention.archive_activities(self.conn, 5)
        self.assertEqual(count, 1)
        with open(backup_path, newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Old One")
        self.assertEqual(rows[0]["date"], "2015-01-01")

    def test_unarchive_clears_the_flag(self):
        _insert_activity(self.conn, "2015-01-01", title="Old", archived=1)
        self.conn.commit()
        activity_id = self.conn.execute("SELECT id FROM activities").fetchone()["id"]

        retention.unarchive_activity(self.conn, activity_id)

        row = self.conn.execute("SELECT archived FROM activities WHERE id = ?", (activity_id,)).fetchone()
        self.assertFalse(bool(row["archived"]))


if __name__ == "__main__":
    unittest.main()
