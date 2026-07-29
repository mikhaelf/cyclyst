# Pre-publication checklist

Steps to complete before flipping this repository public. Delete this file as the last step
(or keep it — nothing in it is sensitive once the items are done).

## Blockers — must be done

- [x] **Scrub the leaked member ID from git history.** Done 2026-05-13: rewrote history in
  place with `git filter-repo --replace-text` (in a fresh standalone clone, not any of the
  entangled worktrees) to replace the real ISACA member ID with `REDACTED_MEMBER_ID`
  everywhere it appeared — both the original leak commit's `body.html` placeholder and this
  checklist's own later prose quoting the number. Force-pushed the rewritten `main`, then
  deleted the 7 other pushed branches that were carrying the same leak in their ancestry
  (all confirmed already squash-merged into `main` via PRs #2–#10, so nothing unique was
  lost) plus the now-merged PR #11 source branch. Verified a pickaxe search for the leaked
  value on the fresh clone returns zero hits, and repo-renamed `agentic-ce-sync` → `cyclyst` right after
  (still private). Note: other **local-only** worktrees on this machine that were checked out
  to the deleted branches still hold the pre-rewrite blobs until those worktrees are cleaned
  up too — not a publishing risk (nothing of GitHub's), just local hygiene.
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
  Re-verified clean 2026-05-13 after the history rewrite above (the only working-tree hit for
  `mikhael|felker` is the LICENSE copyright line, which is expected and fine to publish).

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
