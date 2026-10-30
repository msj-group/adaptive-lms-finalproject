# Research v2 migration proposal and review history

## Applied and verified locally - 2026-10-29

The exact promoted revision a6f2c9d84e71 is installed on local MySQL. Operator
and independent read-only verification pass: 11 empty new tables, preserved
original data, enforced constraints and no model/schema drift. All temporary
privileges removed; original grants restored. Current evidence and limits:
RESEARCH_V2_LOCAL_MIGRATION.md. V2 collection and hosting remain inactive.
The proposal/blocked-application descriptions below are historical.

## Current placement and execution status - 2026-10-29

The owner subsequently approved exact local application. The file was promoted
to migrations/versions/a6f2c9d84e71_research_v2_collection.py. The runtime
account refused DDL before any schema/data change; DB remains 8e5f3b0d7124.
Native backup and full old-table/row preservation checks passed. The separate
local migrator is Listening-only; no grant changes or retry were performed.
An equivalent preflight singleton-CHECK reflection correction changes the file
SHA256 to 17134427fa9bb31193210497db14a60bbaa9ba0d39426c3d446371f25fa97ba6,
with all upgrade DDL unchanged. See RESEARCH_V2_LOCAL_MIGRATION.md for evidence,
the permission proposal and remaining acceptance gates. The inactive-placement,
earlier hashes/counts and no-application narratives below are historical.

## Current integration revision - 2026-10-28

The owner subsequently requested local implementation and continuation.
Models/client/collection/export/retention/monitor tooling are now prepared;
see RESEARCH_V2_IMPLEMENTATION.md. The migration remains outside versions/
and no database application or cloud activation has occurred. Current file
adds the immutable session source digest, frozen study timezone and exact
prompt/event membership. It prepares 11 tables, 16 FKs, 70 new-table CHECKs
and 3 grouped ALTERs (73 named CHECK definitions including existing-table
changes). Current proposal SHA256:
911806f5be7b7c8bcf17d88879537ce3f63352756ab3ffbc0092733c6bdd561e.

The file-only preparation narrative and older verification counts/hash below
are historical evidence of the earlier proposal, not the current source.

Date: 2026-10-28. Authority: the owner explicitly approved creating the
database update file locally and asked for an explanation. This does not
authorize executing it on any database or activating paid services.

## File and inactive placement

Proposed revision: a6f2c9d84e71, after source parent 8e5f3b0d7124.
Proposal path:
[a6f2c9d84e71_research_v2_collection.py](../migrations/versions/a6f2c9d84e71_research_v2_collection.py).

Keep the proposal outside migrations/versions/. The current active source
head remains 8e5f3b0d7124 until promotion is separately reviewed. A file in this proposal directory
does not become an active Alembic revision by being present in the tree.

This is a reviewable DDL artifact. Source integration now exists, while actual
MySQL constraints, manual phone round trips and pilot acceptance remain open.

## What the proposal prepares

| Area | New table(s) | Purpose |
| --- | --- | --- |
| Versioned settings | research_v2_protocols | Keep the new timing/measurement policy distinct from old settings |
| Sampling | research_v2_sampling_state, research_v2_sampling_opportunities | Record time-based opportunities and their decisions once, including reservations |
| Observations | research_v2_events | Store small allowlisted v2 observations and confirmed outcomes, without Student content |
| Optional reference | research_v2_prompts | Distinguish offer/grant/render/answer/Skip, preserve raw 1-5 and original response timing |
| Decision snapshot | research_v2_feature_memberships | Freeze exact committed event membership at grant without copying payloads |
| Delivery | research_v2_delivery_receipts | Count timestamped receipt outcomes and distinguish retry/duplicate/late observations |
| Evaluation separation | research_v2_evaluation_partitions | Keep development and reserved final-test participants separate |
| Quality monitoring | research_quality_runs, research_quality_reports, research_quality_incidents | Store aggregate run/report/notification/recovery evidence; no Student identity or free text |

The only proposed changes to existing tables are:

1. Extend research_configurations' supported-schema CHECK to admit
   natural-use-events.v2 alongside natural-use-events.v1.
2. Extend research_exports' format CHECK to admit natural-use-v2-export.v1
   alongside natural-use-export.v1.
3. Add nullable expiry_at_ms to research_export_archives for future exact
   archive expiry, with a positive-or-NULL CHECK and an expiry index. No
   default, backfill or change to existing archive bytes.

These statements do not create an active collection configuration or change
any existing policy. In particular they do not set hosted retention to 45.
No LMS business table, account, existing event/prompt or export descriptor
is rewritten. No sample rows or fabricated study labels are inserted.

## Integrity choices

- Retain signed BIGINT parent keys and normalized subject/session/configuration
  relationships. Do not duplicate Student/account identities or add cascades.
- Use MySQL/InnoDB and exact code collation; unique references distinguish
  events, transport attempts, sampling decisions and display reservations.
- A selected sampling opportunity can create only one prompt. Repeated grant
  returns the same reservation; future services must enforce participant-wide
  spacing and daily budgets under the existing subject/control locks.
- Store grants separately from browser-reported rendering. Uncertain delivery,
  Skip, expired and unlabelled states do not turn into rating 1 or zero.
- Keep any late raw answer distinguishable and outside the primary timely-label
  analysis. A response deadline is fixed for that prompt; changing the active
  configuration later cannot make the answer timely retroactively.
- Record the question's actual display locale and whether optional help
  preference was asked. Not asked, unanswered and an explicit No are distinct;
  this metadata is excluded from frustration predictors.
- Keep observation occurrence/receipt times and clock uncertainty distinct.
  Typed fields and closed report schemas avoid arbitrary content storage.
- Keep future primary training labels separate from raw observations and
  final-test allocation. SQL existence constraints do not prove active Student
  eligibility, scientific validity or correct offline/online feature cutoffs.
- Perform all future upgrade preflight checks before DDL. MySQL DDL can commit
  in stages; the file is not a transactional rollback guarantee.
- Future downgrade refuses if it would discard new records, v2 settings/
  exports or a recorded new archive expiry. It is not a data-reset shortcut.

## Verification boundary

Local file preparation and bounded static review are complete. Final executed
checks used the existing .venv Python 3.14.6, SQLAlchemy 2.0.51 and Alembic
1.19.0; no installation was needed:

- Python AST parsing passed. The standalone proposal imports no application
  module. In-memory MySQL DDL generation completed without a DB connection.
- Generated upgrade: 10 CREATE TABLE statements, 14 FKs, 68 new-table CHECKs,
  and 3 grouped ALTERs. Those ALTERs extend 2 existing CHECKs and add the
  nullable archive-expiry column/CHECK/index. Total named CHECK definitions
  in the generated upgrade: 71. All 10 new tables request InnoDB/DYNAMIC.
- Generated upgrade has zero INSERT/UPDATE/DELETE/REPLACE/TRUNCATE statements
  and zero DROP TABLE statements. A preview-only warning is included.
- No cascading FK actions, duplicate new CHECK names or identifiers longer
  than 64 characters were found. Largest reviewed composite key has 14
  columns and an estimated declared width of 1480 bytes; this is a static
  estimate, not an executed MySQL index/plan measurement.
- Pure source AST graph remains 48 active revisions with only head
  8e5f3b0d7124. All 48 active migration files have the same combined digest
  as before preparation; 16 protected application/runtime/dependency files
  also retain their before-preparation hashes.
- Independent source review found and resolved grouping, metric coverage,
  reservation/deadline and archive-expiry issues. It does not establish actual
  MySQL acceptance, concurrency correctness or application regression safety.

Final proposal SHA-256:
8a6a79eff15eb4de36105cc9a90e8219df6a0e943b51e35a4876662b80e58aa8.
Generated DDL was inspected in memory; no standalone executable SQL script
was published or sent to a server. Offline rendering is SQL generation only,
not a migration execution or database acceptance result.

No automated test package or pytest is created/run. No dependency install,
DB connection/query/application, data reset/backfill, browser operation,
scheduler, paid activation, Telegram delivery, staging/commit/push or
deployment is authorized by this file-only task.

Documentation links and trailing-space inspection pass. The working tree's
normal git diff --check exits 2 on Windows CR-at-EOL diagnostics; with
core.whitespace=cr-at-eol it exits 0. Existing unrelated Finance work is
preserved; the proposal is untracked, not staged or added to active versions.

## Required before promotion or execution

The proposal must remain inactive until the corresponding version-aware
models, ingestion/outcome/sampling, export, retention and report readers are
implemented and reviewed. Existing code only understands v1 and does not
purge v2 child tables. Promoting or applying this file alone would leave
the application/schema contract incomplete.

Temporal acceptance must also establish committed observation availability
at each feature cutoff. A receipt timestamp alone is not proof that an
uncommitted row was visible to a live feature query. The future runtime must
retain sufficient decision/snapshot evidence to verify offline/online parity;
this schema proposal alone does not establish that property.

An explicitly authorized later operation must verify the actual target
database parent, preservation/backup/recovery evidence, exact source/schema
alignment and manual MySQL constraint/migration acceptance. Do not change
Railway's pinned migration/start commands or copy this proposal into active
versions as part of the current file-preparation request.

Physical-phone acceptance, the real pilot, early model feasibility and
independent cloud/Telegram failure/recovery rehearsals remain separate gates.
The 45-day policy requires explicit enforcement and deployment verification;
creating a column does not perform retention or restore previously purged data.

Collection scope: [RESEARCH_V2_DATA_CONTRACT.md](RESEARCH_V2_DATA_CONTRACT.md).
Cloud operation/cost: [RESEARCH_QUALITY_MONITOR_PLAN.md](RESEARCH_QUALITY_MONITOR_PLAN.md).

Implementation references used in review: [MySQL CHECK constraints](https://dev.mysql.com/doc/refman/8.0/en/create-table-check-constraints.html).
The documented deterministic-expression and NULL rules inform the static
review; actual server enforcement remains an unperformed acceptance gate.
