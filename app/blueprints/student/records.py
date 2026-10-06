"""Own episode history, including released results after withdrawal/archive."""
from flask import abort, current_app, request, url_for
from flask_login import current_user
from sqlalchemy.orm import aliased, joinedload
from app.blueprints.student import student_bp
from app.blueprints.student.routes import private_no_store
from app.extensions import db
from app.models import AcademicRevision, Assignment, AttendanceRecord, AttendanceSession, Enrollment, GradeCategory, GradeItem, GradeRecord, Group, Lesson, LessonProgress, Quiz, QuizAttempt, SpeakingActivity, SpeakingFeedback, SpeakingSubmission, Submission, SubmissionFeedback, Unit, User
from app.security.decorators import roles_required
from app.services.assignment_queries import normalize_page
from app.services.schedule_occurrences import to_app_local

KINDS = {"grades": "Released grades", "attendance": "Finalized attendance", "quizzes": "Quiz and listening results",
         "assignments": "Assignment receipts", "speaking": "Speaking receipts", "progress": "Content progress", "corrections": "Correction history"}


def _episode(public_id):
    return Enrollment.query.join(User, User.id == Enrollment.student_id).filter(Enrollment.public_id == public_id,
        Enrollment.student_id == current_user.id, User.role == "student", User.status == "active").first_or_404()


@student_bp.get("/records")
@roles_required("student")
def records():
    page = Enrollment.query.join(User, User.id == Enrollment.student_id).filter(Enrollment.student_id == current_user.id,
        User.role == "student", User.status == "active").options(joinedload(Enrollment.group).joinedload(Group.course)).order_by(Enrollment.created_at.desc(), Enrollment.id.desc()).paginate(
        page=normalize_page(request.args.get("page")), per_page=20, error_out=False)
    return private_no_store("student/records/index.html", episodes=page, kinds=KINDS, active_nav="records")


@student_bp.get("/records/<episode_public_id>/<kind>")
@roles_required("student")
def episode_records(episode_public_id, kind):
    if kind not in KINDS:
        abort(404)
    episode = _episode(episode_public_id)
    page = normalize_page(request.args.get("page"))
    items, has_next = [], False
    tz_name = current_app.config["APP_TIMEZONE"]
    if kind == "grades":
        query = db.session.query(GradeRecord, GradeItem.title, GradeItem.max_points, Group.name).join(GradeItem,
            GradeItem.id == GradeRecord.grade_item_id).join(GradeCategory, GradeCategory.id == GradeItem.category_id).join(
            Group, Group.id == GradeCategory.group_id).filter(GradeRecord.enrollment_id == episode.id,
            GradeRecord.student_id == current_user.id, GradeItem.released_at.is_not(None)).order_by(GradeItem.released_at.desc(), GradeRecord.id.desc())
        rows = query.offset((page-1)*20).limit(21).all()
        has_next = len(rows) > 20
        items = [{"title": title, "group": group, "text": f"{record.score} / {maximum}", "note": record.comment,
                  "at": to_app_local(tz_name, record.graded_at) if record.graded_at else None} for record, title, maximum, group in rows[:20]]
    elif kind == "attendance":
        rows = db.session.query(AttendanceRecord.status, AttendanceSession.session_date, Group.name).join(AttendanceSession,
            AttendanceSession.id == AttendanceRecord.attendance_session_id).join(Group, Group.id == AttendanceSession.group_id).filter(
            AttendanceRecord.enrollment_id == episode.id, AttendanceRecord.student_id == current_user.id,
            AttendanceSession.finalized_at.is_not(None)).order_by(AttendanceSession.session_date.desc(), AttendanceRecord.id.desc()).offset((page-1)*20).limit(21).all()
        has_next = len(rows) > 20
        items = [{"title": str(day), "group": group, "text": status.title(), "note": None, "at": None} for status, day, group in rows[:20]]
    elif kind == "quizzes":
        rows = db.session.query(QuizAttempt, Quiz.title, Group.name).join(Quiz, Quiz.id == QuizAttempt.quiz_id).join(Group,
            Group.id == Quiz.group_id).filter(QuizAttempt.enrollment_id == episode.id, QuizAttempt.student_id == current_user.id,
            QuizAttempt.status.in_(["submitted", "expired"])).order_by(QuizAttempt.started_at.desc(), QuizAttempt.id.desc()).offset((page-1)*20).limit(21).all()
        has_next = len(rows) > 20
        items = [{"title": title, "group": group, "text": f"{record.correct_count} / {record.total_questions} · {record.status}",
            "note": "Attempt " + str(record.attempt_number), "at": to_app_local(tz_name, record.submitted_at or record.deadline_at)} for record, title, group in rows[:20]]
    elif kind in {"assignments", "speaking"}:
        model, feedback = (Submission, SubmissionFeedback) if kind == "assignments" else (SpeakingSubmission, SpeakingFeedback)
        reviewer = aliased(User)
        query = db.session.query(model, Assignment.title, Group.name, feedback.feedback_text, reviewer.role)
        if kind == "assignments":
            query = query.join(Assignment, Assignment.id == Submission.assignment_id).outerjoin(feedback, feedback.submission_id == Submission.id)
        else:
            query = query.join(SpeakingActivity, SpeakingActivity.id == SpeakingSubmission.speaking_activity_id).join(
                Assignment, Assignment.id == SpeakingActivity.assignment_id).outerjoin(feedback, feedback.speaking_submission_id == SpeakingSubmission.id)
        rows = query.join(Group, Group.id == Assignment.group_id).outerjoin(reviewer, reviewer.id == feedback.reviewer_id).filter(
            model.enrollment_id == episode.id, model.student_id == current_user.id).order_by(model.submitted_at.desc(), model.id.desc()).offset((page-1)*20).limit(21).all()
        has_next = len(rows) > 20
        items = [{"title": title, "group": group, "text": record.answer_text if kind == "assignments" else "Speaking recording submitted",
            "note": wording if reviewer_role == "teacher" else None, "at": to_app_local(tz_name, record.submitted_at),
            "audio_url": url_for("student.episode_speaking_audio", episode_public_id=episode.public_id, submission_public_id=record.public_id) if kind == "speaking" else None} for record, title, group, wording, reviewer_role in rows[:20]]
    elif kind == "progress":
        rows = db.session.query(LessonProgress, Lesson.title, Group.name).join(Lesson,
            Lesson.id == LessonProgress.lesson_id).join(Unit, Unit.id == Lesson.unit_id).join(Group,
            Group.id == LessonProgress.group_id).filter(LessonProgress.enrollment_id == episode.id,
            LessonProgress.student_id == current_user.id, Unit.group_id == LessonProgress.group_id).order_by(
            LessonProgress.created_at.desc(), LessonProgress.id.desc()).offset((page-1)*20).limit(21).all()
        has_next = len(rows) > 20
        items = [{"title": title, "group": group, "text": "Completed" if record.completed_at else "Opened",
            "note": "Content progress records completion, not language mastery.",
            "at": to_app_local(tz_name, record.completed_at or record.last_opened_at or record.created_at)}
            for record, title, group in rows[:20]]
    else:
        # Publication is rechecked per target in SQL; a revision cannot expose
        # an unreleased grade or draft attendance merely because it is old.
        released_grade = db.session.query(GradeRecord.id).join(GradeItem, GradeItem.id == GradeRecord.grade_item_id).filter(
            GradeRecord.id == AcademicRevision.grade_record_id, GradeItem.released_at.is_not(None)).exists()
        finalized_attendance = db.session.query(AttendanceRecord.id).join(AttendanceSession,
            AttendanceSession.id == AttendanceRecord.attendance_session_id).filter(
            AttendanceRecord.id == AcademicRevision.attendance_record_id, AttendanceSession.finalized_at.is_not(None)).exists()
        rows = AcademicRevision.query.filter(AcademicRevision.enrollment_id == episode.id,
            (AcademicRevision.grade_record_id.is_(None) | released_grade),
            (AcademicRevision.attendance_record_id.is_(None) | finalized_attendance)).order_by(AcademicRevision.id.desc()).offset((page-1)*20).limit(21).all()
        has_next = len(rows) > 20
        fields = {"score": "Score", "comment": "Grade comment", "status": "Attendance", "feedback_text": "Feedback"}
        for row in rows[:20]:
            changes = [(label, row.before_snapshot.get(key), row.after_snapshot.get(key)) for key, label in fields.items()
                if row.before_snapshot.get(key) != row.after_snapshot.get(key)]
            if changes:
                items.append({"title": "Recorded correction", "group": "", "at": to_app_local(tz_name, row.created_at), "changes": changes})
    return private_no_store("student/records/detail.html", episode=episode, kind=kind, kinds=KINDS, items=items,
        page=page, has_next=has_next, active_nav="records", tz_name=tz_name)


@student_bp.get("/records/<episode_public_id>/speaking/<submission_public_id>/audio")
@roles_required("student")
def episode_speaking_audio(episode_public_id, submission_public_id):
    from app.services.speaking_queries import audio_upload_for_submission
    from app.services.material_serving import serve_uploaded_file
    episode = _episode(episode_public_id)
    submission = SpeakingSubmission.query.filter_by(public_id=submission_public_id,
        enrollment_id=episode.id, student_id=current_user.id).first_or_404()
    uploaded_file = audio_upload_for_submission(submission)
    if uploaded_file is None or uploaded_file.uploaded_by_id != current_user.id:
        abort(404)
    return serve_uploaded_file(uploaded_file, current_user.id, request.args.get("download") == "yes")
