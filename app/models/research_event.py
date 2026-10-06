"""One validated research event (Phase 6 replacement).

Two sources share one table and one dictionary
(``app/services/research_event_dictionary.py``):

- ``client`` -- an observation the collector sent. It carries the client's
  idempotency id (``event_uid``), the random page-view and tab references,
  the per-page sequence number, the **raw client clock** (``client_ts_ms``),
  the batch clock offset, and the corrected ``occurred_at_ms``. A client
  observation proves only that the browser reported it.
- ``server`` -- an outcome the server itself confirmed after an LMS workflow
  committed or refused (for example ``assignment_submission`` /
  ``submitted``). It carries no client field at all.

``occurred_at_ms`` (server timescale) and ``received_at_ms`` (server receipt)
are never interchangeable and are stored separately.

**Nothing here can hold content.** The only free-form-looking columns are
identifiers from closed allowlists; ``detail_code`` is a closed set per event
type; the three integers are bounded per type. There is no text, URL, value,
coordinate or payload column.

Recorded observations are never edited (``before_update`` refuses). Rows are
removed only by the deliberate retention purge.
"""
from app.models.code_types import CODE_COLLATION

from sqlalchemy import event

from app.extensions import db
from app.models.research_common import ID_TYPE, ResearchDataError, in_list_sql
from app.services.research_event_dictionary import (
    CLIENT_EVENT_TYPES,
    DETAIL_CODE_MAX_LENGTH,
    ELEMENT_ID_MAX_LENGTH,
    EVENT_TYPE_MAX_LENGTH,
    PAGE_ID_MAX_LENGTH,
    SERVER_EVENT_TYPES,
    SOURCE_CLIENT,
    SOURCE_SERVER,
)

_SOURCE_TYPE_SQL = (
    f"(source = '{SOURCE_CLIENT}' AND {in_list_sql('event_type', CLIENT_EVENT_TYPES)})"
    f" OR (source = '{SOURCE_SERVER}' AND {in_list_sql('event_type', SERVER_EVENT_TYPES)})"
)

_CLIENT_FIELDS_SQL = (
    f"(source = '{SOURCE_CLIENT}' AND page_id IS NOT NULL AND page_view_ref IS NOT NULL"
    " AND tab_ref IS NOT NULL AND sequence_number IS NOT NULL AND client_ts_ms IS NOT NULL"
    " AND clock_offset_ms IS NOT NULL)"
    f" OR (source = '{SOURCE_SERVER}' AND page_view_ref IS NULL AND tab_ref IS NULL"
    " AND sequence_number IS NULL AND client_ts_ms IS NULL AND clock_offset_ms IS NULL)"
)


class ResearchEvent(db.Model):
    __tablename__ = "research_events"
    __table_args__ = (
        db.UniqueConstraint("event_uid", name="uq_research_events_event_uid"),
        db.CheckConstraint(_SOURCE_TYPE_SQL, name="ck_research_events_source_type"),
        db.CheckConstraint(_CLIENT_FIELDS_SQL, name="ck_research_events_client_fields"),
        db.CheckConstraint(
            "(count_value IS NULL OR count_value >= 0)"
            " AND (duration_ms IS NULL OR duration_ms >= 0)"
            " AND (position_value IS NULL OR position_value >= 0)"
            " AND (sequence_number IS NULL OR sequence_number >= 1)",
            name="ck_research_events_values_non_negative",
        ),
        db.CheckConstraint(
            "LENGTH(event_uid) = 36", name="ck_research_events_uid_format"
        ),
        db.Index("ix_research_events_session_occurred", "session_id", "occurred_at_ms", "id"),
        db.Index("ix_research_events_type", "event_type"),
    )

    id = db.Column(ID_TYPE, primary_key=True)
    event_uid = db.Column(db.String(36), nullable=False)
    session_id = db.Column(ID_TYPE, db.ForeignKey("research_sessions.id"), nullable=False)
    source = db.Column(db.String(8, collation=CODE_COLLATION), nullable=False)
    event_type = db.Column(db.String(EVENT_TYPE_MAX_LENGTH, collation=CODE_COLLATION), nullable=False)
    page_id = db.Column(db.String(PAGE_ID_MAX_LENGTH), nullable=True)
    element_id = db.Column(db.String(ELEMENT_ID_MAX_LENGTH), nullable=True)
    detail_code = db.Column(db.String(DETAIL_CODE_MAX_LENGTH), nullable=True)
    count_value = db.Column(db.Integer, nullable=True)
    duration_ms = db.Column(db.Integer, nullable=True)
    position_value = db.Column(db.Integer, nullable=True)
    page_view_ref = db.Column(db.String(36), nullable=True)
    tab_ref = db.Column(db.String(36), nullable=True)
    sequence_number = db.Column(db.Integer, nullable=True)
    client_ts_ms = db.Column(db.BigInteger, nullable=True)
    clock_offset_ms = db.Column(db.BigInteger, nullable=True)
    occurred_at_ms = db.Column(db.BigInteger, nullable=False)
    received_at_ms = db.Column(db.BigInteger, nullable=False)


@event.listens_for(ResearchEvent, "before_update")
def _events_are_never_edited(_mapper, _connection, _target):
    raise ResearchDataError("A recorded research event is never edited")
