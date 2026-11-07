# Continuous Unit reader and explicit Unit completion

## Owner-approved behavior

Navigate by Unit instead of Lesson. The owner explicitly selected completing all
published Lessons in the Unit when pressing Complete and next unit. Preserve
the immersive reader, book choices, page-relative media, Teacher additions and
publication guards. Continue on master and the current VPS only; no migration or
content import is needed.

## Reader and files

The existing authorized Lesson/Workbook entry URLs now compose the containing
Unit. This preserves existing bookmarks, file routes and the closed learning-page
family; it does not create artificial per-Lesson page requests or research views.
The header/outline/end card use Unit titles and destinations. Every visible
published Lesson is resolved through the existing own-enrollment hierarchy query.
A publication change during composition fails closed. Only the actual entry
Lesson retains its existing opening record; no sibling openings are fabricated.

Combine authorized book page ranges, displaying a shared physical page once per
Unit. Each main-book image keeps a real owning Lesson's guarded page URL, and
each media marker/material keeps its own Lesson's guarded media URL. Repeated
media placements are retained. Workbook pages use the existing all-owners-visible
map and answer-key restrictions. Course content without page pins and Teacher
additions remain separate collections. No file, pin coordinate or content row
is modified by composition.

## Completion integrity

One purpose-specific timed token binds the actor, Group, Unit, selected book and
the exact published Lesson/version snapshot. CSRF and existing nested role/access
guards remain authoritative. A real user POST completes the snapshot atomically
under AcademicTerm -> Level -> Course -> Group -> Student -> active Enrollment
-> Unit -> all Lessons -> release rows -> current-episode progress rows. Recheck
the locked hierarchy, membership, current released Lesson set and every changing
version before writing. A stale/publication conflict writes nothing. Already
completed Lessons retain their completion timestamps/versions. New progress rows
bind the locked current Enrollment and do not fabricate opening timestamps.

Progress remains stored per Lesson, shared between Student Book and Workbook as
before; the explicit Unit action completes all published Lessons. No new Unit
progress table/counter or historical rewrite is introduced. The actual Unit POST
uses one existing server-confirmed completion-action outcome, not fabricated
per-Lesson client events. Legacy open single-Lesson forms retain their existing
token/write contract. The destination is computed from the current own published
outline and rechecks authorization on GET. The stable fullscreen root and existing
authorized-page loader retain fullscreen through Next unit. Completion is never
inferred from scrolling, image loading or media playback.

## Evidence and limits

Static production-factory/mapper, Python, JavaScript and all-template compilation
run with database connections blocked. A separate bounded local MySQL read-only
audit inspected existing authorized Unit contexts in both books: two Units with
six published Lessons each; each main Unit has 12 unique pages, while Workbook
has seven, with pins and owning Lesson URLs preserved. Signed snapshots match
native publication; no completion or opening transaction is executed by the audit.

No automated test package, fake authentication/progress, migration, business-data
write, course reimport, VPS reboot or SSH/firewall change is part of verification.
The new atomic write path is source-reviewed, not represented as a real Student
completion acceptance test. Work browser use remains disabled; the owner reviews
device behavior. Private deployment receipts establish the hosted source hashes,
unchanged schema and recoverable prior source. Readiness 503 remains its existing
separate owner-accepted exception.
