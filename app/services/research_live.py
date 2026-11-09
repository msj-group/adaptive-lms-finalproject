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
from app.services.research_scope import utcnow_naive, within_period
from app.services.research_workspace_queries import active_configuration

ACTIVE_MS = 90_000
ACTIVITY_MS = 120_000
STUDENT_CAP = 200
EVENT_CAP = 60
RECENT_STUDENT_CAP = 12
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
                  students=[], activities=[], recent_students=[], capped=False, active_seconds=ACTIVE_MS // 1000)
    if config is None:
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
                ResearchSubject.provenance == 'study')
    result['active_count'] = people.count() if running else 0
    rows = people.order_by(latest.c.last_seen.desc(), ResearchSubject.id).limit(STUDENT_CAP).all() if running else []
    result['capped'] = result['active_count'] > len(rows)
    retention_floor = moment - config.retention_days * 86400000
    recent = db.session.query(ResearchSession.subject_id.label('subject_id'),
        func.max(ResearchSession.started_at_ms).label('opened_at')).filter(
            ResearchSession.configuration_id == config.id, ResearchSession.provenance == 'study',
            ResearchSession.last_seen_at_ms >= retention_floor).group_by(
                ResearchSession.subject_id).order_by(func.max(ResearchSession.started_at_ms).desc(),
                    ResearchSession.subject_id).limit(RECENT_STUDENT_CAP).subquery()
    recent_rows = db.session.query(ResearchSubject.id, ResearchSubject.subject_code, recent.c.opened_at,
        ResearchEvaluationGroup.partition_code).join(
            recent, recent.c.subject_id == ResearchSubject.id).outerjoin(ResearchEvaluationGroup,
                ResearchEvaluationGroup.subject_id == ResearchSubject.id).filter(
                    ResearchSubject.provenance == 'study').order_by(recent.c.opened_at.desc(), ResearchSubject.id).all()
    if not rows and not recent_rows:
        return result
    cohort = {row.id: row for row in (*rows, *recent_rows)}
    subject_ids = list(cohort)
    groups = defaultdict(list)
    # Owner-approved cohort metadata only: never select the linked account.
    for subject_id, name in db.session.query(ResearchSubjectLink.subject_id, Group.name).join(
        Enrollment, Enrollment.student_id == ResearchSubjectLink.user_id).join(
            Group, Group.id == Enrollment.group_id).filter(
                ResearchSubjectLink.subject_id.in_(subject_ids), Enrollment.status == 'active').distinct().all():
        groups[subject_id].append(name)
    # Operational page/action labels do not expose raw measurements, labels,
    # prompt windows or features. Held-out final-test activity stays private.
    allowed = {row.id for row in cohort.values() if row.partition_code != 'locked_final_test'}
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
    codes = {row.id: row.subject_code for row in cohort.values()}
    for row in rows:
        review = row.id in allowed
        result['students'].append(dict(code=row.subject_code, groups=sorted(groups[row.id]),
            last_seen_ms=row.last_seen, context=_(CONTEXTS.get(contexts.get(row.id), 'Platform')) if review else _('Active on the platform'),
            detail_available=review))
    active_allowed = [row.id for row in rows if row.id in allowed]
    events = db.session.query(ResearchObservation.event_uid, ResearchSession.subject_id,
        ResearchObservation.event_type, ResearchObservation.action_code,
        ResearchObservation.context_code, ResearchObservation.detail_code,
        ResearchObservation.received_at_ms).join(ResearchSession,
            ResearchSession.id == ResearchObservation.session_id).filter(
                ResearchSession.subject_id.in_(active_allowed), ResearchSession.configuration_id == config.id,
                ResearchSession.provenance == 'study', ResearchObservation.received_at_ms >= moment - ACTIVITY_MS,
                ResearchObservation.event_type.in_(('context_enter', 'action_intent', 'media_action', 'workflow_outcome'))).order_by(
                    ResearchObservation.received_at_ms.desc(), ResearchObservation.id.desc()).limit(EVENT_CAP).all() if active_allowed else []
    result['activities'] = [_display_event(event, codes) for event in events]
    recent_allowed = [row.id for row in recent_rows if row.id in allowed]
    by_student = defaultdict(list)
    if recent_allowed:
        ranking = db.session.query(ResearchObservation.event_uid, ResearchSession.subject_id,
            ResearchObservation.event_type, ResearchObservation.action_code, ResearchObservation.context_code,
            ResearchObservation.detail_code, ResearchObservation.received_at_ms,
            func.row_number().over(partition_by=ResearchSession.subject_id,
                order_by=(ResearchObservation.received_at_ms.desc(), ResearchObservation.id.desc())).label('position')).join(
                    ResearchSession, ResearchSession.id == ResearchObservation.session_id).join(recent,
                        (ResearchSession.subject_id == recent.c.subject_id) &
                        (ResearchSession.started_at_ms == recent.c.opened_at)).filter(
                        ResearchSession.subject_id.in_(recent_allowed), ResearchSession.configuration_id == config.id,
                        ResearchSession.provenance == 'study',
                        ResearchObservation.event_type.in_(('context_enter', 'context_leave', 'action_intent', 'media_action', 'workflow_outcome'))).subquery()
        for event in db.session.query(ranking).filter(ranking.c.position <= 4).order_by(ranking.c.position).all():
            by_student[event.subject_id].append(_display_event(event, codes))
        for subject_id, ended_at in db.session.query(ResearchSession.subject_id, ResearchSession.ended_at_ms).join(
            recent, (ResearchSession.subject_id == recent.c.subject_id) &
            (ResearchSession.started_at_ms == recent.c.opened_at)).filter(
                ResearchSession.configuration_id == config.id, ResearchSession.provenance == 'study',
                ResearchSession.subject_id.in_(recent_allowed), ResearchSession.end_reason == 'logout').all():
            actions = by_student[subject_id]
            actions.append(dict(action=_('Signed out'), detail='', context=_('Platform'), at_ms=ended_at))
            actions.sort(key=lambda event: event['at_ms'], reverse=True)
            del actions[4:]
    result['recent_students'] = [dict(code=row.subject_code, groups=sorted(groups[row.id]),
        opened_at_ms=row.opened_at, active=any(student.id == row.id for student in rows),
        detail_available=row.id in allowed, actions=by_student[row.id]) for row in recent_rows]
    return result


def _display_event(event, codes):
    # Intent stays visibly different from a committed server outcome.
    if event.event_type == 'context_enter':
        action = 'Opened from device notification' if event.detail_code == 'opened' and event.context_code == 'notifications' else 'Opened page'
    elif event.event_type == 'context_leave':
        action = 'Left page'
    elif event.event_type == 'workflow_outcome':
        action = 'Action saved' if event.detail_code == 'committed' else 'Action result received'
    else:
        action = 'Media interaction' if event.event_type == 'media_action' else 'Action requested'
    detail = _(ACTIONS.get(event.action_code, 'Interacted')) if event.action_code else ''
    return dict(id=event.event_uid, code=codes[event.subject_id], action=_(action), detail=detail,
                context=_(CONTEXTS.get(event.context_code, 'Platform')), at_ms=event.received_at_ms)


def session_activity(session_id, subject_id, subject_code):
    """Operational summary for an unassigned session, with no measurements."""
    events = db.session.query(ResearchObservation.event_uid, ResearchSession.subject_id,
        ResearchObservation.event_type, ResearchObservation.action_code, ResearchObservation.context_code,
        ResearchObservation.detail_code, ResearchObservation.received_at_ms).join(
            ResearchSession, ResearchSession.id == ResearchObservation.session_id).outerjoin(
                ResearchEvaluationGroup, ResearchEvaluationGroup.subject_id == ResearchSession.subject_id).filter(
                    ResearchSession.id == session_id,
                    func.coalesce(ResearchEvaluationGroup.partition_code, 'unassigned') == 'unassigned',
                    ResearchObservation.event_type.in_(('context_enter', 'context_leave', 'action_intent', 'media_action', 'workflow_outcome'))).order_by(
                        ResearchObservation.received_at_ms.desc(), ResearchObservation.id.desc()).limit(25).all()
    return [_display_event(event, {subject_id: subject_code}) for event in events]
