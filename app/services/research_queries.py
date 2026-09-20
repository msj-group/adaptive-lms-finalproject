"""Read queries and presentation for the Phase 6 / M01 research workspace.

Flask-independent. **Read-only**: nothing here adds, changes, flushes, locks
or commits a row.

**The privacy boundary lives in this module, not in a template.** Hiding a
name with ``{% if %}`` would still have loaded it, put it in memory, and
left the next template one mistake away from printing it. So the
Researcher-facing functions -- every function whose name starts with
``researcher_`` -- select **column tuples**, never ORM entities, and the
columns they select are exactly: the participant's ``public_id`` and
``participant_code``, its status, its decision moments, and the consent
document's version identifier and title.

They never select, join to, or load:

- a Student's name or email address;
- any ``users`` column at all, including ``users.public_id`` and
  ``users.id``;
- ``research_participants.student_id`` or ``invited_by_id``;
- any internal numeric id;
- any financial, academic, enrollment, group or messaging column;
- any password or password hash;
- the consent document's **body**, which is ethics wording for the Student
  who must read it, not a Researcher's data field.

The Administrator-facing functions deliberately *do* read the Student side:
following the code-to-account mapping is the one operational reason an
Administrator opens these pages, and only they may. They are named
``admin_``.

**This is pseudonymization, not anonymization.** The link exists in
``research_participants.student_id``, and an Administrator -- or anyone with
database access -- can follow it. What these queries guarantee is that the
Researcher surface never loads it.

**Bounded and free of N+1.** Every list is counted once and paged once
(:data:`PAGE_SIZE`); every detail is a fixed number of queries. No ORM
relationship exists on any M01 model, so no lazy load can fire from a
template.
"""

from sqlalchemy import func

from app.extensions import db
from app.models import (
    ResearchConsentDocument,
    ResearchConsentDocumentStatus,
    ResearchConsentEvent,
    ResearchParticipant,
    ResearchParticipantStatus,
    User,
    UserRole,
    UserStatus,
)
from app.services.schedule_occurrences import to_app_local
from app.services.search_terms import escape_like

#: Participants per page, on both the Researcher and the Administrator list.
PAGE_SIZE = 25

#: A page number beyond this is treated as page 1 rather than reaching SQL
#: as an enormous offset.
_MAX_PAGE = 100_000

#: The longest search string either list accepts.
MAX_SEARCH_LENGTH = 40

#: The most consent events one detail page shows. A participant can have at
#: most three in M01 (accept, then withdraw; or decline), so this is a cap on
#: a bug, not on a workflow.
HISTORY_CAP = 50

#: The most Students the invitation form offers at once.
CANDIDATE_LIMIT = 25

_STUDENT = UserRole.STUDENT.value
_USER_ACTIVE = UserStatus.ACTIVE.value
_DOCUMENT_ACTIVE = ResearchConsentDocumentStatus.ACTIVE.value

#: Every participant status, in the order both dashboards and both lists
#: present them.
STATUS_ORDER = (
    ResearchParticipantStatus.INVITED.value,
    ResearchParticipantStatus.ACTIVE.value,
    ResearchParticipantStatus.DECLINED.value,
    ResearchParticipantStatus.WITHDRAWN.value,
)

PARTICIPANT_STATUS_LABELS = {
    ResearchParticipantStatus.INVITED.value: "Invited",
    ResearchParticipantStatus.ACTIVE.value: "Active",
    ResearchParticipantStatus.DECLINED.value: "Declined",
    ResearchParticipantStatus.WITHDRAWN.value: "Withdrawn",
}

DOCUMENT_STATUS_LABELS = {
    ResearchConsentDocumentStatus.DRAFT.value: "Draft",
    ResearchConsentDocumentStatus.ACTIVE.value: "Active",
    ResearchConsentDocumentStatus.SUPERSEDED.value: "Superseded",
}

CONSENT_ACTION_LABELS = {
    "accepted": "Accepted",
    "declined": "Declined",
    "withdrawn": "Withdrawn",
}

#: The status filter both lists offer, in the order they offer it.
STATUS_FILTERS = {"all": "All participants", **PARTICIPANT_STATUS_LABELS}

_PUBLIC_ID_MAX_LENGTH = 36


def _local(tz_name, moment):
    return None if moment is None else to_app_local(tz_name, moment)


def normalize_page(value):
    """A positive page number. A missing, non-numeric, zero, negative or
    absurdly large value becomes page 1 rather than reaching SQL as an
    offset."""
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    if page < 1 or page > _MAX_PAGE:
        return 1
    return page


def normalize_status_filter(value):
    """One of :data:`STATUS_FILTERS`, or ``"all"``. Anything else is dropped
    rather than guessed at or reported."""
    value = (value or "").strip()
    return value if value in STATUS_FILTERS else "all"


def normalize_code_search(raw):
    """One participant-code search term, upper-cased and bounded.

    **Codes only.** The Researcher list searches ``participant_code`` and
    nothing else: a search box that also matched a name or an email address
    would re-identify participants one query at a time, whatever the results
    table chose to print.
    """
    return " ".join((raw or "").split())[:MAX_SEARCH_LENGTH].strip().upper()


def public_id_is_well_formed(value):
    """A cheap shape check before a public id reaches SQL. A malformed or
    over-long identifier is a 404 at the route, never a query."""
    return isinstance(value, str) and 0 < len(value) <= _PUBLIC_ID_MAX_LENGTH


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------


def participant_counts():
    """``{status: count}`` over every participant, every status present.

    One grouped query. These are the only numbers the Researcher dashboard
    shows: real counts of real rows, never a chart, a rate, an experiment
    result or a behavioural metric, none of which M01 collects.
    """
    rows = (
        db.session.query(ResearchParticipant.status, func.count(ResearchParticipant.id))
        .group_by(ResearchParticipant.status)
        .all()
    )
    counted = dict(rows)
    return {status: int(counted.get(status, 0)) for status in STATUS_ORDER}


def consent_document_counts():
    """``{status: count}`` over every consent document. One grouped query."""
    rows = (
        db.session.query(
            ResearchConsentDocument.status, func.count(ResearchConsentDocument.id)
        )
        .group_by(ResearchConsentDocument.status)
        .all()
    )
    counted = dict(rows)
    return {status: int(counted.get(status, 0)) for status in DOCUMENT_STATUS_LABELS}


# ---------------------------------------------------------------------------
# Researcher-facing reads -- pseudonymous columns only
# ---------------------------------------------------------------------------

#: The exact columns a Researcher list row is built from. Declared once, so
#: the projection is reviewable in a single place and a test can assert it.
RESEARCHER_LIST_COLUMNS = (
    ResearchParticipant.public_id,
    ResearchParticipant.participant_code,
    ResearchParticipant.status,
    ResearchParticipant.created_at,
    ResearchParticipant.decided_at,
    ResearchParticipant.withdrawn_at,
    ResearchConsentDocument.version_identifier,
)

#: A detail row adds only the accepted document's title.
RESEARCHER_DETAIL_COLUMNS = RESEARCHER_LIST_COLUMNS + (ResearchConsentDocument.title,)


def _researcher_query(*columns):
    """A participant query joined only to its consent document.

    ``outerjoin``, because an ``invited`` participant has no document yet --
    and deliberately **no** join to ``users`` at any point, so no identity
    column is even reachable from this statement.
    """
    return db.session.query(*columns).select_from(ResearchParticipant).outerjoin(
        ResearchConsentDocument,
        ResearchConsentDocument.id == ResearchParticipant.consent_document_id,
    )


def _apply_participant_filters(query, status, search):
    if status != "all":
        query = query.filter(ResearchParticipant.status == status)
    if search:
        query = query.filter(
            ResearchParticipant.participant_code.like(f"%{escape_like(search)}%", escape="\\")
        )
    return query


def researcher_participants_page(status, search, page):
    """``(rows, total, page)`` -- one page of participants for a Researcher.

    Newest first. The rows are plain column tuples
    (:data:`RESEARCHER_LIST_COLUMNS`); no ORM entity, and therefore no
    identity column, is loaded.
    """
    total = _apply_participant_filters(
        _researcher_query(func.count(ResearchParticipant.id)), status, search
    ).scalar()
    total = int(total or 0)
    if page > 1 and (page - 1) * PAGE_SIZE >= total:
        page = 1
    rows = (
        _apply_participant_filters(_researcher_query(*RESEARCHER_LIST_COLUMNS), status, search)
        .order_by(ResearchParticipant.id.desc())
        .limit(PAGE_SIZE)
        .offset((page - 1) * PAGE_SIZE)
        .all()
    )
    return rows, total, page


def researcher_participant(public_id):
    """One participant's pseudonymous detail row, or ``None``."""
    if not public_id_is_well_formed(public_id):
        return None
    return (
        _researcher_query(*RESEARCHER_DETAIL_COLUMNS)
        .filter(ResearchParticipant.public_id == public_id)
        .first()
    )


def build_participant_view(row, tz_name):
    """One Researcher-facing participant row as template values.

    Every key is safe to render on a Researcher page. There is no name,
    email, internal id or User public id to leave out, because none was
    selected.
    """
    view = {
        "public_id": row.public_id,
        "participant_code": row.participant_code,
        "status": row.status,
        "status_label": PARTICIPANT_STATUS_LABELS.get(row.status, row.status),
        "consent_version": row.version_identifier,
        "invited_local": _local(tz_name, row.created_at),
        "decided_local": _local(tz_name, row.decided_at),
        "withdrawn_local": _local(tz_name, row.withdrawn_at),
    }
    if hasattr(row, "title"):
        view["consent_title"] = row.title
    return view


def build_participant_list_view(rows, tz_name):
    return [build_participant_view(row, tz_name) for row in rows]


# ---------------------------------------------------------------------------
# Administrator-facing reads -- the protected mapping
# ---------------------------------------------------------------------------


def _admin_query(*columns):
    return (
        db.session.query(*columns)
        .select_from(ResearchParticipant)
        .join(User, User.id == ResearchParticipant.student_id)
        .outerjoin(
            ResearchConsentDocument,
            ResearchConsentDocument.id == ResearchParticipant.consent_document_id,
        )
    )


_ADMIN_COLUMNS = (
    ResearchParticipant.public_id,
    ResearchParticipant.participant_code,
    ResearchParticipant.status,
    ResearchParticipant.created_at,
    ResearchParticipant.decided_at,
    ResearchParticipant.withdrawn_at,
    ResearchConsentDocument.version_identifier,
    User.full_name.label("student_name"),
    User.email.label("student_email"),
    User.status.label("student_status"),
    User.role.label("student_role"),
    User.public_id.label("student_public_id"),
)


def admin_participants_page(status, search, page):
    """``(rows, total, page)`` -- one page of participants for an
    Administrator, with the protected mapping to the Student account.

    The search is still over the participant code only: an Administrator has
    the Students list for finding a person by name, and keeping one search
    rule for both surfaces means the Researcher list cannot quietly inherit a
    name search later.
    """
    total = int(
        _apply_participant_filters(
            _admin_query(func.count(ResearchParticipant.id)), status, search
        ).scalar()
        or 0
    )
    if page > 1 and (page - 1) * PAGE_SIZE >= total:
        page = 1
    rows = (
        _apply_participant_filters(_admin_query(*_ADMIN_COLUMNS), status, search)
        .order_by(ResearchParticipant.id.desc())
        .limit(PAGE_SIZE)
        .offset((page - 1) * PAGE_SIZE)
        .all()
    )
    return rows, total, page


def admin_participant(public_id):
    """One participant row with its Student mapping, or ``None``."""
    if not public_id_is_well_formed(public_id):
        return None
    return (
        _admin_query(*_ADMIN_COLUMNS)
        .filter(ResearchParticipant.public_id == public_id)
        .first()
    )


def build_admin_participant_view(row, tz_name):
    view = build_participant_view(row, tz_name)
    view.update(
        {
            "student_name": row.student_name,
            "student_email": row.student_email,
            "student_status": row.student_status,
            "student_role": row.student_role,
            "student_public_id": row.student_public_id,
        }
    )
    return view


def build_admin_participant_list_view(rows, tz_name):
    return [build_admin_participant_view(row, tz_name) for row in rows]


def participant_history(participant_public_id, tz_name):
    """One participant's complete consent history, oldest first.

    Administrator-only. The event's action, moment and consent version --
    never an actor's name or email, and never a free-form field, because no
    such column exists.
    """
    rows = (
        db.session.query(
            ResearchConsentEvent.action,
            ResearchConsentEvent.occurred_at,
            ResearchConsentEvent.consent_version,
        )
        .select_from(ResearchConsentEvent)
        .join(
            ResearchParticipant,
            ResearchParticipant.id == ResearchConsentEvent.participant_id,
        )
        .filter(ResearchParticipant.public_id == participant_public_id)
        .order_by(ResearchConsentEvent.id.asc())
        .limit(HISTORY_CAP)
        .all()
    )
    return [
        {
            "action": row.action,
            "action_label": CONSENT_ACTION_LABELS.get(row.action, row.action),
            "consent_version": row.consent_version,
            "occurred_local": _local(tz_name, row.occurred_at),
        }
        for row in rows
    ]


def invitable_students(search):
    """Active Students who have no participant yet, for the invitation form.

    Administrator-only, bounded to :data:`CANDIDATE_LIMIT`. The role and
    status filters here decide what to *offer*; they authorize nothing --
    ``app/services/research_transactions.py`` re-proves both against the
    locked ``users`` row before anything is written.
    """
    query = (
        db.session.query(User.public_id, User.full_name, User.email)
        .outerjoin(ResearchParticipant, ResearchParticipant.student_id == User.id)
        .filter(
            User.role == _STUDENT,
            User.status == _USER_ACTIVE,
            ResearchParticipant.id.is_(None),
        )
    )
    term = " ".join((search or "").split())[:MAX_SEARCH_LENGTH].strip()
    if term:
        pattern = f"%{escape_like(term)}%"
        query = query.filter(
            db.or_(User.full_name.like(pattern, escape="\\"),
                   User.email.like(pattern, escape="\\"))
        )
    return query.order_by(User.full_name.asc(), User.id.asc()).limit(CANDIDATE_LIMIT).all()


def student_by_public_id(public_id):
    """``(id, public_id, full_name, email, role, status)`` for one account,
    or ``None``. Used only to decide which row the invitation form and the
    create transaction should lock."""
    if not public_id_is_well_formed(public_id):
        return None
    return (
        db.session.query(
            User.id, User.public_id, User.full_name, User.email, User.role, User.status
        )
        .filter(User.public_id == public_id)
        .first()
    )


# ---------------------------------------------------------------------------
# Consent documents
# ---------------------------------------------------------------------------

_DOCUMENT_COLUMNS = (
    ResearchConsentDocument.public_id,
    ResearchConsentDocument.version_identifier,
    ResearchConsentDocument.title,
    ResearchConsentDocument.status,
    ResearchConsentDocument.body_digest,
    ResearchConsentDocument.created_at,
    ResearchConsentDocument.activated_at,
    ResearchConsentDocument.superseded_at,
)


def consent_documents(limit=PAGE_SIZE, offset=0):
    """``(rows, total)`` -- consent documents, newest first, bounded."""
    total = int(db.session.query(func.count(ResearchConsentDocument.id)).scalar() or 0)
    rows = (
        db.session.query(*_DOCUMENT_COLUMNS)
        .order_by(ResearchConsentDocument.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return rows, total


def consent_document(public_id):
    """One consent document entity by public id, or ``None``.

    Returns the entity, not a projection: this is the Administrator's own
    review page and the activation path, both of which need the wording and
    the digest together.
    """
    if not public_id_is_well_formed(public_id):
        return None
    return ResearchConsentDocument.query.filter_by(public_id=public_id).first()


def active_consent_document():
    """The one ``active`` consent document entity, or ``None``.

    The only document a Student is ever shown. A draft is unfinished wording
    and a superseded one has been replaced, so neither is reachable from the
    Student page at all.
    """
    return ResearchConsentDocument.query.filter_by(status=_DOCUMENT_ACTIVE).first()


def build_document_view(row, tz_name):
    return {
        "public_id": row.public_id,
        "version_identifier": row.version_identifier,
        "title": row.title,
        "status": row.status,
        "status_label": DOCUMENT_STATUS_LABELS.get(row.status, row.status),
        "digest_short": row.body_digest[:12],
        "created_local": _local(tz_name, row.created_at),
        "activated_local": _local(tz_name, row.activated_at),
        "superseded_local": _local(tz_name, row.superseded_at),
    }


def build_document_list_view(rows, tz_name):
    return [build_document_view(row, tz_name) for row in rows]


# ---------------------------------------------------------------------------
# The acting Student's own participant
# ---------------------------------------------------------------------------


def student_participant(student_id):
    """The acting Student's own participant entity, or ``None``.

    Scoped to ``student_id`` in SQL -- the participant is found *from* the
    session, never loaded broadly and authorized afterwards -- so the Student
    consent page has no participant identifier in its URL to tamper with.
    """
    return ResearchParticipant.query.filter_by(student_id=student_id).first()


def student_consent_state(student_id):
    """``(participant, document)`` for the acting Student's consent page.

    `document` is the participant's own accepted document once a decision
    exists, and the currently active one while the participant is still
    ``invited`` -- so a Student who accepted version 1 keeps reading version
    1 on their own page after version 2 is published, rather than being shown
    wording they never agreed to.
    """
    participant = student_participant(student_id)
    if participant is None:
        return None, None
    if participant.consent_document_id is None:
        return participant, active_consent_document()
    return participant, db.session.get(
        ResearchConsentDocument, participant.consent_document_id
    )


# ---------------------------------------------------------------------------
# The Student portal's consent link
# ---------------------------------------------------------------------------

#: The statuses whose participant still has something to *do*: an invited
#: Student owes a decision, and an active one may withdraw. A declined or
#: withdrawn Student is not prompted again -- their page still states their
#: current status correctly, and re-invitation is deferred to a later Part.
PORTAL_LINK_STATUSES = (
    ResearchParticipantStatus.INVITED.value,
    ResearchParticipantStatus.ACTIVE.value,
)


def portal_consent_status(user):
    """The acting Student's participant status for the portal header, or
    ``None`` when no link should be shown.

    ``None`` for an anonymous visitor, a Teacher, an Administrator, a
    Researcher, a Student with no invitation, and a Student who has already
    declined or withdrawn -- so no Student is ever shown a consent prompt
    that is not theirs to answer, and nobody is nagged after deciding.

    One bounded, indexed query returning a single status column: no name, no
    email, no document and no wording is loaded to render a navigation link.
    """
    if not getattr(user, "is_authenticated", False):
        return None
    if user.role != _STUDENT or user.status != _USER_ACTIVE:
        return None
    row = (
        db.session.query(ResearchParticipant.status)
        .filter(ResearchParticipant.student_id == user.id)
        .first()
    )
    if row is None or row[0] not in PORTAL_LINK_STATUSES:
        return None
    return row[0]
