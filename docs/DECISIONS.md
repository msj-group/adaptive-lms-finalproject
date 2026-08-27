# Project Decisions

## Group model (Phase 3, Step 7)

**Purpose.** A `Group` represents one actual offering of a `Course` during a
specific `AcademicTerm` -- e.g. "Group A" of "General English" in "Fall 2026".
It is the entity that student enrollments, teacher assignments, schedules,
and attendance attach to. Student enrollments (`Enrollment`) were added
shortly after this step (migration `d6ae31e9754c`, commit `e329e34`), and
teacher assignments (`GroupTeacherAssignment`) were added later still, in
Phase 3, Part 6 (migration `c8e88a1ef64a`) -- see "Group-centered
membership management (Phase 3, Part 6)" below for the full design of both
as they exist today. Schedules and attendance remain future milestones. At
the time this step was written, none of those relationships existed yet;
this step was the database/model foundation only.

**Group -> AcademicTerm.** Many-to-one, required (`academic_term_id`,
`NOT NULL`, indexed FK to `academic_terms.id`). An `AcademicTerm` can have
many `Group`s (`AcademicTerm.groups`).

**Group -> Course.** Many-to-one, required (`course_id`, `NOT NULL`, indexed
FK to `courses.id`). A `Course` can have many `Group`s (`Course.groups`).

**Level is derived through Course, not duplicated.** A `Group` does not have
a `level_id` column. The level is already reachable via
`Group -> Course -> Level`, and a `Course` cannot change level after
creation without an explicit admin action on the course itself. Storing
`level_id` on `Group` as well would let the two disagree (e.g. a course
moved to a different level while a group still pointed at the old level)
with nothing in the schema to prevent it. Reading the level through the
existing relationship (`group.course.level`) keeps the level facts in a
single place.

**Uniqueness.** Group names are **not** globally unique. Two different
`Course`s, or the same `Course` in two different `AcademicTerm`s, may each
have a "Group A" -- this is normal (e.g. the same course repeats every
term). What must be unambiguous is a group name *within one course, in one
term*: `UniqueConstraint(academic_term_id, course_id, name)`. The optional
`code` field follows the same scope:
`UniqueConstraint(academic_term_id, course_id, code)`, with multiple groups
allowed to leave `code` blank in the same term/course (MySQL treats
multiple `NULL`s in a unique index as distinct, matching the existing
`Level.code` / `Course.code` convention).

**Capacity.** Required positive integer, enforced with a database
`CHECK (capacity > 0)` constraint -- the same pattern already used for
`AcademicTerm`'s `start_date < end_date` check, rather than a duplicate
Python-level validator.

**Archiving, not deleting.** `Group` reuses the shared `AcademicStatus`
enum (`active` / `archived`) and has no delete route or method, consistent
with `AcademicTerm`, `Level`, and `Course` -- existing `Enrollment`
history (see "Group-centered membership management (Phase 3, Part 6)"
below) already references a group long after it stops running, and future
Attendance history will do the same.

## Group delete policy (Phase 3, Step 12)

**Decision: no hard delete. Archive (`active` -> `archived`) is the only
lifecycle transition, and it is already implemented (Step 11,
`POST /admin/groups/<public_id>/toggle-status`).**

Reasoning:

- A `Group` is the entity that student enrollments, teacher assignments,
  schedules, attendance, grades, and assignments all eventually reference
  (see the `groups` purpose note above). At the time this decision was
  made, none of those relationships existed in the schema yet, but the
  whole point of `Group` existing before them was to be their stable
  anchor. A hard delete route built then would have needed to be
  revisited (and re-secured) the moment any of those future foreign keys
  landed -- either by blocking deletion once children exist, or by
  cascading, which risks silently destroying enrollment/attendance
  history. Building it then, before there was anything to protect
  against, would have meant designing it twice.

  `Enrollment` (migration `d6ae31e9754c`) and, later, `GroupTeacherAssignment`
  (Phase 3, Part 6, migration `c8e88a1ef64a`) have since landed exactly the
  way this reasoning anticipated: both hold a required, non-cascading
  foreign key to `groups.id`, and neither introduced a cascade-delete or a
  Group-deletion route. The original decision holds unchanged -- Group
  archiving remains the only lifecycle transition.
- This mirrors the decision already made and shipped for `AcademicTerm`,
  `Level`, and `Course`: all three are archive-only, no delete route
  exists for any of them, and every admin list page already reads on
  "Active"/"Archived" rather than existence. `Group` following the same
  rule keeps one consistent lifecycle model across the whole Academic
  Core instead of a special case for one table.
- Nothing observed while building Steps 7-11 changes this: no current
  feature needs to permanently remove a `Group` row, and archiving already
  satisfies every real requirement seen so far (hide it from active use,
  keep the historical record, allow reactivation if it was archived by
  mistake).

No delete route, delete button, or delete confirmation was added anywhere
in the Group admin pages. If a genuine need for permanent removal appears
later (e.g. a compliance/data-retention requirement), it should be
designed at that time against the actual relationships that exist then,
with explicit relationship checks and cascade rules -- not built
speculatively ahead of the data model that would make it safe.

## Student account management (Phase 3, Student Milestone)

**User.public_id.** The `users` table only had an internal numeric `id`.
Student (and every other role's) detail/edit/status/password-reset URLs
must never expose that internal id, so `User` now carries the same
`public_id` (UUID, unique, non-null) pattern already used by
`AcademicTerm`, `Level`, `Course`, and `Group`. Existing rows (the
Administrator) were backfilled with a generated UUID by the migration
before the column was tightened to NOT NULL -- see
`migrations/versions/a23634066372_*.py` for the exact steps.

**Session invalidation (`auth_version`).** Suspending a student, resetting
their password, or changing their login email must kill any session
issued before that action -- otherwise a browser that is still logged in
would keep working after being suspended. `User` gained a small integer
`auth_version` column for this. `User.get_id()` (used by Flask-Login to
build the session cookie) returns `"{id}.{auth_version}"` instead of just
the id; the `user_loader` in `app/__init__.py` parses both parts back out
and rejects the session if the stored version no longer matches the
user's current `auth_version`. `bump_auth_version()` is called on status
toggle, password reset, and email change. This is the smallest change
that fits the existing Flask-Login integration -- no JWT, no server-side
session store, no new framework.

One expected, one-time side effect: every session that existed *before*
this migration was created with the old `get_id()` format (just the raw
id, no version suffix). The user_loader cannot parse that old format and
treats it as invalid, so **every currently logged-in user, including the
Administrator, will be signed out once** the first time they load a page
after this change ships. They simply log in again normally; no data is
affected.

**Password policy for administrator-set student passwords.** Minimum 15
characters, maximum 128, no composition rules (no forced uppercase /
digit / symbol). This favours passphrases over short-but-complex
passwords, following the length-over-composition principle discussed in
guidance such as NIST SP 800-63B -- this is **not** a claim of full
compliance with that document. The minimum was deliberately set above the
more commonly cited 12-character baseline because this application
currently authenticates with a password as the sole factor: there is no
MFA, so the password alone is the entire barrier to an account. Two
things NIST SP 800-63B also recommends are explicitly **not** implemented
yet and are recorded here as future improvements rather than oversights:
blocklisting known-compromised or common passwords, and a password
strength meter/feedback at input time. The maximum exists only to bound
the input size reaching Argon2id, not as a usability restriction.

**No `must_change_password` flag.** A forced first-login password change
would need a student-facing login/dashboard flow to redirect into, and
that does not exist yet. Adding the column now without anywhere to act on
it would be a half-finished control. Recorded here as a follow-up once
the Student dashboard exists: the Administrator currently hands the
temporary password to the student out of band (never by email -- no
email service is approved), and the student keeps using it until an
administrator resets it.

**Student delete policy.** Same reasoning as Groups: student accounts use
the Active/Suspended lifecycle only, there is no delete route or button.
`Enrollment` (migration `d6ae31e9754c`) already references a student long
after they stop attending -- a withdrawn `Enrollment` row is preserved,
never deleted, precisely so that history survives -- and future Attendance,
Grades, Payments, and research records will do the same. Audit/history
records must not be able to silently lose their subject. Permanent
deletion, if ever required, must be designed later as a deliberate
privacy/data-retention workflow evaluated against whatever relationships
exist by then -- not implemented now as ordinary CRUD deletion.

## Group-centered membership management (Phase 3, Part 6)

This section documents `Enrollment` and `GroupTeacherAssignment`, the two
relationships the "Group model" and "Group delete policy" sections above
originally described as future work. Their history is not identical:

- `Enrollment` -- and an original standalone administrator listing page
  for it -- was introduced earlier, by migration `d6ae31e9754c` and commit
  `e329e34`.
- `GroupTeacherAssignment` did not exist before Phase 3, Part 6, which
  introduced it via migration `c8e88a1ef64a` (applied to the development
  database in Part 6E). Migration `c8e88a1ef64a` creates only the new
  `group_teacher_assignments` table -- it does not create, modify, or
  touch the `enrollments` table in any way (see section G below).
- What Part 6 did to `Enrollment` specifically was move its management out
  of that original standalone page and into the Group-centered Manage
  Members workflow described in section A, removing the standalone
  UI/routes entirely (section A and section H).

Both relationships, as they exist today after Part 6, are documented
below.

### A. Group-centered administration

**Decision: Enrollment and Teacher-assignment management live inside each
Group's own page, not on a separate top-level administrator page.**

The earlier Enrollment milestone (migration `d6ae31e9754c`, commit
`e329e34`) introduced the `Enrollment` model together with a standalone
administrator page -- but that page was **read-only**: a single
`GET /admin/enrollments` listing route, its `list.html` template, and an
"Enrollments" navigation item. That milestone did **not** implement any
standalone create, detail, withdraw, or reactivate route -- there was no
way to mutate an Enrollment through the admin interface at all until
Phase 3, Part 6.

Phase 3, Part 6 removed the standalone listing route, its template, and
the navigation item entirely, and introduced the current Group-nested
`POST` routes for Enrollment creation, withdrawal, and reactivation
(`group_enrollment_create/withdraw/reactivate`, section H below). There is
no standalone Enrollment detail page or detail route, before or after
Part 6 -- Enrollment administration (viewing and mutating) now occurs
entirely through each Group's **Manage Members** page
(`GET /admin/groups/<group_public_id>/members`), which also manages that
Group's Teacher assignments in the same place.

This is clearer than a flat, cross-Group list because membership is never
meaningful on its own -- every enrollment decision (is there capacity? is
there an eligible teacher? does this student already hold a conflicting
enrollment elsewhere?) depends on the Group's own Course and AcademicTerm
context. A flat page would either have to repeat that context in every row
or force the administrator to hold it in their head; a Group-scoped page
gets it for free, once, from the URL.

### B. Entity relationships

- `Group` belongs to exactly one `Course` and exactly one `AcademicTerm`
  (see "Group model" above).
- `Level` is not stored on `Group` directly; it is derived through
  `Group -> Course -> Level`, for the same single-source-of-truth reason
  already given in the "Group model" section.
- A Student `User` and a `Group` are connected through `Enrollment`.
- A Teacher `User` and a `Group` are connected through
  `GroupTeacherAssignment`.
- A `Group` can contain many students (many active `Enrollment` rows).
- A student can have historical enrollments in many groups over time (many
  `Enrollment` rows, active and withdrawn, across different groups).
- No teacher is assigned automatically when a `Group` is created --
  `group_teacher_assignments` begins, and stays, empty until an
  administrator explicitly creates an assignment (see section G). The
  normal operational workflow is for an administrator to assign at least
  one eligible teacher before enrolling students (see section C). The
  schema and the Manage Members UI both support multiple active teachers
  on the same Group at once -- there is no database-enforced
  exactly-one-teacher rule, and no cap on active
  `GroupTeacherAssignment` rows per Group.
- A teacher can be assigned to multiple groups (many active
  `GroupTeacherAssignment` rows for the same `teacher_id`, across
  different groups).
- All active teacher assignments on a Group are currently equal: no
  primary-teacher or assistant-teacher role distinction is implemented.
  `GroupTeacherAssignment.status` only distinguishes `active` from
  `removed`, nothing else.

Compact relationship summary (`1 ── *` reads "one side to many side"; each
line is a separate, direct relationship -- there is no direct line between
`Level` and `Group`, only the transitive path through `Course`):

```
AcademicTerm  1 ── * Group
Course        1 ── * Group
Level         1 ── * Course              (Group reaches Level only via Group -> Course -> Level)
Student User  1 ── * Enrollment * ── 1 Group
Teacher User  1 ── * GroupTeacherAssignment * ── 1 Group
```

In words: one `AcademicTerm` has many `Group`s, one `Course` has many
`Group`s, and one `Level` has many `Course`s -- `Group` does not carry its
own `level_id`, so reaching a Group's Level always goes through its
Course. `Enrollment` is the many-to-many join between `Student` and
`Group`: one `Group` has many `Enrollment` rows, and one `Student` has
many `Enrollment` rows across different Groups. `GroupTeacherAssignment`
is the many-to-many join between `Teacher` and `Group`: one `Group` has
many `GroupTeacherAssignment` rows, and one `Teacher` has many
`GroupTeacherAssignment` rows across different Groups.

### C. Teacher prerequisite

**Decision: a Group must have at least one eligible active teacher
assignment before an administrator can add or reactivate a student
enrollment in it.**

"Eligible" is a three-part condition, all required at once:

1. The `GroupTeacherAssignment.teacher_id` references a `User` whose
   `role` is `teacher` (a corrupted assignment referencing any other role
   -- the foreign key cannot forbid this -- never counts).
2. That `User`'s `status` is `active` (a suspended teacher's still-`active`
   assignment does not satisfy the requirement -- a stale assignment to a
   suspended teacher is not eligible).
3. The `GroupTeacherAssignment.status` itself is `active` (a `removed`
   assignment does not count, even to an otherwise-eligible teacher).

This is checked with a single `COUNT` query
(`eligible_active_teacher_count`) shared by both the Enrollment-creation
path and the Enrollment-reactivation path, so the rule cannot drift
between the two.

The same eligibility definition protects removal in the other direction:
removing the last eligible teacher assignment **through the Group
membership route** (`group_teacher_remove`) is blocked while the Group
still has active student enrollments.

**This protection has a real limitation, and it is intentional.** Teacher
account suspension (`teacher_toggle_status`, in
`app/blueprints/admin/teachers.py`) belongs to the independent Teacher
account lifecycle and does not inspect `GroupTeacherAssignment` rows at
all. An administrator can suspend a Teacher account even if that teacher
is the last eligible teacher for a Group that has active student
enrollments. In that situation:

- The existing student enrollments in that Group remain active -- nothing
  automatically withdraws them.
- New enrollment creation and reactivation in that Group are blocked
  (section C's rule above) until another eligible teacher is assigned, or
  an eligible assignment/account is restored.

This is intentional current behavior: urgent account suspension (e.g. a
security incident) is not blocked by academic membership state elsewhere
in the system, and the two lifecycles are deliberately independent. It
means the Group-membership route's last-eligible-teacher protection only
covers actions taken *through that route* -- it is not a system-wide
invariant enforced against every path that can change teacher eligibility.
If a future requirement demands a stronger cross-module guarantee (e.g.
blocking suspension of a Group's last eligible teacher, or a notification
workflow), it must be designed separately as a deliberate decision against
the Teacher-account lifecycle; it is not implemented today.

### D. Enrollment conflict rule

**Decision: a student cannot hold two active enrollments in different
Groups that share the same Course and the same AcademicTerm.**

A student may legitimately be active in several Groups at once -- e.g.
different Courses in the same term, or the same Course repeated across
different terms -- but two active memberships in what is conceptually the
same offering (same Course, same AcademicTerm, different Group) are
disallowed. Historical withdrawn enrollments are preserved and never
block a new or reactivated enrollment on their own; only an *active*
conflicting row does.

This rule is enforced in the service/transaction layer
(`conflicting_active_enrollment` in `app/services/group_memberships.py`),
not as a single-table database constraint. The query joins `Enrollment`
against two aliases of `Group` -- one for the target Group, one for a
candidate conflicting Group -- and compares `Group.course_id` and
`Group.academic_term_id` directly between the two aliases; it does not
need to join or read the `courses` or `academic_terms` tables at all,
since both compared columns already live on `Group` itself. The rule still
cannot be expressed as a simple `UNIQUE` constraint on `enrollments`,
because the values being compared ("does this student have another active
`Enrollment` whose `Group` shares this Group's `course_id` and
`academic_term_id`") live on *related* Group rows, not on the `Enrollment`
row being checked -- a single-table unique index has no way to reach
across that relationship.

`Enrollment` separately keeps a plain database `UniqueConstraint` on
`(student_id, group_id)` -- a simple, single-table guarantee that the same
student cannot get two rows for the *same* Group, independent of and in
addition to the cross-Group Course/AcademicTerm rule above.

### E. Lifecycle and history preservation

- `Enrollment.status`: `active` and `withdrawn`.
- `GroupTeacherAssignment.status`: `active` and `removed`.
- Withdrawing an Enrollment, or removing a GroupTeacherAssignment, is a
  status update -- the row itself, its `public_id`, and its `created_at`
  are preserved, never deleted.
- Reactivating a withdrawn Enrollment or a removed GroupTeacherAssignment
  reuses that same existing row (`status` flips back to `active`) rather
  than inserting a new, duplicate row.
- No hard-delete workflow was introduced for either `Enrollment` or
  `GroupTeacherAssignment`, consistent with the archive-only lifecycle
  already established for `Group`, `AcademicTerm`, `Level`, and `Course`.
- The `UniqueConstraint` on `Enrollment(student_id, group_id)` and on
  `GroupTeacherAssignment(group_id, teacher_id)` prevent a duplicate
  relationship row for the same pair from ever being created -- including
  a duplicate created after the original was withdrawn/removed, which is
  exactly why reactivation reuses the row instead of inserting a new one.

### F. Concurrency and transaction safety

The six membership-mutation routes do not all follow one identical
sequence. What they share, and where they genuinely differ, is documented
route-by-route below.

**Shared Group lock.** Every one of the six routes
(`group_enrollment_create/withdraw/reactivate`,
`group_teacher_assign/remove/reactivate`) calls
`_get_group_locked_or_404()` at some point in its handling. That helper
deliberately calls `db.session.rollback()` -- ending whatever read-only
snapshot an earlier step in the same request may have opened -- and then
locks the current Group row with `SELECT ... FOR UPDATE` in the same
query. Because every one of these six routes takes this same Group lock
before writing anything, they serialize against each other for the *same*
Group: a second request touching that Group's membership blocks until the
first commits or rolls back. This says nothing about routes that do not
call `_get_group_locked_or_404()` -- it does not serialize against them.

**Enrollment creation (`group_enrollment_create`).** First performs an
ordinary, unlocked preview Group lookup and `GroupEnrollmentForm`
validation (friendly early errors: archived Group, ineligible Student,
and so on, all against a possibly-stale read). It then resets the
transaction and locks the Group via `_get_group_locked_or_404()`, and
locks the Student row immediately next, with no ordinary query in
between. There is no existing Enrollment row to lock on a new enrollment
-- the lock order is **Group -> Student -> insert Enrollment**, not
`Group -> Student -> Enrollment`. Teacher eligibility (section C),
capacity, duplicate `(student, group)` membership, and the cross-Group
Course/AcademicTerm conflict (section D) are all rechecked after the
Group and Student locks, against that locked, current data. The database
`UniqueConstraint(student_id, group_id)` remains the final protection
against a duplicate row if a concurrent request still slips past all of
the above.

**Enrollment withdrawal and reactivation
(`group_enrollment_withdraw` / `group_enrollment_reactivate`).** Both
first perform an ordinary, unlocked nested preview lookup (resolve the
Group, then the Enrollment scoped to it and to a Student-role User).
Both then reset the transaction and lock, in order,
**Group -> Student -> existing Enrollment** (`SELECT ... FOR UPDATE` on
all three, the Enrollment lock landing on the *existing* row this time,
not an insert). Reactivation additionally rechecks teacher eligibility,
capacity, Student eligibility (role/active status), and the cross-Group
conflict, all after these three locks.

**Teacher assignment creation (`group_teacher_assign`).** Resets the
transaction and locks the Group **at the very start** of the route --
before `GroupTeacherAssignmentForm` is even constructed. Form validation
(choices query, eligibility checks) then runs as ordinary, unlocked
queries **while that Group lock is already held** -- unlike Enrollment
creation, there is no claim of "no ordinary SELECT between the Group and
Teacher locks" here, because form validation itself issues ordinary
queries in that window. Only after the form validates does the route lock
the selected Teacher row (`SELECT ... FOR UPDATE`) and re-check its role
and active status immediately before inserting the assignment. The
database `UniqueConstraint(group_id, teacher_id)` remains the final
protection against a duplicate row.

**Teacher assignment removal (`group_teacher_remove`).** Resets the
transaction and locks the Group, then reads the target
`GroupTeacherAssignment` and its referenced `User` with **ordinary,
unlocked** queries -- it does not take a `SELECT ... FOR UPDATE` lock on
either the Assignment row or the Teacher row. The held Group lock is what
serializes this route against the other five Group-membership mutation
routes for the same Group; it does not coordinate with, or block on,
independent Teacher account-status changes made through
`teacher_toggle_status` (see section C's limitation above) -- that route
never takes this Group lock. Before marking the assignment removed, the
route checks whether it is the last eligible assignment for the Group
(section C).

**Teacher assignment reactivation (`group_teacher_reactivate`).** Resets
the transaction and locks the Group, reads the existing assignment with
an ordinary (unlocked) query, then locks the referenced Teacher row
(`SELECT ... FOR UPDATE`) before verifying its role and active status and
reactivating the assignment.

**Scope of the concurrency claim.** The goal throughout is to avoid a
stale-snapshot decision under MySQL's default `REPEATABLE READ`
isolation: `SELECT ... FOR UPDATE` always reads the current committed row
regardless of when the transaction's snapshot was opened, while an
ordinary `SELECT` issued afterwards in the same still-open transaction
would otherwise keep reading the earlier snapshot. This is stated
conservatively and only for what the code above actually does:

- The locking strategy is targeted at these six implemented application
  routes, not at the database in general.
- The Student lock (Enrollment creation, withdrawal, reactivation)
  coordinates those three routes against each other for the same student
  -- including the cross-Group conflict check from section D.
- The Group lock coordinates the six routes that call the shared
  `_get_group_locked_or_404()` helper against each other, for the same
  Group.
- MySQL/InnoDB provides the real locking and snapshot behavior described
  above. The project's automated test suite runs against SQLite, which
  has no `SELECT ... FOR UPDATE` syntax and no `REPEATABLE READ` snapshot
  isolation to begin with -- those tests can only verify that the code
  requests the reset and the documented lock order (structure), never
  that a lock actually blocks a concurrent MySQL transaction.
- None of this claims protection against every race involving arbitrary
  hand-written SQL, an external writer to the same database, or a route
  that does not take part in this locking convention. It specifically
  does **not** claim that every Teacher-assignment mutation locks the
  Teacher row (`group_teacher_remove` does not), and it does **not**
  claim that a Group can never end up with active students and zero
  eligible teachers through every possible action in the application
  (`teacher_toggle_status` can produce exactly that, per section C).

### G. Database migration

Migration `c8e88a1ef64a` (applied to the development database in Part 6E)
creates only the `group_teacher_assignments` table: its columns, its
foreign keys to `groups.id` and `users.id`, a unique `public_id`, and a
unique `(group_id, teacher_id)` pair, plus supporting indexes. It does not
assign any teacher automatically, and it does not modify any existing
`users`, `groups`, or `enrollments` row. The table begins, and stays,
empty until an administrator explicitly creates the first assignment
through the application.

### H. Security and routing

- Every membership mutation (`group_enrollment_create/withdraw/
  reactivate`, `group_teacher_assign/remove/reactivate`) is a nested route
  under `/admin/groups/<group_public_id>/...` and accepts only `POST`.
- The existing `roles_required(UserRole.ADMINISTRATOR.value)` decorator
  and the project's standard Flask-WTF CSRF protection apply to all of
  them, unchanged from the rest of the admin section.
- URLs identify a Group, Enrollment, or GroupTeacherAssignment by
  `public_id` (UUID), never by internal numeric primary key -- the same
  convention already used for `AcademicTerm`, `Level`, `Course`, `Group`,
  and `User`.
- The standalone Enrollment admin page and its routes are intentionally
  absent (see section A above); there is no alternate path to the same
  mutations outside a Group's own nested routes.
