# Adaptive English LMS — Operational Rules

These are stable project rules. The current approved Part supplies the dynamic objective,
checkpoint, scope, and any explicit exceptions.

## 1. Authority and truth

- The current user request and approved Part define authorization and intended changes.
- The repository, Git state, migrations, and checks actually executed define current state.
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
- Real MySQL schema/data changes and migration application are default-deny.
- If an additional file outside the expected scope is technically necessary, explain why before editing it.

## 4. Language and project facts

- Reports and explanations to the owners are Arabic.
- Code, filenames, routes, UI text, tests, repository documentation, and commit messages are English.
- The application is a Flask modular monolith using SQLAlchemy/Alembic and MySQL/PyMySQL.
- Automated tests use SQLite in memory. They can validate application logic and requested lock/query
  structure, but they do not prove MySQL/InnoDB blocking, isolation, collation, or migration behavior.
- Credentials and secrets come only from environment configuration and must never be exposed.

## 5. Scope, architecture, and security

- Complete only the named Part; do not begin later modules or unrelated refactors.
- Read the relevant code, tests, migrations, and `docs/DECISIONS.md` before changing behavior.
- Existing regression tests are contracts unless the current Part explicitly changes their documented behavior.
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

- Run technically available focused tests and relevant regressions after code changes.
- Run the full suite and strict-warning checks before final approval of cross-cutting/high-risk code or when
  the Part requires them. Documentation-only work does not automatically require the full suite.
- Use migration checks separately from pytest when schema/model work is involved; SQLite `create_all()` is
  not proof that Alembic migrations work on MySQL.
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
