"""Phase 4 / M13 -- the completion lock chain, the post-lock proof, the races
it closes, idempotency and staleness, and the unlocked opening record.

SQLite honours neither ``FOR UPDATE`` nor REPEATABLE READ, so the structural
tests assert what the code *requests* -- the tables, their order, that each
is requested ``FOR UPDATE``, and the single reset -- and the race tests commit
a competing change at the exact point between the non-locking preview and
the locks. None of this proves real InnoDB blocking.
"""

import re
from datetime import timedelta

import pytest
from sqlalchemy import event

import tests.lesson_progress_fixtures as fx
from app.extensions import db
from app.models import AcademicTerm, Course, Level, LessonProgress, Unit, User
from app.services import lesson_progress_queries as queries
from app.services import lesson_progress_transactions as tx


def _lock_requests(call):
    """``(ordered unique tables, {table: requested FOR UPDATE})`` for every
    SELECT `call` issues."""
    seen = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        match = re.search(r"\bFROM ([a-z_]+)", " ".join(statement.split()))
        if match is None:
            return
        compiled = getattr(context, "compiled", None)
        select = getattr(compiled, "statement", None)
        seen.append((match.group(1), getattr(select, "_for_update_arg", None) is not None))

    event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        call()
    finally:
        event.remove(db.engine, "before_cursor_execute", _rec)
    ordered, for_update = [], {}
    for table, locked in seen:
        if table not in ordered:
            ordered.append(table)
        for_update[table] = for_update.get(table, True) and locked
    return ordered, for_update


def _statements(call):
    seen = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        result = call()
    finally:
        event.remove(db.engine, "before_cursor_execute", _rec)
    return seen, result


def _world(label="A", student_email="student@example.com", teacher_email="teacher@example.com"):
    student, teacher, group = fx.classroom(label, student_email, teacher_email)
    unit = fx.unit(group)
    lesson = fx.lesson(unit)
    term, level, course = fx.ancestors(group)
    ids = fx.lesson_ids(student, group, unit, lesson)
    ids.update(
        term=term.id, level=level.id, course=course.id,
        enrollment=fx.enrollment_of(group, student).id, teacher=teacher.id,
    )
    return ids


def _set(ids, action, version):
    return tx.set_completion(ids["student"], ids["gp"], ids["up"], ids["lp"], action, version)


def _row(ids):
    return fx.progress_of(ids["student"], ids["group"], ids["lesson"])


def _ref(ids):
    return queries.student_lesson_progress(ids["student"], ids["gp"], ids["up"], ids["lp"])


def _add_row(ids, **values):
    row = LessonProgress(
        student_id=ids["student"], group_id=ids["group"], lesson_id=ids["lesson"],
        created_at=values.pop("created_at", fx.NOW), **values,
    )
    db.session.add(row)
    db.session.commit()
    return row.id


_CHAIN = ["academic_terms", "levels", "courses", "groups", "users", "enrollments", "units",
          "lessons", "lesson_progress"]


# ===========================================================================
# Structural lock order
# ===========================================================================


def test_the_chain_requests_the_documented_order_with_every_row_for_update(app):
    with app.app_context():
        ids = _world()
        target = queries.lock_target(ids["gp"], ids["up"], ids["lp"])
        ordered, for_update = _lock_requests(lambda: tx.lock_progress_chain(target, ids["student"]))
        db.session.rollback()
    assert ordered == _CHAIN
    assert all(for_update[table] for table in _CHAIN), for_update


def test_a_completion_previews_then_resets_once_through_the_shared_helpers(app, monkeypatch):
    calls = []
    real_hierarchy = tx.lock_academic_hierarchy
    real_group = tx.lock_group_in_open_transaction

    def spy_hierarchy(**kwargs):
        calls.append("hierarchy")
        return real_hierarchy(**kwargs)

    def spy_group(public_id):
        calls.append("group")
        return real_group(public_id)

    monkeypatch.setattr(tx, "lock_academic_hierarchy", spy_hierarchy)
    monkeypatch.setattr(tx, "lock_group_in_open_transaction", spy_group)
    with app.app_context():
        ids = _world()
        rollbacks = []
        listener = lambda conn: rollbacks.append(1)  # noqa: E731
        event.listen(db.engine, "rollback", listener)
        try:
            assert _set(ids, "complete", 1) == tx.CHANGED
        finally:
            event.remove(db.engine, "rollback", listener)
        assert calls == ["hierarchy", "group"]
        assert len(rollbacks) <= 1


def test_the_lock_preview_names_rows_through_nested_ownership_only(app):
    with app.app_context():
        ids = _world()
        other = _world("B", "other@example.com", "other-teacher@example.com")
        assert queries.lock_target(ids["gp"], ids["up"], ids["lp"]) == queries.LockTarget(
            ids["term"], ids["level"], ids["course"], ids["group"], ids["gp"], ids["unit"],
            ids["lesson"],
        )
        assert queries.lock_target(ids["gp"], ids["up"], other["lp"]) is None
        assert queries.lock_target(ids["gp"], other["up"], other["lp"]) is None
        assert queries.lock_target(other["gp"], ids["up"], ids["lp"]) is None
        assert queries.lock_target("not-a-uuid", ids["up"], ids["lp"]) is None


# ===========================================================================
# Completion, idempotency and staleness
# ===========================================================================


def test_a_first_completion_creates_one_row_at_version_two(app):
    with app.app_context():
        ids = _world()
        assert _set(ids, "complete", 1) == tx.CHANGED
        row = _row(ids)
        assert row.version == 2
        assert row.completed_at is not None and row.completed_at.microsecond == 0
        assert row.created_at == row.completed_at
        assert row.last_opened_at is None
        assert LessonProgress.query.count() == 1


def test_completing_a_completed_lesson_is_a_no_op_that_keeps_the_completion_time(app):
    with app.app_context():
        ids = _world()
        _add_row(ids, completed_at=fx.LATER, last_opened_at=fx.NOW, version=2)
        before = fx.rows()
        for version in (2, 1, 99):
            assert _set(ids, "complete", version) == tx.ALREADY
        assert fx.rows() == before


def test_undo_clears_completion_and_moves_the_version_once(app):
    with app.app_context():
        ids = _world()
        _add_row(ids, completed_at=fx.LATER, last_opened_at=fx.NOW, version=2)
        assert _set(ids, "undo", 2) == tx.CHANGED
        row = _row(ids)
        assert (row.completed_at, row.last_opened_at, row.created_at, row.version) == (
            None, fx.NOW, fx.NOW, 3
        )
        assert _set(ids, "complete", 3) == tx.CHANGED
        row = _row(ids)
        assert row.completed_at is not None and row.version == 4


def test_undo_of_an_incomplete_lesson_changes_nothing_and_creates_nothing(app):
    with app.app_context():
        ids = _world()
        assert _set(ids, "undo", 1) == tx.ALREADY
        assert fx.rows() == []
        _add_row(ids, last_opened_at=fx.NOW, version=3)
        before = fx.rows()
        assert _set(ids, "undo", 3) == tx.ALREADY
        assert _set(ids, "undo", 1) == tx.ALREADY
        assert fx.rows() == before


def test_a_version_that_moved_since_the_form_is_stale_and_writes_nothing(app):
    with app.app_context():
        ids = _world()
        _add_row(ids, last_opened_at=fx.NOW, version=3)
        before = fx.rows()
        assert _set(ids, "complete", 1) == tx.STALE
        assert fx.rows() == before
        row = _row(ids)
        row.completed_at, row.version = fx.LATER, 4
        db.session.commit()
        before = fx.rows()
        assert _set(ids, "undo", 2) == tx.STALE
        assert fx.rows() == before


def test_a_missing_row_is_version_one_for_staleness(app):
    with app.app_context():
        ids = _world()
        assert _set(ids, "complete", 2) == tx.STALE
        assert fx.rows() == []


def test_an_unknown_action_is_refused_without_locking(app, monkeypatch):
    with app.app_context():
        ids = _world()
        monkeypatch.setattr(tx, "lock_progress_chain",
                            lambda *args: (_ for _ in ()).throw(AssertionError("locked")))
        assert _set(ids, "delete", 1) == tx.UNAVAILABLE
        assert fx.rows() == []


def test_no_transaction_is_left_open_after_any_decision(app):
    with app.app_context():
        ids = _world()
        for action, version, expected in (
            ("complete", 1, tx.CHANGED),
            ("complete", 2, tx.ALREADY),
            ("undo", 1, tx.STALE),
        ):
            assert _set(ids, action, version) == expected
            assert not db.session().in_transaction(), expected
        fx.set_status(db.session.get(Unit, ids["unit"]), fx.ARCHIVED)
        assert _set(ids, "undo", 2) == tx.UNAVAILABLE
        assert not db.session().in_transaction()


# ===========================================================================
# The post-lock proof
# ===========================================================================


def _break(ids, link):
    if link == "term":
        fx.set_status(db.session.get(AcademicTerm, ids["term"]), fx.ARCHIVED)
    elif link == "level":
        fx.set_status(db.session.get(Level, ids["level"]), fx.ARCHIVED)
    elif link == "course":
        fx.set_status(db.session.get(Course, ids["course"]), fx.ARCHIVED)
    elif link == "group":
        from app.models import Group
        fx.set_status(db.session.get(Group, ids["group"]), fx.ARCHIVED)
    elif link == "suspended":
        fx.set_status(db.session.get(User, ids["student"]), fx.SUSPENDED)
    elif link == "role":
        account = db.session.get(User, ids["student"])
        account.role = fx.TEACHER
        db.session.commit()
    elif link == "enrollment":
        from app.models import Enrollment
        fx.set_status(db.session.get(Enrollment, ids["enrollment"]), fx.WITHDRAWN)
    elif link == "unit":
        fx.set_status(db.session.get(Unit, ids["unit"]), fx.ARCHIVED)
    elif link == "lesson":
        from app.models import Lesson
        fx.unpublish(db.session.get(Lesson, ids["lesson"]))
    else:  # pragma: no cover
        raise AssertionError(link)


_LINKS = {
    "term": "not_operational",
    "level": "not_operational",
    "course": "not_operational",
    "group": "not_operational",
    "suspended": "account",
    "role": "account",
    "enrollment": "enrollment",
    "unit": "unit",
    "lesson": "lesson",
}


@pytest.mark.parametrize("link", sorted(_LINKS))
def test_the_access_proof_names_each_broken_link_and_nothing_is_written(app, link):
    with app.app_context():
        ids = _world()
        _add_row(ids, completed_at=fx.LATER, version=2)
        before = fx.rows()
        _break(ids, link)
        target = queries.lock_target(ids["gp"], ids["up"], ids["lp"])
        locks = tx.lock_progress_chain(target, ids["student"])
        assert tx.access_proof_error(locks, target, ids["student"]) == _LINKS[link]
        db.session.rollback()
        assert _set(ids, "undo", 2) == tx.UNAVAILABLE
        assert _set(ids, "complete", 1) == tx.UNAVAILABLE
        assert fx.rows() == before


def test_the_proof_passes_for_an_intact_chain_and_names_a_foreign_progress_row(app):
    with app.app_context():
        ids = _world()
        target = queries.lock_target(ids["gp"], ids["up"], ids["lp"])
        locks = tx.lock_progress_chain(target, ids["student"])
        assert tx.access_proof_error(locks, target, ids["student"]) is None
        locks.progress = LessonProgress(
            student_id=ids["teacher"], group_id=ids["group"], lesson_id=ids["lesson"]
        )
        assert tx.access_proof_error(locks, target, ids["student"]) == "progress"
        db.session.rollback()


def test_a_student_who_is_not_enrolled_in_the_lessons_group_is_unavailable(app):
    with app.app_context():
        ids = _world()
        outsider = fx.user("outsider@example.com", fx.STUDENT)
        other_group = fx.hierarchy("B")
        fx.enroll(other_group, outsider)
        assert tx.set_completion(
            outsider.id, ids["gp"], ids["up"], ids["lp"], "complete", 1
        ) == tx.UNAVAILABLE
        assert fx.rows() == []


_RACES = ["enrollment", "lesson", "unit", "suspended", "group"]


@pytest.mark.parametrize("link", _RACES)
def test_a_competing_change_before_the_locks_cannot_authorize_a_completion(
    app, monkeypatch, link
):
    with app.app_context():
        ids = _world()
        real = queries.lock_target

        def preview_then_race(*args):
            target = real(*args)
            _break(ids, link)
            return target

        monkeypatch.setattr(queries, "lock_target", preview_then_race)
        assert _set(ids, "complete", 1) == tx.UNAVAILABLE
        monkeypatch.setattr(queries, "lock_target", real)
        assert fx.rows() == []


def test_a_proof_that_keeps_failing_is_bounded_and_never_writes(app, monkeypatch):
    with app.app_context():
        ids = _world()
        previews = []
        real = queries.lock_target

        def counting(*args):
            previews.append(1)
            target = real(*args)
            # A different row set each time, so only MAX_ATTEMPTS bounds it.
            return target._replace(term_id=target.term_id + len(previews) * 1000)

        monkeypatch.setattr(queries, "lock_target", counting)
        monkeypatch.setattr(tx, "access_proof_error", lambda *args: "moved")
        assert _set(ids, "complete", 1) == tx.UNAVAILABLE
        assert len(previews) == tx.MAX_ATTEMPTS
        monkeypatch.undo()
        assert fx.rows() == []


def test_a_course_moved_to_another_level_is_re_proved_not_rejected(app, monkeypatch):
    with app.app_context():
        ids = _world()
        new_level = Level(name="Level moved", display_order=99, status=fx.ACTIVE)
        db.session.add(new_level)
        db.session.commit()
        new_level_id = new_level.id
        real = queries.lock_target
        previews = []

        def preview_then_move(*args):
            target = real(*args)
            if not previews:
                course = db.session.get(Course, ids["course"])
                course.level_id = new_level_id
                db.session.commit()
            previews.append(target)
            return target

        monkeypatch.setattr(queries, "lock_target", preview_then_move)
        assert _set(ids, "complete", 1) == tx.CHANGED
        assert [target.level_id for target in previews] == [ids["level"], new_level_id]


def test_another_students_withdrawal_does_not_block_a_classmate(app):
    with app.app_context():
        ids = _world()
        from app.models import Group
        group = db.session.get(Group, ids["group"])
        classmate = fx.user("classmate@example.com", fx.STUDENT)
        fx.enroll(group, classmate)
        fx.set_status(fx.enrollment_of(group, classmate), fx.WITHDRAWN)
        assert _set(ids, "complete", 1) == tx.CHANGED


def test_an_insert_losing_the_unique_race_is_a_conflict_without_a_second_row(app, monkeypatch):
    with app.app_context():
        ids = _world()
        _add_row(ids, last_opened_at=fx.NOW, version=1)
        before = fx.rows()
        real = tx.lock_progress_chain

        def hide_the_row(target, student_id):
            locks = real(target, student_id)
            locks.progress = None
            return locks

        monkeypatch.setattr(tx, "lock_progress_chain", hide_the_row)
        assert _set(ids, "complete", 1) == tx.CONFLICT
        assert not db.session().in_transaction()
        monkeypatch.undo()
        assert fx.rows() == before


# ===========================================================================
# The opening record
# ===========================================================================


def test_a_first_opening_inserts_version_one_without_completion(app):
    with app.app_context():
        ids = _world()
        assert tx.record_open(ids["student"], _ref(ids), now=fx.NOW) == tx.RECORDED
        row = _row(ids)
        assert (row.created_at, row.last_opened_at, row.completed_at, row.version) == (
            fx.NOW, fx.NOW, None, 1
        )


def test_an_opening_within_the_window_issues_no_statement(app):
    with app.app_context():
        ids = _world()
        _add_row(ids, last_opened_at=fx.NOW, version=1)
        before = fx.rows()
        moment = fx.NOW + timedelta(seconds=tx.OPEN_REFRESH_SECONDS - 1)
        ref = _ref(ids)
        statements, outcome = _statements(lambda: tx.record_open(ids["student"], ref, now=moment))
        assert outcome == tx.SKIPPED
        assert statements == []
        assert fx.rows() == before


def test_a_later_opening_moves_only_last_opened_forward(app):
    with app.app_context():
        ids = _world()
        _add_row(ids, last_opened_at=fx.NOW, completed_at=fx.NOW, version=2)
        moment = fx.NOW + timedelta(seconds=tx.OPEN_REFRESH_SECONDS)
        assert tx.record_open(ids["student"], _ref(ids), now=moment) == tx.RECORDED
        row = _row(ids)
        assert (row.created_at, row.last_opened_at, row.completed_at, row.version) == (
            fx.NOW, moment, fx.NOW, 2
        )


def test_an_opening_never_moves_last_opened_backwards_or_twice(app):
    with app.app_context():
        ids = _world()
        _add_row(ids, last_opened_at=fx.NOW, version=1)
        stale = _ref(ids)
        row = _row(ids)
        row.last_opened_at = fx.LATEST
        db.session.commit()
        # The request read NOW; a concurrent opening has since recorded
        # LATEST. The WHERE guard leaves the newer value alone.
        tx.record_open(ids["student"], stale, now=fx.LATEST + timedelta(seconds=30))
        assert _row(ids).last_opened_at == fx.LATEST
        assert tx.record_open(ids["student"], _ref(ids), now=fx.NOW) == tx.SKIPPED
        assert _row(ids).last_opened_at == fx.LATEST


def test_a_concurrent_first_opening_is_skipped_without_a_duplicate(app):
    with app.app_context():
        ids = _world()
        ref = _ref(ids)
        _add_row(ids, last_opened_at=fx.NOW, version=1)
        before = fx.rows()
        assert tx.record_open(ids["student"], ref, now=fx.LATER) == tx.SKIPPED
        assert not db.session().in_transaction()
        assert fx.rows() == before


def test_an_opening_never_stales_an_open_completion_form(app):
    with app.app_context():
        ids = _world()
        _add_row(ids, last_opened_at=fx.NOW, version=1)
        form_version = _ref(ids).version
        tx.record_open(ids["student"], _ref(ids), now=fx.LATEST)
        assert _row(ids).version == form_version
        assert _set(ids, "complete", form_version) == tx.CHANGED
