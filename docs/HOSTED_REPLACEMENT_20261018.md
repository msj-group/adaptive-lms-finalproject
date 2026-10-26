# Current release and hosted-data replacement — 2026-10-18

## Completed execution

The reviewed code was pushed normally to both established GitHub branches and
deployed as `10e5003469863ae0f77b0bae9f9e09743fcb4c06`. Railway web deployment
`fd3290bc-f969-41b4-9806-5616c0a61940` is successful. The owner then explicitly
approved a temporary personal-account SSH key with inherited account permissions,
restricted in use to this project's backup, transfer and review, with revocation
and local key-file deletion on completion or stopping.

Hosted replacement committed and verified at **2026-10-18 20:23:35 UTC**:

- All **75 tables / 21,503 rows**, including **97 accounts**, match the prepared
  local snapshot's row fingerprints. All **167 foreign keys** pass orphan checks;
  FK enforcement stayed enabled throughout the single InnoDB data transaction.
- All **348 private attachments / 394,967,304 bytes** match their original names,
  sizes and SHA-256 values. Seven distinct contents were transferred once in a
  6,061,667-byte archive and reconstructed as 348 independent files. There are no
  shared hard links or changes to file identity/lifecycle semantics.
- The prior hosted **75-table / 1,525-row** snapshot and **20 private files** are
  preserved locally. The database was actually restored and verified on an owned
  isolated loopback MySQL process, then the files were restored and checked. That
  process was shut down; the ordinary local database was never written. Its final
  fingerprints still match the prepared source.
- Source and target table definitions match apart from auto-increment counters.
  Head remains `8e5f3b0d7124`; no schema migration was created/applied. Gunicorn
  requests were drained for the bounded cutover and its worker resumed afterward.
  Research remains paused, deployment provenance `development`, copied subjects/
  sessions `demo`, and retention 15 days. Payment/hosting/scheduler settings stay.
- Eighteen actual role-scoped HTTP pages return 200; four cross-role refusals
  return 403. A protected Listening range request returns 206, the expected
  128-byte RIFF slice and WAV type. Public readiness/Login/placement and all five
  changed assets return 200 and match the deployed commit.
- These HTTP requests used environment-signed operator sessions. Password login,
  interactive browser forms, concurrency and comprehensive automated regression
  were not exercised. The audio GET adds its ordinary file-access audit row after
  the exact snapshot comparison; no form was submitted. No automated tests ran.
- Old private files were removed from active hosting only after backup/recovery
  and post-copy review. Owned remote staging was removed. The temporary SSH key
  was revoked at **20:25:48 UTC**, its absence verified and both local key files
  deleted; the operator SSH connection is closed.

Private evidence/recovery: `instance/release_transfer_20261018/`, particularly
`execution.json`, `cutover_result.json`, `hosted_http_review.json`,
`public_release.json`, `temporary_ssh_key.json` and the hosted/source backup
manifests. These contain operational/private artifacts and remain outside Git.
This closeout documentation is a local follow-up; no additional code commit,
push or deployment was performed after the already delivered `10e5003` release.

## Owner authority and exact destination

The owner requests push, deployment and replacement of the existing hosted
application database with the current local database. This is new explicit
authorization for one hosted replacement, superseding earlier local-only data
restrictions. The local source remains unchanged. There is no new migration,
automated test, scheduler change, dependency installation or unrelated feature.

- Source: local MySQL `127.0.0.1:3306/adaptive_english_lms` and its configured
  private attachment directory. Both migration heads must be `8e5f3b0d7124`.
- Existing destination: Railway project `AdaptiveEnglishLMS`, environment
  `production`, services `adaptive-lms-web` and `MySQL`, with their existing
  `/data` and `/var/lib/mysql` persistent volumes.
- GitHub repository: `msj-group/adaptive-lms-finalproject`; existing branches
  `ui/platform-unification-20261009` and `preserve/st2-20261008`. Starting code,
  both remote heads and the successful web source pin were `752d471`.
- Preserve development deployment provenance, synthetic `demo` session/subject
  classification, paused collection, sampling semantics and 15-day retention.
  This operation does not create or approve a Study deployment.

## Release preparation and verification boundary

Release the reviewed Student Activities, Teacher Activities/library navigation
and Review queue changes and their documentation. Exclude unrelated unused
`app/templates/workspace/help.html`, `output/`, secrets and private operational
files. Use one reviewed allowlisted commit, compatible fast-forward pushes and
the existing Railway web service pinned to the verified GitHub SHA. Retain its
build/start/predeploy commands and every unrelated hosting setting.

Current static evidence: all 366 application/script/migration Python sources
parse, including the seven affected modules; all 244 localized
templates compile and literal template references resolve, Arabic JSON parses,
the two changed JavaScript sources pass Node syntax, four publication functions
match the starting commit and CRLF-aware diff checking passes. This is bounded
static review; automated regression or full authenticated workflow acceptance is
not claimed. Release execution/public probes are recorded in ignored private
operator evidence, rather than predicted here.

## Private snapshot and required cutover

The private operator helper `instance/release_transfer_20261018/prepare_local.py`
reads the local database/files only. It captures a consistent full MySQL dump,
archives attachments, verifies gzip/archive/file hashes and checks source row
fingerprints before/after. Secrets and row values are never printed or committed.
The completed snapshot has 75 tables and 21,503 rows (including 97 accounts),
plus 348 attachments totaling 394,967,304 uncompressed bytes. Database row
fingerprints match before/after capture; gzip/archive integrity and all file
hashes passed. Restore has not been exercised. Snapshot paths/manifests remain
ignored. The dataset's earlier creation/review
counts and limitations are in LOCAL_EVALUATION_DATA_20261018.md.

Before any hosted replacement, obtain authenticated shell/database/file-transfer
access, capture a consistent hosted database and private-file backup, verify a
restore, recheck source/destination heads, prevent application writes and perform
the bounded cutover. Verify row content/counts, FK integrity, attachment references
and hashes, non-study classification and ordinary role-scoped pages. Keep the
recovery artifacts. A locally prepared snapshot is not a hosted backup or proof
that a restore has run.

Initially the connected Railway OAuth tools redacted variable values and provided
no shell/file-transfer operation, so replacement was pending access. The owner
subsequently approved the portable official CLI, completed login and explicitly
approved the temporary SSH key described above. Its verified execution supersedes
that preparation boundary. No global CLI installation or dependency change occurred.
