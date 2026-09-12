"""Phase 4 / M11 -- the messaging lock chain, the post-lock proofs, the
concurrency races they close, and IntegrityError handling.

SQLite honours neither ``FOR UPDATE`` nor REPEATABLE READ, so the
structural tests assert what the code *requests* -- the tables, their
order, the User id order and the single reset -- and the race tests
interleave a competing commit at the exact point between the preview and
the locks. None of this proves real InnoDB blocking.
"""

import re

import pytest
from sqlalchemy import event

import tests.message_fixtures as fx
from app.extensions import db
from app.models import (
    AcademicStatus,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Message,
    MessageThread,
    MessageThreadMember,
    User,
    UserStatus,
)
from app.services import message_queries, message_transactions as tx


def _record(app, call, predicate):
    seen = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        flat = " ".join(statement.split())
        value = predicate(flat, parameters)
        if value is not None:
            seen.append(value)

    event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        result = call()
    finally:
        event.remove(db.engine, "before_cursor_execute", _rec)
    return seen, result


def _first_table(flat, _parameters):
    match = re.search(r"\bFROM ([a-z_]+)", flat)
    return match.group(1) if match else None


def _ordered_tables(app, call):
    seen, _ = _record(app, call, _first_table)
    ordered = []
    for name in seen:
        if name not in ordered:
            ordered.append(name)
    return ordered


def _locked_user_id(flat, parameters):
    if re.search(r"\bFROM users WHERE users\.id = \?", flat):
        return tuple(parameters)[0]
    return None


# ===========================================================================
# Structural lock order
# ===========================================================================


def test_the_create_chain_requests_the_documented_lock_order(app):
    with app.app_context():
        student, teacher, _ = fx.pair()
        relationship = message_queries.shared_relationship(student.id, teacher.id)
        user_ids = (student.id, teacher.id)
        ordered = _ordered_tables(app, lambda: tx.lock_message_chain(relationship, user_ids))
    assert ordered == [
        "academic_terms",
        "levels",
        "courses",
        "groups",
        "users",
        "group_teacher_assignments",
        "enrollments",
    ]


def test_the_reply_chain_adds_the_thread_and_its_members_last(app):
    with app.app_context():
        student, teacher, _ = fx.pair()
        thread_id = fx.thread(student, teacher).id
        relationship = message_queries.shared_relationship(student.id, teacher.id)
        user_ids = (student.id, teacher.id)
        ordered = _ordered_tables(
            app, lambda: tx.lock_message_chain(relationship, user_ids, thread_id)
        )
    assert ordered == [
        "academic_terms",
        "levels",
        "courses",
        "groups",
        "users",
        "group_teacher_assignments",
        "enrollments",
        "message_threads",
        "message_thread_members",
    ]


def test_the_chain_resets_the_transaction_exactly_once(app):
    with app.app_context():
        student, teacher, _ = fx.pair()
        relationship = message_queries.shared_relationship(student.id, teacher.id)
        user_ids = (student.id, teacher.id)
        rollbacks = []
        listener = lambda conn: rollbacks.append(1)  # noqa: E731
        event.listen(db.engine, "rollback", listener)
        try:
            tx.lock_message_chain(relationship, user_ids)
        finally:
            event.remove(db.engine, "rollback", listener)
    assert len(rollbacks) <= 1


def test_the_chain_uses_the_shared_hierarchy_and_group_lock_helpers(app, monkeypatch):
    calls = []
    real_hierarchy = tx.lock_academic_hierarchy
    real_group = tx.lock_group_in_open_transaction

    def spy_hierarchy(**kwargs):
        calls.append(("hierarchy", kwargs))
        return real_hierarchy(**kwargs)

    def spy_group(group_public_id):
        calls.append(("group", group_public_id))
        return real_group(group_public_id)

    monkeypatch.setattr(tx, "lock_academic_hierarchy", spy_hierarchy)
    monkeypatch.setattr(tx, "lock_group_in_open_transaction", spy_group)
    with app.app_context():
        student, teacher, group = fx.pair()
        term, level, course = fx.ancestors(group)
        relationship = message_queries.shared_relationship(student.id, teacher.id)
        locks = tx.lock_message_chain(relationship, (teacher.id, student.id))
        assert calls == [
            ("hierarchy", {"term_ids": [term.id], "level_ids": [level.id],
                           "course_ids": [course.id]}),
            ("group", group.public_id),
        ]
        assert locks.group.id == group.id
        assert set(locks.users) == {student.id, teacher.id}


@pytest.mark.parametrize("teacher_first", [True, False])
def test_user_rows_are_locked_in_ascending_id_whoever_sends(app, monkeypatch, teacher_first):
    with app.app_context():
        group = fx.hierarchy("Order")
        if teacher_first:
            teacher = fx.user("teacher@example.com", fx.TEACHER)
            student = fx.user("student@example.com", fx.STUDENT)
        else:
            student = fx.user("student@example.com", fx.STUDENT)
            teacher = fx.user("teacher@example.com", fx.TEACHER)
        fx.enroll(group, student)
        fx.assign(group, teacher)
        sid, tid = student.id, teacher.id
        thread_id = fx.thread(student, teacher).id
        orders = []
        for sender, other in ((sid, tid), (tid, sid)):
            seen, outcome = _record(
                app,
                lambda: tx.send_new_thread(sender, other, sid, tid, "S", "B", fx.nonce()),
                _locked_user_id,
            )
            assert outcome.status == tx.SENT
            orders.append(seen)
            seen, outcome = _record(
                app,
                lambda: tx.send_reply(sender, other, sid, tid, thread_id, "R", fx.nonce()),
                _locked_user_id,
            )
            assert outcome.status == tx.SENT
            orders.append(seen)
    assert orders == [sorted((sid, tid))] * 4


# ===========================================================================
# Post-lock proofs
# ===========================================================================


def test_the_relationship_is_chosen_deterministically_by_lowest_group(app):
    with app.app_context():
        student = fx.user("student@example.com", fx.STUDENT)
        teacher = fx.user("teacher@example.com", fx.TEACHER)
        groups = [fx.hierarchy(f"G{i}") for i in range(3)]
        for group in reversed(groups):
            fx.enroll(group, student)
            fx.assign(group, teacher)
        chosen = {message_queries.shared_relationship(student.id, teacher.id) for _ in range(3)}
        assert len(chosen) == 1
        assert chosen.pop().group_id == min(g.id for g in groups)
        fx.set_status(groups[0], fx.ARCHIVED)
        assert message_queries.shared_relationship(student.id, teacher.id).group_id == groups[1].id


def test_the_relationship_query_requires_the_student_teacher_orientation(app):
    with app.app_context():
        student, teacher, _ = fx.pair()
        assert message_queries.shared_relationship(student.id, teacher.id) is not None
        assert message_queries.shared_relationship(teacher.id, student.id) is None
        assert message_queries.orient_pair(student.id, fx.STUDENT, teacher.id, fx.TEACHER) == (
            student.id, teacher.id)
        assert message_queries.orient_pair(teacher.id, fx.TEACHER, student.id, fx.STUDENT) == (
            student.id, teacher.id)
        assert message_queries.orient_pair(student.id, fx.STUDENT, teacher.id, fx.STUDENT) is None
        assert message_queries.orient_pair(student.id, fx.TEACHER, teacher.id, fx.TEACHER) is None
        assert message_queries.orient_pair(student.id, fx.ADMIN, teacher.id, fx.TEACHER) is None


def test_a_thread_with_a_third_member_or_the_wrong_pair_fails_the_proof(app):
    with app.app_context():
        student, teacher, group = fx.pair()
        intruder = fx.user("intruder@example.com", fx.STUDENT)
        fx.enroll(group, intruder)
        row = fx.thread(student, teacher)
        relationship = message_queries.shared_relationship(student.id, teacher.id)
        locks = tx.lock_message_chain(relationship, (student.id, teacher.id), row.id)
        assert tx.thread_proof_error(locks, row.id, student.id, teacher.id) is None
        assert tx.thread_proof_error(locks, row.id, student.id, intruder.id) == "members"
        assert tx.thread_proof_error(locks, row.id + 1, student.id, teacher.id) == "thread"
        db.session.rollback()
        db.session.add(MessageThreadMember(thread_id=row.id, user_id=intruder.id))
        db.session.commit()
        locks = tx.lock_message_chain(relationship, (student.id, teacher.id), row.id)
        assert tx.thread_proof_error(locks, row.id, student.id, teacher.id) == "members"
        db.session.rollback()
        before = fx.counts()
        outcome = tx.send_reply(student.id, teacher.id, student.id, teacher.id, row.id, "x",
                                fx.nonce())
        assert outcome.status == tx.UNAVAILABLE
        assert fx.counts() == before


def test_a_sender_outside_the_oriented_pair_is_refused_without_locking(app):
    with app.app_context():
        student, teacher, group = fx.pair()
        other = fx.user("other@example.com", fx.STUDENT)
        fx.enroll(group, other)
        row = fx.thread(student, teacher)
        assert tx.send_new_thread(other.id, teacher.id, student.id, teacher.id, "S", "B",
                                  fx.nonce()).status == tx.UNAVAILABLE
        assert tx.send_reply(other.id, teacher.id, student.id, teacher.id, row.id, "B",
                             fx.nonce()).status == tx.UNAVAILABLE
        assert fx.counts()["threads"] == 1 and fx.counts()["messages"] == 1


# ===========================================================================
# Races between the preview and the locks
# ===========================================================================


def _interleave(monkeypatch, mutate):
    """Run `mutate` -- a competing, committed change -- once, immediately
    before the first lock chain is taken."""
    real = tx.lock_message_chain
    calls = []

    def wrapper(relationship, user_ids, thread_id=None):
        calls.append(relationship)
        if len(calls) == 1:
            mutate()
        return real(relationship, user_ids, thread_id)

    monkeypatch.setattr(tx, "lock_message_chain", wrapper)
    return calls


def _mutation(kind, ids):
    def mutate():
        if kind == "enrollment_withdrawn":
            row = db.session.get(Enrollment, ids["enrollment"])
            row.status = EnrollmentStatus.WITHDRAWN.value
        elif kind == "assignment_removed":
            row = db.session.get(GroupTeacherAssignment, ids["assignment"])
            row.status = GroupTeacherAssignmentStatus.REMOVED.value
        elif kind in ("group", "course", "level", "term"):
            model = {"group": Group, "course": Course}.get(kind)
            if model is None:
                from app.models import AcademicTerm, Level

                model = {"level": Level, "term": AcademicTerm}[kind]
            db.session.get(model, ids[kind]).status = AcademicStatus.ARCHIVED.value
        elif kind == "reparented_to_archived_course":
            archived = Course(title="Archived", level_id=ids["level"], display_order=99,
                              status=AcademicStatus.ARCHIVED.value)
            db.session.add(archived)
            db.session.flush()
            db.session.get(Group, ids["group"]).course_id = archived.id
        elif kind in ("student_suspended", "teacher_suspended"):
            user = db.session.get(User, ids[kind.split("_")[0]])
            user.status = UserStatus.SUSPENDED.value
        elif kind == "teacher_role_changed":
            db.session.get(User, ids["teacher"]).role = fx.RESEARCHER
        else:  # pragma: no cover
            raise AssertionError(kind)
        db.session.commit()

    return mutate


_RACES = [
    "enrollment_withdrawn",
    "assignment_removed",
    "group",
    "course",
    "level",
    "term",
    "reparented_to_archived_course",
    "student_suspended",
    "teacher_suspended",
    "teacher_role_changed",
]


def _race_world():
    student, teacher, group = fx.pair()
    term, level, course = fx.ancestors(group)
    return {
        "student": student.id,
        "teacher": teacher.id,
        "group": group.id,
        "course": course.id,
        "level": level.id,
        "term": term.id,
        "enrollment": fx.enrollment_of(group, student).id,
        "assignment": fx.assignment_of(group, teacher).id,
    }


@pytest.mark.parametrize("kind", _RACES)
def test_a_competing_change_before_the_locks_cannot_authorize_a_new_thread(app, monkeypatch, kind):
    with app.app_context():
        ids = _race_world()
        calls = _interleave(monkeypatch, _mutation(kind, ids))
        outcome = tx.send_new_thread(ids["student"], ids["teacher"], ids["student"],
                                     ids["teacher"], "Subject", "Body", fx.nonce())
        assert outcome.status == tx.UNAVAILABLE
        assert len(calls) == 1
        assert fx.counts() == {"threads": 0, "members": 0, "messages": 0, "notifications": 0}


@pytest.mark.parametrize("kind", _RACES)
def test_a_competing_change_before_the_locks_cannot_authorize_a_reply(app, monkeypatch, kind):
    with app.app_context():
        ids = _race_world()
        student = db.session.get(User, ids["student"])
        teacher = db.session.get(User, ids["teacher"])
        thread_id = fx.thread(student, teacher).id
        before = fx.counts()
        _interleave(monkeypatch, _mutation(kind, ids))
        outcome = tx.send_reply(ids["teacher"], ids["student"], ids["student"], ids["teacher"],
                                thread_id, "Reply", fx.nonce())
        assert outcome.status == tx.UNAVAILABLE
        assert fx.counts() == before


def test_a_still_shared_second_group_serves_when_the_first_ends_mid_send(app, monkeypatch):
    with app.app_context():
        ids = _race_world()
        second = fx.hierarchy("Second")
        fx.enroll(second, db.session.get(User, ids["student"]))
        fx.assign(second, db.session.get(User, ids["teacher"]))
        second_id = second.id
        calls = _interleave(monkeypatch, _mutation("group", ids))
        outcome = tx.send_new_thread(ids["student"], ids["teacher"], ids["student"],
                                     ids["teacher"], "Subject", "Body", fx.nonce())
        assert outcome.status == tx.SENT
        assert [c.group_id for c in calls] == [ids["group"], second_id]
        assert fx.counts()["threads"] == 1


def test_a_retarget_to_another_operational_course_is_re_proved_not_rejected(app, monkeypatch):
    with app.app_context():
        ids = _race_world()

        def retarget():
            course = Course(title="Moved", level_id=ids["level"], display_order=77,
                            status=AcademicStatus.ACTIVE.value)
            db.session.add(course)
            db.session.flush()
            db.session.get(Group, ids["group"]).course_id = course.id
            db.session.commit()

        calls = _interleave(monkeypatch, retarget)
        outcome = tx.send_new_thread(ids["student"], ids["teacher"], ids["student"],
                                     ids["teacher"], "Subject", "Body", fx.nonce())
        assert outcome.status == tx.SENT
        assert len(calls) == 2 and calls[0].course_id != calls[1].course_id


def test_a_proof_that_keeps_failing_is_bounded_and_never_writes(app, monkeypatch):
    with app.app_context():
        ids = _race_world()
        calls = _interleave(monkeypatch, lambda: None)
        monkeypatch.setattr(tx, "relationship_proof_error", lambda *args: "forced")
        outcome = tx.send_new_thread(ids["student"], ids["teacher"], ids["student"],
                                     ids["teacher"], "Subject", "Body", fx.nonce())
        assert outcome.status == tx.UNAVAILABLE
        assert 1 <= len(calls) <= tx.MAX_ATTEMPTS
        assert fx.counts()["threads"] == 0


# ===========================================================================
# IntegrityError: roll back first, decide from a fresh scoped read
# ===========================================================================


def test_a_create_losing_a_nonce_race_to_another_user_rolls_back_everything(app, monkeypatch):
    with app.app_context():
        student, teacher, group = fx.pair()
        peer = fx.user("peer@example.com", fx.STUDENT)
        fx.enroll(group, peer)
        existing = fx.thread(peer, teacher)
        before = fx.counts()
        monkeypatch.setattr(tx, "_nonce_used_by_thread", lambda nonce: False)
        outcome = tx.send_new_thread(student.id, teacher.id, student.id, teacher.id, "S", "B",
                                     existing.creation_nonce)
        assert outcome == tx.SendOutcome(tx.CONFLICT, None, None)
        assert fx.counts() == before


def test_a_create_losing_a_nonce_race_to_itself_resolves_to_its_own_thread(app, monkeypatch):
    with app.app_context():
        student, teacher, _ = fx.pair()
        existing = fx.thread(student, teacher)
        before = fx.counts()
        monkeypatch.setattr(tx, "_nonce_used_by_thread", lambda nonce: False)
        outcome = tx.send_new_thread(student.id, teacher.id, student.id, teacher.id, "S", "B",
                                     existing.creation_nonce)
        assert outcome == tx.SendOutcome(tx.DUPLICATE, existing.public_id, None)
        assert fx.counts() == before


def test_a_create_whose_message_insert_fails_leaves_no_thread_or_members(app, monkeypatch):
    with app.app_context():
        student, teacher, _ = fx.pair()
        existing = fx.thread(teacher, student)
        message_nonce = Message.query.one().creation_nonce
        before = fx.counts()
        # A fresh thread nonce, but a message nonce that already exists:
        # the thread and member inserts succeed in the flush, and the
        # message insert then fails inside the same transaction.
        real_message = tx.Message

        def clashing_message(**kwargs):
            kwargs["creation_nonce"] = message_nonce
            return real_message(**kwargs)

        monkeypatch.setattr(tx, "Message", clashing_message)
        outcome = tx.send_new_thread(student.id, teacher.id, student.id, teacher.id, "S", "B",
                                     fx.nonce())
        assert outcome.status == tx.CONFLICT
        assert fx.counts() == before
        assert MessageThread.query.filter(MessageThread.id != existing.id).count() == 0


def test_a_reply_losing_a_nonce_race_rolls_back_and_reports_conflict_or_duplicate(app, monkeypatch):
    with app.app_context():
        student, teacher, group = fx.pair()
        row = fx.thread(student, teacher)
        foreign = Message.query.one()
        own = fx.add_message(row, teacher, body="Mine")
        before = fx.counts()
        monkeypatch.setattr(tx, "_nonce_used_by_message", lambda nonce: False)
        conflict = tx.send_reply(teacher.id, student.id, student.id, teacher.id, row.id, "x",
                                 foreign.creation_nonce)
        assert conflict.status == tx.CONFLICT
        duplicate = tx.send_reply(teacher.id, student.id, student.id, teacher.id, row.id, "x",
                                  own.creation_nonce)
        assert duplicate.status == tx.DUPLICATE
        assert fx.counts() == before


def test_an_already_used_nonce_is_caught_under_the_locks_without_an_insert(app):
    with app.app_context():
        student, teacher, _ = fx.pair()
        row = fx.thread(student, teacher)
        seen, outcome = _record(
            app,
            lambda: tx.send_new_thread(student.id, teacher.id, student.id, teacher.id, "S", "B",
                                       row.creation_nonce),
            lambda flat, _p: flat if flat.startswith("INSERT") else None,
        )
        assert outcome.status == tx.DUPLICATE
        assert seen == []
