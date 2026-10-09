# Adaptive English LMS

An internal learning management system for an English language learning
center. The application is a Flask modular monolith with server-rendered
pages, SQLAlchemy, Alembic/Flask-Migrate and MySQL through PyMySQL.
Administrator, Teacher, Student and Researcher accounts share the same
email/password login. Their stored account roles determine their workspaces
and server-side permissions.

The approved repair preserves this architecture and improves it in stages;
it is not a rewrite. Interface text, code and repository documentation are
English. Reports to the project owners are Arabic.

## Requirements for local development

These requirements apply to a **new installation**. An existing configured
workspace keeps its own `.env`, database and private files; do not replace them
with example configuration or initialize its database again.

| Requirement | What to prepare |
|---|---|
| Operating system | Windows, Linux or macOS with the pinned Python runtime and compatible package wheels. The deployed build targets Linux x86_64. Other CPU architectures need their dependency installation verified separately. |
| Python | **CPython 3.14.6**, matching `.python-version`; `pip` and `venv` must be available. Check `python --version` before installation. |
| Database | **MySQL 8 / InnoDB**, with `utf8mb4`, a running database service and a dedicated database account. The complete migration chain was rehearsed on MySQL **8.0.46**. SQLite and MariaDB are not supported substitutes. |
| Source and packages | Git and internet access for cloning and the initial `pip` installation. Use an isolated virtual environment. |
| Browser | A current Chrome, Edge, Firefox or Safari with JavaScript and cookies enabled. Speaking recording needs microphone permission and HTTPS or `localhost`/loopback. |
| File storage | A writable private directory outside `app/static`, plus enough disk space for uploads, research archives and backups. |
| Timezone | IANA timezone data; install `tzdata` as shown below, especially on Windows and minimal Linux. The centre uses `Africa/Tripoli`. |

Flask/Jinja serves the application directly. **Node.js, npm, React, a GPU,
an ML service and ffmpeg are not runtime requirements.** The repository already
contains the browser assets. CPU, RAM and storage sizing depend on concurrent
users and uploaded media; no load-tested universal hardware minimum is claimed.
MySQL and the web process both need their own resource allowance.

### 1. Get the current source

```text
git clone --branch preserve/st2-20261008 https://github.com/msj-group/adaptive-lms-finalproject.git AdaptiveEnglishLMS
cd AdaptiveEnglishLMS
```

`preserve/st2-20261008` is the Railway release branch. `master` is retained
history and is not the current deployment source. UI development is in the
main workspace on `ui/platform-unification-20261009`; release updates must be
reviewed and pushed deliberately. D1/ST2 tags are historical references.

### 2. Install local Python dependencies

Windows PowerShell, from the repository root:

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install "PyMySQL[rsa]==1.2.0" "tzdata==2026.2"
```

Linux/macOS, using the installed **3.14.6** interpreter:

```sh
python3.14 --version
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install 'PyMySQL[rsa]==1.2.0' 'tzdata==2026.2'
```

The RSA extra supplies MySQL 8 `caching_sha2_password`/`sha256_password`
authentication dependencies. The production lock is platform-specific;
do not use its Linux x86_64 wheel hashes as a Windows or macOS install recipe.
`pytest` packages remain in the local manifest for historical reasons; this
project currently has no approved automated test suite to run.

### 3. Configure a new workspace and database

Copy `.env.example` to `.env` **only when `.env` does not already exist**,
then edit it privately. Supply the actual MySQL host, port, database name,
dedicated username/password and a generated `SECRET_KEY`. Generate the key
in your own private terminal; keep its value out of Git, screenshots and chat:

```text
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Essential local settings:

```dotenv
FLASK_ENV=development
SECRET_KEY=<your-private-generated-secret>
DATABASE_HOST=127.0.0.1
DATABASE_PORT=3306
DATABASE_NAME=adaptive_english_lms
DATABASE_USER=<your-dedicated-mysql-user>
DATABASE_PASSWORD=<your-private-mysql-password>
APP_TIMEZONE=Africa/Tripoli
MATERIAL_STORAGE_ROOT=storage/materials
PAYMENT_PROVIDER_MODE=disabled
RESEARCH_DATA_PROVENANCE=development
RESEARCH_RETENTION_DAYS=15
RAILWAY_ALLOW_STUDY=0
```

Angle-bracket values are placeholders to replace privately. Other upload
settings can retain the values in `.env.example`. The MySQL service is
installed/configured separately; `pip` installs the client driver, not the
database server. A DBA must create a **new empty** MySQL database and give the
installation account schema-scoped migration permissions. For example, the
database creation statement for a new local installation is:

```sql
CREATE DATABASE adaptive_english_lms
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
```

Do not run that statement, reset data, import a historical SQL dump or run
provisioning against an existing project database. On a new database only,
apply the tracked Alembic history and inspect the result:

```powershell
.\.venv\Scripts\python.exe -m flask --app run:app db upgrade
.\.venv\Scripts\python.exe -m flask --app run:app db current
```

On Linux/macOS replace `.\.venv\Scripts\python.exe` with `.venv/bin/python`.
The current expected schema head is **`7d4e2a9c6013`**. Upgrading an existing
database requires checking its revision and backups first; these fresh-install
instructions are not permission to migrate or reset the owner's existing data.

### 4. Create the first account and run

In a private interactive terminal, for a new installation only:

```powershell
.\.venv\Scripts\python.exe scripts/create_admin.py
.\.venv\Scripts\python.exe run.py
```

Use the corresponding `.venv/bin/python` commands on Linux/macOS. The account
helper asks for the Administrator email, name and a password of at least 12
characters; password input is hidden. There are no default passwords or
automatically seeded accounts. Create Teachers/Students and academic content
through normal Administrator workflows. Researcher provisioning is a separate
approved operation using `scripts/create_researcher.py` and the private email
allowlist; it does not activate collection.

Open **http://127.0.0.1:5000/auth/login**. Check `/health/ready` for HTTP 200
and `{"status":"ready"}`. The development server is loopback-only and must
not be exposed as the production server. Missing credentials, an unavailable
MySQL service, a wrong schema revision or unwritable private storage must be
corrected in the installation configuration, not by bypassing validation.

## Hosting requirements

A host must support a persistent **Linux Python web process**, MySQL 8,
private persistent file storage, HTTPS, secret environment variables and an
explicit migration/backup procedure. Static hosting or PHP-only shared hosting
cannot run this application. A shared host is suitable only if it actually
supports this Python/WSGI runtime, dependencies, private storage and operations.

| Area | Required production setup |
|---|---|
| Runtime and packages | CPython **3.14.6**; `requirements-production.txt` or the verified Linux x86_64 `requirements-production.lock`. The hash-locked deployment uses `--only-binary=:all: --require-hashes`. |
| Web server | Gunicorn **26.2.0**, `wsgi:application`, configuration in `deploy/gunicorn.conf.py`. Current baseline: one worker, four threads, one replica. |
| HTTPS/proxy | TLS at the trusted edge, an exact `TRUSTED_HOSTS` allowlist, and `PROXY_TRUSTED_HOPS` matching the actual trusted proxy count. Keep a generic-host WSGI listener private. |
| Database | Persistent MySQL 8/InnoDB; dedicated non-root runtime account and a separate schema-scoped migration account. Use private networking or verified MySQL TLS for external transport. |
| Secrets | Stable private `SECRET_KEY` of at least 32 characters, database credentials and any provisioning allowlist in the host's secret store. Never bake `.env` into an image. |
| Private uploads | Persistent writable storage outside public assets. Back up both the database and private files. Ephemeral container storage alone loses uploads during redeploys. |
| Request limits | Proxy/platform limits must accommodate the configured file sizes: documents 25 MiB, images 10 MiB, audio 50 MiB, video 100 MiB, plus multipart overhead. |
| Rate limiting | `RATELIMIT_STORAGE_URI=memory://` is process-local. Additional workers/replicas require a separately reviewed shared limiter backend first. |
| Research | Keep `RESEARCH_DATA_PROVENANCE=development`, `RAILWAY_ALLOW_STUDY=0` and `RESEARCH_RETENTION_DAYS=15`. Research configurations remain paused until separately authorized. |
| Operations | Monitor `/health/ready`, retain private count-only operational logs, maintain tested backups, and operate the approved daily 15-day retention job separately from web startup. |

### Railway: existing controlled Development candidate

The committed `railpack.json` and `railway.toml` are the deployment source of
truth. Use the existing GitHub-connected web service and release branch
`preserve/st2-20261008`; keep its database, volume, accounts and data intact.
Railway's environment name `production` describes hosting and does not grant
Study approval.

- Repository root: `/`; builder: **Railpack**; Python: **3.14.6**.
- Build command: leave the dashboard override blank; `railpack.json` installs
  the hash-locked production wheels. Do not enable a competing Dockerfile path.
- Pre-deploy command: `python scripts/railway_migrate.py`. It uses separate
  `MIGRATION_DATABASE_USER`/`MIGRATION_DATABASE_PASSWORD`, a MySQL migration
  lock, and verifies head `7d4e2a9c6013`. It performs no seeding/reset; a release
  with unchanged migrations at that head needs no schema change.
- Start command: `python scripts/railway_start.py`; Gunicorn binds to the
  Railway-provided `PORT` (configured target `8080`).
- Health check: `/health/ready`, timeout **180 seconds**; on-failure restart,
  maximum **3** retries.
- One persistent web volume mounted at `/data`;
  `MATERIAL_STORAGE_ROOT=/data/private-files` and
  `RESEARCH_RETENTION_LOG_DIR=/data/operations`.
- Preserve `PYTHON_DOTENV_DISABLED=1`, `FLASK_ENV=production`,
  `RAILPACK_PYTHON_VERSION=3.14.6`, `RAILWAY_RUN_UID=0` for volume
  initialization (the startup script drops the web process to UID 10001),
  and `RAILWAY_DEPLOYMENT_DRAINING_SECONDS=120`.
- Set `TRUSTED_HOSTS` to the actual public domain and
  `healthcheck.railway.app`; behind Railway's edge use
  `PROXY_TRUSTED_HOPS=1`. Reference the existing MySQL service's structured
  connection variables and the dedicated runtime credentials. `MYSQL_URL`
  is not interpreted by the application.
- Push a reviewed commit normally; use the connected branch's autodeploy
  when enabled, otherwise Railway's **Deploy Latest Commit**. Verify the
  deployment's exact commit, successful health check and public login before
  treating the release as available. A successful Git push alone is not a
  successful deployment.

See [Railway Deployment](docs/RAILWAY_DEPLOYMENT.md) for the full variable
inventory, migration permissions, volume setup and diagnostics. Its source
preservation notes describe the earlier preparation; use the current verified
branch/commit for new releases. See [GitHub autodeploys](https://docs.railway.com/deployments/github-autodeploys)
and [Railway health checks](https://docs.railway.com/deployments/healthchecks)
for the platform behavior.

### Other Linux hosts

Use `.env.production.example` as a private configuration recipe, not a ready
credential file. Set `FLASK_ENV=production`, supply real database/secret/host
values, configure persistent storage, and install production dependencies in
a virtual environment. For the verified Linux x86_64 platform:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: --require-hashes -r requirements-production.lock
.venv/bin/gunicorn -c deploy/gunicorn.conf.py wsgi:application
```

Run this under the host's process supervisor as an unprivileged account behind
an HTTPS reverse proxy. Configure environment variables through that supervisor
or secret store; disable `.env` loading with `PYTHON_DOTENV_DISABLED=1` when
using host-managed variables. The generic default bind is `127.0.0.1:8000`;
`PORT`, when set, takes precedence. Neither Gunicorn startup nor the historical
generic-host `deploy/Dockerfile` automatically initializes a database, provisions
accounts or starts research. The operator must approve any initial migration,
provisioning and retention scheduler separately. See
[Hosting Preparation](docs/HOSTING_PREPARATION.md) for additional operational
context; verify the provider's current capabilities before selecting a plan.

## Start here

- [Quick Start](docs/QUICK_START.md): running an already configured workspace
  and preparing a new one deliberately.
- [Hosting Preparation](docs/HOSTING_PREPARATION.md): LibyanSpider options,
  empty installation and ordinary operational evaluation.
- [Approved Repair Contract](docs/APPROVED_REPAIR_CONTRACT.md): the accepted
  target behavior and decisions that supersede older contracts.
- [Implementation Plan](docs/IMPLEMENTATION_PLAN.md): repair phases, scope
  and exit criteria.
- [Project Status](docs/PROJECT_STATUS.md): what is implemented, planned and
  verified in the current repair.
- [Project Decisions](docs/DECISIONS.md): historical decisions and their
  superseding entries.
- [Operational Rules](AGENTS.md): repository working rules.

## Current entry points

For an already configured local workspace, the development server in
`run.py` listens on `http://127.0.0.1:5000`.

| Entry | Current behavior |
|---|---|
| `/` | Redirect to shared login, or the loaded account's role dashboard. |
| `/auth/login` | Shared email/password form for all four account roles. |
| `/admin/dashboard` | Administrator workspace. |
| `/teacher/dashboard` | Teacher workspace. |
| `/student/dashboard` | Student workspace. |
| `/research/dashboard` | Separate workspace restricted to active Researchers. |
| `/research/login` | Compatibility entry using the shared authentication flow. |
| `/health` | Basic application health response; it does not prove database readiness. |
| `/health/ready` | HTTP 200 only when MySQL responds and the schema is at `7d4e2a9c6013`; otherwise HTTP 503. |
| `/admin/rooms` | Administrator Room management and change history. |
| `/admin/student-accounts` | General Student accounts, money movements and history. |
| `/student/activities` | One hub for Assignments, Quizzes, Listening and Speaking. |
| `/student/records` | Own released academic records and prior enrollment progress. |
| `/research/storage` | Researcher storage measurement, cleanup previews and gap history. |

The common login has no Researcher role selector or research link. Ordinary
portals have no research management surface. The Researcher workspace remains
separate and protected even though authentication is shared. Approved
Researcher authentication is password-only; MFA is not required.

The canonical root `/` is implemented in P1A. Existing safe login return-path
rules and role checks remain in force; a root query string cannot select a
different workspace. Unknown environment names now refuse startup rather
than falling back to development.

## Application structure

| Location | Purpose |
|---|---|
| `app/__init__.py` | Application factory and extension/blueprint registration. |
| `app/blueprints/` | HTTP routes grouped by role or feature. |
| `app/models/` | SQLAlchemy domain models and database constraints. |
| `app/services/` | Domain operations, queries, calculations and integrations. |
| `app/templates/`, `app/static/` | Server-rendered UI and browser assets. |
| `migrations/` | Alembic schema history. |
| `scripts/` | Explicit provisioning and operational helpers. |
| `docs/` | Contracts, repair plan, status and operational records. |

The repair covers enrollment lifecycle and history, Course-based pricing,
the general Student Account, academic corrections, enrollment-owned progress,
teacher/room scheduling, the unified Student Activities page and research
integrity. These are approved targets; their presence in a document does not
mean their implementation is complete. See the contract and status documents.

## Current development data and verification

MySQL/InnoDB is the application database. The current schema head is
`7d4e2a9c6013`. A new installation starts with an empty business database;
no accounts, Courses, Groups, payments, sessions or example files are created
automatically. Provision the first Administrator privately, then enter
ordinary content through the appropriate role workflows.

The existing local database is retained by the owner's explicit instruction.
Its older fictional content is not a deployment source. Do not transfer a
local SQL backup, private uploads or old research archives to a new installation.

The owner also requested deletion of the old tests and explicitly declined a
new automated test package. There is no current `tests/` directory or isolated
test runner. This close-out uses bounded manual browser and static review;
historical focused results do not prove comprehensive regression acceptance.
Any future automated database checks require separate authorization and owned
isolated MySQL resources. Never run them against development or production.

[Repair Integration](docs/REPAIR_INTEGRATION.md) records the migration, private
recovery archive, earlier local reset and actual close-out evidence. The completed reset
is not permission to repeat it or perform a production migration.

## Research contract and operations

Research remains Version A natural-use collection, with an optional raw
1–5 self-report. No model training, prediction, binary label or adaptation is
implemented by this repair scope. Sampling, observation windows, display
meaning, provenance and session semantics must not change without a separate
research decision.

The current approved storage policy is **15-day retention with a daily purge**.
There is no pressure-only replacement in the approved repair contract.
The runtime expects an explicit `RESEARCH_RETENTION_DAYS=15` setting; it does
not obtain that value from this README. The recorded local daily job uses
`scripts/run_research_retention.py`. It expires research sessions by last
activity and removes their events, prompts and expired archive bytes while
preserving subject links, exclusions, configurations, export descriptions
and audit history. Ordinary LMS records are outside that purge.

[Phase 6 Completion](docs/PHASE6_COMPLETION.md) records the earlier deployment,
MySQL/browser checks and local scheduled task. Those are historical results,
not checks repeated during P0. Scheduler registration, database provisioning
and starting collection are separate deliberate operations.

- [Natural-Use Research Contract](docs/PHASE6_NATURAL_USE_RESEARCH.md)
- [Research Deployment Runbook](docs/RESEARCH_DEPLOYMENT_RUNBOOK.md)
- [Phase 6 Completion Record](docs/PHASE6_COMPLETION.md)

## Configuration and limitations

Keep real configuration in the ignored `.env` or the deployment's secret
environment. `.env.example` is a template, not an approved ready-to-run
configuration. Never commit credentials, database backups, uploaded files,
research archives or operational logs. Do not overwrite an existing `.env`
while following setup instructions.

The local server is for development, not a production hosting setup. The
online-payment integration is disabled; payment simulation and its routes have
been removed. Ordinary cash and verified bank-transfer collection remain
available. `/health` is liveness only; `/health/ready` checks database access
and the expected schema revision. Neither endpoint proves scheduler execution,
all workflows, backup recovery or authorization to start collection.

New Courses refuse enrollment until an Administrator supplies a price.
There are no demonstration selectors or automatically populated dashboards.
Research pages use the deployment's provenance automatically, with explicit
empty states. Operational evaluation uses ordinary forms with fictional input
and `RESEARCH_DATA_PROVENANCE=development`, even on a production server;
these sessions never enter study exports. The same export workflow can download
them as a separately identified operational-review archive. Its manifest and
session rows record the source; a browser cannot choose another export scope.
See [Local Research Walkthrough](docs/LOCAL_RESEARCH_WALKTHROUGH.md).
Starting a real study and setting
`study` provenance require a separate explicit operational decision.
Restart the application after deploying changed source/schema.
