# Railway deployment of the unfrozen ST2 candidate

Date: 2026-10-08. Intended source: **YC-VA-AT2-20261008**, Flask/Jinja,
SQLAlchemy/Alembic and MySQL. This prepares the existing failed GitHub-connected
Web service; it does not create a replacement application or deploy anything.
Keep collection paused and use fictional Development/Pilot input only.
Railway's environment name `production` is not Study authorization.

## Preserved history and current source

[SOURCE_HISTORY_PRESERVATION.md](SOURCE_HISTORY_PRESERVATION.md) records exact
hash verification, commits and annotated tags. D1 is `9ab3e4c`; ST2 is
`c644298`; ancestry is `9913389 -> D1 -> ST2 -> Railway deployment commit`.
The owner authorized the separate 22-file deployment commit after ST2 on
`preserve/st2-20261008` in the isolated worktree
`C:/Users/abdul/Desktop/GraduationProject/AdaptiveEnglishLMS-preserve-ST2`.
The original dirty `master` working tree has been preserved. Use the isolated
worktree's deployment commit for the later manual push. Resolve its exact ID
with `git log -1 --format=%H`; no source-preservation commit/tag is rewritten.

Never restore the D1 ZIP, rejected template or old GitHub revision over ST2.
The historical `build_release.py` enforces the frozen D1 inventory and rightly
rejects ST2. Do not alter its baseline checks or call that release a new freeze.
Railway builds the reviewed Git branch directly as a controlled candidate.
Login, Messages, platform background, all UI assets/templates/JS, role policies,
domain models, research dictionary/sampling/export contracts are unchanged by
deployment preparation. Startup/configuration/logging paths and invalid-Host
error handling change; normal UI appearance and behavior remain intact.

## Failure cause and primary build strategy

The observed error is Railpack preparation finding no start command, before
the application starts. The inspected local tracked revision `9913389` has
`run.py` and a Flask factory, but no explicit hosting start command and no
Gunicorn in `requirements.txt`. The correct local production entry point
`wsgi:application`, production requirements and newest source/migrations were
untracked before preservation. This explains how the old tracked source can
reach the reported failure. The official GitHub origin was fetched on
2026-10-08: remote master is still `9913389`, without these newest local files.
Railway's currently selected deployment revision itself was not inspected.
See [Railway's start-command explanation](https://docs.railway.com/deployments/troubleshooting/no-start-command-could-be-found).

Use **Railpack only** for this service. `railpack.json` explicitly selects the
Python provider, Python 3.14.6, a production-only hash-locked wheel install,
allowlisted runtime source and the startup command. `.dockerignore` is the
build-context allowlist; it now includes the new lock/start/migration files.
Do not set a Dockerfile path or `RAILWAY_DOCKERFILE_PATH`. The older non-root
`deploy/Dockerfile` is a historical generic-host example, not Railway's builder.
[Railpack configuration](https://railpack.com/config/file/) and
[context exclusions](https://railpack.com/config/excluding-files) document these mechanisms.

41 Linux x86_64 wheels, including CPython 3.14 and ABI3 native packages, were
resolved, downloaded and SHA256-verified. Direct application pins were not
downgraded or changed. `PyMySQL[rsa]` adds MySQL 8 authentication support;
the complete lock pins its cryptography dependency to 50.0.2. `tzdata==2026.2`
provides IANA timezones on minimal images. No ffmpeg/transcoder/audio native
library is used by this application's media validation/storage. Pillow, nh3,
SQLAlchemy, greenlet, CFFI, Argon2, bidi and cryptography have verified wheels.
Only runtime `ca-certificates` is explicitly requested; no C/Rust compilation
is allowed by the locked install. Gunicorn 26.2.0 uses one gthread worker and
four threads. [PyMySQL's authentication requirements](https://pymysql.readthedocs.io/en/latest/user/installation.html)
explain the RSA extra. Python 3.14.6 is an
[official runtime release](https://www.python.org/downloads/release/python-3146/).

This Windows machine has no Docker or installed WSL. Wheel resolution is not
a Linux installation, Railpack image build or Gunicorn startup claim. These
must still run on Linux before declaring full deployment readiness. If the
selected Railway builder cannot supply exactly 3.14.6, stop; review a separate
Docker strategy with this same runtime/lock, replacing the primary strategy
rather than silently downgrading Python or leaving competing configurations.

## Exact existing Web service settings

| Setting | Value |
|---|---|
| Source | Existing GitHub-connected Web service; select reviewed `preserve/st2-20261008` branch after manual push |
| Repository | `msj-group/adaptive-lms-finalproject`; owner-confirmed official origin |
| Root Directory | `/` — the Git repository root contains `wsgi.py`, `app/` and `migrations/` |
| Builder | Railpack; clear Dockerfile-path overrides |
| Build Command | Leave blank; `railpack.json` owns installation/build |
| Custom Start Command | `python scripts/railway_start.py` |
| Actual WSGI command | `gunicorn -c deploy/gunicorn.conf.py wsgi:application` |
| Pre-deploy Command | `python scripts/railway_migrate.py` |
| Health-check Path | `/health/ready` |
| Health-check Timeout | `180` seconds |
| Restart Policy | On failure, maximum 3 retries |
| Python | Exactly `3.14.6`, also pinned by `.python-version` and build assertion |
| Networking | Generate Railway HTTPS domain; target port `8080`; `PORT=8080` |
| Scale | One instance/replica; one Gunicorn worker, four threads |
| Private upload volume | One volume on Web service, mount `/data` |
| Autodeploy | Disable until the owner deliberately deploys the reviewed commit |

`railway.toml` supplies these build/deploy settings for the existing legacy
service. Configuration in code takes priority over dashboard values. Current
Railway documentation says legacy config files remain supported until
2026-12-01; plan its separate IaC migration before that deadline, without
changing the candidate's source/research state.
[Configuration reference](https://docs.railway.com/config-as-code/reference).

The startup script verifies Linux/runtime/PORT/test provenance, rejects a
missing genuine `/data` mount, initializes only `/data/private-files` and
`/data/operations`, and probes their write permission. With `RAILWAY_RUN_UID=0`
it initializes a root-owned new volume then drops groups/GID/UID to 10001
before importing/executing Gunicorn. It never recursively changes uploads.
It removes migration credentials from the Gunicorn environment, preserves
one process-local limiter, and uses exec for signals/clean shutdown.
Gunicorn binds `0.0.0.0:$PORT`; the old loopback fallback remains for other hosts.
Configure `RAILWAY_DEPLOYMENT_DRAINING_SECONDS=120` to allow the configured
graceful timeout; verify actual SIGTERM/drain behavior on the host.

## Complete Web environment inventory

References below use a database service named **MySQL**. If the existing
service has another name, use Railway's variable-reference picker to select
its same named variable; do not paste an invented service reference. The
actual account/service/domain/plan was not accessed during preparation.

| Variable | Railway value/reference | Purpose | Required? | Secret? |
|---|---|---|---|---|
| `FLASK_ENV` | `production` | Production factory/operator settings | Yes | No |
| `PYTHON_DOTENV_DISABLED` | `1` | Never load a local `.env` | Yes | No |
| `RAILPACK_PYTHON_VERSION` | `3.14.6` | Exact runtime override, consistent with file pin | Yes | No |
| `PORT` | `8080` | Railway-injected listener/health/network target | Yes | No |
| `RAILWAY_RUN_UID` | `0` | Mount initialization; script drops WSGI to UID 10001 | Yes for new root-owned volume | No |
| `RAILWAY_VOLUME_MOUNT_PATH` | Platform supplies `/data` when volume attached | Verify genuine mounted storage | Yes, platform-managed | No |
| `RAILWAY_DEPLOYMENT_DRAINING_SECONDS` | `120` | Graceful teardown window | Yes | No |
| `SECRET_KEY` | Generated stable private value, at least 32 chars | Session/form signing | Yes | Yes |
| `APP_TIMEZONE` | `Africa/Tripoli` | Centre schedule/calendar timezone | Yes | No |
| `DATABASE_HOST` | `${{MySQL.MYSQLHOST}}` | Private MySQL hostname | Yes | No |
| `DATABASE_PORT` | `${{MySQL.MYSQLPORT}}` | Private MySQL port | Yes | No |
| `DATABASE_NAME` | `${{MySQL.MYSQLDATABASE}}` | Confirmed isolated Railway schema | Yes | No |
| `DATABASE_USER` | `aelms_runtime` | Schema-scoped DML account | Yes | No |
| `DATABASE_PASSWORD` | Its privately generated password | Runtime DB authentication | Yes | Yes |
| `MIGRATION_DATABASE_USER` | `aelms_migrator` | Separate schema-scoped DDL account | Yes | No |
| `MIGRATION_DATABASE_PASSWORD` | Its separate private password | Pre-deploy migrations only | Yes | Yes |
| `DATABASE_SSL_CA` | Unset on the private Railway network; absolute CA path if TLS configured | Verified MySQL TLS for external transport | Conditional | No |
| `TRUSTED_HOSTS` | `${{RAILWAY_PUBLIC_DOMAIN}},healthcheck.railway.app` | Exact public and health-check Host allowlist | Yes; generate domain first | No |
| `PROXY_TRUSTED_HOPS` | `1` | Trust one Railway edge for client/protocol only | Yes | No |
| `MATERIAL_STORAGE_ROOT` | `/data/private-files` | All persistent private LMS uploads | Yes | No |
| `RESEARCH_RETENTION_LOG_DIR` | `/data/operations` | Persistent count-only operator audit files | Yes for Web startup | No |
| `RESEARCH_DATA_PROVENANCE` | `development` | Smoke/Pilot cannot be Study | Yes | No |
| `RAILWAY_ALLOW_STUDY` | `0` | Candidate startup guard | Yes for controlled deployment | No |
| `RESEARCH_RETENTION_DAYS` | `15` | Existing approved retention | Yes | No |
| `RESEARCHER_EMAIL_ALLOWLIST` | Empty until approved provisioning; then private approved addresses | Researcher provisioning only | Optional | Identity-sensitive |
| `PAYMENT_PROVIDER_MODE` | `disabled` | Preserve native cash/bank operation | Yes | No |
| `RATELIMIT_STORAGE_URI` | `memory://` | Existing single-process limiter | Yes | No |
| `MATERIAL_ALLOWED_EXTENSIONS` | Default `pdf,docx,png,jpg,jpeg,gif,webp,mp3,wav,mp4,webm` | Existing vetted formats | Optional override | No |
| `MATERIAL_MAX_DOCUMENT_BYTES` | Default `26214400` | 25 MiB documents | Optional override | No |
| `MATERIAL_MAX_IMAGE_BYTES` | Default `10485760` | 10 MiB images | Optional override | No |
| `MATERIAL_MAX_AUDIO_BYTES` | Default `52428800` | 50 MiB audio | Optional override | No |
| `MATERIAL_MAX_VIDEO_BYTES` | Default `104857600` | 100 MiB video | Optional override | No |
| `WSGI_BIND` | Unset for Railway | `PORT` takes precedence; other-host fallback only | No | No |

`MYSQLHOST/PORT/USER/PASSWORD/DATABASE` are also accepted as fallback component
names; existing `DATABASE_*` values take precedence. Explicitly use the
schema-scoped account above, not the template's root `MYSQLUSER`. `MYSQL_URL`
is deliberately not required or interpreted; structured URL construction
safely handles password punctuation and forces `mysql+pymysql`/utf8mb4.
Production refuses root/missing DB credentials. Pool pre-ping, recycle 300 s,
pool size 4 plus overflow 2, pool wait 10 s, connect timeout 5 s and socket
read/write timeout 30 s bound resource use. TLS connect arguments remain intact.

Generate each secret once in a private local terminal, for example
`python -c "import secrets; print(secrets.token_urlsafe(48))"`, and paste it
only into the corresponding Railway secret variable/private password prompt.
Never put generated values in source, command arguments, screenshots or this
document. The table is a configuration recipe; secret values, actual domain,
service references and storage resources still require owner configuration.

## MySQL setup and safe initialization

Reuse the project's actual MySQL service if appropriate; otherwise the owner
may add Railway's MySQL template to this same project. Do not provision a new
database resource without the owner's plan/cost decision. Keep it on private
networking with a persistent database volume (normally `/var/lib/mysql`).
Use genuine MySQL/InnoDB with utf8mb4; the complete schema was rehearsed on
MySQL **8.0.46**. Inspect an existing service's actual version before changing
its image; never downgrade/reinitialize a populated database. For a new
explicitly selected empty service, `mysql:8.0.46` matches the rehearsed engine;
confirm image availability in Railway before selecting it. No image was pulled
locally. [Railway MySQL references](https://docs.railway.com/databases/mysql).

Confirm `MYSQLDATABASE` (normally `railway`) and that the target is EMPTY.
Do not copy local users, uploads, Development/Pilot exports or a local dump.
Using a private interactive terminal attached to the running MySQL service,
run `MYSQL_HISTFILE=/dev/null mysql -uroot -p`. Supply its existing DBA password
privately; do not place it on the command line or expose MySQL publicly.

Create the two schema-scoped accounts. The following SQL is a recipe: replace
the two password markers privately with separately generated values **before
execution** and use the confirmed quoted schema name if it is not `railway`.
These markers are not valid deployment credentials. Never rotate an existing
user silently; if the accounts already exist, inspect/reuse or approve rotation.

```sql
ALTER DATABASE `railway` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'aelms_migrator'@'%' IDENTIFIED WITH caching_sha2_password BY 'PRIVATE_MIGRATION_SECRET';
GRANT ALL PRIVILEGES ON `railway`.* TO 'aelms_migrator'@'%';
CREATE USER 'aelms_runtime'@'%' IDENTIFIED WITH caching_sha2_password BY 'PRIVATE_RUNTIME_SECRET';
GRANT SELECT, INSERT, UPDATE, DELETE ON `railway`.* TO 'aelms_runtime'@'%';
```

Inject their actual credentials through Web variables. The pre-deploy script
uses only the separate migration user, acquires a MySQL named lock, runs the
existing Alembic forward history, verifies exact head `7d4e2a9c6013` and checks
model/schema delta. A failure prevents service rollout; it is not retried as a
destructive reset. The same operation passed twice on a clean disposable DB.
Pre-deploy does not write files and does not require the upload mount; Railway
runs it in a separate container without volumes.
[Pre-deploy constraints](https://docs.railway.com/deployments/pre-deploy-command).

For deliberate operator checks after the service is running:

```sh
python -m flask --app wsgi:application db current
python -m flask --app wsgi:application db check
python scripts/deployment_diagnostics.py
```

The migration operation is `python scripts/railway_migrate.py`; the underlying
standard command is `python -m flask --app wsgi:application db upgrade` with
the migration credential selected privately. Do not use `bootstrap_db.py`,
`create_all`, `stamp`, downgrade, DB reset, example seeding or development dumps.

After verified startup, open a private interactive Web container terminal and
run `python scripts/create_admin.py`. Enter the administrator's actual chosen
email/name and a strong passphrase (prefer at least 15 characters) twice.
It refuses non-interactive input and creates no default account. If separately
approved, populate `RESEARCHER_EMAIL_ALLOWLIST` and run
`python scripts/create_researcher.py` interactively. Provision other test roles
and centre setup through existing Admin/Teacher workflows, using fictional input.
No real participant provisioning or research activation belongs to this task.

## Persistent storage, capacity and backups

Mount **a separate Web volume at `/data`**. Teacher materials, Assignment files,
Speaking recordings and private profile images all use `/data/private-files`
through the existing streaming/signature/ownership pipeline. Atomic temporary
parts use the same directory and are removed on handled failures. Multipart
request spooling can use `/tmp`; it is temporary, not persistent media storage.
Profile normalization and generated financial PDFs use bounded memory.
Research export ZIPs live as immutable bytes in MySQL `ResearchExportArchive`;
they are not uploaded into a static/export directory. Count-only retention
logs use `/data/operations`, with bounded rotation and private permissions.

Startup requires an actual mounted `/data` and writable private subdirectories;
it fails instead of silently using ephemeral upload storage. Directories are
0700 and upload/log files 0600, subject to Linux validation. Downloads remain
authenticated and scoped through application routes; no proxy/static directory
alias exists. No changes to upload/Assignment/Speaking business logic were made.
Upload limits retain the existing maximum 100 MiB plus 64 KiB multipart overhead;
Account photo request limits remain separately bounded. Provider/request
timeouts and storage quota still require actual-host smoke verification.

Railway currently permits **one volume per Free project**, **three per Trial**,
and 0.5 GB Free/Trial volumes. Database plus uploads needs **two distinct
volumes**. Therefore the full arrangement cannot persist on the long-term Free
plan. A full Trial can support both only within capacity/credit limits. Do not
delete either volume or accept ephemeral/public uploads to fit the plan.
The smallest unchanged-architecture option is a plan permitting two volumes
(for example Hobby, requiring owner cost approval), or an already available
external private MySQL service plus the Railway upload volume. A VPS with
persistent MySQL and uploads is another hosting choice. A private object-store
adapter is separate implementation scope; it is not silently substituted here.
[Current volume limits and permissions](https://docs.railway.com/volumes/reference).

Back up **both** MySQL and `/data/private-files`, plus necessary operation logs
and secrets in separately protected recovery storage. Provider DB snapshots
alone do not restore uploaded bytes. Pause collection and stop writers for a
consistent coordinated backup; encrypt off-host copies and preserve approved
retention. Use private MySQL defaults files, never command-line passwords.
Restore first into a new isolated empty target with public access and collection
off; verify schema, source ID, file hashes/references and authorized/denied
downloads before opening it. The generic detailed procedure remains in
`VERSION_A_PRODUCTION_RUNBOOK.md`, adapted to `/data` paths. No Railway backup
or disaster recovery has been executed. Do not automatically delete orphaned
upload parts/files after a crash: there is a documented pre-commit crash window
and no approved orphan reconciliation policy; investigate privately first.

## Background operations

No embedded scheduler, startup seeding or per-worker maintenance exists.
Research export generation is synchronous on the authorized request and stores
its archive in MySQL. Handled failed uploads perform their existing cleanup.
Daily research retention is the existing explicit
`python scripts/run_research_retention.py`, preserving 15-day policy and deleting
only expired research rows/affected archive bytes, not LMS uploads/history.

For paused, empty smoke deployment, no research retention workload exists yet.
Before collecting even a bounded operational-review Pilot, assign a daily
operator procedure or separately approve a cron resource. The optional
`deploy/railway-retention.toml` prepares one cron service using the same Railpack
source, start command above, UTC schedule `0 0 * * *` (02:00 Tripoli), no health
check, no pre-deploy migration and no restart loop. Select that file as the
cron service's configuration file, not the Web's `railway.toml`. Keep one
scheduler, remove inherited Web health/pre-deploy settings and verify exit 0.
[Railway cron behavior](https://docs.railway.com/cron-jobs).

The DB-only cron does not need the Web upload volume, which cannot be shared
between services. Supply the same runtime DB credentials, production security
settings, development provenance and 15 days, use an absolute unused private
storage path such as `/app/instance/unused-private-files`, and leave
`RESEARCH_RETENTION_LOG_DIR` unset so the existing ephemeral instance audit copy
and stdout work. Do not give it migration credentials or enable Study. Railway
stdout/job history and an approved protected log archive must provide audit
retention/alerts; verify missed/failed-job handling before Study. Alternatively,
run the command once daily in the Web terminal where `/data/operations` persists.
No scheduler, cron service or paid resource has been provisioned in this task.

## Security and diagnostics

Production requires generated secrets, exact hosts, secure/HttpOnly/Lax session
cookies and CSRF, with debug/testing disabled. ProxyFix trusts only one client
and scheme hop; it does not trust forwarded Host/port/prefix. Railway must own
the edge and block alternate direct public paths. Generate the public domain
before resolving `TRUSTED_HOSTS`; include `healthcheck.railway.app` explicitly.
Public HTTPS certificate/redirect and actual proxy headers remain host checks.
[Railway health-check Host and PORT](https://docs.railway.com/deployments/healthchecks).

`/health` provides liveness. `/health/ready` checks MySQL and exact migration
head, returns only ready/unavailable (200/503), and has no-store caching.
Health requests neither log in users nor activate collection. Health readiness
does not inspect uploaded bytes, scheduler/backup success or acceptance/freeze.
Railway's deployment health check is not continuous monitoring; assign external
uptime/operations monitoring before real centre use.

Gunicorn logs startup/errors and method/path/status/duration to stdout/stderr;
query strings, request bodies, IPs, user agents and headers are excluded.
Paths can include public UUIDs: restrict log access/retention. SQL parameters
are hidden. Unhandled production requests log only exception type and the
existing error-page reference. DB readiness and pre-deploy failures use
redacted class/stage messages. A production handler filter also redacts caught
driver/OS tracebacks and interpolated recipient IDs/private storage keys while
retaining the emitting module/function and exception type. Do not enable SQL
echo or debug logging. Untrusted-Host requests receive a constant 400/no-store
response before Jinja URL generation; the original handler crashed at that
early routing stage. Ordinary error pages and all interfaces remain unchanged.
Authentication/CSRF/authorization and private upload routes remain intact;
existing best-effort collector failure behavior and research definitions remain
unchanged. There is no new permissive CORS policy.

## Existing Railway project: exact owner procedure

1. Open the existing project and the failed **GitHub-connected Web service**.
   In Settings/Source, inspect its connected repository/branch. Disable
   autodeploy first. Do not recreate the project or app. The owner confirmed
   `https://github.com/msj-group/adaptive-lms-finalproject.git` as official;
   keep the existing origin and select that same repository in Railway.
2. Confirm the plan permits two volumes and sufficient MySQL/media capacity.
   Stop for an owner cost/storage choice if it does not. Reuse the actual
   MySQL service or add the MySQL template only after that choice.
3. In MySQL Variables/Networking, confirm schema/private host/port; keep its
   database volume and private networking. Use its private interactive terminal
   for the schema-scoped DB accounts described above; record actual secrets
   privately. Never reset/import the development database.
4. In Web Settings/Build, choose Railpack, Root `/`, Build Command blank and
   Dockerfile overrides blank. Use `/railway.toml` as configuration file if
   Railway requests it. Ensure `railpack.json`, lock and all runtime files
   reach the selected Git branch.
5. In Web Settings/Deploy, set the exact Start/Pre-deploy/Health/Timeout/Restart
   values in the settings table. Set one replica. The config file also supplies
   these values; inspect the deployment's effective config, not just UI text.
6. In Web Settings/Networking, generate its Railway HTTPS domain targeting
   port 8080. Add the `/data` Web volume. Do not mount it under static assets.
7. In Web Variables, add the table values and use the reference picker for
   MySQL/public-domain variables. Supply generated secrets and the two scoped
   DB accounts. Confirm development provenance, allow-study 0 and retention 15.
8. Complete the reviewed manual Git steps below. Select the correct pushed
   `preserve/st2-20261008` branch and exact deployment commit in Settings/Source.
   Source changes and variable changes can trigger deployment; keep autodeploy
   disabled and staged changes un-applied until the owner deliberately deploys.
9. The owner triggers Deploy/Redeploy of that commit. Inspect Build Logs for
   Python 3.14.6 and the hash-locked install, then Pre-deploy Logs for verified
   head/schema, then Deploy Logs for private storage initialization and Gunicorn
   binding. A failed pre-deploy/mount/health check must remain a failed rollout.
10. Open the generated HTTPS domain; verify liveness/readiness and the bounded
    smoke procedure below. Provision only the deliberately selected admin and
    fictional acceptance users through the existing secure workflows.
11. Verify persistent file bytes after Restart and after Redeploy, inspect logs
    for leakage, verify daily operations/backups and record host evidence.
    Deployment success remains separate from ST2 freeze and Study approval.

## Approved local commit and manual push requirements

Two preservation commits/tags and a separate owner-authorized deployment
commit are prepared locally. The deployment commit contains only these 22
reviewed files in the isolated worktree:

```text
.dockerignore
.gitignore
.python-version
app/__init__.py
app/config.py
app/services/deployment_settings.py
app/errors.py
app/services/production_logging.py
deploy/gunicorn.conf.py
deploy/railway-retention.toml
requirements-production.txt
requirements-production.lock
railpack.json
railway.toml
scripts/railway_start.py
scripts/railway_migrate.py
scripts/run_research_retention.py
docs/HOSTING_PREPARATION.md
docs/VERSION_A_PRODUCTION_RUNBOOK.md
docs/SOURCE_HISTORY_PRESERVATION.md
docs/RAILWAY_DEPLOYMENT.md
docs/RAILWAY_VALIDATION.md
```

The local commit's parent is ST2. Private instance/venv/MySQL/log/export/wheel
and external evidence directories were excluded; the original dirty master
was not staged. Do not create another deployment commit merely to follow an
obsolete preparation instruction. Inspect the prepared result:

```powershell
Set-Location 'C:\Users\abdul\Desktop\GraduationProject\AdaptiveEnglishLMS-preserve-ST2'
git status --short --branch
git show --stat HEAD
git log -4 --oneline --decorate
git log -1 --format=%H
```

The owner confirmed the existing origin as official; no new remote is needed.
`git fetch --no-tags origin` succeeded on 2026-10-08. Remote master is
`99133899529376436f52c292df09f5894e9e8cc1`, an ancestor of the prepared branch.
The target branch and both tag refs were absent in `git ls-remote` at review
time. This creates new refs without changing remote master or existing history.
Refresh the check before the owner deliberately pushes, with Railway autodeploy
disabled. If a target tag now exists, verify its object ID against the local
annotated tag before proceeding; stop if it differs. If the target branch now
exists, verify it is an ancestor of the local branch before proceeding.

```powershell
git remote -v
git fetch --no-tags origin
git log --graph --oneline --all -12
git ls-remote origin refs/heads/preserve/st2-20261008 refs/tags/YC-VA-D1-20261008 refs/tags/YC-VA-AT2-20261008
# Owner-operated push only; update all three refs together, never force:
git push --atomic origin refs/heads/preserve/st2-20261008:refs/heads/preserve/st2-20261008 refs/tags/YC-VA-D1-20261008:refs/tags/YC-VA-D1-20261008 refs/tags/YC-VA-AT2-20261008:refs/tags/YC-VA-AT2-20261008
```

No push or deployment was performed. If an atomic push is unsupported or any
ref is rejected, it must stop without partial updates; review the reason.
Never force-push, rebase published history, reset the primary tree or overwrite
a tag. Both preservation commits and the deployment commit must reach the
selected branch; pushing only the old
master, only tags, or only a start-command file does not deliver ST2.

## Bounded Development smoke and later Study gate

Keep collection paused; do not click Start Collection during ordinary smoke.
Use a small fictional course/group and four separate test-role accounts.

| Check | Expected evidence |
|---|---|
| HTTPS/health | Valid TLS, intended HTTP-to-HTTPS behavior, health 200 and readiness 200; no research traffic |
| Login/logout/sessions | Valid four-role login/landing; real logout; Secure/HttpOnly/Lax cookie; CSRF denial; invalid Host denial |
| Roles | Student/Teacher/Admin/Researcher own portal; forbidden cross-role access denied |
| Static/UI | Local CSS/fonts/images/JS load; preserved Login/Messages/background/ST2; light/dark/RTL/mobile visual review |
| Database/schema | Exact head, no model delta; runtime DDL denied; no imported business/study records |
| Learning/uploads | Text and Single File Assignment; valid disposable upload, invalid signature rejection, expected receipt; Speaking/browser recording separately |
| Private downloads | Owning Student and assigned Teacher access; anonymous/unrelated-account denial; private no-store/nosniff headers |
| Research/export | Development provenance, collector inert while paused; Researcher operational-review export availability/empty state; no Study classification |
| Logs | Diagnosable safe errors, no passwords/tokens/answers/messages/private names; no debug traces |
| Restart/redeploy | Stable secret/session policy, unchanged DB rows/schema and authorized file hash after both operations |
| Operations | Daily retention success/alerting and coordinated DB/file backup/restore verification |

If a separate collector/Pilot acceptance run is later explicitly approved,
create a new operational-review configuration with the approved unchanged
sampling/windows, activate only Development collection for that bounded period,
check delivery/export classification, then pause it. Do not relabel smoke
subjects/sessions. A health check, login or deployment never activates collection.

After owner visual, real microphone, screen-reader, non-Blink and actual-host
acceptance, record a **new documented ST2 freeze decision/source inventory**
without changing D1 evidence. Before genuine Study, verify consent/exclusion
arrangements, real participant eligibility, retention/scheduler/backups and
provenance boundaries, preserve non-study history, pause collection and back up.
Only an explicitly approved later operation may set `RESEARCH_DATA_PROVENANCE=study`
and `RAILWAY_ALLOW_STUDY=1`, restart/verify diagnostics, and create/start a new
post-freeze Study configuration through the existing Researcher controls. The
second startup flag permits the environment; it never activates collection.
Do not enable either Study setting in this task or silently reuse test subjects.

## Troubleshooting and upgrades

| Symptom | Action |
|---|---|
| No start command | Verify selected branch/root contains railpack.json and railway.toml; effective start must name railway_start.py |
| Hash/wheel/runtime install failure | Confirm Linux x86_64, exact 3.14.6 and current lock; stop for reviewed lock/runtime correction, no silent version downgrade |
| Missing cryptography/RSA | Confirm PyMySQL[rsa] and complete production lock were installed |
| Missing credentials/root rejected | Configure the two schema-scoped accounts/variables; never disable production validation |
| MySQL DNS/ref failure | Check actual reference picker/service name, private host/port and same environment; do not point at localhost/public fallback |
| Migration failure | Inspect safe phase/type, privileges, lock and exact schema target; fix forward after backup, never stamp/reset/downgrade |
| No `/data`/permission failure | Attach the real Web volume at /data; verify RAILWAY_RUN_UID=0 and script's UID10001 drop/ownership; no ephemeral fallback |
| Health 400 | Add exact public hostname and healthcheck.railway.app; inspect rejected-Host response |
| Health 503 | Check MySQL connectivity/head, PORT=8080 and target port; do not replace readiness with a misleading liveness-only success |
| Login/cookie/CSRF issue | Verify real HTTPS, proxy scheme/hop and stable secret; do not disable CSRF/Secure cookies |
| Missing upload after redeploy | Stop writes; inspect volume mount/key/reference and backups; do not reset data or redirect to static |
| Memory/quota/timeouts | Inspect metrics/upload size/job count; one replica/worker remains; obtain capacity approval before scaling/resources |
| Cron not running | Verify chosen config file, schedule UTC, exit 0, no leftover active process and alerting |

For upgrades, pause approved collection, record source/research impact, back up
DB and private files consistently, review the new commit, run safe forward
pre-deploy migrations and hosted smoke. Retain previous source/tag. A code
rollback is valid only if it supports the current schema; otherwise restore
to a new isolated target from coordinated backups with an approved recovery
decision. Do not use the frozen D1 archive as an unreviewed ST2 rollback.

Actual current validation and remaining blockers are recorded in
[RAILWAY_VALIDATION.md](RAILWAY_VALIDATION.md). No Railway deployment success
or ST2 freeze is claimed here.
