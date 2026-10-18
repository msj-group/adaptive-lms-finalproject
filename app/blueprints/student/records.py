"""Own historical records; GET filters never finalize or mutate business data."""
from flask import abort, current_app, request, url_for
from flask_login import current_user
from sqlalchemy.orm import joinedload
from app.blueprints.student import student_bp
from app.blueprints.student.routes import private_no_store
from app.models import Enrollment, Group, SpeakingSubmission, User
from app.security.decorators import roles_required
from app.services.assignment_queries import normalize_page
from app.services.student_record_queries import ACTIVITY_TYPES, RECORD_TYPES, record_history

KINDS = {"grades": "Released grades", "attendance": "Finalized attendance", "quizzes": "Quiz and listening results",
         "assignments": "Assignment receipts", "speaking": "Speaking receipts", "progress": "Content progress", "corrections": "Correction history"}


def _owned_enrollments():
    return Enrollment.query.join(User, User.id == Enrollment.student_id).filter(
        Enrollment.student_id == current_user.id, User.role == "student", User.status == "active",
    ).options(joinedload(Enrollment.group).joinedload(Group.course),
        joinedload(Enrollment.group).joinedload(Group.academic_term))


def _episode(public_id):
    return _owned_enrollments().filter(Enrollment.public_id == public_id).first_or_404()


def _record_context(episode=None):
    enrollments = _owned_enrollments().order_by(Enrollment.created_at.desc(), Enrollment.id.desc()).all()
    courses = list({item.group.course.public_id: item.group.course for item in enrollments}.values())
    courses.sort(key=lambda course: (course.title.casefold(), course.public_id))
    course_id = episode.group.course.public_id if episode else request.args.get("course", "all") or "all"
    enrollment_id = episode.public_id if episode else request.args.get("enrollment", "all") or "all"
    if course_id != "all" and not any(course.public_id == course_id for course in courses):
        abort(404)
    selected = None
    if enrollment_id != "all":
        selected = next((item for item in enrollments if item.public_id == enrollment_id), None)
        if selected is None:
            abort(404)
        # Native GET fallback for a changed course and an old owned selection.
        if course_id != "all" and selected.group.course.public_id != course_id:
            selected = None
    return dict(episode=selected, enrollments=enrollments, courses=courses,
        selected_course=course_id, selected_enrollment=selected.public_id if selected else "all",
        kinds=KINDS, activity_types=ACTIVITY_TYPES, record_types=RECORD_TYPES, active_nav="records")


def _history_page(context, record_type, page):
    items, has_next, summary = [], False, None
    if context["enrollments"]:
        episode = context["episode"]
        items, has_next, summary = record_history(episode.id if episode else None, current_user.id,
            record_type, page, current_app.config["APP_TIMEZONE"], context["selected_course"])
        for item in items:
            if item["kind"] == "speaking":
                item["audio_url"] = url_for("student.episode_speaking_audio",
                    episode_public_id=item["enrollment_public_id"], submission_public_id=item["public_id"])
    return private_no_store("student/records/index.html", **context, record_type=record_type,
        items=items, summary=summary, page=page, has_next=has_next,
        history_title=RECORD_TYPES.get(record_type, "Record history"))


@student_bp.get("/records")
@roles_required("student")
def records():
    record_type = request.args.get("type", "all") or "all"
    if record_type not in ACTIVITY_TYPES and record_type not in RECORD_TYPES:
        abort(404)
    return _history_page(_record_context(), record_type, normalize_page(request.args.get("page")))


@student_bp.get("/records/<episode_public_id>/<kind>")
@roles_required("student")
def episode_records(episode_public_id, kind):
    if kind not in KINDS:
        abort(404)
    return _history_page(_record_context(_episode(episode_public_id)),
        "results" if kind == "quizzes" else kind, normalize_page(request.args.get("page")))


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
