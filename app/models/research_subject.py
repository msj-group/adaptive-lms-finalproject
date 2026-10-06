"""One pseudonymous research subject (Phase 6 replacement).

A subject is the research identity of one Student: a random ``subject_code``
that every research row groups by, the subject's collection status, the
truthful basis of that status, and a server-controlled provenance.

**This table holds no account reference.** The link to ``users`` lives in
:class:`~app.models.research_subject_link.ResearchSubjectLink`, which no
Researcher-facing query and no export ever reads. That is pseudonymization,
not anonymization: the link exists and anyone with database access can follow
it.

**Status is operational, not consent.** A subject is provisioned by the
server, automatically, at an eligible Student's first collection write, as
``included`` under the population rule; ``excluded`` records that collection
must not happen (an operator's exclusion or a carried-over legacy refusal or
withdrawal). Neither claims a decision the Student took inside the
application.

Database invariants (final defense only):

- ``public_id`` and ``uq_research_subjects_code`` unique;
- the closed sets for ``collection_status``, ``status_basis`` and
  ``provenance``;
- ``ck_research_subjects_status_basis_pair`` -- ``included`` only under
  ``population_rule`` or ``operator_reinstatement``; ``excluded`` only under
  ``external_exclusion`` or ``legacy_collection_exclusion``;
- ``ck_research_subjects_code_format`` and ``ck_research_subjects_timestamps_ordered``.

A subject is never deleted, and its code, public id and creation moment never
change (ORM guards below). ``provenance`` changes only through the audited
operator action that marks a demonstration account.
"""
from app.models.code_types import CODE_COLLATION

import uuid

from sqlalchemy import event, inspect
from sqlalchemy.orm import validates

from app.extensions import db
from app.models.enums import ResearchCollectionStatus, ResearchProvenance, ResearchStatusBasis
from app.models.research_common import (
    ID_TYPE,
    SUBJECT_CODE_LENGTH,
    ResearchDataError,
    changed_columns,
    in_list_sql,
    subject_code_is_valid,
)
from app.models.submission_feedback import whole_second_utc

COLLECTION_STATUSES = tuple(s.value for s in ResearchCollectionStatus)
STATUS_BASES = tuple(b.value for b in ResearchStatusBasis)
SUBJECT_PROVENANCES = (ResearchProvenance.STUDY.value, ResearchProvenance.DEMO.value)

#: Which bases each status may carry.
BASES_BY_STATUS = {
    ResearchCollectionStatus.INCLUDED.value: (
        ResearchStatusBasis.POPULATION_RULE.value,
        ResearchStatusBasis.OPERATOR_REINSTATEMENT.value,
    ),
    ResearchCollectionStatus.EXCLUDED.value: (
        ResearchStatusBasis.EXTERNAL_EXCLUSION.value,
        ResearchStatusBasis.LEGACY_COLLECTION_EXCLUSION.value,
    ),
}

_STATUS_BASIS_SQL = " OR ".join(
    f"(collection_status = '{status}' AND {in_list_sql('status_basis', bases)})"
    for status, bases in BASES_BY_STATUS.items()
)

SUBJECT_IDENTITY_COLUMNS = frozenset({"public_id", "subject_code", "created_at"})


class ResearchSubject(db.Model):
    __tablename__ = "research_subjects"
    __table_args__ = (
        db.UniqueConstraint("subject_code", name="uq_research_subjects_code"),
        db.CheckConstraint(
            in_list_sql("collection_status", COLLECTION_STATUSES),
            name="ck_research_subjects_status_valid",
        ),
        db.CheckConstraint(
            in_list_sql("status_basis", STATUS_BASES), name="ck_research_subjects_basis_valid"
        ),
        db.CheckConstraint(
            in_list_sql("provenance", SUBJECT_PROVENANCES),
            name="ck_research_subjects_provenance_valid",
        ),
        db.CheckConstraint(_STATUS_BASIS_SQL, name="ck_research_subjects_status_basis_pair"),
        db.CheckConstraint(
            f"LENGTH(subject_code) = {SUBJECT_CODE_LENGTH}",
            name="ck_research_subjects_code_format",
        ),
        db.CheckConstraint(
            "updated_at >= created_at AND status_changed_at >= created_at",
            name="ck_research_subjects_timestamps_ordered",
        ),
        db.Index("ix_research_subjects_status_id", "collection_status", "id"),
    )

    id = db.Column(ID_TYPE, primary_key=True)
    public_id = db.Column(
        db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )
    subject_code = db.Column(db.String(SUBJECT_CODE_LENGTH), nullable=False)
    collection_status = db.Column(db.String(16, collation=CODE_COLLATION), nullable=False)
    status_basis = db.Column(db.String(32, collation=CODE_COLLATION), nullable=False)
    provenance = db.Column(
        db.String(16, collation=CODE_COLLATION), nullable=False, default=ResearchProvenance.STUDY.value
    )
    status_changed_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    created_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)
    updated_at = db.Column(db.DateTime, nullable=False, default=whole_second_utc)

    @validates("subject_code")
    def validate_subject_code(self, _key, value):
        if not subject_code_is_valid(value):
            raise ValueError("A subject code must be a server-generated research code")
        return value

    @validates("collection_status")
    def validate_collection_status(self, _key, value):
        if value not in COLLECTION_STATUSES:
            raise ValueError(f"Invalid collection status: {value}")
        return value

    @validates("status_basis")
    def validate_status_basis(self, _key, value):
        if value not in STATUS_BASES:
            raise ValueError(f"Invalid status basis: {value}")
        return value

    @validates("provenance")
    def validate_provenance(self, _key, value):
        if value not in SUBJECT_PROVENANCES:
            raise ValueError(f"Invalid subject provenance: {value}")
        return value

    @property
    def is_included(self):
        return self.collection_status == ResearchCollectionStatus.INCLUDED.value


@event.listens_for(ResearchSubject, "before_update")
def _subject_identity_is_immutable(_mapper, _connection, target):
    changed = changed_columns(inspect(target), SUBJECT_IDENTITY_COLUMNS)
    if changed:
        raise ResearchDataError(
            "A research subject's " + ", ".join(changed) + " cannot change."
        )


@event.listens_for(ResearchSubject, "before_delete")
def _refuse_deleting_a_subject(_mapper, _connection, _target):
    raise ResearchDataError("A research subject is never deleted")
