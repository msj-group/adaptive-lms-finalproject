"""ListeningActivity model contract (Phase 4 / M05).

Column shape, defaults, the closed ``TranscriptVisibility`` set, the three
uniqueness rules, the two foreign keys and the absence of any cascade --
proved against the model and, where the rule is a database one, against a
real INSERT on the SQLite test backend.

The ``audio`` category of the referenced upload is deliberately **not** a
database constraint: it is a cross-table condition a CHECK cannot express.
It is proved here at the service boundary that every read and write path
actually calls, and again through the routes in
``tests/test_teacher_listening.py``.
"""

from datetime import date, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    TRANSCRIPT_MAX_LENGTH,
    VOCABULARY_NOTES_MAX_LENGTH,
    AcademicTerm,
    Course,
    FileAccessLog,
    Group,
    Level,
    ListeningActivity,
    Quiz,
    QuizStatus,
    TranscriptVisibility,
    UploadedFile,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password
from app.services.listening_queries import (
    audio_upload_for_activity,
    listening_activity_for_quiz,
    student_transcript,
    student_vocabulary,
)
from app.services.quiz_queries import quiz_is_listening

MOMENT = datetime(2026, 5, 1, 8, 0, 0)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _group(label="A"):
    term = AcademicTerm(
        name=f"Term {label}", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        status="active",
    )
    level = Level(name=f"Level {label}", display_order=0, status="active")
    db.session.add_all([term, level])
    db.session.commit()
    course = Course(title=f"Course {label}", level_id=level.id, display_order=0, status="active")
    db.session.add(course)
    db.session.commit()
    group = Group(
        academic_term_id=term.id, course_id=course.id, name=f"Group {label}",
        capacity=20, status="active",
    )
    db.session.add(group)
    db.session.commit()
    return group


def _teacher(email="t@example.com"):
    user = User(
        email=email, password_hash=hash_password("Sup3rSecret!123"), full_name="T",
        role=UserRole.TEACHER.value, status=UserStatus.ACTIVE.value,
    )
    db.session.add(user)
    db.session.commit()
    return user


def _upload(uploader, category="audio", extension="mp3", key=None):
    import secrets

    row = UploadedFile(
        storage_key=key or secrets.token_hex(16),
        original_filename=f"clip.{extension}",
        extension=extension,
        category=category,
        content_type="audio/mpeg" if category == "audio" else "application/pdf",
        byte_size=1024,
        sha256="0" * 64,
        uploaded_by_id=uploader.id,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _quiz(group, title="Listening 1"):
    quiz = Quiz(
        group_id=group.id, title=title, instructions="Listen.", version=1,
        status=QuizStatus.DRAFT.value, created_at=MOMENT, updated_at=MOMENT,
    )
    db.session.add(quiz)
    db.session.commit()
    return quiz


def _activity(quiz, upload, nonce=None, **kwargs):
    import secrets

    row = ListeningActivity(
        quiz_id=quiz.id,
        audio_file_id=upload.id,
        creation_nonce=nonce or secrets.token_hex(16),
        created_at=MOMENT,
        updated_at=MOMENT,
        **kwargs,
    )
    db.session.add(row)
    db.session.commit()
    return row


# ---------------------------------------------------------------------------
# Shape and defaults
# ---------------------------------------------------------------------------


def test_defaults_are_server_owned_and_unambiguous(app):
    with app.app_context():
        group = _group()
        teacher = _teacher()
        activity = _activity(_quiz(group), _upload(teacher))

        assert activity.public_id and len(activity.public_id) == 36
        # "Nothing authored" has exactly ONE spelling in both text columns:
        # the empty string. A NULL would mean the same thing twice.
        assert activity.transcript == ""
        assert activity.vocabulary_notes == ""
        # The default policy is the most restrictive one, never the most
        # permissive: a Teacher who never touches the setting has not
        # released anything.
        assert activity.transcript_visibility == TranscriptVisibility.HIDDEN.value
        assert activity.created_at == activity.updated_at == MOMENT


def test_no_onupdate_hook_moves_updated_at_behind_the_write_path(app):
    """``updated_at`` is assigned explicitly by the write path after its
    locks. An implicit ``onupdate`` would bypass that whole-second
    truncation and fire on writes M05 defines as no-ops."""
    with app.app_context():
        assert ListeningActivity.__table__.c.updated_at.onupdate is None
        assert ListeningActivity.__table__.c.created_at.onupdate is None


def test_there_is_no_second_version_counter(app):
    """``Quiz.version`` represents the complete authored activity. A second
    counter here could disagree with it, and the signed tokens would have
    to decide which one to believe."""
    with app.app_context():
        assert "version" not in ListeningActivity.__table__.c


def test_input_bounds_are_declared_beside_the_columns(app):
    with app.app_context():
        from app.blueprints.teacher.listening_forms import ListeningContentForm

        assert ListeningContentForm.TRANSCRIPT_MAX == TRANSCRIPT_MAX_LENGTH
        assert ListeningContentForm.VOCABULARY_MAX == VOCABULARY_NOTES_MAX_LENGTH


# ---------------------------------------------------------------------------
# The closed transcript-visibility set
# ---------------------------------------------------------------------------


def test_transcript_visibility_is_a_closed_three_member_set(app):
    with app.app_context():
        assert [policy.value for policy in TranscriptVisibility] == [
            "hidden", "after_submission", "always",
        ]


@pytest.mark.parametrize(
    "value", ["hidden", "after_submission", "always"]
)
def test_every_approved_policy_is_accepted(app, value):
    with app.app_context():
        group = _group()
        teacher = _teacher()
        activity = _activity(
            _quiz(group), _upload(teacher), transcript_visibility=value
        )
        assert activity.transcript_visibility == value


@pytest.mark.parametrize(
    "value",
    ["", "HIDDEN", "after_close", "on_request", "public", "visible", "1", None],
)
def test_unapproved_policies_are_refused_by_the_model_validator(app, value):
    with app.app_context():
        with pytest.raises(ValueError):
            ListeningActivity(transcript_visibility=value)


def test_the_check_constraint_is_rendered_from_the_enum(app):
    """The application guard and the schema are generated from one source,
    so they cannot drift apart."""
    with app.app_context():
        check = next(
            c for c in ListeningActivity.__table__.constraints
            if getattr(c, "name", None)
            == "ck_listening_activities_transcript_visibility_valid"
        )
        sql = str(check.sqltext)
        for policy in TranscriptVisibility:
            assert f"'{policy.value}'" in sql


# ---------------------------------------------------------------------------
# Uniqueness -- the final defense behind every application rule
# ---------------------------------------------------------------------------


def test_one_extension_per_quiz(app):
    with app.app_context():
        group = _group()
        teacher = _teacher()
        quiz = _quiz(group)
        _activity(quiz, _upload(teacher))
        with pytest.raises(IntegrityError):
            _activity(quiz, _upload(teacher, key="second"))
        db.session.rollback()


def test_one_activity_per_recording(app):
    with app.app_context():
        group = _group()
        teacher = _teacher()
        upload = _upload(teacher)
        _activity(_quiz(group, "One"), upload)
        with pytest.raises(IntegrityError):
            _activity(_quiz(group, "Two"), upload)
        db.session.rollback()


def test_public_id_is_unique(app):
    with app.app_context():
        group = _group()
        teacher = _teacher()
        first = _activity(_quiz(group, "One"), _upload(teacher))
        with pytest.raises(IntegrityError):
            _activity(
                _quiz(group, "Two"), _upload(teacher, key="k2"),
                public_id=first.public_id,
            )
        db.session.rollback()


def test_creation_nonce_is_unique(app):
    """The final defense behind the duplicate-request protection: a
    concurrent replay loses here rather than producing a second Quiz,
    extension row, upload, access log and physical file."""
    with app.app_context():
        group = _group()
        teacher = _teacher()
        _activity(_quiz(group, "One"), _upload(teacher), nonce="shared-nonce")
        with pytest.raises(IntegrityError):
            _activity(
                _quiz(group, "Two"), _upload(teacher, key="k2"), nonce="shared-nonce"
            )
        db.session.rollback()


@pytest.mark.parametrize("column", ["quiz_id", "audio_file_id", "creation_nonce"])
def test_required_columns_are_not_null(app, column):
    with app.app_context():
        assert ListeningActivity.__table__.c[column].nullable is False


@pytest.mark.parametrize("column", ["quiz_id", "audio_file_id"])
def test_both_foreign_keys_are_unique_and_carry_no_cascade(app, column):
    """No Quiz, Group or file lifecycle change may remove an activity, and
    nothing is ever hard-deleted."""
    with app.app_context():
        col = ListeningActivity.__table__.c[column]
        assert col.unique is True
        foreign_key = next(iter(col.foreign_keys))
        assert foreign_key.ondelete is None
        assert foreign_key.onupdate is None


def test_foreign_keys_point_at_the_expected_tables(app):
    with app.app_context():
        targets = {
            column.name: next(iter(column.foreign_keys)).target_fullname
            for column in ListeningActivity.__table__.c
            if column.foreign_keys
        }
        assert targets == {
            "quiz_id": "quizzes.id",
            "audio_file_id": "uploaded_files.id",
        }


def test_the_relationships_carry_no_cascade_and_are_one_to_one(app):
    with app.app_context():
        for relationship in (
            Quiz.__mapper__.relationships["listening_activity"],
            UploadedFile.__mapper__.relationships["listening_activity"],
        ):
            assert relationship.uselist is False
            assert "delete" not in relationship.cascade
            assert "delete-orphan" not in relationship.cascade


# ---------------------------------------------------------------------------
# The audio-category invariant -- an application rule, stated as one
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "category,extension",
    [("document", "pdf"), ("image", "png"), ("video", "mp4")],
)
def test_a_non_audio_upload_is_refused_by_the_service_every_path_uses(
    app, category, extension
):
    """A foreign key proves the ``uploaded_files`` row exists, never that
    it is a recording. The condition is re-checked on every audio request
    and every publication check, not only when the row was created."""
    with app.app_context():
        group = _group()
        teacher = _teacher()
        activity = _activity(
            _quiz(group), _upload(teacher, category=category, extension=extension)
        )
        assert audio_upload_for_activity(activity) is None


def test_an_audio_upload_is_accepted(app):
    with app.app_context():
        group = _group()
        teacher = _teacher()
        upload = _upload(teacher)
        activity = _activity(_quiz(group), upload)
        assert audio_upload_for_activity(activity).id == upload.id


def test_a_missing_upload_row_fails_closed(app):
    with app.app_context():
        assert audio_upload_for_activity(None) is None


# ---------------------------------------------------------------------------
# Classification -- the presence of the row IS the discriminator
# ---------------------------------------------------------------------------


def test_a_quiz_without_the_extension_is_an_ordinary_quiz(app):
    with app.app_context():
        group = _group()
        quiz = _quiz(group)
        assert quiz_is_listening(quiz.id) is False
        assert listening_activity_for_quiz(quiz.id) is None
        assert quiz.listening_activity is None


def test_a_quiz_with_the_extension_is_a_listening_activity(app):
    with app.app_context():
        group = _group()
        teacher = _teacher()
        quiz = _quiz(group)
        activity = _activity(quiz, _upload(teacher))
        assert quiz_is_listening(quiz.id) is True
        assert listening_activity_for_quiz(quiz.id).id == activity.id
        db.session.refresh(quiz)
        assert quiz.listening_activity.id == activity.id


def test_there_is_no_discriminator_column_on_quizzes(app):
    """A nullable flag and the extension row could disagree, and then two
    places would answer the same question differently."""
    with app.app_context():
        for forbidden in ("kind", "is_listening", "activity_type", "listening_id"):
            assert forbidden not in Quiz.__table__.c


# ---------------------------------------------------------------------------
# Transcript policy -- the one function every Student page asks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "policy,on_result,expected",
    [
        ("hidden", False, None),
        ("hidden", True, None),
        ("after_submission", False, None),
        ("after_submission", True, "Flight 42 boarding."),
        ("always", False, "Flight 42 boarding."),
        ("always", True, "Flight 42 boarding."),
    ],
)
def test_the_transcript_policy_decides_in_exactly_one_place(
    app, policy, on_result, expected
):
    with app.app_context():
        group = _group()
        teacher = _teacher()
        activity = _activity(
            _quiz(group), _upload(teacher),
            transcript="Flight 42 boarding.", transcript_visibility=policy,
        )
        assert student_transcript(activity, on_finalized_result=on_result) == expected


def test_an_empty_transcript_is_legitimate_under_every_policy(app):
    """"Released but not written" and "withheld" are different facts, and
    only the second is ``None``."""
    with app.app_context():
        group = _group()
        teacher = _teacher()
        for index, policy in enumerate(TranscriptVisibility):
            activity = _activity(
                _quiz(group, f"Q{index}"), _upload(teacher, key=f"k{index}"),
                transcript="", transcript_visibility=policy.value,
            )
            released = student_transcript(activity, on_finalized_result=True)
            if policy is TranscriptVisibility.HIDDEN:
                assert released is None
            else:
                assert released == ""


def test_an_unknown_stored_policy_fails_closed(app):
    """The CHECK and the validator both forbid it; if one were ever
    bypassed, the reader must withhold rather than release."""

    class _Corrupted:
        transcript = "secret"
        transcript_visibility = "public"

    with app.app_context():
        assert student_transcript(_Corrupted(), on_finalized_result=True) is None


def test_vocabulary_has_no_policy_of_its_own(app):
    """It is teaching support a Teacher wrote *for* the Student to use."""
    with app.app_context():
        group = _group()
        teacher = _teacher()
        activity = _activity(
            _quiz(group), _upload(teacher),
            vocabulary_notes="gate = where you board",
            transcript_visibility=TranscriptVisibility.HIDDEN.value,
        )
        assert student_vocabulary(activity) == "gate = where you board"
        assert student_vocabulary(None) == ""


# ---------------------------------------------------------------------------
# Nothing here creates a second completion or score record
# ---------------------------------------------------------------------------


def test_no_score_or_completion_column_is_duplicated_onto_the_extension(app):
    """A Listening activity is completed for a Student when a QuizAttempt
    becomes submitted or expired and receives its persisted totals. A
    second copy could disagree with the first."""
    with app.app_context():
        for forbidden in (
            "correct_count", "total_questions", "score", "percentage",
            "completed_at", "status", "attempt_limit", "opens_at", "closes_at",
        ):
            assert forbidden not in ListeningActivity.__table__.c


def test_the_upload_access_log_is_reused_rather_than_duplicated(app):
    """M05 adds no second audit table: audio access is recorded in
    ``file_access_logs`` exactly like every other uploaded file."""
    with app.app_context():
        assert FileAccessLog.__tablename__ == "file_access_logs"
        assert "listening_activity_id" not in FileAccessLog.__table__.c
