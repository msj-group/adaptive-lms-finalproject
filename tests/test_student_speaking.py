"""Student Speaking: visibility, the browser recording page, the single
final upload, the immutable receipt and authorized playback
(Phase 4 / M06).

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
REPEATABLE READ snapshot isolation, so the concurrency tests here are
**structural**: they assert the *requested* order and exercise the
post-lock rechecks by injecting a state change at an exact transaction
boundary. They are **not** a demonstration of real InnoDB blocking.

**No real browser, microphone, codec, audio-track or accessibility
verification is performed anywhere in this file.** The recorder
assertions are structural contracts about the rendered markup and about
the shipped JavaScript module's source -- what it references and what it
deliberately never references -- not a demonstration that recording
works in any particular browser.
"""

import io
import pathlib
import re
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import event

from app import create_app
from app.blueprints.student import speaking as speaking_mod
from app.extensions import db
from app.models import (
    AcademicStatus,
    Assignment,
    AssignmentStatus,
    Enrollment,
    EnrollmentStatus,
    FileAccessAction,
    FileAccessLog,
    SpeakingActivity,
    SpeakingSubmission,
    UploadedFile,
    UserRole,
    UserStatus,
)
from tests.file_fixtures import (
    executable_bytes,
    html_bytes,
    minimal_docx,
    minimal_mp3,
    minimal_mp4,
    minimal_pdf,
    minimal_png,
    minimal_wav,
    minimal_webm,
    not_a_real_format,
)
from tests.speaking_fixtures import (
    AFTER_DUE,
    BEFORE_OPEN,
    DUE,
    INSTRUCTIONS,
    NOW,
    OPENS,
    TITLE,
    Clock,
    assign_teacher,
    enroll,
    hidden_value,
    login_as,
    ordinary_assignment,
    serve_get,
    setup_group,
    speaking_activity,
    speaking_feedback,
    speaking_submission,
    stored_files,
    student_detail,
    upload_row,
    user,
)

RECORDER_JS = (
    pathlib.Path(__file__).resolve().parents[1]
    / "app" / "static" / "js" / "speaking_recorder.js"
)


def _at(*moments):
    """Patch the Student Speaking blueprint's clock."""
    return patch.object(speaking_mod, "utc_reference_now", Clock(*moments))


def _published(group, **kwargs):
    kwargs.setdefault("published", True)
    return speaking_activity(group, **kwargs)


def _record_url(gpid, spid):
    return student_detail(gpid, spid) + "/record"


def _submit_url(gpid, spid):
    return student_detail(gpid, spid) + "/submit"


def _receipt_url(gpid, spid):
    return student_detail(gpid, spid) + "/receipt"


def _payload(token, data=None, filename="speaking-recording.webm"):
    payload = {"speaking_submission": token}
    if filename is not None:
        payload["audio"] = (
            io.BytesIO(minimal_webm() if data is None else data),
            filename,
        )
    return payload


def _submit(client, gpid, spid, token=None, follow=False, **kwargs):
    if token is None:
        token = hidden_value(client, _record_url(gpid, spid), "speaking_submission")
    return client.post(
        _submit_url(gpid, spid),
        data=_payload(token, **kwargs),
        content_type="multipart/form-data",
        follow_redirects=follow,
    )


# ===========================================================================
# Role and account authorization
# ===========================================================================


def test_anonymous_is_redirected_to_login(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = _published(group)
        gpid, spid = group.public_id, activity.public_id
    for url in (
        "/student/speaking",
        student_detail(gpid, spid),
        _record_url(gpid, spid),
        _receipt_url(gpid, spid),
        student_detail(gpid, spid) + "/audio",
    ):
        resp = client.get(url)
        assert resp.status_code == 302 and "/auth/login" in resp.headers["Location"]


@pytest.mark.parametrize(
    "role",
    [UserRole.TEACHER.value, UserRole.ADMINISTRATOR.value, UserRole.RESEARCHER.value],
)
def test_non_student_roles_are_forbidden(app, client, role):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = _published(group)
        gpid, spid = group.public_id, activity.public_id
        user("other@example.com", role)
    login_as(client, "other@example.com")
    with _at(NOW):
        assert client.get("/student/speaking").status_code == 403
        assert client.get(student_detail(gpid, spid)).status_code == 403
        assert client.get(_record_url(gpid, spid)).status_code == 403


# ===========================================================================
# Visibility -- the WHERE clause is the authorization
# ===========================================================================


def _visible(app, client, moment=NOW):
    with app.app_context():
        activity = SpeakingActivity.query.one()
        group = db.session.get(Assignment, activity.assignment_id).group
        gpid, spid = group.public_id, activity.public_id
    with _at(moment):
        return client.get(student_detail(gpid, spid)).status_code


def test_an_open_published_activity_is_visible(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _published(group)
        enroll(group)
    login_as(client, "student@example.com")
    assert _visible(app, client) == 200


def test_a_draft_is_undisclosed(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        speaking_activity(group, published=False)
        enroll(group)
    login_as(client, "student@example.com")
    assert _visible(app, client) == 404


def test_a_scheduled_activity_is_undisclosed_before_it_opens(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _published(group)
        enroll(group)
    login_as(client, "student@example.com")
    assert _visible(app, client, moment=BEFORE_OPEN) == 404


def test_a_past_due_activity_stays_readable(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _published(group)
        enroll(group)
    login_as(client, "student@example.com")
    assert _visible(app, client, moment=AFTER_DUE) == 200


def test_it_becomes_visible_exactly_at_opens_at(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _published(group)
        enroll(group)
    login_as(client, "student@example.com")
    assert _visible(app, client, moment=OPENS - timedelta(seconds=1)) == 404
    assert _visible(app, client, moment=OPENS) == 200


def test_a_withdrawn_enrollment_loses_access(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _published(group)
        enroll(group, status=EnrollmentStatus.WITHDRAWN.value)
    login_as(client, "student@example.com")
    assert _visible(app, client) == 404


def test_a_suspended_account_loses_access(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _published(group)
        enroll(group, account_status=UserStatus.SUSPENDED.value)
    login_as(client, "student@example.com")
    # A suspended account cannot even authenticate.
    assert client.get("/student/speaking").status_code in (302, 403)


@pytest.mark.parametrize(
    "status_kwarg",
    ["term_status", "level_status", "course_status", "group_status"],
)
def test_an_archived_ancestor_hides_the_activity(app, client, status_kwarg):
    with app.app_context():
        _teacher, group = setup_group(**{status_kwarg: AcademicStatus.ARCHIVED.value})
        _published(group)
        enroll(group)
    login_as(client, "student@example.com")
    assert _visible(app, client) == 404


def test_another_groups_activity_is_a_404(app, client):
    with app.app_context():
        _teacher, group = setup_group("A")
        _other, other_group = setup_group("B", teacher_email="o@example.com")
        _assignment, activity = _published(other_group)
        enroll(group)
        enroll(other_group, "classmate@example.com")
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "student@example.com")
    with _at(NOW):
        assert client.get(student_detail(gpid, spid)).status_code == 404


def test_an_ordinary_assignment_public_id_is_a_404_on_every_speaking_route(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        ordinary = ordinary_assignment(group)
        enroll(group)
        gpid, apid = group.public_id, ordinary.public_id
    login_as(client, "student@example.com")
    with _at(NOW):
        for suffix in ("", "/record", "/receipt", "/audio", "/audio/download"):
            assert client.get(student_detail(gpid, apid) + suffix).status_code == 404
        assert client.post(_submit_url(gpid, apid), data={}).status_code == 404


def test_a_speaking_activity_is_a_404_on_the_ordinary_assignment_routes(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        assignment, _activity = _published(group)
        enroll(group)
        gpid, apid = group.public_id, assignment.public_id
    login_as(client, "student@example.com")
    base = f"/student/groups/{gpid}/assignments/{apid}"
    assert client.get(base).status_code == 404
    assert client.post(base + "/submit", data={}).status_code == 404


def test_the_student_assignment_list_and_dashboard_exclude_speaking(app, client):
    from app.services.assignment_queries import student_upcoming_deadlines

    with app.app_context():
        _teacher, group = setup_group()
        _published(group, title="Spoken task")
        ordinary_assignment(group, title="Written task")
        student = enroll(group)
        student_id = student.id
    login_as(client, "student@example.com")
    html = client.get("/student/assignments").get_data(as_text=True)
    assert "Written task" in html
    assert "Spoken task" not in html
    with app.app_context():
        titles = [row[0].title for row in student_upcoming_deadlines(student_id, NOW)]
        assert titles == ["Written task"]


# ===========================================================================
# The Student list
# ===========================================================================


def test_the_list_shows_the_students_own_submitted_state_only(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = _published(group, title="Mine")
        me = enroll(group, "me@example.com")
        classmate = enroll(group, "them@example.com")
        speaking_submission(activity, classmate)
        gpid = group.public_id
    login_as(client, "me@example.com")
    with _at(NOW):
        html = client.get("/student/speaking").get_data(as_text=True)
    assert "Mine" in html
    # A classmate's recording must not make MY row look submitted.
    assert "Not submitted" in html
    assert ">Submitted<" not in html


def test_the_list_is_bounded_and_costs_a_fixed_number_of_queries(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        for index in range(22):
            _published(
                group,
                title=f"Task {index:02d}",
                due_at=DUE + timedelta(days=index),
            )
        enroll(group)
        activities = SpeakingActivity.query.count()
        assert activities == 22
    login_as(client, "student@example.com")

    statements = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", record)
    try:
        with _at(NOW):
            html = client.get("/student/speaking").get_data(as_text=True)
    finally:
        with app.app_context():
            event.remove(db.engine, "before_cursor_execute", record)

    assert html.count("speaking/") >= 20
    assert "Next" in html
    listing = [s for s in statements if "FROM assignments" in s]
    assert any("LIMIT" in s for s in listing)
    assert not any("count(" in s.lower() for s in listing)
    # ONE page-level "which have I submitted" query -- never one per row.
    own = [s for s in statements if "speaking_submissions" in s]
    assert len(own) == 1


def test_a_page_past_the_end_falls_back_to_page_one(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _published(group, title="Only one")
        enroll(group)
    login_as(client, "student@example.com")
    with _at(NOW):
        html = client.get("/student/speaking?page=9").get_data(as_text=True)
    assert "Only one" in html
    assert "Previous" not in html


# ===========================================================================
# Detail and the recording page
# ===========================================================================


def test_the_detail_page_offers_the_recorder_while_open(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = _published(group)
        enroll(group)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "student@example.com")
    with _at(NOW):
        html = client.get(student_detail(gpid, spid)).get_data(as_text=True)
    assert _record_url(gpid, spid) in html
    assert "You can submit once" in html
    assert INSTRUCTIONS in html


def test_a_past_due_detail_page_offers_no_recorder(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = _published(group)
        enroll(group)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "student@example.com")
    with _at(AFTER_DUE):
        html = client.get(student_detail(gpid, spid)).get_data(as_text=True)
    assert _record_url(gpid, spid) not in html
    assert "you did not submit a recording" in html


def test_the_record_page_redirects_once_something_is_submitted(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = _published(group)
        student = enroll(group)
        speaking_submission(activity, student)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "student@example.com")
    with _at(NOW):
        resp = client.get(_record_url(gpid, spid))
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/receipt")


def test_the_record_page_redirects_after_the_deadline(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = _published(group)
        enroll(group)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "student@example.com")
    with _at(AFTER_DUE):
        resp = client.get(_record_url(gpid, spid), follow_redirects=True)
    assert "deadline for this speaking activity has passed" in resp.get_data(as_text=True)


def test_the_record_page_carries_the_whole_recorder_contract(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = _published(group)
        enroll(group)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "student@example.com")
    with _at(NOW):
        html = client.get(_record_url(gpid, spid)).get_data(as_text=True)

    # The five documented states are reachable, and the controls exist.
    for hook in (
        "data-speaking-recorder",
        "data-recorder-controls",
        "data-recorder-guidance",
        "data-recorder-status",
        "data-recorder-error",
        "data-recorder-unsupported",
        "data-recorder-start",
        "data-recorder-stop",
        "data-recorder-again",
        "data-recorder-preview",
        "data-speaking-form",
        "data-speaking-file",
        "data-speaking-fallback",
        "data-speaking-submit",
    ):
        assert hook in html, hook

    # Microphone guidance is present BEFORE any permission is requested.
    assert "ask for permission" in html and "microphone" in html
    assert "Nothing is recorded before you press that button" in html
    # Impossible actions start disabled.
    assert re.search(r"data-recorder-stop[^>]*disabled", html)
    assert re.search(r"data-recorder-again[^>]*disabled", html)
    # The preview never autoplays.
    assert "autoplay" not in html
    assert re.search(r"<audio[^>]*data-recorder-preview[^>]*controls", html)
    # The immutability warning is stated in as many words.
    assert "cannot be edited, replaced, or submitted again" in html
    # CSRF and the signed submission token both travel in the form.
    assert 'name="csrf_token"' in html
    assert 'name="speaking_submission"' in html
    assert 'enctype="multipart/form-data"' in html
    # The fallback file input is an ordinary audio one.
    assert 'accept="audio/*' in html
    assert 'type="file"' in html


def _recorder_code():
    """The module with its comments removed.

    The forbidden-API scan below must read what the module *does*, not
    what its documentation *says it never does* -- the docstring names
    every one of those APIs precisely to promise it does not call them.
    """
    source = RECORDER_JS.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"^\s*//.*$", "", source, flags=re.M)
    return source


def test_the_recorder_module_never_persists_or_phones_home():
    source = _recorder_code()
    for forbidden in (
        "localStorage", "sessionStorage", "indexedDB", "document.cookie",
        "fetch(", "XMLHttpRequest", "navigator.sendBeacon", "console.log",
        "console.error", "analytics", "autoplay",
    ):
        assert forbidden not in source, forbidden
    # It uses MediaRecorder, feature-detects everything it needs, stops
    # tracks and revokes superseded object URLs.
    source = RECORDER_JS.read_text(encoding="utf-8")
    for required in (
        "navigator.mediaDevices",
        "getUserMedia",
        "MediaRecorder",
        "isTypeSupported",
        "revokeObjectURL",
        "tracks[i].stop()",
        "DataTransfer",
    ):
        assert required in source, required
    # Permission is requested only from the Start handler.
    assert source.count("getUserMedia") == 2  # the feature test + the one call
    assert "startButton.addEventListener(\"click\", startRecording)" in source


def test_the_recorder_module_prefers_real_browser_audio_formats():
    source = RECORDER_JS.read_text(encoding="utf-8")
    for mime in ("audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/mpeg",
                 "audio/wav"):
        assert f'"{mime}"' in source, mime
    # The generated filename is server-safe and derived from the negotiated
    # type, never from anything the student typed.
    assert 'var BASE_FILENAME = "speaking-recording"' in source
    assert "BASE_FILENAME + \".\" + extension" in source


# ===========================================================================
# The one final submission
# ===========================================================================


def _setup_open_activity(app):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = _published(group)
        enroll(group)
        return group.public_id, activity.public_id


def test_a_first_submission_stores_the_recording_and_the_audit_row(
    material_app, material_client
):
    gpid, spid = _setup_open_activity(material_app)
    login_as(material_client, "student@example.com")
    with _at(NOW):
        resp = _submit(material_client, gpid, spid)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/receipt")

    with material_app.app_context():
        submission = SpeakingSubmission.query.one()
        upload = UploadedFile.query.one()
        assert submission.audio_file_id == upload.id
        assert submission.submitted_at == NOW
        assert submission.submitted_at.microsecond == 0
        assert upload.category == "audio"
        assert upload.content_type == "audio/webm"
        assert upload.extension == "webm"
        assert upload.uploaded_by_id == submission.student_id
        logs = FileAccessLog.query.filter_by(uploaded_file_id=upload.id).all()
        assert [row.action for row in logs] == [FileAccessAction.UPLOAD.value]
    assert len(stored_files(material_app)) == 1


@pytest.mark.parametrize(
    "filename,data,content_type",
    [
        ("speaking-recording.webm", minimal_webm(), "audio/webm"),
        ("speaking-recording.mp4", minimal_mp4(), "audio/mp4"),
        ("speaking-recording.wav", minimal_wav(), "audio/wav"),
        ("speaking-recording.mp3", minimal_mp3(), "audio/mpeg"),
    ],
)
def test_every_supported_format_is_accepted_and_stored_as_audio(
    material_app, material_client, filename, data, content_type
):
    gpid, spid = _setup_open_activity(material_app)
    login_as(material_client, "student@example.com")
    with _at(NOW):
        resp = _submit(material_client, gpid, spid, filename=filename, data=data)
    assert resp.status_code == 302
    with material_app.app_context():
        upload = UploadedFile.query.one()
        assert upload.category == "audio"
        assert upload.content_type == content_type


@pytest.mark.parametrize(
    "filename,data",
    [
        ("speaking-recording.webm", b""),
        ("speaking-recording.webm", not_a_real_format("webm")),
        ("speaking-recording.wav", minimal_webm()),
        ("speaking-recording.mp3", minimal_png()),
        ("speaking-recording.mp4", minimal_pdf()),
        ("speaking-recording.webm", executable_bytes()),
        ("speaking-recording.mp3", html_bytes()),
        ("photo.png", minimal_png()),
        ("notes.pdf", minimal_pdf()),
        ("paper.docx", minimal_docx()),
        ("clip.avi", minimal_webm()),
    ],
)
def test_a_refused_upload_writes_nothing_and_leaves_no_file(
    material_app, material_client, filename, data
):
    gpid, spid = _setup_open_activity(material_app)
    login_as(material_client, "student@example.com")
    with _at(NOW):
        resp = _submit(material_client, gpid, spid, filename=filename, data=data)
    assert resp.status_code == 200
    with material_app.app_context():
        assert SpeakingSubmission.query.count() == 0
        assert UploadedFile.query.count() == 0
        assert FileAccessLog.query.count() == 0
    assert stored_files(material_app) == []


def test_a_missing_file_part_is_refused(material_app, material_client):
    gpid, spid = _setup_open_activity(material_app)
    login_as(material_client, "student@example.com")
    with _at(NOW):
        resp = _submit(material_client, gpid, spid, filename=None)
    assert resp.status_code == 200
    assert "Record or choose an audio file first." in resp.get_data(as_text=True)
    with material_app.app_context():
        assert SpeakingSubmission.query.count() == 0


def test_an_oversized_recording_is_refused_and_cleaned_up(tmp_path):
    flask_app = create_app(
        "testing",
        MATERIAL_STORAGE_ROOT=str(tmp_path / "materials"),
        MATERIAL_MAX_AUDIO_BYTES="64",
    )
    test_client = flask_app.test_client()
    with flask_app.app_context():
        db.create_all()
        try:
            _teacher, group = setup_group()
            _assignment, activity = _published(group)
            enroll(group)
            gpid, spid = group.public_id, activity.public_id

            login_as(test_client, "student@example.com")
            with _at(NOW):
                resp = test_client.post(
                    _submit_url(gpid, spid),
                    data=_payload(
                        hidden_value(
                            test_client, _record_url(gpid, spid), "speaking_submission"
                        ),
                        data=minimal_wav() + b"\x00" * 512,
                        filename="speaking-recording.wav",
                    ),
                    content_type="multipart/form-data",
                )
            assert resp.status_code == 200
            assert "larger than the maximum allowed size" in resp.get_data(as_text=True)
            assert SpeakingSubmission.query.count() == 0
            assert UploadedFile.query.count() == 0
            assert stored_files(flask_app) == []
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def test_a_replayed_submission_returns_the_receipt_and_stores_nothing_more(
    material_app, material_client
):
    gpid, spid = _setup_open_activity(material_app)
    login_as(material_client, "student@example.com")
    with _at(NOW):
        token = hidden_value(
            material_client, _record_url(gpid, spid), "speaking_submission"
        )
        first = _submit(material_client, gpid, spid, token=token)
    with material_app.app_context():
        original = SpeakingSubmission.query.one()
        original_public_id = original.public_id
        original_upload = original.audio_file_id
    with _at(NOW + timedelta(minutes=1)):
        second = _submit(material_client, gpid, spid, token=token)

    assert first.headers["Location"] == second.headers["Location"]
    with material_app.app_context():
        assert SpeakingSubmission.query.count() == 1
        assert UploadedFile.query.count() == 1
        assert FileAccessLog.query.filter_by(
            action=FileAccessAction.UPLOAD.value
        ).count() == 1
        row = SpeakingSubmission.query.one()
        assert row.public_id == original_public_id
        assert row.audio_file_id == original_upload
        assert row.submitted_at == NOW
    assert len(stored_files(material_app)) == 1


def test_a_changed_payload_replay_also_returns_the_immutable_receipt(
    material_app, material_client
):
    gpid, spid = _setup_open_activity(material_app)
    login_as(material_client, "student@example.com")
    with _at(NOW):
        token = hidden_value(
            material_client, _record_url(gpid, spid), "speaking_submission"
        )
        _submit(material_client, gpid, spid, token=token)
    with material_app.app_context():
        original_upload = SpeakingSubmission.query.one().audio_file_id
    with _at(NOW + timedelta(minutes=1)):
        resp = _submit(
            material_client, gpid, spid, token=token,
            data=minimal_wav(), filename="speaking-recording.wav",
        )
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/receipt")
    with material_app.app_context():
        assert SpeakingSubmission.query.one().audio_file_id == original_upload
        assert UploadedFile.query.count() == 1
    assert len(stored_files(material_app)) == 1


def test_a_second_submission_with_a_fresh_page_is_still_refused(
    material_app, material_client
):
    gpid, spid = _setup_open_activity(material_app)
    login_as(material_client, "student@example.com")
    with _at(NOW):
        _submit(material_client, gpid, spid)
        # A fresh recording page is not even rendered any more.
        assert material_client.get(_record_url(gpid, spid)).status_code == 302
        resp = _submit(material_client, gpid, spid, token="anything")
    assert resp.status_code == 302
    with material_app.app_context():
        assert SpeakingSubmission.query.count() == 1


def test_a_concurrent_first_submission_resolves_to_the_winners_receipt(
    material_app, material_client
):
    """Structural: a competing request commits this Student's first
    recording between the pre-lock check and this request's INSERT, so the
    unique constraint fires and the recovery path returns the existing
    receipt -- and deletes only the loser's own file."""
    gpid, spid = _setup_open_activity(material_app)
    login_as(material_client, "student@example.com")
    with _at(NOW):
        token = hidden_value(
            material_client, _record_url(gpid, spid), "speaking_submission"
        )

    original = speaking_mod._acceptance_moment
    winner = {}

    def racing():
        moment = original()
        if not winner:
            from app.models import User

            student = User.query.filter_by(email="student@example.com").one()
            row = speaking_submission(SpeakingActivity.query.one(), student)
            winner["public_id"] = row.public_id
            winner["upload"] = row.audio_file_id
        return moment

    with patch.object(speaking_mod, "_acceptance_moment", racing), _at(NOW):
        resp = _submit(material_client, gpid, spid, token=token)

    assert resp.status_code == 302 and resp.headers["Location"].endswith("/receipt")
    with material_app.app_context():
        rows = SpeakingSubmission.query.all()
        assert len(rows) == 1
        assert rows[0].public_id == winner["public_id"]
        assert rows[0].audio_file_id == winner["upload"]
    # The loser's just-stored file is gone; the winner's row referenced a
    # fixture upload with no physical file, so nothing is left behind.
    assert stored_files(material_app) == []


def test_a_missing_or_forged_submission_token_is_refused(material_app, material_client):
    gpid, spid = _setup_open_activity(material_app)
    login_as(material_client, "student@example.com")
    for token in ("", "not-a-token", "eyJhIjoxfQ.invalid.signature"):
        with _at(NOW):
            resp = _submit(material_client, gpid, spid, token=token)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(f"/speaking/{spid}")
    with material_app.app_context():
        assert SpeakingSubmission.query.count() == 0
        assert UploadedFile.query.count() == 0
    assert stored_files(material_app) == []


def test_a_token_minted_for_another_student_is_refused(material_app, material_client):
    with material_app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = _published(group)
        enroll(group, "me@example.com")
        enroll(group, "them@example.com")
        gpid, spid = group.public_id, activity.public_id
    login_as(material_client, "them@example.com")
    with _at(NOW):
        stolen = hidden_value(
            material_client, _record_url(gpid, spid), "speaking_submission"
        )
    login_as(material_client, "me@example.com")
    with _at(NOW):
        resp = _submit(material_client, gpid, spid, token=stolen)
    assert resp.status_code == 302
    with material_app.app_context():
        assert SpeakingSubmission.query.count() == 0
    assert stored_files(material_app) == []


def test_a_teacher_rewrite_makes_an_open_recording_page_stale(
    material_app, material_client
):
    gpid, spid = _setup_open_activity(material_app)
    login_as(material_client, "student@example.com")
    with _at(NOW):
        token = hidden_value(
            material_client, _record_url(gpid, spid), "speaking_submission"
        )
    with material_app.app_context():
        assignment = Assignment.query.one()
        assignment.instructions = "Completely different task now."
        db.session.commit()
    with _at(NOW):
        resp = _submit(material_client, gpid, spid, token=token, follow=True)
    assert "was changed since this page was opened" in resp.get_data(as_text=True)
    with material_app.app_context():
        assert SpeakingSubmission.query.count() == 0
        assert UploadedFile.query.count() == 0
    assert stored_files(material_app) == []


def test_no_first_submission_is_accepted_at_or_after_the_deadline(
    material_app, material_client
):
    gpid, spid = _setup_open_activity(material_app)
    login_as(material_client, "student@example.com")
    with _at(NOW):
        token = hidden_value(
            material_client, _record_url(gpid, spid), "speaking_submission"
        )
    # Exactly AT the deadline: refused.
    with _at(DUE):
        resp = _submit(material_client, gpid, spid, token=token, follow=True)
    assert "deadline for this speaking activity has passed" in resp.get_data(as_text=True)
    with material_app.app_context():
        assert SpeakingSubmission.query.count() == 0
        assert UploadedFile.query.count() == 0
    assert stored_files(material_app) == []


def test_one_second_before_the_deadline_is_accepted(material_app, material_client):
    gpid, spid = _setup_open_activity(material_app)
    login_as(material_client, "student@example.com")
    moment = DUE - timedelta(seconds=1)
    with _at(moment):
        resp = _submit(material_client, gpid, spid)
    assert resp.status_code == 302
    with material_app.app_context():
        assert SpeakingSubmission.query.one().submitted_at == moment


def test_a_request_that_waits_past_the_deadline_is_refused(material_app, material_client):
    """The authoritative moment is read AFTER the locks: a request that
    arrived in time but waited behind another transaction until after the
    deadline must be refused, and its file cleaned up."""
    gpid, spid = _setup_open_activity(material_app)
    login_as(material_client, "student@example.com")
    # First call (the pre-lock preview and the page read) is in time; the
    # post-lock acceptance moment is past the deadline.
    with patch.object(
        speaking_mod, "utc_reference_now", Clock(NOW, NOW, AFTER_DUE)
    ):
        token = hidden_value(
            material_client, _record_url(gpid, spid), "speaking_submission"
        )
    with patch.object(speaking_mod, "utc_reference_now", Clock(NOW, AFTER_DUE)):
        resp = _submit(material_client, gpid, spid, token=token, follow=True)
    assert "deadline for this speaking activity has passed" in resp.get_data(as_text=True)
    with material_app.app_context():
        assert SpeakingSubmission.query.count() == 0
    assert stored_files(material_app) == []


def test_access_lost_after_the_locks_is_a_404_with_no_orphan(
    material_app, material_client
):
    """Structural: the Enrollment is withdrawn between the pre-lock preview
    and the locked re-check. The request 404s, nothing is written, and the
    just-stored file is removed."""
    gpid, spid = _setup_open_activity(material_app)
    login_as(material_client, "student@example.com")
    with _at(NOW):
        token = hidden_value(
            material_client, _record_url(gpid, spid), "speaking_submission"
        )

    original = speaking_mod._lock_submission_chain
    fired = {}

    def racing(*args, **kwargs):
        result = original(*args, **kwargs)
        if not fired:
            fired["done"] = True
            Enrollment.query.one().status = EnrollmentStatus.WITHDRAWN.value
            db.session.commit()
        return result

    with patch.object(speaking_mod, "_lock_submission_chain", racing), _at(NOW):
        resp = _submit(material_client, gpid, spid, token=token)
    assert resp.status_code == 404
    with material_app.app_context():
        assert SpeakingSubmission.query.count() == 0
        assert UploadedFile.query.count() == 0
    assert stored_files(material_app) == []


def test_the_upload_is_streamed_and_validated_before_any_lock_is_taken(
    material_app, material_client
):
    """Part M12's rule, applied to M06: no database lock may be held while
    a recording is streamed. Structural -- it asserts the *order* the route
    performs, which is what the rule constrains."""
    gpid, spid = _setup_open_activity(material_app)
    login_as(material_client, "student@example.com")
    order = []

    store = speaking_mod.store_speaking_audio
    lock = speaking_mod._lock_submission_chain

    def traced_store(*args, **kwargs):
        order.append("store")
        return store(*args, **kwargs)

    def traced_lock(*args, **kwargs):
        order.append("lock")
        return lock(*args, **kwargs)

    with patch.object(speaking_mod, "store_speaking_audio", traced_store), patch.object(
        speaking_mod, "_lock_submission_chain", traced_lock
    ), _at(NOW):
        resp = _submit(material_client, gpid, spid)
    assert resp.status_code == 302
    assert order == ["store", "lock"]


def test_a_forged_ownership_or_storage_field_has_nowhere_to_land(
    material_app, material_client
):
    with material_app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = _published(group)
        me = enroll(group, "me@example.com")
        classmate = enroll(group, "them@example.com")
        gpid, spid = group.public_id, activity.public_id
        classmate_id = classmate.id
    login_as(material_client, "me@example.com")
    with _at(NOW):
        token = hidden_value(
            material_client, _record_url(gpid, spid), "speaking_submission"
        )
        material_client.post(
            _submit_url(gpid, spid),
            data={
                "speaking_submission": token,
                "student_id": str(classmate_id),
                "submitted_at": "2020-01-01T00:00:00",
                "public_id": "forged",
                "creation_nonce": "forged",
                "storage_key": "forged",
                "category": "video",
                "content_type": "video/webm",
                "audio": (io.BytesIO(minimal_webm()), "speaking-recording.webm"),
            },
            content_type="multipart/form-data",
        )
    with material_app.app_context():
        row = SpeakingSubmission.query.one()
        upload = UploadedFile.query.one()
        assert row.student_id != classmate_id
        assert row.submitted_at == NOW
        assert row.public_id != "forged"
        assert row.creation_nonce != "forged"
        assert upload.storage_key != "forged"
        assert upload.category == "audio"
        assert upload.content_type == "audio/webm"


def test_submission_requires_csrf_when_it_is_enabled(tmp_path):
    flask_app = create_app("testing", MATERIAL_STORAGE_ROOT=str(tmp_path / "materials"))
    flask_app.config["WTF_CSRF_ENABLED"] = True
    test_client = flask_app.test_client()
    with flask_app.app_context():
        db.create_all()
        try:
            _teacher, group = setup_group()
            _assignment, activity = _published(group)
            enroll(group)
            gpid, spid = group.public_id, activity.public_id

            login_page = test_client.get("/auth/login").get_data(as_text=True)
            token = re.search(
                r'name="csrf_token"[^>]*value="([^"]+)"', login_page
            ).group(1)
            test_client.post(
                "/auth/login",
                data={
                    "email": "student@example.com",
                    "password": "Sup3rSecret!123",
                    "csrf_token": token,
                },
            )
            with _at(NOW):
                resp = test_client.post(
                    _submit_url(gpid, spid),
                    data={
                        "speaking_submission": "x",
                        "audio": (io.BytesIO(minimal_webm()), "r.webm"),
                    },
                    content_type="multipart/form-data",
                )
            assert resp.status_code == 400
            assert SpeakingSubmission.query.count() == 0
            assert UploadedFile.query.count() == 0
            assert stored_files(flask_app) == []
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


# ===========================================================================
# The receipt and authorized playback
# ===========================================================================


def test_the_receipt_shows_the_recording_and_the_latest_feedback(
    material_app, material_client
):
    gpid, spid = _setup_open_activity(material_app)
    login_as(material_client, "student@example.com")
    with _at(NOW):
        _submit(material_client, gpid, spid)
    with material_app.app_context():
        teacher = user("reviewer@example.com", UserRole.TEACHER.value, name="Ms Reviewer")
        assign_teacher_row = None
        del assign_teacher_row
        speaking_feedback(
            SpeakingSubmission.query.one(), teacher, text="Very clear, well paced."
        )
        storage_key = UploadedFile.query.one().storage_key
        digest = UploadedFile.query.one().sha256

    with _at(NOW):
        html = material_client.get(_receipt_url(gpid, spid)).get_data(as_text=True)
    assert "Submitted</strong>" in html
    assert "cannot be edited, replaced, or submitted again" in html
    assert "Very clear, well paced." in html
    assert "Ms Reviewer" in html
    assert "not a grade or a pass mark" in html
    assert student_detail(gpid, spid) + "/audio" in html
    # Nothing about the physical file is disclosed.
    assert storage_key not in html and digest not in html


def test_the_receipt_redirects_when_nothing_was_submitted(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = _published(group)
        enroll(group)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "student@example.com")
    with _at(NOW):
        resp = client.get(_receipt_url(gpid, spid))
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/speaking/{spid}")


def test_a_student_can_play_and_download_their_own_recording(
    material_app, material_client
):
    gpid, spid = _setup_open_activity(material_app)
    login_as(material_client, "student@example.com")
    with _at(NOW):
        _submit(material_client, gpid, spid)

    with _at(NOW):
        inline = serve_get(material_client, student_detail(gpid, spid) + "/audio")
        download = serve_get(
            material_client, student_detail(gpid, spid) + "/audio/download"
        )
    assert inline.status_code == 200
    assert inline.headers["Content-Type"].startswith("audio/webm")
    assert inline.headers["Cache-Control"] == "private, no-store, max-age=0"
    assert inline.headers["X-Content-Type-Options"] == "nosniff"
    assert "attachment" not in inline.headers.get("Content-Disposition", "")
    assert download.status_code == 200
    assert "attachment" in download.headers["Content-Disposition"]

    with material_app.app_context():
        actions = [row.action for row in FileAccessLog.query.all()]
        assert actions.count(FileAccessAction.UPLOAD.value) == 1
        assert FileAccessAction.INLINE.value in actions
        assert FileAccessAction.DOWNLOAD.value in actions


def test_a_classmate_cannot_reach_another_students_recording(
    material_app, material_client
):
    with material_app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = _published(group)
        enroll(group, "me@example.com")
        enroll(group, "them@example.com")
        gpid, spid = group.public_id, activity.public_id
    login_as(material_client, "me@example.com")
    with _at(NOW):
        _submit(material_client, gpid, spid)
    login_as(material_client, "them@example.com")
    with _at(NOW):
        assert serve_get(
            material_client, student_detail(gpid, spid) + "/audio"
        ).status_code == 404
        resp = material_client.get(_receipt_url(gpid, spid))
    assert resp.status_code == 302  # nothing of their own to show


def test_a_student_of_another_group_gets_the_same_404(material_app, material_client):
    with material_app.app_context():
        _teacher, group = setup_group("A")
        _other, other_group = setup_group("B", teacher_email="o@example.com")
        _assignment, activity = _published(group)
        enroll(group, "me@example.com")
        enroll(other_group, "outsider@example.com")
        gpid, spid = group.public_id, activity.public_id
    login_as(material_client, "me@example.com")
    with _at(NOW):
        _submit(material_client, gpid, spid)
    login_as(material_client, "outsider@example.com")
    with _at(NOW):
        assert material_client.get(student_detail(gpid, spid)).status_code == 404
        assert serve_get(
            material_client, student_detail(gpid, spid) + "/audio"
        ).status_code == 404


def test_playback_stops_when_the_enrollment_is_withdrawn(material_app, material_client):
    gpid, spid = _setup_open_activity(material_app)
    login_as(material_client, "student@example.com")
    with _at(NOW):
        _submit(material_client, gpid, spid)
    with material_app.app_context():
        Enrollment.query.one().status = EnrollmentStatus.WITHDRAWN.value
        db.session.commit()
    with _at(NOW):
        assert serve_get(
            material_client, student_detail(gpid, spid) + "/audio"
        ).status_code == 404
    with material_app.app_context():
        # The stored row itself is untouched.
        assert SpeakingSubmission.query.count() == 1


def test_a_corrupt_audio_association_fails_closed(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = _published(group)
        student = enroll(group)
        upload = upload_row(
            student.id, category="document", extension="pdf",
            content_type="application/pdf",
        )
        speaking_submission(activity, student, upload=upload)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "student@example.com")
    with _at(NOW):
        assert client.get(_receipt_url(gpid, spid)).status_code == 404
        assert serve_get(client, student_detail(gpid, spid) + "/audio").status_code == 404


def test_a_missing_physical_file_is_a_safe_404(material_app, material_client):
    with material_app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = _published(group)
        student = enroll(group)
        speaking_submission(activity, student)  # key names no real file
        gpid, spid = group.public_id, activity.public_id
    login_as(material_client, "student@example.com")
    with _at(NOW):
        resp = serve_get(material_client, student_detail(gpid, spid) + "/audio")
    assert resp.status_code == 404
    assert "storage" not in resp.get_data(as_text=True).lower()


def test_the_receipt_survives_the_deadline(material_app, material_client):
    gpid, spid = _setup_open_activity(material_app)
    login_as(material_client, "student@example.com")
    with _at(NOW):
        _submit(material_client, gpid, spid)
    with _at(AFTER_DUE):
        assert material_client.get(_receipt_url(gpid, spid)).status_code == 200
        assert serve_get(
            material_client, student_detail(gpid, spid) + "/audio"
        ).status_code == 200


# ===========================================================================
# Response behaviour
# ===========================================================================


@pytest.mark.parametrize("suffix", ["", "/record"])
def test_content_bearing_pages_are_private_and_vary_on_cookie(app, client, suffix):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = _published(group)
        enroll(group)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "student@example.com")
    with _at(NOW):
        resp = client.get(student_detail(gpid, spid) + suffix)
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in resp.headers.get("Vary", "")


def test_the_student_list_is_private_and_varies_on_cookie(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _published(group)
        enroll(group)
    login_as(client, "student@example.com")
    with _at(NOW):
        resp = client.get("/student/speaking")
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in resp.headers.get("Vary", "")


def test_authored_fields_are_escaped(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = _published(
            group,
            title="<script>alert('t')</script>",
            instructions="<img src=x onerror=alert('i')>",
        )
        enroll(group)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "student@example.com")
    with _at(NOW):
        html = client.get(student_detail(gpid, spid)).get_data(as_text=True)
    assert "<script>alert('t')</script>" not in html
    assert "&lt;script&gt;" in html
    assert "<img src=x onerror" not in html


def test_teacher_feedback_is_escaped_on_the_receipt(material_app, material_client):
    gpid, spid = _setup_open_activity(material_app)
    login_as(material_client, "student@example.com")
    with _at(NOW):
        _submit(material_client, gpid, spid)
    with material_app.app_context():
        teacher = user("reviewer@example.com", UserRole.TEACHER.value)
        speaking_feedback(
            SpeakingSubmission.query.one(), teacher,
            text="<script>alert('f')</script>",
        )
    with _at(NOW):
        html = material_client.get(_receipt_url(gpid, spid)).get_data(as_text=True)
    assert "<script>alert('f')</script>" not in html
    assert "&lt;script&gt;" in html


@pytest.mark.parametrize(
    "method,suffix",
    [
        ("post", ""),
        ("post", "/record"),
        ("post", "/receipt"),
        ("get", "/submit"),
        ("delete", "/receipt"),
        ("put", "/submit"),
    ],
)
def test_unsupported_methods_fail_safely(app, client, method, suffix):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = _published(group)
        enroll(group)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "student@example.com")
    with _at(NOW):
        resp = getattr(client, method)(student_detail(gpid, spid) + suffix)
    assert resp.status_code == 405


def test_there_is_no_student_delete_replace_or_resubmit_endpoint(app):
    student_rules = [
        str(rule)
        for rule in app.url_map.iter_rules()
        if "/student/" in str(rule) and "speaking" in str(rule)
    ]
    for forbidden in ("delete", "replace", "resubmit", "edit", "draft", "grade"):
        assert not any(forbidden in rule for rule in student_rules), forbidden
    # Exactly one POST endpoint on the whole Student Speaking surface.
    posts = [
        str(rule)
        for rule in app.url_map.iter_rules()
        if "speaking" in str(rule) and "/student/" in str(rule) and "POST" in rule.methods
    ]
    assert posts == [f"/student/groups/<group_public_id>/speaking/<speaking_public_id>/submit"]
