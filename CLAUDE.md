# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

A working Flask/SQLite web dashboard (the `ce_tracker/` package) for tracking CE/CPE renewal requirements across multiple professional credentials. It is in daily personal use and being prepared for open-source release — see `README.md` for user-facing docs and `PUBLISHING-CHECKLIST.md` for what remains before the repo goes public.

Key documents:
- `README.md` — user-facing: motivation, quickstart, usage, architecture.
- `ce-cpe-tracker-design.md` — original system design. Its §2–§4 (per-issuer credential rules, data model, progress algorithm) remain **authoritative for business logic**; its §8 tech choices and §3 storage format (YAML+CSV) are superseded (see banner at its top and "Tech stack" below).
- `design/ui-handoff/` — the UI prototype the dashboard was built from, kept as design history; its README records what was chosen (Command Center layout) and holds the design tokens `ce_tracker/static/style.css` follows.
- `saas-migration-plan.md` — a hosted-SaaS version is under consideration, not committed; the self-hosted/open-source path remains the plan of record.

Run it: `python run.py` (venv + `pip install -r requirements.txt`). Tests: `python -m unittest discover -s tests` — pure functions and in-memory SQLite only; no test may touch a real database file. Env vars: `CE_TRACKER_DB` (database path override), `CE_TRACKER_DEMO=1` (seed fictional demo data into an empty database).

## Hard constraints (do not violate)

- **No autonomous writes.** Every write to CE records, and any issuer-portal submission, requires explicit human approval. Aggregated data from Gmail/Calendar lands in a pending-review staging table/area for review — never write directly into the approved activities store.
- **No autonomous portal submission.** Submission automation (design doc §7) requires an explicit human "go" per record. No batch auto-submit, ever.
- **Private data never lives in the versioned public-facing code.** The SQLite database file (credentials, activities, evidence references — cert numbers, personal dates) and any evidence files must stay out of the tracked repo history — in the gitignored `data/` directory (the de facto choice; not yet confirmed as final, see CLAUDE.local.md) — since the repo is intended to go public.
- **Aggregation scope stays explicit-signal-only** (certificates, registrations, known-provider emails/calendar events) unless the user has signed off on adding keyword-based categories (reading, lecturing, board/volunteer work) — these are noted in the design doc as needing narrower, per-category query design first.

## Data model shape

The schema supports four renewal shapes, not one: `cpe_hours` (flat, split, or nested-category totals), `exam` (fixed expiration, no CE), `mandatory_module` (vendor-scheduled pass/fail), plus cross-cutting `renews` (stacking) and `depends_on` (cascade) relationships. Progress logic branches on `renewal_type` first, then `reporting_calendar` (`calendar_year` vs `cycle_anniversary` — different clocks, e.g. ISACA vs. ISC2/PMI).

Implemented scope: `cpe_hours` and `exam` are fully computed (in `ce_tracker/progress.py`, on every request — progress is never stored); the dependency cascade (`depends_on` / `depends_on_any_of`) is implemented; `mandatory_module` and `renews` have schema columns reserved but no computation — add that only when a tracked credential actually needs it.

## Tech stack

Web-driven, not CLI-first (decided 2026-07-14, superseding design doc §8). Single-language Python stack, chosen for prototyping speed and so forkers need no Node toolchain:

- Flask + server-rendered Jinja2 templates + HTMX/vanilla JS — no SPA, no build step.
- SQLite via stdlib `sqlite3` (file at `data/tracker.db`, gitignored); ReportLab for PDF export.
- `pypdf` for local-only certificate-upload text extraction (see `ce_tracker/certificate_parse.py`
  and `ce-cpe-tracker-design.md` §11) — no network call, text-layer PDFs only for now.
- No Docker yet — planned alongside the open-source release (see `PUBLISHING-CHECKLIST.md`), not before.
- **Google API access is deliberately absent.** Calendar ingestion (`ce_tracker/calendar_ingest.py`) is pure stdlib: a live session with calendar access (e.g. Claude with the Google Calendar connector) exports events to JSON and hands them to the module's CLI, which scores and stages candidates into pending review. Gmail ingestion is unbuilt; a fully automated API-client pipeline only matters for the SaaS path.
