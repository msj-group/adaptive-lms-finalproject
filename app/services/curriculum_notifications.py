"""Create in-app notices for curriculum edits and due group releases."""
from uuid import uuid4

from flask import current_app, g, url_for
from sqlalchemy import exists, func, literal, select, union_all
from sqlalchemy.dialects.mysql import insert

from app.extensions import db
from app.models import (
    AcademicTerm, Course, Enrollment, Group, GroupTeacherAssignment, Lesson, Level,
    Material, Notification, Unit, User,
)
from app.models.curriculum_release import LessonRelease, MaterialRelease
from app.models.group_curriculum import GroupCurriculum
from app.services.curriculum_visibility import lesson_is_released, material_is_released
from app.services.notification_delivery import _deliver, _entry
from app.services.notification_targets import _build_path, student_lesson_target, student_material_target


def _student_groups(query):
    return (
        query.join(Course, Course.id == Group.course_id)
        .join(Level, Level.id == Course.level_id)
        .join(AcademicTerm, AcademicTerm.id == Group.academic_term_id)
        .join(Enrollment, Enrollment.group_id == Group.id)
        .join(User, User.id == Enrollment.student_id)
        .where(Group.status == "active", Course.status == "active", Level.status == "active",
               AcademicTerm.status == "active", Enrollment.status == "active",
               User.role == "student", User.status == "active")
    )


def _due_releases(recipient_id, model):
    material = Material.public_id if model is MaterialRelease else literal(None)
    query = select(model.notice_key, Group.public_id.label("group_public_id"),
                   Group.name.label("group_name"), Unit.public_id.label("unit_public_id"),
                   Lesson.public_id.label("lesson_public_id"),
                   material.label("material_public_id")).select_from(model)
    if model is MaterialRelease:
        query = (query.join(Material, Material.id == model.material_id)
                 .join(Lesson, Lesson.id == Material.lesson_id).where(Material.status == "active"))
    else:
        query = query.join(Lesson, Lesson.id == model.lesson_id)
    return _student_groups(
        query.join(Unit, Unit.id == Lesson.unit_id)
        .join(Group, Group.id == model.group_id)
        .where(Unit.in_group(Group.id, Group.course_id), Unit.status == "active",
               lesson_is_released(), model.notice_key.is_not(None),
               model.opens_at <= func.utc_timestamp(), User.id == recipient_id)
    )


def sync_curriculum_notices(user, *, background=False):
    """Deliver due releases once, from a visit or the opted-in device worker."""
    if not user.is_authenticated or user.role != "student" or (not background and g.get("curriculum_notices_synced")):
        return
    if not background:
        g.curriculum_notices_synced = True
    try:
        recipient_id = user.id
        due = union_all(_due_releases(recipient_id, LessonRelease),
                        _due_releases(recipient_id, MaterialRelease)).subquery()
        unseen = select(
            due.c.notice_key, due.c.group_public_id, due.c.group_name, func.count().label("item_count"),
            func.min(due.c.unit_public_id).label("unit_public_id"),
            func.min(due.c.lesson_public_id).label("lesson_public_id"),
            func.min(due.c.material_public_id).label("material_public_id"),
        ).where(~exists(
            select(1).where(Notification.recipient_id == recipient_id,
                            Notification.curriculum_notice_key == due.c.notice_key)
        )).group_by(due.c.notice_key, due.c.group_public_id, due.c.group_name).limit(50)
        # The page may have its own read transaction or pending form work.
        # Delivery owns a separate transaction and a unique recipient/key.
        with db.engine.begin() as connection:
            rows = connection.execute(unseen).all()
            if rows:
                entries = []
                for row in rows:
                    kind = "lesson_published"
                    if row.item_count > 1:
                        path = _build_path("workspace.student_course", group_public_id=row.group_public_id)
                    elif row.material_public_id:
                        kind = "material_available"
                        path = student_material_target(row.group_public_id, row.unit_public_id,
                                                       row.lesson_public_id, row.material_public_id)
                    else:
                        path = student_lesson_target(row.group_public_id, row.unit_public_id, row.lesson_public_id)
                    entry = _entry(recipient_id, kind, "Course content available",
                                   f"New course content is available for {row.group_name}.", path)
                    entries.append(dict(entry, curriculum_notice_key=row.notice_key,
                                        public_id=str(uuid4()), created_at=func.utc_timestamp()))
                write = insert(Notification.__table__).values(entries)
                connection.execute(write.on_duplicate_key_update(id=Notification.id))
    except Exception:
        current_app.logger.exception("Could not deliver scheduled curriculum notices")


def notify_curriculum_updated(course_ids, *, kind=None, item_id=None, prior_student_groups=(), group_id=None):
    """Notify assigned teachers and students whose accessible content changed."""
    def entries():
        teacher_scope = (Group.id == group_id if group_id is not None else
                         ~exists(select(1).where(GroupCurriculum.group_id == Group.id)))
        teachers = db.session.execute(
            select(User.id, Group.public_id, Group.name).select_from(Group)
            .join(GroupTeacherAssignment, GroupTeacherAssignment.group_id == Group.id)
            .join(User, User.id == GroupTeacherAssignment.teacher_id)
            .where(teacher_scope, Group.course_id.in_(course_ids), Group.status == "active",
                   GroupTeacherAssignment.status == "active", User.status == "active",
                   User.role == "teacher").distinct()
        )
        messages = [
            _entry(user_id, "lesson_published", "Curriculum updated",
                   f"The {'teacher' if group_id is not None else 'administrator'} updated the curriculum for {name}.",
                   url_for("teacher.curriculum_publication", group_public_id=public_id))
            for user_id, public_id, name in teachers
        ]
        if item_id is not None or group_id is not None:
            groups = (visible_curriculum_groups(course_ids, kind, item_id) if item_id is not None
                      else {group_id})
            groups.update(prior_student_groups)
            if group_id is not None:
                groups.intersection_update({group_id})
            if groups:
                students = db.session.execute(_student_groups(
                    select(User.id, Group.public_id, Group.name).select_from(Group)
                    .where(Group.id.in_(groups))
                ).distinct())
                messages.extend(
                    _entry(user_id, "material_available", "Course content updated",
                           f"Course content for {name} has changed.",
                           url_for("workspace.student_course", group_public_id=public_id))
                    for user_id, public_id, name in students
                )
        return messages

    return _deliver("curriculum update", entries)


def visible_curriculum_groups(course_ids, kind, item_id):
    query = (select(Group.id).select_from(Group)
             .join(Unit, Unit.in_group(Group.id, Group.course_id))
             .join(Lesson, Lesson.unit_id == Unit.id)
             .where(Group.course_id.in_(course_ids), Unit.status == "active", lesson_is_released()))
    if kind == "unit":
        query = query.where(Unit.id == item_id)
    elif kind == "lesson":
        query = query.where(Lesson.id == item_id)
    else:
        query = query.join(Material, Material.lesson_id == Lesson.id).where(
            Material.id == item_id, Material.status == "active", material_is_released())
    return set(db.session.scalars(query.distinct()))


def notify_curriculum_hidden(group_id):
    def entries():
        students = db.session.execute(_student_groups(
            select(User.id, Group.public_id, Group.name).select_from(Group).where(Group.id == group_id)
        ).distinct())
        return [
            _entry(user_id, "material_available", "Course visibility updated",
                   f"The teacher changed which course content is visible in {name}.",
                   url_for("workspace.student_course", group_public_id=public_id))
            for user_id, public_id, name in students
        ]

    return _deliver("curriculum visibility update", entries)
