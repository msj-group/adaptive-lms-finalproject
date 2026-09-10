"""The three Phase 4 / M06 Speaking models: defaults, constraints,
relationships, and the things they deliberately do **not** have.

SQLite (the test backend) enforces NOT NULL, UNIQUE, CHECK and -- with
``PRAGMA foreign_keys=ON``, which ``app.extensions`` sets on every
connection -- foreign keys, so every invariant asserted here is really
enforced by the schema rather than only by the application. What SQLite
cannot demonstrate is InnoDB blocking, isolation or MySQL's own DDL; the
migration suite covers the schema shape and the route suites cover the
application rules.
"""

import secrets
from datetime import datetime

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    SPEAKING_FEEDBACK_MAX_LENGTH,
    Assignment,
    AssignmentStatus,
    SpeakingActivity,
    SpeakingFeedback,
    SpeakingSubmission,
    UserRole,
)
from tests.speaking_fixtures import (
    DUE,
    NOW,
    OPENS,
    enroll,
    ordinary_assignment,
    setup_group,
    speaking_activity,
    speaking_feedback,
    speaking_submission,
    upload_row,
    user,
)


# ===========================================================================
# SpeakingActivity
# ===========================================================================


def test_activity_defaults_are_server_generated(app):
    with app.app_context():
        _teacher, group = setup_group()
        assignment = ordinary_assignment(group, title="Bare", published=False)
        activity = SpeakingActivity(
            assignment_id=assignment.id, creation_nonce=secrets.token_hex(16)
        )
        db.session.add(activity)
        db.session.commit()

        assert activity.public_id and len(activity.public_id) == 36
        assert isinstance(activity.created_at, datetime)
        assert isinstance(activity.updated_at, datetime)
        # Whole seconds: DATETIME on MySQL carries fractional precision 0.
        assert activity.created_at.microsecond == 0
        assert activity.updated_at.microsecond == 0
        assert activity.created_at.tzinfo is None


def test_activity_public_id_is_unique(app):
    with app.app_context():
        _teacher, group = setup_group()
        _a, first = speaking_activity(group, title="One")
        second_assignment = ordinary_assignment(group, title="Two", published=False)
        db.session.add(
            SpeakingActivity(
                assignment_id=second_assignment.id,
                public_id=first.public_id,
                creation_nonce=secrets.token_hex(16),
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_one_assignment_can_carry_only_one_extension(app):
    with app.app_context():
        _teacher, group = setup_group()
        assignment, _activity = speaking_activity(group)
        db.session.add(
            SpeakingActivity(
                assignment_id=assignment.id, creation_nonce=secrets.token_hex(16)
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_activity_creation_nonce_is_unique(app):
    with app.app_context():
        _teacher, group = setup_group()
        _a, first = speaking_activity(group, title="One")
        other = ordinary_assignment(group, title="Two", published=False)
        db.session.add(
            SpeakingActivity(assignment_id=other.id, creation_nonce=first.creation_nonce)
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


@pytest.mark.parametrize("column", ["assignment_id", "creation_nonce"])
def test_activity_required_columns_are_not_null(app, column):
    with app.app_context():
        _teacher, group = setup_group()
        assignment = ordinary_assignment(group, title="Bare", published=False)
        values = {
            "assignment_id": assignment.id,
            "creation_nonce": secrets.token_hex(16),
        }
        values[column] = None
        db.session.add(SpeakingActivity(**values))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_activity_assignment_id_must_reference_a_real_assignment(app):
    with app.app_context():
        setup_group()
        db.session.add(
            SpeakingActivity(assignment_id=987654, creation_nonce=secrets.token_hex(16))
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_the_extension_is_what_makes_an_assignment_speaking(app):
    with app.app_context():
        _teacher, group = setup_group()
        speaking, _activity = speaking_activity(group, title="Spoken")
        ordinary = ordinary_assignment(group, title="Written")

        assert speaking.speaking_activity is not None
        assert ordinary.speaking_activity is None
        # There is no `kind` / discriminator column anywhere on assignments.
        columns = {c.name for c in Assignment.__table__.columns}
        for forbidden in ("kind", "type", "activity_type", "is_speaking"):
            assert forbidden not in columns


def test_activity_navigates_to_its_group_through_the_assignment(app):
    with app.app_context():
        _teacher, group = setup_group()
        assignment, activity = speaking_activity(group)
        assert activity.assignment.id == assignment.id
        assert activity.assignment.group_id == group.id
        # Group / Course / Level / Term are NOT duplicated on the extension.
        columns = {c.name for c in SpeakingActivity.__table__.columns}
        for forbidden in (
            "group_id", "course_id", "level_id", "academic_term_id", "teacher_id",
            "created_by", "status", "published_at", "version", "title", "instructions",
            "opens_at", "due_at", "audio_file_id",
        ):
            assert forbidden not in columns


def test_activity_relationship_declares_no_cascade(app):
    with app.app_context():
        rel = inspect(Assignment).relationships["speaking_activity"]
        assert rel.cascade.delete is False
        assert rel.cascade.delete_orphan is False
        inverse = inspect(SpeakingActivity).relationships["assignment"]
        assert inverse.cascade.delete is False
        assert inverse.cascade.delete_orphan is False


def test_activity_foreign_key_declares_no_ondelete(app):
    with app.app_context():
        for fk in SpeakingActivity.__table__.foreign_keys:
            assert fk.ondelete is None
            assert fk.onupdate is None


# ===========================================================================
# SpeakingSubmission
# ===========================================================================


def test_submission_defaults_are_server_generated(app):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        student = enroll(group)
        upload = upload_row(student.id)
        row = SpeakingSubmission(
            speaking_activity_id=activity.id,
            student_id=student.id,
            audio_file_id=upload.id,
            creation_nonce=secrets.token_hex(16),
        )
        db.session.add(row)
        db.session.commit()

        assert row.public_id and len(row.public_id) == 36
        assert isinstance(row.submitted_at, datetime)
        assert row.submitted_at.microsecond == 0
        assert row.submitted_at.tzinfo is None


def test_exactly_one_submission_per_activity_and_student(app):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        student = enroll(group)
        speaking_submission(activity, student)

        second_upload = upload_row(student.id)
        db.session.add(
            SpeakingSubmission(
                speaking_activity_id=activity.id,
                student_id=student.id,
                audio_file_id=second_upload.id,
                creation_nonce=secrets.token_hex(16),
                submitted_at=NOW,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_two_students_may_each_submit_once_for_one_activity(app):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        first = enroll(group, "a@example.com")
        second = enroll(group, "b@example.com")
        speaking_submission(activity, first)
        speaking_submission(activity, second)
        assert (
            SpeakingSubmission.query.filter_by(speaking_activity_id=activity.id).count()
            == 2
        )


def test_one_recording_backs_exactly_one_submission(app):
    with app.app_context():
        _teacher, group = setup_group()
        _a1, first_activity = speaking_activity(group, title="One")
        _a2, second_activity = speaking_activity(group, title="Two")
        student = enroll(group)
        upload = upload_row(student.id)
        speaking_submission(first_activity, student, upload=upload)

        db.session.add(
            SpeakingSubmission(
                speaking_activity_id=second_activity.id,
                student_id=student.id,
                audio_file_id=upload.id,
                creation_nonce=secrets.token_hex(16),
                submitted_at=NOW,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_submission_creation_nonce_is_unique(app):
    with app.app_context():
        _teacher, group = setup_group()
        _a1, first_activity = speaking_activity(group, title="One")
        _a2, second_activity = speaking_activity(group, title="Two")
        student = enroll(group)
        first = speaking_submission(first_activity, student)
        upload = upload_row(student.id)
        db.session.add(
            SpeakingSubmission(
                speaking_activity_id=second_activity.id,
                student_id=student.id,
                audio_file_id=upload.id,
                creation_nonce=first.creation_nonce,
                submitted_at=NOW,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


@pytest.mark.parametrize(
    "column", ["speaking_activity_id", "student_id", "audio_file_id", "creation_nonce"]
)
def test_submission_required_columns_are_not_null(app, column):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        student = enroll(group)
        upload = upload_row(student.id)
        values = {
            "speaking_activity_id": activity.id,
            "student_id": student.id,
            "audio_file_id": upload.id,
            "creation_nonce": secrets.token_hex(16),
            "submitted_at": NOW,
        }
        values[column] = None
        db.session.add(SpeakingSubmission(**values))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


@pytest.mark.parametrize(
    "column", ["speaking_activity_id", "student_id", "audio_file_id"]
)
def test_submission_foreign_keys_are_enforced(app, column):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        student = enroll(group)
        upload = upload_row(student.id)
        values = {
            "speaking_activity_id": activity.id,
            "student_id": student.id,
            "audio_file_id": upload.id,
            "creation_nonce": secrets.token_hex(16),
            "submitted_at": NOW,
        }
        values[column] = 987654
        db.session.add(SpeakingSubmission(**values))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_submission_carries_no_state_score_or_attempt_column(app):
    columns = {c.name for c in SpeakingSubmission.__table__.columns}
    assert columns == {
        "id", "public_id", "speaking_activity_id", "student_id", "audio_file_id",
        "creation_nonce", "submitted_at",
    }
    for forbidden in (
        "status", "state", "draft", "is_draft", "attempt", "attempt_number", "version",
        "score", "grade", "points", "passed", "reviewed_at", "updated_at", "deleted_at",
        "duration_seconds", "replaced_by_id",
    ):
        assert forbidden not in columns


def test_submission_declares_no_orm_relationship_in_either_direction(app):
    with app.app_context():
        assert list(inspect(SpeakingSubmission).relationships.keys()) == []
        # ...and no other model grew one pointing at it.
        for model in (SpeakingActivity, SpeakingFeedback):
            for rel in inspect(model).relationships.values():
                assert rel.mapper.class_ is not SpeakingSubmission


def test_submission_foreign_keys_declare_no_ondelete(app):
    with app.app_context():
        for fk in SpeakingSubmission.__table__.foreign_keys:
            assert fk.ondelete is None
            assert fk.onupdate is None


def test_submission_student_id_has_its_own_usable_index(app):
    with app.app_context():
        indexed = {
            tuple(column.name for column in index.columns)
            for index in SpeakingSubmission.__table__.indexes
        }
        assert ("student_id",) in indexed
        assert (
            "speaking_activity_id",
            "submitted_at",
            "id",
        ) in indexed


def test_a_foreign_key_into_users_does_not_prove_a_student_role(app):
    """The schema accepts any User id; the role rule is an application
    invariant, re-checked in every read and write path."""
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        administrator = user("admin@example.com", UserRole.ADMINISTRATOR.value)
        upload = upload_row(administrator.id)
        db.session.add(
            SpeakingSubmission(
                speaking_activity_id=activity.id,
                student_id=administrator.id,
                audio_file_id=upload.id,
                creation_nonce=secrets.token_hex(16),
                submitted_at=NOW,
            )
        )
        db.session.commit()  # the DATABASE allows it -- the application does not
        assert SpeakingSubmission.query.count() == 1


# ===========================================================================
# SpeakingFeedback
# ===========================================================================


def test_feedback_defaults_are_server_generated(app):
    with app.app_context():
        teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        student = enroll(group)
        submission = speaking_submission(activity, student)
        row = SpeakingFeedback(
            speaking_submission_id=submission.id,
            reviewer_id=teacher.id,
            feedback_text="Good pace.",
        )
        db.session.add(row)
        db.session.commit()

        assert row.public_id and len(row.public_id) == 36
        assert row.version == 1
        assert row.created_at.microsecond == 0
        assert row.updated_at.microsecond == 0
        assert row.created_at.tzinfo is None


def test_one_feedback_row_per_recording(app):
    with app.app_context():
        teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        student = enroll(group)
        submission = speaking_submission(activity, student)
        speaking_feedback(submission, teacher)

        db.session.add(
            SpeakingFeedback(
                speaking_submission_id=submission.id,
                reviewer_id=teacher.id,
                feedback_text="A second record.",
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


@pytest.mark.parametrize("version", [0, -1, -100])
def test_feedback_version_must_be_positive(app, version):
    with app.app_context():
        teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        student = enroll(group)
        submission = speaking_submission(activity, student)
        db.session.add(
            SpeakingFeedback(
                speaking_submission_id=submission.id,
                reviewer_id=teacher.id,
                feedback_text="Text.",
                version=version,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_feedback_version_is_not_null_in_the_schema(app):
    """``version`` carries a Python-side default of 1, so the ORM can
    never produce a NULL for it. The column is still declared NOT NULL, so
    a raw insert -- a manual row, a script, a future code path that
    bypasses the model -- is refused by the database itself."""
    from sqlalchemy import text

    with app.app_context():
        teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        student = enroll(group)
        submission = speaking_submission(activity, student)
        assert SpeakingFeedback.__table__.c.version.nullable is False
        with pytest.raises(IntegrityError):
            db.session.execute(
                text(
                    "INSERT INTO speaking_feedback (public_id, speaking_submission_id,"
                    " reviewer_id, feedback_text, version, created_at, updated_at)"
                    " VALUES ('raw-1', :submission, :reviewer, 'Text.', NULL,"
                    " '2026-05-10 09:00:00', '2026-05-10 09:00:00')"
                ),
                {"submission": submission.id, "reviewer": teacher.id},
            )
            db.session.commit()
        db.session.rollback()


def test_feedback_public_id_is_unique(app):
    with app.app_context():
        teacher, group = setup_group()
        _a1, first_activity = speaking_activity(group, title="One")
        _a2, second_activity = speaking_activity(group, title="Two")
        student = enroll(group)
        first_submission = speaking_submission(first_activity, student)
        second_submission = speaking_submission(second_activity, student)
        first = speaking_feedback(first_submission, teacher)
        db.session.add(
            SpeakingFeedback(
                speaking_submission_id=second_submission.id,
                reviewer_id=teacher.id,
                feedback_text="Text.",
                public_id=first.public_id,
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


@pytest.mark.parametrize(
    "column", ["speaking_submission_id", "reviewer_id", "feedback_text"]
)
def test_feedback_required_columns_are_not_null(app, column):
    with app.app_context():
        teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        student = enroll(group)
        submission = speaking_submission(activity, student)
        values = {
            "speaking_submission_id": submission.id,
            "reviewer_id": teacher.id,
            "feedback_text": "Text.",
            "version": 1,
            "created_at": NOW,
            "updated_at": NOW,
        }
        values[column] = None
        db.session.add(SpeakingFeedback(**values))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


@pytest.mark.parametrize("column", ["speaking_submission_id", "reviewer_id"])
def test_feedback_foreign_keys_are_enforced(app, column):
    with app.app_context():
        teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        student = enroll(group)
        submission = speaking_submission(activity, student)
        values = {
            "speaking_submission_id": submission.id,
            "reviewer_id": teacher.id,
            "feedback_text": "Text.",
            "version": 1,
            "created_at": NOW,
            "updated_at": NOW,
        }
        values[column] = 987654
        db.session.add(SpeakingFeedback(**values))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_feedback_carries_no_grade_or_history_column(app):
    columns = {c.name for c in SpeakingFeedback.__table__.columns}
    assert columns == {
        "id", "public_id", "speaking_submission_id", "reviewer_id", "feedback_text",
        "version", "created_at", "updated_at",
    }
    for forbidden in (
        "score", "grade", "max_points", "points", "passed", "pass_fail", "rubric",
        "status", "reviewed", "reviewed_at", "published", "published_at", "attempt",
        "previous_text", "history",
    ):
        assert forbidden not in columns


def test_feedback_declares_no_orm_relationship(app):
    with app.app_context():
        assert list(inspect(SpeakingFeedback).relationships.keys()) == []


def test_feedback_foreign_keys_declare_no_ondelete(app):
    with app.app_context():
        for fk in SpeakingFeedback.__table__.foreign_keys:
            assert fk.ondelete is None
            assert fk.onupdate is None


def test_feedback_reviewer_id_has_its_own_index(app):
    with app.app_context():
        indexed = {
            tuple(column.name for column in index.columns)
            for index in SpeakingFeedback.__table__.indexes
        }
        assert ("reviewer_id",) in indexed


def test_feedback_length_boundary_matches_the_form(app):
    from app.blueprints.teacher.speaking_forms import SpeakingFeedbackForm

    assert SpeakingFeedbackForm.FEEDBACK_MAX == SPEAKING_FEEDBACK_MAX_LENGTH
    assert SPEAKING_FEEDBACK_MAX_LENGTH == 10000


def test_a_long_feedback_body_is_storable_at_the_declared_boundary(app):
    with app.app_context():
        teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        student = enroll(group)
        submission = speaking_submission(activity, student)
        text = "x" * SPEAKING_FEEDBACK_MAX_LENGTH
        speaking_feedback(submission, teacher, text=text)
        stored = SpeakingFeedback.query.one()
        assert len(stored.feedback_text) == SPEAKING_FEEDBACK_MAX_LENGTH


# ===========================================================================
# The backing Assignment stays exactly what M01 built
# ===========================================================================


def test_a_speaking_activity_reuses_the_assignment_lifecycle(app):
    with app.app_context():
        _teacher, group = setup_group()
        assignment, _activity = speaking_activity(group, published=False)
        assert assignment.status == AssignmentStatus.DRAFT.value
        assert assignment.published_at is None
        assert assignment.opens_at == OPENS
        assert assignment.due_at == DUE


def test_the_assignment_window_check_still_applies_to_a_speaking_activity(app):
    with app.app_context():
        _teacher, group = setup_group()
        db.session.add(
            Assignment(
                group_id=group.id,
                title="Reversed window",
                instructions="Speak.",
                opens_at=DUE,
                due_at=OPENS,
                status=AssignmentStatus.DRAFT.value,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_one_group_cannot_hold_a_speaking_activity_and_an_assignment_with_one_title(app):
    with app.app_context():
        _teacher, group = setup_group()
        speaking_activity(group, title="Shared title")
        db.session.add(
            Assignment(
                group_id=group.id,
                title="Shared title",
                instructions="Write.",
                opens_at=OPENS,
                due_at=DUE,
                status=AssignmentStatus.DRAFT.value,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
