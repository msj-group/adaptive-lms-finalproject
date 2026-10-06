# Repair integration and database execution

Latest owner authorization, 2026-10-06: complete remaining source/interface
integration, cleanup and documentation. The owner separately requested the
completed development reset/demo seed and deletion of old tests, then explicitly
declined a new automated test package. No tests were recreated or executed in
the final close-out. Earlier results below remain evidence of their own builds.

## Final source and manual review

- Reduced twelve retired financial/enrollment controller modules to their
  actual compatibility handlers and required nested lookups. Kept public URLs,
  role checks, HTTP methods, CSRF and private response headers.
- Extracted current shared response headers to `financial_http.py`, invoice/
  receipt sequence allocation to `financial_numbering.py` and immutable report
  presentation objects to `financial_report_types.py`. The verified provider
  entry now contains only its public types and general-account delegation.
- Removed 26 retired Python modules and 54 obsolete finance templates after
  checking external imports and template consumers. Four additional Student
  list templates were removed when their routes became type-selected redirects
  to Activities. Retained historical migrations and compatibility models/tables;
  no additional database revision or table drop was required.
- Connected the missing Administrator finalized-attendance correction screen.
  It reuses the existing academic/Group/ascending-User/Schedule/session/record
  lock chain, rechecks nested ownership and current actor, compares a signed
  record snapshot, preserves roster/finalization and appends the old/new revision
  in the same transaction. Notes are bounded and remain private to staff.
- Removed private attendance notes from Student historical attendance and
  correction output. Note-only revisions create no public correction entry.
- Finished old Student activity bookmarks and return links. The four execution
  workflows remain separate. Fixed wrapping of shared portal navigation and
  responsive Activities filters; measured document width and scroll width both
  375px in the 390px narrow browser viewport after the fix.

The final browser session used the real local app and the four fictional
accounts. It covered:

| Role | Actually reviewed |
|---|---|
| Administrator | Login/dashboard; Rooms, effective schedules, all three trimmed Course prices; signed account (961.7 LYD), financial history/current report/deleted register; scoped actual-Course correction chooser; finalized attendance correction |
| Teacher | Login/dashboard; Foundation A gradebook, released totals and finalized attendance |
| Student | Login/dashboard; four-type Activities; Type/Course filters; listening detail/audio controls; current and withdrawn enrollment records; private-note hiding; old listening bookmark redirected to Activities with Listening selected; narrow-screen layout |
| Researcher | Password-only login; Demo dashboard (3 sessions/12 events/3 prompts); configurations; storage; full-session cleanup preview; stored export detail (0 study rows) |

A browser save updated only the fictional private attendance note for
Foundation A's `2026-09-21` class. The revision page displayed its original
wording, updated wording, Demo Administrator and `2026-10-06 14:50:07` local
server time. The mark remained Excused. Student attendance and correction pages
did not display either private wording. Administrator access to Research and
Student access to the correction URL both returned Forbidden.

No raw research deletion, archive removal, message sending, money movement,
new enrollment or collection start was performed in this browser review.
The current active configuration was paused and its period started on
`2026-10-07`; that operational state was preserved. A browser connection
interruption during responsive review was recovered with a fresh tab; the final
narrow layout was then observed successfully and the temporary viewport reset.

Static review parsed the current Python modules and Jinja templates, resolved
static template references and found no missing internal imported modules.
Application startup after cleanup succeeded. Exact final counts and whitespace/
route checks are recorded in the close-out entry in `PROJECT_STATUS.md`.
These checks and bounded manual usage do not prove all races, deadline edges,
financial mutations, recovery cases or full regressions. Automated acceptance
was explicitly declined by the owner and is not claimed as passed.

## Completed owner-requested development reset

At `2026-10-06T07:33:58Z`, reset only the current local development business
data in a transaction while preserving the 73-table schema and revision
`085b7a4e9012`. Created four active accounts (Administrator/Teacher/Student/
Researcher), three priced Courses, six Groups, schedules/Rooms, current/future/
withdrawn enrollment episodes, private lesson/audio files, all four activity
types, released grades, attendance, communications, financial examples and
clearly classified demonstration research sessions. All data is fictional.

Verified the four active roles and password hashes, foreign-key ownership,
seed counts, private-file digests and exclusion of demo data from study export.
Passwords remain out of documentation. The old test tree, pytest configuration
and two runner/resource scripts were removed (225 source files). The ignored
private recovery directory is
`instance/repair_mysql_tests/development_backup_20261006T073355Z/`, containing
the SQL snapshot, uploaded-file archive, old-tests archive and SHA-256 manifest.
This is a completed, bounded owner operation, not permission to repeat a reset.

## Earlier implemented source before final cleanup

- Versioned account operations and preserved account changes; Student active
  enrollment and last eligible Teacher guards; Rooms and effective Teacher/Room
  schedule conflicts under shared resource locks.
- Course prices, Decimal LYD `0.001` quantum, one HALF_UP percentage calculation,
  and display without insignificant fractional zeroes. Prices are frozen at
  enrollment; existing unpriced Courses remain unset until an Administrator
  supplies the actual price.
- Derived signed Student Account, repeated partial collections, verified bank
  money, credit and actual payouts. Invoice references are context only. Financial
  revisions preserve identity, exact old/new values, actual actor or verified
  provider, server time and reasons. Current account and history are separate.
- Atomic episode enrollment/invoice/discount/optional initial collection;
  admission closes at study start; withdrawal, same-course transfer and actual
  wrong-course correction preserve source membership and learning context.
  Re-enrollment starts a new episode; transfer displays destination progress.
- Episode-owned learning and own released historical records, including prior
  progress and recording access; academic correction history; trusted complete
  server-body receipt time protects timely quiz/listening submissions even when
  an expiry worker wins the processing race.
- Four-type Activities hub and Upcoming activities with authorized bounded
  queries and intentional research instrumentation.
- Opaque original-account/configuration delivery scope, serialized configuration
  numbering, authenticated Researcher/operator attribution, usage measurement,
  full started-session range cleanup, preserved gap/export metadata and original
  archive availability. Password-only login and environment-configured approved
  15-day daily retention remain unchanged in meaning.
- Legacy finance/enrollment HTTP entries redirect or delegate to new scoped
  review screens; existing public URLs, role checks and CSRF are preserved.
  General financial reports and sandbox verified provider delivery use the new
  account. At that checkpoint private historical helpers were retained;
  the final cleanup described above now removes their superseded algorithms.

## Database chain and recovery

The local development target was inspected read-only: MySQL 8.0.46, 62 InnoDB
tables, exact parent `d574ab56594f`. Row counts were 7 Users, 2 Courses, 3 Groups,
5 Enrollments, 3 Invoices, 4 money movements and no research Sessions/archives.

Exact upgrade chain:

`d574ab56594f -> c1a7e4d9b203 -> e31b8c4d902a -> a47e25d90c61 ->
b52f9d1a0e83 -> d68e4a1b720f -> f74c8e20a315 -> 085b7a4e9012`.

The migration preflights refuse inconsistent historical ownership or significant
fourth-decimal money, rather than rounding or inventing an actor. Existing Course
prices remain unset. Legacy study starts use the first scheduled class, otherwise
the term start in the application timezone. No retrospective EnrollmentEvent or
FinancialRevision is fabricated. Existing learning/financial records are backfilled
to their provable original Student and Enrollment episode.

Recovery uses an ignored private SQL snapshot, manifest and private-file archive.
Verify SHA-256, restore to an owned isolated schema, compare original table counts,
then upgrade that restored copy before applying live DDL. A source archive alone
is insufficient. MySQL DDL is not transactionally reversible: on a partial failure,
preserve evidence and restore deliberately; do not retry blindly or downgrade a
populated development database.

## Executed verification - 2026-10-06

Test execution resumed only after the approved source workflows and integration
were implemented, as the owner required. Checks used owned isolated MySQL schemas
and temporary credentials. No test connected to the development database.

| Gate | Executed result |
|---|---|
| New repair domain + original delivery scope | 44 passed and 35 subtests passed; one history-access test needed an explicit logout between synthetic accounts |
| Corrected own historical progress case | Passed in the subsequent targeted integration run; outsider receives 404 |
| UI/reports/provider/concurrency integration | 4 passed in 32.11 seconds: rendered account/history/reports, stale invoice refusal, partial verified provider delivery/replay, competing last seat |
| Four-type Activities and dashboard | 1 passed in 17.09 seconds: all four types, type filter and other-Student isolation |
| Research cleanup/retention/actual operator | 3 passed in 25.10 seconds: full start-date-selected sessions including events beyond the boundary; last-activity expiry with service attribution; real Researcher CLI authentication/audit |
| Chrome original-scope + entry/auth regressions | 51 passed in 111.20 seconds, including 2 actual Chrome account/configuration-switch cases |
| Clean chain and restored-backup upgrade | 2 passed in 95.02 seconds; exact head, current model/schema agreement, CHECK names, original table counts and three restored private files |

Initial focused probes exposed and corrected the MySQL singleton auto-increment
CHECK conflict, missing private-cache header on enrollment history, trimmed CSV
numeric-shape rejection and mismatched sandbox outcome names. Test preparation
also needed standard-library HTML parsing, actual synthetic operator attribution
and explicit account logout. Failed probes are retained as diagnostics; they are
not claimed as passing runs. Passing focused evidence covers 106 distinct selected
cases across the final targeted runs, plus 35 subtests. This is not a full-suite
acceptance claim.

## Applied development database

At `2026-10-06T06:57:30Z`, applied the exact seven-revision chain from
`d574ab56594f` to `085b7a4e9012` to the inspected local development database.
The resulting schema has **73 InnoDB tables**. Read-only post-upgrade reflection
found no model/type/key/index differences and matched all declared CHECK names.
Every original table retained its row count. Five historical EnrollmentMembership
rows were backfilled; no retrospective event, financial revision, price or actor
was fabricated. No downgrade, reset or arbitrary business-data update occurred.

Ignored private recovery evidence:

`instance/repair_mysql_tests/development_backup_20261006T064243Z/`

- `development.sql`: 171,092-byte consistent SQL backup; digest
  `744c11fea7a1016d4d1aca0d500903ae0a1750de80e325de309f55e8b56397a9`.
- `private-files.zip`: three private files, CRC/content digests verified and
  restored into a temporary owned location during the recovery gate.
- `manifest.json`, `restore-upgrade-verified.json`, `live-upgrade-receipt.json`:
  exact-parent, digest, original row counts, restore/model agreement and live head.

Do not commit or publicly share the private backup or test logs. Existing source
recovery remains separately available; the backup is not an automatic restore
instruction. A running application must reload this source after deployment.

## Historical remaining release acceptance (superseded owner gate)

P1F/full strict suite and broader changed-contract regression acceptance remain
pending. Old assertions for forbidden invoice edits, invoice allocation, fee-plan
runtime and enrollment reactivation must be reviewed against the approved contract;
valid security/privacy/replay/history scenarios must be retained. Private historical
helpers remain while those historical tests/migrations are supported; active HTTP
routes use the new operations. These limits do not mean the development migration
or the selected integration checks are pending.

No collection period, scheduled task, dependency installation, .env change,
staging, commit or push was performed. Tests began after source completion; all
owned resources were stopped by their normal cleanup after the selected gates.
