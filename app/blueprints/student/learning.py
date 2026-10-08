"""Student lesson navigation (M11) and lesson progress (Phase 4 / M13).

Group-centered, public-id-only routes::

    GET   /student/groups/<gp>/units
    GET   /student/groups/<gp>/units/<up>/lessons/<lp>
    POST  /student/groups/<gp>/units/<up>/lessons/<lp>/complete
    POST  /student/groups/<gp>/units/<up>/lessons/<lp>/undo-complete

**Authorization is SQL-scoped** to ``current_user.id`` in
``app/services/student_lessons.py`` -- an object is never loaded broadly
and then authorized. A Student may reach the outline only through their
own **active** ``Enrollment`` for that exact Group, with an active
AcademicTerm / Level / Course / Group; a Lesson page additionally
requires an active Unit and a ``published`` Lesson. Every failure --
withdrawn / missing Enrollment, a different Group, mismatched nested ids,
an inactive Unit or ancestor, a draft or non-existent Lesson -- returns a
non-disclosing 404. Student access does not depend on Schedule existence
or on any current Teacher assignment.

``roles_required(STUDENT)`` gives the role guard (anonymous -> login,
any other role -> 403); a suspended Student cannot hold a session at all
(the Flask-Login ``user_loader`` rejects it).

**Lesson progress (Phase 4 / M13).** The outline marks the Lessons this
Student has completed, and the Lesson page shows its completion state with
exactly one action: mark complete, or undo. Both actions are POST-only,
CSRF-protected, and carry a signed token
(``app/services/lesson_progress_tokens.py``) bound to this Student, Group,
Unit, Lesson, action and the progress version the page showed. A POST
previews access in SQL (any failure is the same 404), verifies the token (a
failure writes nothing and asks for a reload), and then
``app/services/lesson_progress_transactions.py`` decides under the full lock
chain: an action the current state already satisfies is a no-op, a changed
version is stale, and access that ended meanwhile is a 404. Every POST ends
in a redirect.

**The only GET that writes is the Lesson page**, and only for a real GET:
once the page has been authorized and rendered, it records the opening --
at most once per ``OPEN_REFRESH_SECONDS``, never touching completion or the
version. A failure to record is logged and never turns the authorized page
into an error. The outline stays a pure read.

Both pages show this Student's own progress, so they carry
``Cache-Control: private, no-store`` and ``Vary: Cookie``.
"""

from flask import abort, current_app, flash, redirect, request, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from app.blueprints.collector.hooks import note_outcome
from app.blueprints.student import student_bp
from app.blueprints.student.routes import private_no_store
from app.extensions import db
from app.models import UserRole
from app.security.decorators import roles_required
from app.services import lesson_progress_transactions as progress_tx
from app.services.lesson_progress_queries import (
    build_lesson_progress_view,
    student_lesson_progress,
)
from app.services.lesson_progress_tokens import (
    ACTION_COMPLETE,
    ACTION_UNDO,
    load_progress_token,
    make_progress_token,
)
from app.services.student_lessons import (
    outline_units,
    student_lesson_detail,
    student_outline_group,
)

_STUDENT = UserRole.STUDENT.value

_FORM_UNVERIFIED = (
    "This action could not be verified. It may have expired. Reload the lesson and try again."
)
_STALE = (
    "Your progress on this lesson changed after the page was opened, so nothing was changed. "
    "Review its current state and try again."
)
_CONFLICT = (
    "That could not be saved because of a conflicting change. Nothing was written. "
    "Please try again."
)
_DONE = {
    ACTION_COMPLETE: "Lesson marked as complete.",
    ACTION_UNDO: "Completion undone. This lesson is no longer marked as complete.",
}
_ALREADY = {
    ACTION_COMPLETE: "This lesson is already marked as complete. Nothing was changed.",
    ACTION_UNDO: "This lesson is not marked as complete. Nothing was changed.",
}


@student_bp.get("/groups/<group_public_id>/units")
@roles_required(_STUDENT)
def group_units(group_public_id):
    group = student_outline_group(current_user.id, group_public_id)
    if group is None:
        abort(404)
    return private_no_store(
        "student/learning/outline.html",
        group_name=group.name,
        group_public_id=group.public_id,
        course_title=group.course.title,
        level_name=group.course.level.name,
        term_name=group.academic_term.name,
        units=outline_units(group.id, current_user.id),
    )


@student_bp.get("/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>")
@roles_required(_STUDENT)
def lesson_detail(group_public_id, unit_public_id, lesson_public_id):
    student_id = current_user.id
    page = student_lesson_detail(student_id, group_public_id, unit_public_id, lesson_public_id)
    if page is None:
        abort(404)
    progress = page.progress
    action = ACTION_UNDO if progress.completed_at is not None else ACTION_COMPLETE
    tz_name = current_app.config.get("APP_TIMEZONE", "UTC")
    outline = outline_units(progress.group_id, student_id)
    lesson_links = [dict(title=lesson["title"], public_id=lesson["public_id"],
        unit_public_id=unit["public_id"], is_completed=lesson["is_completed"])
        for unit in outline for lesson in unit["lessons"]]
    index = next((i for i, lesson in enumerate(lesson_links) if lesson["public_id"] == lesson_public_id), None)
    response = private_no_store(
        "student/learning/lesson.html",
        progress=build_lesson_progress_view(progress, tz_name),
        progress_action=action,
        progress_state=make_progress_token(
            current_user.public_id,
            progress.group_public_id,
            progress.unit_public_id,
            progress.lesson_public_id,
            action,
            progress.version,
        ),
        tz_name=tz_name,
        lesson_links=lesson_links,
        previous_lesson=lesson_links[index - 1] if index is not None and index > 0 else None,
        next_lesson=lesson_links[index + 1] if index is not None and index + 1 < len(lesson_links) else None,
        **page.view,
    )
    # Recorded only after the page above was authorized and fully rendered,
    # so no read of this request runs after the write commits.
    if request.method == "GET":
        _record_open(student_id, progress)
    return response


def _record_open(student_id, progress):
    """Best effort: an unexpected database error is logged, rolled back,
    and never turns an authorized page into an error."""
    try:
        progress_tx.record_open(student_id, progress)
    except (SQLAlchemyError, ValueError):
        db.session.rollback()
        current_app.logger.exception(
            "Recording a lesson opening failed; the lesson page itself was served normally"
        )


@student_bp.post(
    "/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>/complete"
)
@roles_required(_STUDENT)
def lesson_mark_complete(group_public_id, unit_public_id, lesson_public_id):
    """Mark the Lesson complete for the acting Student."""
    return _change_completion(group_public_id, unit_public_id, lesson_public_id, ACTION_COMPLETE)


@student_bp.post(
    "/groups/<group_public_id>/units/<unit_public_id>/lessons/<lesson_public_id>/undo-complete"
)
@roles_required(_STUDENT)
def lesson_undo_complete(group_public_id, unit_public_id, lesson_public_id):
    """Clear the acting Student's completion mark on the Lesson."""
    return _change_completion(group_public_id, unit_public_id, lesson_public_id, ACTION_UNDO)


def _completion_destination(student_id, progress):
    """Resolve only the next currently visible lesson in this own Group.

    No destination identity/URL comes from the form. The destination GET
    rechecks enrollment/publication if either changes during completion.
    """
    lessons = [dict(unit_public_id=unit["public_id"], public_id=lesson["public_id"])
               for unit in outline_units(progress.group_id, student_id)
               for lesson in unit["lessons"]]
    index = next((i for i, lesson in enumerate(lessons)
                  if lesson["public_id"] == progress.lesson_public_id), None)
    if index is None or index + 1 >= len(lessons):
        return None
    lesson = lessons[index + 1]
    return url_for("student.lesson_detail", group_public_id=progress.group_public_id,
                   unit_public_id=lesson["unit_public_id"], lesson_public_id=lesson["public_id"])


def _change_completion(group_public_id, unit_public_id, lesson_public_id, action):
    # Captured before the transaction's reset, so nothing reloads the user
    # between the reset and the locks.
    student_id = current_user.id
    actor_public_id = current_user.public_id

    progress = student_lesson_progress(
        student_id, group_public_id, unit_public_id, lesson_public_id
    )
    if progress is None:
        abort(404)
    lesson_url = url_for(
        "student.lesson_detail",
        group_public_id=progress.group_public_id,
        unit_public_id=progress.unit_public_id,
        lesson_public_id=progress.lesson_public_id,
        _anchor="progress",
    )

    payload = load_progress_token(
        request.form.get("progress_state"),
        actor_public_id,
        progress.group_public_id,
        progress.unit_public_id,
        progress.lesson_public_id,
        action,
    )
    if payload is None:
        note_outcome("lesson_completion", "rejected")
        flash(_FORM_UNVERIFIED, "warning")
        return redirect(lesson_url)

    continue_url = None
    if action == ACTION_COMPLETE and request.form.get("after_save") == "next_lesson":
        continue_url = _completion_destination(student_id, progress)

    outcome = progress_tx.set_completion(
        student_id,
        progress.group_public_id,
        progress.unit_public_id,
        progress.lesson_public_id,
        action,
        payload["progress_version"],
    )
    if outcome == progress_tx.UNAVAILABLE:
        abort(404)
    if outcome == progress_tx.CHANGED:
        note_outcome("lesson_completion", "completed" if action == ACTION_COMPLETE else "undone")
        flash(_DONE[action], "success")
    elif outcome == progress_tx.ALREADY:
        note_outcome("lesson_completion", "unchanged")
        flash(_ALREADY[action], "info")
    elif outcome == progress_tx.STALE:
        note_outcome("lesson_completion", "rejected")
        flash(_STALE, "warning")
    else:
        note_outcome("lesson_completion", "rejected")
        flash(_CONFLICT, "danger")
    if continue_url and outcome in (progress_tx.CHANGED, progress_tx.ALREADY):
        return redirect(continue_url)
    return redirect(lesson_url)
