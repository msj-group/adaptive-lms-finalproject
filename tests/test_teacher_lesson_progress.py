"""Phase 4 / M13 -- the Teacher read-only Group progress page: the current
assignment as authorization, Group-scoped counts, roster scope, historical
reads under an archived chain, pagination, leakage, query bounds and no
writes.
"""

import uuid

import pytest

import tests.lesson_progress_fixtures as fx
from app.extensions import db
from app.models import Group, User
from app.services.schedule_occurrences import to_app_local


def _html(response):
    return response.get_data(as_text=True)


def _world():
    student, teacher, group = fx.classroom(
        "A", student_name="Amina Student", teacher_name="Tariq Teacher"
    )
    unit = fx.unit(group)
    done = fx.lesson(unit, "Greetings", 0)
    fx.lesson(unit, "Numbers", 1)
    fx.progress(student, group, done, completed_at=fx.LATER, last_opened_at=fx.LATEST)
    return {"gp": group.public_id, "group": group.id, "student": student.id,
            "teacher": teacher.id}


def _login_teacher(client, email="teacher@example.com"):
    fx.login_as(client, email)


def _table(html):
    return html.split("<tbody>", 1)[1].split("</tbody>", 1)[0] if "<tbody>" in html else ""


def test_anonymous_follows_the_login_redirect_and_other_roles_are_forbidden(app, client):
    with app.app_context():
        ids = _world()
        fx.user("admin@example.com", fx.ADMIN)
        fx.user("researcher@example.com", fx.RESEARCHER)
    response = client.get(fx.teacher_url(ids["gp"]))
    assert response.status_code == 302 and "/auth/login" in response.headers["Location"]
    for email in ("student@example.com", "admin@example.com", "researcher@example.com"):
        fx.login_as(client, email)
        assert client.get(fx.teacher_url(ids["gp"])).status_code == 403, email


def test_an_assigned_teacher_reads_the_roster_and_group_scoped_counts(app, client):
    with app.app_context():
        ids = _world()
    _login_teacher(client)
    response = client.get(fx.teacher_url(ids["gp"]))
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in response.headers["Vary"]
    html = _html(response)
    rows = _table(html)
    assert "Amina Student" in rows
    assert "1 of 2" in rows
    # Stored in UTC, shown in the center timezone.
    tz_name = app.config["APP_TIMEZONE"]
    for moment in (fx.LATER, fx.LATEST):
        assert to_app_local(tz_name, moment).strftime("%Y-%m-%d %H:%M") in rows
    assert "Tariq Teacher" not in rows
    assert "not operational" not in html
    # Read-only: the only form on the page is the portal's logout form.
    assert html.count("<form") == 1
    assert "/complete" not in html and "/undo-complete" not in html


def test_unassigned_removed_malformed_and_unknown_are_the_same_404(app, client):
    with app.app_context():
        ids = _world()
        fx.user("stranger@example.com", fx.TEACHER)
        co_teacher = fx.user("removed@example.com", fx.TEACHER)
        group = db.session.get(Group, ids["group"])
        fx.set_status(fx.assign(group, co_teacher), fx.REMOVED)
    for email in ("stranger@example.com", "removed@example.com"):
        fx.login_as(client, email)
        assert client.get(fx.teacher_url(ids["gp"])).status_code == 404, email
    _login_teacher(client)
    for group_public_id in ("not-a-uuid", str(uuid.uuid4())):
        assert client.get(fx.teacher_url(group_public_id)).status_code == 404


def test_a_teacher_of_one_group_cannot_read_another_groups_progress(app, client):
    with app.app_context():
        ids = _world()
        _student, _teacher, other = fx.classroom(
            "B", "other-student@example.com", "other-teacher@example.com"
        )
        other_gp = other.public_id
    _login_teacher(client)
    assert client.get(fx.teacher_url(other_gp)).status_code == 404
    fx.login_as(client, "other-teacher@example.com")
    assert client.get(fx.teacher_url(ids["gp"])).status_code == 404


def test_only_this_groups_active_students_and_rows_are_counted(app, client):
    with app.app_context():
        ids = _world()
        group = db.session.get(Group, ids["group"])
        teacher = db.session.get(User, ids["teacher"])
        student = db.session.get(User, ids["student"])
        other = fx.hierarchy("B")
        fx.assign(other, teacher)
        fx.enroll(other, student)
        other_lesson = fx.lesson(fx.unit(other), "Elsewhere", 0)
        fx.progress(student, other, other_lesson, completed_at=fx.LATEST)
        withdrawn = fx.user("withdrawn@example.com", fx.STUDENT, name="Wafa Withdrawn")
        fx.set_status(fx.enroll(group, withdrawn), fx.WITHDRAWN)
        suspended = fx.user("suspended@example.com", fx.STUDENT, name="Sami Suspended",
                            status=fx.SUSPENDED)
        fx.enroll(group, suspended)
        outsider = fx.user("outsider@example.com", fx.STUDENT, name="Omar Outsider")
        fx.enroll(other, outsider)
        other_gp = other.public_id
    _login_teacher(client)
    rows = _table(_html(client.get(fx.teacher_url(ids["gp"]))))
    assert "Amina Student" in rows and "1 of 2" in rows
    for name in ("Wafa Withdrawn", "Sami Suspended", "Omar Outsider"):
        assert name not in rows
    rows = _table(_html(client.get(fx.teacher_url(other_gp))))
    assert "Amina Student" in rows and "1 of 1" in rows
    assert "Omar Outsider" in rows and "0 of 1" in rows


def test_hidden_lessons_are_not_counted(app, client):
    with app.app_context():
        ids = _world()
        from app.models import Lesson
        fx.unpublish(Lesson.query.filter_by(title="Greetings").one())
    _login_teacher(client)
    html = _html(client.get(fx.teacher_url(ids["gp"])))
    assert "0 of 1" in _table(html)
    assert "the\n  1 published lesson in this group" in html or "1 published lesson" in html


@pytest.mark.parametrize("link", ["group", "term"])
def test_an_archived_chain_stays_readable_with_a_notice(app, client, link):
    with app.app_context():
        ids = _world()
        group = db.session.get(Group, ids["group"])
        term, _level, _course = fx.ancestors(group)
        fx.set_status(group if link == "group" else term, fx.ARCHIVED)
    _login_teacher(client)
    response = client.get(fx.teacher_url(ids["gp"]))
    assert response.status_code == 200
    html = _html(response)
    assert "not operational" in html
    assert "1 of 2" in _table(html)
    dashboard = _html(client.get(fx.TEACHER_DASHBOARD))
    assert f'href="{fx.teacher_url(ids["gp"])}">Lesson progress</a>' in dashboard


def test_pagination_is_bounded_and_falls_back_to_the_first_page(app, client):
    with app.app_context():
        ids = _world()
        group = db.session.get(Group, ids["group"])
        for index in range(20):
            fx.enroll(group, fx.user(f"s{index:02d}@example.com", fx.STUDENT,
                                     name=f"Student {index:02d}"))
    _login_teacher(client)
    first = _html(client.get(fx.teacher_url(ids["gp"])))
    assert _table(first).count("<tr>") == 20
    assert "More students" in first and "Previous students" not in first
    second = _html(client.get(fx.teacher_url(ids["gp"]) + "?page=2"))
    assert _table(second).count("<tr>") == 1
    assert "Student 19" in _table(second)
    assert "Previous students" in second and "More students" not in second
    for page in ("99", "0", "abc"):
        html = _html(client.get(fx.teacher_url(ids["gp"]) + f"?page={page}"))
        assert _table(html) == _table(first), page


def test_no_email_or_internal_id_reaches_the_page(app, client):
    with app.app_context():
        ids = _world()
    _login_teacher(client)
    html = _html(client.get(fx.teacher_url(ids["gp"])))
    assert "student@example.com" not in html
    assert f"/groups/{ids['group']}/" not in html
    assert f'value="{ids["student"]}"' not in html


def test_the_query_count_does_not_grow_with_roster_or_history(app, client):
    with app.app_context():
        ids = _world()
    _login_teacher(client)
    _response, small, _writes = fx.select_count(client, fx.teacher_url(ids["gp"]))
    with app.app_context():
        group = db.session.get(Group, ids["group"])
        from app.models import Unit
        unit = Unit.query.filter_by(group_id=group.id).first()
        lessons = [fx.lesson(unit, f"Extra {index}", index + 5) for index in range(5)]
        for index in range(15):
            student = fx.user(f"s{index:02d}@example.com", fx.STUDENT, name=f"Student {index:02d}")
            fx.enroll(group, student)
            for lesson in lessons:
                fx.progress(student, group, lesson, completed_at=fx.NOW, last_opened_at=fx.NOW)
    response, large, writes = fx.select_count(client, fx.teacher_url(ids["gp"]))
    assert response.status_code == 200
    assert large == small, (small, large)
    assert writes == []


def test_the_teacher_dashboard_offers_lesson_progress_on_every_card(app, client):
    with app.app_context():
        ids = _world()
        teacher = db.session.get(User, ids["teacher"])
        past = fx.hierarchy("Past", group_status=fx.ARCHIVED)
        fx.assign(past, teacher)
        past_gp = past.public_id
        before = fx.rows()
    _login_teacher(client)
    response, _selects, writes = fx.select_count(client, fx.TEACHER_DASHBOARD)
    html = _html(response)
    for group_public_id in (ids["gp"], past_gp):
        assert f'href="{fx.teacher_url(group_public_id)}">Lesson progress</a>' in html
    assert writes == []
    with app.app_context():
        assert fx.rows() == before
