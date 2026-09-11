"""AttendanceSession / AttendanceRecord model contracts (Phase 4 / M07).

Defaults, validators, the database CHECK / UNIQUE / foreign-key
invariants, the declared indexes, and the deliberate **absence** of
cascades and of any relationship that could delete attendance history.

SQLite (the test backend) enforces CHECK and UNIQUE constraints and --
with ``PRAGMA foreign_keys=ON``, which ``app/extensions.py`` sets on every
connection -- foreign keys too, so these are executed rather than merely
asserted about the metadata. They still prove nothing about MySQL/InnoDB
locking, collation or index plans.
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    ATTENDANCE_NOTE_MAX_LENGTH,
    DEFAULT_ATTENDANCE_STATUS,
    AttendanceRecord,
    AttendanceSession,
    AttendanceStatus,
)
from tests import attendance_fixtures as fx


# ===========================================================================
# The status enum
# ===========================================================================


def test_attendance_status_holds_exactly_the_four_approved_members():
    assert [status.value for status in AttendanceStatus] == [
        "present",
        "absent",
        "late",
        "excused",
    ]


def test_the_default_status_is_absent_not_present():
    """A session a teacher has opened but not worked through must never
    claim somebody was there."""
    assert DEFAULT_ATTENDANCE_STATUS == AttendanceStatus.ABSENT.value


def test_the_note_boundary_is_one_thousand_characters():
    assert ATTENDANCE_NOTE_MAX_LENGTH == 1000


# ===========================================================================
# AttendanceSession
# ===========================================================================


def test_session_defaults_are_draft_version_one_and_a_public_id(app):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        row = AttendanceSession(
            group_id=group.id,
            schedule_id=schedule.id,
            session_date=fx.SESSION_DATE,
            start_time=schedule.start_time,
            end_time=schedule.end_time,
            location=schedule.location,
        )
        db.session.add(row)
        db.session.commit()

        assert row.public_id and len(row.public_id) == 36
        assert row.version == 1
        assert row.finalized_at is None
        assert row.is_finalized() is False
        # Whole-second UTC: the column is DATETIME(0) on MySQL, which
        # rounds rather than truncates an excess fraction.
        assert row.created_at.microsecond == 0
        assert row.updated_at.microsecond == 0


def test_a_session_is_finalized_exactly_when_finalized_at_is_set(app):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        row = fx.attendance_session(group, schedule)
        assert row.is_finalized() is False
        row.finalized_at = fx.NOW
        db.session.commit()
        assert row.is_finalized() is True


@pytest.mark.parametrize("bad", [0, -1, True])
def test_session_version_validator_refuses_a_non_positive_integer(app, bad):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        with pytest.raises(ValueError):
            AttendanceSession(
                group_id=group.id,
                schedule_id=schedule.id,
                session_date=fx.SESSION_DATE,
                start_time=schedule.start_time,
                end_time=schedule.end_time,
                version=bad,
            )


def test_session_version_check_constraint_refuses_zero_in_the_database(app):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        row = fx.attendance_session(group, schedule)
        with pytest.raises(IntegrityError):
            db.session.execute(
                sa.text(
                    "UPDATE attendance_sessions SET version = 0 WHERE id = :id"
                ),
                {"id": row.id},
            )
            db.session.commit()
        db.session.rollback()


def test_session_time_order_check_refuses_an_end_before_its_start(app):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        db.session.add(
            AttendanceSession(
                group_id=group.id,
                schedule_id=schedule.id,
                session_date=fx.SESSION_DATE,
                start_time=fx.END_TIME,
                end_time=fx.START_TIME,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_one_session_per_scheduled_occurrence(app):
    """``uq_attendance_sessions_schedule_date`` is the final defense behind
    the locked duplicate check."""
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        fx.attendance_session(group, schedule)
        db.session.add(
            AttendanceSession(
                group_id=group.id,
                schedule_id=schedule.id,
                session_date=fx.SESSION_DATE,
                start_time=schedule.start_time,
                end_time=schedule.end_time,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_the_same_schedule_may_have_a_session_on_a_different_date(app):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        fx.attendance_session(group, schedule, session_date=fx.SESSION_DATE)
        fx.attendance_session(group, schedule, session_date=fx.PREVIOUS_WEEK)
        assert AttendanceSession.query.count() == 2


def test_session_public_id_is_unique(app):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        first = fx.attendance_session(group, schedule)
        second = AttendanceSession(
            group_id=group.id,
            schedule_id=schedule.id,
            session_date=fx.PREVIOUS_WEEK,
            start_time=schedule.start_time,
            end_time=schedule.end_time,
            public_id=first.public_id,
        )
        db.session.add(second)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_session_snapshot_columns_survive_a_later_schedule_change(app):
    """The snapshot is deliberate history: editing or archiving the
    Schedule afterwards must not rewrite what a recorded meeting was."""
    from datetime import time

    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        session = fx.attendance_session(group, schedule)

        schedule.start_time = time(9, 0)
        schedule.end_time = time(11, 0)
        schedule.location = "Somewhere else"
        schedule.status = "archived"
        db.session.commit()
        db.session.expire_all()

        stored = db.session.get(AttendanceSession, session.id)
        assert stored.start_time == fx.START_TIME
        assert stored.end_time == fx.END_TIME
        assert stored.location == fx.LOCATION
        assert stored.session_date == fx.SESSION_DATE


# ===========================================================================
# AttendanceRecord
# ===========================================================================


def test_record_defaults_are_absent_version_one_and_no_note(app):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group)
        session = fx.attendance_session(group, schedule)
        row = AttendanceRecord(
            attendance_session_id=session.id, student_id=student.id
        )
        db.session.add(row)
        db.session.commit()

        assert row.public_id and len(row.public_id) == 36
        assert row.status == AttendanceStatus.ABSENT.value
        assert row.note is None
        assert row.version == 1
        assert row.created_at.microsecond == 0
        assert row.updated_at.microsecond == 0


@pytest.mark.parametrize("status", ["present", "absent", "late", "excused"])
def test_every_approved_status_is_accepted(app, status):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group)
        session = fx.attendance_session(group, schedule)
        row = fx.attendance_record(session, student, status=status)
        assert row.status == status


@pytest.mark.parametrize("bad", ["", "PRESENT", "unknown", "sick", "left_early", None])
def test_record_status_validator_refuses_anything_else(app, bad):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group)
        session = fx.attendance_session(group, schedule)
        with pytest.raises(ValueError):
            AttendanceRecord(
                attendance_session_id=session.id,
                student_id=student.id,
                status=bad,
            )


def test_record_status_check_constraint_refuses_an_unknown_value_in_the_database(app):
    """The model validator is not the only defense: the CHECK refuses a
    value written straight past the ORM."""
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group)
        session = fx.attendance_session(group, schedule)
        record = fx.attendance_record(session, student)
        with pytest.raises(IntegrityError):
            db.session.execute(
                sa.text("UPDATE attendance_records SET status = 'sick' WHERE id = :id"),
                {"id": record.id},
            )
            db.session.commit()
        db.session.rollback()


@pytest.mark.parametrize("bad", [0, -3, True])
def test_record_version_validator_refuses_a_non_positive_integer(app, bad):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group)
        session = fx.attendance_session(group, schedule)
        with pytest.raises(ValueError):
            AttendanceRecord(
                attendance_session_id=session.id,
                student_id=student.id,
                version=bad,
            )


def test_record_version_check_constraint_refuses_zero_in_the_database(app):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group)
        session = fx.attendance_session(group, schedule)
        record = fx.attendance_record(session, student)
        with pytest.raises(IntegrityError):
            db.session.execute(
                sa.text("UPDATE attendance_records SET version = 0 WHERE id = :id"),
                {"id": record.id},
            )
            db.session.commit()
        db.session.rollback()


def test_one_record_per_session_and_student(app):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group)
        session = fx.attendance_session(group, schedule)
        fx.attendance_record(session, student)
        db.session.add(
            AttendanceRecord(
                attendance_session_id=session.id, student_id=student.id
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_the_same_student_may_appear_in_two_different_sessions(app):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group)
        first = fx.attendance_session(group, schedule, session_date=fx.SESSION_DATE)
        second = fx.attendance_session(group, schedule, session_date=fx.PREVIOUS_WEEK)
        fx.attendance_record(first, student)
        fx.attendance_record(second, student)
        assert AttendanceRecord.query.count() == 2


def test_record_public_id_is_unique(app):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        alice = fx.enroll(group, "a@example.com")
        bob = fx.enroll(group, "b@example.com")
        session = fx.attendance_session(group, schedule)
        first = fx.attendance_record(session, alice)
        db.session.add(
            AttendanceRecord(
                attendance_session_id=session.id,
                student_id=bob.id,
                public_id=first.public_id,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_a_note_of_a_thousand_characters_is_stored_whole(app):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group)
        session = fx.attendance_session(group, schedule)
        text = "n" * ATTENDANCE_NOTE_MAX_LENGTH
        record = fx.attendance_record(session, student, note=text)
        db.session.expire_all()
        assert db.session.get(AttendanceRecord, record.id).note == text


# ===========================================================================
# Foreign keys, cascades and relationships
# ===========================================================================


def test_foreign_keys_are_enforced_and_reference_the_expected_parents(app):
    with app.app_context():
        inspector = sa.inspect(db.engine)
        assert {
            fk["constrained_columns"][0]: fk["referred_table"]
            for fk in inspector.get_foreign_keys("attendance_sessions")
        } == {"group_id": "groups", "schedule_id": "schedules"}
        assert {
            fk["constrained_columns"][0]: fk["referred_table"]
            for fk in inspector.get_foreign_keys("attendance_records")
        } == {
            "attendance_session_id": "attendance_sessions",
            "student_id": "users",
        }


def test_no_foreign_key_declares_a_cascade_or_an_update_action(app):
    """History-preserving by construction: no Group, Schedule or account
    lifecycle change may remove attendance."""
    with app.app_context():
        for table in (AttendanceSession.__table__, AttendanceRecord.__table__):
            for constraint in table.foreign_key_constraints:
                assert constraint.ondelete is None, table.name
                assert constraint.onupdate is None, table.name


def test_a_session_cannot_be_orphaned_by_deleting_its_schedule(app):
    """There is no delete route anywhere; this proves the database would
    refuse one even if somebody tried at the SQL level."""
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        fx.attendance_session(group, schedule)
        with pytest.raises(IntegrityError):
            db.session.execute(
                sa.text("DELETE FROM schedules WHERE id = :id"), {"id": schedule.id}
            )
            db.session.commit()
        db.session.rollback()


def test_a_record_cannot_be_orphaned_by_deleting_its_session(app):
    with app.app_context():
        _, group = fx.setup_group()
        schedule = fx.schedule_for(group)
        student = fx.enroll(group)
        session = fx.attendance_session(group, schedule)
        fx.attendance_record(session, student)
        with pytest.raises(IntegrityError):
            db.session.execute(
                sa.text("DELETE FROM attendance_sessions WHERE id = :id"),
                {"id": session.id},
            )
            db.session.commit()
        db.session.rollback()


def test_neither_model_declares_an_orm_relationship_in_either_direction(app):
    """Reads go through explicit joins returning plain dicts, so rendering
    attendance can never lazy-load, and no ``cascade`` /
    ``delete-orphan`` configuration exists anywhere that could remove
    history."""
    from app.models import Group, Schedule, User

    assert not list(sa.inspect(AttendanceSession).relationships)
    assert not list(sa.inspect(AttendanceRecord).relationships)
    for model in (Group, Schedule, User):
        related = {r.mapper.class_ for r in sa.inspect(model).relationships}
        assert AttendanceSession not in related
        assert AttendanceRecord not in related


# ===========================================================================
# Indexes
# ===========================================================================


def test_the_declared_indexes_exist_with_their_documented_columns(app):
    with app.app_context():
        inspector = sa.inspect(db.engine)
        session_indexes = {
            index["name"]: list(index["column_names"])
            for index in inspector.get_indexes("attendance_sessions")
        }
        assert session_indexes["ix_attendance_sessions_group_date_id"] == [
            "group_id",
            "session_date",
            "id",
        ]
        assert session_indexes["ix_attendance_sessions_date_id"] == [
            "session_date",
            "id",
        ]
        record_indexes = {
            index["name"]: list(index["column_names"])
            for index in inspector.get_indexes("attendance_records")
        }
        assert record_indexes["ix_attendance_records_student_id"] == ["student_id"]


def test_the_unique_constraints_carry_their_documented_names_and_columns(app):
    with app.app_context():
        inspector = sa.inspect(db.engine)
        session_uniques = {
            unique["name"]: list(unique["column_names"])
            for unique in inspector.get_unique_constraints("attendance_sessions")
            if unique["name"]
        }
        assert session_uniques["uq_attendance_sessions_schedule_date"] == [
            "schedule_id",
            "session_date",
        ]
        record_uniques = {
            unique["name"]: list(unique["column_names"])
            for unique in inspector.get_unique_constraints("attendance_records")
            if unique["name"]
        }
        assert record_uniques["uq_attendance_records_session_student"] == [
            "attendance_session_id",
            "student_id",
        ]


def test_no_separate_single_column_index_is_declared_where_a_unique_prefix_serves(app):
    """``schedule_id`` and ``attendance_session_id`` each lead a UNIQUE
    index already, and ``group_id`` leads a declared composite one, so no
    redundant single-column index is created for any of them."""
    with app.app_context():
        inspector = sa.inspect(db.engine)
        assert {
            index["name"] for index in inspector.get_indexes("attendance_sessions")
        } == {
            "ix_attendance_sessions_group_date_id",
            "ix_attendance_sessions_date_id",
        }
        assert {
            index["name"] for index in inspector.get_indexes("attendance_records")
        } == {"ix_attendance_records_student_id"}
