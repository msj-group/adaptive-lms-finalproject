# Course books and 500 MiB uploads

The owner requested separate Teacher's Book and Workbook uploads visible only
to teachers, with the main book remaining the Student lesson source. They also
requested a 500 MiB maximum for books and lesson videos.

## Implementation

- Courses > Course Content lists only uploaded books. One Add book form offers
  the missing optional types: main book, Teacher's Book and Workbook. A course
  does not have to provide all three. The main book uses the existing
  CourseDocument; its upload and replacement controls live only on this page.
  Edit division and Edit content live on this page instead of the Course
  directory. Page division does not repeat the upload form.
  An existing Teacher Group's independent main-PDF editor remains available.
- Teacher's Book and Workbook use course_reference_books, a separate normalized
  relation keyed by course and purpose. These files never become Materials,
  LessonReleases or Student-visible source documents.
- Teachers open the books through their assigned Group. Every reader, page and
  download route checks the current Teacher role and Group assignment. Student
  and Researcher roles have no entry point. Reading does not create a Group
  curriculum snapshot. Reference books remain shared after a Group makes its
  own independent main curriculum copy.
- PDF pages are rendered individually using the existing PDFium lock and
  private responses. Downloads reuse the authorized streaming/file-access-log
  service. Replacements check the locked revision and preserve previous file
  metadata and bytes; there is no hard-delete action.
- New migration: f6b3a18d920e -> 6c3e8b1a90d2. It adds one empty table without
  updating existing rows. Downgrade refuses to remove nonempty references.
- Book and document/video defaults are 524288000 bytes. Main/reference PDFs
  retain the 1,000-page bound. Images and audio keep their existing limits.
- The global request bound includes multipart overhead. Browser checks reject
  over-limit selections, but server streaming validation remains authoritative.
  Werkzeug spools large form uploads to temporary disk; application storage
  reads 1 MiB chunks instead of loading 500 MiB into a Python byte string.
- Nginx example: 501m body bound, request buffering on, 600s body/read/send
  inactivity timeouts. Gunicorn's timeout is 600s; worker/thread count is
  unchanged. Live configuration must match and tmp storage must be on disk.

## Scope and rollout

The book-upload feature alone does not import the Empower directory or change
Student membership, payments, research or the existing readiness exception.
The separately authorized local map import is recorded in
[Local Empower map](EMPOWER_LOCAL_MAP_20261106.md); original source files remain
unchanged. Subtitle support is still outside this implementation.

Implementation checks and the VPS rollout are recorded separately after they
run. Do not describe the migration, live Nginx reload, live upload boundaries or
cross-role access checks as complete until verified. The owner previously
required approval before operations affecting live services; prepare the exact
release, backup and migration before requesting that approval. Do not access
Railway or the previous production database.

## Local execution on 2026-11-06

The owner explicitly requested local execution before publication. The existing
local MySQL 8.0.46 target was verified as localhost:3306/adaptive_english_lms at
f6b3a18d920e. A user-only ACL directory outside the repository holds a compressed
mysqldump, an environment backup and before/after row fingerprints. Gzip integrity
was checked before applying only f6b3a18d920e -> 6c3e8b1a90d2.

The 89 existing non-version tables retained identical ordered row fingerprints:
7 users, 2 enrollments, 13 units, 64 lessons and 413 materials were preserved.
The new reference table is empty. Only the local environment's document/video
limit keys were changed to 524288000; no credentials or research settings changed.

Python syntax, nine affected Jinja templates, JavaScript syntax, SQLAlchemy
mapper configuration, the single migration head and offline MySQL DDL were
reviewed. The actual new table's PK/FKs/checks and the active local limits were
read back. A loopback-only development server was started on 127.0.0.1:5000,
without debug/reloader, and /health and /auth/login returned 200. No pytest or
new automated test package was created. A full 500 MiB transfer and live
cross-role access checks are not yet claimed.

The owner's follow-up on 2026-11-06 replaces the three-slot presentation with
the optional list and single upload form described above. This presentation
change adds no schema changes or business data writes.

An authenticated local Administrator browser review on 2026-11-06 confirmed:
the C1 course lists its existing 172-page main PDF once; Add book offers only
Teacher's Book and Workbook; replacement retains the page-range confirmation;
and the shared division page no longer offers main-PDF upload/replacement.
The list and add form were also viewed in the existing narrow Chrome window.
No upload/replacement was submitted. Static review passed for 13 Python files,
nine Jinja templates, both changed JavaScript files and git diff --check.

No VPS migration, environment change, proxy reload, deployment, Git commit or
push was executed. Earlier external rollout artifacts are preparation only and
must be rebuilt against the final local changes before any later publication.
