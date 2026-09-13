"""Phase 4 / M12 -- the discussion lock chains, the post-lock proofs, the
races they close, and IntegrityError handling.

SQLite honours neither ``FOR UPDATE`` nor REPEATABLE READ, so the
structural tests assert what the code *requests* -- the tables, their
order and the single reset -- and the race tests commit a competing change
at the exact point between the non-locking preview and the locks. None of
this proves real InnoDB blocking.
"""

import re

import pytest
from sqlalchemy import event

import tests.discussion_fixtures as fx
from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Course,
    DiscussionReply,
    DiscussionTopic,
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    User,
    UserStatus,
)
from app.services import discussion_queries
from app.services import discussion_transactions as tx


def _record(call, predicate):
    seen = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        value = predicate(" ".join(statement.split()), parameters)
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


def _ordered_tables(call):
    seen, _ = _record(call, _first_table)
    ordered = []
    for name in seen:
        if name not in ordered:
            ordered.append(name)
    return ordered


def _world():
    teacher, student, group = fx.classroom()
    term, level, course = fx.ancestors(group)
    row = fx.topic(group, teacher)
    return {
        "teacher": teacher.id,
        "student": student.id,
        "group": group.id,
        "group_public_id": group.public_id,
        "course": course.id,
        "level": level.id,
        "term": term.id,
        "enrollment": fx.enrollment_of(group, student).id,
        "assignment": fx.assignment_of(group, teacher).id,
        "topic": row.id,
        "topic_public_id": row.public_id,
    }


def _topic(ids):
    return db.session.get(DiscussionTopic, ids["topic"])


_PREFIX = ["academic_terms", "levels", "courses", "groups", "users"]


# ===========================================================================
# Structural lock order
# ===========================================================================


def test_the_creation_chain_requests_the_documented_order(app):
    with app.app_context():
        ids = _world()
        target = discussion_queries.lock_target(ids["group_public_id"])
        ordered = _ordered_tables(
            lambda: tx.lock_discussion_chain(target, ids["teacher"], fx.TEACHER)
        )
    assert ordered == _PREFIX + ["group_teacher_assignments"]


def test_a_student_reply_locks_the_enrollment_then_the_topic(app):
    with app.app_context():
        ids = _world()
        target = discussion_queries.lock_target(ids["group_public_id"])
        ordered = _ordered_tables(
            lambda: tx.lock_discussion_chain(target, ids["student"], fx.STUDENT, ids["topic"])
        )
    assert ordered == _PREFIX + ["enrollments", "discussion_topics"]


def test_a_teacher_reply_and_moderation_lock_the_assignment_then_the_topic(app):
    with app.app_context():
        ids = _world()
        target = discussion_queries.lock_target(ids["group_public_id"])
        ordered = _ordered_tables(
            lambda: tx.lock_discussion_chain(target, ids["teacher"], fx.TEACHER, ids["topic"])
        )
    assert ordered == _PREFIX + ["group_teacher_assignments", "discussion_topics"]


def test_the_chain_resets_once_through_the_shared_helpers(app, monkeypatch):
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
        ids = _world()
        target = discussion_queries.lock_target(ids["group_public_id"])
        rollbacks = []
        listener = lambda conn: rollbacks.append(1)  # noqa: E731
        event.listen(db.engine, "rollback", listener)
        try:
            locks = tx.lock_discussion_chain(target, ids["student"], fx.STUDENT, ids["topic"])
        finally:
            event.remove(db.engine, "rollback", listener)
        assert calls == [
            ("hierarchy", {"term_ids": [ids["term"]], "level_ids": [ids["level"]],
                           "course_ids": [ids["course"]]}),
            ("group", ids["group_public_id"]),
        ]
        assert len(rollbacks) <= 1
        assert locks.group.id == ids["group"]
        assert locks.actor.id == ids["student"]
        assert locks.relationship.id == ids["enrollment"]
        assert locks.topic.id == ids["topic"]
        db.session.rollback()


def test_each_write_requests_its_route_specific_chain(app, monkeypatch):
    requested = []
    real = tx.lock_discussion_chain

    def spy(target, actor_id, role, topic_id=None):
        requested.append((actor_id, role, topic_id))
        return real(target, actor_id, role, topic_id)

    monkeypatch.setattr(tx, "lock_discussion_chain", spy)
    with app.app_context():
        ids = _world()
        gp, t, s, topic_id = ids["group_public_id"], ids["teacher"], ids["student"], ids["topic"]
        assert tx.create_topic(t, gp, "T", "B", fx.nonce()).status == tx.CREATED
        assert tx.create_reply(s, fx.STUDENT, gp, topic_id, 1, "R", fx.nonce()).status == tx.CREATED
        assert tx.create_reply(t, fx.TEACHER, gp, topic_id, 1, "R", fx.nonce()).status == tx.CREATED
        assert tx.moderate_topic(t, gp, topic_id, "lock", 1).status == tx.CHANGED
    assert requested == [
        (t, fx.TEACHER, None),
        (s, fx.STUDENT, topic_id),
        (t, fx.TEACHER, topic_id),
        (t, fx.TEACHER, topic_id),
    ]


def test_no_transaction_is_left_open_after_a_write_decides(app):
    with app.app_context():
        ids = _world()
        gp, t, s, topic_id = ids["group_public_id"], ids["teacher"], ids["student"], ids["topic"]
        outcomes = [
            tx.create_topic(t, gp, "T", "B", fx.nonce()),
            tx.create_reply(s, fx.STUDENT, gp, topic_id, 1, "R", fx.nonce()),
            tx.create_reply(s, fx.STUDENT, gp, topic_id, 9, "R", fx.nonce()),
            tx.create_reply(s, fx.TEACHER, gp, topic_id, 1, "R", fx.nonce()),
            tx.moderate_topic(t, gp, topic_id, "lock", 1),
            tx.moderate_topic(t, gp, topic_id, "lock", 1),
            tx.create_reply(s, fx.STUDENT, gp, topic_id, 2, "R", fx.nonce()),
        ]
        assert [outcome.status for outcome in outcomes] == [
            tx.CREATED, tx.CREATED, tx.STALE, tx.UNAVAILABLE, tx.CHANGED, tx.ALREADY,
            tx.TOPIC_LOCKED,
        ]
        assert not db.session().in_transaction()


# ===========================================================================
# Post-lock proofs
# ===========================================================================


def test_the_access_proof_names_each_broken_link(app):
    with app.app_context():
        ids = _world()
        target = discussion_queries.lock_target(ids["group_public_id"])

        def proof(actor_id, role):
            locks = tx.lock_discussion_chain(target, actor_id, role)
            try:
                return tx.access_proof_error(locks, target, actor_id, role)
            finally:
                db.session.rollback()

        assert proof(ids["teacher"], fx.TEACHER) is None
        assert proof(ids["student"], fx.STUDENT) is None
        assert proof(ids["student"], fx.TEACHER) == "account"
        assert proof(ids["teacher"], fx.STUDENT) == "account"
        assert proof(ids["teacher"], fx.ADMIN) == "account"

        fx.set_status(db.session.get(Enrollment, ids["enrollment"]),
                      EnrollmentStatus.WITHDRAWN.value)
        assert proof(ids["student"], fx.STUDENT) == "relationship"
        fx.set_status(db.session.get(GroupTeacherAssignment, ids["assignment"]),
                      GroupTeacherAssignmentStatus.REMOVED.value)
        assert proof(ids["teacher"], fx.TEACHER) == "relationship"
        fx.set_status(db.session.get(GroupTeacherAssignment, ids["assignment"]),
                      GroupTeacherAssignmentStatus.ACTIVE.value)
        fx.set_status(db.session.get(User, ids["teacher"]), UserStatus.SUSPENDED.value)
        assert proof(ids["teacher"], fx.TEACHER) == "account"
        fx.set_status(db.session.get(User, ids["teacher"]), UserStatus.ACTIVE.value)
        fx.set_status(db.session.get(Level, ids["level"]), fx.ARCHIVED)
        assert proof(ids["teacher"], fx.TEACHER) == "not_operational"
        fx.set_status(db.session.get(Level, ids["level"]), fx.ACTIVE)
        assert proof(99999, fx.TEACHER) == "account"


def test_the_topic_proof_requires_the_locked_topic_of_the_locked_group(app):
    with app.app_context():
        ids = _world()
        teacher = db.session.get(User, ids["teacher"])
        other = fx.hierarchy("Other")
        fx.assign(other, teacher)
        foreign = fx.topic(other, teacher, title="Elsewhere")
        target = discussion_queries.lock_target(ids["group_public_id"])
        locks = tx.lock_discussion_chain(target, ids["teacher"], fx.TEACHER, ids["topic"])
        assert tx.topic_proof_error(locks, ids["topic"]) is None
        assert tx.topic_proof_error(locks, ids["topic"] + 1000) == "topic"
        db.session.rollback()
        locks = tx.lock_discussion_chain(target, ids["teacher"], fx.TEACHER, foreign.id)
        assert tx.topic_proof_error(locks, foreign.id) == "topic"
        db.session.rollback()
        before = fx.counts()
        assert tx.create_reply(ids["teacher"], fx.TEACHER, ids["group_public_id"], foreign.id,
                               1, "R", fx.nonce()).status == tx.UNAVAILABLE
        assert tx.moderate_topic(ids["teacher"], ids["group_public_id"], foreign.id, "lock",
                                 1).status == tx.UNAVAILABLE
        assert fx.counts() == before
        assert db.session.get(DiscussionTopic, foreign.id).status == fx.OPEN


def test_an_unknown_action_is_refused_without_locking(app, monkeypatch):
    with app.app_context():
        ids = _world()
        monkeypatch.setattr(tx, "lock_discussion_chain",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("locked")))
        outcome = tx.moderate_topic(ids["teacher"], ids["group_public_id"], ids["topic"],
                                    "delete", 1)
        assert outcome.status == tx.UNAVAILABLE
        assert _topic(ids).status == fx.OPEN


# ===========================================================================
# Races between the preview and the locks
# ===========================================================================


def _interleave(monkeypatch, mutate):
    """Run `mutate` -- a competing, committed change -- once, immediately
    before the first lock chain is taken."""
    real = tx.lock_discussion_chain
    calls = []

    def wrapper(target, actor_id, role, topic_id=None):
        calls.append(target)
        if len(calls) == 1:
            mutate()
        return real(target, actor_id, role, topic_id)

    monkeypatch.setattr(tx, "lock_discussion_chain", wrapper)
    return calls


def _mutation(kind, ids):
    def mutate():
        if kind == "enrollment_withdrawn":
            db.session.get(Enrollment, ids["enrollment"]).status = EnrollmentStatus.WITHDRAWN.value
        elif kind == "assignment_removed":
            db.session.get(GroupTeacherAssignment, ids["assignment"]).status = (
                GroupTeacherAssignmentStatus.REMOVED.value
            )
        elif kind in ("group", "course", "level", "term"):
            model = {"group": Group, "course": Course, "level": Level, "term": AcademicTerm}[kind]
            db.session.get(model, ids[kind]).status = AcademicStatus.ARCHIVED.value
        elif kind == "reparented_to_archived_course":
            archived = Course(title="Archived", level_id=ids["level"], display_order=99,
                              status=AcademicStatus.ARCHIVED.value)
            db.session.add(archived)
            db.session.flush()
            db.session.get(Group, ids["group"]).course_id = archived.id
        elif kind in ("student_suspended", "teacher_suspended"):
            db.session.get(User, ids[kind.split("_")[0]]).status = UserStatus.SUSPENDED.value
        elif kind in ("student_role_changed", "teacher_role_changed"):
            db.session.get(User, ids[kind.split("_")[0]]).role = fx.RESEARCHER
        elif kind == "topic_locked":
            row = db.session.get(DiscussionTopic, ids["topic"])
            row.status, row.version = fx.LOCKED, row.version + 1
        elif kind == "topic_locked_and_reopened":
            row = db.session.get(DiscussionTopic, ids["topic"])
            row.version = row.version + 2
        else:  # pragma: no cover
            raise AssertionError(kind)
        db.session.commit()

    return mutate


_CHAIN = ["group", "course", "level", "term", "reparented_to_archived_course"]
_TEACHER_RACES = _CHAIN + ["assignment_removed", "teacher_suspended", "teacher_role_changed"]
_STUDENT_RACES = _CHAIN + ["enrollment_withdrawn", "student_suspended", "student_role_changed"]


@pytest.mark.parametrize("kind", _TEACHER_RACES)
def test_a_competing_change_before_the_locks_cannot_authorize_a_topic(app, monkeypatch, kind):
    with app.app_context():
        ids = _world()
        before = fx.counts()
        calls = _interleave(monkeypatch, _mutation(kind, ids))
        outcome = tx.create_topic(ids["teacher"], ids["group_public_id"], "T", "B", fx.nonce())
        assert outcome.status == tx.UNAVAILABLE
        # A reparent proves "moved" and is previewed once more -- onto an
        # archived Course, which is then refused; every other change is
        # refused on the first proof.
        expected_calls = 2 if kind == "reparented_to_archived_course" else 1
        assert len(calls) == expected_calls <= tx.MAX_ATTEMPTS
        assert fx.counts() == before


@pytest.mark.parametrize("kind", _STUDENT_RACES)
def test_a_competing_change_before_the_locks_cannot_authorize_a_student_reply(
        app, monkeypatch, kind):
    with app.app_context():
        ids = _world()
        before = fx.counts()
        _interleave(monkeypatch, _mutation(kind, ids))
        outcome = tx.create_reply(ids["student"], fx.STUDENT, ids["group_public_id"],
                                  ids["topic"], 1, "R", fx.nonce())
        assert outcome.status == tx.UNAVAILABLE
        assert fx.counts() == before


@pytest.mark.parametrize("kind", _TEACHER_RACES)
def test_a_competing_change_before_the_locks_cannot_authorize_a_teacher_reply_or_moderation(
        app, monkeypatch, kind):
    with app.app_context():
        ids = _world()
        before = fx.counts()
        _interleave(monkeypatch, _mutation(kind, ids))
        outcome = tx.create_reply(ids["teacher"], fx.TEACHER, ids["group_public_id"],
                                  ids["topic"], 1, "R", fx.nonce())
        assert outcome.status == tx.UNAVAILABLE
        assert fx.counts() == before
    with app.app_context():
        monkeypatch.undo()
        ids = _world_again()
        _interleave(monkeypatch, _mutation(kind, ids))
        outcome = tx.moderate_topic(ids["teacher"], ids["group_public_id"], ids["topic"],
                                    "lock", 1)
        assert outcome.status == tx.UNAVAILABLE
        assert _topic(ids).status == fx.OPEN and _topic(ids).version == 1


def _world_again():
    """A second, independent classroom in the same database."""
    teacher = fx.user("teacher2@example.com", fx.TEACHER)
    student = fx.user("student2@example.com", fx.STUDENT)
    group = fx.hierarchy("Second")
    fx.assign(group, teacher)
    fx.enroll(group, student)
    term, level, course = fx.ancestors(group)
    row = fx.topic(group, teacher)
    return {
        "teacher": teacher.id, "student": student.id, "group": group.id,
        "group_public_id": group.public_id, "course": course.id, "level": level.id,
        "term": term.id, "enrollment": fx.enrollment_of(group, student).id,
        "assignment": fx.assignment_of(group, teacher).id, "topic": row.id,
        "topic_public_id": row.public_id,
    }


@pytest.mark.parametrize("role", [fx.STUDENT, fx.TEACHER])
def test_a_lock_that_wins_the_topic_row_refuses_the_reply(app, monkeypatch, role):
    with app.app_context():
        ids = _world()
        before = fx.counts()
        _interleave(monkeypatch, _mutation("topic_locked", ids))
        actor = ids["student"] if role == fx.STUDENT else ids["teacher"]
        outcome = tx.create_reply(actor, role, ids["group_public_id"], ids["topic"], 1, "R",
                                  fx.nonce())
        assert outcome.status == tx.TOPIC_LOCKED
        assert fx.counts() == before


def test_a_lock_and_reopen_since_the_form_make_a_reply_stale(app, monkeypatch):
    with app.app_context():
        ids = _world()
        before = fx.counts()
        _interleave(monkeypatch, _mutation("topic_locked_and_reopened", ids))
        outcome = tx.create_reply(ids["student"], fx.STUDENT, ids["group_public_id"],
                                  ids["topic"], 1, "R", fx.nonce())
        assert outcome.status == tx.STALE
        assert fx.counts() == before
        assert tx.create_reply(ids["student"], fx.STUDENT, ids["group_public_id"], ids["topic"],
                               3, "R", fx.nonce()).status == tx.CREATED


def test_moderation_racing_moderation_is_a_no_op_or_stale_never_a_double_change(
        app, monkeypatch):
    with app.app_context():
        ids = _world()
        _interleave(monkeypatch, _mutation("topic_locked", ids))
        assert tx.moderate_topic(ids["teacher"], ids["group_public_id"], ids["topic"], "lock",
                                 1).status == tx.ALREADY
        assert (_topic(ids).status, _topic(ids).version) == (fx.LOCKED, 2)
        monkeypatch.undo()
        _interleave(monkeypatch, _mutation("topic_locked_and_reopened", ids))
        # Now locked at version 4 after a further unlock-shaped version bump:
        # a reopen token for version 2 is stale.
        row = _topic(ids)
        assert (row.status, row.version) == (fx.LOCKED, 2)
        outcome = tx.moderate_topic(ids["teacher"], ids["group_public_id"], ids["topic"],
                                    "reopen", 2)
        assert outcome.status == tx.STALE
        assert (_topic(ids).status, _topic(ids).version) == (fx.LOCKED, 4)


def test_a_retarget_to_another_operational_course_is_re_proved_not_rejected(app, monkeypatch):
    with app.app_context():
        ids = _world()

        def retarget():
            course = Course(title="Moved", level_id=ids["level"], display_order=77,
                            status=AcademicStatus.ACTIVE.value)
            db.session.add(course)
            db.session.flush()
            db.session.get(Group, ids["group"]).course_id = course.id
            db.session.commit()

        calls = _interleave(monkeypatch, retarget)
        outcome = tx.create_topic(ids["teacher"], ids["group_public_id"], "T", "B", fx.nonce())
        assert outcome.status == tx.CREATED
        assert len(calls) == 2 and calls[0].course_id != calls[1].course_id


def test_a_proof_that_keeps_failing_is_bounded_and_never_writes(app, monkeypatch):
    with app.app_context():
        ids = _world()
        calls = _interleave(monkeypatch, lambda: None)
        monkeypatch.setattr(tx, "access_proof_error", lambda *args: "moved")
        outcome = tx.create_topic(ids["teacher"], ids["group_public_id"], "T", "B", fx.nonce())
        assert outcome.status == tx.UNAVAILABLE
        assert 1 <= len(calls) <= tx.MAX_ATTEMPTS
        assert fx.counts()["topics"] == 1


def test_another_students_withdrawal_does_not_block_the_class(app, monkeypatch):
    with app.app_context():
        ids = _world()
        group = db.session.get(Group, ids["group"])
        peer = fx.user("peer@example.com", fx.STUDENT)
        peer_enrollment = fx.enroll(group, peer).id

        def withdraw_peer():
            db.session.get(Enrollment, peer_enrollment).status = EnrollmentStatus.WITHDRAWN.value
            db.session.commit()

        _interleave(monkeypatch, withdraw_peer)
        assert tx.create_reply(ids["student"], fx.STUDENT, ids["group_public_id"], ids["topic"],
                               1, "R", fx.nonce()).status == tx.CREATED


# ===========================================================================
# Duplicate requests and IntegrityError: roll back first, re-authorize,
# then decide from a fresh scoped read
# ===========================================================================


def test_two_requests_carrying_one_creation_nonce_create_one_topic(app):
    with app.app_context():
        ids = _world()
        shared = fx.nonce()
        first = tx.create_topic(ids["teacher"], ids["group_public_id"], "T", "B", shared)
        second = tx.create_topic(ids["teacher"], ids["group_public_id"], "T", "B", shared)
        assert first.status == tx.CREATED
        assert second == tx.Outcome(tx.DUPLICATE, first.public_id, None)
        assert fx.counts()["topics"] == 2


def test_two_requests_carrying_one_reply_nonce_append_one_reply(app):
    with app.app_context():
        ids = _world()
        shared = fx.nonce()
        args = (ids["student"], fx.STUDENT, ids["group_public_id"], ids["topic"], 1, "R", shared)
        first = tx.create_reply(*args)
        second = tx.create_reply(*args)
        assert first.status == tx.CREATED
        assert second == tx.Outcome(tx.DUPLICATE, first.public_id, None)
        assert fx.counts()["replies"] == 1


def test_a_reply_replay_after_a_lock_still_resolves_to_the_posted_reply(app):
    with app.app_context():
        ids = _world()
        shared = fx.nonce()
        args = (ids["student"], fx.STUDENT, ids["group_public_id"], ids["topic"], 1, "R", shared)
        first = tx.create_reply(*args)
        assert tx.moderate_topic(ids["teacher"], ids["group_public_id"], ids["topic"], "lock",
                                 1).status == tx.CHANGED
        assert tx.create_reply(*args) == tx.Outcome(tx.DUPLICATE, first.public_id, None)
        assert tx.create_reply(*args[:-1], fx.nonce()).status == tx.TOPIC_LOCKED
        assert fx.counts()["replies"] == 1


def test_an_already_used_nonce_is_caught_under_the_locks_without_an_insert(app):
    with app.app_context():
        ids = _world()
        existing = _topic(ids).creation_nonce
        seen, outcome = _record(
            lambda: tx.create_topic(ids["teacher"], ids["group_public_id"], "T", "B", existing),
            lambda flat, _p: flat if flat.startswith("INSERT") else None,
        )
        assert outcome == tx.Outcome(tx.DUPLICATE, ids["topic_public_id"], None)
        assert seen == []


def test_a_creation_losing_a_nonce_race_to_a_co_teacher_rolls_back_to_conflict(
        app, monkeypatch):
    with app.app_context():
        ids = _world()
        group = db.session.get(Group, ids["group"])
        co_teacher = fx.user("co@example.com", fx.TEACHER)
        fx.assign(group, co_teacher)
        foreign = fx.topic(group, co_teacher, title="Theirs")
        before = fx.counts()
        monkeypatch.setattr(tx, "_topic_nonce_used", lambda nonce: False)
        outcome = tx.create_topic(ids["teacher"], ids["group_public_id"], "T", "B",
                                  foreign.creation_nonce)
        assert outcome == tx.Outcome(tx.CONFLICT, None, None)
        assert fx.counts() == before


def test_a_creation_losing_a_nonce_race_to_itself_resolves_to_its_own_topic(app, monkeypatch):
    with app.app_context():
        ids = _world()
        before = fx.counts()
        monkeypatch.setattr(tx, "_topic_nonce_used", lambda nonce: False)
        outcome = tx.create_topic(ids["teacher"], ids["group_public_id"], "T", "B",
                                  _topic(ids).creation_nonce)
        assert outcome == tx.Outcome(tx.DUPLICATE, ids["topic_public_id"], None)
        assert fx.counts() == before


def test_an_own_nonce_from_another_group_is_a_conflict_not_a_redirect(app, monkeypatch):
    with app.app_context():
        ids = _world()
        teacher = db.session.get(User, ids["teacher"])
        other = fx.hierarchy("Other")
        fx.assign(other, teacher)
        elsewhere = fx.topic(other, teacher, title="Elsewhere")
        monkeypatch.setattr(tx, "_topic_nonce_used", lambda nonce: False)
        outcome = tx.create_topic(ids["teacher"], ids["group_public_id"], "T", "B",
                                  elsewhere.creation_nonce)
        assert outcome == tx.Outcome(tx.CONFLICT, None, None)


def test_a_replay_re_authorizes_from_current_state_before_redirecting(app, monkeypatch):
    with app.app_context():
        ids = _world()
        monkeypatch.setattr(tx, "_topic_nonce_used", lambda nonce: False)
        monkeypatch.setattr(tx.discussion_queries, "member_group", lambda *args: None)
        outcome = tx.create_topic(ids["teacher"], ids["group_public_id"], "T", "B",
                                  _topic(ids).creation_nonce)
        assert outcome == tx.Outcome(tx.UNAVAILABLE, None, None)


def test_a_reply_losing_a_nonce_race_reports_conflict_or_duplicate_only_for_its_own_reply(
        app, monkeypatch):
    with app.app_context():
        ids = _world()
        row = _topic(ids)
        teacher = db.session.get(User, ids["teacher"])
        student = db.session.get(User, ids["student"])
        foreign = fx.reply(row, teacher, body="Teacher's")
        own = fx.reply(row, student, body="Mine")
        other_topic = fx.topic(db.session.get(Group, ids["group"]), teacher, title="Second")
        before = fx.counts()
        monkeypatch.setattr(tx, "_reply_nonce_used", lambda nonce: False)
        gp = ids["group_public_id"]
        conflict = tx.create_reply(ids["student"], fx.STUDENT, gp, ids["topic"], 1, "x",
                                   foreign.creation_nonce)
        assert conflict == tx.Outcome(tx.CONFLICT, None, None)
        duplicate = tx.create_reply(ids["student"], fx.STUDENT, gp, ids["topic"], 1, "x",
                                    own.creation_nonce)
        assert duplicate == tx.Outcome(tx.DUPLICATE, own.public_id, None)
        cross_topic = tx.create_reply(ids["student"], fx.STUDENT, gp, other_topic.id, 1, "x",
                                      own.creation_nonce)
        assert cross_topic == tx.Outcome(tx.CONFLICT, None, None)
        monkeypatch.setattr(tx.discussion_queries, "member_group", lambda *args: None)
        lost = tx.create_reply(ids["student"], fx.STUDENT, gp, ids["topic"], 1, "x",
                               own.creation_nonce)
        assert lost == tx.Outcome(tx.UNAVAILABLE, None, None)
        assert fx.counts() == before


def test_a_moderation_write_failure_rolls_back_and_changes_nothing(app, monkeypatch):
    with app.app_context():
        ids = _world()
        monkeypatch.setattr(tx, "discussion_now", lambda: None)
        outcome = tx.moderate_topic(ids["teacher"], ids["group_public_id"], ids["topic"],
                                    "lock", 1)
        assert outcome == tx.Outcome(tx.CONFLICT, None, None)
        row = _topic(ids)
        assert (row.status, row.version, row.updated_at) == (fx.OPEN, 1, fx.NOW)


# ===========================================================================
# What a successful moderation writes
# ===========================================================================


def test_lock_and_reopen_move_status_version_and_updated_at_together(app):
    with app.app_context():
        ids = _world()
        gp = ids["group_public_id"]
        replies_before = fx.counts()["replies"]
        assert tx.moderate_topic(ids["teacher"], gp, ids["topic"], "lock", 1) == tx.Outcome(
            tx.CHANGED, None, ids["topic"])
        locked = _topic(ids)
        assert (locked.status, locked.version) == (fx.LOCKED, 2)
        assert locked.updated_at > locked.created_at and locked.updated_at.microsecond == 0
        assert tx.moderate_topic(ids["teacher"], gp, ids["topic"], "reopen", 2).status == tx.CHANGED
        reopened = _topic(ids)
        assert (reopened.status, reopened.version) == (fx.OPEN, 3)
        assert (reopened.title, reopened.body, reopened.created_at) == (
            "Weekend reading", "What did you read this weekend?", fx.NOW)
        assert fx.counts()["replies"] == replies_before
        assert DiscussionReply.query.count() == 0
