"""Phase 4 / M09 -- in-app notifications for a published announcement.

Covers the recipient set for all three scopes, deduplication by User id,
the "never an Administrator" rule, the one-and-only-one delivery, the
safe server-generated targets, the re-authorization an opened
notification performs, and the fault isolation that keeps a delivery
failure from turning a successful publication into an error.
"""

import pytest

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
    NotificationKind,
    User,
    UserRole,
    UserStatus,
)
from app.services.notification_delivery import notify_announcement_published
from app.services.notification_targets import validate_notification_target

_CENTER = AnnouncementScope.CENTER.value
_COURSE = AnnouncementScope.COURSE.value
_GROUP = AnnouncementScope.GROUP.value
_KIND = NotificationKind.ANNOUNCEMENT_PUBLISHED.value


def _deliver(announcement):
    return notify_announcement_published(
        announcement.id, announcement.scope, announcement.course_id, announcement.group_id
    )


def _recipients():
    return sorted(row.recipient_id for row in Notification.query.all())


# ===========================================================================
# The kind itself
# ===========================================================================


def test_exactly_one_new_notification_kind_was_added():
    values = [kind.value for kind in NotificationKind]
    # M09 added exactly one kind, as the eighth. Phase 4 / M11 later
    # appended `message_received` after it in its own revision.
    assert values[7] == "announcement_published"
    assert values[8:] == ["message_received"]
    assert len(values) == 9
    assert "announcement_withdrawn" not in values
    assert "announcement_edited" not in values


def test_the_inbox_has_a_label_for_the_new_kind():
    from app.services.notification_queries import KIND_LABELS

    assert KIND_LABELS[_KIND] == "Announcement"
    # Every kind still has a label -- adding one must not leave a gap.
    assert set(KIND_LABELS) == {kind.value for kind in NotificationKind}


# ===========================================================================
# Recipient selection, per scope
# ===========================================================================


def test_a_center_announcement_reaches_every_active_student_and_teacher(app):
    with app.app_context():
        admin = fx.user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        teacher = fx.user("t@example.com", UserRole.TEACHER.value)
        fx.user("r@example.com", UserRole.RESEARCHER.value)
        fx.user(
            "suspended@example.com",
            UserRole.STUDENT.value,
            status=UserStatus.SUSPENDED.value,
        )
        ann = fx.published_center(admin, title="Holiday")
        assert _deliver(ann) == 2
        assert _recipients() == sorted([student.id, teacher.id])


def test_a_course_announcement_reaches_only_its_courses_current_members(app):
    with app.app_context():
        admin = fx.user("admin@example.com", UserRole.ADMINISTRATOR.value)
        mine = fx.hierarchy("A")
        theirs = fx.hierarchy("B")
        student = fx.enroll(mine, "s@example.com")
        teacher = fx.assign(mine, "t@example.com")
        fx.enroll(theirs, "other-student@example.com")
        fx.assign(theirs, "other-teacher@example.com")
        course = db.session.get(Course, mine.course_id)
        ann = fx.published_course(admin, course, title="Course notice")
        assert _deliver(ann) == 2
        assert _recipients() == sorted([student.id, teacher.id])


def test_a_group_announcement_reaches_only_that_groups_current_members(app):
    with app.app_context():
        teacher, group = fx.setup_group("A")
        other = fx.hierarchy("B")
        student = fx.enroll(group, "s@example.com")
        fx.enroll(other, "other@example.com")
        ann = fx.published_group(teacher, group, title="Group notice")
        assert _deliver(ann) == 2
        assert _recipients() == sorted([student.id, teacher.id])


def test_a_user_eligible_through_several_groups_is_notified_exactly_once(app):
    """Deduplication by User id, for a Student **and** a Teacher who each
    reach one Course through three Groups."""
    with app.app_context():
        admin = fx.user("admin@example.com", UserRole.ADMINISTRATOR.value)
        term = fx.term("T")
        level = fx.level("L")
        course = fx.course(level, "C")
        groups = [fx.group(term, course, f"G{i}") for i in range(3)]
        student = fx.enroll(groups[0], "s@example.com")
        teacher = fx.assign(groups[0], "t@example.com")
        for group in groups[1:]:
            fx.enroll_existing(group, student)
            fx.assign_existing(group, teacher)
        ann = fx.published_course(admin, course, title="One notice")
        assert _deliver(ann) == 2
        assert _recipients() == sorted([student.id, teacher.id])
        assert Notification.query.filter_by(recipient_id=student.id).count() == 1
        assert Notification.query.filter_by(recipient_id=teacher.id).count() == 1


def test_no_administrator_is_ever_notified(app):
    with app.app_context():
        admin = fx.user("admin@example.com", UserRole.ADMINISTRATOR.value)
        second_admin = fx.user("admin2@example.com", UserRole.ADMINISTRATOR.value)
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        ann = fx.published_center(admin, title="Holiday")
        assert _deliver(ann) == 1
        assert _recipients() == [student.id]
        for admin_row in (admin, second_admin):
            assert Notification.query.filter_by(recipient_id=admin_row.id).count() == 0


def test_a_withdrawn_enrollment_or_removed_assignment_removes_the_recipient(app):
    with app.app_context():
        teacher, group = fx.setup_group("A")
        student = fx.enroll(group, "s@example.com")
        fx.withdraw_enrollment(group, student)
        fx.remove_assignment(group, teacher)
        ann = fx.published_group(teacher, group)
        assert _deliver(ann) == 0
        assert Notification.query.count() == 0


def test_an_inactive_account_is_not_notified(app):
    with app.app_context():
        teacher, group = fx.setup_group("A")
        fx.enroll(
            group,
            "suspended@example.com",
            account_status=UserStatus.SUSPENDED.value,
        )
        active = fx.enroll(group, "active@example.com")
        ann = fx.published_group(teacher, group)
        assert _deliver(ann) == 2  # the active student and the teacher
        assert active.id in _recipients()
        assert (
            Notification.query.join(
                User, Notification.recipient_id == User.id
            )
            .filter(User.email == "suspended@example.com")
            .count()
            == 0
        )


def test_an_announcement_under_an_archived_chain_notifies_nobody(app):
    with app.app_context():
        teacher, group = fx.setup_group("A")
        fx.enroll(group, "s@example.com")
        ann = fx.published_group(teacher, group)
        AcademicTerm.query.one().status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        assert _deliver(ann) == 0


def test_a_draft_or_withdrawn_announcement_produces_no_notification(app):
    """Defense in depth: the routes only call the producer on a real
    publication, and the producer's own context read requires the row to
    be ``published`` before it selects anybody."""
    with app.app_context():
        teacher, group = fx.setup_group("A")
        fx.enroll(group, "s@example.com")
        draft = fx.for_group(teacher, group)
        withdrawn = fx.for_group(
            teacher, group, status=AnnouncementStatus.WITHDRAWN.value
        )
        assert _deliver(draft) == 0
        assert _deliver(withdrawn) == 0
        assert Notification.query.count() == 0


# ===========================================================================
# One delivery per announcement, ever
# ===========================================================================


def test_publishing_delivers_exactly_once_and_a_replay_delivers_nothing(app, client):
    with app.app_context():
        teacher, group = fx.setup_group("A")
        fx.enroll(group, "s@example.com")
        ann = fx.for_group(teacher, group, title="Notice")
        gpid, apid = group.public_id, ann.public_id
    fx.login_as(client, "teacher@example.com")
    fx.publish_group_draft(client, gpid, apid)
    with app.app_context():
        assert Notification.query.filter_by(kind=_KIND).count() == 2

    # A refreshed tab, a double submit and a stale token all return before
    # the producer line.
    client.post(fx.group_publish(gpid, apid), data={}, follow_redirects=True)
    client.post(
        fx.group_publish(gpid, apid),
        data={"announcement_state": "garbage"},
        follow_redirects=True,
    )
    with app.app_context():
        assert Notification.query.filter_by(kind=_KIND).count() == 2


def test_a_rejected_publish_delivers_nothing(app, client):
    with app.app_context():
        teacher, group = fx.setup_group("A")
        fx.enroll(group, "s@example.com")
        ann = fx.for_group(teacher, group)
        gpid, apid = group.public_id, ann.public_id
    fx.login_as(client, "teacher@example.com")
    token = fx.token_from(client, fx.group_detail(gpid, apid))
    with app.app_context():
        row = Announcement.query.filter_by(public_id=apid).one()
        row.version = row.version + 1
        db.session.commit()
    client.post(
        fx.group_publish(gpid, apid),
        data={"announcement_state": token},
        follow_redirects=True,
    )
    with app.app_context():
        assert Notification.query.count() == 0


def test_withdrawal_sends_no_notification_and_leaves_the_old_ones_standing(app, client):
    with app.app_context():
        teacher, group = fx.setup_group("A")
        fx.enroll(group, "s@example.com")
        ann = fx.for_group(teacher, group)
        gpid, apid = group.public_id, ann.public_id
    fx.login_as(client, "teacher@example.com")
    fx.publish_group_draft(client, gpid, apid)
    with app.app_context():
        before = [row.public_id for row in Notification.query.order_by(Notification.id)]
    fx.withdraw_group_announcement(client, gpid, apid)
    with app.app_context():
        after = [row.public_id for row in Notification.query.order_by(Notification.id)]
    assert after == before


def test_editing_a_draft_sends_nothing(app, client):
    with app.app_context():
        teacher, group = fx.setup_group("A")
        fx.enroll(group, "s@example.com")
        ann = fx.for_group(teacher, group)
        gpid, apid = group.public_id, ann.public_id
    fx.login_as(client, "teacher@example.com")
    token = fx.token_from(client, fx.group_edit(gpid, apid))
    client.post(
        fx.group_edit(gpid, apid),
        data={"title": "Edited", "body": "Edited body", "announcement_state": token},
        follow_redirects=True,
    )
    with app.app_context():
        assert Notification.query.count() == 0


# ===========================================================================
# What a notification says, and where it points
# ===========================================================================


def test_the_message_names_the_scope_and_title_and_nothing_private(app):
    with app.app_context():
        teacher, group = fx.setup_group("A", teacher_email="writer@example.com")
        student = fx.enroll(group, "s@example.com", name="Sally Student")
        ann = fx.published_group(
            teacher,
            group,
            title="Room change",
            body="SECRET BODY TEXT nobody outside may read",
        )
        _deliver(ann)
        row = Notification.query.filter_by(recipient_id=student.id).one()
        assert row.title == "New announcement"
        assert "Room change" in row.message
        assert group.name in row.message
        # Never the body, the author, the recipient, or an internal id.
        assert "SECRET BODY TEXT" not in row.message
        assert "writer@example.com" not in row.message
        assert "Sally Student" not in row.message
        assert str(ann.id) not in row.message
        assert str(student.id) not in row.message


@pytest.mark.parametrize("scope", [_CENTER, _COURSE, _GROUP])
def test_each_scope_words_its_own_message(app, scope):
    with app.app_context():
        admin = fx.user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = fx.hierarchy("A")
        student = fx.enroll(group, "s@example.com")
        course = db.session.get(Course, group.course_id)
        ann = {
            _CENTER: lambda: fx.published_center(admin, title="T"),
            _COURSE: lambda: fx.published_course(admin, course, title="T"),
            _GROUP: lambda: fx.published_group(admin, group, title="T"),
        }[scope]()
        _deliver(ann)
        message = Notification.query.filter_by(recipient_id=student.id).one().message
        if scope == _CENTER:
            assert "center announcement" in message
        elif scope == _COURSE:
            assert f"course '{course.title}'" in message
        else:
            assert f"group '{group.name}'" in message


def test_each_recipient_is_sent_into_their_own_role_namespace(app):
    with app.app_context():
        teacher, group = fx.setup_group("A")
        student = fx.enroll(group, "s@example.com")
        ann = fx.published_group(teacher, group)
        _deliver(ann)
        student_target = Notification.query.filter_by(
            recipient_id=student.id
        ).one().target_path
        teacher_target = Notification.query.filter_by(
            recipient_id=teacher.id
        ).one().target_path
        assert student_target == f"/student/announcements/{ann.public_id}"
        assert teacher_target == f"/teacher/announcements/{ann.public_id}"
        # Both survive the validator they were stored through...
        assert validate_notification_target("student", student_target) == student_target
        assert validate_notification_target("teacher", teacher_target) == teacher_target
        # ...and neither is valid in the other role's namespace.
        assert validate_notification_target("teacher", student_target) is None
        assert validate_notification_target("student", teacher_target) is None
        # An Administrator has no namespace at all, so neither is valid there.
        assert validate_notification_target("administrator", student_target) is None
        assert validate_notification_target("administrator", teacher_target) is None


def test_the_target_is_server_generated_and_carries_only_a_public_id(app):
    with app.app_context():
        teacher, group = fx.setup_group("A")
        student = fx.enroll(group, "s@example.com")
        ann = fx.published_group(teacher, group)
        _deliver(ann)
        for row in Notification.query.all():
            assert "?" not in row.target_path
            assert ".." not in row.target_path
            assert "\\" not in row.target_path
            # The path is exactly a role namespace plus the announcement's
            # own public id -- no query string, no fragment, no second
            # identifier, and no internal numeric id in any segment.
            segments = row.target_path.split("/")
            assert segments[0] == ""
            assert segments[1] in ("student", "teacher")
            assert segments[2] == "announcements"
            assert segments[3] == ann.public_id
            assert len(segments) == 4


# ===========================================================================
# Opening a notification re-authorizes from scratch
# ===========================================================================


def test_opening_a_notification_lands_on_the_authorized_detail_page(app, client):
    with app.app_context():
        teacher, group = fx.setup_group("A")
        fx.enroll(group, "s@example.com")
        ann = fx.published_group(teacher, group, title="Room change")
        _deliver(ann)
        student = User.query.filter_by(email="s@example.com").one()
        npid = Notification.query.filter_by(recipient_id=student.id).one().public_id
        apid = ann.public_id
    fx.login_as(client, "s@example.com")
    response = client.post(f"/notifications/{npid}/open", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/student/announcements/{apid}")
    assert "Room change" in client.get(fx.student_detail(apid)).get_data(as_text=True)


def test_following_a_notification_after_access_ends_fails_safely(app, client):
    with app.app_context():
        teacher, group = fx.setup_group("A")
        student = fx.enroll(group, "s@example.com")
        ann = fx.published_group(teacher, group, title="Room change")
        _deliver(ann)
        npid = Notification.query.filter_by(recipient_id=student.id).one().public_id
        apid, gpid = ann.public_id, group.public_id
    fx.login_as(client, "s@example.com")
    with app.app_context():
        group = Group.query.filter_by(public_id=gpid).one()
        student = User.query.filter_by(email="s@example.com").one()
        fx.withdraw_enrollment(group, student)
    # The notification row is still there and still opens...
    response = client.post(f"/notifications/{npid}/open", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/student/announcements/{apid}")
    # ...but the destination re-authorizes and refuses.
    assert client.get(fx.student_detail(apid)).status_code == 404
    with app.app_context():
        assert Notification.query.count() == 2  # nothing was deleted


def test_following_a_notification_after_withdrawal_fails_safely(app, client):
    with app.app_context():
        teacher, group = fx.setup_group("A")
        student = fx.enroll(group, "s@example.com")
        ann = fx.for_group(teacher, group, title="Room change")
        gpid, apid = group.public_id, ann.public_id
    fx.login_as(client, "teacher@example.com")
    fx.publish_group_draft(client, gpid, apid)
    fx.withdraw_group_announcement(client, gpid, apid)
    with app.app_context():
        student = User.query.filter_by(email="s@example.com").one()
        npid = Notification.query.filter_by(recipient_id=student.id).one().public_id
        # The historical row survives, with its title and message intact.
        row = Notification.query.filter_by(public_id=npid).one()
        assert row.title == "New announcement"
        assert "Room change" in row.message
    fx.login_as(client, "s@example.com")
    response = client.post(f"/notifications/{npid}/open", follow_redirects=False)
    assert response.status_code == 302
    assert client.get(fx.student_detail(apid)).status_code == 404


def test_a_notification_row_never_carries_the_announcement_body(app, client):
    with app.app_context():
        teacher, group = fx.setup_group("A")
        fx.enroll(group, "s@example.com")
        ann = fx.for_group(
            teacher, group, title="Notice", body="CONFIDENTIAL BODY TEXT"
        )
        gpid, apid = group.public_id, ann.public_id
    fx.login_as(client, "teacher@example.com")
    fx.publish_group_draft(client, gpid, apid)
    fx.withdraw_group_announcement(client, gpid, apid)
    fx.login_as(client, "s@example.com")
    html = client.get("/notifications").get_data(as_text=True)
    assert "Notice" in html  # the title is legitimate
    assert "CONFIDENTIAL BODY TEXT" not in html


# ===========================================================================
# Fault isolation
# ===========================================================================


def test_a_delivery_failure_never_undoes_the_publication(app, client, monkeypatch):
    with app.app_context():
        teacher, group = fx.setup_group("A")
        fx.enroll(group, "s@example.com")
        ann = fx.for_group(teacher, group)
        gpid, apid = group.public_id, ann.public_id

    import app.services.notification_delivery as delivery

    def _boom(*args, **kwargs):
        raise RuntimeError("recipient selection exploded")

    monkeypatch.setattr(delivery, "_announcement_recipients", _boom)

    fx.login_as(client, "teacher@example.com")
    response = fx.publish_group_draft(client, gpid, apid)
    assert response.status_code == 302
    with app.app_context():
        # The publication itself is committed and intact...
        row = Announcement.query.filter_by(public_id=apid).one()
        assert row.status == AnnouncementStatus.PUBLISHED.value
        assert row.published_at is not None
        # ...and the lost notification is simply lost, best-effort.
        assert Notification.query.count() == 0
