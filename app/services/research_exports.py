"""Immutable research exports (Phase 6 replacement, corrected).

An export is a ZIP of CSV files plus a manifest:

- ``sessions.csv`` -- one row per session;
- ``events.csv`` -- one row per validated event (client and server);
- ``prompts.csv`` -- one row per feedback prompt, with its raw 1-5 rating,
  its exact observation window and its derived state at the cutoff;
- ``data_dictionary.csv`` -- every column of every file, and every event type
  of the event dictionary with the reason it is collected;
- ``manifest.json`` -- format, event schema version, configuration versions,
  filters, the snapshot cutoff and its time contract, exclusions, the
  missing-label rule, the label transformation, row counts and the SHA-256 of
  every file.

**What is never exported.** Only ``study`` provenance is exported --
development and demonstration data never are, including the earlier sessions
of an account an operator later marked as a demonstration account. The queries never join
``users`` or ``research_subject_links``: there is no name, email address,
account id, mapping key, secret or content. The pseudonymous
``subject_code`` is the grouping key for participant-grouped evaluation in
Phase 7.

**Labels.** The raw 1-5 rating is preserved. No transformation is applied.
Unrated windows are simply absent from the label columns: a dismissal, a
nonresponse, an offer never shown or a late answer is exported as what it is
and never as a label.

**Immutability.** The ZIP is built once, when the export is created, and
stored with its SHA-256 (``ResearchExportArchive``). Every download serves
exactly those stored bytes after re-checking the digest; nothing is ever
regenerated from rows that may have changed since (a later rating, event or
session end, a retention purge, a different ``APP_TIMEZONE``). An archive
removed by the retention rule is answered as gone, never rebuilt. Rows are
ordered by id, and the ZIP uses fixed timestamps and sorted JSON.

**Time contract.** Every file is read inside **one read transaction** whose
snapshot is established by its first read (REPEATABLE READ on MySQL/InnoDB).
``cutoff_ms`` is read from the server clock
only **after** the snapshot exists. Hence:

- every row and every value in the archive -- including the mutable ones:
  session last activity, end and counters, prompt display, response,
  rating, causes, deferrals and late flag -- is the committed state of one
  database snapshot, and everything in it was committed before
  ``cutoff_ms``;
- a write still in progress at that moment is **not** included, even when
  its own server moment is earlier than ``cutoff_ms``: the archive is the
  state saved by the cutoff, not a reconstruction of every moment up to it;
- derived prompt states (``no_response``, ``offer_expired``, ...) are
  evaluated at ``cutoff_ms``;
- as a check of that contract, no included server moment (session start,
  last activity and end; event receipt; prompt offer, display and
  response) may be later than ``cutoff_ms``. One can only be if a server
  clock ran ahead of this one; the export is then **refused**
  (:data:`CLOCK_AHEAD`) with nothing stored -- never clamped, filtered or
  relabelled -- and a later export, whose cutoff has passed that moment,
  includes it.

The archive and its row are written afterwards, in a separate transaction
that re-proves the Researcher, so the snapshot never has to be upgraded to a
write.

**CSV formula injection.** A text value that begins with ``=``, ``+``,
``-``, ``@``, a tab or a carriage return is prefixed with ``'``. Integer
columns are written as integers and are never altered.
"""

import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime, time, timedelta, timezone

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    CAUSE_COLUMNS,
    EXPORT_FORMAT,
    MAX_EXPORT_ARCHIVE_BYTES,
    ResearchExportArchive,
    ResearchAuditAction,
    ResearchAuditChannel,
    ResearchAuditEvent,
    ResearchConfiguration,
    ResearchEvent,
    ResearchExport,
    ResearchFeedbackPrompt,
    ResearchProvenance,
    ResearchSession,
    ResearchSubject,
    User,
    UserRole,
    UserStatus,
    now_ms,
)
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.research_event_dictionary import EVENT_SCHEMA_VERSION, dictionary_rows
from app.services.research_workspace_queries import derived_prompt_state
from app.services.schedule_occurrences import from_app_local

#: A safety bound on one export's event rows. A pilot of 20-50 Students is
#: far below it; reaching it refuses the export rather than exhausting memory.
MAX_EXPORT_EVENTS = 2_000_000

CREATED = "created"
UNAUTHORIZED = "unauthorized"
TOO_LARGE = "too_large"
CONFLICT = "conflict"
BAD_PERIOD = "bad_period"
SERVED = "served"
NOT_FOUND = "not_found"
EXPIRED = "expired"
CORRUPT = "corrupt"
CLOCK_AHEAD = "clock_ahead"

#: The manifest's statement of what ``cutoff_ms`` means.
TIME_CONTRACT = (
    "Every file was read in one database read snapshot established before cutoff_ms "
    "(server clock, epoch ms, UTC). Every row and value, including session last activity, "
    "end and counters and prompt display, response, rating, causes, deferrals and late "
    "flag, is the committed state of that snapshot; everything in it was saved before "
    "cutoff_ms. Writes still in progress at cutoff_ms are not included even when their "
    "own server moment is earlier. No included server moment is later than cutoff_ms. "
    "Derived prompt states are evaluated at cutoff_ms."
)

_STUDY = ResearchProvenance.STUDY.value
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

SESSION_COLUMNS = (
    ("session_id", "Pseudonymous session identifier (random UUID)."),
    ("subject_code", "Pseudonymous subject code; the participant grouping key."),
    ("configuration_version", "Collection configuration version number."),
    ("event_schema_version", "Event dictionary version the session was collected under."),
    ("started_at_ms", "Session start, server timescale, epoch milliseconds (UTC)."),
    ("last_seen_at_ms", "Latest accepted activity saved by the snapshot cutoff, epoch ms "
                        "(UTC)."),
    ("ended_at_ms", "End saved by the snapshot cutoff, epoch ms (UTC); empty if the session "
                    "was still open in the snapshot."),
    ("end_reason", "logout, inactivity, configuration_changed, collection_stopped or "
                   "subject_ineligible; empty if still open in the snapshot."),
    ("batches_received", "Client batches accepted for processing. This and every counter "
                         "below is the value saved in the export's snapshot (before "
                         "cutoff_ms)."),
    ("events_accepted", "Events stored (client and server)."),
    ("events_duplicate", "Client events refused as replays of a stored event id."),
    ("events_invalid", "Client events refused by the event dictionary."),
    ("events_late", "Client events refused as too old or older than the session."),
    ("events_dropped_client", "Events the browser reported dropping from a full buffer."),
    ("sampling_eligible_checks", "Sampling checks at which a prompt could be offered."),
    ("sampling_ineligible_checks", "Sampling checks at which no prompt could be offered."),
)

EVENT_COLUMNS = (
    ("session_id", "Session identifier."),
    ("subject_code", "Pseudonymous subject code."),
    ("event_uid", "Idempotency identifier (client) or server-generated identifier."),
    ("source", "client (browser observation) or server (confirmed outcome)."),
    ("event_type", "See the event rows of this dictionary."),
    ("page_id", "Allowlisted page identifier (Flask endpoint name), never a URL."),
    ("element_id", "Allowlisted control identifier, or empty."),
    ("detail_code", "Closed-set detail code for the event type, or empty."),
    ("count_value", "Bounded count for the event type, or empty."),
    ("duration_ms", "Bounded duration in milliseconds, or empty."),
    ("position_value", "Bounded position for the event type, or empty."),
    ("page_view_ref", "Random reference of one page view (client events)."),
    ("tab_ref", "Random reference of one browser tab (client events)."),
    ("sequence_number", "Per-page-view sequence number (client events)."),
    ("client_ts_ms", "Raw client clock reading, epoch ms (client events)."),
    ("clock_offset_ms", "Server receipt minus client send time for the batch, ms."),
    ("occurred_at_ms", "Event time on the server timescale, epoch ms."),
    ("received_at_ms", "Server receipt time, epoch ms."),
)

PROMPT_COLUMNS = (
    ("prompt_id", "Pseudonymous prompt identifier."),
    ("session_id", "Session identifier."),
    ("subject_code", "Pseudonymous subject code."),
    ("configuration_version", "Configuration version the prompt was sampled under."),
    ("sampling_reason", "random (eligible moment) or activity_end (natural ending)."),
    ("stored_status", "offered, displayed, answered or dismissed."),
    ("derived_state", "answered, dismissed, no_response, awaiting_response, offer_expired "
                      "or awaiting_display, evaluated at the export's cutoff_ms."),
    ("offered_at_ms", "Sampling moment, epoch ms."),
    ("deferral_count", "Times the browser deferred showing it."),
    ("last_deferral_reason", "timed_activity, recording, uploading or hidden_tab."),
    ("displayed_at_ms", "Display grant moment, epoch ms; ends the window."),
    ("prompt_day", "Local calendar day of the display (APP_TIMEZONE)."),
    ("window_start_ms", "Start of the lookback window, epoch ms."),
    ("window_end_ms", "End of the lookback window (= displayed_at_ms)."),
    ("observed_ms", "Continuously observed milliseconds inside the window."),
    ("responded_at_ms", "Answer or dismissal moment, epoch ms."),
    ("response_delay_ms", "responded_at_ms - displayed_at_ms."),
    ("rating", "Raw self-reported frustration 1-5, only when answered. "
               "1 Not frustrated at all, 2 Slightly, 3 Moderately, 4 Very, 5 Extremely."),
    *((column, f"Optional cause selected (1/0), only when answered: {label}.")
      for _code, column, label in CAUSE_COLUMNS),
    ("late_response", "1 when an answer arrived after the response window and was not stored."),
)


def _safe(value):
    """Neutralise a text cell against spreadsheet formula injection."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _csv(header, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(["" if v is None else _safe(v) for v in row])
    return buffer.getvalue().encode("utf-8")


def _bounds(period_from, period_to, tz_name):
    """UTC epoch-ms bounds of the local calendar period, or ``(None, None)``."""
    lower = upper = None
    if period_from is not None:
        lower = _local_midnight_ms(period_from, tz_name)
    if period_to is not None:
        upper = _local_midnight_ms(period_to + timedelta(days=1), tz_name)
    return lower, upper


def _local_midnight_ms(day, tz_name):
    utc = from_app_local(tz_name, datetime.combine(day, time.min))
    return int(utc.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _sessions(configuration_id, lower, upper):
    query = (
        db.session.query(
            ResearchSession.id,
            ResearchSession.public_id,
            ResearchSubject.subject_code,
            ResearchConfiguration.version_number,
            ResearchConfiguration.event_schema_version,
            ResearchSession.started_at_ms,
            ResearchSession.last_seen_at_ms,
            ResearchSession.ended_at_ms,
            ResearchSession.end_reason,
            ResearchSession.batches_received,
            ResearchSession.events_accepted,
            ResearchSession.events_duplicate,
            ResearchSession.events_invalid,
            ResearchSession.events_late,
            ResearchSession.events_dropped_client,
            ResearchSession.sampling_eligible_checks,
            ResearchSession.sampling_ineligible_checks,
        )
        .join(ResearchSubject, ResearchSubject.id == ResearchSession.subject_id)
        .join(ResearchConfiguration, ResearchConfiguration.id == ResearchSession.configuration_id)
        .filter(ResearchSession.provenance == _STUDY,
                ResearchSubject.provenance == _STUDY)
    )
    if configuration_id is not None:
        query = query.filter(ResearchSession.configuration_id == configuration_id)
    if lower is not None:
        query = query.filter(ResearchSession.started_at_ms >= lower)
    if upper is not None:
        query = query.filter(ResearchSession.started_at_ms < upper)
    return query.order_by(ResearchSession.id.asc()).all()


def build_export(configuration_id, period_from, period_to, cutoff_ms, tz_name):
    """``(zip_bytes, manifest, counts, manifest_digest)`` from the current read
    snapshot, or raise :class:`ExportTooLarge` or :class:`ClockAhead`.
    Read-only; the caller owns the snapshot (see the time contract)."""
    lower, upper = _bounds(period_from, period_to, tz_name)
    sessions = _sessions(configuration_id, lower, upper)
    latest = max((moment for row in sessions for moment in (row[5], row[6], row[7])
                  if moment is not None), default=None)
    session_ids = [row[0] for row in sessions]
    codes = {row[0]: (row[1], row[2]) for row in sessions}
    configuration_versions = sorted({row[3] for row in sessions})

    event_rows, prompt_rows = [], []
    for chunk_start in range(0, len(session_ids), 500):
        chunk = session_ids[chunk_start:chunk_start + 500]
        events = (
            db.session.query(
                ResearchEvent.session_id, ResearchEvent.event_uid, ResearchEvent.source,
                ResearchEvent.event_type, ResearchEvent.page_id, ResearchEvent.element_id,
                ResearchEvent.detail_code, ResearchEvent.count_value, ResearchEvent.duration_ms,
                ResearchEvent.position_value, ResearchEvent.page_view_ref, ResearchEvent.tab_ref,
                ResearchEvent.sequence_number, ResearchEvent.client_ts_ms,
                ResearchEvent.clock_offset_ms, ResearchEvent.occurred_at_ms,
                ResearchEvent.received_at_ms,
            )
            .filter(ResearchEvent.session_id.in_(chunk))
            .order_by(ResearchEvent.id.asc())
            .all()
        )
        for row in events:
            public_id, code = codes[row[0]]
            event_rows.append((public_id, code) + tuple(row[1:]))
            latest = row[16] if latest is None else max(latest, row[16])
        if len(event_rows) > MAX_EXPORT_EVENTS:
            raise ExportTooLarge()
        prompts = (
            db.session.query(
                ResearchFeedbackPrompt, ResearchConfiguration.version_number,
                ResearchConfiguration.offer_ttl_seconds,
                ResearchConfiguration.response_window_seconds,
            )
            .join(ResearchConfiguration,
                  ResearchConfiguration.id == ResearchFeedbackPrompt.configuration_id)
            .filter(ResearchFeedbackPrompt.session_id.in_(chunk))
            .order_by(ResearchFeedbackPrompt.id.asc())
            .all()
        )
        for prompt, version, ttl, window in prompts:
            for moment in (prompt.offered_at_ms, prompt.displayed_at_ms, prompt.responded_at_ms):
                if moment is not None:
                    latest = moment if latest is None else max(latest, moment)
            prompt_rows.append(_prompt_row(prompt, version, ttl, window, codes, cutoff_ms))

    if latest is not None and latest > cutoff_ms:
        # Only a server clock running ahead of this one can store a moment
        # after a cutoff taken once the snapshot existed. Refuse rather than
        # clamp, filter or relabel it.
        raise ClockAhead()

    files = {
        "sessions.csv": _csv([c for c, _d in SESSION_COLUMNS], [row[1:] for row in sessions]),
        "events.csv": _csv([c for c, _d in EVENT_COLUMNS], event_rows),
        "prompts.csv": _csv([c for c, _d in PROMPT_COLUMNS], prompt_rows),
        "data_dictionary.csv": _dictionary_csv(),
    }
    counts = {
        "subjects": len({row[2] for row in sessions}),
        "sessions": len(sessions),
        "events": len(event_rows),
        "prompts": len(prompt_rows),
        "labelled_prompts": sum(1 for row in prompt_rows if row[6] == "answered"),
        "oldest_last_seen_ms": min((row[6] for row in sessions), default=None),
        "_session_refs": [row[1] for row in sessions],
    }
    manifest = {
        "format": EXPORT_FORMAT,
        "event_schema_version": EVENT_SCHEMA_VERSION,
        "configuration_versions": configuration_versions,
        "filters": {
            "configuration_version": _version_of(configuration_id),
            "session_start_local_from": period_from.isoformat() if period_from else None,
            "session_start_local_to": period_to.isoformat() if period_to else None,
            "timezone": tz_name,
            "provenance": _STUDY,
        },
        "cutoff_ms": cutoff_ms,
        "time_contract": TIME_CONTRACT,
        "exclusions": [
            "Development and demonstration provenance are never exported, nor any "
            "session of an account marked as a demonstration account.",
            "Excluded Students are never collected; events refused at ingestion "
            "(invalid, duplicate, late) are counted per session and never stored.",
            "No name, email address, account identifier, subject-to-account mapping or "
            "content is present in any file.",
        ],
        "missing_label_rule": (
            "Only answered prompts carry a label, and each labels only its own window "
            "[window_start_ms, window_end_ms]. Dismissed, unanswered, never-shown and late "
            "prompts, and every unprompted interval, are unlabeled."
        ),
        "label_transformation": "none; raw 1-5 ratings preserved",
        "counts": counts,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8")
    files["manifest.json"] = manifest_bytes
    return _zip(files, cutoff_ms), manifest, counts, hashlib.sha256(manifest_bytes).hexdigest()


class ExportTooLarge(Exception):
    pass


class ClockAhead(Exception):
    """A stored server moment is later than the snapshot cutoff."""


def _version_of(configuration_id):
    if configuration_id is None:
        return None
    return db.session.query(ResearchConfiguration.version_number).filter(
        ResearchConfiguration.id == configuration_id).scalar()


def _prompt_row(prompt, version, ttl, window, codes, cutoff_ms):
    session_public_id, code = codes[prompt.session_id]
    state = derived_prompt_state(prompt.status, prompt.offered_at_ms, prompt.displayed_at_ms,
                                 ttl, window, cutoff_ms, prompt.responded_at_ms)
    answered = state == "answered"
    delay = None
    if prompt.responded_at_ms is not None and prompt.displayed_at_ms is not None:
        delay = prompt.responded_at_ms - prompt.displayed_at_ms
    return (
        prompt.public_id, session_public_id, code, version, prompt.sampling_reason,
        prompt.status, state, prompt.offered_at_ms, prompt.deferral_count,
        prompt.last_deferral_reason, prompt.displayed_at_ms,
        prompt.prompt_day.isoformat() if prompt.prompt_day else None,
        prompt.window_start_ms, prompt.window_end_ms, prompt.observed_ms,
        prompt.responded_at_ms, delay,
        prompt.rating if answered else None,
        *((1 if getattr(prompt, column) else 0) if answered else None
          for _code, column, _label in CAUSE_COLUMNS),
        1 if prompt.late_response else 0,
    )


def _dictionary_csv():
    rows = []
    for file_name, columns in (("sessions.csv", SESSION_COLUMNS), ("events.csv", EVENT_COLUMNS),
                               ("prompts.csv", PROMPT_COLUMNS)):
        rows.extend((file_name, column, description) for column, description in columns)
    for entry in dictionary_rows():
        rows.append((
            "events.csv", f"event_type={entry['event_type']}",
            f"[{entry['source']}/{entry['category']}] {entry['reason']} "
            f"element: {entry['element']}; details: {entry['details'] or '-'}; "
            f"count: {entry['count'] or '-'}; duration_ms: {entry['duration_ms'] or '-'}; "
            f"position: {entry['position'] or '-'}",
        ))
    return _csv(["file", "column", "description"], rows)


def _zip(files, cutoff_ms):
    stamp = datetime.fromtimestamp(cutoff_ms / 1000, tz=timezone.utc)
    date_time = (max(stamp.year, 1980), stamp.month, stamp.day, stamp.hour, stamp.minute,
                 stamp.second)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ("manifest.json", "data_dictionary.csv", "sessions.csv", "events.csv",
                     "prompts.csv"):
            info = zipfile.ZipInfo(name, date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, files[name])
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Transactions: read one snapshot, store it once, audit a download
# ---------------------------------------------------------------------------


def open_read_snapshot():
    """End this session's current transaction, open one read transaction,
    establish its snapshot with a first read, and only then read the cutoff
    from the server clock. Returns ``cutoff_ms``.

    MySQL/InnoDB creates a REPEATABLE READ read view at the first consistent
    (non-locking) read, and every later plain read of the transaction uses
    it; the isolation level is set explicitly for this transaction. The export reads use no
    locking read, which would bypass the snapshot.
    """
    db.session.rollback()
    connection = db.session.connection(
        execution_options={"isolation_level": "REPEATABLE READ"})
    connection.exec_driver_sql("SELECT COUNT(*) FROM research_sessions").scalar()
    return now_ms()


def _researcher_is_active(actor_id):
    row = db.session.query(User.role, User.status).filter(User.id == actor_id).first()
    return row is not None and row[0] == UserRole.RESEARCHER.value \
        and row[1] == UserStatus.ACTIVE.value


def _lock_researcher(actor_id):
    lock_academic_hierarchy()
    actor = db.session.query(User).filter(User.id == actor_id).with_for_update().first()
    if actor is None or actor.role != UserRole.RESEARCHER.value \
            or actor.status != UserStatus.ACTIVE.value:
        db.session.rollback()
        return None
    from app.services.actor_authorization import require_current_actor
    actor = require_current_actor(actor_id, "researcher")
    return actor


def create_export(actor_id, configuration_public_id, period_from, period_to, tz_name):
    """Read one snapshot, build the archive once and store it with its
    description. ``(status, export_public_id)``."""
    if period_from and period_to and period_to < period_from:
        return BAD_PERIOD, None
    cutoff = open_read_snapshot()
    if not _researcher_is_active(actor_id):
        db.session.rollback()
        return UNAUTHORIZED, None
    configuration_id = None
    if configuration_public_id:
        configuration_id = db.session.query(ResearchConfiguration.id).filter(
            ResearchConfiguration.public_id == configuration_public_id).scalar()
        if configuration_id is None:
            db.session.rollback()
            return BAD_PERIOD, None
    try:
        data, _manifest, counts, digest = build_export(
            configuration_id, period_from, period_to, cutoff, tz_name)
    except ExportTooLarge:
        db.session.rollback()
        return TOO_LARGE, None
    except ClockAhead:
        db.session.rollback()
        return CLOCK_AHEAD, None
    # The snapshot ends here; nothing above wrote anything.
    db.session.rollback()
    if len(data) > MAX_EXPORT_ARCHIVE_BYTES:
        return TOO_LARGE, None

    actor = _lock_researcher(actor_id)
    if actor is None:
        return UNAUTHORIZED, None
    from app.services.research_control_gate import lock_research_control
    from app.models.research_storage import ResearchExportSession
    lock_research_control()
    # A snapshot may be older than a concurrently completed cleanup. Never
    # republish the removed rows in a new archive after that cleanup commits.
    refs = counts["_session_refs"]
    retained = 0
    for start in range(0, len(refs), 500):
        retained += len(db.session.query(ResearchSession.id).filter(ResearchSession.public_id.in_(refs[start:start+500])).with_for_update().all())
    if retained != len(refs):
        db.session.rollback()
        return CONFLICT, None
    export = ResearchExport(
        export_format=EXPORT_FORMAT,
        event_schema_version=EVENT_SCHEMA_VERSION,
        configuration_id=configuration_id,
        period_from=period_from,
        period_to=period_to,
        timezone=tz_name,
        cutoff_ms=cutoff,
        oldest_last_seen_ms=counts["oldest_last_seen_ms"],
        subjects_count=counts["subjects"],
        sessions_count=counts["sessions"],
        events_count=counts["events"],
        prompts_count=counts["prompts"],
        manifest_digest=digest,
        created_by_id=actor.id,
    )
    db.session.add(export)
    db.session.flush()
    db.session.add_all(ResearchExportSession(export_id=export.id, session_public_id=public_id) for public_id in refs)
    db.session.add(ResearchExportArchive(
        export_id=export.id,
        archive_sha256=hashlib.sha256(data).hexdigest(),
        byte_size=len(data),
        content=data,
    ))
    db.session.add(ResearchAuditEvent(
        action=ResearchAuditAction.EXPORT_CREATED.value,
        channel=ResearchAuditChannel.WORKSPACE.value,
        actor_id=actor.id, export_id=export.id, configuration_id=configuration_id,
        count_value=counts["sessions"],
    ))
    public_id = export.public_id
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return CONFLICT, None
    return CREATED, public_id


def download_export(actor_id, export_public_id):
    """``(status, zip_bytes)``: the stored bytes of exactly this export.

    Never regenerated. An archive the retention rule removed is
    :data:`EXPIRED`; one whose stored digest no longer matches its bytes is
    :data:`CORRUPT` and is refused rather than served. Only a served
    download is audited as a download.
    """
    actor = _lock_researcher(actor_id)
    if actor is None:
        return UNAUTHORIZED, None
    export = db.session.query(ResearchExport).filter(
        ResearchExport.public_id == export_public_id).first()
    if export is None:
        db.session.rollback()
        return NOT_FOUND, None
    archive = db.session.query(ResearchExportArchive).filter(
        ResearchExportArchive.export_id == export.id).first()
    if archive is None:
        db.session.rollback()
        return EXPIRED, None
    data = bytes(archive.content)
    if hashlib.sha256(data).hexdigest() != archive.archive_sha256 \
            or len(data) != archive.byte_size:
        db.session.rollback()
        return CORRUPT, None
    db.session.add(ResearchAuditEvent(
        action=ResearchAuditAction.EXPORT_DOWNLOADED.value,
        channel=ResearchAuditChannel.WORKSPACE.value,
        actor_id=actor.id, export_id=export.id, configuration_id=export.configuration_id,
    ))
    db.session.commit()
    return SERVED, data


def archive_available(export_id):
    """Whether the stored archive of an export still exists."""
    return db.session.query(ResearchExportArchive.id).filter(
        ResearchExportArchive.export_id == export_id).first() is not None
