"""Static content for the in-app Docs page.

To add a new topic, append a dict to DOCS_SECTIONS with a unique "id" (used as the
in-page anchor), a "title", and a "body" of trusted HTML. The Docs page's table of
contents and section list are both generated from this list, so nothing else needs
to change.
"""

DOCS_SECTIONS = [
    {
        "id": "track-progress",
        "title": "Track a credential's progress",
        "body": """
            <p class="docs-copy">Click a credential in the left list to open it. The
            <strong>Overview</strong> tab shows your percent complete, hours earned toward the cycle
            total, and any category minimums or caps you still need to hit.</p>
        """,
    },
    {
        "id": "log-activity",
        "title": "Log a completed activity",
        "body": """
            <ol class="docs-steps">
                <li>Open a credential, then click the <strong>Activity Log</strong> tab.</li>
                <li>Click <strong>+ Add Activity</strong>.</li>
                <li>Fill in the date, hours, title, and which credential(s) it applies to —
                one activity can count toward more than one credential.</li>
                <li>Click <strong>Save Activity</strong>. It's added to your log right away.</li>
            </ol>
        """,
    },
    {
        "id": "review-pending",
        "title": "Review suggested activities",
        "body": """
            <p class="docs-copy">The <strong>Pending Review</strong> tab lists activities detected
            from sources like Google Calendar. Nothing is added to your record automatically —
            you decide:</p>
            <ul class="docs-steps">
                <li>Click <strong>Review & Approve</strong> to open a pre-filled Activity form —
                check the date, hours, category, and which credential(s) it applies to, then save
                it into your log.</li>
                <li>Click <strong>Dismiss</strong> to ignore it — it won't come back.</li>
                <li>Items marked "⚠ low-confidence match" are worth double-checking before you
                approve them.</li>
                <li>Items flagged as a likely duplicate show which existing activity they may
                overlap with — confirm it's not a repeat before approving.</li>
            </ul>
        """,
    },
    {
        "id": "reports",
        "title": "Generate a report",
        "body": """
            <p class="docs-copy">The <strong>Reports</strong> tab builds an itemized record of your
            activity for a credential's current cycle. Use <strong>Export CSV</strong> for a
            spreadsheet-friendly file, or <strong>Export PDF</strong> for an audit-ready report
            you can hand to an issuer.</p>
        """,
    },
    {
        "id": "good-to-know",
        "title": "Good to know",
        "body": """
            <ul class="docs-steps">
                <li>Nothing is ever submitted to an issuer's portal automatically — that always
                requires your explicit go-ahead.</li>
                <li>Archived credentials (bottom of the sidebar) are kept for reference but no
                longer tracked for renewal.</li>
            </ul>
        """,
    },
]
