# Repair implementation plan

Owner-approved contract: [APPROVED_REPAIR_CONTRACT.md](APPROVED_REPAIR_CONTRACT.md).
Current checkpoint and evidence: [PROJECT_STATUS.md](PROJECT_STATUS.md).

This plan rehabilitates the existing Flask modular monolith in bounded Parts.
It does not authorize one bulk rewrite. Complete each Part's acceptance gate,
report the actual evidence and preserve unrelated work before proceeding.

## Latest owner close-out decision - 2026-10-06

The owner explicitly declined a new automated test package after requesting
deletion of the old tests. Do not recreate or run automated tests. Complete the
remaining legacy cleanup, bounded four-role manual browser review and static
source/template checks, and document their actual scope. This decision supersedes
the historical sequencing and replacement-coverage gates below. A missing full
regression run must not be described as a passed gate.

The development reset and fictional four-account seed were separately authorized
and completed after the in-place upgrade. The current schema remains `085b7a4e9012`;
all three demo Courses have prices. Historical migration files and compatibility
models remain to preserve the supported schema chain and scoped old bookmarks.
Retired runtime algorithms, forms and templates can be removed once their actual
consumers have been moved. No repeat reset, production deployment, scheduler
change or Git mutation is part of this follow-up.

## Historical owner sequencing change - 2026-10-06

The owner instructed all tests to stop and deferred further test execution until
all approved repair changes are implemented. The active P1F gate was interrupted
and its owned resources stopped. Continue P2-P8 and the remaining prepared P7A
source changes in bounded implementation steps, preserving the same domain and
security contract. Per-Part test gates, including P1F, are deferred to final
verification; implementation is not acceptance. Prepare/update meaningful tests
without executing them or invoking collection-only pytest. P9 live database,
scheduler, release and Git actions still require their concrete approved scope.

## Latest research resolution

## Earlier integration checkpoint - 2026-10-06

The owner additionally authorized finishing integration, remaining legacy entry
points and documentation, and applying the new database revisions. P2-P7 source
changes are present; P8 HTTP consumers now delegate to the new episode/general
account workflows. Private historical helpers and historical migrations are
retained until replacement acceptance, rather than deleted before coverage.
The database scope is an in-place upgrade of the inspected local development
database from `d574ab56594f` through `085b7a4e9012`, with a verified backup/restore
and isolated migration gates first. No reset, business seeding, scheduler change
or Git commit is included. Status and executed evidence are recorded separately
in `PROJECT_STATUS.md` and `REPAIR_INTEGRATION.md`.

On 2026-10-05 the owners chose password-only Researcher authentication and
15-day retention with daily expiry. Preserve the pre-existing shared login.
Earlier proposed MFA and pressure-only automatic purge work is removed from
this plan. Storage visibility, started-session range export, manual cleanup,
gap reporting and operator accountability remain in scope.

## P0 - Contract, recovery baseline and documentation

Scope: preserve a verified source baseline; record the final decisions;
update authority pointers; add README/Quick Start and a phase/status tracker.
No application/test/schema/env/scheduler changes, no installs or Git mutation.

Exit criteria:

- Approved/current/historical facts are clearly distinguished.
- MySQL-only target is documented without pretending SQLite is removed.
- Shared login, password-only and 15-day decisions agree across documents.
- Every accepted finance, enrollment, schedule, record and Activities rule
  appears in the contract, including transfer after destination study start.
- Source recovery archive is checked; local links and diff whitespace pass.
- Prior source/tests and deleted-file state are preserved byte-for-byte.

## P1 - Runtime entry and MySQL-only verification foundation

Scope: implement canonical `/` and unknown-environment rejection; create
an explicitly isolated MySQL test harness, then convert DB/migration/browser
fixtures and SQLite-specific runtime branches. Keep pure tests DB-free.
Split this phase into reviewed Parts; test-server provisioning/installations
need their concrete target and authorization before execution.

| Part | Bounded scope |
|---|---|
| P1A | Canonical root and fail-closed environment selection, with DB-free factory/identity checks |
| P1B | Owned disposable MySQL instance/lease infrastructure and pure target-guard checks |
| P1C | Config, central/local app fixtures and isolated per-app schema resources |
| P1D | Browser, committed snapshot/concurrency and CLI database resources |
| P1E | Migration probes through exact parent revisions, MySQL constraint assertions, verified historical downgrade corrections and a reviewed source revision for exact closed-code collations (synthetic execution only) |
| P1F | Fresh Alembic/model agreement, focused/full strict test and browser gates |

P1A through P1E are complete; P1F acceptance is deferred by owner instruction. See the status evidence
and the MySQL transition record before using any destructive test fixture.

Exit criteria:

- Anonymous and all-role root routing is correct; shared login stays intact.
- Misspelled environment fails closed; no debug fallback.
- DB tests refuse non-MySQL engines and non-isolated database targets.
- Fresh Alembic install and schema/model checks pass on isolated MySQL.
- Browser and snapshot/concurrency checks use MySQL; no silent DB-test skip.
- CSRF/rate limits/auth-version and workspace-isolation regressions pass.

## P2 - Accounts, eligibility, Rooms and schedules

Scope: stale-safe account/status writes; Student active-enrollment guard;
last-eligible-Teacher suspension guard; Room management and authoritative
Group/Teacher/Room schedule conflict checks.

Exit criteria:

- Competing edits cannot silently overwrite a later update.
- Account deactivation preserves history and respects active enrollments.
- Teacher suspension cannot bypass the replacement requirement.
- Room/Teacher effective-time overlap and concurrent writes are tested;
  adjacent slots remain valid. Historical schedules stay interpretable.

## P3 - Finance foundation and corrected/history views

Scope: Course price and invoice snapshots; LYD quantum/formatting/discount;
general collections, actual payouts, editable/deletable document revisions,
derived signed Student account and chronological correction reports.

Exit criteria:

- Discount amount/percentage, zero charge and rounding/display are correct.
- Collections are independent of invoice allocation; partial payments work.
- Pending/rejected money is excluded; credits and actual payouts balance.
- Editing obligations below money received creates valid credit.
- Document changes preserve evidence and do not erase older cash history.
- No mutable balance, ReceiptAllocation or installment table is introduced.
- Idempotency, numbering, stale forms and concurrent writes pass on MySQL.
- Existing payment provider security remains; real card processing is absent.

## P4 - Episode enrollment and atomic lifecycle operations

Scope: Enrollment episodes and Group membership history; atomic enrollment,
invoice/discount/optional collection; withdrawal/reversal; same-course transfer;
new episode on re-enrollment; actual wrong-Course/Group correction.

Exit criteria:

- New/re-enrollment is refused at/after Group study start, using one trusted
  authoritative cutoff. Same-course transfer after start is permitted.
- A repeated or failed operation creates no extra episode, liability or cash.
- Last-seat competition and transfer source/target locks are verified.
- Withdrawal releases a seat once; optional net-obligation reversal is audited;
  payout is a distinct real-money operation.
- Same-course transfer creates no charge; source history survives.
- Wrong-course correction changes real membership and financial effect while
  preserving original learning evidence and checking target eligibility.

## P5 - Academic episode ownership, corrections and fair deadlines

Scope: progress/attempt/submission/roster episode linkage; own historical
access; grade/attendance/feedback revisions; trusted request-arrival deadlines.

Exit criteria:

- Repeat enrollment cannot inherit prior attempts/submissions/completion.
- Transfer reports destination content and preserves source progress/history.
- Course Progress is never presented as mastery.
- Historical reads survive withdrawal/archive/account reactivation without
  granting write access or leaking another student's work/answer keys.
- Each correction has old/new/actor/server time and optional reason.
- Server processing/lock delay does not penalize on-time timed submissions;
  forged client clocks, replays and stale activity states remain protected.
- Include an on-time submission competing with a later observer that settles
  expiry. Capturing arrival time alone is insufficient if that observer can
  finalize the attempt first; preserve terminal-state and stale-state defenses
  while proving the fairness requirement with independent MySQL connections.

## P6 - Unified Activities and bounded Student reads

Scope: one Activities hub/navigation entry; type/state/Course filters;
action/deadline priority; all-four-type upcoming dashboard; episode history.
Keep type-specific execution pages, Course lessons and separate grade/attendance
records. Register route/element tracking deliberately under the safety contract.

Exit criteria:

- All four types appear only when authorized and available.
- Actions/states/deadlines and enrollment context are correct and bounded.
- Dashboard includes all types and navigation works on mobile/desktop.
- Existing activity rules, safe historical reads and research sampling/session
  meanings remain intact; coverage tests include the new hub.

## P7 - Research attribution, accountability and storage visibility

Prioritize F15 in a small independent Part after P1; it need not wait for
every finance/academic phase. The rest of this phase preserves Phase 6.

Historical P7A preparation boundary (now applied; current evidence in REPAIR_INTEGRATION.md):

- Issue an opaque purpose-specific delivery proof from the existing account
  public identity/authentication version and configuration public identity/
  immutable version number. Read these in the existing bounded preview query;
  do not store or export the proof or expose account identity to the collector.
- Require the proof in browser event batches and verify it against locked
  account/configuration rows before subject lookup/provisioning. Repeat that
  check on every provisioning retry. A stale/tampered scope is a permanent
  safe refusal, never a request to retry against the new scope.
- Preserve the original proof and event IDs through outbox retries. Discard
  other-scope pending data without crediting its dropped count to the current
  scope; late callbacks must not clear a newer scope's pending data.
- Keep the established session-reference resolution, clocks, event age,
  inactivity, replay rules, sampling, prompt display/answer and provenance
  behavior. Server-confirmed best-effort outcomes remain outside browser F15.
- Expected source boundary: collector hooks/routes, collection scope/ingestion,
  pure batch validation/top-level dictionary, a pure delivery-proof helper,
  collector JavaScript and their directly affected fixtures/regressions. F15
  uses existing identities and needs no schema change. Verify account/config
  switches, missing/forged proofs, lock-time changes, subject non-provisioning,
  unchanged replay IDs, privacy and real-browser delayed callbacks on MySQL.

Preparation evidence (not runtime acceptance): an ignored candidate preserves
all other top-level functions outside its declared changes in six existing Python
source files and adds one pure proof helper. Eight pure proof tests, thirteen pure
protocol/lock-order tests and ten JavaScript delivery simulations pass. Thirteen
owned MySQL checks also pass, including independent-connection account/configuration
changes while ingestion waits for locks. A real configuration race first exposed
opposed secondary-index/primary-key lock acquisition; the candidate now discovers
the current configuration ID without a lock, locks that primary row and rechecks
its liveness and proof before subject access. Four isolated actual-Chrome delivery
scenarios and 46 candidate-overlay regressions pass; the latter include six actual
LMS/Chrome checks and six export/snapshot checks. JavaScript syntax verification
also passes. No canonical runtime, test or migration source was changed for this
candidate during the immutable P1F full gate.

Two further integrated LMS/MySQL/Chrome candidate tests pass: actual browser
delivery is deferred until after an account-cookie or configuration switch;
the unchanged old batch is rejected and new-scope delivery is accepted with
correct subject/configuration rows. The browser harness's sign-in shortcut
disables CSRF; the separate owned transition gate verifies actual CSRF behavior.

Under the 2026-10-06 sequencing instruction, apply the bounded candidate, adapt valid test batches explicitly
to the new transport field and retain raw missing/forged-proof negative cases.
After all repair source changes are implemented, rerun the owned MySQL transition/locking and actual Chrome gates against the
applied source, including navigation/replay and delayed-callback scenarios.
Pure extracted-function checks prove control-flow order, not InnoDB blocking;
JavaScript simulations are not a substitute for the real browser gate.

Scope: original-scope browser delivery binding, safe configuration numbering,
trusted operator attribution, storage usage/gaps, session-start range export
and auditable manual raw-data cleanup. Preserve password-only/shared login,
15-day daily retention, population, sampling and all self-report semantics.

Exit criteria:

- Account/config switches cannot reattribute pending events; repeats keep
  IDs/idempotency. Browser and MySQL transition cases pass.
- No name/account/content/financial data leaks through research pages/exports.
- Action attribution covers workspace/operator/service paths.
- Usage and gap reporting are truthful; selected sessions keep full dependent
  windows. Cleanup leaves exclusion/link/audit metadata enforceable.
- Archive bytes/digest/snapshot/timezone and 410-after-removal remain correct.
- Daily 15-day expiry is tested in isolation; no pressure-only replacement
  or MFA requirement is introduced. Live task changes are separately scoped.
- No real collection period, model, binary labels or adaptation is invented.

## P8 - Legacy removal and service/query consolidation

Scope: convert remaining consumers then remove obsolete runtime for fee
plans/assignments, reactivated episodes and overwritten record history;
finish bounded reads and coherent operation services. SQLite removal belongs
to P1 and is audited here. Keep prototype isolated and migration history intact.

Exit criteria:

- No active route/template/import depends on an eliminated contract.
- KEEP/REWRITE/MERGE/DELETE tests follow approved behavior, not code mirroring.
- Old public links have intentional outcomes; no accidental dead navigation.
- Required focused/full strict gates pass without unexplained skips.
- Shared login, natural-use research and 15-day cleanup remain supported.

## P9 - Clean development database, recovery and release verification

Scope: provision the approved clean MySQL target; apply reviewed revisions
only to that target; optional fictional seed; database/private-file restore
test; all-role browser/security/operational verification. Live actions need
explicit target/revision approval; current test data being disposable does
not authorize indiscriminate deletion or copying credentials into reports.

Exit criteria:

- Fresh migration chain, enforced constraints/indexes and model agreement pass.
- No unexpected revision or manual data repair is silently accepted.
- Backup/restore is verified; source, DB and private-file state are compatible.
- Core operations, mobile/desktop flows and isolation pass against MySQL.
- Deployment settings, rate-limit/header topology and residual risks are
  documented with actual evidence. No unsupported production-readiness claim.
- Real research startup remains an explicit action outside synthetic checks.

## Common evidence and recovery gates

- Inspect pre-existing diff and exact expected files before each Part.
- Use focused tests first, then relevant regression/full strict gates where
  risk requires them. Report checks actually run separately from old results.
- Test MySQL locking with independent connections, not just asserted SQL.
- Review idempotency, consistent lock order, stale snapshots and rollback.
- Inspect migration DDL/constraints and data-loss effects before approval.
- Source archive is not a DB backup. Test restore before destructive DDL.
- Do not reset, stash, drop, install, register tasks, commit or push merely
  because it is the next item in a plan; obtain the action's concrete scope.
- If an acceptance gate fails, diagnose it without starting unrelated phases
  or reporting completion. Keep the tracker honest about remaining work.
