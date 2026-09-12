"""Phase 4 / M09 -- the Administrator announcement surface.

Covers authoring in all three scopes, the closed scope/target rule, the
filters, the lifecycle and its one-way edges, the three stale-state
rejections, the absence of any recipient disclosure, and the fact that
an Administrator has no announcement mutation path anywhere else.
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
    AnnouncementScope,
    AnnouncementStatus,
    Course,
    Group,
    Notification,
    User,
    UserRole,
    UserStatus,
)

_CENTER = AnnouncementScope.CENTER.value
_COURSE = AnnouncementScope.COURSE.value
_GROUP = AnnouncementScope.GROUP.value
_DRAFT = AnnouncementStatus.DRAFT.value
_PUBLISHED = AnnouncementStatus.PUBLISHED.value
_WITHDRAWN = AnnouncementStatus.WITHDRAWN.value


def _admin(email="admin@example.com"):
    return fx.user(email, UserRole.ADMINISTRATOR.value)


# ===========================================================================
# Role authorization
# ===========================================================================


def test_anonymous_is_redirected_to_login(app, client):
    for url in (fx.ADMIN_OVERVIEW, fx.ADMIN_NEW):
        response = client.get(url)
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]


@pytest.mark.parametrize(
    "role",
    [UserRole.STUDENT.value, UserRole.TEACHER.value, UserRole.RESEARCHER.value],
)
def test_every_other_role_is_forbidden(app, client, role):
    with app.app_context():
        fx.user("other@example.com", role)
    fx.login_as(client, "other@example.com")
    for url in (fx.ADMIN_OVERVIEW, fx.ADMIN_NEW):
        assert client.get(url).status_code == 403


def test_a_suspended_administrator_cannot_reach_the_overview(app, client):
    with app.app_context():
        _admin()
    fx.login_as(client, "admin@example.com")
    assert client.get(fx.ADMIN_OVERVIEW).status_code == 200
    with app.app_context():
        row = User.query.filter_by(email="admin@example.com").one()
        row.status = UserStatus.SUSPENDED.value
        db.session.commit()
    fx.fresh_identity()
    assert client.get(fx.ADMIN_OVERVIEW).status_code == 302


def test_the_administrator_announcement_routes_are_exactly_the_six_approved_ones(app):
    rules = sorted(
        str(rule)
        for rule in app.url_map.iter_rules()
        if str(rule).startswith("/admin/") and "announcement" in str(rule)
    )
    assert rules == [
        "/admin/announcements",
        "/admin/announcements/<announcement_public_id>",
        "/admin/announcements/<announcement_public_id>/edit",
        "/admin/announcements/<announcement_public_id>/publish",
        "/admin/announcements/<announcement_public_id>/withdraw",
        "/admin/announcements/new",
    ]


def test_the_navigation_carries_an_announcements_entry(app, client):
    with app.app_context():
        _admin()
    fx.login_as(client, "admin@example.com")
    html = client.get("/admin/dashboard").get_data(as_text=True)
    assert f'href="{fx.ADMIN_OVERVIEW}"' in html
    assert ">Announcements<" in html


# ===========================================================================
# Authoring in all three scopes
# ===========================================================================


def test_an_administrator_creates_a_center_draft(app, client):
    with app.app_context():
        _admin()
    fx.login_as(client, "admin@example.com")
    apid = fx.create_admin_draft(client, _CENTER, title="Holiday")
    assert apid is not None
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        assert (row.scope, row.course_id, row.group_id) == (_CENTER, None, None)
        assert row.status == _DRAFT and row.version == 1


def test_an_administrator_creates_a_course_draft(app, client):
    with app.app_context():
        _admin()
        group = fx.hierarchy("A")
        course = db.session.get(Course, group.course_id)
        cpid, course_id = course.public_id, course.id
    fx.login_as(client, "admin@example.com")
    apid = fx.create_admin_draft(client, _COURSE, target=cpid, title="Course notice")
    assert apid is not None
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        assert (row.scope, row.course_id, row.group_id) == (_COURSE, course_id, None)


def test_an_administrator_creates_a_group_draft(app, client):
    with app.app_context():
        _admin()
        group = fx.hierarchy("A")
        gpid, group_id = group.public_id, group.id
    fx.login_as(client, "admin@example.com")
    apid = fx.create_admin_draft(client, _GROUP, target=gpid, title="Group notice")
    assert apid is not None
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        assert (row.scope, row.course_id, row.group_id) == (_GROUP, None, group_id)


@pytest.mark.parametrize(
    "scope,course,group_value",
    [
        (_CENTER, "TARGET_COURSE", ""),          # centre may carry no target
        (_CENTER, "", "TARGET_GROUP"),
        (_COURSE, "", ""),                        # course needs a course
        (_COURSE, "", "TARGET_GROUP"),
        (_COURSE, "TARGET_COURSE", "TARGET_GROUP"),
        (_GROUP, "", ""),                         # group needs a group
        (_GROUP, "TARGET_COURSE", ""),
        (_GROUP, "TARGET_COURSE", "TARGET_GROUP"),
    ],
)
def test_every_illegal_scope_target_combination_is_refused(
    app, client, scope, course, group_value
):
    with app.app_context():
        _admin()
        group = fx.hierarchy("A")
        course_row = db.session.get(Course, group.course_id)
        mapping = {
            "TARGET_COURSE": course_row.public_id,
            "TARGET_GROUP": group.public_id,
            "": "",
        }
    fx.login_as(client, "admin@example.com")
    token = fx.token_from(client, fx.ADMIN_NEW)
    response = client.post(
        fx.ADMIN_NEW,
        data={
            "scope": scope,
            "course": mapping[course],
            "group": mapping[group_value],
            "title": "Title",
            "body": "Body.",
            "announcement_state": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 200  # re-rendered with the error
    with app.app_context():
        assert Announcement.query.count() == 0


@pytest.mark.parametrize("scope", [_COURSE, _GROUP])
def test_an_unknown_or_archived_target_is_refused(app, client, scope):
    with app.app_context():
        _admin()
        group = fx.hierarchy("A", group_status=AcademicStatus.ARCHIVED.value)
        course = db.session.get(Course, group.course_id)
        archived_target = course.public_id if scope == _COURSE else group.public_id
        if scope == _COURSE:
            course.status = AcademicStatus.ARCHIVED.value
            db.session.commit()
    fx.login_as(client, "admin@example.com")
    for target in ("11111111-2222-3333-4444-555555555555", archived_target):
        assert fx.create_admin_draft(client, scope, target=target) is None
    with app.app_context():
        assert Announcement.query.count() == 0


def test_an_unknown_scope_is_refused_by_the_select_itself(app, client):
    with app.app_context():
        _admin()
    fx.login_as(client, "admin@example.com")
    token = fx.token_from(client, fx.ADMIN_NEW)
    response = client.post(
        fx.ADMIN_NEW,
        data={
            "scope": "level",
            "course": "",
            "group": "",
            "title": "T",
            "body": "B",
            "announcement_state": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 200
    with app.app_context():
        assert Announcement.query.count() == 0


def test_the_author_is_the_acting_administrator(app, client):
    with app.app_context():
        admin = _admin()
        admin_id = admin.id
    fx.login_as(client, "admin@example.com")
    apid = fx.create_admin_draft(client, _CENTER)
    with app.app_context():
        assert Announcement.query.filter_by(public_id=apid).one().author_id == admin_id


# ===========================================================================
# Editing, including retargeting a draft
# ===========================================================================


def test_a_draft_can_be_retargeted_and_the_version_moves(app, client):
    with app.app_context():
        admin = _admin()
        group = fx.hierarchy("A")
        course = db.session.get(Course, group.course_id)
        ann = fx.center(admin, title="Was centre")
        apid, cpid, course_id = ann.public_id, course.public_id, course.id
    fx.login_as(client, "admin@example.com")
    token = fx.token_from(client, fx.admin_edit(apid))
    response = client.post(
        fx.admin_edit(apid),
        data={
            "scope": _COURSE,
            "course": cpid,
            "group": "",
            "title": "Now a course notice",
            "body": "Body.",
            "announcement_state": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        assert (row.scope, row.course_id, row.group_id) == (_COURSE, course_id, None)
        assert row.version == 2


def test_a_no_op_admin_save_moves_no_version_and_no_timestamp(app, client):
    with app.app_context():
        admin = _admin()
        ann = fx.center(admin, title="Same", body="Same body")
        apid = ann.public_id
        before = (ann.version, ann.updated_at)
    fx.login_as(client, "admin@example.com")
    token = fx.token_from(client, fx.admin_edit(apid))
    response = client.post(
        fx.admin_edit(apid),
        data={
            "scope": _CENTER,
            "course": "",
            "group": "",
            "title": " Same ",
            "body": "Same body\n",
            "announcement_state": token,
        },
        follow_redirects=True,
    )
    assert "Nothing was changed" in response.get_data(as_text=True)
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        assert (row.version, row.updated_at) == before


def test_a_stale_admin_edit_token_is_rejected_without_overwriting(app, client):
    with app.app_context():
        admin = _admin()
        ann = fx.center(admin, title="Original", body="Original body")
        apid = ann.public_id
    fx.login_as(client, "admin@example.com")
    token = fx.token_from(client, fx.admin_edit(apid))
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        row.title = "Changed elsewhere"
        row.version = row.version + 1
        db.session.commit()
    response = client.post(
        fx.admin_edit(apid),
        data={
            "scope": _CENTER,
            "course": "",
            "group": "",
            "title": "Mine",
            "body": "Mine",
            "announcement_state": token,
        },
        follow_redirects=True,
    )
    assert "changed by someone else" in response.get_data(as_text=True)
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        assert (row.title, row.body, row.version) == (
            "Changed elsewhere", "Original body", 2,
        )


def test_a_retarget_attempted_against_an_already_retargeted_draft_is_rejected(app, client):
    """The draft token binds the **stored** scope and target, so an edit
    written against the old target cannot land on the new one."""
    with app.app_context():
        admin = _admin()
        group = fx.hierarchy("A")
        course = db.session.get(Course, group.course_id)
        ann = fx.center(admin, title="Centre")
        apid, cpid, gpid = ann.public_id, course.public_id, group.public_id
    fx.login_as(client, "admin@example.com")
    token = fx.token_from(client, fx.admin_edit(apid))
    with app.app_context():
        # Resolve the new target BEFORE mutating, so the ORM's autoflush
        # cannot try to write a half-changed row past the scope/target CHECK.
        group_id = Group.query.filter_by(public_id=gpid).one().id
        row = Announcement.query.filter_by(public_id=apid).one()
        row.group_id = group_id
        row.scope = _GROUP
        row.version = row.version + 1
        db.session.commit()
    response = client.post(
        fx.admin_edit(apid),
        data={
            "scope": _COURSE,
            "course": cpid,
            "group": "",
            "title": "Centre",
            "body": "Body.",
            "announcement_state": token,
        },
        follow_redirects=True,
    )
    assert "changed by someone else" in response.get_data(as_text=True)
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        assert row.scope == _GROUP


# ===========================================================================
# Publication and withdrawal
# ===========================================================================


def test_publishing_a_center_draft_makes_it_readable_and_freezes_it(app, client):
    with app.app_context():
        admin = _admin()
        fx.user("s@example.com", UserRole.STUDENT.value)
        ann = fx.center(admin, title="Holiday", body="Closed Monday.")
        apid = ann.public_id
    fx.login_as(client, "admin@example.com")
    assert fx.publish_admin_draft(client, apid).status_code == 302
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        assert row.status == _PUBLISHED and row.published_at is not None
        assert row.version == 2

    fx.login_as(client, "s@example.com")
    assert client.get(fx.student_detail(apid)).status_code == 200

    fx.login_as(client, "admin@example.com")
    assert client.get(fx.admin_edit(apid), follow_redirects=False).status_code == 302
    token = fx.token_from(client, fx.admin_detail(apid))
    client.post(
        fx.admin_edit(apid),
        data={
            "scope": _CENTER, "course": "", "group": "",
            "title": "Rewritten", "body": "Rewritten",
            "announcement_state": token,
        },
        follow_redirects=True,
    )
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        assert (row.title, row.body) == ("Holiday", "Closed Monday.")


def test_publishing_twice_changes_nothing_the_second_time(app, client):
    with app.app_context():
        admin = _admin()
        ann = fx.center(admin)
        apid = ann.public_id
    fx.login_as(client, "admin@example.com")
    fx.publish_admin_draft(client, apid)
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        before = (row.published_at, row.version)
    response = client.post(fx.admin_publish(apid), data={}, follow_redirects=True)
    assert "already published" in response.get_data(as_text=True)
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        assert (row.published_at, row.version) == before


def test_withdrawing_hides_it_immediately_and_is_permanent(app, client):
    with app.app_context():
        admin = _admin()
        fx.user("s@example.com", UserRole.STUDENT.value)
        ann = fx.center(admin, status=_PUBLISHED, title="Standing")
        apid = ann.public_id
    fx.login_as(client, "s@example.com")
    assert client.get(fx.student_detail(apid)).status_code == 200

    fx.login_as(client, "admin@example.com")
    assert fx.withdraw_admin_announcement(client, apid).status_code == 302
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        assert row.status == _WITHDRAWN and row.withdrawn_at >= row.published_at

    fx.login_as(client, "s@example.com")
    assert client.get(fx.student_detail(apid)).status_code == 404


def test_withdrawal_requires_the_confirmation_box(app, client):
    with app.app_context():
        admin = _admin()
        ann = fx.center(admin, status=_PUBLISHED)
        apid = ann.public_id
    fx.login_as(client, "admin@example.com")
    fx.withdraw_admin_announcement(client, apid, confirm=False)
    with app.app_context():
        assert Announcement.query.filter_by(public_id=apid).one().status == _PUBLISHED


def test_a_stale_withdraw_token_is_rejected(app, client):
    with app.app_context():
        admin = _admin()
        ann = fx.center(admin, status=_PUBLISHED)
        apid = ann.public_id
    fx.login_as(client, "admin@example.com")
    token = fx.token_from(client, fx.admin_detail(apid))
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        row.version = row.version + 1
        db.session.commit()
    response = client.post(
        fx.admin_withdraw(apid),
        data={"announcement_state": token, "confirm": "yes"},
        follow_redirects=True,
    )
    assert "changed by someone else" in response.get_data(as_text=True)
    with app.app_context():
        assert Announcement.query.filter_by(public_id=apid).one().status == _PUBLISHED


def test_a_withdrawn_announcement_can_never_be_edited_or_republished(app, client):
    with app.app_context():
        admin = _admin()
        ann = fx.center(admin, status=_WITHDRAWN, title="Gone")
        apid = ann.public_id
        before = (ann.title, ann.version, ann.published_at, ann.withdrawn_at)
    fx.login_as(client, "admin@example.com")
    assert "permanent" in client.get(
        fx.admin_edit(apid), follow_redirects=True
    ).get_data(as_text=True)
    client.post(
        fx.admin_edit(apid),
        data={
            "scope": _CENTER, "course": "", "group": "",
            "title": "Back", "body": "Back", "announcement_state": "",
        },
        follow_redirects=True,
    )
    client.post(fx.admin_publish(apid), data={}, follow_redirects=True)
    client.post(
        fx.admin_withdraw(apid),
        data={"confirm": "yes", "announcement_state": ""},
        follow_redirects=True,
    )
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        assert (row.title, row.version, row.published_at, row.withdrawn_at) == before
        assert row.status == _WITHDRAWN


def test_withdrawal_stays_possible_after_the_target_is_archived(app, client):
    with app.app_context():
        admin = _admin()
        group = fx.hierarchy("A")
        ann = fx.for_group(admin, group, status=_PUBLISHED)
        apid = ann.public_id
    fx.login_as(client, "admin@example.com")
    token = fx.token_from(client, fx.admin_detail(apid))
    with app.app_context():
        AcademicTerm.query.one().status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    response = client.post(
        fx.admin_withdraw(apid),
        data={"announcement_state": token, "confirm": "yes"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    with app.app_context():
        assert Announcement.query.filter_by(public_id=apid).one().status == _WITHDRAWN


def test_a_draft_whose_target_was_archived_cannot_be_published(app, client):
    with app.app_context():
        admin = _admin()
        group = fx.hierarchy("A")
        ann = fx.for_group(admin, group, title="Aimed at an archived group")
        apid = ann.public_id
    fx.login_as(client, "admin@example.com")
    with app.app_context():
        Group.query.one().status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    html = client.get(fx.admin_detail(apid)).get_data(as_text=True)
    assert "no longer active" in html
    assert "/publish" not in html
    response = client.post(fx.admin_publish(apid), data={}, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        assert Announcement.query.filter_by(public_id=apid).one().status == _DRAFT


# ===========================================================================
# The overview, its filters and what it never shows
# ===========================================================================


def test_the_overview_lists_all_three_scopes_and_all_three_states(app, client):
    with app.app_context():
        admin = _admin()
        group = fx.hierarchy("A")
        course = db.session.get(Course, group.course_id)
        fx.center(admin, title="Centre draft")
        fx.published_course(admin, course, title="Course published")
        fx.for_group(admin, group, status=_WITHDRAWN, title="Group withdrawn")
    fx.login_as(client, "admin@example.com")
    html = client.get(fx.ADMIN_OVERVIEW).get_data(as_text=True)
    for title in ("Centre draft", "Course published", "Group withdrawn"):
        assert title in html


@pytest.mark.parametrize(
    "query,expected,hidden",
    [
        ("scope=center", "Centre draft", "Course published"),
        ("scope=course", "Course published", "Centre draft"),
        ("status=draft", "Centre draft", "Course published"),
        ("status=published", "Course published", "Centre draft"),
        ("status=withdrawn", "Group withdrawn", "Centre draft"),
        ("scope=group&status=withdrawn", "Group withdrawn", "Course published"),
    ],
)
def test_the_filters_narrow_correctly(app, client, query, expected, hidden):
    with app.app_context():
        admin = _admin()
        group = fx.hierarchy("A")
        course = db.session.get(Course, group.course_id)
        fx.center(admin, title="Centre draft")
        fx.published_course(admin, course, title="Course published")
        fx.for_group(admin, group, status=_WITHDRAWN, title="Group withdrawn")
    fx.login_as(client, "admin@example.com")
    html = client.get(f"{fx.ADMIN_OVERVIEW}?{query}").get_data(as_text=True)
    assert expected in html
    assert hidden not in html


@pytest.mark.parametrize(
    "query", ["scope=level", "status=archived", "scope=';DROP", "status="]
)
def test_an_unrecognised_filter_value_is_dropped_rather_than_guessed(app, client, query):
    with app.app_context():
        admin = _admin()
        fx.center(admin, title="Centre draft")
        fx.center(admin, status=_PUBLISHED, title="Centre published")
    fx.login_as(client, "admin@example.com")
    response = client.get(f"{fx.ADMIN_OVERVIEW}?{query}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Centre draft" in html and "Centre published" in html


def test_the_overview_pages_and_never_counts(app, client):
    from app.services.announcement_queries import PAGE_SIZE

    with app.app_context():
        admin = _admin()
        for index in range(PAGE_SIZE + 3):
            fx.center(admin, title=f"Notice {index:03d}")
    fx.login_as(client, "admin@example.com")
    first = client.get(fx.ADMIN_OVERVIEW).get_data(as_text=True)
    second = client.get(f"{fx.ADMIN_OVERVIEW}?page=2").get_data(as_text=True)
    assert first.count("Notice ") >= PAGE_SIZE
    assert "Next" in first and "Previous" in second


def test_no_recipient_or_notification_row_appears_on_any_administrator_page(app, client):
    with app.app_context():
        admin = _admin()
        group = fx.hierarchy("A")
        student = fx.enroll(group, "student@example.com", name="Sally Student")
        teacher = fx.assign(group, "teacher@example.com", name="Terry Teacher")
        ann = fx.for_group(admin, group)
        apid = ann.public_id
    fx.login_as(client, "admin@example.com")
    fx.publish_admin_draft(client, apid)
    with app.app_context():
        assert Notification.query.count() == 2  # the student and the teacher
    with app.app_context():
        notification_ids = [row.public_id for row in Notification.query.all()]
        recipient_ids = [row.recipient_id for row in Notification.query.all()]
    for url in (fx.ADMIN_OVERVIEW, fx.admin_detail(apid)):
        html = client.get(url).get_data(as_text=True)
        # No recipient is named, by display name, by address or by id...
        assert "Sally Student" not in html, url
        assert "Terry Teacher" not in html, url
        assert "student@example.com" not in html, url
        assert "teacher@example.com" not in html, url
        for recipient_id in recipient_ids:
            assert f">{recipient_id}<" not in html, url
        # ...and no individual notification row is exposed either.
        for notification_id in notification_ids:
            assert notification_id not in html, url
        assert "/notifications/" not in html, url


def test_every_administrator_response_is_private_and_non_cacheable(app, client):
    with app.app_context():
        admin = _admin()
        ann = fx.center(admin)
        apid = ann.public_id
    fx.login_as(client, "admin@example.com")
    for url in (fx.ADMIN_OVERVIEW, fx.ADMIN_NEW, fx.admin_detail(apid), fx.admin_edit(apid)):
        response = client.get(url)
        assert response.status_code == 200, url
        assert response.headers["Cache-Control"] == "private, no-store", url
        assert "Cookie" in response.headers["Vary"], url


def test_no_internal_numeric_id_appears_in_the_administrator_markup(app, client):
    with app.app_context():
        admin = _admin()
        group = fx.hierarchy("A")
        ann = fx.for_group(admin, group)
        apid = ann.public_id
        ids = (group.id, ann.id, admin.id, group.course_id)
    fx.login_as(client, "admin@example.com")
    for url in (fx.ADMIN_OVERVIEW, fx.admin_detail(apid), fx.admin_edit(apid), fx.ADMIN_NEW):
        html = client.get(url).get_data(as_text=True)
        # Deliberately NOT a substring search for "/announcements/<id>": a
        # UUID public id can legitimately begin with the same digit as an
        # internal id, which would make such a check fail at random. The real
        # rule is that every announcement path segment the page emits is a
        # public id (or the create route's own literal "new"), and no form
        # value is a bare internal id.
        for segment in re.findall(r"/announcements/([^\"'/?# ]+)", html):
            assert not segment.isdigit(), (url, segment)
            assert segment == "new" or len(segment) == 36, (url, segment)
        values = set(re.findall(r'value="([^"]*)"', html))
        for internal in ids:
            assert str(internal) not in values, (url, internal)


def test_the_overview_cost_does_not_grow_with_the_number_of_announcements(app, client):
    def query_count(count):
        with app.app_context():
            db.drop_all()
            db.create_all()
            admin = _admin()
            group = fx.hierarchy("A")
            for index in range(count):
                fx.for_group(admin, group, title=f"Notice {index:03d}")
        fx.login_as(client, "admin@example.com")

        recorded = []

        def _rec(conn, cursor, statement, parameters, context, executemany):
            recorded.append(statement)

        with app.app_context():
            event.listen(db.engine, "before_cursor_execute", _rec)
        try:
            assert client.get(fx.ADMIN_OVERVIEW).status_code == 200
        finally:
            with app.app_context():
                event.remove(db.engine, "before_cursor_execute", _rec)
        return len([s for s in recorded if s.strip().upper().startswith("SELECT")])

    assert query_count(1) == query_count(15)


def test_an_unknown_announcement_public_id_is_a_404(app, client):
    with app.app_context():
        _admin()
    fx.login_as(client, "admin@example.com")
    for url in (
        fx.admin_detail("nope"),
        fx.admin_edit("nope"),
    ):
        assert client.get(url).status_code == 404
    assert client.post(fx.admin_publish("nope"), data={}).status_code == 404
    assert client.post(fx.admin_withdraw("nope"), data={}).status_code == 404
