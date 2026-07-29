"""Tests for ce_tracker.agenda_parse — the deny-rule-only logistics classifier. Cases are
drawn from two real event agendas (a multi-day conference and a single-day executive
retreat) to keep the denylist evidence-based rather than speculative. Run with:
    python -m unittest discover -s tests
"""

import unittest

from ce_tracker.agenda_parse import is_non_content


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


if __name__ == "__main__":
    unittest.main()
