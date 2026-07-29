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
                <li>Optional: attach a certificate of completion (PDF) and click
                <strong>Extract Details</strong> to pre-fill what it can find — this runs
                locally, nothing is uploaded anywhere. Always double-check the fields it
                fills in before saving.</li>
                <li>Fill in the date, hours, title, and which credential(s) it applies to —
                one activity can count toward more than one credential.</li>
                <li>Click <strong>Save Activity</strong>. It's added to your log right away.</li>
            </ol>
        """,
    },
    {
        "id": "import-transcript",
        "title": "Import a transcript PDF",
        "body": """
            <ol class="docs-steps">
                <li>Open any credential, then click the <strong>Pending Review</strong> tab.</li>
                <li>Under <strong>Import Transcripts</strong>, choose one or more PDF transcripts
                from an issuer — e.g. ISC2's "Official CPE Transcript" export — and click
                <strong>Import Transcripts</strong>.</li>
                <li>Every record found is staged into Pending Review, same as any other source —
                nothing is added to your log automatically. Records that look like near-exact
                duplicates of something already logged or already awaiting review are skipped
                rather than staged again.</li>
                <li>A "rollover" credit line (a carried-over balance from a prior cycle, not a real
                activity) is reported in a note instead of being staged — consider it for a manual
                adjustment on the credential instead.</li>
            </ol>
            <p class="docs-copy"><strong>Heads up:</strong> this has only been tested against
            ISC2's transcript export. Other issuers format their transcripts differently, so a
            PDF from another provider may extract incompletely, extract nothing, or need more
            correction than usual at review — always check what got staged before approving it.</p>
        """,
    },
    {
        "id": "import-agenda",
        "title": "Import an event agenda PDF",
        "body": """
            <ol class="docs-steps">
                <li>Open any credential, then click the <strong>Pending Review</strong> tab.</li>
                <li>Under <strong>Import an event agenda PDF</strong>, choose a multi-session-day
                conference or retreat schedule — not a certificate or issuer transcript — and
                enter a <strong>Year</strong> only if the agenda's own dates don't include one.</li>
                <li>Click <strong>Import Agenda</strong>. Unlike other imports, this stages
                <em>one</em> aggregate candidate for the whole event — a suggested total hours
                plus an itemized session-by-session breakdown to review — rather than one row
                per session.</li>
                <li>Meals, breaks, and other non-content sessions are excluded from the total
                automatically; sessions with no resolvable end time are left out and marked
                "duration unknown" rather than guessed. Always check the breakdown, and fill in
                the provider and which credential(s) it applies to, before approving.</li>
            </ol>
            <p class="docs-copy"><strong>Heads up:</strong> only two agenda layouts are
            supported, each validated against one real sample. A differently-formatted agenda
            may extract incompletely or not at all — check what actually got staged before
            approving it.</p>
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
    {
        "id": "legal-privacy",
        "title": "Legal, privacy & responsibility",
        "body": """
            <p class="docs-copy"><strong>BETA software.</strong> Expect rough edges. Back up
            <code>data/tracker.db</code> before upgrading. Provided as-is, with no warranty —
            see <code>DISCLAIMER.md</code> in the project repo for the full text.</p>
            <ul class="docs-steps">
                <li><strong>Personal use, one principal.</strong> Built for one person tracking
                their own credentials on their own machine — not for tracking or submitting CE
                on someone else's behalf.</li>
                <li><strong>Unofficial and unaffiliated.</strong> Not authorized, sponsored, or
                officially supported by ISC2, ISACA, PMI, IAPP, or any other issuer or vendor it
                references. Portal forms and rules can change without notice. Use at your own
                risk.</li>
                <li><strong>Not institutionally endorsed.</strong> No employer or professional
                body vouches for this tool; it exists to help you stay ahead of your own
                deadlines.</li>
                <li><strong>Private by construction.</strong> Runs entirely on your own machine
                against a local database file. No hosted component, no telemetry, no analytics,
                and nothing about your usage or your CE records is sent to the developer. Any
                issuer-portal login happens in your own browser session under your own
                account — this tool never sees or stores those credentials.</li>
                <li><strong>Human-in-the-loop, not delegated accountability.</strong> This tool
                accelerates tracking — it doesn't remove you from the loop. Every write to your
                CE record and every issuer-portal submission needs your explicit review and
                go-ahead each time; suggested categories or form mappings are suggestions, never
                auto-applied. You're responsible for confirming that what gets saved or
                submitted is accurate.</li>
            </ul>
        """,
    },
]
