# Current release and hosted-data replacement — 2026-10-18

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

At preparation time the connected Railway OAuth tools redact variable values
and provide no arbitrary shell, SQL or file-transfer operation; no authenticated
Railway CLI was found. The browser dashboard did not expose a usable project
interface. Hosted data replacement is pending that access. No hosted database
write or file replacement has occurred, and no recovery success is claimed.
