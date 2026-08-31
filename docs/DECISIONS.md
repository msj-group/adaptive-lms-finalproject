# Project Decisions

## Group model (Phase 3, Step 7)

**Purpose.** A `Group` represents one actual offering of a `Course` during a
specific `AcademicTerm` -- e.g. "Group A" of "General English" in "Fall 2026".
It is the entity that student enrollments, teacher assignments, schedules,
and attendance attach to. Student enrollments (`Enrollment`) were added
shortly after this step (migration `d6ae31e9754c`, commit `e329e34`),
teacher assignments (`GroupTeacherAssignment`) were added in Phase 3,
Part 6 (migration `c8e88a1ef64a`) -- see "Group-centered membership
management (Phase 3, Part 6)" below for the full design of both -- and
recurring weekly schedules (`Schedule`) were added in Phase 3, Part M08
(migration `adf4b5691a7a`) -- see "Recurring Group schedules (Phase 3,
Part M08)" below. Attendance remains a future milestone. At the time this
step was written, none of those relationships existed yet; this step was
the database/model foundation only.

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
single place. Phase 3, Part 7B1 hardened this further: once any `Group`
currently references a `Course`, that `Course`'s `level_id` is frozen --
so the "explicit admin action on the course itself" is itself blocked
while the disagreement it would create is possible (see "Course-level
identity integrity (Phase 3, Part 7B1)" below).

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

  `Enrollment` (migration `d6ae31e9754c`), `GroupTeacherAssignment`
  (Phase 3, Part 6, migration `c8e88a1ef64a`), and `Schedule` (Phase 3,
  Part M08, migration `adf4b5691a7a`) have since landed exactly the way
  this reasoning anticipated: each holds a required, non-cascading
  foreign key to `groups.id`, and none introduced a cascade-delete or a
  Group-deletion route. Only Attendance/Grades remain future. The
  original decision holds unchanged -- Group archiving remains the only
  lifecycle transition, and archiving a Group never cascades to its
  Schedule rows (see "Recurring Group schedules (Phase 3, Part M08)").
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
first commits or rolls back.

`_get_group_locked_or_404()` is only this Blueprint's thin 404 wrapper
around the shared, Flask-independent `lock_group_for_write` /
`lock_group_in_open_transaction` primitives in
`app/services/group_transactions.py`. Group edit and Group status toggle
(Phase 3, Part 7B0 -- `app/blueprints/admin/groups.py`) lock the **same**
Group row through the **same** primitives, via their own route-local
wrappers (`_lock_group_or_404`, `_lock_group_in_open_transaction_or_404`),
so they serialize against these six routes, and against each other, for
the same Group -- see "Group edit integrity (Phase 3, Part 7B0)" below.
What the Group lock says nothing about is only a path that takes neither
wrapper nor the underlying service at all -- e.g. an independent
Teacher-account status change through `teacher_toggle_status`, which
never locks the Group (section C's limitation above).

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
- The Group lock coordinates, for the same Group, the six membership
  routes here **and** Group edit / Group status toggle (Part 7B0) --
  every route that takes the shared `lock_group_for_write` /
  `lock_group_in_open_transaction` service, through whichever route-local
  404 wrapper it calls it. `group_edit` additionally locks its submitted
  target Course first, in a fixed `Course -> Group` order (Part 7B1), so a
  Group retarget also serializes against a concurrent Course-level change;
  see "Course-level identity integrity (Phase 3, Part 7B1)".
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

## Group edit integrity (Phase 3, Part 7B0)

How `group_edit` (`POST /admin/groups/<public_id>/edit`) and
`group_toggle_status` protect a Group's academic identity, its capacity,
and against stale-form overwrites. This Part changed route, service, and
template logic only -- **no model change and no database migration**.

**Part M07C2 amendment (Group status ownership).** Since M07C2, Group
lifecycle status is owned *solely* by `group_toggle_status`: `GroupForm`
and the create/edit HTML carry no `status` control, `group_create`
always stores `active` (decided server-side), and `group_edit` never
reads or writes `status`. The signed edit snapshot dropped from seven
fields to six (`status` removed), so a status-only toggle no longer
stales an open edit form and that edit can never overwrite the toggled
status. The subsections below are updated to reflect this; M07C2 was
also route/form/template-only -- **no model change and no migration**.

**Part M08 amendment (Schedule history freezes identity too).** Since
M08, a Group's academic identity (`academic_term_id` / `course_id`) is
editable **only while the Group has no `Enrollment`, no
`GroupTeacherAssignment`, and no `Schedule` history** -- any one row of
any of the three, in any status, freezes it. `group_edit` now derives
`has_history` from `_group_identity_frozen`
(`app/blueprints/admin/groups.py`), which is
`group_has_membership_history(group_id) or
group_has_schedule_history(group_id)`. `group_has_membership_history`
itself is unchanged and still means *only* Enrollment/GroupTeacherAssignment
(section B); the Schedule half lives in
`group_has_schedule_history` (`app/services/schedule_queries.py`). The
reasoning is identical to section B's: every `Schedule` effective range
was authored against the Group's *current* `AcademicTerm`, so retargeting
the Group would silently reinterpret it. The user-facing message and the
locked-identity form notice both now read "enrollment, teacher-assignment,
or schedule history". Re-submitting the Group's own current Term + Course
stays exempt. M08 also changed route/service/template logic here (plus
the one new `schedules` table) -- see "Recurring Group schedules
(Phase 3, Part M08)" for the whole Part.

### A. Group identity fields and when they become immutable

- A Group's **academic identity** is the pair (`academic_term_id`,
  `course_id`) -- the same two fields the "Group model" and
  "Group-centered membership management" sections already call its
  identity. Level is not one of them; it is still derived through
  `Group -> Course -> Level`.
- Those two fields are **freely editable while the Group has no
  Enrollment, teacher-assignment, or Schedule history at all** (the
  Schedule half added in M08 -- see the M08 amendment above), and
  **immutable once any such history exists**.
- **Non-identity fields stay editable regardless of history:** `name`,
  `code`, and `capacity` can always be changed (subject to their own
  validation -- uniqueness, the capacity rule in section C). `status` is
  **not** editable here at all since M07C2 -- it is changed only through
  `group_toggle_status`.
- Re-submitting the Group's own current `academic_term_id`/`course_id`
  (no actual change) is always allowed even when the identity is frozen,
  so an Administrator can still edit the non-identity fields on a Group
  that has history.
- The rule is `_group_identity_change_error`
  (`app/blueprints/admin/groups.py`), shared by the early pre-lock
  friendly check and the authoritative post-lock recheck so the two
  cannot drift. Its `has_history` argument comes from
  `_group_identity_frozen` (membership history **or** Schedule history);
  the function itself only decides the unchanged-current-values exemption
  and the message.

### B. What counts as Enrollment or teacher-assignment history

`group_has_membership_history(group_id)`
(`app/services/group_memberships.py`) is the **membership-only** helper:
it returns true if **any** `Enrollment` **or** `GroupTeacherAssignment`
row exists for the Group, deliberately **regardless of**:

- **status** -- a `withdrawn` Enrollment or a `removed`
  `GroupTeacherAssignment` counts exactly like an `active` one; and
- **the referenced user's role** -- a malformed row whose
  `student_id`/`teacher_id` points at a non-Student / non-Teacher `User`
  (the foreign key to `users` cannot forbid this) still counts.

It does **not** look at `Schedule`. Since M08 the identity freeze uses
`_group_identity_frozen`, which ORs this helper with
`group_has_schedule_history` (`app/services/schedule_queries.py`, true if
**any** `Schedule` row exists for the Group, active or archived, on the
same "history is history" principle). The split is deliberate:
`group_has_membership_history` keeps its precise membership meaning for
every other caller, and only the identity-freeze call site combines the
two.

Every one of those rows was created under this Group's Course/Term
identity at the time. The same-Course-same-Term Enrollment conflict rule
(`conflicting_active_enrollment`, section D of the Part 6 section above)
reads the Group's *current* `course_id`/`academic_term_id`, not the value
in force when each row was written, and every `Schedule` effective range
is validated against the Group's *current* `AcademicTerm`. Retargeting a
Group with history would retroactively change what those rows mean. This
is intentionally a looser test than the `active`/eligible counts used for
capacity and teacher eligibility, and must stay looser.

### C. Capacity enforcement

On edit, the new `capacity` may not be set below the Group's **current
active student count**, computed by `active_student_enrollment_count`
after the lock. That count only includes `active` Enrollment rows whose
referenced `User` is really a Student: a malformed `active` Enrollment
referencing a non-Student never inflates it, while a suspended Student's
still-`active` Enrollment still occupies its seat. The database
`CHECK (capacity > 0)` constraint (see "Group model") remains the floor.

### D. Signed Group edit snapshot (stale-form protection)

- **Purpose.** The Group row lock only serializes two transactions that
  overlap in time. It does nothing about a form an Administrator opened
  minutes ago and submitted after someone else's edit already committed
  and moved on -- the two requests never overlap, so no lock contention
  catches it. A signed snapshot of the Group's persisted editable state
  at render time, re-verified against the *locked, current* row after
  the fresh lock, catches that instead. No model or migration is needed
  because the snapshot lives only in the rendered form, signed
  (itsdangerous `URLSafeSerializer`, the app `SECRET_KEY`, salt
  `admin.group-edit-snapshot.v1`) so it cannot be forged into claiming
  an original state that never existed.
- **Included fields (6):** `academic_term_id`, `course_id`, `name`,
  `code`, `capacity` -- every field `group_edit` can write -- plus
  `public_id` to bind the token to one Group. `status` is **excluded**
  (M07C2): `group_edit` no longer reads or writes it.
- **Stale = reject.** A token that is missing, empty, invalidly signed,
  wrong-shaped (including a pre-M07C2 seven-field token), signed for a
  different Group, or whose six values no longer match the locked Group
  is stale. A completed Group **status toggle** is deliberately *not* a
  stale trigger: `status` is not compared, so an edit form opened before
  a toggle stays valid as long as its six editable fields still match,
  and applying that edit leaves the toggled status untouched.
- **Stale-form PRG behavior.** The snapshot check runs *before* the
  `GroupForm` is constructed and before any WTForms field validation,
  and is never skipped because another field is also invalid. A stale
  token is rejected with a Post/Redirect/Get to a plain GET of the edit
  page (`_redirect_stale_group_edit`), discarding every submitted value
  and rendering the current persisted values with a fresh, correctly
  paired token. A fresh token is **never** paired with stale/attempted
  values in the same response -- doing so would let an unmodified
  resubmission slip past the next staleness check. For the same reason,
  an ordinary WTForms failure or a business-rule rejection (identity,
  capacity, `IntegrityError`) re-embeds the *original* submitted token
  unchanged, so if the Group changes again before the next submission
  that same token is correctly caught as stale then.
- **Why locking and stale-form protection solve different problems.**
  The lock stops two concurrent transactions from interleaving; the
  snapshot stops a non-overlapping, time-separated form from overwriting
  a change it never saw. Both are kept because neither covers the
  other's case. Dropping `status` from the snapshot is safe *only*
  because `group_edit` no longer reads or writes it (M07C2) -- the
  status-toggle-ownership and stale-form concerns are separate, and the
  snapshot still guards the six fields the edit does write.

### E. Group locking through the shared transaction service

- `group_edit` and `group_toggle_status` lock the Group through the same
  Flask-independent primitives every Enrollment/GroupTeacherAssignment
  mutation uses -- `lock_group_for_write` /
  `lock_group_in_open_transaction` in
  `app/services/group_transactions.py` -- each via a thin route-local
  404 wrapper (`_lock_group_or_404`,
  `_lock_group_in_open_transaction_or_404` here;
  `_get_group_locked_or_404` in `group_members.py`). See section F of the
  Part 6 membership section above for how this makes Group edit, Group
  status toggle, and the six membership routes serialize for the same
  Group.
- **`group_edit` lock order (as extended by Part M07C3) is
  `AcademicTerm(s) -> Level(s) -> Course(s) -> Group`.** It locks the
  union of the Group's current and submitted-target AcademicTerm, Level
  and Course rows (deduplicated, ascending id within each type) via
  `lock_academic_hierarchy` -- which owns the one deliberate reset --
  then the Group with `lock_group_in_open_transaction` (no second reset).
  `group_toggle_status` now uses the same `AcademicTerm -> Level ->
  Course -> Group` chain (and, for a reactivation, extends it with User
  and relationship rows). See "Guarded academic lifecycle (Phase 3,
  Part M07C3)" below for the full order and the race-safety table.
- Staleness, identity, and capacity are all rechecked after the locks
  against freshly queried data; nothing from the pre-lock preview or
  form validation is trusted. No Group field is assigned until every
  check passes, so a rejection never leaves a partial update -- and
  `group.status` is never among the assigned fields (M07C2), so a
  status toggle that committed while this form was open survives intact.
  An `IntegrityError` at commit is caught, rolled back, and reported
  with a generic message -- no SQL, parameters, or driver text reaches
  the user.

### F. Group edit/status and membership-route coordination today

- Group edit <-> Group status toggle: still serialize on the shared
  Group lock (concurrency), but since M07C2 a completed status toggle
  does **not** make an open edit form stale, and the edit -- which never
  writes `status` -- cannot overwrite the toggled value. The two routes
  no longer contend over `status` at all: the toggle solely owns it.
- Group edit / status toggle <-> the six membership routes: all take the
  same Group row lock, so they serialize for the same Group.
- Group edit <-> Course edit / Course status toggle: `group_edit`,
  `course_edit` and `course_toggle_status` all lock the relevant Course
  row (`group_edit` and `course_edit` through
  `lock_academic_hierarchy` / `lock_course_in_open_transaction`, which
  delegate to the same `course_transactions.py` primitive). So a Group
  edit serializes on that Course row against a concurrent Course edit or
  Course status toggle. This is transaction serialization on the Course
  row only -- a Course status change touches none of the six Group
  snapshot fields, so it never makes a Group edit form stale.
- Group edit <-> Course reordering (`move-up` / `move-down`): the Course
  reorder routes currently have no equivalent explicit application
  transaction primitive, so no full serialization is claimed here; this
  stays the low-risk cosmetic concern recorded in the Course section's
  limitations.
- Group edit takes no Teacher or Student row locks -- the membership
  routes own those.

### G. Honest limitations

- The automated suite runs on SQLite, which has no
  `SELECT ... FOR UPDATE` and no `REPEATABLE READ` snapshot isolation.
  The structural tests prove only that the code *requests* the
  deliberate rollback and the locks in the documented order -- never
  that a lock actually blocks a concurrent transaction. Real blocking
  and isolation hold only on MySQL/InnoDB, and no isolated MySQL test
  database exists in this project to verify them.
- M07C2 centralized Group status *ownership*; the guarded-hierarchy
  wiring (ancestor archive guards, parent-first create/retarget/
  reactivation, the Group reactivation guard, archived-parent form
  filtering, the archived-Group conflict-rule change) landed together in
  **M07C3** -- see "Guarded academic lifecycle (Phase 3, Part M07C3)"
  below.
- Attendance remains a future module; nothing here implements or
  presumes it.

## Course-level identity integrity (Phase 3, Part 7B1)

How `course_edit` (`POST /admin/courses/<public_id>/edit`) protects a
Course's `level_id`, and how Group creation/retargeting serializes with
Course-level changes. Like Part 7B0, this Part changed route, service,
and template logic only -- **no model change and no database
migration**.

### A. Policy A -- exactly as implemented

- `Course.level_id` **may change only while no current Group references
  the Course.** If any Group's `course_id` currently points at this
  Course, `level_id` is frozen.
- **Active, archived, empty, and historically used Groups all count**
  while they currently reference the Course. Unlike Group's own identity
  freeze (which waits for *that one Group's* Enrollment, teacher-assignment,
  or -- since M08 -- Schedule history), a single Course can back many
  Groups at once, so moving its Level would silently reinterpret all of
  them together.
- **Same-Level submission remains allowed.** Posting the Course's own
  current `level_id` back (no actual change) is always accepted,
  regardless of any Group reference.
- **Title, code, and description remain editable** on a referenced
  Course; this rule never freezes them.
- **If every legitimately retargetable Group moves away, the Course may
  move again**, because no historical Course-reference audit trail
  exists. `course_has_group_reference` (`app/services/course_integrity.py`)
  answers "does any Group reference this *now*", not "has one ever". If
  every Group that used to reference the Course is individually
  retargeted away (each only ever permitted while that Group itself had
  no Enrollment, teacher-assignment, or Schedule history), nothing
  derives the Course's Level any more and it may move. This is a
  deliberate, accepted consequence -- there is no place that records a
  Course's past Level associations.
- The correction path for a misplaced referenced Course is: archive it,
  create a new Course under the correct Level.
- The rule is `_course_level_change_error`
  (`app/blueprints/admin/courses.py`), shared by the early pre-lock
  check and the authoritative post-lock recheck.

### B. The scalar Course-reference `EXISTS` check

`course_has_group_reference(course_id)` issues a single scalar SQL
`EXISTS` query -- `SELECT EXISTS (SELECT groups.id FROM groups WHERE
groups.course_id = :id)` -- never a `COUNT`, never a Python loop over
loaded rows, never a materialized `Group`. Only existence matters.

### C. Signed Course edit snapshot -- fields and exclusions

Mirrors the Group edit snapshot (section D above), salt
`admin.course-edit-snapshot.v1`.

- **Included fields (5):** `public_id`, `level_id`, `title`, normalized
  `code`, normalized `description`.
- **Excluded -- `status`:** `course_edit` never writes it (the separate
  `course_toggle_status` route owns it), so a completed Course status
  toggle does **not** make an open Course edit form stale.
- **Excluded -- `display_order`:** a same-Level edit never writes it,
  while a valid Level move intentionally computes a *new* destination
  order (`_next_display_order`, after the lock) rather than preserving
  the snapshotted value.
- Same stale-form PRG behavior, same "never pair a fresh token with
  attempted values", and same original-token re-embed on an
  ordinary/business rejection as Group edit.

### D. Course locking and lock order

> Part M07C3 extended every lock chain below with the ancestor rows above
> the Course (`AcademicTerm` for Group routes, `Level` for all of them),
> via `lock_academic_hierarchy` -- which now owns the single deliberate
> reset. The `Course -> Group` guarantees described here are unchanged;
> they are just the tail of a longer chain. See "Guarded academic
> lifecycle (Phase 3, Part M07C3)".

- **Course edit and status toggle** both lock the Course (now preceded by
  its Level) through the shared `course_transactions.py` primitives, so a
  Course edit and a Course status toggle serialize for the same Course.
- **`group_edit`** locks the union of the current + target Course rows
  (preceded by their Levels and the Terms) *before* the Group
  (`lock_group_in_open_transaction`, no second reset).
- **`group_create`** locks `AcademicTerm -> Level -> Course` before
  inserting the new Group that references them.
- **How Group creation/retargeting serializes with Course-Level
  changes.** A `group_create` or identity-changing `group_edit` holds
  `SELECT ... FOR UPDATE` on the target Course row; `course_edit`'s
  Level-change recheck runs only after it holds `SELECT ... FOR UPDATE`
  on that same row. Neither operation makes its decision from a stale
  target-Course read -- that is the whole guarantee, and the two
  outcomes are **asymmetric**:
    - If `group_create` / identity-changing `group_edit` takes the
      Course lock and commits the Group reference first, the later
      Course-Level change rechecks current Group references
      (`course_has_group_reference`) against the committed row and is
      **rejected**.
    - If the Course-Level change takes the Course lock and commits
      first, the later Group create/retarget locks and reads the Course
      in its **new** Level and **may proceed** -- it is not rejected
      merely because the Course moved; the new Group simply references
      the Course at its current Level.
- **Explicit locking is the contract.** This serialization is an
  application-level guarantee from the explicit `SELECT ... FOR UPDATE`
  calls on the Course row, not reliance on any InnoDB foreign-key side
  effect. Per `course_transactions.py`'s module docstring: a Group
  insert or update that references a Course may interact with locks on
  the referenced Course index record while InnoDB checks the
  foreign-key constraint, depending on the operation and isolation
  behaviour, but that engine-level behaviour is not the application's
  concurrency contract and is not relied upon. Course-to-Course
  operations (two `course_edit` submissions, or an edit racing a status
  toggle or a reorder) do not involve the `groups.course_id` foreign
  key at all; their coordination depends on the explicit application
  locks where implemented (`course_edit` and `course_toggle_status` both
  lock `Level -> Course` through the shared `course_transactions.py`
  primitives) and on ordinary database write locking otherwise.
- **Safe `IntegrityError` handling, no partial mutation.** Every rule is
  rechecked post-lock; no field is written until all checks pass, so a
  rejection leaves no partial mutation. An `IntegrityError` at commit is
  caught, rolled back, and reported generically with no SQL or driver
  text.

### E. Honest limitations

- SQLite tests validate the requested structure (deliberate rollback,
  lock calls, lock order) but do **not** prove MySQL/InnoDB blocking or
  isolation behavior. That holds only on MySQL/InnoDB, and no isolated
  MySQL test database exists in this project to verify it directly.
- Explicit `SELECT ... FOR UPDATE` on the Course row is the application
  contract; implicit foreign-key locking is not relied upon.
- Concurrent Course/Level **reordering** (`move-up` / `move-down`, and
  the destination-order computation on a Level move) remains a separate,
  low-risk, cosmetic concern. It is not claimed to be fully serialized;
  a rare race there can only produce a display-order oddity, correctable
  by reordering again.
- The archive-policy interactions between `Course`, `Level`,
  `AcademicTerm` and their active descendants are now **implemented** by
  the guarded hierarchy -- see "Guarded academic lifecycle (Phase 3,
  Part M07C3)" below. Parts 7B0/7B1 themselves did not add those guards;
  M07C1 built the shared query/lock foundations and M07C3 wired them.
- Attendance remains a future module.


## Guarded academic lifecycle (Phase 3, Part M07C3)

Completes milestone M07: the `AcademicTerm -> Level -> Course -> Group`
hierarchy now has *guarded* lifecycle transitions. Built on the M07C1
query/lock foundations and the M07C2 Group-status ownership, this Part
wired the archive guards, parent-first rules, and the Group reactivation
guard together in one atomic change. **No model change and no database
migration** -- route, form, service, and template logic only. Nothing
here hard-deletes, cascades, or automatically repairs legacy rows.

### A. General policy

- Guarded hierarchy, not cascade. Archiving/reactivating one entity
  never automatically archives, withdraws, removes, reactivates, or
  rewrites any descendant. Unsafe transitions are *blocked* until the
  administrator puts the related entities into a valid state.
- No hard deletion of `AcademicTerm`, `Level`, `Course`, `Group`,
  `Enrollment`, or `GroupTeacherAssignment` -- unchanged.
- Pre-existing inconsistent legacy rows (e.g. an active Group already
  under an archived Course) are **not** auto-repaired. The guards only
  stop *new* inconsistency; a metadata-only edit that re-submits the
  entity's unchanged current parent is always allowed so a legacy row
  can still be corrected.
- An archived Group's Enrollment and GroupTeacherAssignment rows remain
  a frozen historical closure roster; membership management for an
  archived Group stays read-only (unchanged from Part 6 / 7B0).

### B. Exact archive blockers

Held under a `SELECT ... FOR UPDATE` on the entity being archived (its
own toggle route locks it; every child mutation that could add an active
descendant locks the same row via `lock_academic_hierarchy`, so they
serialize):

- **AcademicTerm** archive is rejected if any **active Group** directly
  references it (`academic_term_has_active_group`).
- **Course** archive is rejected if any **active Group** directly
  references it (`course_has_active_group`).
- **Level** archive is rejected if **either**: any **active Course**
  directly references it (`level_has_active_course`), **or** any
  **active Group** references *any* Course of that Level **regardless of
  that Course's own status** (`level_has_active_group` -- an active Group
  under an archived Course still blocks the Level).

A rejected archive leaves every row unchanged, rolls back and releases
its locks, flashes a clear Administrator-facing message, and never
exposes SQL/driver text. `AcademicTerm` and `Level` reactivation is
never blocked (they are the roots of their subtrees).

### C. Parent-first creation, retargeting, reactivation

All server-authoritative (checked on locked rows); form filtering is a
usability aid only.

- A **Course** cannot be *created* under an archived Level, nor *moved*
  to a different archived Level. Reactivating a Course requires its
  Level active. Submitting the Course's unchanged current level_id is
  not a move and is exempt.
- A **Group** cannot be *created* unless its AcademicTerm, Course, **and**
  the Course's Level are all active. It cannot be *retargeted* to an
  archived AcademicTerm, an archived Course, or a Course under an
  archived Level. Reactivating a Group requires all three ancestors
  active. A metadata-only edit (unchanged current term **and** course)
  is exempt -- it never permits retargeting elsewhere or reactivating
  beneath an archived ancestor.

### D. Group reactivation guard

Before an archived Group becomes active again, `group_toggle_status`
locks `AcademicTerm -> Level -> Course -> Group`, verifies the three
ancestors are active, then locks every relevant `User` row (ascending
numeric id, students and teachers merged) followed by every relevant
`Enrollment` and `GroupTeacherAssignment` row (ascending id), and checks
against that locked data:

1. Count active valid Student enrollments (referenced User has the
   Student role). A **suspended Student's** active Enrollment is
   retained and still occupies a seat.
2. If that count is `0`, reactivation proceeds -- no capacity or teacher
   check, and no conflict check.
3. Otherwise the count must be `<= group.capacity`.
4. And at least one **eligible active teacher assignment** must exist:
   assignment status `active`, referenced User role `teacher`, and that
   Teacher account `active`. A **suspended Teacher's** assignment is
   retained but does not satisfy the requirement.
5. And no active-enrolled Student may have a **conflicting active
   enrollment** in another *active* Group for the same Course +
   AcademicTerm.

Any failure leaves the Group archived and modifies no row. Teacher
account suspension stays independently allowed -- this guard is a
point-in-time eligibility check at reactivation, not a permanent
coupling (same principle as the existing Part 6 section C limitation).

### E. Operational conflict change

`conflicting_active_enrollment` gained one filter: the conflicting Group
must itself be `active`. An archived Group's active Enrollment rows are
a closure record, so they no longer block a new/reactivated enrollment,
or a Group reactivation, in another active Group for the same Course +
AcademicTerm. Every other conflict dimension is unchanged. This shipped
in the same atomic Part as the reactivation guard.

### F. Global lock order and same-type rule

```
AcademicTerm -> Level -> Course -> Group
  -> User rows (ascending numeric id)
  -> relationship rows (Enrollment then GroupTeacherAssignment, ascending id)
```

Within one entity type, unique numeric ids are locked in ascending
order. `app/services/academic_hierarchy_transactions.py`
(`lock_academic_hierarchy`) owns the single deliberate transaction reset
and the AcademicTerm/Level/Course portion; it delegates to the per-entity
`*_in_open_transaction_by_id` primitives so the `SELECT ... FOR UPDATE`
never drifts. Every later lock joins the same open transaction -- no
reset between locks. Non-locking preview reads only discover which ids to
lock; after locking, existence, relationships, statuses, the signed
snapshot, capacity, and membership state are all rechecked, and a
previewed relationship that changed (e.g. a Course whose Level moved) is
rejected safely rather than continued with the wrong ancestor set. The
resulting lock graph has no reverse-order path.

Race-safety, route by route:

| Route | Locks (in order) | Guard |
|---|---|---|
| `academic_term_toggle_status` | AcademicTerm | archive blocked by active Group |
| `level_toggle_status` | Level | archive blocked by active Course **or** transitive active Group |
| `course_create` | Level | Level must be active |
| `course_edit` | Level(s) (source+target, asc id) -> Course | snapshot + Course identity (7B1) + target Level active on a move |
| `course_toggle_status` | Level -> Course | archive blocked by active Group; reactivation needs active Level |
| `group_create` | AcademicTerm -> Level -> Course | all three ancestors active |
| `group_edit` | AcademicTerm(s) -> Level(s) -> Course(s) (union of current+target, asc id) -> Group | snapshot + identity-history (7B0) + capacity + parent-first on a retarget |
| `group_toggle_status` (archive) | AcademicTerm -> Level -> Course -> Group | none (always allowed; roster untouched) |
| `group_toggle_status` (reactivate) | ... -> Group -> User(s, asc id) -> Enrollment/Assignment rows (asc id) | ancestors active + section D roster guard |

Existing membership routes (`group_enrollment_*`, `group_teacher_*`)
keep their Group -> User -> relationship locking unchanged; they never
lock an ancestor, so they add no reverse path.

### G. Honest limitations

- SQLite (the test backend) has no `SELECT ... FOR UPDATE` and no
  REPEATABLE READ snapshot isolation. The structural tests prove only
  the *requested* lock set and order and the deliberate single reset --
  never that a real InnoDB lock blocks a concurrent transaction. That
  guarantee holds only on MySQL/InnoDB, and no isolated MySQL test
  database exists in this project; MySQL concurrency checks (Term-archive
  vs concurrent Group insert, Group-reactivate vs concurrent conflicting
  enrollment, Level-archive vs concurrent Course create, a deadlock
  probe over the fixed order) would eventually be needed there.
- Course/Level **reordering** (`move-up` / `move-down`) still has no
  explicit transaction primitive -- unchanged low-risk cosmetic concern.
- Legacy inconsistent rows are left as-is; a separate read-only
  consistency report against the real database was not built here.
- Attendance remains a future module.


## Recurring Group schedules (Phase 3, Part M08)

Administrator management of recurring **weekly** Group meeting slots.
Adds one model (`Schedule`) and one additive migration
(`adf4b5691a7a`, `Revises: c8e88a1ef64a`) that creates only the
`schedules` table -- no existing table is touched. Also extends the
Group-identity freeze and the AcademicTerm date-edit path (route/service
only, no schema change beyond the new table).

### A. Model -- `Schedule` as a child of `Group`

`Schedule` belongs to exactly one `Group` (`group_id`, `NOT NULL`,
indexed, plain non-cascading FK to `groups.id`). Like `Enrollment` and
`GroupTeacherAssignment` it duplicates **nothing** the Group already
determines -- Course, Level, Academic Term, Students and Teachers are all
reachable through `schedule.group`, so none of them is stored again.

Columns: `BigInteger` internal `id`; `String(36)` unique `public_id`
(UUID); `group_id`; `day_of_week` (integer); `start_time` / `end_time`
(`TIME`); `effective_start_date` / `effective_end_date` (`DATE`);
optional normalized `location` (`String(255)`, `NULL` = not recorded);
`status` (`String(32)`, reuses the shared `AcademicStatus`
`active`/`archived`, indexed); UTC `created_at` / `updated_at` following
the existing project convention.

**Weekday convention.** `day_of_week` is an integer, **Monday = 0 ..
Sunday = 6** -- identical to Python's `datetime.date.weekday()`, so no
conversion is ever needed between the stored value and date arithmetic.

**Recurrence / timezone semantics.** `start_time` / `end_time` are stored
as local civil `TIME` values, interpreted through `APP_TIMEZONE`. There
is **no timezone column** and weekly wall-clock values are **never**
converted to UTC -- a 09:00 Monday slot stays "09:00 local" regardless of
DST or where the reader is. Only the audit timestamps are UTC. Overnight
slots are out of scope: every slot is `start_time < end_time` on one
civil day.

### B. Database constraints (the final defense only)

On `schedules`:

- `CHECK (day_of_week >= 0 AND day_of_week <= 6)`
  (`ck_schedules_day_of_week_range`);
- `CHECK (start_time < end_time)` (`ck_schedules_time_order`);
- `CHECK (effective_start_date <= effective_end_date)`
  (`ck_schedules_effective_date_order`);
- `UNIQUE (group_id, day_of_week, start_time, end_time,
  effective_start_date, effective_end_date)` (`uq_schedules_exact_slot`)
  -- the database's final defense against a byte-for-byte duplicate row;
- `UNIQUE (public_id)`; index on `group_id`; index on `status`.

The model carries only a `@validates("status")` guard (matching every
other academic model); weekday/time/date-range validity is left to the
CHECK constraints and to the form + route, mirroring how `Group.capacity`
and `AcademicTerm`'s date range are handled.

### C. Containment and the operational overlap rule (authoritative in code)

**Effective-range containment.** A slot's `[effective_start_date,
effective_end_date]` must (1) lie entirely within its Group's
`AcademicTerm` `[start_date, end_date]`, and (2) contain **at least one
actual calendar occurrence** of its selected weekday (a Mon-only slot
whose range is Tue..Sun is rejected).

**Active overlap.** Two **active** slots in the same Group conflict only
when ALL three hold:

- the weekday is equal;
- the half-open time intervals overlap -- `start < other_end and
  end > other_start` -- so adjacent times (10:00-11:00 after 09:00-10:00)
  are allowed;
- their effective date ranges share **at least one actual calendar date
  of that weekday** (not merely a date-range intersection -- an
  intersection that lands only on other weekdays does not conflict).

Archived rows never participate in overlap, in either direction. The
exact-duplicate `UNIQUE` constraint is a separate, final defense; the
cross-row overlap rule is authoritative **application** logic
(`app/services/schedule_queries.py`), rechecked after locking.

All of this lives as small, independently tested pure functions
(`range_contains_weekday`, `term_contains_range`, `times_overlap`,
`effective_ranges_share_weekday`) plus the group-scoped query
`conflicting_active_schedule`; the `ScheduleForm` friendly pre-lock
checks and the route's authoritative post-lock checks both call the same
functions so the rule cannot drift.

### D. Lifecycle and history policy

- **Status is owned solely by the toggle route.** `ScheduleForm` and the
  create/edit HTML carry no `status` control; `group_schedule_create`
  always stores `active`; `group_schedule_edit` never reads or writes
  `status` (its signed snapshot excludes it). Only
  `POST /admin/groups/<gpid>/schedules/<spid>/toggle-status` changes it.
- **No hard delete.** Reactivation reuses the same row.
- **Creating / editing / reactivating** requires the Group **and** its
  AcademicTerm / Course / Level ancestors all active (rechecked on the
  locked rows).
- **Archiving a Schedule is always allowed**, including when its Group or
  an ancestor is already archived.
- **Archiving a Group never cascades to Schedule rows** -- they stay as a
  frozen historical record but are "not operational" while the Group is
  archived. Group reactivation likewise never rewrites a Schedule row.
- **Editing an archived Schedule** keeps it archived (status is never
  assigned) and never silently reactivates it, but still requires an
  active Group/ancestor chain.
- **Reactivating a Schedule** re-checks the containment + weekday +
  active-overlap rules against current rows; a slot that would now
  overlap an active sibling stays archived.

### E. Group-identity freeze extension

Any Schedule row -- active or archived -- now freezes its Group's
academic identity (`academic_term_id` / `course_id`) exactly like
Enrollment / GroupTeacherAssignment history already did.
`group_has_schedule_history` (`app/services/schedule_queries.py`) is
OR-ed with `group_has_membership_history` in `_group_identity_frozen`
(`app/blueprints/admin/groups.py`), used by both the pre-lock friendly
check and the post-lock recheck; `group_has_membership_history` stays
membership-only for every other caller. The user-facing error and the
locked-identity form notice both read "enrollment, teacher-assignment, or
schedule history". Re-submitting the Group's own current Term + Course,
and every non-identity Group edit (name / code / capacity), remain
allowed. See the "Part M08 amendment" under "Group edit integrity
(Phase 3, Part 7B0)" above for how this slots into that section.

### F. AcademicTerm date-edit guard

`academic_term_edit` now locks the `AcademicTerm` row
(`lock_academic_term_for_write`) before writing. When the submitted dates
**differ** from the stored ones, it rejects the edit if **any** Schedule
effective range in that Term -- **including archived schedules and
schedules under archived Groups** (`term_schedule_range_outside`, no
status filter) -- would fall outside the new range. A name-only edit
(dates unchanged) skips the check, so a legacy inconsistency can still be
corrected. AcademicTerm edit and Schedule create/edit serialize on the
`AcademicTerm` lock (Schedule mutations take it first via
`lock_academic_hierarchy`), so a concurrent Term shrink cannot invalidate
a committed Schedule range.

### G. Transactions, lock order, stale forms

Every Schedule mutation follows the approved global order

```
AcademicTerm -> Level -> Course -> Group -> Schedule
```

via `lock_academic_hierarchy` (which owns the single deliberate
`db.session.rollback()` before the first lock) -> a route-local
`lock_group_in_open_transaction` 404 wrapper ->
`lock_schedule_in_open_transaction_by_id`
(`app/services/schedule_transactions.py`, no-reset only -- Schedule is
last in the chain and never the first lock). Schedule is a new tail on
the same graph, so it adds no reverse-order path. Preview reads only
discover which rows to lock; existence, ancestor/Group/Schedule status,
the signed snapshot, containment, and overlap are all rechecked
post-lock, and nothing is written until every check passes (no partial
mutation). `IntegrityError` at commit is caught, rolled back, and shown
as a generic message with no SQL/driver text.

Schedule creation and Group retargeting serialize on the same Group lock:
if Schedule creation commits first, a later retarget sees Schedule
history and is rejected by the identity freeze; if a retarget commits
first, Schedule creation rechecks the current hierarchy after locking and
proceeds (or bails safely if it moved).

**Signed edit snapshot** (`admin.schedule-edit-snapshot.v1`,
`itsdangerous.URLSafeSerializer`, app `SECRET_KEY`): covers every
editable persisted field -- `day_of_week`, `start_time`, `end_time`,
`effective_start_date`, `effective_end_date`, `location` -- plus
`public_id` to bind the token to one Schedule. Times/dates are serialized
as ISO strings for a deterministic token. `status` is **excluded** (edit
never writes it), so a completed status toggle does not stale an open
edit form and an edit can never overwrite the toggled status. A missing,
malformed, wrong-signature, wrong-object, or value-mismatched token is
rejected with a Post/Redirect/Get to a fresh GET (discarding submitted
values); an ordinary WTForms failure or business-rule rejection
re-embeds the *original* token unchanged. Same rules as the Group /
Course edit snapshots.

### H. Routing and UI

- `GET /admin/schedules` -- center-wide read-only overview, filterable by
  Group, Academic Term, Course, Level, weekday, status, and location
  text, with distinct empty states. Eager-loaded (`joinedload`) so the
  row count does not drive the query count. Shows `APP_TIMEZONE` and a
  "Not operational" badge when a slot's Group or an ancestor is archived.
- `GET /admin/groups/<group_public_id>/schedules` -- Group-centered
  management page (list + Add / Edit / Archive / Reactivate actions,
  honest parent-archived / non-operational banners).
- `GET|POST .../schedules/new` and `.../schedules/<schedule_public_id>/edit`
  -- nested create / edit.
- `POST .../schedules/<schedule_public_id>/toggle-status` -- POST-only,
  the sole owner of Schedule status.

Every nested lookup is scoped to the Group in the URL and every object is
addressed by `public_id` (nested-IDOR safe -- a schedule public_id valid
only for another Group 404s). `roles_required(ADMINISTRATOR)`, Flask-WTF
CSRF, safe 404s, no open redirects (fixed redirect targets only). A
"Schedules" item was added to Administrator navigation and a "Manage
Schedule" button to the Group detail page. No unrelated CSS/JS was added.

### I. Honest limitations

- SQLite (the test backend) has no `SELECT ... FOR UPDATE` and no
  REPEATABLE READ snapshot isolation. The structural tests prove only the
  *requested* single reset and lock order
  (`AcademicTerm -> Level -> Course -> Group -> Schedule`) -- never that a
  real InnoDB lock blocks a concurrent transaction. No safe real
  two-session MySQL probe was executed for M08, so no real concurrency
  blocking is claimed; the M08 migration was applied and verified on the
  real MySQL database only at the schema level.
- Out of scope and not built: Room / CalendarEvent / AttendanceSession
  entities, teacher-to-slot assignment, holiday / one-off exception
  handling, a calendar/day-grid rendering, notifications, and any hard
  delete. Overnight (wrap-past-midnight) slots are not supported.
- Legacy Schedule rows already outside their Term (only possible by
  direct DB manipulation) are not auto-repaired; a name-only Term edit
  stays allowed so they can be corrected.
- Attendance remains a future module.
