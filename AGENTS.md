# Adaptive English LMS — Operational Rules

These are stable project rules. The current approved Part supplies the dynamic objective,
checkpoint, scope, and any explicit exceptions.

## 1. Authority and truth

- Latest owner scope, 2026-10-06: enable an end-to-end local Student usage,
  Researcher review and export rehearsal using the same ordinary workflows
  as hosting. Source corrections are authorized. Operational-review exports
  are now allowed, separately labelled and server-scoped; they never become
  study exports. Preserve local data, classifications, sampling and retention.
  No reset, migration, automated tests, scheduler or Git change is included.
- Latest owner scope, 2026-10-06: remove runtime demonstration options and
  simulated payment workflows. Prepare future hosting with an empty business
  database; preserve the existing local database. Ordinary manual evaluation
  uses normal workflows and fictional input, never real study provenance.
  Source/documentation changes and bounded static review are authorized;
  no deployment, database reset, new automated tests or Git mutation is included.
- The current user request and approved Part define authorization and intended changes.
- The repository, Git state, migrations, and checks actually executed define current state.
- `docs/APPROVED_REPAIR_CONTRACT.md` records the owner-approved rehabilitation target;
  `docs/IMPLEMENTATION_PLAN.md` and `docs/PROJECT_STATUS.md` distinguish planned work from
  implemented/verified work. Read their latest authority before interpreting historical policy.
- `docs/DECISIONS.md` records accepted architecture and domain policy.
- Historical chats, reports, handoffs, and `MASTER_PROMPT.md` are context only, not current authorization.
- If the Part, documentation, code, migrations, or tests disagree materially, stop before mutation
  and report the conflict. Do not silently choose a version.

## 2. Work modes

- `READ_ONLY_AUDIT`: inspect and report only; no file, Git, migration, or database writes.
- `IMPLEMENTATION` / `CORRECTION`: edit only the approved project scope and run tests; no migration
  creation/application, real-MySQL mutation, staging, commit, or push unless separately authorized.
- `VERIFICATION`: run approved read-only checks; do not repair failures unless asked.
- `MIGRATION_EXECUTION`: perform only the exact approved revision and database actions.
- `COMMIT`: do not redesign or add features; stage only the explicit allowlist and create at most the
  approved local commit. Never push or rewrite history unless the current request explicitly says so.

## 3. Default safety

- Work only inside this repository and preserve unrelated or pre-existing work.
- Never reset, stash, discard, overwrite, or destructively clean existing work without explicit approval.
- Do not install dependencies, alter machine settings, configure remotes, or change secrets unless authorized.
- Never stage or commit `.claude/settings.local.json`, `.env`, secrets, databases, logs, caches,
  coverage output, temporary files, or QA artifacts.
- For an approved implementation Part that creates a migration, the standard close-out
  workflow is: pass the required test gates, verify development MySQL is exactly at the
  stated parent revision, apply only that Part's migration, inspect the resulting schema,
  then create one reviewed local commit. A Part may explicitly opt out of this workflow.
  Never repair an unexpected MySQL revision, mutate business data manually, run a
  development-MySQL downgrade, push, or rewrite history without separate authorization.
- If an additional file outside the expected scope is technically necessary, explain why before editing it.

## 4. Language and project facts

- Reports and explanations to the owners are Arabic.
- Code, filenames, routes, UI text, tests, repository documentation, and commit messages are English.
- The application is a Flask modular monolith using SQLAlchemy/Alembic and MySQL/PyMySQL.
- MySQL/InnoDB is the only approved application database. Any separately authorized future
  database-backed automated tests must use an owned isolated MySQL target, never development
  or production. The owner deleted the test package on 2026-10-06 and explicitly declined
  creating a replacement. Historical focused results are evidence of earlier builds only.
- Preserve the shared `/auth/login` and separate role-gated Researcher workspace. The owners
  reconfirmed password-only Researcher authentication and 15-day daily retention on 2026-10-05;
  the repair's earlier MFA and pressure-only proposals are superseded. Preserve research sampling,
  windows, display/provenance/label and session meanings unless separately approved.
- On 2026-10-05 the owners authorized isolated MySQL resources throughout the repair's tests,
  migrations and browser checks: existing mysqld, unique ignored `instance/repair_mysql_tests`
  directories, random loopback ports, owned synthetic schemas and generated temporary credentials.
  This scope does not authorize development DB access, installations or machine/service/task changes.
- On 2026-10-06 the owner separately authorized integration, legacy entry points,
  documentation and the new development database migrations. The exact local
  upgrade from `d574ab56594f` to `085b7a4e9012`, recovery and current evidence are
  recorded in `docs/REPAIR_INTEGRATION.md`. The owner then separately authorized a complete
  development-data reset, deletion of old tests and fictional data for four demo accounts.
  That bounded operation is complete; it is not continuing permission to reset or seed again.
  The latest follow-up authorizes remaining source cleanup, interface review and documentation.
  Scheduler changes, production deployment and Git mutation are outside this follow-up.
- Credentials and secrets come only from environment configuration and must never be exposed.

## 5. Scope, architecture, and security

- Complete only the named Part; do not begin later modules or unrelated refactors.
- Read the relevant code, tests, migrations, and `docs/DECISIONS.md` before changing behavior.
- Existing regression tests preserve valid contracts; owner-approved changes take precedence over
  assertions for superseded behavior. Never treat code as correct merely because its tests pass.
- Preserve normalized relationships and approved history/lifecycle policies; do not introduce hard deletion,
  cascades, or duplicated identity columns without explicit approval.
- Admin object URLs use public identifiers. Numeric database IDs may remain in established internal
  select/filter values; do not expose them as object identity URLs.
- Enforce authorization and object/role scoping server-side. Protect state changes with the correct HTTP
  method, CSRF, authoritative validation, and nested ownership checks where applicable.
- A foreign key to `users` proves existence, not Student/Teacher role or active status; recheck conditional
  integrity in every relevant application read/write path.
- Keep database constraints as the final integrity defense. Catch `IntegrityError`, roll back, and show a
  generic safe message without SQL or driver details.
- UI/form preview checks are not authoritative when state may change. Recheck affected invariants after the
  required locks and before mutation, and avoid partial writes.
- Transaction locking and stale-form protection solve different problems; preserve both when the affected
  workflow requires both.
- When touching Group or membership writes, read the shared transaction/membership services and the exact
  affected tests. Preserve the route-specific reset, lock order, rechecks, and documented limitations rather
  than inventing one universal lock sequence.
- Do not invent business rules for Attendance, Grades, Payments, Research, or other undecided modules.

## 6. Verification truthfulness

- Latest owner instruction on 2026-10-06: do not create a new automated test package.
  Old tests were deleted at the owner's explicit request. Complete remaining source cleanup,
  bounded manual browser review with the existing fictional accounts, static source/template
  review and current documentation. Do not recreate or execute automated tests, invoke pytest
  collection, or start a background test supervisor. This explicitly overrides the default
  focused/full-suite gates below for this repair close-out. Report this verification boundary;
  do not describe manual/static review as comprehensive automated regression acceptance.

- Run technically available focused tests and relevant regressions after code changes.
- Run the full suite and strict-warning checks before final approval of cross-cutting/high-risk code or when
  the Part requires them. Documentation-only work does not automatically require the full suite.
- Use migration checks separately from pytest when schema/model work is involved; `create_all()` or
  historical SQLite results are not proof that Alembic migrations work on MySQL. Unless the approved Part explicitly
  opts out, after all required tests pass, apply the exact new revision to development
  MySQL only after confirming the stated parent revision, then inspect the result and
  record the evidence.
- Use `git diff --check` and inspect final tracked/staged state before reporting completion.
- Distinguish automated tests, real browser checks, real MySQL checks, and checks not performed.
- Never report an unexecuted check as passed. State exact results and limitations.

## 7. Standard Arabic report

Report concisely:

1. Verified checkpoint and authorization.
2. Findings or exact changes and files.
3. Key design/integrity decisions.
4. Tests and checks actually run, with exact results.
5. Migration, real-database, browser, and Git impact.
6. Honest limitations, unresolved risks, and the stop point.
