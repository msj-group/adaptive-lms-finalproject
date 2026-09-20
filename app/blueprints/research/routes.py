"""The Researcher portal (Phase 6 / M01).

Three read-only GET rules, every object addressed by ``public_id``::

    GET  /research/dashboard
    GET  /research/participants[?status=][&q=][&page=]
    GET  /research/participants/<participant_public_id>

**Read-only.** Nothing here writes, locks or commits, and there is no form
that posts: a POST, PUT, PATCH or DELETE to any of these is a 405. A
Researcher cannot create a participant, cannot author or edit consent
wording, and cannot consent for anybody -- those are the Administrator's and
the Student's surfaces respectively.

**What a Researcher can see**, and nothing else: a participant's code, its
research status, the version and title of the consent document it is bound
to, and the moments it was invited, decided and (if it happened) withdrew.

**What a Researcher cannot see** is enforced in SQL, not in a template
(``app/services/research_queries.py``): the queries behind these pages never
join ``users`` and never select a name, an email address, a ``users``
public id or any internal numeric id, so no identifying value is loaded at
all. They also read nothing financial, academic, enrollment, group or
messaging, and never the consent **body** -- ethics wording is for the
Student who must read it.

**No research data exists to show.** M01 collects none: no tracking, no
experiment, no session, no survey, no rating, no export and no model. The
dashboard therefore shows four real counts of real rows and nothing that
could be mistaken for a result, and no page here claims to detect or measure
anything about anybody.

``roles_required(RESEARCHER)`` gives the role guard: an anonymous visitor is
redirected to login, and a Student, Teacher or Administrator gets 403. A
suspended Researcher cannot hold a session at all (the Flask-Login
``user_loader`` rejects it). Every response carries ``Cache-Control:
private, no-store`` and ``Vary: Cookie``: these pages describe who is taking
part in a study, and a shared or reused cache entry must never hand one to
somebody else.
"""

from functools import wraps

from flask import abort, current_app, make_response, render_template, request, url_for
from werkzeug.exceptions import HTTPException

from app.blueprints.research import research_bp
from app.models import UserRole
from app.security.decorators import roles_required
from app.services import research_queries as queries

_RESEARCHER = UserRole.RESEARCHER.value


def _tz_name():
    return current_app.config.get("APP_TIMEZONE", "UTC")


def _research_response(view_func):
    """Send ``Cache-Control: private, no-store`` and ``Vary: Cookie`` on
    **every** response this route produces.

    Applied outside ``roles_required``, and catching ``HTTPException``, so
    the rendered page, the login redirect, the 403 and the 404 all carry the
    two headers -- not only the happy path. An error page states, by
    existing, that a participant identifier was or was not recognised, which
    is exactly the kind of answer that must not be cached.
    """

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        try:
            response = make_response(view_func(*args, **kwargs))
        except HTTPException as exc:
            response = make_response(exc.get_response())
        response.headers["Cache-Control"] = "private, no-store"
        response.vary.add("Cookie")
        return response

    return wrapped


@research_bp.get("/dashboard")
@_research_response
@roles_required(_RESEARCHER)
def dashboard():
    """Real participation counts, and an honest statement of what M01 is."""
    counts = queries.participant_counts()
    return render_template(
        "research/dashboard.html",
        counts=counts,
        status_order=queries.STATUS_ORDER,
        status_labels=queries.PARTICIPANT_STATUS_LABELS,
        total=sum(counts.values()),
        participants_url=url_for("research.participants"),
    )


@research_bp.get("/participants")
@_research_response
@roles_required(_RESEARCHER)
def participants():
    """One page of participants, by code and state only."""
    tz_name = _tz_name()
    status = queries.normalize_status_filter(request.args.get("status"))
    search = queries.normalize_code_search(request.args.get("q"))
    rows, total, page = queries.researcher_participants_page(
        status, search, queries.normalize_page(request.args.get("page"))
    )
    records = queries.build_participant_list_view(rows, tz_name)
    for record in records:
        record["detail_url"] = url_for(
            "research.participant_detail", participant_public_id=record["public_id"]
        )
    filters = {
        key: value
        for key, value in (("status", status), ("q", search))
        if value and value != "all"
    }
    first = (page - 1) * queries.PAGE_SIZE + 1 if records else 0
    last = (page - 1) * queries.PAGE_SIZE + len(records)
    list_url = url_for("research.participants")
    return render_template(
        "research/participants.html",
        records=records,
        status=status,
        search=search,
        status_filters=queries.STATUS_FILTERS,
        filtered=bool(filters),
        list_url=list_url,
        tz_name=tz_name,
        pagination={
            "first": first,
            "last": last,
            "total": total,
            "prev_url": url_for("research.participants", page=page - 1, **filters)
            if page > 1
            else None,
            "next_url": url_for("research.participants", page=page + 1, **filters)
            if last < total
            else None,
        },
    )


@research_bp.get("/participants/<participant_public_id>")
@_research_response
@roles_required(_RESEARCHER)
def participant_detail(participant_public_id):
    """One participant's pseudonymous detail. A malformed or unknown
    identifier is the same plain 404, so neither discloses anything."""
    row = queries.researcher_participant(participant_public_id)
    if row is None:
        abort(404)
    return render_template(
        "research/participant.html",
        record=queries.build_participant_view(row, _tz_name()),
        tz_name=_tz_name(),
        list_url=url_for("research.participants"),
    )
