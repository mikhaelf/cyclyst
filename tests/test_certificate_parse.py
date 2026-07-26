"""Tests for ce_tracker.certificate_parse — pure regex/heuristic helpers, plus one
end-to-end check through a real (reportlab-generated) PDF for the pypdf integration.
No network calls, no fixture files on disk. Run with:
    python -m unittest discover -s tests
"""

import io
import unittest

from ce_tracker.certificate_parse import (
    UnsupportedFileType,
    _find_date,
    _find_hours,
    _find_provider,
    _find_title,
    extract_text,
    parse,
)


class _FakeFileStorage:
    def __init__(self, filename, stream):
        self.filename = filename
        self.stream = stream


class FindDateTests(unittest.TestCase):
    def test_iso_format(self):
        self.assertEqual(_find_date("Issued on 2026-06-15 to the attendee"), "2026-06-15")

    def test_month_day_year_with_comma(self):
        self.assertEqual(_find_date("Completed on June 15, 2026"), "2026-06-15")

    def test_day_month_year(self):
        self.assertEqual(_find_date("Completed on 15 June 2026"), "2026-06-15")

    def test_slash_format(self):
        self.assertEqual(_find_date("Date: 06/15/2026"), "2026-06-15")

    def test_no_date_found(self):
        self.assertEqual(_find_date("No date anywhere in this text"), "")


class FindHoursTests(unittest.TestCase):
    def test_cpe_unit(self):
        self.assertEqual(_find_hours("Awarded 20 CPE credits"), "20")

    def test_ceu_unit_decimal(self):
        self.assertEqual(_find_hours("1.5 CEUs earned"), "1.5")

    def test_falls_back_to_hours_word(self):
        self.assertEqual(_find_hours("A 3 hour session"), "3")

    def test_no_hours_found(self):
        self.assertEqual(_find_hours("No units mentioned here"), "")


class FindProviderTests(unittest.TestCase):
    def test_known_provider_matched(self):
        self.assertEqual(_find_provider("Issued by SANS Institute"), "SANS")

    def test_unknown_provider_returns_blank(self):
        self.assertEqual(_find_provider("Issued by Some Random Academy"), "")

    def test_word_boundary_avoids_partial_match(self):
        # "PMI" shouldn't match inside an unrelated longer token.
        self.assertEqual(_find_provider("Optimistic Training Co."), "")


class FindTitleTests(unittest.TestCase):
    def test_cue_phrase_picks_up_next_line(self):
        text = "SANS Institute\nJane Doe\nhas successfully completed\nAdvanced Threat Modeling\n"
        self.assertEqual(_find_title(text), "Advanced Threat Modeling")

    def test_no_cue_found_returns_blank(self):
        self.assertEqual(_find_title("Just some unrelated document text."), "")


class ExtractTextTests(unittest.TestCase):
    def test_rejects_non_pdf_filename(self):
        fake = _FakeFileStorage("cert.png", io.BytesIO(b"not a pdf"))
        with self.assertRaises(UnsupportedFileType):
            extract_text(fake)


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
            "SANS Institute",
            "Jane Doe",
            "has successfully completed",
            "Advanced Threat Modeling",
            "June 15, 2026",
            "20 CPE hours",
        ])
        result = parse(_FakeFileStorage("cert.pdf", pdf))
        self.assertEqual(result["date"], "2026-06-15")
        self.assertEqual(result["hours"], "20")
        self.assertEqual(result["provider"], "SANS")
        self.assertEqual(result["title"], "Advanced Threat Modeling")

    def test_blank_fields_when_nothing_recognizable(self):
        pdf = self._make_pdf(["Just some unrelated document text with no cert markers."])
        result = parse(_FakeFileStorage("cert.pdf", pdf))
        self.assertEqual(result, {"date": "", "hours": "", "provider": "", "title": ""})


if __name__ == "__main__":
    unittest.main()
