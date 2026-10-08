from app.models.code_types import CODE_COLLATION
import uuid

from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import AnnouncementScope, AnnouncementStatus
from app.models.submission_feedback import whole_second_utc

#: The ``title`` column's own width, declared once so the column, the
#: form's ``Length`` validator and the plain-text normaliser cannot
#: disagree -- the same arrangement M04A uses for ``QUIZ_TITLE_MAX_LENGTH``
#: and M08 for ``GRADE_CATEGORY_TITLE_MAX_LENGTH``.
ANNOUNCEMENT_TITLE_MAX_LENGTH = 150

#: The ``body`` column's own width. 5,000 characters is a notice, not a
#: document: M09 adds no attachment, no upload and no rich text, so an
#: announcement that needs more than this is describing learning content,
#: which already has its own module.
ANNOUNCEMENT_BODY_MAX_LENGTH = 5000

#: The exact allowed ``scope`` / ``status`` values, rendered once into the
#: database CHECK constraints below so the application-level
#: ``@validates`` guards and the schema can never drift apart -- exactly
#: how ``app/models/notification.py`` renders its ``kind`` CHECK.
_SCOPE_VALUES = tuple(scope.value for scope in AnnouncementScope)
_STATUS_VALUES = tuple(status.value for status in AnnouncementStatus)
_SCOPE_CHECK_SQL = "scope IN (" + ", ".join(f"'{v}'" for v in _SCOPE_VALUES) + ")"
_STATUS_CHECK_SQL = "status IN (" + ", ".join(f"'{v}'" for v in _STATUS_VALUES) + ")"

#: Exactly one target per scope, expressed so the database can prove it.
#: A ``center`` row carries neither target, a ``course`` row carries only
#: ``course_id``, a ``group`` row carries only ``group_id``. There is no
#: shape in which both are set, and no shape in which a scope that needs
#: one has none.
_SCOPE_TARGET_CHECK_SQL = (
    "(scope = 'center' AND course_id IS NULL AND group_id IS NULL)"
    " OR (scope = 'course' AND course_id IS NOT NULL AND group_id IS NULL)"
    " OR (scope = 'group' AND group_id IS NOT NULL AND course_id IS NULL)"
)

#: The complete state/timestamp truth table, again as something the
#: database proves rather than something the application promises.
_STATUS_TIMESTAMP_CHECK_SQL = (
    "(status = 'draft' AND published_at IS NULL AND withdrawn_at IS NULL)"
    " OR (status = 'published' AND published_at IS NOT NULL AND withdrawn_at IS NULL)"
    " OR (status = 'withdrawn' AND published_at IS NOT NULL"
    " AND withdrawn_at IS NOT NULL AND withdrawn_at >= published_at)"
)


class Announcement(db.Model):
    """One center-, course- or group-scoped announcement (Phase 4 / M09).

    **An announcement is a communication, and only that.** It is not a
    grade, a calendar event, a private message, a discussion thread, a
    file, or learning content. Nothing here carries a score, a weight, a
    deadline, a recipient list, an attachment, a comment, a reaction, an
    acknowledgement or a read receipt, and no placeholder is left for
    one: every one of those is a different object with different rules,
    and several of them are modules nobody has approved yet.

    **Scope is the address, and the address is academic, never personal.**
    ``scope`` says *where* an announcement is posted -- the whole center,
    one Course, or one Group -- and ``course_id`` / ``group_id`` name that
    place. Exactly one combination is legal per scope
    (``ck_announcements_scope_target``), so "which announcements is this
    Student allowed to read?" is answered by joining the places they
    currently belong to, never by consulting a stored list of people. A
    group-scoped announcement deliberately carries **no** ``course_id``:
    its Course is ``group.course``, resolved through the Group, so the two
    can never disagree after an administrator retargets a Group.

    **Visibility is current, not historical.** An announcement is a notice
    on a board: whoever is standing in front of that board today can read
    it, and whoever has left cannot. A Student whose Enrollment is
    withdrawn, a Teacher whose assignment is removed, and anybody under an
    archived academic chain all stop being able to open a scoped
    announcement they could read yesterday -- which is the opposite of the
    Gradebook and Attendance rules, and deliberately so: a released grade
    is a statement about somebody's past that must survive, while a notice
    is a statement about the present. A Notification about an announcement
    is a personal historical row and **does** survive, but following it
    re-authorizes the announcement from scratch and lands on the ordinary
    non-disclosing 404 when access has ended.

    **The lifecycle is one-way.** ``draft -> published -> withdrawn``, and
    no edge back. A draft is author-side working text no reader can reach.
    Publishing freezes ``title``, ``body``, ``scope``, ``course_id``,
    ``group_id`` and ``author_id`` permanently -- people have already read
    them, and silently rewriting what somebody was told is exactly the
    failure this refuses. Withdrawal hides the announcement immediately
    and is terminal: there is no republish, no restore, no unwithdraw, no
    archive, no duplicate and no delete endpoint anywhere in M09, and none
    exists server-side to be re-enabled later. A correction is a new
    announcement.

    **Plain text only.** ``title`` and ``body`` are stored exactly as the
    author typed them after whitespace/control-character normalisation,
    and every surface renders them through Jinja's autoescaping. No HTML
    is stored, none is rendered, and nothing in M09 is ever marked safe.

    **``version`` is the concurrency signal, not a revision history.** It
    starts at 1 and increases by exactly one per *meaningful* change --
    a draft edit that really changed something, the publication, the
    withdrawal -- so a signed form token can detect that the row moved
    under it, including an A -> B -> A round trip and two edits inside one
    whole second, neither of which a timestamp comparison could catch. A
    draft save whose normalized title, body, scope and target all equal
    the stored ones is a **no-op**: neither this column nor ``updated_at``
    moves. No row is kept per version.

    **``author_id`` is immutable and is not an authorization fact.** It
    records who wrote the announcement, for the management surfaces. It
    does **not** decide who may manage it: every actively assigned Teacher
    of a Group is an equal collaborator on that Group's announcements --
    exactly as they already are on its Units, Assignments, Quizzes,
    attendance and gradebook -- and an Administrator manages all three
    scopes. A foreign key into ``users`` proves the row exists, never that
    it is still a Teacher's or an Administrator's, so every write path
    re-checks the *acting* account's role and status in SQL.

    Database invariants (final defense only):

    - ``ck_announcements_scope_valid`` / ``ck_announcements_status_valid``
      -- the two closed sets as literal ``IN`` lists rather than MySQL
      ``ENUM`` columns, the convention every other closed-set column in
      this project uses, so the SQLite test backend enforces them
      identically and adding a member stays a visible schema change.
    - ``ck_announcements_scope_target`` -- exactly one target per scope
      (see :data:`_SCOPE_TARGET_CHECK_SQL`).
    - ``ck_announcements_status_timestamps`` -- the complete state /
      timestamp truth table, including ``withdrawn_at >= published_at``,
      so a row can never claim it was withdrawn before it existed.
    - ``ck_announcements_version_positive`` (``version > 0``).

    Indexes -- one per real query path, and no more:

    - ``ix_announcements_scope_status_published``
      (``scope``, ``status``, ``published_at``, ``id``) -- the center feed
      (``scope = 'center' AND status = 'published'`` ordered newest
      first). Every feed's ordering is ``published_at DESC, id DESC``, and
      a composite whose two trailing columns are exactly that pair can
      serve it without a filesort.
    - ``ix_announcements_course_status_published``
      (``course_id``, ``status``, ``published_at``, ``id``) -- the
      course-scoped feed, and the leftmost prefix the ``course_id``
      foreign key requires.
    - ``ix_announcements_group_status_published``
      (``group_id``, ``status``, ``published_at``, ``id``) -- the
      group-scoped feed and the Teacher's per-Group management list, and
      the leftmost prefix the ``group_id`` foreign key requires.
    - ``ix_announcements_author_status_created``
      (``author_id``, ``status``, ``created_at``, ``id``) -- "what have I
      written?", and the leftmost prefix the ``author_id`` foreign key
      requires.
    - ``ix_announcements_status_scope_created``
      (``status``, ``scope``, ``created_at``, ``id``) -- the
      Administrator's management list, whose two filters are exactly
      status and scope and whose order is ``created_at DESC, id DESC``.

    **No MySQL execution plan has been measured for this table.** As with
    every earlier index in this project, this is a reasoned design pending
    an authorized real ``EXPLAIN``.

    **No ORM relationship is declared in either direction**, the same
    choice M02 / M03 / M06 / M07 / M08 made: every read goes through
    explicit joins in ``app/services/announcement_queries.py`` and returns
    plain presentation dicts, so rendering a feed can never trigger a lazy
    load or an ORM-driven authorization decision, and there is no
    ``cascade`` / ``delete-orphan`` configuration anywhere that could
    remove an announcement when a Course, a Group or an account is
    touched. All three foreign keys are plain references with **no**
    ``ondelete`` and **no** ``onupdate``.
    """

    __tablename__ = "announcements"
    __table_args__ = (
        db.CheckConstraint(_SCOPE_CHECK_SQL, name="ck_announcements_scope_valid"),
        db.CheckConstraint(_STATUS_CHECK_SQL, name="ck_announcements_status_valid"),
        db.CheckConstraint(
            _SCOPE_TARGET_CHECK_SQL, name="ck_announcements_scope_target"
        ),
        db.CheckConstraint(
            _STATUS_TIMESTAMP_CHECK_SQL, name="ck_announcements_status_timestamps"
        ),
        db.CheckConstraint("version > 0", name="ck_announcements_version_positive"),
        db.Index(
            "ix_announcements_scope_status_published",
            "scope",
            "status",
            "published_at",
            "id",
        ),
        db.Index(
            "ix_announcements_course_status_published",
            "course_id",
            "status",
            "published_at",
            "id",
        ),
        db.Index(
            "ix_announcements_group_status_published",
            "group_id",
            "status",
            "published_at",
            "id",
        ),
        db.Index(
            "ix_announcements_author_status_created",
            "author_id",
            "status",
            "created_at",
            "id",
        ),
        db.Index(
            "ix_announcements_status_scope_created",
            "status",
            "scope",
            "created_at",
            "id",
        ),
    )

    id = db.Column(db.BigInteger(), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    #: Who wrote it. Set once, at creation, and never written again by any
    #: code path -- see the class docstring.
    author_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("users.id"),
        nullable=False,
    )
    scope = db.Column(db.String(32, collation=CODE_COLLATION), nullable=False)
    #: NULL unless ``scope`` is ``course``. Never duplicated onto a
    #: group-scoped row.
    course_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("courses.id"),
        nullable=True,
    )
    #: NULL unless ``scope`` is ``group``.
    group_id = db.Column(
        db.BigInteger(),
        db.ForeignKey("groups.id"),
        nullable=True,
    )
    title = db.Column(db.String(ANNOUNCEMENT_TITLE_MAX_LENGTH), nullable=False)
    body = db.Column(db.String(ANNOUNCEMENT_BODY_MAX_LENGTH), nullable=False)
    status = db.Column(
        db.String(32, collation=CODE_COLLATION), nullable=False, default=AnnouncementStatus.DRAFT.value
    )
    published_at = db.Column(db.DateTime, nullable=True)
    withdrawn_at = db.Column(db.DateTime, nullable=True)
    #: 1 on creation, +1 per meaningful change. See the class docstring --
    #: the stale-form signal, not a revision history.
    version = db.Column(db.Integer, nullable=False, default=1)
    #: Defense in depth only; the write path always supplies its own
    #: post-lock whole-second moment and uses the SAME value for both
    #: columns on creation. There is deliberately no ``onupdate`` hook: an
    #: implicit one would bypass that truncation and would fire on a no-op
    #: draft save, which must leave every timestamp alone.
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    @validates("scope")
    def validate_scope(self, _key, value):
        if value not in set(_SCOPE_VALUES):
            raise ValueError(f"Invalid announcement scope: {value}")
        return value

    @validates("status")
    def validate_status(self, _key, value):
        if value not in set(_STATUS_VALUES):
            raise ValueError(f"Invalid announcement status: {value}")
        return value

    @validates("version")
    def validate_version(self, _key, value):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("Announcement version must be a positive integer")
        return value

    @property
    def is_draft(self):
        return self.status == AnnouncementStatus.DRAFT.value

    @property
    def is_published(self):
        return self.status == AnnouncementStatus.PUBLISHED.value

    @property
    def is_withdrawn(self):
        return self.status == AnnouncementStatus.WITHDRAWN.value
