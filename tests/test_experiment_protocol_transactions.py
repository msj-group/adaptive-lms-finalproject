"""Phase 6 / M02A write path, review rules, text normalisation and tokens.

The transactions are called directly with explicit ``stale_check`` callables,
so each rule is proved without the route in the way: the lock chain and its
order, the aggregate version, the post-lock re-checks, the limits, the
activation's supersession and digest, derivation and discard.

**Structural only for locking.** SQLite honours neither ``FOR UPDATE`` nor
REPEATABLE READ, so these tests assert the *requested* lock set and order
and the re-checks performed after it. They prove nothing about real InnoDB
blocking or isolation.
"""

import time

import pytest
import sqlalchemy as sa
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Query

import tests.protocol_fixtures as px
import tests.research_fixtures as rx
from app.extensions import db
from app.models import (
    ExperimentDefinition,
    ExperimentTask,
    ExperimentTaskSet,
    UserStatus,
)
from app.services import experiment_protocol_text as text
from app.services import experiment_protocol_tokens as tokens
from app.services import experiment_protocol_transactions as tx
from app.services.experiment_protocol_queries import (
    preview_digest,
    protocol_content,
    protocol_header,
)
from app.services.experiment_protocol_review import (
    EMPTY_SET,
    MISSING_RATIONALE,
    NO_SETS,
    NOT_PARALLEL,
    UNEQUAL_SETS,
    content_digest,
    structural_problems,
)


def _fresh(_version):
    return False


def _fresh3(_version, _digest, _current):
    return False


@pytest.fixture
def people(app):
    return rx.world(app)


def _researcher(people):
    return people["researcher"].id


def _task(**overrides):
    return {**px.TASK, **overrides}


def _version_of(public_id):
    db.session.expire_all()
    return ExperimentDefinition.query.filter_by(public_id=public_id).one().version


def _draft(people, version="VA-1"):
    outcome, public_id = tx.create_protocol(_researcher(people), version, "Placeholder", None)
    assert outcome == tx.CREATED
    return public_id


def _with_set(people, public_id, code="SET-A"):
    outcome, set_public_id = tx.add_task_set(_researcher(people), public_id, code, "Set", _fresh)
    assert outcome == tx.CREATED
    return set_public_id


def _with_task(people, public_id, set_public_id, **overrides):
    outcome, task_public_id = tx.add_task(_researcher(people), public_id, set_public_id,
                                          _task(**overrides), _fresh)
    assert outcome == tx.CREATED
    return task_public_id


def _complete(people, version="VA-1", tasks=1):
    public_id = _draft(people, version)
    set_public_id = _with_set(people, public_id)
    for number in range(tasks):
        _with_task(people, public_id, set_public_id, title=f"Task {number + 1}")
    return public_id


def _snapshot():
    db.session.expire_all()
    return {
        "definitions": db.session.execute(sa.text(
            "SELECT * FROM experiment_definitions ORDER BY id")).fetchall(),
        "sets": db.session.execute(sa.text(
            "SELECT * FROM experiment_task_sets ORDER BY id")).fetchall(),
        "tasks": db.session.execute(sa.text(
            "SELECT * FROM experiment_tasks ORDER BY id")).fetchall(),
    }


# ===========================================================================
# The lock chain -- structural
# ===========================================================================


def _record_locks(monkeypatch, ids=None):
    """The reset point and every ``with_for_update`` request, in order. With
    `ids`, the locked ``(table, id)`` pairs are recorded there as well."""
    requested = []
    original_lock = Query.with_for_update
    original_reset = tx.lock_academic_hierarchy

    def spy(self, *args, **kwargs):
        table = self.column_descriptions[0]["entity"].__tablename__
        requested.append(table)
        if ids is not None:
            ids.append((table, self.whereclause.right.value))
        return original_lock(self, *args, **kwargs)

    def reset(*args, **kwargs):
        requested.append("reset")
        return original_reset(*args, **kwargs)

    monkeypatch.setattr(Query, "with_for_update", spy)
    monkeypatch.setattr(tx, "lock_academic_hierarchy", reset)
    return requested


def test_every_write_takes_the_documented_lock_order(app, people, monkeypatch):
    rid = _researcher(people)
    public_id = _complete(people, "VA-1", tasks=3)
    set_public_id = px.sets_of(px.definition("VA-1"))[0].public_id
    second_set = _with_set(people, public_id, "SET-B")
    task_public_id = px.tasks_of(px.sets_of(px.definition("VA-1"))[0])[1].public_id
    to_discard = _draft(people, "VA-8")
    requested = _record_locks(monkeypatch)

    def run(label, call, expected):
        requested.clear()
        call()
        assert requested == expected, (label, requested)

    run("create", lambda: tx.create_protocol(rid, "VA-9", "T", None), ["reset", "users"])
    run("edit", lambda: tx.update_protocol(rid, public_id, "VA-1", "Renamed", None, _fresh),
        ["reset", "users", "experiment_definitions"])
    run("add set", lambda: tx.add_task_set(rid, public_id, "SET-C", "T", _fresh),
        ["reset", "users", "experiment_definitions"])
    run("edit set", lambda: tx.update_task_set(rid, public_id, set_public_id, "SET-A", "R",
                                               _fresh),
        ["reset", "users", "experiment_definitions", "experiment_task_sets"])
    run("move set", lambda: tx.move_task_set(rid, public_id, second_set, tx.UP, _fresh),
        ["reset", "users", "experiment_definitions"] + ["experiment_task_sets"] * 3)
    run("add task", lambda: tx.add_task(rid, public_id, set_public_id, _task(), _fresh),
        ["reset", "users", "experiment_definitions", "experiment_task_sets"])
    run("edit task", lambda: tx.update_task(rid, public_id, set_public_id, task_public_id,
                                            _task(title="Renamed"), _fresh),
        ["reset", "users", "experiment_definitions", "experiment_task_sets",
         "experiment_tasks"])
    run("move task", lambda: tx.move_task(rid, public_id, set_public_id, task_public_id,
                                          tx.UP, _fresh),
        ["reset", "users", "experiment_definitions", "experiment_task_sets"]
        + ["experiment_tasks"] * 4)
    run("discard", lambda: tx.discard_protocol(rid, to_discard, _fresh),
        ["reset", "users", "experiment_definitions"])
    run("activate", lambda: tx.activate_protocol(rid, public_id, _fresh3),
        ["reset", "users", "experiment_definitions"])
    run("derive", lambda: tx.derive_protocol(rid, public_id, "VA-7", "T", lambda d: False),
        ["reset", "users", "experiment_definitions"])


def test_moves_lock_their_whole_list_ascending_after_the_definition(app, people, monkeypatch):
    rid = _researcher(people)
    public_id = _draft(people)
    set_public_ids = [_with_set(people, public_id, code) for code in ("SET-A", "SET-B", "SET-C")]
    # Reverse the stored order so ascending id and display order differ.
    tx.move_task_set(rid, public_id, set_public_ids[2], tx.UP, _fresh)
    tx.move_task_set(rid, public_id, set_public_ids[2], tx.UP, _fresh)
    locked = []
    _record_locks(monkeypatch, ids=locked)
    assert tx.move_task_set(rid, public_id, set_public_ids[0], tx.DOWN, _fresh) == tx.MOVED
    set_ids = [value for table, value in locked if table == "experiment_task_sets"]
    assert set_ids == sorted(set_ids) and len(set_ids) == 3
    assert [table for table, _ in locked][:2] == ["users", "experiment_definitions"]


def test_activation_locks_the_draft_and_the_current_version_ascending(app, people,
                                                                      monkeypatch):
    rid = _researcher(people)
    first = _complete(people, "VA-1")
    second = _complete(people, "VA-2")
    assert tx.activate_protocol(rid, second, _fresh3)[0] == tx.ACTIVATED
    first_id, second_id = px.definition("VA-1").id, px.definition("VA-2").id
    locked = []
    requested = _record_locks(monkeypatch, ids=locked)
    assert tx.activate_protocol(rid, first, _fresh3)[0] == tx.ACTIVATED
    assert requested == ["reset", "users", "experiment_definitions", "experiment_definitions"]
    assert locked[0] == ("users", rid)
    assert locked[1:] == [("experiment_definitions", first_id),
                          ("experiment_definitions", second_id)]
    assert first_id < second_id


def test_no_m02a_write_locks_or_reads_participant_or_consent_rows(app, people, monkeypatch):
    rid = _researcher(people)
    statements = []

    def recorder(_conn, _cursor, statement, *_args):
        statements.append(statement)

    sa_event.listen(db.engine, "before_cursor_execute", recorder)
    try:
        public_id = _complete(people, "VA-1", tasks=2)
        tx.activate_protocol(rid, public_id, _fresh3)
        tx.derive_protocol(rid, public_id, "VA-2", "T",
                           lambda digest: False)
        draft = px.definition("VA-2").public_id
        tx.discard_protocol(rid, draft, _fresh)
    finally:
        sa_event.remove(db.engine, "before_cursor_execute", recorder)
    joined = " ".join(statements)
    for table in ("research_participants", "research_consent", "enrollments", "groups",
                  "lessons", "quizzes", "assignments", "submissions", "invoices"):
        assert table not in joined, table


# ===========================================================================
# The aggregate version, and the post-lock re-checks
# ===========================================================================


def test_every_successful_change_moves_the_aggregate_version_exactly_once(app, people):
    rid = _researcher(people)
    public_id = _draft(people)
    assert _version_of(public_id) == 1
    set_a = _with_set(people, public_id, "SET-A")
    set_b = _with_set(people, public_id, "SET-B")
    assert _version_of(public_id) == 3
    task_1 = _with_task(people, public_id, set_a, title="One")
    _with_task(people, public_id, set_a, title="Two")
    assert _version_of(public_id) == 5
    steps = [
        lambda: tx.update_protocol(rid, public_id, "VA-1", "Renamed", None, _fresh),
        lambda: tx.update_task_set(rid, public_id, set_b, "SET-B", "Renamed", _fresh),
        lambda: tx.move_task_set(rid, public_id, set_b, tx.UP, _fresh),
        lambda: tx.update_task(rid, public_id, set_a, task_1, _task(title="Renamed"), _fresh),
        lambda: tx.move_task(rid, public_id, set_a, task_1, tx.DOWN, _fresh),
    ]
    for number, step in enumerate(steps, start=6):
        assert step() in (tx.SAVED, tx.MOVED)
        assert _version_of(public_id) == number
    # Unchanged values and refused moves write nothing and move nothing.
    assert tx.update_protocol(rid, public_id, "VA-1", "Renamed", None, _fresh) == tx.UNCHANGED
    assert tx.move_task(rid, public_id, set_a, task_1, tx.DOWN, _fresh) == tx.EDGE
    assert _version_of(public_id) == 10


def test_a_stale_check_runs_against_the_locked_version_and_writes_nothing(app, people):
    rid = _researcher(people)
    public_id = _complete(people)
    set_public_id = px.sets_of(px.definition("VA-1"))[0].public_id
    seen = []

    def stale(version):
        seen.append(version)
        return True

    before = _snapshot()
    for call in (
        lambda: tx.update_protocol(rid, public_id, "VA-1", "Renamed", None, stale),
        lambda: tx.add_task_set(rid, public_id, "SET-B", "T", stale)[0],
        lambda: tx.add_task(rid, public_id, set_public_id, _task(), stale)[0],
        lambda: tx.discard_protocol(rid, public_id, stale),
    ):
        assert call() == tx.STALE
    assert seen == [_version_of(public_id)] * 4
    assert _snapshot() == before


@pytest.mark.parametrize("change", ["suspended", "student", "administrator"])
def test_the_locked_actor_must_still_be_an_active_researcher(app, people, change):
    public_id = _complete(people)
    actor = people["researcher"]
    if change == "suspended":
        actor.status = UserStatus.SUSPENDED.value
        db.session.commit()
    else:
        actor = people["student" if change == "student" else "admin"]
    before = _snapshot()
    assert tx.create_protocol(actor.id, "VA-9", "T", None) == (tx.UNAUTHORIZED, None)
    assert tx.update_protocol(actor.id, public_id, "VA-1", "R", None, _fresh) == tx.UNAUTHORIZED
    assert tx.activate_protocol(actor.id, public_id, _fresh3) == (tx.UNAUTHORIZED, ())
    assert tx.discard_protocol(actor.id, public_id, _fresh) == tx.UNAUTHORIZED
    assert _snapshot() == before


def test_a_nested_object_is_only_found_inside_its_own_parent(app, people):
    rid = _researcher(people)
    first = _complete(people, "VA-1")
    second = _complete(people, "VA-2")
    foreign_set = px.sets_of(px.definition("VA-2"))[0].public_id
    foreign_task = px.tasks_of(px.sets_of(px.definition("VA-2"))[0])[0].public_id
    own_set = px.sets_of(px.definition("VA-1"))[0].public_id
    assert tx.update_task_set(rid, first, foreign_set, "SET-X", "T", _fresh) == tx.NOT_FOUND
    assert tx.add_task(rid, first, foreign_set, _task(), _fresh) == (tx.NOT_FOUND, None)
    assert tx.update_task(rid, first, own_set, foreign_task, _task(), _fresh) == tx.NOT_FOUND
    assert tx.move_task(rid, first, own_set, foreign_task, tx.UP, _fresh) == tx.NOT_FOUND
    assert tx.update_protocol(rid, "not-a-uuid", "VA-3", "T", None, _fresh) == tx.NOT_FOUND
    assert second


def test_limits_codes_and_identifiers(app, people):
    rid = _researcher(people)
    public_id = _draft(people)
    for code in ("SET-A", "SET-B", "SET-C", "SET-D"):
        _with_set(people, public_id, code)
    assert tx.add_task_set(rid, public_id, "SET-E", "T", _fresh) == (tx.LIMIT, None)
    set_a = px.sets_of(px.definition("VA-1"))[0].public_id
    assert tx.update_task_set(rid, public_id, set_a, "SET-B", "T", _fresh) == tx.CODE_TAKEN
    for number in range(10):
        _with_task(people, public_id, set_a, title=f"Task {number}")
    assert tx.add_task(rid, public_id, set_a, _task(), _fresh) == (tx.LIMIT, None)
    _draft(people, "VA-2")
    assert tx.create_protocol(rid, "VA-2", "T", None) == (tx.VERSION_TAKEN, None)
    assert tx.update_protocol(rid, public_id, "VA-2", "T", None, _fresh) == tx.VERSION_TAKEN


def test_a_move_normalises_the_order_and_refuses_the_edges(app, people):
    rid = _researcher(people)
    public_id = _draft(people)
    set_a = _with_set(people, public_id, "SET-A")
    set_b = _with_set(people, public_id, "SET-B")
    set_c = _with_set(people, public_id, "SET-C")
    assert tx.move_task_set(rid, public_id, set_a, tx.UP, _fresh) == tx.EDGE
    assert tx.move_task_set(rid, public_id, set_c, tx.DOWN, _fresh) == tx.EDGE
    assert tx.move_task_set(rid, public_id, set_c, tx.UP, _fresh) == tx.MOVED
    rows = px.sets_of(px.definition("VA-1"))
    assert [row.public_id for row in rows] == [set_a, set_c, set_b]
    assert [row.display_order for row in rows] == [0, 1, 2]
    assert tx.move_task_set(rid, public_id, set_a, "sideways", _fresh) == tx.NOT_FOUND


def test_nothing_changes_a_frozen_or_discarded_version(app, people):
    rid = _researcher(people)
    active = _complete(people, "VA-1")
    assert tx.activate_protocol(rid, active, _fresh3)[0] == tx.ACTIVATED
    discarded = _complete(people, "VA-2")
    assert tx.discard_protocol(rid, discarded, _fresh) == tx.DISCARDED
    for public_id, version in ((active, "VA-1"), (discarded, "VA-2")):
        set_public_id = px.sets_of(px.definition(version))[0].public_id
        task_public_id = px.tasks_of(px.sets_of(px.definition(version))[0])[0].public_id
        before = _snapshot()
        assert tx.update_protocol(rid, public_id, version, "R", None, _fresh) == tx.FROZEN
        assert tx.add_task_set(rid, public_id, "SET-Z", "T", _fresh) == (tx.FROZEN, None)
        assert tx.update_task_set(rid, public_id, set_public_id, "SET-Z", "T", _fresh) \
            == tx.FROZEN
        assert tx.add_task(rid, public_id, set_public_id, _task(), _fresh) == (tx.FROZEN, None)
        assert tx.update_task(rid, public_id, set_public_id, task_public_id,
                              _task(title="R"), _fresh) == tx.FROZEN
        assert tx.move_task(rid, public_id, set_public_id, task_public_id, tx.DOWN,
                            _fresh) == tx.FROZEN
        assert _snapshot() == before


# ===========================================================================
# Activation
# ===========================================================================


def test_activation_seals_the_reviewed_digest_and_supersedes_the_current_version(app, people):
    rid = _researcher(people)
    first = _complete(people, "VA-1")
    header = protocol_header(first)
    reviewed = preview_digest(header, protocol_content(first))
    seen = []

    def stale(version, digest, current):
        seen.append((version, digest, current))
        return False

    assert tx.activate_protocol(rid, first, stale) == (tx.ACTIVATED, ())
    assert seen == [(3, reviewed, None)]
    one = px.definition("VA-1")
    assert (one.status, one.current_marker, one.content_digest, one.version) == (
        px.ACTIVE, 1, reviewed, 4)
    assert one.activated_by_id == rid and one.activated_at is not None

    second = _complete(people, "VA-2")
    seen.clear()
    assert tx.activate_protocol(rid, second, stale) == (tx.ACTIVATED, ())
    assert seen[0][2] == first
    one, two = px.definition("VA-1"), px.definition("VA-2")
    assert (one.status, one.current_marker, one.superseded_by_id, one.version) == (
        px.SUPERSEDED, None, rid, 5)
    assert (two.status, two.current_marker) == (px.ACTIVE, 1)
    # The frozen content still matches its seal.
    assert content_digest(one, tx._content(one.id)) == one.content_digest


def test_a_lower_id_draft_can_supersede_a_higher_id_active_version(app, people):
    """The outgoing marker is cleared and flushed before the new one is set:
    SQLAlchemy orders one flush's UPDATEs by primary key, and no backend
    defers a unique index."""
    rid = _researcher(people)
    older_draft = _complete(people, "VA-1")
    newer = _complete(people, "VA-2")
    assert tx.activate_protocol(rid, newer, _fresh3)[0] == tx.ACTIVATED
    assert tx.activate_protocol(rid, older_draft, _fresh3)[0] == tx.ACTIVATED
    assert (px.definition("VA-1").status, px.definition("VA-2").status) == (
        px.ACTIVE, px.SUPERSEDED)


def test_activation_outcomes_for_every_other_state(app, people):
    rid = _researcher(people)
    public_id = _complete(people, "VA-1")
    assert tx.activate_protocol(rid, public_id, _fresh3)[0] == tx.ACTIVATED
    before = _snapshot()
    # A replayed activation is a no-op: no version move, no second seal.
    assert tx.activate_protocol(rid, public_id, _fresh3) == (tx.ALREADY, ())
    assert _snapshot() == before
    _complete(people, "VA-2")
    tx.activate_protocol(rid, px.definition("VA-2").public_id, _fresh3)
    discarded = _complete(people, "VA-3")
    tx.discard_protocol(rid, discarded, _fresh)
    before = _snapshot()
    assert tx.activate_protocol(rid, public_id, _fresh3) == (tx.FROZEN, ())
    assert tx.activate_protocol(rid, discarded, _fresh3) == (tx.FROZEN, ())
    assert tx.activate_protocol(rid, "unknown", _fresh3) == (tx.NOT_FOUND, ())
    assert _snapshot() == before


def test_activation_refuses_an_incomplete_draft_and_says_why(app, people):
    rid = _researcher(people)
    empty = _draft(people, "VA-1")
    outcome, problems = tx.activate_protocol(rid, empty, _fresh3)
    assert outcome == tx.INCOMPLETE and [p.code for p in problems] == [NO_SETS]
    assert px.definition("VA-1").status == px.DRAFT
    two_sets = _draft(people, "VA-2")
    set_a = _with_set(people, two_sets, "SET-A")
    _with_set(people, two_sets, "SET-B")
    _with_task(people, two_sets, set_a)
    outcome, problems = tx.activate_protocol(rid, two_sets, _fresh3)
    assert outcome == tx.INCOMPLETE
    assert [p.code for p in problems] == [EMPTY_SET, UNEQUAL_SETS, MISSING_RATIONALE]


def test_activation_is_stale_when_the_current_version_moved_before_the_locks(app, people,
                                                                            monkeypatch):
    rid = _researcher(people)
    public_id = _complete(people, "VA-1")
    other = _complete(people, "VA-2")
    tx.activate_protocol(rid, other, _fresh3)
    answers = iter([None, px.definition("VA-2").id])
    monkeypatch.setattr(tx, "current_definition_id", lambda _stage: next(answers))
    before = _snapshot()
    assert tx.activate_protocol(rid, public_id, _fresh3) == (tx.STALE, ())
    assert _snapshot() == before


def test_the_unique_marker_is_the_final_defense_against_a_first_activation_race(
        app, people, monkeypatch):
    """Two first-ever activations lock different rows and neither sees the
    other; the database refuses the second marker, and the answer is a
    generic conflict with nothing written."""
    rid = _researcher(people)
    winner = _complete(people, "VA-1")
    loser = _complete(people, "VA-2")
    tx.activate_protocol(rid, winner, _fresh3)
    monkeypatch.setattr(tx, "current_definition_id", lambda _stage: None)
    before = _snapshot()
    assert tx.activate_protocol(rid, loser, _fresh3) == (tx.CONFLICT, ())
    assert _snapshot() == before


# ===========================================================================
# Discard and derivation
# ===========================================================================


def test_discard_is_soft_terminal_and_idempotent(app, people):
    rid = _researcher(people)
    public_id = _complete(people)
    counts = (ExperimentDefinition.query.count(), ExperimentTaskSet.query.count(),
              ExperimentTask.query.count())
    assert tx.discard_protocol(rid, public_id, _fresh) == tx.DISCARDED
    row = px.definition("VA-1")
    assert (row.status, row.discarded_by_id, row.content_digest) == (px.DISCARDED, rid, None)
    assert tx.discard_protocol(rid, public_id, _fresh) == tx.ALREADY
    assert (ExperimentDefinition.query.count(), ExperimentTaskSet.query.count(),
            ExperimentTask.query.count()) == counts
    active = _complete(people, "VA-2")
    tx.activate_protocol(rid, active, _fresh3)
    assert tx.discard_protocol(rid, active, _fresh) == tx.FROZEN


def test_derivation_copies_the_frozen_content_exactly_and_records_the_lineage(app, people):
    rid = _researcher(people)
    source = _draft(people, "VA-1")
    tx.update_protocol(rid, source, "VA-1", "Placeholder", "Placeholder rationale.", _fresh)
    for code in ("SET-A", "SET-B"):
        set_public_id = _with_set(people, source, code)
        _with_task(people, source, set_public_id, title=f"{code} one")
        _with_task(people, source, set_public_id, task_type="quiz_completion",
                   completion_criterion="quiz_attempt_submitted", title=f"{code} two")
    tx.move_task_set(rid, source, px.sets_of(px.definition("VA-1"))[1].public_id, tx.UP,
                     _fresh)
    assert tx.activate_protocol(rid, source, _fresh3)[0] == tx.ACTIVATED
    seal = px.definition("VA-1").content_digest
    outcome, copy = tx.derive_protocol(rid, source, "VA-2", "Placeholder",
                                       lambda digest: digest != seal)
    assert outcome == tx.CREATED
    original, derived = px.definition("VA-1"), px.definition("VA-2")
    assert (derived.status, derived.derived_from_id, derived.version, derived.content_digest) \
        == (px.DRAFT, original.id, 1, None)
    assert derived.equivalence_rationale == original.equivalence_rationale

    def shape(definition):
        return [((s.set_code, s.title), [tuple(getattr(t, f) for f in px.TASK)
                                         for t in tasks])
                for s, tasks in tx._content(definition.id)]

    assert shape(derived) == shape(original)
    assert [s.display_order for s, _ in tx._content(derived.id)] == [0, 1]
    # The source never changed.
    assert px.definition("VA-1").content_digest == seal
    # Only frozen versions can be copied.
    assert tx.derive_protocol(rid, copy, "VA-3", "T", lambda d: False) == (
        tx.NOT_DERIVABLE, None)
    assert tx.derive_protocol(rid, source, "VA-2", "T", lambda d: False) == (
        tx.VERSION_TAKEN, None)


def test_derivation_refuses_a_source_that_no_longer_matches_its_seal(app, people):
    """A raw SQL edit bypasses every ORM guard; the digest is what exposes
    it, and a copy of mismatched content is refused rather than carried
    forward."""
    rid = _researcher(people)
    source = _complete(people, "VA-1")
    tx.activate_protocol(rid, source, _fresh3)
    db.session.execute(sa.text("UPDATE experiment_tasks SET title = 'Tampered'"))
    db.session.commit()
    before = ExperimentDefinition.query.count()
    assert tx.derive_protocol(rid, source, "VA-2", "T", lambda d: False) == (tx.CONFLICT, None)
    assert ExperimentDefinition.query.count() == before


# ===========================================================================
# The structural review
# ===========================================================================


class _Row:
    def __init__(self, **values):
        self.__dict__.update(values)


def _content_of(*sets):
    return [(_Row(set_code=code, title="T"),
             [_Row(task_type=kind, completion_criterion=criterion) for kind, criterion in tasks])
            for code, tasks in sets]


def test_the_structural_review_rules():
    find = ("find_lesson", "lesson_opened")
    quiz = ("quiz_completion", "quiz_attempt_submitted")
    assert structural_problems(None, _content_of(("A", [find]))) == ()
    assert [p.code for p in structural_problems(None, [])] == [NO_SETS]
    assert [p.code for p in structural_problems(None, _content_of(("A", [])))] == [EMPTY_SET]
    assert [p.code for p in structural_problems(
        "why", _content_of(("A", [find, quiz]), ("B", [quiz, find])))] == [
        NOT_PARALLEL, NOT_PARALLEL]
    assert [p.code for p in structural_problems(
        None, _content_of(("A", [find]), ("B", [find])))] == [MISSING_RATIONALE]
    assert structural_problems("why", _content_of(("A", [find, quiz]), ("B", [find, quiz]))) \
        == ()


# ===========================================================================
# Text normalisation
# ===========================================================================


def test_codes_are_upper_case_ascii_with_a_strict_format():
    assert text.normalize_protocol_version("  va-2026.01_b ") == ("VA-2026.01_B", None)
    assert text.normalize_set_code("set-a") == ("SET-A", None)
    for raw in ("-VA", "VA 1", "VÄ-1", "VA/1", "VA+1"):
        assert text.normalize_protocol_version(raw)[1] == text.FORMAT, raw
    assert text.normalize_set_code("SET_A")[1] == text.FORMAT
    assert text.normalize_protocol_version("")[1] == text.MISSING
    assert text.normalize_protocol_version("V" * 41)[1] == text.TOO_LONG


def test_obvious_personal_data_is_refused_in_every_field():
    normalizers = (
        text.normalize_protocol_title, text.normalize_set_title, text.normalize_task_title,
        text.normalize_participant_instructions, text.normalize_expected_goal,
        text.normalize_equivalence_rationale,
    )
    for value in ("Ask RP-ABCDEFGHJK to log in", "ask rp-abcdefghjk first",
                  "Email someone@example.com", "x@y.org"):
        for normalizer in normalizers:
            assert normalizer(value)[1] == text.PROHIBITED, (normalizer.__name__, value)
    assert text.normalize_protocol_version("RP-ABCDEFGHJK")[1] == text.PROHIBITED
    # Ordinary task wording that merely mentions email or codes is fine.
    assert text.normalize_participant_instructions(
        "Open the Messages page; do not send an email.")[1] is None
    assert text.normalize_task_title("Find lesson RP-2")[1] is None


def test_control_and_bidirectional_characters_are_rejected_never_dropped():
    assert text.normalize_task_title("A\x00B")[1] == text.CONTROL
    assert text.normalize_participant_instructions("A‮B")[1] == text.CONTROL
    assert text.normalize_participant_instructions("Line one\r\nLine two\tend") == (
        "Line one\nLine two\tend", None)
    assert text.normalize_equivalence_rationale("   ") == (None, None)
    assert text.normalize_participant_instructions("   ")[1] == text.MISSING
    assert text.normalize_expected_goal("x" * 1001)[1] == text.TOO_LONG


# ===========================================================================
# Tokens
# ===========================================================================


def _draft_payload(**overrides):
    payload = {"actor_public_id": "a", "protocol_public_id": "p", "protocol_version": 3,
               "target_public_id": tokens.NONE, "action": tokens.ACTION_ADD_SET}
    payload.update(overrides)
    return payload


def test_a_token_matches_only_its_exact_state_purpose_and_action(app):
    with app.test_request_context():
        token = tokens.make_token(tokens.PURPOSE_DRAFT_CHANGE, **_draft_payload())
        assert not tokens.token_is_stale(token, tokens.PURPOSE_DRAFT_CHANGE, **_draft_payload())
        for change in ({"protocol_version": 4}, {"action": tokens.ACTION_EDIT_SET},
                       {"target_public_id": "x"}, {"actor_public_id": "b"}):
            assert tokens.token_is_stale(token, tokens.PURPOSE_DRAFT_CHANGE,
                                         **_draft_payload(**change)), change
        assert tokens.token_is_stale(token, tokens.PURPOSE_DISCARD, actor_public_id="a",
                                     protocol_public_id="p", protocol_version=3)
        for broken in (None, "", token + "x", "x" * 5000):
            assert tokens.token_is_stale(broken, tokens.PURPOSE_DRAFT_CHANGE,
                                         **_draft_payload())


def test_an_expired_token_is_stale(app, monkeypatch):
    with app.test_request_context():
        token = tokens.make_token(tokens.PURPOSE_DRAFT_CHANGE, **_draft_payload())
        real = time.time
        monkeypatch.setattr(time, "time", lambda: real() + tokens.TOKEN_MAX_AGE_SECONDS + 60)
        assert tokens.token_is_stale(token, tokens.PURPOSE_DRAFT_CHANGE, **_draft_payload())
