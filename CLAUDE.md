# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This repo currently contains only design artifacts — `ce-cpe-tracker-design.md` (system/data design) and `design/ui-handoff/` (UI prototype, see below) — no code has been written yet, and this directory is not yet a git repository. Treat `ce-cpe-tracker-design.md` as the source of truth for architecture, data model, and business-rule scope; don't assume implementation exists until you've checked.

When implementation starts, `git init` the repo (the user has approved this) and follow the build order in design doc §10: (1) migrate data, (2) progress-tracking report, (3) Gmail/Calendar aggregation staging, (4) reporting templates, (5) Claude-in-Chrome submission assist — in that order, not piecemeal. Per the tech stack decision below, step (1) targets SQLite instead of `credentials.yaml`/`activities.csv`, and step (2) is delivered as the local web dashboard (see UI design handoff) rather than a CLI report — the web app *is* the core deliverable now, not a bolt-on.

## UI design handoff (`design/ui-handoff/`)

A Claude Design handoff package (`@design/ui-handoff/README.md` + `CE Tracker Dashboard.dc.html`) provides a high-fidelity HTML prototype of a dashboard UI for this tracker — colors, typography, spacing, and interaction states are intentional and should be recreated closely; sample data is illustrative only.

- **Three alternative layouts in one file** — Command Center (sidebar + tabbed detail), All-at-a-glance (single scrolling table), Deadline-first (urgency cards sorted by days-remaining). Pick one (or merge ideas); don't build all three. Confirm the choice with the user before committing to it.
- **Framework decided (2026-07-14): Python-only, no React/Vue/Node.** Server-rendered HTML (Jinja2) with HTMX or vanilla JS for the tab/row-expand/approve-dismiss interactions — not a SPA. Chosen over React/Vue for prototyping speed and to keep the stack single-language (no Node.js toolchain) for both personal use and future forkers. See "Tech stack" section below for the full rationale.
- **Resolved: this project is now web-driven, not CLI-first**, superseding design doc §8's "no web app/UI" stance (see below). The dashboard is the core deliverable, not an optional add-on.
- Business logic (credential rules, progress-tracking algorithm, category math) still comes from `ce-cpe-tracker-design.md` §2–§4, not from the UI prototype — the prototype only specifies presentation and interaction, using illustrative sample data.
- Pending-review Approve/Dismiss in the prototype are both just client-side list removal; the real implementation must differ (Approve → commit to activity log per §5 of the design doc; Dismiss → mark source event ignored so it doesn't resurface) — this is a stronger version of the same human-approval constraint below.

## Hard constraints (do not violate)

- **No autonomous writes.** Every write to CE records, and any issuer-portal submission, requires explicit human approval. Aggregated data from Gmail/Calendar lands in a pending-review staging table/area for review — never write directly into the approved activities store.
- **No autonomous portal submission.** Submission automation (design doc §7) requires an explicit human "go" per record. No batch auto-submit, ever.
- **Private data never lives in the versioned public-facing code.** The SQLite database file (credentials, activities, evidence references — cert numbers, personal dates) and any evidence files must stay out of the tracked repo history — in a gitignored `data/` directory or a separate private repo — since the repo is intended to go public later. Where exactly this lives is still undecided (see CLAUDE.local.md); don't hardcode a location without checking.
- **Aggregation scope stays explicit-signal-only** (certificates, registrations, known-provider emails/calendar events) unless the user has signed off on adding keyword-based categories (reading, lecturing, board/volunteer work) — these are noted in the design doc as needing narrower, per-category query design first.

## Data model shape

The schema in §3 supports four renewal shapes, not one: `cpe_hours` (flat, split, or nested-category totals), `exam` (fixed expiration, no CE), `mandatory_module` (vendor-scheduled pass/fail), plus cross-cutting `renews` (stacking) and `depends_on` (cascade) relationships. Progress-tracking logic (§4) branches on `renewal_type` first, then `reporting_calendar` (`calendar_year` vs `cycle_anniversary` — these are different clocks, e.g. ISACA vs. ISC2/PMI).

## Tech stack (current plan — supersedes design doc §8)

Decided 2026-07-14: this tracker is **web-driven, not CLI-first**, in two phases. Business-rule logic (design doc §2–§4) is unaffected — only storage engine and delivery mechanism changed.

- **Phase 1 — personal use, local-only, no Docker.** Plain Python venv. Backend: Flask or FastAPI. Frontend: server-rendered Jinja2 templates + HTMX/vanilla JS (no React/Vue, no Node.js build step) — chosen for prototyping speed and a single-language toolchain. Storage: **SQLite** (file-based, zero-config, stdlib `sqlite3`), not the design doc's YAML+CSV — SQLite better suits the dashboard's query/aggregation needs (progress %, category sums, days-remaining sorts) without hand-rolling CSV parsing. Rationale for deferring Docker: at this stage there's one machine and no collaborators, so Docker's main benefit (cross-machine consistency) has no payoff yet — it would only add build/rebuild friction to the iteration loop.
- **Phase 2 — open source.** Same stack, with Docker (Dockerfile/docker-compose) added *at that point* to solve "works on my machine" for forkers. Still self-hosted only — no hosted/SaaS version; each user runs their own instance, matching the design doc's original no-SaaS intent.
- **Google/Gmail-Calendar API access (§5) is a separate, later concern** — needed only for the auto-aggregation feature, not for running the web app/dashboard at all. Skip it entirely until that feature is actually being built; seed SQLite manually (or via a small seed script) for feasibility testing in the meantime.
- `ce-cpe-tracker-design.md` has not been edited to reflect this — its §2–§4 (credential rules, data model shape, progress algorithm) remain authoritative; its §8 tech choices and §3 storage format (YAML+CSV) are superseded by this section.
