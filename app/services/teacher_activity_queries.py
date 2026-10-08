"""Read-only activity library, scoped to the Teacher's active group assignments."""
from sqlalchemy import and_, exists, literal, select, union_all

from app.extensions import db
from app.models import (AcademicTerm, Assignment, Course, Group, GroupTeacherAssignment,
                        Level, ListeningActivity, Quiz, SpeakingActivity, User)
from app.services.activity_queries import TYPES
from app.services.assignment_queries import has_speaking_extension, normalize_page
from app.services.quiz_queries import has_listening_extension
from app.services.schedule_occurrences import to_app_local

PUBLICATIONS = {"draft": "Draft", "published": "Published"}
PAGE_SIZE = 20


def _assigned(teacher_id):
    return and_(exists(select(GroupTeacherAssignment.id).where(
        GroupTeacherAssignment.group_id == Group.id,
        GroupTeacherAssignment.teacher_id == teacher_id,
        GroupTeacherAssignment.status == "active")).correlate(Group),
        exists(select(User.id).where(User.id == teacher_id, User.role == "teacher", User.status == "active")))


def _groups(teacher_id):
    # Historical groups remain readable, just as in the existing Teacher GETs.
    return select(Group.public_id.label("group_public_id"), Group.name.label("group_name"),
                  Course.public_id.label("course_public_id"), Course.title.label("course_title")).select_from(Group).join(
        Course, Course.id == Group.course_id).where(_assigned(teacher_id))


def activity_groups(teacher_id, course=""):
    query = _groups(teacher_id)
    if course:
        query = query.where(Course.public_id == course)
    return db.session.execute(query.order_by(Group.name, Group.public_id).limit(100)).mappings().all()


def activity_courses(teacher_id):
    owned = _groups(teacher_id).subquery("assigned_activity_groups")
    return db.session.execute(select(owned.c.course_public_id, owned.c.course_title).distinct().order_by(
        owned.c.course_title, owned.c.course_public_id).limit(100)).mappings().all()


def _branch(teacher_id, kind, parent, public_id, due_at):
    operational = and_(Group.status == "active", Course.status == "active",
                       Level.status == "active", AcademicTerm.status == "active")
    query = select(literal(kind).label("kind"), public_id.label("public_id"), parent.id.label("sort_id"),
        parent.title.label("title"), parent.status.label("status"), Group.public_id.label("group_public_id"),
        Group.name.label("group_name"), Course.public_id.label("course_public_id"),
        Course.title.label("course_title"), parent.opens_at.label("opens_at"), due_at.label("due_at"),
        parent.updated_at.label("updated_at"), operational.label("operational")).select_from(parent).join(
        Group, Group.id == parent.group_id).join(Course, Course.id == Group.course_id).join(
        Level, Level.id == Course.level_id).join(AcademicTerm, AcademicTerm.id == Group.academic_term_id).where(_assigned(teacher_id))
    if kind == "assignment":
        query = query.where(~has_speaking_extension())
    elif kind == "quiz":
        query = query.where(~has_listening_extension())
    elif kind == "listening":
        query = query.join(ListeningActivity, ListeningActivity.quiz_id == Quiz.id)
    else:
        query = query.join(SpeakingActivity, SpeakingActivity.assignment_id == Assignment.id)
    return query


def activity_statement(teacher_id, *, kind="", publication="", course="", group="", page=1):
    owned = union_all(
        _branch(teacher_id, "assignment", Assignment, Assignment.public_id, Assignment.due_at),
        _branch(teacher_id, "quiz", Quiz, Quiz.public_id, Quiz.closes_at),
        _branch(teacher_id, "listening", Quiz, ListeningActivity.public_id, Quiz.closes_at),
        _branch(teacher_id, "speaking", Assignment, SpeakingActivity.public_id, Assignment.due_at),
    ).subquery("assigned_activities")
    # Keep the internal row identifier exclusively in the stable SQL ordering.
    query = select(*(column for column in owned.c if column.key != "sort_id"))
    if kind in TYPES:
        query = query.where(owned.c.kind == kind)
    if publication in PUBLICATIONS:
        query = query.where(owned.c.status == publication)
    if course:
        query = query.where(owned.c.course_public_id == course)
    if group:
        query = query.where(owned.c.group_public_id == group)
    return query.order_by(owned.c.updated_at.desc(), owned.c.kind, owned.c.sort_id).offset(
        (normalize_page(page) - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1)


def activities_page(teacher_id, **filters):
    rows = db.session.execute(activity_statement(teacher_id, **filters)).mappings().all()
    return [dict(row) for row in rows[:PAGE_SIZE]], len(rows) > PAGE_SIZE


def activity_view(rows, tz_name, url_builder):
    endpoints = {"assignment": ("teacher.assignment_detail", "assignment_public_id"),
                 "quiz": ("teacher.quiz_detail", "quiz_public_id"),
                 "listening": ("teacher.listening_detail", "listening_public_id"),
                 "speaking": ("teacher.speaking_detail", "speaking_public_id")}
    items = []
    for row in rows:
        item = dict(row)
        endpoint, key = endpoints[item["kind"]]
        params = {"group_public_id": item["group_public_id"]}
        params[key] = item["public_id"]
        item.update(type_label=TYPES[item["kind"]], state_label=PUBLICATIONS[item["status"]],
            due_local=to_app_local(tz_name, item["due_at"]) if item["due_at"] else None,
            updated_local=to_app_local(tz_name, item["updated_at"]),
            course_url=url_builder("workspace.teacher_group", group_public_id=item["group_public_id"]),
            url=url_builder(endpoint, **params))
        items.append(item)
    return items
