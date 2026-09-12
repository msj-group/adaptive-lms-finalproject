"""The single deterministic lock chain for every Announcement write
(Phase 4 / M09).

Flask-independent -- no ``abort``, ``flash``, ``redirect`` or template
rendering, exactly like ``app/services/group_transactions.py``,
``app/services/quiz_transactions.py``,
``app/services/attendance_transactions.py`` and
``app/services/grade_transactions.py``. Route-level 404 / redirect /
flash handling belongs in the Blueprint modules that call this.

**One chain, one order, every M09 write** -- creation, draft edit,
publication and withdrawal, on the Teacher surface and on the
Administrator surface alike::

    AcademicTerm -> Level -> Course      (lock_academic_hierarchy, which
                                          owns the one deliberate reset)
    -> Group
    -> acting User
    -> GroupTeacherAssignment
    -> Announcement

There is deliberately one function rather than one per route: the order
is the thing that must not vary, and several near-identical functions
would be several chances for it to. A caller passes only the links its
scope actually has, and the chain simply stops where the arguments stop:

* a **center** announcement locks no academic row and no Group at all --
  it still resets the transaction first, then locks the acting User and
  (for an edit, publish or withdraw) the Announcement;
* a **course** announcement locks that Course's Level and the Course,
  then the acting User, then the Announcement. A Course belongs to no
  AcademicTerm, so no term is locked -- and none is invented;
* a **group** announcement locks the Group's AcademicTerm, Level and
  Course, then the Group, then the acting User, then -- when the actor is
  a Teacher -- their GroupTeacherAssignment, then the Announcement.

Because every scope requests its subset **in the same relative order**, a
Teacher publishing to a Group, a co-teacher withdrawing from the same
Group and an Administrator editing a Course draft that Group belongs to
cannot deadlock against each other. They also serialize against the
academic-lifecycle and Group-membership writes that already take these
same locks: a Group retarget, an archive, an Enrollment change and a
teacher-assignment change all pass through this same sequence, so an
announcement can never be published against a Group that is being
archived in the very same instant.

**Why the GroupTeacherAssignment lock is opt-in.** An Administrator has
no assignment to lock, and locking a row that does not exist is not a
no-op -- it is a ``SELECT ... FOR UPDATE`` that returns nothing and would
tempt a caller into reading ``None`` as "not assigned" for an actor for
whom assignment is not the rule. The Administrator paths therefore do not
request it and do not consult it; their authorization is the role, proved
against the locked acting User row.

**Nothing here authorizes anything.** Every value may come back ``None``
-- a vanished Group, a suspended actor, a removed assignment, an
announcement somebody else withdrew a moment ago -- and the caller must
treat each of those as a rejection, roll back, and 404 or redirect. It
must never "keep going past" one.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
REPEATABLE READ snapshot isolation, so this code runs correctly in tests
without actually locking anything. Tests can assert the *requested* lock
set and order (structural); they prove nothing about real InnoDB
blocking.
"""

from app.models import (
    AcademicStatus,
    Announcement,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    User,
    UserRole,
    UserStatus,
)
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.group_transactions import lock_group_in_open_transaction

_ACTIVE = AcademicStatus.ACTIVE.value
_ASSIGNMENT_ACTIVE = GroupTeacherAssignmentStatus.ACTIVE.value
_USER_ACTIVE = UserStatus.ACTIVE.value
_TEACHER = UserRole.TEACHER.value
_ADMINISTRATOR = UserRole.ADMINISTRATOR.value


class AnnouncementLocks:
    """The rows one M09 lock chain returned.

    Any attribute may be ``None``; see the module docstring. Deliberately
    ``__slots__``-ed, so a typo in a caller raises instead of silently
    reading ``None``.
    """

    __slots__ = ("hierarchy", "group", "actor", "teacher_assignment", "announcement")

    def __init__(
        self, hierarchy, group=None, actor=None, teacher_assignment=None,
        announcement=None,
    ):
        self.hierarchy = hierarchy
        self.group = group
        self.actor = actor
        self.teacher_assignment = teacher_assignment
        self.announcement = announcement


def lock_announcement_chain(
    actor_id,
    term_id=None,
    level_id=None,
    course_id=None,
    group_public_id=None,
    lock_assignment=False,
    announcement_id=None,
):
    """Take the M09 lock order in one open transaction, stopping wherever
    the caller's arguments stop. Returns an :class:`AnnouncementLocks`.

    `announcement_id` is the **internal** id, discovered by a non-locking
    read of the public id before this call. That pre-lock read only
    decides *which* row to lock: every condition -- the scope, the target,
    the lifecycle, the version -- is re-proved against the locked row
    afterwards, and a row that vanished in between locks to ``None``.
    """
    hierarchy = lock_academic_hierarchy(
        term_ids=[term_id], level_ids=[level_id], course_ids=[course_id]
    )
    group = None
    if group_public_id is not None:
        group = lock_group_in_open_transaction(group_public_id)
    actor = None
    if actor_id is not None:
        actor = User.query.filter_by(id=actor_id).with_for_update().first()
    teacher_assignment = None
    if lock_assignment and group is not None and actor_id is not None:
        teacher_assignment = (
            GroupTeacherAssignment.query.filter_by(
                group_id=group.id, teacher_id=actor_id
            )
            .with_for_update()
            .first()
        )
    announcement = None
    if announcement_id is not None:
        announcement = (
            Announcement.query.filter_by(id=announcement_id).with_for_update().first()
        )
    return AnnouncementLocks(
        hierarchy,
        group=group,
        actor=actor,
        teacher_assignment=teacher_assignment,
        announcement=announcement,
    )


def teacher_authz_broken(locks):
    """``True`` when the **locked** rows no longer authorize a Teacher
    announcement write -- the caller rolls back and 404s, with no
    disclosure of which link failed.

    Four current facts, every one of them re-read under the lock: the
    Group still exists, the acting account still exists with the
    ``teacher`` role and an ``active`` status, an assignment row still
    links the two, and that assignment is still ``active``.
    """
    if locks.group is None or locks.actor is None or locks.teacher_assignment is None:
        return True
    if locks.actor.role != _TEACHER or locks.actor.status != _USER_ACTIVE:
        return True
    return locks.teacher_assignment.status != _ASSIGNMENT_ACTIVE


def administrator_authz_broken(locks):
    """``True`` when the **locked** acting account is no longer an active
    Administrator.

    ``roles_required`` ran once, before the view; a request that has
    waited behind somebody else's lock needs the answer as it is *now*.
    There is deliberately no assignment to check: an Administrator manages
    all three scopes by role, and role is exactly what this re-reads.
    """
    if locks.actor is None:
        return True
    return locks.actor.role != _ADMINISTRATOR or locks.actor.status != _USER_ACTIVE


def locked_group_matches(locks, group_id, term_id, level_id, course_id):
    """``True`` when the locked Group is still the same Group, still
    hanging off the same academic chain the request read before the lock.

    A Group that was retargeted to another Course or AcademicTerm between
    the form and the write is **not** the Group this request validated
    against, and proceeding would attach an announcement to a place the
    author never chose. The caller turns a ``False`` here into the
    ordinary "this group changed while you were working" rejection.
    """
    group = locks.group
    course = locks.hierarchy.course(course_id)
    if group is None or course is None:
        return False
    return (
        group.id == group_id
        and group.course_id == course.id
        and group.academic_term_id == term_id
        and course.level_id == level_id
    )


def archived_locked_labels(locks, term_id=None, level_id=None, course_id=None,
                           include_group=False):
    """Which links of the **locked** chain are missing or archived, in
    outside-in order.

    Returns ``[]`` when every requested link exists and is ``active``,
    which is the only state in which a write may proceed. A missing row
    counts as archived rather than being silently skipped: "the course
    vanished" and "the course is archived" are both reasons not to post.
    """
    labels = []
    for label, row, requested in (
        ("academic term", locks.hierarchy.term(term_id), term_id is not None),
        ("level", locks.hierarchy.level(level_id), level_id is not None),
        ("course", locks.hierarchy.course(course_id), course_id is not None),
        ("group", locks.group, include_group),
    ):
        if not requested:
            continue
        if row is None or row.status != _ACTIVE:
            labels.append(label)
    return labels
