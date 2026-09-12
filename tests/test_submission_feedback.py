"""Teacher feedback on immutable text Submissions (Phase 4 / M03).

One nested Teacher GET/POST editor, the derived indicators M03 adds to
the two M02 Teacher read pages, and the feedback section it adds to the
Student's own submission receipt.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
REPEATABLE READ snapshot isolation, so the concurrency tests here prove
only the *requested* reset and lock order and exercise the post-lock
recheck logic by injecting a state change at a chosen point -- they are
**never** a demonstration of real InnoDB blocking. Time is injected, not
waited for, so every timestamp assertion is exact rather than
probabilistic.
"""

import re
from datetime import date, datetime, timedelta
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest

import app.blueprints.teacher.feedback as feedback_mod
from app.extensions import db
from app.models import (
    FEEDBACK_MAX_LENGTH,
    AcademicStatus,
    AcademicTerm,
    Assignment,
    AssignmentStatus,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    Submission,
    SubmissionFeedback,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password
from tests.conftest import login

PW = "Sup3rSecret!123"

OPENS_UTC = datetime(2026, 5, 1, 6, 0)
DUE_UTC = datetime(2026, 5, 8, 21, 59)
SUBMITTED = datetime(2026, 5, 3, 10, 30)

#: Deterministic write moments. NOW is after the deadline on purpose --
#: feedback is written on finished work.
NOW = datetime(2026, 5, 10, 9, 0, 0)
LATER = datetime(2026, 5, 11, 14, 30, 0)

_UUID = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)


class _Clock:
    """A deterministic replacement for ``utc_reference_now``.

    Yields each supplied moment in turn and then repeats the last one, so
    "a second co-teacher committed while this request waited on a lock"
    is an exact scenario rather than a race.
    """

    def __init__(self, *moments):
        self.moments = list(moments) or [NOW]
        self.calls = 0

    def __call__(self, utc_now=None):
        moment = self.moments[min(self.calls, len(self.moments) - 1)]
        self.calls += 1
        return moment


def _at(*moments):
    """Patch the feedback route module's clock for a ``with`` block."""
    return patch.object(feedback_mod, "utc_reference_now", _Clock(*moments))


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _user(email, role, status=UserStatus.ACTIVE.value, full_name=None):
    row = User(email=email, password_hash=hash_password(PW),
               full_name=full_name or email.split("@")[0], role=role, status=status)
    db.session.add(row)
    db.session.commit()
    return row


def _hierarchy(term_status=AcademicStatus.ACTIVE.value, level_status=AcademicStatus.ACTIVE.value,
               course_status=AcademicStatus.ACTIVE.value, group_status=AcademicStatus.ACTIVE.value,
               group_name="Group A"):
    term = AcademicTerm(name=f"Term {group_name}", start_date=date(2026, 1, 1),
                        end_date=date(2026, 12, 31), status=term_status)
    level = Level(name=f"Level {group_name}", display_order=0, status=level_status)
    db.session.add_all([term, level])
    db.session.commit()
    course = Course(title=f"English {group_name}", level_id=level.id, display_order=0,
                    status=course_status)
    db.session.add(course)
    db.session.commit()
    group = Group(academic_term_id=term.id, course_id=course.id, name=group_name,
                  capacity=20, status=group_status)
    db.session.add(group)
    db.session.commit()
    return group


def _assign(group, teacher, status=GroupTeacherAssignmentStatus.ACTIVE.value):
    row = GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id, status=status)
    db.session.add(row)
    db.session.commit()
    return row


def _assignment(group, title="Task 1", opens_at=OPENS_UTC, due_at=DUE_UTC,
                status=AssignmentStatus.PUBLISHED.value, instructions="Do the work."):
    published_at = datetime(2026, 4, 1, 9, 0) if status == AssignmentStatus.PUBLISHED.value else None
    row = Assignment(group_id=group.id, title=title, instructions=instructions,
                     opens_at=opens_at, due_at=due_at, status=status,
                     published_at=published_at)
    db.session.add(row)
    db.session.commit()
    return row


def _enrolled_student(group, email, status=EnrollmentStatus.ACTIVE.value,
                      account=UserStatus.ACTIVE.value, role=UserRole.STUDENT.value,
                      full_name=None):
    student = _user(email, role, status=account, full_name=full_name)
    db.session.add(Enrollment(student_id=student.id, group_id=group.id, status=status))
    db.session.commit()
    return student


def _submit(assignment, student, answer="An answer.", submitted_at=SUBMITTED):
    row = Submission(assignment_id=assignment.id, student_id=student.id,
                     answer_text=answer, submitted_at=submitted_at)
    db.session.add(row)
    db.session.commit()
    return row


def _feedback_row(submission, reviewer, text="Good work.", version=1, moment=NOW):
    row = SubmissionFeedback(submission_id=submission.id, reviewer_id=reviewer.id,
                             feedback_text=text, version=version,
                             created_at=moment, updated_at=moment)
    db.session.add(row)
    db.session.commit()
    return row


class _World:
    """The public identifiers one scenario needs, captured outside the
    app context so no ORM row leaks into a request."""

    __slots__ = ("gpid", "apid", "spid", "gid", "aid", "sid", "tid", "stid")

    def __init__(self, group, assignment, submission, teacher, student):
        self.gpid, self.apid, self.spid = (
            group.public_id, assignment.public_id, submission.public_id
        )
        self.gid, self.aid, self.sid = group.id, assignment.id, submission.id
        self.tid, self.stid = teacher.id, student.id


def _world(email="teacher@example.com", student_email="s@example.com",
           student_name="Sara Student", **hkw):
    teacher = _user(email, UserRole.TEACHER.value, full_name="Tariq Teacher")
    group = _hierarchy(**hkw)
    _assign(group, teacher)
    assignment = _assignment(group)
    student = _enrolled_student(group, student_email, full_name=student_name)
    submission = _submit(assignment, student)
    return _World(group, assignment, submission, teacher, student)


# ---------------------------------------------------------------------------
# URLs and request helpers
# ---------------------------------------------------------------------------


def _list_url(w):
    return f"/teacher/groups/{w.gpid}/assignments/{w.apid}/submissions"


def _detail_url(w):
    return f"{_list_url(w)}/{w.spid}"


def _feedback_url(w):
    return f"{_detail_url(w)}/feedback"


def _student_url(w):
    return f"/student/groups/{w.gpid}/assignments/{w.apid}"


def _fresh_identity():
    """Drop Flask-Login's per-app-context user cache.

    The ``app`` fixture keeps ONE app context open for the whole test, and
    Flask reuses an already-pushed app context rather than pushing a new
    one per test request -- so ``flask.g``, where Flask-Login caches the
    loaded user (``g._login_user``), survives from one request to the
    next. Without this, a second test client's request would silently run
    as the FIRST client's user, and every "two co-teachers" test would
    pass vacuously as one teacher acting twice.

    This is a harness artifact of the shared in-memory SQLite fixture, not
    application behaviour: in production each request gets its own app
    context and therefore its own ``g``.
    """
    from flask import g, has_app_context

    if has_app_context():
        g.pop("_login_user", None)


def _login_as(client, email):
    _fresh_identity()
    return login(client, email)


def _assert_authenticated_as(client, user_id):
    """The client really holds its OWN authenticated session cookie.

    ``User.get_id()`` is "<id>.<auth_version>", so the identity is the part
    before the dot; the version suffix is not what this asserts. Without
    this, a "two co-teachers" scenario can silently degrade into one
    Teacher acting twice -- see :func:`_fresh_identity`.
    """
    with client.session_transaction() as session:
        stored = session.get("_user_id")
        assert stored is not None, "the client is not authenticated at all"
        assert stored.split(".")[0] == str(user_id), stored


def _get(client, url):
    _fresh_identity()
    return client.get(url)


def _token(client, w):
    html = _get(client, _feedback_url(w)).get_data(as_text=True)
    match = re.search(r'name="feedback_state" value="([^"]*)"', html)
    return match.group(1) if match else ""


def _save(client, w, text="Nicely structured.", token=None, follow=True, url=None):
    _fresh_identity()
    return client.post(
        url or _feedback_url(w),
        data={"feedback_text": text, "feedback_state": token if token is not None else ""},
        follow_redirects=follow,
    )


def _stored(app):
    with app.app_context():
        row = SubmissionFeedback.query.one_or_none()
        if row is None:
            return None
        return {
            "text": row.feedback_text,
            "version": row.version,
            "reviewer_id": row.reviewer_id,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "public_id": row.public_id,
            "submission_id": row.submission_id,
        }


# ===========================================================================
# Teacher authorization
# ===========================================================================


def test_an_actively_assigned_teacher_can_open_the_editor(app, client):
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    resp = client.get(_feedback_url(w))
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "No feedback yet" in body
    assert 'name="feedback_state"' in body


def test_anonymous_is_redirected_to_login(app, client):
    with app.app_context():
        w = _world()
    resp = client.get(_feedback_url(w), follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]
    assert _save(client, w, token="x", follow=False).status_code == 302
    assert _stored(app) is None


@pytest.mark.parametrize(
    "role", [UserRole.STUDENT.value, UserRole.ADMINISTRATOR.value, UserRole.RESEARCHER.value]
)
def test_non_teacher_roles_get_403_on_both_methods(app, client, role):
    with app.app_context():
        w = _world()
        _user(f"{role}@example.com", role)
    login(client, f"{role}@example.com")
    assert client.get(_feedback_url(w)).status_code == 403
    assert _save(client, w, token="x", follow=False).status_code == 403
    assert _stored(app) is None


@pytest.mark.parametrize("state", ["unassigned", "removed"])
def test_a_teacher_without_an_active_assignment_gets_404(app, client, state):
    with app.app_context():
        w = _world()
        outsider = _user("outsider@example.com", UserRole.TEACHER.value)
        if state == "removed":
            _assign(Group.query.one(), outsider,
                    status=GroupTeacherAssignmentStatus.REMOVED.value)
    login(client, "outsider@example.com")
    assert client.get(_feedback_url(w)).status_code == 404
    assert _save(client, w, token="x", follow=False).status_code == 404
    assert _stored(app) is None


def test_a_suspended_teacher_cannot_hold_a_session_at_all(app, client):
    with app.app_context():
        w = _world()
        _user("suspended@example.com", UserRole.TEACHER.value,
              status=UserStatus.SUSPENDED.value)
        _assign(Group.query.one(), User.query.filter_by(email="suspended@example.com").one())
    login(client, "suspended@example.com")
    resp = client.get(_feedback_url(w), follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]
    assert _stored(app) is None


def test_all_active_co_teachers_have_equal_access(app, client):
    with app.app_context():
        w = _world()
        second = _user("co@example.com", UserRole.TEACHER.value, full_name="Coach Two")
        _assign(Group.query.one(), second)
    for email in ("teacher@example.com", "co@example.com"):
        fresh = app.test_client()
        _login_as(fresh, email)
        assert _get(fresh, _feedback_url(w)).status_code == 200


def test_wrong_nested_identifiers_all_404_without_disclosure(app, client):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        first = _hierarchy(group_name="First")
        second = _hierarchy(group_name="Second")
        _assign(first, teacher)
        _assign(second, teacher)
        mine = _assignment(first, title="Mine")
        other = _assignment(second, title="Other")
        my_student = _enrolled_student(first, "s1@example.com")
        other_student = _enrolled_student(second, "s2@example.com")
        my_sub = _submit(mine, my_student)
        other_sub = _submit(other, other_student)
        mine_w = _World(first, mine, my_sub, teacher, my_student)
        other_w = _World(second, other, other_sub, teacher, other_student)
        crossed = _World(first, mine, other_sub, teacher, my_student)
        wrong_group = _World(second, mine, my_sub, teacher, my_student)

    login(client, "teacher@example.com")
    for bad in (crossed, wrong_group):
        assert client.get(_feedback_url(bad)).status_code == 404
        assert _save(client, bad, token="x", follow=False).status_code == 404
    # A garbage submission id under a correct pair.
    assert client.get(f"{_list_url(mine_w)}/nope/feedback").status_code == 404
    # Positive controls, so the 404s above are not passing vacuously.
    assert client.get(_feedback_url(mine_w)).status_code == 200
    assert client.get(_feedback_url(other_w)).status_code == 200
    assert _stored(app) is None


def test_a_submission_owned_by_a_non_student_is_not_reviewable(app, client):
    """A foreign key into ``users`` proves existence, never role."""
    with app.app_context():
        w = _world()
        Submission.query.one().student_id = _user(
            "admin@example.com", UserRole.ADMINISTRATOR.value
        ).id
        db.session.commit()
    login(client, "teacher@example.com")
    assert client.get(_feedback_url(w)).status_code == 404
    assert _save(client, w, token="x", follow=False).status_code == 404
    assert _stored(app) is None


def test_feedback_requires_csrf(app):
    csrf_app = __import__("app", fromlist=["create_app"]).create_app(
        "testing", WTF_CSRF_ENABLED=True
    )
    with csrf_app.app_context():
        db.create_all()
        w = _world()
        csrf_client = csrf_app.test_client()
        login(csrf_client, "teacher@example.com")
        resp = csrf_client.post(
            _feedback_url(w), data={"feedback_text": "x", "feedback_state": "y"}
        )
        assert resp.status_code == 400
        assert SubmissionFeedback.query.count() == 0
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


# ===========================================================================
# Create, edit, no-op
# ===========================================================================


def test_a_teacher_creates_the_first_feedback(app, client):
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        token = _token(client, w)
        resp = _save(client, w, text="  Clear thesis.  ", token=token)

    assert resp.status_code == 200
    stored = _stored(app)
    assert stored["text"] == "Clear thesis."          # stripped at the edges
    assert stored["version"] == 1
    assert stored["created_at"] == NOW == stored["updated_at"]
    assert stored["submission_id"] == w.sid
    with app.app_context():
        assert stored["reviewer_id"] == w.tid
        assert _UUID.match(stored["public_id"])


def test_a_meaningful_edit_increments_the_version_once_and_moves_only_updated_at(app, client):
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        _save(client, w, text="First take.", token=_token(client, w))
    first = _stored(app)

    with _at(LATER):
        _save(client, w, text="Second take.", token=_token(client, w))
    second = _stored(app)

    assert second["text"] == "Second take."
    assert second["version"] == 2
    assert second["created_at"] == first["created_at"] == NOW
    assert second["updated_at"] == LATER
    # The record's identity is preserved -- a revision is not a new row.
    assert second["public_id"] == first["public_id"]
    assert second["submission_id"] == first["submission_id"]
    with app.app_context():
        assert SubmissionFeedback.query.count() == 1


def test_another_active_co_teacher_can_revise_and_takes_over_attribution(app, client):
    with app.app_context():
        w = _world()
        second = _user("co@example.com", UserRole.TEACHER.value, full_name="Coach Two")
        _assign(Group.query.one(), second)
        second_id = second.id
    first_client, second_client = app.test_client(), app.test_client()
    _login_as(first_client, "teacher@example.com")
    _login_as(second_client, "co@example.com")
    _assert_authenticated_as(first_client, w.tid)
    _assert_authenticated_as(second_client, second_id)

    with _at(NOW):
        _save(first_client, w, text="First teacher's note.", token=_token(first_client, w))
    with _at(LATER):
        _save(second_client, w, text="Second teacher's note.", token=_token(second_client, w))

    stored = _stored(app)
    assert stored["text"] == "Second teacher's note."
    assert stored["reviewer_id"] == second_id
    assert stored["version"] == 2
    assert stored["updated_at"] == LATER
    # Attribution follows the LAST editor, and the page says so. Scoped to
    # the feedback section: the shared portal header also renders the name
    # of whoever is signed in.
    _login_as(first_client, "teacher@example.com")
    section = _get(first_client, _detail_url(w)).get_data(as_text=True).split("<h2>Feedback</h2>")[-1]
    assert "Last changed by Coach Two" in section
    assert "Tariq Teacher" not in section


def test_an_identical_text_save_is_a_no_op(app, client):
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        _save(client, w, text="Exactly this.", token=_token(client, w))
    before = _stored(app)

    with app.app_context():
        other = _user("co@example.com", UserRole.TEACHER.value)
        _assign(Group.query.one(), other)
        other_id = other.id
    co = app.test_client()
    _login_as(co, "co@example.com")
    _assert_authenticated_as(co, other_id)
    with _at(LATER):
        # Whitespace-only differences normalize to the same text.
        resp = _save(co, w, text="   Exactly this.\n", token=_token(co, w))

    assert "unchanged" in resp.get_data(as_text=True).lower()
    after = _stored(app)
    assert after == before, "a no-op must not touch version, timestamps or attribution"


def test_a_no_op_still_requires_a_valid_non_stale_token(app, client):
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        _save(client, w, text="Exactly this.", token=_token(client, w))
    before = _stored(app)

    with _at(LATER):
        resp = _save(client, w, text="Exactly this.", token="not-a-real-token")
    assert "changed by someone else" in resp.get_data(as_text=True)
    assert _stored(app) == before


def test_the_submission_and_assignment_are_never_touched_by_a_review(app, client):
    with app.app_context():
        w = _world()
        before_submission = {
            "answer": Submission.query.one().answer_text,
            "submitted_at": Submission.query.one().submitted_at,
            "public_id": Submission.query.one().public_id,
            "student_id": Submission.query.one().student_id,
        }
        before_assignment = {
            "title": Assignment.query.one().title,
            "instructions": Assignment.query.one().instructions,
            "opens_at": Assignment.query.one().opens_at,
            "due_at": Assignment.query.one().due_at,
            "status": Assignment.query.one().status,
            "published_at": Assignment.query.one().published_at,
        }
    login(client, "teacher@example.com")
    with _at(NOW):
        _save(client, w, text="Reviewed.", token=_token(client, w))
    with _at(LATER):
        _save(client, w, text="Reviewed again.", token=_token(client, w))

    with app.app_context():
        db.session.expire_all()
        submission = Submission.query.one()
        assignment = Assignment.query.one()
        assert submission.answer_text == before_submission["answer"]
        assert submission.submitted_at == before_submission["submitted_at"]
        assert submission.public_id == before_submission["public_id"]
        assert submission.student_id == before_submission["student_id"]
        for field, value in before_assignment.items():
            assert getattr(assignment, field) == value


# ===========================================================================
# Input validation and escaping
# ===========================================================================


@pytest.mark.parametrize("text", ["", "   ", "\n\n\t "])
def test_empty_and_whitespace_only_feedback_is_rejected(app, client, text):
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        resp = _save(client, w, text=text, token=_token(client, w))
    assert resp.status_code == 200
    assert "This field is required." in resp.get_data(as_text=True)
    assert _stored(app) is None


def test_overlong_feedback_is_rejected_on_the_raw_value(app, client):
    """The limit is applied BEFORE stripping, so padding cannot be used to
    slip a longer body past it."""
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        token = _token(client, w)
        too_long = " " * 20 + "x" * FEEDBACK_MAX_LENGTH + " " * 20
        resp = _save(client, w, text=too_long, token=token)
    assert resp.status_code == 200
    assert _stored(app) is None

    with _at(NOW):
        assert _save(client, w, text="x" * FEEDBACK_MAX_LENGTH, token=_token(client, w))
    assert len(_stored(app)["text"]) == FEEDBACK_MAX_LENGTH


def test_internal_whitespace_line_breaks_and_unicode_survive_exactly(app, client):
    text = "  السطر الأول\n\n\tSecond  line   spaced\nثالث  "
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        _save(client, w, text=text, token=_token(client, w))
    assert _stored(app)["text"] == text.strip()


def test_feedback_is_escaped_and_never_rendered_as_markup(app, client):
    payload = "<script>alert('xss')</script>"
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        _save(client, w, text=f"Careful: {payload}", token=_token(client, w))

    student_client = app.test_client()
    _login_as(student_client, "s@example.com")
    student_html = _get(student_client, _student_url(w)).get_data(as_text=True)
    for html in (
        student_html,
        _get(client, _detail_url(w)).get_data(as_text=True),
        _get(client, _feedback_url(w)).get_data(as_text=True),
    ):
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html


def test_line_breaks_are_preserved_by_css_not_injected_markup(app, client):
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        _save(client, w, text="Line one\nLine two", token=_token(client, w))
    html = client.get(_detail_url(w)).get_data(as_text=True)
    assert "white-space: pre-wrap" in html
    assert "Line one\nLine two" in html


def test_an_ordinary_validation_failure_keeps_the_text_and_the_original_token(app, client):
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        _save(client, w, text="Version one.", token=_token(client, w))
        original = _token(client, w)
        resp = _save(client, w, text="", token=original)

    html = resp.get_data(as_text=True)
    assert f'value="{original}"' in html, "the original token must be re-embedded, not refreshed"
    # And the stored row is untouched by the failed attempt.
    assert _stored(app)["version"] == 1


# ===========================================================================
# What the write path requires -- and what it deliberately does not
# ===========================================================================


@pytest.mark.parametrize("student_state", ["withdrawn", "suspended", "both"])
def test_historical_student_withdrawal_or_suspension_does_not_block_review(
    app, client, student_state
):
    """Genuine historical work must still be reviewable."""
    with app.app_context():
        w = _world()
        if student_state in ("withdrawn", "both"):
            Enrollment.query.one().status = EnrollmentStatus.WITHDRAWN.value
        if student_state in ("suspended", "both"):
            db.session.get(User, w.stid).status = UserStatus.SUSPENDED.value
        db.session.commit()
    login(client, "teacher@example.com")
    with _at(NOW):
        _save(client, w, text="Still reviewable.", token=_token(client, w))
    assert _stored(app)["text"] == "Still reviewable."


@pytest.mark.parametrize("state", ["draft", "past_due"])
def test_an_unpublished_or_past_due_assignment_can_still_receive_feedback(app, client, state):
    with app.app_context():
        w = _world()
        if state == "draft":
            assignment = Assignment.query.one()
            assignment.status = AssignmentStatus.DRAFT.value
            assignment.published_at = None
            db.session.commit()
    login(client, "teacher@example.com")
    # NOW is already past DUE_UTC, which covers the past_due case.
    with _at(NOW):
        _save(client, w, text="Reviewed after the fact.", token=_token(client, w))
    assert _stored(app)["text"] == "Reviewed after the fact."


def test_no_schedule_is_required(app, client):
    with app.app_context():
        w = _world()
        from app.models import Schedule
        assert Schedule.query.count() == 0
    login(client, "teacher@example.com")
    with _at(NOW):
        _save(client, w, text="No schedule needed.", token=_token(client, w))
    assert _stored(app) is not None


@pytest.mark.parametrize("archived", ["term", "level", "course", "group"])
def test_an_archived_chain_keeps_reads_but_blocks_writes(app, client, archived):
    with app.app_context():
        w = _world()
        _feedback_row(Submission.query.one(), db.session.get(User, w.tid),
                      text="Written while active.")
        group = Group.query.one()
        row = {"term": group.academic_term, "level": group.course.level,
               "course": group.course, "group": group}[archived]
        row.status = AcademicStatus.ARCHIVED.value
        db.session.commit()

    login(client, "teacher@example.com")
    page = client.get(_feedback_url(w))
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Written while active." in html                  # read-only view
    assert 'name="feedback_state"' not in html              # no form, no token
    assert "cannot be written or changed" in html           # and it says why

    # A forged POST is rejected server-side, not by the template.
    resp = _save(client, w, text="Sneaky.", token="anything")
    assert "can only be written or changed" in resp.get_data(as_text=True)
    stored = _stored(app)
    assert stored["text"] == "Written while active." and stored["version"] == 1


def test_the_detail_page_hides_the_editor_link_under_an_archived_chain(app, client):
    with app.app_context():
        w = _world()
        group = Group.query.one()
        group.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    login(client, "teacher@example.com")
    html = client.get(_detail_url(w)).get_data(as_text=True)
    assert _feedback_url(w) not in html
    assert "Add Feedback" not in html and "Edit Feedback" not in html


# ===========================================================================
# Reviewer role integrity -- fails closed, never "absent"
# ===========================================================================


def _break_reviewer(app, w):
    """Turn the stored feedback's reviewer into a non-Teacher, the way a
    corrupted or manually seeded row would look."""
    with app.app_context():
        row = SubmissionFeedback.query.one()
        row.reviewer_id = _user("ghost@example.com", UserRole.RESEARCHER.value).id
        db.session.commit()


def test_a_role_inconsistent_row_is_never_shown_and_never_reported_as_absent(app, client):
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        _save(client, w, text="PRIVATE FEEDBACK BODY", token=_token(client, w))
    _break_reviewer(app, w)

    for url in (_feedback_url(w), _detail_url(w)):
        html = _get(client, url).get_data(as_text=True)
        assert "PRIVATE FEEDBACK BODY" not in html
        assert "no longer a teacher" in html
        assert "No feedback yet" not in html
        assert "Awaiting feedback" not in html
    # The list badge is its own state too, never "Awaiting feedback".
    # Asserted on the badge markup, not the bare phrase: the page's
    # explanatory banner legitimately quotes "Feedback provided".
    listing = _get(client, _list_url(w)).get_data(as_text=True)
    assert 'badge--danger">Needs admin review' in listing
    assert 'badge--neutral">Awaiting feedback' not in listing
    assert 'badge--success">Feedback provided' not in listing


def test_a_role_inconsistent_row_offers_no_form_and_refuses_a_forged_write(app, client):
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        _save(client, w, text="Original body.", token=_token(client, w))
        valid_token = _token(client, w)
    _break_reviewer(app, w)
    before = _stored(app)

    assert 'name="feedback_state"' not in _get(client, _feedback_url(w)).get_data(as_text=True)
    # Even the token minted while the row was intact cannot overwrite or
    # "repair" it: the write refuses rather than destroying the record.
    with _at(LATER):
        resp = _save(client, w, text="Overwrite attempt.", token=valid_token)
    assert "no longer a teacher" in resp.get_data(as_text=True)
    assert _stored(app) == before


def test_the_student_receipt_fails_closed_on_a_role_inconsistent_row(app, client):
    with app.app_context():
        w = _world()
    teacher_client = app.test_client()
    _login_as(teacher_client, "teacher@example.com")
    with _at(NOW):
        _save(teacher_client, w, text="PRIVATE FEEDBACK BODY", token=_token(teacher_client, w))
    _break_reviewer(app, w)

    _login_as(client, "s@example.com")
    html = _get(client, _student_url(w)).get_data(as_text=True)
    assert "PRIVATE FEEDBACK BODY" not in html
    assert "cannot be shown right now" in html
    assert "No teacher feedback yet" not in html
    # Nothing was deleted or repaired by rendering it.
    assert _stored(app)["text"] == "PRIVATE FEEDBACK BODY"


# ===========================================================================
# Student visibility
# ===========================================================================


def _give_feedback(app, w, text="Your argument is clear.", moment=NOW):
    with app.app_context():
        _feedback_row(Submission.query.filter_by(id=w.sid).one(),
                      db.session.get(User, w.tid), text=text, moment=moment)


def test_a_student_sees_the_feedback_on_their_own_receipt(app, client):
    from app.services.schedule_occurrences import to_app_local

    with app.app_context():
        w = _world()
        tz_name = app.config["APP_TIMEZONE"]
        expected = to_app_local(tz_name, NOW).strftime("%Y-%m-%d %H:%M")
    _give_feedback(app, w)
    login(client, "s@example.com")
    html = client.get(_student_url(w)).get_data(as_text=True)
    assert "Your argument is clear." in html
    assert "Tariq Teacher" in html
    # The timestamp is localized and the timezone label is shown beside it.
    assert expected in html
    assert tz_name in html


def test_a_student_without_feedback_sees_the_neutral_empty_state(app, client):
    with app.app_context():
        w = _world()
    login(client, "s@example.com")
    html = client.get(_student_url(w)).get_data(as_text=True)
    assert "No teacher feedback yet" in html


def test_a_student_with_no_submission_sees_no_feedback_section_at_all(app, client):
    with app.app_context():
        w = _world()
        Submission.query.delete()
        db.session.commit()
    login(client, "s@example.com")
    html = client.get(_student_url(w)).get_data(as_text=True)
    assert "Teacher Feedback" not in html


def test_a_classmates_feedback_never_leaks(app, client):
    with app.app_context():
        w = _world()
        classmate = _enrolled_student(Group.query.one(), "other@example.com",
                                      full_name="Other Student")
        classmate_submission = _submit(Assignment.query.one(), classmate,
                                       answer="Their answer.")
        _feedback_row(classmate_submission, db.session.get(User, w.tid),
                      text="CLASSMATE ONLY FEEDBACK")
    login(client, "s@example.com")
    html = client.get(_student_url(w)).get_data(as_text=True)
    assert "CLASSMATE ONLY FEEDBACK" not in html
    assert "Their answer." not in html
    assert "No teacher feedback yet" in html


def test_feedback_stays_readable_after_the_deadline(app, client):
    with app.app_context():
        w = _world()
    _give_feedback(app, w, text="Read this later.")
    login(client, "s@example.com")
    # SUBMITTED and NOW are both after OPENS; the real clock is far past
    # DUE_UTC in any case, and the receipt is what carries the feedback.
    html = client.get(_student_url(w)).get_data(as_text=True)
    assert "Read this later." in html
    assert "Past due" in html


@pytest.mark.parametrize(
    "change", ["withdrawn", "unpublished", "archived_term", "archived_group"]
)
def test_losing_receipt_access_hides_feedback_without_deleting_it(app, client, change):
    with app.app_context():
        w = _world()
    _give_feedback(app, w, text="Still stored.")
    login(client, "s@example.com")
    assert "Still stored." in client.get(_student_url(w)).get_data(as_text=True)

    with app.app_context():
        if change == "withdrawn":
            Enrollment.query.one().status = EnrollmentStatus.WITHDRAWN.value
        elif change == "unpublished":
            assignment = Assignment.query.one()
            assignment.status = AssignmentStatus.DRAFT.value
            assignment.published_at = None
        elif change == "archived_term":
            Group.query.one().academic_term.status = AcademicStatus.ARCHIVED.value
        else:
            Group.query.one().status = AcademicStatus.ARCHIVED.value
        db.session.commit()

    assert client.get(_student_url(w)).status_code == 404
    assert _stored(app)["text"] == "Still stored."


def test_a_reviewers_later_suspension_or_removal_does_not_hide_valid_history(app, client):
    with app.app_context():
        w = _world()
    _give_feedback(app, w, text="Written when I was here.")
    with app.app_context():
        db.session.get(User, w.tid).status = UserStatus.SUSPENDED.value
        GroupTeacherAssignment.query.one().status = (
            GroupTeacherAssignmentStatus.REMOVED.value
        )
        db.session.commit()
    login(client, "s@example.com")
    html = client.get(_student_url(w)).get_data(as_text=True)
    assert "Written when I was here." in html
    assert "Tariq Teacher" in html


def test_the_student_receipt_offers_no_write_control_of_any_kind(app, client):
    with app.app_context():
        w = _world()
    _give_feedback(app, w)
    login(client, "s@example.com")
    html = client.get(_student_url(w)).get_data(as_text=True)
    forms = re.findall(r'<form[^>]*action="([^"]*)"', html)
    assert all("logout" in action for action in forms), forms
    # Scoped past the shared portal nav, which since Phase 4 / M08 names a
    # "Grades" *surface* of its own. The word list keeps "Grade": the point
    # of this test is that no grading control reaches the receipt, so the
    # word must stay forbidden in the page's own content rather than be
    # dropped because the chrome now legitimately contains it.
    receipt = html.split("<h1", 1)[1]
    for word in ("Reply", "Comment", "Grade", "Score", "Delete", "Resubmit"):
        assert word not in receipt, word


def test_no_student_feedback_endpoint_exists(app):
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith("student."):
            assert "feedback" not in str(rule), rule


def test_the_student_list_and_dashboard_gained_no_feedback(app, client):
    with app.app_context():
        w = _world()
    _give_feedback(app, w, text="ONLY ON THE RECEIPT")
    login(client, "s@example.com")
    for url in ("/student/assignments", "/student/dashboard"):
        html = client.get(url).get_data(as_text=True)
        assert "ONLY ON THE RECEIPT" not in html
        assert "Feedback" not in html


# ===========================================================================
# Signed stale-form protection
# ===========================================================================


def test_two_co_teachers_opening_an_empty_form_resolve_as_first_save_wins(app, client):
    with app.app_context():
        w = _world()
        second = _user("co@example.com", UserRole.TEACHER.value)
        _assign(Group.query.one(), second)
    first, co = app.test_client(), app.test_client()
    _login_as(first, "teacher@example.com")
    _login_as(co, "co@example.com")

    with _at(NOW):
        first_token = _token(first, w)
        co_token = _token(co, w)                 # both opened an EMPTY form
        _save(first, w, text="First wins.", token=first_token)
        resp = _save(co, w, text="Second loses.", token=co_token)

    assert "changed by someone else" in resp.get_data(as_text=True)
    stored = _stored(app)
    assert stored["text"] == "First wins." and stored["version"] == 1
    with app.app_context():
        assert SubmissionFeedback.query.count() == 1, "no second row was created"


def test_two_editors_on_the_same_version_resolve_as_first_save_wins(app, client):
    with app.app_context():
        w = _world()
        second = _user("co@example.com", UserRole.TEACHER.value)
        _assign(Group.query.one(), second)
        _feedback_row(Submission.query.one(), db.session.get(User, w.tid), text="Base.")
    first, co = app.test_client(), app.test_client()
    _login_as(first, "teacher@example.com")
    _login_as(co, "co@example.com")

    with _at(LATER):
        first_token = _token(first, w)
        co_token = _token(co, w)
        _save(first, w, text="Revision A.", token=first_token)
        resp = _save(co, w, text="Revision B.", token=co_token)

    assert "changed by someone else" in resp.get_data(as_text=True)
    stored = _stored(app)
    assert stored["text"] == "Revision A." and stored["version"] == 2


def test_an_a_b_a_round_trip_still_invalidates_the_original_token(app, client):
    """The text is back to what the first form was opened against, but the
    version moved -- which is exactly why the token binds the version and
    not the text."""
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        _save(client, w, text="A", token=_token(client, w))
        stale = _token(client, w)                       # opened at version 1
        _save(client, w, text="B", token=_token(client, w))
    with _at(LATER):
        _save(client, w, text="A", token=_token(client, w))

    assert _stored(app)["version"] == 3
    with _at(LATER):
        resp = _save(client, w, text="C", token=stale)
    assert "changed by someone else" in resp.get_data(as_text=True)
    assert _stored(app)["version"] == 3


def test_two_edits_inside_one_second_are_still_distinguishable(app, client):
    """Whole-second timestamps cannot separate these; ``version`` can."""
    with app.app_context():
        w = _world()
        second = _user("co@example.com", UserRole.TEACHER.value)
        _assign(Group.query.one(), second)
    first, co = app.test_client(), app.test_client()
    _login_as(first, "teacher@example.com")
    _login_as(co, "co@example.com")

    same_second = NOW
    with _at(same_second):
        first_token = _token(first, w)
        co_token = _token(co, w)
        _save(first, w, text="Inside one second.", token=first_token)
        resp = _save(co, w, text="Also inside it.", token=co_token)

    assert "changed by someone else" in resp.get_data(as_text=True)
    stored = _stored(app)
    assert stored["text"] == "Inside one second."
    assert stored["created_at"] == stored["updated_at"] == same_second


def test_replaying_a_successful_save_cannot_update_again(app, client):
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        token = _token(client, w)
        _save(client, w, text="Once.", token=token)
    first = _stored(app)

    with _at(LATER):
        resp = _save(client, w, text="Once.", token=token)       # exact replay
    assert "changed by someone else" in resp.get_data(as_text=True)
    assert _stored(app) == first


@pytest.mark.parametrize(
    "token",
    ["", "not-a-token", "eyJhIjoxfQ.bogus.signature"],
)
def test_missing_malformed_and_invalidly_signed_tokens_are_rejected(app, client, token):
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        resp = _save(client, w, text="Should not land.", token=token)
    assert "changed by someone else" in resp.get_data(as_text=True)
    assert _stored(app) is None


def _mint(app, payload):
    """A correctly SIGNED token carrying an arbitrary payload -- the only
    way to test shape validation independently of signature validation."""
    with app.test_request_context():
        return feedback_mod._feedback_state_serializer().dumps(payload)


def test_a_wrong_shaped_but_correctly_signed_token_is_rejected(app, client):
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")

    base = {
        "teacher_public_id": "t", "group_public_id": w.gpid,
        "assignment_public_id": w.apid, "submission_public_id": w.spid,
        "feedback_public_id": None, "version": None,
    }
    bad_payloads = [
        "a bare string",
        ["a", "list"],
        {k: v for k, v in base.items() if k != "version"},          # missing field
        dict(base, extra="field"),                                  # extra field
        dict(base, group_public_id=None),                           # wrong type
        dict(base, feedback_public_id="x", version=None),           # half-absence
        dict(base, feedback_public_id=None, version=1),             # half-absence
        dict(base, feedback_public_id="x", version=True),           # bool is not a version
        dict(base, feedback_public_id="x", version=0),              # non-positive
    ]
    for payload in bad_payloads:
        with _at(NOW):
            resp = _save(client, w, text="Should not land.", token=_mint(app, payload))
        assert "changed by someone else" in resp.get_data(as_text=True), payload
        assert _stored(app) is None, payload


def test_a_token_minted_for_another_teacher_group_assignment_or_submission_is_rejected(
    app, client
):
    with app.app_context():
        w = _world()
        second_group = _hierarchy(group_name="Second")
        teacher = db.session.get(User, w.tid)
        _assign(second_group, teacher)
        other_assignment = _assignment(second_group, title="Other")
        other_student = _enrolled_student(second_group, "s2@example.com")
        other_submission = _submit(other_assignment, other_student)
        other = _World(second_group, other_assignment, other_submission, teacher, other_student)
        teacher_public_id = teacher.public_id

    login(client, "teacher@example.com")
    with _at(NOW):
        foreign_token = _token(client, other)      # valid, but for the other chain

    for token in (
        foreign_token,
        _mint(app, {
            "teacher_public_id": "somebody-else", "group_public_id": w.gpid,
            "assignment_public_id": w.apid, "submission_public_id": w.spid,
            "feedback_public_id": None, "version": None,
        }),
        _mint(app, {
            "teacher_public_id": teacher_public_id, "group_public_id": w.gpid,
            "assignment_public_id": w.apid, "submission_public_id": other.spid,
            "feedback_public_id": None, "version": None,
        }),
    ):
        with _at(NOW):
            resp = _save(client, w, text="Cross-object.", token=token)
        assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert SubmissionFeedback.query.count() == 0


def test_a_stale_rejection_discards_the_attempted_text_and_mints_no_paired_token(app, client):
    with app.app_context():
        w = _world()
        _feedback_row(Submission.query.one(), db.session.get(User, w.tid), text="Current text.")
    login(client, "teacher@example.com")
    with _at(NOW):
        resp = _save(client, w, text="ATTEMPTED TEXT", token="stale")

    html = resp.get_data(as_text=True)
    assert "ATTEMPTED TEXT" not in html, "a stale rejection must discard what was typed"
    assert "Current text." in html, "and must show the current persisted feedback"
    assert _stored(app)["version"] == 1


def test_the_stale_rejection_is_a_post_redirect_get(app, client):
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        resp = _save(client, w, text="x", token="stale", follow=False)
    assert resp.status_code == 302
    assert urlsplit(resp.headers["Location"]).path == _feedback_url(w)


def test_the_token_carries_only_public_identifiers(app, client):
    with app.app_context():
        w = _world()
        _feedback_row(Submission.query.one(), db.session.get(User, w.tid))
    login(client, "teacher@example.com")
    token = _token(client, w)
    with app.test_request_context():
        payload = feedback_mod._feedback_state_serializer().loads(token)

    assert set(payload) == set(feedback_mod._FEEDBACK_STATE_FIELDS)
    forbidden = {str(i) for i in (w.gid, w.aid, w.sid, w.tid, w.stid)}
    for key, value in payload.items():
        if key == "version":
            continue
        assert _UUID.match(value), (key, value)
        assert value not in forbidden
    assert payload["version"] == 1
    # And no feedback body travels inside a readable token.
    assert "Good work." not in str(payload)


# ===========================================================================
# Post-lock rechecks: state that changes between preview and lock
# ===========================================================================


def _inject_before_lock(fn):
    """Run `fn` at the moment the Group lock is taken -- i.e. after the
    pre-lock preview has already succeeded."""
    original = feedback_mod.lock_group_in_open_transaction

    def spy(public_id):
        fn()
        return original(public_id)

    return patch.object(feedback_mod, "lock_group_in_open_transaction", side_effect=spy)


def test_a_teaching_assignment_removed_between_preview_and_lock_is_caught(app, client):
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        token = _token(client, w)

    def remove():
        GroupTeacherAssignment.query.filter_by(group_id=w.gid, teacher_id=w.tid).one().status = (
            GroupTeacherAssignmentStatus.REMOVED.value
        )
        db.session.commit()

    with _at(NOW), _inject_before_lock(remove):
        resp = _save(client, w, text="Too late.", token=token, follow=False)
    assert resp.status_code == 404
    assert _stored(app) is None


def test_a_teacher_account_suspended_between_preview_and_lock_is_caught(app, client):
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        token = _token(client, w)

    def suspend():
        db.session.get(User, w.tid).status = UserStatus.SUSPENDED.value
        db.session.commit()

    with _at(NOW), _inject_before_lock(suspend):
        resp = _save(client, w, text="Too late.", token=token, follow=False)
    assert resp.status_code == 404
    assert _stored(app) is None


def test_an_archival_between_preview_and_lock_is_caught(app, client):
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        token = _token(client, w)

    def archive():
        db.session.get(Group, w.gid).status = AcademicStatus.ARCHIVED.value
        db.session.commit()

    with _at(NOW), _inject_before_lock(archive):
        resp = _save(client, w, text="Too late.", token=token)
    assert "can only be written or changed" in resp.get_data(as_text=True)
    assert _stored(app) is None


def test_a_submission_owner_losing_the_student_role_between_preview_and_lock_is_caught(
    app, client
):
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        token = _token(client, w)

    def demote():
        db.session.get(User, w.stid).role = UserRole.RESEARCHER.value
        db.session.commit()

    with _at(NOW), _inject_before_lock(demote):
        resp = _save(client, w, text="Too late.", token=token, follow=False)
    assert resp.status_code == 404
    assert _stored(app) is None


def test_a_competing_first_feedback_committed_between_preview_and_lock_is_caught(app, client):
    """The token claimed absence; by the time the locks were held a row
    existed. No overwrite, no second row."""
    with app.app_context():
        w = _world()
        other = _user("co@example.com", UserRole.TEACHER.value)
        _assign(Group.query.one(), other)
        other_id = other.id
    login(client, "teacher@example.com")
    with _at(NOW):
        token = _token(client, w)

    def race():
        db.session.add(SubmissionFeedback(
            submission_id=w.sid, reviewer_id=other_id, feedback_text="They got there first.",
            version=1, created_at=NOW, updated_at=NOW,
        ))
        db.session.commit()

    with _at(LATER), _inject_before_lock(race):
        resp = _save(client, w, text="Mine.", token=token)

    assert "changed by someone else" in resp.get_data(as_text=True)
    stored = _stored(app)
    assert stored["text"] == "They got there first."
    assert stored["reviewer_id"] == other_id and stored["version"] == 1
    with app.app_context():
        assert SubmissionFeedback.query.count() == 1


def test_an_integrity_error_is_reported_generically_and_writes_nothing(app, client):
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        token = _token(client, w)

    from sqlalchemy.exc import IntegrityError

    def always_fail():
        db.session.rollback()
        raise IntegrityError("INSERT INTO submission_feedback ...", {"x": 1}, Exception("boom"))

    with _at(NOW), patch.object(db.session, "commit", side_effect=always_fail):
        resp = _save(client, w, text="Never lands.", token=token)

    html = resp.get_data(as_text=True)
    assert "could not be saved" in html
    for leak in ("INSERT INTO", "IntegrityError", "submission_feedback ..."):
        assert leak not in html, leak
    assert _stored(app) is None


def test_integrity_recovery_re_authorizes_and_404s_when_access_ended(app, client):
    """A rolled-back read is not authorization evidence: the same
    concurrent change that caused the conflict may have ended access."""
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        token = _token(client, w)

    from sqlalchemy.exc import IntegrityError

    # Captured BEFORE the patch, so applying the concurrent change inside
    # the side effect does not re-enter the patched commit.
    real_commit = db.session.commit

    def fail_and_revoke():
        db.session.rollback()
        GroupTeacherAssignment.query.filter_by(group_id=w.gid, teacher_id=w.tid).one().status = (
            GroupTeacherAssignmentStatus.REMOVED.value
        )
        real_commit()
        raise IntegrityError("stmt", {}, Exception("boom"))

    with _at(NOW), patch.object(db.session, "commit", side_effect=fail_and_revoke):
        resp = _save(client, w, text="Never lands.", token=token, follow=False)

    assert resp.status_code == 404
    assert _stored(app) is None


@pytest.mark.parametrize(
    "case", ["anonymous", "wrong_role", "unassigned", "stale", "archived", "invalid_text"]
)
def test_no_rejected_path_ever_mutates(app, client, case):
    with app.app_context():
        w = _world()
        _feedback_row(Submission.query.one(), db.session.get(User, w.tid),
                      text="Untouched.", version=3)
        if case == "wrong_role":
            _user("other@example.com", UserRole.ADMINISTRATOR.value)
        if case == "unassigned":
            _user("other@example.com", UserRole.TEACHER.value)
        if case == "archived":
            Group.query.one().status = AcademicStatus.ARCHIVED.value
            db.session.commit()
    before = _stored(app)

    if case == "anonymous":
        _save(client, w, text="x", token="t")
    elif case in ("wrong_role", "unassigned"):
        login(client, "other@example.com")
        _save(client, w, text="x", token="t")
    else:
        login(client, "teacher@example.com")
        if case == "stale":
            _save(client, w, text="x", token="stale")
        elif case == "archived":
            _save(client, w, text="x", token="t")
        else:
            with _at(LATER):
                _save(client, w, text="   ", token=_token(client, w))

    assert _stored(app) == before


# ===========================================================================
# Structural: single reset + the route-specific lock order
# ===========================================================================


def _capture_locks(fn):
    from sqlalchemy.orm import Query

    events = []
    original_rollback = db.session.rollback
    original_wfu = Query.with_for_update

    def rollback_spy(*a, **k):
        events.append("reset")
        return original_rollback(*a, **k)

    def wfu_spy(self, *a, **k):
        entity = self.column_descriptions[0]["entity"] if self.column_descriptions else None
        events.append(f"lock:{getattr(entity, '__name__', '?')}")
        return original_wfu(self, *a, **k)

    with patch.object(db.session, "rollback", side_effect=rollback_spy), patch.object(
        Query, "with_for_update", wfu_spy
    ):
        fn()
    return events


#: AcademicTerm -> Level -> Course -> Group -> the involved User rows in
#: ascending internal id (acting Teacher and Submission owner) -> the
#: acting Teacher's GroupTeacherAssignment -> Assignment -> Submission ->
#: the existing SubmissionFeedback, if any.
_EXPECTED_LOCKS = [
    "lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group",
    "lock:User", "lock:User", "lock:GroupTeacherAssignment",
    "lock:Assignment", "lock:Submission", "lock:SubmissionFeedback",
]


def _assert_one_reset_then_canonical_locks(events):
    """Exactly one deliberate reset before the first lock, the canonical
    order, and no reset in the middle of it.

    A *rejection* path rolls back once more after the locks -- that is the
    lock **release**, not a second lock-taking reset -- so a trailing
    reset is allowed and nothing may be locked after it.
    """
    locks = [e for e in events if e.startswith("lock:")]
    assert locks == _EXPECTED_LOCKS, events
    first = next(i for i, e in enumerate(events) if e.startswith("lock:"))
    last = max(i for i, e in enumerate(events) if e.startswith("lock:"))
    assert events[:first] == ["reset"], events
    assert "reset" not in events[first:last], events


def test_a_successful_create_uses_one_reset_and_the_canonical_lock_order(app, client):
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        token = _token(client, w)
        events = _capture_locks(lambda: _save(client, w, text="Saved.", token=token))

    # A successful create commits, so there is no trailing lock release
    # either: exactly one reset in the whole request.
    assert events.count("reset") == 1
    _assert_one_reset_then_canonical_locks(events)
    assert _stored(app)["version"] == 1


def test_an_edit_and_a_no_op_request_the_same_lock_order(app, client):
    with app.app_context():
        w = _world()
        _feedback_row(Submission.query.one(), db.session.get(User, w.tid), text="Base.")
    login(client, "teacher@example.com")

    with _at(LATER):
        token = _token(client, w)
        edit_events = _capture_locks(lambda: _save(client, w, text="Revised.", token=token))
        noop_token = _token(client, w)
        noop_events = _capture_locks(
            lambda: _save(client, w, text="Revised.", token=noop_token)
        )

    for events in (edit_events, noop_events):
        _assert_one_reset_then_canonical_locks(events)
    # The successful edit commits; the no-op releases its locks instead.
    assert edit_events.count("reset") == 1
    assert noop_events[-1] == "reset"


def test_the_involved_user_rows_are_locked_in_ascending_internal_id_order(app, client):
    """Structural. SQLite honours none of this -- it is the *requested*
    order that keeps this route deadlock-compatible with the Administrator
    membership and account write paths, which lock User rows by the same
    ascending-id rule.

    Asserted against the lock helper itself, which is where the ordering
    decision lives, and with the ids deliberately passed in the WRONG
    order so a helper that simply preserved its arguments would fail.
    """
    with app.app_context():
        w = _world()
        assert w.tid != w.stid
        higher, lower = max(w.tid, w.stid), min(w.tid, w.stid)
        _, _, users, _, _, _, _ = feedback_mod._lock_feedback_chain(
            w.gpid, None, None, None, higher, lower, w.aid, w.sid
        )
        assert list(users) == [lower, higher]
        assert set(users) == {w.tid, w.stid}
        db.session.rollback()


def test_no_second_reset_happens_on_a_rejected_path(app, client):
    """The single deliberate reset belongs to the lock chain; a rejection
    then rolls back exactly once more to release it, and never re-locks."""
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        events = _capture_locks(lambda: _save(client, w, text="x", token="stale"))
    # Locks were taken, the token was checked against them, and nothing
    # was locked a second time afterwards.
    _assert_one_reset_then_canonical_locks(events)
    assert _stored(app) is None


# ===========================================================================
# Query bounds, list integration and page hygiene
# ===========================================================================


def test_the_list_indicator_costs_one_query_for_a_whole_page(app, client):
    from sqlalchemy import event
    from app.services.assignment_queries import PAGE_SIZE

    with app.app_context():
        w = _world()
        assignment = Assignment.query.one()
        teacher = db.session.get(User, w.tid)
        for index in range(PAGE_SIZE - 1):
            student = _enrolled_student(Group.query.one(), f"s{index}@example.com")
            submission = _submit(assignment, student,
                                 submitted_at=SUBMITTED + timedelta(minutes=index))
            if index % 2 == 0:
                _feedback_row(submission, teacher, text=f"Note {index}")

    login(client, "teacher@example.com")
    statements = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", record)
        try:
            html = client.get(_list_url(w)).get_data(as_text=True)
        finally:
            event.remove(db.engine, "before_cursor_execute", record)

    feedback_reads = [s for s in statements if "submission_feedback" in s]
    assert len(feedback_reads) == 1, feedback_reads
    assert 'badge--success">Feedback provided' in html
    assert 'badge--neutral">Awaiting feedback' in html


def test_the_list_never_fetches_feedback_or_answer_bodies_or_user_secrets(app, client):
    from sqlalchemy import event

    with app.app_context():
        w = _world()
        _feedback_row(Submission.query.one(), db.session.get(User, w.tid),
                      text="THE FEEDBACK BODY")

    login(client, "teacher@example.com")
    statements = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", record)
        try:
            html = client.get(_list_url(w)).get_data(as_text=True)
        finally:
            event.remove(db.engine, "before_cursor_execute", record)

    assert "THE FEEDBACK BODY" not in html
    assert not any("feedback_text" in s for s in statements), statements
    assert not any("answer_text" in s for s in statements), statements
    feedback_read = next(s for s in statements if "submission_feedback" in s)
    select_list = feedback_read.split("FROM")[0]
    for forbidden in ("password_hash", "auth_version", "email", "feedback_text"):
        assert forbidden not in select_list, forbidden


def test_the_list_pagination_and_ordering_are_unchanged(app, client):
    from app.services.assignment_queries import PAGE_SIZE

    with app.app_context():
        w = _world()
        assignment = Assignment.query.one()
        teacher = db.session.get(User, w.tid)
        for index in range(PAGE_SIZE + 3):
            student = _enrolled_student(Group.query.one(), f"s{index}@example.com",
                                        full_name=f"Student {index:02d}")
            submission = _submit(assignment, student,
                                 submitted_at=SUBMITTED + timedelta(minutes=index + 1))
            _feedback_row(submission, teacher, text=f"Note {index}")

    login(client, "teacher@example.com")
    page_one = client.get(_list_url(w)).get_data(as_text=True)
    names = re.findall(r"Student (\d\d)", page_one)
    assert names == sorted(names, reverse=True), "submitted_at DESC is unchanged"
    assert len(set(names)) == PAGE_SIZE
    assert "Next" in page_one
    page_two = client.get(f"{_list_url(w)}?page=2").get_data(as_text=True)
    assert "Previous" in page_two
    assert f"up to {PAGE_SIZE} per page" in page_one
    # No total counter or review metric was introduced.
    assert "of 23" not in page_one and "23 submissions" not in page_one


def test_the_editor_does_not_lazy_load_or_run_an_unbounded_query(app, client):
    from sqlalchemy import event

    with app.app_context():
        w = _world()
        _feedback_row(Submission.query.one(), db.session.get(User, w.tid))

    login(client, "teacher@example.com")
    statements = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", record)
        try:
            assert client.get(_feedback_url(w)).status_code == 200
        finally:
            event.remove(db.engine, "before_cursor_execute", record)

    feedback_reads = [s for s in statements if "submission_feedback" in s]
    assert len(feedback_reads) == 1, feedback_reads
    assert "LIMIT" in feedback_reads[0]


def test_the_student_receipt_costs_one_bounded_feedback_query(app, client):
    from sqlalchemy import event

    with app.app_context():
        w = _world()
    _give_feedback(app, w)
    login(client, "s@example.com")

    statements = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", record)
        try:
            assert client.get(_student_url(w)).status_code == 200
        finally:
            event.remove(db.engine, "before_cursor_execute", record)

    feedback_reads = [s for s in statements if "submission_feedback" in s]
    assert len(feedback_reads) == 1, feedback_reads
    assert "LIMIT" in feedback_reads[0]
    select_list = feedback_reads[0].split("FROM")[0]
    for forbidden in ("password_hash", "auth_version", "email"):
        assert forbidden not in select_list, forbidden


@pytest.mark.parametrize("page", ["feedback", "detail", "list"])
def test_every_feedback_bearing_teacher_page_is_private_no_store(app, client, page):
    with app.app_context():
        w = _world()
        _feedback_row(Submission.query.one(), db.session.get(User, w.tid))
    login(client, "teacher@example.com")
    url = {"feedback": _feedback_url(w), "detail": _detail_url(w), "list": _list_url(w)}[page]
    resp = client.get(url)
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in resp.headers["Vary"]


def test_a_form_error_response_is_also_private_no_store(app, client):
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    with _at(NOW):
        resp = _save(client, w, text="", token=_token(client, w), follow=False)
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in resp.headers["Vary"]


def test_the_student_receipt_with_feedback_is_private_no_store(app, client):
    with app.app_context():
        w = _world()
    _give_feedback(app, w)
    login(client, "s@example.com")
    resp = client.get(_student_url(w))
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in resp.headers["Vary"]


def test_no_internal_identifier_reaches_any_feedback_page(app, client):
    """Asserted positively -- every identifier segment of every rendered
    URL is parsed and required to BE a UUID -- rather than by hunting for
    a numeric substring, which a legitimate UUID prefix can match."""
    with app.app_context():
        w = _world()
        row = _feedback_row(Submission.query.one(), db.session.get(User, w.tid))
        feedback_id, feedback_public_id = row.id, row.public_id
    forbidden = {str(i) for i in (w.gid, w.aid, w.sid, w.tid, w.stid, feedback_id)}

    login(client, "teacher@example.com")
    checked = 0
    for url in (_feedback_url(w), _detail_url(w), _list_url(w)):
        html = client.get(url).get_data(as_text=True)
        for href in re.findall(r'href="([^"]*)"', html):
            segments = [seg for seg in urlsplit(href).path.split("/") if seg]
            for position, segment in enumerate(segments):
                assert not segment.isdigit(), href
                if segment in ("groups", "assignments", "submissions") and position < len(segments) - 1:
                    identifier = segments[position + 1]
                    assert _UUID.match(identifier), (href, identifier)
                    assert identifier not in forbidden
                    checked += 1
        for value in re.findall(r'value="([^"]*)"', html):
            assert value not in forbidden
    assert checked, "the pages rendered no identifier-bearing link to validate"
    # The feedback row's own public id never needs to appear in a URL.
    assert _UUID.match(feedback_public_id)
    assert feedback_public_id != str(feedback_id)


def test_the_editor_states_the_replace_immediate_and_no_history_rules(app, client):
    with app.app_context():
        w = _world()
    login(client, "teacher@example.com")
    html = _get(client, _feedback_url(w)).get_data(as_text=True)
    assert "Saving replaces the current feedback." in html
    assert "earlier versions are not stored anywhere and cannot be recovered." in html
    assert "visible immediately to the student" in html
    assert "<strong>not</strong> a grade" in html


def test_no_grade_publish_delete_or_resubmit_control_exists_anywhere(app, client):
    with app.app_context():
        w = _world()
        _feedback_row(Submission.query.one(), db.session.get(User, w.tid))
    login(client, "teacher@example.com")
    for url in (_feedback_url(w), _detail_url(w), _list_url(w)):
        html = client.get(url).get_data(as_text=True)
        for word in ("Score", "Rubric", "Publish Feedback", "Delete Feedback",
                     "Resubmit", "Mark as complete", "Pass", "Fail"):
            assert word not in html, (url, word)


def test_the_editor_route_is_the_only_feedback_writer(app):
    """Scoped to the **Assignment** surface (Phase 4 / M06).

    M06 added a Speaking recording aggregate with its own single feedback
    writer; that is a different object with its own table, route and
    token, so it is asserted separately below rather than being allowed to
    widen this contract. Across the whole application there are exactly
    two feedback writers, both POST-only, and neither accepts PUT, PATCH
    or DELETE -- feedback is never deleted anywhere.
    """
    writers = [
        r for r in app.url_map.iter_rules()
        if "feedback" in str(r)
        and "/assignments/" in str(r)
        and r.methods & {"POST", "PUT", "PATCH", "DELETE"}
    ]
    assert [r.endpoint for r in writers] == ["teacher.submission_feedback"]
    assert writers[0].methods & {"PUT", "PATCH", "DELETE"} == set()


def test_every_feedback_writer_in_the_application_is_post_only(app):
    writers = sorted(
        (r.endpoint, frozenset(r.methods & {"POST", "PUT", "PATCH", "DELETE"}))
        for r in app.url_map.iter_rules()
        if "feedback" in str(r) and r.methods & {"POST", "PUT", "PATCH", "DELETE"}
    )
    assert writers == [
        ("teacher.speaking_submission_feedback", frozenset({"POST"})),
        ("teacher.submission_feedback", frozenset({"POST"})),
    ]
    # No Student surface can write feedback at all.
    assert not any(
        "/student/" in str(r)
        for r in app.url_map.iter_rules()
        if "feedback" in str(r)
    )


# ===========================================================================
# Fresh authorization after a rollback
# ===========================================================================
#
# `roles_required` runs once, at the start of the request. Every path that
# rolls back and then renders private content, mints a token, or picks a
# recovery response has therefore RELEASED its locks and can no longer
# treat that decorator -- or a cached `current_user` -- as current
# evidence: the acting Teacher's account or assignment may have changed in
# exactly that window. These tests inject the change at the precise
# transaction boundary rather than waiting for one.


def _at_rollback(index, action):
    """Run `action` immediately after the `index`-th
    ``db.session.rollback()`` of the request -- an exact transaction
    boundary, with no sleeps and no race."""
    real_rollback = db.session.rollback
    state = {"n": 0}

    def spy(*a, **k):
        state["n"] += 1
        result = real_rollback(*a, **k)
        if state["n"] == index:
            action()
        return result

    return patch.object(db.session, "rollback", side_effect=spy)


def _fail_commit_then(action):
    """Make the write raise ``IntegrityError``, applying `action` first so
    the recovery path starts from the changed state."""
    from sqlalchemy.exc import IntegrityError

    real_rollback = db.session.rollback
    real_commit = db.session.commit

    def side_effect():
        real_rollback()
        action()
        real_commit()
        raise IntegrityError("stmt", {}, Exception("boom"))

    return patch.object(db.session, "commit", side_effect=side_effect)


def _suspend_actor(w):
    def action():
        db.session.get(User, w.tid).status = UserStatus.SUSPENDED.value
    return action


def _demote_actor(w):
    def action():
        db.session.get(User, w.tid).role = UserRole.RESEARCHER.value
    return action


def _unassign_actor(w):
    def action():
        GroupTeacherAssignment.query.filter_by(
            group_id=w.gid, teacher_id=w.tid
        ).one().status = GroupTeacherAssignmentStatus.REMOVED.value
    return action


def _commit_after(action):
    """`action`, followed by a real commit -- for injection points that are
    not already inside a patched commit."""
    real_commit = db.session.commit

    def wrapped():
        action()
        real_commit()

    return wrapped


def _assert_no_private_content(resp, *, answer, feedback_text):
    """A rejected response leaks nothing and claims nothing."""
    html = resp.get_data(as_text=True)
    assert answer not in html
    assert feedback_text not in html
    assert 'name="feedback_state"' not in html, "a rejected path must not mint a token"
    for claim in ("Feedback saved", "Feedback updated", "already", "unchanged, so nothing"):
        assert claim not in html, claim


PRIVATE_ANSWER = "PRIVATE STUDENT ANSWER"
PRIVATE_FEEDBACK = "PRIVATE FEEDBACK BODY"


def _reviewed_world(app):
    """A world whose Submission already carries private feedback."""
    w = _world()
    Submission.query.filter_by(id=w.sid).one().answer_text = PRIVATE_ANSWER
    db.session.commit()
    _feedback_row(
        Submission.query.filter_by(id=w.sid).one(),
        db.session.get(User, w.tid),
        text=PRIVATE_FEEDBACK,
    )
    return w


@pytest.mark.parametrize(
    "loss", ["suspended", "demoted", "unassigned"],
)
def test_actor_authorization_lost_at_the_validation_render_rollback(app, client, loss):
    """The ordinary-validation re-render rolls back, releasing its locks,
    and only then reads the page it is about to show. If the acting
    Teacher stopped being authorized in exactly that window, the response
    must not carry the student's answer, the stored feedback, or a fresh
    edit token."""
    with app.app_context():
        w = _reviewed_world(app)
    _login_as(client, "teacher@example.com")
    with _at(NOW):
        token = _token(client, w)

    action = {"suspended": _suspend_actor, "demoted": _demote_actor,
              "unassigned": _unassign_actor}[loss](w)
    # Rollback #1 belongs to the lock chain; #2 is the render's own.
    with _at(NOW), _at_rollback(2, _commit_after(action)):
        resp = _save(client, w, text="   ", token=token, follow=False)

    assert resp.status_code == 404
    _assert_no_private_content(resp, answer=PRIVATE_ANSWER, feedback_text=PRIVATE_FEEDBACK)
    stored = _stored(app)
    assert stored["text"] == PRIVATE_FEEDBACK and stored["version"] == 1


@pytest.mark.parametrize(
    "loss", ["suspended", "demoted", "unassigned"],
)
def test_actor_authorization_lost_before_integrity_recovery(app, client, loss):
    """The IntegrityError path rolls back and then re-reads. Whatever
    caused the conflict may also have ended this Teacher's access, so the
    recovery must re-prove the ACTOR, not just the object."""
    with app.app_context():
        w = _reviewed_world(app)
    _login_as(client, "teacher@example.com")
    with _at(NOW):
        token = _token(client, w)

    action = {"suspended": _suspend_actor, "demoted": _demote_actor,
              "unassigned": _unassign_actor}[loss](w)
    with _at(NOW), _fail_commit_then(action):
        resp = _save(client, w, text="A revision.", token=token, follow=False)

    assert resp.status_code == 404
    _assert_no_private_content(resp, answer=PRIVATE_ANSWER, feedback_text=PRIVATE_FEEDBACK)
    stored = _stored(app)
    assert stored["text"] == PRIVATE_FEEDBACK
    assert stored["version"] == 1
    assert stored["updated_at"] == NOW


def test_a_still_authorized_validation_render_keeps_working(app, client):
    """The positive control: with authorization intact, the re-render
    still shows the attempted text and the ORIGINAL token."""
    with app.app_context():
        w = _reviewed_world(app)
    _login_as(client, "teacher@example.com")
    with _at(NOW):
        original = _token(client, w)
        resp = _save(client, w, text="   ", token=original, follow=False)

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert f'value="{original}"' in html
    assert PRIVATE_ANSWER in html
    assert _stored(app)["version"] == 1


def test_a_still_authorized_integrity_recovery_keeps_working(app, client):
    """The positive control for recovery: a generic failure message, no
    mutation, and no 404."""
    with app.app_context():
        w = _reviewed_world(app)
    _login_as(client, "teacher@example.com")
    with _at(NOW):
        token = _token(client, w)

    with _at(NOW), _fail_commit_then(lambda: None):
        resp = _save(client, w, text="A revision.", token=token)

    assert resp.status_code == 200
    assert "could not be saved" in resp.get_data(as_text=True)
    stored = _stored(app)
    assert stored["text"] == PRIVATE_FEEDBACK and stored["version"] == 1


def test_a_suspended_actor_is_refused_even_on_a_plain_get(app, client):
    """The same fresh check guards the editor GET, which is the other path
    that rolls back before rendering private content."""
    with app.app_context():
        w = _reviewed_world(app)
    _login_as(client, "teacher@example.com")
    assert PRIVATE_FEEDBACK in _get(client, _feedback_url(w)).get_data(as_text=True)

    with app.app_context():
        db.session.get(User, w.tid).status = UserStatus.SUSPENDED.value
        db.session.commit()

    # Rollback #1 is the render's own on a GET.
    with _at(NOW):
        resp = _get(client, _feedback_url(w))
    assert resp.status_code in (302, 404)
    _assert_no_private_content(resp, answer=PRIVATE_ANSWER, feedback_text=PRIVATE_FEEDBACK)


def test_fresh_authorization_does_not_deny_archived_historical_reads(app, client):
    """Regression guard for the fix itself: the fresh ACTOR check must not
    turn an archived chain into a read denial."""
    with app.app_context():
        w = _reviewed_world(app)
        Group.query.filter_by(id=w.gid).one().academic_term.status = (
            AcademicStatus.ARCHIVED.value
        )
        db.session.commit()
    _login_as(client, "teacher@example.com")
    html = _get(client, _feedback_url(w)).get_data(as_text=True)
    assert PRIVATE_FEEDBACK in html
    assert PRIVATE_ANSWER in html
    assert 'name="feedback_state"' not in html      # read-only, but readable


def test_fresh_authorization_does_not_hide_a_suspended_or_removed_reviewers_work(
    app, client
):
    """The check concerns the ACTING Teacher. A historical reviewer who has
    since been suspended and removed must not make valid feedback vanish
    for a co-teacher who is still authorized."""
    with app.app_context():
        w = _reviewed_world(app)
        co = _user("co@example.com", UserRole.TEACHER.value, full_name="Coach Two")
        _assign(Group.query.filter_by(id=w.gid).one(), co)
        # The original reviewer is suspended AND removed from the Group.
        db.session.get(User, w.tid).status = UserStatus.SUSPENDED.value
        GroupTeacherAssignment.query.filter_by(
            group_id=w.gid, teacher_id=w.tid
        ).one().status = GroupTeacherAssignmentStatus.REMOVED.value
        db.session.commit()

    co_client = app.test_client()
    _login_as(co_client, "co@example.com")
    html = _get(co_client, _feedback_url(w)).get_data(as_text=True)
    assert PRIVATE_FEEDBACK in html
    assert "Tariq Teacher" in html
    assert 'name="feedback_state"' in html, "the still-authorized co-teacher may still edit"


@pytest.mark.parametrize("hidden", ["withdrawn", "suspended", "unpublished"])
def test_a_save_on_historical_work_does_not_promise_current_visibility(app, client, hidden):
    """Writing is deliberately allowed for a withdrawn or suspended
    Student and for an unpublished Assignment -- none of whom can open the
    receipt right now -- so the success message must not claim the student
    can see it."""
    with app.app_context():
        w = _world()
        if hidden == "withdrawn":
            Enrollment.query.one().status = EnrollmentStatus.WITHDRAWN.value
        elif hidden == "suspended":
            db.session.get(User, w.stid).status = UserStatus.SUSPENDED.value
        else:
            assignment = Assignment.query.one()
            assignment.status = AssignmentStatus.DRAFT.value
            assignment.published_at = None
        db.session.commit()

    _login_as(client, "teacher@example.com")
    with _at(NOW):
        created = _save(client, w, text="Reviewed historical work.", token=_token(client, w))
    first = created.get_data(as_text=True)
    assert "Feedback saved." in first
    assert "can see it now" not in first
    assert "whenever they can" in first

    with _at(LATER):
        updated = _save(client, w, text="Revised.", token=_token(client, w))
    second = updated.get_data(as_text=True)
    assert "Feedback updated." in second
    assert "sees only this latest version" not in second
    assert "latest version" in second and "whenever they can" in second

    # And the Student really cannot reach it, which is what makes the
    # unconditional wording wrong in the first place.
    student_client = app.test_client()
    _login_as(student_client, "s@example.com")
    resp = _get(student_client, _student_url(w))
    assert resp.status_code in (302, 404)
