"""Use the same group release predicates for outlines, files and progress."""
from datetime import datetime, timezone

from sqlalchemy import and_, exists, func, or_, select

from app.models import Group, Lesson, Material, Unit
from app.models.curriculum_release import LessonRelease, MaterialRelease, WorkbookLessonRelease


def curriculum_now():
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _release_exists(model, item_column, item_id, group_id, *, open_only=False):
    criteria = [model.group_id == group_id, item_column == item_id]
    if open_only:
        criteria.append(model.opens_at <= func.utc_timestamp())
    return exists(select(1).where(*criteria).correlate(Group, Lesson, Material))


def lesson_release_model(book):
    if book == "main":
        return LessonRelease
    if book == "workbook":
        return WorkbookLessonRelease
    raise ValueError("Unknown curriculum book")


def lesson_is_released(group_id=Group.id, *, book="main"):
    model = lesson_release_model(book)
    released = _release_exists(model, model.lesson_id, Lesson.id, group_id, open_only=True)
    if book == "workbook":
        return and_(Lesson.status == "published", released)
    explicit = _release_exists(model, model.lesson_id, Lesson.id, group_id)
    return and_(Lesson.status == "published",
                or_(released, and_(Unit.group_id.is_not(None), ~explicit)))


def material_is_released(group_id=Group.id):
    explicit = _release_exists(MaterialRelease, MaterialRelease.material_id, Material.id, group_id)
    released = _release_exists(MaterialRelease, MaterialRelease.material_id, Material.id, group_id, open_only=True)
    return or_(~explicit, released)


def lesson_is_released_locked(group, unit, lesson, *, book="main"):
    if lesson.status != "published":
        return False
    model = lesson_release_model(book)
    release = model.query.filter_by(group_id=group.id, lesson_id=lesson.id).with_for_update().first()
    if release is None:
        return book == "main" and unit.group_id is not None
    return release.opens_at is not None and release.opens_at <= curriculum_now()
