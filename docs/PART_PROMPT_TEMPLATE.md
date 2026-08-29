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
| Apply migration | {{Yes/No}} | {{exact database/revision}} |
| Read real MySQL | {{Yes/No}} | {{exact purpose}} |
| Modify real MySQL | {{Yes/No}} | {{exact actions/data}} |
| Stage/commit | {{Yes/No}} | {{allowlist/message}} |
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

## Deliverable

Use the standard concise Arabic report. Stop after this milestone and do not begin the next Part.
