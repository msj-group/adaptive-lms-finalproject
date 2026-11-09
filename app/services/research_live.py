"""Bounded live presence, with the existing participant-review quarantine.

Presence means server contact in the last 90 seconds, not a login audit or a
guarantee that a browser is foregrounded. No identity, message or answer is read.
"""
from collections import defaultdict

from flask import current_app
from sqlalchemy import func

from app.extensions import db
from app.i18n import gettext as _
from app.models import Enrollment, Group, ResearchSession, ResearchSubject, ResearchSubjectLink
from app.models.research_observations import ResearchEvaluationGroup, ResearchObservation
from app.services.research_observation_queries import REVIEW_GROUPS
from app.services.research_scope import utcnow_naive, within_period
from app.services.research_workspace_queries import active_configuration

ACTIVE_MS = 90_000
ACTIVITY_MS = 120_000
STUDENT_CAP = 200
EVENT_CAP = 60
CONTEXTS = {
    'dashboard': 'Home', 'course_list': 'Courses', 'course': 'Course',
    'settings': 'Settings', 'activity_list': 'Activities', 'records': 'Records',
    'lesson': 'Lesson', 'reader': 'Book', 'search': 'Search',
    'assignment': 'Assignment', 'quiz': 'Quiz', 'listening': 'Listening',
    'speaking': 'Speaking', 'attendance': 'Attendance', 'grades': 'Grades',
    'announcement': 'Announcements', 'calendar': 'Calendar',
    'discussion': 'Discussion', 'messaging': 'Messages',
    'notifications': 'Notifications', 'help': 'Help', 'unknown': 'Platform',
}
ACTIONS = {
    'navigate': 'Navigated', 'open': 'Opened', 'close': 'Closed',
    'search': 'Searched', 'save': 'Saved', 'submit': 'Submitted',
    'answer': 'Answered', 'complete': 'Completed', 'undo_complete': 'Reopened',
    'start': 'Started', 'next': 'Moved forward', 'previous': 'Moved back',
    'retry': 'Retried', 'upload': 'Uploaded', 'download': 'Downloaded',
    'play': 'Played media', 'pause': 'Paused media', 'replay': 'Replayed media',
    'seek': 'Moved in media', 'record': 'Recorded', 'send': 'Sent',
    'help': 'Opened help', 'revise': 'Edited', 'unknown': 'Interacted',
}


def snapshot(moment):
    config = active_configuration()
    running = bool(current_app.config.get('RESEARCH_COLLECTION_ENABLED')
        and current_app.config.get('RESEARCH_DATA_PROVENANCE') == 'study'
        and config and config.is_collecting
        and within_period(config.collection_starts_at, config.collection_ends_at, utcnow_naive()))
    result = dict(server_at_ms=moment, collecting=running, active_count=0,
                  students=[], activities=[], capped=False, active_seconds=ACTIVE_MS // 1000)
    if not running:
        return result
    latest = db.session.query(ResearchSession.subject_id.label('subject_id'),
        func.max(ResearchSession.last_seen_at_ms).label('last_seen')).filter(
            ResearchSession.configuration_id == config.id,
            ResearchSession.provenance == 'study', ResearchSession.ended_at_ms.is_(None),
            ResearchSession.last_seen_at_ms >= moment - ACTIVE_MS).group_by(
                ResearchSession.subject_id).subquery()
    people = db.session.query(ResearchSubject.id, ResearchSubject.subject_code,
        latest.c.last_seen, ResearchEvaluationGroup.partition_code).join(
            latest, latest.c.subject_id == ResearchSubject.id).outerjoin(
            ResearchEvaluationGroup, ResearchEvaluationGroup.subject_id == ResearchSubject.id).filter(
                ResearchSubject.provenance == 'study', ResearchSubject.collection_status == 'included')
    result['active_count'] = people.count()
    rows = people.order_by(latest.c.last_seen.desc(), ResearchSubject.id).limit(STUDENT_CAP).all()
    result['capped'] = result['active_count'] > len(rows)
    if not rows:
        return result
    subject_ids = [row.id for row in rows]
    groups = defaultdict(list)
    # Owner-approved cohort metadata only: never select the linked account.
    for subject_id, name in db.session.query(ResearchSubjectLink.subject_id, Group.name).join(
        Enrollment, Enrollment.student_id == ResearchSubjectLink.user_id).join(
            Group, Group.id == Enrollment.group_id).filter(
                ResearchSubjectLink.subject_id.in_(subject_ids), Enrollment.status == 'active').distinct().all():
        groups[subject_id].append(name)
    allowed = [row.id for row in rows if row.partition_code in REVIEW_GROUPS]
    # Rank within the bounded active cohort; latest context is independent of
    # feed limits and repeated heartbeat/media-interval events.
    recent_context = db.session.query(ResearchSession.subject_id.label('subject_id'),
        ResearchObservation.context_code.label('context'),
        func.row_number().over(partition_by=ResearchSession.subject_id,
            order_by=(ResearchObservation.occurred_at_ms.desc(), ResearchObservation.id.desc())).label('position')).join(
                ResearchSession, ResearchSession.id == ResearchObservation.session_id).filter(
                    ResearchSession.subject_id.in_(allowed), ResearchSession.configuration_id == config.id,
                    ResearchSession.provenance == 'study',
                    ResearchObservation.event_type.in_(('context_enter', 'visible_interval')),
                    ResearchObservation.received_at_ms >= moment - ACTIVE_MS).subquery()
    contexts = dict(db.session.query(recent_context.c.subject_id, recent_context.c.context).filter(
        recent_context.c.position == 1).all()) if allowed else {}
    codes = {row.id: row.subject_code for row in rows}
    for row in rows:
        review = row.id in allowed
        result['students'].append(dict(code=row.subject_code, groups=sorted(groups[row.id]),
            last_seen_ms=row.last_seen, context=_(CONTEXTS.get(contexts.get(row.id), 'Platform')) if review else _('Active on the platform'),
            detail_available=review))
    if not allowed:
        return result
    events = db.session.query(ResearchObservation.event_uid, ResearchSession.subject_id,
        ResearchObservation.event_type, ResearchObservation.action_code,
        ResearchObservation.context_code, ResearchObservation.detail_code,
        ResearchObservation.received_at_ms).join(ResearchSession,
            ResearchSession.id == ResearchObservation.session_id).filter(
                ResearchSession.subject_id.in_(allowed), ResearchSession.configuration_id == config.id,
                ResearchSession.provenance == 'study', ResearchObservation.received_at_ms >= moment - ACTIVITY_MS,
                ResearchObservation.event_type.in_(('context_enter', 'action_intent', 'media_action', 'workflow_outcome'))).order_by(
                    ResearchObservation.received_at_ms.desc(), ResearchObservation.id.desc()).limit(EVENT_CAP).all()
    for event in events:
        # Intent stays visibly different from a committed server outcome.
        action = ('Opened page' if event.event_type == 'context_enter' else
                  ('Action saved' if event.detail_code == 'committed' else 'Action result received')
                  if event.event_type == 'workflow_outcome' else 'Media interaction'
                  if event.event_type == 'media_action' else 'Action requested')
        detail = _(ACTIONS.get(event.action_code, 'Interacted')) if event.action_code else ''
        result['activities'].append(dict(id=event.event_uid, code=codes[event.subject_id],
            action=_(action), detail=detail, context=_(CONTEXTS.get(event.context_code, 'Platform')),
            at_ms=event.received_at_ms))
    return result
