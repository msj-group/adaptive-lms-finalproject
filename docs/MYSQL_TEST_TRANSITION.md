# MySQL test transition

Historical record only: on 2026-10-06 the owner requested deletion of the old
test package and its runner, then explicitly declined creating replacement
tests. Commands, fixtures and acceptance gates below describe earlier work
and must not be treated as available current instructions. Current authority
and manual/static close-out evidence are in `PROJECT_STATUS.md` and
`REPAIR_INTEGRATION.md`. No automated tests are authorized for this close-out.

P1A through P1E are complete, including historical migration conversion.
P1F focused, actual CLI, Chrome and export gates have passed; fresh full-suite
acceptance remains required. All DB resources use owned isolated MySQL.
Detailed current evidence is in `PROJECT_STATUS.md`; older sections below
retain the original audit and staged transition history.

## Initial audit findings before conversion

The inspected test tree has 22 files with explicit SQLite URLs, 34 with
PRAGMA checks and 51 calling `create_app` (including DB-free config checks).
Changing TestingConfig's URL alone would expose create/drop helpers to a
shared database and would leave browser/migration overrides on SQLite.

Migration probes sometimes fabricate parent INTEGER keys; actual MySQL
revisions reference BIGINT. Replace probes with the exact parent Alembic
revision and representative seed rows, then assert MySQL constraints.
Browser and export-snapshot writer threads need independent InnoDB
connections, not SQLite WAL or a global shared schema.

## P1B resource infrastructure

`tests/mysql_runtime.py` prepares an explicitly authorized, disposable
local MySQL process. Importing it or checking ownership starts nothing.
Pure guards are in `tests/test_mysql_runtime.py`.

The P1A and ownership checks passed 42 strict-warning tests (20 + 22),
without subprocess/MySQL execution. The reviewed command for the next
operational check, after its scope is accepted, is:

```powershell
.\.venv\Scripts\python.exe -B scripts/verify_mysql_test_resources.py --authorize-isolated-mysql
```

It verifies initialization, endpoint identity, InnoDB/FK settings, one
synthetic table/row, owned schema removal and owned process shutdown.
It does not run the existing DB suite or any development migration.

Concrete proposed operational scope:

- Existing executable: `C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqld.exe`.
- Fresh run/data/log directory under ignored `instance/repair_mysql_tests`.
- Random loopback port, `--no-defaults`, no MySQL X or binary log; no service,
  scheduled task, package installation or machine configuration change.
- Generated temporary credentials, no development `.env` or existing server URL.
- Harness-issued `aelms_test_<random>` schema leases only; verify process,
  port and server data directory before create/drop/shutdown.
- No wildcard cleanup or recursive directory deletion. Run files are retained;
  only the process launched by the helper may be stopped.
- Synthetic tests/migrations belong only to those owned schemas. Nothing
  connects to the development database or changes existing accounts/data.

On 2026-10-05 the owners approved the one-schema smoke check, then explicitly
approved these isolated resources throughout repair tests/migrations/browser
checks, including multiple synthetic schemas. Repeated owned test runs need
no new approval. The scope excludes development DB access, installations and
machine/service/task changes. A code flag is a guard, not a substitute for
that recorded owner authorization.

The real smoke check passed on MySQL 8.0.46/InnoDB with foreign keys enabled;
the synthetic schema was released and the owned process stopped. Evidence is
`instance/repair_mysql_tests/run-7f3b9fa8cf7445ca96bc6b597fbb10be/resource-verification.json`.

## P1C core configuration and fixtures

`app/testing.py` rejects SQLite/non-owned target shapes before engine setup
and requires a lease guard before any actual connection. The default testing
URI is an unallocated sentinel; it cannot connect or borrow development
credentials. A harness guard verifies the exact URL and server ownership.

`tests/mysql_fixtures.py` provides `mysql_resources`/`mysql_app_factory`;
the original central `app`/`client`/material APIs now use it. Each app receives
its own schema and private temporary material path. DB resources require
`--isolated-mysql` explicitly; an omitted flag was verified to stop before
initialization. Pure checks never request the DB fixtures.

Payment/report helpers retain their APIs through `tests/app_factories.py`.
DB fixtures request `mysql_app_factory` before using them; pure factory checks
retain an unallocated target and cannot connect. Local fixtures still need
explicit conversion rather than silently taking a shared schema.

The hardened DB-free runtime/resource/config/material checks passed **108
tests**, and provider/foundation checks passed **68**, with warnings as errors.
The real role/authentication/redirect/rate-limit group passed **28 tests**.
Legacy retained fixture contexts exposed stale InnoDB read snapshots. The
test client now gives every HTTP request a distinct app scope and ends only
an outer read-only AUTOBEGIN transaction before entering it. Uncommitted
writes, explicit/nested transactions and locking reads are refused without
rollback. This is test infrastructure; isolation remains REPEATABLE READ.
Eight MySQL boundary checks and the two affected cases passed together (10);
seven DB-free client/stream cleanup checks passed. The complete role/session/
authentication and representative local fixture gate passed **38 tests**
(319.81 seconds), with warnings as errors. P1C is closed; see Project Status.
Use the clean runner before any app import: it disables dotenv,
removes deployment DB credentials and prevents every named factory mode
from falling back to a development connection. A supported targeted command is:

```powershell
.\.venv\Scripts\python.exe -B scripts/run_isolated_tests.py --isolated-mysql -p no:cacheprovider tests/test_runtime_entry_mysql.py tests/test_auth_redirect.py -q -W error
```

Do not expand this command to the full suite before migration probes are
converted and reviewed.

P1D real gates: six export checks, seven Chrome scenarios and one actual CLI
first-revision round trip passed. After cleanup review the affected mixed
gate passed 13 checks; final WSGI/cleanup/client pure gate passed 16. Live
workers prevent engine disposal/schema release. See Project Status for
counts and exact boundaries.

P1E's financial migration gates have passed 114 tests on real owned MySQL;
all academic/research resources are converted but reverification and full
acceptance remain pending. The probes
also exposed MySQL downgrade dependency failures, now corrected and checked
in the affected financial and first academic batch without changing upgrades.

P1E's shared migration resource builds a blank owned schema through the
actual Alembic chain to a literal parent. Its initial guard/two-parent gate
passed eight checks. Historical target Operations probes retain the startup
parent in the version table; full CLI version advancement belongs to P1F.
No schema fabrication, version stamp or disabled MySQL foreign key is used.

## Remaining Parts

P1E is closed: 55 research checks and 24 exact-code/error/real CLI checks
passed in their final strict gates. Current-head model agreement includes
effective collation inspection. Source and history evidence are in Project Status.

1. P1F: focused/full strict gates, plus
   real browser and independent-connection concurrency checks.

P1F resource review removed independent table/engine teardown from 59 model
fixture/scenario files. The common factory alone releases engines/schemas
after worker guards; a failed session/engine cleanup retains the schema.
Intentional in-test resets use an owned, quiescence-checked reset helper.
Blank model-schema creation compiles equivalent indexes inline; existing
tables or deferred/unsupported definitions retain standard creation.
Actual MySQL reflection compares every table, constraint, index and column
collation. Runtime and Alembic never use this helper. The affected 51-case
strict gate and 11 DB-free cleanup checks passed; fresh full acceptance is
running and remains required.

P1F's optional empty-model-table templates retain at most two physically
unchanged, row-free schemas per template manager; normal resources attach one
manager per owned temporary server. Each application gets
a fresh schema and lease; only a verified empty template may move into it.
Old URLs, active workers and outstanding connections are refused. Actual
counters reset, FK enforcement and durability stay enabled, and migration
targets remain blank. Populated self-FK links decline reuse before data
mutation and use the existing owned schema-drop path. Focused schema/CLI
checks passed 39, reversal cleanup regression passed 2, and the subsequent
adapter/real Chrome/export gate passed 56 with strict warnings. Full coverage
is still pending.

The later four-part full run was interrupted after host memory allocation
failures; its 2,845 complete passes are diagnostic only. Model-template state
now weakly references its application to avoid retaining completed applications
through Flask-SQLAlchemy's weak-key engine map. A reproduced DB-free failure and
the subsequent strict 97-case lifetime/template/schema/cleanup/affected-case
gate verify this correction. The Material CHECK observer removes MySQL's
identifier quotes without altering its assertions. Default discovery is now
restricted to `tests/` by `pytest.ini`; the default collection audit counts 7,416
cases and preserves every prior canonical case. Fresh full acceptance uses
eight whole-file parts, at most two active database parts, and remains pending.

The durable first pair subsequently completed 1,871 passes, seven SQL-observer
failures and four known pure IANA skips; it did not execute the other six parts.
Three reviewed test-only adapters now read actual MySQL bound pagination values,
count one required per-request authentication lookup separately from the unchanged
ten-query dashboard feature budget, and match exact CHECK 3819 messages/names.
The seven affected canonical cases pass with strict warnings. Refreshed collection
preserves all 7,416 cases. A fresh eight-part durable run is in progress; full
acceptance remains pending. See Project Status for identities and evidence.

The subsequent eight-part gate completed all 7,416 cases exactly once:
**7,402 passed / 10 failed / 4 known pure IANA skips**, with no database/browser
skips or resource/cleanup failures. The ten failures exposed test isolation and
MySQL observer differences: in-process CLI logger disablement, a retained
read-only purge snapshot, explicit historical collation text, exact overlength
error types, the required authentication lookup, and an implicit timezone.
Ten test-only files now address those observations while preserving all 651
existing assertions and adding six. Runtime source remains unchanged from that
completed diagnostic gate. A strict 15-case focused gate passed, including both
actual migration CLI checks before the logging assertions and all ten failed
cases. A fresh durable eight-part gate is running; P1F full acceptance remains
pending. See Project Status for the exact run identities and source receipts.

Pure unit tests require no lease or server. Database tests must fail clearly
when safe resources are unavailable; silent skips and a development URI
fallback are unacceptable. Do not run old fixtures against a live database.
Shared login and existing research sampling/session/retention contracts remain
unchanged by this infrastructure transition.
