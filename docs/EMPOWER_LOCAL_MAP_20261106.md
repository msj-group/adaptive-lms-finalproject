# Local Empower content map - 2026-11-06

## Authority and target

The owner requested analysis of the complete Empower INTERMEDITE directory,
confirmed that C1 is an internal centre code for this Intermediate B1+ book,
and authorized applying the reviewed map locally before publication. The owner
also allowed replacing trial local catalogue entries with the hosted setup.
This execution does not authorize a new hosted release or database import.

The target is the local C1 Course with public identifier
`d0f65096-4eab-41f5-8d12-272ef7ca4b55`. A read-only hosted catalogue audit supplied
its Course/Level/Term/Group metadata. Local A1 and A2 share the October 2026 to
February 2027 term. No hosted accounts, enrollments or research data were copied.
The two old local Courses and Groups were archived, preserving their content,
files and memberships; old Course codes were retained as TRIAL-36 and TRIAL-37.

## Imported content

| Area | Local result |
|---|---|
| Main Student book | 172 pages; 10 teaching Units and 3 reference modules |
| Course structure | 13 Units and 88 Lessons with contiguous, non-overlapping ranges |
| Student Materials | 252 active Materials, including 208 main-book media anchors |
| Class video | 24 matched videos at their corresponding book anchors, with 24 separate audio-only alternatives |
| Documentaries | 10 videos and 10 activity sheets in the corresponding Unit review Lessons |
| Teacher's Book | 162 pages, private teaching reference |
| Workbook | 87 pages, private teaching reference including its answer pages |
| Other teaching resources | 150 private resources and 146 private audio/video anchors across Workbook and Academic Skills pages |
| Stored upload metadata | 405 new UploadedFile rows from 359 eligible unique source files |

Repeated book references remain separate Materials where the same recording
is used by different activities. Class videos use the existing play control;
their audio-only alternatives remain in the Lesson's ordinary material list.
Documentary activity sheets do not receive invented page anchors.

Source audio labels were extracted with local OCR and matched against track
names; all 208 main-book label crops were visually reviewed. Video placement
also used supplied transcripts and Teacher's Book references. The main book's
printed-page offset is six PDF pages. Cover, contents and acknowledgements
remain readable in the complete book without becoming artificial Lessons.

## Visibility and source implementation

Private teaching files use TeachingAsset and TeachingAssetPin rather than
Student Materials. Administrator routes require the Administrator role and
the actual Course. Teacher routes prove current assignment to the actual
Group for every read, page, stream and download. These routes are unavailable
to Student and Researcher roles. This preserves the existing authorized file
streaming, private cache headers and PDFium page rendering.

New Group curriculum copies record GroupMaterialSource links to the original
Course Materials. Student presentation uses proved same-Course lineage rather
than uploader identity: imported pins remain on the book, unpinned originals
appear under Course content, and later Teacher additions appear in a separate
section. Existing release, enrollment, progress and research rules remain.
Legacy copies without lineage are conservatively classified as Teacher content;
this local target had no copies. A future rollout must recheck hosted copies.

The ordinary Course Content page owns the optional main book, Teacher's Book
and Workbook list, Edit division and Edit content. Private supplementary
resources are grouped below it. The shared media-player macro is also used by
the book readers, so mapped audio/video plays without entering editing mode.

## Migration, preservation and storage

The exact local migration was 6c3e8b1a90d2 -> 8e74c029ab61. Four additive tables
were created: group_material_sources, course_content_imports, teaching_assets
and teaching_asset_pins. MySQL constraints enforce the same-Course resource
references, the exclusive book/document target and bounded coordinates.
The downgrade refuses nonempty new tables; it was not executed.

A private user-only ACL directory outside Git holds the fresh full compressed
MySQL backup and before/after fingerprints. Gzip integrity was verified before
migration. Backup SHA-256:
`edec4d7b9b5d9fc36f63dcc0ac7a89df0bd91f574b646c37f890c23c03664894`.

The content transaction checked the exact local engine, Course, empty target,
two Groups and absence of Group copies under the existing locks. The import
marker records manifest SHA-256
`b05f4c662f9130b08bc7e8fb547f9f993b1bca7ee49f8acb2601794fa94a650e`.
An identical retry returns ALREADY_APPLIED before content writes; a different
manifest is refused. This retry branch was reviewed, not separately executed.

After import, 83 unrelated preexisting tables retained identical row
fingerprints, and preexisting content/upload rows were preserved. Users and
memberships did not change. No Group curriculum copy, Lesson release or
Material release was created. The Course version advanced for its new content.

Every eligible source file passed the existing storage validation. To fit the
available disk space, 344 private immutable app-storage paths reuse verified
app-owned bytes through NTFS hard links; 61 unique physical files were added
(about 103.49 MiB). No user source file was linked or modified. A future transfer
must copy every referenced storage key as an ordinary file; it must not assume
cross-machine hard links or reuse local database numeric IDs.

## Checks actually performed

- Static review: 18 changed Python files, 13 changed Jinja templates,
  SQLAlchemy mapper configuration, one Alembic head, model/offline MySQL DDL,
  JavaScript syntax and git diff --check. The static factory blocked database
  connections explicitly.
- Real local MySQL: exact migration head and new constraints read back; content
  counts, page-range ownership, private-resource separation and preservation
  fingerprints verified in the bounded import/audit.
- Authenticated local Administrator browser: three books displayed once on
  Course Content; main-book Audio 01.05 and Unit 1 Part 1 video played;
  Workbook Audio 01.01 and Academic Skills 1.1 played. Media elements reached
  readyState 4 with no media error. The video reported 640 x 360 dimensions.
- Ordinary editor Student preview for Lesson 1C showed pages 20-21 and their
  play controls. The narrow viewport was visually reviewed. This is an Admin
  preview, not a Student-role authorization check.

No automated application tests or replacement test package were run or created.
Not every media file was played. Teacher/Student cross-role browser acceptance
was not performed: the mirrored local Groups have no new Teacher assignments
or enrollments. A 500 MiB upload transfer and hosted proxy behavior were not
verified. The prior readiness-revision exception remains; it is not acceptance
of this new schema for a future deployment.

## Deferred content and next publication boundary

The supplied 287 VTT sidecars remain in the source directory; the current
player has no subtitle import. The German word-list XLS is unsupported by the
current Material format and was not imported. The supplied final exam is a
private reference only: its header declares 60 marks while the present sections
sum to 32, so it must be corrected before use as an assessment. No questions,
grades or answer keys were automatically converted into Student assessments.

Course content is ready for local review. Student access still requires the
existing per-Group publication workflow. No publication was forced. Before any
later VPS release, prepare a fresh source artifact, current hosted backup and
exact schema/storage import plan; preserve hosted users and enrollment IDs.
The earlier deployment helpers are not a release of these uncommitted changes.
No Railway access, hosted migration, upload, restart, Git commit or push occurred
in this local operation.

Operator receipts, source map and private backup are in the external
`Empower-content-analysis-20261106` directory. They are operational artifacts,
not runtime dependencies or files to commit.
