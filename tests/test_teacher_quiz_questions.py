"""Teacher multiple-choice question authoring (Phase 4 / M04B): the two
answer modes and their cardinality rules, option retirement, server-owned
ordering, aggregate no-ops, parent/child version behaviour, the three
signed tokens, the lock order, post-lock rechecks, ``IntegrityError``
recovery, CSRF / method / cache-header behaviour, and non-disclosing 404s.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
REPEATABLE READ snapshot isolation, so the concurrency tests here are
**structural**: they assert the *requested* reset and lock order and
exercise the post-lock recheck logic by injecting a state change at a
chosen transaction boundary. They are **not** a demonstration of real
InnoDB blocking. Time is injected rather than waited for.

M04C now provides the Alembic revision for the accepted quiz aggregate.
These route tests still use SQLite and do not claim real MySQL execution.
"""

import re
from datetime import date, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import event

from app.blueprints.teacher import quizzes as quizzes_mod
from app.extensions import db
from app.models import (
    MAX_ACTIVE_OPTIONS,
    MIN_ACTIVE_OPTIONS,
    OPTION_TEXT_MAX_LENGTH,
    QUESTION_PROMPT_MAX_LENGTH,
    AcademicStatus,
    AcademicTerm,
    Course,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    QuestionAnswerMode,
    QuestionOption,
    Quiz,
    QuizQuestion,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password
from tests.conftest import login

PW = "Sup3rSecret!123"

NOW = datetime(2026, 5, 10, 9, 0, 0)
LATER = datetime(2026, 5, 11, 14, 30, 0)

SINGLE = QuestionAnswerMode.SINGLE.value
MULTIPLE = QuestionAnswerMode.MULTIPLE.value


class _Clock:
    """A deterministic replacement for ``utc_reference_now``."""

    def __init__(self, *moments):
        self.moments = list(moments) or [NOW]
        self.calls = 0

    def __call__(self, utc_now=None):
        moment = self.moments[min(self.calls, len(self.moments) - 1)]
        self.calls += 1
        return moment


def _at(*moments):
    return patch.object(quizzes_mod, "utc_reference_now", _Clock(*moments))


# ---------------------------------------------------------------------------
# identity helpers (see the M04A tests for the full rationale)
# ---------------------------------------------------------------------------


def _fresh_identity():
    """Drop Flask-Login's per-app-context user cache.

    The ``app`` fixture keeps ONE app context open for a whole test and
    Flask reuses it per request, so ``flask.g`` -- where Flask-Login caches
    the loaded user -- survives between requests. Without clearing it, a
    second test client silently runs as the FIRST client's user and every
    "two co-teachers" assertion passes vacuously. A fixture artifact, not
    application behaviour.
    """
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
        email=email,
        password_hash=hash_password(PW),
        full_name=email.split("@")[0],
        role=role,
        status=status,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _hierarchy(
    term_status=AcademicStatus.ACTIVE.value,
    level_status=AcademicStatus.ACTIVE.value,
    course_status=AcademicStatus.ACTIVE.value,
    group_status=AcademicStatus.ACTIVE.value,
    group_name="Group A",
    course_title="English",
):
    term = AcademicTerm(
        name=f"Term {group_name}", start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31), status=term_status,
    )
    level = Level(name=f"Level {group_name}", display_order=0, status=level_status)
    db.session.add_all([term, level])
    db.session.commit()
    course = Course(
        title=course_title, level_id=level.id, display_order=0, status=course_status
    )
    db.session.add(course)
    db.session.commit()
    group = Group(
        academic_term_id=term.id, course_id=course.id, name=group_name,
        capacity=20, status=group_status,
    )
    db.session.add(group)
    db.session.commit()
    return group


def _assign(group, teacher, status=GroupTeacherAssignmentStatus.ACTIVE.value):
    row = GroupTeacherAssignment(
        group_id=group.id, teacher_id=teacher.id, status=status
    )
    db.session.add(row)
    db.session.commit()
    return row


def _quiz_row(group, title="Unit 1 check", version=1):
    row = Quiz(
        group_id=group.id, title=title, instructions="Answer every question.",
        version=version, created_at=NOW, updated_at=NOW,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _question_row(quiz, prompt="Capital of France?", mode=SINGLE, order=0, version=1):
    row = QuizQuestion(
        quiz_id=quiz.id, prompt=prompt, answer_mode=mode, display_order=order,
        version=version, created_at=NOW, updated_at=NOW,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _option_row(question, text, order, is_correct=False, is_active=True, retired_at=None):
    row = QuestionOption(
        question_id=question.id, option_text=text, display_order=order,
        is_correct=is_correct, is_active=is_active, retired_at=retired_at,
        created_at=NOW, updated_at=NOW,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _question_with_options(quiz, prompt="Capital of France?", mode=SINGLE, order=0,
                           options=(("Paris", True), ("Rome", False))):
    question = _question_row(quiz, prompt=prompt, mode=mode, order=order)
    for index, (text, correct) in enumerate(options):
        _option_row(question, text, index, is_correct=correct)
    return question


def _setup(email="teacher@example.com", **hkw):
    """(teacher, group, quiz) with an active teaching assignment."""
    teacher = _user(email, UserRole.TEACHER.value)
    group = _hierarchy(**hkw)
    _assign(group, teacher)
    quiz = _quiz_row(group)
    return teacher, group, quiz


# ---------------------------------------------------------------------------
# request helpers
# ---------------------------------------------------------------------------


def _quiz_base(gpid, qpid):
    return f"/teacher/groups/{gpid}/quizzes/{qpid}"


def _detail_url(gpid, qpid):
    return _quiz_base(gpid, qpid)


def _new_url(gpid, qpid):
    return f"{_quiz_base(gpid, qpid)}/questions/new"


def _edit_url(gpid, qpid, xpid):
    return f"{_quiz_base(gpid, qpid)}/questions/{xpid}/edit"


def _move_url(gpid, qpid, xpid, direction):
    return f"{_quiz_base(gpid, qpid)}/questions/{xpid}/{direction}"


_TOKEN_RE = re.compile(r'name="question_state" value="([^"]*)"')

_MOVE_RE = re.compile(
    r'action="[^"]*/questions/(?P<qid>[^/"]+)/(?P<direction>move-up|move-down)"'
    r'[\s\S]{0,600}?name="question_state" value="(?P<token>[^"]*)"'
)


def _token_from(client, url):
    html = client.get(url).get_data(as_text=True)
    match = _TOKEN_RE.search(html)
    return match.group(1) if match else ""


def _move_tokens(client, gpid, qpid):
    """``{(question_public_id, "move-up"|"move-down"): token}`` from the
    rendered detail page."""
    html = client.get(_detail_url(gpid, qpid)).get_data(as_text=True)
    return {
        (m.group("qid"), m.group("direction")): m.group("token")
        for m in _MOVE_RE.finditer(html)
    }


def _options_payload(rows):
    """`rows` is a list of ``(key, text, is_correct)`` triples, in the
    order the browser would submit them."""
    data = {
        "option_key": [row[0] for row in rows],
        "option_text": [row[1] for row in rows],
    }
    correct = [row[0] for row in rows if row[2]]
    if correct:
        # An unchecked checkbox submits nothing at all, so an empty answer
        # key means the field is simply absent -- exactly like a browser.
        data["option_correct"] = correct
    return data


def _new_rows(*specs):
    """``("Paris", True)`` -> ``("new:0", "Paris", True)``."""
    return [(f"new:{index}", text, correct) for index, (text, correct) in enumerate(specs)]


def _create(client, gpid, qpid, token, prompt="Capital of France?", mode=SINGLE,
            rows=None, follow=True, **extra):
    data = {"prompt": prompt, "answer_mode": mode, "question_state": token}
    data.update(_options_payload(rows if rows is not None else _new_rows(("Paris", True), ("Rome", False))))
    data.update(extra)
    return client.post(_new_url(gpid, qpid), data=data, follow_redirects=follow)


def _edit(client, gpid, qpid, xpid, token, prompt="Capital of France?", mode=SINGLE,
          rows=None, follow=True, **extra):
    data = {"prompt": prompt, "answer_mode": mode, "question_state": token}
    data.update(_options_payload(rows or []))
    data.update(extra)
    return client.post(_edit_url(gpid, qpid, xpid), data=data, follow_redirects=follow)


def _rows_from_question(question, **overrides):
    """The submitted rows that would leave `question` exactly as stored."""
    active = (
        QuestionOption.query.filter_by(question_id=question.id, is_active=True)
        .order_by(QuestionOption.display_order, QuestionOption.id)
        .all()
    )
    return [
        (option.public_id, option.option_text, bool(option.is_correct))
        for option in active
    ]


def _active_options(question_id):
    return (
        QuestionOption.query.filter_by(question_id=question_id, is_active=True)
        .order_by(QuestionOption.display_order, QuestionOption.id)
        .all()
    )


# ===========================================================================
# Authorization
# ===========================================================================


def test_anonymous_is_redirected_to_login(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(quiz)
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
    for url in (_new_url(gpid, qpid), _edit_url(gpid, qpid, xpid)):
        resp = client.get(url)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]
    for direction in ("move-up", "move-down"):
        resp = client.post(_move_url(gpid, qpid, xpid, direction))
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]


@pytest.mark.parametrize(
    "role",
    [UserRole.STUDENT.value, UserRole.ADMINISTRATOR.value, UserRole.RESEARCHER.value],
)
def test_non_teacher_roles_are_forbidden(app, client, role):
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(quiz)
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        _user("other@example.com", role)
    _login_as(client, "other@example.com")
    assert client.get(_new_url(gpid, qpid)).status_code == 403
    assert client.get(_edit_url(gpid, qpid, xpid)).status_code == 403
    assert client.post(_move_url(gpid, qpid, xpid, "move-up")).status_code == 403
    with app.app_context():
        assert QuizQuestion.query.count() == 1


@pytest.mark.parametrize("assignment", ["none", "removed"])
def test_unassigned_or_removed_teacher_gets_a_non_disclosing_404(app, client, assignment):
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(quiz, prompt="SECRET-PROMPT")
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        outsider = _user("outsider@example.com", UserRole.TEACHER.value)
        if assignment == "removed":
            _assign(group, outsider, GroupTeacherAssignmentStatus.REMOVED.value)
    _login_as(client, "outsider@example.com")
    for url in (_new_url(gpid, qpid), _edit_url(gpid, qpid, xpid)):
        resp = client.get(url)
        assert resp.status_code == 404, url
        assert b"SECRET-PROMPT" not in resp.data, url
    assert client.post(_move_url(gpid, qpid, xpid, "move-up")).status_code == 404
    assert _create(client, gpid, qpid, "tok", follow=False).status_code == 404
    with app.app_context():
        assert QuizQuestion.query.count() == 1


def test_post_lock_recheck_rejects_a_suspended_or_demoted_actor(app, client):
    with app.app_context():
        teacher, group, quiz = _setup()
        gpid, qpid, tid = group.public_id, quiz.public_id, teacher.id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))

    def suspend():
        db.session.get(User, tid).status = UserStatus.SUSPENDED.value
        db.session.commit()

    with _inject_before_group_lock(suspend):
        resp = _create(client, gpid, qpid, token, follow=False)
    assert resp.status_code == 404
    with app.app_context():
        assert QuizQuestion.query.count() == 0


# ===========================================================================
# Nested ownership -- cross-object tampering, numeric ids
# ===========================================================================


def test_a_question_from_another_quiz_404s_through_this_quizs_url(app, client):
    with app.app_context():
        _, group, mine = _setup()
        theirs = _quiz_row(group, title="Other quiz")
        foreign = _question_with_options(theirs, prompt="Their question")
        gpid, mine_qpid, theirs_qpid = group.public_id, mine.public_id, theirs.public_id
        foreign_xpid = foreign.public_id
    _login_as(client, "teacher@example.com")
    # Legitimate under its OWN quiz...
    assert client.get(_edit_url(gpid, theirs_qpid, foreign_xpid)).status_code == 200
    # ...and never through the other quiz's URL.
    assert client.get(_edit_url(gpid, mine_qpid, foreign_xpid)).status_code == 404
    assert client.post(
        _move_url(gpid, mine_qpid, foreign_xpid, "move-up")
    ).status_code == 404
    with app.app_context():
        assert QuizQuestion.query.one().prompt == "Their question"


def test_a_quiz_from_another_group_404s_through_this_groups_url(app, client):
    with app.app_context():
        teacher, mine, my_quiz = _setup()
        other_group = _hierarchy(group_name="Group B", course_title="Writing")
        _assign(other_group, teacher)
        other_quiz = _quiz_row(other_group, title="Their quiz")
        question = _question_with_options(other_quiz)
        gpid, other_qpid, xpid = mine.public_id, other_quiz.public_id, question.public_id
    _login_as(client, "teacher@example.com")
    assert client.get(_new_url(gpid, other_qpid)).status_code == 404
    assert client.get(_edit_url(gpid, other_qpid, xpid)).status_code == 404


@pytest.mark.parametrize("bogus", ["1", "0", "-1", "9999", "abc"])
def test_a_numeric_or_bogus_question_identifier_404s(app, client, bogus):
    with app.app_context():
        _, group, quiz = _setup()
        _question_with_options(quiz)
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    assert client.get(_edit_url(gpid, qpid, bogus)).status_code == 404
    assert client.post(_move_url(gpid, qpid, bogus, "move-up")).status_code == 404


def test_an_option_from_another_question_is_refused(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        target = _question_with_options(quiz, prompt="Mine", order=0)
        other = _question_with_options(quiz, prompt="Theirs", order=1)
        gpid, qpid, xpid = group.public_id, quiz.public_id, target.public_id
        foreign_option = _active_options(other.id)[0].public_id
        keep = _active_options(target.id)[0].public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _edit_url(gpid, qpid, xpid))
    resp = _edit(
        client, gpid, qpid, xpid, token,
        rows=[(keep, "Paris", True), (foreign_option, "Stolen", False)],
        follow=False,
    )
    assert resp.status_code == 200
    with app.app_context():
        stolen = QuestionOption.query.filter_by(public_id=foreign_option).one()
        assert stolen.question_id == QuizQuestion.query.filter_by(
            public_id=other.public_id
        ).one().id
        assert stolen.option_text != "Stolen"
        assert QuizQuestion.query.filter_by(public_id=xpid).one().version == 1


def test_no_internal_numeric_id_appears_in_question_markup(app, client):
    with app.app_context():
        teacher, group, quiz = _setup()
        question = _question_with_options(quiz)
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        ids = (group.id, quiz.id, question.id, teacher.id,
               _active_options(question.id)[0].id)
    _login_as(client, "teacher@example.com")
    for url in (_detail_url(gpid, qpid), _new_url(gpid, qpid), _edit_url(gpid, qpid, xpid)):
        html = client.get(url).get_data(as_text=True)
        # Structural, deliberately NOT a naive substring search. A UUID
        # public id can legitimately BEGIN with the same digit as a small
        # internal id, so `"/quizzes/1" in html` fires at random against a
        # perfectly correct `/quizzes/183674a7-...` link -- a ~1-in-16
        # false failure per rendered public id, and nothing to do with what
        # this test is about. What the rule actually says is that every
        # id-shaped path segment the page emits is a public id (or a
        # literal route word such as "new"), and that no form value is a
        # bare internal id.
        for segment in re.findall(
            r"/(?:groups|quizzes|questions)/([^\"'/?# ]+)", html
        ):
            assert not segment.isdigit(), (url, segment)
        values = set(re.findall(r'value="([^"]*)"', html))
        for internal in ids:
            assert str(internal) not in values, (url, internal)


# ===========================================================================
# Creating a question
# ===========================================================================


def test_create_a_single_answer_question(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))
    with _at(LATER):
        resp = _create(
            client, gpid, qpid, token, prompt="Capital of France?", mode=SINGLE,
            rows=_new_rows(("Paris", True), ("Rome", False), ("Madrid", False)),
        )
    assert resp.status_code == 200
    with app.app_context():
        question = QuizQuestion.query.one()
        assert question.prompt == "Capital of France?"
        assert question.answer_mode == SINGLE
        assert question.display_order == 0
        assert question.version == 1
        assert question.created_at == LATER and question.updated_at == LATER
        options = _active_options(question.id)
        assert [o.option_text for o in options] == ["Paris", "Rome", "Madrid"]
        assert [o.display_order for o in options] == [0, 1, 2]
        assert [o.is_correct for o in options] == [True, False, False]
        # The parent draft's concurrency signal moved exactly once.
        assert Quiz.query.one().version == 2
        assert Quiz.query.one().updated_at == LATER


def test_create_a_multiple_answer_question(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))
    _create(
        client, gpid, qpid, token, mode=MULTIPLE,
        rows=_new_rows(("Paris", True), ("Lyon", True), ("Rome", False)),
    )
    with app.app_context():
        question = QuizQuestion.query.one()
        assert question.answer_mode == MULTIPLE
        assert sum(1 for o in _active_options(question.id) if o.is_correct) == 2


def test_multiple_mode_permits_every_option_to_be_correct(app, client):
    """No distractor is required: no such business rule was approved."""
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))
    _create(
        client, gpid, qpid, token, mode=MULTIPLE,
        rows=_new_rows(("A", True), ("B", True), ("C", True)),
    )
    with app.app_context():
        question = QuizQuestion.query.one()
        options = _active_options(question.id)
        assert len(options) == 3
        assert all(o.is_correct for o in options)


@pytest.mark.parametrize(
    "mode,rows",
    [
        (SINGLE, [("A", False), ("B", False)]),          # none correct
        (SINGLE, [("A", True), ("B", True)]),            # two correct
        (SINGLE, [("A", True), ("B", True), ("C", True)]),
        (MULTIPLE, [("A", False), ("B", False)]),        # none correct
        (MULTIPLE, [("A", True), ("B", False)]),         # only one correct
    ],
)
def test_answer_cardinality_is_enforced(app, client, mode, rows):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))
    resp = _create(
        client, gpid, qpid, token, mode=mode, rows=_new_rows(*rows), follow=False
    )
    assert resp.status_code == 200
    with app.app_context():
        assert QuizQuestion.query.count() == 0
        assert Quiz.query.one().version == 1


@pytest.mark.parametrize("mode", ["", "true_false", "SINGLE", "short_answer"])
def test_an_unsupported_answer_mode_is_rejected(app, client, mode):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))
    resp = _create(client, gpid, qpid, token, mode=mode, follow=False)
    assert resp.status_code == 200
    with app.app_context():
        assert QuizQuestion.query.count() == 0


@pytest.mark.parametrize("count", [0, 1, 9, 12])
def test_option_count_bounds_are_enforced(app, client, count):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))
    rows = _new_rows(*[(f"Option {i}", i == 0) for i in range(count)])
    resp = _create(client, gpid, qpid, token, rows=rows, follow=False)
    assert resp.status_code == 200
    with app.app_context():
        assert QuizQuestion.query.count() == 0


@pytest.mark.parametrize("count", [MIN_ACTIVE_OPTIONS, 5, MAX_ACTIVE_OPTIONS])
def test_option_counts_inside_the_bounds_are_accepted(app, client, count):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))
    rows = _new_rows(*[(f"Option {i}", i == 0) for i in range(count)])
    _create(client, gpid, qpid, token, rows=rows)
    with app.app_context():
        assert len(_active_options(QuizQuestion.query.one().id)) == count


@pytest.mark.parametrize(
    "field,length,ok",
    [
        ("prompt", QUESTION_PROMPT_MAX_LENGTH, True),
        ("prompt", QUESTION_PROMPT_MAX_LENGTH + 1, False),
        ("option", OPTION_TEXT_MAX_LENGTH, True),
        ("option", OPTION_TEXT_MAX_LENGTH + 1, False),
    ],
)
def test_raw_length_limits_are_enforced(app, client, field, length, ok):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))
    if field == "prompt":
        _create(client, gpid, qpid, token, prompt="x" * length, follow=False)
    else:
        _create(
            client, gpid, qpid, token,
            rows=_new_rows(("x" * length, True), ("Other", False)), follow=False,
        )
    with app.app_context():
        assert QuizQuestion.query.count() == (1 if ok else 0)


def test_length_is_measured_before_trimming(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))
    padded = " " * 60 + "x" * (OPTION_TEXT_MAX_LENGTH - 40) + " " * 60
    assert len(padded) > OPTION_TEXT_MAX_LENGTH
    assert len(padded.strip()) < OPTION_TEXT_MAX_LENGTH
    _create(
        client, gpid, qpid, token,
        rows=_new_rows((padded, True), ("Other", False)), follow=False,
    )
    with app.app_context():
        assert QuizQuestion.query.count() == 0


@pytest.mark.parametrize("blank", ["", "   ", "\n\n", "\t "])
def test_blank_prompt_or_option_is_rejected(app, client, blank):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))
    assert _create(client, gpid, qpid, token, prompt=blank, follow=False).status_code == 200
    assert _create(
        client, gpid, qpid, token,
        rows=_new_rows((blank, True), ("Other", False)), follow=False,
    ).status_code == 200
    with app.app_context():
        assert QuizQuestion.query.count() == 0


@pytest.mark.parametrize(
    "first,second", [("Paris", "Paris"), ("Paris", "  Paris  "), ("Paris", "Paris\n")]
)
def test_duplicate_normalized_option_text_is_rejected(app, client, first, second):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))
    resp = _create(
        client, gpid, qpid, token,
        rows=_new_rows((first, True), (second, False)), follow=False,
    )
    assert resp.status_code == 200
    with app.app_context():
        assert QuizQuestion.query.count() == 0


def test_outer_whitespace_is_trimmed_but_inner_text_is_kept(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))
    _create(
        client, gpid, qpid, token, prompt="  line one\n\n  two  ",
        rows=_new_rows(("  Paris  ", True), (" Rome ", False)),
    )
    with app.app_context():
        question = QuizQuestion.query.one()
        assert question.prompt == "line one\n\n  two"
        assert [o.option_text for o in _active_options(question.id)] == ["Paris", "Rome"]


def test_repeated_option_keys_in_one_request_are_rejected(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))
    resp = _create(
        client, gpid, qpid, token,
        rows=[("new:0", "Paris", True), ("new:0", "Rome", False)], follow=False,
    )
    assert resp.status_code == 200
    with app.app_context():
        assert QuizQuestion.query.count() == 0


def test_a_create_may_not_claim_a_persisted_option(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        existing = _question_with_options(quiz)
        gpid, qpid = group.public_id, quiz.public_id
        stolen = _active_options(existing.id)[0].public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))
    resp = _create(
        client, gpid, qpid, token,
        rows=[("new:0", "Paris", True), (stolen, "Rome", False)], follow=False,
    )
    assert resp.status_code == 200
    with app.app_context():
        assert QuizQuestion.query.count() == 1
        assert QuestionOption.query.filter_by(public_id=stolen).one().option_text == "Paris"


def test_questions_append_in_server_owned_order(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    for index in range(3):
        token = _token_from(client, _new_url(gpid, qpid))
        _create(client, gpid, qpid, token, prompt=f"Q{index}")
    with app.app_context():
        rows = QuizQuestion.query.order_by(QuizQuestion.display_order).all()
        assert [r.prompt for r in rows] == ["Q0", "Q1", "Q2"]
        assert [r.display_order for r in rows] == [0, 1, 2]
        assert Quiz.query.one().version == 4  # 1 + one per creation


def test_forged_ordering_and_identity_fields_have_nowhere_to_land(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        other_quiz = _quiz_row(group, title="Other")
        gpid, qpid, other_id = group.public_id, quiz.public_id, other_quiz.id
        quiz_id = quiz.id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))
    _create(
        client, gpid, qpid, token,
        quiz_id=str(other_id), display_order="99", version="42",
        public_id="forged-public-id", is_active="false", id="4242",
        option_display_order="7", is_correct="true",
    )
    with app.app_context():
        question = QuizQuestion.query.one()
        assert question.quiz_id == quiz_id
        assert question.display_order == 0
        assert question.version == 1
        assert question.public_id != "forged-public-id"
        assert question.id != 4242
        assert [o.display_order for o in _active_options(question.id)] == [0, 1]


# ===========================================================================
# Editing a question
# ===========================================================================


def test_a_meaningful_edit_increments_both_versions_exactly_once(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(quiz)
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        rows = _rows_from_question(question)
        created = question.created_at
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _edit_url(gpid, qpid, xpid))
    with _at(LATER):
        _edit(client, gpid, qpid, xpid, token, prompt="Rewritten?", rows=rows)
    with app.app_context():
        question = QuizQuestion.query.one()
        assert question.prompt == "Rewritten?"
        assert question.version == 2
        assert question.updated_at == LATER
        assert question.created_at == created
        assert Quiz.query.one().version == 2
        assert Quiz.query.one().updated_at == LATER


def test_an_unchanged_save_is_a_complete_no_op(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(quiz)
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        rows = _rows_from_question(question)
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _edit_url(gpid, qpid, xpid))
    with _at(LATER):
        resp = _edit(client, gpid, qpid, xpid, token, rows=rows)
    assert "unchanged, so nothing was saved" in resp.get_data(as_text=True)
    with app.app_context():
        question = QuizQuestion.query.one()
        assert question.version == 1 and question.updated_at == NOW
        assert Quiz.query.one().version == 1 and Quiz.query.one().updated_at == NOW
        for option in _active_options(question.id):
            assert option.updated_at == NOW
        assert QuestionOption.query.filter_by(is_active=False).count() == 0


def test_a_save_that_only_adds_outer_whitespace_is_also_a_no_op(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(quiz)
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        rows = [(pid, f"  {text} ", correct) for pid, text, correct in _rows_from_question(question)]
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _edit_url(gpid, qpid, xpid))
    _edit(client, gpid, qpid, xpid, token, prompt="  Capital of France? ", rows=rows)
    with app.app_context():
        assert QuizQuestion.query.one().version == 1
        assert Quiz.query.one().version == 1


def test_an_option_can_be_added_while_editing(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(quiz)
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        rows = _rows_from_question(question) + [("new:9", "Madrid", False)]
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _edit_url(gpid, qpid, xpid))
    with _at(LATER):
        _edit(client, gpid, qpid, xpid, token, rows=rows)
    with app.app_context():
        question = QuizQuestion.query.one()
        options = _active_options(question.id)
        assert [o.option_text for o in options] == ["Paris", "Rome", "Madrid"]
        assert [o.display_order for o in options] == [0, 1, 2]
        assert options[2].created_at == LATER
        assert question.version == 2


def test_a_removed_option_is_retired_in_place_never_deleted(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(
            quiz, options=(("Paris", True), ("Rome", False), ("Madrid", False))
        )
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        rows = _rows_from_question(question)
        dropped_public_id = rows[2][0]
        keep = rows[:2]
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _edit_url(gpid, qpid, xpid))
    with _at(LATER):
        _edit(client, gpid, qpid, xpid, token, rows=keep)
    with app.app_context():
        retired = QuestionOption.query.filter_by(public_id=dropped_public_id).one()
        # Kept as history: text, answer key, public id, creation time and
        # stored order all survive.
        assert retired.is_active is False
        assert retired.retired_at == LATER
        assert retired.option_text == "Madrid"
        assert retired.display_order == 2
        assert retired.created_at == NOW
        assert QuestionOption.query.count() == 3
        assert len(_active_options(QuizQuestion.query.one().id)) == 2


def test_a_retired_option_is_absent_from_the_editor_and_from_validation(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(quiz)
        retired = _option_row(
            question, "RETIRED-MARKER", 5, is_active=False, retired_at=NOW
        )
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        retired_pid = retired.public_id
        rows = _rows_from_question(question)
    _login_as(client, "teacher@example.com")
    html = client.get(_edit_url(gpid, qpid, xpid)).get_data(as_text=True)
    assert "RETIRED-MARKER" not in html
    assert retired_pid not in html
    # ...and it cannot be resurrected by submitting its identifier.
    token = _token_from(client, _edit_url(gpid, qpid, xpid))
    resp = _edit(
        client, gpid, qpid, xpid, token,
        rows=rows + [(retired_pid, "Back again", False)], follow=False,
    )
    assert resp.status_code == 200
    with app.app_context():
        still_retired = QuestionOption.query.filter_by(public_id=retired_pid).one()
        assert still_retired.is_active is False
        assert still_retired.option_text == "RETIRED-MARKER"


def test_options_can_be_reordered_and_are_renumbered_from_zero(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(
            quiz, options=(("Paris", True), ("Rome", False), ("Madrid", False))
        )
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        rows = _rows_from_question(question)
        reordered = [rows[2], rows[0], rows[1]]
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _edit_url(gpid, qpid, xpid))
    _edit(client, gpid, qpid, xpid, token, rows=reordered)
    with app.app_context():
        options = _active_options(QuizQuestion.query.one().id)
        assert [o.option_text for o in options] == ["Madrid", "Paris", "Rome"]
        assert [o.display_order for o in options] == [0, 1, 2]


def test_an_unchanged_option_row_keeps_its_own_timestamp(app, client):
    """One authoritative moment is used for the rows this request really
    changes -- a row whose text, answer key and order are all identical is
    left completely alone."""
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(quiz)
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        rows = _rows_from_question(question)
        untouched_pid = rows[1][0]
        changed = [(rows[0][0], "Paris (France)", True), rows[1]]
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _edit_url(gpid, qpid, xpid))
    with _at(LATER):
        _edit(client, gpid, qpid, xpid, token, rows=changed)
    with app.app_context():
        assert QuestionOption.query.filter_by(public_id=untouched_pid).one().updated_at == NOW
        assert QuestionOption.query.filter_by(
            option_text="Paris (France)"
        ).one().updated_at == LATER


@pytest.mark.parametrize("correct_count", [2, 3])
def test_multiple_to_single_never_silently_truncates_correct_answers(
    app, client, correct_count
):
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(
            quiz, mode=MULTIPLE,
            options=tuple(
                (f"Option {i}", i < correct_count) for i in range(correct_count + 1)
            ),
        )
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        rows = _rows_from_question(question)
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _edit_url(gpid, qpid, xpid))
    resp = _edit(client, gpid, qpid, xpid, token, mode=SINGLE, rows=rows, follow=False)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "exactly one correct option" in html
    # The attempted values are retained so the teacher can choose.
    assert "Option 0" in html
    with app.app_context():
        question = QuizQuestion.query.one()
        assert question.answer_mode == MULTIPLE
        assert question.version == 1
        assert sum(1 for o in _active_options(question.id) if o.is_correct) == correct_count
        assert Quiz.query.one().version == 1


def test_multiple_to_single_succeeds_once_exactly_one_is_selected(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(
            quiz, mode=MULTIPLE,
            options=(("A", True), ("B", True), ("C", False)),
        )
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        rows = _rows_from_question(question)
        chosen = [(rows[0][0], "A", True), (rows[1][0], "B", False), (rows[2][0], "C", False)]
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _edit_url(gpid, qpid, xpid))
    _edit(client, gpid, qpid, xpid, token, mode=SINGLE, rows=chosen)
    with app.app_context():
        question = QuizQuestion.query.one()
        assert question.answer_mode == SINGLE
        assert [o.is_correct for o in _active_options(question.id)] == [True, False, False]


def test_single_to_multiple_never_selects_another_answer(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(quiz)
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        rows = _rows_from_question(question)
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _edit_url(gpid, qpid, xpid))
    resp = _edit(client, gpid, qpid, xpid, token, mode=MULTIPLE, rows=rows, follow=False)
    assert resp.status_code == 200
    assert "at least two correct options" in resp.get_data(as_text=True)
    with app.app_context():
        question = QuizQuestion.query.one()
        assert question.answer_mode == SINGLE
        assert question.version == 1
        assert sum(1 for o in _active_options(question.id) if o.is_correct) == 1


def test_a_question_with_a_structurally_invalid_option_set_is_refused_safely(app, client):
    """A stored question holding fewer than two active options cannot be
    edited: the editor refuses rather than truncating or inventing."""
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_row(quiz)
        _option_row(question, "Only one", 0, is_correct=True)
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
    _login_as(client, "teacher@example.com")
    resp = client.get(_edit_url(gpid, qpid, xpid), follow_redirects=True)
    assert "cannot safely change" in resp.get_data(as_text=True)
    with app.app_context():
        assert QuestionOption.query.count() == 1
        assert QuizQuestion.query.one().version == 1


def test_escaping_of_prompt_and_option_text(app, client):
    payload = "<script>alert('xss')</script>"
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(
            quiz, prompt=f"P {payload}", options=((f"O {payload}", True), ("Rome", False))
        )
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
    _login_as(client, "teacher@example.com")
    for url in (_detail_url(gpid, qpid), _edit_url(gpid, qpid, xpid)):
        html = client.get(url).get_data(as_text=True)
        assert payload not in html, url
        assert "&lt;script&gt;" in html, url


# ===========================================================================
# Ordering questions
# ===========================================================================


def _make_questions(quiz, count, start_order=0, step=1):
    return [
        _question_with_options(quiz, prompt=f"Q{i}", order=start_order + i * step)
        for i in range(count)
    ]


def _ordered_prompts(quiz_id):
    return [
        q.prompt
        for q in QuizQuestion.query.filter_by(quiz_id=quiz_id)
        .order_by(QuizQuestion.display_order, QuizQuestion.id)
        .all()
    ]


def test_move_down_and_move_up_swap_neighbours(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        questions = _make_questions(quiz, 3)
        gpid, qpid, quiz_id = group.public_id, quiz.public_id, quiz.id
        first_xpid = questions[0].public_id
    _login_as(client, "teacher@example.com")

    tokens = _move_tokens(client, gpid, qpid)
    client.post(
        _move_url(gpid, qpid, first_xpid, "move-down"),
        data={"question_state": tokens[(first_xpid, "move-down")]},
        follow_redirects=True,
    )
    with app.app_context():
        assert _ordered_prompts(quiz_id) == ["Q1", "Q0", "Q2"]
        assert Quiz.query.one().version == 2
        # Both moved rows changed their stored order, so both versions moved.
        assert sorted(q.version for q in QuizQuestion.query.all()) == [1, 2, 2]

    tokens = _move_tokens(client, gpid, qpid)
    client.post(
        _move_url(gpid, qpid, first_xpid, "move-up"),
        data={"question_state": tokens[(first_xpid, "move-up")]},
        follow_redirects=True,
    )
    with app.app_context():
        assert _ordered_prompts(quiz_id) == ["Q0", "Q1", "Q2"]
        assert Quiz.query.one().version == 3


@pytest.mark.parametrize("direction,index", [("move-up", 0), ("move-down", 2)])
def test_a_boundary_move_changes_nothing_and_bumps_no_version(app, client, direction, index):
    with app.app_context():
        _, group, quiz = _setup()
        questions = _make_questions(quiz, 3)
        gpid, qpid, quiz_id = group.public_id, quiz.public_id, quiz.id
        xpid = questions[index].public_id
    _login_as(client, "teacher@example.com")
    tokens = _move_tokens(client, gpid, qpid)
    resp = client.post(
        _move_url(gpid, qpid, xpid, direction),
        data={"question_state": tokens[(xpid, direction)]},
        follow_redirects=True,
    )
    assert "already" in resp.get_data(as_text=True)
    with app.app_context():
        assert _ordered_prompts(quiz_id) == ["Q0", "Q1", "Q2"]
        assert Quiz.query.one().version == 1
        assert all(q.version == 1 for q in QuizQuestion.query.all())


def test_moving_works_across_display_order_gaps(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        questions = _make_questions(quiz, 3, step=50)
        gpid, qpid, quiz_id = group.public_id, quiz.public_id, quiz.id
        xpid = questions[2].public_id
    _login_as(client, "teacher@example.com")
    tokens = _move_tokens(client, gpid, qpid)
    client.post(
        _move_url(gpid, qpid, xpid, "move-up"),
        data={"question_state": tokens[(xpid, "move-up")]},
        follow_redirects=True,
    )
    with app.app_context():
        assert _ordered_prompts(quiz_id) == ["Q0", "Q2", "Q1"]


def test_moving_uses_the_real_neighbour_across_a_page_boundary(app, client):
    """The last question on page 1 and the first on page 2 are genuine
    neighbours in the complete order, so the move must swap them."""
    with app.app_context():
        _, group, quiz = _setup()
        _make_questions(quiz, 21)
        gpid, qpid, quiz_id = group.public_id, quiz.public_id, quiz.id
        last_on_page_one = QuizQuestion.query.filter_by(prompt="Q19").one().public_id
    _login_as(client, "teacher@example.com")
    tokens = _move_tokens(client, gpid, qpid)
    client.post(
        _move_url(gpid, qpid, last_on_page_one, "move-down"),
        data={"question_state": tokens[(last_on_page_one, "move-down")]},
        follow_redirects=True,
    )
    with app.app_context():
        order = _ordered_prompts(quiz_id)
        assert order[19] == "Q20" and order[20] == "Q19"


def test_a_shared_display_order_still_produces_a_real_move(app, client):
    """Two rows can legitimately share a stored order; swapping identical
    values would change nothing, so exactly one row is nudged instead."""
    with app.app_context():
        _, group, quiz = _setup()
        first = _question_with_options(quiz, prompt="Q0", order=0)
        second = _question_with_options(quiz, prompt="Q1", order=0)
        gpid, qpid, quiz_id = group.public_id, quiz.public_id, quiz.id
        second_xpid = second.public_id
    _login_as(client, "teacher@example.com")
    tokens = _move_tokens(client, gpid, qpid)
    client.post(
        _move_url(gpid, qpid, second_xpid, "move-up"),
        data={"question_state": tokens[(second_xpid, "move-up")]},
        follow_redirects=True,
    )
    with app.app_context():
        assert _ordered_prompts(quiz_id) == ["Q1", "Q0"]
        assert Quiz.query.one().version == 2
        # Only the row whose stored order actually changed bumped.
        versions = {q.prompt: q.version for q in QuizQuestion.query.all()}
        assert versions == {"Q0": 2, "Q1": 1}


def test_move_is_post_only(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(quiz)
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
    _login_as(client, "teacher@example.com")
    for direction in ("move-up", "move-down"):
        assert client.get(_move_url(gpid, qpid, xpid, direction)).status_code == 405


# ===========================================================================
# Detail page -- bounded listing
# ===========================================================================


def test_the_detail_page_summarises_each_question(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        _question_with_options(
            quiz, prompt="Single one", mode=SINGLE, order=0,
            options=(("A", True), ("B", False)),
        )
        _question_with_options(
            quiz, prompt="Several answers", mode=MULTIPLE, order=1,
            options=(("A", True), ("B", True), ("C", False)),
        )
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    html = client.get(_detail_url(gpid, qpid)).get_data(as_text=True)
    assert "Single one" in html and "Several answers" in html
    assert "Single answer" in html and "Multiple answers" in html
    assert html.index("Single one") < html.index("Several answers")
    assert "Add Question" in html


def test_the_detail_page_shows_an_empty_state_with_an_add_action(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    html = client.get(_detail_url(gpid, qpid)).get_data(as_text=True)
    assert "No questions yet" in html
    assert "Add Question" in html


def test_the_prompt_preview_is_bounded(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        _question_with_options(quiz, prompt="A" * 400 + "TAIL-MARKER")
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    html = client.get(_detail_url(gpid, qpid)).get_data(as_text=True)
    assert "TAIL-MARKER" not in html
    assert "&hellip;" in html


def test_question_pagination_is_bounded_at_twenty(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        _make_questions(quiz, 21)
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    first = client.get(_detail_url(gpid, qpid)).get_data(as_text=True)
    assert "Q0" in first and "Q19" in first
    assert "Q20" not in first
    assert "page=2" in first

    second = client.get(f"{_detail_url(gpid, qpid)}?page=2").get_data(as_text=True)
    assert "Q20" in second
    assert "Q19" not in second


@pytest.mark.parametrize("bad", ["0", "-3", "abc", "", "99999999", "2.5"])
def test_invalid_question_page_values_normalize_to_page_one(app, client, bad):
    with app.app_context():
        _, group, quiz = _setup()
        _question_with_options(quiz, prompt="Only one")
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    resp = client.get(f"{_detail_url(gpid, qpid)}?page={bad}")
    assert resp.status_code == 200
    assert "Only one" in resp.get_data(as_text=True)


def _detail_page_selects(app, client, question_count):
    """Render the Quiz detail page for a draft holding `question_count`
    questions and return how many SELECTs it took."""
    with app.app_context():
        db.drop_all()
        db.create_all()
        _, group, quiz = _setup()
        for index in range(question_count):
            _question_with_options(
                quiz, prompt=f"Q{index}", order=index,
                options=(("A", True), ("B", False), ("C", False), ("D", False)),
            )
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")

    statements = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", _record)
    try:
        resp = client.get(_detail_url(gpid, qpid))
    finally:
        event.remove(db.engine, "before_cursor_execute", _record)

    assert resp.status_code == 200
    return len([s for s in statements if s.strip().upper().startswith("SELECT")])


def test_the_detail_page_query_count_does_not_grow_with_the_draft(app, client):
    """No N+1: option counts come from ONE grouped query for the whole
    page, no relationship is iterated, and the M04D publication-readiness
    panel is bounded the same way.

    Comparing a 2-question draft with a 20-question one is the real proof
    -- a fixed upper bound alone would pass even if the count crept up.
    """
    small = _detail_page_selects(app, client, 2)
    large = _detail_page_selects(app, client, 20)
    assert small == large, (small, large)
    assert large <= 20, large


@pytest.mark.parametrize("page", ["detail", "new", "edit"])
def test_content_bearing_responses_carry_the_cache_headers(app, client, page):
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(quiz)
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
    _login_as(client, "teacher@example.com")
    url = {
        "detail": _detail_url(gpid, qpid),
        "new": _new_url(gpid, qpid),
        "edit": _edit_url(gpid, qpid, xpid),
    }[page]
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in resp.headers.get("Vary", "")


def test_a_question_form_error_render_still_carries_the_cache_headers(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))
    resp = _create(client, gpid, qpid, token, prompt="", follow=False)
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in resp.headers.get("Vary", "")


# ===========================================================================
# Archived hierarchy -- read-only history
# ===========================================================================


@pytest.mark.parametrize("archived", ["term", "level", "course", "group"])
def test_questions_stay_readable_but_unwritable_under_an_archived_chain(
    app, client, archived
):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy(**{f"{archived}_status": AcademicStatus.ARCHIVED.value})
        _assign(group, teacher)
        quiz = _quiz_row(group)
        question = _question_with_options(quiz, prompt="Historic question")
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        rows = _rows_from_question(question)
    _login_as(client, "teacher@example.com")

    detail = client.get(_detail_url(gpid, qpid))
    assert detail.status_code == 200
    assert "Historic question" in detail.get_data(as_text=True)
    assert "Add Question" not in detail.get_data(as_text=True)
    assert "Read only" in detail.get_data(as_text=True)

    for resp in (
        client.get(_new_url(gpid, qpid), follow_redirects=True),
        client.get(_edit_url(gpid, qpid, xpid), follow_redirects=True),
        _create(client, gpid, qpid, "tok"),
        _edit(client, gpid, qpid, xpid, "tok", rows=rows),
        client.post(
            _move_url(gpid, qpid, xpid, "move-up"), data={"question_state": "tok"},
            follow_redirects=True,
        ),
    ):
        assert "can only be added, edited, or reordered" in resp.get_data(as_text=True)

    with app.app_context():
        question = QuizQuestion.query.one()
        assert question.prompt == "Historic question" and question.version == 1
        assert len(_active_options(question.id)) == 2
        assert Quiz.query.one().version == 1


# ===========================================================================
# Signed tokens
# ===========================================================================


def _mint_create(app, **overrides):
    payload = {
        "purpose": quizzes_mod._QUESTION_CREATE_PURPOSE,
        "teacher_public_id": "t",
        "group_public_id": "g",
        "quiz_public_id": "q",
        "quiz_version": 1,
    }
    payload.update(overrides)
    with app.app_context():
        return quizzes_mod._serializer(quizzes_mod._QUESTION_CREATE_SALT).dumps(payload)


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c"])
def test_a_missing_or_malformed_create_token_is_rejected(app, client, token):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    resp = _create(client, gpid, qpid, token)
    assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert QuizQuestion.query.count() == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"purpose": "quiz-question-edit"},
        {"quiz_version": True},
        {"quiz_version": 0},
        {"quiz_version": "1"},
        {"quiz_version": 1.0},
        {"teacher_public_id": 7},
    ],
)
def test_a_wrong_shaped_create_token_is_rejected(app, client, overrides):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
        tpid = User.query.filter_by(email="teacher@example.com").one().public_id
    base = {
        "teacher_public_id": tpid, "group_public_id": gpid,
        "quiz_public_id": qpid, "quiz_version": 1,
    }
    base.update(overrides)
    _login_as(client, "teacher@example.com")
    resp = _create(client, gpid, qpid, _mint_create(app, **base))
    assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert QuizQuestion.query.count() == 0


def test_a_create_token_with_extra_or_missing_keys_is_rejected(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
        tpid = User.query.filter_by(email="teacher@example.com").one().public_id
        serializer = quizzes_mod._serializer(quizzes_mod._QUESTION_CREATE_SALT)
        extra = serializer.dumps({
            "purpose": quizzes_mod._QUESTION_CREATE_PURPOSE,
            "teacher_public_id": tpid, "group_public_id": gpid,
            "quiz_public_id": qpid, "quiz_version": 1, "admin": True,
        })
        missing = serializer.dumps({"purpose": quizzes_mod._QUESTION_CREATE_PURPOSE})
    _login_as(client, "teacher@example.com")
    for token in (extra, missing):
        assert "changed by someone else" in _create(
            client, gpid, qpid, token
        ).get_data(as_text=True)
    with app.app_context():
        assert QuizQuestion.query.count() == 0


def test_a_token_signed_with_another_m04_salt_is_rejected(app, client):
    """Each M04B purpose has its own dedicated salt: an M04A quiz-edit
    token, and a question-edit token, both carry no authority here."""
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
        tpid = User.query.filter_by(email="teacher@example.com").one().public_id
        payload = {
            "purpose": quizzes_mod._QUESTION_CREATE_PURPOSE,
            "teacher_public_id": tpid, "group_public_id": gpid,
            "quiz_public_id": qpid, "quiz_version": 1,
        }
        foreign = [
            quizzes_mod._serializer(quizzes_mod._QUIZ_EDIT_STATE_SALT).dumps(payload),
            quizzes_mod._serializer(quizzes_mod._QUESTION_EDIT_SALT).dumps(payload),
            quizzes_mod._serializer(quizzes_mod._QUESTION_MOVE_SALT).dumps(payload),
        ]
    _login_as(client, "teacher@example.com")
    for token in foreign:
        assert "changed by someone else" in _create(
            client, gpid, qpid, token
        ).get_data(as_text=True)
    with app.app_context():
        assert QuizQuestion.query.count() == 0


def test_a_create_token_from_another_teacher_is_rejected(app, client):
    with app.app_context():
        first, group, quiz = _setup("first@example.com")
        second = _user("second@example.com", UserRole.TEACHER.value)
        _assign(group, second)
        gpid, qpid = group.public_id, quiz.public_id
        first_id, second_id = first.id, second.id

    first_client, second_client = app.test_client(), app.test_client()
    _login_as(first_client, "first@example.com")
    _assert_authenticated_as(first_client, first_id)
    _login_as(second_client, "second@example.com")
    _assert_authenticated_as(second_client, second_id)

    _fresh_identity()
    stolen = _token_from(first_client, _new_url(gpid, qpid))
    _fresh_identity()
    resp = _create(second_client, gpid, qpid, stolen)
    assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert QuizQuestion.query.count() == 0


def test_a_stale_create_token_is_rejected_after_a_concurrent_change(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid, quiz_id = group.public_id, quiz.public_id, quiz.id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))
    with app.app_context():
        db.session.get(Quiz, quiz_id).version = 2
        db.session.commit()
    resp = _create(client, gpid, qpid, token, prompt="ATTEMPTED-MARKER")
    html = resp.get_data(as_text=True)
    assert "changed by someone else" in html
    assert "ATTEMPTED-MARKER" not in html
    with app.app_context():
        assert QuizQuestion.query.count() == 0


def test_replaying_a_successful_create_is_rejected(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))
    _create(client, gpid, qpid, token, prompt="First")
    resp = _create(client, gpid, qpid, token, prompt="Second")
    assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert QuizQuestion.query.count() == 1
        assert QuizQuestion.query.one().prompt == "First"


def test_an_edit_token_is_invalidated_by_a_changed_option_set(app, client):
    """Two co-teachers can hold matching version expectations while one has
    already retired an option; binding the ordered identifier set makes
    that a detectable change rather than a silent one."""
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(
            quiz, options=(("Paris", True), ("Rome", False), ("Madrid", False))
        )
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        rows = _rows_from_question(question)
        question_id = question.id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _edit_url(gpid, qpid, xpid))

    with app.app_context():
        # A co-teacher retires one option, leaving BOTH versions untouched
        # so only the option-set binding can catch it.
        dropped = QuestionOption.query.filter_by(public_id=rows[2][0]).one()
        dropped.is_active = False
        dropped.retired_at = NOW
        db.session.commit()

    resp = _edit(client, gpid, qpid, xpid, token, prompt="ATTEMPTED-MARKER", rows=rows)
    html = resp.get_data(as_text=True)
    assert "changed by someone else" in html
    assert "ATTEMPTED-MARKER" not in html
    with app.app_context():
        assert QuizQuestion.query.one().prompt == "Capital of France?"
        assert QuizQuestion.query.one().version == 1


def test_an_edit_token_is_invalidated_by_a_question_version_bump(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(quiz)
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        rows = _rows_from_question(question)
        question_id = question.id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _edit_url(gpid, qpid, xpid))
    with app.app_context():
        db.session.get(QuizQuestion, question_id).version = 5
        db.session.commit()
    resp = _edit(client, gpid, qpid, xpid, token, prompt="Nope", rows=rows)
    assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert QuizQuestion.query.one().prompt == "Capital of France?"


def test_replaying_a_successful_edit_is_rejected(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(quiz)
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        rows = _rows_from_question(question)
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _edit_url(gpid, qpid, xpid))
    _edit(client, gpid, qpid, xpid, token, prompt="First edit", rows=rows)
    resp = _edit(client, gpid, qpid, xpid, token, prompt="Second edit", rows=rows)
    assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        question = QuizQuestion.query.one()
        assert question.prompt == "First edit" and question.version == 2


def test_an_ordinary_validation_error_keeps_the_original_edit_token(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(quiz)
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        rows = _rows_from_question(question)
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _edit_url(gpid, qpid, xpid))
    resp = _edit(
        client, gpid, qpid, xpid, token, prompt="", rows=rows, follow=False
    )
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert f'name="question_state" value="{token}"' in html
    with app.app_context():
        assert QuizQuestion.query.one().version == 1


def test_a_move_token_cannot_be_replayed_in_the_other_direction(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        questions = _make_questions(quiz, 3)
        gpid, qpid, quiz_id = group.public_id, quiz.public_id, quiz.id
        xpid = questions[1].public_id
    _login_as(client, "teacher@example.com")
    tokens = _move_tokens(client, gpid, qpid)
    resp = client.post(
        _move_url(gpid, qpid, xpid, "move-down"),
        data={"question_state": tokens[(xpid, "move-up")]},
        follow_redirects=True,
    )
    assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert _ordered_prompts(quiz_id) == ["Q0", "Q1", "Q2"]
        assert Quiz.query.one().version == 1


def test_a_move_token_for_another_question_is_rejected(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        questions = _make_questions(quiz, 3)
        gpid, qpid, quiz_id = group.public_id, quiz.public_id, quiz.id
        first, second = questions[0].public_id, questions[1].public_id
    _login_as(client, "teacher@example.com")
    tokens = _move_tokens(client, gpid, qpid)
    resp = client.post(
        _move_url(gpid, qpid, first, "move-down"),
        data={"question_state": tokens[(second, "move-down")]},
        follow_redirects=True,
    )
    assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert _ordered_prompts(quiz_id) == ["Q0", "Q1", "Q2"]


def test_replaying_a_successful_move_is_rejected(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        questions = _make_questions(quiz, 3)
        gpid, qpid, quiz_id = group.public_id, quiz.public_id, quiz.id
        xpid = questions[0].public_id
    _login_as(client, "teacher@example.com")
    tokens = _move_tokens(client, gpid, qpid)
    token = tokens[(xpid, "move-down")]
    client.post(
        _move_url(gpid, qpid, xpid, "move-down"),
        data={"question_state": token}, follow_redirects=True,
    )
    resp = client.post(
        _move_url(gpid, qpid, xpid, "move-down"),
        data={"question_state": token}, follow_redirects=True,
    )
    assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert _ordered_prompts(quiz_id) == ["Q1", "Q0", "Q2"]
        assert Quiz.query.one().version == 2


# ===========================================================================
# Co-teacher collaboration
# ===========================================================================


def test_every_actively_assigned_co_teacher_may_author_questions(app, client):
    with app.app_context():
        first, group, quiz = _setup("first@example.com")
        second = _user("second@example.com", UserRole.TEACHER.value)
        _assign(group, second)
        gpid, qpid = group.public_id, quiz.public_id
        first_id, second_id = first.id, second.id

    first_client, second_client = app.test_client(), app.test_client()
    _login_as(first_client, "first@example.com")
    _assert_authenticated_as(first_client, first_id)
    _login_as(second_client, "second@example.com")
    _assert_authenticated_as(second_client, second_id)

    _fresh_identity()
    token = _token_from(first_client, _new_url(gpid, qpid))
    _fresh_identity()
    _create(first_client, gpid, qpid, token, prompt="By the first teacher")

    with app.app_context():
        xpid = QuizQuestion.query.one().public_id
        rows = _rows_from_question(QuizQuestion.query.one())

    _fresh_identity()
    edit_token = _token_from(second_client, _edit_url(gpid, qpid, xpid))
    _fresh_identity()
    _edit(
        second_client, gpid, qpid, xpid, edit_token,
        prompt="Edited by the co-teacher", rows=rows,
    )
    with app.app_context():
        question = QuizQuestion.query.one()
        assert question.prompt == "Edited by the co-teacher"
        assert question.version == 2


# ===========================================================================
# CSRF and methods
# ===========================================================================


def test_question_writes_require_csrf(app):
    csrf_app = __import__("app", fromlist=["create_app"]).create_app(
        "testing", WTF_CSRF_ENABLED=True
    )
    with csrf_app.app_context():
        db.create_all()
        _, group, quiz = _setup()
        question = _question_with_options(quiz)
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        csrf_client = csrf_app.test_client()
        login(csrf_client, "teacher@example.com")

        assert csrf_client.post(
            _new_url(gpid, qpid), data={"prompt": "P", "answer_mode": SINGLE}
        ).status_code == 400
        assert csrf_client.post(
            _edit_url(gpid, qpid, xpid), data={"prompt": "P", "answer_mode": SINGLE}
        ).status_code == 400
        assert csrf_client.post(_move_url(gpid, qpid, xpid, "move-up")).status_code == 400

        assert QuizQuestion.query.count() == 1
        assert QuizQuestion.query.one().version == 1
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_unsupported_methods_are_rejected(app, client, method):
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(quiz)
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
    _login_as(client, "teacher@example.com")
    for url in (
        _new_url(gpid, qpid),
        _edit_url(gpid, qpid, xpid),
        _move_url(gpid, qpid, xpid, "move-up"),
    ):
        assert getattr(client, method)(url).status_code == 405, url


# ===========================================================================
# Structural: lock order, single reset, post-lock rechecks
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


_PREFIX = [
    "lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group",
    "lock:User", "lock:GroupTeacherAssignment", "lock:Quiz",
]


def test_create_locks_the_established_prefix_under_one_reset(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))
    events = _capture_locks(lambda: _create(client, gpid, qpid, token))
    assert events.count("reset") == 1
    assert [e for e in events if e.startswith("lock:")] == _PREFIX


def test_edit_locks_the_quiz_then_the_question_then_its_options(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(quiz)
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        rows = _rows_from_question(question)
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _edit_url(gpid, qpid, xpid))
    events = _capture_locks(
        lambda: _edit(client, gpid, qpid, xpid, token, prompt="Changed", rows=rows)
    )
    assert events.count("reset") == 1
    locks = [e for e in events if e.startswith("lock:")]
    assert locks == _PREFIX + ["lock:QuizQuestion", "lock:QuestionOption", "lock:QuestionOption"]


def test_move_locks_the_quiz_then_both_question_rows(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        questions = _make_questions(quiz, 2)
        gpid, qpid = group.public_id, quiz.public_id
        xpid = questions[0].public_id
    _login_as(client, "teacher@example.com")
    tokens = _move_tokens(client, gpid, qpid)
    events = _capture_locks(
        lambda: client.post(
            _move_url(gpid, qpid, xpid, "move-down"),
            data={"question_state": tokens[(xpid, "move-down")]},
            follow_redirects=True,
        )
    )
    assert events.count("reset") == 1
    locks = [e for e in events if e.startswith("lock:")]
    assert locks == _PREFIX + ["lock:QuizQuestion", "lock:QuizQuestion"]


def _inject_before_group_lock(action):
    """Run `action` immediately before the Group row lock -- an exact
    transaction boundary, standing in for a concurrent commit landing in
    the window between the unlocked preview read and the locks."""
    original = quizzes_mod.lock_group_in_open_transaction

    def side_effect(public_id):
        action()
        return original(public_id)

    return patch.object(
        quizzes_mod, "lock_group_in_open_transaction", side_effect=side_effect
    )


def test_post_lock_recheck_rejects_a_concurrently_removed_assignment(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
        row_id = GroupTeacherAssignment.query.one().id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))

    def remove():
        db.session.get(GroupTeacherAssignment, row_id).status = (
            GroupTeacherAssignmentStatus.REMOVED.value
        )
        db.session.commit()

    with _inject_before_group_lock(remove):
        resp = _create(client, gpid, qpid, token, follow=False)
    assert resp.status_code == 404
    with app.app_context():
        assert QuizQuestion.query.count() == 0


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
    token = _token_from(client, _new_url(gpid, qpid))
    model, row_id = target

    def archive():
        db.session.get(model, row_id).status = AcademicStatus.ARCHIVED.value
        db.session.commit()

    with _inject_before_group_lock(archive):
        resp = _create(client, gpid, qpid, token, follow=True)
    assert resp.status_code == 200
    assert "can only be added, edited, or reordered" in resp.get_data(as_text=True)
    with app.app_context():
        assert QuizQuestion.query.count() == 0


def _archive_target(group, entity):
    """``(model, internal_id)`` for the ancestor an archived-chain test
    should archive. Mirrors the mapping the create test above builds
    inline, so the edit and move cases below cover the same four ancestors
    without restating it twice more."""
    return {
        "group": (Group, group.id),
        "term": (AcademicTerm, group.academic_term_id),
        "course": (Course, group.course_id),
        "level": (Level, Course.query.one().level_id),
    }[entity]


def _aggregate_snapshot(quiz_public_id):
    """Everything a rejected question write must leave untouched.

    Deliberately exhaustive rather than a spot check: the parent Quiz's
    version and timestamp, and for every question and option row its
    identity, authored text, answer mode, **stored order**, answer key,
    version, both timestamps and its retirement state. A partial write of
    any kind -- a reordered option, a flipped checkbox, a bumped counter, a
    moved timestamp, an option retired but not replaced -- changes this
    value.
    """
    quiz = Quiz.query.filter_by(public_id=quiz_public_id).one()
    questions = (
        QuizQuestion.query.filter_by(quiz_id=quiz.id)
        .order_by(QuizQuestion.display_order, QuizQuestion.id)
        .all()
    )
    return {
        "quiz": (quiz.version, quiz.updated_at),
        "questions": [
            (
                q.public_id, q.prompt, q.answer_mode, q.display_order,
                q.version, q.created_at, q.updated_at,
            )
            for q in questions
        ],
        "options": [
            (
                o.public_id, o.question_id, o.option_text, o.display_order,
                bool(o.is_correct), bool(o.is_active), o.retired_at,
                o.created_at, o.updated_at,
            )
            for o in QuestionOption.query.order_by(QuestionOption.id).all()
        ],
    }


@pytest.mark.parametrize("entity", ["group", "term", "level", "course"])
def test_post_lock_archived_ancestor_rejects_a_question_edit(app, client, entity):
    """The archived-chain rejection on the EDIT path states the question
    rule, not the quiz one, and writes nothing at all.

    The submission used here is otherwise completely valid -- it rewrites
    the prompt, retires one option, reorders the survivors, moves the
    answer key and adds a new option -- so the rejection is provably the
    post-lock operational check rather than a validation failure, and a
    leak of any one of those changes would show up in the snapshot.
    """
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(
            quiz, options=(("Paris", True), ("Rome", False), ("Madrid", False))
        )
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        rows = _rows_from_question(question)
        model, row_id = _archive_target(group, entity)
        before = _aggregate_snapshot(qpid)
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _edit_url(gpid, qpid, xpid))

    def archive():
        db.session.get(model, row_id).status = AcademicStatus.ARCHIVED.value
        db.session.commit()

    attempted = [
        rows[1],
        (rows[0][0], "Paris (France)", False),
        ("new:7", "Berlin", True),
    ]
    with _inject_before_group_lock(archive):
        resp = _edit(
            client, gpid, qpid, xpid, token,
            prompt="Never lands", rows=attempted, follow=True,
        )
    assert resp.status_code == 200
    assert "can only be added, edited, or reordered" in resp.get_data(as_text=True)
    with app.app_context():
        assert _aggregate_snapshot(qpid) == before
        # Spelled out as well as snapshotted, so a failure says what broke.
        assert QuestionOption.query.filter_by(is_active=False).count() == 0
        assert QuestionOption.query.count() == 3
        assert QuizQuestion.query.one().version == 1
        assert Quiz.query.one().version == 1


@pytest.mark.parametrize("entity", ["group", "term", "level", "course"])
def test_post_lock_archived_ancestor_rejects_a_question_move(app, client, entity):
    """The archived-chain rejection on the MOVE path states the question
    rule too, and leaves the authored order and both counters alone."""
    with app.app_context():
        _, group, quiz = _setup()
        _make_questions(quiz, 3)
        gpid, qpid, quiz_id = group.public_id, quiz.public_id, quiz.id
        model, row_id = _archive_target(group, entity)
        xpid = QuizQuestion.query.filter_by(prompt="Q0").one().public_id
        before = _aggregate_snapshot(qpid)
    _login_as(client, "teacher@example.com")
    tokens = _move_tokens(client, gpid, qpid)

    def archive():
        db.session.get(model, row_id).status = AcademicStatus.ARCHIVED.value
        db.session.commit()

    with _inject_before_group_lock(archive):
        resp = client.post(
            _move_url(gpid, qpid, xpid, "move-down"),
            data={"question_state": tokens[(xpid, "move-down")]},
            follow_redirects=True,
        )
    assert resp.status_code == 200
    assert "can only be added, edited, or reordered" in resp.get_data(as_text=True)
    with app.app_context():
        assert _aggregate_snapshot(qpid) == before
        assert _ordered_prompts(quiz_id) == ["Q0", "Q1", "Q2"]
        assert all(q.version == 1 for q in QuizQuestion.query.all())
        assert Quiz.query.one().version == 1


def test_the_quiz_paths_keep_their_own_archived_chain_wording(app, client):
    """The question wording must not have leaked into the M04A Quiz paths:
    the two surfaces share one check but state genuinely different rules."""
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
        group_id = group.id
    _login_as(client, "teacher@example.com")
    # The M04A quiz form carries its own `edit_state` hidden field, not the
    # M04B `question_state` one, so the shared token helper does not apply.
    quiz_edit_url = f"/teacher/groups/{gpid}/quizzes/{qpid}/edit"
    quiz_token = re.search(
        r'name="edit_state" value="([^"]*)"',
        client.get(quiz_edit_url).get_data(as_text=True),
    ).group(1)

    def archive():
        db.session.get(Group, group_id).status = AcademicStatus.ARCHIVED.value
        db.session.commit()

    with _inject_before_group_lock(archive):
        resp = client.post(
            quiz_edit_url,
            data={
                "title": "Renamed", "instructions": "Rewritten.",
                "edit_state": quiz_token,
            },
            follow_redirects=True,
        )
    html = resp.get_data(as_text=True)
    assert "can only be created or edited" in html
    assert "Existing drafts stay readable." in html
    assert "can only be added, edited, or reordered" not in html
    with app.app_context():
        assert Quiz.query.one().title == "Unit 1 check"
        assert Quiz.query.one().version == 1


def test_post_lock_recheck_catches_a_quiz_version_bump_in_the_window(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid, quiz_id = group.public_id, quiz.public_id, quiz.id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))

    def bump():
        db.session.get(Quiz, quiz_id).version = 9
        db.session.commit()

    with _inject_before_group_lock(bump):
        resp = _create(client, gpid, qpid, token, prompt="Raced")
    assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert QuizQuestion.query.count() == 0


def test_post_lock_recheck_catches_a_question_removed_in_the_window(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(quiz)
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        rows = _rows_from_question(question)
        question_id = question.id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _edit_url(gpid, qpid, xpid))

    def remove_question():
        QuestionOption.query.filter_by(question_id=question_id).delete()
        db.session.delete(db.session.get(QuizQuestion, question_id))
        db.session.commit()

    with _inject_before_group_lock(remove_question):
        resp = _edit(client, gpid, qpid, xpid, token, prompt="Ghost", rows=rows, follow=False)
    assert resp.status_code == 404
    with app.app_context():
        assert QuizQuestion.query.count() == 0


def test_post_lock_recheck_catches_a_corrupted_option_cardinality(app, client):
    """A co-teacher retires options down to one between the preview and
    the locks: the editor refuses safely rather than saving against a set
    it cannot validate."""
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(quiz)
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        rows = _rows_from_question(question)
        question_id = question.id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _edit_url(gpid, qpid, xpid))

    def retire_one():
        option = (
            QuestionOption.query.filter_by(question_id=question_id, is_active=True)
            .order_by(QuestionOption.display_order.desc())
            .first()
        )
        option.is_active = False
        option.retired_at = NOW
        db.session.commit()

    with _inject_before_group_lock(retire_one):
        resp = _edit(client, gpid, qpid, xpid, token, prompt="Nope", rows=rows)
    assert "cannot safely change" in resp.get_data(as_text=True)
    with app.app_context():
        assert QuizQuestion.query.one().prompt == "Capital of France?"
        assert QuizQuestion.query.one().version == 1


# ===========================================================================
# IntegrityError -- rollback, generic message, fresh authorization
# ===========================================================================


def _fail_commit_then(action=None):
    from sqlalchemy.exc import IntegrityError

    real_rollback = db.session.rollback
    real_commit = db.session.commit

    def side_effect():
        real_rollback()
        if action is not None:
            action()
            real_commit()
        raise IntegrityError(
            "INSERT INTO quiz_questions (quiz_id, prompt) VALUES (?, ?)",
            {"quiz_id": 1, "prompt": "x"},
            Exception("UNIQUE constraint failed: quiz_questions.public_id"),
        )

    return patch.object(db.session, "commit", side_effect=side_effect)


def test_create_integrity_error_rolls_back_and_reports_generically(app, client):
    with app.app_context():
        _, group, quiz = _setup()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _new_url(gpid, qpid))
    with _fail_commit_then():
        resp = _create(client, gpid, qpid, token, follow=False)
    html = resp.get_data(as_text=True)
    assert "could not be saved" in html
    for leak in ("INSERT INTO", "IntegrityError", "UNIQUE constraint", "quiz_questions."):
        assert leak not in html, leak
    with app.app_context():
        assert QuizQuestion.query.count() == 0
        assert Quiz.query.one().version == 1


@pytest.mark.parametrize("change", ["unassign", "suspend", "demote"])
def test_integrity_recovery_re_authorizes_and_404s_when_access_ended(app, client, change):
    with app.app_context():
        teacher, group, quiz = _setup()
        question = _question_with_options(quiz)
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
        rows = _rows_from_question(question)
        tid, gid = teacher.id, group.id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _edit_url(gpid, qpid, xpid))

    def revoke():
        if change == "unassign":
            GroupTeacherAssignment.query.filter_by(
                group_id=gid, teacher_id=tid
            ).one().status = GroupTeacherAssignmentStatus.REMOVED.value
        elif change == "suspend":
            db.session.get(User, tid).status = UserStatus.SUSPENDED.value
        else:
            db.session.get(User, tid).role = UserRole.RESEARCHER.value

    with _fail_commit_then(revoke):
        resp = _edit(
            client, gpid, qpid, xpid, token, prompt="Never lands", rows=rows, follow=False
        )
    assert resp.status_code == 404
    with app.app_context():
        question = QuizQuestion.query.one()
        assert question.prompt == "Capital of France?" and question.version == 1


# ===========================================================================
# Surface boundaries
# ===========================================================================


def test_every_question_route_is_group_and_quiz_scoped(app):
    """Phase 4 / M04D added the Student answer route, which is nested one
    level deeper still -- under the attempt. Phase 4 / M05 added the
    Listening surface, whose questions are the *same* QuizQuestion rows
    reached through the Listening activity's own public identifier.

    Every route that names a question is still scoped by a Group public
    identifier **and** the public identifier of the object that owns the
    question -- the Quiz on the quiz surface, the Listening activity on
    the listening one. The assertion stays exhaustive: a question route
    that fitted none of these four prefixes would fail here."""
    rules = [
        str(r) for r in app.url_map.iter_rules() if "question" in str(r).lower()
    ]
    assert rules, "the question routes must exist"
    prefixes = (
        "/teacher/groups/<group_public_id>/quizzes/<quiz_public_id>/questions",
        "/student/groups/<group_public_id>/quizzes/<quiz_public_id>"
        "/attempts/<attempt_public_id>/questions",
        "/teacher/groups/<group_public_id>/listening/<listening_public_id>/questions",
        "/student/groups/<group_public_id>/listening/<listening_public_id>"
        "/attempts/<attempt_public_id>/questions",
    )
    for rule in rules:
        assert any(rule.startswith(prefix) for prefix in prefixes), rule
    for prefix in prefixes:
        assert any(r.startswith(prefix) for r in rules), prefix


def test_there_is_no_grade_override_or_answer_key_endpoint(app):
    """M04D added publication, attempts, submission and results, so those
    words are no longer forbidden path segments. Deleting, archiving,
    manual or re-grading, overriding a score and releasing the answer key
    remain absent, and no route accepts DELETE."""
    for rule in app.url_map.iter_rules():
        text = str(rule).lower()
        if "quiz" not in text:
            continue
        for forbidden in (
            "delete", "remove", "archive", "grade", "regrade", "override",
            "answer-key", "release", "duplicate", "toggle",
        ):
            assert forbidden not in text, (text, forbidden)
        assert "DELETE" not in rule.methods
        assert rule.methods <= {"GET", "HEAD", "OPTIONS", "POST"}


def test_a_student_cannot_reach_a_question_anywhere(app, client):
    from app.models import Enrollment, EnrollmentStatus
    from tests.conftest import make_user

    with app.app_context():
        _, group, quiz = _setup()
        student = make_user("s@example.com", UserRole.STUDENT.value)
        db.session.add(
            Enrollment(
                student_id=student.id, group_id=group.id,
                status=EnrollmentStatus.ACTIVE.value,
            )
        )
        db.session.commit()
        question = _question_with_options(
            quiz, prompt="INVISIBLE-QUESTION-MARKER",
            options=(("INVISIBLE-OPTION-MARKER", True), ("Other", False)),
        )
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id

    _login_as(client, "s@example.com")
    for url in (
        "/student/dashboard",
        "/student/assignments",
        "/student/search?q=INVISIBLE",
        f"/student/groups/{gpid}/units",
    ):
        resp = client.get(url)
        assert b"INVISIBLE-QUESTION-MARKER" not in resp.data, url
        assert b"INVISIBLE-OPTION-MARKER" not in resp.data, url
    assert client.get(_detail_url(gpid, qpid)).status_code == 403
    assert client.get(_edit_url(gpid, qpid, xpid)).status_code == 403
    assert client.post(_move_url(gpid, qpid, xpid, "move-up")).status_code == 403


def test_the_answer_key_never_reaches_a_signed_token(app, client):
    """Tokens are authenticated, not encrypted: no authored text and no
    answer key may appear in one."""
    with app.app_context():
        _, group, quiz = _setup()
        question = _question_with_options(
            quiz, prompt="SECRET-PROMPT",
            options=(("SECRET-CORRECT", True), ("SECRET-WRONG", False)),
        )
        gpid, qpid, xpid = group.public_id, quiz.public_id, question.public_id
    _login_as(client, "teacher@example.com")
    token = _token_from(client, _edit_url(gpid, qpid, xpid))
    with app.app_context():
        payload = quizzes_mod._load_question_edit_token(token)
    assert payload is not None
    flat = repr(payload)
    for secret in ("SECRET-PROMPT", "SECRET-CORRECT", "SECRET-WRONG"):
        assert secret not in flat, secret
    assert "is_correct" not in flat
