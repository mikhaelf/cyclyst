"""Tests for ce_tracker.transcript_parse — pure row-extraction over already-extracted text
(including a regression fixture modeled on ISC2's "Official CPE Transcript" export format),
plus stage_batch's dedup/auto-skip behavior over an in-memory database. No network calls, no
fixture files on disk — PDFs used for the pypdf-integration checks are generated on the fly
with reportlab, same approach as test_certificate_parse.py. Run with:
    python -m unittest discover -s tests
"""

import io
import sqlite3
import unittest

from ce_tracker.db import SCHEMA
from ce_tracker.transcript_parse import extract_rows, parse, stage_batch

# A two-table excerpt modeled on an ISC2 transcript export's raw pypdf text extraction — used
# as a regression fixture so future regex changes can't silently break parsing of the shape
# this feature was built and validated against.
REAL_ISC2_EXCERPT = """Group A Credits (minimum 20)
Activity Date Activity Description CPE Type Credit
3-Dec-2025 Cedar Valley Symposium Online webinars, podcasts and other
online materials
5.00
22-Aug-2024 Purple Fun Con 2024 Industry Conference 12.00
6-May-2024 2024 ISACA Spring Conference Industry Conference 8.00
Total  Credits: Group A 25.00
Certification awarded on Feb 9, 2006
Start date: Jun 1, 2024
End date: May 31, 2027
CPE progress
30% Satisfied
Group A Credits (minimum 90)
Activity Date Activity Description CPE Type Credit
1-Jun-2024 Rollover - Term ending May 31, 2027 CPE Rollover 3.00
3-Dec-2025 Cedar Valley Symposium Online webinars, podcasts and
other online materials
5.00
22-Aug-2024 Purple Fun Con 2024 Industry Conference 12.00
6-May-2024 2024 ISACA Spring Conference Industry Conference 8.00
Total  Credits: Group A 28.00
Candidate and Member Services
"""


class _FakeFileStorage:
    def __init__(self, filename, stream):
        self.filename = filename
        self.stream = stream


class ExtractRowsTests(unittest.TestCase):
    def test_parses_a_single_row(self):
        text = (
            "Group A Credits (minimum 20)\n"
            "Activity Date Activity Description CPE Type Credit\n"
            "15-Jun-2026 Test Webinar Online webinars, podcasts and other\n"
            "online materials\n"
            "2.00\n"
            "Total  Credits: Group A 2.00\n"
        )
        candidates, rollovers = extract_rows(text)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(rollovers, [])
        c = candidates[0]
        self.assertEqual(c["date"], "2026-06-15")
        self.assertEqual(c["title"], "Test Webinar")
        self.assertEqual(c["hours_guess"], 2.0)
        self.assertEqual(c["category"], "Group A")
        self.assertEqual(c["applies"], "")  # never guessed from PDF layout — human fills in at review
        self.assertEqual(c["source"], "transcript_pdf")

    def test_rollover_row_excluded_from_candidates(self):
        text = (
            "Group A Credits (minimum 90)\n"
            "Activity Date Activity Description CPE Type Credit\n"
            "1-Apr-2024 Rollover - Term ending Mar 31, 2027 CPE Rollover 2.00\n"
            "Total  Credits: Group A 2.00\n"
        )
        candidates, rollovers = extract_rows(text)
        self.assertEqual(candidates, [])
        self.assertEqual(len(rollovers), 1)
        self.assertEqual(rollovers[0]["hours"], 2.0)
        self.assertEqual(rollovers[0]["cpe_type"], "CPE Rollover")

    def test_real_isc2_excerpt_merges_shared_rows_and_excludes_rollover(self):
        # Same 3 activities printed once under a "minimum 20" table and once under a
        # "minimum 90" table (an ISC2 concentration sharing the base credential's Group A
        # pool) — must collapse to 3 unique candidates, not 6, and the Rollover row must not
        # become a fourth.
        candidates, rollovers = extract_rows(REAL_ISC2_EXCERPT)
        titles = sorted(c["title"] for c in candidates)
        self.assertEqual(titles, ["2024 ISACA Spring Conference", "Cedar Valley Symposium", "Purple Fun Con 2024"])
        self.assertEqual(len(rollovers), 1)
        self.assertEqual(rollovers[0]["title"], "Rollover - Term ending May 31, 2027")

        cedar_valley = next(c for c in candidates if c["title"] == "Cedar Valley Symposium")
        self.assertEqual(cedar_valley["hours_guess"], 5.0)
        self.assertEqual(cedar_valley["date"], "2025-12-03")


class ParseEndToEndTests(unittest.TestCase):
    def _make_pdf(self, lines):
        from reportlab.pdfgen import canvas

        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        y = 750
        for line in lines:
            c.drawString(72, y, line)
            y -= 20
        c.save()
        buf.seek(0)
        return buf

    def test_parses_known_fields_from_generated_pdf(self):
        pdf = self._make_pdf([
            "Group A Credits (minimum 20)",
            "Activity Date Activity Description CPE Type Credit",
            "15-Jun-2026 Test Webinar Industry Conference 2.00",
            "Total  Credits: Group A 2.00",
        ])
        candidates, rollovers = parse(_FakeFileStorage("transcript.pdf", pdf))
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["title"], "Test Webinar")
        self.assertEqual(candidates[0]["hours_guess"], 2.0)
        self.assertEqual(rollovers, [])


class StageBatchTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def tearDown(self):
        self.conn.close()

    def _pdf(self, lines):
        from reportlab.pdfgen import canvas

        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        y = 750
        for line in lines:
            c.drawString(72, y, line)
            y -= 20
        c.save()
        buf.seek(0)
        return buf

    def _transcript_pdf(self, filename, date_line, credit_line, header_extra=None):
        lines = ["Group A Credits (minimum 20)", "Activity Date Activity Description CPE Type Credit", date_line]
        if credit_line:
            lines.append(credit_line)
        lines.append("Total  Credits: Group A 0.00")
        return _FakeFileStorage(filename, self._pdf(lines))

    def test_new_candidate_gets_staged(self):
        pdf = self._transcript_pdf("t.pdf", "1-Jul-2026 Fresh Webinar Industry Conference", "2.00")
        summary = stage_batch(self.conn, [pdf])
        self.assertEqual(len(summary["staged"]), 1)
        count = self.conn.execute("SELECT COUNT(*) FROM pending_activities").fetchone()[0]
        self.assertEqual(count, 1)

    def test_near_exact_match_against_existing_pending_is_skipped_not_staged(self):
        self.conn.execute(
            "INSERT INTO pending_activities (id, date, provider, title, hours_guess, applies, source, flagged, status) "
            "VALUES ('isc2:2026-07-01:test-webinar', '2026-07-01', NULL, 'Test Webinar', 2.0, '', 'isc2_portal', 0, 'pending')"
        )
        pdf = self._transcript_pdf("t.pdf", "1-Jul-2026 Test Webinar Industry Conference", "2.00")
        summary = stage_batch(self.conn, [pdf])
        self.assertEqual(summary["staged"], [])
        self.assertEqual(len(summary["skipped_exact_duplicates"]), 1)
        # nothing new was added — the pre-existing row is still the only one
        count = self.conn.execute("SELECT COUNT(*) FROM pending_activities").fetchone()[0]
        self.assertEqual(count, 1)

    def test_fuzzy_match_is_staged_and_flagged_not_skipped(self):
        self.conn.execute(
            "INSERT INTO pending_activities (id, date, provider, title, hours_guess, applies, source, flagged, status) "
            "VALUES ('cal:xyz', '2026-06-15', NULL, 'Cloud Security Symposium 2026', 2.0, '', 'calendar', 0, 'pending')"
        )
        pdf = self._transcript_pdf("t.pdf", "15-Jun-2026 Cloud Security Symposium Industry Conference", "2.50")
        summary = stage_batch(self.conn, [pdf])
        self.assertEqual(len(summary["staged"]), 1)
        self.assertEqual(len(summary["flagged_possible_duplicates"]), 1)
        row = self.conn.execute(
            "SELECT * FROM pending_activities WHERE source = 'transcript_pdf'"
        ).fetchone()
        self.assertEqual(row["flagged"], 1)
        self.assertIsNotNone(row["duplicate_of"])

    def test_same_activity_in_two_uploaded_files_only_stages_once(self):
        pdf_a = self._transcript_pdf("a.pdf", "1-Jul-2026 Shared Event Industry Conference", "3.00")
        pdf_b = self._transcript_pdf("b.pdf", "1-Jul-2026 Shared Event Industry Conference", "3.00")
        summary = stage_batch(self.conn, [pdf_a, pdf_b])
        self.assertEqual(len(summary["staged"]), 1)
        count = self.conn.execute("SELECT COUNT(*) FROM pending_activities").fetchone()[0]
        self.assertEqual(count, 1)

    def test_rollover_rows_reported_but_not_staged(self):
        pdf = self._transcript_pdf(
            "t.pdf", "1-Apr-2024 Rollover - Term ending Mar 31, 2027 CPE Rollover", "2.00"
        )
        summary = stage_batch(self.conn, [pdf])
        self.assertEqual(summary["staged"], [])
        self.assertEqual(len(summary["rollover_rows"]), 1)
        count = self.conn.execute("SELECT COUNT(*) FROM pending_activities").fetchone()[0]
        self.assertEqual(count, 0)

    def test_unreadable_file_is_reported_not_raised(self):
        bad = _FakeFileStorage("not-a-pdf.pdf", io.BytesIO(b"not a pdf"))
        summary = stage_batch(self.conn, [bad])
        self.assertEqual(summary["file_errors"], ["not-a-pdf.pdf"])
        self.assertEqual(summary["staged"], [])


if __name__ == "__main__":
    unittest.main()
