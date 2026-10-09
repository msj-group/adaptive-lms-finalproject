"""Bounded, read-only workspace search. No answers, messages or account mapping.

Student content reuses the existing enrollment/visibility query layer. Teacher
content is SQL-scoped to active assignments. Administrator searches project
public record identifiers only. Researcher queries never join accounts and use
the same server-selected session provenance as the Sessions page.
"""
from sqlalchemy import and_, exists, func, or_, select, union_all

from app.extensions import db
from app.models import (AcademicTerm, Course, Group, GroupTeacherAssignment, Lesson,
                        Level, Material, ResearchConfiguration, ResearchSession,
                        ResearchSubject, Room, Unit, User)
from app.services.search_terms import escape_like

CAP = 5


def _match(columns, norm):
    return and_(*(or_(*(func.lower(column).like(
        "%" + escape_like(token) + "%", escape="\\") for column in columns))
        for token in norm.tokens))


def _section(label, icon, rows, build):
    return {"label": label, "icon": icon, "items": [build(row) for row in rows[:CAP]],
            "has_more": len(rows) > CAP}


def destinations(role):
    """Existing GET destinations only; no generic endpoint or caller role input."""
    common = [("Account settings", "account.settings", "user")]
    if role == "student":
        return [("Dashboard", "student.dashboard", "dashboard"),
                ("My courses", "workspace.student_courses", "book"),
                ("Activities", "student.activities", "activity"),
                ("My records", "student.records", "file"),
                ("Attendance", "student.attendance_list", "check"),
                ("Grades", "student.grades_list", "chart"),
                ("Calendar", "student.calendar", "calendar"),
                ("Announcements", "student.announcements_list", "megaphone"),
                ("Discussions", "student.discussions_overview", "users"),
                ("Messages", "messages.inbox", "message"),
                ("Notifications", "notifications.inbox", "bell")] + common
    if role == "teacher":
        return [("Dashboard", "teacher.dashboard", "dashboard"),
                ("My groups", "workspace.teacher_groups", "book"),
                ("Activities", "teacher.activities", "activity"),
                ("Review queue", "workspace.review_queue", "check"),
                ("Attendance", "teacher.attendance_overview", "check"),
                ("Gradebook", "teacher.gradebook_overview", "graduation"),
                ("Calendar", "teacher.calendar", "calendar"),
                ("Announcements", "teacher.announcements_feed", "megaphone"),
                ("Discussions", "teacher.discussions_overview", "users"),
                ("Messages", "messages.inbox", "message"),
                ("Notifications", "notifications.inbox", "bell")] + common
    if role == "administrator":
        from app.blueprints.admin.navigation import ADMIN_NAV_SECTIONS
        return [("Dashboard", "admin.dashboard", "dashboard")] + [
            (item["label"], item["endpoint"], "file") for section in ADMIN_NAV_SECTIONS
            for item in section["links"] if item.get("endpoint")] + common
    if role == "researcher":
        return [("Dashboard", "research.dashboard", "dashboard"),
                ("Configurations", "research.configurations", "settings"),
                ("Sessions", "research.sessions", "activity"),
                ("Exports", "research.exports", "file"),
                ("Exclusions", "research.exclusions", "users"),
                ("Storage and gaps", "research.storage", "layers"),
                ("Audit log", "research.activity_log", "lock")] + common
    return []


def _student(user_id, norm, url):
    from app.services.search_queries import search_learning_content
    headings = {"course": "Courses", "unit": "Units", "lesson": "Lessons",
                "material": "Materials", "announcement": "Announcements"}
    result = []
    for kind, section in search_learning_content(user_id, norm, cap=CAP).items():
        items = []
        for row in section["items"]:
            params = {key: value for key, value in row.items() if key.endswith("_public_id")}
            if kind in ("course", "unit"):
                target = url("student.group_units", group_public_id=params["group_public_id"])
                if kind == "unit":
                    target += "#unit-" + params["unit_public_id"]
            elif kind in ("lesson", "material"):
                target = url("student.lesson_detail", **{key: params[key] for key in
                    ("group_public_id", "unit_public_id", "lesson_public_id")})
                if kind == "material":
                    target += "#material-" + params["material_public_id"]
            else:
                target = url("student.announcement_detail",
                             announcement_public_id=params["announcement_public_id"])
            items.append({"title": row["title"], "context": " › ".join(row["breadcrumb"]),
                          "url": target})
        result.append({"label": headings[kind], "icon": "megaphone" if kind == "announcement"
                       else "file" if kind == "material" else "book",
                       "items": items, "has_more": section["has_more"]})
    return result


def _assigned(user_id):
    return and_(exists(select(GroupTeacherAssignment.id).where(
        GroupTeacherAssignment.group_id == Group.id,
        GroupTeacherAssignment.teacher_id == user_id,
        GroupTeacherAssignment.status == "active")).correlate(Group),
        exists(select(User.id).where(User.id == user_id, User.role == "teacher",
                                     User.status == "active")))


def _teacher(user_id, norm, url):
    # Historical content remains readable under a current assignment, exactly
    # as in the Group/Unit/Lesson GETs. No broad load followed by Python filtering.
    result = []
    rows = db.session.query(Group.public_id, Group.name, Course.title).select_from(Group).join(
        Course, Course.id == Group.course_id).filter(_assigned(user_id),
        _match((Group.name, Group.code, Course.title, Course.code), norm)).order_by(
        Group.name, Group.public_id).limit(CAP + 1).all()
    result.append(_section("Groups", "users", rows, lambda row: {
        "title": row.name, "context": row.title,
        "url": url("workspace.teacher_group", group_public_id=row.public_id)}))
    for model, label in ((Unit, "Units"), (Lesson, "Lessons"), (Material, "Materials")):
        query = db.session.query(model.title.label("title"), Group.name.label("group_name"),
            Group.public_id.label("group_public_id"), Unit.public_id.label("unit_public_id"))
        if model is Unit:
            query = query.select_from(Unit).join(Group, Group.id == Unit.group_id)
        else:
            query = query.add_columns(Lesson.public_id.label("lesson_public_id"))
            if model is Material:
                query = query.select_from(Material).join(Lesson, Lesson.id == Material.lesson_id)
            else:
                query = query.select_from(Lesson)
            query = query.join(Unit, Unit.id == Lesson.unit_id).join(Group, Group.id == Unit.group_id)
        rows = query.filter(_assigned(user_id), _match((model.title, model.search_keywords), norm)).order_by(
            model.title, model.public_id).limit(CAP + 1).all()
        def build(row, current=model):
            params = {"group_public_id": row.group_public_id, "unit_public_id": row.unit_public_id}
            target = "teacher.group_lessons"
            if current is not Unit:
                params["lesson_public_id"] = row.lesson_public_id
                target = "teacher.lesson_materials" if current is Material else "workspace.lesson_preview"
            return {"title": row.title, "context": row.group_name, "url": url(target, **params)}
        result.append(_section(label, "file" if model is Material else "book", rows, build))
    # Reuse the existing activity library branches, including the Listening /
    # Speaking extension exclusions and current active-assignment boundaries.
    from app.models import Assignment, ListeningActivity, Quiz, SpeakingActivity
    from app.services.teacher_activity_queries import _branch
    library = union_all(
        _branch(user_id, "assignment", Assignment, Assignment.public_id, Assignment.due_at),
        _branch(user_id, "quiz", Quiz, Quiz.public_id, Quiz.closes_at),
        _branch(user_id, "listening", Quiz, ListeningActivity.public_id, Quiz.closes_at),
        _branch(user_id, "speaking", Assignment, SpeakingActivity.public_id, Assignment.due_at),
    ).subquery("workspace_search_activities")
    rows = db.session.execute(select(library.c.title, library.c.kind, library.c.public_id,
        library.c.group_public_id, library.c.group_name).where(_match((library.c.title,), norm)).order_by(
        library.c.title, library.c.kind, library.c.public_id).limit(CAP + 1)).all()
    def activity(row):
        key = row.kind + "_public_id"
        return {"title": row.title, "context": row.group_name,
                "url": url("teacher." + row.kind + "_detail",
                           group_public_id=row.group_public_id, **{key: row.public_id})}
    result.append(_section("Activities", "activity", rows, activity))
    return result


def _admin(norm, url):
    result = []
    for model, title, label, endpoint, icon in (
        (AcademicTerm, AcademicTerm.name, "Academic Terms", "admin.academic_term_edit", "calendar"),
        (Level, Level.name, "Levels", "admin.level_edit", "layers"),
        (Course, Course.title, "Courses", "admin.course_edit", "book"),
        (Group, Group.name, "Groups", "admin.group_detail", "users"),
        (Room, Room.name, "Rooms", "admin.room_edit", "room"),
    ):
        columns = [title]
        if hasattr(model, "code"):
            columns.append(model.code)
        rows = db.session.query(model.public_id, title.label("title"), model.status).filter(
            _match(columns, norm)).order_by(title, model.public_id).limit(CAP + 1).all()
        result.append(_section(label, icon, rows, lambda row, target=endpoint: {
            "title": row.title, "context": row.status, "context_is_label": True,
            "url": url(target, public_id=row.public_id)}))
    for role, label, endpoint in (("student", "Students", "admin.student_detail"),
                                  ("teacher", "Teachers", "admin.teacher_detail")):
        rows = db.session.query(User.public_id, User.full_name, User.status).filter(
            User.role == role, _match((User.full_name, User.email), norm)).order_by(
            User.full_name, User.public_id).limit(CAP + 1).all()
        result.append(_section(label, "users", rows, lambda row, target=endpoint: {
            "title": row.full_name, "context": row.status, "context_is_label": True,
            "url": url(target, public_id=row.public_id)}))
    return result


def _research(norm, provenance, url):
    from app.services.research_scope import export_session_provenances
    rows = db.session.query(ResearchConfiguration.public_id, ResearchConfiguration.label,
        ResearchConfiguration.status).filter(_match((ResearchConfiguration.label,), norm)).order_by(
        ResearchConfiguration.label, ResearchConfiguration.public_id).limit(CAP + 1).all()
    result = [_section("Configurations", "settings", rows, lambda row: {
        "title": row.label, "context": row.status, "context_is_label": True,
        "url": url("research.configuration_detail", configuration_public_id=row.public_id)})]
    rows = db.session.query(ResearchSession.public_id, ResearchSubject.subject_code).select_from(
        ResearchSession).join(ResearchSubject, ResearchSubject.id == ResearchSession.subject_id).filter(
        ResearchSession.provenance.in_(export_session_provenances(provenance)),
        _match((ResearchSession.public_id, ResearchSubject.subject_code), norm)).order_by(
        ResearchSession.started_at_ms.desc(), ResearchSession.public_id).limit(CAP + 1).all()
    result.append(_section("Sessions", "activity", rows, lambda row: {
        "title": row.public_id, "context": row.subject_code,
        "url": url("research.session_detail", session_public_id=row.public_id)}))
    return result


def search_records(role, user_id, norm, provenance, url):
    if not norm.is_searchable:
        return []
    if role == "student":
        return _student(user_id, norm, url)
    if role == "teacher":
        return _teacher(user_id, norm, url)
    if role == "administrator":
        return _admin(norm, url)
    if role == "researcher":
        return _research(norm, provenance, url)
    return []
