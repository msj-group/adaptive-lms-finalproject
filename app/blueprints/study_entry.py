"""Passwordless, per-browser student experience outside research collection."""
import json
import secrets
import uuid
from pathlib import Path

from flask import Blueprint, abort, current_app, make_response, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db, limiter
from app.models import Course, EnrollmentEvent, Group, GroupTeacherAssignment, Notification, User
from app.models.submission_feedback import whole_second_utc
from app.services.academic_hierarchy_transactions import lock_academic_hierarchy
from app.services.enrollment_operations import _new_episode, _target_error
from app.services.financial_history import document_snapshot
from app.services.general_finance import issue_enrollment_invoice
from app.security.passwords import hash_password
from app.services.showcase_scope import is_showcase_student

bp=Blueprint('study_entry',__name__,url_prefix='/showcase')
COOKIE='youth_study_entry'
MAX_AGE=30*86400
DOMAIN='@study.invalid'

def _settings():
    return current_app.config['STUDY_ENTRY_SETTINGS']

def _signer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'],salt='student-study-entry-v1')

def _private(response):
    response=make_response(response)
    response.headers['Cache-Control']='private, no-store'
    response.headers['X-Robots-Tag']='noindex, nofollow, noarchive'
    response.vary.add('Cookie')
    return response

def _resume():
    ticket=request.cookies.get(COOKIE,'')
    if not ticket or len(ticket)>1024:return None
    try:claim=_signer().loads(ticket,max_age=MAX_AGE)
    except (BadSignature,SignatureExpired):return None
    if not isinstance(claim,dict) or set(claim)!={'public_id','auth_version'}:return None
    if not isinstance(claim['public_id'],str) or not isinstance(claim['auth_version'],int):return None
    user=User.query.filter_by(public_id=claim['public_id'],role='student',status='active',auth_version=claim['auth_version']).first()
    if user is None or user.email!='visitor.'+user.public_id+DOMAIN:return None
    return user

def _enter(user):
    login_user(user,remember=True,fresh=True)
    session.pop('workspace_opening_target',None)
    session.pop('study_entry_pending',None)
    response=_private(redirect(url_for('student.dashboard'),code=303))
    response.set_cookie(COOKIE,_signer().dumps({'public_id':user.public_id,'auth_version':user.auth_version}),
        max_age=MAX_AGE,secure=current_app.config['SESSION_COOKIE_SECURE'],httponly=True,samesite='Lax',path='/showcase/')
    return response

def _current_home():
    if current_user.is_authenticated:
        if not is_showcase_student(current_user, cached=True):
            return _private((render_template('auth/study_entry.html',unavailable=False,account_conflict=True),403))
        return _private(redirect(url_for('student.dashboard'),code=303))
    user=_resume()
    return _enter(user) if user is not None else None

def _provision(admission_ref):
    settings=_settings()
    specs=Group.query.filter(Group.public_id.in_(settings['groups'])).all()
    expected={g.id:(g.public_id,g.academic_term_id,g.course_id) for g in specs}
    courses={c.id:c.level_id for c in Course.query.filter(Course.id.in_([g.course_id for g in specs])).all()}
    if len(specs)!=len(settings['groups']):raise ValueError('Entry groups unavailable')
    hierarchy=lock_academic_hierarchy(term_ids=[g[1] for g in expected.values()],course_ids=courses,level_ids=courses.values())
    groups=Group.query.filter(Group.id.in_(expected)).order_by(Group.id).populate_existing().with_for_update().all()
    if len(groups)!=len(expected) or any((g.public_id,g.academic_term_id,g.course_id)!=expected[g.id] for g in groups):
        raise ValueError('Entry group changed')
    teacher_ids=[r[0] for r in db.session.query(GroupTeacherAssignment.teacher_id).filter(
        GroupTeacherAssignment.group_id.in_(expected),GroupTeacherAssignment.status=='active').with_for_update().all()]
    users=User.query.filter(User.id.in_(set(teacher_ids+[settings['owner_admin_id']]))).order_by(User.id).populate_existing().with_for_update().all()
    owner=next((u for u in users if u.id==settings['owner_admin_id'] and u.role=='administrator' and u.status=='active'),None)
    if owner is None:raise ValueError('Entry authorization unavailable')
    existing=User.query.filter_by(public_id=admission_ref).first()
    if existing is not None:
        if existing.role!='student' or existing.status!='active' or existing.email!='visitor.'+admission_ref+DOMAIN:
            raise ValueError('Admission identity unavailable')
        return existing
    # Dedicated group locks serialize admissions across all production workers.
    now=whole_second_utc()
    count=User.query.filter(User.email.like('visitor.%'+DOMAIN),User.created_at>=now.replace(hour=0,minute=0,second=0)).count()
    if count>=settings.get('daily_new_students',200):raise ValueError('Daily admission capacity reached')
    for group in groups:
        course=hierarchy.course(group.course_id)
        if course is None or course.level_id!=courses[group.course_id] or course.price is None or course.price!=0:
            raise ValueError('Only the configured free study courses are admitted')
    public_id=admission_ref
    user=User(public_id=public_id,email='visitor.'+public_id+DOMAIN,full_name='Learner '+public_id[:8],
        password_hash=hash_password(secrets.token_urlsafe(48)),role='student',status='active')
    db.session.add(user);db.session.flush()
    for group in groups:
        error=_target_error(hierarchy,group,user)
        if error:raise ValueError('Study enrollment unavailable')
        episode=_new_episode(user,group,now)
        invoice=issue_enrollment_invoice(user,episode,hierarchy.course(group.course_id),owner.id)
        db.session.add(EnrollmentEvent(enrollment_id=episode.id,actor_id=owner.id,operation_key=str(uuid.uuid4()),
            action='enroll',before_snapshot=None,after_snapshot={'episode':document_snapshot(episode),
                'group':document_snapshot(group),'course':document_snapshot(hierarchy.course(group.course_id)),
                'invoice':document_snapshot(invoice),'admission_workflow':'public-student-experience'},created_at=now))
        db.session.add(Notification(recipient_id=user.id,kind='enrollment_activated',title='Your course is ready',
            message=hierarchy.course(group.course_id).title,target_path='/student/courses/'+group.public_id))
    # No completions, grades, self-reports or research events are fabricated.
    db.session.commit()
    return user

@bp.get('/')
def landing():
    home=_current_home()
    if home is not None:return home
    if 'study_entry_pending' not in session:session['study_entry_pending']=str(uuid.uuid4())
    return _private(render_template('auth/study_entry.html',unavailable=False))

@bp.post('/')
@limiter.limit('5 per minute; 100 per hour')
def begin():
    home=_current_home()
    if home is not None:return home
    if request.form.get('participate')!='yes':abort(400)
    admission_ref=session.get('study_entry_pending')
    try:assert isinstance(admission_ref,str) and str(uuid.UUID(admission_ref))==admission_ref
    except (AssertionError,ValueError):abort(400)
    try:user=_provision(admission_ref)
    except (SQLAlchemyError,ValueError) as exc:
        db.session.rollback()
        current_app.logger.warning('Study admission unavailable type=%s',type(exc).__name__)
        return _private((render_template('auth/study_entry.html',unavailable=True),503))
    return _enter(user)

def register(app):
    settings=app.config.get('STUDY_ENTRY_SETTINGS')
    if settings is None:
        path=Path('/etc/aelms-study-entry/settings.json')
        if not path.is_file():return
        settings=json.loads(path.read_text())
    if not settings or not settings.get('enabled'):return
    assert isinstance(settings.get('owner_admin_id'),int) and settings['owner_admin_id']>0
    assert 1<=len(settings.get('groups',[]))<=2 and len(set(settings['groups']))==len(settings['groups'])
    assert all(str(uuid.UUID(g))==g for g in settings['groups'])
    app.config['STUDY_ENTRY_SETTINGS']=settings
    app.register_blueprint(bp)
    @app.context_processor
    def experience_feedback():
        if current_user.is_authenticated and is_showcase_student(current_user, cached=True):
            from app.models.research_feedback_prompt import RATING_ANCHORS, CAUSE_COLUMNS
            return {'experience_survey': {'anchors': RATING_ANCHORS, 'causes': CAUSE_COLUMNS}}
        return {'experience_survey': None}
    @app.after_request
    def clear_entry_on_logout(response):
        if request.endpoint=='auth.logout':response.delete_cookie(COOKIE,path='/showcase/',secure=True,httponly=True,samesite='Lax')
        return response
