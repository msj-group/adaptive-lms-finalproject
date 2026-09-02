"""M14 event matrix and failure isolation.

Positive and negative cases for every producer, driven through the real
Administrator / Teacher routes wherever the route is what decides the
event, plus service-level cases for recipient eligibility and the
effective-visibility chain. The last section proves the central promise:
a notification-delivery failure never turns a successful enrollment,
assignment, schedule change, lesson publication, or file upload into an
error, and never removes a committed file.
"""

import io
from datetime import datetime

import pytest

from app.extensions import db
from app.models import (
    AcademicStatus,
    Enrollment,
    EnrollmentStatus,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Lesson,
    LessonStatus,
    Material,
    Notification,
    NotificationKind,
    Schedule,
    UploadedFile,
    UserRole,
    UserStatus,
)
from app.services import notification_delivery
from tests import notification_fixtures as fx
from tests.conftest import login
from tests.file_fixtures import minimal_png


def _kinds_for(user):
    return sorted(
        row.kind
        for row in Notification.query.filter_by(recipient_id=user.id).all()
    )


def _count(user=None, kind=None):
    query = Notification.query
    if user is not None:
        query = query.filter_by(recipient_id=user.id)
    if kind is not None:
        query = query.filter_by(kind=kind)
    return query.count()


# ===========================================================================
# 1 + 2: Enrollment activation / withdrawal (through the real routes)
# ===========================================================================


def _admin_group_with_teacher(group_name="Group A"):
    admin = fx.user("admin@example.com", UserRole.ADMINISTRATOR.value)
    group = fx.hierarchy(group_name=group_name)
    teacher = fx.user("t@example.com", UserRole.TEACHER.value)
    fx.assign(group, teacher)
    return admin, group, teacher


def test_enrollment_create_notifies_that_student_only(app, client):
    with app.app_context():
        _admin_group_with_teacher()
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        other = fx.user("other@example.com", UserRole.STUDENT.value)
        from app.models import Group

        gpid = Group.query.first().public_id
        spid = student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{gpid}/enrollments", data={"student_public_id": spid}
    )
    assert resp.status_code == 302
    with app.app_context():
        from app.models import User

        me = User.query.filter_by(email="s@example.com").first()
        them = User.query.filter_by(email="other@example.com").first()
        assert _kinds_for(me) == [NotificationKind.ENROLLMENT_ACTIVATED.value]
        assert _count(them) == 0
        row = Notification.query.filter_by(recipient_id=me.id).first()
        assert row.target_path == "/student/dashboard"
        assert "Group A" in row.message
        assert row.read_at is None


def test_enrollment_withdraw_then_reactivate_notify_in_order(app, client):
    with app.app_context():
        _admin_group_with_teacher()
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        from app.models import Group

        group = Group.query.first()
        enrollment = fx.enroll(group, student)
        gpid, epid = group.public_id, enrollment.public_id
    login(client, "admin@example.com")

    client.post(f"/admin/groups/{gpid}/enrollments/{epid}/withdraw")
    client.post(f"/admin/groups/{gpid}/enrollments/{epid}/reactivate")
    with app.app_context():
        from app.models import User

        me = User.query.filter_by(email="s@example.com").first()
        rows = (
            Notification.query.filter_by(recipient_id=me.id)
            .order_by(Notification.id)
            .all()
        )
        assert [r.kind for r in rows] == [
            NotificationKind.ENROLLMENT_WITHDRAWN.value,
            NotificationKind.ENROLLMENT_ACTIVATED.value,
        ]


def test_a_blocked_enrollment_create_produces_nothing(app, client):
    """No active teacher -> the route rejects the enrollment. A rejected
    mutation must produce no notification at all."""
    with app.app_context():
        fx.user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = fx.hierarchy(group_name="No Teacher")
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        gpid, spid = group.public_id, student.public_id
    login(client, "admin@example.com")

    client.post(f"/admin/groups/{gpid}/enrollments", data={"student_public_id": spid})
    with app.app_context():
        assert Enrollment.query.count() == 0
        assert _count() == 0


def test_a_duplicate_withdraw_produces_nothing_the_second_time(app, client):
    with app.app_context():
        _admin_group_with_teacher()
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        from app.models import Group

        group = Group.query.first()
        enrollment = fx.enroll(group, student)
        gpid, epid = group.public_id, enrollment.public_id
    login(client, "admin@example.com")

    client.post(f"/admin/groups/{gpid}/enrollments/{epid}/withdraw")
    client.post(f"/admin/groups/{gpid}/enrollments/{epid}/withdraw")
    with app.app_context():
        assert _count(kind=NotificationKind.ENROLLMENT_WITHDRAWN.value) == 1


# ===========================================================================
# 3 + 4: Teacher assignment (through the real routes)
# ===========================================================================


def test_teacher_assign_remove_reactivate_notify_that_teacher(app, client):
    with app.app_context():
        fx.user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = fx.hierarchy(group_name="Group T")
        teacher = fx.user("t@example.com", UserRole.TEACHER.value)
        gpid, tpid = group.public_id, teacher.public_id
    login(client, "admin@example.com")

    client.post(f"/admin/groups/{gpid}/teachers", data={"teacher_public_id": tpid})
    with app.app_context():
        assignment = GroupTeacherAssignment.query.first()
        apid = assignment.public_id
    client.post(f"/admin/groups/{gpid}/teachers/{apid}/remove")
    client.post(f"/admin/groups/{gpid}/teachers/{apid}/reactivate")

    with app.app_context():
        from app.models import User

        teacher = User.query.filter_by(email="t@example.com").first()
        rows = (
            Notification.query.filter_by(recipient_id=teacher.id)
            .order_by(Notification.id)
            .all()
        )
        assert [r.kind for r in rows] == [
            NotificationKind.TEACHER_ASSIGNMENT_ACTIVATED.value,
            NotificationKind.TEACHER_ASSIGNMENT_REMOVED.value,
            NotificationKind.TEACHER_ASSIGNMENT_ACTIVATED.value,
        ]
        assert all(r.target_path == "/teacher/dashboard" for r in rows)
        assert all("Group T" in r.message for r in rows)


def test_a_blocked_last_teacher_removal_produces_nothing(app, client):
    with app.app_context():
        fx.user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = fx.hierarchy(group_name="Group T")
        teacher = fx.user("t@example.com", UserRole.TEACHER.value)
        assignment = fx.assign(group, teacher)
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.enroll(group, student)
        gpid, apid = group.public_id, assignment.public_id
    login(client, "admin@example.com")

    client.post(f"/admin/groups/{gpid}/teachers/{apid}/remove")
    with app.app_context():
        assert (
            GroupTeacherAssignment.query.first().status
            == GroupTeacherAssignmentStatus.ACTIVE.value
        )
        assert _count() == 0


def test_removing_a_corrupted_assignment_notifies_nobody(app, client):
    """A GroupTeacherAssignment can legitimately reference a non-Teacher
    (the FK cannot prevent it) and an administrator may clean it up. The
    producer's own role re-check is what keeps that from notifying."""
    with app.app_context():
        fx.user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = fx.hierarchy(group_name="Group T")
        fx.assign(group, fx.user("real@example.com", UserRole.TEACHER.value))
        impostor = fx.user("nott@example.com", UserRole.RESEARCHER.value)
        corrupted = fx.assign(group, impostor)
        gpid, apid = group.public_id, corrupted.public_id
    login(client, "admin@example.com")

    client.post(f"/admin/groups/{gpid}/teachers/{apid}/remove")
    with app.app_context():
        from app.models import User

        assert (
            db.session.get(GroupTeacherAssignment, corrupted.id).status
            == GroupTeacherAssignmentStatus.REMOVED.value
        )
        impostor = User.query.filter_by(email="nott@example.com").first()
        assert _count(impostor) == 0


# ===========================================================================
# 5: Schedule changes reach every active member of the Group
# ===========================================================================


def test_schedule_create_notifies_every_active_member(app, client):
    with app.app_context():
        fx.user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = fx.hierarchy(group_name="Group S")
        fx.assign(group, fx.user("t@example.com", UserRole.TEACHER.value))
        fx.enroll(group, fx.user("s1@example.com", UserRole.STUDENT.value))
        fx.enroll(group, fx.user("s2@example.com", UserRole.STUDENT.value))
        gpid = group.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{gpid}/schedules/new",
        data={
            "day_of_week": "0",
            "start_time": "09:00",
            "end_time": "10:30",
            "effective_start_date": "2026-01-05",
            "effective_end_date": "2026-06-30",
            "location": "Room 1",
        },
    )
    assert resp.status_code == 302
    with app.app_context():
        from app.models import User

        assert Schedule.query.count() == 1
        assert _count(kind=NotificationKind.SCHEDULE_CHANGED.value) == 3
        for email, target in (
            ("s1@example.com", "/student/dashboard"),
            ("s2@example.com", "/student/dashboard"),
            ("t@example.com", "/teacher/dashboard"),
        ):
            user = User.query.filter_by(email=email).first()
            row = Notification.query.filter_by(recipient_id=user.id).first()
            assert row is not None, email
            assert row.target_path == target
            assert "Group S" in row.message


def test_schedule_notifications_exclude_inactive_and_wrong_role_members(app, client):
    with app.app_context():
        fx.user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = fx.hierarchy(group_name="Group S")
        fx.assign(group, fx.user("t@example.com", UserRole.TEACHER.value))
        fx.enroll(group, fx.user("active@example.com", UserRole.STUDENT.value))
        fx.enroll(
            group,
            fx.user("withdrawn@example.com", UserRole.STUDENT.value),
            status=EnrollmentStatus.WITHDRAWN.value,
        )
        fx.enroll(
            group,
            fx.user(
                "suspended@example.com",
                UserRole.STUDENT.value,
                status=UserStatus.SUSPENDED.value,
            ),
        )
        fx.assign(
            group,
            fx.user("removed@example.com", UserRole.TEACHER.value),
            status=GroupTeacherAssignmentStatus.REMOVED.value,
        )
        fx.assign(group, fx.user("notateacher@example.com", UserRole.RESEARCHER.value))
        # Somebody in a completely different Group must never be reached.
        other_group = fx.hierarchy(group_name="Other")
        fx.enroll(other_group, fx.user("outsider@example.com", UserRole.STUDENT.value))
        gpid = group.public_id
    login(client, "admin@example.com")

    client.post(
        f"/admin/groups/{gpid}/schedules/new",
        data={
            "day_of_week": "0",
            "start_time": "09:00",
            "end_time": "10:30",
            "effective_start_date": "2026-01-05",
            "effective_end_date": "2026-06-30",
            "location": "Room 1",
        },
    )
    with app.app_context():
        from app.models import User

        notified = {
            db.session.get(User, row.recipient_id).email
            for row in Notification.query.all()
        }
        assert notified == {"active@example.com", "t@example.com"}


def test_schedule_edit_archive_and_reactivate_each_notify_once(app, client):
    with app.app_context():
        fx.user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = fx.hierarchy(group_name="Group S")
        fx.assign(group, fx.user("t@example.com", UserRole.TEACHER.value))
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.enroll(group, student)
        slot = fx.schedule(group)
        gpid, spid = group.public_id, slot.public_id
    login(client, "admin@example.com")

    edit_page = client.get(f"/admin/groups/{gpid}/schedules/{spid}/edit").get_data(
        as_text=True
    )
    token = edit_page.split('name="edit_snapshot" value="')[1].split('"')[0]
    resp = client.post(
        f"/admin/groups/{gpid}/schedules/{spid}/edit",
        data={
            "day_of_week": "1",
            "start_time": "11:00",
            "end_time": "12:30",
            "effective_start_date": "2026-01-05",
            "effective_end_date": "2026-06-30",
            "location": "Room 2",
            "edit_snapshot": token,
        },
    )
    assert resp.status_code == 302
    client.post(f"/admin/groups/{gpid}/schedules/{spid}/toggle-status")  # archive
    client.post(f"/admin/groups/{gpid}/schedules/{spid}/toggle-status")  # reactivate

    with app.app_context():
        from app.models import User

        me = User.query.filter_by(email="s@example.com").first()
        rows = (
            Notification.query.filter_by(recipient_id=me.id)
            .order_by(Notification.id)
            .all()
        )
        assert len(rows) == 3
        assert {r.kind for r in rows} == {NotificationKind.SCHEDULE_CHANGED.value}
        assert "changed" in rows[0].message
        assert "removed" in rows[1].message
        assert "restored" in rows[2].message


def test_a_rejected_schedule_edit_produces_nothing(app, client):
    """A stale snapshot token is rejected before anything is written."""
    with app.app_context():
        fx.user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = fx.hierarchy(group_name="Group S")
        fx.assign(group, fx.user("t@example.com", UserRole.TEACHER.value))
        fx.enroll(group, fx.user("s@example.com", UserRole.STUDENT.value))
        slot = fx.schedule(group)
        gpid, spid = group.public_id, slot.public_id
    login(client, "admin@example.com")

    client.post(
        f"/admin/groups/{gpid}/schedules/{spid}/edit",
        data={
            "day_of_week": "1",
            "start_time": "11:00",
            "end_time": "12:30",
            "effective_start_date": "2026-01-05",
            "effective_end_date": "2026-06-30",
            "location": "Room 2",
            "edit_snapshot": "not-a-valid-token",
        },
    )
    with app.app_context():
        assert Schedule.query.first().day_of_week == 0
        assert _count() == 0


# ===========================================================================
# 6: Lesson publication (through the real Teacher route)
# ===========================================================================


def _teacher_lesson_setup(lesson_status=LessonStatus.DRAFT.value):
    teacher = fx.user("t@example.com", UserRole.TEACHER.value)
    group = fx.hierarchy(group_name="Group L")
    fx.assign(group, teacher)
    student = fx.user("s@example.com", UserRole.STUDENT.value)
    fx.enroll(group, student)
    unit = fx.unit(group, title="Unit 1")
    lesson = fx.lesson(unit, title="Present Simple", status=lesson_status)
    return group, unit, lesson, student


def _toggle_publication_url(group, unit, lesson):
    return (
        f"/teacher/groups/{group.public_id}/units/{unit.public_id}"
        f"/lessons/{lesson.public_id}/toggle-publication"
    )


def test_publishing_a_lesson_notifies_enrolled_students(app, client):
    with app.app_context():
        group, unit, lesson, _ = _teacher_lesson_setup()
        url = _toggle_publication_url(group, unit, lesson)
        expected_target = (
            f"/student/groups/{group.public_id}/units/{unit.public_id}"
            f"/lessons/{lesson.public_id}"
        )
    login(client, "t@example.com")

    assert client.post(url).status_code == 302
    with app.app_context():
        from app.models import User

        me = User.query.filter_by(email="s@example.com").first()
        row = Notification.query.filter_by(recipient_id=me.id).first()
        assert row.kind == NotificationKind.LESSON_PUBLISHED.value
        assert row.target_path == expected_target
        assert "Present Simple" in row.message
        assert "Group L" in row.message


def test_unpublishing_notifies_nobody(app, client):
    with app.app_context():
        group, unit, lesson, _ = _teacher_lesson_setup(
            lesson_status=LessonStatus.PUBLISHED.value
        )
        url = _toggle_publication_url(group, unit, lesson)
    login(client, "t@example.com")

    client.post(url)  # published -> draft
    with app.app_context():
        assert Lesson.query.first().status == LessonStatus.DRAFT.value
        assert _count() == 0


def test_republishing_notifies_again(app, client):
    with app.app_context():
        group, unit, lesson, _ = _teacher_lesson_setup()
        url = _toggle_publication_url(group, unit, lesson)
    login(client, "t@example.com")

    client.post(url)  # publish
    client.post(url)  # unpublish
    client.post(url)  # republish
    with app.app_context():
        assert _count(kind=NotificationKind.LESSON_PUBLISHED.value) == 2


def test_lesson_edit_create_and_reorder_notify_nobody(app, client):
    with app.app_context():
        group, unit, lesson, _ = _teacher_lesson_setup(
            lesson_status=LessonStatus.PUBLISHED.value
        )
        fx.lesson(unit, title="Second", status=LessonStatus.PUBLISHED.value, display_order=1)
        base = f"/teacher/groups/{group.public_id}/units/{unit.public_id}/lessons"
        lpid = lesson.public_id
    login(client, "t@example.com")

    edit_page = client.get(f"{base}/{lpid}/edit").get_data(as_text=True)
    token = edit_page.split('name="edit_snapshot" value="')[1].split('"')[0]
    client.post(
        f"{base}/{lpid}/edit",
        data={"title": "Present Simple v2", "description": "", "search_keywords": "",
              "edit_snapshot": token},
    )
    client.post(f"{base}/new", data={"title": "Third", "description": "",
                                     "search_keywords": ""})
    client.post(f"{base}/{lpid}/move-down")

    with app.app_context():
        assert Lesson.query.filter_by(title="Present Simple v2").count() == 1
        assert _count() == 0


def test_a_draft_lesson_in_an_archived_chain_publishes_to_nobody(app, client):
    """Publishing is blocked entirely under an archived chain, so there is
    no successful mutation and therefore no notification."""
    with app.app_context():
        teacher = fx.user("t@example.com", UserRole.TEACHER.value)
        group = fx.hierarchy(group_name="Group L")
        fx.assign(group, teacher)
        fx.enroll(group, fx.user("s@example.com", UserRole.STUDENT.value))
        unit = fx.unit(group)
        lesson = fx.lesson(unit)
        url = _toggle_publication_url(group, unit, lesson)
        group.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    login(client, "t@example.com")

    client.post(url)
    with app.app_context():
        assert Lesson.query.first().status == LessonStatus.DRAFT.value
        assert _count() == 0


def test_publication_excludes_students_who_cannot_see_the_lesson(app):
    """Service level: only Students with an own active Enrollment, an
    active account, and a fully active chain are recipients."""
    with app.app_context():
        group = fx.hierarchy(group_name="Group L")
        unit = fx.unit(group)
        lesson = fx.lesson(unit, status=LessonStatus.PUBLISHED.value)

        visible = fx.user("visible@example.com", UserRole.STUDENT.value)
        fx.enroll(group, visible)
        fx.enroll(
            group,
            fx.user("withdrawn@example.com", UserRole.STUDENT.value),
            status=EnrollmentStatus.WITHDRAWN.value,
        )
        fx.enroll(
            group,
            fx.user("susp@example.com", UserRole.STUDENT.value,
                    status=UserStatus.SUSPENDED.value),
        )
        fx.assign(group, fx.user("t@example.com", UserRole.TEACHER.value))
        other = fx.hierarchy(group_name="Other")
        fx.enroll(other, fx.user("outsider@example.com", UserRole.STUDENT.value))

        assert notification_delivery.notify_lesson_published(lesson.id) == 1
        row = Notification.query.one()
        assert row.recipient_id == visible.id


@pytest.mark.parametrize(
    "archived",
    ["term", "level", "course", "group", "unit"],
)
def test_publication_notifies_nobody_when_a_link_of_the_chain_is_archived(app, archived):
    with app.app_context():
        group = fx.hierarchy(group_name="Group L")
        unit = fx.unit(group)
        lesson = fx.lesson(unit, status=LessonStatus.PUBLISHED.value)
        fx.enroll(group, fx.user("s@example.com", UserRole.STUDENT.value))

        rows = {
            "term": group.academic_term,
            "level": group.course.level,
            "course": group.course,
            "group": group,
            "unit": unit,
        }
        rows[archived].status = AcademicStatus.ARCHIVED.value
        db.session.commit()

        assert notification_delivery.notify_lesson_published(lesson.id) == 0
        assert Notification.query.count() == 0


def test_a_draft_lesson_notifies_nobody_at_the_service_level(app):
    with app.app_context():
        group = fx.hierarchy()
        unit = fx.unit(group)
        lesson = fx.lesson(unit, status=LessonStatus.DRAFT.value)
        fx.enroll(group, fx.user("s@example.com", UserRole.STUDENT.value))
        assert notification_delivery.notify_lesson_published(lesson.id) == 0


# ===========================================================================
# 7: Material availability
# ===========================================================================


def _material_urls(group, unit, lesson):
    base = (
        f"/teacher/groups/{group.public_id}/units/{unit.public_id}"
        f"/lessons/{lesson.public_id}/materials"
    )
    return base


def test_creating_a_material_on_a_published_lesson_notifies_students(app, client):
    with app.app_context():
        group, unit, lesson, _ = _teacher_lesson_setup(
            lesson_status=LessonStatus.PUBLISHED.value
        )
        base = _material_urls(group, unit, lesson)
        expected_prefix = (
            f"/student/groups/{group.public_id}/units/{unit.public_id}"
            f"/lessons/{lesson.public_id}#material-"
        )
    login(client, "t@example.com")

    page = client.get(f"{base}/new/rich-text").get_data(as_text=True)
    token = page.split('name="create_token" value="')[1].split('"')[0]
    resp = client.post(
        f"{base}/new/rich-text",
        data={"title": "Handout", "content_html": "<p>hello</p>",
              "search_keywords": "", "create_token": token},
    )
    assert resp.status_code == 302

    with app.app_context():
        from app.models import User

        me = User.query.filter_by(email="s@example.com").first()
        row = Notification.query.filter_by(recipient_id=me.id).one()
        assert row.kind == NotificationKind.MATERIAL_AVAILABLE.value
        assert row.target_path.startswith(expected_prefix)
        assert Material.query.first().public_id in row.target_path
        assert "Handout" in row.message
        assert "Present Simple" in row.message


def test_creating_a_material_on_a_draft_lesson_notifies_nobody(app, client):
    with app.app_context():
        group, unit, lesson, _ = _teacher_lesson_setup(
            lesson_status=LessonStatus.DRAFT.value
        )
        base = _material_urls(group, unit, lesson)
    login(client, "t@example.com")

    page = client.get(f"{base}/new/rich-text").get_data(as_text=True)
    token = page.split('name="create_token" value="')[1].split('"')[0]
    client.post(
        f"{base}/new/rich-text",
        data={"title": "Draft handout", "content_html": "<p>hi</p>",
              "search_keywords": "", "create_token": token},
    )
    with app.app_context():
        assert Material.query.count() == 1
        assert _count() == 0


def test_material_edit_archive_and_reorder_notify_nobody(app, client):
    with app.app_context():
        group, unit, lesson, _ = _teacher_lesson_setup(
            lesson_status=LessonStatus.PUBLISHED.value
        )
        first = fx.material(lesson, title="One", display_order=1)
        second = fx.material(lesson, title="Two", display_order=2)
        base = _material_urls(group, unit, lesson)
        first_pid, second_pid = first.public_id, second.public_id
    login(client, "t@example.com")

    edit_page = client.get(f"{base}/{first_pid}/edit").get_data(as_text=True)
    token = edit_page.split('name="edit_snapshot" value="')[1].split('"')[0]
    client.post(
        f"{base}/{first_pid}/edit",
        data={"title": "One v2", "content_html": "<p>changed</p>",
              "search_keywords": "", "edit_snapshot": token},
    )
    client.post(f"{base}/{second_pid}/move-up")
    client.post(f"{base}/{first_pid}/toggle-status")  # archive

    with app.app_context():
        assert Material.query.filter_by(title="One v2").count() == 1
        assert _count() == 0


def test_reactivating_a_material_notifies_students_again(app, client):
    with app.app_context():
        group, unit, lesson, _ = _teacher_lesson_setup(
            lesson_status=LessonStatus.PUBLISHED.value
        )
        row = fx.material(lesson, title="Handout", status=AcademicStatus.ARCHIVED.value)
        base = _material_urls(group, unit, lesson)
        mpid = row.public_id
    login(client, "t@example.com")

    client.post(f"{base}/{mpid}/toggle-status")  # reactivate
    with app.app_context():
        assert Material.query.first().status == AcademicStatus.ACTIVE.value
        assert _count(kind=NotificationKind.MATERIAL_AVAILABLE.value) == 1


def test_an_archived_material_notifies_nobody_at_the_service_level(app):
    with app.app_context():
        group = fx.hierarchy()
        unit = fx.unit(group)
        lesson = fx.lesson(unit, status=LessonStatus.PUBLISHED.value)
        row = fx.material(lesson, status=AcademicStatus.ARCHIVED.value)
        fx.enroll(group, fx.user("s@example.com", UserRole.STUDENT.value))
        assert notification_delivery.notify_material_available(row.id) == 0


def test_a_file_material_notifies_after_the_file_and_row_are_committed(
    material_app, material_client
):
    with material_app.app_context():
        group, unit, lesson, _ = _teacher_lesson_setup(
            lesson_status=LessonStatus.PUBLISHED.value
        )
        base = _material_urls(group, unit, lesson)
    login(material_client, "t@example.com")

    page = material_client.get(f"{base}/new/file").get_data(as_text=True)
    token = page.split('name="create_token" value="')[1].split('"')[0]
    resp = material_client.post(
        f"{base}/new/file",
        data={
            "title": "Slides",
            "search_keywords": "",
            "create_token": token,
            "file": (io.BytesIO(minimal_png()), "slides.png"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    with material_app.app_context():
        assert UploadedFile.query.count() == 1
        assert _count(kind=NotificationKind.MATERIAL_AVAILABLE.value) == 1


# ===========================================================================
# Recipient eligibility at the service level
# ===========================================================================


@pytest.mark.parametrize(
    "role, status",
    [
        (UserRole.TEACHER.value, UserStatus.ACTIVE.value),
        (UserRole.ADMINISTRATOR.value, UserStatus.ACTIVE.value),
        (UserRole.RESEARCHER.value, UserStatus.ACTIVE.value),
        (UserRole.STUDENT.value, UserStatus.SUSPENDED.value),
    ],
)
def test_enrollment_producer_rejects_a_wrong_role_or_inactive_recipient(app, role, status):
    with app.app_context():
        user = fx.user("x@example.com", role, status=status)
        assert notification_delivery.notify_enrollment_activated(user.id, "G") == 0
        assert notification_delivery.notify_enrollment_withdrawn(user.id, "G") == 0
        assert Notification.query.count() == 0


@pytest.mark.parametrize(
    "role, status",
    [
        (UserRole.STUDENT.value, UserStatus.ACTIVE.value),
        (UserRole.ADMINISTRATOR.value, UserStatus.ACTIVE.value),
        (UserRole.TEACHER.value, UserStatus.SUSPENDED.value),
    ],
)
def test_assignment_producer_rejects_a_wrong_role_or_inactive_recipient(app, role, status):
    with app.app_context():
        user = fx.user("x@example.com", role, status=status)
        assert notification_delivery.notify_teacher_assignment_activated(user.id, "G") == 0
        assert notification_delivery.notify_teacher_assignment_removed(user.id, "G") == 0
        assert Notification.query.count() == 0


def test_a_missing_recipient_id_produces_nothing(app):
    with app.app_context():
        assert notification_delivery.notify_enrollment_activated(999999, "G") == 0
        assert Notification.query.count() == 0


def test_a_group_with_no_active_members_produces_nothing(app):
    with app.app_context():
        group = fx.hierarchy()
        assert notification_delivery.notify_schedule_changed(group.id, "G", "created") == 0
        assert Notification.query.count() == 0


def test_recipient_selection_is_deterministic_and_deduplicated(app):
    with app.app_context():
        group = fx.hierarchy(group_name="G")
        students = [
            fx.user(f"s{i}@example.com", UserRole.STUDENT.value) for i in range(4)
        ]
        for student in students:
            fx.enroll(group, student)
        teacher = fx.user("t@example.com", UserRole.TEACHER.value)
        fx.assign(group, teacher)

        assert notification_delivery.notify_schedule_changed(group.id, "G", "created") == 5
        recipients = [
            row.recipient_id
            for row in Notification.query.order_by(Notification.id).all()
        ]
        assert recipients == sorted(recipients)
        assert len(set(recipients)) == len(recipients)


def test_maximum_length_names_still_fit_the_columns(app):
    """The longest message M14 can build quotes a 150-character Material
    title, a 150-character Lesson title, and a 100-character Group name --
    it must still fit ``notifications.message`` without being clipped."""
    with app.app_context():
        group = fx.hierarchy(group_name="G" * 100)
        fx.enroll(group, fx.user("s@example.com", UserRole.STUDENT.value))
        unit = fx.unit(group)
        lesson = fx.lesson(unit, title="L" * 150, status=LessonStatus.PUBLISHED.value)
        material = fx.material(lesson, title="M" * 150)

        assert notification_delivery.notify_material_available(material.id) == 1
        row = Notification.query.one()
        assert len(row.title) <= 150
        assert len(row.message) <= 500
        assert "M" * 150 in row.message
        assert "L" * 150 in row.message
        assert "G" * 100 in row.message
        assert not row.message.endswith("…")


def test_the_clip_helper_bounds_anything_longer():
    """Defensive backstop: if a future column ever quotes something
    longer, the write is clipped rather than failing under MySQL strict
    mode."""
    clipped = notification_delivery._clip("x" * 900, 500)
    assert len(clipped) == 500
    assert clipped.endswith("…")
    assert notification_delivery._clip("short", 500) == "short"
    assert notification_delivery._clip(None, 500) == ""


# ===========================================================================
# Failure isolation -- the central M14 promise
# ===========================================================================


@pytest.fixture
def broken_delivery(monkeypatch):
    """Make every notification write fail, the way a missing table or a
    database error would."""

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated notification failure")

    monkeypatch.setattr(notification_delivery, "_entry", _boom)
    return _boom


def test_delivery_failure_never_raises(app, broken_delivery):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        assert notification_delivery.notify_enrollment_activated(student.id, "G") == 0
        assert Notification.query.count() == 0


def test_delivery_failure_preserves_a_successful_enrollment(app, client, broken_delivery):
    with app.app_context():
        _admin_group_with_teacher()
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        from app.models import Group

        gpid, spid = Group.query.first().public_id, student.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{gpid}/enrollments", data={"student_public_id": spid},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "enrolled in" in resp.get_data(as_text=True)
    with app.app_context():
        assert Enrollment.query.count() == 1
        assert Enrollment.query.first().status == EnrollmentStatus.ACTIVE.value
        assert Notification.query.count() == 0


def test_delivery_failure_preserves_a_successful_assignment(app, client, broken_delivery):
    with app.app_context():
        fx.user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = fx.hierarchy(group_name="G")
        teacher = fx.user("t@example.com", UserRole.TEACHER.value)
        gpid, tpid = group.public_id, teacher.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{gpid}/teachers", data={"teacher_public_id": tpid}
    )
    assert resp.status_code == 302
    with app.app_context():
        assert GroupTeacherAssignment.query.count() == 1
        assert Notification.query.count() == 0


def test_delivery_failure_preserves_a_successful_schedule_change(app, client, broken_delivery):
    with app.app_context():
        fx.user("admin@example.com", UserRole.ADMINISTRATOR.value)
        group = fx.hierarchy(group_name="G")
        fx.assign(group, fx.user("t@example.com", UserRole.TEACHER.value))
        fx.enroll(group, fx.user("s@example.com", UserRole.STUDENT.value))
        gpid = group.public_id
    login(client, "admin@example.com")

    resp = client.post(
        f"/admin/groups/{gpid}/schedules/new",
        data={
            "day_of_week": "0",
            "start_time": "09:00",
            "end_time": "10:30",
            "effective_start_date": "2026-01-05",
            "effective_end_date": "2026-06-30",
            "location": "Room 1",
        },
    )
    assert resp.status_code == 302
    with app.app_context():
        assert Schedule.query.count() == 1
        assert Notification.query.count() == 0


def test_delivery_failure_preserves_a_successful_lesson_publication(app, client, broken_delivery):
    with app.app_context():
        group, unit, lesson, _ = _teacher_lesson_setup()
        url = _toggle_publication_url(group, unit, lesson)
    login(client, "t@example.com")

    resp = client.post(url)
    assert resp.status_code == 302
    with app.app_context():
        published = Lesson.query.first()
        assert published.status == LessonStatus.PUBLISHED.value
        assert published.published_at is not None
        assert Notification.query.count() == 0


def test_delivery_failure_preserves_a_successful_rich_text_material(app, client, broken_delivery):
    with app.app_context():
        group, unit, lesson, _ = _teacher_lesson_setup(
            lesson_status=LessonStatus.PUBLISHED.value
        )
        base = _material_urls(group, unit, lesson)
    login(client, "t@example.com")

    page = client.get(f"{base}/new/rich-text").get_data(as_text=True)
    token = page.split('name="create_token" value="')[1].split('"')[0]
    resp = client.post(
        f"{base}/new/rich-text",
        data={"title": "Handout", "content_html": "<p>hello</p>",
              "search_keywords": "", "create_token": token},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        assert Material.query.count() == 1
        assert Notification.query.count() == 0


def test_delivery_failure_keeps_a_committed_file_material_and_its_file(
    material_app, material_client, broken_delivery
):
    """The M14 rule for uploads: delivery runs strictly after the file and
    its rows are committed, so a delivery failure must not delete the
    file, roll back the Material, or be reported as a failed upload."""
    with material_app.app_context():
        group, unit, lesson, _ = _teacher_lesson_setup(
            lesson_status=LessonStatus.PUBLISHED.value
        )
        base = _material_urls(group, unit, lesson)
    login(material_client, "t@example.com")

    page = material_client.get(f"{base}/new/file").get_data(as_text=True)
    token = page.split('name="create_token" value="')[1].split('"')[0]
    resp = material_client.post(
        f"{base}/new/file",
        data={
            "title": "Slides",
            "search_keywords": "",
            "create_token": token,
            "file": (io.BytesIO(minimal_png()), "slides.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "created" in body
    assert "simulated notification failure" not in body

    with material_app.app_context():
        from app.services.file_storage import open_stored_file
        from app.services.material_config import current_material_config

        material = Material.query.one()
        uploaded = UploadedFile.query.one()
        assert material.uploaded_file_id == uploaded.id
        stored_path = open_stored_file(current_material_config(), uploaded.storage_key)
        assert stored_path.exists()
        assert stored_path.stat().st_size == uploaded.byte_size
        assert Notification.query.count() == 0


def test_delivery_failure_is_logged(app, caplog, broken_delivery):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        with caplog.at_level("ERROR"):
            notification_delivery.notify_enrollment_activated(student.id, "G")
    assert any("Notification delivery failed" in r.message for r in caplog.records)


def test_a_failed_delivery_leaves_the_session_usable(app, broken_delivery):
    """The domain response is built after delivery, so the request must be
    able to keep using the session afterwards."""
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        notification_delivery.notify_enrollment_activated(student.id, "G")
        # The session still works: a normal read and write both succeed.
        assert fx.user("s2@example.com", UserRole.STUDENT.value) is not None
        assert Notification.query.count() == 0


# ===========================================================================
# Deferred / noise events produce nothing
# ===========================================================================


def test_unit_crud_and_ordering_notify_nobody(app, client):
    with app.app_context():
        teacher = fx.user("t@example.com", UserRole.TEACHER.value)
        group = fx.hierarchy(group_name="G")
        fx.assign(group, teacher)
        fx.enroll(group, fx.user("s@example.com", UserRole.STUDENT.value))
        existing = fx.unit(group, title="U1", display_order=0)
        base = f"/teacher/groups/{group.public_id}/units"
        upid = existing.public_id
    login(client, "t@example.com")

    client.post(f"{base}/new", data={"title": "U2", "description": "",
                                     "search_keywords": ""})
    client.post(f"{base}/{upid}/move-down")
    client.post(f"{base}/{upid}/toggle-status")
    with app.app_context():
        assert _count() == 0


def test_reads_searches_and_dashboards_notify_nobody(app, client):
    with app.app_context():
        group, unit, lesson, _ = _teacher_lesson_setup(
            lesson_status=LessonStatus.PUBLISHED.value
        )
        gpid, upid, lpid = group.public_id, unit.public_id, lesson.public_id
    login(client, "s@example.com")

    client.get("/student/dashboard")
    client.get("/student/search?q=present")
    client.get(f"/student/groups/{gpid}/units")
    client.get(f"/student/groups/{gpid}/units/{upid}/lessons/{lpid}")
    client.get("/notifications")
    with app.app_context():
        assert _count() == 0


def test_an_account_change_notifies_nobody(app, client):
    with app.app_context():
        fx.user("admin@example.com", UserRole.ADMINISTRATOR.value)
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        spid = student.public_id
    login(client, "admin@example.com")

    client.post(f"/admin/students/{spid}/toggle-status")
    with app.app_context():
        assert _count() == 0
