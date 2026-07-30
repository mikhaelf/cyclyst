"""Tests for ce_tracker.profile_sync — matching/diff logic plus the two single-record write
helpers. Pure in-memory-SQLite checks, no real data/tracker.db involved. Run with:
    python -m unittest discover -s tests
"""

import sqlite3
import unittest

from ce_tracker.db import SCHEMA, _migrate_credential_columns
from ce_tracker.profile_sync import (
    _badge_id,
    apply_link_fix,
    diff_badges,
    insert_discovered_credential,
    suggested_new_credential_fields,
)


def _badge(name, issuer, url, platform="credly", issued_date=None, expires_date=None):
    return {
        "platform": platform, "name": name, "issuer": issuer, "url": url,
        "issued_date": issued_date, "expires_date": expires_date,
    }


class ProfileSyncTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        _migrate_credential_columns(self.conn)  # adds renewal_type/cycle_end_date etc.

    def tearDown(self):
        self.conn.close()

    def _insert_credential(self, id, name, issuer, badge_platform=None, badge_url=None, archived=0):
        self.conn.execute(
            "INSERT INTO credentials (id, name, issuer, status, badge_platform, badge_url, badge_visibility, archived) "
            "VALUES (?, ?, ?, 'on-track', ?, ?, ?, ?)",
            (id, name, issuer, badge_platform, badge_url, "public" if badge_url else None, archived),
        )

    # -- _badge_id -----------------------------------------------------------------------

    def test_badge_id_strips_query_string(self):
        self.assertEqual(
            _badge_id("https://www.credential.net/abc-123?key=deadbeef"),
            _badge_id("https://www.credential.net/abc-123"),
        )

    def test_badge_id_strips_credly_path_prefix_naturally(self):
        self.assertEqual(_badge_id("https://www.credly.com/badges/abc-123"), "abc-123")

    def test_badge_id_none_for_empty_url(self):
        self.assertIsNone(_badge_id(None))
        self.assertIsNone(_badge_id(""))

    # -- diff_badges: match / wrong_link / missing_link -----------------------------------

    def test_exact_url_match_regardless_of_name_drift(self):
        self._insert_credential("cipt", "CIPT", "IAPP", "Credential.net",
                                 "https://www.credential.net/abc-123?key=xyz")
        badge = _badge("Certified Information Privacy Technologist (CIPT)", "The IAPP",
                        "https://www.credential.net/abc-123", platform="credential.net")
        [finding] = diff_badges(self.conn, [badge])
        self.assertEqual(finding["kind"], "match")
        self.assertEqual(finding["credential_id"], "cipt")
        self.assertFalse(finding["name_mismatch"])

    def test_exact_url_match_but_unrelated_name_flags_name_mismatch(self):
        # Bug this catches: a row's badge_url can be an exact, valid link to a *different*
        # badge than what the row's own name claims (the "gcp" row's link opened a Google
        # Cloud "Platform Leader" badge while the row itself was named/dated for
        # "Cloud GenAI Leader") -- exact URL agreement alone shouldn't hide that.
        self._insert_credential("gcp", "GCP Cloud GenAI Leader", "Google Cloud",
                                 "Credly", "https://www.credly.com/badges/platform-leader-uuid")
        badge = _badge("Platform Leader Certification", "Google Cloud",
                        "https://www.credly.com/badges/platform-leader-uuid")
        [finding] = diff_badges(self.conn, [badge])
        self.assertEqual(finding["kind"], "match")
        self.assertTrue(finding["name_mismatch"])

    def test_wrong_link_when_matched_row_has_a_different_url(self):
        # Mirrors a real-shaped CISSP/PMP bug: cissp's stored badge_url was actually PMP's badge.
        self._insert_credential("cissp", "CISSP", "ISC2", "Credly", "https://www.credly.com/badges/pmp-uuid")
        badge = _badge("Certified Information Systems Security Professional (CISSP)", "ISC2",
                        "https://www.credly.com/badges/cissp-uuid")
        [finding] = diff_badges(self.conn, [badge])
        self.assertEqual(finding["kind"], "wrong_link")
        self.assertEqual(finding["credential_id"], "cissp")
        self.assertEqual(finding["current_badge_url"], "https://www.credly.com/badges/pmp-uuid")

    def test_missing_link_when_matched_row_has_no_url(self):
        self._insert_credential("issep", "ISSEP", "ISC2")
        badge = _badge("Information Systems Security Engineering Professional (ISSEP)", "ISC2",
                        "https://www.credly.com/badges/issep-uuid")
        [finding] = diff_badges(self.conn, [badge])
        self.assertEqual(finding["kind"], "missing_link")
        self.assertEqual(finding["credential_id"], "issep")

    def test_archived_credentials_are_not_matched(self):
        self._insert_credential("ceh", "CEH", "EC-Council", archived=1)
        badge = _badge("CEH", "EC-Council", "https://www.credly.com/badges/ceh-uuid")
        [finding] = diff_badges(self.conn, [badge])
        self.assertEqual(finding["kind"], "new_credential")

    # -- diff_badges: issuer must match, not just name -------------------------------------

    def test_name_similarity_alone_without_issuer_match_is_not_enough(self):
        self._insert_credential("cissp", "CISSP", "ISC2")
        badge = _badge("CISSP", "A Completely Different Org", "https://www.credly.com/badges/x")
        [finding] = diff_badges(self.conn, [badge])
        self.assertEqual(finding["kind"], "new_credential")

    # -- diff_badges: new_credential --------------------------------------------------------

    def test_untracked_credential_is_new_credential(self):
        self._insert_credential("gcp", "GCP Cloud GenAI Leader", "Google Cloud",
                                 "Credly", "https://www.credly.com/badges/genai-uuid")
        badge = _badge("Platform Leader Certification", "Google Cloud",
                        "https://www.credly.com/badges/platform-leader-uuid", expires_date="2028-05-13")
        [finding] = diff_badges(self.conn, [badge])
        self.assertEqual(finding["kind"], "new_credential")
        self.assertEqual(finding["badge"]["name"], "Platform Leader Certification")

    # -- diff_badges: two scraped badges from the same issuer resolve independently --------

    def test_two_same_issuer_badges_each_claim_the_right_row(self):
        # Shape this covers: GCP row exists for "Cloud GenAI Leader" only; a second Google Cloud
        # badge ("Platform Leader") should NOT get matched to the same row.
        self._insert_credential("gcp", "GCP Cloud GenAI Leader", "Google Cloud",
                                 "Credly", "https://www.credly.com/badges/wrong-uuid")
        badges = [
            _badge("Cloud GenAI Leader Certification", "Google Cloud",
                   "https://www.credly.com/badges/genai-uuid", expires_date="2029-06-25"),
            _badge("Platform Leader Certification", "Google Cloud",
                   "https://www.credly.com/badges/platform-leader-uuid", expires_date="2028-05-13"),
        ]
        findings = diff_badges(self.conn, badges)
        genai_finding = next(f for f in findings if f["badge"]["name"].startswith("Cloud GenAI"))
        digital_leader_finding = next(f for f in findings if f["badge"]["name"].startswith("Platform"))
        self.assertEqual(genai_finding["kind"], "wrong_link")
        self.assertEqual(genai_finding["credential_id"], "gcp")
        self.assertEqual(digital_leader_finding["kind"], "new_credential")

    # -- diff_badges: ambiguous --------------------------------------------------------------

    def test_two_equally_close_rows_are_ambiguous_not_guessed(self):
        self._insert_credential("cred-a", "Security Fundamentals", "Big Org")
        self._insert_credential("cred-b", "Security Fundamentals II", "Big Org")
        badge = _badge("Security Fundamentals", "Big Org", "https://www.credly.com/badges/x")
        [finding] = diff_badges(self.conn, [badge])
        self.assertEqual(finding["kind"], "ambiguous")
        self.assertIn("cred-a", finding["candidates"])
        self.assertIn("cred-b", finding["candidates"])

    # -- suggested_new_credential_fields -----------------------------------------------------

    def test_suggested_fields_default_to_exam_and_never_guess_hours(self):
        badge = _badge("Platform Leader Certification", "Google Cloud",
                        "https://www.credly.com/badges/platform-leader-uuid", expires_date="2028-05-13")
        fields = suggested_new_credential_fields(badge)
        self.assertEqual(fields["renewal_type"], "exam")
        self.assertEqual(fields["cycle_end_date"], "2028-05-13")
        self.assertEqual(fields["badge_platform"], "Credly")
        self.assertNotIn("total", fields)
        self.assertNotIn("annual_minimum", fields)

    # -- apply_link_fix -----------------------------------------------------------------------

    def test_apply_link_fix_updates_url_and_derives_public_visibility(self):
        self._insert_credential("cipt", "CIPT", "IAPP", "Credential.net", "https://old-url")
        apply_link_fix(self.conn, "cipt", "Credential.net", "https://new-url")
        row = self.conn.execute("SELECT badge_url, badge_visibility FROM credentials WHERE id='cipt'").fetchone()
        self.assertEqual(row["badge_url"], "https://new-url")
        self.assertEqual(row["badge_visibility"], "public")

    # -- insert_discovered_credential -----------------------------------------------------------

    def test_insert_discovered_credential_slugifies_name_and_persists_fields(self):
        fields = suggested_new_credential_fields(
            _badge("Platform Leader Certification", "Google Cloud",
                   "https://www.credly.com/badges/platform-leader-uuid", expires_date="2028-05-13")
        )
        new_id = insert_discovered_credential(self.conn, fields)
        self.assertEqual(new_id, "platform-leader-certification")
        row = self.conn.execute("SELECT * FROM credentials WHERE id=?", (new_id,)).fetchone()
        self.assertEqual(row["name"], "Platform Leader Certification")
        self.assertEqual(row["renewal_type"], "exam")
        self.assertEqual(row["cycle_end_date"], "2028-05-13")
        self.assertEqual(row["badge_visibility"], "public")
        self.assertEqual(row["archived"], 0)

    def test_insert_discovered_credential_avoids_id_collision(self):
        self._insert_credential("platform-leader-certification", "existing", "x")
        fields = suggested_new_credential_fields(
            _badge("Platform Leader Certification", "Google Cloud", "https://www.credly.com/badges/y")
        )
        new_id = insert_discovered_credential(self.conn, fields)
        self.assertEqual(new_id, "platform-leader-certification-2")


if __name__ == "__main__":
    unittest.main()
