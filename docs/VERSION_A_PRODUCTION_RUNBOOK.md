# Version A production deployment and recovery

Authority: final owner approval, 2026-10-08. Flask modular monolith, Jinja,
WTForms, SQLAlchemy and MySQL remain. This runbook prepares deployment; it
does not claim an actual host was provisioned or accepted. See the acceptance
record for executed evidence and the external hosting gates.

## Release and runtime

Use the data-free release ZIP and verify its adjacent SHA-256 and internal
`release-manifest.json`. Extract into a new release directory. It contains
application/assets, complete migrations, operator scripts, deployment examples
and current Version A documentation. It excludes `.env`, Git, environments,
instance data, private files, backups, credentials, Development and Pilot exports.

The prepared Linux runtime is Python 3.14.6, MySQL 8.0/InnoDB/utf8mb4 and the
exact runtime pins in `requirements-production.txt`. Windows local acceptance
used Python 3.14.6 and MySQL 8.0. A Linux image/dependency build and provider
compatibility check remain external acceptance requirements. The WSGI process
uses [Gunicorn 26.2.0](https://pypi.org/project/gunicorn/) with one `gthread`
worker and four threads. This preserves the existing process-local limiter
semantics; do not add workers/replicas without a reviewed shared limiter backend.
Rate counters reset on process restart. Do not use Flask's development server.

Create a venv and install the runtime pins on the selected host. Alternatively
build with `docker build -f deploy/Dockerfile -t youth-centre:version-a .`.
Do not publish the container's port directly: a private network/proxy must own
the listener. Mount persistent private files at `/srv/youth-centre/private-files`
and writable persistent `instance/` for retention logs. Match UID 10001 in the
prepared image or the unprivileged service account in a venv installation.

## Environment inventory

Copy `.env.production.example` into a private environment file outside the
release, readable only by the service/operator account (0600). Never commit,
print, email or package populated settings. Supply values through the host's
secret/environment mechanism; shell command arguments must not hold secrets.

| Settings | Meaning |
|---|---|
| `FLASK_ENV=production` | Debug off; secure session cookie; production validation |
| `SECRET_KEY` | Private random stable value of at least 32 characters; rotation invalidates signed sessions/forms |
| `APP_TIMEZONE=Africa/Tripoli` | Centre calendar; stored research timestamps remain UTC |
| `DATABASE_HOST/PORT/NAME/USER/PASSWORD` | Explicit MySQL target and least-privilege account; structured URL handles punctuation |
| `DATABASE_SSL_CA` | Existing CA certificate for a remote MySQL TLS connection; hostname verified |
| `TRUSTED_HOSTS` | Exact public hostnames, comma separated; no wildcard catch-all |
| `PROXY_TRUSTED_HOPS` | 0 for direct secure WSGI or exact trusted proxy chain (normally 1); maximum 2 |
| `MATERIAL_STORAGE_ROOT` | Absolute private persistent directory outside static/web roots |
| `MATERIAL_ALLOWED_EXTENSIONS` | Existing approved allowlist; default PDF/DOCX/images/audio/video |
| `MATERIAL_MAX_DOCUMENT_BYTES` | Default 26214400 (25 MiB) |
| `MATERIAL_MAX_IMAGE_BYTES` | Default 10485760 (10 MiB) |
| `MATERIAL_MAX_AUDIO_BYTES` | Default 52428800 (50 MiB) |
| `MATERIAL_MAX_VIDEO_BYTES` | Default 104857600 (100 MiB) |
| `PAYMENT_PROVIDER_MODE=disabled` | Native recorded cash/bank operations; no simulated/online provider |
| `RATELIMIT_STORAGE_URI=memory://` | Single process only; no new infrastructure installed |
| `RESEARCH_DATA_PROVENANCE=development` | Mandatory setup/smoke classification; Study is a deliberate later operational step |
| `RESEARCH_RETENTION_DAYS=15` | Approved daily research retention, validated at startup |
| `RESEARCHER_EMAIL_ALLOWLIST` | Private approved provisioning addresses; does not grant a role at Login |
| `WSGI_BIND` | Private loopback listener or isolated container listener |

Secure/HttpOnly/SameSite=Lax sessions and CSRF remain enabled. HTTPS is required.
Do not disable CSRF to solve proxy misconfiguration. ProxyFix trusts forwarded
client/protocol information only for the configured hops; the network must
prevent direct access. The proxy must set Host and forwarded headers itself.
`deploy/nginx.example.conf` supplies this composition, TLS and bounded upload
size/timeouts. Replace host/certificate paths with actual provider values.
No private-file alias or directory listing is permitted. Static fonts/images
are included locally; financial PDF font assets are included under `app/assets`.

## Empty installation and first administrator

1. Create a NEW empty MySQL database. Do not import a development dump/files.
   Set InnoDB, utf8mb4 and a private DB connection. Confirm database target
   privately before any migration. Back up existing databases before upgrades.
2. Use a dedicated migration credential with schema DDL permission on this
   database only. With production settings loaded privately and app stopped,
   run `python -m flask --app wsgi:application db upgrade`.
3. Verify `python -m flask --app wsgi:application db current` reaches
   `7d4e2a9c6013`; run `python -m flask --app wsgi:application db check`.
   Do not use `create_all`, stamp or a partially prepared database. The complete
   migration path was actually rehearsed on an empty owned MySQL database:
   75 tables, zero business/research/file rows, no model delta.
4. Switch to a runtime credential with only required SELECT/INSERT/UPDATE/DELETE
   permissions on this schema (no global/DDL privileges). Never use root.
5. Open a private interactive operator terminal with these settings loaded:
   `python scripts/create_admin.py`. Enter the designated centre administrator's
   email/name and password twice; the script hashes the password and refuses
   non-interactive input. Choose a strong passphrase at least 15 characters.
   Existing bootstrap minimum (12) is preserved; ordinary account forms use
   their existing 15-character policy. No default administrator is created.
6. If approved, set the private Researcher allowlist and run
   `python scripts/create_researcher.py` interactively. Researcher remains
   password-only. Other accounts and centre content use normal Admin/Teacher UI.

No automatic migration, seeding, account creation or collection occurs on startup.
The centre creates terms, levels, courses/prices, groups, rooms/schedules,
Teachers/Students and enrollments in its ordinary approved workflows.

## Startup, restart and diagnostics

Run `gunicorn -c deploy/gunicorn.conf.py wsgi:application` under an unprivileged
service supervisor. Adapt `deploy/youth-centre.service.example` or the provider's
equivalent. Do not install a scheduler/service on the developer machine.

`GET /health` is liveness; `GET /health/ready` checks bounded MySQL connectivity
and migration head and returns only ready/unavailable, with no DB details.
Run `python scripts/deployment_diagnostics.py` for count-free, identity-free
settings/current-configuration diagnostics. It cannot prove HTTPS, scheduler
execution, storage write permission or consent arrangements; these require
separate operational checks.

Gunicorn access logs contain method/path/status/duration, no query/IP/UA/body.
Paths may contain object UUIDs; restrict access and retention. Nginx example
access logs are disabled. SQL parameters are hidden. Never enable SQL echo or
debug in production. Restrict error logs; do not expose them to centre users.
The error page supplies a reference without SQL/driver details. Verify actual
host log redaction using fictional smoke traffic before real use.

## Backups and consistent restore

Back up BOTH MySQL and private-file storage; a database dump alone cannot
restore uploads. Schedule according to the centre's approved operational policy,
encrypt off-host backups, restrict access and test restore periodically. Research
retention also applies to copies under the approved governance policy; do not
keep study backups indefinitely by accident. No LMS academic/financial/file
deletion policy was introduced by this release.

For a simple consistent centre backup: pause research in its existing UI, stop
the WSGI process/maintenance access, and verify no writers remain. Using a
private MySQL defaults file (0600; no command-line password), run:

```sh
mysqldump --defaults-extra-file=/private/mysql-backup.cnf --single-transaction --routines --triggers --events --no-tablespaces youth_centre > /private/backup/database.sql
tar -cf /private/backup/private-files.tar -C /srv/youth-centre/private-files .
sha256sum /private/backup/database.sql /private/backup/private-files.tar > /private/backup/SHA256SUMS
```

The protected defaults file provides host/user/password/database access. Keep
app/environment secrets in separate encrypted operational recovery storage.
Back up migration/release manifests and retention state. Restart only after both
artifacts are complete. This stop-writers procedure avoids DB/files drift.

Restore first into an isolated EMPTY target: verify hashes, restore the dump
with `mysql --defaults-extra-file=/private/mysql-restore.cnf restored_database < database.sql`,
extract private files into the explicitly selected empty private directory,
restore service ownership/0700 directories and 0600 files, point a separate
private environment at it, and confirm migration/head/diagnostics. Keep public
access and collection stopped. Reconcile referenced storage keys against bytes;
do not automatically delete unknown/missing files. Confirm an authorized download
and unauthorized denial, current role access and representative records. Run the
approved expired-research cleanup before opening a restored old backup. Never
reclassify restored sessions or permit duplicate collection as a new Study.

An owned cold migration + MySQL dump/restore + private-byte archive/hash
rehearsal passed locally. It did not prove host snapshots, encryption, disaster
recovery time or an actual hosted authorized-file restore.

## Upgrades and recovery

Pause collection; capture baseline/change record and research impact before
material Student UI changes. Back up consistent DB/files. Stop writers, deploy
to a new release directory, use migration credentials for safe FORWARD migrations,
then restore runtime credentials and restart. Keep the prior release available.
Never casually downgrade/reset a production database. File Assignment downgrade
refuses when file tasks/submissions exist. A code rollback is only safe when
the earlier release supports the current schema; otherwise recover into an
isolated target from a consistent backup with a documented data-loss decision.

## Daily research retention

Adapt `deploy/research-retention.service.example` and `.timer.example` on the
chosen host, or its equivalent daily scheduler, with the SAME private production
environment and storage. It calls `python scripts/run_research_retention.py` once
daily, preserves the approved 15-day policy and removes expired research
sessions/events/prompts and affected research export archives. It does not purge
Student LMS uploads or academic/financial history. Inspect success/exit status
and `instance/research-retention.jsonl` count-only log daily; rotation is bounded.
Missed/failed jobs must alert the operator; the mere presence of a timer file
does not mean retention runs. Installation/execution on actual hosting remains
an external gate. Exclusion/identity recovery uses the private Researcher-authenticated
operator tool described in the research operations guide.

## Required hosting smoke before real centre use

Use fictional operator accounts/input with DEVELOPMENT provenance and collection
paused unless a bounded operational-review collection check is deliberate.
Verify real TLS certificate/redirects, trusted Host rejection, secure session/
CSRF proxy behavior, Login, all four role guards, readiness/static/font assets,
private-file upload/download/denial, one representative workflow, logs,
backup/restore, daily retention, collector availability/stop and export state.
Record host/date/release/operator/result. Do not call this traffic Study.
No actual hosting smoke or real collection has yet been verified.
