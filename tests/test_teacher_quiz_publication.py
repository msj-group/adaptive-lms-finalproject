"""Teacher Quiz publication, availability settings, authoring freezes and
attempt review (Phase 4 / M04D).

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
REPEATABLE READ snapshot isolation, so the concurrency tests here are
**structural**: they assert the *requested* reset and lock order and
exercise the post-lock rechecks by injecting a state change at an exact
transaction boundary. They are **not** a demonstration of real InnoDB
blocking. Time is injected rather than waited for.
"""

import re
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import event

from app.blueprints.teacher import quizzes as quizzes_mod
from app.extensions import db
from app.models import (
    MAX_ATTEMPT_LIMIT,
    MAX_TIME_LIMIT_MINUTES,
    AcademicStatus,
    AcademicTerm,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    QuestionAnswerMode,
    QuestionOption,
    Quiz,
    QuizAttempt,
    QuizAttemptStatus,
    QuizQuestion,
    QuizStatus,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password
from tests.conftest import login

PW = "Sup3rSecret!123"
NOW = datetime(2026, 5, 10, 9, 0, 0)
OPENS = datetime(2026, 5, 1, 8, 0, 0)
CLOSES = datetime(2026, 12, 1, 8, 0, 0)

DRAFT = QuizStatus.DRAFT.value
PUBLISHED = QuizStatus.PUBLISHED.value
SINGLE = QuestionAnswerMode.SINGLE.value
MULTIPLE = QuestionAnswerMode.MULTIPLE.value


class _Clock:
    def __init__(self, *moments):
        self.moments = list(moments) or [NOW]
        self.calls = 0

    def __call__(self, utc_now=None):
        moment = self.moments[min(self.calls, len(self.moments) - 1)]
        self.calls += 1
        return moment


def _at(*moments):
    return patch.object(quizzes_mod, "utc_reference_now", _Clock(*moments))


def _fresh_identity():
    """Drop Flask-Login's per-app-context user cache -- see the M04B tests
    for the full rationale. A fixture artifact, not application
    behaviour."""
    from flask import g, has_app_context

    if has_app_context():
        g.pop("_login_user", None)


def _login_as(client, email):
    _fresh_identity()
    return login(client, email)


def _assert_authenticated_as(client, user_id):
    with client.session_transaction() as session:
        assert session.get("_user_id", "").split(".")[0] == str(user_id)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _user(email, role, status=UserStatus.ACTIVE.value):
    row = User(
        email=email, password_hash=hash_password(PW), full_name=email.split("@")[0],
        role=role, status=status,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _hierarchy(**statuses):
    term = AcademicTerm(
        name="Term", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        status=statuses.get("term_status", AcademicStatus.ACTIVE.value),
    )
    level = Level(
        name="Level", display_order=0,
        status=statuses.get("level_status", AcademicStatus.ACTIVE.value),
    )
    db.session.add_all([term, level])
    db.session.commit()
    course = Course(
        title="Course", level_id=level.id, display_order=0,
        status=statuses.get("course_status", AcademicStatus.ACTIVE.value),
    )
    db.session.add(course)
    db.session.commit()
    group = Group(
        academic_term_id=term.id, course_id=course.id, name="Group A", capacity=20,
        status=statuses.get("group_status", AcademicStatus.ACTIVE.value),
    )
    db.session.add(group)
    db.session.commit()
    return group


def _quiz_row(group, title="Unit 1 check", **kwargs):
    quiz = Quiz(
        group_id=group.id, title=title, instructions="Answer every question.",
        version=kwargs.pop("version", 1), created_at=NOW, updated_at=NOW, **kwargs,
    )
    db.session.add(quiz)
    db.session.commit()
    return quiz


def _question(quiz, prompt="Capital of France?", mode=SINGLE, order=0,
              options=(("Paris", True), ("Rome", False))):
    question = QuizQuestion(
        quiz_id=quiz.id, prompt=prompt, answer_mode=mode, display_order=order,
        version=1, created_at=NOW, updated_at=NOW,
    )
    db.session.add(question)
    db.session.commit()
    for index, (text, correct) in enumerate(options):
        db.session.add(
            QuestionOption(
                question_id=question.id, option_text=text, display_order=index,
                is_correct=correct, is_active=True, created_at=NOW, updated_at=NOW,
            )
        )
    db.session.commit()
    return question


def _setup(email="teacher@example.com", publishable=True, **statuses):
    """(teacher, group, quiz) with an active assignment. `publishable`
    gives the quiz a complete window and one valid question."""
    teacher = _user(email, UserRole.TEACHER.value)
    group = _hierarchy(**statuses)
    db.session.add(GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id))
    db.session.commit()
    quiz = (
        _quiz_row(group, opens_at=OPENS, closes_at=CLOSES)
        if publishable
        else _quiz_row(group)
    )
    if publishable:
        _question(quiz)
    return teacher, group, quiz


def _student_with_attempt(quiz, email="s@example.com", **kwargs):
    student = _user(email, UserRole.STUDENT.value)
    db.session.add(
        Enrollment(student_id=student.id, group_id=quiz.group_id,
                   status=EnrollmentStatus.ACTIVE.value)
    )
    db.session.commit()
    attempt = QuizAttempt(
        quiz_id=quiz.id, student_id=student.id, attempt_number=1,
        status=kwargs.pop("status", QuizAttemptStatus.IN_PROGRESS.value),
        quiz_version=quiz.version, started_at=NOW,
        deadline_at=kwargs.pop("deadline_at", NOW + timedelta(hours=1)),
        **kwargs,
    )
    db.session.add(attempt)
    db.session.commit()
    return student, attempt


# ---------------------------------------------------------------------------
# request helpers
# ---------------------------------------------------------------------------


def _base(gpid, qpid):
    return f"/teacher/groups/{gpid}/quizzes/{qpid}"


def _token_from(client, url, field="quiz_state"):
    html = client.get(url).get_data(as_text=True)
    match = re.search(rf'name="{field}" value="([^"]*)"', html)
    return match.group(1) if match else ""


def _action_token(client, gpid, qpid, action):
    """The signed token embedded in the Publish or Withdraw form."""
    html = client.get(_base(gpid, qpid)).get_data(as_text=True)
    match = re.search(
        rf'action="[^"]*/{action}"[\s\S]{{0,500}}?name="quiz_state" value="([^"]*)"',
        html,
    )
    return match.group(1) if match else ""


def _save_settings(client, gpid, qpid, token, **overrides):
    data = {
        "opens_at": "2026-05-01T08:00",
        "closes_at": "2026-12-01T08:00",
        "time_limit_minutes": "",
        "attempt_limit": "1",
        "quiz_state": token,
    }
    data.update(overrides)
    return client.post(
        _base(gpid, qpid) + "/settings", data=data, follow_redirects=True
    )


# ===========================================================================
# Availability settings
# ===========================================================================


def test_settings_saves_the_window_and_bumps_the_version(app, client):
    with app.app_context():
        _, group, quiz = _setup(publishable=False)
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _base(gpid, qpid) + "/settings")
    with _at(NOW):
        resp = _save_settings(
            client, gpid, qpid, token, time_limit_minutes="45", attempt_limit="3"
        )
    assert resp.status_code == 200
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.opens_at is not None and quiz.closes_at is not None
        assert quiz.opens_at < quiz.closes_at
        assert quiz.time_limit_minutes == 45
        assert quiz.attempt_limit == 3
        assert quiz.version == 2
        assert quiz.updated_at == NOW
        # Settings never publish anything.
        assert quiz.status == DRAFT and quiz.published_at is None


def _local(app, moment):
    """The local wall-clock string a stored naive-UTC instant renders as.

    Derived through the same shared helper the form uses rather than
    hard-coded, so these tests stay correct under any configured
    ``APP_TIMEZONE`` -- the project's is deliberately not UTC.
    """
    from app.services.schedule_occurrences import to_app_local

    with app.app_context():
        return to_app_local(app.config["APP_TIMEZONE"], moment).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )


def test_an_unchanged_settings_save_is_a_no_op(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _base(gpid, qpid) + "/settings")
    resp = _save_settings(
        client, gpid, qpid, token,
        opens_at=_local(app, OPENS), closes_at=_local(app, CLOSES),
    )
    assert "unchanged, so nothing was saved" in resp.get_data(as_text=True)
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.version == 1 and quiz.updated_at == NOW


def test_the_settings_form_round_trips_the_stored_window(app, client):
    """A GET renders the stored UTC instants as local wall clocks, so what
    a Teacher sees is what they entered."""
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    html = client.get(_base(gpid, qpid) + "/settings").get_data(as_text=True)
    assert _local(app, OPENS) in html
    assert _local(app, CLOSES) in html


@pytest.mark.parametrize(
    "overrides",
    [
        {"opens_at": "2026-05-01T08:00", "closes_at": ""},
        {"opens_at": "", "closes_at": "2026-12-01T08:00"},
        {"opens_at": "2026-12-01T08:00", "closes_at": "2026-05-01T08:00"},
        {"opens_at": "2026-05-01T08:00", "closes_at": "2026-05-01T08:00"},
        {"time_limit_minutes": "0"},
        {"time_limit_minutes": str(MAX_TIME_LIMIT_MINUTES + 1)},
        {"attempt_limit": "0"},
        {"attempt_limit": str(MAX_ATTEMPT_LIMIT + 1)},
        {"attempt_limit": ""},
    ],
)
def test_invalid_settings_are_rejected(app, client, overrides):
    with app.app_context():
        _, group, quiz = _setup(publishable=False)
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _base(gpid, qpid) + "/settings")
    resp = _save_settings(client, gpid, qpid, token, **overrides)
    assert resp.status_code == 200
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.version == 1
        assert quiz.opens_at is None and quiz.closes_at is None


def test_clearing_both_availability_times_is_allowed(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _base(gpid, qpid) + "/settings")
    _save_settings(client, gpid, qpid, token, opens_at="", closes_at="")
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.opens_at is None and quiz.closes_at is None
        assert quiz.version == 2


def test_a_stale_settings_token_is_rejected(app, client):
    with app.app_context():
        _, group, quiz = _setup(publishable=False)
        gpid, qpid, quiz_id = group.public_id, quiz.public_id, quiz.id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _base(gpid, qpid) + "/settings")
    with app.app_context():
        db.session.get(Quiz, quiz_id).version = 5
        db.session.commit()
    resp = _save_settings(client, gpid, qpid, token, attempt_limit="4")
    assert "out of date" in resp.get_data(as_text=True) or "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert Quiz.query.one().attempt_limit == 1


# ===========================================================================
# Publication readiness and publishing
# ===========================================================================


def test_publishing_a_ready_quiz_sets_status_and_stamps_the_time(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _action_token(client, gpid, qpid, "publish")
    assert token
    with _at(NOW):
        resp = client.post(
            _base(gpid, qpid) + "/publish", data={"quiz_state": token},
            follow_redirects=True,
        )
    assert resp.status_code == 200
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.status == PUBLISHED
        assert quiz.published_at == NOW
        assert quiz.version == 2


@pytest.mark.parametrize(
    "case,expected",
    [
        ("no_window", "opening time"),
        ("no_questions", "at least one question"),
        ("bad_single", "exactly one correct"),
        ("bad_multiple", "at least two correct"),
        ("bad_structure", "answer options"),
    ],
)
def test_every_publication_readiness_rule_blocks(app, client, case, expected):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        db.session.add(
            GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id)
        )
        db.session.commit()
        if case == "no_window":
            quiz = _quiz_row(group)
            _question(quiz)
        else:
            quiz = _quiz_row(group, opens_at=OPENS, closes_at=CLOSES)
            if case == "bad_single":
                _question(quiz, options=(("A", True), ("B", True)))
            elif case == "bad_multiple":
                _question(quiz, mode=MULTIPLE, options=(("A", True), ("B", False)))
            elif case == "bad_structure":
                question = _question(quiz, options=(("A", True), ("B", False)))
                # Retire one option, leaving a single active one.
                option = (
                    QuestionOption.query.filter_by(question_id=question.id)
                    .order_by(QuestionOption.display_order.desc())
                    .first()
                )
                option.is_active = False
                option.retired_at = NOW
                db.session.commit()
        gpid, qpid = group.public_id, quiz.public_id

    _login_as(client, "teacher@example.com")
    html = client.get(_base(gpid, qpid)).get_data(as_text=True)
    assert "Not ready to publish yet" in html
    assert expected in html
    # The Publish control is not even offered...
    assert 'action="/teacher/groups/%s/quizzes/%s/publish"' % (gpid, qpid) not in html
    # ...and a forged POST is refused server-side anyway.
    forged = quizzes_mod._make_publication_token(
        quizzes_mod._PUBLISH_ACTION,
        _teacher_public_id(app), gpid, qpid, _quiz_version(app), DRAFT,
    )
    resp = client.post(
        _base(gpid, qpid) + "/publish", data={"quiz_state": forged},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.status == DRAFT and quiz.published_at is None
        assert quiz.version == 1


def _teacher_public_id(app):
    with app.app_context():
        return User.query.filter_by(email="teacher@example.com").one().public_id


def _quiz_version(app):
    with app.app_context():
        return Quiz.query.one().version


def test_a_quiz_over_the_question_maximum_is_refused_not_truncated(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        # 101 questions: one over the approved maximum.
        for index in range(1, 101):
            _question(quiz, prompt=f"Q{index}", order=index)
        gpid, qpid = group.public_id, quiz.public_id
        before = QuizQuestion.query.count()
    _login_as(client, "teacher@example.com")
    html = client.get(_base(gpid, qpid)).get_data(as_text=True)
    assert "more than 100 questions" in html
    assert "Nothing has been changed or removed" in html
    with app.app_context():
        assert QuizQuestion.query.count() == before
        assert Quiz.query.one().status == DRAFT


# ===========================================================================
# Freezes
# ===========================================================================


def _publish(app, client, gpid, qpid):
    token = _action_token(client, gpid, qpid, "publish")
    return client.post(
        _base(gpid, qpid) + "/publish", data={"quiz_state": token},
        follow_redirects=True,
    )


@pytest.mark.parametrize(
    "route",
    ["edit", "settings", "question_new", "question_edit", "move_up", "move_down"],
)
def test_publishing_freezes_every_authoring_route(app, client, route):
    with app.app_context():
        _, group, quiz = _setup()
        question = _question(quiz, prompt="Second", order=1)
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
    _login_as(client, "teacher@example.com")
    _publish(app, client, gpid, qpid)
    with app.app_context():
        assert Quiz.query.one().status == PUBLISHED
        version_before = Quiz.query.one().version

    base = _base(gpid, qpid)
    responses = {
        "edit": client.get(base + "/edit", follow_redirects=True),
        "settings": client.get(base + "/settings", follow_redirects=True),
        "question_new": client.get(base + "/questions/new", follow_redirects=True),
        "question_edit": client.get(
            f"{base}/questions/{xpid}/edit", follow_redirects=True
        ),
        "move_up": client.post(
            f"{base}/questions/{xpid}/move-up", data={"question_state": "x"},
            follow_redirects=True,
        ),
        "move_down": client.post(
            f"{base}/questions/{xpid}/move-down", data={"question_state": "x"},
            follow_redirects=True,
        ),
    }
    body = responses[route].get_data(as_text=True)
    assert "read-only" in body or "no longer be changed" in body
    with app.app_context():
        assert Quiz.query.one().version == version_before
        assert [q.prompt for q in QuizQuestion.query.order_by(
            QuizQuestion.display_order
        )] == ["Capital of France?", "Second"]


def test_an_attempt_freezes_the_quiz_permanently(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    _publish(app, client, gpid, qpid)
    with app.app_context():
        quiz = Quiz.query.one()
        _student_with_attempt(quiz)
        version_before = quiz.version

    # Withdrawal is refused once anybody has started.
    html = client.get(_base(gpid, qpid)).get_data(as_text=True)
    assert "Withdraw" not in html
    assert "already started this quiz" in html
    forged = quizzes_mod._make_publication_token(
        quizzes_mod._UNPUBLISH_ACTION,
        _teacher_public_id(app), gpid, qpid, version_before, PUBLISHED,
    )
    resp = client.post(
        _base(gpid, qpid) + "/unpublish", data={"quiz_state": forged},
        follow_redirects=True,
    )
    assert "already started this quiz" in resp.get_data(as_text=True)
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.status == PUBLISHED and quiz.version == version_before


def test_withdrawing_before_any_attempt_returns_the_quiz_to_draft(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    _publish(app, client, gpid, qpid)
    with app.app_context():
        assert Quiz.query.one().status == PUBLISHED
        version_after_publish = Quiz.query.one().version

    token = _action_token(client, gpid, qpid, "unpublish")
    assert token
    resp = client.post(
        _base(gpid, qpid) + "/unpublish", data={"quiz_state": token},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.status == DRAFT
        assert quiz.published_at is None
        assert quiz.version == version_after_publish + 1
    # ...and authoring is possible again.
    assert client.get(_base(gpid, qpid) + "/edit").status_code == 200


def test_a_publish_token_cannot_be_replayed_as_a_withdraw(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    publish_token = _action_token(client, gpid, qpid, "publish")
    resp = client.post(
        _base(gpid, qpid) + "/unpublish", data={"quiz_state": publish_token},
        follow_redirects=True,
    )
    assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert Quiz.query.one().status == DRAFT


def test_replaying_a_successful_publish_is_rejected(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _action_token(client, gpid, qpid, "publish")
    client.post(_base(gpid, qpid) + "/publish", data={"quiz_state": token},
                follow_redirects=True)
    with app.app_context():
        published_at = Quiz.query.one().published_at
        version = Quiz.query.one().version
    resp = client.post(_base(gpid, qpid) + "/publish", data={"quiz_state": token},
                       follow_redirects=True)
    assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.published_at == published_at and quiz.version == version


# ===========================================================================
# Authorization, methods, CSRF
# ===========================================================================


def test_anonymous_and_wrong_roles_are_refused(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
        _user("other@example.com", UserRole.STUDENT.value)
    base = _base(gpid, qpid)
    for url in (base + "/settings", base + "/attempts"):
        resp = client.get(url)
        assert resp.status_code == 302 and "/auth/login" in resp.headers["Location"]
    _login_as(client, "other@example.com")
    assert client.get(base + "/settings").status_code == 403
    assert client.get(base + "/attempts").status_code == 403
    assert client.post(base + "/publish").status_code == 403


@pytest.mark.parametrize("assignment", ["none", "removed"])
def test_unassigned_or_removed_teacher_gets_a_non_disclosing_404(app, client, assignment):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
        outsider = _user("outsider@example.com", UserRole.TEACHER.value)
        if assignment == "removed":
            db.session.add(
                GroupTeacherAssignment(
                    group_id=group.id, teacher_id=outsider.id,
                    status=GroupTeacherAssignmentStatus.REMOVED.value,
                )
            )
            db.session.commit()
    _login_as(client, "outsider@example.com")
    base = _base(gpid, qpid)
    for url in (base + "/settings", base + "/attempts"):
        resp = client.get(url)
        assert resp.status_code == 404
        assert b"Unit 1 check" not in resp.data
    assert client.post(base + "/publish", data={"quiz_state": "x"}).status_code == 404


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_unsupported_methods_are_rejected(app, client, method):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    base = _base(gpid, qpid)
    for url in (base + "/settings", base + "/publish", base + "/unpublish",
                base + "/attempts"):
        assert getattr(client, method)(url).status_code == 405, url


def test_publish_and_settings_require_csrf(app):
    csrf_app = __import__("app", fromlist=["create_app"]).create_app(
        "testing", WTF_CSRF_ENABLED=True
    )
    with csrf_app.app_context():
        db.create_all()
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
        csrf_client = csrf_app.test_client()
        login(csrf_client, "teacher@example.com")
        base = _base(gpid, qpid)
        assert csrf_client.post(base + "/publish").status_code == 400
        assert csrf_client.post(base + "/unpublish").status_code == 400
        assert csrf_client.post(
            base + "/settings", data={"attempt_limit": "2"}
        ).status_code == 400
        assert Quiz.query.one().status == DRAFT
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def test_there_is_no_delete_grade_or_override_endpoint(app):
    for rule in app.url_map.iter_rules():
        text = str(rule).lower()
        if "quiz" not in text:
            continue
        for forbidden in ("delete", "grade", "override", "score", "regrade", "release"):
            assert forbidden not in text, (text, forbidden)
        assert "DELETE" not in rule.methods
        assert rule.methods <= {"GET", "HEAD", "OPTIONS", "POST"}


@pytest.mark.parametrize("page", ["detail", "settings", "attempts"])
def test_content_bearing_responses_carry_the_cache_headers(app, client, page):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    url = {
        "detail": _base(gpid, qpid),
        "settings": _base(gpid, qpid) + "/settings",
        "attempts": _base(gpid, qpid) + "/attempts",
    }[page]
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in resp.headers.get("Vary", "")


# ===========================================================================
# Attempt review
# ===========================================================================


def test_teacher_sees_attempts_and_the_answer_key(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    _publish(app, client, gpid, qpid)
    with app.app_context():
        quiz = Quiz.query.one()
        student, attempt = _student_with_attempt(
            quiz, status=QuizAttemptStatus.SUBMITTED.value,
            submitted_at=NOW, correct_count=1, total_questions=1,
        )
        apid = attempt.public_id

    listing = client.get(_base(gpid, qpid) + "/attempts")
    assert listing.status_code == 200
    body = listing.get_data(as_text=True)
    assert "1 / 1" in body and "100%" in body

    detail = client.get(f"{_base(gpid, qpid)}/attempts/{apid}")
    assert detail.status_code == 200
    body = detail.get_data(as_text=True)
    # A teacher IS authorized to see the key.
    assert "Correct answer" in body
    assert "Paris" in body and "Rome" in body


def test_attempt_pagination_is_bounded_and_query_count_does_not_grow(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        for index in range(1, 5):
            _question(quiz, prompt=f"Q{index}", order=index)
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    _publish(app, client, gpid, qpid)
    with app.app_context():
        quiz = Quiz.query.one()
        for index in range(21):
            student = _user(f"s{index}@example.com", UserRole.STUDENT.value)
            db.session.add(
                Enrollment(student_id=student.id, group_id=quiz.group_id,
                           status=EnrollmentStatus.ACTIVE.value)
            )
            db.session.commit()
            db.session.add(
                QuizAttempt(
                    quiz_id=quiz.id, student_id=student.id, attempt_number=1,
                    status=QuizAttemptStatus.SUBMITTED.value, quiz_version=quiz.version,
                    started_at=NOW + timedelta(minutes=index),
                    deadline_at=NOW + timedelta(hours=2),
                    submitted_at=NOW + timedelta(minutes=index + 1),
                    correct_count=1, total_questions=5,
                )
            )
            db.session.commit()

    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        first = client.get(_base(gpid, qpid) + "/attempts")
    finally:
        event.remove(db.engine, "before_cursor_execute", record)

    assert first.status_code == 200
    body = first.get_data(as_text=True)
    assert "page=2" in body
    selects = [s for s in statements if s.strip().upper().startswith("SELECT")]
    # One page, one joined statement for names -- never one query per row.
    assert len(selects) <= 12, (len(selects), selects)

    second = client.get(_base(gpid, qpid) + "/attempts?page=2")
    assert second.status_code == 200


def _select_count(client, url):
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        resp = client.get(url)
    finally:
        event.remove(db.engine, "before_cursor_execute", record)
    assert resp.status_code == 200
    return len([s for s in statements if s.strip().upper().startswith("SELECT")]), resp


def _attempt_detail_selects(app, client, question_count):
    """Render one attempt-detail page for a Quiz with `question_count`
    questions and return how many SELECTs it took."""
    with app.app_context():
        db.drop_all()
        db.create_all()
        _, group, quiz = _setup()
        for index in range(1, question_count):
            _question(quiz, prompt=f"Q{index}", order=index,
                      options=(("A", True), ("B", False), ("C", False)))
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    _publish(app, client, gpid, qpid)
    with app.app_context():
        quiz = Quiz.query.one()
        _, attempt = _student_with_attempt(
            quiz, status=QuizAttemptStatus.SUBMITTED.value,
            submitted_at=NOW, correct_count=1, total_questions=question_count,
        )
        apid = attempt.public_id
    count, _ = _select_count(client, f"{_base(gpid, qpid)}/attempts/{apid}")
    return count


def test_attempt_detail_query_count_does_not_grow_with_the_quiz(app, client):
    """The real no-N+1 proof: a 20-question quiz costs exactly as many
    statements as a 2-question one. Four bounded reads build the page --
    never one per question or per answer."""
    small = _attempt_detail_selects(app, client, 2)
    large = _attempt_detail_selects(app, client, 20)
    assert small == large, (small, large)
    # And it is a small fixed number, not merely a constant large one.
    assert large <= 20, large


def test_an_attempt_from_another_quiz_404s(app, client):
    with app.app_context():
        teacher, group, quiz = _setup()
        other = _quiz_row(group, title="Other", opens_at=OPENS, closes_at=CLOSES)
        _question(other, prompt="Other question")
        student, attempt = _student_with_attempt(other)
        gpid, qpid, apid = group.public_id, quiz.public_id, attempt.public_id
    _login_as(client, "teacher@example.com")
    assert client.get(f"{_base(gpid, qpid)}/attempts/{apid}").status_code == 404


@pytest.mark.parametrize("bogus", ["1", "0", "-1", "9999", "abc"])
def test_a_numeric_attempt_identifier_404s(app, client, bogus):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    assert client.get(f"{_base(gpid, qpid)}/attempts/{bogus}").status_code == 404


def test_a_role_inconsistent_attempt_owner_fails_closed(app, client):
    """A foreign key into ``users`` proves the row exists, never its
    role."""
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    _publish(app, client, gpid, qpid)
    with app.app_context():
        quiz = Quiz.query.one()
        student, attempt = _student_with_attempt(quiz)
        apid = attempt.public_id
        student.role = UserRole.RESEARCHER.value
        db.session.commit()
    assert client.get(f"{_base(gpid, qpid)}/attempts/{apid}").status_code == 404
    # And the list simply omits it rather than rendering it as student work.
    body = client.get(_base(gpid, qpid) + "/attempts").get_data(as_text=True)
    assert "No attempts yet" in body


def test_request_driven_expiry_finalizes_an_overdue_attempt_on_a_teacher_read(
    app, client
):
    """No background job: the next authorized reader settles it."""
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    _publish(app, client, gpid, qpid)
    with app.app_context():
        quiz = Quiz.query.one()
        _, attempt = _student_with_attempt(
            quiz, deadline_at=NOW - timedelta(minutes=1)
        )
        apid = attempt.public_id
        assert attempt.status == QuizAttemptStatus.IN_PROGRESS.value

    with _at(NOW):
        resp = client.get(_base(gpid, qpid) + "/attempts")
    assert resp.status_code == 200
    with app.app_context():
        attempt = QuizAttempt.query.one()
        assert attempt.status == QuizAttemptStatus.EXPIRED.value
        assert attempt.submitted_at is None
        assert attempt.correct_count == 0 and attempt.total_questions == 1

    # Idempotent: a second read changes nothing.
    with app.app_context():
        before = (QuizAttempt.query.one().correct_count,
                  QuizAttempt.query.one().total_questions)
    with _at(NOW + timedelta(hours=1)):
        client.get(_base(gpid, qpid) + "/attempts")
    with app.app_context():
        attempt = QuizAttempt.query.one()
        assert (attempt.correct_count, attempt.total_questions) == before
        assert attempt.status == QuizAttemptStatus.EXPIRED.value


# ===========================================================================
# Structural: lock order and post-lock rechecks
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


def test_publish_locks_the_established_prefix_under_one_reset(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _action_token(client, gpid, qpid, "publish")
    events = _capture_locks(
        lambda: client.post(_base(gpid, qpid) + "/publish",
                            data={"quiz_state": token}, follow_redirects=True)
    )
    assert events.count("reset") == 1
    assert [e for e in events if e.startswith("lock:")] == [
        "lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group",
        "lock:User", "lock:GroupTeacherAssignment", "lock:Quiz",
    ]


def _inject_before_group_lock(action):
    original = quizzes_mod.lock_group_in_open_transaction

    def side_effect(public_id):
        action()
        return original(public_id)

    return patch.object(
        quizzes_mod, "lock_group_in_open_transaction", side_effect=side_effect
    )


def test_post_lock_recheck_catches_an_attempt_created_in_the_window(app, client):
    """The readiness panel and the pre-lock preview saw no attempt, but one
    is created before the Quiz lock. The authoritative post-lock check must
    refuse the withdrawal."""
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    _publish(app, client, gpid, qpid)
    token = _action_token(client, gpid, qpid, "unpublish")
    with app.app_context():
        quiz_id = Quiz.query.one().id
        student = _user("late@example.com", UserRole.STUDENT.value)
        db.session.add(
            Enrollment(student_id=student.id, group_id=Group.query.one().id,
                       status=EnrollmentStatus.ACTIVE.value)
        )
        db.session.commit()
        student_id = student.id
        version = Quiz.query.one().version

    def start_attempt():
        db.session.add(
            QuizAttempt(
                quiz_id=quiz_id, student_id=student_id, attempt_number=1,
                status=QuizAttemptStatus.IN_PROGRESS.value, quiz_version=version,
                started_at=NOW, deadline_at=NOW + timedelta(hours=1),
            )
        )
        db.session.commit()

    with _inject_before_group_lock(start_attempt):
        resp = client.post(
            _base(gpid, qpid) + "/unpublish", data={"quiz_state": token},
            follow_redirects=True,
        )
    assert "already started this quiz" in resp.get_data(as_text=True)
    with app.app_context():
        assert Quiz.query.one().status == PUBLISHED
        assert Quiz.query.one().version == version


@pytest.mark.parametrize("entity", ["group", "term", "level", "course"])
def test_post_lock_recheck_rejects_a_concurrently_archived_ancestor(app, client, entity):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
        target = {
            "group": (Group, group.id),
            "term": (AcademicTerm, group.academic_term_id),
            "course": (Course, group.course_id),
            "level": (Level, Course.query.one().level_id),
        }[entity]
    _login_as(client, "teacher@example.com")
    token = _action_token(client, gpid, qpid, "publish")
    model, row_id = target

    def archive():
        db.session.get(model, row_id).status = AcademicStatus.ARCHIVED.value
        db.session.commit()

    with _inject_before_group_lock(archive):
        resp = client.post(_base(gpid, qpid) + "/publish",
                           data={"quiz_state": token}, follow_redirects=True)
    assert resp.status_code == 200
    assert "can only be published or withdrawn" in resp.get_data(as_text=True)
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.status == DRAFT and quiz.version == 1


def test_publication_integrity_error_rolls_back_and_reports_generically(app, client):
    from sqlalchemy.exc import IntegrityError

    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _action_token(client, gpid, qpid, "publish")

    real_rollback = db.session.rollback

    def fail():
        real_rollback()
        raise IntegrityError("UPDATE quizzes SET status=?", {"status": "published"},
                             Exception("boom"))

    with patch.object(db.session, "commit", side_effect=fail):
        resp = client.post(_base(gpid, qpid) + "/publish",
                           data={"quiz_state": token}, follow_redirects=True)
    body = resp.get_data(as_text=True)
    assert "could not be updated" in body
    for leak in ("UPDATE quizzes", "IntegrityError", "boom"):
        assert leak not in body, leak
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.status == DRAFT and quiz.published_at is None
