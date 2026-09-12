"""Phase 4 / M11 -- the messaging models and schema, text normalisation,
the signed state tokens and the message notification target."""

import time
import uuid

import pytest
import sqlalchemy as sa
from itsdangerous import URLSafeTimedSerializer
from itsdangerous.timed import TimestampSigner
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

import tests.message_fixtures as fx
from app.extensions import db
from app.models import (
    MESSAGE_BODY_MAX_LENGTH,
    MESSAGE_SUBJECT_MAX_LENGTH,
    Message,
    MessageThread,
    MessageThreadMember,
    Notification,
    NotificationKind,
    User,
)
from app.models.message_thread import utc_whole_second_now
from app.services import message_text as text
from app.services import message_tokens as tokens
from app.services.notification_targets import (
    MESSAGE_THREAD_PREFIX,
    ROLE_NAMESPACES,
    message_thread_target,
    validate_message_thread_target,
    validate_notification_target,
)

_PID = "43f44f1a-e67f-4df1-9465-7a95fcedcd2b"


def _people():
    student = fx.user("s@example.com", fx.STUDENT)
    teacher = fx.user("t@example.com", fx.TEACHER)
    return student, teacher


def _integrity(statement, params=None):
    with pytest.raises(IntegrityError):
        db.session.execute(sa.text(statement), params or {})
        db.session.flush()
    db.session.rollback()


# ===========================================================================
# Models and schema
# ===========================================================================


def test_default_timestamps_are_whole_second_naive_utc(app):
    with app.app_context():
        student, teacher = _people()
        row = MessageThread(created_by_id=student.id, subject="Hi", creation_nonce=fx.nonce())
        db.session.add(row)
        db.session.flush()
        member = MessageThreadMember(thread_id=row.id, user_id=student.id)
        message = Message(
            thread_id=row.id, sender_id=student.id, body="Hello", creation_nonce=fx.nonce()
        )
        db.session.add_all([member, message])
        db.session.commit()
        for moment in (row.created_at, member.joined_at, message.created_at):
            assert moment.microsecond == 0
            assert moment.tzinfo is None
        assert utc_whole_second_now().microsecond == 0
        assert uuid.UUID(row.public_id).version == 4
        assert uuid.UUID(message.public_id).version == 4


def test_public_ids_are_unique(app):
    with app.app_context():
        student, teacher = _people()
        first = fx.thread(student, teacher)
        duplicate = MessageThread(
            public_id=first.public_id,
            created_by_id=student.id,
            subject="Again",
            creation_nonce=fx.nonce(),
        )
        db.session.add(duplicate)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        message = Message.query.one()
        db.session.add(
            Message(
                public_id=message.public_id,
                thread_id=first.id,
                sender_id=student.id,
                body="Again",
                creation_nonce=fx.nonce(),
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_creation_nonces_are_unique_for_threads_and_for_messages(app):
    with app.app_context():
        student, teacher = _people()
        first = fx.thread(student, teacher)
        db.session.add(
            MessageThread(
                created_by_id=teacher.id, subject="Other", creation_nonce=first.creation_nonce
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        existing = Message.query.one()
        db.session.add(
            Message(
                thread_id=first.id,
                sender_id=teacher.id,
                body="Other",
                creation_nonce=existing.creation_nonce,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_a_user_can_be_a_member_of_one_thread_only_once(app):
    with app.app_context():
        student, teacher = _people()
        row = fx.thread(student, teacher)
        db.session.add(MessageThreadMember(thread_id=row.id, user_id=student.id))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        assert MessageThreadMember.query.filter_by(thread_id=row.id).count() == 2


def test_database_checks_reject_blank_text_and_foreign_keys_are_enforced(app):
    with app.app_context():
        student, teacher = _people()
        row = fx.thread(student, teacher)
        _integrity(
            "INSERT INTO message_threads (public_id, created_by_id, subject, creation_nonce,"
            " created_at) VALUES (:p, :u, '   ', :n, '2026-05-13 09:00:00')",
            {"p": str(uuid.uuid4()), "u": student.id, "n": fx.nonce()},
        )
        _integrity(
            "INSERT INTO messages (public_id, thread_id, sender_id, body, creation_nonce,"
            " created_at) VALUES (:p, :t, :u, '  ', :n, '2026-05-13 09:00:00')",
            {"p": str(uuid.uuid4()), "t": row.id, "u": student.id, "n": fx.nonce()},
        )
        _integrity(
            "INSERT INTO messages (public_id, thread_id, sender_id, body, creation_nonce,"
            " created_at) VALUES (:p, 9999, :u, 'x', :n, '2026-05-13 09:00:00')",
            {"p": str(uuid.uuid4()), "u": student.id, "n": fx.nonce()},
        )
        _integrity(
            "INSERT INTO message_thread_members (thread_id, user_id, joined_at)"
            " VALUES (:t, 9999, '2026-05-13 09:00:00')",
            {"t": row.id},
        )


def test_the_declared_indexes_unique_constraints_and_foreign_keys_exist(app):
    with app.app_context():
        schema = inspect(db.engine)
        assert {i["name"] for i in schema.get_indexes("message_threads")} >= {
            "ix_message_threads_created_by_created_id"
        }
        assert {i["name"] for i in schema.get_indexes("message_thread_members")} >= {
            "ix_message_thread_members_user_thread"
        }
        assert {i["name"] for i in schema.get_indexes("messages")} >= {
            "ix_messages_thread_created_id",
            "ix_messages_sender_created_id",
        }
        member_uniques = {
            tuple(u["column_names"]) for u in schema.get_unique_constraints("message_thread_members")
        }
        assert ("thread_id", "user_id") in member_uniques
        for table in ("message_threads", "messages"):
            uniques = {tuple(u["column_names"]) for u in schema.get_unique_constraints(table)}
            assert ("public_id",) in uniques
            assert ("creation_nonce",) in uniques
        fks = {
            table: {
                (fk["constrained_columns"][0], fk["referred_table"])
                for fk in schema.get_foreign_keys(table)
            }
            for table in ("message_threads", "message_thread_members", "messages")
        }
        assert fks == {
            "message_threads": {("created_by_id", "users")},
            "message_thread_members": {("thread_id", "message_threads"), ("user_id", "users")},
            "messages": {("thread_id", "message_threads"), ("sender_id", "users")},
        }


def test_no_relationship_cascade_or_ondelete_ownership_exists():
    for model in (MessageThread, MessageThreadMember, Message):
        assert list(sa.inspect(model).relationships) == []
        for fk in model.__table__.foreign_keys:
            assert fk.ondelete is None
            assert fk.onupdate is None
    for relationship in sa.inspect(User).relationships:
        assert relationship.mapper.class_ not in (MessageThread, MessageThreadMember, Message)


def test_model_validators_refuse_blank_oversized_text_and_malformed_nonces():
    with pytest.raises(ValueError):
        MessageThread(subject="   ")
    with pytest.raises(ValueError):
        MessageThread(subject="x" * (MESSAGE_SUBJECT_MAX_LENGTH + 1))
    with pytest.raises(ValueError):
        MessageThread(creation_nonce="not-a-nonce")
    with pytest.raises(ValueError):
        Message(body="\n\t ")
    with pytest.raises(ValueError):
        Message(body="x" * (MESSAGE_BODY_MAX_LENGTH + 1))
    with pytest.raises(ValueError):
        Message(creation_nonce=fx.nonce().upper())
    assert MessageThread(subject="x" * MESSAGE_SUBJECT_MAX_LENGTH).subject
    assert Message(body="x" * MESSAGE_BODY_MAX_LENGTH).body


def test_the_notification_kind_accepts_message_received_and_every_prior_kind(app):
    with app.app_context():
        student, _ = _people()
        assert NotificationKind.MESSAGE_RECEIVED.value == "message_received"
        for kind in NotificationKind:
            db.session.add(
                Notification(
                    recipient_id=student.id,
                    kind=kind.value,
                    title="T",
                    message="M",
                    target_path="/student/dashboard",
                )
            )
        db.session.commit()
        assert Notification.query.count() == len(NotificationKind)
        _integrity(
            "INSERT INTO notifications (public_id, recipient_id, kind, title, message,"
            " target_path, created_at) VALUES (:p, :u, 'message_edited', 'T', 'M', '/x',"
            " '2026-05-13 09:00:00')",
            {"p": str(uuid.uuid4()), "u": student.id},
        )


# ===========================================================================
# Text normalisation
# ===========================================================================


def test_subject_normalisation_collapses_whitespace_and_enforces_bounds():
    assert text.normalize_subject("  Grammar    question  ") == ("Grammar question", None)
    # A subject is one line: like an announcement title, a control
    # character -- a newline or a tab included -- is rejected, not dropped.
    assert text.normalize_subject("Grammar\nquestion") == (None, text.CONTROL)
    assert text.normalize_subject("Grammar\tquestion") == (None, text.CONTROL)
    assert text.normalize_subject("   ") == (None, text.MISSING)
    assert text.normalize_subject(None) == (None, text.MISSING)
    assert text.normalize_subject("Bad\x00subject") == (None, text.CONTROL)
    assert text.normalize_subject("x" * 150) == ("x" * 150, None)
    assert text.normalize_subject("x" * 151) == (None, text.TOO_LONG)
    # Bounds are measured after the collapse.
    assert text.normalize_subject("  " + "x" * 150 + "  ") == ("x" * 150, None)


def test_body_normalisation_keeps_line_breaks_and_enforces_bounds():
    assert text.normalize_body("  Line one\r\nLine two\r\n\r\n\tIndented  ") == (
        "Line one\nLine two\n\n\tIndented",
        None,
    )
    assert text.normalize_body("\n\t  \r\n") == (None, text.MISSING)
    assert text.normalize_body("Bell\x07") == (None, text.CONTROL)
    assert text.normalize_body("x" * 5000) == ("x" * 5000, None)
    assert text.normalize_body("x" * 5001) == (None, text.TOO_LONG)
    assert text.normalize_body("<script>alert(1)</script>") == (
        "<script>alert(1)</script>",
        None,
    )


def test_recipient_search_is_normalised_and_capped():
    assert text.normalize_recipient_query("  Ada\x00  Love\nlace ") == "Ada Love lace"
    assert len(text.normalize_recipient_query("y" * 200)) == text.RECIPIENT_QUERY_MAX_LENGTH == 64
    assert text.normalize_recipient_query(None) == ""


def test_previews_are_one_line_and_clipped():
    preview = text.preview_text("First line\n\nSecond " + "z" * 300)
    assert "\n" not in preview
    assert len(preview) == text.PREVIEW_LENGTH
    assert preview.endswith("…")
    assert text.preview_text("short") == "short"


# ===========================================================================
# Signed state tokens
# ===========================================================================

_ACTOR = "11111111-1111-4111-8111-111111111111"
_OTHER = "22222222-2222-4222-8222-222222222222"
_TARGET = "33333333-3333-4333-8333-333333333333"
_TARGET_2 = "44444444-4444-4444-8444-444444444444"


def test_a_fresh_token_round_trips_for_its_own_actor_and_target(app):
    with app.app_context():
        for purpose in tokens.PURPOSES:
            token = tokens.make_token(purpose, _ACTOR, _TARGET)
            payload = tokens.load_token(purpose, token, _ACTOR, _TARGET)
            assert payload is not None
            assert payload["purpose"] == purpose
            assert len(payload["nonce"]) == 64
            # Nothing but public ids, the purpose and the nonce.
            assert set(payload) == {
                "purpose", "actor_public_id", "nonce",
                "recipient_public_id" if purpose == tokens.PURPOSE_CREATE else "thread_public_id",
            }


def test_missing_malformed_and_tampered_tokens_are_rejected(app):
    with app.app_context():
        token = tokens.make_token(tokens.PURPOSE_CREATE, _ACTOR, _TARGET)
        tampered = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
        for candidate in (None, "", "garbage", "a.b.c", tampered, token + "x", "x" * 5000):
            assert tokens.load_token(tokens.PURPOSE_CREATE, candidate, _ACTOR, _TARGET) is None


def test_an_expired_token_is_rejected(app, monkeypatch):
    with app.app_context():
        past = int(time.time()) - tokens.TOKEN_MAX_AGE_SECONDS - 60
        monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: past)
        token = tokens.make_token(tokens.PURPOSE_REPLY, _ACTOR, _TARGET)
        monkeypatch.undo()
        assert tokens.load_token(tokens.PURPOSE_REPLY, token, _ACTOR, _TARGET) is None
        fresh = tokens.make_token(tokens.PURPOSE_REPLY, _ACTOR, _TARGET)
        assert tokens.load_token(tokens.PURPOSE_REPLY, fresh, _ACTOR, _TARGET) is not None


def test_cross_user_cross_target_and_cross_purpose_tokens_are_rejected(app):
    with app.app_context():
        create = tokens.make_token(tokens.PURPOSE_CREATE, _ACTOR, _TARGET)
        reply = tokens.make_token(tokens.PURPOSE_REPLY, _ACTOR, _TARGET)
        assert tokens.load_token(tokens.PURPOSE_CREATE, create, _OTHER, _TARGET) is None
        assert tokens.load_token(tokens.PURPOSE_CREATE, create, _ACTOR, _TARGET_2) is None
        assert tokens.load_token(tokens.PURPOSE_REPLY, reply, _ACTOR, _TARGET_2) is None
        assert tokens.load_token(tokens.PURPOSE_REPLY, create, _ACTOR, _TARGET) is None
        assert tokens.load_token(tokens.PURPOSE_CREATE, reply, _ACTOR, _TARGET) is None
        assert tokens.load_token("message-delete", create, _ACTOR, _TARGET) is None


def test_a_correctly_signed_token_of_the_wrong_shape_is_rejected(app):
    with app.app_context():
        serializer = URLSafeTimedSerializer(
            app.config["SECRET_KEY"] or "", salt=tokens._SALTS[tokens.PURPOSE_CREATE]
        )
        base = {
            "purpose": tokens.PURPOSE_CREATE,
            "actor_public_id": _ACTOR,
            "recipient_public_id": _TARGET,
            "nonce": fx.nonce(),
        }
        for payload in (
            dict(base, extra="x"),
            {k: v for k, v in base.items() if k != "nonce"},
            dict(base, nonce="short"),
            dict(base, nonce=7),
            dict(base, purpose=tokens.PURPOSE_REPLY),
            ["not", "a", "dict"],
        ):
            forged = serializer.dumps(payload)
            assert tokens.load_token(tokens.PURPOSE_CREATE, forged, _ACTOR, _TARGET) is None


def test_every_token_mints_a_new_nonce_unless_one_is_kept(app):
    with app.app_context():
        first = tokens.load_token(
            tokens.PURPOSE_CREATE, tokens.make_token(tokens.PURPOSE_CREATE, _ACTOR, _TARGET),
            _ACTOR, _TARGET,
        )
        second = tokens.load_token(
            tokens.PURPOSE_CREATE, tokens.make_token(tokens.PURPOSE_CREATE, _ACTOR, _TARGET),
            _ACTOR, _TARGET,
        )
        kept = tokens.load_token(
            tokens.PURPOSE_CREATE,
            tokens.make_token(tokens.PURPOSE_CREATE, _ACTOR, _TARGET, first["nonce"]),
            _ACTOR, _TARGET,
        )
        assert first["nonce"] != second["nonce"]
        assert kept["nonce"] == first["nonce"]


# ===========================================================================
# The message notification target
# ===========================================================================


def test_the_builder_produces_the_canonical_thread_path_for_both_roles(app):
    with app.app_context():
        for role in (fx.STUDENT, fx.TEACHER):
            target = message_thread_target(role, _PID)
            assert target == f"/messages/threads/{_PID}" == MESSAGE_THREAD_PREFIX + _PID
            assert validate_notification_target(role, target) == target
            assert validate_message_thread_target(role, target) == target


@pytest.mark.parametrize(
    "candidate",
    [
        f"/messages/threads/{_PID}?page=2",
        f"/messages/threads/{_PID}#message-1",
        f"/messages/threads/{_PID}/",
        f"/messages/threads/{_PID}/reply",
        f"/messages/threads/{_PID}/../../teacher/dashboard",
        f"/messages/threads/{_PID.upper()}",
        f"/messages/threads/{_PID[:-1]}",
        f"/messages/threads/{_PID}\n",
        f"/messages/threads/{_PID}\x00",
        f"/messages/threads/{_PID};x",
        f"/messages/threads/{_PID}%2F",
        "/messages/threads/../dashboard",
        "/messages/threads/./" + _PID,
        "/messages/threads/",
        "/messages/threads/not-a-uuid",
        "/messages",
        "/messages/new",
        "/messages/",
        f"https://evil.example/messages/threads/{_PID}",
        f"//evil.example/messages/threads/{_PID}",
        f"\\messages\\threads\\{_PID}",
        f" /messages/threads/{_PID}",
    ],
)
def test_every_other_shared_messages_path_is_rejected(candidate):
    for role in (fx.STUDENT, fx.TEACHER):
        assert validate_notification_target(role, candidate) is None


@pytest.mark.parametrize("role", [fx.ADMIN, fx.RESEARCHER, "", None, "root"])
def test_roles_without_an_inbox_never_validate_a_thread_target(role):
    assert validate_notification_target(role, f"/messages/threads/{_PID}") is None
    assert validate_message_thread_target(role, f"/messages/threads/{_PID}") is None


def test_the_role_namespaces_are_not_widened():
    assert ROLE_NAMESPACES == {fx.STUDENT: "/student/", fx.TEACHER: "/teacher/"}
    assert validate_notification_target(fx.STUDENT, "/student/dashboard") == "/student/dashboard"
    assert validate_notification_target(fx.STUDENT, "/teacher/dashboard") is None


def test_the_builder_refuses_an_unsafe_value_or_a_role_without_an_inbox(app):
    with app.app_context():
        with pytest.raises(ValueError):
            message_thread_target(fx.STUDENT, "../../admin")
        with pytest.raises(ValueError):
            message_thread_target(fx.STUDENT, _PID.upper())
        with pytest.raises(ValueError):
            message_thread_target(fx.ADMIN, _PID)
