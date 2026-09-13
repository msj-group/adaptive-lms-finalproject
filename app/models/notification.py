import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import NotificationKind

#: The exact allowed ``kind`` values, rendered once into the database
#: CHECK constraint below so the application-level ``@validates`` guard
#: and the schema can never drift apart.
_KIND_VALUES = tuple(kind.value for kind in NotificationKind)
_KIND_CHECK_SQL = "kind IN (" + ", ".join(f"'{value}'" for value in _KIND_VALUES) + ")"


class Notification(db.Model):
    """One stored, already-delivered in-app notification for exactly one
    recipient (M14).

    A Notification is a **personal historical record**, not a live view of
    the domain. It is written once, immediately after a high-signal domain
    mutation has already committed, and afterwards only its ``read_at``
    ever changes. There is no edit route, no delete route, and no cascade:
    losing access to the thing a notification is about never rewrites or
    removes the notification, and never grants access either -- opening
    one always re-authorizes the target from scratch (see
    ``app/blueprints/notifications/routes.py``).

    **Recipients are Students and Teachers only.** ``recipient_id`` is a
    plain foreign key into the shared ``users`` table, which holds every
    role, so -- exactly like ``Enrollment.student_id`` and
    ``GroupTeacherAssignment.teacher_id`` -- the FK proves the referenced
    row exists, never that it is a Student/Teacher or that the account is
    still active. Every producer in
    ``app/services/notification_delivery.py`` re-verifies role **and**
    active account in the SQL that selects recipients, and the inbox
    itself is behind ``roles_required(STUDENT, TEACHER)``.

    **Content is plain text.** ``title`` and ``message`` are short,
    server-authored, escaped-on-render strings. They deliberately store no
    HTML, no serialized ORM row, no JSON payload, no actor identity ("who
    did it"), and no internal numeric id -- only the object *names* a
    recipient may already see. There is likewise no polymorphic
    ``source_type``/``source_id`` pair: a notification points at a place
    (``target_path``), not at a row.

    Phase 4 / M11 ``message_received`` rows are the one deliberate
    exception to "no actor identity": a private message is meaningless
    without saying who sent it, so they name the sender's display name
    and a clipped thread subject -- never the message body.

    Phase 4 / M12 ``discussion_topic_created`` rows name the Group and the
    topic title only -- never the topic body or any reply -- and point at
    the exact Student topic page, which re-proves the current Enrollment
    and operational chain every time it is opened.

    ``target_path`` is a **server-generated**, role-namespaced relative
    path (``/student/...`` or ``/teacher/...``), built and re-validated by
    ``app/services/notification_targets.py`` -- or, for ``message_received``
    only, the exact shared ``/messages/threads/<thread_public_id>`` shape
    that module admits and the thread route re-authorizes. It is never rendered as a
    clickable link; opening a notification POSTs to a notification-owned
    route that re-validates the stored value, marks the row read, and
    redirects -- falling back to the inbox if the stored value is somehow
    unsafe.

    ``created_at`` is UTC (stored naive, like every other timestamp in
    this project) and rendered in ``APP_TIMEZONE`` through the M09
    timezone utilities. ``read_at`` is NULL while unread and is set once,
    to the current UTC moment, the first time the recipient reads it --
    marking an already-read notification read again is a no-op, so both
    read routes are idempotent.

    Two composite indexes serve the three bounded queries M14 issues,
    because the two inbox filters sort on different key suffixes:

    - ``ix_notifications_recipient_unread_created`` (``recipient_id``,
      ``read_at``, ``created_at``) -- the header unread ``COUNT`` (which
      it covers outright) and the newest-first **unread** filter;
    - ``ix_notifications_recipient_created_id`` (``recipient_id``,
      ``created_at``, ``id``) -- the newest-first **all** filter, whose
      ``ORDER BY created_at DESC, id DESC`` the unread index cannot
      satisfy (``read_at`` sits between the equality column and the sort
      columns, so MySQL resolves it with ``Using filesort``).

    The ``recipient_id`` leftmost prefix they share is also what the
    ``recipient_id`` foreign key uses, so no separate single-column index
    is declared for it.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        db.CheckConstraint(_KIND_CHECK_SQL, name="ck_notifications_kind_valid"),
        db.Index(
            "ix_notifications_recipient_unread_created",
            "recipient_id",
            "read_at",
            "created_at",
        ),
        db.Index(
            "ix_notifications_recipient_created_id",
            "recipient_id",
            "created_at",
            "id",
        ),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    recipient_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=False,
    )
    kind = db.Column(db.String(48), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    target_path = db.Column(db.String(512), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    read_at = db.Column(db.DateTime, nullable=True)

    recipient = db.relationship("User")

    @validates("kind")
    def validate_kind(self, _key, value):
        if value not in set(_KIND_VALUES):
            raise ValueError(f"Invalid notification kind: {value}")
        return value

    @property
    def is_unread(self):
        return self.read_at is None
