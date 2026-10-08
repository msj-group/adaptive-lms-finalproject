"""Bounded, read-only comment queue shared by the Teacher workspace and dashboard."""
from datetime import date, datetime, time, timedelta

from sqlalchemy import exists, literal, select, union_all

from app.extensions import db
from app.models import (AcademicTerm, Assignment, Course, Group, GroupTeacherAssignment,
                        Level, SpeakingActivity, SpeakingFeedback, SpeakingSubmission,
                        Submission, SubmissionFeedback, User)
from app.services.schedule_occurrences import from_app_local, to_app_local


def review_page(teacher_id, tz_name, *, page=1, cap=25, kind="", state="pending",
                group_id="", since="", until=""):
    """Oldest submissions first, scoped in SQL to active assigned groups.

    Pending means no saved comment, never an inferred missing grade. Date
    parsing raises ValueError/OverflowError for the owning route to handle.
    Fetch one extra row rather than counting or loading the whole queue.
    """
    membership = exists().where(GroupTeacherAssignment.group_id == Group.id,
        GroupTeacherAssignment.teacher_id == teacher_id, GroupTeacherAssignment.status == "active")
    common = [membership, User.role == "student", Group.status == "active",
              Course.status == "active", Level.status == "active", AcademicTerm.status == "active"]
    if group_id:
        common.append(Group.public_id == group_id)
    queries = []
    for submission, feedback, feedback_key, activity_key, label in [
        (Submission, SubmissionFeedback, SubmissionFeedback.submission_id, Assignment.public_id, "assignment"),
        (SpeakingSubmission, SpeakingFeedback, SpeakingFeedback.speaking_submission_id, SpeakingActivity.public_id, "speaking")]:
        if kind and kind != label:
            continue
        query = select(submission.public_id.label("submission_id"), User.full_name.label("student_name"),
            Assignment.title.label("title"), Group.name.label("group_name"), Group.public_id.label("group_id"),
            activity_key.label("activity_id"), submission.submitted_at.label("submitted_at"),
            feedback.id.label("feedback_id"), literal(label).label("kind")).select_from(submission)
        if label == "speaking":
            query = query.join(SpeakingActivity, SpeakingActivity.id == submission.speaking_activity_id).join(
                Assignment, Assignment.id == SpeakingActivity.assignment_id)
        else:
            query = query.join(Assignment, Assignment.id == submission.assignment_id).where(
                ~exists().where(SpeakingActivity.assignment_id == Assignment.id))
        query = query.join(Group, Group.id == Assignment.group_id).join(Course, Course.id == Group.course_id).join(
            Level, Level.id == Course.level_id).join(AcademicTerm, AcademicTerm.id == Group.academic_term_id).join(
            User, User.id == submission.student_id).outerjoin(feedback, feedback_key == submission.id).where(*common)
        if state == "pending":
            query = query.where(feedback.id.is_(None))
        elif state == "reviewed":
            query = query.where(feedback.id.is_not(None))
        for raw, end in [(since, False), (until, True)]:
            if raw:
                day = date.fromisoformat(raw) + (timedelta(days=1) if end else timedelta())
                moment = from_app_local(tz_name, datetime.combine(day, time.min))
                query = query.where(submission.submitted_at < moment if end else submission.submitted_at >= moment)
        queries.append(query)
    if not queries:
        return [], False
    combined = union_all(*queries).subquery()
    result = db.session.execute(select(combined).order_by(combined.c.submitted_at, combined.c.submission_id).
        offset((page - 1) * cap).limit(cap + 1)).mappings().all()
    return [dict(row) for row in result[:cap]], len(result) > cap


def review_view(rows, tz_name, url_builder):
    items = []
    for row in rows:
        item = dict(row)
        args = dict(group_public_id=row["group_id"], submission_public_id=row["submission_id"])
        args["speaking_public_id" if row["kind"] == "speaking" else "assignment_public_id"] = row["activity_id"]
        item["url"] = url_builder("teacher.speaking_submission_detail" if row["kind"] == "speaking" else
                                  "teacher.submission_detail", **args)
        # The existing editor includes the answer/recording and rechecks write
        # eligibility itself. Opening it never saves a comment or a grade.
        item["feedback_url"] = url_builder("teacher.speaking_submission_feedback" if row["kind"] == "speaking" else
                                           "teacher.submission_feedback", **args)
        item["local_time"] = to_app_local(tz_name, row["submitted_at"])
        items.append(item)
    return items
