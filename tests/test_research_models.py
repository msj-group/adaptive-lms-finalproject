"""Phase 6 / M01 model, constraint and guard checks.

The three tables, their closed sets, their lifecycle CHECKs, the
one-current-document invariant, the append-only consent history, the
immutability of activated wording, and the refusal of bulk rewrites and hard
deletes.

SQLite (the test backend) enforces CHECK and UNIQUE constraints and, with
``PRAGMA foreign_keys=ON``, foreign keys -- which ``app/extensions.py``
turns on for every connection. It proves nothing about MySQL/InnoDB
blocking, isolation or collation.
"""

import pytest
import sqlalchemy as sa
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError

import tests.research_fixtures as rx
from app.extensions import db
from app.models import (
    CONSENT_BODY_MAX_LENGTH,
    CONSENT_DIGEST_LENGTH,
    PARTICIPANT_CODE_ALPHABET,
    PARTICIPANT_CODE_LENGTH,
    PARTICIPANT_CODE_PREFIX,
    STATUS_AFTER_ACTION,
    ResearchConsentAction,
    ResearchConsentDocument,
    ResearchConsentDocumentStatus,
    ResearchConsentEvent,
    ResearchHistoryError,
    ResearchParticipant,
    ResearchParticipantStatus,
    consent_digest,
    generate_participant_code,
)
from app.models.research_participant import ALLOWED_PARTICIPANT_TRANSITIONS
from app.models.submission_feedback import whole_second_utc

_DRAFT = ResearchConsentDocumentStatus.DRAFT.value
_DOC_ACTIVE = ResearchConsentDocumentStatus.ACTIVE.value
_SUPERSEDED = ResearchConsentDocumentStatus.SUPERSEDED.value

_INVITED = ResearchParticipantStatus.INVITED.value
_ACTIVE = ResearchParticipantStatus.ACTIVE.value
_DECLINED = ResearchParticipantStatus.DECLINED.value
_WITHDRAWN = ResearchParticipantStatus.WITHDRAWN.value

_TABLES = ("research_consent_documents", "research_participants", "research_consent_events")


def _actors(_app):
    """The two accounts most of these tests need.

    Deliberately **not** inside a nested ``app.app_context()``: the ``app``
    fixture already pushes one for the whole test, and pushing a second
    would pop the Flask-SQLAlchemy session with it and detach every row the
    test still holds.
    """
    return rx.make_admin(), rx.make_student()


def _event(participant, document, action, actor, moment=None):
    event = ResearchConsentEvent(
        participant_id=participant.id,
        consent_document_id=document.id,
        action=action,
        actor_id=actor.id,
        consent_version=document.version_identifier,
        consent_digest=document.body_digest,
        occurred_at=moment or whole_second_utc(),
    )
    db.session.add(event)
    db.session.commit()
    return event


def _refused(statement_or_callable):
    """Run it and require the database (not the ORM) to refuse it."""
    with pytest.raises(IntegrityError):
        statement_or_callable()
    db.session.rollback()


# ===========================================================================
# Shape: columns, closed sets, indexes and the absence of cascades
# ===========================================================================


def test_the_three_tables_exist_with_exactly_the_expected_columns(app):
    inspector = sa.inspect(db.engine)
    assert {c["name"] for c in inspector.get_columns(_TABLES[0])} == {
        "id", "public_id", "version_identifier", "title", "body", "body_digest",
        "status", "current_marker", "created_by_id", "activated_at", "activated_by_id",
        "superseded_at", "superseded_by_id", "created_at", "updated_at",
    }
    assert {c["name"] for c in inspector.get_columns(_TABLES[1])} == {
        "id", "public_id", "student_id", "participant_code", "status",
        "consent_document_id", "decided_at", "withdrawn_at", "invited_by_id",
        "version", "created_at", "updated_at",
    }
    assert {c["name"] for c in inspector.get_columns(_TABLES[2])} == {
        "id", "participant_id", "consent_document_id", "action", "actor_id",
        "consent_version", "consent_digest", "occurred_at",
    }


def test_no_column_is_shaped_to_hold_tracking_or_personal_extras(app):
    """M01 collects no behavioural interaction data, so no column exists
    that could hold any -- not an address, an agent string, a fingerprint,
    a keystroke, a comment, a rating, a survey answer or a generic event
    payload. The participant row and the consent events it *does* store are
    research administration, not observation."""
    forbidden = (
        "ip_address", "remote_addr", "user_agent", "agent", "fingerprint", "device",
        "browser", "keystroke", "screen", "audio", "video", "recording", "comment",
        "note", "reason", "rating", "frustration", "emotion", "survey", "answer",
        "payload", "event_type", "session", "experiment", "task", "metric", "score",
        "password", "token", "amount", "balance", "invoice", "payment",
    )
    inspector = sa.inspect(db.engine)
    for table in _TABLES:
        for column in inspector.get_columns(table):
            for word in forbidden:
                assert word not in column["name"], (table, column["name"], word)


def test_no_foreign_key_carries_a_delete_or_update_action(app):
    """Nothing cascades into research history, from any direction."""
    inspector = sa.inspect(db.engine)
    for table in _TABLES:
        for fk in inspector.get_foreign_keys(table):
            assert not fk.get("options"), (table, fk)


def test_the_indexes_are_exactly_the_declared_query_paths(app):
    inspector = sa.inspect(db.engine)
    indexed = {
        table: {i["name"]: list(i["column_names"]) for i in inspector.get_indexes(table)}
        for table in _TABLES
    }
    assert indexed[_TABLES[0]] == {
        "ix_research_consent_documents_status_id": ["status", "id"],
        "ix_research_consent_documents_created_by_id": ["created_by_id"],
        "ix_research_consent_documents_activated_by_id": ["activated_by_id"],
        "ix_research_consent_documents_superseded_by_id": ["superseded_by_id"],
    }
    assert indexed[_TABLES[1]] == {
        "ix_research_participants_status_id": ["status", "id"],
        "ix_research_participants_invited_by_id": ["invited_by_id"],
        "ix_research_participants_consent_document_id": ["consent_document_id"],
    }
    assert indexed[_TABLES[2]] == {
        "ix_research_consent_events_participant_id_id": ["participant_id", "id"],
        "ix_research_consent_events_consent_document_id": ["consent_document_id"],
        "ix_research_consent_events_actor_id": ["actor_id"],
    }


def test_no_orm_relationship_is_declared_on_any_m01_model():
    """Every read is an explicit projection in research_queries, so no
    template can lazy-load a Student's name through a participant."""
    for model in (ResearchConsentDocument, ResearchParticipant, ResearchConsentEvent):
        assert list(sa.inspect(model).relationships) == [], model.__name__


# ===========================================================================
# Participant codes
# ===========================================================================


def test_a_generated_participant_code_has_the_declared_shape():
    for _ in range(50):
        code = generate_participant_code()
        assert code.startswith(PARTICIPANT_CODE_PREFIX)
        assert len(code) == PARTICIPANT_CODE_LENGTH
        assert all(ch in PARTICIPANT_CODE_ALPHABET for ch in code[len(PARTICIPANT_CODE_PREFIX):])


def test_generated_codes_are_not_derived_from_anything_and_do_not_repeat():
    """The generator takes no argument at all, so nothing about a Student
    can reach it, and 200 draws produce 200 distinct codes."""
    import inspect as py_inspect

    assert py_inspect.signature(generate_participant_code).parameters == {}
    assert len({generate_participant_code() for _ in range(200)}) == 200


def test_the_code_alphabet_excludes_the_ambiguous_characters():
    for ch in "ILOU01":
        assert ch not in PARTICIPANT_CODE_ALPHABET, ch


def test_a_participant_code_of_the_wrong_shape_is_refused_by_the_model(app):
    admin, student = _actors(app)
    for bad in ("", "RP-SHORT", "XX-AAAAAAAAAA", "RP-AAAAAAAAA1", None):
        with pytest.raises(ValueError):
            ResearchParticipant(
                student_id=student.id, participant_code=bad, status=_INVITED,
                invited_by_id=admin.id, version=1,
            )


# ===========================================================================
# Consent documents: digest, lifecycle, uniqueness, immutability
# ===========================================================================


def test_the_digest_covers_the_version_title_and_body_and_cannot_be_re_split():
    a = consent_digest("v1", "AB", "C")
    b = consent_digest("v1", "A", "BC")
    assert a != b
    assert consent_digest("v1", "AB", "C") == a
    assert len(a) == CONSENT_DIGEST_LENGTH
    for changed in (("v2", "AB", "C"), ("v1", "AC", "C"), ("v1", "AB", "D")):
        assert consent_digest(*changed) != a


def test_digest_matches_reports_a_document_whose_text_and_digest_disagree(app):
    admin, _ = _actors(app)
    document = rx.document_row(admin)
    assert document.digest_matches()
    # Reach past the guards to simulate a row that disagrees, however it
    # came to: a draft's wording is changed without its digest.
    db.session.execute(
        sa.text("UPDATE research_consent_documents SET body = :b WHERE id = :i"),
        {"b": "Tampered wording.", "i": document.id},
    )
    db.session.commit()
    db.session.expire_all()
    assert not db.session.get(ResearchConsentDocument, document.id).digest_matches()


def test_a_bad_digest_is_refused_by_the_model_and_by_the_database(app):
    admin, _ = _actors(app)
    for bad in ("", "xyz", "A" * 64, "0" * 63):
        with pytest.raises(ValueError):
            ResearchConsentDocument(
                version_identifier="vX", title="T", body="B", body_digest=bad,
                status=_DRAFT, created_by_id=admin.id,
            )
    _refused(lambda: db.session.execute(sa.text(
        "INSERT INTO research_consent_documents (public_id, version_identifier, title,"
        " body, body_digest, status, current_marker, created_by_id, created_at, updated_at)"
        " VALUES ('d-bad', 'vBad', 'T', 'B', 'short', 'draft', NULL, :a,"
        " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"), {"a": admin.id}))


def test_two_documents_cannot_share_a_version_identifier(app):
    admin, _ = _actors(app)
    rx.document_row(admin, version="v1.0")
    _refused(lambda: rx.document_row(admin, version="v1.0"))


def test_only_one_document_can_be_current_at_a_time(app):
    """The cross-row invariant is a real database constraint here: the
    current marker is 1 exactly while active, and unique over the table."""
    admin, _ = _actors(app)
    rx.document_row(admin, version="v1.0", status=_DOC_ACTIVE)
    _refused(lambda: rx.document_row(admin, version="v2.0", status=_DOC_ACTIVE))
    db.session.rollback()
    # Many drafts and many superseded documents coexist happily: only
    # the marker is unique, and only an active document carries one.
    rx.document_row(admin, version="v3.0")
    rx.document_row(admin, version="v4.0")
    rx.document_row(admin, version="v5.0", status=_SUPERSEDED)
    rx.document_row(admin, version="v6.0", status=_SUPERSEDED)
    assert ResearchConsentDocument.query.filter_by(status=_DOC_ACTIVE).count() == 1


def test_the_current_marker_must_match_the_status(app):
    admin, _ = _actors(app)
    for status, marker in ((_DRAFT, 1), (_SUPERSEDED, 1), (_DOC_ACTIVE, None)):
        activated = "'2026-01-01 00:00:00'" if status != _DRAFT else "NULL"
        actor = str(admin.id) if status != _DRAFT else "NULL"
        superseded = "'2026-01-02 00:00:00'" if status == _SUPERSEDED else "NULL"
        s_actor = str(admin.id) if status == _SUPERSEDED else "NULL"
        _refused(lambda st=status, mk=marker, a=activated, ac=actor, s=superseded,
                 sa_=s_actor: db.session.execute(sa.text(
                     "INSERT INTO research_consent_documents (public_id,"
                     " version_identifier, title, body, body_digest, status,"
                     " current_marker, created_by_id, activated_at, activated_by_id,"
                     " superseded_at, superseded_by_id, created_at, updated_at) VALUES"
                     f" ('d-{st}-{mk}', 'v-{st}-{mk}', 'T', 'B', '{'a' * 64}', '{st}',"
                     f" {'NULL' if mk is None else mk}, {admin.id}, {a}, {ac}, {s}, {sa_},"
                     " '2026-01-01 00:00:00', '2026-01-03 00:00:00')")))


def test_an_unknown_document_status_is_refused(app):
    admin, _ = _actors(app)
    with pytest.raises(ValueError):
        rx.document_row(admin, status="retired")
    db.session.rollback()
    _refused(lambda: db.session.execute(sa.text(
        "INSERT INTO research_consent_documents (public_id, version_identifier, title,"
        " body, body_digest, status, current_marker, created_by_id, created_at, updated_at)"
        f" VALUES ('d-x', 'vX', 'T', 'B', '{'a' * 64}', 'retired', NULL, {admin.id},"
        " '2026-01-01 00:00:00', '2026-01-01 00:00:00')")))


def test_an_empty_body_is_refused_by_the_database(app):
    admin, _ = _actors(app)
    _refused(lambda: db.session.execute(sa.text(
        "INSERT INTO research_consent_documents (public_id, version_identifier, title,"
        " body, body_digest, status, current_marker, created_by_id, created_at, updated_at)"
        f" VALUES ('d-e', 'vE', 'T', '', '{'a' * 64}', 'draft', NULL, {admin.id},"
        " '2026-01-01 00:00:00', '2026-01-01 00:00:00')")))


def test_the_body_bound_fits_a_mysql_text_column_even_in_four_byte_characters():
    """TEXT is 65,535 bytes on MySQL; the application's own character bound
    must stay inside that even for the worst-case encoding."""
    assert CONSENT_BODY_MAX_LENGTH * 4 <= 65535


def test_an_activated_document_cannot_have_its_wording_changed(app):
    admin, _ = _actors(app)
    document = rx.document_row(admin, status=_DOC_ACTIVE)
    for field, value in (
        ("body", "Different wording."),
        ("title", "Different title"),
        ("version_identifier", "v9.9"),
        ("body_digest", "b" * 64),
        ("public_id", "something-else"),
        ("created_by_id", admin.id + 1),
    ):
        setattr(document, field, value)
        with pytest.raises(ResearchHistoryError):
            db.session.commit()
        db.session.rollback()
        db.session.expire_all()
        document = ResearchConsentDocument.query.filter_by(status=_DOC_ACTIVE).one()


def test_an_activated_document_may_still_be_superseded(app):
    """The freeze covers the wording, not the lifecycle: superseding an
    activated document is the one transition it still has."""
    admin, _ = _actors(app)
    document = rx.document_row(admin, status=_DOC_ACTIVE)
    # Read before the first assignment: touching a possibly-expired `admin`
    # mid-transition would autoflush a half-written row into its own CHECK.
    admin_id, moment = admin.id, whole_second_utc()
    document.status = _SUPERSEDED
    document.current_marker = None
    document.superseded_at = moment
    document.superseded_by_id = admin_id
    document.updated_at = moment
    db.session.commit()
    assert db.session.get(ResearchConsentDocument, document.id).is_superseded


def test_a_draft_may_still_be_activated(app):
    admin, _ = _actors(app)
    document = rx.document_row(admin)
    admin_id, moment = admin.id, whole_second_utc()
    document.status = _DOC_ACTIVE
    document.current_marker = 1
    document.activated_at = moment
    document.activated_by_id = admin_id
    document.updated_at = moment
    db.session.commit()
    assert db.session.get(ResearchConsentDocument, document.id).is_active


def test_a_consent_document_is_never_deleted(app):
    admin, _ = _actors(app)
    document = rx.document_row(admin)
    db.session.delete(document)
    with pytest.raises(ResearchHistoryError):
        db.session.commit()
    db.session.rollback()
    assert ResearchConsentDocument.query.count() == 1


def test_the_document_lifecycle_check_rejects_impossible_combinations(app):
    admin, _ = _actors(app)
    cases = (
        # a draft that was activated
        ("draft", "'2026-01-02 00:00:00'", str(admin.id), "NULL", "NULL", "NULL"),
        # an active document that was superseded
        ("active", "'2026-01-02 00:00:00'", str(admin.id), "'2026-01-03 00:00:00'",
         str(admin.id), "1"),
        # a superseded document that was never activated
        ("superseded", "NULL", "NULL", "'2026-01-03 00:00:00'", str(admin.id), "NULL"),
    )
    for i, (status, act, act_by, sup, sup_by, marker) in enumerate(cases):
        _refused(lambda s=status, a=act, ab=act_by, su=sup, sb=sup_by, mk=marker,
                 n=i: db.session.execute(sa.text(
                     "INSERT INTO research_consent_documents (public_id,"
                     " version_identifier, title, body, body_digest, status,"
                     " current_marker, created_by_id, activated_at, activated_by_id,"
                     " superseded_at, superseded_by_id, created_at, updated_at) VALUES"
                     f" ('d-l{n}', 'v-l{n}', 'T', 'B', '{'a' * 64}', '{s}', {mk},"
                     f" {admin.id}, {a}, {ab}, {su}, {sb}, '2026-01-01 00:00:00',"
                     " '2026-01-04 00:00:00')")))


# ===========================================================================
# Participants: one per Student, lifecycle, identity immutability
# ===========================================================================


def test_a_student_may_have_at_most_one_participant(app):
    admin, student = _actors(app)
    rx.participant_row(student, admin, code="RP-AAAAAAAAAA")
    _refused(lambda: rx.participant_row(student, admin, code="RP-BBBBBBBBBB"))


def test_two_participants_may_not_share_a_code(app):
    admin, student = _actors(app)
    other = rx.make_student("second@example.com", name="Second Student")
    rx.participant_row(student, admin, code="RP-AAAAAAAAAA")
    _refused(lambda: rx.participant_row(other, admin, code="RP-AAAAAAAAAA"))


def test_an_unknown_participant_status_is_refused(app):
    admin, student = _actors(app)
    with pytest.raises(ValueError):
        ResearchParticipant(
            student_id=student.id, participant_code="RP-CCCCCCCCCC", status="enrolled",
            invited_by_id=admin.id, version=1,
        )
    db.session.rollback()
    _refused(lambda: db.session.execute(sa.text(
        "INSERT INTO research_participants (public_id, student_id, participant_code,"
        " status, invited_by_id, version, created_at, updated_at) VALUES"
        f" ('p-x', {student.id}, 'RP-CCCCCCCCCC', 'enrolled', {admin.id}, 1,"
        " '2026-01-01 00:00:00', '2026-01-01 00:00:00')")))


def test_the_participant_lifecycle_check_rejects_impossible_combinations(app):
    admin, student = _actors(app)
    document = rx.document_row(admin, status=_DOC_ACTIVE)
    cases = (
        # invited, but carrying a decision
        ("invited", str(document.id), "'2026-02-01 00:00:00'", "NULL"),
        # active, but with no document
        ("active", "NULL", "'2026-02-01 00:00:00'", "NULL"),
        # active, but withdrawn
        ("active", str(document.id), "'2026-02-01 00:00:00'", "'2026-02-01 00:00:00'"),
        # withdrawn, but with no withdrawal moment
        ("withdrawn", str(document.id), "'2026-02-01 00:00:00'", "NULL"),
        # withdrawn, with a withdrawal moment that is not the decision
        ("withdrawn", str(document.id), "'2026-02-01 00:00:00'", "'2026-02-02 00:00:00'"),
        # declined, but with no decision moment
        ("declined", str(document.id), "NULL", "NULL"),
    )
    for i, (status, doc, decided, withdrawn) in enumerate(cases):
        _refused(lambda s=status, d=doc, de=decided, w=withdrawn, n=i:
                 db.session.execute(sa.text(
                     "INSERT INTO research_participants (public_id, student_id,"
                     " participant_code, status, consent_document_id, decided_at,"
                     " withdrawn_at, invited_by_id, version, created_at, updated_at)"
                     f" VALUES ('p-l{n}', {student.id}, 'RP-LLLLLLLLL{n}', '{s}', {d},"
                     f" {de}, {w}, {admin.id}, 1, '2026-01-01 00:00:00',"
                     " '2026-03-01 00:00:00')")))


def test_a_participant_version_must_be_a_positive_integer(app):
    admin, student = _actors(app)
    participant = rx.participant_row(student, admin)
    for bad in (0, -1, True, "2", None):
        with pytest.raises(ValueError):
            participant.version = bad
    db.session.rollback()
    _refused(lambda: db.session.execute(sa.text(
        "UPDATE research_participants SET version = 0 WHERE id = :i"),
        {"i": participant.id}))


def test_a_participants_identity_columns_can_never_change(app):
    admin, student = _actors(app)
    participant = rx.participant_row(student, admin)
    other = rx.make_student("third@example.com", name="Third Student")
    for field, value in (
        ("student_id", other.id),
        ("participant_code", "RP-ZZZZZZZZZZ"),
        ("public_id", "not-the-same"),
        ("invited_by_id", other.id),
    ):
        setattr(participant, field, value)
        with pytest.raises(ResearchHistoryError):
            db.session.commit()
        db.session.rollback()
        db.session.expire_all()
        participant = ResearchParticipant.query.one()


def test_a_participant_is_never_deleted(app):
    admin, student = _actors(app)
    participant = rx.participant_row(student, admin)
    db.session.delete(participant)
    with pytest.raises(ResearchHistoryError):
        db.session.commit()
    db.session.rollback()
    assert ResearchParticipant.query.count() == 1


def test_withdrawal_and_refusal_are_terminal_in_m01():
    """The allowed transition table has no edge leaving ``declined`` or
    ``withdrawn``, so no form, replay or race can reactivate either."""
    leaving = {source for source, _target in ALLOWED_PARTICIPANT_TRANSITIONS}
    assert leaving == {_INVITED, _ACTIVE}
    assert ALLOWED_PARTICIPANT_TRANSITIONS == {
        (_INVITED, _ACTIVE), (_INVITED, _DECLINED), (_ACTIVE, _WITHDRAWN)
    }


# ===========================================================================
# Consent history: append-only, and never in bulk
# ===========================================================================


def test_a_consent_event_can_be_written_and_read_back(app):
    admin, student = _actors(app)
    document = rx.document_row(admin, status=_DOC_ACTIVE)
    participant = rx.participant_row(student, admin, status=_ACTIVE, document=document)
    event = _event(participant, document, ResearchConsentAction.ACCEPTED.value, student)
    assert event.consent_version == document.version_identifier
    assert event.consent_digest == document.body_digest
    assert event.actor_id == student.id


def test_a_consent_event_is_never_updated(app):
    admin, student = _actors(app)
    document = rx.document_row(admin, status=_DOC_ACTIVE)
    participant = rx.participant_row(student, admin, status=_ACTIVE, document=document)
    event = _event(participant, document, ResearchConsentAction.ACCEPTED.value, student)
    event.action = ResearchConsentAction.WITHDRAWN.value
    with pytest.raises(ResearchHistoryError):
        db.session.commit()
    db.session.rollback()
    db.session.expire_all()
    assert ResearchConsentEvent.query.one().action == ResearchConsentAction.ACCEPTED.value


def test_a_consent_event_is_never_deleted(app):
    admin, student = _actors(app)
    document = rx.document_row(admin, status=_DOC_ACTIVE)
    participant = rx.participant_row(student, admin, status=_ACTIVE, document=document)
    event = _event(participant, document, ResearchConsentAction.ACCEPTED.value, student)
    db.session.delete(event)
    with pytest.raises(ResearchHistoryError):
        db.session.commit()
    db.session.rollback()
    assert ResearchConsentEvent.query.count() == 1


def test_no_m01_table_can_be_rewritten_or_emptied_in_bulk(app):
    """Mapper events never see session.execute(update(...)) or delete(...);
    the session-level guard refuses those for all three tables."""
    admin, student = _actors(app)
    document = rx.document_row(admin, status=_DOC_ACTIVE)
    participant = rx.participant_row(student, admin, status=_ACTIVE, document=document)
    _event(participant, document, ResearchConsentAction.ACCEPTED.value, student)
    for model, values in (
        (ResearchConsentEvent, {"action": ResearchConsentAction.WITHDRAWN.value}),
        (ResearchParticipant, {"status": _WITHDRAWN}),
        (ResearchConsentDocument, {"status": _DRAFT}),
    ):
        with pytest.raises(ResearchHistoryError):
            db.session.execute(update(model).values(**values))
        db.session.rollback()
        with pytest.raises(ResearchHistoryError):
            db.session.execute(delete(model))
        db.session.rollback()
    assert ResearchConsentEvent.query.count() == 1
    assert ResearchParticipant.query.one().status == _ACTIVE
    assert ResearchConsentDocument.query.one().status == _DOC_ACTIVE


def test_an_unknown_consent_action_is_refused(app):
    admin, student = _actors(app)
    document = rx.document_row(admin, status=_DOC_ACTIVE)
    participant = rx.participant_row(student, admin, status=_ACTIVE, document=document)
    with pytest.raises(ValueError):
        _event(participant, document, "revoked", student)
    db.session.rollback()
    _refused(lambda: db.session.execute(sa.text(
        "INSERT INTO research_consent_events (participant_id, consent_document_id,"
        " action, actor_id, consent_version, consent_digest, occurred_at) VALUES"
        f" ({participant.id}, {document.id}, 'revoked', {student.id}, 'v1.0',"
        f" '{'a' * 64}', '2026-02-01 00:00:00')")))


def test_a_consent_event_needs_a_participant_a_document_and_an_actor(app):
    admin, student = _actors(app)
    document = rx.document_row(admin, status=_DOC_ACTIVE)
    participant = rx.participant_row(student, admin, status=_ACTIVE, document=document)
    for participant_id, document_id, actor_id in (
        (999, document.id, student.id),
        (participant.id, 999, student.id),
        (participant.id, document.id, 999),
    ):
        _refused(lambda p=participant_id, d=document_id, a=actor_id:
                 db.session.execute(sa.text(
                     "INSERT INTO research_consent_events (participant_id,"
                     " consent_document_id, action, actor_id, consent_version,"
                     f" consent_digest, occurred_at) VALUES ({p}, {d}, 'accepted', {a},"
                     f" 'v1.0', '{'a' * 64}', '2026-02-01 00:00:00')")))


def test_the_action_to_status_map_covers_every_action_exactly_once():
    assert set(STATUS_AFTER_ACTION) == {a.value for a in ResearchConsentAction}
    assert set(STATUS_AFTER_ACTION.values()) == {_ACTIVE, _DECLINED, _WITHDRAWN}


def test_the_history_and_the_stored_status_cannot_contradict_each_other(app):
    """Every write goes through one transaction that moves both together, so
    the latest event's implied status is the stored status. This proves the
    property over a full accept-then-withdraw history."""
    admin, student = _actors(app)
    document = rx.document_row(admin, status=_DOC_ACTIVE)
    participant = rx.participant_row(student, admin, status=_ACTIVE, document=document)
    _event(participant, document, ResearchConsentAction.ACCEPTED.value, student)
    moment, version = whole_second_utc(), participant.version
    participant.status = _WITHDRAWN
    participant.decided_at = moment
    participant.withdrawn_at = moment
    participant.version = version + 1
    participant.updated_at = moment
    _event(participant, document, ResearchConsentAction.WITHDRAWN.value, student, moment)
    db.session.expire_all()
    events = ResearchConsentEvent.query.order_by(ResearchConsentEvent.id).all()
    assert [e.action for e in events] == ["accepted", "withdrawn"]
    assert STATUS_AFTER_ACTION[events[-1].action] == ResearchParticipant.query.one().status
