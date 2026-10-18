# Local operational evaluation dataset — 2026-10-18

## Authorization and boundary

The owner explicitly requested removal of old example/development business data
and a much larger realistic fictional population, then selected **the local copy
on this computer only**. This new, bounded reset permission supersedes the earlier
local preservation-only instruction for this operation. It is not permission to
repeat the reset. Railway and hosted data are unchanged.

Checkpoint: `752d471`. Target: loopback MySQL on port 3306,
`adaptive_english_lms`; migration head remains `8e5f3b0d7124`. No application
feature, model, schema, migration, dependency, scheduler, machine/service setting
or Git mutation belongs to this operation. Concurrent unrelated Review queue
presentation/documentation work and existing untracked Help/output work are preserved.

## Persisted population

| Area | Records |
| --- | ---: |
| Accounts | 97: 80 Students, 12 Teachers, 2 Administrators, 3 Researchers |
| Academic structure | 6 levels, A1–C2; 12 Courses; 3 Terms; 36 Groups |
| Teaching and timetable | 72 Teacher assignments; 8 Rooms; 72 schedules |
| Enrollment | 268 episodes; 268 memberships; 316 lifecycle events |
| Content | 108 Units; 324 Lessons; 684 Materials |
| Activities | 144 written Assignments; 108 Speaking; 108 Quizzes; 72 Listening |
| Activity backing tables | 252 Assignment rows and 180 Quiz rows, including Speaking/Listening parents |
| Student work | 1,060 Lesson progress rows; 264 written submissions; 240 Speaking submissions; 570 Quiz attempts |
| Teacher feedback | 150 written comments; 164 Speaking comments, plus pending reviews |
| Attendance | 216 sessions; 1,984 roster marks |
| Grades | 144 categories; 216 items; 1,032 Student records |
| Communications | 78 Announcements; 72 topics; 324 replies; 60 private threads; 180 messages; 724 notifications |
| Calendar | 30 events |
| Finance | 268 invoices; 268 cash/bank movements; 201 receipts; 875 document revisions |
| Research operational review | 36 synthetic subjects/sessions; 432 events; 36 prompts; 2 configurations; 1 stored export |
| Private attachments | 348 complete files with verified size and SHA-256 |

Summer Groups/episodes are historical, Autumn Groups are operational and Winter
Groups support upcoming registration. Activity examples include open, past-due,
scheduled and draft states; Quiz attempts include submitted, expired and in-progress
states. Attendance has drafts/finalized sessions and all four marks; grading has
released/unreleased items. Finance includes discounts, full/partial cash, pending/
confirmed/rejected bank transfers, corrections and visible deleted/cancelled
obligations. Online payments stay disabled; no provider events were fabricated.

Learning content is original fictional material. Listening passages use locally
generated English speech with matching questions. Speaking samples are synthetic
speech, not actual Student recordings. Main Student access accounts retain open,
unsubmitted work so the owner can exercise the ordinary workflows.

Five access accounts retain their public identities and existing password hashes:
`admin@example.com`, `teacher@example.com`, `student@example.com`,
`researcher@example.com` and the owner's existing personal Student account.
Authentication versions advanced, so old sessions must sign in again. Added
fictional accounts inherit the existing local credentials of their primary role.
No passwords/hashes are recorded in documentation or console output.

Research is explicitly **non-study**: deployment provenance `development`,
synthetic subject/session provenance `demo` (the existing supported classification),
collection paused and retention 15 days. Sampling/window/label meanings are unchanged.
The existing export service generated one development-scoped archive with 36 sessions,
432 events and 36 prompts; its rows/provenance and SHA-256 were verified. No Study
subject/session/export was created. This synthetic interaction history is fixture
content, not evidence of observed human usage. No security submission receipts or
actual collection/sampling audit evidence were fabricated. Retired fee-plan and
simulated-payment compatibility features remain empty.

## Recovery and execution

Private ignored evidence is in `instance/local_evaluation_20261018/`.
`backup_20261018T100832Z/` holds the original full 75-table MySQL dump, available
private files, preserved accounts and checksum manifest. There were originally
1,029 business rows. Gzip integrity and archive checksums passed; restore was
**not** exercised. Seventeen old file references were already missing on disk;
the backup preserves the four files that actually existed.

Deletion and replacement committed in one InnoDB transaction with FK checks
enabled. Earlier preparation failures rolled back their business changes. After
commit, the four obsolete private files were removed from active storage after
digest checks, and the old demo credential/count files were moved into recovery.
All 348 new attachment references resolve to verified actual bytes.

Private evidence includes `completed.json`, `export_review.json`,
`http_review.json`, `cleanup_review.json`, `final_review.json` and `closeout.json`. Operator helpers,
credentials, backups and review artifacts remain ignored and outside deployment.

## Checks actually executed and limits

- Twenty-four local data reviews passed before commit: roles/levels, non-study
  classification, paused collection, no online transactions, FK enforcement,
  capacity, Course/Term membership, episode/role ownership and membership lifecycle,
  grade weights/bounds, submission/attempt timing and ownership, financial ownership/
  receipts, every declared FK, attachment bytes/digests and finalized Quiz scores.
- Actual HTTP review of 77 real-data scoped list/detail/pagination views: all 200.
  Readiness/public Login: 200. Four cross-role exclusions: 403. These requests used
  environment-signed local operator sessions; password login was not exercised.
- The existing operational export service created/stored the verified archive.
- The final read-only persisted-data review repeated all 24 checks successfully
  after export. All five retained password hashes/public identities matched their
  original values. A real protected Listening audio range request returned 206,
  correct WAV content type and the expected 128-byte RIFF header slice.
- CRLF-aware `git diff --check` passed. HEAD stays `752d471`, staging is empty
  and the documentation remains uncommitted; unrelated changes are preserved.

No automated tests were created or executed. This is bounded data/HTTP review,
not automated regression acceptance. Interactive browser forms, password-login
interaction, concurrency and complete end-to-end writes were not exercised.
