# Research deployment runbook (Phase 6 replacement)

This runbook moves an existing deployment from the superseded Phase 6 schema
(`b86838ce23db`) to the natural-use research schema (`d574ab56594f`) and then
to a pilot. It describes operations; it does not authorize them. Applying
migrations to a real database, deleting any real record, and starting real
collection each need their own explicit approval.

**Order matters.** Once the new application code is deployed, it expects the
new schema: the old schema does not have the research tables the portal
layout reads, so the application must not run against the old schema. The
steps below keep the service stopped from the code deployment until the
schema has been verified.

**Population rule.** Once a Researcher starts collecting, every eligible
Student (an active Student account that is not excluded) is collected
automatically, including Students created later. There is no inclusion step.
Every exclusion and every demonstration account must therefore be recorded
**before** collection starts (section 6).

## 1. Before the maintenance window

1. Confirm the target: the database name, the current Alembic revision
   (`flask db current` must print `b86838ce23db`) and the release that
   contains `69c4bae553fe` and `d574ab56594f`. If the revision differs, stop:
   never repair an unexpected revision by hand.
2. Read-only preflight of the six legacy tables (row counts only):
   `research_consent_documents`, `research_participants`,
   `research_consent_events`, `experiment_definitions`,
   `experiment_task_sets`, `experiment_tasks`. On the development database
   all six were observed empty on 2026-09-29 and again on 2026-10-01
   (read-only sessions).
3. Decide the deployment settings (see section 3). `RESEARCH_RETENTION_DAYS`
   must come from the centre's external arrangement; the application has no
   default.

## 2. Deployment requirements

- **Researcher authentication.** Approved email/password accounts use the
  dedicated login. The owners removed the second-factor requirement on
  2026-10-04; password authentication is the accepted contract.
- **Retention.** The owners selected 15 days on 2026-10-04, configured
  locally as `RESEARCH_RETENTION_DAYS=15`. Other deployments must supply
  their setting explicitly; no configuration can be activated without it.
- **External arrangements** (the adult-only population, what the centre
  tells Students, how an exclusion is requested) are handled by the centre
  outside the application and are not verified by it. The application shows
  Students no research notice of any kind.
- Isolated MySQL 8.0.46 verification covered backup restoration, fresh
  migrations, upgrade of the restored database, competing Student
  provisioning requests, a consistent export snapshot during concurrent
  writes, and retention. See `docs/PHASE6_COMPLETION.md` for the completed
  checks and deployment state.

## 3. Deployment settings

| Setting | Value |
|---|---|
| `RESEARCH_DATA_PROVENANCE` | `study` only on the real study deployment; `development` everywhere else |
| `RESEARCH_RETENTION_DAYS` | Whole days from the external arrangement (1-3650) |
| `RESEARCHER_EMAIL_ALLOWLIST` | The owners' approved researcher addresses, comma-separated |
| `APP_TIMEZONE` | The centre's timezone (defines the prompt budget day and the default export timezone; each export stores its own) |

## 4. Migration (maintenance window)

Both revisions must run **online** against the database: `flask db upgrade
--sql` (offline rendering) is refused, because both read rows before they
act.

1. Stop the application service so no request writes during the migration.
2. Take a **recoverable backup** of the whole database and verify it can be
   restored (restore it to a scratch database and check the row counts).
   MySQL DDL is not transactional: the backup is the only rollback for data.
3. Confirm again: `flask db current` prints `b86838ce23db`.
4. Apply the additive revision only: `flask db upgrade 69c4bae553fe`.
   It creates the nine research tables and turns every legacy refusal or
   withdrawal into an exclusion (`legacy_collection_exclusion`). It deletes
   nothing.
5. Apply the destructive revision straight after, with nothing in between:
   `flask db upgrade d574ab56594f`.
   - It first verifies that every legacy refusal or withdrawal is still
     excluded with basis `legacy_collection_exclusion`. A missing exclusion,
     a subject changed to included, or any other basis refuses the upgrade
     with nothing changed; stop and investigate (never edit rows by hand to
     make it pass).
   - With empty legacy tables it removes them.
   - With populated legacy tables it **refuses** and prints only table names
     and row counts. Review them. If the approved decision is to discard the
     legacy rows (the backup keeps them), re-run with exactly the printed
     counts:
     `flask db upgrade d574ab56594f -x legacy_research_disposition=discard -x legacy_research_reviewed_counts=<printed counts>`.
     If the counts changed, it refuses again.
6. Verify the schema: `flask db current` prints `d574ab56594f`; the nine
   `research_*` tables exist with their CHECK constraints (`ENFORCED = YES`
   in `information_schema.TABLE_CONSTRAINTS`), unique keys and indexes;
   `research_export_archives.content` is `LONGBLOB`; the six legacy tables
   are gone; every foreign key has no referential action; and the earlier LMS
   tables' row counts equal the pre-migration counts.
7. Start the application and check: LMS login, a Student dashboard (no
   research link, notice or indicator), a Teacher dashboard, an
   Administrator dashboard (no research entry), and the research login page.
   With no active configuration nothing is collected.

## 5. Researcher access

1. Put the approved addresses in `RESEARCHER_EMAIL_ALLOWLIST`.
2. Run `python scripts/create_researcher.py` interactively for each address.
3. Researchers sign in only at `/research/login` (the LMS login refuses them).

## 6. Pilot enablement (separately authorized)

1. **Before anything collects**, the operator records the exceptions with
   the operator tool, in a private terminal on the server:
   - every exclusion the centre's external process requires:
     `python scripts/research_operator.py exclude EMAIL`;
   - every demonstration or development Student account:
     `python scripts/research_operator.py mark-demo EMAIL` (its data is
     stored as `demo` and never exported);
   - review: `python scripts/research_operator.py list-excluded` (prints
     emails: keep the output private). Researchers see the same list by
     pseudonymous code under **Exclusions**.
2. A Researcher creates a configuration draft (period and sampling settings;
   there is no area or Student selection), reviews it, and activates it.
   Activation records the retention in force and **does not start
   collection**.
3. A Researcher presses **Start collecting**. From then on every eligible
   Student is collected automatically within the period; a pseudonymous
   subject is created at a Student's first collected request.
4. Later exclusions take effect at once and close open sessions. A lifted
   exclusion is recorded with
   `python scripts/research_operator.py reinstate EMAIL` (a legacy exclusion
   additionally needs `--lift-legacy-exclusion`, stating that the external
   process changed it).
5. During the pilot, check the dashboard daily: delivery quality (invalid,
   duplicate, late, dropped), sampling eligibility, prompt display and
   response rates, deferral reasons, and coverage. Verify with a few Students
   that the question is understood (comprehension check).
6. Pause at any time with **Pause collection**; it closes open sessions.

## 7. Exports and retention

Each export is stored once as an immutable archive and served unchanged on
every download. `python scripts/research_operator.py purge-expired` reports
the sessions (with events and prompts) and the export archives that the
retention would remove; `--execute` deletes them permanently and is audited.
A purged export's download then answers 410 Gone and is never rebuilt.
Running the purge on the real database needs its own authorization.

The owner-authorized local daily job uses `scripts/run_research_retention.py`.
It reads the same environment, executes expiry and logs counts under ignored
`instance/research-retention.jsonl`. To register it on Windows, run
`scripts/register_research_retention.ps1`; it defaults to 03:00 in the
computer's local timezone. It runs as the current user while signed in,
and `StartWhenAvailable` catches a missed run when the computer is available.
It ignores overlapping invocations. Task name:
`AdaptiveEnglishLMS-ResearchRetention`. Registration refuses to overwrite an
existing task. A server deployment should schedule this entry point under
its service account instead of depending on a developer's login.

## 8. What this runbook does not claim

Completing these steps does not mean research data was collected, that the
question is valid, that a model exists, or that any approval was obtained.
