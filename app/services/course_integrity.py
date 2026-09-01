"""Query-only helper answering whether any Group currently references a
Course.

Deliberately independent of Flask: no `request`, `flash`, `redirect`, or
template rendering here, and no route decorators -- a plain function over
the ORM so `app/blueprints/admin/courses.py` (the Course level-change
guard, its only current caller) can import it without pulling in
route-level concerns. Mirrors `app/services/group_memberships.py`'s own
reasoning for staying Flask-independent.
"""

from app.extensions import db
from app.models import Group


def course_has_group_reference(course_id):
    """True if any Group row currently has this course_id -- regardless
    of that Group's status (active or archived), and regardless of
    whether it has any Enrollment/GroupTeacherAssignment history at all.

    This is the approved Course-level identity-freeze rule (Part 7B1
    Policy A, documented in docs/DECISIONS.md, "Course-level identity
    integrity (Phase 3, Part 7B1)"): unlike Group's own
    academic_term_id/course_id freeze, which waits for real history on
    that one Group -- Enrollment or teacher-assignment
    (`app.services.group_memberships.group_has_membership_history`), a
    Schedule row (M08, `app.services.schedule_queries.group_has_schedule_history`),
    or a Unit row (M10, `app.services.unit_queries.group_has_unit_history`),
    all combined in `app.blueprints.admin.groups._group_identity_frozen` -- a
    Course's level_id freezes on the mere *existence* of any current
    Group reference. A single Course can back many Groups at once, so
    moving its level would silently reinterpret every one of them
    simultaneously, not just one Group's own data -- and the correction
    path (archive this Course, create a new one under the correct Level)
    is cheap for what is, in practice, almost always a still-empty,
    freshly-misplaced Group. Submitting a Course's own current level_id
    back is unaffected by this check (see `_course_level_change_error` in
    the Course Blueprint, which only calls this when the level is
    actually changing).

    Deliberately answers "current references" only, not a historical
    "ever referenced" question -- there is no audit trail for that. If
    every Group that used to reference this Course has since been
    individually retargeted away (each only ever allowed while that
    Group itself had no Enrollment, teacher-assignment, Schedule, or Unit
    history -- the combined `_group_identity_frozen` test), nothing in
    the system's data derives this Course's level any more, and the
    Course may move again.

    A genuine SQL `EXISTS` query -- `SELECT EXISTS (SELECT ... FROM
    groups WHERE ...)` -- never a Python loop over loaded Group rows,
    never a `COUNT`, and never a `Group` instance materialized, since
    only existence matters here.
    """
    exists_clause = db.session.query(Group.id).filter(Group.course_id == course_id).exists()
    return bool(db.session.query(exists_clause).scalar())
