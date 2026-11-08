# Single natural-use collection workspace — 2026-11-08

## Owner authority

The owner requests one method with descriptive source filenames and no release
choices/labels. The owner confirms that collection starts now and no previous
research data needs a historical workflow. This supersedes the earlier historical
review proposal. Source cleanup/renaming is authorized; no database reset, DDL,
collection activation, production deployment, scheduler or Git operation is run.

## One runtime and one source of evidence

New drafts use the reviewed policy only, with no method selector. Saved settings
have numbers for configuration accountability, not alternate software versions.
Activation freezes settings; Start/Pause remains separate. Authentication, CSRF,
rate/body limits, signed delivery, server eligibility and database locks remain.

The only collector routes are /collect/events, /collect/sample, /collect/clock
and /collect/prompts/{reference}/{action}. Overview, Sessions, quality and exports
now use the same observation/question schema. The old browser, sampling and
batch-parser/dashboard/session query paths are removed; no historical UI is introduced.
Storage counts, manual-cleanup previews and retention count/remove the same
observation/question rows and explicit export membership. They do not merge an
older collection source or infer expiry/membership for older archives. These are
source changes to authorized maintenance actions; no cleanup was executed.
Overview selects technical counts without rating values. Detailed sessions and
ordinary exports retain development/monitor partition restrictions and protect
unassigned/final-test participants. No LMS identity/content mapping is selected.

Detector/context features and optional preferences remain separate. The unnecessary
historical source-compatibility branch is removed before first collection; temporal
membership, support/coverage missing values and eligibility rules remain.

## Source names

| Responsibility | Current source |
| --- | --- |
| Observation collection | app/services/research_observation_collection.py |
| Policy / vocabulary | app/services/research_policy.py; research_dictionary.py |
| Features | app/services/research_features.py; research_context_features.py |
| Quality / observation cleanup | app/services/research_quality.py; research_observation_storage.py |
| Dataset / saved export service | app/services/research_dataset_exports.py; research_exports.py |
| Models | app/models/research_observations.py |
| Quality / evaluation groups | app/blueprints/research/collection_review.py |
| Browser / question | app/static/js/usage_collector.js; app/templates/_research_feedback.html |
| Question styles | app/static/css/research_feedback.css |

The source descriptor for Alembic revision a6f2c9d84e71 is renamed to
a6f2c9d84e71_research_observation_collection.py; its revision/parent/DDL are unchanged.
Installed MySQL table/constraint and opaque format identifiers remain compatible.
Protocol identifiers are centralized in research_storage_contract.py; they are
storage contracts, not alternate runtime modes or visible labels. No DDL is needed.

## First start and operational boundary

RESEARCH_COLLECTION_ENABLED is the only enable setting, false by default, without
an old-variable alias. Enable deliberately after reviewing the installed schema
and deployment. The current policy still requires 45-day retention at activation.
Production configuration requires 45 days as well. No private environment is
edited. Monitoring uses /research/collection-quality. Operator diagnostics report
the current collection schema/protocol/features and the single enable setting.

Before Study use, review source classification/dates/retention and participant
eligibility, assign groups by the study plan, activate the intended settings and
deliberately start collection. Verify computer AND phone journeys through events,
server outcomes, question render, timely eligible answer, counters and actual ZIP
export. Daily retention and quality/alert jobs need actual deployment verification.
Source checks establish neither delivery/sample adequacy nor model/help effectiveness.

## Verification boundary

Only bounded source/markup/import/route review is used under the repository's
instruction not to recreate/run an automated test suite. No application factory,
real SQL, data write, export, migration, physical-device or hosted acceptance,
scheduler change, staging/commit/push or deployment is claimed. Executed results
are recorded below at close-out.

Executed bounded checks:

- Python AST parsing: 427 sources under app, scripts and migrations.
- Jinja parsing with the actual OwnedUIExtension: 319 templates in the final working tree (including concurrent Teacher UI work, left untouched).
- Arabic catalog: 5,221 unique JSON entries, no duplicate keys.
- Node syntax checks: six changed browser scripts.
- Plain Flask registration, without create_app or database initialization: four
  canonical collector endpoints and /research/collection-quality. ConfigurationForm
  constructs with collection disabled and uses one fixed hidden schema field.
- Standalone feature and quality imports do not import the LMS package; the
  dedicated read-only checker remains independent of application credentials.
- All eleven renamed observation model class definitions are structurally
  equivalent after class/constant-name normalization. The renamed migration's
  executable AST, revision and parent are unchanged. The export format CHECK
  still matches its installed literals; configuration CHECK values are preserved.
- Fictional standalone markup renders for empty Overview, populated Session
  detail and disabled quality views, using a stub parent layout. This does not
  constitute browser or real-route acceptance.
- Active-source reference scans found no retired module/API/route/enable-setting
  references and no release-number filenames under app/scripts/migrations.
  Installed opaque storage/protocol identifiers remain deliberately.
- git diff --check passed after normalizing changed text-file line endings.

No automated tests were created or executed, following the repository's explicit
owner instruction. Phone and computer delivery, real MySQL/export acceptance,
deployment settings and job execution still require operational verification.
