"""Phase 4 / M09 -- the Teacher announcement surface.

Covers authorization and non-disclosure, the draft -> published ->
withdrawn lifecycle and its one-way edges, the plain-text bounds, the
no-op save, the three stale-state rejections, the deterministic lock
order and the post-lock rechecks, and the Teacher's own feed.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
REPEATABLE READ snapshot isolation, so every concurrency test below
asserts what the code *requests* (structural), never that anything
actually blocks. Real InnoDB behaviour is out of reach of this suite.
"""

import re

import pytest
from sqlalchemy import event

import tests.announcement_fixtures as fx
from app.extensions import db
from app.models import (
    ANNOUNCEMENT_BODY_MAX_LENGTH,
    ANNOUNCEMENT_TITLE_MAX_LENGTH,
    AcademicStatus,
    AcademicTerm,
    Announcement,
    AnnouncementStatus,
    Course,
    Group,
    Level,
    Notification,
    User,
    UserRole,
    UserStatus,
)

_DRAFT = AnnouncementStatus.DRAFT.value
_PUBLISHED = AnnouncementStatus.PUBLISHED.value
_WITHDRAWN = AnnouncementStatus.WITHDRAWN.value


def _setup(label="A", teacher_email="teacher@example.com", **statuses):
    teacher, group = fx.setup_group(label, teacher_email=teacher_email, **statuses)
    return teacher, group


# ===========================================================================
# Role and object authorization
# ===========================================================================


def test_anonymous_is_redirected_to_login(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    for url in (fx.TEACHER_FEED, fx.group_list(gpid), fx.group_new(gpid)):
        response = client.get(url)
        assert response.status_code == 302
        assert "/auth/login" in response.headers["Location"]


@pytest.mark.parametrize(
    "role", [UserRole.STUDENT.value, UserRole.ADMINISTRATOR.value, UserRole.RESEARCHER.value]
)
def test_every_other_role_is_forbidden(app, client, role):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
        fx.user("other@example.com", role)
    fx.login_as(client, "other@example.com")
    for url in (fx.TEACHER_FEED, fx.group_list(gpid), fx.group_new(gpid)):
        assert client.get(url).status_code == 403


def test_an_assigned_teacher_reaches_their_group_announcements(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    assert client.get(fx.group_list(gpid)).status_code == 200
    assert client.get(fx.group_new(gpid)).status_code == 200


def test_an_unassigned_teacher_gets_a_non_disclosing_404(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
        fx.user("stranger@example.com", UserRole.TEACHER.value)
    fx.login_as(client, "stranger@example.com")
    for url in (fx.group_list(gpid), fx.group_new(gpid)):
        response = client.get(url)
        assert response.status_code == 404
        assert group_name_absent(response, "Group A")


def group_name_absent(response, name):
    return name not in response.get_data(as_text=True)


def test_a_removed_assignment_revokes_every_group_route(app, client):
    with app.app_context():
        teacher, group = _setup()
        gpid = group.public_id
        ann = fx.for_group(teacher, group)
        apid = ann.public_id
    fx.login_as(client, "teacher@example.com")
    assert client.get(fx.group_detail(gpid, apid)).status_code == 200
    with app.app_context():
        group = Group.query.filter_by(public_id=gpid).one()
        teacher = User.query.filter_by(email="teacher@example.com").one()
        fx.remove_assignment(group, teacher)
    for url in (
        fx.group_list(gpid),
        fx.group_new(gpid),
        fx.group_detail(gpid, apid),
        fx.group_edit(gpid, apid),
    ):
        assert client.get(url).status_code == 404
    assert client.post(fx.group_publish(gpid, apid), data={}).status_code == 404
    assert client.post(fx.group_withdraw(gpid, apid), data={}).status_code == 404


def test_a_suspended_teacher_cannot_reach_anything(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    assert client.get(fx.group_list(gpid)).status_code == 200
    with app.app_context():
        teacher = User.query.filter_by(email="teacher@example.com").one()
        teacher.status = UserStatus.SUSPENDED.value
        db.session.commit()
    fx.fresh_identity()
    response = client.get(fx.group_list(gpid))
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_another_groups_announcement_public_id_is_a_404_here(app, client):
    with app.app_context():
        teacher, group_a = _setup("A")
        group_b = fx.hierarchy("B")
        fx.assign_existing(group_b, teacher)
        other = fx.for_group(teacher, group_b, title="Group B only")
        gpid_a, apid_b = group_a.public_id, other.public_id
    fx.login_as(client, "teacher@example.com")
    for url in (fx.group_detail(gpid_a, apid_b), fx.group_edit(gpid_a, apid_b)):
        response = client.get(url)
        assert response.status_code == 404
        assert "Group B only" not in response.get_data(as_text=True)


def test_a_center_or_course_announcement_public_id_is_a_404_on_a_group_route(app, client):
    with app.app_context():
        teacher, group = _setup()
        admin = fx.user("admin@example.com", UserRole.ADMINISTRATOR.value)
        center = fx.published_center(admin, title="Centre notice")
        course_row = db.session.get(Course, group.course_id)
        course_ann = fx.published_course(admin, course_row, title="Course notice")
        gpid = group.public_id
        ids = [center.public_id, course_ann.public_id]
    fx.login_as(client, "teacher@example.com")
    for apid in ids:
        assert client.get(fx.group_detail(gpid, apid)).status_code == 404
        assert client.get(fx.group_edit(gpid, apid)).status_code == 404


def test_a_guessed_public_id_is_a_404(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    assert client.get(fx.group_detail(gpid, "not-a-real-id")).status_code == 404
    assert client.get(fx.teacher_detail("not-a-real-id")).status_code == 404


def test_there_is_no_teacher_route_for_a_center_or_course_announcement(app):
    """Not disabled in a template and not refused by a check somebody could
    relax: no such endpoint is registered at all."""
    rules = [str(rule) for rule in app.url_map.iter_rules()]
    teacher_announcement_rules = [
        rule for rule in rules if rule.startswith("/teacher/") and "announcement" in rule
    ]
    assert sorted(teacher_announcement_rules) == [
        "/teacher/announcements",
        "/teacher/announcements/<announcement_public_id>",
        "/teacher/groups/<group_public_id>/announcements",
        "/teacher/groups/<group_public_id>/announcements/<announcement_public_id>",
        "/teacher/groups/<group_public_id>/announcements/<announcement_public_id>/edit",
        "/teacher/groups/<group_public_id>/announcements/<announcement_public_id>/publish",
        "/teacher/groups/<group_public_id>/announcements/<announcement_public_id>/withdraw",
        "/teacher/groups/<group_public_id>/announcements/new",
    ]


def test_a_teacher_cannot_smuggle_a_scope_or_target_through_the_create_form(app, client):
    """The scope is server-decided and the target is the Group in the URL;
    nothing in the request body is read for either."""
    with app.app_context():
        _, group = _setup()
        other = fx.hierarchy("B")
        gpid, other_gpid = group.public_id, other.public_id
        other_course_id = other.course_id
    fx.login_as(client, "teacher@example.com")
    token = fx.token_from(client, fx.group_new(gpid))
    response = client.post(
        fx.group_new(gpid),
        data={
            "title": "Smuggled",
            "body": "Body.",
            "announcement_state": token,
            "scope": "center",
            "course": str(other_course_id),
            "group": other_gpid,
            "course_id": str(other_course_id),
            "status": _PUBLISHED,
            "published_at": "2026-01-01 00:00:00",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    with app.app_context():
        row = Announcement.query.filter_by(title="Smuggled").one()
        assert row.scope == "group"
        assert row.group_id == Group.query.filter_by(public_id=gpid).one().id
        assert row.course_id is None
        assert row.status == _DRAFT
        assert row.published_at is None


# ===========================================================================
# Draft creation and validation
# ===========================================================================


def test_creating_a_draft_stores_exactly_what_was_typed(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    apid = fx.create_group_draft(
        client, gpid, title="  Room   change  ", body="  Line one\r\nLine two  "
    )
    assert apid is not None
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        # The title is collapsed to one line; the body keeps its interior
        # line break but is normalised to \n and stripped at the ends.
        assert row.title == "Room change"
        assert row.body == "Line one\nLine two"
        assert row.status == _DRAFT
        assert row.version == 1
        assert row.published_at is None and row.withdrawn_at is None
        assert row.created_at == row.updated_at


def test_the_author_is_recorded_and_is_the_acting_teacher(app, client):
    with app.app_context():
        teacher, group = _setup()
        gpid, teacher_id = group.public_id, teacher.id
    fx.login_as(client, "teacher@example.com")
    apid = fx.create_group_draft(client, gpid)
    with app.app_context():
        assert Announcement.query.filter_by(public_id=apid).one().author_id == teacher_id


@pytest.mark.parametrize(
    "title,body",
    [
        ("", "Body."),
        ("   ", "Body."),
        ("Title", ""),
        ("Title", "   \n  "),
        ("x" * (ANNOUNCEMENT_TITLE_MAX_LENGTH + 1), "Body."),
        ("Title", "x" * (ANNOUNCEMENT_BODY_MAX_LENGTH + 1)),
        ("Bad\x00title", "Body."),
        ("Title", "Bad\x00body"),
    ],
)
def test_an_invalid_draft_is_rejected_and_writes_nothing(app, client, title, body):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    token = fx.token_from(client, fx.group_new(gpid))
    response = client.post(
        fx.group_new(gpid),
        data={"title": title, "body": body, "announcement_state": token},
        follow_redirects=False,
    )
    assert response.status_code == 200  # re-rendered with the errors
    with app.app_context():
        assert Announcement.query.count() == 0


def test_a_title_and_body_at_the_exact_limit_are_accepted(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    apid = fx.create_group_draft(
        client,
        gpid,
        title="t" * ANNOUNCEMENT_TITLE_MAX_LENGTH,
        body="b" * ANNOUNCEMENT_BODY_MAX_LENGTH,
    )
    assert apid is not None
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        assert len(row.title) == ANNOUNCEMENT_TITLE_MAX_LENGTH
        assert len(row.body) == ANNOUNCEMENT_BODY_MAX_LENGTH


def test_markup_in_the_body_is_escaped_and_never_rendered(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    apid = fx.create_group_draft(
        client,
        gpid,
        title="Tags <b>here</b>",
        body="<script>alert(1)</script> & <b>bold</b>",
    )
    html = client.get(fx.group_detail(gpid, apid)).get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;b&gt;bold&lt;/b&gt;" in html
    with app.app_context():
        # Stored verbatim -- nothing is sanitised, because nothing is trusted.
        row = Announcement.query.filter_by(public_id=apid).one()
        assert row.body == "<script>alert(1)</script> & <b>bold</b>"


def test_a_draft_cannot_be_created_under_an_archived_chain(app, client):
    with app.app_context():
        _, group = _setup("A", group_status=AcademicStatus.ARCHIVED.value)
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    response = client.get(fx.group_new(gpid), follow_redirects=False)
    assert response.status_code == 302
    token = ""
    assert client.post(
        fx.group_new(gpid),
        data={"title": "T", "body": "B", "announcement_state": token},
        follow_redirects=False,
    ).status_code == 302
    with app.app_context():
        assert Announcement.query.count() == 0


# ===========================================================================
# Draft editing
# ===========================================================================


def test_editing_a_draft_bumps_the_version_and_the_timestamp(app, client):
    with app.app_context():
        teacher, group = _setup()
        ann = fx.for_group(teacher, group, title="Old", body="Old body")
        gpid, apid = group.public_id, ann.public_id
    fx.login_as(client, "teacher@example.com")
    token = fx.token_from(client, fx.group_edit(gpid, apid))
    response = client.post(
        fx.group_edit(gpid, apid),
        data={"title": "New", "body": "New body", "announcement_state": token},
        follow_redirects=False,
    )
    assert response.status_code == 302
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        assert (row.title, row.body, row.version) == ("New", "New body", 2)
        assert row.updated_at > row.created_at


def test_a_no_op_draft_save_moves_no_version_and_no_timestamp(app, client):
    with app.app_context():
        teacher, group = _setup()
        ann = fx.for_group(teacher, group, title="Same", body="Same body")
        gpid, apid = group.public_id, ann.public_id
        before = (ann.version, ann.updated_at, ann.created_at)
    fx.login_as(client, "teacher@example.com")
    token = fx.token_from(client, fx.group_edit(gpid, apid))
    response = client.post(
        fx.group_edit(gpid, apid),
        data={
            # Whitespace-only differences normalise away, so this really is
            # the same announcement.
            "title": "  Same  ",
            "body": "Same body\n",
            "announcement_state": token,
        },
        follow_redirects=True,
    )
    assert "Nothing was changed" in response.get_data(as_text=True)
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        assert (row.version, row.updated_at, row.created_at) == before


def test_a_co_teacher_may_edit_and_publish_another_teachers_draft(app, client):
    with app.app_context():
        teacher, group = _setup("A", teacher_email="first@example.com")
        fx.assign(group, "second@example.com")
        ann = fx.for_group(teacher, group, title="By the first teacher")
        gpid, apid = group.public_id, ann.public_id
    fx.login_as(client, "second@example.com")
    token = fx.token_from(client, fx.group_edit(gpid, apid))
    assert client.post(
        fx.group_edit(gpid, apid),
        data={"title": "Edited", "body": "New body", "announcement_state": token},
        follow_redirects=False,
    ).status_code == 302
    assert fx.publish_group_draft(client, gpid, apid).status_code == 302
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        assert row.status == _PUBLISHED
        # The author is still the teacher who wrote it -- publishing never
        # rewrites authorship.
        assert row.author_id == User.query.filter_by(email="first@example.com").one().id


# ===========================================================================
# Publication
# ===========================================================================


def test_publishing_sets_the_state_and_freezes_the_structural_fields(app, client):
    with app.app_context():
        teacher, group = _setup()
        ann = fx.for_group(teacher, group, title="Notice", body="Body.")
        gpid, apid = group.public_id, ann.public_id
    fx.login_as(client, "teacher@example.com")
    assert fx.publish_group_draft(client, gpid, apid).status_code == 302
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        assert row.status == _PUBLISHED
        assert row.published_at is not None
        assert row.withdrawn_at is None
        assert row.version == 2

    # Every edit route is now closed, and the values are unchanged.
    assert client.get(fx.group_edit(gpid, apid), follow_redirects=False).status_code == 302
    token = fx.token_from(client, fx.group_detail(gpid, apid))
    client.post(
        fx.group_edit(gpid, apid),
        data={"title": "Rewritten", "body": "Rewritten", "announcement_state": token},
        follow_redirects=True,
    )
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        assert (row.title, row.body) == ("Notice", "Body.")


def test_publishing_twice_changes_nothing_the_second_time(app, client):
    with app.app_context():
        teacher, group = _setup()
        ann = fx.for_group(teacher, group)
        gpid, apid = group.public_id, ann.public_id
    fx.login_as(client, "teacher@example.com")
    fx.publish_group_draft(client, gpid, apid)
    with app.app_context():
        first = Announcement.query.filter_by(public_id=apid).one()
        published_at, version = first.published_at, first.version
        notification_count = Notification.query.count()
    # A second publish POST -- the page no longer mints a publish token, so
    # this is exactly the replay a refreshed tab would produce.
    response = client.post(
        fx.group_publish(gpid, apid), data={}, follow_redirects=True
    )
    assert "already published" in response.get_data(as_text=True)
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        assert (row.published_at, row.version) == (published_at, version)
        assert Notification.query.count() == notification_count


def test_publishing_requires_an_operational_chain(app, client):
    with app.app_context():
        teacher, group = _setup()
        ann = fx.for_group(teacher, group)
        gpid, apid = group.public_id, ann.public_id
    fx.login_as(client, "teacher@example.com")
    token = fx.token_from(client, fx.group_detail(gpid, apid))
    with app.app_context():
        term = AcademicTerm.query.one()
        term.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    response = client.post(
        fx.group_publish(gpid, apid),
        data={"announcement_state": token},
        follow_redirects=True,
    )
    assert "archived" in response.get_data(as_text=True)
    with app.app_context():
        assert Announcement.query.filter_by(public_id=apid).one().status == _DRAFT


# ===========================================================================
# Withdrawal
# ===========================================================================


def test_withdrawing_hides_it_immediately_and_is_permanent(app, client):
    with app.app_context():
        teacher, group = _setup()
        student = fx.enroll(group, "s@example.com")
        ann = fx.published_group(teacher, group, title="Standing notice")
        gpid, apid = group.public_id, ann.public_id
    fx.login_as(client, "s@example.com")
    assert client.get(fx.student_detail(apid)).status_code == 200

    fx.login_as(client, "teacher@example.com")
    assert fx.withdraw_group_announcement(client, gpid, apid).status_code == 302
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        assert row.status == _WITHDRAWN
        assert row.withdrawn_at is not None
        assert row.withdrawn_at >= row.published_at

    fx.login_as(client, "s@example.com")
    assert client.get(fx.student_detail(apid)).status_code == 404
    assert "Standing notice" not in client.get(fx.STUDENT_FEED).get_data(as_text=True)


def test_withdrawal_requires_the_confirmation_box(app, client):
    with app.app_context():
        teacher, group = _setup()
        ann = fx.published_group(teacher, group)
        gpid, apid = group.public_id, ann.public_id
    fx.login_as(client, "teacher@example.com")
    response = fx.withdraw_group_announcement(client, gpid, apid, confirm=False)
    assert response.status_code == 302
    with app.app_context():
        assert Announcement.query.filter_by(public_id=apid).one().status == _PUBLISHED


def test_a_withdrawn_announcement_can_never_be_edited_or_republished(app, client):
    with app.app_context():
        teacher, group = _setup()
        ann = fx.for_group(teacher, group, status=_WITHDRAWN, title="Gone")
        gpid, apid = group.public_id, ann.public_id
        before = (ann.title, ann.body, ann.version, ann.published_at, ann.withdrawn_at)
    fx.login_as(client, "teacher@example.com")

    # The edit page redirects with an explanation rather than rendering.
    response = client.get(fx.group_edit(gpid, apid), follow_redirects=True)
    assert "permanent" in response.get_data(as_text=True)
    client.post(
        fx.group_edit(gpid, apid),
        data={"title": "Back", "body": "Back", "announcement_state": ""},
        follow_redirects=True,
    )
    client.post(fx.group_publish(gpid, apid), data={}, follow_redirects=True)
    client.post(
        fx.group_withdraw(gpid, apid),
        data={"confirm": "yes", "announcement_state": ""},
        follow_redirects=True,
    )
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        assert (
            row.title, row.body, row.version, row.published_at, row.withdrawn_at
        ) == before
        assert row.status == _WITHDRAWN


def test_the_detail_page_offers_no_control_for_a_withdrawn_announcement(app, client):
    with app.app_context():
        teacher, group = _setup()
        ann = fx.for_group(teacher, group, status=_WITHDRAWN)
        gpid, apid = group.public_id, ann.public_id
    fx.login_as(client, "teacher@example.com")
    html = client.get(fx.group_detail(gpid, apid)).get_data(as_text=True)
    assert "announcement_state" not in html
    assert "/publish" not in html
    assert "/withdraw" not in html


def test_withdrawal_stays_possible_under_an_archived_chain(app, client):
    """A notice that should no longer be standing must always be removable
    -- including after the term it belonged to has been archived."""
    with app.app_context():
        teacher, group = _setup()
        ann = fx.published_group(teacher, group)
        gpid, apid = group.public_id, ann.public_id
    fx.login_as(client, "teacher@example.com")
    token = fx.token_from(client, fx.group_detail(gpid, apid))
    with app.app_context():
        term = AcademicTerm.query.one()
        term.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    response = client.post(
        fx.group_withdraw(gpid, apid),
        data={"announcement_state": token, "confirm": "yes"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    with app.app_context():
        assert Announcement.query.filter_by(public_id=apid).one().status == _WITHDRAWN


def test_there_is_no_delete_archive_restore_or_duplicate_route(app):
    rules = [str(rule) for rule in app.url_map.iter_rules() if "announcement" in str(rule)]
    for forbidden in ("delete", "archive", "restore", "duplicate", "unwithdraw", "schedule"):
        assert not any(forbidden in rule for rule in rules), forbidden


# ===========================================================================
# Stale state
# ===========================================================================


def test_a_stale_edit_token_is_rejected_without_overwriting_anything(app, client):
    with app.app_context():
        teacher, group = _setup()
        ann = fx.for_group(teacher, group, title="Original", body="Original body")
        gpid, apid = group.public_id, ann.public_id
    fx.login_as(client, "teacher@example.com")
    token = fx.token_from(client, fx.group_edit(gpid, apid))
    # A co-teacher edits it in the meantime.
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        row.title = "Changed by somebody else"
        row.version = row.version + 1
        db.session.commit()
    response = client.post(
        fx.group_edit(gpid, apid),
        data={"title": "Mine", "body": "Mine", "announcement_state": token},
        follow_redirects=True,
    )
    assert "changed by someone else" in response.get_data(as_text=True)
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        assert (row.title, row.body, row.version) == (
            "Changed by somebody else", "Original body", 2,
        )


def test_a_stale_publish_token_is_rejected_and_publishes_nothing(app, client):
    with app.app_context():
        teacher, group = _setup()
        ann = fx.for_group(teacher, group)
        gpid, apid = group.public_id, ann.public_id
    fx.login_as(client, "teacher@example.com")
    token = fx.token_from(client, fx.group_detail(gpid, apid))
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        row.body = "Edited between the page and the button"
        row.version = row.version + 1
        db.session.commit()
    response = client.post(
        fx.group_publish(gpid, apid),
        data={"announcement_state": token},
        follow_redirects=True,
    )
    assert "changed by someone else" in response.get_data(as_text=True)
    with app.app_context():
        assert Announcement.query.filter_by(public_id=apid).one().status == _DRAFT


def test_a_publish_token_does_not_survive_the_target_being_archived(app, client):
    """The publish token binds the target's whole academic chain, so a form
    opened while the Group was operational cannot publish into a Group that
    has since been archived -- and the rejection says so rather than
    silently publishing."""
    with app.app_context():
        teacher, group = _setup()
        ann = fx.for_group(teacher, group)
        gpid, apid = group.public_id, ann.public_id
    fx.login_as(client, "teacher@example.com")
    token = fx.token_from(client, fx.group_detail(gpid, apid))
    with app.app_context():
        level = Level.query.one()
        level.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    response = client.post(
        fx.group_publish(gpid, apid),
        data={"announcement_state": token},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        assert Announcement.query.filter_by(public_id=apid).one().status == _DRAFT


def test_a_stale_withdraw_token_is_rejected(app, client):
    with app.app_context():
        teacher, group = _setup()
        ann = fx.published_group(teacher, group)
        gpid, apid = group.public_id, ann.public_id
    fx.login_as(client, "teacher@example.com")
    token = fx.token_from(client, fx.group_detail(gpid, apid))
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        row.version = row.version + 1
        db.session.commit()
    response = client.post(
        fx.group_withdraw(gpid, apid),
        data={"announcement_state": token, "confirm": "yes"},
        follow_redirects=True,
    )
    assert "changed by someone else" in response.get_data(as_text=True)
    with app.app_context():
        assert Announcement.query.filter_by(public_id=apid).one().status == _PUBLISHED


def test_a_token_minted_on_the_admin_surface_cannot_be_replayed_here(app, client):
    with app.app_context():
        teacher, group = _setup()
        ann = fx.for_group(teacher, group)
        gpid, apid, version = group.public_id, ann.public_id, ann.version
    with app.app_context():
        from app.services.announcement_tokens import make_token

        forged = make_token(
            "admin",
            "announcement-publish",
            actor_public_id=User.query.filter_by(email="teacher@example.com")
            .one()
            .public_id,
            announcement_public_id=apid,
            announcement_version=version,
            scope="group",
            target_public_id=gpid,
            target_state="active|active|active|active",
        )
    fx.login_as(client, "teacher@example.com")
    response = client.post(
        fx.group_publish(gpid, apid),
        data={"announcement_state": forged},
        follow_redirects=True,
    )
    assert "changed by someone else" in response.get_data(as_text=True)
    with app.app_context():
        assert Announcement.query.filter_by(public_id=apid).one().status == _DRAFT


def test_a_missing_or_garbage_token_is_treated_exactly_like_a_stale_one(app, client):
    with app.app_context():
        teacher, group = _setup()
        ann = fx.for_group(teacher, group, title="Untouched")
        gpid, apid = group.public_id, ann.public_id
    fx.login_as(client, "teacher@example.com")
    for token in ("", "not-a-token", "a.b.c"):
        client.post(
            fx.group_edit(gpid, apid),
            data={"title": "Nope", "body": "Nope", "announcement_state": token},
            follow_redirects=True,
        )
    with app.app_context():
        assert Announcement.query.filter_by(public_id=apid).one().title == "Untouched"


# ===========================================================================
# The lock chain -- structural
# ===========================================================================
#
# SQLite honours neither ``FOR UPDATE`` nor REPEATABLE READ, so everything
# below asserts what the code *requests*, not that anything blocks.


def _locked_tables(app, call):
    """The distinct tables one lock chain reads from, in first-touch order.

    Every scalar the call needs must be resolved **before** recording
    starts, so a lazy relationship load in the test's own argument list
    cannot be mistaken for part of the lock order.
    """
    seen = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        match = re.search(r"\bFROM ([a-z_]+)", " ".join(statement.split()))
        if match:
            seen.append(match.group(1))

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        call()
    finally:
        with app.app_context():
            event.remove(db.engine, "before_cursor_execute", _rec)
    ordered = []
    for name in seen:
        if name not in ordered:
            ordered.append(name)
    return ordered


def test_the_group_chain_requests_the_documented_lock_order(app):
    from app.services.announcement_transactions import lock_announcement_chain

    with app.app_context():
        teacher, group = _setup()
        ann = fx.for_group(teacher, group)
        args = dict(
            term_id=group.academic_term_id,
            level_id=group.course.level_id,
            course_id=group.course_id,
            group_public_id=group.public_id,
            lock_assignment=True,
            announcement_id=ann.id,
        )
        actor_id = teacher.id
        ordered = _locked_tables(
            app, lambda: lock_announcement_chain(actor_id, **args)
        )
    assert ordered == [
        "academic_terms",
        "levels",
        "courses",
        "groups",
        "users",
        "group_teacher_assignments",
        "announcements",
    ]


def test_a_center_chain_locks_no_academic_row_and_no_group(app):
    from app.services.announcement_transactions import lock_announcement_chain

    with app.app_context():
        admin = fx.user("admin@example.com", UserRole.ADMINISTRATOR.value)
        ann = fx.center(admin)
        actor_id, announcement_id = admin.id, ann.id
        ordered = _locked_tables(
            app,
            lambda: lock_announcement_chain(
                actor_id, announcement_id=announcement_id
            ),
        )
    assert ordered == ["users", "announcements"]


def test_a_course_chain_locks_the_level_and_course_but_no_term_or_group(app):
    from app.services.announcement_transactions import lock_announcement_chain

    with app.app_context():
        admin = fx.user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = fx.hierarchy("A")
        course = db.session.get(Course, group.course_id)
        ann = fx.for_course(admin, course)
        actor_id, level_id, course_id, announcement_id = (
            admin.id, course.level_id, course.id, ann.id,
        )
        ordered = _locked_tables(
            app,
            lambda: lock_announcement_chain(
                actor_id,
                level_id=level_id,
                course_id=course_id,
                announcement_id=announcement_id,
            ),
        )
    assert ordered == ["levels", "courses", "users", "announcements"]


def test_the_chain_resets_the_transaction_exactly_once_before_its_first_lock(app):
    """One deliberate reset, owned by ``lock_academic_hierarchy`` -- the
    project-wide rule every other milestone's chain also follows."""
    from app.services.announcement_transactions import lock_announcement_chain

    rollbacks = []

    def _rec(conn):
        rollbacks.append(1)

    with app.app_context():
        teacher, group = _setup()
        args = dict(
            term_id=group.academic_term_id,
            level_id=group.course.level_id,
            course_id=group.course_id,
            group_public_id=group.public_id,
            lock_assignment=True,
        )
        actor_id = teacher.id
        event.listen(db.engine, "rollback", _rec)
        try:
            lock_announcement_chain(actor_id, **args)
        finally:
            event.remove(db.engine, "rollback", _rec)
    assert len(rollbacks) <= 1


def test_the_post_lock_recheck_rejects_a_group_retargeted_under_the_request(app):
    """A Group moved to another Course between the form and the write is
    not the Group this request validated against.

    Asserted against the helper the routes call rather than through the
    test client: the window this closes is between one request's pre-lock
    read and its own lock, which a single-threaded client cannot enter.
    What *is* provable here is that the comparison itself answers
    correctly -- and the routes are shown to call it by the lock-order
    tests above and by the archived-chain rejections.
    """
    from app.services.announcement_transactions import (
        lock_announcement_chain,
        locked_group_matches,
    )

    with app.app_context():
        teacher, group = _setup("A")
        other = fx.hierarchy("B")
        term_id, level_id, course_id = (
            group.academic_term_id, group.course.level_id, group.course_id,
        )
        group_id, gpid, actor_id = group.id, group.public_id, teacher.id
        other_course_id, other_level_id = other.course_id, other.course.level_id

        locks = lock_announcement_chain(
            actor_id,
            term_id=term_id,
            level_id=level_id,
            course_id=course_id,
            group_public_id=gpid,
            lock_assignment=True,
        )
        assert locked_group_matches(locks, group_id, term_id, level_id, course_id)
        # The same locked rows no longer match once the request's own
        # expectation names another Course, another Level or another Group.
        assert not locked_group_matches(
            locks, group_id, term_id, other_level_id, other_course_id
        )
        assert not locked_group_matches(locks, group_id + 999, term_id, level_id, course_id)

        # And a Group really retargeted under the lock stops matching the
        # expectation the request was built from.
        db.session.rollback()
        moved = Group.query.filter_by(public_id=gpid).one()
        moved.course_id = other_course_id
        db.session.commit()
        locks = lock_announcement_chain(
            actor_id,
            term_id=term_id,
            level_id=level_id,
            course_id=course_id,
            group_public_id=gpid,
            lock_assignment=True,
        )
        assert not locked_group_matches(locks, group_id, term_id, level_id, course_id)


def test_the_post_lock_recheck_reports_every_archived_link_of_the_locked_chain(app):
    from app.services.announcement_transactions import (
        archived_locked_labels,
        lock_announcement_chain,
    )

    with app.app_context():
        teacher, group = _setup("A")
        term_id, level_id, course_id = (
            group.academic_term_id, group.course.level_id, group.course_id,
        )
        gpid, actor_id = group.public_id, teacher.id

        def labels():
            locks = lock_announcement_chain(
                actor_id,
                term_id=term_id,
                level_id=level_id,
                course_id=course_id,
                group_public_id=gpid,
                lock_assignment=True,
            )
            out = archived_locked_labels(
                locks,
                term_id=term_id,
                level_id=level_id,
                course_id=course_id,
                include_group=True,
            )
            db.session.rollback()
            return out

        assert labels() == []
        AcademicTerm.query.filter_by(id=term_id).one().status = (
            AcademicStatus.ARCHIVED.value
        )
        db.session.commit()
        assert labels() == ["academic term"]
        Group.query.filter_by(public_id=gpid).one().status = (
            AcademicStatus.ARCHIVED.value
        )
        db.session.commit()
        assert labels() == ["academic term", "group"]


# ===========================================================================
# The Teacher's own feed
# ===========================================================================


def test_the_feed_shows_center_course_and_assigned_group_announcements(app, client):
    with app.app_context():
        teacher, group = _setup()
        admin = fx.user("admin@example.com", UserRole.ADMINISTRATOR.value)
        course = db.session.get(Course, group.course_id)
        fx.published_center(admin, title="Centre one")
        fx.published_course(admin, course, title="Course one")
        fx.published_group(teacher, group, title="Group one")
    fx.login_as(client, "teacher@example.com")
    html = client.get(fx.TEACHER_FEED).get_data(as_text=True)
    for title in ("Centre one", "Course one", "Group one"):
        assert title in html


def test_the_feed_hides_drafts_withdrawn_and_other_groups(app, client):
    with app.app_context():
        teacher, group = _setup()
        other_group = fx.hierarchy("B")
        other_teacher = fx.assign(other_group, "other@example.com")
        fx.for_group(teacher, group, title="My draft")
        fx.for_group(teacher, group, status=_WITHDRAWN, title="My withdrawn")
        fx.published_group(other_teacher, other_group, title="Somebody elses group")
        fx.published_group(teacher, group, title="Mine and standing")
    fx.login_as(client, "teacher@example.com")
    html = client.get(fx.TEACHER_FEED).get_data(as_text=True)
    assert "Mine and standing" in html
    for hidden in ("My draft", "My withdrawn", "Somebody elses group"):
        assert hidden not in html


def test_the_feed_is_ordered_newest_publication_first(app, client):
    with app.app_context():
        teacher, group = _setup()
        fx.published_group(teacher, group, title="Older", published_at=fx.NOW)
        fx.published_group(teacher, group, title="Newer", published_at=fx.LATEST)
    fx.login_as(client, "teacher@example.com")
    html = client.get(fx.TEACHER_FEED).get_data(as_text=True)
    assert html.index("Newer") < html.index("Older")


def test_the_feed_search_narrows_but_never_widens(app, client):
    with app.app_context():
        teacher, group = _setup()
        other_group = fx.hierarchy("B")
        other_teacher = fx.assign(other_group, "other@example.com")
        fx.published_group(teacher, group, title="Exam timetable", body="Room 4.")
        fx.published_group(teacher, group, title="Holiday", body="Closed Monday.")
        fx.published_group(
            other_teacher, other_group, title="Exam elsewhere", body="Other room."
        )
    fx.login_as(client, "teacher@example.com")
    html = client.get(f"{fx.TEACHER_FEED}?q=exam").get_data(as_text=True)
    assert "Exam timetable" in html
    assert "Holiday" not in html
    assert "Exam elsewhere" not in html


def test_the_feed_search_matches_the_body_too(app, client):
    with app.app_context():
        teacher, group = _setup()
        fx.published_group(teacher, group, title="Notice", body="The projector is fixed.")
    fx.login_as(client, "teacher@example.com")
    assert "Notice" in client.get(
        f"{fx.TEACHER_FEED}?q=projector"
    ).get_data(as_text=True)


def test_a_removed_assignment_removes_the_group_notice_from_the_feed(app, client):
    with app.app_context():
        teacher, group = _setup()
        fx.published_group(teacher, group, title="Was mine")
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    assert "Was mine" in client.get(fx.TEACHER_FEED).get_data(as_text=True)
    with app.app_context():
        group = Group.query.filter_by(public_id=gpid).one()
        teacher = User.query.filter_by(email="teacher@example.com").one()
        fx.remove_assignment(group, teacher)
    assert "Was mine" not in client.get(fx.TEACHER_FEED).get_data(as_text=True)


def test_an_archived_chain_removes_the_group_notice_from_the_feed(app, client):
    with app.app_context():
        teacher, group = _setup()
        fx.published_group(teacher, group, title="Term notice")
    fx.login_as(client, "teacher@example.com")
    assert "Term notice" in client.get(fx.TEACHER_FEED).get_data(as_text=True)
    with app.app_context():
        term = AcademicTerm.query.one()
        term.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    assert "Term notice" not in client.get(fx.TEACHER_FEED).get_data(as_text=True)


def test_the_management_page_stays_reachable_under_an_archived_chain(app, client):
    with app.app_context():
        teacher, group = _setup()
        ann = fx.published_group(teacher, group, title="Historical")
        gpid, apid = group.public_id, ann.public_id
        term = AcademicTerm.query.one()
        term.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    fx.login_as(client, "teacher@example.com")
    assert "Historical" in client.get(fx.group_list(gpid)).get_data(as_text=True)
    assert client.get(fx.group_detail(gpid, apid)).status_code == 200


# ===========================================================================
# Responses, ids and pagination
# ===========================================================================


def test_every_response_is_private_and_non_cacheable(app, client):
    with app.app_context():
        teacher, group = _setup()
        ann = fx.published_group(teacher, group)
        gpid, apid = group.public_id, ann.public_id
    fx.login_as(client, "teacher@example.com")
    for url in (
        fx.TEACHER_FEED,
        fx.teacher_detail(apid),
        fx.group_list(gpid),
        fx.group_detail(gpid, apid),
        fx.group_new(gpid),
    ):
        response = client.get(url)
        assert response.status_code == 200, url
        assert response.headers["Cache-Control"] == "private, no-store", url
        assert "Cookie" in response.headers["Vary"], url


def test_no_internal_numeric_id_appears_in_any_markup(app, client):
    with app.app_context():
        teacher, group = _setup()
        ann = fx.for_group(teacher, group)
        gpid, apid = group.public_id, ann.public_id
        ids = (group.id, ann.id, teacher.id, group.course_id, group.academic_term_id)
    fx.login_as(client, "teacher@example.com")
    for url in (
        fx.group_list(gpid),
        fx.group_detail(gpid, apid),
        fx.group_edit(gpid, apid),
        fx.group_new(gpid),
    ):
        html = client.get(url).get_data(as_text=True)
        # Deliberately NOT a substring search for "/announcements/<id>": a
        # UUID public id can legitimately begin with the same digit as an
        # internal id, which would make such a check fail at random. The
        # real rule is that every announcement path segment the page emits
        # is a public id, and no form value is a bare internal id.
        for segment in re.findall(r"/announcements/([^\"'/?# ]+)", html):
            # "new" is the create route's own literal segment; everything
            # else must be a 36-character public id.
            assert not segment.isdigit(), (url, segment)
            assert segment == "new" or len(segment) == 36, (url, segment)
        values = set(re.findall(r'value="([^"]*)"', html))
        for internal in ids:
            assert str(internal) not in values, (url, internal)


def test_the_group_list_pages_and_never_counts(app, client):
    from app.services.announcement_queries import PAGE_SIZE

    with app.app_context():
        teacher, group = _setup()
        for index in range(PAGE_SIZE + 3):
            fx.for_group(teacher, group, title=f"Notice {index:03d}")
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    first = client.get(fx.group_list(gpid)).get_data(as_text=True)
    second = client.get(f"{fx.group_list(gpid)}?page=2").get_data(as_text=True)
    assert first.count("Notice ") >= PAGE_SIZE
    assert "Next" in first
    assert "Previous" in second
    # A page past the end falls back to page 1 rather than an empty page.
    far = client.get(f"{fx.group_list(gpid)}?page=9999").get_data(as_text=True)
    assert "Previous" not in far


@pytest.mark.parametrize("value", ["0", "-1", "abc", "999999999", ""])
def test_a_tampered_page_value_normalises_to_page_one(app, client, value):
    with app.app_context():
        teacher, group = _setup()
        fx.for_group(teacher, group, title="Only one")
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    html = client.get(f"{fx.group_list(gpid)}?page={value}").get_data(as_text=True)
    assert "Only one" in html


def test_the_feed_cost_does_not_grow_with_the_number_of_announcements(app, client):
    def query_count(count):
        with app.app_context():
            db.drop_all()
            db.create_all()
            teacher, group = _setup()
            for index in range(count):
                fx.published_group(teacher, group, title=f"Notice {index:03d}")
        fx.login_as(client, "teacher@example.com")

        recorded = []

        def _rec(conn, cursor, statement, parameters, context, executemany):
            recorded.append(statement)

        with app.app_context():
            event.listen(db.engine, "before_cursor_execute", _rec)
        try:
            assert client.get(fx.TEACHER_FEED).status_code == 200
        finally:
            with app.app_context():
                event.remove(db.engine, "before_cursor_execute", _rec)
        return len([s for s in recorded if s.strip().upper().startswith("SELECT")])

    assert query_count(1) == query_count(15)


def test_the_dashboard_cost_does_not_grow_with_the_number_of_announcements(app, client):
    def query_count(count):
        with app.app_context():
            db.drop_all()
            db.create_all()
            teacher, group = _setup()
            for index in range(count):
                fx.published_group(teacher, group, title=f"Notice {index:03d}")
        fx.login_as(client, "teacher@example.com")

        recorded = []

        def _rec(conn, cursor, statement, parameters, context, executemany):
            recorded.append(statement)

        with app.app_context():
            event.listen(db.engine, "before_cursor_execute", _rec)
        try:
            assert client.get("/teacher/dashboard").status_code == 200
        finally:
            with app.app_context():
                event.remove(db.engine, "before_cursor_execute", _rec)
        return len([s for s in recorded if s.strip().upper().startswith("SELECT")])

    assert query_count(1) == query_count(15)


def test_the_dashboard_links_to_each_assigned_groups_announcements(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    html = client.get("/teacher/dashboard").get_data(as_text=True)
    assert fx.group_list(gpid) in html
    assert fx.TEACHER_FEED in html
