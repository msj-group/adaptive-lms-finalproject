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

**No `must_change_password` flag.** Adding the column without a control
that acts on it would be a half-finished feature. The Administrator
currently hands the temporary password to the student out of band (never
by email -- no email service is approved), and it stays valid until an
administrator resets it.

M09 added Student and Teacher dashboards, but a dashboard is only a
landing page -- it is **not** the forced-change flow, and this note must
not be read as "the dashboard resolves this". A real temporary-password
workflow is a separate, still-deferred **account-security** design
requiring an explicit decision across three layers at once: schema (a
`must_change_password` / credential-age column or equivalent), session
(how a not-yet-changed session is quarantined and where it is forced to
redirect), and password policy (self-service change form, current-password
re-entry, reuse rules, and the `bump_auth_version` interaction). None of
that is in scope for M09, which deliberately adds no `must_change_password`
column, no self-service password-mutation route, and no migration. See
"Role dashboards (Phase 3, Part M09)" for the boundary.

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
the Group would silently reinterpret it. Re-submitting the Group's own
current Term + Course stays exempt. M08 also changed route/service/template
logic here (plus the one new `schedules` table) -- see "Recurring Group
schedules (Phase 3, Part M08)" for the whole Part.

**Part M10 amendment (Unit history freezes identity too).** Since M10,
`_group_identity_frozen` also ORs in
`group_has_unit_history` (`app/services/unit_queries.py`, true if **any**
`Unit` row exists for the Group, active or archived). A Unit is teaching
content authored for the Group's *current* Course, so retargeting a Group
that has Units would reattach that content to a different Course.
`group_has_membership_history` stays membership-only; each other kind of
history lives in its own helper and only the identity-freeze call site
combines them. The user-facing message and the locked-identity form
notice now read "enrollment, teacher-assignment, schedule, or unit
history". Teacher Unit creation
(`app/blueprints/teacher/units.py`) and admin Group retarget
(`group_edit`) both take the same Group row lock
(`lock_group_in_open_transaction` after `lock_academic_hierarchy`), so a
Unit committed first makes the retarget's post-lock recheck reject, and a
retarget committed first makes Unit creation re-check the current
hierarchy -- neither can bypass the freeze. See "Group-owned Units
(Phase 3, Part M10)" for the whole Part.

### A. Group identity fields and when they become immutable

- A Group's **academic identity** is the pair (`academic_term_id`,
  `course_id`) -- the same two fields the "Group model" and
  "Group-centered membership management" sections already call its
  identity. Level is not one of them; it is still derived through
  `Group -> Course -> Level`.
- Those two fields are **freely editable while the Group has no
  Enrollment, teacher-assignment, Schedule, or Unit history at all**
  (the Schedule half added in M08, the Unit half in M10 -- see the M08
  and M10 amendments above), and **immutable once any such history
  exists**.
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
  `_group_identity_frozen` (membership history **or** Schedule history
  **or** Unit history); the function itself only decides the
  unchanged-current-values exemption and the message.

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

It does **not** look at `Schedule` or `Unit`. Since M08 the identity
freeze uses `_group_identity_frozen`, which ORs this helper with
`group_has_schedule_history` (`app/services/schedule_queries.py`) and,
since M10, `group_has_unit_history` (`app/services/unit_queries.py`) --
each true if **any** row of its kind exists for the Group, active or
archived, on the same "history is history" principle. The split is
deliberate: `group_has_membership_history` keeps its precise membership
meaning for every other caller, and only the identity-freeze call site
combines them.

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
membership-only for every other caller. M10 extended the same OR chain
with `group_has_unit_history`, so the user-facing error and the
locked-identity form notice now read "enrollment, teacher-assignment,
schedule, or unit history". Re-submitting the Group's own current Term +
Course, and every non-identity Group edit (name / code / capacity),
remain allowed. See the "Part M08 amendment" / "Part M10 amendment" under
"Group edit integrity (Phase 3, Part 7B0)" above for how this slots into
that section.

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


## Role dashboards (Phase 3, Part M09)

Real, server-authorized dashboards for Administrator, Teacher, and
Student, built **only** on the domain that exists through M08
(`AcademicTerm -> Level -> Course -> Group -> Enrollment /
GroupTeacherAssignment / Schedule`). **No model change and no database
migration** -- route, service, template, and narrowly-scoped CSS only.

### A. Boundaries -- what each dashboard is, and is not

- Every dashboard is a **read-only landing page**. No dashboard route
  takes a lock, writes a row, or exposes a mutation. Mutations stay where
  they already live (the Administrator section).
- **Administrator** (`GET /admin/dashboard`, unchanged URL, still
  Administrator-only): the existing academic-structure counts, plus
  people counts (Student/Teacher total/active/suspended), Group counts,
  clearly-labelled *active Enrollment-row* and *active
  Teacher-assignment-row* counts, a current/upcoming operational class
  list, and setup indicators for active Groups missing an eligible active
  teacher or an active Schedule. Direct links point only to pages that
  exist. No Attendance / Grades / Payments / announcements / messages /
  charts / fabricated data.
- **Teacher** (new `teacher` blueprint, `GET /teacher/dashboard`): only
  data derived from the authenticated Teacher's own **ACTIVE**
  `GroupTeacherAssignment` rows -- assigned Group/Course/Level/Term,
  operational state, the active eligible enrolled-student **count** (no
  student identities), active schedules + location + timezone, and the
  current/next + upcoming-class lists. No roster page, no mutation
  routes, no Lessons/Attendance/reviews/announcements/messages. (M10
  added a per-card **"Manage Units"** link; the dashboard route itself
  stays read-only -- Unit management lives on its own group-centered
  pages, see "Group-owned Units (Phase 3, Part M10)".)
- **Student** (new `student` blueprint, `GET /student/dashboard`): only
  data derived from the authenticated Student's own **ACTIVE**
  `Enrollment` rows -- enrolled Group/Course/Level/Term, operational
  state, active schedules + location + timezone, and the current/next +
  upcoming-class lists. Never exposes other students, withdrawn
  enrollments, unauthorized Groups, or internal numeric IDs in object
  URLs. No Assignments/Grades/Attendance/progress/Lessons/Materials/
  announcements/messages/calendar-event entities.
- **Researcher** keeps the `DEFAULT_HOME_ENDPOINT` (`design_system.index`)
  fallback -- its dashboard is deferred to Phase 6.

### B. Ownership and operational-status semantics

- **Ownership is enforced in SQL**, never by filtering an unscoped
  result in a template: the Teacher/Student queries filter
  `GroupTeacherAssignment.teacher_id == current_user.id` /
  `Enrollment.student_id == current_user.id` **and** `status == active`
  in the query itself.
- **A row is *operational* only when the whole chain is active** --
  Schedule, Group, AcademicTerm, Course, and Level. A legacy
  inconsistent ancestor (e.g. an active Group under an archived Course)
  is treated honestly as **non-operational**: the assignment/enrollment
  still shows as historical context with a clear "Not operational" badge,
  but it contributes **no** current/next/upcoming occurrence.
- **"Eligible" counts require role + active account.** The Teacher
  dashboard's enrolled-student count is `Enrollment.status == active AND
  User.role == student AND User.status == active` -- stricter than M08's
  `active_student_enrollment_count` (which does not check `User.status`),
  because a dashboard headline number should reflect students who can
  actually attend. A suspended student's active enrollment is therefore
  excluded from this figure (it still occupies a seat for capacity
  purposes elsewhere -- the two questions differ). The
  "missing eligible teacher" indicator reuses M08's
  `eligible_active_teacher_count == 0` definition unchanged.

### C. Recurring occurrence / timezone semantics

- `app/services/schedule_occurrences.py` is a Flask-independent,
  stdlib-only island of pure functions over **naive local wall-clock
  datetimes**. `day_of_week` stays Monday=0 .. Sunday=6; effective date
  ranges stay inclusive at both ends; one occurrence is the half-open
  interval `[combine(date, start_time), combine(date, end_time))` on a
  single civil day (overnight slots remain out of scope, per M08).
- `current_or_next_occurrence(spec, now)` returns the single earliest
  occurrence whose `end > now` -- "current" when `start <= now < end`,
  "next" otherwise -- or `None` when the effective range holds no such
  occurrence (it has ended, or never contained the weekday).
- `upcoming_occurrences(specs, now, horizon=7d, cap)` returns a sorted,
  capped list of occurrences with `end > now` and `start <= now +
  horizon`. `earliest_occurrence(specs, now)` is the combined
  no-horizon "next class" across a member's operational schedules.
- **`now` is injectable** -- the real callers pass
  `app_now(APP_TIMEZONE)`; every occurrence test injects a fixed `now`,
  and every date-dependent *route* test monkeypatches the route module's
  own `app_now` binding to a fixed local `NOW`, so no dashboard test
  depends on the wall clock (they stay valid after 2026).
- **Timezone resolution (`to_app_local`) -- fail-closed.** Weekly
  `start_time` / `end_time` are local civil values and are never
  converted to UTC (the M08 decision). `to_app_local(tz_name,
  utc_moment)` resolves in this order:
    1. an **explicit fixed-offset spec** -- `UTC` / `GMT`, `UTC+2` /
       `GMT-05:30`, `+02:00` / `-0530` / `+2`. An offset that is
       malformed, or beyond ±14:00 (including ±14 with non-zero
       minutes), raises `TimezoneConfigError` -- it is not passed on as
       an IANA name.
    2. a **named IANA zone** the host's `zoneinfo` can load (production
       Linux, or any host with the `tzdata` package). This is attempted
       **before** any deployment fallback, so a resolvable IANA value
       always wins.
    3. **only if step 2 could not resolve it**: a small
       deployment-specific fixed-offset table -- currently just
       `Africa/Tripoli -> +02:00` (Libya abolished DST in 2013, so the
       offset is constant year-round) -- so occurrence math stays
       deterministic on a host without an IANA database (e.g. Windows
       dev without `tzdata`).
    4. **otherwise `TimezoneConfigError`.** An empty, unknown, or
       unresolvable `APP_TIMEZONE` is a configuration bug that must be
       fixed (install `tzdata`, or set `APP_TIMEZONE` to `UTC` / an
       explicit offset) -- it is **never** silently reinterpreted as
       UTC, because a dashboard whose "now" is wrong by the center's
       real offset on every request is worse than a loud, obvious
       failure. `app_now` propagates the error.
  No new dependency was added.

### D. Query / performance strategy

- `Group -> Course -> Level` and `Group -> AcademicTerm` are always
  eager-loaded (`joinedload`).
- No per-row membership or schedule query: the Teacher/Student dashboards
  issue one scoped assignment/enrollment query, one batched
  `Schedule ... WHERE group_id IN (...)` query, and (Teacher) one grouped
  `COUNT ... GROUP BY group_id` aggregate -- the SELECT count does not
  grow with the number of assigned/enrolled Groups. `admin_setup_indicators`
  is at most three fixed queries regardless of Group count; the same
  operational-active-Group list it already fetches yields
  `operational_group_count`, so the dashboard can honestly distinguish
  "no operational active groups yet" (neutral empty state) from "every
  operational active group is fully configured" (success) from "some are
  missing a teacher / schedule" (the warning lists) -- an empty center is
  never described as fully configured.
- Templates receive plain dicts / `Occurrence` namedtuples and **never
  touch the ORM** -- all display strings (`group_name`, `course_title`,
  `location`, ...) are materialized in the query layer.
- Occurrence arithmetic is pure Python over already-fetched rows; it
  issues no queries and is bounded (`window / 7 + 1` iterations per
  slot).

### E. Authentication / authorization

- `ROLE_HOME_ENDPOINT` gained `teacher -> teacher.dashboard` and
  `student -> student.dashboard`; the `_home_endpoint_for` /
  safe-`next` login logic is otherwise unchanged, so it applies both
  after a successful login and when an already-authenticated user loads
  `/auth/login`. Researcher still falls through to
  `DEFAULT_HOME_ENDPOINT`.
- Each dashboard is guarded by `roles_required(<its role>)`: anonymous
  access redirects to login (preserving `?next=`), and every other
  authenticated role gets 403. Changing the URL or a query parameter
  cannot surface another Teacher's or Student's data because the scoping
  is in the SQL `WHERE`, keyed off `current_user.id`, not off any
  request input. The existing safe-`next` and logout-CSRF tests are
  preserved.

### F. UI

- A shared responsive `layouts/portal_base.html` (top bar: brand,
  role-appropriate nav, current user name + role badge, CSRF-protected
  POST logout) for the Teacher/Student portal, using the existing design
  tokens and brand logo. The Administrator shell is untouched.
- New CSS is deliberately minimal: `portal.css` (the header/shell only)
  and `dashboard.css` (stat grid, dashboard tables -- shared with the
  Administrator dashboard, which pulls it in via `{{ super() }}` in its
  `extra_head`). No unrelated redesign. No "Soon" / dead links for
  unimplemented features anywhere on the new pages.

### G. Deferred

- **Researcher dashboard** -- Phase 6.
- **Temporary-password / forced-change workflow** -- still deferred, and
  M09 does **not** resolve it. It is a separate account-security design
  spanning schema + session + password policy (see the amended
  "No `must_change_password` flag" note in the Student account
  management section). M09 adds no `must_change_password` column, no
  self-service password-mutation route, and no migration.
- Attendance, Grades, Payments, Assignments/Lessons/Materials,
  announcements, messages, progress/Continue-Learning, and
  calendar-event entities -- all remain future modules and appear on no
  dashboard.

### H. Honest limitations

- `APP_TIMEZONE` resolution is **fail-closed** (section C): a host that
  can neither load the configured IANA zone via `zoneinfo` nor fall back
  through the small deployment table raises `TimezoneConfigError` on the
  first dashboard request instead of silently pretending the center runs
  on UTC. The operator's fix is to install `tzdata` or set a fixed
  offset. The configured `Africa/Tripoli` resolves on production Linux
  (real zone) and on a bare Windows dev box (deployment fallback).
- Wall-clock occurrence arithmetic does not model a DST transition that
  lands on a class hour. `Africa/Tripoli` has no DST, so this is inert
  for the configured deployment; a future multi-timezone or DST-observing
  center would need explicit handling.
- The automated suite runs on SQLite and a real wall clock is never
  used in assertions (every occurrence test injects `now`); browser
  verification of the three dashboards was performed only to the extent
  noted in the M09 report.


## Group-owned Units (Phase 3, Part M10)

Teacher management of **Units** -- the structural teaching container one
level below Group in the hierarchy
(`AcademicTerm -> Level -> Course -> Group -> Unit`). Adds one model
(`Unit`) and one additive migration (`c3e39736023a`,
`Revises: adf4b5691a7a`) that creates only the `units` table. Lessons
(M11) and content/materials (M12) are deliberately out of scope.

### A. Model -- a Unit belongs directly to a Group

`Unit` has a required, non-cascading `group_id` FK and **nothing else
that ties it to the hierarchy** -- no `course_id` / `level_id` /
`academic_term_id` (all reachable via `unit.group`), and no `teacher_id`
/ `created_by` (every active assigned Teacher is an equal collaborator,
so ownership is the Group, not a person). This mirrors how `Enrollment`,
`GroupTeacherAssignment`, and `Schedule` each avoid duplicating what the
Group already determines.

Columns: `BigInteger` id; `String(36)` unique `public_id` (the only
identifier that ever crosses a request boundary); `group_id`;
`title` (`String(150)`, required); optional `description` (`Text`);
`display_order` (`Integer`, server-owned); `status` (`String(32)`,
reuses the shared `AcademicStatus` `active` / `archived`); UTC
`created_at` / `updated_at`.

Constraints / indexes on `units`: `UNIQUE (group_id, title)` (a teaching
order never has two same-named Units -- archived Units count, so a title
can't be "freed" by archiving); `UNIQUE (public_id)`;
`CHECK (display_order >= 0)`; and `group_id` / `status` / `display_order`
indexes. There is deliberately **no** uniqueness constraint on
`display_order` -- gaps are fine and the reorder logic never renumbers.

**Lifecycle is `active` / `archived` only.** A Unit is a container, so it
has no draft/published state -- that belongs to Lesson (M11). No hard
delete; reactivation reuses the same row. `status` is owned solely by the
toggle route; the create/edit form carries neither `status` nor
`display_order`.

### B. Authorization and co-teacher semantics

- `roles_required(TEACHER)` gives the standard role guard: anonymous ->
  login redirect, any non-Teacher role -> 403.
- **Object authorization is server-side and non-disclosing.** The current
  Teacher must hold an **active** `GroupTeacherAssignment` to the Group
  named in the URL. An unassigned Teacher, a `removed` assignment, a Unit
  `public_id` that belongs to another Group, a bad UUID, or a missing
  object all return **404** -- never 403, and never any hint that the
  object exists (`_teacher_group_or_404` / `_unit_for_group_or_404` in
  `app/blueprints/teacher/units.py`).
- **Co-teachers are equal.** Any number of active assigned Teachers may
  view and manage the same Group's Units; there is no "owner".
- **GET stays available under an archived Group / ancestor** for an
  actively assigned Teacher, so historical Units can be read
  (`_teacher_group_or_404` checks the assignment, never the Group's own
  status).
- All routes are group-centered (`/teacher/groups/<group_public_id>/units/...`);
  there is **no** flat `/teacher/units` collection. No internal numeric id
  appears in any URL, form value, or rendered page.

### C. Lifecycle rules for mutations

- **Create / edit / reorder / reactivate** require, re-checked against
  the locked rows: an active Teacher account with role `teacher`, an
  **active** assignment to the Group, an **active** Group, and an active
  AcademicTerm, Course, and Level.
- **Archiving** is allowed while the assignment is active **even under an
  archived Group / ancestor** -- so a Teacher can always clean up.
- Editing an archived Unit keeps it archived (`status` is never assigned
  by the edit route); a Group / ancestor archive or reactivation **never**
  cascades into Unit status; nothing is ever hard-deleted.

### D. Ordering

- A new Unit gets `display_order = MAX(display_order over all the Group's
  Units) + 1`, or `0` for the first (`next_unit_display_order`). A
  reactivated Unit is appended the same way -- it goes after everything
  currently in the Group, active or archived.
- Only **active** Units have move controls, and move-up / move-down swap
  `display_order` with the **nearest active sibling** (archived Units are
  filtered out of the ordered list, so they are skipped over and keep
  their stored order). Reordering an archived Unit is rejected.
- A boundary move (already first / already last) is a safe no-op with a
  clear flash and a Post/Redirect/Get -- not an error.
- Gaps in `display_order` are acceptable and never repaired.
- `display_order` is never a client-controlled field: the move routes are
  bodyless `POST` (CSRF token only).

### E. Concurrency, locking, and the stale edit form

Every Unit mutation follows the canonical lock order

```
AcademicTerm -> Level -> Course -> Group -> Teacher User ->
GroupTeacherAssignment -> Unit rows (ascending internal id)
```

`lock_academic_hierarchy` owns the single deliberate transaction reset
and takes the AcademicTerm/Level/Course locks; then
`lock_group_in_open_transaction`, then `SELECT ... FOR UPDATE` on the
Teacher `User` row, the `(group_id, teacher_id)` `GroupTeacherAssignment`
row, and any Unit rows -- for a reorder, the target Unit **and** its swap
neighbour, locked lowest-id-first. The **held Group lock serializes every
same-Group create / reorder / toggle** (and admin Group edit / status
toggle / membership routes, which lock the same row), so a plain re-read
of the teaching order under that lock is consistent. After the locks,
authorization, the operational hierarchy, and (for edit) the signed
snapshot are all re-checked against the locked rows before any write.

**Signed edit snapshot** (`teacher.unit-edit-snapshot.v1`,
`itsdangerous.URLSafeSerializer`, app `SECRET_KEY`): covers `title`,
`description`, and `public_id` -- everything the edit route can write,
plus the object binding. `status` and `display_order` are excluded (edit
writes neither). A missing / malformed / wrong-signature / wrong-object /
value-mismatched token is rejected with a PRG to a fresh GET (discarding
the submitted values); an ordinary WTForms failure re-embeds the
original token. This stops one co-teacher from unknowingly overwriting
another's completed edit -- the row lock alone cannot, because the two
submissions never overlap in time. Same rules as the Group / Course /
Schedule edit snapshots.

> **M13 amendment.** The Unit edit route also writes `search_keywords`,
> so the snapshot now covers `("public_id", "title", "description",
> "search_keywords")`. A pre-M13 three-field token is wrong-shaped and
> rejected with the same PRG-to-fresh-GET path. See "Authorized learning
> content search (Phase 3, Part M13)".

`IntegrityError` (e.g. a racing duplicate `(group_id, title)`) is caught,
rolled back, and reported with a generic message -- no SQL, no internal
id, no driver text.

### F. Group identity freeze extension

The existence of **any** Unit row -- active or archived -- now counts as
history that freezes the Group's `academic_term_id` / `course_id`
(`group_has_unit_history` OR-ed into `_group_identity_frozen`; see the
"Part M10 amendment" under "Group edit integrity (Phase 3, Part 7B0)").
A Unit is content authored for the Group's *current* Course, so a
retarget would silently reattach it elsewhere. Teacher Unit creation and
admin Group retarget take a compatible Group lock, so a concurrent Unit
create cannot slip past the freeze. Non-identity Group fields
(`name` / `code` / `capacity`) are not affected.

### G. UI

- A "Manage Units" link on each Teacher-dashboard Group card, scoped by
  `group_public_id` (a Teacher never sees a link to a Group they are not
  assigned to).
- Group-centered Units pages on the existing portal layout: Group /
  Course / Level / AcademicTerm context; active Units in teaching order
  with edit / archive / move-up / move-down; archived Units in a separate
  read-only-ish section with reactivate; clear operational vs
  historical explanations; clear empty states; CSRF-protected `POST`
  actions; no internal ids anywhere.

### H. Deliberate deferrals

> **M11 amendment (honest wording).** M11 has since landed
> `Lesson` and its draft/published lifecycle, the Teacher Lesson
> management pages, **and** the first authorized Student Lesson
> navigation (a Group learning outline plus a single Lesson page) with a
> **plain-text** Lesson description. So the M10 line "Students get no Unit
> pages in M10 -- student content access begins only when the
> Lesson/content model exists" is now fulfilled *for Lessons*: students
> read only published Lessons under an active Unit and active hierarchy.
> Still deferred to M12+: **rich HTML / a rich-text editor, Materials,
> file uploads and downloads, external links, PDF/Word/image/audio/video,
> file storage, and any Lesson completion / progress / recently-opened
> tracking.** See "Lesson content and navigation (Phase 3, Part M11)"
> below.

- **Lessons** and their draft/published lifecycle -- M11 (done).
- **Materials / uploads / rich content**, completion / progress tracking
  -- M12+.
- No Course-level Unit templates, no Unit copying, no drag-and-drop
  ordering, no Administrator Unit management, no Unit search, no
  notifications.

### I. Honest limitations

- SQLite (the test backend) has no `SELECT ... FOR UPDATE` and no
  REPEATABLE READ isolation. The structural tests prove only the
  *requested* single reset and lock order -- never that a real InnoDB
  lock blocks a concurrent transaction. The M10 migration was applied and
  verified on the real MySQL database at the schema level (engine, FK
  rule, CHECK, unique constraints, indexes, unchanged existing row
  counts); no safe real two-session concurrency probe was run.
- `UNIQUE (group_id, title)` is case-insensitive on MySQL
  (`utf8mb4_0900_ai_ci`) and case-sensitive on SQLite -- the same
  portability nuance the project already has for Group name / Course
  title. The friendly form check compares exact stripped strings; the DB
  constraint is the final defense.


## Lesson content and navigation (Phase 3, Part M11)

Ordered **Lessons** inside a Unit, Teacher draft/publication management,
and the first authorization-safe Student Lesson navigation. Adds one
model (`Lesson`) and one additive migration (`cb9112548dd6`,
`Revises: c3e39736023a`) that creates only the `lessons` table. Rich
content, Materials, uploads/downloads, and completion/progress tracking
are deliberately out of scope (M12+).

### A. Model -- a Lesson belongs directly to a Unit

`Lesson` has a required, non-cascading `unit_id` FK and **nothing else
that ties it to the hierarchy** -- no `group_id` / `course_id` /
`level_id` / `academic_term_id` (all reachable via `lesson.unit.group`),
and no `teacher_id` / `created_by` (every active assigned Teacher of the
Group is an equal collaborator). This mirrors how `Enrollment`,
`GroupTeacherAssignment`, `Schedule`, and `Unit` each avoid duplicating
what the parent already determines.

Columns: `BigInteger` id; `String(36)` unique `public_id` (the only
identifier that ever crosses a request boundary); `unit_id`;
`title` (`String(150)`, required); optional plain-text `description`
(`Text`; 5000-character form boundary); `display_order` (`Integer`,
server-owned); `status` (`String(32)`, the **Lesson-specific**
`LessonStatus` `draft` / `published` -- *not* the academic
`active`/`archived` enum); nullable `published_at` (UTC); UTC
`created_at` / `updated_at`.

Constraints / indexes on `lessons`:

- `UNIQUE (unit_id, title)` (`uq_lessons_unit_title`) -- an ordered Unit
  never has two same-named Lessons; draft Lessons count, so a title
  cannot be "freed" by leaving a Lesson unpublished. The same title is
  fine in a different Unit.
- `UNIQUE (public_id)`.
- `CHECK (display_order >= 0)` (`ck_lessons_display_order_non_negative`).
- `CHECK (status IN ('draft','published'))` (`ck_lessons_status_valid`).
- `CHECK ((status='draft' AND published_at IS NULL) OR (status='published'
  AND published_at IS NOT NULL))`
  (`ck_lessons_status_published_at_consistency`) -- a draft always has a
  NULL timestamp, a published Lesson always has one.
- `unit_id` / `status` / `display_order` indexes.

There is deliberately **no** uniqueness constraint on `display_order` --
gaps are fine and the reorder logic never renumbers.

`Unit.lessons` <-> `Lesson.unit` is the only new relationship; **no
schema change was made to `units`**.

### B. Draft / published lifecycle and `published_at` semantics

- A new Lesson always starts `draft` with `published_at` NULL.
- **publish**: `status='published'`, `published_at` = current UTC moment.
- **unpublish**: `status='draft'`, `published_at` cleared. Unpublishing
  returns a Lesson to draft -- there is no archived Lesson state and no
  hard delete in M11.
- **republish**: `status='published'` with a **new** current timestamp.
- **editing never touches `status`, `published_at`, or `display_order`**
  -- editing a published Lesson keeps it published at its existing
  timestamp and position; editing a draft keeps it a draft.
- A Unit / Group / academic-ancestor lifecycle change **never** rewrites
  a Lesson's `status` or `published_at`. A published Lesson under an
  inactive Unit or ancestor is *effectively unavailable* to Students
  (section E) but stays `published` in the row; reactivating the chain
  restores visibility with no status rewrite.
- Publication status is owned solely by the `toggle-publication` route;
  `LessonForm` and the create/edit HTML carry neither `status` nor
  `display_order`.

### C. Teacher authorization and co-teacher behavior

- Group- and Unit-centered routes only, all under
  `/teacher/groups/<group_public_id>/units/<unit_public_id>/lessons/...`
  -- there is **no** flat `/teacher/lessons` collection, and no internal
  numeric id appears in any URL, form value, or rendered page.
- `roles_required(TEACHER)` gives the standard role guard (anonymous ->
  login, any non-Teacher role -> 403).
- **Object authorization is server-side and nested.** The current Teacher
  must hold an **active** `GroupTeacherAssignment` to the Group in the
  URL; the Unit must belong to that Group; the Lesson must belong to that
  Unit. An unassigned Teacher, a `removed` assignment, a Unit public_id
  from another Group, a Lesson public_id from another Unit, a bad UUID,
  or a missing object all return **404** -- never 403, never any hint the
  object exists. The teacher lessons module reuses M10's
  `_teacher_group_or_404` / `_unit_for_group_or_404` (imported from
  `app/blueprints/teacher/units.py`, the natural home for "this
  Teacher's access to a Group + its Units") and adds
  `_lesson_for_unit_or_404`.
- **Co-teachers are equal.** Any number of active assigned Teachers may
  view and manage the same Unit's Lessons; there is no "owner". The
  signed edit snapshot (section F) is what stops one co-teacher silently
  overwriting another's completed edit.
- **GET stays available** to an actively assigned Teacher even when the
  Unit, Group, or an academic ancestor is archived, so historical
  Lessons can be read. A **"Manage Lessons"** link is added to every Unit
  row -- active and archived -- on the M10 Teacher Units page.

### D. Lifecycle rules for Lesson mutations

Re-checked against the **locked** rows:

- **Create / edit / reorder / publish** require: an active Teacher
  account with role `teacher`, an **active** assignment to the Group, an
  active AcademicTerm / Level / Course / Group, **and an active Unit**
  belonging to that Group.
- **Unpublish** is allowed while the assignment is active **even when the
  Unit, Group, or an ancestor is archived** -- published content can
  always be withdrawn. This is the one asymmetry: the
  `toggle-publication` route, when the Lesson is currently `published`,
  takes the locks, confirms Teacher/assignment, and unpublishes without
  the operational-hierarchy check; when the Lesson is `draft` it applies
  the full check before publishing.
- Teacher assignment controls **management**, not continued Student
  visibility (section E) -- a Student keeps reading a published Lesson
  after every Teacher assignment is removed.

### E. Effective Student-visibility formula

A Lesson is visible to a Student **iff all** of:

```
Enrollment(student, group).status == active
  AND AcademicTerm.status == active
  AND Level.status          == active
  AND Course.status         == active
  AND Group.status          == active
  AND Unit.status           == active
  AND Lesson.status         == published
```

Any archived link in the chain makes every published Lesson under it
*effectively unavailable* without changing a single `lessons` row;
reactivating the chain restores visibility. Draft Lessons are never
visible to Students -- their titles and descriptions never reach a
Student response (the queries filter `status == 'published'`, so a draft
is never loaded).

### F. Ordering across draft and published Lessons

- All Lessons in a Unit -- draft and published -- share **one**
  Teacher-visible order (`display_order` then `id`). `display_order` is
  **server-owned**: a new Lesson gets `MAX(display_order over ALL the
  Unit's Lessons) + 1`, or `0` for the first
  (`next_lesson_display_order`). The client cannot set it -- the create
  form has no such field, and the move routes are bodyless `POST`
  (CSRF token only).
- **move-up / move-down** swap `display_order` with the nearest Lesson
  **regardless of draft/published status** (unlike M10 Units, where an
  archived Unit is skipped -- a Lesson has no archived state, so every
  sibling participates).
- A boundary move (already first / already last) is a safe no-op with a
  clear flash and a Post/Redirect/Get -- not an error.
- Gaps in `display_order` are acceptable and never repaired; there is no
  DB uniqueness constraint on it.
- Students see published Lessons in their relative position within the
  complete Teacher order -- the Student view
  (`published_lessons_ordered`) is a filtered subsequence of
  `lessons_ordered`.

### G. Student active-Enrollment authorization

- `GET /student/groups/<group_public_id>/units` -- the Student learning
  outline (active Units in Unit order, each with only its published
  Lessons in Lesson order; an active Unit with no published Lesson shows
  an honest empty state).
- `GET /student/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>`
  -- one Lesson page: the authorized hierarchy context, the Lesson
  title, and the safely-escaped plain-text description (line breaks
  preserved with CSS `white-space: pre-wrap` -- **never** Jinja `|safe`,
  never stored HTML).
- **All authorization is SQL-scoped** to `current_user.id` in
  `app/services/student_lessons.py` -- an object is never loaded broadly
  and authorized afterwards, and no ORM-driven authorization runs in a
  template. Every failure -- withdrawn / missing Enrollment, a different
  Group, mismatched nested ids, an inactive Unit or ancestor, a draft or
  non-existent Lesson -- yields **no row** and the route returns a
  non-disclosing 404.
- The outline issues a **bounded** query count: one for the authorized
  Group, one for its active Units, one batched
  `Lesson ... WHERE unit_id IN (...) AND status='published'` -- never one
  query per Unit. Templates receive plain view dicts only.
- Student access **does not depend on Schedule existence or on any
  current Teacher assignment**.
- An **"Open Lessons"** link is added to *operational* Group cards on the
  Student dashboard only; historical / non-operational cards carry no
  active content link.

### H. Lifecycle non-cascade behavior

Consistent with every earlier academic entity: archiving/reactivating a
Unit, Group, or ancestor **never** archives, deletes, publishes,
unpublishes, or otherwise rewrites a Lesson. Nothing is hard-deleted.
Archiving a Unit makes its Lessons effectively unavailable to Students
while preserving every publication status; reactivating restores
visibility. The M10 Unit lifecycle semantics are unchanged -- only the
confirmation/wording was extended to say so.

### I. Stale-edit snapshot and the canonical lock policy

Every Lesson mutation follows the canonical M11 lock order

```
AcademicTerm -> Level -> Course -> Group -> Teacher User ->
GroupTeacherAssignment -> Unit -> Lesson rows (ascending internal id)
```

`lock_academic_hierarchy` owns the single deliberate transaction reset
and takes the AcademicTerm/Level/Course locks; then
`lock_group_in_open_transaction`, then `SELECT ... FOR UPDATE` on the
Teacher `User` row, the `(group_id, teacher_id)` `GroupTeacherAssignment`
row, the **Unit** row, and any Lesson rows -- for a reorder the target
Lesson **and** its swap neighbour, locked lowest-id-first after the full
Lesson order is re-read under the held Group + Unit locks. **The locked
Unit row serializes same-Unit Lesson creation, ordering, and
publication** (and the compatible M10 Unit lifecycle operations, which
take the same Group + Unit locks under the same single reset). This is a
new tail on the M10 graph, so it adds no reverse-order path. After the
locks, authorization, the operational hierarchy + active Unit, and (for
edit) the signed snapshot are all re-checked against the locked rows
before any write; nothing is written until every check passes (no partial
mutation).

**M11 deliberately adds no new Group identity-freeze helper.** Every
Lesson requires a Unit, and the existence of that Unit row already
freezes the Group's `academic_term_id` / `course_id`
(`group_has_unit_history`, M10). A Lesson under it changes nothing about
that.

**Signed edit snapshot** (`teacher.lesson-edit-snapshot.v1`,
`itsdangerous.URLSafeSerializer`, app `SECRET_KEY`): covers `public_id`,
`title`, and `description` -- everything the edit route can write, plus
the object binding. `status`, `published_at`, and `display_order` are
**excluded** because the edit route must never write them, so a completed
publish / unpublish / reorder by a co-teacher does not stale an open edit
form and an edit can never overwrite those. A missing / malformed /
wrong-signature / wrong-object / value-mismatched token is rejected with
a Post/Redirect/Get to a fresh GET that discards the submitted values; an
ordinary WTForms failure or a caught `IntegrityError` re-embeds the
*original* token unchanged. Same rules as the Group / Course / Schedule /
Unit edit snapshots.

> **M13 amendment.** The Lesson edit route also writes `search_keywords`,
> so the snapshot now covers `("public_id", "title", "description",
> "search_keywords")`; a pre-M13 token is wrong-shaped and rejected the
> same way. See "Authorized learning content search (Phase 3, Part M13)".

`IntegrityError` (e.g. a racing duplicate `(unit_id, title)`, or the
status/`published_at` CHECK as a last-resort defense) is caught, rolled
back, and reported with a generic message -- no SQL, no internal id, no
driver text.

### J. Migration and MySQL

`cb9112548dd6` (`Revises: c3e39736023a`) creates only the `lessons`
table -- columns, the FK to `units.id`, the three CHECKs, the
`(unit_id, title)` + `public_id` unique constraints, and the
`unit_id` / `status` / `display_order` indexes. It alters no existing
table. Applied and verified on the real development MySQL database at the
schema level (InnoDB engine, FK, CHECK expressions, unique constraints,
indexes) with the existing table row counts unchanged.

### K. Deliberate M12+ deferrals

Rich HTML and a rich-text editor; Materials; uploads and downloads; PDF /
Word / images / audio / video / external links; file storage; Lesson
completion, "recently opened", and progress calculations; assignments /
quizzes / activities; Lesson copying; drag-and-drop ordering;
Administrator Lesson management; Lesson search; notifications. No
dependency was added for any future rich-content feature.

### L. Honest limitations

- SQLite (the test backend) has no `SELECT ... FOR UPDATE` and no
  REPEATABLE READ isolation. The structural tests prove only the
  *requested* single reset and lock order -- never that a real InnoDB
  lock blocks a concurrent transaction. No safe real two-session MySQL
  concurrency probe was run for M11; the M11 lock chain is the M10
  pattern with a Unit + Lesson tail, and existing rows were left
  untouched rather than exercised with a probe. The migration was
  verified on the real MySQL database at the schema level only.
- `UNIQUE (unit_id, title)` is case-insensitive on MySQL
  (`utf8mb4_0900_ai_ci`) and case-sensitive on SQLite -- the same
  portability nuance already noted for Group name / Course title / Unit
  title. The friendly form check compares exact stripped strings; the DB
  constraint is the final defense.
- The status/`published_at` consistency CHECK uses portable SQL
  (`IS NULL` / `IS NOT NULL`) enforced on both SQLite and MySQL 8.


## Materials -- secure Lesson content and file storage (Phase 3, Part M12)

Lesson-owned learning **Materials** in three kinds (`rich_text`,
`external_link`, `file`), Teacher management, authorized file serving
with an append-only audit trail, and Student rendering on the existing
M11 Lesson page. Adds three models (`UploadedFile`, `Material`,
`FileAccessLog`), one additive migration (`8319232a5609`,
`Revises: cb9112548dd6`), one direct dependency (`nh3==0.3.7`), and a
private on-disk file store outside `app/static`.

### A. Domain and ownership

- A `Material` belongs **directly** to exactly one `Lesson`
  (`lesson_id`, required, non-cascading FK). Unit / Group / Course /
  Level / AcademicTerm are all reachable via
  `material.lesson.unit.group`; every active assigned Teacher of the
  owning Group is an equal collaborator, so there is **no** per-Material
  owner column -- the same reasoning as Enrollment / GroupTeacherAssignment
  / Schedule / Unit / Lesson.
- `kind` (`rich_text` / `external_link` / `file`) is **immutable after
  creation**: no route writes it, and the payload CHECK rejects
  switching kind without also switching payload
  (`ck_materials_payload_matches_kind`): `rich_text` -> `content_html`
  only; `external_link` -> `external_url` only; `file` ->
  `uploaded_file_id` only.
- `UploadedFile` holds server-validated metadata for one physical file:
  a random unguessable `storage_key` (the on-disk name), the normalized
  `original_filename` (display / `Content-Disposition` only -- never a
  path), the validated `extension`, the server-derived `category`
  (`document` / `image` / `audio` / `video`), the server-determined
  canonical `content_type`, a positive `byte_size`, the streamed
  `sha256`, the uploader, and a UTC `created_at`. `materials.uploaded_file_id`
  is `UNIQUE` -- one file backs exactly one Material.
- `FileAccessLog` is an append-only audit row (`upload` / `inline` /
  `download`), with **no** IP address and **no** user-agent column.
- `Lesson.materials` is the only new relationship; **no schema change to
  `lessons` or `units`**.

### B. Lifecycle, ordering, and visibility

- A Material reuses the shared `AcademicStatus` (`active` / `archived`),
  starts `active`, and follows the **M10 Unit pattern** (not the M11
  Lesson draft/published pattern): archive keeps the row **and** the
  physical file; reactivation appends the Material to the end of the
  active list; move-up / move-down operate only on active Materials and
  skip archived rows; a boundary move is a safe no-op. `display_order`
  is server-owned and **positive** (`>= 1`, scoped to the Lesson); gaps
  are fine and never renumbered; there is no hard delete and no cascade.
- `title` is unique within the Lesson, including archived Materials.
- Rich-text edits change title + content; external-link edits change
  title + URL; **file edits change title only** -- uploaded bytes and
  kind are immutable, and replacing a wrong file means archiving the old
  Material and creating a new one.
  > **M13 amendment.** Every kind's edit -- `file` included -- also
  > writes the optional `search_keywords` field. The uploaded bytes and
  > `kind` stay immutable; a `file` edit is now "title + keywords".
- **Effective Student visibility** extends the M11 formula with two
  terms: a Material is visible iff the M11 Lesson formula holds (own
  active Enrollment; active AcademicTerm / Level / Course / Group / Unit;
  **published** Lesson) **and** `Material.status == active`. A new active
  Material on an active published Lesson is therefore immediately visible
  to enrolled Students -- the Teacher UI warns before submission.

### C. Rich-text sanitisation (nh3)

- `app/services/material_content.sanitize_rich_text_html` runs `nh3`
  against a small explicit allowlist: `p br h2 h3 strong b em u ul ol li
  blockquote code pre hr a`, `href` on `<a>` only. `script` / `style` /
  `iframe` / `object` / `embed` / `form` / `noscript` / `svg` /
  `template` are removed with their content. Links: `url_schemes={https}`
  + `url_relative='deny'` (relative and non-HTTPS hrefs are stripped),
  `link_rel="noopener noreferrer nofollow"`. No `style` / `id` / `class`
  / event-handler attribute survives on any tag. Content that is
  effectively empty after sanitising (a lone `<hr>` excepted) is
  **rejected**, not stored as blank.
- Only the sanitised result is persisted. It is **re-sanitised at the
  rendering boundary** (`student_visible_materials`) as defense in depth,
  and only that result is marked safe for the one `| safe` in the
  Student template. No template applies `| safe` to a raw database value.
- Images / audio / video are always separate `file` Materials, never
  rich-text embeds.

### D. External-link policy

`validate_external_url` performs **structural validation only -- no
network I/O** (no DNS resolution, no fetch, no redirect-following, no
preview, no iframe, no proxy): HTTPS scheme only; a valid host; no
`user:password` userinfo; rejects `localhost` / `*.localhost`; rejects
loopback / private / link-local / multicast / unspecified / reserved IP
literals (v4 and v6) via `ipaddress`; rejects control characters,
whitespace, malformed URLs, and over-length input. The link renders as a
normal external anchor with `target="_blank"
rel="noopener noreferrer nofollow"`.

### E. File allowlist and validation

- Hard supported set (never expandable by config):
  `pdf docx png jpg jpeg gif webp mp3 wav mp4 webm`.
  `MATERIAL_ALLOWED_EXTENSIONS` may only *narrow* it.
- Validation pipeline (`app/services/file_validation.py` +
  `file_storage.py`): normalize the filename to a safe basename (strip
  any Windows/POSIX directory component, control chars, CR/LF; never a
  path); validate the extension against the configured allowlist; derive
  the category; cross-check the browser-declared MIME against a small
  vetted alias map (a generic / missing declared type defers to the
  signature; any other mismatch is rejected); stream the body in 1 MiB
  chunks to a random `.part` file, computing size + SHA-256 and capturing
  the leading bytes, aborting the moment the category size cap is
  exceeded; reject an empty file; verify the binary signature/container
  for the claimed extension.
- **DOCX** is inspected as a ZIP **without extraction**
  (`inspect_docx_zip`, central-directory metadata only): must be a real
  ZIP (rejects a legacy binary `.doc`); must contain every required part
  (`[Content_Types].xml`, `_rels/.rels`, `word/document.xml` -- rejects a
  generic ZIP or a different Office package); no member name may be
  absolute or contain a `..` segment; **no member may be encrypted**
  (flag bit 0 -- a plain `.docx` never is); **a non-empty member may not
  report a zero compressed size** (a physically impossible,
  crafted-central-directory value). Zip-bomb protection is enforced at
  **two levels**: (1) per-member -- the entry count, each member's
  uncompressed size, and each member's decompression ratio each stay
  under a conservative cap; **and (2) aggregate -- the total uncompressed
  size across all members, and the whole-archive decompression ratio,
  each stay under a conservative cap**, so an archive whose members each
  pass individually but together expand hugely is still rejected. No
  `word/vbaProject.bin` (rejects a macro-enabled `.docm` renamed to
  `.docx`). All caps are module-level constants in `file_validation.py`
  (a real Word document is orders of magnitude smaller than every one of
  them) so a test can monkeypatch a tiny value instead of allocating a
  real large archive.
- Rejected: empty files, extension/MIME/signature mismatches,
  unsupported formats, SVG, HTML, legacy DOC, macro Office files,
  executables, malformed containers.
- `validate_signature` / `inspect_docx_zip` is the **explicit seam for a
  future real malware scanner** -- nothing here claims to *be* one; it
  only proves container/byte structure.

### F. Storage and configuration

- `MATERIAL_STORAGE_ROOT` (relative resolves from the project root) is a
  **private, non-executable directory outside `app/static`**. The final
  stored name is `token_hex(24).<ext>`; the `.part` temp file lives in
  the **same directory** so `os.replace` is an atomic same-filesystem
  move. Every filesystem operation goes through `resolve_within_root`,
  which refuses a path that escapes the root. Restrictive permissions
  are applied best-effort (a no-op on Windows dev). **On any handled
  failure -- validation error, containment error, stale form,
  authorization failure, replay loss, or DB rollback -- deletion of the
  temp file and the just-written final file is *attempted* before the
  exception propagates.** `_safe_unlink` returns a success flag, **never
  raises**, and logs any OS-level deletion failure (with a traceback)
  through the `file_storage` module logger; `_cleanup_orphan_upload`
  adds one request-context line naming a key that could not be removed.
  So a cleanup failure can neither mask the original exception nor reach
  an HTTP response, and a rare undeletable file is visible in the server
  log for reconciliation -- the atomicity of the OS `unlink` itself is
  not something application code can absolutely guarantee.
- `resolve_material_config` validates the configuration **at start-up
  and fails closed** (`MaterialConfigError` -> the app refuses to start):
  root non-empty and not inside `app/static`; allowed extensions a
  non-empty subset of the hard set; positive size limits; the root's
  nearest existing ancestor usable as a directory. It **never creates a
  directory** (lazy `mkdir` on first upload), so a fresh checkout or a
  non-uploading test never gains a filesystem side effect.
- Flask `MAX_CONTENT_LENGTH` = the largest per-file limit **among the
  categories that actually have an enabled extension** + a small fixed
  64 KiB multipart overhead. A configured-but-disabled category never
  inflates the request limit -- e.g. with only `pdf` enabled the limit
  is the document limit, not the (disabled) video limit. Every size key
  is still validated as a positive integer at start-up even when its
  category is disabled (fail closed on a bad value). A friendly
  `errors/413.html` handles an over-limit body.
- **Honest limitation (deferred):** there is a small, unavoidable crash
  window between the atomic `os.replace` and the caller's DB commit -- a
  crash there leaves an unreferenced file with no `UploadedFile` row.
  M12 deliberately does **not** implement a reconciliation / retention
  cleanup job; it is recorded here as future work.

### G. Authorized serving and audit

- Separate fully-nested **Teacher** and **Student** routes for
  `.../materials/<material_public_id>/open` and `/download`. Teacher
  routes reuse M11's historical-access policy (available under an
  archived Unit/Group/ancestor while the assignment is active). Student
  routes require the full effective-visibility formula, SQL-scoped in
  `student_lessons.student_file_material` -- an archived Material, a
  draft Lesson, a non-`file` Material's public_id, a withdrawn/absent
  Enrollment, cross-group access, or a mismatched nested id all yield no
  row and a non-disclosing 404.
- `serve_uploaded_file` (shared core): `/download` always sends
  `as_attachment`; `/open` sends inline only for `image` / `audio` /
  `video` -- `document` (PDF/DOCX) always downloads as an attachment,
  even via `/open`. `Content-Type` comes from the stored server-validated
  metadata; the disposition filename uses Flask's RFC-safe
  `download_name`; `X-Content-Type-Options: nosniff` and
  `Cache-Control: private, no-store, max-age=0` are always set; HTTP
  Range requests are supported via `send_file(conditional=True)`.
- **Audit.** The initial `upload` log is written in the *same
  transaction* as the `UploadedFile` + `Material`. Every authorized
  serve writes exactly one row per HTTP request (`inline` for an inline
  response, `download` for an attachment, including a Range request).
  A missing physical file 404s **without a success log** and without
  leaking the path; if the audit row cannot be committed the file is
  **not served** (fail closed). Unauthorized requests never reach the
  logging path.

### H. Locking and idempotency

- Every Material mutation extends the M11 canonical lock order with two
  links:
  `AcademicTerm -> Level -> Course -> Group -> Teacher User ->
  GroupTeacherAssignment -> Unit -> Lesson -> Material rows (ascending
  id)`, under the **same single** `lock_academic_hierarchy` reset. The
  locked **Lesson** row serialises same-Lesson Material creation and
  ordering and is compatible with M11's own Lesson lock
  (publish/unpublish). A reorder re-reads the active order under the held
  locks, then locks the target + neighbour Material lowest-id-first
  before swapping. Authorization + the operational chain (M11's
  `_operational_block`) are re-checked against the locked rows before any
  write.
- **A file is streamed and validated to disk BEFORE any DB lock is
  taken** -- streaming a large upload must never hold a write lock. Once
  a final file exists, the route runs its lock / re-validate / insert
  under a `try/except/finally` gated by a `committed` flag: **every exit
  before a confirmed `db.session.commit()` -- a blocked redirect, a
  post-lock 404, a replay-loss redirect, an `IntegrityError`, an
  unexpected lock/DB error, or a rollback -- *attempts* deletion of the
  just-stored file** (containment-checked; `_safe_unlink` never raises
  and logs an OS-level failure, and `_cleanup_orphan_upload` logs one
  request-context line if the key could not be removed), while a
  successful commit leaves it untouched. Only
  a user-caused `FileValidationError` from `store_validated_upload` is
  caught and shown (its message is deliberately safe); any other
  exception from storage propagates, is logged by Flask, and surfaces as
  a generic 500 -- **never** as a flashed `str(exc)` that could leak a
  path, SQL, or driver text.
- **Creation idempotency.** Every create form carries a signed one-time
  token binding a random nonce to the Teacher + Lesson + kind
  (`materials.creation_nonce` `UNIQUE`). An ordinary replay (the nonce
  already names a committed Material) redirects to it and creates
  nothing -- checked as an early fast path *before* the form is even
  validated. A genuinely concurrent replay is caught: the post-lock
  re-check (or, failing that, the `UNIQUE` constraint at commit) makes
  the loser roll back, delete its own just-written file, and resolve to
  the winner's Material. Exactly one Material, one `UploadedFile`, one
  `upload` access log, and one physical file result system-wide.
- **Stale-edit snapshot** (`teacher.material-edit-snapshot.v1`): covers
  `public_id` + `title` + (per immutable kind) `content_html` /
  `external_url` / nothing more. `status`, `display_order`, and the
  uploaded-file metadata are excluded -- a completed archive / reorder by
  a co-teacher never stales an open edit form. Same PRG rejection /
  original-token-re-embed rules as the Group / Course / Schedule / Unit /
  Lesson snapshots.
  > **M13 amendment.** `search_keywords` is editable for every kind
  > (including `file` -- title + keywords now, still not the bytes/kind),
  > so each kind's snapshot tuple gains `search_keywords`
  > (`rich_text` -> `+content_html`; `external_link` -> `+external_url`;
  > `file` -> title + keywords only). A pre-M13 token is wrong-shaped and
  > rejected the same way. See "Authorized learning content search
  > (Phase 3, Part M13)".
- `IntegrityError` is caught, rolled back (with file cleanup where a file
  was written), and reported generically -- no SQL, no internal id, no
  driver text.

### I. UI

- Teacher: a "Manage Materials" link on every Lesson row (active and
  archived, historical access); a Materials page with ordered active
  Materials, a separate archived section, create (Rich Text / External
  Link / File), edit, archive/reactivate, move-up/down, open/download,
  clear type/category/status labels, empty states, and the
  published-visibility warning. A small **local** `contenteditable`
  editor (`app/static/js/material_editor.js`, `css/materials.css`) -- no
  CDN, no external editor library; the server sanitises regardless.
- Student: the M11 Lesson page renders active Materials in order --
  sanitised rich text, inline `<img>` / `<audio controls>` /
  `<video controls>`, external HTTPS links, and PDF/DOCX download
  actions -- with an honest empty state. No internal ids, no filesystem
  info, and no Teacher identity in any HTML / URL / flash / error.
- **Both** the Student Lesson page and the Teacher Materials list page
  receive plain view dicts from a scoped query layer -- the templates
  navigate no ORM relationship. `teacher_lesson_materials_view` returns
  a context dict of display strings + public ids plus the active and
  archived Materials each as plain dicts (`uploaded_file` joined once per
  list, no storage key / path / internal id), so the list-page query
  count stays bounded regardless of Material count.

### J. Migration and MySQL

`8319232a5609` (`Revises: cb9112548dd6`) creates only `uploaded_files`,
`file_access_logs`, and `materials` -- FKs, the payload/kind/status/size
CHECKs, the `(lesson_id, title)` + `public_id` + `creation_nonce` +
`uploaded_file_id` unique constraints, and the
`lesson_id` / `status` / `display_order` / file-history / actor-history /
time indexes. It alters no existing table. Applied and verified on the
real development MySQL database at the schema level (InnoDB, FK rules,
CHECK expressions, unique constraints, indexes, collation) with the
existing academic-table row counts unchanged.

### K. Dependency

`nh3==0.3.7` -- the only direct dependency added, used solely for
server-side HTML sanitisation. No `python-magic` (signature checks are
hand-rolled against a small vetted table); no rich-text editor library.

### L. Deliberate deferrals (M12+ / later)

Lesson/Material **completion, progress, and "recently opened" tracking**;
search; notifications; assignments / quizzes / activities; student
uploads; media transcoding / thumbnails / OCR; external embeds; an
Administrator Material-management UI; the storage reconciliation /
retention cleanup job (section F). No dependency was added for any of
these.

### M. Honest limitations

- SQLite (the test backend) has no `SELECT ... FOR UPDATE` and no
  REPEATABLE READ isolation -- the structural tests prove only the
  *requested* single reset + lock order, never real InnoDB blocking. No
  safe two-session MySQL concurrency probe was run for M12; the lock
  chain is the M11 pattern with a Lesson + Material tail, and existing
  rows were left untouched.
- `UNIQUE (lesson_id, title)` is case-insensitive on MySQL
  (`utf8mb4_0900_ai_ci`) and case-sensitive on SQLite -- the same
  portability nuance already noted for Group / Course / Unit / Lesson.
- Restrictive filesystem permissions are best-effort and largely inert
  on the Windows development host; path containment + application
  authorization + the audit log are the real boundary, not POSIX mode
  bits.
- Signature/container validation is **not** malware scanning; the
  validation-service boundary is where a real scanner would be inserted.
- A two-session MySQL race probe was still not run in the review pass;
  the loser-path cleanup and idempotency are covered by controlled
  race-path / mocked-branch tests, and the honest limitation above
  stands.

### N. Review-correction pass (post-M12, uncommitted)

A focused review-correction pass hardened the M12 implementation without
any schema change (migration stays `8319232a5609`; `flask db current` /
`heads` / `check` clean):

1. The Teacher file-create route no longer catches arbitrary
   `Exception` and flashes `str(exc)`: only a user-caused
   `FileValidationError` is shown; every other failure propagates to the
   generic 500 handler and is logged server-side (no path / SQL / driver
   text in the response).
2. Post-storage cleanup is now *attempted* for **every** handled failure
   before a confirmed commit via a `committed`-gated
   `try/except/finally` -- lock/DB failure, post-lock rejection, replay
   loss, `IntegrityError`, other commit exceptions, and rollback paths
   all delete the just-stored file; a successful upload's file is never
   removed.
7. `_safe_unlink` now returns a success flag, never raises, and logs any
   OS-level deletion failure (with a traceback) through the
   `file_storage` module logger; `delete_stored_file` propagates that
   flag and `_cleanup_orphan_upload` logs one request-context line when a
   key could not be removed. Cleanup failures are therefore visible in
   server logs, never reach an HTTP response, and never mask the original
   validation/database exception -- but application code does not claim
   that the OS deletion is absolutely guaranteed (docstrings and section
   F reworded accordingly).
3. DOCX ZIP-bomb validation gained aggregate caps (total uncompressed
   bytes, whole-archive compression ratio), plus rejection of encrypted
   members and of a non-empty member reporting zero compressed size --
   all alongside the existing per-entry / entry-count / traversal /
   required-entry / macro checks (section E).
4. `MaterialConfig.max_upload_bytes` / `max_content_length` now consider
   only categories with an enabled extension (section F).
5. The Teacher Materials list page moved behind a plain-dict
   presentation boundary (`teacher_lesson_materials_view`) -- no ORM
   navigation in the template, bounded query count (section I).
6. Added deterministic coverage: undisclosed-exception upload,
   post-storage cleanup on a non-`IntegrityError` failure, fail-closed
   serving when the audit log cannot persist, no success log on a denied
   file request, the concurrent-replay loser resolving to the winner and
   cleaning its own file, reactivation acquiring the Lesson + Material
   locks, and the list-page query-count bound.


## Authorized learning content search (Phase 3, Part M13)

A secure, **GET-only** Student search across the learning content that
already exists through M12 -- Courses, Units, Lessons, Materials -- plus
optional Teacher-authored search keywords on Teacher-managed Units,
Lessons, and Materials. Adds one nullable column to each of three tables
(`search_keywords`), one additive migration (`ed1e6c7c2548`,
`Revises: 8319232a5609`), three small services
(`app/services/search_terms.py`, `app/services/search_queries.py`,
`app/blueprints/student/search.py`), one Student template + one scoped
CSS file, and the keyword field on the existing Teacher forms. **No new
dependency, no Full-Text index, no new lifecycle.**

### A. Scope and role boundary

- The global search page is **Student-only**: `GET /student/search`.
  Anonymous users get the standard login redirect; every other
  authenticated role (`teacher` / `administrator` / `researcher`) gets
  **403** via `roles_required(STUDENT)`. There is deliberately **no**
  Teacher / Administrator / Researcher global-search page, and the
  existing Administrator resource-list searches (students, teachers,
  courses, ...) are untouched.
- Searchable content is exactly the four existing types. Assignments,
  Quizzes, Announcements, Phase 6 research events, adaptive
  help/spelling intervention, and any future content are **out of scope**
  -- no placeholder model, route, navigation, counter, or UI was added
  for them.
- Teachers may set search keywords only while creating/editing their
  assigned Groups' Units / Lessons / Materials, through the **existing**
  authorization, lifecycle, transaction, locking, and stale-form rules
  (M10 §E, M11 §I, M12 §H) -- M13 adds a field, not a new mutation path.

### B. Authorization and visibility (SQL-scoped, keyed by `current_user.id`)

Every result query enforces authorization **in the SQL `WHERE` clause**
(`app/services/search_queries.py`) -- content is never loaded broadly and
filtered in Python, and no template navigates an ORM relationship. A
result is visible to a Student **iff** all hold:

- their own **active** `Enrollment` for the exact Group;
- active `AcademicTerm`, `Level`, `Course`, `Group`;
- active `Unit` (for Unit / Lesson / Material results);
- **published** `Lesson` (for Lesson / Material results);
- active `Material` (for Material results).

This is exactly the M11/M12 effective-visibility formula. Student search
**does not** depend on a `Schedule` or on any current Teacher assignment.
Unauthorized, withdrawn, archived, draft, cross-Group, mismatched, and
non-existent content never affects results, ranking, snippets, filter
choices, or the `has_more` indicator.

A **Course result is Group-contextual**, not globally de-duplicated: the
same Course appears once per authorized enrolled Group, because its
destination (that Group's learning outline) and breadcrumb differ.

### C. Teacher-defined keywords -- `search_keywords`

- Nullable `search_keywords VARCHAR(500)` on `units`, `lessons`,
  `materials` (migration `ed1e6c7c2548`). **Not** on `courses` -- a
  Course is Administrator-owned and already searchable by title, code,
  and description.
- One shared pure helper,
  `app/services/search_terms.normalize_search_keywords`: accepts comma-
  or newline-separated input; trims and collapses internal whitespace;
  drops empty entries; **rejects control characters**; de-duplicates
  case-insensitively keeping the first spelling; **rejects** more than 20
  keywords, any keyword over 50 characters, or a canonical string over
  500 characters; stores a canonical `", "`-separated string or `NULL`.
- The field is on every relevant Unit / Lesson / Material create **and**
  edit form (`_SearchKeywordsMixin` in
  `app/blueprints/teacher/forms.py`), rendered only as autoescaped plain
  text -- never HTML, never `| safe`. It is editable for **every**
  Material kind, **including `file`** (whose bytes and `kind` stay
  immutable) -- this extends the M12 "file edit changes title only" line
  to "title + keywords".
- `search_keywords` is included in every relevant signed stale-edit
  snapshot (`_UNIT_EDIT_SNAPSHOT_FIELDS`,
  `_LESSON_EDIT_SNAPSHOT_FIELDS`, and each branch of the Material
  `_snapshot_fields(kind)`), so a concurrent keyword edit by a
  co-teacher cannot be silently overwritten. A pre-M13 snapshot token
  (missing `search_keywords`) is wrong-shaped and rejected with the
  existing PRG-to-fresh-GET path -- the same precedent as M07C2's Group
  snapshot field-count change. Lifecycle, ownership, ordering,
  publication, archiving, file, and lock semantics are unchanged.

### D. Query normalisation and matching

`app/services/search_terms.normalize_query` deterministically: strips and
collapses whitespace; truncates to 100 characters; requires at least 2
characters before a search runs (`too_short`); splits into at most 8
lower-cased whitespace tokens. Matching is **AND across tokens, OR across
the authorized fields of one result type**, case-insensitive
(`func.lower(col).like(...)` with both sides lower-cased for
SQLite/MySQL portability). `%`, `_`, and the escape character are
neutralised by `escape_like` so they match **literally**; every value is
bound through SQLAlchemy -- there is no raw SQL fragment.

Searchable fields: Course = title, code, description; Unit = title,
description, keywords; Lesson = title, description, keywords; Material =
title, keywords, external URL, and the joined `UploadedFile`
`original_filename`. Raw rich-text HTML, physical file contents, storage
keys, hashes, paths, uploader identity, access logs, and internal numeric
ids are **never** searched or exposed. No PDF/DOCX/image/audio/video
content is parsed or OCR'd.

### E. Ranking, bounds, and filters

- Deterministic relevance bucket (`sqlalchemy.case`, lower is better):
  1 exact title/code; 2 title/code prefix; 3 every token within
  title/code/keywords; 4 otherwise (matched only description / Material
  metadata). Tie-breaks -- hierarchy `display_order` then internal `id`
  -- are used **only inside `ORDER BY`**, never selected or exposed.
- Fixed cap of **20 results per type**; each query fetches `limit + 1` to
  derive a non-disclosing `has_more` -- no separate COUNT query, no
  unbounded search. The query count is **bounded**: one
  `authorized_search_groups` query plus one query per requested content
  type (so 2 when a type filter is set, at most 5 otherwise), regardless
  of result volume.
- Filters: content type (all/course/unit/lesson/material); one Group,
  chosen only from the Student's authorized active enrollments by
  **public id**; Material kind (all/rich_text/external_link/file).
  Invalid `type` / `kind` values normalise to `all`. An unknown or
  unauthorized `group` value produces an **empty, non-disclosing** result
  (the route short-circuits without revealing whether the Group exists).

### F. Presentation and navigation

- Results are grouped by content type; each result carries an escaped
  title, a type badge, a hierarchy breadcrumb, a safe plain-text snippet
  (built around the first token hit; may quote a matched Teacher
  keyword), and an authorized destination. Destinations: Course -> the
  Student Group learning outline; Unit -> its `#unit-<public_id>` anchor
  on that outline; Lesson -> the Student Lesson detail page; Material ->
  its `#material-<public_id>` anchor on that page. Anchors use **public
  ids only**.
- The Student portal header gains a shared **Search** link
  (`app/templates/student/_portal_nav.html`, included by the Student
  dashboard / outline / lesson / search templates). Teacher and
  Administrator navigation is unchanged.
- States: initial (before a valid query); too-short guidance; no-results;
  clear-filters (preserves `q`); clear-search; static search tips; and a
  refine-search notice when a type group is capped. No personalized
  spelling suggestion or adaptive intervention -- deferred.
- Responses are `Cache-Control: private, no-store` and `Vary: Cookie`.

### G. Migration and MySQL

`ed1e6c7c2548` (`Revises: 8319232a5609`) adds only the three nullable
`search_keywords VARCHAR(500)` columns (`units`, `lessons`, `materials`)
and nothing else -- no constraint, index, or existing-row change.
Existing rows stay valid with `NULL`; there is no content backfill.
`UNIQUE`/`LIKE` case behaviour follows the project's existing MySQL
(`utf8mb4_0900_ai_ci`, case-insensitive) vs SQLite (case-sensitive)
portability nuance; the query layer lower-cases both sides so matching is
case-insensitive on both engines.

### H. Deliberate deferrals and limitations

- The bounded `LIKE`/`ILIKE` implementation is deliberate for the current
  center scale and SQLite/MySQL portability. A dedicated indexed search
  projection (or Full-Text index) may replace it **if measured data
  volume later warrants it** -- no dependency or index was added now.
- Deferred: search over Assignments / Quizzes / Announcements / research
  events / adaptive interventions; personalized spelling suggestions;
  completion / progress / "recently opened" signals in results;
  relevance tuning beyond the four fixed buckets; a non-Student search
  surface.
- SQLite (the test backend) is case-sensitive for `LIKE` and lacks
  MySQL's collation; the query layer's explicit `lower()` on both sides
  is what makes the tests meaningful for matching behaviour. Real MySQL
  collation/'`A1`'/literal-`%` behaviour was checked with a
  rollback-only smoke probe (see the M13 report).

## Stored in-app notifications (Phase 3, Part M14)

A **stored, server-authorized in-app notification inbox** for Student and
Teacher users, generated automatically from the high-signal domain events
that already exist through M13. Adds one table (`notifications`), one
additive migration (`023a5f5814a8`, `Revises: ed1e6c7c2548`), one shared
blueprint (`/notifications`), three services
(`app/services/notification_targets.py`,
`app/services/notification_delivery.py`,
`app/services/notification_queries.py`), one template, one scoped CSS
file, and a Notifications link + unread badge in the shared portal
header. **No new dependency, no scheduler, no worker, no queue, no
polling, no WebSocket, no email, no browser push.**

### A. What M14 is -- and what it is deliberately not

- A notification is an **automatic, personal, historical record** of
  something that already happened: it is written once, immediately after
  a domain mutation has committed, and afterwards only its `read_at` ever
  changes.
- M14 is **not Announcements**. There is no way for any human to compose,
  address, schedule, edit, or broadcast a message. Administrator-authored
  Announcements remain a Phase 4 module with its own model, authoring UI,
  audience rules, and lifecycle; nothing here is a placeholder for it.
- M14 is also not messaging, email, browser push, WebSockets, polling,
  scheduled reminders, notification preferences, or an activity/audit
  log. The append-only `file_access_logs` audit trail (M12) stays a
  separate, operator-facing concern and is untouched.
- **Recipients are Students and Teachers only.** Administrator and
  Researcher have no inbox: the blueprint returns **403** for them, no
  producer ever selects them, and the shared header helper returns
  nothing for them (Administrator pages use `admin_base.html` and issue
  no notification query at all).

### B. Model -- `notifications`

`Notification` (`app/models/notification.py`): `id`, UUID `public_id`
(UNIQUE), `recipient_id` (FK to `users.id`, `NOT NULL`), `kind`
(VARCHAR(48), CHECK-constrained), plain-text `title` (VARCHAR(150)) and
`message` (VARCHAR(500)), `target_path` (VARCHAR(512), `NOT NULL`), UTC
`created_at` (`NOT NULL`), nullable UTC `read_at`.

- **Kinds are a closed set**, enforced twice from one source: the
  `NotificationKind` enum drives a SQLAlchemy `@validates` guard *and* is
  rendered into the `ck_notifications_kind_valid` CHECK constraint, so
  application and schema cannot drift. The seven kinds are
  `enrollment_activated`, `enrollment_withdrawn`,
  `teacher_assignment_activated`, `teacher_assignment_removed`,
  `schedule_changed`, `lesson_published`, `material_available`. There is
  no generic "other" kind and no placeholder for a future module.
- **Content is plain text and self-contained.** No HTML, no serialized
  ORM row, no JSON payload, no `source_type`/`source_id` polymorphic
  pointer, no `actor_id` -- a notification never says *who* did something,
  only *what* is now true, and it names a place rather than a row. Only
  object *names* the recipient may already see (Group name, Lesson /
  Material title) appear in text; no internal numeric id ever does.
- **Role integrity boundary.** `recipient_id` is a plain FK into the
  shared `users` table, so -- exactly as already documented for
  `Enrollment.student_id` and `GroupTeacherAssignment.teacher_id` -- it
  proves existence only, never role or active status. Every producer
  re-verifies role **and** active account in the SQL that selects
  recipients.
- **No cascade, no hard delete, no deletion route** (single or bulk).
  Archiving a Group, withdrawing an Enrollment, or unpublishing a Lesson
  never rewrites or removes a notification.
- **Two** composite indexes, because the two inbox filters sort on
  different key suffixes and one index cannot serve both without a sort
  step:
  - `ix_notifications_recipient_unread_created` (`recipient_id`,
    `read_at`, `created_at`) -- covers the header unread `COUNT`
    outright and orders the newest-first `unread` filter;
  - `ix_notifications_recipient_created_id` (`recipient_id`,
    `created_at`, `id`) -- orders the newest-first `all` filter. The
    unread index cannot do this: `read_at` sits between the
    `recipient_id` equality and the `created_at`/`id` sort columns, so
    MySQL resolves `ORDER BY created_at DESC, id DESC` against it with
    `Using filesort` (measured -- see §I).

  Their shared `recipient_id` leftmost prefix is also what the
  `recipient_id` foreign key uses, so no separate single-column index is
  declared for it.

### C. Event matrix

Notifications are produced **only** after these existing mutations
succeed:

| # | Event | Recipients | Target |
|---|---|---|---|
| 1 | Enrollment created or reactivated | that Student | Student dashboard |
| 2 | Enrollment withdrawn | that Student | Student dashboard |
| 3 | Teacher assignment created or reactivated | that Teacher | Teacher dashboard |
| 4 | Teacher assignment removed | that Teacher | Teacher dashboard |
| 5 | Schedule created / edited / archived / reactivated | every currently active, eligible Student **and** Teacher of that Group | each recipient's own role dashboard |
| 6 | Lesson published or republished | Students who can effectively reach the Lesson now | that Student Lesson page |
| 7 | Material created or reactivated | Students who can effectively see it now (requires a **published** Lesson) | that Lesson page, anchored `#material-<public_id>` |

Deliberately **not** producers: Unit create / edit / reorder / archive;
Lesson create, edit, reorder, or **unpublish**; Material edit, archive, or
reorder; a Material on a **draft** Lesson; account changes; and every
read, search, dashboard, or blocked/rolled-back mutation. A rejected
mutation writes nothing and therefore reaches no producer at all -- there
is no separate "was it blocked?" check that could fall out of sync.

A Material has no standalone Student page (rich text and external links
render inline on the Lesson page, and a `file` Material is opened from
there), so event 7's target is the Lesson page plus a `#material-`
anchor -- the same destination M13 search already uses for a Material
result.

The Schedule message names *what kind of* change happened ("A class time
was added / changed / removed / restored") but never the weekday, time,
or location: the dashboard is the single source of truth for the current
timetable, and a stored message must not become a stale second copy of
it.

### D. Effective visibility -- reused, not re-invented

Recipient selection is **SQL-scoped, bounded, deterministic, and
de-duplicated** (`app/services/notification_delivery.py`). It never loads
rows broadly and filters in Python, and it never trusts its caller for
who may see what:

- events 1-4 re-check the single recipient's **role** and **active
  account**;
- event 5 requires an ACTIVE `Enrollment` (Student) or ACTIVE
  `GroupTeacherAssignment` (Teacher) **plus** the matching role **plus**
  an ACTIVE account -- the same "valid active seat / eligible teacher"
  definitions as `app/services/group_memberships.py`;
- events 6-7 apply the full M11/M12/M13 effective-visibility formula in
  one `WHERE` clause: own ACTIVE `Enrollment`, ACTIVE `AcademicTerm` /
  `Level` / `Course` / `Group` / `Unit`, **published** `Lesson`, and (for
  a Material) ACTIVE `Material`, plus Student role and ACTIVE account.

Consequences: a suspended account, a withdrawn Student, a removed
Teacher, a corrupted membership row referencing the wrong role, a Student
in another Group, and anything under an archived link of the chain are
all excluded. Recipients are never exposed to one another, and a Teacher
never learns Student identities from a notification.

### E. Failure isolation -- post-commit, best effort, **not exactly-once**

Notification delivery is never inside the locked core domain
transaction. Every producer call site follows the same shape:

1. the domain mutation commits with its **existing, unchanged** locking,
   validation, stale-form, and error handling -- no lock order was
   changed and no lock set was widened;
2. the route captures plain scalars (ids, public ids, display names) --
   never an ORM row carried across the transaction boundary;
3. the route builds its response;
4. delivery runs last, in its own new transaction, taking no domain
   locks;
5. any failure rolls back only that transaction, logs server-side, and is
   swallowed -- `_deliver` never raises.

So a notification failure can never turn a successful enrollment,
assignment, schedule change, lesson publication, or material creation
into an error. **File materials get particular care**: delivery happens
strictly after `committed = True` and after the `finally` block that owns
orphan-file cleanup, so a delivery failure can never delete a committed
file, roll back the `UploadedFile` / `FileAccessLog` rows, or be reported
to the Teacher as a failed upload.

When a producer selects no recipients it writes nothing and deliberately
leaves its read-only transaction to ordinary request teardown rather than
issuing an explicit rollback: the M08/M11/M12 lock-order regression tests
assert that a mutation request performs exactly **one** deliberate
transaction reset (the one owned by `lock_academic_hierarchy` /
`lock_group_for_write`), and no lock is held by the time a producer runs.

**Honest limitation.** This synchronous, post-commit design is *at most
once*, not exactly once: a process crash, lost connection, or database
error in the window between the domain commit and the notification commit
loses that notification permanently. There is no retry, outbox,
dead-letter, or reconciliation job. The alternative -- writing
notifications inside the domain transaction -- would let an optional
convenience feature roll back real academic work, which is strictly
worse. A durable transactional outbox is **deferred**.

### F. Target safety -- generated once, validated twice

`app/services/notification_targets.py` is the only source of a
`target_path` and the only validator of one.

- Targets are built from already-known **public ids** through the
  application's own URL map -- not by string concatenation (a route
  rename would silently break it) and not through Flask's request-bound
  URL helper (a post-commit producer must not depend on an active request
  context, or a future non-request caller would lose every notification
  silently).
- `validate_notification_target(role, candidate)` is **fail-closed** and
  rejects, with no attempt to repair: a role with no inbox; absolute and
  protocol-relative URLs; any scheme or network location; backslashes;
  control characters; a query string; `.` / `..` segments and doubled or
  trailing slashes; an unexpected fragment; anything over the column
  width; and anything outside the recipient role's own namespace
  (Student -> `/student/`, Teacher -> `/teacher/`, including lookalike
  prefixes such as `/studentx/`). This is deliberately stricter than
  `app.security.redirects.get_safe_redirect_target`, which answers a
  different question (may I follow a *user-supplied* `next` value?) and
  has no role namespace. The `..` rule matters concretely: Werkzeug does
  not escape `/` inside a string URL parameter, so a hostile value
  reaching a builder would otherwise produce a path a browser normalises
  out of the namespace.
- A stored target is **never rendered as a link**. Opening a notification
  POSTs to a notification-owned route, which marks the row read, commits,
  re-validates the stored value against the *current* recipient's role,
  and only then redirects. An invalid stored value (hand-edited in the
  database, or written by a future bug) logs server-side and falls back
  to the inbox with a generic message.
- **Passing validation is not authorization.** The destination route
  enforces its own current rules, so a notification is never proof of
  access: after a withdrawal, an unpublish, or an archive, the redirect
  is still issued but the destination returns its usual non-disclosing
  404.

### G. Inbox and routes

Shared blueprint under `/notifications`, `roles_required(STUDENT,
TEACHER)`:

- `GET /notifications` -- newest-first (`created_at DESC`, then `id
  DESC`, so rows written in one transaction still page stably),
  `all` / `unread` filters, fixed pages of **20** (fetching `limit + 1`
  to derive a non-disclosing "next page" without a COUNT), explicit empty
  states per filter, `Cache-Control: private, no-store`, `Vary: Cookie`.
  A page past the end shows page 1. Unknown `filter` / `page` values
  normalise instead of erroring.
- `POST /notifications/<public_id>/open` -- mark read, re-validate,
  redirect.
- `POST /notifications/<public_id>/read` -- mark read, stay in the inbox.
- `POST /notifications/read-all` -- one bounded UPDATE scoped to the
  recipient.

**GET never writes.** All three mutations are POST-only, CSRF-protected,
and idempotent (a second `open` / `read` never moves the original read
time; a second `read-all` matches nothing). Every per-notification lookup
includes `recipient_id == current_user.id`, so a missing `public_id` and
another recipient's `public_id` produce the identical **404**. Only
`public_id` appears in URLs and HTML. Times are stored UTC and rendered
in `APP_TIMEZONE` via the M09 `to_app_local` utility, with the timezone
label shown; templates receive plain presentation dicts, never ORM rows.

### H. Shared header and the unread badge

`layouts/portal_base.html` -- used by Student and Teacher pages only --
carries the Notifications link and unread badge, so every portal page
gets them without each page's `portal_nav` block opting in. The context
processor injects a **callable**, so a template that never calls it
(every Administrator page, login, the error pages) costs no query at all.

- `header_badge(user)` returns `None` for anonymous users and for every
  role without an inbox, with **no** query.
- For a Student or Teacher it issues exactly **one** bounded, indexed
  `COUNT`, memoised on the request object so a request rendering several
  templates still pays once. (Memoised on the request rather than on
  `flask.g`: `g` is app-context scoped and an app context can outlive a
  request, and an identity-based key would be unreliable because CPython
  reuses object addresses.)
- The display caps at `99+`; unread state in the list is carried by an
  explicit "Unread" badge and a bolder title, not by colour alone.
- **The count fails open to zero.** It runs on its **own** connection
  checked out from the shared engine, never through `db.session`, so a
  missing table during a partial deploy, a broken query, or a lock
  timeout cannot poison the request session and take the dashboard,
  lesson, material, or search page down with it. Any exception is logged
  server-side and the badge simply disappears; no driver or SQL detail
  reaches the page.

Existing bounded-query regression tests (Student dashboard, Teacher
dashboard, Student outline) still pass **unchanged**: the single header
query fits inside their existing bounds, so no bound was loosened and no
N+1 behaviour is masked.

### I. Migration and MySQL

`023a5f5814a8` (`Revises: ed1e6c7c2548`) creates `notifications` and its
two indexes, and nothing else -- no existing table, column, index,
constraint, or row is touched, and there is **no data backfill**.
Notifications describe only events that happen after the migration runs,
so pre-existing Enrollments, assignments, Schedules, Lessons, and
Materials deliberately produce no historical rows. The downgrade is
symmetric: both indexes are dropped in the reverse of their creation
order, then the table. Engine, charset, and collation follow the existing
project defaults (InnoDB, `utf8mb4_0900_ai_ci`); isolation remains
REPEATABLE-READ.

Measured on the development MySQL 8.0 database with real `EXPLAIN`.
With only the unread index present, the `all` filter's
`WHERE recipient_id = ? ORDER BY created_at DESC, id DESC LIMIT 21`
planned as `ref` on `ix_notifications_recipient_unread_created` with
**`Using filesort`** — `read_at` sits between the equality column and the
sort columns, so that index cannot supply the ordering. Forcing the
`all` query onto it still reports `Using filesort` at **every** data
size tested, which is the direct evidence that a second index was
needed.

Plans after adding `ix_notifications_recipient_created_id` (probe rows
inserted inside a rolled-back transaction; one recipient holding ~10% of
the table, which is the production shape):

| Query | Chosen index | `Extra` | filesort |
|---|---|---|---|
| `all` page (≥ ~1 000 rows for that recipient) | `ix_notifications_recipient_created_id` | `Using index condition; Backward index scan` | no |
| `unread` page | `ix_notifications_recipient_unread_created` | `Using where; Backward index scan` | no |
| header unread count | `ix_notifications_recipient_unread_created` | `Using where; Using index` | no |

**Honest qualification.** Below roughly a thousand notifications for one
recipient the optimizer still chooses the unread index and sorts, because
sorting a few hundred rows really is cheaper than the extra index scan —
that is the cost model working correctly, not a missing index. Forcing
the new index at those sizes confirms it produces `Backward index scan`
with no sort. The index therefore removes the *unbounded* growth in sort
cost as a recipient's history accumulates, which is what matters; it is
not expected to change the plan for a nearly empty inbox.

The table carries exactly these two secondary indexes plus the
`public_id` UNIQUE; the `recipient_id -> users.id` foreign key uses their
shared leftmost prefix rather than a duplicate of its own, which is why
the model deliberately does **not** also mark `recipient_id` as
`index=True`.

Revision `023a5f5814a8` was already applied when the second index was
added, so the index was brought to the existing development table with a
single additive, inspect-first, idempotent `CREATE INDEX` (no downgrade,
no table recreation, no row touched); `flask db check` then reported no
new operations, confirming model and schema agree.

### J. Deliberate deferrals

Deferred to later phases, with no placeholder model, route, column,
navigation entry, or counter added now: Administrator-authored
**Announcements** (Phase 4); real-time delivery (WebSocket / SSE /
polling); email and browser push; scheduled or digest reminders;
per-user notification preferences, mute, or unsubscribe; grouping,
collapsing, or de-duplicating repeated events; notification deletion,
bulk deletion, or retention/pruning; a durable transactional outbox with
retry and dead-lettering; Administrator or Researcher inboxes; and
producers for Assignments, Quizzes, Attendance, Grades, Payments,
Calendar, Research events, and messaging.

### K. Honest limitations

- Delivery is **at most once** (see §E) -- a rare post-commit failure
  loses a notification silently, visible only in the server log.
- Automated tests run on SQLite in memory. They prove the application
  logic, the SQL scoping, the query structure, and the model/schema
  alignment; they do **not** prove MySQL/InnoDB blocking, isolation,
  collation, or that the migration runs on MySQL. Migration and schema
  behaviour were verified separately against the real development MySQL
  database (see the M14 report).
- The header count's failure isolation depends on a genuinely separate
  pooled connection. That holds on MySQL; on the SQLite in-memory test
  backend the engine uses `StaticPool`, so the "separate" connection is
  physically the same DBAPI connection and the tests exercise the
  isolation only structurally -- the same class of limitation already
  recorded for `SELECT ... FOR UPDATE`.
- Targets assume the application is mounted at the URL root. A deployment
  under a path prefix would produce targets outside the role namespace,
  which the validator rejects **fail-closed** -- notifications would stop
  opening rather than redirect somewhere unsafe.
- A notification's stored text is a snapshot. If a Group, Lesson, or
  Material is later renamed, older notifications keep the old wording on
  purpose: they are history, not a live view.
- Notifications remain visible after the recipient loses access to what
  they describe. That is intended (they are personal records), and it
  means an old notification's *text* can still name a Group or Lesson the
  recipient can no longer open.


## Group-owned Assignments (Phase 4, Part M01)

The first Assignments milestone: assigned Teachers author, edit, publish
and unpublish Group-owned Assignments; authorized Students see the ones
that are published **and** already open, plus a bounded list of upcoming
deadlines on their dashboard. Submissions, uploads, feedback, grading,
quizzes, notifications, search, calendar and progress are deliberately
absent -- see §L.

### A. Direct Group ownership, and the columns that are not there

An `Assignment` belongs **directly** to exactly one `Group`, through a
required, non-cascading `group_id`.

- No `course_id` / `level_id` / `academic_term_id`: all three are already
  determined by the Group (`assignment.group.course.level`,
  `assignment.group.academic_term`). Storing them again would create a
  second, divergable source of truth -- the same reasoning already
  applied to Enrollment, GroupTeacherAssignment, Schedule, Unit and
  Lesson.
- No `unit_id` / `lesson_id`. An Assignment is **Group work**, not a
  child of one teaching Lesson. Attaching it to a Unit or Lesson would
  make its visibility depend on that container's lifecycle, and would
  force a Teacher to invent a Lesson before setting any task.
- No `teacher_id` / `created_by_id`. Every **active** assigned Teacher of
  the Group is an equal collaborator: co-teachers list, create, edit,
  publish and unpublish each other's Assignments identically. An owner
  column would either be decorative (never enforced) or would silently
  lock a colleague out when a Teacher's assignment is removed. The Group
  is the unit of responsibility, and `GroupTeacherAssignment` already
  records who is currently responsible for it.
- No `display_order`. Assignment presentation is **time-driven**; the
  internal `id` breaks ordering ties inside SQL only and is never
  exposed in a URL, a form value, or the rendered HTML.

`Group.assignments` / `Assignment.group` are the ORM inverse of that one
foreign key, so the `groups` table needs no schema change (§J).

### B. Schema and constraints

`assignments`: BigInteger `id` (SQLite `Integer` variant), unique
non-null UUID `public_id`, `group_id`, `title` `String(150)`, required
plain-text `instructions` `Text`, naive-UTC `opens_at` / `due_at`,
`status`, nullable UTC `published_at`, UTC `created_at` / `updated_at`.

Four named constraints are the **final** integrity defense; every one is
also checked in the application first, so the database is never the
mechanism that produces a user-facing message:

- `uq_assignments_group_title` -- one title per Group, **drafts
  included**, so a teaching plan never carries two same-named
  Assignments. The same title in another Group is fine.
- `ck_assignments_opens_before_due` -- `opens_at < due_at`; equal and
  reversed windows are rejected outright.
- `ck_assignments_status_valid` -- the closed `draft` / `published` set,
  rendered into the constraint from the enum itself so the
  `@validates` guard and the schema cannot drift.
- `ck_assignments_status_published_at_consistency` -- a draft has
  `published_at IS NULL`; a published row has it NOT NULL.

`instructions` is plain text in an unbounded `Text` column. The finite
boundary that actually protects a request is the form's
`Length(max=10000)` in `AssignmentForm`; it is rendered autoescaped with
line breaks preserved by CSS and **never** through `|safe`.

**Indexes.** Exactly two, each driven by a real M01 query shape:

- `ix_assignments_group_due_id` (`group_id`, `due_at`, `id`) -- the
  Teacher list, which is a **single-Group** equality on `group_id`
  ordered by deadline, so the ordering columns follow the equality column
  directly. It lists draft **and** published rows together, which is why
  `status` is deliberately *not* in the middle: that is precisely the
  shape M14 measured resolving as `Using filesort` on `notifications`,
  because a column between the equality column and the sort columns
  cannot supply the ordering.
- `ix_assignments_group_status_opens_due` (`group_id`, `status`,
  `opens_at`, `due_at`) -- the Student visibility **filter**: equality on
  `group_id` + `status`, then the `opens_at <= now` range.

**What the second index is not claimed to do.** It does **not** supply
the Student list's or dashboard's global `due_at` ordering, for three
independent reasons: `opens_at` is a *range* predicate, so columns after
it cannot generally be assumed to provide ordering; the Student list
spans **every** Group the Student is enrolled in rather than one; and the
list's current/history ordering is a `CASE` expression (§G), which
requires a sort of its own. Those reads are expected to sort, and the
index earns its place by narrowing what has to be sorted.

Both indexes and `uq_assignments_group_title` begin with `group_id`, so
the `group_id` foreign key already has a usable leftmost prefix and
**no** separate single-column index is declared for it -- the same
reasoning M14 applied to `notifications.recipient_id`.

**No MySQL execution plan has been measured for this table.** Unlike M14,
whose two `notifications` indexes were chosen from real `EXPLAIN` output,
both index choices here are a *reasoned design* pending an authorized
real `EXPLAIN` (§K). Nothing in the schema was changed on speculation.

### C. Time: UTC in the database, local only at the edges

`opens_at`, `due_at` and `published_at` are stored as **naive UTC**.
They are entered and rendered in `APP_TIMEZONE`, and every page showing
one also shows the timezone label.

`app/services/schedule_occurrences.py` -- the module that already owns
`to_app_local` / `app_now` -- gains the inverse rather than a second
implementation, so the fixed-offset parser, the IANA lookup, the
deployment fallback table (`Africa/Tripoli` -> +02:00) and the
fail-closed `TimezoneConfigError` behave identically in both directions:

- `from_app_local(tz_name, local_moment)` takes a **naive local
  wall-clock** value and returns naive UTC. An **aware** value is
  rejected with `ValueError`: it already carries its own offset, and
  reinterpreting it as a wall clock in `APP_TIMEZONE` would discard that
  silently.
- `utc_reference_now(utc_now=None)` yields the single naive-UTC
  reference moment for one request or test.

**DST is never guessed.** For a real IANA zone the value is interpreted
at both folds and validated by round trip: if converting back from the
`fold=0` instant does not reproduce the submitted wall clock, the time
falls in a spring-forward **gap** and does not exist; otherwise, if the
two folds land on different UTC instants, it is a fall-back **repeat**
and is genuinely ambiguous. Both raise `LocalTimeError`, a `ValueError`
subclass whose message is safe to render verbatim as a form validation
error. The order matters -- both cases produce two instants, and only the
round trip separates a gap from a repeat. Silently choosing `fold=0` or
`fold=1` would place a deadline an hour from what the Teacher typed, in
the one direction nobody would check. `LocalTimeError` is deliberately
distinct from `TimezoneConfigError`: the latter means the deployment is
misconfigured and nothing can render at all.

**One reference moment per request.** Routes call `utc_reference_now()`
exactly once and pass the result down through the query layer and the
presentation builders; nothing below the route reads a clock. The Student
dashboard derives *both* its local schedule `now` and its UTC deadline
reference from one `datetime.now(timezone.utc)` call, so a response that
straddles a deadline can never contradict itself. Tests inject the moment
and move time instead of waiting.

**Seconds survive an edit round trip.** The columns are `DATETIME`, so
they carry seconds, and the `datetime-local` controls render
`%Y-%m-%dT%H:%M:%S` with `step="1"`. WTForms renders using the *first*
accepted format, so a minute-only render would have let a Teacher who
only fixed a typo in the title silently truncate a stored `08:00:37` to
`08:00`. The invalid shape for `datetime-local` is the *space*
separator -- not seconds -- and WTForms' own default starts with a
space-separated format, which is why the list is set explicitly. Ordinary
minute-only input stays accepted. No offset is ever put into a
`datetime-local` value, and the schema is unchanged.

Schedule wall-clock storage semantics (the M08 decision -- weekly
`start_time` / `end_time` are local civil values, never converted to UTC)
are **unchanged**.

Assignment times are deliberately **not** constrained to the
AcademicTerm's date range. A deadline after the formal end of a term is
legitimate, and no such business rule is approved.

### D. Derived states -- computed, never stored

`AssignmentStatus` is its own closed set (`draft`, `published`) --
separate from `LessonStatus` so the two can never be redefined by
accident, and emphatically not `AcademicStatus`: an Assignment is never
*archived*.

Presentation states are derived from the reference moment and are **not**
enum members and **not** columns:

- **Scheduled** (Teacher-only): published, but `now < opens_at`;
- **Open**: `opens_at <= now < due_at`;
- **Past due**: `now >= due_at`.

Because they are computed, the passage of time can never leave a stale
status in a row, and no scheduled job is needed to "flip" anything.

A published Assignment is **not** Student-visible before `opens_at`, and
**remains** visible at and after `due_at`. M01 has no submission route,
so "Past due" is informational only -- withdrawing the row at the
deadline would hide the record of what was set.

### E. Lifecycle

Create always starts `draft` with `published_at = NULL` -- both set from
constants in the route, never from the request, so a forged `status` or
`published_at` field has nowhere to land. Publish stamps the current UTC
moment; unpublish returns it to `draft` and clears the stamp; republish
stamps a fresh one. There is no archived state and no hard delete: the
blueprint exposes no delete route and no route accepts `DELETE`.

Editing never writes `status` or `published_at`, and a Group or ancestor
lifecycle change never rewrites either -- archiving hides an Assignment
from Students without touching the row, and reactivating restores
visibility without rewriting anything.

Create, edit and publish require -- re-checked against the **locked**
rows -- an active Teacher account with role `teacher`, an active
`GroupTeacherAssignment` to the exact Group, and an active
AcademicTerm / Level / Course / Group. **Unpublish stays available**
while the assignment is active even under an archived Group or ancestor,
so published work can always be withdrawn.

In M01 every editable field may be edited while those preconditions hold,
because no Submission rows exist yet. Post-submission edit and
immutability policy is **not** invented here.

### F. Teacher authorization and routes

Group-centered, public-id-only:

- `GET  /teacher/groups/<gpid>/assignments`
- `GET|POST /teacher/groups/<gpid>/assignments/new`
- `GET|POST /teacher/groups/<gpid>/assignments/<apid>/edit`
- `POST /teacher/groups/<gpid>/assignments/<apid>/toggle-publication`

`roles_required(TEACHER)` handles anonymous (login redirect) and
non-Teacher (403). Object authorization is then server-side: an **active**
`GroupTeacherAssignment` to the Group in the URL, and every nested lookup
constrained with `Assignment.group_id == group.id`. A missing Group, a
missing Assignment, another Group's Assignment `public_id`, an unassigned
Teacher and a removed assignment all return the identical non-disclosing
**404**. The GET list stays available under an archived Group/ancestor so
history can be read; publication changes are POST-only and CSRF
protected; the list is paginated in fixed pages of **20** (fetching
`limit + 1` to derive "next page" without a COUNT), and invalid, zero,
negative and absurd `page` values normalise to 1 rather than reaching the
database as an offset. Each Teacher dashboard Group card carries a
working **Manage Assignments** link.

### G. Student effective visibility -- proven by the SQL itself

- `GET /student/assignments`
- `GET /student/groups/<gpid>/assignments/<apid>`

A Student receives a row only when the query itself proves:

```text
User is the authenticated Student
AND User role/status are valid
AND Enrollment(student, group).status == active
AND AcademicTerm.status == active
AND Level.status  == active
AND Course.status == active
AND Group.status  == active
AND Assignment.status == published
AND Assignment.opens_at <= reference UTC time
```

All of it lives in one shared base query in
`app/services/assignment_queries.py`, keyed off `current_user.id` -- the
list, the detail page and the dashboard section are built from that same
query, so a future change cannot tighten one path and leave another open.
An Assignment is never loaded broadly and filtered in Python.

The `users` join is not redundant with the session: a foreign key into
`users` proves the row exists, never that it is still a Student or still
active -- the project rule already applied to Enrollment and
GroupTeacherAssignment.

Visibility does **not** depend on a Schedule existing, nor on any current
Teacher assignment; those answer different questions. Every failure --
draft, not-yet-open, withdrawn or absent Enrollment, archived ancestor,
cross-Group pairing, mismatched nested ids, non-existent id -- yields no
row and the identical non-disclosing 404. A suspended Student cannot hold
a session at all, and the query re-proves role and status regardless.

**List ordering: current work first, history after it.** A single
`due_at ASC` sequence was wrong and was corrected before acceptance.
Past-due Assignments stay visible on purpose, so the oldest deadline in a
Student's whole history sorted *first*, and one full page of old work
could push an Assignment whose deadline is approaching onto a later page
-- the opposite of what the page says it shows. The order is now, in one
SQL `ORDER BY` against the single injected reference moment:

1. bucket -- open (`due_at > reference`) before past due;
2. inside the open bucket, `due_at ASC` (nearest deadline first);
3. inside the past-due bucket, `due_at DESC` (most recent history first);
4. `Assignment.id ASC` as the deterministic final tie-break.

Two opposite directions cannot be one sort key, so terms 2 and 3 are
complementary `CASE` expressions: within either bucket exactly one of
them is non-NULL for every row of that bucket, so NULL ordering can never
mix the buckets. The bucket boundary matches the derived state exactly --
`now == due_at` is **past due**. Bucketing, ordering, offset and limit
all happen in that one statement; nothing is fetched broadly and
reordered in Python. Pages stay fixed at 20 with the same `limit + 1` and
`page` normalisation as the Teacher list, and the page copy states the
real behaviour rather than "nearest deadline first".

The Student templates receive plain presentation dicts throughout --
never an ORM row -- so rendering cannot lazy-load or make an
authorization decision, and no internal id reaches the HTML. (The
*Teacher* list is narrower: its Assignment rows are dicts, but the route
also hands that template the eagerly loaded `Group` object for the page
header, and the template says so.)

Both responses -- **and the Student dashboard**, which now carries the
same personal, time-gated Assignment data -- return
`Cache-Control: private, no-store` and `Vary: Cookie`: these pages are
per-Student and time-gated, so a shared or reused cache entry could show
one Student another's list, or an Assignment after it stopped being
visible. The one wrapper that sets both lives in `student/routes.py` and
is imported by `student/assignments.py`; `routes.py` imports nothing
back, so there is no cycle. The shared Student portal navigation gains an
**Assignments** entry.

### H. Student dashboard deadlines

A bounded "Upcoming assignment deadlines" section: the same visibility
query plus `due_at > now`, ordered nearest-first with the `id` tie-break,
capped at **5**. It is deliberately **stricter** than the list, which
keeps past-due rows as history -- so the §G bucketing would be a no-op
here and a plain `due_at ASC, id ASC` is the whole ordering. It is exactly **one** additional query regardless of how
many Groups or Assignments the Student has -- no per-Group or
per-Assignment follow-up read -- so a deadline can never appear for
something the Student could not open, and there is no N+1. Draft,
not-yet-open, past-due, withdrawn and archived-chain rows are excluded by
that query, not by the template. An honest empty state is shown when
nothing is upcoming. The existing M09 schedule sections are unchanged and
still use their injected local moment.

The Teacher dashboard gains **only** the Manage Assignments link. There
is no "Pending reviews" data: submissions and reviews do not exist yet.

### I. Concurrency and stale-edit protection

Every Teacher mutation follows one lock order in one transaction:

    AcademicTerm -> Level -> Course  (via lock_academic_hierarchy, which
    owns the single deliberate reset)
    -> Group -> Teacher User -> GroupTeacherAssignment
    -> Assignment rows (ascending internal id)

with **no** second reset. After locking, the Group's Course / Level /
AcademicTerm identity is re-checked, along with Teacher role, account
status, the active assignment, nested Assignment ownership, and the
hierarchy's status. No model field is assigned until every one of those
passes, so a rejection leaves no partial mutation. `IntegrityError` is
caught, rolled back before rendering or redirecting, and reported as
generic prose with no SQL, parameters, internal ids, or driver text.

The Group lock is what serializes same-Group Assignment creation against
an Administrator Group **retarget**, which takes the same Group lock --
so a new Assignment cannot slip past the identity freeze (§J).

**Signed stale-edit snapshot**, salt
`teacher.assignment-edit-snapshot.phase4-m01.v1`, covering exactly
`public_id`, `title`, `instructions`, and the canonical UTC `opens_at` /
`due_at` serialized with a fixed `%Y-%m-%dT%H:%M:%S` format so a token
round trip compares byte-for-byte. `status`, `published_at`,
`created_at` and `updated_at` are **excluded**, which is what makes a
concurrent publish or unpublish *not* stale an open edit form: the edit
route cannot overwrite those fields, so there is nothing to protect.

Following the established Group / Course / Schedule / Unit / Lesson
behaviour: a missing, malformed, invalidly signed, wrong-shaped or
cross-Assignment token is stale; so is one whose editable values no
longer match the current **locked** row (the check runs both before and
after the locks). A stale rejection is Post/Redirect/Get and discards
every attempted value, reloading current persisted state with a freshly
paired token -- a fresh token is never paired with stale attempted
values. An ordinary WTForms or business-rule failure re-embeds the
**original** still-valid token unchanged, for the same reason.

### J. Group identity freeze

`group_has_assignment_history(group_id)` returns true when **any**
Assignment row exists for the Group, draft or published, and joins
membership, Schedule and Unit history in
`app.blueprints.admin.groups._group_identity_frozen`. Publication status
is irrelevant: even a draft was authored against this Group's current
Course and AcademicTerm, so retargeting afterwards would silently
reinterpret it. The Administrator wording, help text, early pre-lock
check and authoritative post-lock recheck all now read "enrollment,
teacher-assignment, schedule, unit, or assignment history".

This is an identity freeze **only**. It adds no new ancestor archive
blocker, does not block archiving a Group that has Assignments, does not
cascade any lifecycle change into an Assignment, and changes no
Course-level identity rule beyond the wording.

### K. Migration and MySQL

`4f7c1d9b2e30` (`Revises: 023a5f5814a8`) creates `assignments`, its four
named constraints and its two indexes, and nothing else. No existing
table, column, index, constraint or row is touched -- `groups` in
particular is untouched -- and there is **no data backfill**: an
Assignment is authored work, so pre-existing Groups, Units, Lessons and
Enrollments deliberately produce no historical rows. The downgrade is
symmetric: both indexes are dropped in the reverse of their creation
order, then the table. Engine, charset and collation follow the existing
project defaults (InnoDB, `utf8mb4_0900_ai_ci`).

**The migration was not applied to any real database in this Part.** It
was verified by repository-only Alembic head/history inspection (single
head, linear chain) and by running `upgrade()` then `downgrade()` against
an **isolated temporary SQLite file**, which proves the operations are
internally consistent and genuinely reversible. It proves nothing about
MySQL/InnoDB DDL, and unlike M14 there is no real `EXPLAIN` evidence
behind the two index choices (§B) -- they are reasoned from the query
shapes and from M14's measured `filesort` result, not measured here. The
correction pass that fixed the list ordering deliberately left both
indexes and every migration operation untouched: changing DDL on
speculation, with no authorized `EXPLAIN`, would be worse than an
honestly documented open question.

### L. Deliberate deferrals

No placeholder table, column, route, UI element, enum value, counter or
TODO was added for any of the following.

Later Assignment milestones: `Submission`, submission attempts and
revisions, submission history, Student comments, duplicate-submit /
attempt nonces, submission states, late policy and late-until timestamps,
attempt limits, resubmission rules, allowed file types, maximum file size
and count, Teacher starter attachments, Student uploads, submission-file
storage / access logs / cleanup, teacher review state, and feedback.

Grades: numeric score, maximum points, categories, weighting, official
grade records, grade publication, Administrator grade reports.

Quizzes: quiz models, questions, answer options, attempts, saved answers,
timers, automatic grading, and any generic Activity superclass.

Integrations: Assignment search, Assignment notification kinds or
producers (`NotificationKind`, the notifications CHECK, the notifications
migration, delivery and targets are all untouched), deadline reminders,
scheduled jobs, Calendar events, Student progress/completion, and Teacher
pending-review dashboard data.

### M. Honest limitations

- Automated tests run on SQLite in memory. They prove the application
  logic, the SQL scoping, the query structure, the model/schema
  alignment and the *requested* lock order -- they do **not** prove
  MySQL/InnoDB row blocking, isolation, collation, index plans, or that
  the migration runs on MySQL. No real database was contacted.
- The development host has no IANA time-zone database, which is exactly
  why the deployment fixed-offset fallback exists. The real-IANA DST
  tests are therefore skipped there and run only where `tzdata` is
  installed; to keep the gap/repeat logic covered on every host, the same
  code path is additionally exercised against a purpose-built fold-aware
  `tzinfo` injected in place of the zone lookup.
- Rejecting ambiguous and nonexistent local times is deliberately strict:
  in a DST-observing deployment a Teacher genuinely cannot set a deadline
  inside the transition hour and must pick another time. On a fixed-offset
  deployment (including this project's `Africa/Tripoli`, +02:00
  year-round since 2013) the case cannot arise at all.
- Mid-session account suspension cannot be exercised faithfully in the
  test suite: the SQLite `StaticPool` backend makes every request reuse
  the fixture's session, so a row mutated from a nested app context is
  not re-read by the user loader. The tested contract is the real one --
  a suspended account cannot obtain a session at all.
- "Past due" is informational. M01 has no submission route, so nothing
  enforces the deadline; that enforcement arrives with Submissions.
- No browser, accessibility, responsive or real-concurrency verification
  was performed in this Part.
