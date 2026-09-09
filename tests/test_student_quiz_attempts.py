"""Student Quiz attempts: eligibility, availability boundaries, attempt
limits, deadlines, request-driven expiry, answer saving, exact-set grading,
submission idempotence and answer-key non-disclosure (Phase 4 / M04D).

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
REPEATABLE READ snapshot isolation, so the concurrency tests here are
**structural**: they assert the *requested* reset and lock order and
exercise the post-lock rechecks by injecting a state change at an exact
transaction boundary. They are **not** a demonstration of real InnoDB
blocking. Time is injected rather than waited for, so the "at exactly this
second" boundaries are exact rather than probabilistic.
"""

import re
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import event

from app.blueprints.student import quizzes as student_quizzes
from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    Level,
    QuestionAnswerMode,
    QuestionOption,
    Quiz,
    QuizAnswer,
    QuizAnswerSelection,
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
OPENS = datetime(2026, 5, 1, 8, 0, 0)
CLOSES = datetime(2026, 6, 1, 8, 0, 0)
NOW = datetime(2026, 5, 10, 9, 0, 0)

SINGLE = QuestionAnswerMode.SINGLE.value
MULTIPLE = QuestionAnswerMode.MULTIPLE.value
IN_PROGRESS = QuizAttemptStatus.IN_PROGRESS.value
SUBMITTED = QuizAttemptStatus.SUBMITTED.value
EXPIRED = QuizAttemptStatus.EXPIRED.value


class _Clock:
    def __init__(self, *moments):
        self.moments = list(moments) or [NOW]
        self.calls = 0

    def __call__(self, utc_now=None):
        moment = self.moments[min(self.calls, len(self.moments) - 1)]
        self.calls += 1
        return moment


def _at(*moments):
    """Patch the Student blueprint's clock. The service layer reads its
    moment from the caller, so patching here is enough."""
    return patch.object(student_quizzes, "utc_reference_now", _Clock(*moments))


def _fresh_identity():
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


def _hierarchy(label="A", **statuses):
    """One AcademicTerm / Level / Course / Group chain.

    `label` keeps the names unique so a test can build two independent
    chains -- AcademicTerm names, Level names and Group names all carry
    uniqueness constraints of their own.
    """
    term = AcademicTerm(
        name=f"Term {label}", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        status=statuses.get("term_status", AcademicStatus.ACTIVE.value),
    )
    level = Level(name=f"Level {label}", display_order=0,
                  status=statuses.get("level_status", AcademicStatus.ACTIVE.value))
    db.session.add_all([term, level])
    db.session.commit()
    course = Course(title=f"Course {label}", level_id=level.id, display_order=0,
                    status=statuses.get("course_status", AcademicStatus.ACTIVE.value))
    db.session.add(course)
    db.session.commit()
    group = Group(
        academic_term_id=term.id, course_id=course.id, name=f"Group {label}",
        capacity=20,
        status=statuses.get("group_status", AcademicStatus.ACTIVE.value),
    )
    db.session.add(group)
    db.session.commit()
    return group


def _question(quiz, prompt, mode, order, options):
    question = QuizQuestion(
        quiz_id=quiz.id, prompt=prompt, answer_mode=mode, display_order=order,
        version=1, created_at=OPENS, updated_at=OPENS,
    )
    db.session.add(question)
    db.session.commit()
    for index, (text, correct) in enumerate(options):
        db.session.add(
            QuestionOption(
                question_id=question.id, option_text=text, display_order=index,
                is_correct=correct, is_active=True, created_at=OPENS, updated_at=OPENS,
            )
        )
    db.session.commit()
    return question


def _world(
    enrollment_status=EnrollmentStatus.ACTIVE.value,
    student_status=UserStatus.ACTIVE.value,
    student_role=UserRole.STUDENT.value,
    published=True,
    opens_at=OPENS,
    closes_at=CLOSES,
    time_limit=None,
    attempt_limit=1,
    **statuses,
):
    """A published two-question Quiz with one enrolled Student."""
    group = _hierarchy(**statuses)
    teacher = _user("t@example.com", UserRole.TEACHER.value)
    student = _user("s@example.com", student_role, status=student_status)
    db.session.add(GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id))
    db.session.add(
        Enrollment(student_id=student.id, group_id=group.id, status=enrollment_status)
    )
    db.session.commit()

    quiz = Quiz(
        group_id=group.id, title="Unit 1 check", instructions="Answer every question.",
        version=1, created_at=OPENS, updated_at=OPENS,
        opens_at=opens_at, closes_at=closes_at,
        time_limit_minutes=time_limit, attempt_limit=attempt_limit,
        status=QuizStatus.PUBLISHED.value if published else QuizStatus.DRAFT.value,
        published_at=OPENS if published else None,
    )
    db.session.add(quiz)
    db.session.commit()

    q1 = _question(quiz, "Capital of France?", SINGLE, 0,
                   [("Paris", True), ("Rome", False), ("Madrid", False)])
    q2 = _question(quiz, "Which are even?", MULTIPLE, 1,
                   [("2", True), ("3", False), ("4", True)])
    return group, student, quiz, q1, q2


def _options(question):
    return {
        o.option_text: o.public_id
        for o in QuestionOption.query.filter_by(question_id=question.id, is_active=True)
    }


# ---------------------------------------------------------------------------
# request helpers
# ---------------------------------------------------------------------------


def _base(gpid, qpid):
    return f"/student/groups/{gpid}/quizzes/{qpid}"


def _question_url(gpid, qpid, apid, xpid):
    return f"{_base(gpid, qpid)}/attempts/{apid}/questions/{xpid}"


def _result_url(gpid, qpid, apid):
    return f"{_base(gpid, qpid)}/attempts/{apid}/result"


def _token(client, url, field):
    html = client.get(url).get_data(as_text=True)
    match = re.search(rf'name="{field}" value="([^"]*)"', html)
    return match.group(1) if match else ""


def _start(client, gpid, qpid, follow=False):
    return client.post(_base(gpid, qpid) + "/start", follow_redirects=follow)


def _answer(client, gpid, qpid, apid, xpid, options, token=None, follow=True, **extra):
    url = _question_url(gpid, qpid, apid, xpid)
    if token is None:
        token = _token(client, url, "answer_state")
    data = {"answer_state": token, "option": options}
    data.update(extra)
    return client.post(url + "/answer", data=data, follow_redirects=follow)


def _submit(client, gpid, qpid, apid, xpid, token=None, confirm=False, follow=True):
    if token is None:
        token = _token(client, _question_url(gpid, qpid, apid, xpid), "submit_state")
    data = {"submit_state": token, "from_question": xpid}
    if confirm:
        data["confirm_unanswered"] = "yes"
    return client.post(
        f"{_base(gpid, qpid)}/attempts/{apid}/submit", data=data, follow_redirects=follow
    )


# ===========================================================================
# Eligibility and non-disclosure
# ===========================================================================


def test_anonymous_is_redirected_to_login(app, client):
    with app.app_context():
        group, student, quiz, q1, _ = _world()
        gpid, qpid = group.public_id, quiz.public_id
    for url in ("/student/quizzes", _base(gpid, qpid)):
        resp = client.get(url)
        assert resp.status_code == 302 and "/auth/login" in resp.headers["Location"]
    assert _start(client, gpid, qpid).status_code == 302


@pytest.mark.parametrize(
    "role", [UserRole.TEACHER.value, UserRole.ADMINISTRATOR.value,
             UserRole.RESEARCHER.value]
)
def test_non_student_roles_are_forbidden(app, client, role):
    with app.app_context():
        group, _, quiz, _, _ = _world()
        gpid, qpid = group.public_id, quiz.public_id
        _user("other@example.com", role)
    _login_as(client, "other@example.com")
    assert client.get("/student/quizzes").status_code == 403
    assert client.get(_base(gpid, qpid)).status_code == 403
    assert _start(client, gpid, qpid).status_code == 403


@pytest.mark.parametrize(
    "case",
    ["draft", "withdrawn_enrollment", "archived_group", "archived_term",
     "archived_level", "archived_course", "not_yet_open"],
)
def test_ineligible_quizzes_are_invisible_and_unstartable(app, client, case):
    kwargs = {}
    if case == "draft":
        kwargs["published"] = False
    elif case == "withdrawn_enrollment":
        kwargs["enrollment_status"] = EnrollmentStatus.WITHDRAWN.value
    elif case.startswith("archived_"):
        kwargs[f"{case.split('_')[1]}_status"] = AcademicStatus.ARCHIVED.value
    with app.app_context():
        group, _, quiz, _, _ = _world(**kwargs)
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "s@example.com")
    moment = OPENS - timedelta(seconds=1) if case == "not_yet_open" else NOW
    with _at(moment):
        listing = client.get("/student/quizzes")
        detail = client.get(_base(gpid, qpid))
        start = _start(client, gpid, qpid)
    assert b"Unit 1 check" not in listing.data
    assert detail.status_code == 404
    assert start.status_code == 404
    with app.app_context():
        assert QuizAttempt.query.count() == 0


def test_another_groups_quiz_public_id_404s(app, client):
    with app.app_context():
        group, student, quiz, _, _ = _world()
        other_group = _hierarchy(label="B")
        gpid_other, qpid = other_group.public_id, quiz.public_id
    _login_as(client, "s@example.com")
    with _at(NOW):
        assert client.get(_base(gpid_other, qpid)).status_code == 404


def test_one_student_cannot_reach_another_students_attempt(app, client):
    with app.app_context():
        group, student, quiz, q1, _ = _world(attempt_limit=2)
        other = _user("other@example.com", UserRole.STUDENT.value)
        db.session.add(
            Enrollment(student_id=other.id, group_id=group.id,
                       status=EnrollmentStatus.ACTIVE.value)
        )
        db.session.commit()
        gpid, qpid, x1 = group.public_id, quiz.public_id, q1.public_id
    _login_as(client, "s@example.com")
    with _at(NOW):
        _start(client, gpid, qpid)
    with app.app_context():
        apid = QuizAttempt.query.one().public_id

    other_client = app.test_client()
    _login_as(other_client, "other@example.com")
    with _at(NOW):
        assert other_client.get(
            _question_url(gpid, qpid, apid, x1)
        ).status_code == 404
        assert other_client.get(_result_url(gpid, qpid, apid)).status_code == 404


@pytest.mark.parametrize("bogus", ["1", "0", "-1", "9999", "abc"])
def test_numeric_identifiers_are_rejected(app, client, bogus):
    with app.app_context():
        group, _, quiz, q1, _ = _world()
        gpid, qpid, x1 = group.public_id, quiz.public_id, q1.public_id
    _login_as(client, "s@example.com")
    with _at(NOW):
        _start(client, gpid, qpid)
    with app.app_context():
        apid = QuizAttempt.query.one().public_id
    with _at(NOW):
        assert client.get(_question_url(gpid, qpid, bogus, x1)).status_code == 404
        assert client.get(_question_url(gpid, qpid, apid, bogus)).status_code == 404
        assert client.get(_base(gpid, bogus)).status_code == 404


# ===========================================================================
# Availability boundaries -- exact seconds
# ===========================================================================


@pytest.mark.parametrize(
    "moment,visible,startable",
    [
        (OPENS - timedelta(seconds=1), False, False),
        (OPENS, True, True),
        (CLOSES - timedelta(seconds=1), True, True),
        (CLOSES, True, False),
        (CLOSES + timedelta(days=1), True, False),
    ],
)
def test_availability_boundaries_are_exact(app, client, moment, visible, startable):
    with app.app_context():
        group, _, quiz, _, _ = _world()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "s@example.com")
    with _at(moment):
        detail = client.get(_base(gpid, qpid))
        start = _start(client, gpid, qpid, follow=True)
    if not visible:
        assert detail.status_code == 404
    else:
        assert detail.status_code == 200
    with app.app_context():
        assert QuizAttempt.query.count() == (1 if startable else 0)
    if visible and not startable:
        assert "not open right now" in start.get_data(as_text=True)


def test_a_closed_quiz_stays_readable_so_a_receipt_is_reachable(app, client):
    with app.app_context():
        group, _, quiz, q1, q2 = _world()
        gpid, qpid, x1 = group.public_id, quiz.public_id, q1.public_id
    _login_as(client, "s@example.com")
    with _at(NOW):
        _start(client, gpid, qpid)
    with app.app_context():
        apid = QuizAttempt.query.one().public_id
    with _at(NOW):
        _submit(client, gpid, qpid, apid, x1, confirm=True)
    # Long after the quiz closed, the receipt is still reachable.
    with _at(CLOSES + timedelta(days=30)):
        listing = client.get("/student/quizzes")
        result = client.get(_result_url(gpid, qpid, apid))
    assert b"Unit 1 check" in listing.data
    assert result.status_code == 200


# ===========================================================================
# Starting attempts
# ===========================================================================


def test_starting_creates_one_attempt_with_a_computed_deadline(app, client):
    with app.app_context():
        group, _, quiz, q1, _ = _world(time_limit=30)
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "s@example.com")
    with _at(NOW):
        resp = _start(client, gpid, qpid)
    assert resp.status_code == 302
    with app.app_context():
        attempt = QuizAttempt.query.one()
        assert attempt.attempt_number == 1
        assert attempt.status == IN_PROGRESS
        assert attempt.started_at == NOW
        assert attempt.deadline_at == NOW + timedelta(minutes=30)
        assert attempt.quiz_version == Quiz.query.one().version
        assert attempt.correct_count is None and attempt.total_questions is None


def test_deadline_without_a_time_limit_is_the_closing_time(app, client):
    with app.app_context():
        group, _, quiz, _, _ = _world()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "s@example.com")
    with _at(NOW):
        _start(client, gpid, qpid)
    with app.app_context():
        assert QuizAttempt.query.one().deadline_at == CLOSES


def test_deadline_is_the_earlier_of_the_limit_and_the_closing_time(app, client):
    """A time limit must never let an attempt run past the window."""
    with app.app_context():
        group, _, quiz, _, _ = _world(time_limit=300)
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "s@example.com")
    late = CLOSES - timedelta(minutes=10)
    with _at(late):
        _start(client, gpid, qpid)
    with app.app_context():
        assert QuizAttempt.query.one().deadline_at == CLOSES


def test_a_repeated_start_returns_the_existing_attempt(app, client):
    with app.app_context():
        group, _, quiz, _, _ = _world(attempt_limit=3)
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "s@example.com")
    with _at(NOW):
        first = _start(client, gpid, qpid)
        second = _start(client, gpid, qpid)
    assert first.headers["Location"] == second.headers["Location"]
    with app.app_context():
        assert QuizAttempt.query.count() == 1


def test_the_attempt_limit_is_enforced(app, client):
    with app.app_context():
        group, student, quiz, q1, _ = _world(attempt_limit=2)
        gpid, qpid, x1 = group.public_id, quiz.public_id, q1.public_id
    _login_as(client, "s@example.com")
    for _ in range(2):
        with _at(NOW):
            _start(client, gpid, qpid)
        with app.app_context():
            attempt = QuizAttempt.query.filter_by(status=IN_PROGRESS).one()
            apid = attempt.public_id
        with _at(NOW):
            _submit(client, gpid, qpid, apid, x1, confirm=True)
    with app.app_context():
        assert QuizAttempt.query.count() == 2
    with _at(NOW):
        resp = _start(client, gpid, qpid, follow=True)
    assert "used all 2 attempts" in resp.get_data(as_text=True)
    with app.app_context():
        assert QuizAttempt.query.count() == 2


def test_a_later_attempt_may_start_after_the_previous_one_ended(app, client):
    with app.app_context():
        group, _, quiz, q1, _ = _world(attempt_limit=2)
        gpid, qpid, x1 = group.public_id, quiz.public_id, q1.public_id
    _login_as(client, "s@example.com")
    with _at(NOW):
        _start(client, gpid, qpid)
    with app.app_context():
        apid = QuizAttempt.query.one().public_id
    with _at(NOW):
        _submit(client, gpid, qpid, apid, x1, confirm=True)
    with _at(NOW + timedelta(minutes=5)):
        _start(client, gpid, qpid)
    with app.app_context():
        numbers = sorted(a.attempt_number for a in QuizAttempt.query.all())
        assert numbers == [1, 2]


def test_a_concurrent_start_that_loses_the_race_returns_the_winners_attempt(app, client):
    """The unique constraint is the final defense; the route re-reads and
    returns the winner rather than reporting a failure."""
    with app.app_context():
        group, student, quiz, _, _ = _world(attempt_limit=3)
        gpid, qpid = group.public_id, quiz.public_id
        quiz_id, student_id, version = quiz.id, student.id, quiz.version
    _login_as(client, "s@example.com")

    real_commit = db.session.commit
    state = {"raced": False}

    def commit_after_race():
        if not state["raced"]:
            state["raced"] = True
            db.session.rollback()
            db.session.add(
                QuizAttempt(
                    quiz_id=quiz_id, student_id=student_id, attempt_number=1,
                    status=IN_PROGRESS, quiz_version=version,
                    started_at=NOW, deadline_at=CLOSES,
                )
            )
            real_commit()
            from sqlalchemy.exc import IntegrityError

            raise IntegrityError("INSERT INTO quiz_attempts", {}, Exception("dup"))
        return real_commit()

    with _at(NOW), patch.object(db.session, "commit", side_effect=commit_after_race):
        resp = _start(client, gpid, qpid, follow=False)
    assert resp.status_code == 302
    with app.app_context():
        assert QuizAttempt.query.count() == 1
        assert QuizAttempt.query.one().public_id in resp.headers["Location"]


def test_start_is_post_only_and_requires_csrf(app):
    csrf_app = __import__("app", fromlist=["create_app"]).create_app(
        "testing", WTF_CSRF_ENABLED=True
    )
    with csrf_app.app_context():
        db.create_all()
        group, _, quiz, _, _ = _world()
        gpid, qpid = group.public_id, quiz.public_id
        csrf_client = csrf_app.test_client()
        login(csrf_client, "s@example.com")
        assert csrf_client.get(_base(gpid, qpid) + "/start").status_code == 405
        assert csrf_client.post(_base(gpid, qpid) + "/start").status_code == 400
        assert QuizAttempt.query.count() == 0
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


# ===========================================================================
# Saving answers
# ===========================================================================


def _started(app, client, **world):
    with app.app_context():
        group, student, quiz, q1, q2 = _world(**world)
        data = (group.public_id, quiz.public_id, q1.public_id, q2.public_id,
                _options(q1), _options(q2))
    _login_as(client, "s@example.com")
    with _at(NOW):
        _start(client, data[0], data[1])
    with app.app_context():
        apid = QuizAttempt.query.one().public_id
    return (*data, apid)


def test_saving_a_single_answer(app, client):
    gpid, qpid, x1, x2, o1, o2, apid = _started(app, client)
    with _at(NOW):
        resp = _answer(client, gpid, qpid, apid, x1, [o1["Paris"]])
    assert "Answer saved" in resp.get_data(as_text=True)
    with app.app_context():
        answer = QuizAnswer.query.one()
        assert [s.option.option_text for s in answer.selections] == ["Paris"]


def test_saving_a_multiple_answer_replaces_the_previous_set_atomically(app, client):
    gpid, qpid, x1, x2, o1, o2, apid = _started(app, client)
    with _at(NOW):
        _answer(client, gpid, qpid, apid, x2, [o2["2"], o2["3"]])
    with app.app_context():
        assert QuizAnswerSelection.query.count() == 2
    with _at(NOW):
        _answer(client, gpid, qpid, apid, x2, [o2["2"], o2["4"]])
    with app.app_context():
        answer = QuizAnswer.query.one()
        assert sorted(s.option.option_text for s in answer.selections) == ["2", "4"]
        assert QuizAnswerSelection.query.count() == 2


@pytest.mark.parametrize("count", [0, 2])
def test_a_single_answer_question_needs_exactly_one_selection(app, client, count):
    gpid, qpid, x1, x2, o1, o2, apid = _started(app, client)
    chosen = [] if count == 0 else [o1["Paris"], o1["Rome"]]
    with _at(NOW):
        resp = _answer(client, gpid, qpid, apid, x1, chosen)
    assert "exactly one answer" in resp.get_data(as_text=True)
    with app.app_context():
        assert QuizAnswer.query.count() == 0


def test_a_multiple_answer_question_needs_at_least_one_selection(app, client):
    gpid, qpid, x1, x2, o1, o2, apid = _started(app, client)
    with _at(NOW):
        resp = _answer(client, gpid, qpid, apid, x2, [])
    assert "at least one answer" in resp.get_data(as_text=True)
    with app.app_context():
        assert QuizAnswer.query.count() == 0


@pytest.mark.parametrize("tamper", ["foreign", "retired", "duplicate", "invented"])
def test_option_tampering_is_refused(app, client, tamper):
    gpid, qpid, x1, x2, o1, o2, apid = _started(app, client)
    if tamper == "foreign":
        chosen = [o2["2"]]                      # another question's option
    elif tamper == "duplicate":
        chosen = [o1["Paris"], o1["Paris"]]
    elif tamper == "invented":
        chosen = ["11111111-2222-3333-4444-555555555555"]
    else:
        with app.app_context():
            option = QuestionOption.query.filter_by(option_text="Rome").one()
            option.is_active = False
            option.retired_at = NOW
            db.session.commit()
            chosen = [option.public_id]
    with _at(NOW):
        resp = _answer(client, gpid, qpid, apid, x1, chosen)
    assert "could not be read" in resp.get_data(as_text=True)
    with app.app_context():
        assert QuizAnswer.query.count() == 0
        assert QuizAnswerSelection.query.count() == 0


def test_a_stale_answer_token_is_refused(app, client):
    gpid, qpid, x1, x2, o1, o2, apid = _started(app, client)
    with _at(NOW):
        token = _token(client, _question_url(gpid, qpid, apid, x1), "answer_state")
    with app.app_context():
        db.session.get(Quiz, QuizAttempt.query.one().quiz_id).version = 9
        db.session.commit()
    with _at(NOW):
        resp = _answer(client, gpid, qpid, apid, x1, [o1["Paris"]], token=token)
    assert "out of date" in resp.get_data(as_text=True)
    with app.app_context():
        assert QuizAnswer.query.count() == 0


def test_an_answer_token_from_another_question_is_refused(app, client):
    gpid, qpid, x1, x2, o1, o2, apid = _started(app, client)
    with _at(NOW):
        other_token = _token(
            client, _question_url(gpid, qpid, apid, x2), "answer_state"
        )
        resp = _answer(client, gpid, qpid, apid, x1, [o1["Paris"]], token=other_token)
    assert "out of date" in resp.get_data(as_text=True)
    with app.app_context():
        assert QuizAnswer.query.count() == 0


def test_answering_and_submitting_require_csrf(app):
    """The attempt is created directly in the database so the answer and
    submit POSTs can be exercised on their own -- starting is blocked by
    CSRF too, which would otherwise make this test vacuous."""
    csrf_app = __import__("app", fromlist=["create_app"]).create_app(
        "testing", WTF_CSRF_ENABLED=True
    )
    with csrf_app.app_context():
        db.create_all()
        group, student, quiz, q1, _ = _world()
        gpid, qpid, x1 = group.public_id, quiz.public_id, q1.public_id
        option_id = _options(q1)["Paris"]
        attempt = QuizAttempt(
            quiz_id=quiz.id, student_id=student.id, attempt_number=1,
            status=IN_PROGRESS, quiz_version=quiz.version,
            started_at=NOW, deadline_at=CLOSES,
        )
        db.session.add(attempt)
        db.session.commit()
        apid = attempt.public_id

        csrf_client = csrf_app.test_client()
        login(csrf_client, "s@example.com")

        with _at(NOW):
            assert csrf_client.post(
                _base(gpid, qpid) + "/start"
            ).status_code == 400
            assert csrf_client.post(
                _question_url(gpid, qpid, apid, x1) + "/answer",
                data={"answer_state": "x", "option": option_id},
            ).status_code == 400
            assert csrf_client.post(
                f"{_base(gpid, qpid)}/attempts/{apid}/submit",
                data={"submit_state": "x"},
            ).status_code == 400

        # Nothing was written by any of the three.
        assert QuizAttempt.query.count() == 1
        assert QuizAttempt.query.one().status == IN_PROGRESS
        assert QuizAnswer.query.count() == 0
        assert QuizAnswerSelection.query.count() == 0
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


# ===========================================================================
# Navigation
# ===========================================================================


def test_previous_and_next_follow_the_authored_order_across_gaps(app, client):
    with app.app_context():
        group, _, quiz, q1, q2 = _world()
        q3 = _question(quiz, "Third", SINGLE, 90, [("A", True), ("B", False)])
        gpid, qpid = group.public_id, quiz.public_id
        x1, x2, x3 = q1.public_id, q2.public_id, q3.public_id
    _login_as(client, "s@example.com")
    with _at(NOW):
        _start(client, gpid, qpid)
    with app.app_context():
        apid = QuizAttempt.query.one().public_id

    with _at(NOW):
        first = client.get(_question_url(gpid, qpid, apid, x1)).get_data(as_text=True)
        middle = client.get(_question_url(gpid, qpid, apid, x2)).get_data(as_text=True)
        last = client.get(_question_url(gpid, qpid, apid, x3)).get_data(as_text=True)
    assert "Question 1 of 3" in first and x2 in first
    assert "Question 2 of 3" in middle and x1 in middle and x3 in middle
    assert "Question 3 of 3" in last and x2 in last
    # Boundaries offer no neighbour beyond the ends.
    assert first.count(">Previous<") == 0
    assert last.count(">Next<") == 0


def test_a_question_from_another_quiz_404s_in_an_attempt(app, client):
    with app.app_context():
        group, student, quiz, q1, _ = _world()
        other = Quiz(
            group_id=group.id, title="Other", instructions="x", version=1,
            created_at=OPENS, updated_at=OPENS, opens_at=OPENS, closes_at=CLOSES,
            status=QuizStatus.PUBLISHED.value, published_at=OPENS,
        )
        db.session.add(other)
        db.session.commit()
        foreign = _question(other, "Foreign", SINGLE, 0, [("A", True), ("B", False)])
        gpid, qpid, fxid = group.public_id, quiz.public_id, foreign.public_id
    _login_as(client, "s@example.com")
    with _at(NOW):
        _start(client, gpid, qpid)
    with app.app_context():
        apid = QuizAttempt.query.one().public_id
    with _at(NOW):
        assert client.get(
            _question_url(gpid, qpid, apid, fxid)
        ).status_code == 404


# ===========================================================================
# Grading -- exact-set matching
# ===========================================================================


@pytest.mark.parametrize(
    "q1_choice,q2_choice,expected",
    [
        (["Paris"], ["2", "4"], 2),      # both exactly right
        (["Rome"], ["2", "4"], 1),       # single wrong
        (["Paris"], ["2"], 1),           # multiple: subset scores zero
        (["Paris"], ["2", "3", "4"], 1),  # multiple: superset scores zero
        (["Paris"], ["3"], 1),           # multiple: wrong set
        (["Rome"], ["3"], 0),            # both wrong
    ],
)
def test_exact_set_grading(app, client, q1_choice, q2_choice, expected):
    gpid, qpid, x1, x2, o1, o2, apid = _started(app, client)
    with _at(NOW):
        _answer(client, gpid, qpid, apid, x1, [o1[c] for c in q1_choice])
        _answer(client, gpid, qpid, apid, x2, [o2[c] for c in q2_choice])
        _submit(client, gpid, qpid, apid, x1)
    with app.app_context():
        attempt = QuizAttempt.query.one()
        assert attempt.status == SUBMITTED
        assert (attempt.correct_count, attempt.total_questions) == (expected, 2)


def test_a_question_whose_every_option_is_correct_grades_on_the_full_set(app, client):
    with app.app_context():
        group, _, quiz, q1, q2 = _world()
        # Make every option of q2 correct.
        for option in QuestionOption.query.filter_by(question_id=q2.id):
            option.is_correct = True
        db.session.commit()
        gpid, qpid, x1, x2 = (group.public_id, quiz.public_id,
                              q1.public_id, q2.public_id)
        o1, o2 = _options(q1), _options(q2)
    _login_as(client, "s@example.com")
    with _at(NOW):
        _start(client, gpid, qpid)
    with app.app_context():
        apid = QuizAttempt.query.one().public_id
    with _at(NOW):
        _answer(client, gpid, qpid, apid, x1, [o1["Paris"]])
        _answer(client, gpid, qpid, apid, x2, [o2["2"], o2["3"], o2["4"]])
        _submit(client, gpid, qpid, apid, x1)
    with app.app_context():
        assert QuizAttempt.query.one().correct_count == 2


def test_unanswered_questions_grade_as_incorrect_after_confirmation(app, client):
    gpid, qpid, x1, x2, o1, o2, apid = _started(app, client)
    with _at(NOW):
        _answer(client, gpid, qpid, apid, x1, [o1["Paris"]])
        # Without confirmation the submission is refused.
        refused = _submit(client, gpid, qpid, apid, x1)
    assert "not answered 1 question" in refused.get_data(as_text=True)
    with app.app_context():
        assert QuizAttempt.query.one().status == IN_PROGRESS
    with _at(NOW):
        _submit(client, gpid, qpid, apid, x1, confirm=True)
    with app.app_context():
        attempt = QuizAttempt.query.one()
        assert attempt.status == SUBMITTED
        assert (attempt.correct_count, attempt.total_questions) == (1, 2)


def test_submission_freezes_the_attempt(app, client):
    gpid, qpid, x1, x2, o1, o2, apid = _started(app, client)
    with _at(NOW):
        _answer(client, gpid, qpid, apid, x1, [o1["Paris"]])
        _submit(client, gpid, qpid, apid, x1, confirm=True)
    with app.app_context():
        before = {
            "selections": QuizAnswerSelection.query.count(),
            "answers": QuizAnswer.query.count(),
            "correct": QuizAttempt.query.one().correct_count,
            "submitted_at": QuizAttempt.query.one().submitted_at,
        }
    # Every further write is refused; the question page redirects to the result.
    with _at(NOW + timedelta(minutes=1)):
        page = client.get(_question_url(gpid, qpid, apid, x1), follow_redirects=False)
        assert page.status_code == 302 and "result" in page.headers["Location"]
        _answer(client, gpid, qpid, apid, x2, [o2["2"], o2["4"]], token="stale")
    with app.app_context():
        assert QuizAnswerSelection.query.count() == before["selections"]
        assert QuizAnswer.query.count() == before["answers"]
        attempt = QuizAttempt.query.one()
        assert attempt.correct_count == before["correct"]
        assert attempt.submitted_at == before["submitted_at"]


def test_a_replayed_submission_returns_the_existing_result_unchanged(app, client):
    gpid, qpid, x1, x2, o1, o2, apid = _started(app, client)
    with _at(NOW):
        _answer(client, gpid, qpid, apid, x1, [o1["Paris"]])
        token = _token(client, _question_url(gpid, qpid, apid, x1), "submit_state")
        _submit(client, gpid, qpid, apid, x1, token=token, confirm=True)
    with app.app_context():
        attempt = QuizAttempt.query.one()
        before = (attempt.submitted_at, attempt.correct_count,
                  attempt.total_questions, attempt.status)
    with _at(NOW + timedelta(minutes=5)):
        resp = _submit(client, gpid, qpid, apid, x1, token=token, confirm=True,
                       follow=False)
    assert resp.status_code == 302 and "result" in resp.headers["Location"]
    with app.app_context():
        attempt = QuizAttempt.query.one()
        assert (attempt.submitted_at, attempt.correct_count,
                attempt.total_questions, attempt.status) == before


# ===========================================================================
# Expiry
# ===========================================================================


def test_an_overdue_attempt_is_finalized_and_graded_on_the_next_request(app, client):
    gpid, qpid, x1, x2, o1, o2, apid = _started(app, client, time_limit=30)
    with _at(NOW):
        _answer(client, gpid, qpid, apid, x1, [o1["Paris"]])

    after = NOW + timedelta(minutes=31)
    with _at(after):
        resp = client.get(_result_url(gpid, qpid, apid))
    assert resp.status_code == 200
    with app.app_context():
        attempt = QuizAttempt.query.one()
        assert attempt.status == EXPIRED
        assert attempt.submitted_at is None
        assert (attempt.correct_count, attempt.total_questions) == (1, 2)


def test_expiry_is_idempotent(app, client):
    gpid, qpid, x1, x2, o1, o2, apid = _started(app, client, time_limit=30)
    after = NOW + timedelta(minutes=31)
    with _at(after):
        client.get(_result_url(gpid, qpid, apid))
    with app.app_context():
        before = (QuizAttempt.query.one().correct_count,
                  QuizAttempt.query.one().total_questions,
                  QuizAttempt.query.one().status)
    with _at(after + timedelta(hours=3)):
        client.get(_result_url(gpid, qpid, apid))
        client.get(_base(gpid, qpid))
    with app.app_context():
        attempt = QuizAttempt.query.one()
        assert (attempt.correct_count, attempt.total_questions,
                attempt.status) == before


def test_a_save_arriving_after_the_deadline_is_refused_and_finalizes(app, client):
    gpid, qpid, x1, x2, o1, o2, apid = _started(app, client, time_limit=30)
    with _at(NOW):
        token = _token(client, _question_url(gpid, qpid, apid, x1), "answer_state")
    with _at(NOW + timedelta(minutes=31)):
        resp = _answer(client, gpid, qpid, apid, x1, [o1["Paris"]], token=token)
    assert "time ran out" in resp.get_data(as_text=True)
    with app.app_context():
        assert QuizAnswer.query.count() == 0
        assert QuizAttempt.query.one().status == EXPIRED


def test_a_new_attempt_may_start_after_an_expiry_while_the_quiz_is_open(app, client):
    gpid, qpid, x1, x2, o1, o2, apid = _started(
        app, client, time_limit=30, attempt_limit=2
    )
    with _at(NOW + timedelta(minutes=31)):
        client.get(_base(gpid, qpid))
        _start(client, gpid, qpid)
    with app.app_context():
        assert QuizAttempt.query.count() == 2
        statuses = sorted(a.status for a in QuizAttempt.query.all())
        assert statuses == [EXPIRED, IN_PROGRESS]


# ===========================================================================
# Answer-key non-disclosure
# ===========================================================================


def test_no_answer_key_leaks_anywhere_in_the_student_surface(app, client):
    gpid, qpid, x1, x2, o1, o2, apid = _started(app, client)
    with _at(NOW):
        _answer(client, gpid, qpid, apid, x1, [o1["Paris"]])
        _answer(client, gpid, qpid, apid, x2, [o2["2"], o2["4"]])
        _submit(client, gpid, qpid, apid, x1)

    with _at(CLOSES + timedelta(days=7)):
        pages = [
            client.get("/student/quizzes"),
            client.get(_base(gpid, qpid)),
            client.get(_result_url(gpid, qpid, apid)),
        ]
    for resp in pages:
        body = resp.get_data(as_text=True)
        for leak in ("is_correct", "correct_option", "answer_key", "Correct answer"):
            assert leak not in body, leak
    # The result page reports right/wrong without any option text at all.
    result = pages[2].get_data(as_text=True)
    for option_text in ("Paris", "Rome", "Madrid", "2", "3", "4"):
        if option_text in ("2", "3", "4"):
            continue  # digits legitimately appear in counts
        assert option_text not in result, option_text


def test_the_question_page_never_marks_which_option_is_correct(app, client):
    gpid, qpid, x1, x2, o1, o2, apid = _started(app, client)
    with _at(NOW):
        body = client.get(_question_url(gpid, qpid, apid, x1)).get_data(as_text=True)
    assert "Paris" in body and "Rome" in body
    for leak in ("is_correct", "Correct answer", "correct=\"true\""):
        assert leak not in body, leak


def test_tokens_carry_no_authored_text_or_answer_key(app, client):
    gpid, qpid, x1, x2, o1, o2, apid = _started(app, client)
    with _at(NOW):
        url = _question_url(gpid, qpid, apid, x1)
        answer_token = _token(client, url, "answer_state")
        submit_token = _token(client, url, "submit_state")
    with app.app_context():
        answer_payload = student_quizzes._load_token(
            answer_token, student_quizzes._ANSWER_SALT,
            student_quizzes._ANSWER_PURPOSE, student_quizzes._ANSWER_FIELDS,
            student_quizzes._ANSWER_FIELDS[1:6],
        )
        submit_payload = student_quizzes._load_token(
            submit_token, student_quizzes._SUBMIT_SALT,
            student_quizzes._SUBMIT_PURPOSE, student_quizzes._SUBMIT_FIELDS,
            student_quizzes._SUBMIT_FIELDS[1:5],
        )
    assert answer_payload is not None and submit_payload is not None
    flat = repr(answer_payload) + repr(submit_payload)
    for secret in ("Paris", "Rome", "Capital of France", "is_correct", "option"):
        assert secret not in flat, secret


def test_authored_text_is_escaped(app, client):
    payload = "<script>alert('xss')</script>"
    with app.app_context():
        group, _, quiz, q1, q2 = _world()
        quiz.title = f"T {payload}"
        q1.prompt = f"P {payload}"
        option = QuestionOption.query.filter_by(question_id=q1.id).first()
        option.option_text = f"O {payload}"
        db.session.commit()
        gpid, qpid, x1 = group.public_id, quiz.public_id, q1.public_id
    _login_as(client, "s@example.com")
    with _at(NOW):
        _start(client, gpid, qpid)
    with app.app_context():
        apid = QuizAttempt.query.one().public_id
    with _at(NOW):
        for url in ("/student/quizzes", _base(gpid, qpid),
                    _question_url(gpid, qpid, apid, x1)):
            body = client.get(url).get_data(as_text=True)
            assert payload not in body, url
            assert "&lt;script&gt;" in body, url


@pytest.mark.parametrize("page", ["list", "detail", "question", "result"])
def test_content_bearing_responses_carry_the_cache_headers(app, client, page):
    gpid, qpid, x1, x2, o1, o2, apid = _started(app, client)
    if page == "result":
        with _at(NOW):
            _submit(client, gpid, qpid, apid, x1, confirm=True)
    url = {
        "list": "/student/quizzes",
        "detail": _base(gpid, qpid),
        "question": _question_url(gpid, qpid, apid, x1),
        "result": _result_url(gpid, qpid, apid),
    }[page]
    with _at(NOW):
        resp = client.get(url)
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in resp.headers.get("Vary", "")


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_unsupported_methods_are_rejected(app, client, method):
    gpid, qpid, x1, x2, o1, o2, apid = _started(app, client)
    for url in ("/student/quizzes", _base(gpid, qpid),
                _base(gpid, qpid) + "/start",
                _question_url(gpid, qpid, apid, x1),
                _question_url(gpid, qpid, apid, x1) + "/answer",
                f"{_base(gpid, qpid)}/attempts/{apid}/submit"):
        assert getattr(client, method)(url).status_code == 405, url


def test_there_is_no_student_delete_or_grade_endpoint(app):
    for rule in app.url_map.iter_rules():
        text = str(rule).lower()
        if not text.startswith("/student") or "quiz" not in text:
            continue
        for forbidden in ("delete", "grade", "override", "answer-key", "reopen"):
            assert forbidden not in text, text
        assert "DELETE" not in rule.methods


# ===========================================================================
# Bounded reads
# ===========================================================================


def test_the_question_page_query_count_does_not_grow_with_the_quiz(app, client):
    """One question at a time: a 30-question quiz costs the same as a
    2-question one."""

    def selects_for(question_count):
        with app.app_context():
            db.drop_all()
            db.create_all()
            group, _, quiz, q1, _ = _world()
            for index in range(2, question_count):
                _question(quiz, f"Q{index}", SINGLE, index,
                          [("A", True), ("B", False)])
            gpid, qpid, x1 = group.public_id, quiz.public_id, q1.public_id
        _login_as(client, "s@example.com")
        with _at(NOW):
            _start(client, gpid, qpid)
        with app.app_context():
            apid = QuizAttempt.query.one().public_id

        statements = []

        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", record)
        try:
            with _at(NOW):
                resp = client.get(_question_url(gpid, qpid, apid, x1))
        finally:
            event.remove(db.engine, "before_cursor_execute", record)
        assert resp.status_code == 200
        return len([s for s in statements if s.strip().upper().startswith("SELECT")])

    small, large = selects_for(2), selects_for(30)
    assert small == large, (small, large)
    assert large <= 20, large


def test_the_student_list_uses_limit_plus_one_and_no_count(app, client):
    with app.app_context():
        group = _hierarchy()
        student = _user("s@example.com", UserRole.STUDENT.value)
        db.session.add(
            Enrollment(student_id=student.id, group_id=group.id,
                       status=EnrollmentStatus.ACTIVE.value)
        )
        db.session.commit()
        for index in range(21):
            db.session.add(
                Quiz(
                    group_id=group.id, title=f"Quiz {index:02d}",
                    instructions="x", version=1, created_at=OPENS, updated_at=OPENS,
                    opens_at=OPENS, closes_at=CLOSES + timedelta(days=index),
                    status=QuizStatus.PUBLISHED.value, published_at=OPENS,
                )
            )
        db.session.commit()
    _login_as(client, "s@example.com")

    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        with _at(NOW):
            first = client.get("/student/quizzes")
    finally:
        event.remove(db.engine, "before_cursor_execute", record)

    assert first.status_code == 200
    assert "page=2" in first.get_data(as_text=True)
    quiz_statements = [s for s in statements if "quizzes" in s.lower()]
    assert quiz_statements, "the list must actually query quizzes"
    # No total-count query over a table that grows: the next-page flag comes
    # from LIMIT PAGE_SIZE + 1. (The shared portal header separately counts
    # unread notifications, which is M14's concern and not this list's.)
    assert not any("COUNT" in s.upper() for s in quiz_statements)
    assert any("LIMIT" in s.upper() for s in quiz_statements)
    with _at(NOW):
        second = client.get("/student/quizzes?page=2")
    assert second.status_code == 200


@pytest.mark.parametrize("bad", ["0", "-3", "abc", "", "99999999", "2.5"])
def test_invalid_page_values_normalize(app, client, bad):
    with app.app_context():
        _world()
    _login_as(client, "s@example.com")
    with _at(NOW):
        assert client.get(f"/student/quizzes?page={bad}").status_code == 200


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


def test_start_locks_the_extended_chain_under_one_reset(app, client):
    with app.app_context():
        group, _, quiz, _, _ = _world()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "s@example.com")
    with _at(NOW):
        events = _capture_locks(lambda: _start(client, gpid, qpid))
    assert events.count("reset") == 1
    assert [e for e in events if e.startswith("lock:")] == [
        "lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group",
        "lock:User", "lock:Enrollment", "lock:Quiz",
    ]


def test_answer_locks_down_to_the_options_and_the_answer_row(app, client):
    gpid, qpid, x1, x2, o1, o2, apid = _started(app, client)
    with _at(NOW):
        token = _token(client, _question_url(gpid, qpid, apid, x1), "answer_state")
        events = _capture_locks(
            lambda: _answer(client, gpid, qpid, apid, x1, [o1["Paris"]], token=token)
        )
    locks = [e for e in events if e.startswith("lock:")]
    assert locks[:7] == [
        "lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group",
        "lock:User", "lock:Enrollment", "lock:Quiz",
    ]
    assert locks[7] == "lock:QuizAttempt"
    assert locks[8] == "lock:QuizQuestion"
    assert locks[9:12] == ["lock:QuestionOption"] * 3
    assert locks[-1] == "lock:QuizAnswer"


def _inject_before_group_lock(action):
    original = student_quizzes.lock_quiz_aggregate

    def side_effect(*args, **kwargs):
        action()
        return original(*args, **kwargs)

    return patch.object(student_quizzes, "lock_quiz_aggregate", side_effect=side_effect)


@pytest.mark.parametrize(
    "change", ["withdraw", "suspend", "demote", "archive_group", "unpublish"]
)
def test_post_lock_rechecks_reject_a_concurrent_change(app, client, change):
    with app.app_context():
        group, student, quiz, _, _ = _world()
        gpid, qpid = group.public_id, quiz.public_id
        student_id, group_id, quiz_id = student.id, group.id, quiz.id
    _login_as(client, "s@example.com")

    def apply_change():
        if change == "withdraw":
            Enrollment.query.filter_by(
                student_id=student_id, group_id=group_id
            ).one().status = EnrollmentStatus.WITHDRAWN.value
        elif change == "suspend":
            db.session.get(User, student_id).status = UserStatus.SUSPENDED.value
        elif change == "demote":
            db.session.get(User, student_id).role = UserRole.RESEARCHER.value
        elif change == "archive_group":
            db.session.get(Group, group_id).status = AcademicStatus.ARCHIVED.value
        else:
            quiz = db.session.get(Quiz, quiz_id)
            quiz.status = QuizStatus.DRAFT.value
            quiz.published_at = None
        db.session.commit()

    with _at(NOW), _inject_before_group_lock(apply_change):
        resp = _start(client, gpid, qpid, follow=False)
    assert resp.status_code == 404
    with app.app_context():
        assert QuizAttempt.query.count() == 0
