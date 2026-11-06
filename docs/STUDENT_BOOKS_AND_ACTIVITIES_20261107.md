# Student books, focused reader and activity presentation

The owner requested source changes, a reviewed commit/push and deployment to the
new Libyan Spider VPS only, with practice activities for its existing Test Group.
The later clarification requires Workbook exercises to follow native Lesson
publication; the complete book and its answers remain private to Teachers.

## Resulting behavior

Courses show course/group names and Student Book / Workbook actions. Home removes
the extra heading, reference breadcrumb and separate outline link. Both readers
use continuous Lesson content, Unit dropdowns, native zoom and fullscreen content
with only an exit-fullscreen control. Authored media anchors, including repeated
listen/watch instructions, remain in place. Added page captions are removed.

A deliberate downward gesture reaching the loaded Lesson end opens a modal that
animates from below to the center. It offers Complete and next lesson and
Complete and exit, using the existing signed native writer. Completed Lessons
offer next/exit and signed undo. No automatic completion or success toast occurs;
validation/conflict warnings remain. Filters move to the physical upper-right
header, including activity type. Activity ordering/count prose is removed, with
actual previous/next paging retained.

## Workbook access

Revision a4b9d0c21e76 adds only workbook_lesson_pages. Explicit page assignments
reference a native Lesson, the Course Workbook and its revision. The reviewed
full-book SHA256 is c59abc5c39a8cf148f95ee2233f6c559753dccd12f1846ddf61c4461ed8c9da7.
PDF pages 4–63 map to ten Units' A/B/C/D and review Lessons. Vox Pop exercise pages
64–71 map to Unit C Lessons through existing video anchors. Pages 1–3 and 72–87
are unmapped: front matter, audio scripts and answers have no Student route.
The original 87-page PDF is preserved for Teachers.

Each Student HTML/image/media request rechecks active enrollment and academic
ancestors, nested identities, current book revision, active Units and native
Lesson publication. A page assigned to several Lessons is withheld until all
are visible. Replacing a PDF invalidates its map. Only audio/video pinned to an
authorized Workbook page can be served; Teacher PDFs and generic assets remain
private. There is no full-PDF Student download.

The master map has 74 page/Lesson assignments across 50 existing Lessons.
Independent Group copies use explicit Material lineage and keep their Lesson
identities. scripts/map_empower_workbook.py refuses a different PDF, ambiguous
lineage or an existing different map; repeating the exact job is a no-op.
Workbook completion is the same native Lesson progress, with no invented second
progress record. Teachers publish existing Lessons individually or together.

## Evidence and deployment boundary

Local backup preceded the additive migration. Every preexisting table fingerprint
except alembic_version was preserved. Static Python/Jinja/mapper/MySQL DDL review
and JavaScript syntax checks are used; no automated tests were created or run.
Real local browser review observed Course book buttons, Unit dropdowns, the
end-only modal without submitting it, content-only fullscreen, Workbook sections,
playable Workbook audio and an answer-page 404. Browser access was subsequently
disabled in Work settings; remaining browser review requires restored access.

Test already has 13 Units, 88 Lessons and 252 Materials; do not overwrite them.
Its assigned Teacher was active and its activity catalogue empty. The bounded
append prepares three original five-question practice Quizzes and two text
Assignments for Units 1–3, open until the existing term end, using native Teacher
locks and Quiz publication blockers. No attempt, submission, grade, financial,
research or membership data is fabricated.

Deployment verifies the exact committed archive, backs up the current database
and root helper, applies only the additive revision, retains the previous source
release, appends the explicit map and Test practice, and checks health plus the
known owner-accepted readiness exception. No database downgrade, media re-upload,
VPS reboot, SSH/firewall change, Railway operation or release-branch merge occurs.
