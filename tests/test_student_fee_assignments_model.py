"""Phase 5 / M03 -- the StudentFeeAssignment model.

Application validators and defaults first, then every database CHECK and
foreign key driven directly with raw SQL so the validators cannot mask a
missing constraint, then the absence of any relationship, cascade, duplicated
identity column or payment column.
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

import tests.fee_assignment_fixtures as fx
from app.extensions import db
from app.models import StudentFeeAssignment, StudentFeeAssignmentStatus
from app.models.submission_feedback import whole_second_utc

_T0 = "'2026-05-10 09:00:00'"
_T1 = "'2026-05-11 09:00:00'"
_EARLIER = "'2026-05-09 09:00:00'"


def test_the_closed_status_set_is_exactly_assigned_and_cancelled():
    assert [member.value for member in StudentFeeAssignmentStatus] == ["assigned", "cancelled"]


def test_defaults_public_ids_status_version_and_timestamp_hooks(app):
    with app.app_context():
        actor = fx.admin()
        owner = fx.enrollment()
        fee_plan = fx.active_plan(actor)
        rows = []
        for _ in range(2):
            row = StudentFeeAssignment(
                enrollment_id=owner.id, fee_plan_id=fee_plan.id, assigned_at=fx.ASSIGNED_AT,
                assigned_by_id=actor.id, created_at=fx.ASSIGNED_AT, updated_at=fx.ASSIGNED_AT,
            )
            db.session.add(row)
            db.session.commit()
            rows.append(row)
        first, second = rows
        assert len(first.public_id) == 36 and first.public_id != second.public_id
        assert first.status == fx.ASSIGNED and first.version == 1
        assert first.cancelled_at is None and first.cancelled_by_id is None
        assert first.is_assigned and not first.is_cancelled

        columns = StudentFeeAssignment.__table__.c
        assert columns.created_at.default.is_callable and columns.updated_at.default.is_callable
        assert columns.assigned_at.default is None and columns.cancelled_at.default is None
        assert columns.created_at.onupdate is None and columns.updated_at.onupdate is None
        assert whole_second_utc().microsecond == 0


@pytest.mark.parametrize(
    "field, value",
    [
        ("status", "paid"),
        ("status", "ASSIGNED"),
        ("status", "active"),
        ("version", 0),
        ("version", -1),
        ("version", True),
        ("version", "1"),
    ],
)
def test_validators_refuse_bad_values(app, field, value):
    with app.app_context():
        with pytest.raises(ValueError):
            StudentFeeAssignment(**{field: value})


def test_a_cancelled_row_reports_its_state(app):
    with app.app_context():
        actor = fx.admin()
        row = fx.assignment(fx.enrollment(), fx.active_plan(actor), actor, status=fx.CANCELLED)
        assert row.is_cancelled and not row.is_assigned


# ===========================================================================
# Database constraints, driven with raw SQL
# ===========================================================================

_COLUMNS = (
    "public_id", "enrollment_id", "fee_plan_id", "status", "assigned_at", "assigned_by_id",
    "cancelled_at", "cancelled_by_id", "version", "created_at", "updated_at",
)


def _sql(enrollment_ref, plan_ref, actor_ref, **overrides):
    values = {
        "public_id": f"'a-{fx._next()}'",
        "enrollment_id": str(enrollment_ref),
        "fee_plan_id": str(plan_ref),
        "status": "'assigned'",
        "assigned_at": _T0,
        "assigned_by_id": str(actor_ref),
        "cancelled_at": "NULL",
        "cancelled_by_id": "NULL",
        "version": "1",
        "created_at": _T0,
        "updated_at": _T0,
    }
    values.update(overrides)
    return (
        f"INSERT INTO student_fee_assignments ({', '.join(_COLUMNS)}) VALUES "
        f"({', '.join(values[column] for column in _COLUMNS)})"
    )


def _accepted(statement):
    db.session.execute(sa.text(statement))
    db.session.commit()


def _refused(statement, rule):
    try:
        db.session.execute(sa.text(statement))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return
    raise AssertionError(f"{rule} was not enforced")


def _ids():
    actor = fx.admin()
    owner = fx.enrollment()
    fee_plan = fx.active_plan(actor)
    return owner.id, fee_plan.id, actor.id


def test_every_check_not_null_unique_and_foreign_key_is_enforced(app):
    with app.app_context():
        enrollment_id, plan_id, actor_id = _ids()
        actor = str(actor_id)
        cancelled = {"status": "'cancelled'", "cancelled_at": _T1, "cancelled_by_id": actor,
                     "updated_at": _T1, "version": "2"}
        _accepted(_sql(enrollment_id, plan_id, actor_id, public_id="'legal-1'"))
        _accepted(_sql(enrollment_id, plan_id, actor_id, **cancelled))

        for overrides, rule in (
            ({"public_id": "'legal-1'"}, "public_id uniqueness"),
            ({"public_id": "NULL"}, "public_id NOT NULL"),
            ({"status": "'paid'"}, "ck_student_fee_assignments_status_valid"),
            ({"status": "'ASSIGNED'"}, "the case-sensitive closed set"),
            ({"version": "0"}, "ck_student_fee_assignments_version_positive"),
            ({"version": "-1"}, "a negative version"),
            ({"assigned_at": "NULL"}, "assigned_at NOT NULL / the assignment pair"),
            ({"assigned_by_id": "NULL"}, "assigned_by_id NOT NULL / the assignment pair"),
            ({"cancelled_at": _T1, "updated_at": _T1}, "a cancellation moment without its actor"),
            ({"cancelled_by_id": actor}, "a cancelling actor without the moment"),
            (dict(cancelled, cancelled_by_id="NULL"), "a cancelled row without its actor"),
            (dict(cancelled, cancelled_at="NULL", cancelled_by_id="NULL", updated_at=_T0),
             "a cancelled row without a cancellation"),
            (dict(cancelled, status="'assigned'"), "an assigned row carrying a cancellation"),
            ({"assigned_at": _EARLIER}, "assigned_at before created_at"),
            ({"updated_at": _EARLIER, "assigned_at": _EARLIER, "created_at": _EARLIER,
              "cancelled_at": "NULL"} | {"updated_at": "'2026-05-08 09:00:00'"},
             "updated_at before assigned_at"),
            (dict(cancelled, cancelled_at=_EARLIER), "a cancellation before the assignment"),
            (dict(cancelled, updated_at=_T0), "updated_at before the cancellation"),
            ({"enrollment_id": "999999"}, "the enrollment foreign key"),
            ({"enrollment_id": "NULL"}, "enrollment_id NOT NULL"),
            ({"fee_plan_id": "999999"}, "the fee_plan foreign key"),
            ({"fee_plan_id": "NULL"}, "fee_plan_id NOT NULL"),
            ({"assigned_by_id": "999999"}, "the assigned_by foreign key"),
            (dict(cancelled, cancelled_by_id="999999"), "the cancelled_by foreign key"),
            ({"status": "NULL"}, "status NOT NULL"),
            ({"version": "NULL"}, "version NOT NULL"),
        ):
            _refused(_sql(enrollment_id, plan_id, actor_id, **overrides), rule)
        assert StudentFeeAssignment.query.count() == 2


def test_one_assigned_plan_per_enrollment_is_an_application_rule_not_a_constraint(app):
    """MySQL has no portable partial unique index for "assigned rows only", so
    the database accepts a second assigned row; the routes prove the rule
    under the Enrollment lock (see the route and transaction suites)."""
    with app.app_context():
        enrollment_id, plan_id, actor_id = _ids()
        _accepted(_sql(enrollment_id, plan_id, actor_id))
        _accepted(_sql(enrollment_id, plan_id, actor_id))
        assert StudentFeeAssignment.query.filter_by(status=fx.ASSIGNED).count() == 2


def test_nothing_referenced_can_be_hard_deleted(app):
    with app.app_context():
        enrollment_id, plan_id, actor_id = _ids()
        _accepted(_sql(enrollment_id, plan_id, actor_id))
        _refused(f"DELETE FROM enrollments WHERE id = {enrollment_id}", "no cascade from enrollments")
        _refused(f"DELETE FROM fee_plans WHERE id = {plan_id}", "no cascade from fee_plans")
        _refused(f"DELETE FROM users WHERE id = {actor_id}", "no cascade from users")
        assert StudentFeeAssignment.query.count() == 1


def test_no_relationship_cascade_or_ondelete_exists(app):
    with app.app_context():
        assert not sa.inspect(StudentFeeAssignment).relationships
        foreign_keys = StudentFeeAssignment.__table__.foreign_keys
        for foreign_key in foreign_keys:
            assert foreign_key.ondelete is None and foreign_key.onupdate is None
        assert {(fk.parent.name, fk.column.table.name) for fk in foreign_keys} == {
            ("enrollment_id", "enrollments"),
            ("fee_plan_id", "fee_plans"),
            ("assigned_by_id", "users"),
            ("cancelled_by_id", "users"),
        }


def test_no_identity_financial_or_payment_data_is_duplicated_into_the_row():
    columns = {column.name for column in StudentFeeAssignment.__table__.columns}
    assert columns == {
        "id", "public_id", "enrollment_id", "fee_plan_id", "status", "assigned_at",
        "assigned_by_id", "cancelled_at", "cancelled_by_id", "version", "created_at",
        "updated_at",
    }
    for forbidden in ("student_id", "group_id", "course_id", "level_id", "academic_term_id",
                      "name", "label", "currency_code", "amount", "total", "invoice_id",
                      "payment_status", "paid_at"):
        assert forbidden not in columns, forbidden
