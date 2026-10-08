# Quick Start

New installations start with an empty business database and ordinary
operational workflows. No accounts or example content are seeded. The
existing local database was retained by the owner's latest instruction.
Schema head is `085b7a4e9012`; see [Hosting Preparation](HOSTING_PREPARATION.md)
for the empty-installation procedure and hosting requirements.

Read [Project Status](PROJECT_STATUS.md), the
[Approved Repair Contract](APPROVED_REPAIR_CONTRACT.md) and
[Implementation Plan](IMPLEMENTATION_PLAN.md) first. Historical deployment
evidence is in [Phase 6 Completion](PHASE6_COMPLETION.md).

## 1. Run an already configured workspace

Before launching, confirm privately that:

1. The existing `.venv` and installed packages belong to this project.
2. `.env` points to the intended development MySQL database and contains a
   valid secret key. Do not print or share its contents.
3. The database schema is compatible with the checked-out application.
   The current local development revision is `085b7a4e9012`; the Phase 6
   record's `d574ab56594f` is historical. Do not run schema changes to make an unexpected revision
   match a document.
4. Private material storage is configured outside `app/static`.
5. The development configuration uses `RESEARCH_DATA_PROVENANCE=development`
   and the approved retention setting `RESEARCH_RETENTION_DAYS=15`.

From PowerShell:

```powershell
Set-Location -LiteralPath 'C:\Users\abdul\Desktop\GraduationProject\AdaptiveEnglishLMS'
.\.venv\Scripts\python.exe run.py
```

Open [Application](http://127.0.0.1:5000/). All four roles use the same login.
Use the accounts deliberately provisioned for the intended installation,
with credentials entered privately.
An account's stored role routes it to the appropriate workspace; there is
no role selection on the form. A Researcher uses the same form and reaches
the separate research workspace. The old `/research/login` remains a
compatibility path.

The root URL `/` redirects by account role, with the shared login for an
anonymous visitor. `/health` is a basic application response, not
proof of database/schema readiness. Stop the local server with `Ctrl+C` in
the same terminal. The four-role manual review is documented in the integration
record; it is a bounded usage review, not comprehensive regression acceptance.

## 2. Prepare a new workspace deliberately

These are future, user-executed setup steps. They install packages and may
lead to database provisioning; P0 itself does not perform them.

### Python environment

Use a supported Python interpreter compatible with the pinned
`requirements.txt`. The dependency manifest records resolution against
Python 3.14.6; that historical note does not establish compatibility with
every interpreter or machine.

Only when this checkout has no existing `.venv`, create one and install the
manifest in your own terminal:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Do not recreate or replace an existing project environment just to follow
these examples.

### Local configuration

Create `.env` manually from the setting names in `.env.example` only if it
does not already exist. Supply real local values privately. The example
contains placeholders and leaves research retention blank; it is not a
finished configuration and should not replace an existing `.env`.

| Setting group | What to confirm |
|---|---|
| `FLASK_ENV` | Explicitly use `development` for a local workspace; unknown or empty names now refuse startup. |
| `SECRET_KEY` | A deployment-specific secret supplied privately. |
| `DATABASE_HOST`, `DATABASE_PORT`, `DATABASE_NAME`, `DATABASE_USER`, `DATABASE_PASSWORD` | The exact approved MySQL target and account. |
| `APP_TIMEZONE` | `Africa/Tripoli` for the center's local operation. |
| `MATERIAL_STORAGE_ROOT` | Private non-public storage, outside `app/static`. |
| `PAYMENT_PROVIDER_MODE` | `disabled`; no online gateway or payment simulation is available. |
| `RESEARCH_DATA_PROVENANCE` | `development` for operational evaluation on any host; only an explicitly approved actual study uses `study`. |
| `RESEARCH_RETENTION_DAYS` | `15`, as explicitly approved. |
| `RESEARCHER_EMAIL_ALLOWLIST` | Only approved provisioning addresses, kept in private configuration. It does not grant a role at login. |

`.env` is ignored by Git. Do not put passwords in command arguments,
documentation or screenshots. Keep backups, uploads and research data in
their approved private locations.

### MySQL and schema

Choose and approve a new database target before creating it. The later
clean-database stage of the repair will review the schema, exact migration
path, backup/recovery needs and verification steps. Creating a database or
applying an Alembic revision writes to that target.

There is intentionally no blind `upgrade` command in this guide. The
[Research Deployment Runbook](RESEARCH_DEPLOYMENT_RUNBOOK.md) contains the
earlier Phase 6 upgrade procedure from a specific parent revision; do not
replay it on a database that is already upgraded or has another revision.
Once the approved target and compatible schema are verified, use the launch
steps in section 1.

### Account provisioning

The project includes interactive `scripts/create_admin.py` and
`scripts/create_researcher.py` helpers. They create accounts in the configured
database and therefore require a confirmed target and an explicit decision
to provision. They are not P0 checks or automatic startup steps. Researcher
provisioning additionally requires the configured allowlist. Passwords are
entered through hidden interactive prompts and stored as hashes; existing
accounts should not be recreated.

## 3. Research retention and collection

Approved authentication is password-only; MFA is not required. The storage
policy is 15-day retention with a daily purge. The earlier local deployment
record describes `AdaptiveEnglishLMS-ResearchRetention` at 03:00 Libya local
time using `scripts/run_research_retention.py`. P0 has not checked whether
that task is currently enabled, running or pointing to the intended target.

The purge executes deletions, and `scripts/register_research_retention.ps1`
changes the Windows scheduler. Do not register a second task or run either
helper as part of a startup check. Review the approved target, task and
runbook first. Exclusions, configurations, subject links, export descriptions
and audit history survive retention; ordinary LMS records are not purged.

Launching Flask does not itself approve or start a study. A Researcher must
prepare the period/configuration and explicitly start collection according
to the research contract. Record required exclusions before doing so.
Operational evaluation remains `development` provenance even if hosted with
production HTTP settings; only an explicitly approved study uses `study`.
Operational review can now use the same Researcher export workflow, with a
separate source label and dataset manifest. It remains excluded from study
exports. Follow [Local Research Walkthrough](LOCAL_RESEARCH_WALKTHROUGH.md)
for collection setup, Student tasks, review and download.
The optional raw 1–5 question and existing
sampling/session semantics remain unchanged by the repair plan.

## 4. Tests during the transition

The owner requested deletion of the old tests and then explicitly declined
a new automated test package on 2026-10-06. Do not recreate or run tests for
this close-out. The old test runner and `tests/` directory are absent.
The [MySQL Test Transition](MYSQL_TEST_TRANSITION.md) is a historical record,
not an executable current procedure. Manual browser and static source/template
review are recorded separately from historical test results.
Any future automated database verification requires a separately authorized
owned isolated MySQL target; never substitute development or production.

## 5. Troubleshooting boundaries

| Symptom | Check |
|---|---|
| `/` returns 404 | Check that the launched checkout includes P1A; its root now redirects to login or the role dashboard. |
| Startup rejects a setting | Review the named configuration locally; do not expose secret values. |
| Missing table or column | Confirm the intended database and compatible revision; do not force a migration. |
| Invalid email/password | Confirm the existing active account; the allowlist does not turn an ordinary account into a Researcher. |
| Workspace access returns 403 | Check the account's stored role and status. Workspace separation is enforced server-side. |
| Collection configuration cannot activate | Confirm the explicit retention setting and period; activation and starting collection are separate steps. |
| New enrollment says Course price is unset | Set the actual Course price in the catalogue; migration does not invent a price from a historical fee plan. |
| Study has already started | New enrollment and re-enrollment remain allowed. Review the start date and late-entry notice; eligibility, teacher availability and capacity checks still apply. |

The owner-authorized local upgrade is recorded in
[Repair Integration](REPAIR_INTEGRATION.md), including the exact revision and
private backup/restore evidence. Do not use `create_all`, stamp a revision or
reset a database as a substitute for the migration chain. Historical fee-plan
and enrollment-reactivation bookmarks now open the relevant new review workflow.

For implementation progress and remaining limitations, use
[Project Status](PROJECT_STATUS.md). This guide does not replace production
deployment planning or authorize database, scheduler or collection changes.
