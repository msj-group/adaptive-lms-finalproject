"""M14 inbox routes: role authorization, per-recipient scoping,
non-disclosing 404s, filters / ordering / pagination / empty states,
GET-never-writes, the POST read + open + mark-all behaviour (CSRF and
idempotence included), the corrupted-target fallback, and the rule that
opening a notification re-authorizes its destination from scratch.
"""

import re
from datetime import datetime

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import (
    AcademicStatus,
    EnrollmentStatus,
    LessonStatus,
    Notification,
    NotificationKind,
    UserRole,
    UserStatus,
)
from tests import notification_fixtures as fx
from tests.conftest import login

INBOX = "/notifications"


def _csrf_app():
    """A second app with CSRF actually enabled -- the shared `app`
    fixture uses the testing config, which turns WTF CSRF off."""
    from app import create_app

    app = create_app("testing", WTF_CSRF_ENABLED=True)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


csrf_app = pytest.fixture(_csrf_app)


# ===========================================================================
# Authorization
# ===========================================================================


def test_anonymous_is_redirected_to_login(client):
    resp = client.get(INBOX)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


@pytest.mark.parametrize("role_name", ["student", "teacher"])
def test_student_and_teacher_may_open_the_inbox(app, client, role_name):
    with app.app_context():
        fx.user("u@example.com", fx.ROLES[role_name])
    login(client, "u@example.com")
    assert client.get(INBOX).status_code == 200


@pytest.mark.parametrize("role_name", ["administrator", "researcher"])
def test_administrator_and_researcher_get_403(app, client, role_name):
    with app.app_context():
        fx.user("u@example.com", fx.ROLES[role_name])
    login(client, "u@example.com")
    assert client.get(INBOX).status_code == 403


@pytest.mark.parametrize("role_name", ["administrator", "researcher"])
def test_other_roles_cannot_reach_any_mutation(app, client, role_name):
    with app.app_context():
        owner = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.user("u@example.com", fx.ROLES[role_name])
        pid = fx.notification(owner).public_id
    login(client, "u@example.com")
    assert client.post(f"{INBOX}/{pid}/open").status_code == 403
    assert client.post(f"{INBOX}/{pid}/read").status_code == 403
    assert client.post(f"{INBOX}/read-all").status_code == 403


def test_anonymous_mutations_redirect_to_login(app, client):
    with app.app_context():
        owner = fx.user("s@example.com", UserRole.STUDENT.value)
        pid = fx.notification(owner).public_id
    for path in (f"{INBOX}/{pid}/open", f"{INBOX}/{pid}/read", f"{INBOX}/read-all"):
        resp = client.post(path)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]


def test_get_is_the_only_method_on_the_inbox(app, client):
    with app.app_context():
        fx.user("s@example.com", UserRole.STUDENT.value)
    login(client, "s@example.com")
    assert client.post(INBOX).status_code == 405


# ===========================================================================
# Recipient isolation
# ===========================================================================


def test_inbox_shows_only_the_current_recipients_rows(app, client):
    with app.app_context():
        mine = fx.user("mine@example.com", UserRole.STUDENT.value)
        theirs = fx.user("theirs@example.com", UserRole.STUDENT.value)
        fx.notification(mine, title="Mine only", message="visible to me")
        fx.notification(theirs, title="Theirs only", message="not for me")
    login(client, "mine@example.com")
    html = client.get(INBOX).get_data(as_text=True)
    assert "Mine only" in html
    assert "Theirs only" not in html


@pytest.mark.parametrize("action", ["open", "read"])
def test_another_recipients_public_id_404s(app, client, action):
    with app.app_context():
        mine = fx.user("mine@example.com", UserRole.STUDENT.value)
        theirs = fx.user("theirs@example.com", UserRole.STUDENT.value)
        fx.notification(mine)
        other_pid = fx.notification(theirs).public_id
    login(client, "mine@example.com")
    assert client.post(f"{INBOX}/{other_pid}/{action}").status_code == 404


@pytest.mark.parametrize("action", ["open", "read"])
def test_a_missing_public_id_404s_identically(app, client, action):
    with app.app_context():
        fx.user("mine@example.com", UserRole.STUDENT.value)
    login(client, "mine@example.com")
    resp = client.post(f"{INBOX}/00000000-0000-0000-0000-000000000000/{action}")
    assert resp.status_code == 404


def test_a_cross_recipient_open_does_not_mark_the_other_row_read(app, client):
    with app.app_context():
        mine = fx.user("mine@example.com", UserRole.STUDENT.value)
        theirs = fx.user("theirs@example.com", UserRole.STUDENT.value)
        fx.notification(mine)
        other = fx.notification(theirs)
        other_pid, other_id = other.public_id, other.id
    login(client, "mine@example.com")
    assert client.post(f"{INBOX}/{other_pid}/open").status_code == 404
    with app.app_context():
        assert db.session.get(Notification, other_id).read_at is None


def test_a_teacher_cannot_read_a_students_notification(app, client):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.user("t@example.com", UserRole.TEACHER.value)
        pid = fx.notification(student).public_id
    login(client, "t@example.com")
    assert client.post(f"{INBOX}/{pid}/open").status_code == 404


def test_mark_all_read_only_touches_the_current_recipient(app, client):
    with app.app_context():
        mine = fx.user("mine@example.com", UserRole.STUDENT.value)
        theirs = fx.user("theirs@example.com", UserRole.STUDENT.value)
        fx.notification(mine)
        other_id = fx.notification(theirs).id
    login(client, "mine@example.com")
    client.post(f"{INBOX}/read-all")
    with app.app_context():
        assert db.session.get(Notification, other_id).read_at is None


# ===========================================================================
# Filters, ordering, pagination, empty states
# ===========================================================================


def test_empty_state_for_a_recipient_with_no_notifications(app, client):
    with app.app_context():
        fx.user("s@example.com", UserRole.STUDENT.value)
    login(client, "s@example.com")
    html = client.get(INBOX).get_data(as_text=True)
    assert "No notifications yet" in html


def test_unread_filter_empty_state_when_everything_is_read(app, client):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.notification(student, read_at=datetime(2026, 5, 2, 9, 0, 0))
    login(client, "s@example.com")
    html = client.get(f"{INBOX}?filter=unread").get_data(as_text=True)
    assert "No unread notifications" in html


def test_unread_filter_excludes_read_rows(app, client):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.notification(student, title="Still unread")
        fx.notification(student, title="Already read", read_at=datetime(2026, 5, 2, 9, 0, 0))
    login(client, "s@example.com")
    unread_html = client.get(f"{INBOX}?filter=unread").get_data(as_text=True)
    assert "Still unread" in unread_html
    assert "Already read" not in unread_html

    all_html = client.get(f"{INBOX}?filter=all").get_data(as_text=True)
    assert "Still unread" in all_html
    assert "Already read" in all_html


@pytest.mark.parametrize("value", ["", "bogus", "READ", "all;drop", "1"])
def test_an_unknown_filter_normalises_to_all(app, client, value):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.notification(student, title="Already read", read_at=datetime(2026, 5, 2, 9, 0, 0))
    login(client, "s@example.com")
    html = client.get(f"{INBOX}?filter={value}").get_data(as_text=True)
    assert "Already read" in html


def test_newest_first_ordering(app, client):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.many_notifications(student, 3)
    login(client, "s@example.com")
    html = client.get(INBOX).get_data(as_text=True)
    assert html.index("Notice 000") < html.index("Notice 001") < html.index("Notice 002")


def test_ordering_is_deterministic_for_identical_timestamps(app, client):
    """Notifications written in one transaction (a whole class told a
    lesson was published) share `created_at`; the id tiebreak keeps the
    page stable."""
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        same = datetime(2026, 5, 1, 12, 0, 0)
        for i in range(5):
            fx.notification(student, title=f"Same {i}", created_at=same)
    login(client, "s@example.com")
    first = client.get(INBOX).get_data(as_text=True)
    second = client.get(INBOX).get_data(as_text=True)
    order = [first.index(f"Same {i}") for i in range(5)]
    assert order == sorted(order, reverse=True)  # newest id first
    assert [second.index(f"Same {i}") for i in range(5)] == order


def test_pagination_caps_a_page_at_twenty(app, client):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.many_notifications(student, 25)
    login(client, "s@example.com")

    page1 = client.get(INBOX).get_data(as_text=True)
    assert page1.count("notif-item card") == 20
    assert "Notice 000" in page1 and "Notice 019" in page1
    assert "Notice 020" not in page1
    assert "Next" in page1
    assert "Previous" not in page1

    page2 = client.get(f"{INBOX}?page=2").get_data(as_text=True)
    assert page2.count("notif-item card") == 5
    assert "Notice 020" in page2 and "Notice 024" in page2
    assert "Notice 019" not in page2
    assert "Previous" in page2


def test_no_pagination_controls_for_a_single_page(app, client):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.many_notifications(student, 3)
    login(client, "s@example.com")
    html = client.get(INBOX).get_data(as_text=True)
    assert "notif-pagination" not in html


@pytest.mark.parametrize("value", ["0", "-3", "abc", "", "99999999", "1e9"])
def test_a_bad_page_value_falls_back_to_page_one(app, client, value):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.many_notifications(student, 3)
    login(client, "s@example.com")
    resp = client.get(f"{INBOX}?page={value}")
    assert resp.status_code == 200
    assert "Notice 000" in resp.get_data(as_text=True)


def test_a_page_past_the_end_shows_page_one(app, client):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.many_notifications(student, 3)
    login(client, "s@example.com")
    html = client.get(f"{INBOX}?page=9").get_data(as_text=True)
    assert "Notice 000" in html
    assert "notif-pagination" not in html


# ===========================================================================
# GET never writes
# ===========================================================================


def test_opening_the_inbox_does_not_mark_anything_read(app, client):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        nid = fx.notification(student).id
    login(client, "s@example.com")
    client.get(INBOX)
    client.get(f"{INBOX}?filter=unread")
    client.get(f"{INBOX}?filter=all&page=1")
    with app.app_context():
        assert db.session.get(Notification, nid).read_at is None


def test_the_inbox_issues_no_write_statements(app, client):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.many_notifications(student, 5)
    login(client, "s@example.com")

    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        assert client.get(INBOX).status_code == 200
    finally:
        with app.app_context():
            event.remove(db.engine, "before_cursor_execute", _rec)

    for statement in statements:
        head = statement.strip().upper()
        assert not head.startswith(("INSERT", "UPDATE", "DELETE")), statement


# ===========================================================================
# Mark read / mark all read
# ===========================================================================


def test_mark_read_sets_read_at_once_and_is_idempotent(app, client):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        row = fx.notification(student)
        pid, nid = row.public_id, row.id
    login(client, "s@example.com")

    assert client.post(f"{INBOX}/{pid}/read").status_code == 302
    with app.app_context():
        first_read_at = db.session.get(Notification, nid).read_at
        assert first_read_at is not None

    assert client.post(f"{INBOX}/{pid}/read").status_code == 302
    with app.app_context():
        assert db.session.get(Notification, nid).read_at == first_read_at


def test_mark_all_read_marks_every_unread_row_and_is_idempotent(app, client):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.many_notifications(student, 4)
        already = fx.notification(student, read_at=datetime(2026, 4, 1, 8, 0, 0))
        already_id, already_read_at = already.id, already.read_at
    login(client, "s@example.com")

    client.post(f"{INBOX}/read-all")
    with app.app_context():
        assert Notification.query.filter(Notification.read_at.is_(None)).count() == 0
        assert db.session.get(Notification, already_id).read_at == already_read_at

    resp = client.post(f"{INBOX}/read-all", follow_redirects=True)
    assert "no unread notifications" in resp.get_data(as_text=True).lower()


def test_mutations_require_csrf(csrf_app):
    client = csrf_app.test_client()
    with csrf_app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        row = fx.notification(student)
        pid, nid = row.public_id, row.id
    login(client, "s@example.com")

    for path in (f"{INBOX}/{pid}/open", f"{INBOX}/{pid}/read", f"{INBOX}/read-all"):
        assert client.post(path).status_code == 400, path
    with csrf_app.app_context():
        assert db.session.get(Notification, nid).read_at is None


# ===========================================================================
# Open: mark read, re-validate the stored target, redirect
# ===========================================================================


def test_open_marks_read_and_redirects_to_the_stored_target(app, client):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        row = fx.notification(student, target_path="/student/dashboard")
        pid, nid = row.public_id, row.id
    login(client, "s@example.com")

    resp = client.post(f"{INBOX}/{pid}/open")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/student/dashboard")
    with app.app_context():
        assert db.session.get(Notification, nid).read_at is not None


def test_open_is_idempotent(app, client):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        row = fx.notification(student)
        pid, nid = row.public_id, row.id
    login(client, "s@example.com")

    client.post(f"{INBOX}/{pid}/open")
    with app.app_context():
        first = db.session.get(Notification, nid).read_at
    client.post(f"{INBOX}/{pid}/open")
    with app.app_context():
        assert db.session.get(Notification, nid).read_at == first


@pytest.mark.parametrize(
    "corrupt_target",
    [
        "https://evil.example/",
        "//evil.example/student/dashboard",
        "/student/../admin/students",
        "/admin/students",
        "/teacher/dashboard",
        "/student/dash\nboard",
        "",
    ],
)
def test_a_corrupted_stored_target_falls_back_to_the_inbox(app, client, corrupt_target):
    """A row that somehow holds an unsafe target -- hand-edited in the
    database, or written by a future bug -- is never followed. It is
    still marked read, and the recipient lands back in the inbox."""
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        row = fx.notification(student)
        nid, pid = row.id, row.public_id
        # Bypass the builders on purpose: this is what a corrupted row
        # looks like, and the open route must survive it.
        db.session.query(Notification).filter_by(id=nid).update(
            {Notification.target_path: corrupt_target}, synchronize_session=False
        )
        db.session.commit()
    login(client, "s@example.com")

    resp = client.post(f"{INBOX}/{pid}/open")
    assert resp.status_code == 302
    assert "/notifications" in resp.headers["Location"]
    assert "evil.example" not in resp.headers["Location"]
    assert "/admin/" not in resp.headers["Location"]
    with app.app_context():
        assert db.session.get(Notification, nid).read_at is not None


def test_the_stored_target_is_never_rendered_as_a_link(app, client):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        group = fx.hierarchy(group_name="G")
        fx.enroll(group, student)
        unit = fx.unit(group)
        lsn = fx.lesson(unit, status=LessonStatus.PUBLISHED.value)
        target = f"/student/groups/{group.public_id}/units/{unit.public_id}/lessons/{lsn.public_id}"
        fx.notification(student, target_path=target)
    login(client, "s@example.com")
    html = client.get(INBOX).get_data(as_text=True)
    assert f'href="{target}"' not in html
    assert target not in html
    # The Open control is a POST form to the notification-owned route.
    assert 'method="post"' in html
    assert "/open" in html


# ===========================================================================
# Opening re-authorizes: an old notification is never proof of access
# ===========================================================================


def test_open_still_404s_at_the_destination_after_the_enrollment_is_withdrawn(app, client):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        group = fx.hierarchy(group_name="G")
        enrollment = fx.enroll(group, student)
        unit = fx.unit(group)
        lsn = fx.lesson(unit, status=LessonStatus.PUBLISHED.value)
        target = f"/student/groups/{group.public_id}/units/{unit.public_id}/lessons/{lsn.public_id}"
        pid = fx.notification(
            student, kind=NotificationKind.LESSON_PUBLISHED.value, target_path=target
        ).public_id
        enrollment.status = EnrollmentStatus.WITHDRAWN.value
        db.session.commit()
    login(client, "s@example.com")

    resp = client.post(f"{INBOX}/{pid}/open")
    assert resp.status_code == 302  # the redirect itself is still issued
    assert client.get(target).status_code == 404  # ... and the destination refuses


def test_open_still_404s_after_the_lesson_is_unpublished(app, client):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        group = fx.hierarchy(group_name="G")
        fx.enroll(group, student)
        unit = fx.unit(group)
        lsn = fx.lesson(unit, status=LessonStatus.PUBLISHED.value)
        target = f"/student/groups/{group.public_id}/units/{unit.public_id}/lessons/{lsn.public_id}"
        pid = fx.notification(
            student, kind=NotificationKind.LESSON_PUBLISHED.value, target_path=target
        ).public_id
        lsn.status = LessonStatus.DRAFT.value
        lsn.published_at = None
        db.session.commit()
    login(client, "s@example.com")

    client.post(f"{INBOX}/{pid}/open")
    assert client.get(target).status_code == 404


def test_a_notification_stays_visible_after_access_is_lost(app, client):
    """Notifications are personal historical records -- losing access to
    the target never rewrites or hides the row."""
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        group = fx.hierarchy(group_name="Group Gone")
        enrollment = fx.enroll(group, student)
        fx.notification(
            student,
            kind=NotificationKind.ENROLLMENT_ACTIVATED.value,
            title="Enrollment activated",
            message="You are now enrolled in the group 'Group Gone'.",
        )
        enrollment.status = EnrollmentStatus.WITHDRAWN.value
        group.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    login(client, "s@example.com")
    html = client.get(INBOX).get_data(as_text=True)
    assert "Group Gone" in html


# ===========================================================================
# Role navigation inside the inbox
# ===========================================================================


def test_student_inbox_keeps_the_student_navigation(app, client):
    """Opening Notifications must not strip the rest of the Student's
    portal navigation: Dashboard and Search stay, and the shared layout
    adds exactly one active Notifications link."""
    with app.app_context():
        fx.user("s@example.com", UserRole.STUDENT.value)
    login(client, "s@example.com")
    html = client.get(INBOX).get_data(as_text=True)

    assert 'href="/student/dashboard"' in html
    assert ">Dashboard</a>" in html
    assert 'href="/student/search"' in html
    assert ">Search</a>" in html
    assert 'href="/teacher/dashboard"' not in html


def test_teacher_inbox_keeps_the_teacher_navigation(app, client):
    with app.app_context():
        fx.user("t@example.com", UserRole.TEACHER.value)
    login(client, "t@example.com")
    html = client.get(INBOX).get_data(as_text=True)

    assert 'href="/teacher/dashboard"' in html
    assert ">Dashboard</a>" in html
    # A Teacher has no Student search page and must not be offered one.
    assert 'href="/student/dashboard"' not in html
    assert 'href="/student/search"' not in html


def _portal_nav(html):
    """Just the shared portal header's <nav> block.

    The page body legitimately links to /notifications too (the "All"
    filter chip), so a whole-page count would not answer the question
    this test asks -- which is whether the *navigation* renders the
    Notifications entry exactly once.
    """
    start = html.index('<nav class="portal-nav"')
    return html[start : html.index("</nav>", start)]


@pytest.mark.parametrize("role_name", ["student", "teacher"])
def test_inbox_renders_exactly_one_active_notifications_link(app, client, role_name):
    with app.app_context():
        fx.user("u@example.com", fx.ROLES[role_name])
    login(client, "u@example.com")
    nav = _portal_nav(client.get(INBOX).get_data(as_text=True))

    assert nav.count('href="/notifications"') == 1
    assert nav.count("portal-nav__link--active") == 1
    # The single active link is the Notifications one, not Dashboard/Search.
    active_anchor = nav.split("portal-nav__link--active")[1].split(">")[0]
    assert 'href="/notifications"' in active_anchor


@pytest.mark.parametrize("role_name", ["student", "teacher"])
def test_inbox_navigation_holds_exactly_the_expected_links(app, client, role_name):
    with app.app_context():
        fx.user("u@example.com", fx.ROLES[role_name])
    login(client, "u@example.com")
    nav = _portal_nav(client.get(INBOX).get_data(as_text=True))

    expected = (
        # Phase 4 / M01 added Assignments to the shared Student portal nav,
        # M04D added Quizzes after it, M05 added Listening after that, and
        # M06 added Speaking after Listening -- each is a Student surface
        # the Part introduced, so the shared nav names it. The assertion
        # stays exact: the inbox must hold these links, in this order, and
        # no others -- one Notifications link included.
        [
            "/student/dashboard",
            "/student/assignments",
            "/student/quizzes",
            "/student/listening",
            "/student/speaking",
            "/student/search",
            "/notifications",
        ]
        if role_name == "student"
        else ["/teacher/dashboard", "/notifications"]
    )
    hrefs = re.findall(r'href="([^"]+)"', nav)
    assert hrefs == expected


@pytest.mark.parametrize("role_name", ["student", "teacher"])
def test_other_portal_pages_still_render_one_notifications_link(app, client, role_name):
    """The inbox's own `portal_nav` override must not make any *other*
    portal page render the Notifications link twice."""
    with app.app_context():
        if role_name == "student":
            fx.user("u@example.com", UserRole.STUDENT.value)
            path = "/student/dashboard"
        else:
            fx.user("u@example.com", UserRole.TEACHER.value)
            path = "/teacher/dashboard"
    login(client, "u@example.com")
    html = client.get(path).get_data(as_text=True)
    assert html.count('href="/notifications"') == 1


def test_the_inbox_navigation_adds_no_extra_queries(app, client):
    """The role branch is pure template logic -- it must not introduce a
    lazy ORM load or another notification query."""
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.many_notifications(student, 30)
    login(client, "s@example.com")

    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        assert client.get(INBOX).status_code == 200
    finally:
        with app.app_context():
            event.remove(db.engine, "before_cursor_execute", _rec)

    selects = [x for x in statements if x.strip().upper().startswith("SELECT")]
    assert len(selects) <= 5, (len(selects), selects)


# ===========================================================================
# Presentation
# ===========================================================================


def test_inbox_exposes_public_ids_only(app, client):
    with app.app_context():
        # Several users and rows first, so the ids under test are not the
        # small numbers the pagination hidden field legitimately carries.
        for i in range(4):
            fx.user(f"filler{i}@example.com", UserRole.STUDENT.value)
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.many_notifications(student, 6)
        row = fx.notification(student, title="Target row")
        nid, sid, pid = row.id, student.id, row.public_id
        assert nid > 3 and sid > 3
    login(client, "s@example.com")
    html = client.get(INBOX).get_data(as_text=True)
    assert pid in html
    assert f"/notifications/{nid}/" not in html
    assert f'value="{nid}"' not in html
    assert f'value="{sid}"' not in html


def test_created_at_is_rendered_in_the_configured_timezone(app, client):
    with app.app_context():
        app.config["APP_TIMEZONE"] = "UTC+02:00"
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.notification(student, created_at=datetime(2026, 5, 1, 12, 0, 0))
    login(client, "s@example.com")
    html = client.get(INBOX).get_data(as_text=True)
    assert "2026-05-01 14:00" in html  # 12:00 UTC -> 14:00 local
    assert "UTC+02:00" in html


def test_kind_labels_are_rendered(app, client):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.notification(student, kind=NotificationKind.LESSON_PUBLISHED.value)
    login(client, "s@example.com")
    assert "Lesson" in client.get(INBOX).get_data(as_text=True)


def test_unread_and_read_have_distinct_visual_state(app, client):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.notification(student, title="Fresh")
    login(client, "s@example.com")
    unread_html = client.get(INBOX).get_data(as_text=True)
    assert "notif-item--unread" in unread_html
    assert ">Unread<" in unread_html

    pid = None
    with app.app_context():
        pid = Notification.query.first().public_id
    client.post(f"{INBOX}/{pid}/read")
    read_html = client.get(INBOX).get_data(as_text=True)
    assert "notif-item--unread" not in read_html


def test_filter_chips_emit_a_valid_aria_current_attribute(app, client):
    """Regression: a quoted attribute built as a Jinja *expression* would
    be autoescaped into `aria-current=&#34;page&#34;`. It must be emitted
    as literal template text instead."""
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.notification(student)
    login(client, "s@example.com")

    all_html = client.get(INBOX).get_data(as_text=True)
    assert 'aria-current="page"' in all_html
    assert "aria-current=&#34;" not in all_html

    unread_html = client.get(f"{INBOX}?filter=unread").get_data(as_text=True)
    assert 'aria-current="page"' in unread_html
    assert "aria-current=&#34;" not in unread_html


def test_inbox_is_private_and_not_cached(app, client):
    with app.app_context():
        fx.user("s@example.com", UserRole.STUDENT.value)
    login(client, "s@example.com")
    resp = client.get(INBOX)
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in resp.headers["Vary"]


def test_message_content_is_escaped_not_rendered_as_html(app, client):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.notification(
            student,
            title="<script>alert(1)</script>",
            message="<img src=x onerror=alert(1)>",
        )
    login(client, "s@example.com")
    html = client.get(INBOX).get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "<img src=x" not in html


# ===========================================================================
# Bounded queries
# ===========================================================================


def test_inbox_query_count_is_bounded_no_n_plus_1(app, client):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.many_notifications(student, 60)
    login(client, "s@example.com")

    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        resp = client.get(INBOX)
    finally:
        with app.app_context():
            event.remove(db.engine, "before_cursor_execute", _rec)

    assert resp.status_code == 200
    selects = [s for s in statements if s.strip().upper().startswith("SELECT")]
    # user_loader + page + unread total + header badge, and nothing per row.
    assert len(selects) <= 5, (len(selects), selects)


def test_a_suspended_recipient_cannot_hold_a_session(app, client):
    from app.models import User

    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.notification(student)
    login(client, "s@example.com")
    assert client.get(INBOX).status_code == 200

    # Suspend on the *fixture's* session -- a Flask test request reuses an
    # already-pushed app context (and therefore its SQLAlchemy session),
    # so a write made in a nested context would leave that session's
    # identity map holding the pre-suspension User.
    row = User.query.filter_by(email="s@example.com").first()
    row.status = UserStatus.SUSPENDED.value
    db.session.commit()

    resp = client.get(INBOX)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]
