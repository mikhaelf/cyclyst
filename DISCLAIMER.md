# Legal disclaimer

**Status: BETA.** This project is under active development. Expect bugs, schema changes, and
rough edges. Back up `data/tracker.db` before upgrading.

Cyclyst is provided "as is," without warranty of any kind, express or implied, including
but not limited to warranties of merchantability, fitness for a particular purpose, and
non-infringement. To the maximum extent permitted by applicable law, the author is not liable
for any claim, damages, missed deadline, lapsed credential, or other liability arising from
use of this software. **You are solely responsible for verifying your own CE/CPE compliance
and for the accuracy of anything you submit to an issuer.** See "Human-in-the-loop" below.

## 1. Personal use, one principal

Cyclyst is designed to be run by one person, tracking that person's own credentials, on
that person's own machine. It assumes the person running the tool (the "principal") and the
person whose CE record it tracks are the same individual.

Using it to track or submit CE on someone else's behalf — as a service, on request, or as
part of a job duty — is outside its intended use and may create confidentiality, data
protection, or professional-conduct obligations this tool was never designed to satisfy
(handling another person's certification numbers, personal dates, and portal credentials).
If you want to do this, get your own legal/compliance sign-off first; nothing here is
designed for it.

## 2. Unofficial, unaffiliated tool

Cyclyst is an independent, unofficial project. It is **not authorized, endorsed,
sponsored, or officially supported by ISC2, ISACA, PMI, IAPP, Google, Credly, or any other
credential issuer, examination body, or vendor** referenced in this codebase, its
documentation, or its UI. Any product, provider, or organization names that appear are used
only to identify the credentials and portals this tool helps you track — nothing more.

Issuer portals, form layouts, category taxonomies, and submission rules change without
notice and without informing this project. **Use at your own risk.**

## 3. No institutional endorsement

No employer, professional association, or standards body endorses or recommends this tool.
It exists to help individuals — including employees maintaining certifications their job
requires — stay ahead of their own renewal deadlines. It is not vetted, certified, or
approved by any organization for that or any other purpose.

## 4. Privacy and security posture

Cyclyst is built to run entirely on hardware and accounts you control:

- **No hosted/SaaS component.** There is no server operated by the developer. The app runs
  locally (`python run.py`) against a local SQLite file.
- **No telemetry, no analytics, no data collection.** Nothing about your usage, your
  credentials, or your CE records is sent to the developer or to any third party by this
  codebase.
- **No credentials handled by this tool.** Any login to an issuer portal (e.g. `cpe.isc2.org`)
  happens in your own browser session, under your own account, with your own MCP/browser-
  automation configuration if you choose to use one — this project never stores, transmits,
  or has access to those credentials.
- **Your data stays on your machine — for the core tracker.** `data/tracker.db` and any
  uploaded certificate files are processed locally and are gitignored — they are never
  committed, uploaded, or transmitted anywhere by this codebase.
- **The two optional MCP-assisted features are the exception.** Calendar ingestion and ISC2
  portal submission automation are both opt-in, and both necessarily send data off your
  machine for the session in which you use them: calendar events pass through Google's API to
  a connected AI-assistant session, and whatever's on the portal page passes through Claude
  for Chrome to Anthropic while it processes that page. See §6 below and the README's
  "Prerequisites" section before enabling either.

This design choice reduces risk, but it does not eliminate it. You are responsible for the
security of your own machine, browser, accounts, and backups. **Use at your own risk.**

## 5. Human-in-the-loop, not delegated accountability

This tool is built to *accelerate* tracking and preparation — it does not, and is not meant
to, *delegate accountability* for your CE compliance or for what gets submitted to an issuer.

- Nothing is written to your approved CE record without your review.
- Nothing is ever submitted to an issuer's portal in bulk or without your explicit,
  per-record "go." There is no batch auto-submit mode, and there will not be one.
- Any suggested category, domain, or form-field mapping (e.g. the ISC2 submission helper) is
  a **suggestion for you to confirm**, never an auto-applied value.
- Before you approve, submit, or export anything, **you are responsible for checking that it
  is accurate** — the dates, hours, category, and description are only as good as what was
  logged, and only you can confirm they're correct against your actual records.

If a deadline, an audit, or a portal submission goes wrong, the responsibility for catching
it before it happens sits with the human at the keyboard — that's the design, not a gap in
it.

## 6. Third-party AI-assistant tools (Claude for Chrome, MCP connectors)

Calendar ingestion and ISC2 portal submission automation are both built to be driven by a
separate, MCP-connected AI-assistant session — this repo does not ship or control that
session. Using either feature means accepting a second set of terms and risks on top of this
project's own:

- **Claude for Chrome is a separate Anthropic product with its own terms of use.** Portal
  submission automation requires it, granted site access, actively reading and acting on the
  portal page. Before enabling it on any page showing your CE data, read and accept Claude
  for Chrome's own terms of use and documentation, and understand what you're granting it:
  once active on a page, it can read that page's content and take actions on it (click,
  type, submit), and that content is processed by Claude to do so.
- **An MCP connector grants its full scope, not a scope narrowed to this project's use of
  it.** The Google Calendar connector used for calendar ingestion, and the Claude-in-Chrome
  browser-automation tool used for portal submission, both grant the AI-assistant session
  whatever access that connector provides for as long as it's attached — e.g. the Calendar
  connector can read your entire calendar, not just events this project's scoring logic
  looks at. This project's own code only ever queries or acts within that broader grant; it
  cannot narrow the grant itself.
- **This is a deliberate, opt-in trade, not a default.** Neither feature is required for the
  core tracker, and neither activates unless you set up and attach the relevant connector
  yourself for a given session. You are responsible for deciding, per session, whether
  granting that access is acceptable to you, and for reviewing Anthropic's and Google's own
  terms and privacy practices for the tools you choose to attach — this project has no
  visibility into or control over how those third parties handle data once it reaches them.
