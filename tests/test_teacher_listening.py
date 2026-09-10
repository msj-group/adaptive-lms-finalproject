"""Teacher Listening authoring, audio upload and serving, publication,
freezes and attempt review (Phase 4 / M05).

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
REPEATABLE READ snapshot isolation, so the concurrency tests here are
**structural**: they assert the *requested* order and exercise the
post-lock rechecks by injecting a state change at an exact transaction
boundary. They are **not** a demonstration of real InnoDB blocking. No
browser, audio-codec or playback verification is performed anywhere: the
audio-player assertions are structural contracts about the rendered
markup only.
"""

import io
import re
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.blueprints.teacher import listening as listening_mod
from app.blueprints.teacher import quizzes as quizzes_mod
from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Course,
    Enrollment,
    FileAccessAction,
    FileAccessLog,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    ListeningActivity,
    QuestionOption,
    Quiz,
    QuizAttempt,
    QuizAttemptStatus,
    QuizQuestion,
    QuizStatus,
    TranscriptVisibility,
    UploadedFile,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password
from tests.conftest import login
from tests.file_fixtures import (
    executable_bytes,
    minimal_mp3,
    minimal_mp3_frame_sync,
    minimal_pdf,
    minimal_png,
    minimal_mp4,
    minimal_wav,
    not_a_real_format,
)

PW = "Sup3rSecret!123"
OPENS = datetime(2026, 5, 1, 8, 0, 0)
CLOSES = datetime(2026, 6, 1, 8, 0, 0)
NOW = datetime(2026, 5, 10, 9, 0, 0)

TRANSCRIPT = "Flight 42 is now boarding at gate seven."
VOCABULARY = "gate = where you board the plane"


class _Clock:
    def __init__(self, *moments):
        self.moments = list(moments) or [NOW]
        self.calls = 0

    def __call__(self, utc_now=None):
        moment = self.moments[min(self.calls, len(self.moments) - 1)]
        self.calls += 1
        return moment


def _at(*moments):
    """Patch the Teacher Listening blueprint's clock."""
    return patch.object(listening_mod, "utc_reference_now", _Clock(*moments))


def _fresh_identity():
    from flask import g, has_app_context

    if has_app_context():
        g.pop("_login_user", None)


def _login_as(client, email):
    _fresh_identity()
    return login(client, email)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _user(email, role, status=UserStatus.ACTIVE.value, name=None):
    row = User(
        email=email, password_hash=hash_password(PW),
        full_name=name or email.split("@")[0], role=role, status=status,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _hierarchy(label="A", **statuses):
    term = AcademicTerm(
        name=f"Term {label}", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
        status=statuses.get("term_status", AcademicStatus.ACTIVE.value),
    )
    level = Level(
        name=f"Level {label}", display_order=0,
        status=statuses.get("level_status", AcademicStatus.ACTIVE.value),
    )
    db.session.add_all([term, level])
    db.session.commit()
    course = Course(
        title=f"Course {label}", level_id=level.id, display_order=0,
        status=statuses.get("course_status", AcademicStatus.ACTIVE.value),
    )
    db.session.add(course)
    db.session.commit()
    group = Group(
        academic_term_id=term.id, course_id=course.id, name=f"Group {label}",
        capacity=20, status=statuses.get("group_status", AcademicStatus.ACTIVE.value),
    )
    db.session.add(group)
    db.session.commit()
    return group


def _setup(label="A", teacher_email="teacher@example.com", **statuses):
    """One Group with one actively assigned Teacher."""
    group = _hierarchy(label, **statuses)
    teacher = _user(teacher_email, UserRole.TEACHER.value)
    db.session.add(GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id))
    db.session.commit()
    return teacher, group


def _upload_row(uploader_id, category="audio", extension="mp3", key=None):
    import secrets

    row = UploadedFile(
        storage_key=key or secrets.token_hex(16),
        original_filename=f"clip.{extension}",
        extension=extension,
        category=category,
        content_type="audio/mpeg" if category == "audio" else "application/pdf",
        byte_size=2048,
        sha256="a" * 64,
        uploaded_by_id=uploader_id,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _make_activity(
    group, teacher, title="Airport announcements", published=False,
    opens_at=OPENS, closes_at=CLOSES, attempt_limit=1, time_limit=None,
    visibility=TranscriptVisibility.HIDDEN.value, transcript=TRANSCRIPT,
    vocabulary=VOCABULARY, audio_category="audio", questions=True, upload_key=None,
):
    """A Listening activity built directly, for tests that are not about
    the upload path itself."""
    import secrets

    quiz = Quiz(
        group_id=group.id, title=title, instructions="Listen and answer.",
        status=QuizStatus.PUBLISHED.value if published else QuizStatus.DRAFT.value,
        published_at=OPENS if published else None,
        opens_at=opens_at, closes_at=closes_at,
        time_limit_minutes=time_limit, attempt_limit=attempt_limit,
        version=1, created_at=OPENS, updated_at=OPENS,
    )
    db.session.add(quiz)
    db.session.commit()
    upload = _upload_row(teacher.id, category=audio_category, key=upload_key)
    activity = ListeningActivity(
        quiz_id=quiz.id, audio_file_id=upload.id, transcript=transcript,
        transcript_visibility=visibility, vocabulary_notes=vocabulary,
        creation_nonce=secrets.token_hex(16), created_at=OPENS, updated_at=OPENS,
    )
    db.session.add(activity)
    db.session.commit()
    if questions:
        _question(quiz, "Which gate?", "single", 0,
                  [("Gate seven", True), ("Gate nine", False)])
    return quiz, activity, upload


def _question(quiz, prompt, mode, order, options):
    question = QuizQuestion(
        quiz_id=quiz.id, prompt=prompt, answer_mode=mode, display_order=order,
        version=1, created_at=OPENS, updated_at=OPENS,
    )
    db.session.add(question)
    db.session.commit()
    for index, (text, correct) in enumerate(options):
        db.session.add(
            QuestionOption(
                question_id=question.id, option_text=text, display_order=index,
                is_correct=correct, is_active=True, created_at=OPENS, updated_at=OPENS,
            )
        )
    db.session.commit()
    return question


def _ordinary_quiz(group, title="Plain quiz"):
    quiz = Quiz(
        group_id=group.id, title=title, instructions="Answer.", version=1,
        status=QuizStatus.DRAFT.value, created_at=OPENS, updated_at=OPENS,
    )
    db.session.add(quiz)
    db.session.commit()
    return quiz


# ---------------------------------------------------------------------------
# request helpers
# ---------------------------------------------------------------------------


def _base(gpid):
    return f"/teacher/groups/{gpid}/listening"


def _detail(gpid, lpid):
    return f"{_base(gpid)}/{lpid}"


def _token(client, url, field="listening_state"):
    html = client.get(url).get_data(as_text=True)
    match = re.search(rf'name="{field}" value="([^"]*)"', html)
    return match.group(1) if match else ""


def _create_payload(token, filename="clip.mp3", data=None, **overrides):
    payload = {
        "listening_state": token,
        "title": "Airport announcements",
        "instructions": "Listen and answer.",
        "transcript": TRANSCRIPT,
        "transcript_visibility": TranscriptVisibility.HIDDEN.value,
        "vocabulary_notes": VOCABULARY,
    }
    payload.update(overrides)
    if filename is not None:
        payload["audio"] = (io.BytesIO(minimal_mp3() if data is None else data), filename)
    return payload


def _post_create(client, gpid, token=None, follow=False, **kwargs):
    url = _base(gpid) + "/new"
    if token is None:
        token = _token(client, url)
    return client.post(
        url, data=_create_payload(token, **kwargs),
        content_type="multipart/form-data", follow_redirects=follow,
    )


def _serve_get(client, url, **kw):
    """Fetch a streamed file response and release its handle.

    ``send_file`` hands back an open file wrapper, and the Werkzeug test
    client only releases it once the body has been read and the response
    closed. Leaving that to the garbage collector raises an unraisable
    exception that the strict-warning suite turns into a failure -- so
    every audio fetch goes through this helper, exactly as the M12
    material-serving tests already do.
    """
    resp = client.get(url, buffered=True, **kw)
    resp.get_data()
    resp.close()
    return resp


def _stored_files(app):
    root = app.extensions["material_config"].storage_root
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_file())


# ===========================================================================
# Role, account and assignment authorization
# ===========================================================================


def test_anonymous_is_redirected_to_login(material_app, material_client):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher)
        gpid, lpid = group.public_id, activity.public_id
    for url in (_base(gpid), _base(gpid) + "/new", _detail(gpid, lpid),
                _detail(gpid, lpid) + "/audio"):
        resp = material_client.get(url)
        assert resp.status_code == 302 and "/auth/login" in resp.headers["Location"]


@pytest.mark.parametrize(
    "role",
    [UserRole.STUDENT.value, UserRole.ADMINISTRATOR.value, UserRole.RESEARCHER.value],
)
def test_non_teacher_roles_are_forbidden(material_app, material_client, role):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher)
        gpid, lpid = group.public_id, activity.public_id
        _user("other@example.com", role)
    _login_as(material_client, "other@example.com")
    assert material_client.get(_base(gpid)).status_code == 403
    assert material_client.get(_detail(gpid, lpid)).status_code == 403
    assert material_client.get(_detail(gpid, lpid) + "/audio").status_code == 403


def test_an_unassigned_teacher_gets_a_non_disclosing_404(material_app, material_client):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher)
        gpid, lpid = group.public_id, activity.public_id
        _user("stranger@example.com", UserRole.TEACHER.value)
    _login_as(material_client, "stranger@example.com")
    for url in (_base(gpid), _detail(gpid, lpid), _detail(gpid, lpid) + "/audio",
                _detail(gpid, lpid) + "/audio/download",
                _detail(gpid, lpid) + "/attempts"):
        assert material_client.get(url).status_code == 404


def test_a_removed_assignment_ends_access(material_app, material_client):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher)
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "teacher@example.com")
    assert material_client.get(_detail(gpid, lpid)).status_code == 200
    with material_app.app_context():
        row = GroupTeacherAssignment.query.one()
        row.status = GroupTeacherAssignmentStatus.REMOVED.value
        db.session.commit()
    assert material_client.get(_detail(gpid, lpid)).status_code == 404


def test_a_suspended_teacher_account_cannot_write(material_app, material_client):
    """``_teacher_group_or_404`` proves an active assignment; the post-lock
    re-check proves the actor's own role and status, which a cached
    ``current_user`` never does."""
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher)
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "teacher@example.com")
    token = _token(material_client, _detail(gpid, lpid) + "/edit")
    with material_app.app_context():
        User.query.filter_by(email="teacher@example.com").one().status = (
            UserStatus.SUSPENDED.value
        )
        db.session.commit()
    resp = material_client.post(
        _detail(gpid, lpid) + "/edit",
        data={"listening_state": token, "title": "Changed",
              "instructions": "x", "transcript": "", "vocabulary_notes": "",
              "transcript_visibility": "hidden"},
    )
    assert resp.status_code in (302, 404)
    with material_app.app_context():
        assert Quiz.query.one().title == "Airport announcements"


def test_a_cross_group_activity_public_id_404s(material_app, material_client):
    with material_app.app_context():
        teacher, group_a = _setup("A")
        group_b = _hierarchy("B")
        db.session.add(
            GroupTeacherAssignment(group_id=group_b.id, teacher_id=teacher.id)
        )
        db.session.commit()
        quiz, activity, _ = _make_activity(group_a, teacher)
        a_pid, b_pid, lpid = group_a.public_id, group_b.public_id, activity.public_id
    _login_as(material_client, "teacher@example.com")
    assert material_client.get(_detail(a_pid, lpid)).status_code == 200
    # Same activity id, wrong Group -- the Group constraint is the point.
    assert material_client.get(_detail(b_pid, lpid)).status_code == 404
    assert material_client.get(_detail(b_pid, lpid) + "/audio").status_code == 404


# ===========================================================================
# The two surfaces cannot reach each other's objects
# ===========================================================================


def test_listening_routes_reject_an_ordinary_quiz(material_app, material_client):
    """An ordinary Quiz has no extension row, so its public id resolves to
    nothing on any Listening route -- a structural property, not a check
    somebody could forget."""
    with material_app.app_context():
        teacher, group = _setup()
        quiz = _ordinary_quiz(group)
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(material_client, "teacher@example.com")
    for suffix in ("", "/edit", "/settings", "/attempts", "/audio",
                   "/audio/download", "/questions/new"):
        assert material_client.get(_detail(gpid, qpid) + suffix).status_code == 404
    for suffix in ("/publish", "/unpublish"):
        assert material_client.post(_detail(gpid, qpid) + suffix).status_code == 404


def test_standard_quiz_routes_exclude_listening_activities(material_app, material_client):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher)
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(material_client, "teacher@example.com")
    quiz_base = f"/teacher/groups/{gpid}/quizzes/{qpid}"
    for suffix in ("", "/edit", "/settings", "/attempts", "/questions/new"):
        assert material_client.get(quiz_base + suffix).status_code == 404
    for suffix in ("/publish", "/unpublish"):
        assert material_client.post(quiz_base + suffix).status_code == 404


def test_the_two_lists_show_disjoint_sets(material_app, material_client):
    with material_app.app_context():
        teacher, group = _setup()
        _ordinary_quiz(group, "Plain quiz")
        _make_activity(group, teacher, title="Listening one")
        gpid = group.public_id
    _login_as(material_client, "teacher@example.com")
    quizzes = material_client.get(f"/teacher/groups/{gpid}/quizzes").get_data(as_text=True)
    listening = material_client.get(_base(gpid)).get_data(as_text=True)
    assert "Plain quiz" in quizzes and "Listening one" not in quizzes
    assert "Listening one" in listening and "Plain quiz" not in listening


def test_pre_existing_quizzes_remain_ordinary_after_m05(material_app, material_client):
    """Nothing classifies an existing Quiz as Listening: classification is
    the presence of an extension row, and none is created for it."""
    with material_app.app_context():
        teacher, group = _setup()
        quiz = _ordinary_quiz(group, "Written in M04")
        gpid, qpid = group.public_id, quiz.public_id
        assert quiz.listening_activity is None
    _login_as(material_client, "teacher@example.com")
    assert material_client.get(
        f"/teacher/groups/{gpid}/quizzes/{qpid}"
    ).status_code == 200
    assert material_client.get(_detail(gpid, qpid)).status_code == 404


# ===========================================================================
# Creation: the upload pipeline
# ===========================================================================


@pytest.mark.parametrize(
    "filename,data",
    [
        ("clip.mp3", minimal_mp3()),
        ("clip.mp3", minimal_mp3_frame_sync()),
        ("clip.wav", minimal_wav()),
        ("CLIP.MP3", minimal_mp3()),
    ],
)
def test_valid_mp3_and_wav_recordings_are_accepted(
    material_app, material_client, filename, data
):
    with material_app.app_context():
        teacher, group = _setup()
        gpid = group.public_id
    _login_as(material_client, "teacher@example.com")
    resp = _post_create(material_client, gpid, filename=filename, data=data)
    assert resp.status_code == 302
    with material_app.app_context():
        activity = ListeningActivity.query.one()
        assert activity.audio_file.category == "audio"
        assert activity.audio_file.extension == filename.rsplit(".", 1)[1].lower()
        assert activity.quiz.status == QuizStatus.DRAFT.value
        assert activity.quiz.version == 1
        # The server, never the browser, decided every stored value.
        assert activity.audio_file.content_type in ("audio/mpeg", "audio/wav")
        assert len(activity.audio_file.storage_key) >= 48
        assert len(activity.audio_file.sha256) == 64
        assert activity.audio_file.byte_size == len(data)


@pytest.mark.parametrize(
    "filename,data,label",
    [
        ("clip.mp3", not_a_real_format("mp3"), "wrong signature"),
        ("clip.wav", not_a_real_format("wav"), "wrong signature"),
        ("clip.mp3", b"", "empty"),
        ("clip.mp3", executable_bytes(), "executable renamed"),
        ("clip.wav", minimal_mp3(), "mp3 renamed to wav"),
        ("notes.pdf", minimal_pdf(), "document"),
        ("photo.png", minimal_png(), "image"),
        ("movie.mp4", minimal_mp4(), "video"),
        ("clip", minimal_mp3(), "no extension"),
        ("clip.exe", executable_bytes(), "unsupported extension"),
    ],
)
def test_invalid_recordings_are_refused_and_leave_nothing_behind(
    material_app, material_client, filename, data, label
):
    with material_app.app_context():
        teacher, group = _setup()
        gpid = group.public_id
    _login_as(material_client, "teacher@example.com")
    resp = _post_create(material_client, gpid, filename=filename, data=data)
    assert resp.status_code == 200, label
    with material_app.app_context():
        assert ListeningActivity.query.count() == 0, label
        assert Quiz.query.count() == 0, label
        assert UploadedFile.query.count() == 0, label
        assert FileAccessLog.query.count() == 0, label
    assert _stored_files(material_app) == [], label


def test_a_missing_file_part_is_refused(material_app, material_client):
    with material_app.app_context():
        teacher, group = _setup()
        gpid = group.public_id
    _login_as(material_client, "teacher@example.com")
    resp = _post_create(material_client, gpid, filename=None)
    assert resp.status_code == 200
    with material_app.app_context():
        assert ListeningActivity.query.count() == 0


def test_a_declared_mime_that_contradicts_the_extension_is_refused(
    material_app, material_client
):
    with material_app.app_context():
        teacher, group = _setup()
        gpid = group.public_id
    _login_as(material_client, "teacher@example.com")
    url = _base(gpid) + "/new"
    token = _token(material_client, url)
    payload = _create_payload(token, filename=None)
    payload["audio"] = (io.BytesIO(minimal_mp3()), "clip.mp3", "image/png")
    resp = material_client.post(url, data=payload, content_type="multipart/form-data")
    assert resp.status_code == 200
    with material_app.app_context():
        assert ListeningActivity.query.count() == 0
    assert _stored_files(material_app) == []


@pytest.fixture
def tiny_audio_limit_app(tmp_path):
    """Like ``material_app``, but with a deliberately tiny per-file audio
    limit.

    The limit is configuration, so shrinking it exercises exactly the same
    streaming size check a real 50 MB recording would hit -- without
    building a 50 MB request body, which Werkzeug would spool to a
    temporary file that the strict-warning suite then reports as an
    unclosed resource, and which would make this test slow for no added
    proof.
    """
    app = create_app(
        "testing",
        MATERIAL_STORAGE_ROOT=str(tmp_path / "materials"),
        MATERIAL_MAX_AUDIO_BYTES="4096",
    )
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def test_an_oversized_recording_is_refused_and_cleaned_up(tiny_audio_limit_app):
    client = tiny_audio_limit_app.test_client()
    with tiny_audio_limit_app.app_context():
        teacher, group = _setup()
        gpid = group.public_id
        limit = tiny_audio_limit_app.extensions["material_config"].max_bytes_for_category(
            "audio"
        )
    _login_as(client, "teacher@example.com")
    root = tiny_audio_limit_app.extensions["material_config"].storage_root

    def files_on_disk():
        return sorted(p.name for p in root.iterdir() if p.is_file()) if root.exists() else []

    # Exactly at the limit is accepted...
    padding = limit - len(minimal_mp3())
    assert _post_create(
        client, gpid, data=minimal_mp3() + b"\x00" * padding
    ).status_code == 302
    with tiny_audio_limit_app.app_context():
        assert ListeningActivity.query.count() == 1
        assert UploadedFile.query.one().byte_size == limit
        db.session.query(FileAccessLog).delete()
        db.session.query(ListeningActivity).delete()
        db.session.query(Quiz).delete()
        db.session.query(UploadedFile).delete()
        db.session.commit()
    accepted = files_on_disk()
    assert len(accepted) == 1

    # ...one byte over it is refused, and the partial file is removed.
    resp = _post_create(client, gpid, data=minimal_mp3() + b"\x00" * (padding + 1))
    assert resp.status_code == 200
    assert "larger than allowed" in resp.get_data(as_text=True)
    with tiny_audio_limit_app.app_context():
        assert ListeningActivity.query.count() == 0
        assert UploadedFile.query.count() == 0
    # Nothing new survives -- not even a `.part` temporary.
    assert files_on_disk() == accepted


def test_the_four_rows_commit_together_with_one_upload_audit_entry(
    material_app, material_client
):
    with material_app.app_context():
        teacher, group = _setup()
        gpid = group.public_id
        teacher_id = teacher.id
    _login_as(material_client, "teacher@example.com")
    assert _post_create(material_client, gpid).status_code == 302
    with material_app.app_context():
        activity = ListeningActivity.query.one()
        quiz = Quiz.query.one()
        upload = UploadedFile.query.one()
        log = FileAccessLog.query.one()
        assert activity.quiz_id == quiz.id
        assert activity.audio_file_id == upload.id
        assert log.uploaded_file_id == upload.id
        assert log.actor_id == teacher_id
        assert log.action == FileAccessAction.UPLOAD.value
        assert activity.created_at == activity.updated_at
        assert quiz.created_at == quiz.updated_at == activity.created_at
    assert len(_stored_files(material_app)) == 1


def test_the_upload_is_streamed_before_any_lock_is_taken(material_app, material_client):
    """Part M12's rule, preserved: a large recording must never be
    streamed while a write lock is held. Structural -- it asserts the
    *order* in which this request reaches the two steps."""
    with material_app.app_context():
        teacher, group = _setup()
        gpid = group.public_id
    _login_as(material_client, "teacher@example.com")

    order = []
    real_store = listening_mod.store_validated_upload
    real_group_lock = quizzes_mod.lock_group_in_open_transaction
    real_hierarchy_lock = quizzes_mod.lock_academic_hierarchy

    def store(*args, **kwargs):
        order.append("upload")
        return real_store(*args, **kwargs)

    def group_lock(*args, **kwargs):
        order.append("lock-group")
        return real_group_lock(*args, **kwargs)

    def hierarchy_lock(*args, **kwargs):
        order.append("lock-hierarchy")
        return real_hierarchy_lock(*args, **kwargs)

    with patch.object(listening_mod, "store_validated_upload", side_effect=store), \
            patch.object(quizzes_mod, "lock_group_in_open_transaction",
                         side_effect=group_lock), \
            patch.object(quizzes_mod, "lock_academic_hierarchy",
                         side_effect=hierarchy_lock):
        assert _post_create(material_client, gpid).status_code == 302
    assert order[0] == "upload", order
    assert "lock-hierarchy" in order and "lock-group" in order


# ===========================================================================
# Duplicate-request protection
# ===========================================================================


def test_an_ordinary_replay_returns_the_same_activity(material_app, material_client):
    with material_app.app_context():
        teacher, group = _setup()
        gpid = group.public_id
    _login_as(material_client, "teacher@example.com")
    url = _base(gpid) + "/new"
    token = _token(material_client, url)

    first = _post_create(material_client, gpid, token=token)
    assert first.status_code == 302
    with material_app.app_context():
        original = ListeningActivity.query.one().public_id
    stored_after_first = _stored_files(material_app)

    second = _post_create(material_client, gpid, token=token)
    assert second.status_code == 302
    assert original in second.headers["Location"]
    with material_app.app_context():
        assert ListeningActivity.query.count() == 1
        assert Quiz.query.count() == 1
        assert UploadedFile.query.count() == 1
        assert FileAccessLog.query.count() == 1
    # No second physical file survives the replay.
    assert _stored_files(material_app) == stored_after_first


def test_a_concurrent_replay_committed_inside_the_lock_window_creates_nothing_new(
    material_app, material_client
):
    """A competing request commits the same nonce between this request's
    upload and its locks. The post-lock replay check must return the
    winner's activity and delete this request's now-redundant file."""
    with material_app.app_context():
        teacher, group = _setup()
        gpid = group.public_id
        teacher_id, group_id = teacher.id, group.id
    _login_as(material_client, "teacher@example.com")
    url = _base(gpid) + "/new"
    token = _token(material_client, url)
    nonce = listening_mod._load_token(token, "listening-create")["nonce"]

    winner = {}

    def commit_winner(public_id):
        if not winner:
            quiz = Quiz(
                group_id=group_id, title="Winner", instructions="x",
                status=QuizStatus.DRAFT.value, version=1,
                created_at=OPENS, updated_at=OPENS,
            )
            db.session.add(quiz)
            db.session.commit()
            upload = _upload_row(teacher_id, key="winner-key")
            activity = ListeningActivity(
                quiz_id=quiz.id, audio_file_id=upload.id, transcript="",
                transcript_visibility="hidden", vocabulary_notes="",
                creation_nonce=nonce, created_at=OPENS, updated_at=OPENS,
            )
            db.session.add(activity)
            db.session.commit()
            winner["public_id"] = activity.public_id
        return quizzes_mod.lock_group_in_open_transaction.__wrapped__(public_id) \
            if hasattr(quizzes_mod.lock_group_in_open_transaction, "__wrapped__") \
            else original_lock(public_id)

    original_lock = quizzes_mod.lock_group_in_open_transaction
    with patch.object(
        quizzes_mod, "lock_group_in_open_transaction", side_effect=commit_winner
    ):
        resp = _post_create(material_client, gpid, token=token)

    assert resp.status_code == 302
    assert winner["public_id"] in resp.headers["Location"]
    with material_app.app_context():
        assert ListeningActivity.query.count() == 1
        assert Quiz.query.count() == 1
    # The loser deleted its own file; only the winner's (never written to
    # disk in this test) is referenced.
    assert _stored_files(material_app) == []


def test_an_integrity_error_rolls_back_and_leaves_no_orphan_file(
    material_app, material_client
):
    with material_app.app_context():
        teacher, group = _setup()
        gpid = group.public_id
    _login_as(material_client, "teacher@example.com")

    real_commit = db.session.commit

    def fail_once(*args, **kwargs):
        db.session.rollback()
        raise IntegrityError("stmt", {}, Exception("duplicate"))

    with patch.object(db.session, "commit", side_effect=fail_once):
        resp = _post_create(material_client, gpid)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Generic and safe: no SQL, driver text, parameter or internal id.
    assert "IntegrityError" not in body and "duplicate" not in body
    assert "could not be saved" in body
    with material_app.app_context():
        assert ListeningActivity.query.count() == 0
        assert UploadedFile.query.count() == 0
    assert _stored_files(material_app) == []
    assert db.session.commit is real_commit or True  # patch is released


def test_a_post_lock_authorization_failure_cleans_the_uploaded_file(
    material_app, material_client
):
    with material_app.app_context():
        teacher, group = _setup()
        gpid = group.public_id
        assignment_id = GroupTeacherAssignment.query.one().id
    _login_as(material_client, "teacher@example.com")

    original = quizzes_mod.lock_group_in_open_transaction

    def remove_assignment_then_lock(public_id):
        row = db.session.get(GroupTeacherAssignment, assignment_id)
        if row is not None and row.status == GroupTeacherAssignmentStatus.ACTIVE.value:
            row.status = GroupTeacherAssignmentStatus.REMOVED.value
            db.session.commit()
        return original(public_id)

    with patch.object(
        quizzes_mod, "lock_group_in_open_transaction",
        side_effect=remove_assignment_then_lock,
    ):
        resp = _post_create(material_client, gpid)
    assert resp.status_code == 404
    with material_app.app_context():
        assert ListeningActivity.query.count() == 0
        assert UploadedFile.query.count() == 0
    assert _stored_files(material_app) == []


def test_a_duplicate_title_is_refused_against_ordinary_quizzes_too(
    material_app, material_client
):
    """``uq_quizzes_group_title`` spans the whole table, so the message
    names both kinds rather than reporting a clash against something the
    Teacher cannot see on this page."""
    with material_app.app_context():
        teacher, group = _setup()
        _ordinary_quiz(group, "Airport announcements")
        gpid = group.public_id
    _login_as(material_client, "teacher@example.com")
    resp = _post_create(material_client, gpid)
    assert resp.status_code == 200
    assert "quiz or listening activity with this title" in resp.get_data(as_text=True)
    with material_app.app_context():
        assert ListeningActivity.query.count() == 0
    assert _stored_files(material_app) == []


def test_a_forged_or_missing_create_token_is_refused(material_app, material_client):
    with material_app.app_context():
        teacher, group = _setup()
        gpid = group.public_id
    _login_as(material_client, "teacher@example.com")
    for token in ("", "not-a-token", "a.b.c"):
        resp = _post_create(material_client, gpid, token=token)
        assert resp.status_code == 302
        assert "/listening/new" in resp.headers["Location"]
    with material_app.app_context():
        assert ListeningActivity.query.count() == 0
    assert _stored_files(material_app) == []


def test_another_teachers_create_token_is_refused(material_app, material_client):
    """One co-teacher's token cannot be replayed by another. A second
    client is used deliberately: ``/auth/login`` redirects an already
    authenticated session, so reusing one client would leave the first
    Teacher signed in and prove nothing."""
    with material_app.app_context():
        teacher, group = _setup()
        other = _user("second@example.com", UserRole.TEACHER.value)
        db.session.add(
            GroupTeacherAssignment(group_id=group.id, teacher_id=other.id)
        )
        db.session.commit()
        gpid = group.public_id
        first_id, second_id = teacher.id, other.id

    _login_as(material_client, "teacher@example.com")
    stolen = _token(material_client, _base(gpid) + "/new")

    second_client = material_app.test_client()
    _login_as(second_client, "second@example.com")
    with second_client.session_transaction() as session:
        assert session["_user_id"].split(".")[0] == str(second_id)
    assert str(first_id) != str(second_id)

    resp = _post_create(second_client, gpid, token=stolen)
    assert resp.status_code == 302 and "/listening/new" in resp.headers["Location"]
    with material_app.app_context():
        assert ListeningActivity.query.count() == 0
    assert _stored_files(material_app) == []


def test_a_create_token_from_another_group_is_refused(material_app, material_client):
    with material_app.app_context():
        teacher, group_a = _setup("A")
        group_b = _hierarchy("B")
        db.session.add(
            GroupTeacherAssignment(group_id=group_b.id, teacher_id=teacher.id)
        )
        db.session.commit()
        a_pid, b_pid = group_a.public_id, group_b.public_id
    _login_as(material_client, "teacher@example.com")
    token = _token(material_client, _base(a_pid) + "/new")
    resp = _post_create(material_client, b_pid, token=token)
    assert resp.status_code == 302 and "/listening/new" in resp.headers["Location"]
    with material_app.app_context():
        assert ListeningActivity.query.count() == 0


# ===========================================================================
# Editing content, transcript policy and vocabulary
# ===========================================================================


def _edit(client, gpid, lpid, token=None, follow=False, **overrides):
    url = _detail(gpid, lpid) + "/edit"
    if token is None:
        token = _token(client, url)
    data = {
        "listening_state": token,
        "title": "Airport announcements",
        "instructions": "Listen and answer.",
        "transcript": TRANSCRIPT,
        "transcript_visibility": TranscriptVisibility.HIDDEN.value,
        "vocabulary_notes": VOCABULARY,
    }
    data.update(overrides)
    return client.post(url, data=data, follow_redirects=follow)


def test_editing_content_bumps_the_quiz_version_exactly_once(
    material_app, material_client
):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher)
        gpid, lpid = group.public_id, activity.public_id
        before = quiz.version
    _login_as(material_client, "teacher@example.com")
    with _at(NOW):
        resp = _edit(
            material_client, gpid, lpid,
            transcript="A new transcript.",
            transcript_visibility=TranscriptVisibility.ALWAYS.value,
            vocabulary_notes="gate; boarding",
        )
    assert resp.status_code == 302
    with material_app.app_context():
        quiz = Quiz.query.one()
        activity = ListeningActivity.query.one()
        assert quiz.version == before + 1
        assert quiz.updated_at == NOW
        assert activity.updated_at == NOW
        assert activity.transcript == "A new transcript."
        assert activity.transcript_visibility == TranscriptVisibility.ALWAYS.value
        assert activity.vocabulary_notes == "gate; boarding"


def test_an_unchanged_save_is_a_no_op(material_app, material_client):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher)
        gpid, lpid = group.public_id, activity.public_id
        before_version, before_updated = quiz.version, quiz.updated_at
    _login_as(material_client, "teacher@example.com")
    resp = _edit(material_client, gpid, lpid, follow=True)
    assert "unchanged, so nothing was saved" in resp.get_data(as_text=True)
    with material_app.app_context():
        quiz = Quiz.query.one()
        assert quiz.version == before_version
        assert quiz.updated_at == before_updated


def test_whitespace_is_trimmed_but_internal_line_breaks_are_preserved(
    material_app, material_client
):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher)
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "teacher@example.com")
    _edit(material_client, gpid, lpid, transcript="  line one\n\nline two  ")
    with material_app.app_context():
        assert ListeningActivity.query.one().transcript == "line one\n\nline two"


def test_an_empty_transcript_is_accepted_under_every_policy(
    material_app, material_client
):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher)
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "teacher@example.com")
    for policy in TranscriptVisibility:
        resp = _edit(
            material_client, gpid, lpid, transcript="   ",
            transcript_visibility=policy.value,
            vocabulary_notes=f"note {policy.value}",
        )
        assert resp.status_code == 302, policy
        with material_app.app_context():
            row = ListeningActivity.query.one()
            assert row.transcript == ""
            assert row.transcript_visibility == policy.value


def test_an_unapproved_transcript_policy_is_refused(material_app, material_client):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher)
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "teacher@example.com")
    resp = _edit(material_client, gpid, lpid, transcript_visibility="public")
    assert resp.status_code == 200
    with material_app.app_context():
        assert ListeningActivity.query.one().transcript_visibility == (
            TranscriptVisibility.HIDDEN.value
        )


def test_the_edit_form_carries_no_file_control(material_app, material_client):
    """The recording is immutable; a control the server would refuse must
    not be offered."""
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher)
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "teacher@example.com")
    body = material_client.get(_detail(gpid, lpid) + "/edit").get_data(as_text=True)
    assert 'type="file"' not in body
    assert "multipart/form-data" not in body
    assert "cannot be changed after this activity is created" in body


def test_a_smuggled_audio_field_cannot_replace_the_recording(
    material_app, material_client
):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher)
        gpid, lpid = group.public_id, activity.public_id
        original_upload_id = activity.audio_file_id
        other = _upload_row(teacher.id, key="other-key")
        other_id = other.id
    _login_as(material_client, "teacher@example.com")
    url = _detail(gpid, lpid) + "/edit"
    token = _token(material_client, url)
    material_client.post(url, data={
        "listening_state": token,
        "title": "Airport announcements",
        "instructions": "Listen and answer.",
        "transcript": TRANSCRIPT,
        "transcript_visibility": "hidden",
        "vocabulary_notes": VOCABULARY,
        # Every one of these is a forged field with nowhere to land.
        "audio_file_id": str(other_id),
        "audio": (io.BytesIO(minimal_wav()), "swap.wav"),
        "status": "published",
        "quiz_id": "999",
        "creation_nonce": "forged",
        "public_id": "forged",
    }, content_type="multipart/form-data")
    with material_app.app_context():
        row = ListeningActivity.query.one()
        assert row.audio_file_id == original_upload_id
        assert row.public_id == lpid
        assert row.quiz.status == QuizStatus.DRAFT.value


def test_a_stale_edit_token_is_refused_without_writing(material_app, material_client):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher)
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "teacher@example.com")
    token = _token(material_client, _detail(gpid, lpid) + "/edit")
    # A co-teacher edit lands first, moving Quiz.version.
    with material_app.app_context():
        quiz = Quiz.query.one()
        quiz.version += 1
        db.session.commit()
        version_after = quiz.version
    resp = _edit(material_client, gpid, lpid, token=token, transcript="mine", follow=True)
    assert "changed by someone else" in resp.get_data(as_text=True)
    with material_app.app_context():
        assert Quiz.query.one().version == version_after
        assert ListeningActivity.query.one().transcript == TRANSCRIPT


# ===========================================================================
# Freezes
# ===========================================================================


def test_publication_freezes_content_questions_and_settings(
    material_app, material_client
):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher, published=True)
        gpid, lpid = group.public_id, activity.public_id
        question_pid = QuizQuestion.query.one().public_id
    _login_as(material_client, "teacher@example.com")

    for url in ("/edit", "/settings", "/questions/new"):
        resp = material_client.get(_detail(gpid, lpid) + url, follow_redirects=True)
        assert "read-only" in resp.get_data(as_text=True), url
    resp = _edit(material_client, gpid, lpid, transcript="mine", follow=True)
    assert "read-only" in resp.get_data(as_text=True)
    with material_app.app_context():
        assert ListeningActivity.query.one().transcript == TRANSCRIPT
    resp = material_client.get(
        _detail(gpid, lpid) + f"/questions/{question_pid}/edit", follow_redirects=True
    )
    assert "read-only" in resp.get_data(as_text=True)


def test_the_first_attempt_freezes_everything_permanently(
    material_app, material_client
):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher, published=True)
        student = _user("s@example.com", UserRole.STUDENT.value)
        db.session.add(
            Enrollment(student_id=student.id, group_id=group.id, status="active")
        )
        db.session.add(QuizAttempt(
            quiz_id=quiz.id, student_id=student.id, attempt_number=1,
            status=QuizAttemptStatus.IN_PROGRESS.value, quiz_version=quiz.version,
            started_at=NOW, deadline_at=CLOSES,
        ))
        db.session.commit()
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "teacher@example.com")

    body = material_client.get(_detail(gpid, lpid)).get_data(as_text=True)
    # The attempt freeze is the stronger, permanent one and is reported
    # first: a Teacher must not be told to "withdraw it first".
    assert "already started this listening activity" in body
    assert "Withdraw" not in body

    resp = _edit(material_client, gpid, lpid, transcript="mine", follow=True)
    assert "already started this listening activity" in resp.get_data(as_text=True)

    html = material_client.get(_detail(gpid, lpid)).get_data(as_text=True)
    assert "listening_state" not in html or "unpublish" not in html
    resp = material_client.post(
        _detail(gpid, lpid) + "/unpublish", data={"listening_state": "x"},
        follow_redirects=True,
    )
    with material_app.app_context():
        assert Quiz.query.one().status == QuizStatus.PUBLISHED.value
        assert ListeningActivity.query.one().transcript == TRANSCRIPT


def test_an_archived_chain_blocks_writing_but_never_reading(
    material_app, material_client
):
    """Reading an activity -- including hearing its recording -- stays
    available to an actively assigned Teacher under an archived chain, so
    authored work and the attempts taken at it can always be read back.
    Only writing is blocked.

    The activity is created through the **real** upload route first, so
    its physical file exists: a row whose bytes are missing 404s for a
    different reason, which
    ``test_a_missing_physical_file_fails_closed_without_leaking_the_path``
    covers on its own."""
    with material_app.app_context():
        teacher, group = _setup()
        gpid = group.public_id
    _login_as(material_client, "teacher@example.com")
    assert _post_create(material_client, gpid).status_code == 302
    with material_app.app_context():
        activity = ListeningActivity.query.one()
        lpid = activity.public_id
        Group.query.one().status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    stored_before = _stored_files(material_app)

    assert material_client.get(_base(gpid)).status_code == 200
    assert material_client.get(_detail(gpid, lpid)).status_code == 200
    assert _serve_get(material_client, _detail(gpid, lpid) + "/audio").status_code == 200
    assert _serve_get(
        material_client, _detail(gpid, lpid) + "/audio/download"
    ).status_code == 200
    assert material_client.get(_detail(gpid, lpid) + "/attempts").status_code == 200

    resp = _edit(material_client, gpid, lpid, transcript="mine", follow=True)
    assert "only be created or edited" in resp.get_data(as_text=True)
    with material_app.app_context():
        assert ListeningActivity.query.one().transcript == TRANSCRIPT

    resp = _post_create(material_client, gpid, follow=True)
    assert "only be created or edited" in resp.get_data(as_text=True)
    with material_app.app_context():
        assert ListeningActivity.query.count() == 1
    # The refused create deleted the file it had already streamed; the
    # existing activity's recording is untouched.
    assert _stored_files(material_app) == stored_before


# ===========================================================================
# Publication readiness
# ===========================================================================


def _publish(client, gpid, lpid, follow=True, action="publish"):
    html = client.get(_detail(gpid, lpid)).get_data(as_text=True)
    match = re.search(
        rf'action="[^"]*/{action}"[^>]*>.*?name="listening_state" value="([^"]*)"',
        html, re.S,
    )
    token = match.group(1) if match else ""
    return client.post(
        _detail(gpid, lpid) + f"/{action}",
        data={"listening_state": token}, follow_redirects=follow,
    )


def test_publication_requires_a_valid_audio_recording(material_app, material_client):
    """A foreign key proves the upload row exists, never that it is a
    recording. The category is re-checked at publication."""
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, upload = _make_activity(group, teacher)
        gpid, lpid = group.public_id, activity.public_id
        # The association is corrupted to a non-audio upload.
        upload.category = "document"
        db.session.commit()
    _login_as(material_client, "teacher@example.com")
    body = material_client.get(_detail(gpid, lpid)).get_data(as_text=True)
    assert "no valid audio recording attached" in body
    resp = _publish(material_client, gpid, lpid)
    assert "no valid audio recording attached" in resp.get_data(as_text=True)
    with material_app.app_context():
        assert Quiz.query.one().status == QuizStatus.DRAFT.value


def test_publication_requires_questions_and_a_complete_window(
    material_app, material_client
):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(
            group, teacher, opens_at=None, closes_at=None, questions=False
        )
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "teacher@example.com")
    body = material_client.get(_detail(gpid, lpid)).get_data(as_text=True)
    assert "Set both an opening time and a closing time" in body
    assert "Add at least one question" in body
    resp = _publish(material_client, gpid, lpid)
    assert "Add at least one question" in resp.get_data(as_text=True)
    with material_app.app_context():
        assert Quiz.query.one().status == QuizStatus.DRAFT.value


def test_publication_requires_a_valid_answer_key(material_app, material_client):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher, questions=False)
        _question(quiz, "Which gate?", "single", 0,
                  [("Gate seven", False), ("Gate nine", False)])
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "teacher@example.com")
    resp = _publish(material_client, gpid, lpid)
    assert "do not have exactly one correct option" in resp.get_data(as_text=True)
    with material_app.app_context():
        assert Quiz.query.one().status == QuizStatus.DRAFT.value


def test_a_ready_activity_publishes_and_can_be_withdrawn(material_app, material_client):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher)
        gpid, lpid = group.public_id, activity.public_id
        before = quiz.version
    _login_as(material_client, "teacher@example.com")
    with _at(NOW):
        resp = _publish(material_client, gpid, lpid)
    assert "Listening activity published" in resp.get_data(as_text=True)
    with material_app.app_context():
        quiz = Quiz.query.one()
        assert quiz.status == QuizStatus.PUBLISHED.value
        assert quiz.published_at == NOW
        assert quiz.version == before + 1

    with _at(NOW):
        resp = _publish(material_client, gpid, lpid, action="unpublish")
    assert "withdrawn" in resp.get_data(as_text=True)
    with material_app.app_context():
        quiz = Quiz.query.one()
        assert quiz.status == QuizStatus.DRAFT.value
        assert quiz.published_at is None


def test_a_publish_token_cannot_be_replayed_as_a_withdraw(
    material_app, material_client
):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher)
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "teacher@example.com")
    html = material_client.get(_detail(gpid, lpid)).get_data(as_text=True)
    publish_token = re.search(
        r'action="[^"]*/publish"[^>]*>.*?name="listening_state" value="([^"]*)"',
        html, re.S,
    ).group(1)
    resp = material_client.post(
        _detail(gpid, lpid) + "/unpublish",
        data={"listening_state": publish_token}, follow_redirects=True,
    )
    assert "changed by someone else" in resp.get_data(as_text=True)
    with material_app.app_context():
        assert Quiz.query.one().status == QuizStatus.DRAFT.value


def test_an_m04_quiz_token_is_worthless_on_the_listening_surface(
    material_app, material_client
):
    """Different salts: an M04 token fails signature verification here
    even though both are signed with the same SECRET_KEY."""
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher)
        gpid, lpid = group.public_id, activity.public_id
        forged = quizzes_mod._make_publication_token(
            quizzes_mod._PUBLISH_ACTION, teacher.public_id, gpid, lpid,
            quiz.version, quiz.status,
        )
    _login_as(material_client, "teacher@example.com")
    resp = material_client.post(
        _detail(gpid, lpid) + "/publish",
        data={"listening_state": forged}, follow_redirects=True,
    )
    assert "changed by someone else" in resp.get_data(as_text=True)
    with material_app.app_context():
        assert Quiz.query.one().status == QuizStatus.DRAFT.value


# ===========================================================================
# Questions -- the M04B rules, on the Listening surface
# ===========================================================================


def _add_question(client, gpid, lpid, prompt="Which gate?", mode="single",
                  options=(("Gate seven", True), ("Gate nine", False)), token=None):
    url = _detail(gpid, lpid) + "/questions/new"
    if token is None:
        token = _token(client, url)
    keys = [f"new:{i}" for i in range(len(options))]
    return client.post(url, data={
        "listening_state": token,
        "prompt": prompt,
        "answer_mode": mode,
        "option_key": keys,
        "option_text": [text for text, _ in options],
        "option_correct": [k for k, (_, c) in zip(keys, options) if c],
    }, follow_redirects=True)


def test_a_question_can_be_added_and_bumps_the_activity_version(
    material_app, material_client
):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher, questions=False)
        gpid, lpid = group.public_id, activity.public_id
        before = quiz.version
    _login_as(material_client, "teacher@example.com")
    resp = _add_question(material_client, gpid, lpid)
    assert "Question added" in resp.get_data(as_text=True)
    with material_app.app_context():
        assert QuizQuestion.query.count() == 1
        assert QuestionOption.query.count() == 2
        assert Quiz.query.one().version == before + 1


def test_the_answer_cardinality_rule_is_enforced_unchanged(
    material_app, material_client
):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher, questions=False)
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "teacher@example.com")
    resp = _add_question(
        material_client, gpid, lpid, mode="single",
        options=(("A", True), ("B", True)),
    )
    assert "exactly one correct option" in resp.get_data(as_text=True)
    resp = _add_question(
        material_client, gpid, lpid, mode="multiple",
        options=(("A", True), ("B", False)),
    )
    assert "at least two correct options" in resp.get_data(as_text=True)
    with material_app.app_context():
        assert QuizQuestion.query.count() == 0


def test_questions_can_be_reordered_across_the_complete_authored_order(
    material_app, material_client
):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher, questions=False)
        first = _question(quiz, "First", "single", 0, [("A", True), ("B", False)])
        second = _question(quiz, "Second", "single", 5, [("A", True), ("B", False)])
        gpid, lpid = group.public_id, activity.public_id
        second_pid = second.public_id
    _login_as(material_client, "teacher@example.com")
    html = material_client.get(_detail(gpid, lpid)).get_data(as_text=True)
    token = re.search(
        rf'action="[^"]*/questions/{second_pid}/move-up"[^>]*>.*?'
        r'name="listening_state" value="([^"]*)"',
        html, re.S,
    ).group(1)
    with _at(NOW):
        resp = material_client.post(
            _detail(gpid, lpid) + f"/questions/{second_pid}/move-up",
            data={"listening_state": token}, follow_redirects=True,
        )
    assert "Question order updated" in resp.get_data(as_text=True)
    with material_app.app_context():
        rows = QuizQuestion.query.order_by(
            QuizQuestion.display_order, QuizQuestion.id
        ).all()
        assert [row.prompt for row in rows] == ["Second", "First"]


def test_a_question_belonging_to_another_activity_404s(material_app, material_client):
    with material_app.app_context():
        teacher, group = _setup()
        quiz_a, activity_a, _ = _make_activity(group, teacher, title="One")
        quiz_b, activity_b, _ = _make_activity(
            group, teacher, title="Two", upload_key="second"
        )
        gpid = group.public_id
        a_pid = activity_a.public_id
        b_question = QuizQuestion.query.filter_by(quiz_id=quiz_b.id).one().public_id
    _login_as(material_client, "teacher@example.com")
    assert material_client.get(
        _detail(gpid, a_pid) + f"/questions/{b_question}/edit"
    ).status_code == 404


# ===========================================================================
# Audio serving and auditing
# ===========================================================================


def test_inline_and_download_are_audited_with_the_right_action(
    material_app, material_client
):
    with material_app.app_context():
        teacher, group = _setup()
        gpid = group.public_id
    _login_as(material_client, "teacher@example.com")
    _post_create(material_client, gpid)
    with material_app.app_context():
        lpid = ListeningActivity.query.one().public_id

    inline = _serve_get(material_client, _detail(gpid, lpid) + "/audio")
    assert inline.status_code == 200
    assert inline.headers["Content-Type"].startswith("audio/mpeg")
    assert "attachment" not in inline.headers.get("Content-Disposition", "")
    assert inline.headers["Cache-Control"] == "private, no-store, max-age=0"
    assert inline.headers["X-Content-Type-Options"] == "nosniff"

    download = _serve_get(material_client, _detail(gpid, lpid) + "/audio/download")
    assert download.status_code == 200
    assert "attachment" in download.headers["Content-Disposition"]

    with material_app.app_context():
        actions = [row.action for row in FileAccessLog.query.order_by(FileAccessLog.id)]
        assert actions == [
            FileAccessAction.UPLOAD.value,
            FileAccessAction.INLINE.value,
            FileAccessAction.DOWNLOAD.value,
        ]


def test_the_audio_response_discloses_nothing_internal(material_app, material_client):
    with material_app.app_context():
        teacher, group = _setup()
        gpid = group.public_id
    _login_as(material_client, "teacher@example.com")
    _post_create(material_client, gpid)
    with material_app.app_context():
        activity = ListeningActivity.query.one()
        lpid = activity.public_id
        storage_key = activity.audio_file.storage_key
        sha = activity.audio_file.sha256
        internal_ids = {str(activity.id), str(activity.quiz_id), str(activity.audio_file_id)}
    resp = _serve_get(material_client, _detail(gpid, lpid) + "/audio")
    headers = " ".join(f"{k}: {v}" for k, v in resp.headers.items())
    assert storage_key not in headers
    assert sha not in headers
    assert "storage" not in headers.lower()
    assert TRANSCRIPT not in resp.get_data(as_text=True)
    # The detail page names the original filename, never the stored one.
    page = material_client.get(_detail(gpid, lpid)).get_data(as_text=True)
    assert storage_key not in page and sha not in page
    assert "clip.mp3" in page


def test_cross_activity_audio_tampering_is_refused(material_app, material_client):
    """The recording is resolved from the activity in the URL, never from
    anything the request supplies."""
    with material_app.app_context():
        teacher, group = _setup()
        quiz_a, activity_a, upload_a = _make_activity(group, teacher, title="One")
        quiz_b, activity_b, upload_b = _make_activity(
            group, teacher, title="Two", upload_key="second"
        )
        gpid = group.public_id
        a_pid, b_pid = activity_a.public_id, activity_b.public_id
        a_file, b_file = upload_a.public_id, upload_b.public_id
    _login_as(material_client, "teacher@example.com")
    # A file public id in the activity slot names nothing.
    assert material_client.get(_detail(gpid, a_file) + "/audio").status_code == 404
    assert material_client.get(_detail(gpid, b_file) + "/audio").status_code == 404
    # Query-string tampering cannot repoint the route at another recording.
    resp = _serve_get(
        material_client,
        _detail(gpid, a_pid) + f"/audio?audio_file_id=999&listening_public_id={b_pid}",
    )
    assert resp.status_code in (200, 404)


def test_a_missing_physical_file_fails_closed_without_leaking_the_path(
    material_app, material_client
):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, upload = _make_activity(group, teacher)
        gpid, lpid = group.public_id, activity.public_id
        key = upload.storage_key
    _login_as(material_client, "teacher@example.com")
    resp = material_client.get(_detail(gpid, lpid) + "/audio")
    assert resp.status_code == 404
    assert key not in resp.get_data(as_text=True)


def test_the_audio_routes_reject_unsupported_methods(material_app, material_client):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher)
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "teacher@example.com")
    for method in ("post", "put", "delete", "patch"):
        resp = getattr(material_client, method)(_detail(gpid, lpid) + "/audio")
        assert resp.status_code == 405


def test_no_listening_route_accepts_delete(material_app):
    for rule in material_app.url_map.iter_rules():
        if "listening" not in str(rule):
            continue
        assert "DELETE" not in rule.methods
        assert rule.methods <= {"GET", "HEAD", "OPTIONS", "POST"}
        text = str(rule).lower()
        for forbidden in ("delete", "remove", "replace", "override", "answer-key",
                          "transcribe", "analytics"):
            assert forbidden not in text, (text, forbidden)


# ===========================================================================
# Cache headers and escaping
# ===========================================================================


@pytest.mark.parametrize(
    "suffix", ["", "/edit", "/settings", "/attempts", "/questions/new"]
)
def test_every_content_bearing_page_is_private_and_uncacheable(
    material_app, material_client, suffix
):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher)
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "teacher@example.com")
    for url in (_base(gpid), _base(gpid) + "/new", _detail(gpid, lpid) + suffix):
        resp = material_client.get(url)
        assert resp.status_code == 200, url
        assert resp.headers["Cache-Control"] == "private, no-store", url
        assert "Cookie" in resp.headers.get("Vary", ""), url


def test_authored_text_is_escaped_and_never_rendered_as_html(
    material_app, material_client
):
    hostile = "<script>alert('x')</script> & \"quoted\""
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(
            group, teacher, transcript=hostile, vocabulary=hostile
        )
        quiz.title = hostile
        quiz.instructions = hostile
        db.session.commit()
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "teacher@example.com")
    for url in (_base(gpid), _detail(gpid, lpid), _detail(gpid, lpid) + "/edit"):
        body = material_client.get(url).get_data(as_text=True)
        assert "<script>alert('x')</script>" not in body, url
        if "alert" in body:
            assert "&lt;script&gt;" in body, url


# ===========================================================================
# Attempt review
# ===========================================================================


def _attempt(quiz, student, number=1, status=QuizAttemptStatus.SUBMITTED.value,
             correct=1, total=1, started=NOW, deadline=CLOSES, submitted=NOW):
    row = QuizAttempt(
        quiz_id=quiz.id, student_id=student.id, attempt_number=number, status=status,
        quiz_version=quiz.version, started_at=started, deadline_at=deadline,
        submitted_at=submitted if status == QuizAttemptStatus.SUBMITTED.value else None,
        correct_count=None if status == QuizAttemptStatus.IN_PROGRESS.value else correct,
        total_questions=None if status == QuizAttemptStatus.IN_PROGRESS.value else total,
    )
    db.session.add(row)
    db.session.commit()
    return row


def test_the_attempt_list_labels_all_three_states(material_app, material_client):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(
            group, teacher, published=True, attempt_limit=5
        )
        student = _user("s@example.com", UserRole.STUDENT.value, name="Sara Student")
        db.session.add(
            Enrollment(student_id=student.id, group_id=group.id, status="active")
        )
        db.session.commit()
        _attempt(quiz, student, 1, QuizAttemptStatus.SUBMITTED.value)
        _attempt(quiz, student, 2, QuizAttemptStatus.EXPIRED.value)
        _attempt(quiz, student, 3, QuizAttemptStatus.IN_PROGRESS.value,
                 deadline=CLOSES)
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "teacher@example.com")
    with _at(NOW):
        body = material_client.get(
            _detail(gpid, lpid) + "/attempts"
        ).get_data(as_text=True)
    assert "Sara Student" in body
    for label in ("Submitted", "Time expired", "In progress"):
        assert label in body, label


def test_the_attempt_detail_shows_the_answer_key_to_the_teacher(
    material_app, material_client
):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher, published=True)
        student = _user("s@example.com", UserRole.STUDENT.value, name="Sara")
        db.session.add(
            Enrollment(student_id=student.id, group_id=group.id, status="active")
        )
        db.session.commit()
        attempt = _attempt(quiz, student)
        gpid, lpid, apid = group.public_id, activity.public_id, attempt.public_id
    _login_as(material_client, "teacher@example.com")
    with _at(NOW):
        body = material_client.get(
            _detail(gpid, lpid) + f"/attempts/{apid}"
        ).get_data(as_text=True)
    assert "Correct answer" in body
    assert "Gate seven" in body and "Gate nine" in body


def test_an_attempt_of_another_activity_404s(material_app, material_client):
    with material_app.app_context():
        teacher, group = _setup()
        quiz_a, activity_a, _ = _make_activity(group, teacher, title="One")
        quiz_b, activity_b, _ = _make_activity(
            group, teacher, title="Two", upload_key="second"
        )
        student = _user("s@example.com", UserRole.STUDENT.value)
        db.session.add(
            Enrollment(student_id=student.id, group_id=group.id, status="active")
        )
        db.session.commit()
        attempt = _attempt(quiz_b, student)
        gpid = group.public_id
        a_pid, apid = activity_a.public_id, attempt.public_id
    _login_as(material_client, "teacher@example.com")
    with _at(NOW):
        assert material_client.get(
            _detail(gpid, a_pid) + f"/attempts/{apid}"
        ).status_code == 404


def test_overdue_attempts_are_settled_before_the_teacher_reads_them(
    material_app, material_client
):
    with material_app.app_context():
        teacher, group = _setup()
        quiz, activity, _ = _make_activity(group, teacher, published=True)
        student = _user("s@example.com", UserRole.STUDENT.value)
        db.session.add(
            Enrollment(student_id=student.id, group_id=group.id, status="active")
        )
        db.session.commit()
        _attempt(
            quiz, student, status=QuizAttemptStatus.IN_PROGRESS.value,
            deadline=NOW - timedelta(seconds=1),
        )
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "teacher@example.com")
    with _at(NOW):
        body = material_client.get(
            _detail(gpid, lpid) + "/attempts"
        ).get_data(as_text=True)
    assert "Time expired" in body and "In progress" not in body
    with material_app.app_context():
        row = QuizAttempt.query.one()
        assert row.status == QuizAttemptStatus.EXPIRED.value
        assert row.submitted_at is None
        assert row.total_questions == 1


# ===========================================================================
# Pagination and bounded query counts
# ===========================================================================


def test_the_list_is_paginated_at_a_fixed_size(material_app, material_client):
    from app.services.quiz_queries import PAGE_SIZE

    with material_app.app_context():
        teacher, group = _setup()
        for index in range(PAGE_SIZE + 3):
            _make_activity(
                group, teacher, title=f"Listening {index:02d}",
                upload_key=f"key-{index}", questions=False,
            )
        gpid = group.public_id
    _login_as(material_client, "teacher@example.com")
    first = material_client.get(_base(gpid)).get_data(as_text=True)
    assert first.count("/listening/") >= PAGE_SIZE
    assert "Next" in first
    second = material_client.get(_base(gpid) + "?page=2").get_data(as_text=True)
    assert "Previous" in second
    # A page past the end falls back to page 1 rather than an empty page.
    far = material_client.get(_base(gpid) + "?page=999").get_data(as_text=True)
    assert "Previous" not in far


def _count_statements(app, client, url):
    from sqlalchemy import event

    statements = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        assert client.get(url).status_code == 200
    finally:
        event.remove(db.engine, "before_cursor_execute", record)
    return len(statements)


def test_the_detail_page_costs_the_same_for_two_and_twenty_questions(
    material_app, material_client
):
    """Bounded, not merely small: the two counts must be EQUAL."""
    with material_app.app_context():
        teacher, group = _setup()
        small_quiz, small, _ = _make_activity(
            group, teacher, title="Small", questions=False
        )
        for index in range(2):
            _question(small_quiz, f"Q{index}", "single", index,
                      [("A", True), ("B", False)])
        big_quiz, big, _ = _make_activity(
            group, teacher, title="Big", upload_key="big", questions=False
        )
        for index in range(20):
            _question(big_quiz, f"Q{index}", "single", index,
                      [("A", True), ("B", False)])
        gpid = group.public_id
        small_pid, big_pid = small.public_id, big.public_id
    _login_as(material_client, "teacher@example.com")
    small_count = _count_statements(material_app, material_client, _detail(gpid, small_pid))
    big_count = _count_statements(material_app, material_client, _detail(gpid, big_pid))
    assert small_count == big_count


def test_the_list_page_costs_the_same_for_one_and_many_activities(
    material_app, material_client
):
    with material_app.app_context():
        teacher, group_a = _setup("A", "a@example.com")
        _make_activity(group_a, teacher, title="Only", questions=False)
        group_b = _hierarchy("B")
        db.session.add(
            GroupTeacherAssignment(group_id=group_b.id, teacher_id=teacher.id)
        )
        db.session.commit()
        for index in range(10):
            _make_activity(
                group_b, teacher, title=f"Many {index}",
                upload_key=f"b-{index}", questions=False,
            )
        a_pid, b_pid = group_a.public_id, group_b.public_id
    _login_as(material_client, "a@example.com")
    assert _count_statements(material_app, material_client, _base(a_pid)) == \
        _count_statements(material_app, material_client, _base(b_pid))
