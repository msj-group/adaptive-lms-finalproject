"""Phase 4 / M12 -- the discussion models and their database rules, the
plain-text normalisation boundary, and the signed form-state tokens."""

import re
import time
import uuid

import pytest
import sqlalchemy as sa
from itsdangerous import URLSafeTimedSerializer
from itsdangerous.timed import TimestampSigner
from sqlalchemy.exc import IntegrityError

import tests.discussion_fixtures as fx
from app.extensions import db
from app.models import (
    DISCUSSION_BODY_MAX_LENGTH,
    DISCUSSION_TITLE_MAX_LENGTH,
    DiscussionReply,
    DiscussionTopic,
    DiscussionTopicStatus,
    Notification,
    NotificationKind,
)
from app.services import discussion_text as text
from app.services import discussion_tokens as tokens
from app.services import message_tokens

_UUID4 = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
_MOMENT = "'2026-05-13 09:00:00'"


def _integrity(statement, params=None):
    with pytest.raises(IntegrityError):
        db.session.execute(sa.text(statement), params or {})
        db.session.flush()
    db.session.rollback()


def _world():
    teacher, student, group = fx.classroom()
    return teacher, student, group


def _topic_insert(group_id, author_id, title="'Title'", body="'Body'", status="'open'",
                  version="1", nonce=None, public_id=None):
    return (
        "INSERT INTO discussion_topics (public_id, group_id, author_id, title, body, status,"
        " version, creation_nonce, created_at, updated_at) VALUES"
        f" ('{public_id or uuid.uuid4()}', {group_id}, {author_id}, {title}, {body}, {status},"
        f" {version}, '{nonce or fx.nonce()}', {_MOMENT}, {_MOMENT})"
    )


def _reply_insert(topic_id, author_id, body="'Reply'", nonce=None, public_id=None):
    return (
        "INSERT INTO discussion_replies (public_id, topic_id, author_id, body, creation_nonce,"
        f" created_at) VALUES ('{public_id or uuid.uuid4()}', {topic_id}, {author_id}, {body},"
        f" '{nonce or fx.nonce()}', {_MOMENT})"
    )


# ===========================================================================
# Enums
# ===========================================================================


def test_the_topic_status_is_exactly_open_or_locked():
    assert [status.value for status in DiscussionTopicStatus] == ["open", "locked"]


def test_exactly_one_notification_kind_is_added_after_every_prior_kind(app):
    values = [kind.value for kind in NotificationKind]
    assert values[8] == "message_received"
    assert values[9:] == ["discussion_topic_created"]
    assert len(values) == 10
    for forbidden in ("discussion_reply_created", "discussion_topic_locked",
                      "discussion_topic_reopened"):
        assert forbidden not in values
    with app.app_context():
        student = fx.user("student@example.com", fx.STUDENT)
        for kind in NotificationKind:
            db.session.add(Notification(recipient_id=student.id, kind=kind.value, title="T",
                                        message="M", target_path="/student/dashboard"))
        db.session.commit()
        assert Notification.query.count() == len(NotificationKind)
        _integrity(
            "INSERT INTO notifications (public_id, recipient_id, kind, title, message,"
            f" target_path, created_at) VALUES (:p, :u, 'discussion_reply_created', 'T', 'M',"
            f" '/x', {_MOMENT})",
            {"p": str(uuid.uuid4()), "u": student.id},
        )


# ===========================================================================
# Defaults, identifiers and validation
# ===========================================================================


def test_a_new_topic_is_open_at_version_one_with_a_canonical_public_id(app):
    with app.app_context():
        teacher, _, group = _world()
        row = DiscussionTopic(group_id=group.id, author_id=teacher.id, title="T", body="B",
                              creation_nonce=fx.nonce())
        db.session.add(row)
        db.session.commit()
        assert row.status == "open"
        assert row.version == 1
        assert _UUID4.match(row.public_id)
        assert row.created_at.microsecond == 0
        assert row.updated_at == row.created_at
        reply = DiscussionReply(topic_id=row.id, author_id=teacher.id, body="R",
                                creation_nonce=fx.nonce())
        db.session.add(reply)
        db.session.commit()
        assert _UUID4.match(reply.public_id) and reply.public_id != row.public_id
        assert reply.created_at.microsecond == 0


@pytest.mark.parametrize("field, value", [
    ("title", ""),
    ("title", "   \t\n "),
    ("title", None),
    ("title", "x" * (DISCUSSION_TITLE_MAX_LENGTH + 1)),
    ("body", ""),
    ("body", "\n\t "),
    ("body", "x" * (DISCUSSION_BODY_MAX_LENGTH + 1)),
    ("status", "archived"),
    ("status", "OPEN"),
    ("status", None),
    ("version", 0),
    ("version", -1),
    ("version", True),
    ("version", "1"),
    ("creation_nonce", "short"),
    ("creation_nonce", "A" * 64),
    ("creation_nonce", None),
])
def test_topic_validation_rejects_invalid_values(field, value):
    with pytest.raises(ValueError):
        DiscussionTopic(**{field: value})


@pytest.mark.parametrize("field, value", [
    ("body", ""),
    ("body", "  "),
    ("body", "x" * (DISCUSSION_BODY_MAX_LENGTH + 1)),
    ("creation_nonce", "g" * 64),
])
def test_reply_validation_rejects_invalid_values(field, value):
    with pytest.raises(ValueError):
        DiscussionReply(**{field: value})


def test_values_at_the_limits_are_accepted():
    assert DiscussionTopic(title="x" * DISCUSSION_TITLE_MAX_LENGTH).title
    assert DiscussionTopic(body="x" * DISCUSSION_BODY_MAX_LENGTH).body
    assert DiscussionTopic(status="locked", version=2**31 - 1).version == 2**31 - 1
    assert DiscussionReply(body="x" * DISCUSSION_BODY_MAX_LENGTH).body


# ===========================================================================
# Database constraints -- the final defense
# ===========================================================================


def test_topic_checks_refuse_what_validation_would_have_refused(app):
    with app.app_context():
        teacher, _, group = _world()
        g, t = group.id, teacher.id
        _integrity(_topic_insert(g, t, title="'   '"))
        _integrity(_topic_insert(g, t, body="''"))
        _integrity(_topic_insert(g, t, status="'archived'"))
        _integrity(_topic_insert(g, t, version="0"))
        _integrity(_topic_insert(g, t, version="-3"))
        db.session.execute(sa.text(_topic_insert(g, t, status="'locked'", version="7")))
        db.session.commit()
        assert DiscussionTopic.query.one().status == "locked"


def test_reply_check_refuses_a_blank_body(app):
    with app.app_context():
        teacher, _, group = _world()
        row = fx.topic(group, teacher)
        _integrity(_reply_insert(row.id, teacher.id, body="'  '"))
        assert DiscussionReply.query.count() == 0


def test_public_ids_and_creation_nonces_are_unique(app):
    with app.app_context():
        teacher, student, group = _world()
        row = fx.topic(group, teacher)
        reply = fx.reply(row, student)
        _integrity(_topic_insert(group.id, teacher.id, public_id=row.public_id))
        _integrity(_topic_insert(group.id, teacher.id, nonce=row.creation_nonce))
        _integrity(_reply_insert(row.id, student.id, public_id=reply.public_id))
        _integrity(_reply_insert(row.id, student.id, nonce=reply.creation_nonce))
        assert fx.counts()["topics"] == 1 and fx.counts()["replies"] == 1


def test_foreign_keys_are_enforced(app):
    with app.app_context():
        teacher, _, group = _world()
        row = fx.topic(group, teacher)
        _integrity(_topic_insert(99999, teacher.id))
        _integrity(_topic_insert(group.id, 99999))
        _integrity(_reply_insert(99999, teacher.id))
        _integrity(_reply_insert(row.id, 99999))


def test_foreign_keys_are_plain_and_no_relationship_is_declared():
    expected = {
        DiscussionTopic: {("group_id", "groups.id"), ("author_id", "users.id")},
        DiscussionReply: {("topic_id", "discussion_topics.id"), ("author_id", "users.id")},
    }
    for model, pairs in expected.items():
        fks = list(model.__table__.foreign_keys)
        assert {(fk.parent.name, fk.target_fullname) for fk in fks} == pairs
        for fk in fks:
            assert fk.ondelete is None and fk.onupdate is None
        assert list(sa.inspect(model).relationships) == []


def test_indexes_match_the_real_reads():
    def shape(model):
        return {
            index.name: ([column.name for column in index.columns], index.unique)
            for index in model.__table__.indexes
        }

    assert shape(DiscussionTopic) == {
        "ix_discussion_topics_group_created_id": (["group_id", "created_at", "id"], False),
        "ix_discussion_topics_author_created_id": (["author_id", "created_at", "id"], False),
    }
    assert shape(DiscussionReply) == {
        "ix_discussion_replies_topic_created_id": (["topic_id", "created_at", "id"], False),
        "ix_discussion_replies_author_created_id": (["author_id", "created_at", "id"], False),
    }
    for model in (DiscussionTopic, DiscussionReply):
        unique = {
            tuple(column.name for column in constraint.columns)
            for constraint in model.__table__.constraints
            if isinstance(constraint, sa.UniqueConstraint)
        }
        unique |= {(column.name,) for column in model.__table__.columns if column.unique}
        assert unique == {("public_id",), ("creation_nonce",)}


def test_the_check_constraints_are_named_and_exact():
    def checks(model):
        return {
            constraint.name: " ".join(str(constraint.sqltext).split())
            for constraint in model.__table__.constraints
            if isinstance(constraint, sa.CheckConstraint)
        }

    assert checks(DiscussionTopic) == {
        "ck_discussion_topics_title_not_blank": "LENGTH(TRIM(title)) > 0",
        "ck_discussion_topics_body_not_blank": "LENGTH(TRIM(body)) > 0",
        "ck_discussion_topics_status_valid": "status IN ('open', 'locked')",
        "ck_discussion_topics_version_positive": "version > 0",
    }
    assert checks(DiscussionReply) == {
        "ck_discussion_replies_body_not_blank": "LENGTH(TRIM(body)) > 0",
    }


# ===========================================================================
# Immutability and no hard deletion
# ===========================================================================


@pytest.mark.parametrize("field, value", [
    ("title", "Changed"),
    ("body", "Changed"),
    ("creation_nonce", "f" * 64),
])
def test_topic_content_cannot_change_after_insertion(app, field, value):
    with app.app_context():
        teacher, _, group = _world()
        row = fx.topic(group, teacher)
        before = (row.title, row.body, row.creation_nonce)
        setattr(row, field, value)
        with pytest.raises(ValueError):
            db.session.commit()
        db.session.rollback()
        row = DiscussionTopic.query.one()
        assert (row.title, row.body, row.creation_nonce) == before


def test_topic_ownership_cannot_change_after_insertion(app):
    with app.app_context():
        teacher, student, group = _world()
        other_group = fx.hierarchy("Other")
        row = fx.topic(group, teacher)
        row.group_id = other_group.id
        with pytest.raises(ValueError):
            db.session.commit()
        db.session.rollback()
        row = DiscussionTopic.query.one()
        row.author_id = student.id
        with pytest.raises(ValueError):
            db.session.commit()
        db.session.rollback()
        row = DiscussionTopic.query.one()
        assert (row.group_id, row.author_id) == (group.id, teacher.id)


def test_only_the_lock_state_of_a_topic_may_change(app):
    with app.app_context():
        teacher, _, group = _world()
        row = fx.topic(group, teacher)
        row.status = "locked"
        row.version = 2
        row.updated_at = fx.LATER
        db.session.commit()
        row = DiscussionTopic.query.one()
        assert (row.status, row.version, row.updated_at) == ("locked", 2, fx.LATER)


def test_topics_and_replies_are_never_deleted_and_replies_never_change(app):
    with app.app_context():
        teacher, student, group = _world()
        row = fx.topic(group, teacher)
        reply = fx.reply(row, student)
        reply.body = "Rewritten"
        with pytest.raises(ValueError):
            db.session.commit()
        db.session.rollback()
        db.session.delete(DiscussionReply.query.one())
        with pytest.raises(ValueError):
            db.session.commit()
        db.session.rollback()
        db.session.delete(DiscussionTopic.query.one())
        with pytest.raises(ValueError):
            db.session.commit()
        db.session.rollback()
        assert fx.counts()["topics"] == 1 and fx.counts()["replies"] == 1
        assert DiscussionReply.query.one().body == "I read a short story."


# ===========================================================================
# Text normalisation
# ===========================================================================


@pytest.mark.parametrize("raw, expected", [
    ("  Weekend   reading  ", "Weekend reading"),
    ("Line one", "Line one"),
    ("<script>x</script>", "<script>x</script>"),
    ("x" * DISCUSSION_TITLE_MAX_LENGTH, "x" * DISCUSSION_TITLE_MAX_LENGTH),
    ("  " + "x" * DISCUSSION_TITLE_MAX_LENGTH + "   ", "x" * DISCUSSION_TITLE_MAX_LENGTH),
])
def test_titles_are_normalised_before_validation(raw, expected):
    assert text.normalize_title(raw) == (expected, None)


@pytest.mark.parametrize("raw, code", [
    (None, text.MISSING),
    ("", text.MISSING),
    ("   ", text.MISSING),
    ("Tab\there", text.CONTROL),
    ("New\nline", text.CONTROL),
    ("Bell\x07", text.CONTROL),
    ("Del\x7f", text.CONTROL),
    ("x" * (DISCUSSION_TITLE_MAX_LENGTH + 1), text.TOO_LONG),
    (["not", "text"], text.MISSING),
])
def test_invalid_titles_are_rejected_with_a_code(raw, code):
    assert text.normalize_title(raw) == (None, code)


@pytest.mark.parametrize("raw, expected", [
    ("\r\n  First\r\nSecond\rThird\n\n\tIndented  \n", "First\nSecond\nThird\n\n\tIndented"),
    ("<img src=x onerror=alert(1)>", "<img src=x onerror=alert(1)>"),
    ("x" * DISCUSSION_BODY_MAX_LENGTH, "x" * DISCUSSION_BODY_MAX_LENGTH),
])
def test_bodies_keep_their_formatting_and_are_measured_after_normalisation(raw, expected):
    assert text.normalize_body(raw) == (expected, None)


@pytest.mark.parametrize("raw, code", [
    (None, text.MISSING),
    ("\r\n\t  \n", text.MISSING),
    ("Nul\x00", text.CONTROL),
    ("Escape\x1b[31m", text.CONTROL),
    ("x" * (DISCUSSION_BODY_MAX_LENGTH + 1), text.TOO_LONG),
    ("x" * DISCUSSION_BODY_MAX_LENGTH + "\r\n", None),
])
def test_invalid_bodies_are_rejected_with_a_code(raw, code):
    body, error = text.normalize_body(raw)
    assert error == code
    assert (body is None) == (code is not None)


def test_previews_collapse_whitespace_and_clip():
    assert text.preview_text("One\n\ntwo\tthree") == "One two three"
    clipped = text.preview_text("word " * 100)
    assert len(clipped) == text.PREVIEW_LENGTH and clipped.endswith("…")


# ===========================================================================
# Signed tokens
# ===========================================================================


_G = str(uuid.uuid4())
_T = str(uuid.uuid4())
_A = str(uuid.uuid4())
_B = str(uuid.uuid4())


def test_each_token_round_trips_with_exactly_its_own_shape(app):
    with app.app_context():
        create = tokens.load_create_token(tokens.make_create_token(_A, _G), _A, _G)
        assert set(create) == {"purpose", "actor_public_id", "group_public_id", "nonce"}
        assert re.fullmatch(r"[0-9a-f]{64}", create["nonce"])

        reply = tokens.load_reply_token(tokens.make_reply_token(_A, _G, _T, 3), _A, _G, _T)
        assert reply["topic_version"] == 3
        assert set(reply) == {"purpose", "actor_public_id", "group_public_id",
                              "topic_public_id", "topic_version", "nonce"}

        for action in tokens.ACTIONS:
            moderation = tokens.load_moderation_token(
                tokens.make_moderation_token(_A, _G, _T, 2, action), _A, _G, _T, action
            )
            assert moderation["action"] == action and moderation["topic_version"] == 2
            assert "nonce" not in moderation

        fixed = fx.nonce()
        again = tokens.load_create_token(tokens.make_create_token(_A, _G, fixed), _A, _G)
        assert again["nonce"] == fixed


def test_tokens_are_bound_to_actor_group_topic_and_action(app):
    with app.app_context():
        create = tokens.make_create_token(_A, _G)
        assert tokens.load_create_token(create, _B, _G) is None
        assert tokens.load_create_token(create, _A, _T) is None

        reply = tokens.make_reply_token(_A, _G, _T, 1)
        assert tokens.load_reply_token(reply, _B, _G, _T) is None
        assert tokens.load_reply_token(reply, _A, _B, _T) is None
        assert tokens.load_reply_token(reply, _A, _G, _B) is None

        lock = tokens.make_moderation_token(_A, _G, _T, 1, tokens.ACTION_LOCK)
        assert tokens.load_moderation_token(lock, _A, _G, _T, tokens.ACTION_REOPEN) is None
        assert tokens.load_moderation_token(lock, _B, _G, _T, tokens.ACTION_LOCK) is None
        assert tokens.load_moderation_token(lock, _A, _G, _B, tokens.ACTION_LOCK) is None


def test_a_token_of_one_purpose_is_refused_by_every_other(app):
    with app.app_context():
        create = tokens.make_create_token(_A, _G)
        reply = tokens.make_reply_token(_A, _G, _T, 1)
        lock = tokens.make_moderation_token(_A, _G, _T, 1, tokens.ACTION_LOCK)
        message = message_tokens.make_token(message_tokens.PURPOSE_REPLY, _A, _T)
        for candidate in (reply, lock, message):
            assert tokens.load_create_token(candidate, _A, _G) is None
        for candidate in (create, lock, message):
            assert tokens.load_reply_token(candidate, _A, _G, _T) is None
        for candidate in (create, reply, message):
            assert tokens.load_moderation_token(candidate, _A, _G, _T, tokens.ACTION_LOCK) is None
        assert message_tokens.load_token(message_tokens.PURPOSE_REPLY, reply, _A, _T) is None


def test_tampered_oversized_missing_and_expired_tokens_are_refused(app, monkeypatch):
    with app.app_context():
        good = tokens.make_reply_token(_A, _G, _T, 1)
        for candidate in (None, "", 42, good[:-2] + "xx", "x" + good[1:], good + "a" * 1100):
            assert tokens.load_reply_token(candidate, _A, _G, _T) is None
        past = int(time.time()) - tokens.TOKEN_MAX_AGE_SECONDS - 60
        monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: past)
        expired = tokens.make_reply_token(_A, _G, _T, 1)
        monkeypatch.undo()
        assert tokens.load_reply_token(expired, _A, _G, _T) is None
        assert tokens.load_reply_token(tokens.make_reply_token(_A, _G, _T, 1), _A, _G, _T)


@pytest.mark.parametrize("purpose, mutate", [
    (tokens.PURPOSE_CREATE, lambda body: body.update(extra="x")),
    (tokens.PURPOSE_CREATE, lambda body: body.pop("nonce")),
    (tokens.PURPOSE_CREATE, lambda body: body.update(nonce="A" * 64)),
    (tokens.PURPOSE_CREATE, lambda body: body.update(group_public_id=7)),
    (tokens.PURPOSE_REPLY, lambda body: body.update(topic_version=0)),
    (tokens.PURPOSE_REPLY, lambda body: body.update(topic_version=True)),
    (tokens.PURPOSE_REPLY, lambda body: body.update(topic_version="1")),
    (tokens.PURPOSE_REPLY, lambda body: body.update(topic_version=2**31)),
    (tokens.PURPOSE_REPLY, lambda body: body.update(purpose=tokens.PURPOSE_CREATE)),
    (tokens.PURPOSE_MODERATE, lambda body: body.update(action="delete")),
    (tokens.PURPOSE_MODERATE, lambda body: body.update(nonce=fx.nonce())),
])
def test_a_genuinely_signed_payload_of_the_wrong_shape_is_refused(app, purpose, mutate):
    with app.app_context():
        body = {
            "purpose": purpose,
            "actor_public_id": _A,
            "group_public_id": _G,
        }
        if purpose != tokens.PURPOSE_CREATE:
            body.update(topic_public_id=_T, topic_version=1)
        if purpose != tokens.PURPOSE_MODERATE:
            body["nonce"] = fx.nonce()
        else:
            body["action"] = tokens.ACTION_LOCK
        mutate(body)
        signed = URLSafeTimedSerializer(
            app.config["SECRET_KEY"], salt=tokens._SALTS[purpose]
        ).dumps(body)
        loaders = {
            tokens.PURPOSE_CREATE: lambda: tokens.load_create_token(signed, _A, _G),
            tokens.PURPOSE_REPLY: lambda: tokens.load_reply_token(signed, _A, _G, _T),
            tokens.PURPOSE_MODERATE: lambda: tokens.load_moderation_token(
                signed, _A, _G, _T, tokens.ACTION_LOCK),
        }
        assert loaders[purpose]() is None


def test_an_unknown_moderation_action_cannot_be_minted(app):
    with app.app_context():
        with pytest.raises(ValueError):
            tokens.make_moderation_token(_A, _G, _T, 1, "delete")


def test_salts_are_distinct_and_versioned():
    salts = list(tokens._SALTS.values())
    assert len(set(salts)) == len(tokens.PURPOSES) == 3
    assert all(salt.endswith(".phase4-m12.v1") for salt in salts)
