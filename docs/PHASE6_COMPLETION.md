# Phase 6 completion record

Date: 2026-10-04. Scope: the natural-use research replacement and its
owner-authorized local development deployment. No later model-training or
adaptive-intervention phase is included.

**Status: implementation and local development deployment complete.**

## Accepted deployment settings

- Researcher authentication uses the dedicated email/password login. The
  owners removed the second-factor requirement.
- Retention is 15 days, supplied through the ignored local environment.
- One approved Researcher address is configured and its active account was
  provisioned using the supplied password through the Argon2id service.
  Credentials are not stored in repository source or this report.
- Local data provenance remains `development`. Only a real study deployment
  should set `RESEARCH_DATA_PROVENANCE=study`; demonstrations are marked
  separately and are not exported as study data.

## Verification completed

### MySQL 8.0.46 / InnoDB

Verification used temporary, loopback-only MySQL instances, separate from
the development database, with synthetic students and collection periods.

- A whole-development-database backup was restored: 59 tables and 133 rows
  matched before the upgrade.
- The restored database upgraded through `69c4bae553fe` and
  `d574ab56594f`. All 52 earlier LMS tables were unchanged by row count and
  canonical row digest; nine new research tables were present.
- A fresh database completed the entire Alembic migration chain.
- Automatic student collection, the optional feedback question, role
  separation and exclusions worked against MySQL.
- Competing first collection requests produced one subject/link.
- A concurrent writer did not change an export's established InnoDB
  snapshot. A subsequent export included the write; the first archive's
  bytes stayed unchanged.
- Simulated 15-day expiry removed three sessions and two export archives;
  purged downloads returned 410.
- Development data was unchanged after these isolated checks.

### Browser and retention job

- All six collector and uncertain-feedback scenarios passed in headless
  Chrome against a running Flask server backed by MySQL: **6 passed**.
- The daily job's expiry and fail-closed configuration checks passed under
  strict warnings: **2 passed**.
- The complete strict-warning run covered all 163 existing test files:
  **7,200 passed, 4 skipped, 1 failed**. The sole failure was a stale
  workspace expectation that the removed MFA requirement should still say
  "Not implemented". That expectation was corrected; no product behavior
  was changed for the repair.
- The entire affected shard was rerun under strict warnings: **1,001
  passed**. The other seven shards had passed, so the final result for the
  existing suite is **7,201 passed, 4 skipped**. With the two new retention
  tests, the final coverage is **7,203 passed, 4 skipped across 164 files**,
  with no remaining failure or error. The four skips are the inherited
  Windows IANA-time-zone cases.
- Workspace and retention suites also passed together: **39 passed**.

## Development deployment completed

- Confirmed the exact parent `b86838ce23db`, the verified backup digest and
  unchanged data before mutation; no other application connection was
  attached to the database during the preflight.
- Applied only `69c4bae553fe`, then `d574ab56594f`. Current development
  revision is **`d574ab56594f`**. Nine research tables exist and their MySQL
  CHECK constraints are enforced.
- All original rows in the 52 earlier LMS tables match their pre-migration
  digests. The separately authorized Researcher account is the only new
  ordinary-LMS row; no existing account was repurposed.
- The Researcher's CSRF-protected login and dashboard returned 302/200;
  Administrator access was refused with 403. Student, Teacher and
  Administrator dashboards each returned 200, and each was refused access
  to the research dashboard with 403.
- There are **zero collection configurations**. No real collection period
  or real research dataset was created during close-out.
- Registered **`AdaptiveEnglishLMS-ResearchRetention`** daily at **03:00
  Libya Standard Time (UTC+02:00)**. Its first scheduled-task execution
  completed with **LastTaskResult = 0**, reporting 15-day retention and zero
  expired sessions/events/prompts/archives. The next scheduled run was
  2026-10-05 at 03:00.

The first multi-client verification helper reused one Flask application
context and therefore reused Flask-Login's cached Researcher identity for
a Student request, producing a false 403. Read-only verification was rerun
with a separate context per request; all role checks above passed. No
application authorization change or migration retry was needed.

## Retention operation

`scripts/run_research_retention.py` removes expired sessions, their events
and feedback prompts, and export archive bytes. Session expiry uses last
activity; an archive expires with its oldest included session. A daily run
can remove an eligible record at the next scheduled run after the 15-day
threshold. Subjects/links, exclusions, configuration versions, export
descriptions and audit records remain so exclusions and history stay
enforceable; the job does not remove ordinary LMS records.

The Windows registration script creates
`AdaptiveEnglishLMS-ResearchRetention` at 03:00 local time, as the current
signed-in user, with `StartWhenAvailable` and overlapping runs disabled.
It logs counts only under ignored `instance/research-retention.jsonl` and
returns a failing exit code if expiry cannot run. On a server, schedule the
same entry point under its service account.

## Recovery and study startup

The verified backup and operational verification outputs stay under ignored
`instance/phase6_operations/`; they contain local database material and are
not committed. MySQL DDL is not transactional; recover the pre-migration
data from the backup if required.

The deployment does not invent a real research period or start collection.
A Researcher creates and activates a configuration, then uses **Start
collecting** for its period. Every eligible active Student is included
automatically, including future accounts; ordinary portals show no research
link, notice or indicator. The optional frustration question is retained.

These checks establish local implementation and deployment behavior with
synthetic data. They do not establish questionnaire validity, real study
results, model quality, assistive-technology behavior, real device coverage
or behavior under actual multi-server clock skew.
