"""Administrator review of recorded attendance (Phase 4 / M07).

An Administrator reads everything -- drafts, historical sessions under
archived ancestors, and the Teacher-only private notes -- and can write
**nothing**: M07 adds no administrator mutation endpoint at all, and this
suite proves the absence rather than a disabled control.
"""

import re
from datetime import timedelta

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import AcademicStatus, AttendanceStatus, UserRole
from app.services.attendance_queries import PAGE_SIZE
from tests import attendance_fixtures as fx
from tests.conftest import make_user

OVERVIEW = "/admin/attendance"

PRESENT = AttendanceStatus.PRESENT.value
LATE = AttendanceStatus.LATE.value


def _group_url(gpid):
    return f"/admin/groups/{gpid}/attendance"


def _setup(app, label="A", finalized=False, note=None):
    _, group = fx.setup_group(label, teacher_email=f"teacher-{label}@example.com")
    schedule = fx.schedule_for(group)
    students = [
        fx.enroll(group, f"alice-{label}@example.com", name=f"Alice {label}"),
        fx.enroll(group, f"bob-{label}@example.com", name=f"Bob {label}"),
    ]
    session, records = fx.full_session(
        group, schedule, students, finalized_at=fx.LATER if finalized else None
    )
    records[0].status = PRESENT
    if note is not None:
        records[0].note = note
    db.session.commit()
    return group, schedule, session, records


def _dates(html):
    return re.findall(r"<td><strong>(\d{4}-\d{2}-\d{2})</strong></td>", html)


# ===========================================================================
# Access control
# ===========================================================================


def test_anonymous_is_redirected_to_login(app, client):
    with app.app_context():
        group, _, session, _ = _setup(app)
        gpid, spid = group.public_id, session.public_id
    for url in (OVERVIEW, _group_url(gpid), f"{_group_url(gpid)}/{spid}"):
        resp = client.get(url)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]


@pytest.mark.parametrize(
    "role", [UserRole.TEACHER.value, UserRole.STUDENT.value, UserRole.RESEARCHER.value]
)
def test_a_non_administrator_is_forbidden(app, client, role):
    with app.app_context():
        group, _, session, _ = _setup(app)
        gpid, spid = group.public_id, session.public_id
        make_user("other@example.com", role)
    fx.login_as(client, "other@example.com")
    assert client.get(OVERVIEW).status_code == 403
    assert client.get(_group_url(gpid)).status_code == 403
    assert client.get(f"{_group_url(gpid)}/{spid}").status_code == 403


def test_a_session_public_id_from_another_group_404s(app, client):
    with app.app_context():
        group_a, _, _, _ = _setup(app, "A")
        _, _, session_b, _ = _setup(app, "B")
        gpid_a, spid_b = group_a.public_id, session_b.public_id
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    assert client.get(f"{_group_url(gpid_a)}/{spid_b}").status_code == 404


def test_unknown_group_and_session_ids_404(app, client):
    with app.app_context():
        group, _, session, _ = _setup(app)
        gpid = group.public_id
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    assert client.get(_group_url("no-such-group")).status_code == 404
    assert client.get(f"{_group_url(gpid)}/no-such-session").status_code == 404


# ===========================================================================
# No mutation exists
# ===========================================================================


def test_no_administrator_attendance_mutation_endpoint_exists(app, client):
    with app.app_context():
        group, _, session, _ = _setup(app)
        gpid, spid = group.public_id, session.public_id
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")

    # The read routes themselves accept GET only.
    for url in (OVERVIEW, _group_url(gpid), f"{_group_url(gpid)}/{spid}"):
        assert client.post(url).status_code == 405, url
        assert client.put(url).status_code == 405, url
        assert client.delete(url).status_code == 405, url

    # And no create / edit / finalize / reopen / delete / export endpoint
    # exists to aim a request at.
    for suffix in (
        "/new", "/edit", "/mark", "/finalize", "/reopen", "/unlock", "/delete",
        "/duplicate", "/export", "/bulk", "/grade",
    ):
        assert client.post(f"{_group_url(gpid)}/{spid}{suffix}").status_code == 404
        assert client.get(f"{_group_url(gpid)}/{spid}{suffix}").status_code == 404
    for suffix in ("/new", "/export", "/bulk"):
        assert client.post(OVERVIEW + suffix).status_code == 404
        assert client.get(OVERVIEW + suffix).status_code == 404


def test_no_attendance_endpoint_at_all_accepts_a_write_from_an_administrator(app):
    """Structural: every attendance rule registered under the admin
    blueprint is GET-only."""
    rules = [
        rule
        for rule in app.url_map.iter_rules()
        if str(rule).startswith("/admin") and "attendance" in str(rule)
    ]
    assert rules
    for rule in rules:
        assert rule.methods - {"HEAD", "OPTIONS"} == {"GET"}, str(rule)


def test_the_review_pages_render_no_form_and_no_token(app, client):
    with app.app_context():
        group, _, session, _ = _setup(app, note="Kept disrupting.")
        gpid, spid = group.public_id, session.public_id
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    detail = client.get(f"{_group_url(gpid)}/{spid}").get_data(as_text=True)
    # The only POST form on the detail page is the shared header's logout.
    assert re.findall(r'<form[^>]*action="([^"]*)"', detail) == ["/auth/logout"]
    assert "attendance_state" not in detail
    assert 'name="status__' not in detail
    assert "confirm_finalize" not in detail


def test_reading_the_review_pages_issues_no_write_statement(app, client):
    with app.app_context():
        group, _, session, _ = _setup(app)
        gpid, spid = group.public_id, session.public_id
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")

    recorded = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        recorded.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        for url in (OVERVIEW, _group_url(gpid), f"{_group_url(gpid)}/{spid}"):
            assert client.get(url).status_code == 200
    finally:
        with app.app_context():
            event.remove(db.engine, "before_cursor_execute", _rec)
    for statement in recorded:
        assert not statement.strip().upper().startswith(("INSERT", "UPDATE", "DELETE"))


# ===========================================================================
# What an Administrator may read
# ===========================================================================


def test_an_administrator_reads_statuses_and_private_notes(app, client):
    with app.app_context():
        group, _, session, _ = _setup(app, finalized=True, note="Kept disrupting.")
        gpid, spid = group.public_id, session.public_id
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    html = client.get(f"{_group_url(gpid)}/{spid}").get_data(as_text=True)
    assert "Alice A" in html and "Bob A" in html
    assert "Present" in html and "Absent" in html
    assert "Kept disrupting." in html
    assert "Finalized" in html


def test_a_draft_session_is_visible_to_an_administrator(app, client):
    with app.app_context():
        group, _, session, _ = _setup(app, finalized=False)
        gpid, spid = group.public_id, session.public_id
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    overview = client.get(OVERVIEW).get_data(as_text=True)
    detail = client.get(f"{_group_url(gpid)}/{spid}").get_data(as_text=True)
    assert fx.SESSION_DATE.isoformat() in overview
    assert "Draft" in overview
    assert "still a <strong>draft</strong>" in detail


def test_historical_sessions_under_archived_ancestors_stay_readable(app, client):
    with app.app_context():
        group, schedule, session, _ = _setup(app, finalized=True)
        group.status = AcademicStatus.ARCHIVED.value
        group.academic_term.status = AcademicStatus.ARCHIVED.value
        group.course.status = AcademicStatus.ARCHIVED.value
        schedule.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        gpid, spid = group.public_id, session.public_id
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    assert client.get(OVERVIEW).status_code == 200
    assert client.get(_group_url(gpid)).status_code == 200
    assert client.get(f"{_group_url(gpid)}/{spid}").status_code == 200


def test_a_note_is_escaped_not_rendered_as_markup(app, client):
    with app.app_context():
        group, _, session, _ = _setup(app, note="<script>alert(1)</script>")
        gpid, spid = group.public_id, session.public_id
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    html = client.get(f"{_group_url(gpid)}/{spid}").get_data(as_text=True)
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;" in html


# ===========================================================================
# Filters
# ===========================================================================


def test_the_group_filter_narrows_to_that_group(app, client):
    with app.app_context():
        group_a, _, _, _ = _setup(app, "A")
        group_b, _, _, _ = _setup(app, "B")
        gpid_a = group_a.public_id
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    html = client.get(f"{OVERVIEW}?group={gpid_a}").get_data(as_text=True)
    assert "Group A" in html
    assert "Group B" not in html


def test_an_unknown_group_filter_narrows_to_nothing_rather_than_widening(app, client):
    with app.app_context():
        _setup(app, "A")
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    html = client.get(f"{OVERVIEW}?group=not-a-real-id").get_data(as_text=True)
    assert "No group has that public ID" in html
    assert "Group A" not in html


def test_the_date_filter_narrows_to_that_class_date(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group, "s@example.com")
        fx.full_session(group, schedule, [student], session_date=fx.SESSION_DATE)
        fx.full_session(group, schedule, [student], session_date=fx.PREVIOUS_WEEK)
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    html = client.get(
        f"{OVERVIEW}?session_date={fx.SESSION_DATE.isoformat()}"
    ).get_data(as_text=True)
    assert _dates(html) == [fx.SESSION_DATE.isoformat()]


@pytest.mark.parametrize("value", ["not-a-date", "2026-02-30", "2026-13-01", ""])
def test_a_malformed_date_filter_is_dropped_rather_than_reaching_sql(app, client, value):
    with app.app_context():
        _setup(app, "A")
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    resp = client.get(f"{OVERVIEW}?session_date={value}")
    assert resp.status_code == 200
    assert fx.SESSION_DATE.isoformat() in resp.get_data(as_text=True)


def test_the_state_filter_separates_final_from_draft(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group, "s@example.com")
        fx.full_session(
            group, schedule, [student], session_date=fx.SESSION_DATE,
            finalized_at=fx.LATER,
        )
        fx.full_session(group, schedule, [student], session_date=fx.PREVIOUS_WEEK)
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    final_only = client.get(f"{OVERVIEW}?state=final").get_data(as_text=True)
    draft_only = client.get(f"{OVERVIEW}?state=draft").get_data(as_text=True)
    both = client.get(OVERVIEW).get_data(as_text=True)
    assert _dates(final_only) == [fx.SESSION_DATE.isoformat()]
    assert _dates(draft_only) == [fx.PREVIOUS_WEEK.isoformat()]
    assert len(_dates(both)) == 2


@pytest.mark.parametrize("value", ["FINAL", "finalised", "true", "1", "nonsense"])
def test_an_unknown_state_filter_is_dropped(app, client, value):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group, "s@example.com")
        fx.full_session(
            group, schedule, [student], session_date=fx.SESSION_DATE,
            finalized_at=fx.LATER,
        )
        fx.full_session(group, schedule, [student], session_date=fx.PREVIOUS_WEEK)
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    html = client.get(f"{OVERVIEW}?state={value}").get_data(as_text=True)
    assert len(_dates(html)) == 2


# ===========================================================================
# Pagination and query bounds
# ===========================================================================


def test_the_overview_is_paginated_at_twenty_newest_first(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group, "s@example.com")
        dates, a_date = [], fx.SESSION_DATE
        for _ in range(PAGE_SIZE + 2):
            dates.append(a_date)
            fx.full_session(group, schedule, [student], session_date=a_date)
            a_date = a_date - timedelta(days=7)
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    page1 = _dates(client.get(OVERVIEW).get_data(as_text=True))
    page2 = _dates(client.get(OVERVIEW + "?page=2").get_data(as_text=True))
    assert len(page1) == PAGE_SIZE
    assert len(page2) == 2
    assert page1 == [d.isoformat() for d in dates[:PAGE_SIZE]]
    assert page1 == sorted(page1, reverse=True)


def test_the_group_scoped_list_is_paginated_too(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group, "s@example.com")
        a_date = fx.SESSION_DATE
        for _ in range(PAGE_SIZE + 1):
            fx.full_session(group, schedule, [student], session_date=a_date)
            a_date = a_date - timedelta(days=7)
        gpid = group.public_id
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    page1 = _dates(client.get(_group_url(gpid)).get_data(as_text=True))
    page2 = _dates(client.get(_group_url(gpid) + "?page=2").get_data(as_text=True))
    assert len(page1) == PAGE_SIZE
    assert len(page2) == 1


def test_the_overview_fetches_page_size_plus_one_and_never_counts_sessions(app, client):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group, "s@example.com")
        a_date = fx.SESSION_DATE
        for _ in range(PAGE_SIZE + 4):
            fx.full_session(group, schedule, [student], session_date=a_date)
            a_date = a_date - timedelta(days=7)
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")

    recorded = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        recorded.append((statement, parameters))

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        assert client.get(OVERVIEW).status_code == 200
    finally:
        with app.app_context():
            event.remove(db.engine, "before_cursor_execute", _rec)

    listing = [
        (statement, parameters)
        for statement, parameters in recorded
        if "FROM attendance_sessions" in statement
    ]
    assert listing
    assert any(
        "LIMIT" in statement.upper() and 21 in tuple(parameters or ())
        for statement, parameters in listing
    )
    for statement, _ in listing:
        assert "count(" not in statement.lower()


def test_the_overview_cost_does_not_grow_with_the_number_of_sessions(app, client):
    def query_count(session_count):
        with app.app_context():
            db.drop_all()
            db.create_all()
            _, group = fx.setup_group()
            schedule = fx.schedule_for(group)
            students = [fx.enroll(group, f"s{i}@example.com") for i in range(3)]
            a_date = fx.SESSION_DATE
            for _ in range(session_count):
                fx.full_session(group, schedule, students, session_date=a_date)
                a_date = a_date - timedelta(days=7)
            make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        fx.login_as(client, "admin@example.com")

        recorded = []

        def _rec(conn, cursor, statement, parameters, context, executemany):
            recorded.append(statement)

        with app.app_context():
            event.listen(db.engine, "before_cursor_execute", _rec)
        try:
            assert client.get(OVERVIEW).status_code == 200
        finally:
            with app.app_context():
                event.remove(db.engine, "before_cursor_execute", _rec)
        return len([s for s in recorded if s.strip().upper().startswith("SELECT")])

    assert query_count(1) == query_count(PAGE_SIZE)


# ===========================================================================
# Headers, navigation and leakage
# ===========================================================================


def test_every_review_page_is_private_no_store_and_varies_on_cookie(app, client):
    with app.app_context():
        group, _, session, _ = _setup(app)
        gpid, spid = group.public_id, session.public_id
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    for url in (OVERVIEW, _group_url(gpid), f"{_group_url(gpid)}/{spid}"):
        resp = client.get(url)
        assert resp.status_code == 200, url
        assert resp.headers["Cache-Control"] == "private, no-store", url
        assert "Cookie" in resp.headers.get("Vary", ""), url


def test_urls_use_public_identifiers_only(app, client):
    with app.app_context():
        group, schedule, session, records = _setup(app)
        gpid, spid = group.public_id, session.public_id
        ids = (group.id, schedule.id, session.id, records[0].id)
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    for url in (OVERVIEW, _group_url(gpid), f"{_group_url(gpid)}/{spid}"):
        html = client.get(url).get_data(as_text=True)
        hrefs = re.findall(r'href="([^"]*)"', html)
        for value in ids:
            assert not any(
                href.rstrip("/").endswith(f"/{value}") for href in hrefs
            ), (url, value)
    listing = client.get(OVERVIEW).get_data(as_text=True)
    assert gpid in listing and spid in listing


def test_the_admin_navigation_now_links_attendance(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    html = client.get("/admin/dashboard").get_data(as_text=True)
    assert 'href="/admin/attendance"' in html
    # Phase 4 / M08 enabled Grades; Payments and Research remain deferred
    # with no endpoint.
    assert 'href="/admin/grades"' in html
    assert 'href="/admin/payments"' not in html
    assert 'href="/admin/research"' not in html
    assert "Soon" in html
