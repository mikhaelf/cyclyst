# SaaS Migration Plan — Local CE/CPE Tracker → Hosted Service

*Drafted 2026-07-15. Status: **under consideration**, not committed. This plan explores what
going from the local single-user app to a hosted SaaS would take. It deliberately supersedes
the design doc's original "no SaaS, self-host only" stance **only if and when a milestone
below is actually green-lit** — until then, the self-hosted/open-source path in CLAUDE.md
remains the plan of record.*

Grounding: as of this writing the app is a Flask app-factory (`ce_tracker/`), raw `sqlite3`
with a hardcoded `data/tracker.db` path, server-rendered Jinja2 + vanilla JS, **no auth
anywhere**, ~2,100 lines of Python.

---

## 1. The honest framing — what SaaS actually changes

The hard part of this migration is not cloud infrastructure — it's that the app is
architecturally single-user and single-trust:

1. **No concept of a user exists.** Every table (`credentials`, `activities`, `categories`)
   and every query in `routes.py` assumes one owner. Multi-tenancy means adding a `user_id`
   to essentially every table and scoping *every single query* — a miss anywhere is a data
   leak between customers.
2. **Custodianship of other people's sensitive data.** Cert numbers, member IDs, renewal
   dates, evidence PDFs — and eventually Gmail/Calendar OAuth tokens. That brings backups,
   deletion requests, a privacy policy, and breach responsibility.
3. **The Gmail aggregation feature changes category entirely.** Reading a user's Gmail
   requires a Google *restricted scope*. For a multi-user hosted app, Google requires OAuth
   app verification **plus an annual third-party security assessment (CASA), historically
   ~$500–$4,500+/yr**. Calendar-readonly is only a "sensitive" scope — verification but no
   paid assessment.

**Consequence for the roadmap:** design the SaaS around Calendar ingestion + manual entry +
the Sheets pull. Keep Gmail scanning out of scope (or as a local-only companion tool).
Revisit only if the product earns it.

---

## 2. Target architecture on GCP (recommended)

GCP is genuinely the better fit, not just a preference: the project is already
Google-ecosystem-centric (Sheets as source of truth, Google OAuth is the natural login,
Calendar/Gmail APIs), and Cloud Run's scale-to-zero has the best low-traffic economics of
any container platform.

| Concern | GCP choice | Why / notes |
|---|---|---|
| App hosting | **Cloud Run** | Runs the Docker container, scales to zero (≈$0 idle), HTTPS included. The Docker work is already planned for the open-source milestone — same artifact. |
| Database | **Cloud SQL for Postgres** | The boring, correct choice. Smallest instance ≈ $10–30/mo — this is the cost floor; it does not scale to zero. |
| Login | **Google Sign-In via Identity Platform** (or Authlib directly in Flask) | Users are certification professionals with Google accounts; do not build password auth. |
| Evidence files | **Cloud Storage (GCS)** | Private bucket, signed URLs for download. Replaces "files on disk." |
| Secrets / OAuth tokens | **Secret Manager** + encrypted columns | Per-user Google tokens never in the DB in plaintext. |
| Scheduled ingestion | **Cloud Scheduler → Cloud Run jobs** | Replaces manual ingest runs. Still lands in pending-review — the no-autonomous-writes constraint carries over unchanged and works fine multi-tenant. |
| Environments | Two GCP projects: `cpe-staging`, `cpe-prod` | Project-level isolation is GCP's cleanest boundary. |

**AWS equivalent** (for completeness): App Runner (or ECS Fargate) / RDS Postgres / Cognito /
S3 / EventBridge Scheduler. It all works; friction points are Cognito being clunkier than
Google sign-in on GCP, and App Runner's weaker scale-to-zero. Nothing needs AWS-only
services, so the GCP preference costs nothing.

---

## 3. Tradeoffs worth deliberating

### SQLite → Postgres, and when
The code calls `sqlite3` directly with SQLite-flavored SQL (`INSERT OR REPLACE`,
`AUTOINCREMENT`, dynamic `ALTER TABLE` migrations in `db.py`). Options:

- **Introduce SQLAlchemy** (the standard Python DB toolkit) as part of the migration.
  Upside: dialect differences handled; SQLite stays usable for local dev/tests while prod
  runs Postgres. Downside: a real rewrite of `db.py` and every query in `routes.py`, and an
  ORM is a new abstraction to learn.
- **Contrarian option — stay on SQLite, one DB file per user** (Litestream for
  replication/backup). Tenant isolation becomes trivial (separate files); code barely
  changes. Downside: unconventional, awkward with Cloud Run's stateless containers, and
  cross-user queries (admin views, metrics) get painful. Viable to ~dozens of users;
  **Postgres is the choice that won't need re-litigating.**

### How multi is the multi-tenant, really?
Decide who this is for: **(a)** just me, hosted; **(b)** me + a handful of colleagues;
**(c)** the public. Each step up adds cost: (b) needs auth + tenant scoping; (c) additionally
needs billing (Stripe), abuse handling, support, and the legal wrapper. Many "I want a SaaS"
projects are happiest at (b).

### Gmail scope
See §1 — Calendar-only ingestion keeps the app out of Google's restricted-scope/CASA regime.

### SaaS vs. the already-planned open-source path
Not exclusive — containerization, config-via-env-vars, and Postgres-compatibility work
serves both. But the private-data-location decision (see CLAUDE.local.md) needs a real
answer *before* either path, because both involve the repo going public or the data leaving
this machine.

---

## 4. Milestones

Each milestone is independently shippable and a legitimate stopping point.

### M0 — Tests first (prerequisite, not optional)
Unit tests for `compute_progress()` (pure date math: leap years, calendar-year vs.
anniversary windows, on-track formula), plus route tests against a temp SQLite DB
(monkeypatch `DB_PATH`). Every later milestone rewrites the storage layer or query paths —
without tests there is no way to tell whether the Postgres port broke the CPE math.
*(Already priority #2 on the existing open-items list.)*

### M1 — Twelve-factor cleanup + Docker
Move `DB_PATH` and all config to environment variables; add `gunicorn` (Flask's dev server
is not production-grade); write the Dockerfile; app runs identically in Docker locally. No
behavior change. **Also the already-planned open-source milestone — zero wasted work even if
SaaS stalls.**

### M2 — "SaaS for one" ← decision gate
Deploy the still-single-user app to Cloud Run **behind Identity-Aware Proxy (IAP)** — a GCP
feature that puts a Google-login wall in front of the app and only admits allowlisted
accounts (i.e., just me). Database: Cloud SQL now, or SQLite + Litestream to defer the DB
cost. Use the hosted version daily. This teaches deploys, secrets, logs, backups, and cost —
all the ops of SaaS — with zero multi-tenant rewrite and zero exposure of anyone else's
data. **Honest checkpoint: if hosted-for-me is all that was wanted, stop here at ~$0–15/mo.**

### M3 — Postgres + SQLAlchemy
Rewrite the data layer; one-shot migration script (read `tracker.db`, insert into Postgres);
verify by running both backends against the M0 test suite and diffing dashboard output
row-for-row. Keep SQLite as the dev/test backend via SQLAlchemy.

### M4 — Auth + multi-tenancy (the data-leak-risk milestone)
Add `users` table; `user_id` FK on `credentials` (everything else scopes through it); Google
Sign-In; a request-scoped "current tenant" so no query can forget the filter (centralize —
every DB accessor takes the user, never a global). Invite 2–5 personally-known beta users.
Gets the adversarial isolation test suite (§5).

### M5 — Files + scheduled ingestion
Evidence uploads to GCS with signed URLs; Cloud Scheduler-driven **Calendar** ingestion per
user into their pending-review queue; per-user OAuth token storage via Secret Manager.
(Gmail deferred per §1.)

### M6 — Public hardening (only if going beyond friends-and-family)
Automated Cloud SQL backups + point-in-time recovery, error monitoring (Sentry or Cloud
Error Reporting), rate limiting, account-deletion flow, privacy policy/ToS, Stripe if paid.

---

## 5. Testing strategy, mapped to the risks

- **Business-rule regression (M0, runs forever):** pure-function tests on `progress.py`;
  golden-output tests on dashboard data (given this fixture DB, these exact
  statuses/percentages come out). The safety net for every storage rewrite.
- **Storage-port correctness (M3):** same suite against SQLite and Postgres (SQLAlchemy
  makes this a parametrized fixture); plus the migration-script diff on real data.
- **Tenant isolation (M4 — the one that really matters):** explicit adversarial tests —
  create user A and user B, assert every route returns nothing of A's when authenticated as
  B, *including* edit/delete by ID (`POST /activities/123/delete` where 123 belongs to A
  must 404, not succeed). Write these before the multi-tenant refactor, as a checklist of
  every route.
- **Deploy safety (M2+):** staging project gets every deploy first; post-deploy smoke test
  (log in, load dashboard, add/delete a test activity). Cloud Run revisions give one-click
  rollback.
- **Skip for now:** load testing, chaos anything, multi-region — theater at this scale.

---

## 6. Cost reality check

- **M2** (hosted for me): ~$0–5/mo with Litestream, ~$15–35/mo with Cloud SQL.
- **M4–M5** (small multi-tenant): ~$20–50/mo — Cloud SQL dominates; Cloud Run stays near zero.
- The step-function costs are non-infrastructure: the Gmail CASA assessment if ever added,
  and personal time on support/ops.

---

## 7. Recommendation

Do **M0–M2 regardless** — they're shared with the open-source plan and end with a hosted
version in daily use. Treat **M2 as the decision gate** for whether multi-tenant SaaS (M3+)
is actually wanted, and design the SaaS as **Calendar-only ingestion** to stay out of
Gmail's restricted-scope regime.
