"""The Researcher portal (Phase 6 / M01; the protocol catalogue is M02A).

This module holds the portal's three read-only participation rules, every
object addressed by ``public_id``::

    GET  /research/dashboard
    GET  /research/participants[?status=][&q=][&page=]
    GET  /research/participants/<participant_public_id>

**These three are read-only.** Nothing here writes, locks or commits, and
there is no form on them that posts: a POST, PUT, PATCH or DELETE to any of
them is a 405. A Researcher cannot create a participant, cannot author or
edit consent wording, and cannot consent for anybody -- those are the
Administrator's and the Student's surfaces respectively, and M02A leaves
them exactly so. The Researcher's only writes are to the experiment protocol
catalogue in ``app/blueprints/research/protocols.py``, which holds no
participant data.

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

**No behavioural research data exists to show (M01R).** What M01 records is
research administration -- the participant invitation, the consent decision,
the consent-document reference and the append-only consent events. What it
does **not** collect is behavioural interaction data: no interaction events,
no browser tracking, no experiment or task sessions, no surveys, no
frustration ratings, no observer annotations, no exports, no datasets, no
model training, no inference and no adaptive intervention. M02A adds
Researcher-authored experiment protocol versions -- task definitions, not
observations -- and still collects none of the above. The dashboard
therefore shows real counts of real participant and protocol rows and
nothing that could be mistaken for a result, and no page here claims to
detect or measure anything about anybody.

``roles_required(RESEARCHER)`` gives the role guard: an anonymous visitor is
redirected to login, and a Student, Teacher or Administrator gets 403. A
suspended Researcher cannot hold a session at all (the Flask-Login
``user_loader`` rejects it). Every response carries ``Cache-Control:
private, no-store`` and ``Vary: Cookie``: these pages describe who is taking
part in a study, and a shared or reused cache entry must never hand one to
somebody else.

**Where the headers come from (M02A).** :func:`_research_response` wraps each
of the three views, so their pages, redirects, 403s and 404s carry the
headers. A view wrapper never sees a response Flask produces **before** the
view runs -- the 405 for a wrong method, or the 404 for an unknown path
under ``/research`` -- so :func:`_no_store_under_research` adds the same two
headers to every response whose request path is ``/research`` or lies under
it, whatever produced it. Its guarantee is exactly that: any response Flask
finalizes for such a path. A response that never passes through Flask's
``after_request`` processing (a server or proxy error outside the
application) is outside it.
"""

from functools import wraps

from flask import abort, current_app, make_response, render_template, request, url_for
from werkzeug.exceptions import HTTPException

from app.blueprints.research import research_bp
from app.models import UserRole
from app.security.decorators import roles_required
from app.services import experiment_protocol_queries as protocol_queries
from app.services import research_queries as queries

_RESEARCHER = UserRole.RESEARCHER.value


def _is_research_path(path):
    prefix = research_bp.url_prefix
    return path == prefix or path.startswith(prefix + "/")


@research_bp.after_app_request
def _no_store_under_research(response):
    """``Cache-Control: private, no-store`` and ``Vary: Cookie`` on every
    response for a path under ``/research`` -- including Flask's own 404 and
    405 responses, which never reach a view or a blueprint-level hook.

    Registered application-wide (a blueprint's own ``after_request`` does
    not run for a request whose routing failed, because such a request has
    no blueprint), and scoped by path so no other page is affected.
    """
    if _is_research_path(request.path):
        response.headers["Cache-Control"] = "private, no-store"
        response.vary.add("Cookie")
    return response


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
    """Real participation and protocol counts, and an honest statement of
    what this workspace records and what it does not collect."""
    counts = queries.participant_counts()
    protocol_counts = protocol_queries.protocol_counts()
    return render_template(
        "research/dashboard.html",
        counts=counts,
        status_order=queries.STATUS_ORDER,
        status_labels=queries.PARTICIPANT_STATUS_LABELS,
        total=sum(counts.values()),
        participants_url=url_for("research.participants"),
        protocol_counts=protocol_counts,
        protocol_status_order=protocol_queries.STATUS_ORDER,
        protocol_status_labels=protocol_queries.STATUS_LABELS,
        protocol_total=sum(protocol_counts.values()),
        protocols_url=url_for("research.protocols"),
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
