# Adaptive English LMS

An internal learning management system for an English language learning
center. The application is a Flask modular monolith with server-rendered
pages, SQLAlchemy, Alembic/Flask-Migrate and MySQL through PyMySQL.
Administrator, Teacher, Student and Researcher accounts share the same
email/password login. Their stored account roles determine their workspaces
and server-side permissions.

The approved repair preserves this architecture and improves it in stages;
it is not a rewrite. Interface text, code and repository documentation are
English. Reports to the project owners are Arabic.

## Start here

- [Quick Start](docs/QUICK_START.md): running an already configured workspace
  and preparing a new one deliberately.
- [Approved Repair Contract](docs/APPROVED_REPAIR_CONTRACT.md): the accepted
  target behavior and decisions that supersede older contracts.
- [Implementation Plan](docs/IMPLEMENTATION_PLAN.md): repair phases, scope
  and exit criteria.
- [Project Status](docs/PROJECT_STATUS.md): what is implemented, planned and
  verified in the current repair.
- [Project Decisions](docs/DECISIONS.md): historical decisions and their
  superseding entries.
- [Operational Rules](AGENTS.md): repository working rules.

## Current entry points

For an already configured local workspace, the development server in
`run.py` listens on `http://127.0.0.1:5000`.

| Entry | Current behavior |
|---|---|
| `/` | Redirect to shared login, or the loaded account's role dashboard. |
| `/auth/login` | Shared email/password form for all four account roles. |
| `/admin/dashboard` | Administrator workspace. |
| `/teacher/dashboard` | Teacher workspace. |
| `/student/dashboard` | Student workspace. |
| `/research/dashboard` | Separate workspace restricted to active Researchers. |
| `/research/login` | Compatibility entry using the shared authentication flow. |
| `/health` | Basic application health response; it does not prove database readiness. |
| `/admin/rooms` | Administrator Room management and change history. |
| `/admin/student-accounts` | General Student accounts, money movements and history. |
| `/student/activities` | One hub for Assignments, Quizzes, Listening and Speaking. |
| `/student/records` | Own released academic records and prior enrollment progress. |
| `/research/storage` | Researcher storage measurement, cleanup previews and gap history. |

The common login has no Researcher role selector or research link. Ordinary
portals have no research management surface. The Researcher workspace remains
separate and protected even though authentication is shared. Approved
Researcher authentication is password-only; MFA is not required.

The canonical root `/` is implemented in P1A. Existing safe login return-path
rules and role checks remain in force; a root query string cannot select a
different workspace. Unknown environment names now refuse startup rather
than falling back to development.

## Application structure

| Location | Purpose |
|---|---|
| `app/__init__.py` | Application factory and extension/blueprint registration. |
| `app/blueprints/` | HTTP routes grouped by role or feature. |
| `app/models/` | SQLAlchemy domain models and database constraints. |
| `app/services/` | Domain operations, queries, calculations and integrations. |
| `app/templates/`, `app/static/` | Server-rendered UI and browser assets. |
| `migrations/` | Alembic schema history. |
| `scripts/` | Explicit provisioning and operational helpers. |
| `docs/` | Contracts, repair plan, status and operational records. |

The repair covers enrollment lifecycle and history, Course-based pricing,
the general Student Account, academic corrections, enrollment-owned progress,
teacher/room scheduling, the unified Student Activities page and research
integrity. These are approved targets; their presence in a document does not
mean their implementation is complete. See the contract and status documents.

## Current development data and verification

MySQL/InnoDB is the application database. The current local development schema
is `085b7a4e9012`. After the verified in-place upgrade, the owner separately
requested a complete data reset and fictional data for exactly four active
accounts: `admin@example.com`, `teacher@example.com`, `student@example.com` and
`researcher@example.com`. Use the password supplied privately by the owner;
passwords are not stored in this guide. All three demo Courses have prices.

The owner also requested deletion of the old tests and explicitly declined a
new automated test package. There is no current `tests/` directory or isolated
test runner. This close-out uses bounded manual browser and static review;
historical focused results do not prove comprehensive regression acceptance.
Any future automated database checks require separate authorization and owned
isolated MySQL resources. Never run them against development or production.

[Repair Integration](docs/REPAIR_INTEGRATION.md) records the migration, private
recovery archive, demo reset and actual close-out evidence. The completed reset
is not permission to repeat it or perform a production migration.

## Research contract and operations

Research remains Version A natural-use collection, with an optional raw
1–5 self-report. No model training, prediction, binary label or adaptation is
implemented by this repair scope. Sampling, observation windows, display
meaning, provenance and session semantics must not change without a separate
research decision.

The current approved storage policy is **15-day retention with a daily purge**.
There is no pressure-only replacement in the approved repair contract.
The runtime expects an explicit `RESEARCH_RETENTION_DAYS=15` setting; it does
not obtain that value from this README. The recorded local daily job uses
`scripts/run_research_retention.py`. It expires research sessions by last
activity and removes their events, prompts and expired archive bytes while
preserving subject links, exclusions, configurations, export descriptions
and audit history. Ordinary LMS records are outside that purge.

[Phase 6 Completion](docs/PHASE6_COMPLETION.md) records the earlier deployment,
MySQL/browser checks and local scheduled task. Those are historical results,
not checks repeated during P0. Scheduler registration, database provisioning
and starting collection are separate deliberate operations.

- [Natural-Use Research Contract](docs/PHASE6_NATURAL_USE_RESEARCH.md)
- [Research Deployment Runbook](docs/RESEARCH_DEPLOYMENT_RUNBOOK.md)
- [Phase 6 Completion Record](docs/PHASE6_COMPLETION.md)

## Configuration and limitations

Keep real configuration in the ignored `.env` or the deployment's secret
environment. `.env.example` is a template, not an approved ready-to-run
configuration. Never commit credentials, database backups, uploaded files,
research archives or operational logs. Do not overwrite an existing `.env`
while following setup instructions.

The local server is for development, not a production hosting setup. The
current payment-provider integration supports disabled or mock/sandbox mode;
it does not process real online payments. The health endpoint does not verify
schema compatibility, scheduler execution or collection readiness.

The current demo Courses are priced; newly imported unpriced Courses still
refuse enrollment until an Administrator supplies a price. Research collection
is initially paused. The Student is classified as demo and development-provenance
data does not enter study exports. Use the Researcher workspace's Demo filter
to review demonstration sessions. Starting a real study is a separate operation.
Restart the application after deploying changed source/schema.
