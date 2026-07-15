"""Tests for ce_tracker.calendar_ingest.score_event() and the fuzzy-duplicate helpers — pure
functions plus a couple of in-memory-SQLite checks, no real data/tracker.db involved. Run with:
    python -m unittest discover -s tests
"""

import sqlite3
import unittest

from ce_tracker.calendar_ingest import _find_duplicate, _title_similarity, score_event, stage_candidates
from ce_tracker.db import SCHEMA


def _event(summary, start, end, organizer_email=None, organizer_name=None, status=None, event_id="evt1"):
    """start=None builds an all-day event (`end` is treated as the all-day date string)."""
    if start:
        e = {"id": event_id, "summary": summary, "start": {"dateTime": start}, "end": {"dateTime": end}}
    else:
        e = {"id": event_id, "summary": summary, "start": {"date": end}, "end": {"date": end}}
    if organizer_email or organizer_name:
        e["organizer"] = {}
        if organizer_email:
            e["organizer"]["email"] = organizer_email
        if organizer_name:
            e["organizer"]["displayName"] = organizer_name
    if status:
        e["status"] = status
    return e


class ScoreEventTests(unittest.TestCase):
    def test_known_provider_title_match_is_unflagged(self):
        e = _event("Northbridge Learning Security Briefing", "2026-06-15T09:00:00-07:00", "2026-06-15T11:00:00-07:00")
        result = score_event(e)
        self.assertIsNotNone(result)
        self.assertEqual(result["provider"], "Northbridge Learning")
        self.assertEqual(result["hours_guess"], 2.0)
        self.assertFalse(result["flagged"])

    def test_organizer_domain_match_alone_still_flagged(self):
        # Domain match is a weaker signal than a title hit — any event organized through a
        # vendor's calendar system matches, including non-CE ones (a purely social "harbor
        # cruise" organized via a known vendor's domain is a real example this must not trust).
        e = _event(
            "Quarterly Update Call", "2026-06-15T09:00:00-07:00", "2026-06-15T10:00:00-07:00",
            organizer_email="events@isaca.org",
        )
        result = score_event(e)
        self.assertIsNotNone(result)
        self.assertEqual(result["provider"], "ISACA")
        self.assertTrue(result["flagged"])

    def test_non_ce_social_event_from_known_domain_is_flagged_not_trusted(self):
        e = _event(
            "Networking Reception", "2026-06-04T18:00:00-07:00", "2026-06-04T21:00:00-07:00",
            organizer_email="events@fieldstone ventures.com",
        )
        result = score_event(e)
        self.assertIsNotNone(result)  # still staged as a candidate...
        self.assertTrue(result["flagged"])  # ...but never silently trusted

    def test_generic_ce_keyword_without_provider_is_flagged(self):
        e = _event("Guest lecture at campus", "2026-06-15T09:00:00-07:00", "2026-06-15T10:30:00-07:00")
        result = score_event(e)
        self.assertIsNotNone(result)
        self.assertIsNone(result["provider"])
        self.assertTrue(result["flagged"])
        self.assertEqual(result["hours_guess"], 1.5)

    def test_vendor_demo_keyword_matches(self):
        e = _event("Vendor demo: new SIEM platform", "2026-06-15T13:00:00-07:00", "2026-06-15T14:00:00-07:00")
        result = score_event(e)
        self.assertIsNotNone(result)
        self.assertTrue(result["flagged"])

    def test_denylist_blocks_even_with_keyword_substring(self):
        # "lunch" is a CE keyword substring-adjacent term but plain "lunch" invites are noise.
        e = _event("Team lunch", "2026-06-15T12:00:00-07:00", "2026-06-15T13:00:00-07:00")
        self.assertIsNone(score_event(e))

    def test_no_signal_at_all_is_not_staged(self):
        e = _event("1:1 with manager", "2026-06-15T09:00:00-07:00", "2026-06-15T09:30:00-07:00")
        self.assertIsNone(score_event(e))

    def test_cancelled_event_is_skipped(self):
        e = _event(
            "ISC2 Security Congress", "2026-06-15T09:00:00-07:00", "2026-06-15T17:00:00-07:00",
            status="cancelled",
        )
        self.assertIsNone(score_event(e))

    def test_all_day_event_has_no_hours_guess_and_is_flagged(self):
        e = _event("PMI Global Summit", None, "2026-06-15")
        result = score_event(e)
        self.assertIsNotNone(result)
        self.assertIsNone(result["hours_guess"])
        self.assertTrue(result["flagged"])

    def test_outlier_duration_is_flagged_even_with_known_provider(self):
        e = _event("Northbridge Learning Multi-Day Bootcamp", "2026-06-15T09:00:00-07:00", "2026-06-17T17:00:00-07:00")
        result = score_event(e)
        self.assertIsNotNone(result)
        self.assertGreater(result["hours_guess"], 8)
        self.assertTrue(result["flagged"])

    def test_id_is_prefixed_for_dedup(self):
        e = _event("SANS Cloud Security Training", "2026-06-15T09:00:00-07:00", "2026-06-15T12:00:00-07:00", event_id="abc123")
        result = score_event(e)
        self.assertEqual(result["id"], "cal:abc123")


class DuplicateDetectionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def tearDown(self):
        self.conn.close()

    def test_title_similarity_is_high_for_near_identical_titles(self):
        score = _title_similarity("Executive Risk & Governance Summit", "Executive Risk & Governance Summit Conference")
        self.assertGreater(score, 0.6)

    def test_finds_duplicate_against_existing_activity_same_day_similar_title(self):
        self.conn.execute(
            "INSERT INTO activities (date, provider, title, hours, category, applies, status) "
            "VALUES ('2026-06-15', 'Brightline Events', 'Executive Risk & Governance Summit', 6, NULL, 'cissp', 'logged')"
        )
        candidate = {"id": "cal:xyz", "date": "2026-06-15", "title": "Executive Risk & Governance Summit Conference", "provider": "Brightline Events"}
        dup = _find_duplicate(self.conn, candidate)
        self.assertIsNotNone(dup)
        ref, score = dup
        self.assertEqual(ref, "activity:1")
        self.assertGreater(score, 0.6)

    def test_no_duplicate_when_dates_differ(self):
        self.conn.execute(
            "INSERT INTO activities (date, provider, title, hours, category, applies, status) "
            "VALUES ('2025-01-01', 'Brightline Events', 'Executive Risk & Governance Summit', 6, NULL, 'cissp', 'logged')"
        )
        candidate = {"id": "cal:xyz", "date": "2026-06-15", "title": "Executive Risk & Governance Summit Conference", "provider": "Brightline Events"}
        self.assertIsNone(_find_duplicate(self.conn, candidate))

    def test_stage_candidates_dedups_on_rerun(self):
        candidates = [
            {"id": "cal:evt1", "date": "2026-06-15", "provider": "Northbridge Learning", "title": "Northbridge Learning Briefing",
             "hours_guess": 2.0, "source": "calendar", "flagged": False}
        ]
        first = stage_candidates(self.conn, candidates, "2026-06-01", "2026-06-30", events_seen=10)
        self.assertEqual(first["candidates_staged"], 1)

        second = stage_candidates(self.conn, candidates, "2026-06-01", "2026-06-30", events_seen=10)
        self.assertEqual(second["candidates_staged"], 0)

        count = self.conn.execute("SELECT COUNT(*) FROM pending_activities").fetchone()[0]
        self.assertEqual(count, 1)

    def test_stage_candidates_logs_run(self):
        stage_candidates(self.conn, [], "2026-06-01", "2026-06-30", events_seen=5)
        run = self.conn.execute("SELECT * FROM calendar_ingest_runs").fetchone()
        self.assertIsNotNone(run)
        self.assertEqual(run["events_seen"], 5)
        self.assertEqual(run["candidates_staged"], 0)


if __name__ == "__main__":
    unittest.main()
