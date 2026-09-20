"""One research participant: a pseudonymous identity for an existing
Student account (Phase 6 / M01).

**A participant is a Student, addressed by a code.** ``student_id`` is the
internal link an Administrator may see; ``participant_code`` is the only
identity a Researcher ever sees. The code is generated on the server from a
cryptographically secure source and is **not derived** from the Student's
name, email, ``public_id``, internal id, enrollment or any other
identifying value, so no Researcher can invert it.

**This is pseudonymization, not anonymization.** The link exists, in this
table, and an Administrator can follow it. Anyone with database access can
follow it too. What M01 provides is that the Researcher-facing surface --
routes, queries and templates alike -- never loads or renders the
identifying side of it (``app/services/research_queries.py``).

**A foreign key proves existence, never a role.** ``student_id`` references
``users``; it cannot say that the account is still a Student, still active,
or ever was either. Every application read and write that depends on those
facts re-proves them against the locked row
(``app/services/research_transactions.py``).

**One participant per Student**, by
``uq_research_participants_student_id``. Creating one changes no User,
Enrollment, Group, academic or financial row, and **is not consent**: a new
participant is ``invited`` until the Student themselves decides.

**The status is stored, not derived** (see ``docs/DECISIONS.md``,
Phase 6 / M01): it moves only in the same transaction and the same commit
as the append-only :class:`~app.models.research_consent_event.
ResearchConsentEvent` that explains it, and the closed set is a database
CHECK. ``version`` is the optimistic-concurrency signal a signed consent
form is bound to.

**Nothing is ever physically deleted**, there is no cascade and no
``ondelete``; every foreign key is a plain reference. A Student's later
suspension preserves the research history and, separately, stops that
account logging in at all -- so no new consent action can follow it.

Database invariants (final defense only):

- ``public_id``, ``uq_research_participants_student_id`` and
  ``uq_research_participants_code`` unique;
- ``ck_research_participants_status_valid`` -- the closed set as a literal
  ``IN`` list;
- ``ck_research_participants_code_format`` -- the code's exact stored
  length, so a truncated or empty code cannot exist;
- ``ck_research_participants_version_positive``;
- ``ck_research_participants_lifecycle_state`` -- ``invited`` carries no
  decision at all; ``active`` and ``declined`` carry a document and a
  decision moment and no withdrawal; ``withdrawn`` carries all three, with
  the withdrawal moment being the decision moment;
- ``ck_research_participants_timestamps_ordered``.

Conditions a CHECK cannot express are stated here rather than hidden: that
``student_id`` names an active Student, that a status matches the latest
consent event, and that a withdrawn participant is never reactivated. The
application proves each against locked rows, and the ORM guards below
refuse a flush that would rewrite identity or delete a participant.

Indexes -- one per real query path:

- ``ix_research_participants_status_id`` (``status``, ``id``) -- the
  Researcher and Administrator lists, and the dashboard counts;
- ``ix_research_participants_invited_by_id`` and
  ``ix_research_participants_consent_document_id`` -- declared for the two
  foreign keys InnoDB requires an index for that no unique constraint
  already covers.

**No MySQL execution plan has been measured for this table.**

**No ORM relationship is declared in either direction**: every read is an
explicit query in ``app/services/research_queries.py``, so rendering a
participant can never lazy-load a Student's name or email by accident.
"""

import secrets
import uuid

from sqlalchemy import event, inspect
from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import ResearchParticipantStatus
from app.models.research_consent_document import ResearchHistoryError
from app.models.submission_feedback import whole_second_utc

#: The fixed prefix every participant code carries, so a code is
#: recognisable in a log or a screenshot without being informative.
PARTICIPANT_CODE_PREFIX = "RP-"

#: The random part's length. Drawn from a 31-character alphabet, ten
#: characters carry roughly 49 bits -- far beyond any plausible participant
#: count, so a collision is a retry, not a design problem.
PARTICIPANT_CODE_RANDOM_LENGTH = 10

#: Crockford-style: upper-case letters and digits with ``I``, ``L``, ``O``,
#: ``U`` and ``0``/``1`` removed, so a code read aloud or copied off a
#: screen cannot become a different code.
PARTICIPANT_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"

#: The exact stored width, so the column, the CHECK and the generator agree.
PARTICIPANT_CODE_LENGTH = len(PARTICIPANT_CODE_PREFIX) + PARTICIPANT_CODE_RANDOM_LENGTH


def generate_participant_code():
    """A fresh, unpredictable participant code.

    ``secrets.choice`` -- never ``random`` -- because a guessable code would
    let anyone holding one participant's code enumerate the others. Nothing
    about the Student reaches this function, by construction: it takes no
    arguments.
    """
    body = "".join(
        secrets.choice(PARTICIPANT_CODE_ALPHABET)
        for _ in range(PARTICIPANT_CODE_RANDOM_LENGTH)
    )
    return f"{PARTICIPANT_CODE_PREFIX}{body}"


_STATUS_VALUES = tuple(status.value for status in ResearchParticipantStatus)
_STATUS_CHECK_SQL = "status IN (" + ", ".join(f"'{v}'" for v in _STATUS_VALUES) + ")"
_CODE_FORMAT_SQL = f"LENGTH(participant_code) = {PARTICIPANT_CODE_LENGTH}"

#: The lifecycle truth table. ``invited`` is the only state with no
#: decision; ``withdrawn`` is the only one with a withdrawal moment, and
#: that moment *is* the decision moment, so the two can never disagree.
_LIFECYCLE_STATE_SQL = (
    "(status = 'invited' AND consent_document_id IS NULL AND decided_at IS NULL"
    " AND withdrawn_at IS NULL)"
    " OR (status IN ('active', 'declined') AND consent_document_id IS NOT NULL"
    " AND decided_at IS NOT NULL AND withdrawn_at IS NULL)"
    " OR (status = 'withdrawn' AND consent_document_id IS NOT NULL"
    " AND decided_at IS NOT NULL AND withdrawn_at IS NOT NULL"
    " AND withdrawn_at = decided_at)"
)

_TIMESTAMPS_ORDERED_SQL = (
    "updated_at >= created_at"
    " AND (decided_at IS NULL OR (decided_at >= created_at AND updated_at >= decided_at))"
)

#: What a participant row may never change after it is created. The status,
#: the consent link, the decision moments, the version and ``updated_at``
#: are exactly what a consent decision moves.
PARTICIPANT_IDENTITY_COLUMNS = frozenset(
    {"public_id", "student_id", "participant_code", "invited_by_id", "created_at"}
)

#: The transitions the application is allowed to ask for, as
#: ``(from, to)``. Withdrawal is terminal in M01: nothing leaves
#: ``withdrawn``, ``declined`` is terminal too, and re-invitation and
#: re-consent are deferred to a later Part.
ALLOWED_PARTICIPANT_TRANSITIONS = frozenset(
    {
        (ResearchParticipantStatus.INVITED.value, ResearchParticipantStatus.ACTIVE.value),
        (ResearchParticipantStatus.INVITED.value, ResearchParticipantStatus.DECLINED.value),
        (ResearchParticipantStatus.ACTIVE.value, ResearchParticipantStatus.WITHDRAWN.value),
    }
)


class ResearchParticipant(db.Model):
    __tablename__ = "research_participants"
    __table_args__ = (
        db.UniqueConstraint("student_id", name="uq_research_participants_student_id"),
        db.UniqueConstraint("participant_code", name="uq_research_participants_code"),
        db.CheckConstraint(_STATUS_CHECK_SQL, name="ck_research_participants_status_valid"),
        db.CheckConstraint(_CODE_FORMAT_SQL, name="ck_research_participants_code_format"),
        db.CheckConstraint("version > 0", name="ck_research_participants_version_positive"),
        db.CheckConstraint(
            _LIFECYCLE_STATE_SQL, name="ck_research_participants_lifecycle_state"
        ),
        db.CheckConstraint(
            _TIMESTAMPS_ORDERED_SQL, name="ck_research_participants_timestamps_ordered"
        ),
        db.Index("ix_research_participants_status_id", "status", "id"),
        db.Index("ix_research_participants_invited_by_id", "invited_by_id"),
        db.Index("ix_research_participants_consent_document_id", "consent_document_id"),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    #: The internal link only an Administrator ever follows.
    student_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=False,
    )
    #: The only identity a Researcher sees. Server-generated; never derived.
    participant_code = db.Column(db.String(PARTICIPANT_CODE_LENGTH), nullable=False)
    status = db.Column(
        db.String(32), nullable=False, default=ResearchParticipantStatus.INVITED.value
    )
    #: The document the latest decision was taken against. NULL exactly
    #: while ``invited``.
    consent_document_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("research_consent_documents.id"),
        nullable=True,
    )
    #: When the latest decision was taken.
    decided_at = db.Column(db.DateTime, nullable=True)
    #: Set exactly while ``withdrawn``, and equal to ``decided_at``.
    withdrawn_at = db.Column(db.DateTime, nullable=True)
    invited_by_id = db.Column(
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        db.ForeignKey("users.id"),
        nullable=False,
    )
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    @validates("status")
    def validate_status(self, _key, value):
        if value not in _STATUS_VALUES:
            raise ValueError(f"Invalid research participant status: {value}")
        return value

    @validates("participant_code")
    def validate_participant_code(self, _key, value):
        if (
            not isinstance(value, str)
            or len(value) != PARTICIPANT_CODE_LENGTH
            or not value.startswith(PARTICIPANT_CODE_PREFIX)
            or any(ch not in PARTICIPANT_CODE_ALPHABET
                   for ch in value[len(PARTICIPANT_CODE_PREFIX):])
        ):
            raise ValueError("A participant code must be a server-generated research code")
        return value

    @validates("version")
    def validate_version(self, _key, value):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("A research participant version must be a positive integer")
        return value

    @property
    def is_invited(self):
        return self.status == ResearchParticipantStatus.INVITED.value

    @property
    def is_active(self):
        return self.status == ResearchParticipantStatus.ACTIVE.value

    @property
    def is_declined(self):
        return self.status == ResearchParticipantStatus.DECLINED.value

    @property
    def is_withdrawn(self):
        return self.status == ResearchParticipantStatus.WITHDRAWN.value

    @property
    def may_decide(self):
        """Whether this participant still has a consent decision to take.

        Only an ``invited`` participant does. A declined or withdrawn one
        does not, and an active one may withdraw rather than decide again.
        """
        return self.is_invited


@event.listens_for(ResearchParticipant, "before_update")
def _participant_identity_is_immutable(_mapper, _connection, target):
    """Refuse a flush that would change who a participant *is*."""
    state = inspect(target)
    changed = sorted(
        attr.key
        for attr in state.mapper.column_attrs
        if attr.key in PARTICIPANT_IDENTITY_COLUMNS
        and state.attrs[attr.key].history.has_changes()
    )
    if changed:
        raise ResearchHistoryError(
            "A research participant's " + ", ".join(changed)
            + " cannot change after it is created."
        )


@event.listens_for(ResearchParticipant, "before_delete")
def _refuse_deleting_a_participant(_mapper, _connection, _target):
    raise ResearchHistoryError("A research participant is never deleted")
