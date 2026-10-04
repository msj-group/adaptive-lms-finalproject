"""Who is inside the natural-use collection population, and the one read that
decides it (Phase 6 replacement, corrected).

**The population rule.** Collection covers every eligible Student account
automatically -- no operator includes Students one by one. A Student is
collected while all of these hold:

1. the account is a **Student** and **active** (a suspended account cannot
   hold a session at all, and is refused here too);
2. the account is **not excluded**: either it has no research subject yet
   (one is provisioned automatically, by the server, at its first collection
   write) or its subject is ``included``. An explicit exclusion -- recorded
   by an operator, or carried over from a legacy refusal or withdrawal -- is
   never reversed by logging in or visiting a page;
3. a configuration is ``active`` **and collecting**;
4. the server moment lies inside that configuration's collection period.

An active configuration covers **every** Student page and outcome in the
event dictionary: there is no per-area switch that could silently narrow it.
The only pages not observed are the technical exclusions the dictionary
declares (file and audio responses, which are not pages).

None of this is consent and none of it claims any: the external
participation arrangements are the centre's responsibility outside the
application, and an automatically provisioned subject records the
population rule, not an individual decision.

Everything here is re-proved against locked rows by the write paths
(``research_collection``); the unlocked :func:`collection_context` decides
only whether to render the collector and whether a write is worth
attempting.

**One query.** :func:`collection_context` reads the account, its optional
subject and the active configuration in a single statement, memoised per
request by its caller, so a Student page pays exactly one bounded query.

**Provenance** is decided on the server, from the subject and the
deployment's ``RESEARCH_DATA_PROVENANCE``; the client never supplies it.
"""

from collections import namedtuple
from datetime import datetime, timezone

from app.extensions import db
from app.models import (
    ResearchCollectionStatus,
    ResearchConfiguration,
    ResearchConfigurationStatus,
    ResearchProvenance,
    ResearchSubject,
    ResearchSubjectLink,
    User,
    UserRole,
    UserStatus,
)

_STUDENT = UserRole.STUDENT.value
_USER_ACTIVE = UserStatus.ACTIVE.value
_INCLUDED = ResearchCollectionStatus.INCLUDED.value
_ACTIVE = ResearchConfigurationStatus.ACTIVE.value
_DEMO = ResearchProvenance.DEMO.value
_STUDY = ResearchProvenance.STUDY.value

#: The provenances a deployment may declare for what it collects.
DEPLOYMENT_PROVENANCES = (ResearchProvenance.STUDY.value, ResearchProvenance.DEVELOPMENT.value)

CollectionContext = namedtuple(
    "CollectionContext",
    "user_id subject_id subject_provenance configuration_id configuration_version "
    "event_schema_version starts_at ends_at is_collecting policy",
)

#: The configuration policy columns every collection decision needs.
POLICY_COLUMNS = (
    "session_inactivity_minutes", "max_prompts_per_session", "max_prompts_per_day",
    "lookback_seconds", "min_observed_seconds", "random_prompt_permille",
    "activity_end_prompt_permille", "offer_ttl_seconds", "response_window_seconds",
)


def utc_naive(moment_ms):
    return datetime.fromtimestamp(moment_ms / 1000, tz=timezone.utc).replace(tzinfo=None)


def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def within_period(starts_at, ends_at, moment):
    """``starts_at <= moment < ends_at`` on naive UTC values."""
    return starts_at <= moment < ends_at


def collection_context(user_id):
    """The account's collection context when it is **currently eligible**
    (rules 1-4 above), else ``None``. One statement, keyed only by the
    authenticated account id -- so a caller never has to touch a possibly
    expired ``current_user`` to ask.

    ``None`` for any non-Student, a suspended account, an excluded Student,
    no active configuration, a paused one, and a moment outside the period.
    A Student with no subject yet is eligible: the subject is provisioned by
    the first collection write, never here.
    """
    if user_id is None:
        return None
    row = (
        db.session.query(
            User.role,
            User.status,
            ResearchSubject.id,
            ResearchSubject.collection_status,
            ResearchSubject.provenance,
            ResearchConfiguration.id,
            ResearchConfiguration.version_number,
            ResearchConfiguration.event_schema_version,
            ResearchConfiguration.collection_starts_at,
            ResearchConfiguration.collection_ends_at,
            ResearchConfiguration.is_collecting,
            *(getattr(ResearchConfiguration, column) for column in POLICY_COLUMNS),
        )
        .select_from(User)
        .join(ResearchConfiguration, ResearchConfiguration.current_marker == 1)
        .outerjoin(ResearchSubjectLink, ResearchSubjectLink.user_id == User.id)
        .outerjoin(ResearchSubject, ResearchSubject.id == ResearchSubjectLink.subject_id)
        .filter(User.id == user_id)
        .first()
    )
    if row is None or row[0] != _STUDENT or row[1] != _USER_ACTIVE:
        return None
    subject_id, status, provenance = row[2], row[3], row[4]
    if subject_id is not None and status != _INCLUDED:
        return None
    context = CollectionContext(
        user_id=user_id,
        subject_id=subject_id,
        subject_provenance=provenance or _STUDY,
        configuration_id=row[5],
        configuration_version=row[6],
        event_schema_version=row[7],
        starts_at=row[8],
        ends_at=row[9],
        is_collecting=bool(row[10]),
        policy={column: int(row[11 + i]) for i, column in enumerate(POLICY_COLUMNS)},
    )
    if not context.is_collecting:
        return None
    if not within_period(context.starts_at, context.ends_at, utcnow_naive()):
        return None
    return context


def session_provenance(subject_provenance, deployment_provenance):
    """``demo`` for a demonstration subject; otherwise what the deployment
    declares. An unknown deployment value fails safe to ``development``, so a
    misconfiguration can never mark data as study data."""
    if subject_provenance == _DEMO:
        return _DEMO
    if deployment_provenance in DEPLOYMENT_PROVENANCES:
        return deployment_provenance
    return ResearchProvenance.DEVELOPMENT.value


def configuration_is_live(configuration, moment):
    """Rules 3 and 4 against a (locked) configuration row."""
    return (
        configuration is not None
        and configuration.status == _ACTIVE
        and bool(configuration.is_collecting)
        and within_period(
            configuration.collection_starts_at, configuration.collection_ends_at, moment
        )
    )


def account_is_eligible_student(user):
    """Rule 1 against a (locked) ``users`` row."""
    return user is not None and user.role == _STUDENT and user.status == _USER_ACTIVE


def subject_is_included(subject):
    """Rule 2 against a (locked) subject row."""
    return subject is not None and subject.collection_status == _INCLUDED
