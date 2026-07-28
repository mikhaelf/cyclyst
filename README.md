# Cyclyst

A self-hosted dashboard for tracking continuing-education (CE/CPE/PDU) requirements across
multiple professional certifications — local-first, private by default, and strictly
human-in-the-loop.

> "Life is like riding a bicycle. To keep your balance, you must keep moving" — Einstein,
> in a letter to his son Eduard, February 5, 1930. Renewal cycles work the same way: the
> credentials stay current as long as you keep moving through them. Cyclyst keeps you
> balanced across all of them at once.

> **Status:** working and in daily personal use, pre-1.0. Self-hosted only — there is no
> hosted version, and your data never leaves your machine.

## The problem

If you hold more than one professional certification, you already know the mess:

- **Every issuer runs a different clock.** ISACA (CISA, CRISC, CGEIT) reports on the
  *calendar year* with a hard January deadline; ISC2 (CISSP), PMI (PMP), and IAPP run
  three- or two-year cycles anchored to *your* certification anniversary. "Am I on pace?"
  means different math for each one, at the same time.
- **Every issuer counts differently.** Flat hour totals; category minimums (ISC2 Group A
  ≥ 90 of 120); category caps (PMI "Giving Back" ≤ 25 of 60); *nested* buckets (PMI's
  Talent Triangle legs living inside an "Education" floor); suggested vs. mandatory annual
  minimums; grace periods that exist for some issuers and not others.
- **One activity often counts several times.** A security-audit webinar can legitimately
  apply to CISA *and* CRISC *and* CISSP — but each portal wants its own entry.
- **Some credentials aren't hour-based at all** — they renew by re-exam on a fixed date, or
  they're valid only while another credential stays active.

The usual answer is a spreadsheet that can't answer the only question that matters — *"am I
going to make every deadline at my current pace?"* — plus an annual scramble to reconstruct
an itemized, evidence-backed record if an issuer audits you.

Cyclyst replaces that with one local dashboard: enter each credential's actual rules
once, log each activity once (tagged to every credential it applies to), and get computed
per-credential progress, pace status, and audit-ready reports.

## Design principles

1. **Local-first, private by default.** Your data is a single SQLite file in a gitignored
   `data/` directory. No accounts, no telemetry, no cloud services, nothing to sign up for.
2. **Human-in-the-loop, always.** Nothing writes to your CE record automatically. Suggested
   activities (e.g. from calendar ingestion, below) land in a *Pending Review* queue where
   you approve or dismiss each one. And nothing is ever submitted to an issuer's portal on
   your behalf.
3. **The issuer's portal stays the system of record.** This tracker is a private aggregator
   and early-warning system. "Manual adjustment hours" let you reconcile with totals the
   issuer's portal already confirms, without fabricating itemized entries.

## Features

- **Dashboard** with per-credential status (on track / at risk / in grace period /
  reminder-only), percent complete, and days remaining — all *computed on every page load*
  from your cycle dates and logged hours, never stored as stale numbers.
- **Real renewal semantics:** calendar-year vs. anniversary reporting windows, suggested vs.
  mandatory annual minimums, per-issuer grace periods, min/max category rules with
  arbitrary nesting, exam-only credentials (no CE, just an expiration to watch), and
  dependency cascades ("this credential lapses if that one does" — including *any-of*
  groups).
- **One activity, many credentials:** each logged activity applies to any set of
  hour-based credentials; category rollups update everywhere at once.
- **Certificate upload auto-fill:** attach a certificate of completion (PDF) on the Add
  Training form and it pre-fills date, hours, provider, and title where confidently found —
  local-only, no network call, no file ever saved to disk. You still review and save the
  form yourself.
- **Bulk transcript PDF import:** upload one or more issuer-exported transcript PDFs (e.g.
  ISC2's "Official CPE Transcript") from the Pending Review tab and every CE record found is
  staged for review at once — local-only, same as certificate upload. Only tested against
  ISC2's export so far; see [Transcript PDF import](#transcript-pdf-import-optional) below.
- **Pending-review queue** for ingested candidates, with duplicate detection against your
  existing log and sticky dismissals (a dismissed event never resurfaces).
- **Reports:** itemized CSV and audit-ready PDF exports scoped to a credential's current
  cycle window.
- **Quality-of-life:** public badge links (Credly / Credential.net), archived credentials,
  in-app docs, in-place editing of everything — no hand-editing the database.

## Quickstart

Requires Python 3.10+ (3.11+ recommended). No Node.js, no build step, no Docker required.

```bash
git clone https://github.com/mikhaelf/agentic-ce-sync.git
cd agentic-ce-sync
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

> **Private repo:** if you clone over HTTPS, authenticate with `gh auth login` or a
> personal access token with `repo` scope. Collaborators must be added on GitHub before
> they can clone.

Open http://127.0.0.1:5000. A fresh install starts **empty** — click **+ Add Credential**
and enter your first credential's rules (cycle dates, renewal type, hour total, category
rules, grace period). Then log activities against it and the dashboard takes care of the
math.

> The page loads the small [HTMX](https://htmx.org) library from a CDN, so the browser
> needs internet access; your data still never leaves your machine.

### Try it with demo data

To explore the UI with a fictional sample portfolio (CISSP/CISA/CRISC/PMP-style credentials
with illustrative numbers) instead of starting empty:

```bash
CE_TRACKER_DEMO=1 CE_TRACKER_DB=/tmp/ce-demo.db python run.py
```

Demo seeding only ever touches an *empty* database, and pointing `CE_TRACKER_DB` at a
throwaway file keeps it out of your real data.

### Where your data lives

Everything is stored in `data/tracker.db` (SQLite), which is gitignored — it never enters
version control. Backups are just a copy of that one file.

| Environment variable | Default | Purpose |
|---|---|---|
| `CE_TRACKER_DB` | `data/tracker.db` (under the repo root) | Path to the SQLite file. Use this to keep test and production data separate, or to store the DB outside the repo directory. |
| `CE_TRACKER_DEMO` | unset | Set to `1` to load fictional sample credentials on startup — **only if the database is empty**. |

Example — isolated test database with demo data:

```bash
CE_TRACKER_DEMO=1 CE_TRACKER_DB="$HOME/ce-tracker-test/tracker.db" python run.py
```

### What is in git vs. what you rebuild locally

| Piece | In git? | On a fresh machine / user account |
|---|---|---|
| Application code | Yes | `git clone` (then `git pull` for updates) |
| Python venv (`.venv/`) | No | Create with `python3 -m venv .venv` and `pip install -r requirements.txt` |
| Your tracker data (`data/tracker.db`) | No | Starts empty, or copy a `.db` file manually (see below) |
| `CLAUDE.local.md`, `.claude/` | No | Optional local notes / AI-tool config — copy only if you want them |
| Issuer handbook PDFs (`Credential Providers/`) | No | Local reference material only |

**Code sync:** push from one environment, `git pull` in another.

**Data sync:** never through git. Copy `tracker.db` explicitly, or use separate
`CE_TRACKER_DB` paths so environments do not accidentally share production data.

### Setup on another macOS user account (testing)

Each macOS user has their own home directory, so the usual pattern is: **same repo from
GitHub, separate database per account.** Do not copy `.venv/` across accounts — paths and
permissions differ; rebuild the venv instead.

#### Recommended: sandbox with demo data

Safest for UI or feature testing — no real cert numbers or activity history.

```bash
gh auth login   # or use HTTPS + a personal access token
git clone https://github.com/mikhaelf/agentic-ce-sync.git
cd agentic-ce-sync
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

CE_TRACKER_DEMO=1 CE_TRACKER_DB="$HOME/ce-tracker-test/tracker.db" python run.py
```

Open http://127.0.0.1:5000. Demo seeding runs only when the target database has no
credentials yet.

#### Optional: copy an existing database for realistic testing

Use this only when you intentionally want the same layout and records on the test account
(e.g. to reproduce a bug against real data). Stop the app on both accounts before copying.

On the **source** account:

```bash
cp /path/to/agentic-ce-sync/data/tracker.db /Users/Shared/ce-tracker-test.db
chmod 644 /Users/Shared/ce-tracker-test.db
```

On the **test** account:

```bash
git clone https://github.com/mikhaelf/agentic-ce-sync.git
cd agentic-ce-sync
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

mkdir -p data
cp /Users/Shared/ce-tracker-test.db data/tracker.db
python run.py
```

Remove the file from `/Users/Shared/` when finished if it contains sensitive cert data.

#### Optional shell aliases

Add to `~/.zshrc` or `~/.bashrc` on any account:

```bash
alias ce-tracker='cd ~/agentic-ce-sync && source .venv/bin/activate && python run.py'
alias ce-tracker-demo='cd ~/agentic-ce-sync && source .venv/bin/activate && CE_TRACKER_DEMO=1 CE_TRACKER_DB="$HOME/ce-tracker-test/tracker.db" python run.py'
```

Adjust the `cd` path if the repo lives somewhere other than `~/agentic-ce-sync`.

#### GitHub push note (email privacy)

If `git push` is rejected with an email-privacy error (GitHub error GH007), either allow
command-line pushes in [GitHub email settings](https://github.com/settings/emails) or set
your git author email to your GitHub noreply address:

```bash
git config --global user.email "YOUR_GITHUB_USERNAME@users.noreply.github.com"
```

New commits will use that address. Existing commits already in history keep their original
author email until you rewrite history (usually unnecessary once the first push succeeds).

## Using the tracker

1. **Add each credential** with its real rules. The form covers the whole schema: renewal
   type (`cpe_hours` / `exam` / `mandatory_module`), cycle start/end, reporting calendar
   (calendar-year or anniversary), hour total and unit, annual minimum (suggested or
   mandatory), grace period days, category min/max rules (nestable), dependencies, and
   "confirmed elsewhere" adjustment hours.
2. **Log activities** as you complete them — date, provider, title, hours, category, and
   *every* credential they apply to. Only hour-based (`cpe_hours`) credentials are offered
   as targets; exam-only credentials never accumulate hours. Optionally attach a certificate
   of completion (PDF) first and click **Extract Details** to pre-fill what it can find —
   review the fields before saving either way.
3. **Review pending items** (if you use ingestion): approve to commit an item to your log —
   you supply the category and applies-to yourself, the machine never guesses them — or
   dismiss it permanently.
4. **Export reports** from the Reports tab when renewing or when an issuer audits you:
   itemized CSV or PDF for the credential's current cycle.
5. **Archive** credentials you're letting lapse; they stay visible for reference but stop
   being tracked.

The in-app **Docs** page (📘 in the sidebar) covers the same ground from inside the tool.

## Calendar ingestion (optional, AI-assisted)

The tracker can scan your Google Calendar for events that look like CE activities —
conferences, webinars, chapter meetings, trainings — and stage them for review. Its shape is
deliberately unusual:

- **The app itself never talks to Google.** There's no API client, no OAuth flow, no stored
  tokens. You (or an AI assistant session that already has calendar access, e.g. Claude
  with the Google Calendar connector) export events to a JSON file, then hand that file to
  a pure-Python CLI:

  ```bash
  python -m ce_tracker.calendar_ingest --events-file events.json
  ```

  The file is a JSON array of standard Google Calendar API event resources. `--since` /
  `--until` override the scan window; by default each run resumes where the last one ended.

- **Scoring is tiered and conservative:** known CE providers (editable list at the top of
  `ce_tracker/calendar_ingest.py`) stage cleanly; generic CE keywords ("webinar",
  "symposium", "chapter meeting", …) stage *flagged* for closer review; an editable
  denylist ("1:1", "standup", "book club", …) never stages at all.
- **Everything lands in Pending Review.** The scorer never guesses which credential or
  category an event belongs to — you fill those in at approval. Duplicates of activities
  you already logged are detected and labeled with a confidence score.

Gmail ingestion (course-completion emails, registration receipts) is a planned follow-up
and intentionally not built yet.

## Transcript PDF import (optional)

The Pending Review tab also accepts one or more issuer-exported transcript PDFs at once —
ISC2's "Official CPE Transcript" export is the case this was built and tested against. Local
extraction only, same posture as certificate upload auto-fill: no network call, nothing saved
to disk beyond the parsed rows landing in Pending Review.

- **Handles multi-record transcripts, not just single certificates.** Every dated CE row in
  the file is extracted, including titles that wrap across multiple lines in the PDF.
- **Merges duplicate rows within one document.** Some issuers print the same activity under
  more than one heading in a single export — e.g. a concentration certification (ISSEP)
  sharing its base credential's (CISSP) CPE pool prints that pool's activities once per
  heading. These are recognized as the same activity, not staged twice.
  "Rollover" credit lines (a carried-over balance, not a real activity) are reported in a
  note instead of being staged as one.
- **Skips near-certain duplicates automatically.** A row that's essentially identical — same
  date, same title, same hours — to something already in your log or already awaiting review
  isn't staged a second time. Anything less certain still stages, flagged, for you to check.
- **⚠️ Not tested against every issuer.** This has only been validated against a real ISC2
  transcript export. Other issuers format their transcripts differently — layout, date
  format, how multi-credential sharing is represented, if at all — so a PDF from a different
  provider may extract incompletely, extract nothing useful, or need more correction at
  review than usual. Always check what actually got staged before approving it; nothing is
  ever added to your log without that review step regardless.

## Architecture

Single-language Python stack, chosen for hackability over ceremony:

| Layer | Choice |
|---|---|
| Web framework | Flask, server-rendered Jinja2 templates |
| Interactivity | HTMX + a little vanilla JS — no SPA, no Node toolchain |
| Storage | SQLite via the stdlib `sqlite3` module |
| PDF export | ReportLab |
| Certificate parsing | pypdf (local text extraction, no network) |

```
ce_tracker/
  db.py               schema, migrations, optional demo seed
  progress.py         all cycle/date/pace math (pure functions, no DB)
  views.py            row → template-context shaping
  routes.py           Flask routes (dashboard, CRUD, review, exports)
  reports.py          CSV/PDF generation
  calendar_ingest.py  event scoring + staging CLI (pure stdlib)
  certificate_parse.py  local PDF text extraction + field heuristics for upload auto-fill
  transcript_parse.py  bulk multi-record transcript PDF extraction + staging
  docs_content.py     in-app docs text
  templates/ static/  UI
run.py                dev entry point
tests/                unit tests (pure functions + in-memory SQLite)
```

Progress is **computed, not stored**: `progress.py` derives status, percent, pace, and
days-left from cycle dates and summed activity hours on every request, so the dashboard can
never drift out of sync with the log. The date math — leap-year anniversaries,
calendar-year vs. anniversary annual windows, grace periods, dependency cascades — is pure
functions over plain dicts, which is what makes it testable.

Design history lives in [`ce-cpe-tracker-design.md`](ce-cpe-tracker-design.md) (data model
and business rules) and [`design/ui-handoff/`](design/ui-handoff/) (the original UI
prototype).

## Testing

```bash
python -m unittest discover -s tests
```

The suite covers every `compute_progress()` branch (including leap-day and year-boundary
edges, nested category rollups, and the dependency cascade) and the calendar-event scorer —
all against plain dicts or in-memory SQLite. **No test reads or writes a real database
file.**

## Scope and honest limitations

- `cpe_hours` and `exam` renewal types are fully computed. `mandatory_module`
  (vendor-scheduled pass/fail refreshers) has schema columns reserved but no computation
  yet — it'll be added when a tracked credential actually needs it.
- Certificate upload auto-fill only reads text-layer PDFs — scanned/photographed
  certificates (image-only PDFs) aren't handled yet, and fields it isn't confident about are
  left blank rather than guessed. See `ce-cpe-tracker-design.md` §11 for the heuristic and a
  possible future LLM-based fallback (opt-in, not built).
- **Transcript PDF import is only tested against ISC2's export.** The row-extraction rules
  (table layout, date format, how a shared multi-credential pool is represented) were built
  and validated against one real issuer's transcript. Other issuers' exports aren't
  guaranteed to parse correctly, or at all — see `ce-cpe-tracker-design.md` §11a.
- Single-user by design. No auth, no multi-tenancy — run it on your own machine.
- The Flask dev server is fine for localhost use; put a real WSGI server (e.g. gunicorn)
  in front of it if you expose it beyond loopback.

## Roadmap

- Dockerfile for easier self-hosting
- Route-level test coverage (business logic is covered; HTTP layer isn't yet)
- Gmail ingestion into the same pending-review queue (local-only, same
  explicit-signal-first philosophy)
- OCR fast-follow for scanned/image certificate uploads (`pytesseract`, adds a system
  dependency — deferred until local PDF-text parsing proves useful enough to justify it)
- Opt-in LLM-based fallback for certificate parsing when local heuristics miss — would be
  the project's first outbound network call, so off by default and disclosed clearly
- Browser-assisted submission helper — pre-filling issuer portal forms with a required
  human "go" per record, never batch auto-submit

## License

Not yet chosen — this repository is being prepared for public release. A license file will
be added before publication.
