"""Deny-rule classifier for agenda-style event schedules (multi-session-day conference/
retreat agendas, as distinct from certificates or issuer transcripts -- see
certificate_parse.py and transcript_parse.py for those).

Scope, deliberately narrow: this is a denylist that flags line items as hospitality/
logistics (meals, breaks, travel, socializing) -- nothing more. It is NOT a content
classifier and does not attempt to recognize what a real CE session looks like: two real
agendas checked against each other (a multi-day conference and a single-day executive
retreat) used completely different vocabulary for actual sessions ("Fireside Chat"/"Panel"
vs "Session I/II/III"), so there's no reliable positive signal to build an allowlist from.
Hospitality/logistics phrasing, by contrast, is far more standardized across events -- so
assuming a line counts unless it matches a known-non-content pattern is the safer default.

Every pattern here is evidence-based, taken from two real agendas, not speculative. Extend
only when a real agenda surfaces logistics phrasing this list misses.

Deliberately out of scope for this slice: date/time parsing, duration computation,
aggregation, and any upload route or review-queue wiring. "Welcoming/Opening/Closing
Remarks"-style lines are also deliberately NOT denied -- whether brief ceremonial remarks
count as CE is a judgment call, not a logistics fact, so it's left to a human either way.
"""
import re

_NON_CONTENT_PATTERNS = [
    re.compile(r"\bbreakfast\b", re.I),
    re.compile(r"\bbrunch\b", re.I),
    re.compile(r"\blunch\b", re.I),
    re.compile(r"\bdinner\b", re.I),
    re.compile(r"\breception\b", re.I),
    re.compile(r"\bmixer\b", re.I),
    re.compile(r"\bcocktail\b", re.I),
    re.compile(r"\barrivals?\b", re.I),
    re.compile(r"\bcheck[\s-]?in\b", re.I),
    re.compile(r"\bcheck[\s-]?out\b", re.I),
    re.compile(r"\bdeparture\b", re.I),
    re.compile(r"\bbreak\b", re.I),  # standalone "Break" -- word boundary excludes "breakout"
    re.compile(r"\bworkout\b", re.I),
    re.compile(r"\byoga\b", re.I),
    re.compile(r"\bpilates\b", re.I),
    re.compile(r"\bconcludes?\b", re.I),
    re.compile(r"\bfarewell\b", re.I),
    re.compile(r"\bafternoon activities\b", re.I),
    re.compile(r"\bat your leisure\b", re.I),
]


def is_non_content(description):
    """True if this agenda line item is hospitality/logistics rather than CE content.

    A denylist, not a content classifier: False means "not recognized as logistics," not
    "confirmed to be real CE content" -- a human still decides which surviving lines
    actually count and for how long.
    """
    return any(pattern.search(description) for pattern in _NON_CONTENT_PATTERNS)
