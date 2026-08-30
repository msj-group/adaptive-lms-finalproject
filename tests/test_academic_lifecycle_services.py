"""M07C1 -- academic-lifecycle query helpers and the AcademicTerm / Level
/ Course transaction-lock primitives.

SQLite (the test backend) has no `SELECT ... FOR UPDATE` and no
REPEATABLE READ snapshot isolation, so nothing here proves that a lock
actually blocks a concurrent MySQL/InnoDB transaction. These tests
verify only the *requested* structure and ordering:

- the query helpers emit a scalar SQL ``EXISTS`` (never a ``COUNT`` and
  never a descendant-row fetch), and return genuine Python booleans;
  their contract is the direct entity reference plus ``Group.status``
  only -- never an ancestor's status;
- each first-lock entry point issues exactly one ``db.session.rollback()``
  *before* its locking query, with no query in between;
- the ``in_open_transaction`` primitives issue no transaction-ending
  call of their own (no rollback / commit / close / remove), so they do
  not themselves end the transaction an earlier first-lock entry point
  opened. SQLite cannot show that a real InnoDB lock stays held or
  blocks another transaction, and these tests do not claim it does.

None of the new services is imported by any Blueprint yet; this Part is
infrastructure only.
"""

import contextlib
from datetime import date
from unittest.mock import patch

from sqlalchemy import event
from sqlalchemy.orm import Query

from app.extensions import db
from app.models import AcademicStatus, AcademicTerm, Course, Group, Level
from app.services.academic_lifecycle import (
    academic_term_has_active_group,
    course_has_active_group,
    level_has_active_course,
    level_has_active_group,
)
from app.services.academic_term_transactions import (
    lock_academic_term_for_write,
    lock_academic_term_for_write_by_id,
)
from app.services.course_transactions import (
    lock_course_for_write,
    lock_course_for_write_by_id,
    lock_course_in_open_transaction,
    lock_course_in_open_transaction_by_id,
)
from app.services.level_transactions import (
    lock_level_for_write,
    lock_level_for_write_by_id,
    lock_level_in_open_transaction_by_id,
)

_MISSING_ID = 999_999
_MISSING_PUBLIC_ID = "00000000-0000-0000-0000-000000000000"


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


def _term(name="Fall 2026", status=AcademicStatus.ACTIVE.value):
    term = AcademicTerm(
        name=name, start_date=date(2026, 9, 1), end_date=date(2026, 12, 31), status=status
    )
    db.session.add(term)
    db.session.commit()
    return term


def _level(name="Level 1", status=AcademicStatus.ACTIVE.value):
    level = Level(name=name, display_order=0, status=status)
    db.session.add(level)
    db.session.commit()
    return level


def _course(level, title="Course A", status=AcademicStatus.ACTIVE.value):
    course = Course(level_id=level.id, title=title, display_order=0, status=status)
    db.session.add(course)
    db.session.commit()
    return course


def _group(term, course, name="Group A", status=AcademicStatus.ACTIVE.value, capacity=20):
    group = Group(
        academic_term_id=term.id,
        course_id=course.id,
        name=name,
        capacity=capacity,
        status=status,
    )
    db.session.add(group)
    db.session.commit()
    return group


@contextlib.contextmanager
def _sql_capture():
    """Record every SQL statement the DBAPI actually executes while the
    block runs. Robust-capture alternative to matching module source
    text.
    """
    statements = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", before_cursor_execute)
    try:
        yield statements
    finally:
        event.remove(db.engine, "before_cursor_execute", before_cursor_execute)


_SESSION_BOUNDARY_CALLS = {
    "rollback": "reset",
    "commit": "commit",
    "close": "close",
    "remove": "remove",
}


@contextlib.contextmanager
def _lock_trace():
    """Record, in call order:

    - every ``db.session`` transaction-boundary call -- ``reset``
      (rollback), ``commit``, ``close``, ``remove`` -- each wrapping the
      real method so behaviour is unchanged;
    - every ``Query.with_for_update()`` tagged with its target model
      (``lock:<Model>``);
    - the leading verb of every executed SQL statement (``sql:<VERB>``).

    A test can then assert the exact reset / lock-request / query
    ordering *and* the absence of any unwanted transaction-ending call.
    No production code is touched -- only the shared scoped session and
    the legacy ``Query`` class are spied on, for the duration of the
    ``with`` block.
    """
    events = []
    session = db.session
    originals = {name: getattr(session, name) for name in _SESSION_BOUNDARY_CALLS}
    original_with_for_update = Query.with_for_update

    def make_boundary_spy(name):
        original = originals[name]
        label = _SESSION_BOUNDARY_CALLS[name]

        def spy(*args, **kwargs):
            events.append(label)
            return original(*args, **kwargs)

        return spy

    def with_for_update_spy(self, *args, **kwargs):
        cols = self.column_descriptions
        entity = cols[0]["entity"] if cols else None
        events.append(f"lock:{getattr(entity, '__name__', '?')}")
        return original_with_for_update(self, *args, **kwargs)

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        events.append("sql:" + statement.strip().split(None, 1)[0].upper())

    event.listen(db.engine, "before_cursor_execute", before_cursor_execute)
    patchers = [
        patch.object(session, name, side_effect=make_boundary_spy(name))
        for name in _SESSION_BOUNDARY_CALLS
    ]
    patchers.append(patch.object(Query, "with_for_update", with_for_update_spy))
    try:
        with contextlib.ExitStack() as stack:
            for patcher in patchers:
                stack.enter_context(patcher)
            yield events
    finally:
        event.remove(db.engine, "before_cursor_execute", before_cursor_execute)


# ===========================================================================
# Query helpers -- semantics
# ===========================================================================


def test_academic_term_has_active_group_false_without_group(app):
    with app.app_context():
        term = _term()
        assert academic_term_has_active_group(term.id) is False


def test_academic_term_has_active_group_true_with_active_group(app):
    with app.app_context():
        term = _term()
        _group(term, _course(_level()), status=AcademicStatus.ACTIVE.value)
        assert academic_term_has_active_group(term.id) is True


def test_academic_term_has_active_group_ignores_archived_group(app):
    with app.app_context():
        term = _term()
        _group(term, _course(_level()), status=AcademicStatus.ARCHIVED.value)
        assert academic_term_has_active_group(term.id) is False


def test_academic_term_has_active_group_scoped_to_the_term(app):
    with app.app_context():
        term_a = _term("Term A")
        term_b = _term("Term B")
        _group(term_b, _course(_level()), status=AcademicStatus.ACTIVE.value)
        assert academic_term_has_active_group(term_a.id) is False
        assert academic_term_has_active_group(term_b.id) is True


def test_academic_term_has_active_group_false_for_missing_id(app):
    with app.app_context():
        assert academic_term_has_active_group(_MISSING_ID) is False


def test_academic_term_has_active_group_true_even_when_course_and_level_archived(app):
    """Legacy-data contract: the helper depends only on the direct Term
    reference and ``Group.status`` -- never on ancestor status. An active
    Group whose Course *and* Level are both archived must still make the
    Term report an active Group, so a future ancestor-status filter can
    never silently narrow this."""
    with app.app_context():
        term = _term()
        archived_level = _level(status=AcademicStatus.ARCHIVED.value)
        archived_course = _course(archived_level, status=AcademicStatus.ARCHIVED.value)
        _group(term, archived_course, status=AcademicStatus.ACTIVE.value)
        assert academic_term_has_active_group(term.id) is True


def test_course_has_active_group_false_without_group(app):
    with app.app_context():
        course = _course(_level())
        assert course_has_active_group(course.id) is False


def test_course_has_active_group_true_with_active_group(app):
    with app.app_context():
        course = _course(_level())
        _group(_term(), course, status=AcademicStatus.ACTIVE.value)
        assert course_has_active_group(course.id) is True


def test_course_has_active_group_ignores_archived_group(app):
    with app.app_context():
        course = _course(_level())
        _group(_term(), course, status=AcademicStatus.ARCHIVED.value)
        assert course_has_active_group(course.id) is False


def test_course_has_active_group_scoped_to_the_course(app):
    with app.app_context():
        level = _level()
        course_a = _course(level, title="Course A")
        course_b = _course(level, title="Course B")
        _group(_term(), course_b, status=AcademicStatus.ACTIVE.value)
        assert course_has_active_group(course_a.id) is False
        assert course_has_active_group(course_b.id) is True


def test_course_has_active_group_false_for_missing_id(app):
    with app.app_context():
        assert course_has_active_group(_MISSING_ID) is False


def test_course_has_active_group_true_even_when_term_archived(app):
    """Legacy-data contract: the helper depends only on the direct Course
    reference and ``Group.status`` -- never on the Group's AcademicTerm
    status. An active Group in an archived Term must still make its Course
    report an active Group."""
    with app.app_context():
        archived_term = _term(status=AcademicStatus.ARCHIVED.value)
        course = _course(_level())
        _group(archived_term, course, status=AcademicStatus.ACTIVE.value)
        assert course_has_active_group(course.id) is True


def test_level_has_active_course_false_without_course(app):
    with app.app_context():
        level = _level()
        assert level_has_active_course(level.id) is False


def test_level_has_active_course_true_with_active_course(app):
    with app.app_context():
        level = _level()
        _course(level, status=AcademicStatus.ACTIVE.value)
        assert level_has_active_course(level.id) is True


def test_level_has_active_course_ignores_archived_course(app):
    with app.app_context():
        level = _level()
        _course(level, status=AcademicStatus.ARCHIVED.value)
        assert level_has_active_course(level.id) is False


def test_level_has_active_course_scoped_to_the_level(app):
    with app.app_context():
        level_a = _level("Level A")
        level_b = _level("Level B")
        _course(level_b, title="Only In B")
        assert level_has_active_course(level_a.id) is False
        assert level_has_active_course(level_b.id) is True


def test_level_has_active_course_false_for_missing_id(app):
    with app.app_context():
        assert level_has_active_course(_MISSING_ID) is False


def test_level_has_active_group_false_without_group(app):
    with app.app_context():
        level = _level()
        _course(level)  # active course, but no group under it
        assert level_has_active_group(level.id) is False


def test_level_has_active_group_true_via_active_course(app):
    with app.app_context():
        level = _level()
        course = _course(level, status=AcademicStatus.ACTIVE.value)
        _group(_term(), course, status=AcademicStatus.ACTIVE.value)
        assert level_has_active_group(level.id) is True


def test_level_has_active_group_true_even_when_intermediate_course_archived(app):
    """The legacy inconsistency the guard must survive: an active Group
    beneath an *archived* Course still blocks archiving that Course's
    Level, because the Group's derived Level would otherwise flip to
    archived while the Group runs.
    """
    with app.app_context():
        level = _level()
        archived_course = _course(level, status=AcademicStatus.ARCHIVED.value)
        _group(_term(), archived_course, status=AcademicStatus.ACTIVE.value)
        assert level_has_active_course(level.id) is False
        assert level_has_active_group(level.id) is True


def test_level_has_active_group_ignores_archived_group(app):
    with app.app_context():
        level = _level()
        course = _course(level)
        _group(_term(), course, status=AcademicStatus.ARCHIVED.value)
        assert level_has_active_group(level.id) is False


def test_level_has_active_group_scoped_to_the_level(app):
    with app.app_context():
        level_a = _level("Level A")
        level_b = _level("Level B")
        _group(_term(), _course(level_b, title="Course B"), status=AcademicStatus.ACTIVE.value)
        assert level_has_active_group(level_a.id) is False
        assert level_has_active_group(level_b.id) is True


def test_level_has_active_group_false_for_missing_id(app):
    with app.app_context():
        assert level_has_active_group(_MISSING_ID) is False


def test_all_query_helpers_return_genuine_python_bool(app):
    with app.app_context():
        term = _term()
        level = _level()
        course = _course(level)
        _group(term, course, status=AcademicStatus.ACTIVE.value)

        values = [
            academic_term_has_active_group(term.id),
            academic_term_has_active_group(_MISSING_ID),
            course_has_active_group(course.id),
            course_has_active_group(_MISSING_ID),
            level_has_active_course(level.id),
            level_has_active_course(_MISSING_ID),
            level_has_active_group(level.id),
            level_has_active_group(_MISSING_ID),
        ]
        for value in values:
            assert value is True or value is False
            assert type(value) is bool


def test_query_helpers_emit_one_scalar_exists_and_never_count(app):
    """Robust SQL capture instead of source-text matching: each helper
    issues a single ``SELECT EXISTS (...)`` round-trip and never a
    ``COUNT`` or a descendant-row fetch.
    """
    with app.app_context():
        term = _term()
        level = _level()
        course = _course(level)
        _group(term, course, status=AcademicStatus.ACTIVE.value)
        # Bind plain ids up front so a lazy attribute refresh inside the
        # capture block cannot be mistaken for the helper's own query.
        term_id, level_id, course_id = term.id, level.id, course.id

        helper_calls = (
            lambda: academic_term_has_active_group(term_id),
            lambda: course_has_active_group(course_id),
            lambda: level_has_active_course(level_id),
            lambda: level_has_active_group(level_id),
        )
        for call in helper_calls:
            with _sql_capture() as statements:
                call()
            assert len(statements) == 1
            sql = statements[0].upper()
            assert "SELECT EXISTS (" in sql
            assert "COUNT(" not in sql


# ===========================================================================
# Transaction primitives -- lookup + missing
# ===========================================================================


def test_lock_academic_term_for_write_returns_current_row_by_public_id(app):
    with app.app_context():
        term = _term()
        public_id, internal_id = term.public_id, term.id
        locked = lock_academic_term_for_write(public_id)
        assert locked is not None
        assert locked.id == internal_id


def test_lock_academic_term_for_write_by_id_returns_current_row(app):
    with app.app_context():
        term = _term()
        internal_id = term.id
        locked = lock_academic_term_for_write_by_id(internal_id)
        assert locked is not None
        assert locked.id == internal_id


def test_lock_academic_term_missing_returns_none(app):
    with app.app_context():
        assert lock_academic_term_for_write(_MISSING_PUBLIC_ID) is None
        assert lock_academic_term_for_write_by_id(_MISSING_ID) is None


def test_lock_level_primitives_return_current_row(app):
    with app.app_context():
        level = _level()
        public_id, internal_id = level.public_id, level.id
        assert lock_level_for_write(public_id).id == internal_id
        assert lock_level_for_write_by_id(internal_id).id == internal_id
        assert lock_level_in_open_transaction_by_id(internal_id).id == internal_id


def test_lock_level_missing_returns_none(app):
    with app.app_context():
        assert lock_level_for_write(_MISSING_PUBLIC_ID) is None
        assert lock_level_for_write_by_id(_MISSING_ID) is None
        assert lock_level_in_open_transaction_by_id(_MISSING_ID) is None


def test_lock_course_primitives_return_current_row(app):
    with app.app_context():
        course = _course(_level())
        public_id, internal_id = course.public_id, course.id
        assert lock_course_for_write(public_id).id == internal_id
        assert lock_course_for_write_by_id(internal_id).id == internal_id
        assert lock_course_in_open_transaction(public_id).id == internal_id
        assert lock_course_in_open_transaction_by_id(internal_id).id == internal_id


def test_lock_course_missing_returns_none(app):
    with app.app_context():
        assert lock_course_for_write(_MISSING_PUBLIC_ID) is None
        assert lock_course_for_write_by_id(_MISSING_ID) is None
        assert lock_course_in_open_transaction(_MISSING_PUBLIC_ID) is None
        assert lock_course_in_open_transaction_by_id(_MISSING_ID) is None


# ===========================================================================
# Transaction primitives -- reset / lock / query ordering
# ===========================================================================


def _assert_reset_then_lock(events, model_name):
    """The first-lock entry point's *complete* event stream must be
    exactly: one reset, then the FOR UPDATE request for `model_name`,
    then that lock's single SELECT -- and nothing else.

    A single equality assertion proves all of: exactly one reset; the
    reset first; the expected model locked next; exactly one locking
    SELECT after it; and no `commit` / `close` / `remove`, no second
    reset, no extra lock request, no extra SQL statement, and no other
    unexpected event anywhere inside the helper.
    """
    assert events == ["reset", f"lock:{model_name}", "sql:SELECT"], events


def _assert_no_reset_then_lock(events, model_name):
    assert events[0] == f"lock:{model_name}", events
    assert [e for e in events if e.startswith("sql:")] == ["sql:SELECT"], events
    # a no-reset primitive ends no transaction of its own
    for boundary in ("reset", "commit", "close", "remove"):
        assert boundary not in events, events


def test_lock_academic_term_for_write_resets_then_locks_academicterm(app):
    with app.app_context():
        public_id = _term().public_id
        with _lock_trace() as events:
            assert lock_academic_term_for_write(public_id) is not None
        _assert_reset_then_lock(events, "AcademicTerm")


def test_lock_academic_term_for_write_by_id_resets_then_locks_academicterm(app):
    with app.app_context():
        internal_id = _term().id
        with _lock_trace() as events:
            assert lock_academic_term_for_write_by_id(internal_id) is not None
        _assert_reset_then_lock(events, "AcademicTerm")


def test_lock_level_for_write_resets_then_locks_level(app):
    with app.app_context():
        public_id = _level().public_id
        with _lock_trace() as events:
            assert lock_level_for_write(public_id) is not None
        _assert_reset_then_lock(events, "Level")


def test_lock_level_for_write_by_id_resets_exactly_once_then_locks_level(app):
    """Even though it delegates to the no-reset primitive, the by-id
    resetting entry point still performs exactly one reset."""
    with app.app_context():
        internal_id = _level().id
        with _lock_trace() as events:
            assert lock_level_for_write_by_id(internal_id) is not None
        _assert_reset_then_lock(events, "Level")


def test_lock_level_in_open_transaction_by_id_never_resets(app):
    with app.app_context():
        internal_id = _level().id
        with _lock_trace() as events:
            assert lock_level_in_open_transaction_by_id(internal_id) is not None
        _assert_no_reset_then_lock(events, "Level")


def test_lock_course_for_write_still_resets_then_locks_after_delegation(app):
    with app.app_context():
        public_id = _course(_level()).public_id
        with _lock_trace() as events:
            assert lock_course_for_write(public_id) is not None
        _assert_reset_then_lock(events, "Course")


def test_lock_course_for_write_by_id_still_resets_exactly_once_then_locks(app):
    with app.app_context():
        internal_id = _course(_level()).id
        with _lock_trace() as events:
            assert lock_course_for_write_by_id(internal_id) is not None
        _assert_reset_then_lock(events, "Course")


def test_lock_course_in_open_transaction_never_resets(app):
    with app.app_context():
        public_id = _course(_level()).public_id
        with _lock_trace() as events:
            assert lock_course_in_open_transaction(public_id) is not None
        _assert_no_reset_then_lock(events, "Course")


def test_lock_course_in_open_transaction_by_id_never_resets(app):
    with app.app_context():
        internal_id = _course(_level()).id
        with _lock_trace() as events:
            assert lock_course_in_open_transaction_by_id(internal_id) is not None
        _assert_no_reset_then_lock(events, "Course")


def test_course_reset_entry_points_are_thin_wrappers_over_the_no_reset_primitives(app):
    """The refactor must not have changed the external contract: each
    resetting entry point is exactly one ``reset`` followed by the
    matching no-reset primitive's own event sequence.
    """
    with app.app_context():
        course = _course(_level())
        public_id, internal_id = course.public_id, course.id

        with _lock_trace() as reset_pid:
            lock_course_for_write(public_id)
        with _lock_trace() as no_reset_pid:
            lock_course_in_open_transaction(public_id)
        assert reset_pid == ["reset"] + no_reset_pid

        with _lock_trace() as reset_by_id:
            lock_course_for_write_by_id(internal_id)
        with _lock_trace() as no_reset_by_id:
            lock_course_in_open_transaction_by_id(internal_id)
        assert reset_by_id == ["reset"] + no_reset_by_id


def test_full_term_level_course_chain_resets_once_and_ends_no_transaction(app):
    """Structurally exercise the approved chain

        AcademicTerm (first-lock reset + lock)
          -> Level  (no-reset lock)
          -> Course (no-reset lock)

    and prove: exactly one reset, occurring before the AcademicTerm
    lock; requested lock order exactly AcademicTerm -> Level -> Course
    with no extra lock request; exactly three locking SELECTs; and no
    second reset and no ``commit`` / ``close`` / ``remove`` anywhere in
    the two no-reset steps.

    SQLite proves only the *requested* structure. It cannot show that the
    AcademicTerm and Level locks are still held when the Course lock is
    taken, nor that they would block another transaction -- only
    MySQL/InnoDB can. This asserts structure and the absence of any
    transaction-ending call, nothing more.
    """
    with app.app_context():
        term = _term()
        level = _level()
        course = _course(level)
        term_id, level_id, course_id = term.id, level.id, course.id

        with _lock_trace() as events:
            locked_term = lock_academic_term_for_write_by_id(term_id)
            events_after_first_lock = list(events)
            locked_level = lock_level_in_open_transaction_by_id(level_id)
            locked_course = lock_course_in_open_transaction_by_id(course_id)

        assert locked_term is not None and locked_term.id == term_id
        assert locked_level is not None and locked_level.id == level_id
        assert locked_course is not None and locked_course.id == course_id

        # exactly one reset, immediately before the AcademicTerm lock,
        # with no query in between
        assert events.count("reset") == 1
        assert events_after_first_lock[:2] == ["reset", "lock:AcademicTerm"]

        # requested lock order is exactly Term -> Level -> Course, nothing more
        assert [e for e in events if e.startswith("lock:")] == [
            "lock:AcademicTerm",
            "lock:Level",
            "lock:Course",
        ]

        # exactly three locking SELECTs -- one per step, no extras
        assert [e for e in events if e.startswith("sql:")] == ["sql:SELECT"] * 3

        # the two no-reset steps ended nothing: no second reset, and no
        # commit / close / remove anywhere in the chain
        assert "commit" not in events
        assert "close" not in events
        assert "remove" not in events

        # full expected event stream
        assert events == [
            "reset",
            "lock:AcademicTerm",
            "sql:SELECT",
            "lock:Level",
            "sql:SELECT",
            "lock:Course",
            "sql:SELECT",
        ]
