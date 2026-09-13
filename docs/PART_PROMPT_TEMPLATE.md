# {{MILESTONE_ID}} — {{TITLE}}

Mode: {{READ_ONLY_AUDIT | IMPLEMENTATION | CORRECTION | VERIFICATION | MIGRATION_EXECUTION | COMMIT}}

Follow the project `AGENTS.md`, the relevant parts of `docs/DECISIONS.md`, and this Part.
This Part is authoritative only for the explicit task and exceptions below.

## Current checkpoint

- Repository: `C:\Users\abdul\Desktop\GraduationProject\AdaptiveEnglishLMS`
- Branch / expected HEAD: `{{BRANCH}}` / `{{HASH_AND_SUBJECT}}`
- Expected parent, if important: `{{HASH_AND_SUBJECT | N/A}}`
- Expected migration state: `{{STATE | N/A}}`
- Expected tracked/staged state: `{{STATE}}`
- Last observed test baseline, if relevant: `{{COUNT | N/A}}`

Verify the checkpoint using read-only commands. If it differs unexpectedly, stop and report.
Do not reset, stash, discard, or overwrite anything.

## Authorization matrix

| Action | Authorized | Exact boundary |
|---|---:|---|
| Read repository | Yes | Current project |
| Edit source/tests | {{Yes/No}} | {{allowlist or scope}} |
| Edit documentation | {{Yes/No}} | {{allowlist}} |
| Create migration | {{Yes/No}} | {{exact schema/revision scope}} |
| Apply migration | Yes by default for schema Parts | Development MySQL only; first verify the exact stated parent revision, then apply only this Part's revision. Set No only with an explicit reason. |
| Read real MySQL | Yes by default for schema Parts | Verify pre-upgrade revision and inspect only this Part's resulting schema and relevant row preservation. |
| Modify real MySQL | Yes by default for schema Parts | Alembic upgrade of this Part's exact revision only; never manually mutate business data or repair an unexpected revision. |
| Stage/commit | Yes by default | Stage only the explicit reviewed allowlist and create one local English commit after every required check passes. Set No only with an explicit reason. |
| Push/history rewrite | No | — |

## Objective

{{One concrete outcome or audit decision.}}

## Required behavior / audit questions

{{Only task-specific requirements. Do not repeat global language, Git, security, or reporting rules.}}

## Relevant invariants

- {{Only the existing decisions directly affected by this Part.}}
- {{Exact route-specific transaction/lock/stale-form rules when relevant.}}
- {{Reference files/tests instead of copying unrelated history.}}

## Scope

Expected files:

- `{{path}}`

Out of scope:

- {{Only plausible neighboring work, not the complete future LMS roadmap.}}

## Acceptance and tests

- {{New task-specific positive/negative/tampering/race cases.}}
- {{Relevant regression group.}}
- {{MySQL/migration/browser evidence required, or an explicit limitation.}}

Use the standard verification profile from `AGENTS.md`, plus:

- {{Part-specific extra checks | None}}

## Standard MySQL and Git close-out

For every schema Part, unless this Part explicitly opts out:

1. Run the required focused tests, regressions, and strict full suite successfully.
2. Verify development MySQL is exactly at the stated parent Alembic revision. If it is not,
   stop; do not upgrade, downgrade, repair, or manually alter it.
3. Apply only this Part's Alembic revision.
4. Verify both `db current` and `db heads` equal the new revision.
5. Inspect the affected MySQL tables, columns, constraints, indexes, foreign keys, engine,
   collation where relevant, and preservation of relevant existing rows.
6. Do not run downgrade on development MySQL unless separately authorized.
7. Run `git diff --check`, inspect `git status`, and stage only the explicit allowlist.
8. Exclude `.env`, secrets, databases, logs, caches, scratch files, QA artifacts, and all
   unrelated changes from staging.
9. Create exactly one local English commit, then verify a clean worktree.
10. Never push, amend, rebase, reset, or rewrite history unless separately authorized.

## Deliverable

Use the standard concise Arabic report. Include exact test results, MySQL migration and schema-inspection
evidence, the local commit hash and subject, final Git state, and limitations. Stop after this milestone
and do not begin the next Part.
