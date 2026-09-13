"""Shared fixtures for the Phase 4 / M12 Group discussion test modules.

Kept in one module -- like ``tests/message_fixtures.py`` -- so the model,
transaction, route, notification and migration suites all build the same
classroom and the same topics. The academic-chain, account and session
helpers are the generic ones M11 already proved, re-exported here.

Most helpers write rows **directly**, so a test about (say) a withdrawn
Enrollment is not also a test of the creation form. The route-driven
helpers at the bottom read the signed state token out of the page the
server actually rendered rather than minting one in the test, so they keep
proving that each page carries a usable token.
"""

import re
import secrets
from datetime import datetime

from app.extensions import db
from app.models import (
    DiscussionReply,
    DiscussionTopic,
    DiscussionTopicStatus,
    Notification,
)
from tests.message_fixtures import (  # noqa: F401 -- re-exported for the M12 suites
    ACTIVE,
    ADMIN,
    ARCHIVED,
    PW,
    RESEARCHER,
    STUDENT,
    TEACHER,
    ancestors,
    assign,
    assignment_of,
    enroll,
    enrollment_of,
    fresh_identity,
    hierarchy,
    login_as,
    logout,
    set_status,
    user,
)

OPEN = DiscussionTopicStatus.OPEN.value
LOCKED = DiscussionTopicStatus.LOCKED.value

#: Whole-second naive-UTC reference moments.
NOW = datetime(2026, 5, 13, 9, 0, 0)
LATER = datetime(2026, 5, 13, 10, 30, 0)

TEACHER_OVERVIEW = "/teacher/discussions"
STUDENT_OVERVIEW = "/student/discussions"

_STATE_PATTERNS = {
    name: re.compile(rf'name="{name}" value="([^"]+)"')
    for name in ("topic_state", "reply_state", "moderation_state")
}


def nonce():
    return secrets.token_hex(32)


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------


def teacher_list(group_public_id):
    return f"/teacher/groups/{group_public_id}/discussions"


def teacher_new(group_public_id):
    return f"/teacher/groups/{group_public_id}/discussions/new"


def teacher_topic(group_public_id, topic_public_id):
    return f"/teacher/groups/{group_public_id}/discussions/{topic_public_id}"


def teacher_reply(group_public_id, topic_public_id):
    return teacher_topic(group_public_id, topic_public_id) + "/reply"


def teacher_lock(group_public_id, topic_public_id):
    return teacher_topic(group_public_id, topic_public_id) + "/lock"


def teacher_reopen(group_public_id, topic_public_id):
    return teacher_topic(group_public_id, topic_public_id) + "/reopen"


def student_list(group_public_id):
    return f"/student/groups/{group_public_id}/discussions"


def student_topic(group_public_id, topic_public_id):
    return f"/student/groups/{group_public_id}/discussions/{topic_public_id}"


def student_reply(group_public_id, topic_public_id):
    return student_topic(group_public_id, topic_public_id) + "/reply"


def topic_url(role, group_public_id, topic_public_id):
    builder = teacher_topic if role == TEACHER else student_topic
    return builder(group_public_id, topic_public_id)


def reply_url(role, group_public_id, topic_public_id):
    builder = teacher_reply if role == TEACHER else student_reply
    return builder(group_public_id, topic_public_id)


def list_url(role, group_public_id):
    return teacher_list(group_public_id) if role == TEACHER else student_list(group_public_id)


# ---------------------------------------------------------------------------
# The classroom and its discussion rows, written directly
# ---------------------------------------------------------------------------


def classroom(label="A", teacher_email="teacher@example.com", student_email="student@example.com",
              teacher_name=None, student_name=None):
    """An operational Group with one actively assigned Teacher and one
    actively enrolled Student: ``(teacher, student, group)``."""
    group = hierarchy(label)
    teacher = user(teacher_email, TEACHER, name=teacher_name)
    student = user(student_email, STUDENT, name=student_name)
    assign(group, teacher)
    enroll(group, student)
    return teacher, student, group


def topic(group, author, title="Weekend reading", body="What did you read this weekend?",
          status=OPEN, version=1, created_at=NOW):
    row = DiscussionTopic(
        group_id=group.id,
        author_id=author.id,
        title=title,
        body=body,
        status=status,
        version=version,
        creation_nonce=nonce(),
        created_at=created_at,
        updated_at=created_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def reply(topic_row, author, body="I read a short story.", created_at=LATER):
    row = DiscussionReply(
        topic_id=topic_row.id,
        author_id=author.id,
        body=body,
        creation_nonce=nonce(),
        created_at=created_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def set_topic_state(topic_row, status):
    """Move a topic's lock state the way the moderation transaction does:
    status and version together."""
    topic_row.status = status
    topic_row.version = topic_row.version + 1
    db.session.commit()
    return topic_row


def counts():
    return {
        "topics": DiscussionTopic.query.count(),
        "replies": DiscussionReply.query.count(),
        "notifications": Notification.query.count(),
    }


# ---------------------------------------------------------------------------
# Through the application's own routes
# ---------------------------------------------------------------------------


def state_from(html, name):
    """The signed state token named `name` the rendered page carries, or
    ``None``."""
    match = _STATE_PATTERNS[name].search(html)
    return match.group(1) if match else None


def page_state(client, url, name):
    return state_from(client.get(url).get_data(as_text=True), name)


def create_via_route(client, group_public_id, title="Weekend reading",
                     body="What did you read this weekend?", token=None):
    if token is None:
        token = page_state(client, teacher_new(group_public_id), "topic_state")
    return client.post(
        teacher_new(group_public_id),
        data={"title": title, "body": body, "topic_state": token},
    )


def reply_via_route(client, role, group_public_id, topic_public_id, body="A reply.",
                    token=None):
    if token is None:
        token = page_state(client, topic_url(role, group_public_id, topic_public_id),
                           "reply_state")
    return client.post(
        reply_url(role, group_public_id, topic_public_id),
        data={"body": body, "reply_state": token},
    )


def moderate_via_route(client, group_public_id, topic_public_id, action, token=None):
    if token is None:
        token = page_state(client, teacher_topic(group_public_id, topic_public_id),
                           "moderation_state")
    builder = teacher_lock if action == "lock" else teacher_reopen
    return client.post(
        builder(group_public_id, topic_public_id), data={"moderation_state": token}
    )


def location_public_id(response):
    """The last path segment of a redirect, without query or fragment."""
    location = response.headers["Location"].split("#", 1)[0].split("?", 1)[0]
    return location.rstrip("/").split("/")[-1]
