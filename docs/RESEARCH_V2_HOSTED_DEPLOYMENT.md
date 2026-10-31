# Research v2 hosted deployment — 2026-10-30

## Verified checkpoint and authority

The owner explicitly requested execution of the hosted deployment after asking
whether it had been deployed. The owner separately approved a temporary SSH
key, with revocation and local private-key removal after the operation.
Their monitoring instruction remains preparation only, without paid activation.

The reviewed Research-only Git publication had already completed. This operation
made no additional Git commit, push, merge or original-worktree cleanup.

## Exact live release

- URL: https://adaptive-lms-web-production.up.railway.app
- Railway project: AdaptiveEnglishLMS / c0fa2362-4e1e-4412-908a-1b4cbee38efd
- Environment: production / 78c6b914-a09a-4a0e-81a9-ec82cd22a5da
- Web service: 0134642d-f6f5-4e40-9f16-6fc389502d9c
- Commit: b33535e568ae5a2a3b9720aeedbbb8bfec1bb252
- Branch: codex/research-v2-launch-20261030
- Deployment: 371a2d5a-dc13-4c78-9d51-e24d9dfcfe06
- Provider SUCCESS: 2026-10-30T19:20:47.358Z
- Database: railway, existing private MySQL 8.0.46 / InnoDB, page size 16384
- Exact migration: 8e5f3b0d7124 -> a6f2c9d84e71
- Migration SHA256: 17134427fa9bb31193210497db14a60bbaa9ba0d39426c3d446371f25fa97ba6

Final provider reads show one running web replica and one running MySQL
replica, no failures/issues, no staged changes and no pending work. The MySQL
service and both existing 500MB volumes were preserved. No extra service,
bucket, scheduler, public database endpoint or paid monitor was created.

## Execution and preservation

1. Read-only checks identified the exact hosted parent, 75 InnoDB tables and
   separate non-root runtime/migration accounts. Their existing database-scoped
   CRUD / ALL privileges were sufficient. No grants or passwords were changed.
   A privileged SELECT-only inventory confirmed zero triggers, routines,
   scheduled database events and views; runtime visibility alone was not used.
2. The authenticated Researcher used the ordinary UI to pause old v1 collection
   and close ten open sessions. Follow-up SELECT confirmed collecting=false and
   zero open sessions. Its ordinary audit entry made the source total 22141 rows.
3. The old web master was paused and its sole worker drained before the backup.
   A bounded safety helper was installed first. After deployment dispatch was
   armed it could not resume old code automatically. It belonged only to the
   old ephemeral container; the provider replaced that container normally.
4. A protected local frozen-parent backup captured all 75 tables, original DDL,
   FK/column metadata and 22141 typed rows. Full gzip, typed round-trip, counts
   and multiset/inventory hashes passed. A recovery procedure accompanies it.
   No restoration onto a separate MySQL server was executed or claimed.
5. The reviewed staged patch changed only the web branch/commit and the two
   variables RESEARCH_V2_ENABLED=true and RESEARCH_RETENTION_DAYS=45. The value
   was corrected to the application's exact boolean spelling before dispatch.
   Development provenance and RAILWAY_ALLOW_STUDY=0 remain in force.
6. The provider predeploy runner applied only the reviewed revision. Enforced
   CHECK/FK/index and model-schema verification passed, with no new upgrade
   operations detected. The production WSGI process started and /health/ready
   returned HTTP 200.
7. A separate postdeploy READ ONLY consistent snapshot compared every original
   column in all 74 non-version tables with the backup's typed multiset hashes.
   All matched. The sole allowed difference was the Alembic head value. Total
   original rows remain 22141; the database now has 86 tables and all 11 newly
   introduced tables were empty at verification time.
8. Browser review confirmed the retained authenticated Researcher session,
   paused old configuration, and the new v2 phone/desktop draft form with its
   fixed pilot settings and 45-day activation requirement. The form was viewed
   and cancelled; no new draft, protocol, release or collection was created.
9. The temporary SSH key was revoked, its absence checked, and its two local
   key files removed. The pre-existing operations key was untouched.

The activated historical v1 configuration retains its recorded 15-day policy.
The 45-day deployment setting governs upcoming v2 activation; no old research
row was reclassified or had its recorded policy rewritten.

## Evidence and verification boundary

Private receipts/operators/screenshots are under the ignored directory
instance/research_v2_hosted_deploy_20261030. The sensitive backup subdirectory
hosted_backup_20261030T191456Z has restricted Windows ACLs. Do not commit,
upload as source, or expose its data. Its gzip SHA256 is
c7ea8e0115b7741cb42c559bc1794a30f6f0aa362ec766db07872f9c92e72ed4
(1110051 compressed bytes / 8664420 JSON bytes). Inventory SHA256:
35c7827fb5f27ee23ef9681cd7cf0b2d37b502d1250d1e16c59ff9ccb6100226.

Executed: bounded static Python AST checks, independent source review,
committed-candidate diff whitespace check, real MySQL metadata/preservation
checks, actual provider migration/startup/health verification and browser review.
No automated application tests or pytest ran. Backup integrity verification
does not stand for a completed recovery rehearsal. The original mixed worktree
has pre-existing unrelated Finance/CRLF whitespace findings; the clean isolated
commit was checked separately and that exact commit was deployed.

## Remaining launch gates

The hosted installation is complete. Phase 6 / main study acceptance remains
open for a new hosted v2 configuration and recorded release/protocol, actual
Android and iPhone workflows, a small consented adult pilot, hosted delivery/
failure/recovery/export checks, and an explicit monitoring/retention operating
decision. Fictional rehearsal must keep development provenance; no study
collection or trained frustration model is claimed.

Monitoring source is packaged, but no quality/retention scheduler, Telegram,
independent heartbeat, AI review or paid monitoring service was activated.
Unattended monitoring while the owners' computer is off is not yet accepted.

Observed storage also needs a capacity decision before main collection: web
private storage had 48369664 free bytes (about 46MiB); MySQL provider disk usage
was about 0.2705GB on its existing 500MB volume. No storage was enlarged and no
data was deleted. Do not infer a month-long collection capacity guarantee.

The sensitive local recovery backup is outside the application retention
runner. Record an owner-approved backup retention/removal date separately;
this deployment did not enable automatic deletion of recovery copies.

Do not roll old source 4544d569 back blindly after a6f2c9d84e71. Any failure
requires a reviewed forward correction or separately approved backup recovery
with writers stopped; no downgrade, stamp, reset or seed is authorized.
