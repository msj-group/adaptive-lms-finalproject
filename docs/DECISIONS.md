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

### A. Group identity fields and when they become immutable

- A Group's **academic identity** is the pair (`academic_term_id`,
  `course_id`) -- the same two fields the "Group model" and
  "Group-centered membership management" sections already call its
  identity. Level is not one of them; it is still derived through
  `Group -> Course -> Level`.
- Those two fields are **freely editable while the Group has no
  Enrollment or teacher-assignment history at all**, and **immutable once
  any such history exists**.
- **Non-identity fields stay editable regardless of history:** `name`,
  `code`, `capacity`, and `status` can always be changed (subject to
  their own validation -- uniqueness, the capacity rule in section C,
  the status enum).
- Re-submitting the Group's own current `academic_term_id`/`course_id`
  (no actual change) is always allowed even when the identity is frozen,
  so an Administrator can still edit the non-identity fields on a Group
  that has history.
- The rule is `_group_identity_change_error`
  (`app/blueprints/admin/groups.py`), shared by the early pre-lock
  friendly check and the authoritative post-lock recheck so the two
  cannot drift.

### B. What counts as Enrollment or teacher-assignment history

`group_has_membership_history(group_id)`
(`app/services/group_memberships.py`) returns true if **any**
`Enrollment` **or** `GroupTeacherAssignment` row exists for the Group,
deliberately **regardless of**:

- **status** -- a `withdrawn` Enrollment or a `removed`
  `GroupTeacherAssignment` counts exactly like an `active` one; and
- **the referenced user's role** -- a malformed row whose
  `student_id`/`teacher_id` points at a non-Student / non-Teacher `User`
  (the foreign key to `users` cannot forbid this) still counts.

Every one of those rows was created under this Group's Course/Term
identity at the time, and the same-Course-same-Term Enrollment conflict
rule (`conflicting_active_enrollment`, section D of the Part 6 section
above) reads the Group's *current* `course_id`/`academic_term_id`, not
the value in force when each row was written. Retargeting a Group with
history would retroactively change what counts as a conflict for its
existing enrollments. This is intentionally a looser test than the
`active`/eligible counts used for capacity and teacher eligibility, and
must stay looser.

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
- **Included fields (7):** `public_id`, `academic_term_id`, `course_id`,
  `name`, `code`, `capacity`, `status` -- every field `group_edit` can
  write, plus `public_id` to bind the token to one Group.
- **Stale = reject.** A token that is missing, empty, invalidly signed,
  wrong-shaped, signed for a different Group, or whose values no longer
  match the locked Group is stale. Because `status` is one of the seven
  fields, a completed Group **status toggle** also makes an open edit
  form stale.
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
  other's case.

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
- **`group_edit` lock order is `Course -> Group`.** It first locks the
  *submitted target* Course by internal id
  (`lock_course_for_write_by_id`, which performs the
  transaction-boundary reset), then the current Group with
  `lock_group_in_open_transaction` -- the no-reset variant, because a
  second reset would release the Course lock just taken. This fixed
  order is what lets retargeting an unused Group to a different Course
  serialize against a concurrent Course-level change on that same Course
  (see "Course-level identity integrity" below). `group_toggle_status`,
  taking no Course, keeps calling `lock_group_for_write` (with its
  reset) directly.
- Staleness, identity, and capacity are all rechecked after the locks
  against freshly queried data; nothing from the pre-lock preview or
  form validation is trusted. No Group field is assigned until every
  check passes, so a rejection never leaves a partial update. An
  `IntegrityError` at commit is caught, rolled back, and reported with a
  generic message -- no SQL, parameters, or driver text reaches the
  user.

### F. Group edit/status and membership-route coordination today

- Group edit <-> Group status toggle: serialize on the shared Group
  lock. A status toggle that commits while an edit form is open also
  makes that form stale (section D).
- Group edit / status toggle <-> the six membership routes: all take the
  same Group row lock, so they serialize for the same Group.
- Group edit <-> Course edit / Course status toggle: `group_edit` locks
  its submitted target Course, and `course_edit` / `course_toggle_status`
  lock the Course through the same shared explicit Course write lock
  (`lock_course_for_write` / `lock_course_for_write_by_id`,
  `app/services/course_transactions.py`). So a Group edit serializes on
  that Course row against a concurrent Course edit or Course status
  toggle for the same Course. This is transaction serialization on the
  Course row only -- a Course status change touches none of the seven
  Group snapshot fields, so it never makes a Group edit form stale
  (contrast a Group status toggle, section D).
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
  freeze (which waits for *that one Group's* membership history), a
  single Course can back many Groups at once, so moving its Level would
  silently reinterpret all of them together.
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
  no membership history), nothing derives the Course's Level any more
  and it may move. This is a deliberate, accepted consequence -- there
  is no place that records a Course's past Level associations.
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

- **Course edit and status toggle** both lock the Course through the
  shared `lock_course_for_write` (`app/services/course_transactions.py`,
  with the transaction-boundary reset) via the route-local
  `_lock_course_or_404` wrapper, so a Course edit and a Course status
  toggle serialize against each other for the same Course.
- **`group_edit` uses the explicit `Course -> Group` order:** it locks
  the submitted target Course (`lock_course_for_write_by_id`) *before*
  the Group (`lock_group_in_open_transaction`, no second reset).
- **`group_create` locks the target Course**
  (`lock_course_for_write_by_id`) as the first query of its fresh
  transaction, before inserting the new Group that references it.
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
  locks where implemented (`course_edit` and `course_toggle_status`
  share `lock_course_for_write`) and on ordinary database write locking
  otherwise.
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
  `AcademicTerm` and their active descendants remain **undecided** and
  are unchanged by Parts 7B0/7B1.
- Attendance remains a future module.
