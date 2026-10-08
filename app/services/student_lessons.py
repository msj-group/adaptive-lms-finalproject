"""Scoped, read-only query layer for the Student learning outline and
Lesson detail (M11).

Flask-independent: plain functions over the ORM, no ``request`` /
``flash`` / route decorators, mirroring ``app/services/dashboard_queries.py``.
**Every function here is read-only** -- no locks, no writes.

Design rules (Part M11, Student section):

- ALL authorization is expressed in the SQL ``WHERE`` clause, keyed off
  the caller-supplied ``student_id`` -- never "load an object broadly and
  authorize it afterwards";
- Student visibility requires an **active** ``Enrollment`` for that exact
  Group, an active AcademicTerm / Level / Course / Group, an active Unit,
  and -- for a Lesson -- ``status == published``;
- templates receive plain view dicts, never ORM rows, so a template can
  never trigger a lazy load or an ORM-driven authorization check;
- the outline issues a bounded number of queries (no per-Unit or
  per-Lesson query) -- one for Units, one batched query for their
  published Lessons.

Phase 4 / M13 folds the Student's own lesson progress into the two
queries that already exist rather than adding one: the outline's batched
Lesson query and the Lesson detail query each outer-join the Student's
``LessonProgress`` row for that exact Group, and the Lesson detail query
also proves the acting account is an active Student.
"""

from collections import namedtuple

from sqlalchemy import and_
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    Lesson,
    LessonProgress,
    LessonStatus,
    Level,
    Material,
    MaterialKind,
    Unit,
    UploadedFile,
    User,
    UserRole,
    UserStatus,
)
from app.services.lesson_progress_queries import progress_join, progress_ref
from app.services.material_queries import student_visible_materials

_ACTIVE = AcademicStatus.ACTIVE.value
_ENROLLMENT_ACTIVE = EnrollmentStatus.ACTIVE.value
_PUBLISHED = LessonStatus.PUBLISHED.value
_FILE = MaterialKind.FILE.value
_STUDENT = UserRole.STUDENT.value
_USER_ACTIVE = UserStatus.ACTIVE.value

#: The Lesson page: ``view`` is the template's plain display dict, and
#: ``progress`` the Student's
#: :class:`~app.services.lesson_progress_queries.LessonProgressRef`, whose
#: internal ids are for the write paths only and never reach a template.
StudentLessonPage = namedtuple("StudentLessonPage", "view progress")


def student_outline_group(student_id, group_public_id):
    """The Group a Student may open as a learning outline, or ``None``.

    Requires -- all in one SQL query -- an active ``Enrollment`` for
    exactly this Group owned by ``student_id`` and an active
    AcademicTerm / Level / Course / Group. ``group.course.level`` and
    ``group.academic_term`` are eager-loaded for the page header.
    """
    return (
        db.session.query(Group)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .join(Enrollment, Enrollment.group_id == Group.id)
        .options(
            joinedload(Group.course).joinedload(Course.level),
            joinedload(Group.academic_term),
        )
        .filter(
            Group.public_id == group_public_id,
            Group.status == _ACTIVE,
            Course.status == _ACTIVE,
            Level.status == _ACTIVE,
            AcademicTerm.status == _ACTIVE,
            Enrollment.student_id == student_id,
            Enrollment.status == _ENROLLMENT_ACTIVE,
        )
        .first()
    )


def outline_units(group_id, student_id):
    """The learning outline body for a Group already authorized for
    `student_id`: every ACTIVE Unit in Unit display order, each carrying
    only its PUBLISHED Lessons in Lesson display order, as plain view dicts.

    One query for the Units, one batched query for all their published
    Lessons -- never one query per Unit. An active Unit with no published
    Lesson yields an empty ``lessons`` list (the template renders an
    honest empty state). Each Lesson's ``is_completed`` comes from the
    Student's own progress row for **this** Group, outer-joined into the
    same batched query (Phase 4 / M13).
    """
    units = (
        Unit.query.filter_by(group_id=group_id, status=_ACTIVE)
        .order_by(Unit.display_order, Unit.id)
        .all()
    )
    if not units:
        return []
    unit_ids = [u.id for u in units]
    lessons = (
        db.session.query(
            Lesson.unit_id, Lesson.title, Lesson.public_id, LessonProgress.completed_at
        )
        .select_from(Lesson)
        .outerjoin(
            LessonProgress,
            and_(
                LessonProgress.lesson_id == Lesson.id,
                LessonProgress.group_id == group_id,
                LessonProgress.student_id == student_id,
                LessonProgress.enrollment_id == db.session.query(Enrollment.id).filter(
                    Enrollment.student_id == student_id, Enrollment.group_id == group_id,
                    Enrollment.status == _ENROLLMENT_ACTIVE).scalar_subquery(),
            ),
        )
        .filter(Lesson.unit_id.in_(unit_ids), Lesson.status == _PUBLISHED)
        .order_by(Lesson.display_order, Lesson.id)
        .all()
    )
    lessons_by_unit = {}
    for unit_id, title, public_id, completed_at in lessons:
        lessons_by_unit.setdefault(unit_id, []).append(
            {"title": title, "public_id": public_id, "is_completed": completed_at is not None}
        )
    return [
        {
            "title": unit.title,
            "public_id": unit.public_id,
            "lessons": lessons_by_unit.get(unit.id, []),
        }
        for unit in units
    ]


def student_lesson_detail(student_id, group_public_id, unit_public_id, lesson_public_id):
    """One SQL query: a PUBLISHED Lesson plus its authorized hierarchy
    context and the Student's own progress on it, as a
    :class:`StudentLessonPage`, or ``None``.

    Every scoping predicate -- own active Enrollment, an active Student
    account, active AcademicTerm / Level / Course / Group, active Unit
    belonging to that Group, published Lesson belonging to that Unit, and
    the nested public ids matching -- lives in the ``WHERE`` clause. A draft
    Lesson, an inactive Unit or ancestor, a withdrawn / missing Enrollment,
    a different Group, or a mismatched nested id all produce no row (the
    route then returns a non-disclosing 404). The progress row is
    outer-joined on this Student, this Group and this Lesson.

    ``view`` is a plain dict of display strings and public ids only -- no
    ORM row, no Teacher identity, no internal id.
    """
    row = (
        db.session.query(
            Lesson,
            Unit,
            Group,
            Course,
            Level,
            AcademicTerm,
            LessonProgress.id,
            LessonProgress.version,
            LessonProgress.completed_at,
            LessonProgress.last_opened_at,
        )
        .select_from(Lesson)
        .join(Unit, Lesson.unit_id == Unit.id)
        .join(Group, Unit.group_id == Group.id)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .join(Enrollment, Enrollment.group_id == Group.id)
        .join(User, User.id == Enrollment.student_id)
        .outerjoin(LessonProgress, progress_join(student_id))
        .filter(
            User.role == _STUDENT,
            User.status == _USER_ACTIVE,
            Lesson.public_id == lesson_public_id,
            Lesson.status == _PUBLISHED,
            Unit.public_id == unit_public_id,
            Unit.status == _ACTIVE,
            Group.public_id == group_public_id,
            Group.status == _ACTIVE,
            Course.status == _ACTIVE,
            Level.status == _ACTIVE,
            AcademicTerm.status == _ACTIVE,
            Enrollment.student_id == student_id,
            Enrollment.status == _ENROLLMENT_ACTIVE,
        )
        .first()
    )
    if row is None:
        return None
    lesson, unit, group, course, level, term = row[:6]
    progress = progress_ref(
        group.id, group.public_id, unit.public_id, lesson.id, lesson.public_id, *row[6:]
    )
    view = {
        "group_name": group.name,
        "group_public_id": group.public_id,
        "unit_public_id": unit.public_id,
        "lesson_public_id": lesson.public_id,
        "course_title": course.title,
        "level_name": level.name,
        "term_name": term.name,
        "unit_title": unit.title,
        "lesson_title": lesson.title,
        "lesson_description": lesson.description,
        "materials": student_visible_materials(lesson.id),
    }
    return StudentLessonPage(view, progress)


def student_file_material(student_id, group_public_id, unit_public_id, lesson_public_id, material_public_id):
    """One SQL query: the ``(Material, UploadedFile)`` pair for a `file`
    Material a Student may open/download right now, or ``None``.

    Mirrors :func:`student_lesson_detail`'s full authorization chain
    (own active Enrollment; active AcademicTerm / Level / Course / Group
    / Unit; published Lesson) plus two more conditions specific to file
    serving: ``Material.status == active`` and ``Material.kind ==
    'file'`` -- an archived Material or a non-file Material's public_id
    (rich_text / external_link have no bytes to serve) both yield no row.
    """
    return (
        db.session.query(Material, UploadedFile)
        .join(UploadedFile, Material.uploaded_file_id == UploadedFile.id)
        .join(Lesson, Material.lesson_id == Lesson.id)
        .join(Unit, Lesson.unit_id == Unit.id)
        .join(Group, Unit.group_id == Group.id)
        .join(Course, Group.course_id == Course.id)
        .join(Level, Course.level_id == Level.id)
        .join(AcademicTerm, Group.academic_term_id == AcademicTerm.id)
        .join(Enrollment, Enrollment.group_id == Group.id)
        .filter(
            Material.public_id == material_public_id,
            Material.kind == _FILE,
            Material.status == _ACTIVE,
            Lesson.public_id == lesson_public_id,
            Lesson.status == _PUBLISHED,
            Unit.public_id == unit_public_id,
            Unit.status == _ACTIVE,
            Group.public_id == group_public_id,
            Group.status == _ACTIVE,
            Course.status == _ACTIVE,
            Level.status == _ACTIVE,
            AcademicTerm.status == _ACTIVE,
            Enrollment.student_id == student_id,
            Enrollment.status == _ENROLLMENT_ACTIVE,
        )
        .first()
    )
