# Latest platform updates release - 2026-10-31

## Authority and source boundary

The owner explicitly requested pushing and deploying the latest changes on
2026-10-31. This authorizes one allowlisted source commit, a normal GitHub push
and deployment to the existing Railway production web service. It supersedes
prior local-only publication limits for these completed changes. It does not
authorize a data reset, study activation, new migration, scheduler, paid resource,
secret change or automated application test.

Branch: `codex/latest-updates-20261031`.
Parent: `b33535e568ae5a2a3b9720aeedbbb8bfec1bb252`, the verified live source pin.
The release is prepared in an isolated worktree. The original mixed checkout,
its branch/index, local database and design/QA artifacts are preserved.

The release includes the accumulated Finance account/report presentation,
shared workspace navigation and Theme, search/Login fields, direct post-login
redirect and approved light Researcher interface, together with their English
implementation records. Existing Research v2 runtime and hosted migration are
retained. The immutable Research-only baseline describes its earlier source;
it is preserved as history and is not approval of this newer runtime inventory.

## Bounded verification

- Python AST parsing: 386 sources, passed.
- Actual localization-aware Jinja syntax and literal template references:
  295 templates, passed.
- `node --check`: 58 JavaScript files, passed.
- Arabic JSON parsed; 292 additions retain every deployed translation.
- Four Researcher SVGs parsed; no script or foreign-object elements.
- Migration tree and the migration runner match the live parent after the
  existing Git line-ending normalization. No new migration is included.
- Staged CRLF-aware `git diff --cached --check` passed. Plain checking
  reports the existing CR-at-EOL convention as trailing whitespace; file
  endings are preserved and no Git configuration is changed.
- Source allowlist excludes `output/`, `.env`, private `instance/`, credentials,
  databases, backups, exports, caches and operator/QA evidence.
- Two extra EOF blank lines in operating documents were removed from this
  isolated candidate for a clean diff. No original worktree file was rewritten.

No automated application tests, pytest collection or local database access
were performed. Previous feature-specific manual presentation evidence is
linked from the implementation records; it is not newly executed end-to-end
business, concurrency, actual-phone or scientific study acceptance.

## Hosted execution boundary

Target: existing AdaptiveEnglishLMS / production / adaptive-lms-web on Railway,
using the existing GitHub repository and public domain. Review and apply only
the source branch and exact commit pin; preserve all variables, volumes, MySQL,
service count and schedules. Existing predeploy verifies the expected schema
`a6f2c9d84e71`; at the already-current head it performs no DDL. Never repair an
unexpected schema, reset/seed, downgrade or silently roll back to pre-v2 code.

Verify provider deployment completion, predeploy/startup logs, readiness, public
login/assets and environment status. Keep exact private receipts under ignored
`instance/latest_release_20261031/`; report the resulting source commit and
public application URL to the owner. New research configuration, phone/pilot
acceptance and unattended paid monitoring remain outside this release.
