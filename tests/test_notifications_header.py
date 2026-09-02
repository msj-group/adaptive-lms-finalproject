"""M14 shared portal header: the Notifications link and unread badge.

Covers presence for Student and Teacher across every portal page, absence
for Administrator / Researcher / anonymous, the ``99+`` display cap, the
once-per-request computation, the bounded and indexed query, and -- the
point of the whole design -- that a failing unread-count query fails open
to zero and leaves every learning page working.
"""

from datetime import datetime

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import LessonStatus, UserRole
from app.services import notification_queries
from tests import notification_fixtures as fx
from tests.conftest import login


def _portal_pages(group, unit, lesson):
    return {
        "student_dashboard": "/student/dashboard",
        "student_search": "/student/search",
        "student_outline": f"/student/groups/{group.public_id}/units",
        "student_lesson": (
            f"/student/groups/{group.public_id}/units/{unit.public_id}"
            f"/lessons/{lesson.public_id}"
        ),
        "inbox": "/notifications",
    }


def _student_setup():
    student = fx.user("s@example.com", UserRole.STUDENT.value)
    group = fx.hierarchy(group_name="G")
    fx.enroll(group, student)
    unit = fx.unit(group)
    lesson = fx.lesson(unit, status=LessonStatus.PUBLISHED.value)
    return student, group, unit, lesson


# ===========================================================================
# Presence and absence
# ===========================================================================


def test_student_portal_pages_all_carry_the_notifications_link(app, client):
    with app.app_context():
        student, group, unit, lesson = _student_setup()
        fx.notification(student)
        pages = _portal_pages(group, unit, lesson)
    login(client, "s@example.com")
    for name, path in pages.items():
        html = client.get(path).get_data(as_text=True)
        assert 'href="/notifications"' in html, name
        assert "notif-badge" in html, name


def test_teacher_portal_pages_carry_the_notifications_link(app, client):
    with app.app_context():
        teacher = fx.user("t@example.com", UserRole.TEACHER.value)
        group = fx.hierarchy(group_name="G")
        fx.assign(group, teacher)
        unit = fx.unit(group)
        fx.notification(teacher, target_path="/teacher/dashboard")
        paths = [
            "/teacher/dashboard",
            f"/teacher/groups/{group.public_id}/units",
            f"/teacher/groups/{group.public_id}/units/{unit.public_id}/lessons",
            "/notifications",
        ]
    login(client, "t@example.com")
    for path in paths:
        html = client.get(path).get_data(as_text=True)
        assert 'href="/notifications"' in html, path
        assert "notif-badge" in html, path


@pytest.mark.parametrize("role_name", ["administrator", "researcher"])
def test_other_roles_never_see_a_notifications_link(app, client, role_name):
    with app.app_context():
        fx.user("u@example.com", fx.ROLES[role_name])
    login(client, "u@example.com")
    html = client.get("/admin/dashboard").get_data(as_text=True)
    assert "/notifications" not in html
    assert "notif-badge" not in html


def test_the_login_page_shows_no_badge(client):
    html = client.get("/auth/login").get_data(as_text=True)
    assert "/notifications" not in html
    assert "notif-badge" not in html


def test_administrator_pages_issue_no_notification_query(app, client):
    with app.app_context():
        fx.user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        assert client.get("/admin/dashboard").status_code == 200
    finally:
        with app.app_context():
            event.remove(db.engine, "before_cursor_execute", _rec)

    assert not any("notifications" in s.lower() for s in statements), statements


# ===========================================================================
# The count itself
# ===========================================================================


def test_the_badge_is_hidden_when_nothing_is_unread(app, client):
    with app.app_context():
        student, group, unit, lesson = _student_setup()
        fx.notification(student, read_at=datetime(2026, 5, 2, 9, 0, 0))
    login(client, "s@example.com")
    html = client.get("/student/dashboard").get_data(as_text=True)
    assert 'href="/notifications"' in html
    assert "notif-badge" not in html


def test_the_badge_counts_only_unread_rows_of_this_recipient(app, client):
    with app.app_context():
        student, _group, _unit, _lesson = _student_setup()
        other = fx.user("other@example.com", UserRole.STUDENT.value)
        fx.many_notifications(student, 3)
        fx.notification(student, read_at=datetime(2026, 5, 2, 9, 0, 0))
        fx.many_notifications(other, 7)
    login(client, "s@example.com")
    html = client.get("/student/dashboard").get_data(as_text=True)
    assert '<span class="notif-badge" aria-hidden="true">3</span>' in html
    assert 'aria-label="Notifications, 3 unread"' in html


def test_the_badge_updates_after_marking_everything_read(app, client):
    with app.app_context():
        student, _g, _u, _l = _student_setup()
        fx.many_notifications(student, 4)
    login(client, "s@example.com")
    assert ">4</span>" in client.get("/student/dashboard").get_data(as_text=True)
    client.post("/notifications/read-all")
    assert "notif-badge" not in client.get("/student/dashboard").get_data(as_text=True)


@pytest.mark.parametrize(
    "count, expected",
    [(0, "0"), (1, "1"), (98, "98"), (99, "99"), (100, "99+"), (5000, "99+")],
)
def test_badge_label_caps_at_99_plus(count, expected):
    assert notification_queries.badge_label(count) == expected


def test_the_rendered_badge_caps_at_99_plus(app, client):
    with app.app_context():
        student, _g, _u, _l = _student_setup()
        fx.many_notifications(student, 101)
    login(client, "s@example.com")
    html = client.get("/student/dashboard").get_data(as_text=True)
    assert ">99+</span>" in html
    assert 'aria-label="Notifications, 101 unread"' in html


# ===========================================================================
# Bounded / once-per-request
# ===========================================================================


def test_the_badge_costs_exactly_one_bounded_query(app, client):
    with app.app_context():
        student, _g, _u, _l = _student_setup()
        fx.many_notifications(student, 40)
    login(client, "s@example.com")

    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        assert client.get("/student/dashboard").status_code == 200
    finally:
        with app.app_context():
            event.remove(db.engine, "before_cursor_execute", _rec)

    badge_queries = [s for s in statements if "notifications" in s.lower()]
    assert len(badge_queries) == 1, badge_queries
    assert "count" in badge_queries[0].lower()
    assert "read_at IS NULL" in badge_queries[0]


def test_the_count_is_computed_once_per_request(app):
    """`header_badge` caches on `flask.g`, so a request that renders more
    than one template still pays for a single count."""
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.many_notifications(student, 3)

    calls = {"n": 0}
    real = notification_queries.unread_count

    def _counting(recipient_id):
        calls["n"] += 1
        return real(recipient_id)

    with app.test_request_context("/student/dashboard"):
        from flask_login import login_user

        with app.app_context():
            pass
        notification_queries.unread_count = _counting
        try:
            from app.models import User

            user = User.query.filter_by(email="s@example.com").first()
            login_user(user)
            first = notification_queries.header_badge(user)
            second = notification_queries.header_badge(user)
        finally:
            notification_queries.unread_count = real

    assert first == second == {"unread_count": 3, "unread_label": "3"}
    assert calls["n"] == 1


def test_header_badge_is_none_without_an_inbox_role(app):
    class _Fake:
        def __init__(self, role):
            self.id = 1
            self.role = role
            self.is_authenticated = True

    with app.test_request_context("/"):
        assert notification_queries.header_badge(None) is None
        assert notification_queries.header_badge(_Fake(UserRole.ADMINISTRATOR.value)) is None
        assert notification_queries.header_badge(_Fake(UserRole.RESEARCHER.value)) is None
        assert notification_queries.header_badge(_Fake("something-else")) is None


# ===========================================================================
# Failure isolation -- a broken count must not break any page
# ===========================================================================


@pytest.fixture
def broken_unread_count(monkeypatch):
    def _boom(_recipient_id):
        raise RuntimeError("simulated notifications table failure")

    monkeypatch.setattr(notification_queries, "_unread_count_isolated", _boom)
    return _boom


def test_a_failing_count_still_renders_every_learning_page(app, client, broken_unread_count):
    with app.app_context():
        student, group, unit, lesson = _student_setup()
        fx.material(lesson, title="Handout")
        fx.notification(student)
        pages = _portal_pages(group, unit, lesson)
    login(client, "s@example.com")
    for name, path in pages.items():
        resp = client.get(path)
        assert resp.status_code == 200, (name, resp.status_code)
        html = resp.get_data(as_text=True)
        assert 'href="/notifications"' in html, name
        # Failed open to zero: the link is there, the badge is not.
        assert "notif-badge" not in html, name


def test_a_failing_count_still_renders_teacher_pages(app, client, broken_unread_count):
    with app.app_context():
        teacher = fx.user("t@example.com", UserRole.TEACHER.value)
        group = fx.hierarchy(group_name="G")
        fx.assign(group, teacher)
        unit = fx.unit(group)
        fx.notification(teacher, target_path="/teacher/dashboard")
        paths = [
            "/teacher/dashboard",
            f"/teacher/groups/{group.public_id}/units",
            f"/teacher/groups/{group.public_id}/units/{unit.public_id}/lessons",
        ]
    login(client, "t@example.com")
    for path in paths:
        assert client.get(path).status_code == 200, path


def test_a_failing_count_is_logged_and_returns_zero(app, caplog, broken_unread_count):
    with app.app_context():
        with caplog.at_level("ERROR"):
            assert notification_queries.unread_count(1) == 0
    assert any(
        "Unread notification count failed" in record.message for record in caplog.records
    )
    # No driver / SQL detail is put in front of the user -- it only goes
    # to the server log.
    assert "simulated notifications table failure" not in "".join(
        record.message for record in caplog.records
    )


def test_a_failing_count_does_not_break_the_inbox_itself(app, client, broken_unread_count):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.many_notifications(student, 3)
    login(client, "s@example.com")
    resp = client.get("/notifications")
    assert resp.status_code == 200
    assert "Notice 000" in resp.get_data(as_text=True)


def test_the_count_runs_on_its_own_connection(app):
    """The isolated read must not go through `db.session` -- that is what
    keeps a notification-query failure from poisoning the request's own
    transaction."""
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.many_notifications(student, 2)

        used = {"session": False}
        original_execute = db.session.execute

        def _tracking(*args, **kwargs):
            used["session"] = True
            return original_execute(*args, **kwargs)

        db.session.execute = _tracking
        try:
            assert notification_queries.unread_count(student.id) == 2
        finally:
            del db.session.execute

        assert used["session"] is False
