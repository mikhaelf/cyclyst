"""Tests for ce_tracker.agenda_parse. Cases are drawn from two real event agendas (a
multi-day conference, Meridian Security-style: no year, explicit time ranges; a single-day executive
retreat, Fieldstone Ventures-style: year included, start-time-only) to keep both the denylist and the
date/time parsing evidence-based rather than speculative. Run with:
    python -m unittest discover -s tests
"""

import io
import unittest
from datetime import date

from ce_tracker.agenda_parse import (
    extract_sessions,
    extract_text,
    is_non_content,
    parse,
    summarize,
)


class _FakeFileStorage:
    def __init__(self, filename, stream):
        self.filename = filename
        self.stream = stream


class DeniedLinesTests(unittest.TestCase):
    def test_meals(self):
        for line in [
            "Lunch",
            "Farewell Lunch",
            "Welcome Reception & Dinner",
            "Breakfast & Networking",
            "Hosted Coastal Lunch Experience",
        ]:
            with self.subTest(line=line):
                self.assertTrue(is_non_content(line))

    def test_socializing(self):
        for line in [
            "Cocktail Reception & Coastal Bites",
            "Welcome Reception & Dinner",
        ]:
            with self.subTest(line=line):
                self.assertTrue(is_non_content(line))

    def test_travel_and_checkin(self):
        for line in [
            "Arrivals & Check-in",
            "Guest Check in",
            "Departure for Evening Reception & Dinner at Silver Oak Winery",
        ]:
            with self.subTest(line=line):
                self.assertTrue(is_non_content(line))

    def test_wellness_and_recreation(self):
        for line in [
            "Morning Yoga Workout",
            "Morning Pilates Workout",
            "Afternoon Activities",
        ]:
            with self.subTest(line=line):
                self.assertTrue(is_non_content(line))

    def test_breaks_and_event_boundaries(self):
        for line in [
            "Break",
            "Event Concludes",
            "Event Concludes -Evening at Your Leisure",
        ]:
            with self.subTest(line=line):
                self.assertTrue(is_non_content(line))


class NotDeniedLinesTests(unittest.TestCase):
    def test_real_sessions_pass_through(self):
        for line in [
            "Opening Fireside Chat: Daniel Reyes, Co-founder & CEO, Meridian Security & "
            "Michael Chen, CEO, Cloud Provider PurpleFastFollow",
            "An Optimistic Outlook on the AI Arms Race. Noah Bennett, Co-founder & "
            "VP Product, Meridian Security",
            "Industry Spotlight Panel: Harnessing AI and Eliminating Exposure to AI Threats",
            "Building Defensive AI at the Frontier: Lessons from Anthropic's Mythos",
            "Session I: Straight from the Top: Fieldstone Ventures's CEO Panel with Robert Hale",
            "Session II: Frontline Decisions: Cybersecurity at the Executive Level "
            "(Identity + AI)",
            "Session III: Client Perspective & Open Discussion",
        ]:
            with self.subTest(line=line):
                self.assertFalse(is_non_content(line))

    def test_remarks_deliberately_not_denied(self):
        # Whether brief ceremonial remarks count as CE is a judgment call, not a logistics
        # fact -- left to a human, not auto-denied by this classifier.
        for line in ["Welcoming Remarks", "Opening Remarks", "Closing Remarks"]:
            with self.subTest(line=line):
                self.assertFalse(is_non_content(line))

    def test_breakout_session_not_confused_with_break(self):
        # Word-boundary matching on "break" must not catch "breakout".
        self.assertFalse(is_non_content("Breakout Session: Zero Trust Deep Dive"))


class ExtractSessionsWizStyleTests(unittest.TestCase):
    """No-year day headers, explicit start-end time ranges."""

    def test_requires_default_year(self):
        text = "Monday | June 22\n12:00 PM – 4:00 PM Arrivals & Check-in\n"
        sessions, year_needed = extract_sessions(text, default_year=None)
        self.assertEqual(sessions, [])
        self.assertTrue(year_needed)

    def test_resolves_dates_with_default_year(self):
        text = (
            "Tuesday | June 23\n"
            "8:40 AM – 9:30 AM Opening Fireside Chat: Daniel Reyes, Co-founder & CEO, Meridian Security\n"
            "9:30 AM – 10:30 AM An Optimistic Outlook on the AI Arms Race\n"
        )
        sessions, year_needed = extract_sessions(text, default_year=2026)
        self.assertFalse(year_needed)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0]["date"], date(2026, 6, 23))
        self.assertEqual(sessions[0]["start_minutes"], 8 * 60 + 40)
        self.assertEqual(sessions[0]["end_minutes"], 9 * 60 + 30)
        self.assertEqual(
            sessions[0]["description"],
            "Opening Fireside Chat: Daniel Reyes, Co-founder & CEO, Meridian Security",
        )

    def test_wrapped_continuation_line_merges_into_description(self):
        # Regression: pypdf sometimes extracts a time-range with no trailing text on its own
        # line, pushing the actual description onto the next (unprefixed) line. The range
        # regex must still match (empty description), then the continuation line merges in --
        # not silently mis-parsed as a single-time entry that swallows the second timestamp
        # as literal description text.
        text = (
            "Tuesday | June 23\n"
            "11:30 AM – 12:00 PM\n"
            "Industry Spotlight Panel: The Future of Code Security\n"
        )
        sessions, _ = extract_sessions(text, default_year=2026)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["start_minutes"], 11 * 60 + 30)
        self.assertEqual(sessions[0]["end_minutes"], 12 * 60)
        self.assertEqual(
            sessions[0]["description"], "Industry Spotlight Panel: The Future of Code Security"
        )
        self.assertNotIn("12:00", sessions[0]["description"])


class ExtractSessionsOptivStyleTests(unittest.TestCase):
    """Day headers with year included, bulleted start-time-only entries."""

    def test_resolves_dates_without_default_year(self):
        text = (
            "Thursday, June 4, 2026\n"
            "• 9:00 a.m. - Session I: Straight from the Top\n"
            "• 10:30 a.m. - Session II: Frontline Decisions\n"
            "• 12:00 p.m. - Hosted Coastal Lunch Experience\n"
        )
        sessions, year_needed = extract_sessions(text, default_year=None)
        self.assertFalse(year_needed)
        self.assertEqual(len(sessions), 3)
        self.assertEqual(sessions[0]["date"], date(2026, 6, 4))
        self.assertEqual(sessions[0]["start_minutes"], 9 * 60)
        self.assertIsNone(sessions[0]["end_minutes"])  # no explicit end -- inferred later

    def test_last_session_of_day_has_no_next_line_to_infer_from(self):
        text = (
            "Thursday, June 4, 2026\n"
            "• 1:00 p.m. - Session III: Client Perspective & Open Discussion\n"
        )
        sessions, _ = extract_sessions(text, default_year=None)
        summary = summarize(sessions)
        self.assertEqual(summary["unresolved_count"], 1)
        self.assertEqual(summary["included_count"], 0)
        self.assertIn("Skipped (duration unknown)", summary["breakdown"])


class ResolveDurationsAndSummarizeTests(unittest.TestCase):
    def test_infers_duration_from_next_session_start(self):
        text = (
            "Thursday, June 4, 2026\n"
            "• 9:00 a.m. - Session I: Straight from the Top\n"
            "• 10:30 a.m. - Session II: Frontline Decisions\n"
            "• 12:00 p.m. - Hosted Coastal Lunch Experience\n"
            "• 1:00 p.m. - Session III: Client Perspective & Open Discussion\n"
            "• 4:00 p.m. - Cocktail Reception & Coastal Bites\n"
        )
        sessions, _ = extract_sessions(text, default_year=None)
        summary = summarize(sessions)
        # Session I: 9:00-10:30 (1.5h), Session II: 10:30-12:00 (1.5h), lunch excluded,
        # Session III: 1:00-4:00 (3h), reception excluded. Total content: 6.0h.
        self.assertEqual(summary["hours"], 6.0)
        self.assertEqual(summary["included_count"], 3)
        self.assertEqual(summary["excluded_count"], 2)
        self.assertEqual(summary["date"], date(2026, 6, 4))

    def test_denied_sessions_excluded_from_total_regardless_of_duration(self):
        text = (
            "Monday | June 22\n"
            "12:30 PM – 3:00 PM Lunch\n"
            "6:30 PM Welcome Reception & Dinner\n"
        )
        sessions, _ = extract_sessions(text, default_year=2026)
        summary = summarize(sessions)
        self.assertEqual(summary["hours"], 0)
        self.assertEqual(summary["included_count"], 0)
        self.assertEqual(summary["excluded_count"], 2)
        # A denied session with no explicit/inferable end time still counts as excluded,
        # not unresolved -- its duration is irrelevant once denied.
        self.assertEqual(summary["unresolved_count"], 0)


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

    def test_wiz_style_end_to_end_with_year(self):
        pdf = self._make_pdf([
            "Tuesday | June 23",
            "8:40 AM - 9:30 AM Opening Fireside Chat",
            "9:30 AM - 10:30 AM An Optimistic Outlook on the AI Arms Race",
            "11:00 AM - 11:30 AM Break",
        ])
        candidate, error = parse(_FakeFileStorage("Meridian Security Agenda.pdf", pdf), default_year=2026)
        self.assertIsNone(error)
        self.assertEqual(candidate["hours_guess"], 1.83)  # 50min (0.83h) + 60min (1.0h)
        self.assertEqual(candidate["date"], "2026-06-23")
        self.assertEqual(candidate["title"], "Meridian Security Agenda")
        self.assertIsNone(candidate["provider"])
        self.assertEqual(candidate["applies"], "")
        self.assertEqual(candidate["source"], "agenda_pdf")

    def test_wiz_style_without_year_returns_specific_error(self):
        pdf = self._make_pdf([
            "Tuesday | June 23",
            "8:40 AM - 9:30 AM Opening Fireside Chat",
        ])
        candidate, error = parse(_FakeFileStorage("Meridian Security Agenda.pdf", pdf), default_year=None)
        self.assertIsNone(candidate)
        self.assertIn("year", error)

    def test_optiv_style_end_to_end_no_year_needed(self):
        pdf = self._make_pdf([
            "Thursday, June 4, 2026",
            "9:00 a.m. - Session I: Straight from the Top",
            "10:30 a.m. - Session II: Frontline Decisions",
            "12:00 p.m. - Hosted Coastal Lunch Experience",
        ])
        candidate, error = parse(_FakeFileStorage("Fieldstone Ventures Retreat.pdf", pdf), default_year=None)
        self.assertIsNone(error)
        self.assertEqual(candidate["hours_guess"], 3.0)  # two 1.5h sessions
        self.assertEqual(candidate["date"], "2026-06-04")
        self.assertEqual(candidate["title"], "Fieldstone Ventures Retreat")

    def test_no_countable_sessions_returns_error(self):
        pdf = self._make_pdf(["Just some unrelated document text with no agenda markers."])
        candidate, error = parse(_FakeFileStorage("random.pdf", pdf), default_year=2026)
        self.assertIsNone(candidate)
        self.assertEqual(error, "No countable sessions found in this agenda.")


if __name__ == "__main__":
    unittest.main()
