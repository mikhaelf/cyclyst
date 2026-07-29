# Pre-publication checklist

Steps to complete before flipping this repository public. Delete this file as the last step
(or keep it — nothing in it is sensitive once the items are done).

## Blockers — must be done

- [ ] **Scrub the leaked member ID from git history.** Commit `fae1bcc` committed a real
  ISACA member ID (`REDACTED_MEMBER_ID`) as the member-ID form placeholder in
  `ce_tracker/templates/partials/body.html`. The working tree is fixed (now `e.g. 123456`),
  but the value is still reachable in history. Two options:
  - **Recommended — publish with fresh history:** create the public repo from a single
    squashed initial commit (`git checkout --orphan public && git commit`), keeping this
    repo as the private full-history original. Simplest, zero risk of missing something,
    and the public repo starts clean.
  - Alternative: rewrite history in place with `git filter-repo --replace-text`, then
    force-push. More fiddly; only worth it if preserving the commit-by-commit history
    publicly matters.
- [x] **Choose a license** and add `LICENSE`. Decided 2026-07-28: MIT. The SaaS milestone
  is still only "under consideration," not committed, so the AGPL hedge wasn't worth the
  friction it adds for forkers; revisit only if SaaS actually gets green-lit.
- [ ] **Confirm the private-data location as final** (gitignored `data/` inside the repo —
  the de facto choice). It has to be settled before publishing because the README documents
  it.
- [ ] **Final sweep on the exact tree being published:**
  ```bash
  git ls-files                          # nothing unexpected tracked
  git grep -inE 'mikhael|felker|@gmail|spreadsheets' -- .
  git grep -in 'credly.com/badges/' -- . | grep -v example   # only fictional badge URLs
  git log --all --name-only --pretty=format: | sort -u   # data/, *.db never appear
  ```
  Verified clean as of 2026-07-16 except for the member-ID history item above.

## Should do

- [ ] **Decide what happens to `CLAUDE.md`.** It's tracked and would go public. It's
  coherent as a contributor guide (and references `CLAUDE.local.md`, which is gitignored
  and stays private), but re-read it with public eyes — e.g. the Google Sheet mention is in
  the *local* file only (verified), but confirm nothing personal has crept in since.
- [ ] **Decide whether `saas-migration-plan.md` ships publicly.** Nothing sensitive in it
  (verified), but it reveals commercial plans; keeping it private is a legitimate choice.
- [x] **Add a README screenshot** taken in demo mode. Done 2026-07-28: two screenshots
  (`docs/screenshots/dashboard-overview.jpg`, `docs/screenshots/pending-review.jpg`) captured
  against `CE_TRACKER_DEMO=1`, fictional data only. Along the way, found and fixed a demo-seed
  bug: one pending-review row was tagged `"gmail"` as its source, which doesn't exist as a
  real ingestion pipeline (only `calendar` and `transcript_pdf` do) — would have visibly
  contradicted the README's "Gmail ingestion... not built yet" line.
- [ ] **Repo metadata:** name, description, topics (`cpe`, `continuing-education`,
  `cissp`, `flask`, `htmx`, `self-hosted`), and disable features you won't tend (wiki,
  projects).

## Optional / can follow after publishing

- [ ] Vendor `htmx.min.js` into `ce_tracker/static/` to drop the unpkg CDN dependency
  (fully offline app, and no third-party request from users' browsers).
- [ ] Dockerfile (M1 of `saas-migration-plan.md` — also serves pure self-hosters).
- [ ] Route-level tests (remainder of M0).
- [ ] CONTRIBUTING.md / issue templates, if inviting contributions rather than just
  publishing.
