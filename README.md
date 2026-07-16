# CE Tracker

A self-hosted dashboard for tracking continuing-education (CE/CPE/PDU) requirements across
multiple professional certifications — local-first, private by default, and strictly
human-in-the-loop.

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

CE Tracker replaces that with one local dashboard: enter each credential's actual rules
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
- **Pending-review queue** for ingested candidates, with duplicate detection against your
  existing log and sticky dismissals (a dismissed event never resurfaces).
- **Reports:** itemized CSV and audit-ready PDF exports scoped to a credential's current
  cycle window.
- **Quality-of-life:** public badge links (Credly / Credential.net), archived credentials,
  in-app docs, in-place editing of everything — no hand-editing the database.

## Quickstart

Requires Python 3.10+ (3.11+ recommended). No Node.js, no build step, no Docker required.

```bash
git clone <this-repo>
cd <this-repo>
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

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
version control. Set the `CE_TRACKER_DB` environment variable to keep it somewhere else.
Backups are just a copy of that one file.

## Using the tracker

1. **Add each credential** with its real rules. The form covers the whole schema: renewal
   type (`cpe_hours` / `exam` / `mandatory_module`), cycle start/end, reporting calendar
   (calendar-year or anniversary), hour total and unit, annual minimum (suggested or
   mandatory), grace period days, category min/max rules (nestable), dependencies, and
   "confirmed elsewhere" adjustment hours.
2. **Log activities** as you complete them — date, provider, title, hours, category, and
   *every* credential they apply to. Only hour-based (`cpe_hours`) credentials are offered
   as targets; exam-only credentials never accumulate hours.
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

## Architecture

Single-language Python stack, chosen for hackability over ceremony:

| Layer | Choice |
|---|---|
| Web framework | Flask, server-rendered Jinja2 templates |
| Interactivity | HTMX + a little vanilla JS — no SPA, no Node toolchain |
| Storage | SQLite via the stdlib `sqlite3` module |
| PDF export | ReportLab |

```
ce_tracker/
  db.py               schema, migrations, optional demo seed
  progress.py         all cycle/date/pace math (pure functions, no DB)
  views.py            row → template-context shaping
  routes.py           Flask routes (dashboard, CRUD, review, exports)
  reports.py          CSV/PDF generation
  calendar_ingest.py  event scoring + staging CLI (pure stdlib)
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
- Single-user by design. No auth, no multi-tenancy — run it on your own machine.
- The Flask dev server is fine for localhost use; put a real WSGI server (e.g. gunicorn)
  in front of it if you expose it beyond loopback.

## Roadmap

- Dockerfile for easier self-hosting
- Route-level test coverage (business logic is covered; HTTP layer isn't yet)
- Gmail ingestion into the same pending-review queue (local-only, same
  explicit-signal-first philosophy)
- Browser-assisted submission helper — pre-filling issuer portal forms with a required
  human "go" per record, never batch auto-submit

## License

Not yet chosen — this repository is being prepared for public release. A license file will
be added before publication.
