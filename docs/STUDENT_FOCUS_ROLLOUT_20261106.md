# Student focus reader and VPS course rollout - 2026-11-06

The owner authorized implementation and deployment of the reviewed continuous
Student reader, together with the locally verified Empower course map to the
existing Libyan Spider VPS. C1 is the centre's internal code for this Intermediate
B1+ book. Existing A1/A2 accounts, enrollments, finance and research are preserved.
Railway, Git staging, commits and pushes are excluded.

## Reader behavior

My learning and course cards open an authorized Lesson directly. The dedicated
reader removes the surrounding platform navigation, offers an on-demand course
contents panel and optional fullscreen. All Lesson PDF pages are one continuous
scroll. There are no page Previous/Next or Zoom controls. Native browser pinch
and Ctrl+wheel zoom remain enabled. Complete/undo forms retain CSRF and signed
progress state and occur only in ordinary flow at the end of the Lesson.

Every imported media position remains an independent marker, including repeated
Listen/Watch again references; no clustering or coordinate relocation occurs.
Course materials and Teacher additions remain separate. Private teaching books,
Workbook, answers and supplementary resources are never Student releases.

The shared media player progressively enhances real HTML audio/video elements
with custom playback, seeking, skip, volume, speed, supported PiP/fullscreen and
a live Web Audio waveform for both audio and video. It streams protected media,
never decodes whole files in memory, and preserves native playback/research
events. Animation stops on pause/page exit and respects reduced motion. Native
controls remain a fallback if the enhancement cannot initialize.

## Transfer and activation boundary

The immutable content bundle contains 359 unique source files (963,295,020 raw
bytes), 405 UploadedFile records, 13 Units, 88 Lessons, 252 Student Materials,
208 main-book markers, three books and 150 private teaching resources with 146
private anchors. It excludes users, credentials, enrollments, finance, research,
progress and local Group copies/releases. IDs are remapped to existing hosted
public Course/Group identifiers; no local database dump is imported.

The prepared VPS operation verifies source/content SHA256, runs production code
as adaptiveapp, takes protected database/file/config backups, pauses app writers,
hashes preexisting rows, applies only f6b3a18d920e -> 6c3e8b1a90d2 -> 8e74c029ab61,
and appends the course in one content transaction after ordinary file validation.
It publishes the 88 shared Lessons to each existing A1/A2 Group using native
LessonRelease semantics; Materials follow their Lesson. It does not create
independent Group curricula or release private Teacher resources.

Cutover changes the immutable release symlink, the reviewed upload limits and
Nginx proxy limits, then restarts only the platform service and reloads Nginx.
SSH, firewall and VPS reboot are unaffected. Failure recovery returns source,
proxy and runtime configuration to their backups; it never downgrades or
automatically restores business data. The known owner-accepted readiness 503
remains explicit; liveness, login, actual schema and content are separate checks.

## Evidence and current boundary

Local static review parsed 19 Python files, compiled 16 changed templates,
configured ORM mappers and confirmed the single Alembic head 8e74c029ab61.
Both new JavaScript files passed Node syntax review. An authenticated local
Student opened the real two-page Lesson 1C with all ten separate media markers,
played audio/video with custom controls, used ten-second skip and fullscreen,
and confirmed completion stays out of the initial viewport with static layout.
Responsive widths 1024 and 390 were inspected; 390 had no horizontal overflow
and no Zoom buttons. Physical touch devices were not tested. No completion,
grade, payment or research label was fabricated. No automated tests ran, per
the repository's current owner instruction.

Transfer/deployment artifacts and receipts are outside the repository at
`C:\Users\abdul\AdaptiveEnglishLMS-VPS\2026-11-06-student-course-rollout`.
## Hosted execution and acceptance

Hosted release `20261106T203821Z-3067b33` succeeded on 2026-11-06 at 20:58 UTC
(22:58 Libya time). All 894 packaged source files matched the reviewed manifest.
The exact additive schema upgrade reached 8e74c029ab61. The complete bundle
SHA256 matched before import; all 359 unique source files and 405 newly validated
UploadedFile records were verified. The import preserved old row hashes across
89 existing tables (excluding Alembic metadata); only the target Course's
optimistic version/time changed. A1/A2 each received 88 Lesson releases.

The first guard rejected an additional hosted Group named Test. An initial
retry improved diagnostics but retained that unnecessarily global restriction.
The corrected operation selected only the existing A1/A2 public identifiers,
preserved Test and reused the prepared runtime. The first stopped operation
successfully restored the original source/proxy; the second stopped before
maintenance. Neither changed schema/content. No database downgrade or automatic
business-data restore occurred.

The owner then separately authorized exposing the course to Test. Test already
had an independent curriculum snapshot, so shared publication alone was refused.
The subsequent bounded operation proved its snapshot had no Units or progress,
backed up the database/private files, and populated that existing empty snapshot
through the normalized Group curriculum relationships. It created 13 Units,
88 Lessons, 252 Materials and explicit Course-material lineage; each new upload
key shares only validated immutable app-owned bytes through a hardlink. It
published 88 Test Lessons, preserved the original Course and other Groups'
publication, created no extra payload copies and restarted no service. No Group
snapshot/history was deleted and no existing Teacher material was overwritten.

Authenticated hosted Student acceptance used the owner's designated account
without saving its password. Start course opened a Lesson directly. Lesson 1C
loaded both PDF images and all ten separate markers, including repeated video
references. Audio advanced to 24.6/34.5 seconds; video played through protected
HTTP 206 range responses with actual dimensions 640px, native controls disabled,
custom controls visible and a non-flat live waveform. Ten-second skip, 1.25x
speed, video fullscreen and reader fullscreen worked. At 1024px the maximum
marker-centre difference from the stored coordinate was below 0.002 percentage
points; 1024px and 390px had no horizontal overflow. Complete stayed outside the
initial viewport and entered view only after scrolling to the end, with static
position. No completion was submitted. The Student received 403 at Teacher
books. Physical tablet gestures and automated regressions were not tested.

Native browser screenshots were inspected. A data-URL screenshot export was
rejected by the browser URL policy; no workaround was attempted after that
rejection, and no screenshot export is claimed. Temporary review DOM links were
removed; viewport overrides and network instrumentation were reset. The hosted
Student course tab remains open for the owner.

External HTTPS liveness and the new media JavaScript/reader CSS returned 200;
TLS validation succeeded. The known owner-accepted `/health/ready` 503 remains
unchanged and is not described as a readiness pass. Live schema/content and
liveness/login were checked separately. No Railway, Git staging, commit/push,
SSH/firewall change, VPS reboot, account reset, finance or research-label edit
was performed.

Receipts outside the repository:

- `hosted-rollout-receipt.json`: source, content, migration and old-row evidence.
- `hosted-test-publication-receipt.json`: empty Test snapshot fill/publication.
- `hosted-browser-review.json`: bounded real-browser acceptance and limitations.

Protected server backups are retained under
`/root/adaptiveenglishlms-vps/student-course-rollout-20261106/backup.before.r3`
and `backup.quiescent.r3` (database, private files and configurations), and
`/root/adaptiveenglishlms-vps/test-course-publication-v2-20261106` (before Test
population). Failed attempts, staged inputs and the previous immutable release
are retained. Recovery must preserve additive schema/history; do not blindly
run the old f6 deployment script or restore/downgrade the business database.
