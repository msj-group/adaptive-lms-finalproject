"""Phase 4 / M13 -- the Student completion routes, the Lesson page and
outline progress state, signed-token rejection, non-disclosing denial,
Student-to-Student isolation, and the one GET that writes.
"""

import re
import uuid

import pytest
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy.exc import OperationalError

import tests.lesson_progress_fixtures as fx
from app import create_app
from app.extensions import db
from app.models import AcademicTerm, Course, Group, Lesson, Level, Unit, User
from app.services import lesson_progress_tokens as tokens
from app.services import lesson_progress_transactions as progress_tx


def _html(response):
    return response.get_data(as_text=True)


def _standard(label="A"):
    student, _teacher, group = fx.classroom(label)
    unit = fx.unit(group)
    first = fx.lesson(unit, "Greetings", 0)
    second = fx.lesson(unit, "Numbers", 1)
    ids = fx.lesson_ids(student, group, unit, first)
    ids["lesson2"], ids["lp2"] = second.id, second.public_id
    ids["enrollment"] = fx.enrollment_of(group, student).id
    return ids


def _login(client):
    fx.login_as(client, "student@example.com")


def _second_lesson(ids):
    return dict(ids, lesson=ids["lesson2"], lp=ids["lp2"])


def _location(response):
    return response.headers["Location"]


# ===========================================================================
# Role guard and methods
# ===========================================================================


def test_anonymous_actions_follow_the_login_redirect(app, client):
    with app.app_context():
        ids = _standard()
    for action in ("complete", "undo"):
        response = client.post(fx.action_url(action, ids["gp"], ids["up"], ids["lp"]))
        assert response.status_code == 302
        assert "/auth/login" in _location(response)
    with app.app_context():
        assert fx.rows() == []


@pytest.mark.parametrize("role", [fx.TEACHER, fx.ADMIN, fx.RESEARCHER])
def test_every_other_role_is_forbidden(app, client, role):
    with app.app_context():
        ids = _standard()
        fx.user(f"{role}-other@example.com", role)
    fx.login_as(client, f"{role}-other@example.com")
    for action in ("complete", "undo"):
        assert client.post(fx.action_url(action, ids["gp"], ids["up"], ids["lp"])).status_code == 403
    assert client.get(fx.lesson_url(ids["gp"], ids["up"], ids["lp"])).status_code == 403
    with app.app_context():
        assert fx.rows() == []


def test_the_actions_are_post_only(app, client):
    with app.app_context():
        ids = _standard()
    _login(client)
    for action in ("complete", "undo"):
        assert client.get(fx.action_url(action, ids["gp"], ids["up"], ids["lp"])).status_code == 405
    with app.app_context():
        assert all(row[5] is None for row in fx.rows())


def _csrf_from(html):
    tag = re.search(r'<input[^>]*name="csrf_token"[^>]*>', html).group(0)
    return re.search(r'value="([^"]+)"', tag).group(1)


def test_csrf_is_enforced_when_enabled(tmp_path):
    app = create_app("testing", WTF_CSRF_ENABLED=True)
    with app.app_context():
        db.create_all()
        try:
            ids = _standard()
            client = app.test_client()
            login_page = _html(client.get("/auth/login"))
            client.post("/auth/login", data={
                "email": "student@example.com", "password": fx.PW,
                "csrf_token": _csrf_from(login_page),
            })
            page = _html(client.get(fx.lesson_url(ids["gp"], ids["up"], ids["lp"])))
            token = fx.state_from(page)
            url = fx.complete_url(ids["gp"], ids["up"], ids["lp"])
            assert client.post(url, data={"progress_state": token}).status_code == 400
            assert fx.progress_of(ids["student"], ids["group"], ids["lesson"]).completed_at is None
            response = client.post(url, data={"progress_state": token,
                                              "csrf_token": _csrf_from(page)})
            assert response.status_code == 302
            assert fx.progress_of(ids["student"], ids["group"], ids["lesson"]).completed_at
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


# ===========================================================================
# The Lesson page and the completion workflow
# ===========================================================================


def test_the_lesson_page_offers_exactly_one_action_for_each_state(app, client):
    with app.app_context():
        ids = _standard()
    _login(client)
    url = fx.lesson_url(ids["gp"], ids["up"], ids["lp"])
    response = client.get(url)
    html = _html(response)
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in response.headers["Vary"]
    assert "Not completed" in html and "Mark as complete" in html
    assert f'action="{fx.complete_url(ids["gp"], ids["up"], ids["lp"])}"' in html
    assert "Undo completion" not in html
    assert fx.state_from(html)

    assert fx.post_action(client, "complete", ids).status_code == 302
    html = _html(client.get(url))
    assert ">Completed</span>" in html and "Undo completion" in html
    assert f'action="{fx.undo_url(ids["gp"], ids["up"], ids["lp"])}"' in html
    assert "Mark as complete" not in html


def test_completing_and_undoing_through_the_page(app, client):
    with app.app_context():
        ids = _standard()
    _login(client)
    response = fx.post_action(client, "complete", ids)
    assert response.status_code == 302
    assert _location(response).endswith(fx.lesson_url(ids["gp"], ids["up"], ids["lp"]) + "#progress")
    assert "Lesson marked as complete." in _html(client.get(_location(response)))
    with app.app_context():
        row = fx.progress_of(ids["student"], ids["group"], ids["lesson"])
        assert row.completed_at is not None and row.version == 2

    response = fx.post_action(client, "undo", ids)
    assert "Completion undone." in _html(client.get(_location(response)))
    with app.app_context():
        row = fx.progress_of(ids["student"], ids["group"], ids["lesson"])
        assert row.completed_at is None and row.version == 3


def test_a_duplicate_submission_is_harmless(app, client):
    with app.app_context():
        ids = _standard()
    _login(client)
    token = fx.page_state(client, ids)
    fx.post_action(client, "complete", ids, token)
    with app.app_context():
        before = fx.rows()
    response = fx.post_action(client, "complete", ids, token)
    assert "already marked as complete. Nothing was changed." in _html(client.get(_location(response)))
    with app.app_context():
        assert fx.rows() == before


def test_a_form_opened_before_later_changes_is_stale_and_writes_nothing(app, client):
    with app.app_context():
        ids = _standard()
    _login(client)
    old_complete = fx.page_state(client, ids)
    fx.post_action(client, "complete", ids)
    old_undo = fx.page_state(client, ids)
    fx.post_action(client, "undo", ids)
    fx.post_action(client, "complete", ids)
    with app.app_context():
        before = fx.rows()
        assert before[0][7] == 4
    response = fx.post_action(client, "undo", ids, old_undo)
    assert "changed after the page was opened" in _html(client.get(_location(response)))
    fx.post_action(client, "undo", ids)
    with app.app_context():
        before = fx.rows()
    response = fx.post_action(client, "complete", ids, old_complete)
    assert "changed after the page was opened" in _html(client.get(_location(response)))
    with app.app_context():
        assert fx.rows() == before


def _foreign_token(ids, **overrides):
    payload = {
        "purpose": "lesson-progress",
        "actor_public_id": ids["student_public_id"],
        "group_public_id": ids["gp"],
        "unit_public_id": ids["up"],
        "lesson_public_id": ids["lp"],
        "action": "complete",
        "progress_version": 1,
    }
    salt = overrides.pop("_salt", "lesson-progress.completion.phase4-m13.v1")
    drop = overrides.pop("_drop", None)
    payload.update(overrides)
    if drop:
        payload.pop(drop)
    from flask import current_app
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=salt).dumps(payload)


_FORGED = [
    "missing", "garbage", "tampered", "discussion_salt", "extra_field", "missing_field",
    "string_version", "zero_version", "other_student", "other_group", "other_unit",
    "other_lesson", "other_action", "overlong",
]


@pytest.mark.parametrize("case", _FORGED)
def test_forged_missing_and_mismatched_tokens_write_nothing(app, client, case):
    with app.app_context():
        ids = _standard()
        other = fx.user("classmate@example.com", fx.STUDENT)
        genuine = _foreign_token(ids)
        token = {
            "missing": False,
            "garbage": "not-a-token",
            "tampered": genuine[:-3] + ("aaa" if not genuine.endswith("aaa") else "bbb"),
            "discussion_salt": _foreign_token(ids, _salt="discussions.reply.phase4-m12.v1"),
            "extra_field": _foreign_token(ids, nonce="x"),
            "missing_field": _foreign_token(ids, _drop="unit_public_id"),
            "string_version": _foreign_token(ids, progress_version="1"),
            "zero_version": _foreign_token(ids, progress_version=0),
            "other_student": _foreign_token(ids, actor_public_id=other.public_id),
            "other_group": _foreign_token(ids, group_public_id=str(uuid.uuid4())),
            "other_unit": _foreign_token(ids, unit_public_id=str(uuid.uuid4())),
            "other_lesson": _foreign_token(ids, lesson_public_id=ids["lp2"]),
            "other_action": _foreign_token(ids, action="undo"),
            "overlong": "a" * 2000,
        }[case]
    _login(client)
    with app.app_context():
        client.get(fx.lesson_url(ids["gp"], ids["up"], ids["lp"]))
        before = fx.rows()
    response = fx.post_action(client, "complete", ids, token)
    assert response.status_code == 302
    assert "could not be verified" in _html(client.get(_location(response)))
    with app.app_context():
        after = fx.rows()
        assert [row[5] for row in after] == [row[5] for row in before]
        assert [row[7] for row in after] == [row[7] for row in before]


def test_an_expired_token_writes_nothing(app, client, monkeypatch):
    with app.app_context():
        ids = _standard()
    _login(client)
    token = fx.page_state(client, ids)
    monkeypatch.setattr(tokens, "TOKEN_MAX_AGE_SECONDS", -1)
    response = fx.post_action(client, "complete", ids, token)
    assert "could not be verified" in _html(client.get(_location(response)))
    with app.app_context():
        assert fx.progress_of(ids["student"], ids["group"], ids["lesson"]).completed_at is None


def test_the_token_binds_every_field_and_carries_no_internal_id(app, client):
    with app.app_context():
        ids = _standard()
        token = tokens.make_progress_token(
            ids["student_public_id"], ids["gp"], ids["up"], ids["lp"], "complete", 1
        )
        args = [ids["student_public_id"], ids["gp"], ids["up"], ids["lp"], "complete"]
        assert tokens.load_progress_token(token, *args)["progress_version"] == 1
        for position in range(len(args)):
            wrong = list(args)
            wrong[position] = "undo" if position == 4 else "x"
            assert tokens.load_progress_token(token, *wrong) is None
        with pytest.raises(ValueError):
            tokens.make_progress_token(*args[:4], "delete", 1)
    _login(client)
    page_token = fx.page_state(client, ids)
    with app.app_context():
        from flask import current_app
        payload = URLSafeTimedSerializer(
            current_app.config["SECRET_KEY"], salt="lesson-progress.completion.phase4-m13.v1"
        ).loads(page_token)
    assert payload == {
        "purpose": "lesson-progress",
        "actor_public_id": ids["student_public_id"],
        "group_public_id": ids["gp"],
        "unit_public_id": ids["up"],
        "lesson_public_id": ids["lp"],
        "action": "complete",
        "progress_version": 1,
    }


# ===========================================================================
# Denial is the same 404, and writes nothing
# ===========================================================================


def _deny(ids, case):
    if case == "withdrawn":
        from app.models import Enrollment
        fx.set_status(db.session.get(Enrollment, ids["enrollment"]), fx.WITHDRAWN)
    elif case in ("term", "level", "course", "group"):
        group = db.session.get(Group, ids["group"])
        course = db.session.get(Course, group.course_id)
        row = {
            "term": db.session.get(AcademicTerm, group.academic_term_id),
            "level": db.session.get(Level, course.level_id),
            "course": course,
            "group": group,
        }[case]
        fx.set_status(row, fx.ARCHIVED)
    elif case == "unit":
        fx.set_status(db.session.get(Unit, ids["unit"]), fx.ARCHIVED)
    elif case == "draft":
        fx.unpublish(db.session.get(Lesson, ids["lesson"]))
    else:  # pragma: no cover
        raise AssertionError(case)


@pytest.mark.parametrize("case", ["withdrawn", "term", "level", "course", "group", "unit", "draft"])
def test_ended_or_missing_access_is_a_404_that_writes_nothing(app, client, case):
    with app.app_context():
        ids = _standard()
    _login(client)
    token = fx.page_state(client, ids)
    with app.app_context():
        before = fx.rows()
        _deny(ids, case)
    assert client.get(fx.lesson_url(ids["gp"], ids["up"], ids["lp"])).status_code == 404
    for action in ("complete", "undo"):
        assert fx.post_action(client, action, ids, token).status_code == 404
    with app.app_context():
        assert fx.rows() == before


def test_a_suspended_student_loses_the_session_and_writes_nothing(app, client):
    with app.app_context():
        ids = _standard()
    _login(client)
    token = fx.page_state(client, ids)
    with app.app_context():
        before = fx.rows()
        fx.set_status(db.session.get(User, ids["student"]), fx.SUSPENDED)
        fx.fresh_identity()
    response = fx.post_action(client, "complete", ids, token)
    assert response.status_code == 302 and "/auth/login" in _location(response)
    with app.app_context():
        assert fx.rows() == before


def test_malformed_unknown_cross_group_and_mismatched_identifiers_are_404(app, client):
    with app.app_context():
        ids = _standard()
        other_group = fx.hierarchy("B")
        fx.enroll(other_group, db.session.get(User, ids["student"]))
        other_unit = fx.unit(other_group)
        other_lesson = fx.lesson(other_unit, "Colours")
        other_up, other_lp, other_gp = other_unit.public_id, other_lesson.public_id, other_group.public_id
        unknown = str(uuid.uuid4())
    _login(client)
    token = fx.page_state(client, ids)
    cases = [
        ("not-a-uuid", ids["up"], ids["lp"]),
        (ids["gp"], "not-a-uuid", ids["lp"]),
        (ids["gp"], ids["up"], "not-a-uuid"),
        (unknown, ids["up"], ids["lp"]),
        (ids["gp"], unknown, ids["lp"]),
        (ids["gp"], ids["up"], unknown),
        (ids["gp"], other_up, other_lp),
        (other_gp, ids["up"], ids["lp"]),
        (ids["gp"], ids["up"], other_lp),
    ]
    for gp, up, lp in cases:
        assert client.get(fx.lesson_url(gp, up, lp)).status_code == 404, (gp, up, lp)
        for action in ("complete", "undo"):
            response = client.post(fx.action_url(action, gp, up, lp), data={"progress_state": token})
            assert response.status_code == 404, (action, gp, up, lp)
    with app.app_context():
        assert all(row[5] is None for row in fx.rows())


# ===========================================================================
# Isolation
# ===========================================================================


def test_students_never_see_or_change_each_others_progress(app, client):
    with app.app_context():
        ids = _standard()
        group = db.session.get(Group, ids["group"])
        classmate = fx.user("classmate@example.com", fx.STUDENT)
        fx.enroll(group, classmate)
        classmate_ids = dict(ids, student=classmate.id, student_public_id=classmate.public_id)
    fx.login_as(client, "classmate@example.com")
    classmate_token = fx.page_state(client, classmate_ids)
    fx.post_action(client, "complete", classmate_ids)

    _login(client)
    html = _html(client.get(fx.lesson_url(ids["gp"], ids["up"], ids["lp"])))
    assert "Not completed" in html and "Mark as complete" in html
    assert ">Completed</span>" not in _html(client.get(fx.outline_url(ids["gp"])))
    response = fx.post_action(client, "complete", ids, classmate_token)
    assert "could not be verified" in _html(client.get(_location(response)))
    with app.app_context():
        mine = fx.progress_of(ids["student"], ids["group"], ids["lesson"])
        theirs = fx.progress_of(classmate_ids["student"], ids["group"], ids["lesson"])
        assert mine.completed_at is None
        assert theirs.completed_at is not None and theirs.version == 2


def test_the_outline_marks_only_this_students_completions_in_this_group(app, client):
    with app.app_context():
        ids = _standard()
        student = db.session.get(User, ids["student"])
        group = db.session.get(Group, ids["group"])
        fx.progress(student, group, db.session.get(Lesson, ids["lesson"]), completed_at=fx.NOW)
        # A row bound to another Group for this Lesson is never shown here.
        other_group = fx.hierarchy("B")
        fx.enroll(other_group, student)
        fx.progress(student, other_group, db.session.get(Lesson, ids["lesson2"]), completed_at=fx.NOW)
    _login(client)
    response = client.get(fx.outline_url(ids["gp"]))
    html = _html(response)
    assert response.headers["Cache-Control"] == "private, no-store"
    greetings = html.split("Greetings", 1)[1].split("</a>", 1)[0]
    numbers = html.split("Numbers", 1)[1].split("</a>", 1)[0]
    assert ">Completed</span>" in greetings
    assert ">Completed</span>" not in numbers


# ===========================================================================
# The one GET that writes
# ===========================================================================


def test_opening_the_lesson_page_records_one_opening(app, client):
    with app.app_context():
        ids = _standard()
    _login(client)
    assert client.get(fx.lesson_url(ids["gp"], ids["up"], ids["lp"])).status_code == 200
    with app.app_context():
        [row] = fx.rows()
        assert row[1:4] == (ids["student"], ids["group"], ids["lesson"])
        assert row[6] is not None and row[5] is None and row[7] == 1


def test_a_refresh_within_the_window_writes_nothing_and_a_later_one_moves_forward(
    app, client, monkeypatch
):
    with app.app_context():
        ids = _standard()
    _login(client)
    url = fx.lesson_url(ids["gp"], ids["up"], ids["lp"])
    moments = iter([fx.NOW, fx.NOW.replace(second=59), fx.LATER])
    monkeypatch.setattr(progress_tx, "progress_now", lambda: next(moments))
    client.get(url)
    _response, _selects, writes = fx.select_count(client, url)
    assert writes == []
    with app.app_context():
        assert fx.rows()[0][6] == fx.NOW
    client.get(url)
    with app.app_context():
        [row] = fx.rows()
        assert (row[4], row[6], row[7]) == (fx.NOW, fx.LATER, 1)


def test_no_other_request_writes_progress(app, client):
    with app.app_context():
        ids = _standard()
        draft = fx.lesson(db.session.get(Unit, ids["unit"]), "Draft", 5, status=fx.DRAFT)
        draft_lp = draft.public_id
    _login(client)
    for method, url, status in (
        ("head", fx.lesson_url(ids["gp"], ids["up"], ids["lp"]), 200),
        ("get", fx.outline_url(ids["gp"]), 200),
        ("get", fx.STUDENT_DASHBOARD, 200),
        ("get", fx.lesson_url(ids["gp"], ids["up"], draft_lp), 404),
        ("get", fx.lesson_url(ids["gp"], ids["up"], str(uuid.uuid4())), 404),
    ):
        response, _selects, writes = fx.select_count(client, url, method)
        assert response.status_code == status, url
        assert writes == [], url
    with app.app_context():
        assert fx.rows() == []


def test_a_failed_opening_record_never_breaks_the_page(app, client, monkeypatch):
    with app.app_context():
        ids = _standard()

    def broken(*args, **kwargs):
        raise OperationalError("UPDATE lesson_progress", {}, Exception("unavailable"))

    monkeypatch.setattr(progress_tx, "record_open", broken)
    _login(client)
    response = client.get(fx.lesson_url(ids["gp"], ids["up"], ids["lp"]))
    assert response.status_code == 200
    assert "Mark as complete" in _html(response)
    with app.app_context():
        assert fx.rows() == []


# ===========================================================================
# Leakage and bounds
# ===========================================================================


def test_no_internal_ids_reach_the_page_or_the_outline(app, client):
    with app.app_context():
        ids = _standard()
    _login(client)
    fx.post_action(client, "complete", ids)
    with app.app_context():
        progress_id = fx.rows()[0][0]
    for url in (fx.lesson_url(ids["gp"], ids["up"], ids["lp"]), fx.outline_url(ids["gp"])):
        html = _html(client.get(url))
        assert f"/lessons/{ids['lesson']}/" not in html
        assert f"/groups/{ids['group']}/" not in html
        assert f'value="{progress_id}"' not in html
        assert f'value="{ids["lesson"]}"' not in html


def test_the_lesson_page_and_outline_stay_within_their_query_bounds(app, client):
    with app.app_context():
        ids = _standard()
        student = db.session.get(User, ids["student"])
        group = db.session.get(Group, ids["group"])
        for index in range(6):
            unit = fx.unit(group, f"Unit {index + 2}", index + 1)
            for position in range(4):
                lesson = fx.lesson(unit, f"U{index}L{position}", position)
                fx.progress(student, group, lesson, completed_at=fx.NOW, last_opened_at=fx.NOW)
    _login(client)
    client.get(fx.lesson_url(ids["gp"], ids["up"], ids["lp"]))
    for url in (fx.lesson_url(ids["gp"], ids["up"], ids["lp"]), fx.outline_url(ids["gp"])):
        response, selects, _writes = fx.select_count(client, url)
        assert response.status_code == 200
        assert selects <= 6, (url, selects)
