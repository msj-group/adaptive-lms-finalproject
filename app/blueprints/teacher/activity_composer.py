"""One form and one atomic create/publish for all four native activity types."""
import json
import secrets

from flask import abort, current_app, redirect, request, url_for
from flask_login import current_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import (Assignment, Quiz, QuizQuestion, QuestionOption, ListeningActivity,
                        SpeakingActivity, UploadedFile, FileAccessLog, FileAccessAction,
                        MAX_QUIZ_QUESTIONS)
from app.blueprints.teacher.forms import AssignmentForm
from app.blueprints.teacher.quiz_forms import QuizForm, QuizQuestionForm, QuizSettingsForm
from app.blueprints.teacher.listening_forms import ListeningCreateForm
from app.blueprints.teacher.speaking_forms import SpeakingActivityForm
from app.blueprints.teacher.units import (_teacher_group_or_404, _group_is_operational,
    _lock_unit_chain, _authz_broken, _hierarchy_active_error)
from app.services.activity_queries import TYPES
from app.services import teacher_activity_queries as library
from app.services.course_books import private_render
from app.services.curriculum_visibility import curriculum_now
from app.services.file_storage import store_validated_upload
from app.services.file_validation import FileValidationError
from app.services.material_config import current_material_config
from app.services.quiz_attempts import publication_blockers
from app.services.listening_queries import listening_publication_blockers

_SALT = "teacher.activity-composer.20261108"
_ENDPOINTS = {"assignment":("teacher.assignment_detail","assignment_public_id"),
              "quiz":("teacher.quiz_detail","quiz_public_id"),
              "listening":("teacher.listening_detail","listening_public_id"),
              "speaking":("teacher.speaking_detail","speaking_public_id")}


class QuestionInputError(ValueError):
    def __init__(self, message, rows):
        super().__init__(message)
        self.rows = rows


def _signer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_SALT)


def _state(group, kind):
    return dict(actor=current_user.public_id, auth=current_user.auth_version,
                group=group.public_id, course=group.course.public_id, term=group.academic_term.public_id, kind=kind)


def _questions(raw):
    """Validate new question rows with the existing MCQ form contract."""
    if len(raw.encode("utf-8")) > 256 * 1024:
        raise ValueError("The question list is too large.")
    try:
        rows = json.loads(raw)
    except (TypeError, ValueError, RecursionError):
        raise ValueError("Review the questions and try again.") from None
    if not isinstance(rows, list) or len(rows) > MAX_QUIZ_QUESTIONS:
        raise ValueError("Too many questions. Split this activity into smaller tasks.")
    forms = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("options"), list):
            raise ValueError("Review the questions and try again.")
        options = row["options"]
        if any(not isinstance(option, dict) or not isinstance(option.get("text"), str)
               or not isinstance(option.get("correct"), bool) for option in options):
            raise ValueError("Review the answer options and try again.")
        if (not isinstance(row.get("prompt"),str) or not isinstance(row.get("mode"),str)
                or row["mode"] not in {"single","multiple"}):
            raise ValueError("Review each question and its answer type.")
        try:
            row["prompt"].encode("utf-8")
            for option in options:
                option["text"].encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("Review the question text and try again.") from None
        keys = [f"new:{i}" for i in range(len(options))]
        form = QuizQuestionForm(formdata=MultiDict({"prompt":row["prompt"],"answer_mode":row["mode"]}),
            meta={"csrf":False}, option_keys=keys, option_texts=[option["text"] for option in options],
            correct_keys=[keys[i] for i,option in enumerate(options) if option["correct"]])
        if not form.validate():
            errors = [error for values in form.errors.values() for error in values] + form.option_errors
            raise QuestionInputError(f"Question {index+1}: " + " ".join(errors), rows)
        forms.append(form)
    return forms, rows


def _render(group, groups, kind, form=None, settings=None, state=None, questions=None, error=""):
    return private_render("teacher/activities/composer.html", group=group, creation_groups=groups,
        activity_kind=kind, types=TYPES, composer_form=form, settings_form=settings,
        composer_state=state, questions=questions or [], composer_error=error,
        max_questions=MAX_QUIZ_QUESTIONS, question_prompt_max=QuizQuestionForm.PROMPT_MAX,
        option_text_max=QuizQuestionForm.OPTION_TEXT_MAX, tz_name=current_app.config["APP_TIMEZONE"])


def compose_activity():
    actor_id = current_user.id
    values = request.form if request.method == "POST" else request.args
    kind = values.get("type", "assignment")
    if kind not in TYPES:
        abort(400)
    groups = [row for row in library.activity_groups(actor_id) if row["operational"]]
    public_id = values.get("group", "")
    if not public_id and len(groups) == 1:
        public_id = groups[0]["group_public_id"]
    if not public_id:
        if request.method == "POST":
            abort(400)
        return _render(None, groups, kind)
    group = _teacher_group_or_404(public_id)
    if not _group_is_operational(group):
        if request.method == "POST":
            abort(409)
        return _render(None,groups,kind,error="Choose an active group to create an activity.")
    if not any(row["group_public_id"] == group.public_id for row in groups):
        groups.insert(0,dict(group_public_id=group.public_id,group_name=group.name,
                           course_title=group.course.title))
    tz_name = current_app.config["APP_TIMEZONE"]
    constructors = {"assignment":AssignmentForm, "speaking":SpeakingActivityForm,
                    "quiz":QuizForm, "listening":ListeningCreateForm}
    options = dict(group_id=group.id)
    if kind in {"assignment","speaking"}:
        options["tz_name"] = tz_name
    form = constructors[kind](**options)
    settings = QuizSettingsForm(tz_name=tz_name) if kind in {"quiz","listening"} else None
    if settings and request.method == "GET":
        settings.attempt_limit.data = settings.MIN_ATTEMPTS
    state = values.get("composer_state") if request.method == "POST" else _signer().dumps(dict(_state(group,kind),nonce=secrets.token_hex(32)))
    question_rows = []
    if request.method == "GET":
        return _render(group,groups,kind,form,settings,state)
    try:
        payload = _signer().loads(state or "",max_age=7200)
        if (not isinstance(payload,dict) or any(payload.get(key) != value for key,value in _state(group,kind).items())
                or not isinstance(payload.get("nonce"),str) or len(payload["nonce"]) != 64):
            raise ValueError
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        return _render(group,groups,kind,form,settings,state,error="This form expired. Open a new activity and try again.")
    replay_model = ListeningActivity if kind == "listening" else SpeakingActivity if kind == "speaking" else None
    if replay_model:
        previous = replay_model.query.filter_by(creation_nonce=payload["nonce"]).first()
        if previous is not None:
            parent = previous.quiz if kind == "listening" else previous.assignment
            if parent.group_id != group.id:
                abort(404)
            previous_id = previous.public_id
            db.session.rollback()
            endpoint,key = _ENDPOINTS[kind]
            return redirect(url_for(endpoint,group_public_id=public_id,**{key:previous_id}))
    intent = request.form.get("intent")
    if intent not in {"draft","publish"}:
        abort(400)
    question_forms = []
    valid = form.validate()
    if settings:
        valid = settings.validate() and valid
        try:
            question_forms, question_rows = _questions(request.form.get("questions_json","[]"))
        except QuestionInputError as error:
            return _render(group,groups,kind,form,settings,state,error.rows,error=str(error))
        except ValueError as error:
            return _render(group,groups,kind,form,settings,state,error=str(error))
    if not valid:
        return _render(group,groups,kind,form,settings,state,question_rows,error="Review the highlighted fields.")
    stored = None
    committed = False
    commit_unknown = False
    config = None
    try:
        if kind == "listening":
            config = current_material_config()
            upload = form.audio.data
            if _extension(upload.filename) not in {"mp3","wav"}:
                raise ValueError("Choose an MP3 or WAV recording.")
            stored = store_validated_upload(config,upload,upload.filename)
            if stored.category != "audio":
                raise ValueError("Choose an MP3 or WAV recording.")
        term_id, level_id, course_id = group.academic_term_id, group.course.level_id, group.course_id
        hierarchy, group, actor, assignment, _ = _lock_unit_chain(public_id,term_id,level_id,course_id,actor_id)
        if _authz_broken(group,actor,assignment):
            db.session.rollback(); abort(404)
        blocked = _hierarchy_active_error(hierarchy,group,term_id,level_id,course_id)
        if blocked:
            raise ValueError(blocked)
        if actor.auth_version != payload["auth"]:
            db.session.rollback(); abort(404)
        # Extension nonces keep native replay protection. No POST is retried.
        model = ListeningActivity if kind == "listening" else SpeakingActivity if kind == "speaking" else None
        if model:
            prior = model.query.filter_by(creation_nonce=payload["nonce"]).first()
            if prior:
                parent = prior.quiz if kind == "listening" else prior.assignment
                if parent.group_id != group.id:
                    abort(404)
                endpoint,key = _ENDPOINTS[kind]
                prior_id = prior.public_id
                db.session.rollback()
                return redirect(url_for(endpoint,group_public_id=public_id,**{key:prior_id}))
        now = curriculum_now()
        title, instructions = form.title.data.strip(), form.instructions.data.strip()
        if kind in {"assignment","speaking"}:
            item = Assignment(group_id=group.id,title=title,instructions=instructions,
                submission_type=form.submission_type.data if kind == "assignment" else "text",
                opens_at=form.opens_at_utc,due_at=form.due_at_utc,status="draft",published_at=None)
            record = SpeakingActivity(assignment=item,creation_nonce=payload["nonce"],created_at=now,updated_at=now) if kind == "speaking" else item
            db.session.add_all([item,record])
        else:
            item = Quiz(group_id=group.id,title=title,instructions=instructions,status="draft",published_at=None,
                opens_at=settings.opens_at_utc,closes_at=settings.closes_at_utc,
                time_limit_minutes=settings.time_limit_minutes.data,attempt_limit=settings.attempt_limit.data,
                version=1,created_at=now,updated_at=now)
            db.session.add(item)
            record = item
            if kind == "listening":
                uploaded = UploadedFile(storage_key=stored.storage_key,original_filename=stored.original_filename,
                    extension=stored.extension,category=stored.category,content_type=stored.content_type,
                    byte_size=stored.byte_size,sha256=stored.sha256,uploaded_by_id=actor_id)
                record = ListeningActivity(quiz=item,audio_file=uploaded,transcript=(form.transcript.data or "").strip(),
                    transcript_visibility=form.transcript_visibility.data,vocabulary_notes=(form.vocabulary_notes.data or "").strip(),
                    playback_speed_mask=form.normalized_playback_speed_mask(),creation_nonce=payload["nonce"],created_at=now,updated_at=now)
                db.session.add_all([uploaded,record,FileAccessLog(uploaded_file=uploaded,actor_id=actor_id,action=FileAccessAction.UPLOAD.value)])
            db.session.flush()
            for position,question_form in enumerate(question_forms):
                question = QuizQuestion(quiz_id=item.id,prompt=question_form.normalized_prompt(),
                    answer_mode=question_form.answer_mode.data,display_order=position,version=1,created_at=now,updated_at=now)
                db.session.add(question); db.session.flush()
                db.session.add_all([QuestionOption(question_id=question.id,option_text=option.text,
                    display_order=index,is_correct=option.is_correct,created_at=now,updated_at=now)
                    for index,option in enumerate(question_form.options)])
            db.session.flush()
        if intent == "publish":
            if kind in {"quiz","listening"}:
                blockers = listening_publication_blockers(item,record) if kind == "listening" else publication_blockers(item)
                if blockers:
                    raise ValueError(" ".join(blockers))
            item.status = "published"; item.published_at = now
        commit_unknown = True
        db.session.commit(); committed = True; commit_unknown = False
        endpoint,key = _ENDPOINTS[kind]
        return redirect(url_for(endpoint,group_public_id=public_id,**{key:record.public_id}))
    except (ValueError,FileValidationError,IntegrityError) as error:
        db.session.rollback()
        commit_unknown = False
        group = _teacher_group_or_404(public_id)
        message = "This activity could not be saved. Check its title and try again." if isinstance(error,IntegrityError) else str(error)
        return _render(group,groups,kind,form,settings,state,question_rows,error=message)
    except Exception:
        db.session.rollback()
        raise
    finally:
        if stored is not None and not committed and not commit_unknown:
            from app.blueprints.teacher.materials import _cleanup_orphan_upload
            _cleanup_orphan_upload(config,stored.storage_key)


def _extension(name):
    return str(name or "").rsplit(".",1)[-1].lower()
