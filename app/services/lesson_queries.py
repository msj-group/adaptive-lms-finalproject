"""Query-only helpers for Unit-owned Lessons (M11).

Flask-independent: no ``request`` / ``flash`` / route decorators -- plain
functions over the ORM, mirroring ``app/services/unit_queries.py``.
Nothing here takes a lock or writes; the Teacher write path in
``app/blueprints/teacher/lessons.py`` does the locking and re-checks the
order against the locked rows.
"""

from sqlalchemy import func

from app.extensions import db
from app.models import Lesson, LessonStatus

_PUBLISHED = LessonStatus.PUBLISHED.value


def next_lesson_display_order(unit_id):
    """The ``display_order`` a newly created Lesson should take: strictly
    after the Unit's current highest order across **all** Lessons (draft
    and published), or ``0`` for the first Lesson. Gaps are acceptable, so
    this never renumbers existing rows.
    """
    highest = (
        db.session.query(func.max(Lesson.display_order))
        .filter(Lesson.unit_id == unit_id)
        .scalar()
    )
    return 0 if highest is None else highest + 1


def lessons_ordered(unit_id):
    """Every Lesson of the Unit -- draft and published together -- in the
    single Teacher-visible teaching order (``display_order`` then ``id``
    as a stable tie-break)."""
    return (
        Lesson.query.filter_by(unit_id=unit_id)
        .order_by(Lesson.display_order, Lesson.id)
        .all()
    )


def published_lessons_ordered(unit_id):
    """Only the PUBLISHED Lessons of the Unit, in teaching order -- the
    Student-visible subset, keeping its relative position within the
    complete order."""
    return (
        Lesson.query.filter_by(unit_id=unit_id, status=_PUBLISHED)
        .order_by(Lesson.display_order, Lesson.id)
        .all()
    )
