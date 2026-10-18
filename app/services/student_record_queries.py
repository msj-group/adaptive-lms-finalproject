"""Read-only Student history, scoped to owned courses/enrollment episodes."""
from sqlalchemy import and_, case, func, literal, select, union_all
from sqlalchemy.orm import aliased

from app.extensions import db
from app.models import (
    AcademicRevision, AcademicTerm, Assignment, AttendanceRecord, AttendanceSession,
    Course, Enrollment, GradeCategory, GradeItem, GradeRecord, Group, Lesson,
    LessonProgress, ListeningActivity, Quiz, QuizAttempt, SpeakingActivity,
    SpeakingFeedback, SpeakingSubmission, Submission, SubmissionFeedback, Unit, User,
)
from app.services.schedule_occurrences import to_app_local

PAGE_SIZE = 20
ACTIVITY_TYPES = {
    "all": "All", "quizzes": "Quizzes", "listening": "Listening",
    "assignments": "Assignments", "speaking": "Speaking",
    "results": "Quiz and listening results",
}
RECORD_TYPES = {"grades": "Released grades", "attendance": "Finalized attendance",
    "progress": "Content progress", "corrections": "Correction history"}
TYPE_LABELS = {"quizzes": "Quiz", "listening": "Listening", "assignments": "Assignment",
    "speaking": "Speaking", **RECORD_TYPES}
TYPE_ICONS = {"quizzes": "quiz", "listening": "headphones", "assignments": "pen",
    "speaking": "microphone", "grades": "chart", "attendance": "calendar",
    "progress": "book", "corrections": "pen"}
SUBMISSION_TYPES = ("quizzes", "listening", "assignments", "speaking")
_enrollment_group = aliased(Group, name="record_enrollment_group")


def _owned(statement, model, episode_id, student_id, course_id=None):
    # History survives withdrawal/archive. Recheck Student role/status and retain
    # the original source Group, separately from the enrollment's current Group.
    predicates = [Enrollment.id == model.enrollment_id]
    if hasattr(model, "student_id"):
        predicates.append(Enrollment.student_id == model.student_id)
    statement = statement.join(Enrollment, and_(*predicates)).join(
        User, User.id == Enrollment.student_id,
    ).join(_enrollment_group, _enrollment_group.id == Enrollment.group_id).join(
        Course, Course.id == _enrollment_group.course_id,
    ).join(AcademicTerm, AcademicTerm.id == _enrollment_group.academic_term_id).where(
        Enrollment.student_id == student_id, User.role == "student", User.status == "active",
    )
    if episode_id is not None:
        statement = statement.where(Enrollment.id == episode_id)
    if course_id and course_id != "all":
        statement = statement.where(Course.public_id == course_id)
    return statement


def _columns(model, kind, title, group_name, at, status=None, score=None, maximum=None, attempt=None):
    def value(expression):
        return literal(None) if expression is None else expression
    return (
        model.id.label("source_id"),
        (model.public_id if hasattr(model, "public_id") else literal(None)).label("public_id"),
        (literal(kind) if isinstance(kind, str) else kind).label("kind"),
        title.label("title"), group_name.label("group_name"), at.label("at"),
        value(status).label("status"), value(score).label("score"),
        value(maximum).label("maximum"), value(attempt).label("attempt"),
        Enrollment.public_id.label("enrollment_public_id"),
        Enrollment.created_at.label("enrollment_started"),
        Course.title.label("course_title"), AcademicTerm.name.label("term_name"),
        _enrollment_group.name.label("enrollment_group_name"),
    )


def _revision_visibility():
    released_grade = select(GradeRecord.id).join(GradeItem, GradeItem.id == GradeRecord.grade_item_id).where(
        GradeRecord.id == AcademicRevision.grade_record_id, GradeItem.released_at.is_not(None)).exists()
    finalized_attendance = select(AttendanceRecord.id).join(AttendanceSession,
        AttendanceSession.id == AttendanceRecord.attendance_session_id).where(
        AttendanceRecord.id == AcademicRevision.attendance_record_id,
        AttendanceSession.finalized_at.is_not(None)).exists()
    return and_(AcademicRevision.grade_record_id.is_(None) | released_grade,
        AcademicRevision.attendance_record_id.is_(None) | finalized_attendance)


def record_projection(episode_id, student_id, record_type, course_id=None):
    """Narrow UNION identities only; hydrate at most one page of owned payloads."""
    quiz_kind = case((ListeningActivity.id.is_not(None), literal("listening")), else_=literal("quizzes"))
    quiz = select(*_columns(QuizAttempt, quiz_kind, Quiz.title, Group.name,
        func.coalesce(QuizAttempt.submitted_at, QuizAttempt.deadline_at), QuizAttempt.status,
        QuizAttempt.correct_count, QuizAttempt.total_questions, QuizAttempt.attempt_number,
    )).select_from(QuizAttempt).join(Quiz, Quiz.id == QuizAttempt.quiz_id).join(
        Group, Group.id == Quiz.group_id,
    ).outerjoin(ListeningActivity, ListeningActivity.quiz_id == Quiz.id).where(
        QuizAttempt.status.in_(("submitted", "expired")),
    )
    sources = [quiz]
    models = [QuizAttempt]
    for model, kind in ((Submission, "assignments"), (SpeakingSubmission, "speaking")):
        statement = select(*_columns(model, kind, Assignment.title, Group.name,
            model.submitted_at, literal("submitted"))).select_from(model)
        if kind == "speaking":
            statement = statement.join(SpeakingActivity, SpeakingActivity.id == model.speaking_activity_id).join(
                Assignment, Assignment.id == SpeakingActivity.assignment_id)
        else:
            statement = statement.join(Assignment, Assignment.id == model.assignment_id)
        sources.append(statement.join(Group, Group.id == Assignment.group_id))
        models.append(model)

    sources.append(select(*_columns(GradeRecord, "grades", GradeItem.title, Group.name,
        func.coalesce(GradeRecord.graded_at, GradeItem.released_at), score=GradeRecord.score,
        maximum=GradeItem.max_points)).select_from(GradeRecord).join(
        GradeItem, GradeItem.id == GradeRecord.grade_item_id).join(
        GradeCategory, GradeCategory.id == GradeItem.category_id).join(
        Group, Group.id == GradeCategory.group_id).where(GradeItem.released_at.is_not(None)))
    models.append(GradeRecord)
    # The timeline records when attendance was finalized; its original session
    # date stays the card title. No session time or attendance rate is invented.
    sources.append(select(*_columns(AttendanceRecord, "attendance",
        func.cast(AttendanceSession.session_date, db.String(10)), Group.name,
        AttendanceSession.finalized_at)).select_from(AttendanceRecord).join(
        AttendanceSession, AttendanceSession.id == AttendanceRecord.attendance_session_id).join(
        Group, Group.id == AttendanceSession.group_id).where(AttendanceSession.finalized_at.is_not(None)))
    models.append(AttendanceRecord)
    sources.append(select(*_columns(LessonProgress, "progress", Lesson.title, Group.name,
        func.coalesce(LessonProgress.completed_at, LessonProgress.last_opened_at, LessonProgress.created_at),
    )).select_from(LessonProgress).join(Lesson, Lesson.id == LessonProgress.lesson_id).join(
        Unit, Unit.id == Lesson.unit_id).join(Group, Group.id == LessonProgress.group_id).where(
        Unit.group_id == LessonProgress.group_id))
    models.append(LessonProgress)

    sources.append(select(*_columns(AcademicRevision, "corrections", literal("Recorded correction"),
        literal(""), AcademicRevision.created_at)).select_from(AcademicRevision).where(_revision_visibility()))
    models.append(AcademicRevision)
    history = union_all(*[_owned(statement, model, episode_id, student_id, course_id)
        for statement, model in zip(sources, models)]).subquery()
    if record_type != "all":
        kinds = ("quizzes", "listening") if record_type == "results" else (record_type,)
        history = select(history).where(history.c.kind.in_(kinds)).subquery()
    return history


def _details(rows, episode_id, student_id, course_id):
    details = {}
    for kind, model, feedback, foreign_key in (
        ("assignments", Submission, SubmissionFeedback, SubmissionFeedback.submission_id),
        ("speaking", SpeakingSubmission, SpeakingFeedback, SpeakingFeedback.speaking_submission_id),
    ):
        ids = [row["source_id"] for row in rows if row["kind"] == kind]
        if not ids:
            continue
        reviewer = aliased(User)
        statement = select(model, feedback.feedback_text, reviewer.role).select_from(model).outerjoin(
            feedback, foreign_key == model.id).outerjoin(reviewer, reviewer.id == feedback.reviewer_id).where(
            model.id.in_(ids))
        for record, wording, role in db.session.execute(_owned(statement, model, episode_id, student_id, course_id)):
            details[(kind, record.id)] = {"text": record.answer_text if kind == "assignments" else None,
                "note": wording if role == "teacher" else None}
    for kind, model in (("grades", GradeRecord), ("attendance", AttendanceRecord),
                        ("progress", LessonProgress), ("corrections", AcademicRevision)):
        ids = [row["source_id"] for row in rows if row["kind"] == kind]
        if not ids:
            continue
        statement = select(model).where(model.id.in_(ids))
        if kind == "grades":
            statement = statement.join(GradeItem, GradeItem.id == GradeRecord.grade_item_id).where(
                GradeItem.released_at.is_not(None))
        elif kind == "attendance":
            statement = statement.join(AttendanceSession,
                AttendanceSession.id == AttendanceRecord.attendance_session_id).where(
                AttendanceSession.finalized_at.is_not(None))
        elif kind == "corrections":
            statement = statement.where(_revision_visibility())
        else:
            statement = statement.join(Lesson, Lesson.id == LessonProgress.lesson_id).join(
                Unit, Unit.id == Lesson.unit_id).where(Unit.group_id == LessonProgress.group_id)
        statement = _owned(statement, model, episode_id, student_id, course_id)
        for record in db.session.execute(statement).scalars():
            data = {}
            if kind == "grades":
                data["note"] = record.comment
            elif kind == "attendance":
                data["text"] = record.status.title()
            elif kind == "progress":
                data.update(text="Completed" if record.completed_at else "Opened",
                    note="Content progress records completion, not language mastery.")
            else:
                fields = {"score": "Score", "comment": "Grade comment", "status": "Attendance", "feedback_text": "Feedback"}
                data["changes"] = [(label, record.before_snapshot.get(key), record.after_snapshot.get(key))
                    for key, label in fields.items() if record.before_snapshot.get(key) != record.after_snapshot.get(key)]
            details[(kind, record.id)] = data
    return details


def record_history(episode_id, student_id, record_type, page, tz_name, course_id=None):
    history = record_projection(episode_id, student_id, record_type, course_id)
    counts = {key: 0 for key in SUBMISSION_TYPES}
    expired = 0
    if record_type in ACTIVITY_TYPES:
        totals = select(history.c.kind, history.c.status, func.count()).where(
            history.c.kind.in_(SUBMISSION_TYPES)).group_by(history.c.kind, history.c.status)
        for kind, status, count in db.session.execute(totals):
            if status == "submitted":
                counts[kind] = count
            elif status == "expired":
                expired += count
    rows = db.session.execute(select(history).order_by(history.c.at.desc(), history.c.kind.asc(),
        history.c.source_id.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1)).mappings().all()
    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    details = _details(rows, episode_id, student_id, course_id)
    items = []
    for row in rows:
        kind = row["kind"]
        item = dict(row)
        item.update(type_label=TYPE_LABELS[kind], icon=TYPE_ICONS[kind], group=row["group_name"],
            at=to_app_local(tz_name, row["at"]) if row["at"] else None,
            enrollment_started=to_app_local(tz_name, row["enrollment_started"]), has_time=kind != "attendance",
            score_text=f'{row["score"]} / {row["maximum"]}' if row["score"] is not None else None,
            text=None, note=None, audio_url=None)
        item.update(details.get((kind, row["source_id"]), {}))
        # MySQL's UNION may promote integer Quiz counts to the grade DECIMAL
        # type. Keep Quiz/Listening's established whole-question score display.
        if kind in {"quizzes", "listening"} and row["score"] is not None:
            item["score_text"] = f'{int(row["score"])} / {int(row["maximum"])}'
        if kind in RECORD_TYPES and (kind, row["source_id"]) not in details:
            continue
        if kind == "corrections" and not item.get("changes"):
            continue
        items.append(item)
    summary = dict(total=sum(counts.values()), counts=counts, expired=expired) if record_type in ACTIVITY_TYPES else None
    return items, has_next, summary
