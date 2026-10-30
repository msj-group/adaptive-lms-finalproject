# Research v2 hosted release: prepared execution boundary

Prepared 2026-10-30. This is a review proposal, not permission to commit, push,
deploy, mutate a hosted database, purge data or enable a paid resource.

## Verified read-only hosting inventory

Railway project AdaptiveEnglishLMS / production currently has two online
services: adaptive-lms-web and MySQL. The web service remains pinned to
4544d56915b7e321921f9ea652ba9b6b9c86e357 in
msj-group/adaptive-lms-finalproject, branch master. The latest live web
deployment is 0fbcbdf5-fc44-4e9b-a043-8efa06a3522a from Oct 26. There are no
pending deployment changes or cron services, and no quality/Telegram/heartbeat
variable names on the web service. This was rechecked Oct 30 without changing
Railway. Local HEAD still equals that source pin; the mixed worktree is not a
deployment artifact.

The provider's online state is not proof of the application's current database
revision, backup validity, data provenance, absence of writers or study readiness.
The hosted application database name/revision and effective migration privileges
have not been directly verified. Never substitute the local database receipt.

## Concrete proposed source operation

Use the separately inventoried Research-only candidate under
instance/research_v2_release_candidate_20261029T173101Z/source, after its final
document refresh and independent hash review. Its runtime fingerprint is
201ead4f8d0fd95a320800cbb1fd56d50404d3d222f0f55bf23d14542b525c37
over 826 runtime files. The final source proposal has 73 Research overlays,
919 source files and 846 unchanged base paths; the candidate review records
the exact file hashes. Its allowlist excludes the
unrelated Finance changes and preserves the base commit outside named overlays.
The candidate record and final acceptance ledger identify the exact source;
do not stage the mixed working tree, local .env, ignored instance data, private
operator receipts, screenshots or downloaded research archives.

The next reviewable operation is an isolated checkout based on the exact source
pin above and branch codex/research-v2-launch-20261030 in the existing repository.
After concrete owner approval, create the source-owned baseline from the
independently reviewed runtime inventory, record local acceptance and limits,
then create one Research-only commit and push that branch for review. Preserve
the original mixed working tree and its empty index. Do not merge master,
change the Railway pin, deploy or activate collection as part of this source
operation. Owner approval remains required by AGENTS.md. A source review,
baseline, package or commit does not authorize a production database transition
or Study collection. The ZIP's document limit is recorded separately in
RESEARCH_V2_RELEASE_PREPARATION.md.

## Hosted execution gates

1. Verify the intended MySQL service, exact database/schema, current revision,
   supported MySQL/InnoDB settings and actual account grants read-only. The
   prepared transition accepts exactly 8e5f3b0d7124 -> a6f2c9d84e71, or verifies
   an already-current a6f2c9d84e71 without DDL. Any other state means stop.
2. Obtain a fresh protected backup with a recoverable restoration procedure.
   Record scope, time and integrity privately; do not print credentials/data.
   Stop ordinary writers and close collection sessions before the snapshot and
   upgrade. The named migration lease coordinates migrators, not web writers.
3. Review the exact source pin, staged provider diff and any narrowly necessary
   temporary migration privileges. The local grant approval does not authorize
   a hosted account change. No reset, seed, downgrade or repair of partial DDL.
4. Execute only the approved transition under separate migration credentials,
   validate preserved old rows, constraints, metadata and readiness, and remove
   any separately approved temporary rights. The prepared runner's in-memory
   fingerprints are not a replacement for a backup.
5. Use development provenance for fictional hosted/physical-phone rehearsal.
   Recheck ordinary Student/Researcher workflows and data exports on that exact
   deployed candidate. Do not change existing data into Study provenance.

Do not assume an old-code rollback is safe after the database reaches a6: the
old readiness check expects 8e. A failure needs a reviewed forward repair or
explicitly approved backup recovery with writers stopped, not an automatic
downgrade, stamp, reset or blind old-source redeployment.

## Collection and monitoring stop point

The owner selected preparation only for monitoring. No new paid service,
scheduled worker, account or Telegram test is enabled by this proposal. Cloud
quality, independent missed-run detection and daily-retention success still
need operational acceptance before claiming unattended monitoring while the
owners' computer is off.

Main collection remains gated by actual phone checks, a small consented adult
pilot, recorded retention/configuration/provenance and a documented monitoring
decision. Local viewport checks and fictional labels do not satisfy these
scientific/operational gates. See RESEARCH_V2_LAUNCH_ACCEPTANCE.md for the actual
executed checks and RESEARCH_V2_CLOUD_COST_PROPOSAL.md for the unapproved estimate.
