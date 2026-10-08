# Approved repair contract

Approved by the owners in the final-discovery conversation; implementation
authorized on 2026-10-05. This is the canonical target for rehabilitation of
the existing application. A target in this document is not a claim that it
has already been implemented or verified.

## Authority and current checkpoint

- Latest redesign authorization, 2026-10-07: P01-P06 and the Design Proposal /
  Architecture Review are approved. Begin W0, then W1, and follow dependency
  gates. The canonical visual/wave target is
  [VERSION_A_DESIGN_CONTRACT.md](VERSION_A_DESIGN_CONTRACT.md). Keep the shared
  Flask monolith, domain/security/research semantics and stable identities.
  No Version B or real study collection. Assignment file policy/migration and
  instrumentation are later, separately gated wave work. Source completion is
  not responsive/accessibility/runtime acceptance or Baseline Freeze.
- Latest enrollment decision, 2026-10-06: the owner removed the study-start
  admission cutoff. New enrollment, re-enrollment and wrong-course correction
  may use a started Group, with an informational notice before saving and after
  success. Keep the configured study start, eligibility, capacity, teacher,
  duplicate-enrollment, signed-preview, history and atomic-finance protections.
- Latest local-rehearsal decision, 2026-10-06: the owner authorized source
  corrections so ordinary Student usage can be reviewed and exported locally
  before redesign. Operational-review exports are allowed as a separate,
  server-selected dataset, explicitly identified in the manifest, session
  rows and creation audit. Study exports still exclude all non-study data.
  Preserve classifications, 15-day retention and sampling; no schema change,
  reset, automated tests, scheduler changes or Git mutation is included.
- Latest hosting/evaluation decision, 2026-10-06: a future deployment starts
  with schema and technical control rows only, no seeded users or business
  content. Preserve the current local database. Remove payment simulation and
  demonstration selectors/operator actions. Evaluation uses ordinary forms
  with manually entered fictional data. Its research sessions remain excluded
  from study exports; genuine study provenance requires a separate explicit
  deployment decision. This supersedes earlier demonstration-mode provisions.
- Latest owner decision, 2026-10-06: old tests were deleted and a new automated
  test package was explicitly declined. Complete the remaining source/interface
  integration, static review and documentation without recreating or running tests.
  Historical test gates below no longer block this owner-approved close-out;
  their absence is a verification limitation, not evidence that all cases passed.
- The separately authorized development reset and fictional four-account seed
  are complete at schema head `085b7a4e9012`. All three current Courses are priced.
  Research data remains demo/development, with collection initially paused.

- Direct owner instructions take precedence. Read this contract together
  with `DECISIONS.md` and the current approved implementation Part.
- Preserve the Flask modular monolith; no rewrite, framework replacement or
  automatic import of the separate `design/` prototype.
- The original from-scratch instructions in `MASTER_PROMPT.md` and the
  inputs describe the project's origin, not this repair's starting point.
- Current inspected HEAD: `fd500fd` (natural-use Phase 6 completion).
  The shared-login correction is pre-existing working-tree work and must
  be preserved. All roles use `/auth/login`; Researchers retain a separate,
  server-protected workspace. `/research/login` is a compatibility entry.
- `PHASE6_COMPLETION.md` records prior MySQL/browser checks and development
  revision `d574ab56594f`. Those checks were not rerun during Part P0.
- Work proceeds in bounded Parts with acceptance gates. A Part authorizes
  its own source/docs changes and isolated checks; database provisioning,
  migrations, live writes, dependency installation, scheduler changes and
  Git mutations must have an explicit scope before execution.

## 1. Runtime, database and documentation

- MySQL/InnoDB is the only approved database engine for development,
  production and every database-backed test, including browser and migration
  tests. Pure unit tests use no database or database fixtures.
- SQLite was present at the pre-P0 inspection. P1C has replaced the named
  TestingConfig/central fixtures with guarded owned MySQL resources; P1D
  converted browser resources and P1E converted historical migration probes.
  Runtime SQLite branches/type variants are removed. No full MySQL-only completion claim
  before the remaining P1 gate passes.
- Use an explicitly isolated test target and credentials. Fail closed if a
  test could use a development/production database or a non-MySQL engine.
- Prepare a new, clean MySQL development database later. Current development
  rows are disposable test data; this does not authorize an immediate drop
  or deletion of source, private files, credentials or history.
- Add canonical `GET /`: unauthenticated visitors go to the shared login;
  authenticated users go to their role's home. Preserve shared login,
  authorization, CSRF, safe return targets and its common rate-limit budget.
- Reject unknown environment names. Document setup, current entry points,
  migrations and verification without exposing credentials.
- Keep repository documentation, code and application UI in English; owner
  reports remain Arabic.

## 2. Money, course price and discounts

- Course owns the default price and currency; Group has no price override.
- Capture the price used at enrollment in the invoice. Later Course price
  changes affect future enrollment quotes, not existing liabilities.
- Operational currency is LYD, with a `0.001` monetary quantum. Use Decimal,
  never float. Calculate a percentage discount at sufficient precision and
  round once to the nearest quantum; use deterministic `ROUND_HALF_UP` for
  an exact half-quantum tie. Validate the final amounts before persistence.
  The rounding tie rule is an implementation convention, not a research rule.
- Display up to three fractional digits and remove trailing fractional
  zeroes only: `125.500 -> 125.5`, `125.000 -> 125`, `0.005 -> 0.005`.
  Formatting must not change stored values or remove significant zeroes.
- A discount is either an amount or a percentage, with the resulting amount
  snapshotted. Validate that the discount cannot exceed the course charge.
  A fully discounted course has a zero obligation with a traceable invoice.
- The authenticated actor granting the discount is recorded automatically;
  no editable actor selection. The reason is optional.

## 3. General student account and document history

- The student account is derived from effective invoices, confirmed
  collections and confirmed refunds/payouts. No mutable stored balance,
  `ReceiptAllocation`, receipt-to-invoice distribution or installment table.
- Collections reduce the student's general account. An optional invoice
  reference supplies context only, not an allocation or balance dependency.
- Multiple partial payments are supported. Pending/rejected movements are
  not confirmed money. Pagination and request limits must not become a
  maximum lifetime count of collections or payment attempts.
- Signed balance:
  `net obligations - net confirmed collections + net confirmed payouts`.
  Positive means the student owes the center; negative means the center
  owes the student; zero means balanced. Do not reject a valid credit balance.
- Invoices and receipts are operationally editable/deletable, including
  after collection. Preserve old/new values, actor, time and document
  identity/revisions. No hard deletion of financial history.
- A receipt and its underlying money movement must not be counted twice.
  Correction/deletion of a recorded movement is distinct from an actual
  cash/bank payout. A refund records money really returned to the student.
- Present the corrected current account separately from chronological
  financial movements and correction history. A correction today must not
  silently erase the evidence of an older movement.
- Numbers, public IDs, audit records, idempotency and authorization remain
  protected. Future real online payments require their own approved adapter;
  no card processing or card credentials are added by this repair.

## 4. Enrollment lifecycle

### New enrollment

- One operation validates Student eligibility, Group eligibility, capacity,
  teacher availability, configured group study start and authoritative price.
- Create Enrollment, invoice, discount history and optional confirmed
  initial collection/receipt in one database transaction. Failure rolls
  back every part. Notifications are secondary to that transaction.
- With no received amount, the invoice remains due for its net amount;
  a zero net charge is balanced rather than artificially outstanding.
- New enrollment and re-enrollment into a Group remain allowed at or after
  its study start, with a clear informational notice. Use the authoritative
  Group study-start timestamp to describe late entry, not to refuse it.
  Do not backdate membership or automatically grant earlier learning records.
- Repeated requests and competing requests for the last seat cannot create
  duplicate enrollment, charges or collections.

### Withdrawal and payout

- End the active membership, release its seat and preserve the full record.
- Offer an optional reversal of the course's net obligation after discount.
  If it is not chosen, the obligation remains; preserve that choice in history.
- Reversing the obligation does not reverse a real collection or claim that
  cash was returned. Credit becomes a negative general-account balance.
- A separate actual refund/payout settles money owed to the student.
  Replays must not repeat withdrawal, reversal or payout.

### Transfer and re-enrollment

- Transfer is to another Group of the same Course, subject to capacity and
  other authoritative target eligibility checks. It is allowed even when
  the destination has started studying. New admission and re-enrollment now
  also allow a started destination, with their own notice and new episode.
- Preserve source membership and transfer events, release/reserve the
  respective seats atomically, and create no new invoice or obligation.
- Current Course Progress uses the destination Group's learning content.
  Keep source progress/history separately; do not copy completion by title
  or fabricate equivalence. No automatic progress carryover is authorized.
- Re-enrollment creates a new Enrollment episode, not reactivation of the
  previous episode. Previous attempts, submissions and progress remain
  attached to their original episode.

### Wrong-course correction

- Correcting a mistakenly entered course must correct the actual Course and
  Group relationship as well as the financial effect. It is not a free-form
  invoice-description change and is not a same-course transfer.
- Show affected academic/financial facts and recheck target eligibility,
  capacity and configured study start. Show the late-entry notice when the
  destination has started. Keep old/new membership and financial
  snapshots. Preserve source learning records under their original context;
  do not relabel prior answers/grades as work for the replacement course.

## 5. Accounts, teaching and scheduling

- Suspend/reactivate accounts without deleting their history. A reactivated
  Student can read their old and current authorized records.
- Do not suspend a Student while any active Enrollment remains. After a
  withdrawal do not disable their account if another active Enrollment exists.
- Protect account edits/status/password changes against stale forms and
  competing writes. `auth_version` invalidation is not a substitute for
  domain change history or optimistic concurrency.
- Apply the last-eligible-Teacher guard to account suspension as well as
  Group assignment removal; require a valid replacement when needed.
- Room management is in scope now. Validate overlapping effective slots
  for Group, Teacher and Room, with authoritative server rechecks under
  appropriate locks, including concurrent schedule/assignment changes.
  Adjacent half-open time slots are not overlaps.

## 6. Academic records, progress and deadlines

- Learning records and their uniqueness/authorization must distinguish
  Enrollment episodes. Review submissions, speaking submissions, quiz
  attempts, grade/attendance rosters and progress together.
- Course Progress is enrollment-owned content progress, not mastery or a
  claim of language competence. New enrollment starts independent progress;
  a same-course transfer reports the destination content with source history.
- Read access to the student's own released results, submission evidence,
  attendance and feedback must survive withdrawal/archive and be available
  after account reactivation. Historical reading grants no new write access,
  access to other students or disclosure of quiz answer keys.
- Correct grades, finalized attendance and assignment/speaking feedback via
  current values plus preserved revisions: old/new, actor, server time and
  optional reason. A version counter alone is not history.
- Use trusted server request-arrival time for timed quiz/listening submission
  deadlines. Lock/processing delay must not penalize a request received on
  time. Never trust a client timestamp; still recheck ownership, activity
  identity, stale state and terminal-attempt rules under the required locks.
- Keep GET side effects explicit. Health/root/docs verification must not
  imply that visiting a learning/material/result page is a read-only action.

## 7. Unified Student Activities

- One Student navigation entry and hub: `Activities`.
- Include Assignments, Quizzes, Listening and Speaking, with All/type,
  action-state and Course filters. Show type, Course, deadline, state and
  the applicable action (Submit, Continue, Record, View result).
- Prioritize actionable work and nearest deadlines; keep completed/past
  records available and distinguish Enrollment episodes.
- Dashboard `Upcoming activities` covers all four types.
- Each activity retains its appropriate execution page and domain rules.
  Lessons/materials remain within Course content; grades and attendance
  remain separate records.
- Queries must enforce ownership/publication and be bounded. Instrument
  new routes intentionally without silently changing research measurements.

## 8. Research safety and the final retention decision

### Latest owner clarification on 2026-10-05

The owners explicitly resolved the cross-chat conflict:

- Keep password-only Researcher login; no MFA implementation or MFA
  readiness blocker in this repair.
- Keep 15-day retention and daily expiry. This supersedes this conversation's
  earlier pressure-only automatic purge proposal. Do not add pressure-based
  automatic raw-data deletion or high-water/target gates as a requirement.
- Preserve the shared login and separate authorized Researcher workspace.

### Collection and measurement contract

- Version A natural use only. Preserve population rule, exclusions,
  pseudonymous identity, raw optional self-report 1-5 and cause choices.
  Unrated/Skip data has no inferred label. No binary transformation, ML,
  prediction or adaptive intervention in this repair.
- Fix F15: browser pending batches must be bound to their original account
  and configuration context using opaque server-verified delivery scope.
  Never attribute a delayed batch to the current account/configuration just
  because the cookie changed. Preserve event IDs, replay/idempotency and
  minimization; do not place account identity in research exports.
- Scope binding does not authorize redefining session start/end, inactivity,
  tab behavior, sampling chance/budgets, lookback, minimum observation,
  display-grant semantics, provenance/population or labels. Changes to
  those meanings require a separate research decision.
- Preserve final stored answers, frozen uncertain retries, accurate
  acknowledgements, question interactions outside the feature window and
  best-effort failure isolation from the ordinary LMS workflow.
- Fix concurrent configuration-number error handling without changing
  methodology. Dashboard/export population differences and best-effort
  outcome delivery are documented limits, not automatic redesign authority.
- Only Researchers access research management/raw data through the app;
  Administrator/Teacher accounts do not. Record the real authenticated
  Researcher/operator or trusted service identity for actions. This is
  application isolation, not isolation from a server/database administrator.

### Exports, storage visibility and cleanup

- Retention remains an environment setting, with the approved deployment
  value 15 days; no hard-coded credentials or retention constant in source.
- Preserve session expiry by last activity, dependent event/prompt removal,
  archive expiry with its oldest included data, and daily operation. Do not
  change retrospective configuration-policy semantics during this repair.
- Date-range export/manual purge selects sessions whose start falls in the
  requested local-date interval, with the full dependent data for each
  selected session. Do not clip events/windows at the range boundary.
- Display storage usage and last measurement in the Researcher workspace,
  and explicitly describe missing/purged periods. Usage display must not be
  mistaken for proof that deleting rows released physical disk space.
- Add auditable manual range/all raw-data cleanup with a concrete preview.
  Preserve subjects/links, exclusions, configurations, export descriptions
  and audit/gap records. Never delete ordinary LMS records through cleanup.
- Archive bytes may be cleaned under the approved cleanup/retention rules;
  keep metadata/digest/availability state. Each ID serves its original
  bytes while present, or reports unavailability; it is never rebuilt.
- Preserve single-snapshot export, truthful cutoff/timezone/digest, privacy
  and CSV-injection defenses. Larger exports need a bounded-resource design
  that still reads one consistent snapshot.
- Installing/changing/running a scheduled task or starting a real study
  collection period is a separate explicitly scoped operational action.

## 9. Target schema and implementation boundaries

Design incrementally around real workflows, not speculative enterprise tables:

| Area | Required direction |
|---|---|
| Catalogue | Course price/currency/version; Rooms and scheduling relations |
| Enrollment | Episode identity; historical Group memberships and events; active-membership integrity |
| Learning | Episode-aware progress, attempts, submissions and roster relationships |
| Revisions | Grade, attendance, feedback and account/domain change history |
| Finance | Direct student/enrollment invoice context, price/discount snapshots, general collections, actual payouts, receipt/document revisions and chronological financial effects |
| Research | Delivery-scope validation, operator attribution, usage/cleanup/gaps while retaining existing collection and expiry contract |

Keep public identifiers, indexed foreign keys, local CHECKs/unique constraints,
Decimal amounts, consistent UTC server timestamps and protected history.
Composite invariants belong in the schema where practical; dynamic role and
active eligibility still need authoritative application operations.

Routes handle HTTP/forms/rendering; operation services own locks, rechecks,
transactions and audit; query services enforce scope and bounded reads;
pure calculations remain independently testable. Extract existing behavior
gradually rather than adding one large generic service.

## 10. Testing, removal and recovery

- KEEP valid security, privacy, idempotency, answer-key and immutable-export
  contracts. REWRITE changed enrollment/finance/history and SQLite contracts.
  MERGE repeated setup/invariants without dropping meaningful scenarios.
  DELETE obsolete runtime contracts only after replacements pass.
- Use MySQL connections for actual locking, capacity, numbering, migration,
  Decimal/collation and export-snapshot checks. Existing successful tests
  are evidence of their checked build, not authority over the new domain.
- Replace consumers before removing fee-plan/assignment runtime, old
  enrollment reactivation, invoice-scoped account assumptions, lost-history
  corrections and SQLite support. Retain historical migration upgrade
  coverage while the historical chain remains supported.
- Preserve shared-login and Phase 6 retention work. Do not erase historical
  decisions/test results or treat a changed document as a runtime repair.
- Maintain a recoverable source baseline and verify database/file backups
  before authorized live operations. A source ZIP is not a DB/private-file
  backup. MySQL DDL/data loss recovery needs a tested restore plan.

The phase sequence and completion evidence are in `IMPLEMENTATION_PLAN.md`
and `PROJECT_STATUS.md`. No runtime phase is completed by Part P0 documentation.
