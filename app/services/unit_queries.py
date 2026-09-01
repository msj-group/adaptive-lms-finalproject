"""Query-only helpers for group-owned Units (M10).

Flask-independent: no ``request`` / ``flash`` / route decorators -- plain
functions over the ORM, mirroring ``app/services/schedule_queries.py``.
Nothing here takes a lock or writes; the write path in
``app/blueprints/teacher/units.py`` does the locking and re-checks these
against the locked rows.
"""

from sqlalchemy import func

from app.extensions import db
from app.models import AcademicStatus, Unit

_ACTIVE = AcademicStatus.ACTIVE.value


def group_has_unit_history(group_id):
    """True if **any** Unit row -- active or archived -- exists for this
    Group.

    A Unit row freezes the Group's academic identity
    (``academic_term_id`` / ``course_id``) exactly like
    Enrollment / GroupTeacherAssignment / Schedule history does -- see
    ``app.blueprints.admin.groups._group_identity_frozen``. Every Unit was
    authored against this Group's Term/Course at the time.
    """
    return db.session.query(Unit.id).filter_by(group_id=group_id).first() is not None


def next_unit_display_order(group_id):
    """The ``display_order`` a newly created or reactivated Unit should
    take: strictly after the Group's current highest order across **all**
    Units (active and archived), or ``0`` for the first Unit. Gaps are
    acceptable, so this never renumbers existing rows.
    """
    highest = (
        db.session.query(func.max(Unit.display_order)).filter(Unit.group_id == group_id).scalar()
    )
    return 0 if highest is None else highest + 1


def active_units_ordered(group_id):
    """Every ACTIVE Unit of the Group in teaching order
    (``display_order`` then ``id`` as a stable tie-break). Archived Units
    are excluded -- they do not participate in active reordering."""
    return (
        Unit.query.filter_by(group_id=group_id, status=_ACTIVE)
        .order_by(Unit.display_order, Unit.id)
        .all()
    )


def archived_units_ordered(group_id):
    """Every ARCHIVED Unit of the Group, in their stored order."""
    return (
        Unit.query.filter(Unit.group_id == group_id, Unit.status != _ACTIVE)
        .order_by(Unit.display_order, Unit.id)
        .all()
    )
