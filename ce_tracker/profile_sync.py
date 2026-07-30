"""Diffs badges scraped from a public credential-wallet profile (Accredible/Credential.net,
Credly) against the `credentials` table, to catch stale/wrong/missing badge links and surface
credentials that exist publicly but aren't tracked yet. Built after a manual pass (2026-06-02)
found a stale CIPT link (pointed at an expired, private prior-term cert), a CISSP badge_url
that was actually a copy of PMP's, a GCP badge_url pointing at the wrong Google Cloud cert, and
an untracked "Platform Leader" credential — see CLAUDE.local.md for the full account.

Like calendar_ingest.py and portal_submit.py, this module never talks to a badge platform
directly — Accredible/Credly wallet pages are JS-rendered and a plain HTTP fetch got blocked
outright (403) during development. Gathering badge data is always a live Claude-in-Chrome
session's job: visit the public profile URL(s), read each badge's name/issuer/link/dates off
the page, and write them to a JSON file shaped like:

    [
      {
        "platform": "credential.net",  # or "credly"
        "name": "Certified Information Privacy Technologist (CIPT)",
        "issuer": "The IAPP",
        "url": "https://www.credential.net/example-cipt",
        "issued_date": "2024-01-01",   # ISO date, or null if not shown/known
        "expires_date": "2026-01-01"   # ISO date, or null if "does not expire"
      },
      ...
    ]

This module only compares and (on explicit per-record confirmation) writes — same
"suggest, never guess" posture as portal_submit's mapping suggesters, and the same "explicit
go per record, no batch auto-apply" rule as record_submission. `diff` never writes; `apply`
walks each finding and asks before touching the database.
"""

import argparse
import json
import re
from difflib import SequenceMatcher

from .db import get_db

NAME_MATCH_THRESHOLD = 0.45
AMBIGUOUS_MARGIN = 0.08

_PAREN_RE = re.compile(r"\(([^)]+)\)")


def _acronym(name):
    """The parenthesized abbreviation in a full credential title, e.g. 'Certified ...
    Professional (CISSP)' -> 'CISSP'. Badge platforms show the long official title; this
    tracker's own credential names are usually just the acronym ('CISSP', 'CIPT', 'ISSEP') --
    comparing those directly catches real matches that plain string-similarity between a
    5-letter acronym and a 60-character title would score far too low to clear."""
    matches = _PAREN_RE.findall(name or "")
    return matches[-1].strip() if matches else None


def _title_similarity(a, b):
    a_l, b_l = (a or "").lower().strip(), (b or "").lower().strip()
    ratio = SequenceMatcher(None, a_l, b_l).ratio()

    a_acronym, b_acronym = _acronym(a), _acronym(b)
    if (a_acronym and a_acronym.lower() == b_l) or (b_acronym and b_acronym.lower() == a_l):
        return 1.0

    # One name appearing whole, on a word boundary, inside the other is also a strong signal
    # (e.g. a DB name that's a shorter/older variant of the badge's full title).
    if a_l and b_l and (
        re.search(r"\b" + re.escape(a_l) + r"\b", b_l) or re.search(r"\b" + re.escape(b_l) + r"\b", a_l)
    ):
        return max(ratio, 0.9)

    return ratio


def _issuer_match(a, b):
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    return bool(a) and bool(b) and (a in b or b in a)


def _badge_id(url):
    """The platform-issued badge id/UUID a link points to, stripping query strings (e.g.
    Credential.net's '?key=...' sharing token) and any path prefix (Credly's '/badges/') so
    two links differing only in those cosmetic ways are still recognized as the same badge."""
    if not url:
        return None
    return url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1].lower()


def diff_badges(conn, badges):
    """Compares scraped badges (see module docstring for the JSON shape) against non-archived
    rows in `credentials`. Returns one finding dict per input badge, each with a `kind`:
      "match"          -- already correct, nothing to do
      "wrong_link"      -- matched a credential row, but its stored badge_url differs
      "missing_link"    -- matched a credential row with no badge_url set
      "new_credential"  -- no confident match; looks like an untracked credential
      "ambiguous"       -- two+ credential rows are close enough matches to need a human pick
    A "match" finding also carries `name_mismatch: True` if the row's own name doesn't
    resemble the badge's title even though the URL is identical — a link can be pointed at a
    real, valid badge that just isn't the credential the row's name claims it is (seen for
    real: a `gcp` row named "Cloud GenAI Leader" whose stored link actually opened a
    different, legitimately-existing "Platform Leader" badge). Exact-URL agreement alone
    doesn't rule that out, so it's surfaced rather than silently folded into "already correct".
    Pure comparison — never writes.
    """
    rows = conn.execute("SELECT * FROM credentials WHERE archived = 0").fetchall()
    row_claimed = [False] * len(rows)
    findings = [None] * len(badges)

    # Pass 1: an exact badge-id match (same UUID already stored) wins outright as far as
    # row/badge pairing goes, regardless of name similarity — the link demonstrably opens this
    # badge. Whether that's actually the badge this row's name claims to be is a separate
    # check (name_mismatch, see docstring), not a reason to treat it as unmatched.
    for bi, badge in enumerate(badges):
        for ri, row in enumerate(rows):
            if row_claimed[ri]:
                continue
            if row["badge_url"] and _badge_id(row["badge_url"]) == _badge_id(badge["url"]):
                row_claimed[ri] = True
                findings[bi] = {
                    "kind": "match", "badge": badge,
                    "credential_id": row["id"], "credential_name": row["name"],
                    "current_badge_url": row["badge_url"],
                    "name_mismatch": _title_similarity(badge["name"], row["name"]) < NAME_MATCH_THRESHOLD,
                }
                break

    # Pass 2: for what's left, score every (badge, unclaimed same-issuer row) pair by name
    # similarity. Candidate lists are computed once, up front, so the ambiguity check below
    # sees a consistent rival set regardless of the order badges get resolved in.
    candidates = {}  # bi -> [(score, ri), ...] sorted desc, only for badges pass 1 didn't settle
    for bi, badge in enumerate(badges):
        if findings[bi] is not None:
            continue
        scored = [
            (_title_similarity(badge["name"], row["name"]), ri)
            for ri, row in enumerate(rows)
            if not row_claimed[ri] and _issuer_match(badge["issuer"], row["issuer"])
        ]
        candidates[bi] = sorted((s for s in scored if s[0] >= NAME_MATCH_THRESHOLD), reverse=True)

    # Resolve most-confident badges first, so a strong match claims its row before a weaker
    # match for a different badge can contest it.
    order = sorted(candidates, key=lambda bi: candidates[bi][0][0] if candidates[bi] else -1, reverse=True)
    for bi in order:
        badge = badges[bi]
        scored = [(s, ri) for s, ri in candidates[bi] if not row_claimed[ri]]
        if not scored:
            findings[bi] = {"kind": "new_credential", "badge": badge}
            continue
        if len(scored) > 1 and (scored[0][0] - scored[1][0]) < AMBIGUOUS_MARGIN:
            findings[bi] = {
                "kind": "ambiguous", "badge": badge,
                "candidates": [rows[ri]["id"] for _, ri in scored[:3]],
            }
            continue
        _, ri = scored[0]
        row = rows[ri]
        row_claimed[ri] = True
        # Pass 1 already checked this exact (badge, row) pair for an id match and it didn't
        # match (otherwise this row would already be claimed) — so this is always a real
        # difference: either nothing stored yet, or something stored that's now wrong.
        kind = "missing_link" if not row["badge_url"] else "wrong_link"
        findings[bi] = {
            "kind": kind, "badge": badge,
            "credential_id": row["id"], "credential_name": row["name"],
            "current_badge_url": row["badge_url"],
        }

    return findings


def _platform_label(badge):
    return {"credly": "Credly", "credential.net": "Credential.net"}.get(badge["platform"], badge["platform"])


def suggested_new_credential_fields(badge):
    """Conservative defaults for a credential row built from a scraped badge. renewal_type
    defaults to 'exam' since a badge listing alone can't tell us a CE-hours cycle's total/annual
    minimum (those need the issuer's own handbook, same "never guess" posture as
    suggest_isc2_mapping/suggest_pmi_mapping) — always meant for human review and correction via
    the Edit Credential form afterward, not a finished row."""
    return {
        "name": badge["name"],
        "issuer": badge["issuer"],
        "renewal_type": "exam",
        "cycle_end_date": badge.get("expires_date"),
        "badge_platform": _platform_label(badge),
        "badge_url": badge["url"],
        "badge_visibility": "public",
        "note": "Discovered via public-profile sync — confirm renewal_type/cycle dates against the issuer.",
    }


def apply_link_fix(conn, credential_id, badge_platform, badge_url):
    """Single-record write: updates one credential's badge fields. Must only be called after
    explicit human confirmation of that specific record — see module docstring."""
    visibility = "public" if badge_url else ("private" if badge_platform else None)
    conn.execute(
        "UPDATE credentials SET badge_platform=?, badge_url=?, badge_visibility=? WHERE id=?",
        (badge_platform, badge_url, visibility, credential_id),
    )
    conn.commit()


def _slugify(name, existing_ids):
    base = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-") or "credential"
    slug = base
    n = 2
    while slug in existing_ids:
        slug = f"{base}-{n}"
        n += 1
    return slug


def insert_discovered_credential(conn, fields):
    """Single-record insert for a credential found via profile sync but not yet tracked.
    Mirrors routes.create_credential's INSERT shape, but with the smaller field set profile
    sync can actually populate — member_id, cert_number, and (for a cpe_hours credential)
    total/annual_minimum are left for the Edit Credential form. Must only be called after
    explicit human confirmation — see module docstring. Returns the new credential id."""
    existing_ids = {r["id"] for r in conn.execute("SELECT id FROM credentials").fetchall()}
    new_id = _slugify(fields["name"], existing_ids)
    conn.execute(
        "INSERT INTO credentials (id, name, issuer, status, renewal_type, cycle_end_date, "
        "note, badge_platform, badge_url, badge_visibility, archived) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,0)",
        (
            new_id, fields["name"], fields["issuer"], "on-track", fields["renewal_type"],
            fields.get("cycle_end_date"), fields.get("note"),
            fields["badge_platform"], fields["badge_url"], fields["badge_visibility"],
        ),
    )
    conn.commit()
    return new_id


def _print_findings(findings):
    groups = {"match": [], "wrong_link": [], "missing_link": [], "new_credential": [], "ambiguous": []}
    for f in findings:
        groups[f["kind"]].append(f)

    name_mismatches = [f for f in groups["match"] if f.get("name_mismatch")]
    if name_mismatches:
        print("\nLINKED, BUT NAME LOOKS UNRELATED (double-check this is really the right badge):")
        for f in name_mismatches:
            print(f"  [{f['credential_id']}] {f['credential_name']} -> badge titled \"{f['badge']['name']}\"")
            print(f"      {f['current_badge_url']}")

    if groups["wrong_link"]:
        print("\nWRONG LINK (stored badge_url points elsewhere):")
        for f in groups["wrong_link"]:
            print(f"  [{f['credential_id']}] {f['credential_name']}")
            print(f"      currently: {f['current_badge_url']}")
            print(f"      should be: {f['badge']['url']}")

    if groups["missing_link"]:
        print("\nMISSING LINK (badge_url empty):")
        for f in groups["missing_link"]:
            print(f"  [{f['credential_id']}] {f['credential_name']} -> {f['badge']['url']}")

    if groups["new_credential"]:
        print("\nNEW / UNTRACKED CREDENTIAL:")
        for f in groups["new_credential"]:
            b = f["badge"]
            print(f"  {b['name']} ({b['issuer']}) -> {b['url']}")

    if groups["ambiguous"]:
        print("\nAMBIGUOUS (needs a manual pick, not auto-resolved):")
        for f in groups["ambiguous"]:
            print(f"  {f['badge']['name']} could match: {', '.join(f['candidates'])}")

    print(
        f"\n{len(groups['match'])} already correct, {len(groups['wrong_link'])} wrong link(s), "
        f"{len(groups['missing_link'])} missing link(s), {len(groups['new_credential'])} new "
        f"credential(s), {len(groups['ambiguous'])} ambiguous."
    )


def _confirm(prompt):
    return input(f"{prompt} [y/N] ").strip().lower() == "y"


def main():
    parser = argparse.ArgumentParser(
        description="Diff public-profile badges (Credential.net/Accredible, Credly) against the credentials table."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    diff_p = sub.add_parser("diff", help="Show what would change, without writing anything.")
    diff_p.add_argument("--file", required=True, help="JSON file: array of scraped badges (see module docstring for shape)")

    apply_p = sub.add_parser("apply", help="Walk each finding and, on explicit per-record confirmation, apply it.")
    apply_p.add_argument("--file", required=True)

    args = parser.parse_args()
    with open(args.file) as fh:
        badges = json.load(fh)

    conn = get_db()
    findings = diff_badges(conn, badges)

    if args.command == "diff":
        _print_findings(findings)

    elif args.command == "apply":
        for f in findings:
            if f["kind"] == "match" and f.get("name_mismatch"):
                print(f"\n[{f['credential_id']}] {f['credential_name']} links to a badge titled "
                      f"\"{f['badge']['name']}\" — name doesn't match; skipping (resolve manually).")
            elif f["kind"] == "wrong_link":
                b = f["badge"]
                print(f"\n[{f['credential_id']}] {f['credential_name']}")
                print(f"  currently: {f['current_badge_url']}")
                print(f"  should be: {b['url']}")
                if _confirm("Apply this link fix?"):
                    apply_link_fix(conn, f["credential_id"], _platform_label(b), b["url"])
                    print("  applied.")
            elif f["kind"] == "missing_link":
                b = f["badge"]
                print(f"\n[{f['credential_id']}] {f['credential_name']} -> {b['url']}")
                if _confirm("Set this badge link?"):
                    apply_link_fix(conn, f["credential_id"], _platform_label(b), b["url"])
                    print("  applied.")
            elif f["kind"] == "new_credential":
                fields = suggested_new_credential_fields(f["badge"])
                print(f"\nNew credential: {fields['name']} ({fields['issuer']})")
                print(f"  suggested renewal_type=exam, cycle_end_date={fields['cycle_end_date']}, badge_url={fields['badge_url']}")
                if _confirm("Add this credential?"):
                    new_id = insert_discovered_credential(conn, fields)
                    print(f"  added as '{new_id}' — review renewal_type/cycle dates via the Edit Credential form.")
            elif f["kind"] == "ambiguous":
                print(f"\nAmbiguous, skipping: {f['badge']['name']} could match {', '.join(f['candidates'])} — resolve manually.")
        print("\nDone.")

    conn.close()


if __name__ == "__main__":
    main()
