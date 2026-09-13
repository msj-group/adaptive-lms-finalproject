"""Teacher authoring, grading and release of a Group gradebook
(Phase 4 / M08).

Authorization is nested and server-side, the roster is captured once and
frozen, a failed bulk edit writes nothing at all, and release is the one
permanent act. Every rejection is proved to have changed **no row**, not
merely to have shown a message.
"""

import re
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import (
    AcademicStatus,
    GradeCategory,
    GradeItem,
    GradeRecord,
    GroupTeacherAssignmentStatus,
    UserRole,
    UserStatus,
)
from tests import grade_fixtures as fx
from tests.conftest import make_user


def _gradebook(client, gpid):
    return client.get(fx.teacher_base(gpid)).get_data(as_text=True)


# ===========================================================================
# Access control
# ===========================================================================


def test_anonymous_is_redirected_to_login(app, client):
    with app.app_context():
        data = fx.full_gradebook()
        gpid = data["group_public_id"]
        ipid = data["item_public_ids"][0]
    for url in (
        "/teacher/grades",
        fx.teacher_base(gpid),
        fx.category_new(gpid),
        fx.item_new(gpid),
        fx.item_detail(gpid, ipid),
        fx.item_scores(gpid, ipid),
    ):
        resp = client.get(url)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]


def test_a_student_is_refused_every_teacher_route(app, client):
    with app.app_context():
        data = fx.full_gradebook()
        gpid = data["group_public_id"]
        ipid = data["item_public_ids"][0]
    fx.login_as(client, data["student_emails"][0])
    for url in (
        "/teacher/grades",
        fx.teacher_base(gpid),
        fx.category_new(gpid),
        fx.item_new(gpid),
        fx.item_detail(gpid, ipid),
        fx.item_scores(gpid, ipid),
    ):
        assert client.get(url).status_code == 403, url
    for url in (fx.category_new(gpid), fx.item_new(gpid), fx.item_scores(gpid, ipid),
                fx.item_release(gpid, ipid)):
        assert client.post(url, data={}).status_code == 403, url


def test_an_administrator_is_refused_the_teacher_routes(app, client):
    with app.app_context():
        data = fx.full_gradebook()
        gpid = data["group_public_id"]
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    fx.login_as(client, "admin@example.com")
    assert client.get(fx.teacher_base(gpid)).status_code == 403
    assert client.post(fx.category_new(gpid), data={}).status_code == 403


def test_an_unassigned_teacher_gets_a_non_disclosing_404(app, client):
    with app.app_context():
        data = fx.full_gradebook()
        gpid = data["group_public_id"]
        ipid = data["item_public_ids"][0]
        fx.user("outsider@example.com", UserRole.TEACHER.value)
    fx.login_as(client, "outsider@example.com")
    for url in (
        fx.teacher_base(gpid),
        fx.category_new(gpid),
        fx.item_new(gpid),
        fx.item_detail(gpid, ipid),
        fx.item_scores(gpid, ipid),
    ):
        assert client.get(url).status_code == 404, url


def test_a_removed_assignment_ends_access_immediately(app, client):
    with app.app_context():
        data = fx.full_gradebook()
        gpid = data["group_public_id"]
        group_id, teacher_id = data["group_id"], data["teacher_id"]
    fx.login_as(client, data["teacher_email"])
    assert client.get(fx.teacher_base(gpid)).status_code == 200
    with app.app_context():
        from app.models import GroupTeacherAssignment

        row = GroupTeacherAssignment.query.filter_by(
            group_id=group_id, teacher_id=teacher_id
        ).one()
        row.status = GroupTeacherAssignmentStatus.REMOVED.value
        db.session.commit()
    assert client.get(fx.teacher_base(gpid)).status_code == 404


def test_a_cross_group_category_or_item_public_id_404s(app, client):
    """Another Group's public id is not merely hidden -- the nested
    lookup is Group-scoped in SQL, through the category for an item, so
    it resolves to nothing."""
    with app.app_context():
        a = fx.full_gradebook("A", teacher_email="ta@example.com")
        b = fx.full_gradebook("B", teacher_email="tb@example.com")
        gpid_a = a["group_public_id"]
        cpid_b = b["category_public_ids"][0]
        ipid_b = b["item_public_ids"][0]
    fx.login_as(client, "ta@example.com")
    for url in (
        fx.category_edit(gpid_a, cpid_b),
        fx.item_detail(gpid_a, ipid_b),
        fx.item_edit(gpid_a, ipid_b),
        fx.item_scores(gpid_a, ipid_b),
    ):
        assert client.get(url).status_code == 404, url
    assert client.post(fx.item_release(gpid_a, ipid_b), data={}).status_code == 404


def test_an_invented_public_id_fails_identically(app, client):
    with app.app_context():
        data = fx.full_gradebook()
        gpid = data["group_public_id"]
    fx.login_as(client, data["teacher_email"])
    fake = "00000000-0000-0000-0000-000000000000"
    assert client.get(fx.category_edit(gpid, fake)).status_code == 404
    assert client.get(fx.item_detail(gpid, fake)).status_code == 404


def test_every_gradebook_page_is_private_no_store_and_varies_on_cookie(app, client):
    with app.app_context():
        data = fx.full_gradebook()
        gpid = data["group_public_id"]
        ipid = data["item_public_ids"][0]
    fx.login_as(client, data["teacher_email"])
    for url in (
        "/teacher/grades",
        fx.teacher_base(gpid),
        fx.category_new(gpid),
        fx.item_new(gpid),
        fx.item_detail(gpid, ipid),
        fx.item_scores(gpid, ipid),
    ):
        resp = client.get(url)
        assert resp.status_code == 200, url
        assert resp.headers["Cache-Control"] == "private, no-store", url
        assert "Cookie" in resp.headers.get("Vary", ""), url


def test_no_internal_numeric_id_appears_in_any_url_or_field(app, client):
    with app.app_context():
        data = fx.full_gradebook()
        gpid = data["group_public_id"]
        ipid = data["item_public_ids"][0]
        ids = {
            "group": data["group_id"],
            "category": data["category_ids"][0],
            "item": data["item_ids"][0],
            "record": data["record_ids"][0][0],
            "student": data["student_ids"][0],
        }
    fx.login_as(client, data["teacher_email"])
    for url in (
        fx.teacher_base(gpid),
        fx.item_detail(gpid, ipid),
        fx.item_scores(gpid, ipid),
        fx.item_new(gpid),
    ):
        html = client.get(url).get_data(as_text=True)
        hrefs = re.findall(r'href="([^"]*)"', html)
        names = re.findall(r'name="([^"]*)"', html)
        for label, value in ids.items():
            assert not any(
                href.rstrip("/").endswith(f"/gradebook/items/{value}") for href in hrefs
            ), (url, label)
            assert not any(name.endswith(f"__{value}") for name in names), (url, label)


# ===========================================================================
# Categories
# ===========================================================================


def test_a_teacher_creates_a_weighted_category(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    resp = client.post(
        fx.category_new(gpid),
        data=fx.category_payload(client, gpid, "Homework", "25.5"),
        follow_redirects=True,
    )
    assert "Grade category saved" in resp.get_data(as_text=True)
    with app.app_context():
        row = GradeCategory.query.one()
        assert row.title == "Homework"
        assert row.weight_basis_points == 2550
        assert row.version == 1


def test_a_duplicate_category_title_is_rejected(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        fx.category(group, "Homework", 5000)
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    resp = client.post(
        fx.category_new(gpid),
        data=fx.category_payload(client, gpid, "Homework", "10"),
    )
    assert b"already has a grade category with this name" in resp.data
    with app.app_context():
        assert GradeCategory.query.count() == 1


@pytest.mark.parametrize("weight", ["0", "0.00", "-5", "abc", "", "100.001", "1e2"])
def test_an_invalid_weight_is_rejected_and_writes_nothing(app, client, weight):
    with app.app_context():
        teacher, group = fx.setup_group()
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    client.post(
        fx.category_new(gpid), data=fx.category_payload(client, gpid, "X", weight)
    )
    with app.app_context():
        assert GradeCategory.query.count() == 0


def test_the_group_total_may_not_exceed_one_hundred_percent(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        fx.category(group, "Homework", 6000)
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    resp = client.post(
        fx.category_new(gpid), data=fx.category_payload(client, gpid, "Speaking", "40.01")
    )
    assert b"may not add up to more than 100" in resp.data
    with app.app_context():
        assert GradeCategory.query.count() == 1
    # Exactly 100% is fine.
    resp = client.post(
        fx.category_new(gpid),
        data=fx.category_payload(client, gpid, "Speaking", "40"),
        follow_redirects=True,
    )
    assert "Grade category saved" in resp.get_data(as_text=True)


def test_a_group_may_sit_below_one_hundred_percent_while_being_configured(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    client.post(
        fx.category_new(gpid),
        data=fx.category_payload(client, gpid, "Homework", "60"),
        follow_redirects=True,
    )
    html = _gradebook(client, gpid)
    assert "Weights total 60.00%" in html
    assert "do not add up to exactly 100%" in html


def test_editing_a_category_bumps_its_version_once(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        cat = fx.category(group, "Homework", 5000)
        gpid, cpid = group.public_id, cat.public_id
    fx.login_as(client, "teacher@example.com")
    client.post(
        fx.category_edit(gpid, cpid),
        data=fx.category_payload(client, gpid, "Homework 2", "60", cpid=cpid),
        follow_redirects=True,
    )
    with app.app_context():
        row = GradeCategory.query.one()
        assert row.title == "Homework 2"
        assert row.weight_basis_points == 6000
        assert row.version == 2
        assert row.updated_at != row.created_at


def test_a_no_op_category_edit_moves_no_version_and_no_timestamp(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        cat = fx.category(group, "Homework", 5000)
        gpid, cpid = group.public_id, cat.public_id
    fx.login_as(client, "teacher@example.com")
    resp = client.post(
        fx.category_edit(gpid, cpid),
        data=fx.category_payload(client, gpid, "Homework", "50", cpid=cpid),
        follow_redirects=True,
    )
    assert "Nothing was changed" in resp.get_data(as_text=True)
    with app.app_context():
        row = GradeCategory.query.one()
        assert row.version == 1
        assert row.updated_at == row.created_at


def test_a_category_editing_itself_does_not_count_against_its_own_weight(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        cat = fx.category(group, "Homework", 10000)
        gpid, cpid = group.public_id, cat.public_id
    fx.login_as(client, "teacher@example.com")
    resp = client.post(
        fx.category_edit(gpid, cpid),
        data=fx.category_payload(client, gpid, "Homework", "100", cpid=cpid),
        follow_redirects=True,
    )
    assert b"may not add up to more than 100" not in resp.data


def test_a_category_freezes_once_one_of_its_items_is_released(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        student = fx.enroll(group)
        cat = fx.category(group, "Homework", 10000)
        fx.item_with_roster(cat, [student], scores=("10.00",), released=True)
        gpid, cpid = group.public_id, cat.public_id
    fx.login_as(client, "teacher@example.com")
    resp = client.get(fx.category_edit(gpid, cpid), follow_redirects=True)
    assert b"can no longer be changed" in resp.data
    resp = client.post(
        fx.category_edit(gpid, cpid),
        data={"title": "Renamed", "weight": "50", "grade_state": "anything"},
        follow_redirects=True,
    )
    assert b"can no longer be changed" in resp.data
    with app.app_context():
        row = GradeCategory.query.one()
        assert row.title == "Homework"
        assert row.weight_basis_points == 10000
        assert row.version == 1


def test_a_draft_item_does_not_freeze_its_category(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        student = fx.enroll(group)
        cat = fx.category(group, "Homework", 5000)
        fx.item_with_roster(cat, [student])
        gpid, cpid = group.public_id, cat.public_id
    fx.login_as(client, "teacher@example.com")
    assert client.get(fx.category_edit(gpid, cpid)).status_code == 200


def test_there_is_no_category_delete_route(app, client):
    with app.app_context():
        data = fx.full_gradebook()
        gpid, cpid = data["group_public_id"], data["category_public_ids"][0]
    fx.login_as(client, data["teacher_email"])
    for url in (
        f"{fx.teacher_base(gpid)}/categories/{cpid}/delete",
        f"{fx.teacher_base(gpid)}/categories/{cpid}",
    ):
        assert client.post(url, data={}).status_code in (404, 405), url
    assert client.delete(fx.category_edit(gpid, cpid)).status_code == 405


def test_a_stale_category_token_is_rejected_and_writes_nothing(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    stale = fx.token_from(client, fx.category_new(gpid))
    # A co-teacher adds a category, changing the bound set.
    with app.app_context():
        fx.category(group, "Added elsewhere", 3000)
    resp = client.post(
        fx.category_new(gpid),
        data={"title": "Mine", "weight": "20", "grade_state": stale},
        follow_redirects=True,
    )
    assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert GradeCategory.query.count() == 1


# ===========================================================================
# Grade items
# ===========================================================================


def _make_item(client, app, gpid, cpid, **kwargs):
    return client.post(
        fx.item_new(gpid),
        data=fx.item_payload(client, gpid, cpid, **kwargs),
        follow_redirects=True,
    )


def test_creating_an_item_captures_the_whole_eligible_roster_atomically(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        fx.enroll(group, "a@example.com", name="Alice")
        fx.enroll(group, "b@example.com", name="Bob")
        cat = fx.category(group, "Homework", 10000)
        gpid, cpid = group.public_id, cat.public_id
    fx.login_as(client, "teacher@example.com")
    resp = _make_item(client, app, gpid, cpid, title="HW 1", max_points="20")
    assert "Grade item created for 2 students" in resp.get_data(as_text=True)
    with app.app_context():
        item = GradeItem.query.one()
        assert item.max_points == Decimal("20.00")
        assert item.released_at is None
        assert item.version == 1
        records = GradeRecord.query.filter_by(grade_item_id=item.id).all()
        assert len(records) == 2
        assert all(r.score is None and r.comment is None for r in records)
        assert all(r.graded_by_id is None and r.graded_at is None for r in records)
        assert all(r.created_at == r.updated_at == item.created_at for r in records)


@pytest.mark.parametrize(
    "status_kwargs",
    [
        {"status": "withdrawn"},
        {"account_status": UserStatus.SUSPENDED.value},
        {"role": UserRole.TEACHER.value},
    ],
)
def test_only_eligible_active_students_are_captured(app, client, status_kwargs):
    with app.app_context():
        teacher, group = fx.setup_group()
        fx.enroll(group, "ok@example.com", name="Ok")
        fx.enroll(group, "no@example.com", name="No", **status_kwargs)
        cat = fx.category(group, "Homework", 10000)
        gpid, cpid = group.public_id, cat.public_id
    fx.login_as(client, "teacher@example.com")
    resp = _make_item(client, app, gpid, cpid)
    assert "created for 1 student." in resp.get_data(as_text=True)
    with app.app_context():
        assert GradeRecord.query.count() == 1


def test_an_item_cannot_be_created_for_an_empty_roster(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        cat = fx.category(group, "Homework", 10000)
        gpid, cpid = group.public_id, cat.public_id
    fx.login_as(client, "teacher@example.com")
    resp = _make_item(client, app, gpid, cpid)
    assert b"no active enrolled students" in resp.data
    with app.app_context():
        assert GradeItem.query.count() == 0
        assert GradeRecord.query.count() == 0


def test_an_item_cannot_be_created_before_a_category_exists(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        fx.enroll(group)
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    resp = client.get(fx.item_new(gpid), follow_redirects=True)
    assert b"Create at least one grade category" in resp.data


def test_a_later_enrollment_never_joins_an_existing_item(app, client):
    """The roster is a statement about who was being graded when the item
    was created; re-deriving it later would rewrite history."""
    with app.app_context():
        teacher, group = fx.setup_group()
        fx.enroll(group, "first@example.com", name="First")
        cat = fx.category(group, "Homework", 10000)
        gpid, cpid = group.public_id, cat.public_id
    fx.login_as(client, "teacher@example.com")
    _make_item(client, app, gpid, cpid)
    with app.app_context():
        assert GradeRecord.query.count() == 1
        fx.enroll(group, "later@example.com", name="Later")
    # Merely reading the gradebook must not top the roster up.
    _gradebook(client, gpid)
    with app.app_context():
        assert GradeRecord.query.count() == 1


def test_a_withdrawal_never_removes_an_existing_record(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        student = fx.enroll(group, "s@example.com", name="S")
        cat = fx.category(group, "Homework", 10000)
        item, records = fx.item_with_roster(cat, [student], scores=("10.00",))
        record_id = records[0].id
        fx.withdraw(group, student)
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")
    _gradebook(client, gpid)
    with app.app_context():
        kept = db.session.get(GradeRecord, record_id)
        assert kept is not None
        assert kept.score == Decimal("10.00")


@pytest.mark.parametrize(
    "kind,field",
    [
        ("assignment", "assignment_source"),
        ("quiz", "quiz_source"),
        ("speaking", "speaking_source"),
    ],
)
def test_a_linked_kind_requires_its_own_source(app, client, kind, field):
    with app.app_context():
        teacher, group = fx.setup_group()
        fx.enroll(group)
        cat = fx.category(group, "Homework", 10000)
        gpid, cpid = group.public_id, cat.public_id
    fx.login_as(client, "teacher@example.com")
    resp = _make_item(client, app, gpid, cpid, source_kind=kind)
    assert b"Choose which one of this group" in resp.data
    with app.app_context():
        assert GradeItem.query.count() == 0


@pytest.mark.parametrize("kind", ["activity", "manual"])
def test_an_unlinked_kind_may_not_carry_a_source(app, client, kind):
    with app.app_context():
        teacher, group = fx.setup_group()
        fx.enroll(group)
        cat = fx.category(group, "Homework", 10000)
        quiz = fx.quiz_for(group)
        gpid, cpid, qpid = group.public_id, cat.public_id, quiz.public_id
    fx.login_as(client, "teacher@example.com")
    resp = _make_item(client, app, gpid, cpid, source_kind=kind, quiz_source=qpid)
    assert b"not linked to anything" in resp.data
    with app.app_context():
        assert GradeItem.query.count() == 0


def test_a_source_from_another_group_is_refused(app, client):
    with app.app_context():
        teacher, group = fx.setup_group("A")
        fx.enroll(group, "a@example.com")
        cat = fx.category(group, "Homework", 10000)
        _, other = fx.setup_group("B", teacher_email="tb@example.com")
        foreign = fx.quiz_for(other, title="Foreign quiz")
        gpid, cpid, qpid = group.public_id, cat.public_id, foreign.public_id
    fx.login_as(client, "teacher@example.com")
    resp = _make_item(client, app, gpid, cpid, source_kind="quiz", quiz_source=qpid)
    body = resp.get_data(as_text=True)
    assert "not one of this group" in body or "Choose which one of this group" in body
    with app.app_context():
        assert GradeItem.query.count() == 0


def test_a_category_from_another_group_is_refused(app, client):
    with app.app_context():
        teacher, group = fx.setup_group("A")
        fx.enroll(group, "a@example.com")
        fx.category(group, "Homework", 10000)
        b = fx.full_gradebook("B", teacher_email="tb@example.com")
        gpid, foreign_cpid = group.public_id, b["category_public_ids"][0]
    fx.login_as(client, "teacher@example.com")
    resp = _make_item(client, app, gpid, foreign_cpid)
    assert resp.status_code == 200
    with app.app_context():
        assert GradeItem.query.filter_by(title="New item").count() == 0


def test_a_listening_activity_is_graded_as_its_backing_quiz(app, client):
    """A Listening activity *is* a Quiz, so it appears in the quiz
    source list and there is no separate source kind for it."""
    with app.app_context():
        from app.models import ListeningActivity, TranscriptVisibility

        teacher, group = fx.setup_group()
        fx.enroll(group)
        cat = fx.category(group, "Listening", 10000)
        quiz = fx.quiz_for(group, title="Listening quiz")
        from app.models import FileCategory, UploadedFile

        audio = UploadedFile(
            storage_key="listening-audio-1",
            original_filename="clip.mp3",
            extension="mp3",
            category=FileCategory.AUDIO.value,
            content_type="audio/mpeg",
            byte_size=1024,
            sha256="0" * 64,
            uploaded_by_id=teacher.id,
        )
        db.session.add(audio)
        db.session.commit()
        db.session.add(
            ListeningActivity(
                quiz_id=quiz.id,
                audio_file_id=audio.id,
                transcript=None,
                transcript_visibility=TranscriptVisibility.HIDDEN.value,
                vocabulary_notes=None,
                creation_nonce="listening-nonce-1",
                created_at=fx.NOW,
                updated_at=fx.NOW,
            )
        )
        db.session.commit()
        gpid, cpid, qpid = group.public_id, cat.public_id, quiz.public_id
    fx.login_as(client, "teacher@example.com")
    resp = _make_item(
        client, app, gpid, cpid, source_kind="quiz", quiz_source=qpid, title="L1"
    )
    assert "Grade item created" in resp.get_data(as_text=True)
    with app.app_context():
        item = GradeItem.query.one()
        assert item.source_kind == "quiz"
        assert item.quiz_id is not None


def test_no_score_is_imported_from_a_linked_quiz_attempt(app, client):
    """Quiz attempts keep their own results; a grade is the number a
    Teacher deliberately entered."""
    with app.app_context():
        from app.models import QuizAttempt, QuizAttemptStatus

        teacher, group = fx.setup_group()
        student = fx.enroll(group, "s@example.com")
        cat = fx.category(group, "Quizzes", 10000)
        quiz = fx.quiz_for(group)
        db.session.add(
            QuizAttempt(
                quiz_id=quiz.id,
                student_id=student.id,
                attempt_number=1,
                status=QuizAttemptStatus.SUBMITTED.value,
                quiz_version=1,
                started_at=fx.NOW,
                deadline_at=fx.LATER,
                submitted_at=fx.LATER,
                correct_count=7,
                total_questions=10,
            )
        )
        db.session.commit()
        gpid, cpid, qpid = group.public_id, cat.public_id, quiz.public_id
    fx.login_as(client, "teacher@example.com")
    _make_item(client, app, gpid, cpid, source_kind="quiz", quiz_source=qpid)
    with app.app_context():
        record = GradeRecord.query.one()
        assert record.score is None


def test_an_item_edit_changes_a_draft_and_never_the_roster(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        student = fx.enroll(group, "s@example.com")
        homework = fx.category(group, "Homework", 5000)
        speaking = fx.category(group, "Speaking", 5000)
        item, _ = fx.item_with_roster(homework, [student], title="Draft")
        gpid, ipid = group.public_id, item.public_id
        target_cpid = speaking.public_id
    fx.login_as(client, "teacher@example.com")
    resp = client.post(
        fx.item_edit(gpid, ipid),
        data=fx.item_payload(
            client, gpid, target_cpid, title="Moved", max_points="35", ipid=ipid
        ),
        follow_redirects=True,
    )
    assert "Grade item saved" in resp.get_data(as_text=True)
    with app.app_context():
        row = GradeItem.query.one()
        assert row.title == "Moved"
        assert row.max_points == Decimal("35.00")
        assert row.version == 2
        assert GradeRecord.query.filter_by(grade_item_id=row.id).count() == 1


def test_changing_a_draft_item_from_quiz_to_manual_clears_the_old_link(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        student = fx.enroll(group, "s@example.com")
        cat = fx.category(group, "Homework", 10000)
        quiz = fx.quiz_for(group)
        item, _ = fx.item_with_roster(
            cat, [student], source_kind="quiz", quiz=quiz, title="Q"
        )
        gpid, ipid, cpid = group.public_id, item.public_id, cat.public_id
    fx.login_as(client, "teacher@example.com")
    client.post(
        fx.item_edit(gpid, ipid),
        data=fx.item_payload(
            client, gpid, cpid, title="Q", source_kind="manual", ipid=ipid
        ),
        follow_redirects=True,
    )
    with app.app_context():
        row = GradeItem.query.one()
        assert row.source_kind == "manual"
        assert row.quiz_id is None


def test_a_released_item_cannot_be_edited_or_reassigned(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        student = fx.enroll(group, "s@example.com")
        homework = fx.category(group, "Homework", 5000)
        speaking = fx.category(group, "Speaking", 5000)
        item, _ = fx.item_with_roster(
            homework, [student], scores=("10.00",), released=True, title="Released"
        )
        gpid, ipid = group.public_id, item.public_id
        target_cpid = speaking.public_id
        original_category_id = item.category_id
    fx.login_as(client, "teacher@example.com")
    resp = client.get(fx.item_edit(gpid, ipid), follow_redirects=True)
    assert b"can no longer be changed" in resp.data
    resp = client.post(
        fx.item_edit(gpid, ipid),
        data={
            "category": target_cpid,
            "title": "Hacked",
            "source_kind": "manual",
            "assignment_source": "",
            "quiz_source": "",
            "speaking_source": "",
            "max_points": "1",
            "grade_state": "anything",
        },
        follow_redirects=True,
    )
    assert b"can no longer be changed" in resp.data
    with app.app_context():
        row = GradeItem.query.one()
        assert row.title == "Released"
        assert row.category_id == original_category_id
        assert row.max_points == Decimal("20.00")


def test_there_is_no_item_delete_or_unrelease_route(app, client):
    with app.app_context():
        data = fx.full_gradebook()
        gpid, ipid = data["group_public_id"], data["item_public_ids"][0]
    fx.login_as(client, data["teacher_email"])
    for url in (
        f"{fx.item_detail(gpid, ipid)}/delete",
        f"{fx.item_detail(gpid, ipid)}/unrelease",
        f"{fx.item_detail(gpid, ipid)}/reopen",
        f"{fx.item_detail(gpid, ipid)}/duplicate",
        f"{fx.item_detail(gpid, ipid)}/import",
    ):
        assert client.post(url, data={}).status_code in (404, 405), url
    assert client.delete(fx.item_detail(gpid, ipid)).status_code == 405


# ===========================================================================
# Scores
# ===========================================================================


def test_a_teacher_enters_scores_and_comments(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        alice = fx.enroll(group, "a@example.com", name="Alice")
        cat = fx.category(group, "Homework", 10000)
        item, _ = fx.item_with_roster(cat, [alice])
        gpid, ipid, teacher_id = group.public_id, item.public_id, teacher.id
    fx.login_as(client, "teacher@example.com")
    url = fx.item_scores(gpid, ipid)
    rid = fx.record_public_ids(client, url)[0]
    resp = client.post(
        url,
        data=fx.score_payload(
            client, url, scores={rid: "17.25"}, comments={rid: "Nice work"}
        ),
        follow_redirects=True,
    )
    assert "Scores saved" in resp.get_data(as_text=True)
    with app.app_context():
        record = GradeRecord.query.one()
        assert record.score == Decimal("17.25")
        assert record.comment == "Nice work"
        assert record.graded_by_id == teacher_id
        assert record.graded_at is not None
        assert record.version == 2
        assert GradeItem.query.one().version == 2


def test_a_no_op_score_save_changes_nothing(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        alice = fx.enroll(group, "a@example.com", name="Alice")
        cat = fx.category(group, "Homework", 10000)
        item, _ = fx.item_with_roster(cat, [alice], scores=("10.00",))
        gpid, ipid = group.public_id, item.public_id
    fx.login_as(client, "teacher@example.com")
    url = fx.item_scores(gpid, ipid)
    resp = client.post(url, data=fx.score_payload(client, url), follow_redirects=True)
    assert "Nothing was changed" in resp.get_data(as_text=True)
    with app.app_context():
        record = GradeRecord.query.one()
        assert record.version == 1
        assert record.updated_at == record.created_at
        assert GradeItem.query.one().version == 1


@pytest.mark.parametrize("bad", ["abc", "-1", "1.005", "1e2", "20,5", "NaN", "5."])
def test_an_invalid_score_rejects_the_whole_sheet(app, client, bad):
    """One bad value writes nothing at all -- not the bad one, and not
    the good one beside it."""
    with app.app_context():
        teacher, group = fx.setup_group()
        alice = fx.enroll(group, "a@example.com", name="Alice")
        bob = fx.enroll(group, "b@example.com", name="Bob")
        cat = fx.category(group, "Homework", 10000)
        item, _ = fx.item_with_roster(cat, [alice, bob])
        gpid, ipid = group.public_id, item.public_id
    fx.login_as(client, "teacher@example.com")
    url = fx.item_scores(gpid, ipid)
    ids = fx.record_public_ids(client, url)
    resp = client.post(
        url, data=fx.score_payload(client, url, scores={ids[0]: bad, ids[1]: "12.00"})
    )
    assert resp.status_code == 200
    with app.app_context():
        assert GradeRecord.query.filter(GradeRecord.score.isnot(None)).count() == 0
        assert all(r.version == 1 for r in GradeRecord.query.all())
        assert GradeItem.query.one().version == 1


def test_a_score_above_the_maximum_rejects_the_whole_sheet(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        alice = fx.enroll(group, "a@example.com", name="Alice")
        bob = fx.enroll(group, "b@example.com", name="Bob")
        cat = fx.category(group, "Homework", 10000)
        item, _ = fx.item_with_roster(cat, [alice, bob], max_points="20.00")
        gpid, ipid = group.public_id, item.public_id
    fx.login_as(client, "teacher@example.com")
    url = fx.item_scores(gpid, ipid)
    ids = fx.record_public_ids(client, url)
    resp = client.post(
        url,
        data=fx.score_payload(client, url, scores={ids[0]: "20.01", ids[1]: "10.00"}),
    )
    assert b"higher than this item" in resp.data
    with app.app_context():
        assert GradeRecord.query.filter(GradeRecord.score.isnot(None)).count() == 0


def test_a_score_exactly_at_the_maximum_is_accepted(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        alice = fx.enroll(group, "a@example.com", name="Alice")
        cat = fx.category(group, "Homework", 10000)
        item, _ = fx.item_with_roster(cat, [alice], max_points="20.00")
        gpid, ipid = group.public_id, item.public_id
    fx.login_as(client, "teacher@example.com")
    url = fx.item_scores(gpid, ipid)
    rid = fx.record_public_ids(client, url)[0]
    client.post(
        url, data=fx.score_payload(client, url, scores={rid: "20"}), follow_redirects=True
    )
    with app.app_context():
        assert GradeRecord.query.one().score == Decimal("20.00")


def test_a_blank_score_is_legitimate_on_a_draft(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        alice = fx.enroll(group, "a@example.com", name="Alice")
        cat = fx.category(group, "Homework", 10000)
        item, _ = fx.item_with_roster(cat, [alice], scores=("10.00",))
        gpid, ipid = group.public_id, item.public_id
    fx.login_as(client, "teacher@example.com")
    url = fx.item_scores(gpid, ipid)
    rid = fx.record_public_ids(client, url)[0]
    client.post(
        url, data=fx.score_payload(client, url, scores={rid: ""}), follow_redirects=True
    )
    with app.app_context():
        assert GradeRecord.query.one().score is None


def test_a_comment_longer_than_the_limit_is_refused(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        alice = fx.enroll(group, "a@example.com", name="Alice")
        cat = fx.category(group, "Homework", 10000)
        item, _ = fx.item_with_roster(cat, [alice])
        gpid, ipid = group.public_id, item.public_id
    fx.login_as(client, "teacher@example.com")
    url = fx.item_scores(gpid, ipid)
    rid = fx.record_public_ids(client, url)[0]
    resp = client.post(
        url,
        data=fx.score_payload(
            client, url, scores={rid: "1"}, comments={rid: "x" * 1001}
        ),
    )
    assert b"limited to 1000 characters" in resp.data
    with app.app_context():
        assert GradeRecord.query.one().comment is None


def test_a_whitespace_only_comment_is_stored_as_no_comment(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        alice = fx.enroll(group, "a@example.com", name="Alice")
        cat = fx.category(group, "Homework", 10000)
        item, _ = fx.item_with_roster(cat, [alice])
        gpid, ipid = group.public_id, item.public_id
    fx.login_as(client, "teacher@example.com")
    url = fx.item_scores(gpid, ipid)
    rid = fx.record_public_ids(client, url)[0]
    client.post(
        url,
        data=fx.score_payload(client, url, scores={rid: "1"}, comments={rid: "   "}),
        follow_redirects=True,
    )
    with app.app_context():
        assert GradeRecord.query.one().comment is None


def test_a_request_naming_an_unknown_record_changes_nothing(app, client):
    """The submitted field set is matched against the roster read from
    the database, never the other way round."""
    with app.app_context():
        teacher, group = fx.setup_group()
        alice = fx.enroll(group, "a@example.com", name="Alice")
        cat = fx.category(group, "Homework", 10000)
        item, _ = fx.item_with_roster(cat, [alice])
        gpid, ipid = group.public_id, item.public_id
    fx.login_as(client, "teacher@example.com")
    url = fx.item_scores(gpid, ipid)
    payload = fx.score_payload(client, url)
    payload["score__00000000-0000-0000-0000-000000000000"] = "99"
    payload["comment__00000000-0000-0000-0000-000000000000"] = "injected"
    client.post(url, data=payload, follow_redirects=True)
    with app.app_context():
        assert GradeRecord.query.count() == 1
        assert GradeRecord.query.one().comment is None


def test_a_request_omitting_a_record_is_an_ordinary_validation_error(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        alice = fx.enroll(group, "a@example.com", name="Alice")
        bob = fx.enroll(group, "b@example.com", name="Bob")
        cat = fx.category(group, "Homework", 10000)
        item, _ = fx.item_with_roster(cat, [alice, bob])
        gpid, ipid = group.public_id, item.public_id
    fx.login_as(client, "teacher@example.com")
    url = fx.item_scores(gpid, ipid)
    payload = fx.score_payload(client, url)
    dropped = fx.record_public_ids(client, url)[0]
    payload.pop(f"score__{dropped}")
    resp = client.post(url, data=payload)
    assert b"was missing from this form" in resp.data
    with app.app_context():
        assert all(r.score is None for r in GradeRecord.query.all())


def test_a_stale_score_token_is_rejected_and_writes_nothing(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        alice = fx.enroll(group, "a@example.com", name="Alice")
        cat = fx.category(group, "Homework", 10000)
        item, records = fx.item_with_roster(cat, [alice])
        gpid, ipid, record_id = group.public_id, item.public_id, records[0].id
    fx.login_as(client, "teacher@example.com")
    url = fx.item_scores(gpid, ipid)
    payload = fx.score_payload(client, url, scores={fx.record_public_ids(client, url)[0]: "5"})
    # A co-teacher saves first, bumping the record's version.
    with app.app_context():
        row = db.session.get(GradeRecord, record_id)
        row.score = Decimal("9.00")
        row.version = 2
        db.session.commit()
    resp = client.post(url, data=payload, follow_redirects=True)
    assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        row = db.session.get(GradeRecord, record_id)
        assert row.score == Decimal("9.00")
        assert row.version == 2


def test_an_empty_or_forged_token_is_rejected(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        alice = fx.enroll(group, "a@example.com", name="Alice")
        cat = fx.category(group, "Homework", 10000)
        item, _ = fx.item_with_roster(cat, [alice])
        gpid, ipid = group.public_id, item.public_id
    fx.login_as(client, "teacher@example.com")
    url = fx.item_scores(gpid, ipid)
    rid = fx.record_public_ids(client, url)[0]
    for token in ("", "not-a-token", "a.b.c"):
        client.post(
            url,
            data=fx.score_payload(client, url, scores={rid: "5"}, token=token),
            follow_redirects=True,
        )
        with app.app_context():
            assert GradeRecord.query.one().score is None


# ===========================================================================
# Release
# ===========================================================================


def test_a_complete_item_releases(app, client):
    with app.app_context():
        data = fx.full_gradebook()
        gpid, ipid = data["group_public_id"], data["item_public_ids"][0]
    fx.login_as(client, data["teacher_email"])
    resp = fx.release_via_route(client, gpid, ipid)
    assert "Grade item released" in resp.get_data(as_text=True)
    with app.app_context():
        item = GradeItem.query.filter_by(public_id=ipid).one()
        assert item.released_at is not None
        assert item.version == 2


def test_release_requires_the_confirmation_box(app, client):
    with app.app_context():
        data = fx.full_gradebook()
        gpid, ipid = data["group_public_id"], data["item_public_ids"][0]
    fx.login_as(client, data["teacher_email"])
    token = fx.token_from(client, fx.item_detail(gpid, ipid))
    resp = client.post(
        fx.item_release(gpid, ipid), data={"grade_state": token}, follow_redirects=True
    )
    assert b"tick the confirmation box" in resp.data
    with app.app_context():
        assert GradeItem.query.filter_by(public_id=ipid).one().released_at is None


def test_release_is_refused_while_a_score_is_missing(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        alice = fx.enroll(group, "a@example.com", name="Alice")
        bob = fx.enroll(group, "b@example.com", name="Bob")
        cat = fx.category(group, "Homework", 10000)
        item, _ = fx.item_with_roster(cat, [alice, bob], scores=("10.00",))
        gpid, ipid = group.public_id, item.public_id
    fx.login_as(client, "teacher@example.com")
    detail = client.get(fx.item_detail(gpid, ipid)).get_data(as_text=True)
    assert "needs a score before it can be released" in detail
    # The page does not even render a token, and a hand-built POST fails too.
    resp = client.post(
        fx.item_release(gpid, ipid),
        data={"grade_state": "anything", "confirm_release": "yes"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        assert GradeItem.query.filter_by(public_id=ipid).one().released_at is None


def test_release_is_refused_when_the_weights_are_not_exactly_one_hundred(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        alice = fx.enroll(group, "a@example.com", name="Alice")
        cat = fx.category(group, "Homework", 6000)
        item, _ = fx.item_with_roster(cat, [alice], scores=("10.00",))
        gpid, ipid = group.public_id, item.public_id
    fx.login_as(client, "teacher@example.com")
    detail = client.get(fx.item_detail(gpid, ipid)).get_data(as_text=True)
    assert "must add up to exactly 100%" in detail
    resp = client.post(
        fx.item_release(gpid, ipid),
        data={"grade_state": "anything", "confirm_release": "yes"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        assert GradeItem.query.filter_by(public_id=ipid).one().released_at is None


@pytest.mark.parametrize("kind", ["assignment", "quiz", "speaking"])
def test_release_is_refused_while_the_linked_source_is_a_draft(app, client, kind):
    with app.app_context():
        teacher, group = fx.setup_group()
        alice = fx.enroll(group, "a@example.com", name="Alice")
        cat = fx.category(group, "Homework", 10000)
        source = {
            "assignment": lambda: fx.assignment_for(group, "A1", published=False),
            "quiz": lambda: fx.quiz_for(group, "Q1", published=False),
            "speaking": lambda: fx.speaking_for(group, "S1", published=False),
        }[kind]()
        kwargs = {
            "assignment": {"assignment": source},
            "quiz": {"quiz": source},
            "speaking": {"speaking": source},
        }[kind]
        item, _ = fx.item_with_roster(
            cat, [alice], scores=("10.00",), source_kind=kind, **kwargs
        )
        gpid, ipid = group.public_id, item.public_id
    fx.login_as(client, "teacher@example.com")
    detail = client.get(fx.item_detail(gpid, ipid)).get_data(as_text=True)
    assert "still a draft" in detail
    resp = client.post(
        fx.item_release(gpid, ipid),
        data={"grade_state": "anything", "confirm_release": "yes"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        assert GradeItem.query.filter_by(public_id=ipid).one().released_at is None


def test_a_published_linked_source_releases(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        alice = fx.enroll(group, "a@example.com", name="Alice")
        cat = fx.category(group, "Homework", 10000)
        source = fx.assignment_for(group, "A1", published=True)
        item, _ = fx.item_with_roster(
            cat, [alice], scores=("10.00",), source_kind="assignment", assignment=source
        )
        gpid, ipid = group.public_id, item.public_id
    fx.login_as(client, "teacher@example.com")
    resp = fx.release_via_route(client, gpid, ipid)
    assert "Grade item released" in resp.get_data(as_text=True)


def test_a_release_replay_is_safe(app, client):
    with app.app_context():
        data = fx.full_gradebook()
        gpid, ipid = data["group_public_id"], data["item_public_ids"][0]
    fx.login_as(client, data["teacher_email"])
    token = fx.token_from(client, fx.item_detail(gpid, ipid))
    client.post(
        fx.item_release(gpid, ipid),
        data={"grade_state": token, "confirm_release": "yes"},
        follow_redirects=True,
    )
    with app.app_context():
        first = GradeItem.query.filter_by(public_id=ipid).one()
        released_at, version = first.released_at, first.version
    resp = client.post(
        fx.item_release(gpid, ipid),
        data={"grade_state": token, "confirm_release": "yes"},
        follow_redirects=True,
    )
    assert "was already released" in resp.get_data(as_text=True)
    with app.app_context():
        again = GradeItem.query.filter_by(public_id=ipid).one()
        assert again.released_at == released_at
        assert again.version == version


def test_a_stale_release_token_is_rejected(app, client):
    with app.app_context():
        data = fx.full_gradebook()
        gpid, ipid = data["group_public_id"], data["item_public_ids"][0]
        record_id = data["record_ids"][0][0]
    fx.login_as(client, data["teacher_email"])
    token = fx.token_from(client, fx.item_detail(gpid, ipid))
    with app.app_context():
        row = db.session.get(GradeRecord, record_id)
        row.score = Decimal("1.00")
        row.version = 2
        db.session.commit()
    resp = client.post(
        fx.item_release(gpid, ipid),
        data={"grade_state": token, "confirm_release": "yes"},
        follow_redirects=True,
    )
    assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert GradeItem.query.filter_by(public_id=ipid).one().released_at is None


def test_a_released_score_may_be_corrected_but_not_cleared(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        alice = fx.enroll(group, "a@example.com", name="Alice")
        cat = fx.category(group, "Homework", 10000)
        item, _ = fx.item_with_roster(
            cat, [alice], scores=("10.00",), released=True
        )
        gpid, ipid = group.public_id, item.public_id
    fx.login_as(client, "teacher@example.com")
    url = fx.item_scores(gpid, ipid)
    rid = fx.record_public_ids(client, url)[0]

    resp = client.post(
        url,
        data=fx.score_payload(client, url, scores={rid: "12.50"}),
        follow_redirects=True,
    )
    assert "Scores corrected" in resp.get_data(as_text=True)
    with app.app_context():
        assert GradeRecord.query.one().score == Decimal("12.50")

    resp = client.post(url, data=fx.score_payload(client, url, scores={rid: ""}))
    assert b"cannot be left blank" in resp.data
    with app.app_context():
        assert GradeRecord.query.one().score == Decimal("12.50")


# ===========================================================================
# Archived chains -- readable, not writable
# ===========================================================================


@pytest.mark.parametrize(
    "kwargs",
    [
        {"group_status": AcademicStatus.ARCHIVED.value},
        {"term_status": AcademicStatus.ARCHIVED.value},
        {"course_status": AcademicStatus.ARCHIVED.value},
        {"level_status": AcademicStatus.ARCHIVED.value},
    ],
)
def test_an_archived_chain_stays_readable_and_refuses_every_write(app, client, kwargs):
    with app.app_context():
        teacher, group = fx.setup_group(**kwargs)
        alice = fx.enroll(group, "a@example.com", name="Alice")
        cat = fx.category(group, "Homework", 10000)
        item, _ = fx.item_with_roster(cat, [alice], scores=("10.00",))
        gpid, ipid, cpid = group.public_id, item.public_id, cat.public_id
    fx.login_as(client, "teacher@example.com")
    assert client.get(fx.teacher_base(gpid)).status_code == 200
    assert client.get(fx.item_detail(gpid, ipid)).status_code == 200

    for url in (fx.category_new(gpid), fx.category_edit(gpid, cpid), fx.item_new(gpid),
                fx.item_edit(gpid, ipid), fx.item_scores(gpid, ipid)):
        resp = client.get(url, follow_redirects=True)
        assert b"can only be changed while the group" in resp.data, url
    resp = client.post(
        fx.item_release(gpid, ipid),
        data={"grade_state": "x", "confirm_release": "yes"},
        follow_redirects=True,
    )
    assert b"can only be changed while the group" in resp.data
    with app.app_context():
        assert GradeItem.query.one().released_at is None
        assert GradeRecord.query.one().score == Decimal("10.00")


# ===========================================================================
# Performance -- bounded, and free of N+1
# ===========================================================================


def test_the_gradebook_page_cost_does_not_grow_with_its_size(app, client):
    def query_count(items_per_category, students):
        with app.app_context():
            db.drop_all()
            db.create_all()
            teacher, group = fx.setup_group()
            roster = [
                fx.enroll(group, f"s{i}@example.com", name=f"S{i}")
                for i in range(students)
            ]
            for index, (title, weight) in enumerate((("A", 5000), ("B", 5000))):
                cat = fx.category(group, title, weight)
                for n in range(items_per_category):
                    fx.item_with_roster(
                        cat,
                        roster,
                        scores=("10.00",) * students,
                        released=True,
                        title=f"{title}-{n}",
                    )
            gpid = group.public_id
        fx.login_as(client, "teacher@example.com")

        recorded = []

        def _rec(conn, cursor, statement, parameters, context, executemany):
            recorded.append(statement)

        with app.app_context():
            event.listen(db.engine, "before_cursor_execute", _rec)
        try:
            assert client.get(fx.teacher_base(gpid)).status_code == 200
        finally:
            with app.app_context():
                event.remove(db.engine, "before_cursor_execute", _rec)
        return len([s for s in recorded if s.strip().upper().startswith("SELECT")])

    assert query_count(1, 1) == query_count(5, 6)


def test_the_gradebook_page_issues_no_per_item_or_per_student_query(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        roster = [fx.enroll(group, f"s{i}@example.com", name=f"S{i}") for i in range(4)]
        cat = fx.category(group, "Homework", 10000)
        for n in range(4):
            fx.item_with_roster(
                cat, roster, scores=("10.00",) * 4, released=True, title=f"I{n}"
            )
        gpid = group.public_id
    fx.login_as(client, "teacher@example.com")

    recorded = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        recorded.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        assert client.get(fx.teacher_base(gpid)).status_code == 200
    finally:
        with app.app_context():
            event.remove(db.engine, "before_cursor_execute", _rec)

    item_reads = [s for s in recorded if "FROM grade_items" in s]
    record_reads = [s for s in recorded if "FROM grade_records" in s]
    assert len(item_reads) == 1, item_reads
    assert len(record_reads) <= 2, record_reads


# ===========================================================================
# The lock chain -- structural
# ===========================================================================
#
# SQLite honours neither ``FOR UPDATE`` nor REPEATABLE READ, so everything
# below asserts what the code *requests*, not that anything blocks. Real
# InnoDB behaviour is out of reach of this suite and is stated as such in
# docs/DECISIONS.md.


def _locked_tables(app, call):
    """The distinct tables one lock chain reads from, in first-touch
    order.

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


def test_the_creation_chain_requests_the_documented_lock_order(app):
    from app.services.grade_transactions import lock_gradebook_chain

    with app.app_context():
        teacher, group = fx.setup_group()
        alice = fx.enroll(group, "a@example.com", name="Alice")
        fx.category(group, "Homework", 10000)
        quiz = fx.quiz_for(group)
        from app.models import Enrollment

        args = (
            group.public_id,
            group.academic_term_id,
            group.course.level_id,
            group.course_id,
            teacher.id,
        )
        student_ids = [alice.id]
        enrollment_ids = [row.id for row in Enrollment.query.order_by(Enrollment.id)]
        quiz_id = quiz.id
        ordered = _locked_tables(
            app,
            lambda: lock_gradebook_chain(
                *args,
                source_kind="quiz",
                source_id=quiz_id,
                student_ids=student_ids,
                enrollment_ids=enrollment_ids,
            ),
        )
    assert ordered == [
        "academic_terms",
        "levels",
        "courses",
        "groups",
        "users",
        "group_teacher_assignments",
        "grade_categories",
        "quizzes",
        "enrollments",
    ]


def test_the_scoring_chain_requests_the_documented_lock_order(app):
    from app.services.grade_transactions import lock_gradebook_chain

    with app.app_context():
        teacher, group = fx.setup_group()
        alice = fx.enroll(group, "a@example.com", name="Alice")
        cat = fx.category(group, "Homework", 10000)
        item, records = fx.item_with_roster(cat, [alice])
        args = (
            group.public_id,
            group.academic_term_id,
            group.course.level_id,
            group.course_id,
            teacher.id,
        )
        item_ids = [item.id]
        student_ids = [alice.id]
        record_ids = [record.id for record in records]
        ordered = _locked_tables(
            app,
            lambda: lock_gradebook_chain(
                *args,
                item_ids=item_ids,
                student_ids=student_ids,
                record_ids=record_ids,
            ),
        )
    assert ordered == [
        "academic_terms",
        "levels",
        "courses",
        "groups",
        "users",
        "group_teacher_assignments",
        "grade_categories",
        "grade_items",
        "grade_records",
    ]


def test_every_chain_locks_rows_of_one_type_in_ascending_internal_id(app):
    """Never display order, and never the order a form submitted things
    in -- which matters especially here because the score sheet is ordered
    by Student *name*."""
    from app.services.grade_transactions import lock_gradebook_chain

    seen = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        flat = " ".join(statement.split())
        for table in ("grade_records", "grade_categories", "users"):
            if f"FROM {table}" in flat and parameters:
                seen.append((table, tuple(parameters)))

    with app.app_context():
        teacher, group = fx.setup_group()
        # Deliberately created so that alphabetical name order is the
        # REVERSE of internal id order.
        students = [
            fx.enroll(group, f"s{index}@example.com", name=name)
            for index, name in enumerate(("Zoe", "Yara", "Xena", "Wade"))
        ]
        cat = fx.category(group, "Homework", 10000)
        item, records = fx.item_with_roster(cat, students)
        args = (
            group.public_id,
            group.academic_term_id,
            group.course.level_id,
            group.course_id,
            teacher.id,
        )
        item_ids = [item.id]
        # Submitted in a deliberately scrambled order.
        student_ids = list(reversed([s.id for s in students]))
        record_ids = list(reversed([r.id for r in records]))
        expected_students = sorted(s.id for s in students)
        expected_records = sorted(r.id for r in records)

        event.listen(db.engine, "before_cursor_execute", _rec)
        try:
            lock_gradebook_chain(
                *args,
                item_ids=item_ids,
                student_ids=student_ids,
                record_ids=record_ids,
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", _rec)

    record_order = [p[0] for table, p in seen if table == "grade_records"]
    assert record_order == expected_records
    # The acting Teacher and captured Students form one global ascending
    # User set, regardless of role or submitted order.
    user_order = [p[0] for table, p in seen if table == "users"]
    assert user_order == sorted({teacher.id, *expected_students})


def test_chain_sorts_teacher_with_students_when_a_student_has_the_lower_id(app):
    """Prevent the cross-Group inversion with M11 messaging: role must
    not decide which of the two shared User rows is locked first."""
    from app.models import Enrollment
    from app.services.grade_transactions import lock_gradebook_chain

    with app.app_context():
        group = fx.hierarchy("Participant order")
        student = fx.user("low-student@example.com", UserRole.STUDENT.value)
        teacher = fx.assign_teacher(group, "high-teacher@example.com")
        enrollment = Enrollment(group_id=group.id, student_id=student.id)
        db.session.add(enrollment)
        db.session.commit()

        student_id = student.id
        teacher_id = teacher.id
        enrollment_id = enrollment.id
        args = (
            group.public_id,
            group.academic_term_id,
            group.course.level_id,
            group.course_id,
            teacher_id,
        )

        seen = []

        def _rec(conn, cursor, statement, parameters, context, executemany):
            if re.search(r"\bFROM users WHERE users\.id = \?", " ".join(statement.split())):
                seen.append(tuple(parameters)[0])

        event.listen(db.engine, "before_cursor_execute", _rec)
        try:
            locks = lock_gradebook_chain(
                *args,
                student_ids=(value for value in [student_id]),
                enrollment_ids=[enrollment_id],
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", _rec)

        assert list(locks.students) == [student_id]
        assert locks.students[student_id] is not None

    assert student_id < teacher_id
    assert seen == sorted((student_id, teacher_id))


def test_the_chain_discovers_the_category_set_itself_rather_than_trusting_a_caller(
    app,
):
    """The property that closes the two-co-teachers-each-adding-60% race.

    The caller passes **no** category ids at all, yet every one of the
    Group's categories is locked -- because the chain re-reads the set
    inside the transaction, after the Group row is locked. A pre-lock
    preview could not have contained a category a competing request
    committed in the meantime.
    """
    from app.services.grade_transactions import lock_gradebook_chain

    with app.app_context():
        teacher, group = fx.setup_group()
        first = fx.category(group, "Homework", 3000)
        second = fx.category(group, "Speaking", 3000)
        third = fx.category(group, "Quizzes", 3000)
        # Another Group's category must not be locked or counted.
        _, other = fx.setup_group("B", teacher_email="tb@example.com")
        fx.category(other, "Elsewhere", 10000)
        args = (
            group.public_id,
            group.academic_term_id,
            group.course.level_id,
            group.course_id,
            teacher.id,
        )
        expected = sorted([first.id, second.id, third.id])

        locks = lock_gradebook_chain(*args)
        assert sorted(locks.categories) == expected
        assert sorted(locks.weights()) == [3000, 3000, 3000]
        assert len(locks.category_state()) == 3


def test_the_weight_total_is_read_from_the_locked_rows_not_from_a_preview(app):
    """A category committed after the preview is still counted, because
    the total is summed from what the chain locked."""
    from app.services.grade_calculations import total_weight
    from app.services.grade_queries import category_weight_rows
    from app.services.grade_transactions import lock_gradebook_chain

    with app.app_context():
        teacher, group = fx.setup_group()
        fx.category(group, "Homework", 6000)
        preview = [weight for _, weight in category_weight_rows(group.id)]
        assert total_weight(preview) == 6000

        # A competing request commits a second category.
        fx.category(group, "Speaking", 4000)

        locks = lock_gradebook_chain(
            group.public_id,
            group.academic_term_id,
            group.course.level_id,
            group.course_id,
            teacher.id,
        )
        assert total_weight(locks.weights()) == 10000
        assert total_weight(preview) == 6000


def test_the_chain_resets_the_transaction_exactly_once_before_its_first_lock(app):
    """The single deliberate reset is owned by ``lock_academic_hierarchy``
    and is the FIRST thing the chain does, so every read afterwards is in
    one fresh transaction."""
    from app.services import grade_transactions

    calls = []
    real = grade_transactions.lock_academic_hierarchy

    with app.app_context():
        teacher, group = fx.setup_group()
        fx.category(group, "Homework", 10000)
        args = (
            group.public_id,
            group.academic_term_id,
            group.course.level_id,
            group.course_id,
            teacher.id,
        )

        def _spy(*a, **kw):
            calls.append((a, kw))
            return real(*a, **kw)

        grade_transactions.lock_academic_hierarchy = _spy
        try:
            grade_transactions.lock_gradebook_chain(*args)
        finally:
            grade_transactions.lock_academic_hierarchy = real
    assert len(calls) == 1


def test_a_missing_row_anywhere_in_the_chain_is_returned_as_none(app):
    """Every ``None`` is a rejection the caller must act on, never
    something to keep going past."""
    from app.services.grade_transactions import lock_gradebook_chain

    with app.app_context():
        teacher, group = fx.setup_group()
        args = (
            group.public_id,
            group.academic_term_id,
            group.course.level_id,
            group.course_id,
            teacher.id,
        )
        locks = lock_gradebook_chain(
            *args, item_ids=[999999], source_kind="quiz", source_id=999999,
            student_ids=[999999], enrollment_ids=[999999], record_ids=[999999],
        )
        assert locks.item(999999) is None
        assert locks.source is None
        assert locks.students[999999] is None
        assert locks.enrollments[999999] is None
        assert locks.records[999999] is None

        missing = lock_gradebook_chain(
            "00000000-0000-0000-0000-000000000000", *args[1:]
        )
        assert missing.group is None
        assert missing.categories == {}


def test_an_unknown_group_public_id_locks_no_category_at_all(app):
    from app.services.grade_transactions import lock_gradebook_chain

    with app.app_context():
        teacher, group = fx.setup_group()
        fx.category(group, "Homework", 10000)
        locks = lock_gradebook_chain(
            "00000000-0000-0000-0000-000000000000",
            group.academic_term_id,
            group.course.level_id,
            group.course_id,
            teacher.id,
        )
        assert locks.group is None
        assert locks.weights() == []
        assert locks.category_state() == []


# ===========================================================================
# Atomicity -- the roster snapshot is all or nothing
# ===========================================================================


def test_a_failed_item_creation_leaves_neither_item_nor_record(app, client):
    """The item and every one of its records are one transaction: a
    failure at the commit leaves nothing behind, never an item with a
    partial roster."""
    from sqlalchemy.exc import IntegrityError

    with app.app_context():
        teacher, group = fx.setup_group()
        fx.enroll(group, "a@example.com", name="Alice")
        fx.enroll(group, "b@example.com", name="Bob")
        cat = fx.category(group, "Homework", 10000)
        gpid, cpid = group.public_id, cat.public_id
    fx.login_as(client, "teacher@example.com")

    real_commit = db.session.commit
    calls = {"n": 0}

    def _explode():
        calls["n"] += 1
        raise IntegrityError("forced", None, Exception("forced"))

    payload = fx.item_payload(client, gpid, cpid, title="Doomed")
    db.session.commit = _explode
    try:
        resp = client.post(fx.item_new(gpid), data=payload, follow_redirects=True)
    finally:
        db.session.commit = real_commit
    assert calls["n"] >= 1
    assert resp.status_code == 200
    with app.app_context():
        assert GradeItem.query.count() == 0
        assert GradeRecord.query.count() == 0


def test_the_roster_snapshot_is_written_with_one_shared_moment(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        for index in range(4):
            fx.enroll(group, f"s{index}@example.com", name=f"S{index}")
        cat = fx.category(group, "Homework", 10000)
        gpid, cpid = group.public_id, cat.public_id
    fx.login_as(client, "teacher@example.com")
    client.post(
        fx.item_new(gpid),
        data=fx.item_payload(client, gpid, cpid, title="Snapshot"),
        follow_redirects=True,
    )
    with app.app_context():
        item = GradeItem.query.one()
        records = GradeRecord.query.all()
        assert len(records) == 4
        moments = {record.created_at for record in records} | {
            record.updated_at for record in records
        }
        assert moments == {item.created_at}
