# Railway ST2 preparation evidence — 2026-10-08

Source state: **YC-VA-AT2-20261008**, candidate **NOT FROZEN**.
No GitHub push, Railway deployment/resource provisioning, Study activation,
development database mutation or automated test suite was performed.
The owner approved the two local source-preservation commits/tags, then
explicitly authorized a separate 22-file deployment commit in the isolated
worktree and official-origin fetch/ref verification. Push remains unauthorized.

## Requested status

| Item | Status | Evidence / remaining boundary |
|---|---|---|
| SOURCE PREPARED | PASS | Exact D1/ST2 commits verified; deployment configuration present; 691 primary source hashes and 512 protected candidate files unchanged |
| BUILD VALIDATED | PARTIAL | All 41 Linux x86_64 dependency wheels resolved/downloaded/hash-verified; JSON/TOML parsed and Railpack source/config documentation reviewed; no Linux/Railpack image build executed |
| WSGI STARTUP VALIDATED | PARTIAL | Actual production `wsgi:application` imported; native Flask request dispatch exercised; Gunicorn PORT/resource configuration verified; no Linux Gunicorn listener, UID drop or shutdown exercised |
| MYSQL CONFIGURED | PARTIAL | Actual disposable MySQL 8.0.46, caching_sha2_password/RSA, punctuation-safe URL, component aliases and DML-only runtime verified; Railway account variables/service not configured |
| MIGRATIONS VALIDATED | PASS | Actual pre-deploy script ran twice on a clean owned MySQL schema; 75 tables, exact head `7d4e2a9c6013`, no model delta or seeded records; actual Railway pre-deploy remains pending |
| STORAGE READY | PARTIAL | Persistent-mount startup guard implemented; ordinary private photo upload/download and denial verified locally; Railway volume/capacity/Unix permissions/redeploy persistence not tested |
| SECURITY VERIFIED | PARTIAL | Four-role login, secure-cookie flags, CSRF, role denial, Host rejection, private file authorization, unsafe-config refusal and diagnostic redaction passed locally; actual TLS/edge/drain/resource behavior pending |
| RESEARCH MODE SAFE | PASS | Development provenance, 15-day retention and no active configuration; health/login/upload/smoke produced zero research sessions/events/configurations; no Study activation or methodology change |
| RAILWAY SETTINGS READY | PARTIAL | Exact settings, references, secrets procedure, volume requirements, migrations/bootstrap, daily operations and smoke/recovery guide prepared; dashboard/effective deployed config not applied/verified |
| READY TO PUSH | PASS | Official origin confirmed/fetched; target branch and both tags absent remotely at review; 22-file separate deployment commit authorized and reviewed; push remains manual |
| READY TO DEPLOY | BLOCKED | Requires manual push, correct source service/branch, secrets/scoped DB users, two persistent volumes and Linux/target-host verification; long-term Free cannot host both required volumes |

PASS is scoped to the stated local evidence, never hosted acceptance.
See [RAILWAY_DEPLOYMENT.md](RAILWAY_DEPLOYMENT.md) for the complete variable
table, exact owner-operated UI/Git commands, storage plan and smoke procedure.

## Executed source/configuration review

- Parsed 360 Python files using Python 3.14.6 AST; additionally evaluated the
  Gunicorn configuration. Compiled all 208 Jinja templates with the actual
  application environment. This is bounded static review, not pytest or a
  replacement automated test package.
- Checked 40 literal Jinja static asset references with exact filename case;
  no missing/case-mismatched literal was found. Dynamic references/browser
  rendering remain host acceptance checks.
- Eight production configurations fail closed: missing secret, missing hosts,
  root MySQL user, missing DB password, debug enabled, CSRF disabled, insecure
  session cookie and changed retention. No database connection occurred in
  these factory checks.
- Verified native Railway MySQL aliases, existing DATABASE_* precedence,
  mysql+pymysql/utf8mb4 and a generated password containing `:/@%`.
- Evaluated PORT binding `0.0.0.0:43819`, one gthread worker/four threads,
  graceful timeout 120 seconds and the access-log exclusions. This evaluates
  configuration; it does not prove that a Linux process actually bound a port.
- Injected a synthetic caught exception with secret/private-filename markers
  into the production diagnostic path. Type/operation survived; marker values
  and traceback did not. No SQL echo, permissive CORS or debug mode was enabled.
- Verified all original 691 source bytes still match the ST2 delivery manifest;
  512 protected UI/static/blueprint/domain/migration/research/freeze files in
  the deployment worktree remain byte-identical. This does not repeat or
  approve ST2 visual/accessibility/browser acceptance.
- `git diff --check` passed; the separate deployment commit uses only the
  reviewed 22-file allowlist and leaves both worktree indexes empty. Private
  `.env`, instance/venv/data, backups and exports are Git-ignored and excluded
  from the Railpack context/runtime inputs. A heuristic secret review found
  no real credential in preserved authored source; this is not a formal
  comprehensive secret-scanner certification.

## Dependency evidence and Linux boundary

Production direct pins are preserved. PyMySQL's RSA extra adds cryptography;
tzdata supplies minimal-image IANA timezones. The complete Linux lock has 41
pinned packages, each with a verified downloaded wheel SHA256. Installation
is wheel-only and hash-required. No C/Rust/media compilation was attempted.
The same versions installed into an owned Windows validation venv; `pip check`
reported `No broken requirements found.` Main development dependencies and
machine installation were not changed.

The host has Python 3.14.6 and MySQL 8.0.46. Docker is absent and WSL is not
installed. No Linux package installation/import, Railpack build, Gunicorn
process startup, real port binding, non-root volume access, TLS edge or actual
Railway deployment ran. The remote JSON schema endpoints were unavailable;
configuration syntax and fields were checked against official documentation
and Railpack's Python provider source, including v0.40.1, not a successful
schema-validation/image-build result.

Before declaring full readiness, execute the selected Railpack build and
production command on Linux with a real disposable volume/database and the
documented non-study environment. Verify exact interpreter, hash install,
UID 10001, listener, signals, permissions and private-file access. Do not
silently downgrade runtime or replace storage on failure.

## Actual disposable MySQL and native-flow rehearsal

Only a unique ignored `instance/railway_rehearsal_<random>` data directory,
random loopback port and generated transient credentials were used. Live
`@@datadir`/`@@port` guards were checked before creation and shutdown. Native
Windows MySQL uses a monitor/child pair: early harness termination left child
servers alive; all owned servers were subsequently shut down with guarded
SQL SHUTDOWN, and the final rehearsal uses that operation. No data was deleted
and no existing MySQL service was stopped.

The final bounded rehearsal completed all 45 recorded checks successfully:

- Actual `scripts/railway_migrate.py` twice: clean migration then repeat;
  exact 75-table schema/head/model delta. Initially zero users, groups,
  assignments, submissions, uploaded files and research records.
- Separate schema-scoped migration/runtime users using caching_sha2_password;
  runtime CREATE TABLE denied with MySQL error 1142. Generated runtime password
  punctuation and RSA authentication worked.
- Actual production WSGI import, secure configuration, development provenance,
  15-day policy, liveness/readiness 200 and Railway health-check Host accepted.
- Untrusted Host rejected 400; missing-CSRF POST rejected 400. A demonstrated
  pre-routing URL-generation crash in the historical error handler was fixed
  with a constant Host-error response. Normal error UI is unchanged.
- Four fictional disposable users: Administrator/Student/Teacher/Researcher
  ordinary login 302, Secure/HttpOnly/Lax cookies and own dashboard 200.
  Student/Admin cross-workspace denial 403; Messages and research export
  empty-state page 200. Valid HTTPS forms retain same-origin Referer checks.
- Student CSS, local Inter font, protected Login CSS and collector JS 200.
- Native Account PNG upload, owner download, private no-store caching,
  anonymous denial and unrelated account denial; invalid image signature
  rejected 400. No private upload went into static assets.
- Native logout invalidated access. Smoke/health/login/upload activity left
  research sessions/events/configurations at zero. Empty Development retention
  command succeeded without activating collection.
- Missing migration secret exited safely. After actual guarded database
  shutdown, readiness returned 503/unavailable with class-only diagnostics;
  an independently unallocated-port probe also returned 503.

Requests used Flask's actual WSGI/test-client dispatch on Windows, not its
development server and not a real browser/Linux Gunicorn process. An empty
export listing is not populated export generation/contract acceptance.
Assignment Text/Single File submission/Teacher review, real speaking/browser
recording, screen reader, non-Blink, visual/RTL/mobile and file persistence
after actual restart/redeploy remain bounded target-host acceptance gates.
The existing delivery evidence is historical ST2 evidence, not a new pass.

## Git and operational stop point

| Item | Final state |
|---|---|
| D1 commit/tag | `9ab3e4c3ba89d6eae581bce63951674b14c4e41b` / `YC-VA-D1-20261008` |
| ST2 commit/tag | `c64429855252df8177d9a455f9bd25d39cc32753` / `YC-VA-AT2-20261008` |
| Ancestry | `99133899529376436f52c292df09f5894e9e8cc1 -> D1 -> ST2 -> separate Railway deployment commit` |
| Primary branch | `master`, same HEAD/index/396 expanded status entries as the initial audit |
| Prepared branch | `preserve/st2-20261008`; separate deployment commit changes 11 existing/adds 11 files; resolve exact ID with `git log -1 --format=%H` |
| Needed normal pushes | D1, ST2 and the deployment commit on the selected branch, both annotated tags; remote master stays unchanged |
| Remote | Owner-confirmed official origin is `msj-group/adaptive-lms-finalproject`; fetch succeeded; remote master `9913389`, target branch/tags absent at review |
| Cloud state | No push, deployment, variable/resource/volume/scheduler change or paid provisioning |
| Research state | Candidate not frozen; Study collection never activated |

External evidence remains in the task's owner-controlled visualization folder:
`SOURCE-PRESERVATION-AUDIT.json`, `linux-dependency-resolution.json`, downloaded
Linux wheels, `railway-static-review.json`, `railway-local-rehearsal.json` and
`owned-rehearsal-shutdown.json`. These QA/private runtime artifacts are not
deployment source and must not be committed/pushed. Both independently
preserved historical source snapshots/manifests remain untouched.
