# Research v2: local migration attempt and preservation evidence

## Current result: local upgrade verified and temporary grants removed - 2026-10-29

The owner entered the administrator password locally and ran the reviewed
operator successfully. Latest receipt:
instance/research_v2_migration_20261028T214934Z/authorized_run_20261028T222434981469Z_0cf3de51/operation.json.
Status: local_migration_verified_temporary_privileges_removed. The agent also
independently repeated the read-only real-MySQL/schema/preservation and current
runtime/migrator grant-baseline checks after the operator finished; all passed.
No administrator password was copied, displayed or stored by the operator.

| Verified result | Evidence |
| --- | --- |
| Exact local target and final revision | Loopback adaptive_english_lms, MySQL 8.4.8, a6f2c9d84e71 |
| Schema installation | 26 approved DDL statements; 86 total tables, including 11 empty new tables |
| Original rows | All 21543 original rows retain typed original-column fingerprints; only the intended version-row value changes |
| Existing archives | Original columns/bytes unchanged; new expiry values are NULL |
| New-table constraints | 16 FKs without cascades, 12 explicit indexes, 70 enforced CHECKs |
| Existing-table changes | Archive CHECK/index verified; both exact v1/v2 version CHECKs enforced |
| Storage/types | Reviewed columns, nullability, PK/unique/FK/index structure, InnoDB/DYNAMIC/binary collations match |
| Alembic model/schema comparison | Zero differences |
| Temporary permissions | All 17 reviewed additions applied and all 17 revokes processed; original migrator grants restored exactly |
| Runtime account | Original grants unchanged; no DDL/global privilege added |
| Collection gate | RESEARCH_V2_ENABLED remains false |

Fresh successful-run backup:
adaptive_english_lms_tables_before_a6f2c9d84e71.sql within the latest run directory.
Size 5716715 bytes; SHA256
b5daad72e7fac283b0b2771f18b8b1ee0177ce8dd3a9132748f4ccb29beaac2d.
All 75 original table definitions and row fingerprints were stable through
backup. The byte hash happens to match the preceding verified table snapshot;
the fresh artifact is separate and did not overwrite it. Backup excludes
stored routines/events and is limited to visible triggers, as documented below;
no restore drill was performed.

Reflected reviewed CHECK-definition SHA256, operator and independent check:
fc47e8844161fb9a41cb15635ea38399792f3b7ea263444ac5cea45a026defe1.
The active migration bytes still have SHA256
17134427fa9bb31193210497db14a60bbaa9ba0d39426c3d446371f25fa97ba6.

## Local source integration after exact execution

MIGRATION_EXECUTION is complete. Resumed the owner's existing local v2 source
implementation authority for the routine readiness integration, after explaining
the two-file change before editing. Changed only the expected revision literal
in app/__init__.py and scripts/deployment_diagnostics.py to a6f2c9d84e71.
Independent diff review confirms no route/auth/security/policy change. This is
the separately recorded local source followup; it does not extend hosting scope.

Python AST checks pass. The real read-only diagnostic CLI exits 0 with
database_ready=true, development provenance, the preserved v1 15-day recorded
policy and collection disabled. No HTTP/browser readiness probe was performed.
No automated application tests or test package were created/executed.

No reset, seed, downgrade, hard deletion, reclassification, persistent secret/
environment change, scheduler/Telegram/paid activation, deployment or Git mutation.
The temporary reviewed grant/revoke sequence is the only permission change;
the original grants were verified restored. Unrelated Finance work is preserved.
Railway predeploy and release-manifest expectations remain outside this local
operation and must be reviewed before a future hosting release.

Phase 6 remains open for fictional Student/Researcher phone workflows, export/
retention acceptance, a small pilot and monitoring acceptance before real study
collection. The 45-day v2 study policy is not activated by this migration.

All blocked, unapproved-permission, not-launched, old-readiness and incomplete-
upgrade statements below are historical checkpoints superseded by this result.

## Observed on-device attempt: another connection blocked execution - 2026-10-29

Read the private receipt from authorized_run_20261028T222019691753Z_1a36d732
without exposing credentials. Administrative authentication succeeded, but the
first all-user connection guard stopped with
other_database_or_writer_connection_exists. The receipt confirms final revision
8e5f3b0d7124, 75 tables, no new tables and temporary_grants_started=false.
No privilege addition or migration DDL started; there were no added rights to
remove. The default cleanup_complete=false in this pre-grant receipt is not a
failed revocation. Independent source review found no own-connection exclusion
bug: administrator, runtime and migrator connection IDs were already allowed.

Close other local LMS/MySQL clients connected to this schema, including idle
connections, while keeping the MySQL service running. Then use the same reviewed
launcher and enter the administrator password locally again. That password is
not stored in the helper or available to the agent. No password reset, session
kill, ACL expansion or service change is authorized/performed here.

The private run directory intentionally restricts Windows access. Read-only
receipt access required a sandbox escalation; no directory ACL was changed.
No new database operation, application test, browser check or Git mutation was
performed during this receipt/source review. This supersedes the earlier
not-yet-launched checkpoint below; successful migration acceptance is pending.

## Current owner decision - 2026-10-29

The owner has now approved the exact temporary privilege proposal below for
the existing local migration account only, its revocation after verification,
and continuing the exact local upgrade. They also confirmed they can enter the
local DBA password through hidden on-device input. The permission decision is
resolved; the administrative credential is not available to the agent.
An ignored interactive operator is prepared and independently reviewed for
that secure local step. Python AST parsing passed; it has not been executed.
No new grant or database action has occurred at this checkpoint. The earlier
unapproved-permission statements below describe the preceding checkpoint.

Source followup discovered during execution review: app/__init__.py readiness
and scripts/deployment_diagnostics.py expect 8e5f3b0d7124. After the approved
upgrade, they will report not-ready until a separate bounded source update.
This is not migration/schema failure or authority to deploy/enable collection.

## Secure on-device launch

Run the existing ignored file
instance/research_v2_migration_20261028T214934Z/RUN_AUTHORIZED_LOCAL.cmd
in a normal local interactive console. First close the local LMS and other
clients of adaptive_english_lms; keep the MySQL server running. Enter the local
administrator username and password at the console prompt. The password input
is hidden and is used only in process memory, never a command argument or file.
Do not send the password in chat. Keep the console open until the result.

The launcher runs execute_authorized_local.py and postverify_v2.py. They pin
the reviewed migration and grant/revoke byte hashes, require loopback identity
and exact parent, verify the separate migration/runtime grant baselines,
take a fresh private snapshot, apply the exact upgrade as the migration user,
check preservation/schema, then remove only the temporary rights. Cleanup also
runs after grant/DDL/verification failure; it processes the full reviewed list
and verifies the original grant baseline. It never retries DDL, resets, seeds,
downgrades, edits .env, creates accounts or activates collection/cloud services.

Each execution gets a uniquely named authorized_run_* directory below the
ignored operation directory with a structured operation.json receipt and fresh
backup. Success status is local_migration_verified_temporary_privileges_removed.
Any failure requires inspection of the recorded actual revision/schema and
cleanup result before another attempt. Do not rerun blindly. Forced process
termination or power loss can prevent finally cleanup; this requires DBA review.
The advisory lease coordinates migrations only; it does not block new ordinary
writers. The per-DDL connection check supplements actual local writer quiescence.

Reviewed files: the operator/launcher, read-only verifier and imported snapshot
helper. Review found and corrected Windows dump newline comparison and two
CHECK comparison-normalization defects before launch. No application automated
tests, database connection/mutation or interactive authentication was performed
while preparing these helpers. Static review is not execution acceptance.

Actual-byte package hashes are recorded privately in operator_package.json.
The source readiness followup above is still separate, even after success.

Checkpoint: 2026-10-29 Africa/Tripoli (2026-10-28 UTC).
**The database upgrade is not complete.** No schema or row change occurred.
Phase 6 and real collection acceptance remain open.

## Authority and exact artifact

The owner approved applying only a6f2c9d84e71 to the configured loopback
adaptive_english_lms database, after backup and exact parent verification
8e5f3b0d7124. No hosting, reset, seed, downgrade, permission/configuration
change, paid service, automated application test or Git mutation was included.

The reviewed proposal was promoted, without further byte changes, to
[the active revision](../migrations/versions/a6f2c9d84e71_research_v2_collection.py).
The active source graph has 49 revisions and one head a6f2c9d84e71. The
database remains at 8e5f3b0d7124. Promotion is not execution acceptance.

Before promotion, one preflight-only correction made the v1 configuration
CHECK expectation match MySQL's singleton equality reflection. The allowed
values, upgrade, downgrade, other helpers and every DDL statement are unchanged.
Independent AST and canonical offline-DDL review confirmed this equivalence.

| Evidence | SHA256 |
| --- | --- |
| Original reviewed proposal | 911806f5be7b7c8bcf17d88879537ce3f63352756ab3ffbc0092733c6bdd561e |
| Promoted file with equivalent preflight correction | 17134427fa9bb31193210497db14a60bbaa9ba0d39426c3d446371f25fa97ba6 |
| Canonical 26 upgrade statements, before and after correction | 7852a3fc7ea3952932caadebc724f8119c177fab08c080b56aae42692d95d51e |

## Checks actually performed

- Local identity and parent: MySQL 8.4.8, loopback adaptive_english_lms,
  exactly one version row 8e5f3b0d7124, InnoDB page size 16384 bytes.
- Corrected online read-only preflight passed: engines, parent keys/FKs,
  precise code-column shapes/collations, CHECKs and absence of v2 objects.
- No local Python/Flask/Gunicorn process was found. The connected runtime
  account saw no other target connections. Without PROCESS, this is visibility
  into that account's connections only; it is not proof about all users.
- Initial broader transaction inspection lacked privilege. A bounded identity/
  revision retry passed; no permission repair or global-lock operation followed.
- A native mysqldump table-definition/data snapshot completed with exit code 0,
  --single-transaction, --quick, --hex-blob and no tablespace/GTID operations.
  All 75 CREATE TABLE definitions and the parent version are present.
- Typed row-multiset SHA256 fingerprints and counts for every original table
  were identical before and after backup.
- Normal Alembic command.upgrade targeted the exact revision inside the local
  development application context, without an HTTP server, seed or collector.
  It raised OperationalError before any schema change. The application runtime
  account has only SELECT/INSERT/UPDATE/DELETE; it cannot create the first table.
- Read-only inspection of the existing separate migration account confirmed
  schema SELECT, ALTER only on listening_activities and version-table DML.
  It also cannot perform this new migration. It was not used for another attempt.
- After the refusal, all 75 SHOW CREATE TABLE results exactly matched the SQL
  backup. All 21543 original rows, including the original version row, retained
  the same typed multiset fingerprints. No v2/quality table or expiry column
  exists. The database schema and data are unchanged.

No runtime collection, browser, retention purge, INSERT constraint probe,
automated application test or post-upgrade model/schema acceptance was run.
The application gate RESEARCH_V2_ENABLED remains false. No configuration,
classification, recorded retention, scheduler, Telegram delivery or cloud
resource was changed. No staging, commit or push occurred.

Close-out static checks passed: migration AST and 49-revision/single-head
graph, actual promoted-file SHA256, verified backup SHA256 and actual-byte
grant/revoke manifest hashes. git diff --check passed with CR-at-EOL awareness
(line-ending conversion warnings only); the staged-file list is empty.
Unrelated pre-existing Finance work was preserved. These are static/migration
checks, not automated application regression acceptance.

## Private backup and receipts

Ignored operation directory:
instance/research_v2_migration_20261028T214934Z/.

Verified backup: adaptive_english_lms_tables_before_a6f2c9d84e71.sql.
Size: 5716715 bytes. SHA256:
b5daad72e7fac283b0b2771f18b8b1ee0177ce8dd3a9132748f4ccb29beaac2d.
Other private receipts: operation.json, before_rows.json and one-off operator
helpers. Do not commit, expose or use an unsuccessful partial dump as a backup.

The initial dump with --events failed for lack of EVENT access. The successful
backup covers all application table definitions and rows plus accessible
triggers; it excludes stored routines and scheduled-event definitions. Trigger
visibility is permission-limited. This is an application-table snapshot,
not a complete server-object backup or an independently restored recovery drill.
The proposed migration does not modify triggers, routines or scheduled events.

## Concrete permission proposal: not approved or executed

Prepared private files proposed_temporary_grants.sql and
proposed_revoke_added_grants.sql, with a nonsecret scope summary in
proposed_permissions.json. They target the existing local migration account,
use 17 specific table scopes in adaptive_english_lms, and do not grant global
rights, GRANT OPTION, DROP or account/password changes. Existing runtime
privileges and the earlier migration account's grants are preserved.

| Object scope | Proposed additional privileges |
| --- | --- |
| The 11 named new tables | CREATE; INDEX only on the 8 tables with standalone indexes; REFERENCES only where another new table references them |
| research_configurations | ALTER, CREATE, INSERT, REFERENCES |
| research_exports | ALTER, CREATE, INSERT |
| research_export_archives | ALTER, CREATE, INSERT, INDEX |
| users, research_subjects, research_sessions | REFERENCES |

The supporting CREATE/INSERT privileges on changed tables follow MySQL's
[ALTER TABLE privilege requirements](https://dev.mysql.com/doc/refman/8.4/en/alter-table.html).
Parent REFERENCES follows the [foreign-key requirements](https://dev.mysql.com/doc/refman/8.4/en/create-table-foreign-keys.html).
MySQL permits table-specific grants for future tables when CREATE is included;
see [GRANT](https://dev.mysql.com/doc/refman/8.4/en/grant.html).
These rights are not permission to insert or reclassify study/business data.

Grant-file SHA256:
19996932acc5466530cfa9bc2a10462a61a65fd2a52be95afcdbecbf205c8efd.
Added-rights revoke-file SHA256:
6ff2ed0704ea9bed4933f3bfc7d10e0bb94b16ab9ab7d504a328b7d15f11d883.
These hashes cover actual file bytes with LF line endings. Independent review
confirmed no missing upgrade rights; unnecessary INDEX grants on the protocol,
sampling-state and evaluation-partition tables were removed before final review.

This is a review artifact only. Applying grants requires separate owner
authorization and an authorized local DBA connection supplied securely. No
suitable administrative credential is currently configured among the local
database/migration environment variables. Do not request passwords in chat,
search unrelated private artifacts for them, grant rights through the runtime
account, or run bootstrap_db.py.

## Resume and acceptance boundary

After explicit permission approval and secure DBA availability: inspect the
live grants, apply only the reviewed additions, recheck exact parent and
unchanged schema, establish quiescent writers across users, take a fresh
private backup, then apply only the named revision with the separate migration
identity. Use the established aelms_schema_migration advisory-lock name.
Do not blindly rerun historical one-off helpers or overwrite backup artifacts.
Any partial DDL is a stop condition; no automatic drop, downgrade or reset.

Verify revision a6f2c9d84e71, 86 tables including 11 empty new tables, original
row/column fingerprints except the intentional version-row change, NULL expiry
on existing archives, 16 new FKs without cascades, reviewed indexes, InnoDB/
DYNAMIC/binary collations and all 70 new-table CHECKs plus the archive CHECK
ENFORCED=YES. Explicitly inspect the two replaced v1/v2 CHECKs. Run the separate
read-only Alembic model/schema check and report unrelated drift without repair.
Then revoke only the added temporary rights and verify the original grants.

Manual phone/desktop workflows, a small pilot, study-policy activation and cloud
monitor/Telegram setup remain separate acceptance work. Schema installation
alone does not close Phase 6 or authorize real collection.
