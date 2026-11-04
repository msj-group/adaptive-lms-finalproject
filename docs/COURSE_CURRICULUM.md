# Course curriculum and publication

## Add and edit course content

Open Academic setup → Courses → Edit division and upload the main PDF
(up to 64 MiB and 1,000 pages). Save each unit and lesson with its first
and last pages. Lessons must stay inside their unit. Active unit ranges
and sibling lesson ranges cannot overlap. Pages such as the cover can
remain unassigned. Reopening the editor shows the last saved division.

Choose Edit content, select a lesson in the outline, and use Add file
to select MP3, WAV, MP4 or WebM. Drag the marker to a position on a
page and confirm the position. Icons are shown without filenames.
Confirm position saves the file and position together without a page reload. Drag a saved
marker to move it. Student preview shows the clickable media markers
and their player. Replacing the PDF requires confirming that existing
page numbers and media placements still refer to the intended pages.

Lessons start as drafts. Mark the lessons ready for teachers in Edit
division. The teacher releases lessons and media to each group.

## Edit a group's independent copy

Assigned Teachers can choose Edit division or Edit content in the Group workspace,
Curriculum or Publication page. The first click submits a CSRF-protected POST
that snapshots the Course into that Group once. Later clicks reopen the same
saved copy. A GET never creates a copy. All currently assigned Teachers of the
same Group use its one copy, with stale-division and material-version checks.

The existing full-screen editors support page ranges, units, lessons, the main
PDF and positioned media. Changes affect that Group only. The Course's identity
and original content, and every other Group's content, stay independent. Later
administrator changes to the original are not automatically merged into copies.
Unassigned, inactive or removed Teachers cannot mutate the Group copy.

Existing Group-owned legacy units remain alongside the copied Course units.
Files belonging to copied Materials receive independent immutable storage rows
and bytes. The main PDF initially references its immutable original upload; a
replacement changes only the Group document reference. File staging happens
before write locks; the source is rechecked under the Course/Group lock prefix
before the copy is committed atomically. Failed/replayed copies clean their
staged files and do not create a second curriculum.

Publication, hidden materials and scheduled releases retain their existing
states. New lessons remain hidden until made ready and released in Publication.
Episode-bound progress snapshots retain completion, timestamps and versions;
original progress rows and enrollment identities are preserved as history.
Outlines, search, files, PDF pages, progress and Teacher previews use the same
effective Group ownership predicate, excluding the shared Course after copying.
Saved links naming original objects do not gain access to those original objects
through the copied Group; the current outline supplies the copy's identifiers.

## Release content to a group

Administrators edit the curriculum and mark shared lessons ready for teachers.
Teachers open a group and select Publication. They can publish the whole ready
curriculum, publish or hide a selection, or schedule selected units, lessons
and materials using the displayed time zone. An individual material is
available while both it and its parent lesson are available. The full-course
action also makes previously restricted active materials available.

Students open the units and lessons outline, expand a lesson's Materials list,
and jump directly to a material. Lesson pages have their own material index.
The same group rules govern search, progress and direct downloads. Teachers
can browse the saved materials for their assigned groups. Existing visible
lessons are preserved when the publication migration is applied.

The in-app inbox notifies assigned teachers of curriculum changes and alerts
students about changes to available content. Due releases produce one notice
per publication action on the student's next platform visit. A schedule that
was cancelled or replaced before it became available does not produce the
original release notice.

## Database setup

Revision `8a21f7d9c460` adds the private PDF reference, page ranges and
media positions. It follows `d49ac73eb621`. The PDF stays in private
material storage. Student page requests return only individually
authorized page images.

Revision `f6b3a18d920e`, following `8a21f7d9c460`, adds `group_curricula` with
Group/Course/file foreign keys and document/revision checks. It adds an empty
table without copying or changing existing business rows. Copies are created
only by an authorized Teacher's editor action. Downgrade refuses once a copy
exists, because removing ownership would expose both shared and copied content.

The owner separately approved creation and local application on 2026-11-04.
Development MySQL was verified at the exact parent before applying only this
revision. The empty new table, InnoDB engine, three foreign keys and two checks
were verified afterwards; existing Course/Group/Unit/Lesson/Material/progress/file
row counts were unchanged. No real Group was copied by the migration.

## Verification on 2026-11-04

Bounded browser review used the existing isolated MySQL fixture and fictional
accounts, with a dedicated fictional Course, two assigned Groups and one
unassigned Group. Ordinary routes covered administrator PDF/audio saves,
Student completion, two independent copies, live Unit/Lesson creation without
reload, division saves, AJAX media insertion and replay, text editing, own-PDF
replacement, readiness and selected publication. Read-only database checks
confirmed original/other-Group isolation, immutable separate file identities,
progress snapshots, publication schedules and one copy per Group. Unauthorized
role/original-object/Group attempts and a stale division were rejected. A phone
editor and Student PDF page were checked in Chrome. This is bounded manual
review, not a replacement automated regression suite or a concurrency/load test.
