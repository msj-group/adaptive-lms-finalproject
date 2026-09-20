"""One versioned research consent document (Phase 6 / M01).

**Ethics text, not marketing text.** A document carries the exact English
wording a Student is asked to agree to, a version identifier, and a digest
of what was written. Nothing here claims the wording is ethics-approved:
M01 builds the infrastructure that makes an approval *recordable*, and the
project ships no seeded consent text at all.

**The lifecycle freezes the wording.** See
:class:`~app.models.enums.ResearchConsentDocumentStatus`. Only a ``draft``
may be edited, and a draft is never shown to a Student as valid consent
text. Activation freezes ``version_identifier``, ``title``, ``body`` and
``body_digest`` **forever** and supersedes whatever was active before.
Changing the wording means activating a new document; the old one keeps
saying what the people who accepted it actually read.

**At most one document is current.** ``current_marker`` is ``1`` exactly
while the document is ``active`` and ``NULL`` otherwise, and
``uq_research_consent_documents_current`` is unique over it. Both MySQL and
SQLite allow many NULLs in a unique index and exactly one ``1``, so the
cross-row "only one active document" invariant is a real database
constraint here rather than a promise -- and the write path still takes the
documented lock order and re-proves everything after its locks, because a
constraint violation is a crash, not a workflow.

**Nothing is ever physically deleted**, there is no cascade and no
``ondelete``; every foreign key is a plain reference.

Database invariants (final defense only):

- ``public_id`` and ``uq_research_consent_documents_version`` unique;
- ``uq_research_consent_documents_current`` -- one current document;
- ``ck_research_consent_documents_status_valid`` -- the closed set as a
  literal ``IN`` list, the project's convention;
- ``ck_research_consent_documents_current_marker`` -- the marker is set
  exactly while ``active``;
- ``ck_research_consent_documents_activation_pair`` and
  ``ck_research_consent_documents_supersession_pair`` -- an attribution
  moment and its actor are stored together;
- ``ck_research_consent_documents_lifecycle_state`` -- a draft was never
  activated or superseded; an active document was activated and not
  superseded; a superseded document was activated, then superseded no
  earlier than that;
- ``ck_research_consent_documents_digest_format`` -- 64 lowercase hex
  characters;
- ``ck_research_consent_documents_body_present`` -- wording is never empty;
- ``ck_research_consent_documents_timestamps_ordered``.

Conditions a CHECK cannot express are stated here rather than hidden: that
an attributed account is an active Administrator, and that a frozen
document's wording never changes. The application proves each against
locked rows, and the ORM guards below refuse the flush outright.

Indexes -- one per real query path:

- ``ix_research_consent_documents_status_id`` (``status``, ``id``) -- the
  Administrator list, an equality on ``status`` ordered by ``id DESC``, and
  the "current document" lookup;
- ``ix_research_consent_documents_created_by_id``,
  ``ix_research_consent_documents_activated_by_id`` and
  ``ix_research_consent_documents_superseded_by_id`` -- declared for the
  three foreign keys InnoDB requires an index for, rather than left
  implicit.

**No MySQL execution plan has been measured for this table.**

**No ORM relationship is declared in either direction**: every read is an
explicit query in ``app/services/research_queries.py``, so rendering a
document can never trigger a lazy load, and no ``delete-orphan``
configuration exists that could remove anything.
"""

import hashlib
import uuid

from sqlalchemy import event, inspect
from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import ResearchConsentDocumentStatus
from app.models.submission_feedback import whole_second_utc

#: The ``version_identifier`` column's own width, declared once so the
#: column, the normaliser and the form wording cannot disagree.
CONSENT_VERSION_MAX_LENGTH = 40

#: The ``title`` column's own width.
CONSENT_TITLE_MAX_LENGTH = 200

#: The longest consent wording the application accepts, in **characters**.
#: The column itself is ``TEXT``, the project's convention for long plain
#: text, which MySQL bounds at 65,535 **bytes**: 15,000 characters is
#: 60,000 bytes even if every single one is a four-byte character, so the
#: value an Administrator is told and the value the database can hold are
#: the same number on every backend. That is roughly five printed pages --
#: a full participant information sheet -- and the application, not the
#: column, is what refuses more.
CONSENT_BODY_MAX_LENGTH = 15000

#: A SHA-256 hex digest.
CONSENT_DIGEST_LENGTH = 64

#: The value ``current_marker`` carries exactly while the document is
#: ``active``.
CURRENT_MARKER = 1


class ResearchHistoryError(RuntimeError):
    """A write that would edit frozen consent text, rewrite append-only
    research consent history, or delete either of them.

    Its own class, deliberately separate from the financial
    ``FinancialHistoryError``: research history and financial history are
    different obligations to different people, and a handler for one must
    never silently absorb the other.
    """


def consent_digest(version_identifier, title, body):
    """The stable digest of exactly what a Student is asked to accept.

    SHA-256 over the three frozen fields, each length-prefixed, so no
    combination of values can be re-split into a different document (a
    title ending in what looks like a separator cannot borrow the body's
    first line). The digest proves *which text* a consent event accepted,
    which is the whole point of storing it beside the version.
    """
    parts = []
    for value in (version_identifier, title, body):
        text = value if isinstance(value, str) else ""
        encoded = text.encode("utf-8")
        parts.append(str(len(encoded)).encode("ascii"))
        parts.append(b":")
        parts.append(encoded)
        parts.append(b"|")
    return hashlib.sha256(b"".join(parts)).hexdigest()


_STATUS_VALUES = tuple(status.value for status in ResearchConsentDocumentStatus)
_STATUS_CHECK_SQL = "status IN (" + ", ".join(f"'{v}'" for v in _STATUS_VALUES) + ")"

#: The marker exists exactly while the document is the current one.
#:
#: ``current_marker IS NOT NULL`` is not redundant beside ``= 1``. A CHECK
#: that evaluates to NULL **passes** in SQL, and ``NULL = 1`` is NULL -- so
#: without the explicit NOT NULL test an ``active`` row carrying no marker
#: would satisfy this constraint, and two such rows would then satisfy
#: ``uq_research_consent_documents_current`` as well (a unique index admits
#: many NULLs). That would silently defeat the whole one-current-document
#: invariant, which is the reason the marker exists at all.
_CURRENT_MARKER_SQL = (
    "(status = 'active' AND current_marker IS NOT NULL"
    f" AND current_marker = {CURRENT_MARKER})"
    " OR (status <> 'active' AND current_marker IS NULL)"
)

#: Who activated a document and when are recorded together or not at all.
_ACTIVATION_PAIR_SQL = (
    "(activated_at IS NULL AND activated_by_id IS NULL)"
    " OR (activated_at IS NOT NULL AND activated_by_id IS NOT NULL)"
)

#: Likewise the supersession.
_SUPERSESSION_PAIR_SQL = (
    "(superseded_at IS NULL AND superseded_by_id IS NULL)"
    " OR (superseded_at IS NOT NULL AND superseded_by_id IS NOT NULL)"
)

#: The lifecycle truth table, proved by the database rather than promised by
#: the application.
_LIFECYCLE_STATE_SQL = (
    "(status = 'draft' AND activated_at IS NULL AND superseded_at IS NULL)"
    " OR (status = 'active' AND activated_at IS NOT NULL AND superseded_at IS NULL)"
    " OR (status = 'superseded' AND activated_at IS NOT NULL"
    " AND superseded_at IS NOT NULL AND superseded_at >= activated_at)"
)

_DIGEST_FORMAT_SQL = f"LENGTH(body_digest) = {CONSENT_DIGEST_LENGTH}"

#: A consent document with no wording is not a consent document.
_BODY_PRESENT_SQL = "LENGTH(body) > 0"

_TIMESTAMPS_ORDERED_SQL = (
    "updated_at >= created_at"
    " AND (activated_at IS NULL OR (activated_at >= created_at AND updated_at >= activated_at))"
    " AND (superseded_at IS NULL"
    " OR (superseded_at >= created_at AND updated_at >= superseded_at))"
)

#: Everything an activated document may still change. Its wording may not.
CONSENT_DOCUMENT_FROZEN_COLUMNS = frozenset(
    {"public_id", "version_identifier", "title", "body", "body_digest", "created_by_id",
     "created_at", "activated_at", "activated_by_id"}
)


class ResearchConsentDocument(db.Model):
    __tablename__ = "research_consent_documents"
    __table_args__ = (
        db.UniqueConstraint(
            "version_identifier", name="uq_research_consent_documents_version"
        ),
        db.UniqueConstraint("current_marker", name="uq_research_consent_documents_current"),
        db.CheckConstraint(
            _STATUS_CHECK_SQL, name="ck_research_consent_documents_status_valid"
        ),
        db.CheckConstraint(
            _CURRENT_MARKER_SQL, name="ck_research_consent_documents_current_marker"
        ),
        db.CheckConstraint(
            _ACTIVATION_PAIR_SQL, name="ck_research_consent_documents_activation_pair"
        ),
        db.CheckConstraint(
            _SUPERSESSION_PAIR_SQL, name="ck_research_consent_documents_supersession_pair"
        ),
        db.CheckConstraint(
            _LIFECYCLE_STATE_SQL, name="ck_research_consent_documents_lifecycle_state"
        ),
        db.CheckConstraint(
            _DIGEST_FORMAT_SQL, name="ck_research_consent_documents_digest_format"
        ),
        db.CheckConstraint(
            _BODY_PRESENT_SQL, name="ck_research_consent_documents_body_present"
        ),
        db.CheckConstraint(
            _TIMESTAMPS_ORDERED_SQL, name="ck_research_consent_documents_timestamps_ordered"
        ),
        db.Index("ix_research_consent_documents_status_id", "status", "id"),
        db.Index("ix_research_consent_documents_created_by_id", "created_by_id"),
        db.Index("ix_research_consent_documents_activated_by_id", "activated_by_id"),
        db.Index("ix_research_consent_documents_superseded_by_id", "superseded_by_id"),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    #: The human-readable version this document is known by, unique across
    #: every status -- an Administrator naming a new version cannot reuse a
    #: retired one and make two texts share an identity.
    version_identifier = db.Column(db.String(CONSENT_VERSION_MAX_LENGTH), nullable=False)
    title = db.Column(db.String(CONSENT_TITLE_MAX_LENGTH), nullable=False)
    body = db.Column(db.Text, nullable=False)
    #: :func:`consent_digest` of the three frozen fields.
    body_digest = db.Column(db.String(CONSENT_DIGEST_LENGTH), nullable=False)
    status = db.Column(
        db.String(32), nullable=False, default=ResearchConsentDocumentStatus.DRAFT.value
    )
    #: ``1`` exactly while ``active``, ``NULL`` otherwise. The unique index
    #: over it is the one-current-document invariant.
    current_marker = db.Column(db.SmallInteger, nullable=True)
    created_by_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=False,
    )
    activated_at = db.Column(db.DateTime, nullable=True)
    activated_by_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=True,
    )
    superseded_at = db.Column(db.DateTime, nullable=True)
    superseded_by_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=True,
    )
    #: Defaults are defense in depth only; every write supplies its own
    #: post-lock whole-second moment.
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    @validates("status")
    def validate_status(self, _key, value):
        if value not in _STATUS_VALUES:
            raise ValueError(f"Invalid research consent document status: {value}")
        return value

    @validates("current_marker")
    def validate_current_marker(self, _key, value):
        if value is not None and value != CURRENT_MARKER:
            raise ValueError("A consent document's current marker is 1 or NULL")
        return value

    @validates("body_digest")
    def validate_body_digest(self, _key, value):
        if (
            not isinstance(value, str)
            or len(value) != CONSENT_DIGEST_LENGTH
            or any(ch not in "0123456789abcdef" for ch in value)
        ):
            raise ValueError("A consent document digest is 64 lowercase hex characters")
        return value

    @property
    def is_draft(self):
        return self.status == ResearchConsentDocumentStatus.DRAFT.value

    @property
    def is_active(self):
        return self.status == ResearchConsentDocumentStatus.ACTIVE.value

    @property
    def is_superseded(self):
        return self.status == ResearchConsentDocumentStatus.SUPERSEDED.value

    def digest_matches(self):
        """Whether the stored digest still describes the stored wording.

        Read by the consent workflow before a Student is shown anything: a
        document whose text and digest disagree is never presented and never
        accepted, however it came to disagree.
        """
        return self.body_digest == consent_digest(
            self.version_identifier, self.title, self.body
        )


@event.listens_for(ResearchConsentDocument, "before_update")
def _refuse_editing_frozen_consent_text(_mapper, _connection, target):
    """Once a document has been activated, its wording is frozen forever.

    Activation itself is the transition that sets ``activated_at``, so the
    guard reads the value as it was **before** this flush: a row that was
    already activated may still change its status, marker, supersession
    attribution and ``updated_at``, and nothing else.
    """
    state = inspect(target)
    history = state.attrs["activated_at"].history
    was_activated = (
        history.deleted[0] is not None
        if history.deleted
        else target.activated_at is not None and not history.added
    )
    if not was_activated:
        return
    changed = sorted(
        attr.key
        for attr in state.mapper.column_attrs
        if attr.key in CONSENT_DOCUMENT_FROZEN_COLUMNS
        and state.attrs[attr.key].history.has_changes()
    )
    if changed:
        raise ResearchHistoryError(
            "An activated research consent document's "
            + ", ".join(changed)
            + " cannot change. Activate a new version instead."
        )


@event.listens_for(ResearchConsentDocument, "before_delete")
def _refuse_deleting_a_consent_document(_mapper, _connection, _target):
    raise ResearchHistoryError("A research consent document is never deleted")
