"""Phase 4 / M09 -- the Student announcement surface.

Covers the three scopes' visibility, the deduplication a Student in
several Groups of one Course needs, the non-disclosing 404, the complete
absence of any mutation endpoint, the dashboard preview, and the
pagination and response headers.
"""

import re

import pytest
from sqlalchemy import event

import tests.announcement_fixtures as fx
from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Announcement,
    AnnouncementStatus,
    Course,
    Group,
    Level,
    User,
    UserRole,
    UserStatus,
)

_DRAFT = AnnouncementStatus.DRAFT.value
_WITHDRAWN = AnnouncementStatus.WITHDRAWN.value


def _author():
    return fx.user("author@example.com", UserRole.ADMINISTRATOR.value)


# ===========================================================================
# Role authorization
# ===========================================================================


def test_anonymous_is_redirected_to_login(app, client):
    for url in (fx.STUDENT_FEED, fx.student_detail("x")):
        response = client.get(url)
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]


@pytest.mark.parametrize(
    "role",
    [UserRole.TEACHER.value, UserRole.ADMINISTRATOR.value, UserRole.RESEARCHER.value],
)
def test_every_other_role_is_forbidden(app, client, role):
    with app.app_context():
        fx.user("other@example.com", role)
    fx.login_as(client, "other@example.com")
    assert client.get(fx.STUDENT_FEED).status_code == 403
    assert client.get(fx.student_detail("x")).status_code == 403


def test_a_suspended_student_cannot_reach_the_feed(app, client):
    with app.app_context():
        fx.user("s@example.com", UserRole.STUDENT.value)
    fx.login_as(client, "s@example.com")
    assert client.get(fx.STUDENT_FEED).status_code == 200
    with app.app_context():
        student = User.query.filter_by(email="s@example.com").one()
        student.status = UserStatus.SUSPENDED.value
        db.session.commit()
    fx.fresh_identity()
    response = client.get(fx.STUDENT_FEED)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


# ===========================================================================
# There is no mutation endpoint of any HTTP method
# ===========================================================================


def test_the_student_announcement_routes_are_get_only(app):
    methods = {
        str(rule): rule.methods - {"HEAD", "OPTIONS"}
        for rule in app.url_map.iter_rules()
        if str(rule).startswith("/student/announcements")
    }
    assert methods == {
        "/student/announcements": {"GET"},
        "/student/announcements/<announcement_public_id>": {"GET"},
    }


def test_every_write_method_is_refused_on_both_student_routes(app, client):
    with app.app_context():
        group = fx.hierarchy("A")
        student = fx.enroll(group, "s@example.com")
        ann = fx.published_group(_author(), group)
        apid = ann.public_id
    fx.login_as(client, "s@example.com")
    for url in (fx.STUDENT_FEED, fx.student_detail(apid)):
        for method in ("post", "put", "patch", "delete"):
            response = getattr(client, method)(url)
            assert response.status_code == 405, (url, method)
    with app.app_context():
        # Nothing moved.
        assert Announcement.query.count() == 1
        assert Announcement.query.one().version == 1


# ===========================================================================
# Visibility -- the three scopes
# ===========================================================================


def test_a_student_sees_a_center_announcement_without_any_enrollment(app, client):
    with app.app_context():
        fx.user("s@example.com", UserRole.STUDENT.value)
        fx.published_center(_author(), title="Centre notice")
    fx.login_as(client, "s@example.com")
    assert "Centre notice" in client.get(fx.STUDENT_FEED).get_data(as_text=True)


def test_a_student_sees_a_course_announcement_through_their_enrollment(app, client):
    with app.app_context():
        group = fx.hierarchy("A")
        fx.enroll(group, "s@example.com")
        course = db.session.get(Course, group.course_id)
        fx.published_course(_author(), course, title="Course notice")
    fx.login_as(client, "s@example.com")
    assert "Course notice" in client.get(fx.STUDENT_FEED).get_data(as_text=True)


def test_a_student_sees_their_own_groups_announcement(app, client):
    with app.app_context():
        group = fx.hierarchy("A")
        fx.enroll(group, "s@example.com")
        fx.published_group(_author(), group, title="Group notice")
    fx.login_as(client, "s@example.com")
    assert "Group notice" in client.get(fx.STUDENT_FEED).get_data(as_text=True)


def test_a_student_never_sees_another_groups_announcement(app, client):
    with app.app_context():
        mine = fx.hierarchy("A")
        theirs = fx.hierarchy("B")
        fx.enroll(mine, "s@example.com")
        other = fx.published_group(_author(), theirs, title="Not for you")
        apid = other.public_id
    fx.login_as(client, "s@example.com")
    assert "Not for you" not in client.get(fx.STUDENT_FEED).get_data(as_text=True)
    assert client.get(fx.student_detail(apid)).status_code == 404


def test_a_student_never_sees_another_courses_announcement(app, client):
    with app.app_context():
        mine = fx.hierarchy("A")
        theirs = fx.hierarchy("B")
        fx.enroll(mine, "s@example.com")
        course = db.session.get(Course, theirs.course_id)
        other = fx.published_course(_author(), course, title="Other course")
        apid = other.public_id
    fx.login_as(client, "s@example.com")
    assert "Other course" not in client.get(fx.STUDENT_FEED).get_data(as_text=True)
    assert client.get(fx.student_detail(apid)).status_code == 404


def test_drafts_and_withdrawn_announcements_are_never_visible(app, client):
    with app.app_context():
        group = fx.hierarchy("A")
        fx.enroll(group, "s@example.com")
        author = _author()
        draft = fx.for_group(author, group, title="Still a draft")
        withdrawn = fx.for_group(
            author, group, status=_WITHDRAWN, title="Taken down"
        )
        center_draft = fx.center(author, title="Centre draft")
        ids = [draft.public_id, withdrawn.public_id, center_draft.public_id]
    fx.login_as(client, "s@example.com")
    html = client.get(fx.STUDENT_FEED).get_data(as_text=True)
    for hidden in ("Still a draft", "Taken down", "Centre draft"):
        assert hidden not in html
    for apid in ids:
        assert client.get(fx.student_detail(apid)).status_code == 404


def test_a_withdrawn_enrollment_revokes_access_immediately(app, client):
    with app.app_context():
        group = fx.hierarchy("A")
        fx.enroll(group, "s@example.com")
        course = db.session.get(Course, group.course_id)
        group_ann = fx.published_group(_author(), group, title="Group notice")
        course_ann = fx.published_course(
            User.query.filter_by(email="author@example.com").one(),
            course,
            title="Course notice",
        )
        gpid = group.public_id
        ids = (group_ann.public_id, course_ann.public_id)
    fx.login_as(client, "s@example.com")
    assert client.get(fx.student_detail(ids[0])).status_code == 200
    assert client.get(fx.student_detail(ids[1])).status_code == 200
    with app.app_context():
        group = Group.query.filter_by(public_id=gpid).one()
        student = User.query.filter_by(email="s@example.com").one()
        fx.withdraw_enrollment(group, student)
    html = client.get(fx.STUDENT_FEED).get_data(as_text=True)
    assert "Group notice" not in html and "Course notice" not in html
    for apid in ids:
        assert client.get(fx.student_detail(apid)).status_code == 404


@pytest.mark.parametrize(
    "archived", ["term_status", "level_status", "course_status", "group_status"]
)
def test_an_archived_link_of_the_chain_hides_the_scoped_announcements(
    app, client, archived
):
    with app.app_context():
        group = fx.hierarchy("A")
        fx.enroll(group, "s@example.com")
        author = _author()
        course = db.session.get(Course, group.course_id)
        group_ann = fx.published_group(author, group, title="Group notice")
        course_ann = fx.published_course(author, course, title="Course notice")
        fx.published_center(author, title="Centre notice")
        ids = (group_ann.public_id, course_ann.public_id)
        row = {
            "term_status": AcademicTerm.query.one(),
            "level_status": Level.query.one(),
            "course_status": course,
            "group_status": group,
        }[archived]
        row.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    fx.login_as(client, "s@example.com")
    html = client.get(fx.STUDENT_FEED).get_data(as_text=True)
    assert "Group notice" not in html
    assert "Course notice" not in html
    # The centre's own board is unaffected by one group's term ending.
    assert "Centre notice" in html
    for apid in ids:
        assert client.get(fx.student_detail(apid)).status_code == 404


def test_a_course_announcement_appears_once_for_a_student_in_several_of_its_groups(
    app, client
):
    """The authorization is a correlated ``EXISTS``, not a join, so one
    announcement is always exactly one row."""
    with app.app_context():
        term = fx.term("T")
        level = fx.level("L")
        course = fx.course(level, "C")
        group_one = fx.group(term, course, "One")
        group_two = fx.group(term, course, "Two")
        group_three = fx.group(term, course, "Three")
        student = fx.enroll(group_one, "s@example.com")
        fx.enroll_existing(group_two, student)
        fx.enroll_existing(group_three, student)
        fx.published_course(_author(), course, title="One course notice")
    fx.login_as(client, "s@example.com")
    html = client.get(fx.STUDENT_FEED).get_data(as_text=True)
    assert html.count("One course notice") == 1


def test_a_student_in_two_groups_of_one_course_sees_both_group_notices_once_each(
    app, client
):
    with app.app_context():
        term = fx.term("T")
        level = fx.level("L")
        course = fx.course(level, "C")
        group_one = fx.group(term, course, "One")
        group_two = fx.group(term, course, "Two")
        student = fx.enroll(group_one, "s@example.com")
        fx.enroll_existing(group_two, student)
        author = _author()
        fx.published_group(author, group_one, title="Notice one")
        fx.published_group(author, group_two, title="Notice two")
    fx.login_as(client, "s@example.com")
    html = client.get(fx.STUDENT_FEED).get_data(as_text=True)
    assert html.count("Notice one") == 1
    assert html.count("Notice two") == 1


def test_a_teacher_account_enrolled_by_mistake_is_not_treated_as_a_student(app, client):
    """A foreign key into ``users`` proves a row exists, never that it is a
    Student's -- so the feed is behind ``roles_required`` and every
    recipient query re-checks the role in SQL."""
    with app.app_context():
        group = fx.hierarchy("A")
        fx.enroll(group, "t@example.com", role=UserRole.TEACHER.value)
        fx.published_group(_author(), group, title="Group notice")
    fx.login_as(client, "t@example.com")
    assert client.get(fx.STUDENT_FEED).status_code == 403


# ===========================================================================
# The detail page
# ===========================================================================


def test_the_detail_page_shows_the_body_escaped_and_nothing_author_only(app, client):
    with app.app_context():
        group = fx.hierarchy("A")
        fx.enroll(group, "s@example.com")
        author = fx.user("writer@example.com", UserRole.TEACHER.value)
        ann = fx.published_group(
            author,
            group,
            title="Notice",
            body="Line one\nLine two <b>bold</b>",
        )
        apid = ann.public_id
    fx.login_as(client, "s@example.com")
    html = client.get(fx.student_detail(apid)).get_data(as_text=True)
    assert "Line one" in html and "Line two" in html
    assert "<b>bold</b>" not in html
    assert "&lt;b&gt;bold&lt;/b&gt;" in html
    # No author, no lifecycle control, no token, no form at all.
    assert "writer@example.com" not in html
    assert "announcement_state" not in html
    assert "/publish" not in html
    assert "/withdraw" not in html
    # The only form on the page is the shared header's logout button: there
    # is no announcement control of any kind for a Student to submit.
    assert html.count("<form") == 1


def test_a_guessed_public_id_is_the_same_404_as_an_unauthorized_one(app, client):
    with app.app_context():
        mine = fx.hierarchy("A")
        theirs = fx.hierarchy("B")
        fx.enroll(mine, "s@example.com")
        other = fx.published_group(_author(), theirs, title="Not yours")
        apid = other.public_id
    fx.login_as(client, "s@example.com")
    unauthorized = client.get(fx.student_detail(apid))
    invented = client.get(fx.student_detail("11111111-2222-3333-4444-555555555555"))
    assert unauthorized.status_code == invented.status_code == 404
    assert "Not yours" not in unauthorized.get_data(as_text=True)


# ===========================================================================
# Feed ordering, pagination and headers
# ===========================================================================


def test_the_feed_is_ordered_newest_publication_first_across_scopes(app, client):
    with app.app_context():
        group = fx.hierarchy("A")
        fx.enroll(group, "s@example.com")
        author = _author()
        course = db.session.get(Course, group.course_id)
        fx.published_center(author, title="Oldest", published_at=fx.NOW)
        fx.published_course(author, course, title="Middle", published_at=fx.LATER)
        fx.published_group(author, group, title="Newest", published_at=fx.LATEST)
    fx.login_as(client, "s@example.com")
    html = client.get(fx.STUDENT_FEED).get_data(as_text=True)
    assert html.index("Newest") < html.index("Middle") < html.index("Oldest")


def test_the_feed_pages_and_never_counts(app, client):
    from app.services.announcement_queries import PAGE_SIZE

    with app.app_context():
        group = fx.hierarchy("A")
        fx.enroll(group, "s@example.com")
        author = _author()
        for index in range(PAGE_SIZE + 3):
            fx.published_group(author, group, title=f"Notice {index:03d}")
    fx.login_as(client, "s@example.com")
    first = client.get(fx.STUDENT_FEED).get_data(as_text=True)
    second = client.get(f"{fx.STUDENT_FEED}?page=2").get_data(as_text=True)
    assert first.count("Notice ") >= PAGE_SIZE
    assert "Next" in first and "Previous" in second
    far = client.get(f"{fx.STUDENT_FEED}?page=9999").get_data(as_text=True)
    assert "Previous" not in far


@pytest.mark.parametrize("value", ["0", "-1", "abc", "999999999", ""])
def test_a_tampered_page_value_normalises_to_page_one(app, client, value):
    with app.app_context():
        group = fx.hierarchy("A")
        fx.enroll(group, "s@example.com")
        fx.published_group(_author(), group, title="Only one")
    fx.login_as(client, "s@example.com")
    html = client.get(f"{fx.STUDENT_FEED}?page={value}").get_data(as_text=True)
    assert "Only one" in html


def test_both_responses_are_private_and_non_cacheable(app, client):
    with app.app_context():
        group = fx.hierarchy("A")
        fx.enroll(group, "s@example.com")
        ann = fx.published_group(_author(), group)
        apid = ann.public_id
    fx.login_as(client, "s@example.com")
    for url in (fx.STUDENT_FEED, fx.student_detail(apid)):
        response = client.get(url)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "private, no-store"
        assert "Cookie" in response.headers["Vary"]


def test_no_internal_numeric_id_or_author_identity_reaches_the_markup(app, client):
    with app.app_context():
        group = fx.hierarchy("A")
        student = fx.enroll(group, "s@example.com")
        author = fx.user("writer@example.com", UserRole.TEACHER.value)
        ann = fx.published_group(author, group)
        apid = ann.public_id
        ids = (group.id, ann.id, author.id, student.id, group.course_id)
    fx.login_as(client, "s@example.com")
    for url in (fx.STUDENT_FEED, fx.student_detail(apid), "/student/dashboard"):
        html = client.get(url).get_data(as_text=True)
        assert "writer@example.com" not in html, url
        # Deliberately NOT a substring search for "/announcements/<id>": a
        # UUID public id can legitimately begin with the same digit as an
        # internal id, which would make such a check fail at random. The real
        # rule is that every announcement path segment the page emits is a
        # public id.
        for segment in re.findall(r"/announcements/([^\"'/?# ]+)", html):
            assert not segment.isdigit(), (url, segment)
            assert len(segment) == 36, (url, segment)
        assert not any(str(internal) in re.findall(r'value="([^"]*)"', html)
                       for internal in ids), url


def test_the_feed_cost_does_not_grow_with_the_number_of_announcements(app, client):
    def query_count(count):
        with app.app_context():
            db.drop_all()
            db.create_all()
            group = fx.hierarchy("A")
            fx.enroll(group, "s@example.com")
            author = _author()
            for index in range(count):
                fx.published_group(author, group, title=f"Notice {index:03d}")
        fx.login_as(client, "s@example.com")

        recorded = []

        def _rec(conn, cursor, statement, parameters, context, executemany):
            recorded.append(statement)

        with app.app_context():
            event.listen(db.engine, "before_cursor_execute", _rec)
        try:
            assert client.get(fx.STUDENT_FEED).status_code == 200
        finally:
            with app.app_context():
                event.remove(db.engine, "before_cursor_execute", _rec)
        return len([s for s in recorded if s.strip().upper().startswith("SELECT")])

    assert query_count(1) == query_count(15)


# ===========================================================================
# Navigation and the dashboard preview
# ===========================================================================


def test_the_student_portal_nav_carries_an_announcements_entry(app, client):
    with app.app_context():
        fx.user("s@example.com", UserRole.STUDENT.value)
    fx.login_as(client, "s@example.com")
    html = client.get("/student/dashboard").get_data(as_text=True)
    assert f'href="{fx.STUDENT_FEED}"' in html
    assert ">Announcements<" in html


def test_the_dashboard_previews_only_visible_announcements(app, client):
    with app.app_context():
        mine = fx.hierarchy("A")
        theirs = fx.hierarchy("B")
        fx.enroll(mine, "s@example.com")
        author = _author()
        fx.published_group(author, mine, title="Mine and standing")
        fx.published_group(author, theirs, title="Somebody elses")
        fx.for_group(author, mine, title="My groups draft")
        fx.for_group(author, mine, status=_WITHDRAWN, title="Taken down")
    fx.login_as(client, "s@example.com")
    html = client.get("/student/dashboard").get_data(as_text=True)
    assert "Mine and standing" in html
    for hidden in ("Somebody elses", "My groups draft", "Taken down"):
        assert hidden not in html


def test_the_dashboard_preview_is_capped_and_newest_first(app, client):
    from app.services.announcement_queries import DASHBOARD_PREVIEW_CAP

    with app.app_context():
        group = fx.hierarchy("A")
        fx.enroll(group, "s@example.com")
        author = _author()
        from datetime import timedelta

        for index in range(DASHBOARD_PREVIEW_CAP + 4):
            fx.published_group(
                author,
                group,
                title=f"Notice {index:03d}",
                published_at=fx.NOW + timedelta(minutes=index),
            )
    fx.login_as(client, "s@example.com")
    html = client.get("/student/dashboard").get_data(as_text=True)
    total = DASHBOARD_PREVIEW_CAP + 4
    shown = [f"Notice {i:03d}" for i in range(total) if f"Notice {i:03d}" in html]
    # Exactly the cap, and exactly the newest ones.
    assert len(shown) == DASHBOARD_PREVIEW_CAP
    assert set(shown) == {
        f"Notice {i:03d}" for i in range(total - DASHBOARD_PREVIEW_CAP, total)
    }
    # ...in newest-first order on the page.
    positions = [html.index(title) for title in sorted(shown, reverse=True)]
    assert positions == sorted(positions)


def test_a_student_with_no_enrollment_still_sees_the_centres_announcements(app, client):
    """The preview is rendered outside the "no active enrollments" branch on
    purpose: a Student between terms still belongs to the centre."""
    with app.app_context():
        fx.user("s@example.com", UserRole.STUDENT.value)
        fx.published_center(_author(), title="Centre notice")
    fx.login_as(client, "s@example.com")
    html = client.get("/student/dashboard").get_data(as_text=True)
    assert "No active enrollments" in html
    assert "Centre notice" in html


def test_the_dashboard_cost_does_not_grow_with_the_number_of_announcements(app, client):
    def query_count(count):
        with app.app_context():
            db.drop_all()
            db.create_all()
            group = fx.hierarchy("A")
            fx.enroll(group, "s@example.com")
            author = _author()
            for index in range(count):
                fx.published_group(author, group, title=f"Notice {index:03d}")
        fx.login_as(client, "s@example.com")

        recorded = []

        def _rec(conn, cursor, statement, parameters, context, executemany):
            recorded.append(statement)

        with app.app_context():
            event.listen(db.engine, "before_cursor_execute", _rec)
        try:
            assert client.get("/student/dashboard").status_code == 200
        finally:
            with app.app_context():
                event.remove(db.engine, "before_cursor_execute", _rec)
        return len([s for s in recorded if s.strip().upper().startswith("SELECT")])

    assert query_count(1) == query_count(15)
