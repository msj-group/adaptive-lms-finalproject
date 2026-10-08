"""One bounded SQL union for the four existing Student activity types."""
from sqlalchemy import case, exists, literal, select, union_all
from app.extensions import db
from app.models import Assignment, Course, Enrollment, Group, Level, ListeningActivity, Quiz, QuizAttempt, SpeakingActivity, SpeakingSubmission, Submission
from app.services.assignment_queries import _visible_assignment_query, normalize_page
from app.services.quiz_queries import student_visible_quiz_query
from app.services.listening_queries import student_listening_query
from app.services.speaking_queries import student_speaking_query
from app.services.schedule_occurrences import to_app_local

TYPES = {"assignment": "Assignment", "quiz": "Quiz", "listening": "Listening", "speaking": "Speaking"}
STATES = {"open": "Open", "scheduled": "Upcoming", "submitted": "Submitted", "past_due": "Closed", "in_progress": "In progress"}
PAGE_SIZE = 20


def _submission_exists(model, activity_column, activity_id, student_id):
    return exists(select(model.id).where(activity_column == activity_id, model.student_id == student_id,
        model.enrollment_id == Enrollment.id).correlate(Enrollment, Assignment, Quiz, SpeakingActivity))


def _branch(query, kind, parent, public_id, due_at, complete, running=None):
    return query.with_entities(literal(kind).label("kind"), public_id.label("public_id"), parent.id.label("sort_id"),
        parent.title.label("title"), Group.public_id.label("group_public_id"), Group.name.label("group_name"),
        Course.public_id.label("course_public_id"), Course.title.label("course_title"), Level.name.label("level_name"),
        parent.opens_at.label("opens_at"), due_at.label("due_at"), complete.label("complete"), (running if running is not None else literal(False)).label("running")).statement


def _activity_union(student_id, reference_utc):
    assignment_done = _submission_exists(Submission, Submission.assignment_id, Assignment.id, student_id)
    speaking_done = _submission_exists(SpeakingSubmission, SpeakingSubmission.speaking_activity_id, SpeakingActivity.id, student_id)
    quiz_done = exists(select(QuizAttempt.id).where(QuizAttempt.quiz_id == Quiz.id, QuizAttempt.student_id == student_id,
        QuizAttempt.enrollment_id == Enrollment.id, QuizAttempt.status.in_(["submitted", "expired"])).correlate(Quiz, Enrollment))
    quiz_running = exists(select(QuizAttempt.id).where(QuizAttempt.quiz_id == Quiz.id, QuizAttempt.student_id == student_id,
        QuizAttempt.enrollment_id == Enrollment.id, QuizAttempt.status == "in_progress", QuizAttempt.deadline_at > reference_utc).correlate(Quiz, Enrollment))
    return union_all(
        _branch(_visible_assignment_query(student_id, reference_utc), "assignment", Assignment, Assignment.public_id, Assignment.due_at, assignment_done),
        _branch(student_visible_quiz_query(student_id, reference_utc), "quiz", Quiz, Quiz.public_id, Quiz.closes_at, quiz_done, quiz_running),
        _branch(student_listening_query(student_id, reference_utc), "listening", Quiz, ListeningActivity.public_id, Quiz.closes_at, quiz_done, quiz_running),
        _branch(student_speaking_query(student_id, reference_utc), "speaking", Assignment, SpeakingActivity.public_id, Assignment.due_at, speaking_done),
    ).subquery("visible_activities")


def activities_page(student_id, reference_utc, *, kind="", state="", course="", group="", page=1, upcoming=False, cap=PAGE_SIZE):
    table = _activity_union(student_id, reference_utc)
    state_expr = case((table.c.running, "in_progress"), (table.c.complete, "submitted"),
        (table.c.opens_at > reference_utc, "scheduled"),
        (table.c.due_at > reference_utc, "open"), else_="past_due").label("state")
    window_expr = case((table.c.opens_at > reference_utc, "Upcoming"),
        (table.c.due_at > reference_utc, "Open"), else_="Closed").label("window_label")
    progress_expr = case((table.c.running, "In progress"), (table.c.complete, "Submitted"),
        else_="Not submitted").label("progress_label")
    query = select(table.c.kind, table.c.public_id, table.c.title, table.c.group_public_id, table.c.group_name,
        table.c.course_public_id, table.c.course_title, table.c.level_name, table.c.due_at, state_expr, window_expr, progress_expr)
    if kind in TYPES:
        query = query.where(table.c.kind == kind)
    if state in STATES:
        query = query.where(state_expr == state)
    if course:
        query = query.where(table.c.course_public_id == course)
    if group:
        query = query.where(table.c.group_public_id == group)
    if upcoming:
        query = query.where(table.c.due_at > reference_utc, table.c.complete.is_(False))
    query = query.order_by(case((table.c.due_at > reference_utc, 0), else_=1), table.c.due_at, table.c.kind, table.c.sort_id)
    rows = db.session.execute(query.offset((normalize_page(page) - 1) * cap).limit(cap + 1)).mappings().all()
    return [dict(row) for row in rows[:cap]], len(rows) > cap


def activity_view(rows, tz_name, url_builder):
    endpoints = {"assignment": ("student.assignment_detail", "assignment_public_id"), "quiz": ("student.quiz_detail", "quiz_public_id"),
        "listening": ("student.listening_detail", "listening_public_id"), "speaking": ("student.speaking_detail", "speaking_public_id")}
    items = []
    for row in rows:
        item = dict(row)
        endpoint, key = endpoints[item["kind"]]
        item.update(result_available=item["kind"] in {"quiz", "listening"} and item["progress_label"] == "Submitted",
            type_label=TYPES[item["kind"]], state_label=STATES[item["state"]],
            due_local=to_app_local(tz_name, item["due_at"]),
            course_url=url_builder("workspace.student_course", group_public_id=item["group_public_id"]),
            url=url_builder(endpoint, group_public_id=item["group_public_id"], **{key: item["public_id"]}),
            action_label="Continue" if item["state"] == "in_progress" else
                ("View receipt" if item["kind"] in {"assignment","speaking"} else "View result") if item["state"] == "submitted" else
                "View activity" if item["state"] == "past_due" else {"assignment":"Submit","speaking":"Record","quiz":"Start quiz","listening":"Start listening"}[item["kind"]])
        items.append(item)
    return items


def activity_groups(student_id, reference_utc, course=""):
    table = _activity_union(student_id, reference_utc)
    query = select(table.c.group_public_id, table.c.group_name, table.c.course_public_id, table.c.course_title).distinct()
    if course:
        query = query.where(table.c.course_public_id == course)
    return db.session.execute(query.order_by(table.c.group_name, table.c.group_public_id).limit(100)).mappings().all()


def activity_courses(student_id, reference_utc):
    table = _activity_union(student_id, reference_utc)
    return db.session.execute(select(table.c.course_public_id, table.c.course_title).distinct().order_by(
        table.c.course_title, table.c.course_public_id).limit(100)).mappings().all()
