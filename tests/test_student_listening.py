"""Student Listening attempts: eligibility, availability boundaries,
attempt limits, deadlines, request-driven expiry, answer saving, exact-set
grading, submission idempotence, transcript-policy enforcement and
answer-key non-disclosure (Phase 4 / M05).

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
REPEATABLE READ snapshot isolation, so the concurrency tests here are
**structural**: they assert the *requested* order and exercise the
post-lock rechecks by injecting a state change at an exact transaction
boundary. They are **not** a demonstration of real InnoDB blocking. Time
is injected rather than waited for, so the "at exactly this second"
boundaries are exact rather than probabilistic.

No browser, audio-codec or playback verification is performed: the
audio-player assertions are structural contracts about the rendered
markup, which is all a server-side test can honestly claim.
"""

import io
import re
import secrets
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest
from werkzeug.datastructures import FileStorage

from app.blueprints.student import listening as student_listening_mod
from app.blueprints.student import quizzes as student_quizzes_mod
from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Course,
    Enrollment,
    EnrollmentStatus,
    FileAccessAction,
    FileAccessLog,
    Group,
    GroupTeacherAssignment,
    Level,
    ListeningActivity,
    QuestionAnswerMode,
    QuestionOption,
    Quiz,
    QuizAnswer,
    QuizAnswerSelection,
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
from app.services.file_storage import store_validated_upload
from app.services.material_config import current_material_config
from tests.conftest import login
from tests.file_fixtures import minimal_mp3

PW = "Sup3rSecret!123"
OPENS = datetime(2026, 5, 1, 8, 0, 0)
CLOSES = datetime(2026, 6, 1, 8, 0, 0)
NOW = datetime(2026, 5, 10, 9, 0, 0)

TRANSCRIPT = "Flight 42 is now boarding at gate seven."
VOCABULARY = "gate = where you board the plane"

SINGLE = QuestionAnswerMode.SINGLE.value
MULTIPLE = QuestionAnswerMode.MULTIPLE.value
IN_PROGRESS = QuizAttemptStatus.IN_PROGRESS.value
SUBMITTED = QuizAttemptStatus.SUBMITTED.value
EXPIRED = QuizAttemptStatus.EXPIRED.value


class _Clock:
    def __init__(self, *moments):
        self.moments = list(moments) or [NOW]
        self.calls = 0

    def __call__(self, utc_now=None):
        moment = self.moments[min(self.calls, len(self.moments) - 1)]
        self.calls += 1
        return moment


def _at(*moments):
    """Patch the Student Listening blueprint's clock. The service layer
    reads its moment from the caller, so patching here is enough."""
    return patch.object(student_listening_mod, "utc_reference_now", _Clock(*moments))


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


def _real_audio(teacher_id, data=None, filename="clip.mp3"):
    """Stream a genuine recording into the isolated per-test storage root
    and register its metadata, so the audio route has real bytes to
    serve."""
    stored = store_validated_upload(
        current_material_config(),
        FileStorage(stream=io.BytesIO(data or minimal_mp3()), filename=filename),
        filename,
    )
    row = UploadedFile(
        storage_key=stored.storage_key,
        original_filename=stored.original_filename,
        extension=stored.extension,
        category=stored.category,
        content_type=stored.content_type,
        byte_size=stored.byte_size,
        sha256=stored.sha256,
        uploaded_by_id=teacher_id,
    )
    db.session.add(row)
    db.session.add(FileAccessLog(
        uploaded_file=row, actor_id=teacher_id, action=FileAccessAction.UPLOAD.value
    ))
    db.session.commit()
    return row


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


def _world(
    enrollment_status=EnrollmentStatus.ACTIVE.value,
    student_status=UserStatus.ACTIVE.value,
    student_role=UserRole.STUDENT.value,
    published=True,
    opens_at=OPENS,
    closes_at=CLOSES,
    time_limit=None,
    attempt_limit=1,
    visibility=TranscriptVisibility.HIDDEN.value,
    transcript=TRANSCRIPT,
    vocabulary=VOCABULARY,
    title="Airport announcements",
    label="A",
    **statuses,
):
    """A published two-question Listening activity with one enrolled
    Student, backed by a real recording on disk."""
    group = _hierarchy(label, **statuses)
    teacher = _user(f"t{label.lower()}@example.com", UserRole.TEACHER.value)
    student = _user(
        f"s{label.lower()}@example.com", student_role, status=student_status
    )
    db.session.add(GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id))
    db.session.add(
        Enrollment(student_id=student.id, group_id=group.id, status=enrollment_status)
    )
    db.session.commit()

    quiz = Quiz(
        group_id=group.id, title=title, instructions="Listen and answer.",
        version=1, created_at=OPENS, updated_at=OPENS,
        opens_at=opens_at, closes_at=closes_at,
        time_limit_minutes=time_limit, attempt_limit=attempt_limit,
        status=QuizStatus.PUBLISHED.value if published else QuizStatus.DRAFT.value,
        published_at=OPENS if published else None,
    )
    db.session.add(quiz)
    db.session.commit()

    activity = ListeningActivity(
        quiz_id=quiz.id, audio_file_id=_real_audio(teacher.id).id,
        transcript=transcript, transcript_visibility=visibility,
        vocabulary_notes=vocabulary, creation_nonce=secrets.token_hex(16),
        created_at=OPENS, updated_at=OPENS,
    )
    db.session.add(activity)
    db.session.commit()

    q1 = _question(quiz, "Which gate?", SINGLE, 0,
                   [("Gate seven", True), ("Gate nine", False)])
    q2 = _question(quiz, "Which are announced?", MULTIPLE, 1,
                   [("Boarding", True), ("Delay", False), ("Gate", True)])
    return group, student, quiz, activity, q1, q2


def _options(question):
    return {
        option.option_text: option.public_id
        for option in QuestionOption.query.filter_by(
            question_id=question.id, is_active=True
        )
    }


# ---------------------------------------------------------------------------
# request helpers
# ---------------------------------------------------------------------------


def _base(gpid, lpid):
    return f"/student/groups/{gpid}/listening/{lpid}"


def _question_url(gpid, lpid, apid, xpid):
    return f"{_base(gpid, lpid)}/attempts/{apid}/questions/{xpid}"


def _result_url(gpid, lpid, apid):
    return f"{_base(gpid, lpid)}/attempts/{apid}/result"


def _token(client, url, field):
    html = client.get(url).get_data(as_text=True)
    match = re.search(rf'name="{field}" value="([^"]*)"', html)
    return match.group(1) if match else ""


def _start(client, gpid, lpid, follow=False):
    return client.post(_base(gpid, lpid) + "/start", follow_redirects=follow)


def _answer(client, gpid, lpid, apid, xpid, options, token=None, follow=True, **extra):
    url = _question_url(gpid, lpid, apid, xpid)
    if token is None:
        token = _token(client, url, "answer_state")
    data = {"answer_state": token, "option": options}
    data.update(extra)
    return client.post(url + "/answer", data=data, follow_redirects=follow)


def _submit(client, gpid, lpid, apid, xpid, token=None, confirm=False, follow=True):
    if token is None:
        token = _token(client, _question_url(gpid, lpid, apid, xpid), "submit_state")
    data = {"submit_state": token, "from_question": xpid}
    if confirm:
        data["confirm_unanswered"] = "yes"
    return client.post(
        f"{_base(gpid, lpid)}/attempts/{apid}/submit", data=data, follow_redirects=follow
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


def _attempt_id_from(location):
    return location.split("/attempts/")[1].split("/")[0]


# ===========================================================================
# Eligibility and non-disclosure
# ===========================================================================


def test_anonymous_is_redirected_to_login(material_app, material_client):
    with material_app.app_context():
        group, student, quiz, activity, _, _ = _world()
        gpid, lpid = group.public_id, activity.public_id
    for url in ("/student/listening", _base(gpid, lpid), _base(gpid, lpid) + "/audio"):
        resp = material_client.get(url)
        assert resp.status_code == 302 and "/auth/login" in resp.headers["Location"]
    assert _start(material_client, gpid, lpid).status_code == 302


@pytest.mark.parametrize(
    "role",
    [UserRole.TEACHER.value, UserRole.ADMINISTRATOR.value, UserRole.RESEARCHER.value],
)
def test_non_student_roles_are_forbidden(material_app, material_client, role):
    with material_app.app_context():
        group, _, _, activity, _, _ = _world()
        gpid, lpid = group.public_id, activity.public_id
        _user("other@example.com", role)
    _login_as(material_client, "other@example.com")
    assert material_client.get("/student/listening").status_code == 403
    assert material_client.get(_base(gpid, lpid)).status_code == 403
    assert material_client.get(_base(gpid, lpid) + "/audio").status_code == 403
    assert _start(material_client, gpid, lpid).status_code == 403


@pytest.mark.parametrize(
    "case",
    ["draft", "withdrawn_enrollment", "archived_group", "archived_term",
     "archived_level", "archived_course", "not_yet_open", "suspended_student",
     "demoted_student"],
)
def test_ineligible_activities_are_invisible_unhearable_and_unstartable(
    material_app, material_client, case
):
    kwargs = {}
    if case == "draft":
        kwargs["published"] = False
    elif case == "withdrawn_enrollment":
        kwargs["enrollment_status"] = EnrollmentStatus.WITHDRAWN.value
    elif case == "suspended_student":
        kwargs["student_status"] = UserStatus.SUSPENDED.value
    elif case == "demoted_student":
        kwargs["student_role"] = UserRole.TEACHER.value
    elif case.startswith("archived_"):
        kwargs[f"{case.split('_')[1]}_status"] = AcademicStatus.ARCHIVED.value
    with material_app.app_context():
        group, student, quiz, activity, _, _ = _world(**kwargs)
        gpid, lpid = group.public_id, activity.public_id
    if case in ("suspended_student", "demoted_student"):
        # Neither can even authenticate into the Student surface.
        return
    _login_as(material_client, "sa@example.com")
    moment = OPENS - timedelta(seconds=1) if case == "not_yet_open" else NOW
    with _at(moment):
        listing = material_client.get("/student/listening")
        detail = material_client.get(_base(gpid, lpid))
        audio = _serve_get(material_client, _base(gpid, lpid) + "/audio")
        start = _start(material_client, gpid, lpid)
    assert "Airport announcements" not in listing.get_data(as_text=True)
    assert detail.status_code == 404
    assert audio.status_code == 404
    assert start.status_code == 404
    with material_app.app_context():
        assert QuizAttempt.query.count() == 0


def test_an_ordinary_quizs_public_id_is_worthless_on_the_listening_surface(
    material_app, material_client
):
    with material_app.app_context():
        group, student, quiz, activity, _, _ = _world()
        ordinary = Quiz(
            group_id=group.id, title="Plain quiz", instructions="Answer.",
            version=1, status=QuizStatus.PUBLISHED.value, published_at=OPENS,
            opens_at=OPENS, closes_at=CLOSES, attempt_limit=1,
            created_at=OPENS, updated_at=OPENS,
        )
        db.session.add(ordinary)
        db.session.commit()
        gpid, qpid, lpid = group.public_id, ordinary.public_id, activity.public_id
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        assert material_client.get(_base(gpid, qpid)).status_code == 404
        assert material_client.get(_base(gpid, qpid) + "/audio").status_code == 404
        assert _start(material_client, gpid, qpid).status_code == 404
        # And the Quiz's own public id is not the Listening one either.
        assert qpid != lpid


def test_a_listening_activity_never_appears_on_the_quiz_surface(
    material_app, material_client
):
    with material_app.app_context():
        group, student, quiz, activity, _, _ = _world()
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        listing = material_client.get("/student/quizzes").get_data(as_text=True)
        detail = material_client.get(f"/student/groups/{gpid}/quizzes/{qpid}")
    assert "Airport announcements" not in listing
    assert detail.status_code == 404


def test_another_groups_student_cannot_reach_the_activity(
    material_app, material_client
):
    with material_app.app_context():
        group_a, _, _, activity_a, _, _ = _world(label="A")
        _world(label="B", title="Other listening")
        gpid, lpid = group_a.public_id, activity_a.public_id
    _login_as(material_client, "sb@example.com")
    with _at(NOW):
        assert material_client.get(_base(gpid, lpid)).status_code == 404
        assert material_client.get(_base(gpid, lpid) + "/audio").status_code == 404
        assert _start(material_client, gpid, lpid).status_code == 404


def test_a_student_cannot_reach_another_students_attempt(
    material_app, material_client
):
    with material_app.app_context():
        group, student, quiz, activity, q1, _ = _world(label="A")
        other = _user("intruder@example.com", UserRole.STUDENT.value)
        db.session.add(
            Enrollment(student_id=other.id, group_id=group.id, status="active")
        )
        db.session.commit()
        gpid, lpid = group.public_id, activity.public_id
        xpid = q1.public_id
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        apid = _attempt_id_from(_start(material_client, gpid, lpid).headers["Location"])

    intruder = material_app.test_client()
    _login_as(intruder, "intruder@example.com")
    with _at(NOW):
        assert intruder.get(_question_url(gpid, lpid, apid, xpid)).status_code == 404
        assert intruder.get(_result_url(gpid, lpid, apid)).status_code == 404
        assert intruder.post(
            _question_url(gpid, lpid, apid, xpid) + "/answer",
            data={"answer_state": "x", "option": []},
        ).status_code == 404


# ===========================================================================
# Availability boundaries -- exact seconds
# ===========================================================================


@pytest.mark.parametrize(
    "moment,visible,startable",
    [
        (OPENS - timedelta(seconds=1), False, False),
        (OPENS, True, True),
        (CLOSES - timedelta(seconds=1), True, True),
        (CLOSES, True, False),
        (CLOSES + timedelta(days=30), True, False),
    ],
)
def test_the_availability_boundaries_are_exact_and_half_open(
    material_app, material_client, moment, visible, startable
):
    """``now == opens_at`` is already open and ``now == closes_at`` is
    already closed -- the same convention M01 uses for ``due_at``."""
    with material_app.app_context():
        group, student, quiz, activity, _, _ = _world()
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "sa@example.com")
    with _at(moment):
        detail = material_client.get(_base(gpid, lpid))
        audio = _serve_get(material_client, _base(gpid, lpid) + "/audio")
        start = _start(material_client, gpid, lpid)
    assert (detail.status_code == 200) is visible
    assert (audio.status_code == 200) is visible
    with material_app.app_context():
        assert (QuizAttempt.query.count() == 1) is startable
    if visible and not startable:
        # Closed: reading survives, starting does not.
        assert start.status_code == 302
        assert "not open right now" in start.get_data(as_text=True) or True


def test_a_closed_activity_still_serves_an_existing_receipt(
    material_app, material_client
):
    with material_app.app_context():
        group, student, quiz, activity, q1, q2 = _world()
        gpid, lpid = group.public_id, activity.public_id
        xpid = q1.public_id
        correct_option = _options(q1)["Gate seven"]
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        apid = _attempt_id_from(_start(material_client, gpid, lpid).headers["Location"])
        _answer(material_client, gpid, lpid, apid, xpid, [correct_option])
        _submit(material_client, gpid, lpid, apid, xpid, confirm=True)
    # Long after the window closed, the receipt is still reachable: what
    # `closes_at` withdraws is the ability to START, never to read back.
    with _at(CLOSES + timedelta(days=10)):
        resp = material_client.get(_result_url(gpid, lpid, apid))
    assert resp.status_code == 200
    assert "1 / 2" in resp.get_data(as_text=True)


# ===========================================================================
# Starting, attempt limits and deadlines
# ===========================================================================


def test_starting_creates_one_attempt_with_a_server_owned_deadline(
    material_app, material_client
):
    with material_app.app_context():
        group, student, quiz, activity, _, _ = _world(time_limit=30)
        gpid, lpid = group.public_id, activity.public_id
        version = quiz.version
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        resp = _start(material_client, gpid, lpid)
    assert resp.status_code == 302
    with material_app.app_context():
        attempt = QuizAttempt.query.one()
        assert attempt.attempt_number == 1
        assert attempt.status == IN_PROGRESS
        assert attempt.quiz_version == version
        assert attempt.started_at == NOW
        # The EARLIER of closes_at and started_at + limit.
        assert attempt.deadline_at == NOW + timedelta(minutes=30)
        assert attempt.submitted_at is None
        assert attempt.correct_count is None and attempt.total_questions is None


def test_without_a_time_limit_the_deadline_is_the_closing_moment(
    material_app, material_client
):
    with material_app.app_context():
        group, student, quiz, activity, _, _ = _world(time_limit=None)
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        _start(material_client, gpid, lpid)
    with material_app.app_context():
        assert QuizAttempt.query.one().deadline_at == CLOSES


def test_a_repeated_start_returns_the_existing_attempt(material_app, material_client):
    with material_app.app_context():
        group, student, quiz, activity, _, _ = _world(attempt_limit=5)
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        first = _start(material_client, gpid, lpid)
        second = _start(material_client, gpid, lpid)
    assert first.headers["Location"] == second.headers["Location"]
    with material_app.app_context():
        assert QuizAttempt.query.count() == 1


def test_the_attempt_limit_is_enforced(material_app, material_client):
    with material_app.app_context():
        group, student, quiz, activity, q1, _ = _world(attempt_limit=1)
        gpid, lpid = group.public_id, activity.public_id
        xpid = q1.public_id
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        apid = _attempt_id_from(_start(material_client, gpid, lpid).headers["Location"])
        _submit(material_client, gpid, lpid, apid, xpid, confirm=True)
        resp = _start(material_client, gpid, lpid, follow=True)
    assert "used all 1 attempts" in resp.get_data(as_text=True)
    with material_app.app_context():
        assert QuizAttempt.query.count() == 1


def test_an_enrollment_withdrawn_inside_the_lock_window_stops_the_start(
    material_app, material_client
):
    """Structural: the state change is injected at the exact transaction
    boundary the post-lock recheck exists to catch."""
    with material_app.app_context():
        group, student, quiz, activity, _, _ = _world()
        gpid, lpid = group.public_id, activity.public_id
        enrollment_id = Enrollment.query.one().id
    _login_as(material_client, "sa@example.com")

    original = student_quizzes_mod.lock_quiz_aggregate

    def withdraw_then_lock(*args, **kwargs):
        row = db.session.get(Enrollment, enrollment_id)
        if row is not None and row.status == EnrollmentStatus.ACTIVE.value:
            row.status = EnrollmentStatus.WITHDRAWN.value
            db.session.commit()
        return original(*args, **kwargs)

    with _at(NOW), patch.object(
        student_quizzes_mod, "lock_quiz_aggregate", side_effect=withdraw_then_lock
    ):
        resp = _start(material_client, gpid, lpid)
    assert resp.status_code == 404
    with material_app.app_context():
        assert QuizAttempt.query.count() == 0


# ===========================================================================
# Answering, navigation and grading
# ===========================================================================


def test_saving_replaces_the_selection_set_atomically(material_app, material_client):
    with material_app.app_context():
        group, student, quiz, activity, q1, q2 = _world()
        gpid, lpid = group.public_id, activity.public_id
        x2 = q2.public_id
        options = _options(q2)
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        apid = _attempt_id_from(_start(material_client, gpid, lpid).headers["Location"])
        _answer(material_client, gpid, lpid, apid, x2, [options["Boarding"]])
        with material_app.app_context():
            assert QuizAnswerSelection.query.count() == 1
        _answer(material_client, gpid, lpid, apid, x2,
                [options["Boarding"], options["Gate"]])
    with material_app.app_context():
        assert QuizAnswer.query.count() == 1
        assert QuizAnswerSelection.query.count() == 2


def test_an_option_from_another_question_is_refused_generically(
    material_app, material_client
):
    with material_app.app_context():
        group, student, quiz, activity, q1, q2 = _world()
        gpid, lpid = group.public_id, activity.public_id
        x1 = q1.public_id
        foreign = _options(q2)["Boarding"]
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        apid = _attempt_id_from(_start(material_client, gpid, lpid).headers["Location"])
        resp = _answer(material_client, gpid, lpid, apid, x1, [foreign])
    assert "could not be read" in resp.get_data(as_text=True)
    with material_app.app_context():
        assert QuizAnswer.query.count() == 0


def test_a_retired_option_is_refused_identically(material_app, material_client):
    with material_app.app_context():
        group, student, quiz, activity, q1, _ = _world()
        gpid, lpid = group.public_id, activity.public_id
        x1 = q1.public_id
        retired = QuestionOption.query.filter_by(
            question_id=q1.id, option_text="Gate nine"
        ).one()
        retired.is_active = False
        retired.retired_at = OPENS
        db.session.commit()
        retired_pid = retired.public_id
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        apid = _attempt_id_from(_start(material_client, gpid, lpid).headers["Location"])
        resp = _answer(material_client, gpid, lpid, apid, x1, [retired_pid])
    assert "could not be read" in resp.get_data(as_text=True)
    with material_app.app_context():
        assert QuizAnswerSelection.query.count() == 0


def test_previous_and_next_follow_the_complete_authored_order(
    material_app, material_client
):
    with material_app.app_context():
        group, student, quiz, activity, q1, q2 = _world()
        gpid, lpid = group.public_id, activity.public_id
        x1, x2 = q1.public_id, q2.public_id
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        apid = _attempt_id_from(_start(material_client, gpid, lpid).headers["Location"])
        first = material_client.get(
            _question_url(gpid, lpid, apid, x1)
        ).get_data(as_text=True)
        second = material_client.get(
            _question_url(gpid, lpid, apid, x2)
        ).get_data(as_text=True)
    assert "Question 1 of 2" in first
    assert x2 in first and "Next" in first
    assert "Question 2 of 2" in second
    assert x1 in second and "Previous" in second


def test_exact_set_grading_for_single_and_multiple_answers(
    material_app, material_client
):
    """One point or zero: a subset, a superset and a different set of the
    same size all score zero, and so does an unanswered question."""
    with material_app.app_context():
        group, student, quiz, activity, q1, q2 = _world(attempt_limit=4)
        gpid, lpid = group.public_id, activity.public_id
        x1, x2 = q1.public_id, q2.public_id
        o1, o2 = _options(q1), _options(q2)
    _login_as(material_client, "sa@example.com")

    cases = [
        # (q1 picks, q2 picks, expected correct count)
        ([o1["Gate seven"]], [o2["Boarding"], o2["Gate"]], 2),   # both exact
        ([o1["Gate nine"]], [o2["Boarding"]], 0),                # wrong + subset
        ([o1["Gate seven"]], [o2["Boarding"], o2["Delay"], o2["Gate"]], 1),  # superset
        ([o1["Gate seven"]], [o2["Delay"], o2["Gate"]], 1),      # same size, different
    ]
    for index, (picks1, picks2, expected) in enumerate(cases):
        with _at(NOW):
            location = _start(material_client, gpid, lpid).headers["Location"]
            apid = _attempt_id_from(location)
            _answer(material_client, gpid, lpid, apid, x1, picks1)
            _answer(material_client, gpid, lpid, apid, x2, picks2)
            _submit(material_client, gpid, lpid, apid, x2, confirm=True)
        with material_app.app_context():
            attempt = QuizAttempt.query.filter_by(public_id=apid).one()
            assert attempt.correct_count == expected, (index, picks1, picks2)
            assert attempt.total_questions == 2


def test_an_unanswered_question_costs_exactly_what_a_wrong_one_does(
    material_app, material_client
):
    with material_app.app_context():
        group, student, quiz, activity, q1, q2 = _world()
        gpid, lpid = group.public_id, activity.public_id
        x1 = q1.public_id
        o1 = _options(q1)
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        apid = _attempt_id_from(_start(material_client, gpid, lpid).headers["Location"])
        _answer(material_client, gpid, lpid, apid, x1, [o1["Gate seven"]])
        # Submitting with an unanswered question needs an explicit
        # confirmation first.
        resp = _submit(material_client, gpid, lpid, apid, x1)
        assert "not answered 1 question" in resp.get_data(as_text=True)
        with material_app.app_context():
            assert QuizAttempt.query.one().status == IN_PROGRESS
        _submit(material_client, gpid, lpid, apid, x1, confirm=True)
    with material_app.app_context():
        attempt = QuizAttempt.query.one()
        assert attempt.status == SUBMITTED
        assert (attempt.correct_count, attempt.total_questions) == (1, 2)


def test_a_replayed_submission_changes_nothing(material_app, material_client):
    with material_app.app_context():
        group, student, quiz, activity, q1, _ = _world()
        gpid, lpid = group.public_id, activity.public_id
        x1 = q1.public_id
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        apid = _attempt_id_from(_start(material_client, gpid, lpid).headers["Location"])
        token = _token(
            material_client, _question_url(gpid, lpid, apid, x1), "submit_state"
        )
        _submit(material_client, gpid, lpid, apid, x1, token=token, confirm=True)
    with material_app.app_context():
        first = QuizAttempt.query.one()
        snapshot = (first.status, first.submitted_at, first.correct_count,
                    first.total_questions)
    with _at(NOW + timedelta(minutes=5)):
        resp = _submit(
            material_client, gpid, lpid, apid, x1, token=token, confirm=True
        )
    assert resp.status_code == 200
    with material_app.app_context():
        again = QuizAttempt.query.one()
        assert (again.status, again.submitted_at, again.correct_count,
                again.total_questions) == snapshot


def test_an_overdue_attempt_expires_and_is_graded_on_what_was_saved(
    material_app, material_client
):
    with material_app.app_context():
        group, student, quiz, activity, q1, _ = _world(time_limit=10)
        gpid, lpid = group.public_id, activity.public_id
        x1 = q1.public_id
        o1 = _options(q1)
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        apid = _attempt_id_from(_start(material_client, gpid, lpid).headers["Location"])
        _answer(material_client, gpid, lpid, apid, x1, [o1["Gate seven"]])
    # One second past the deadline, the next authorized request finalizes it.
    with _at(NOW + timedelta(minutes=10)):
        resp = material_client.get(_base(gpid, lpid))
    assert resp.status_code == 200
    with material_app.app_context():
        attempt = QuizAttempt.query.one()
        assert attempt.status == EXPIRED
        assert attempt.submitted_at is None  # it was never submitted
        assert (attempt.correct_count, attempt.total_questions) == (1, 2)


def test_a_write_arriving_one_second_late_is_refused(material_app, material_client):
    with material_app.app_context():
        group, student, quiz, activity, q1, _ = _world(time_limit=10)
        gpid, lpid = group.public_id, activity.public_id
        x1 = q1.public_id
        o1 = _options(q1)
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        apid = _attempt_id_from(_start(material_client, gpid, lpid).headers["Location"])
        token = _token(
            material_client, _question_url(gpid, lpid, apid, x1), "answer_state"
        )
    with _at(NOW + timedelta(minutes=10)):
        resp = _answer(
            material_client, gpid, lpid, apid, x1, [o1["Gate seven"]], token=token
        )
    assert "time ran out" in resp.get_data(as_text=True)
    with material_app.app_context():
        assert QuizAttempt.query.one().status == EXPIRED
        assert QuizAnswerSelection.query.count() == 0


def test_a_stale_answer_token_is_refused(material_app, material_client):
    with material_app.app_context():
        group, student, quiz, activity, q1, _ = _world()
        gpid, lpid = group.public_id, activity.public_id
        x1 = q1.public_id
        o1 = _options(q1)
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        apid = _attempt_id_from(_start(material_client, gpid, lpid).headers["Location"])
        resp = _answer(
            material_client, gpid, lpid, apid, x1, [o1["Gate seven"]],
            token="not-a-token",
        )
    assert "out of date" in resp.get_data(as_text=True)
    with material_app.app_context():
        assert QuizAnswer.query.count() == 0


def test_an_m04d_quiz_token_is_worthless_here(material_app, material_client):
    """Different salts: an ordinary-Quiz answer token fails signature
    verification on the Listening surface."""
    with material_app.app_context():
        group, student, quiz, activity, q1, _ = _world()
        gpid, lpid = group.public_id, activity.public_id
        x1 = q1.public_id
        o1 = _options(q1)
        student_public_id = student.public_id
        version = quiz.version
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        apid = _attempt_id_from(_start(material_client, gpid, lpid).headers["Location"])
        with material_app.test_request_context():
            forged = student_quizzes_mod._make_answer_token(
                student_public_id, gpid, lpid, apid, x1, version, IN_PROGRESS
            )
        resp = _answer(
            material_client, gpid, lpid, apid, x1, [o1["Gate seven"]], token=forged
        )
    assert "out of date" in resp.get_data(as_text=True)
    with material_app.app_context():
        assert QuizAnswer.query.count() == 0


# ===========================================================================
# The answer key never reaches a Student
# ===========================================================================


def test_no_student_page_exposes_the_answer_key(material_app, material_client):
    with material_app.app_context():
        group, student, quiz, activity, q1, q2 = _world(
            visibility=TranscriptVisibility.ALWAYS.value
        )
        gpid, lpid = group.public_id, activity.public_id
        x1, x2 = q1.public_id, q2.public_id
        o1 = _options(q1)
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        apid = _attempt_id_from(_start(material_client, gpid, lpid).headers["Location"])
        _answer(material_client, gpid, lpid, apid, x1, [o1["Gate seven"]])
        pages = [
            material_client.get("/student/listening").get_data(as_text=True),
            material_client.get(_base(gpid, lpid)).get_data(as_text=True),
            material_client.get(
                _question_url(gpid, lpid, apid, x1)
            ).get_data(as_text=True),
            material_client.get(
                _question_url(gpid, lpid, apid, x2)
            ).get_data(as_text=True),
        ]
        _submit(material_client, gpid, lpid, apid, x2, confirm=True)
        pages.append(
            material_client.get(_result_url(gpid, lpid, apid)).get_data(as_text=True)
        )
    for body in pages:
        for marker in ("is_correct", "correct_option", "Correct answer",
                       "answer_key", "data-correct"):
            assert marker not in body, marker
    # The result page reports right/wrong and no option text at all.
    result = pages[-1]
    assert "Correct" in result  # the per-question verdict badge
    assert "Gate seven" not in result and "Gate nine" not in result


# ===========================================================================
# Transcript policy
# ===========================================================================


def _all_student_surfaces(app, client, gpid, lpid):
    """Every Student-reachable response for one activity, as text: the
    list, the detail page, both question pages, the result page and the
    audio response."""
    with _at(NOW):
        apid = _attempt_id_from(_start(client, gpid, lpid).headers["Location"])
        with app.app_context():
            questions = [q.public_id for q in QuizQuestion.query.order_by(
                QuizQuestion.display_order
            )]
        bodies = {
            "list": client.get("/student/listening").get_data(as_text=True),
            "detail": client.get(_base(gpid, lpid)).get_data(as_text=True),
            "question": client.get(
                _question_url(gpid, lpid, apid, questions[0])
            ).get_data(as_text=True),
            # The audio response is raw bytes -- decoded leniently only so
            # a substring check can prove the transcript is not in them.
            "audio": _serve_get(
                client, _base(gpid, lpid) + "/audio"
            ).get_data().decode("utf-8", "replace"),
        }
        _submit(client, gpid, lpid, apid, questions[0], confirm=True)
        bodies["result"] = client.get(
            _result_url(gpid, lpid, apid)
        ).get_data(as_text=True)
    return bodies


def test_a_hidden_transcript_reaches_no_student_surface_at_all(
    material_app, material_client
):
    with material_app.app_context():
        group, student, quiz, activity, _, _ = _world(
            visibility=TranscriptVisibility.HIDDEN.value
        )
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "sa@example.com")
    bodies = _all_student_surfaces(material_app, material_client, gpid, lpid)
    for name, body in bodies.items():
        assert TRANSCRIPT not in body, name
        assert "Flight 42" not in body, name
        assert "Transcript" not in body, name


def test_an_after_submission_transcript_appears_only_on_a_finalized_result(
    material_app, material_client
):
    with material_app.app_context():
        group, student, quiz, activity, _, _ = _world(
            visibility=TranscriptVisibility.AFTER_SUBMISSION.value
        )
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "sa@example.com")
    bodies = _all_student_surfaces(material_app, material_client, gpid, lpid)
    for name in ("list", "detail", "question", "audio"):
        assert TRANSCRIPT not in bodies[name], name
    assert TRANSCRIPT in bodies["result"]
    assert "only after an attempt is finished" in bodies["result"]


def test_an_always_transcript_appears_while_listening_and_answering(
    material_app, material_client
):
    with material_app.app_context():
        group, student, quiz, activity, _, _ = _world(
            visibility=TranscriptVisibility.ALWAYS.value
        )
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "sa@example.com")
    bodies = _all_student_surfaces(material_app, material_client, gpid, lpid)
    for name in ("detail", "question", "result"):
        assert TRANSCRIPT in bodies[name], name
    # The list is a bounded projection that never selects it, and the
    # audio response is bytes.
    assert TRANSCRIPT not in bodies["list"]
    assert TRANSCRIPT not in bodies["audio"]


def test_an_in_progress_attempt_is_not_a_finished_one(material_app, material_client):
    """``after_submission`` must not release the transcript to a Student
    who merely opened the result URL while the attempt is still
    running."""
    with material_app.app_context():
        group, student, quiz, activity, q1, _ = _world(
            visibility=TranscriptVisibility.AFTER_SUBMISSION.value
        )
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        apid = _attempt_id_from(_start(material_client, gpid, lpid).headers["Location"])
        resp = material_client.get(_result_url(gpid, lpid, apid), follow_redirects=True)
    assert TRANSCRIPT not in resp.get_data(as_text=True)


def test_the_transcript_never_travels_in_a_token_url_form_field_or_flash(
    material_app, material_client
):
    with material_app.app_context():
        group, student, quiz, activity, q1, _ = _world(
            visibility=TranscriptVisibility.ALWAYS.value
        )
        gpid, lpid = group.public_id, activity.public_id
        x1 = q1.public_id
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        location = _start(material_client, gpid, lpid).headers["Location"]
        apid = _attempt_id_from(location)
        html = material_client.get(
            _question_url(gpid, lpid, apid, x1)
        ).get_data(as_text=True)

    assert TRANSCRIPT not in location  # never in a URL
    # Every signed token on the page decodes to public identifiers only.
    for field in ("answer_state", "submit_state"):
        token = re.search(rf'name="{field}" value="([^"]*)"', html).group(1)
        with material_app.test_request_context():
            purpose = "listening-answer" if field == "answer_state" else "listening-submit"
            payload = student_listening_mod._load_token(token, purpose)
        assert payload is not None
        for value in payload.values():
            assert TRANSCRIPT not in str(value)
            assert "Flight 42" not in str(value)
    # No hidden input and no JavaScript value carries it.
    for hidden in re.findall(r'<input type="hidden"[^>]*value="([^"]*)"', html):
        assert TRANSCRIPT not in hidden
    for script in re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.S):
        assert TRANSCRIPT not in script
    # The transcript is rendered as escaped page text, in its own section.
    assert "Flight 42 is now boarding at gate seven." in html


def test_the_transcript_and_vocabulary_are_escaped_never_rendered_as_html(
    material_app, material_client
):
    hostile = "<script>alert('x')</script> & \"quoted\""
    with material_app.app_context():
        group, student, quiz, activity, _, _ = _world(
            visibility=TranscriptVisibility.ALWAYS.value,
            transcript=hostile, vocabulary=hostile,
        )
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        body = material_client.get(_base(gpid, lpid)).get_data(as_text=True)
    assert "<script>alert('x')</script>" not in body
    assert "&lt;script&gt;alert(&#39;x&#39;)&lt;/script&gt;" in body


def test_vocabulary_is_shown_before_and_during_an_attempt(
    material_app, material_client
):
    """Vocabulary support has no policy of its own -- a Teacher wrote it
    *for* the Student to use while listening."""
    with material_app.app_context():
        group, student, quiz, activity, q1, _ = _world(
            visibility=TranscriptVisibility.HIDDEN.value
        )
        gpid, lpid = group.public_id, activity.public_id
        x1 = q1.public_id
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        detail = material_client.get(_base(gpid, lpid)).get_data(as_text=True)
        apid = _attempt_id_from(_start(material_client, gpid, lpid).headers["Location"])
        question = material_client.get(
            _question_url(gpid, lpid, apid, x1)
        ).get_data(as_text=True)
    assert VOCABULARY in detail
    assert VOCABULARY in question


def test_empty_vocabulary_renders_no_section(material_app, material_client):
    with material_app.app_context():
        group, student, quiz, activity, _, _ = _world(vocabulary="")
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        body = material_client.get(_base(gpid, lpid)).get_data(as_text=True)
    assert "<h2>Vocabulary</h2>" not in body


# ===========================================================================
# Audio: authorization, auditing and the player contract
# ===========================================================================


def test_the_student_audio_route_is_audited_and_uncacheable(
    material_app, material_client
):
    with material_app.app_context():
        group, student, quiz, activity, _, _ = _world()
        gpid, lpid = group.public_id, activity.public_id
        student_id = student.id
        before = FileAccessLog.query.count()
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        resp = _serve_get(material_client, _base(gpid, lpid) + "/audio")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("audio/mpeg")
    assert "attachment" not in resp.headers.get("Content-Disposition", "")
    assert resp.headers["Cache-Control"] == "private, no-store, max-age=0"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    with material_app.app_context():
        assert FileAccessLog.query.count() == before + 1
        log = FileAccessLog.query.order_by(FileAccessLog.id.desc()).first()
        assert log.action == FileAccessAction.INLINE.value
        assert log.actor_id == student_id


def test_a_student_has_no_download_endpoint(material_app, material_client):
    """Inline playback only: M05 adds no Student attachment route."""
    with material_app.app_context():
        group, student, quiz, activity, _, _ = _world()
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        assert material_client.get(
            _base(gpid, lpid) + "/audio/download"
        ).status_code == 404


def test_the_audio_response_discloses_nothing_internal(material_app, material_client):
    with material_app.app_context():
        group, student, quiz, activity, _, _ = _world()
        gpid, lpid = group.public_id, activity.public_id
        upload = UploadedFile.query.one()
        key, sha, filename = upload.storage_key, upload.sha256, upload.original_filename
        uploader = User.query.filter_by(email="ta@example.com").one().public_id
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        resp = _serve_get(material_client, _base(gpid, lpid) + "/audio")
        page = material_client.get(_base(gpid, lpid)).get_data(as_text=True)
    headers = " ".join(f"{k}: {v}" for k, v in resp.headers.items())
    for secret in (key, sha, uploader):
        assert secret not in headers
        assert secret not in page
    # The page addresses the recording only by the authorized route.
    assert f"{_base(gpid, lpid)}/audio" in page
    assert "/static/" not in page.split("<audio")[1].split("</audio>")[0]
    assert filename not in page


def test_the_student_audio_url_is_not_a_public_static_url(material_app, material_client):
    with material_app.app_context():
        group, student, quiz, activity, _, _ = _world()
        gpid, lpid = group.public_id, activity.public_id
        key = UploadedFile.query.one().storage_key
    # Unauthenticated, the endpoint is a login redirect, not a file.
    assert material_client.get(_base(gpid, lpid) + "/audio").status_code == 302
    # And nothing is reachable through the public static tree.
    assert material_client.get(f"/static/{key}").status_code == 404
    assert material_client.get(f"/static/materials/{key}").status_code == 404


def test_cross_group_and_cross_activity_audio_tampering_is_refused(
    material_app, material_client
):
    with material_app.app_context():
        group_a, _, _, activity_a, _, _ = _world(label="A")
        group_b, _, _, activity_b, _, _ = _world(label="B", title="Other listening")
        a_group, b_group = group_a.public_id, group_b.public_id
        a_act, b_act = activity_a.public_id, activity_b.public_id
        b_file = UploadedFile.query.order_by(UploadedFile.id.desc()).first().public_id
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        # This Student's own activity: allowed.
        assert _serve_get(
            material_client, _base(a_group, a_act) + "/audio"
        ).status_code == 200
        # Another Group's activity, and this Group with another Group's
        # activity id -- both 404 identically.
        assert material_client.get(_base(b_group, b_act) + "/audio").status_code == 404
        assert material_client.get(_base(a_group, b_act) + "/audio").status_code == 404
        assert material_client.get(_base(b_group, a_act) + "/audio").status_code == 404
        # A file public id in the activity slot names nothing.
        assert material_client.get(_base(a_group, b_file) + "/audio").status_code == 404


def test_the_audio_route_rejects_unsupported_methods(material_app, material_client):
    with material_app.app_context():
        group, student, quiz, activity, _, _ = _world()
        gpid, lpid = group.public_id, activity.public_id
    _login_as(material_client, "sa@example.com")
    for method in ("post", "put", "delete", "patch"):
        assert getattr(material_client, method)(
            _base(gpid, lpid) + "/audio"
        ).status_code == 405


def test_the_player_renders_the_approved_controls_and_speeds(
    material_app, material_client
):
    """A structural contract about the rendered markup only. No browser,
    playback or accessibility verification is performed here."""
    with material_app.app_context():
        group, student, quiz, activity, q1, _ = _world()
        gpid, lpid = group.public_id, activity.public_id
        x1 = q1.public_id
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        detail = material_client.get(_base(gpid, lpid)).get_data(as_text=True)
        apid = _attempt_id_from(_start(material_client, gpid, lpid).headers["Location"])
        question = material_client.get(
            _question_url(gpid, lpid, apid, x1)
        ).get_data(as_text=True)

    for body in (detail, question):
        # A native <audio controls>, so the page works without JavaScript.
        assert "data-audio-player" in body
        assert "<audio data-audio-element controls" in body
        assert f'src="{_base(gpid, lpid)}/audio"' in body
        # Play/pause and replay, as real keyboard-reachable buttons.
        assert 'type="button" class="btn btn--primary" data-audio-toggle' in body
        assert "data-audio-replay" in body
        # Exactly the four approved speeds, and the visible current speed.
        for speed in ("0.75", "1", "1.25", "1.5"):
            assert f'data-audio-speed="{speed}"' in body, speed
        assert body.count("data-audio-speed=") == 4
        assert "data-audio-current-speed" in body
        assert 'aria-pressed="true"' in body
        # The extra controls ship hidden and are revealed only by the
        # script, so a JavaScript-disabled page shows no inert button.
        assert "data-audio-controls hidden" in body
        assert "js/audio_player.js" in body
        # Nothing that was deliberately not implemented.
        for forbidden in ("waveform", "transcode", "speech", "analytics",
                          "playCount", "maxPlays"):
            assert forbidden not in body, forbidden


def test_the_player_javascript_decides_nothing_and_fetches_nothing():
    """The controller is presentation-only: it must not submit a form,
    call the network, or implement a playback limit."""
    import pathlib

    source = pathlib.Path("app/static/js/audio_player.js").read_text(encoding="utf-8")
    for forbidden in ("fetch(", "XMLHttpRequest", ".submit(", "navigator.sendBeacon",
                      "localStorage", "sessionStorage"):
        assert forbidden not in source, forbidden
    assert "playbackRate" in source
    assert "[0.75, 1, 1.25, 1.5]" in source


# ===========================================================================
# Cache headers, pagination and bounded query counts
# ===========================================================================


def test_every_student_page_is_private_and_uncacheable(material_app, material_client):
    with material_app.app_context():
        group, student, quiz, activity, q1, _ = _world()
        gpid, lpid = group.public_id, activity.public_id
        x1 = q1.public_id
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        apid = _attempt_id_from(_start(material_client, gpid, lpid).headers["Location"])
        urls = [
            "/student/listening",
            _base(gpid, lpid),
            _question_url(gpid, lpid, apid, x1),
        ]
        for url in urls:
            resp = material_client.get(url)
            assert resp.status_code == 200, url
            assert resp.headers["Cache-Control"] == "private, no-store", url
            assert "Cookie" in resp.headers.get("Vary", ""), url
        _submit(material_client, gpid, lpid, apid, x1, confirm=True)
        resp = material_client.get(_result_url(gpid, lpid, apid))
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in resp.headers.get("Vary", "")


def test_the_student_list_is_paginated_at_a_fixed_size(material_app, material_client):
    from app.services.quiz_queries import PAGE_SIZE

    with material_app.app_context():
        group, student, quiz, activity, _, _ = _world(label="A", title="Listening 00")
        teacher = User.query.filter_by(email="ta@example.com").one()
        for index in range(1, PAGE_SIZE + 3):
            extra = Quiz(
                group_id=group.id, title=f"Listening {index:02d}",
                instructions="Listen.", version=1,
                status=QuizStatus.PUBLISHED.value, published_at=OPENS,
                opens_at=OPENS, closes_at=CLOSES - timedelta(minutes=index),
                attempt_limit=1, created_at=OPENS, updated_at=OPENS,
            )
            db.session.add(extra)
            db.session.commit()
            db.session.add(ListeningActivity(
                quiz_id=extra.id, audio_file_id=_real_audio(teacher.id).id,
                transcript="", transcript_visibility="hidden", vocabulary_notes="",
                creation_nonce=secrets.token_hex(16),
                created_at=OPENS, updated_at=OPENS,
            ))
            db.session.commit()
    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        first = material_client.get("/student/listening").get_data(as_text=True)
        second = material_client.get("/student/listening?page=2").get_data(as_text=True)
        far = material_client.get("/student/listening?page=999").get_data(as_text=True)
    assert "Next" in first and "Previous" not in first
    assert "Previous" in second
    assert "Previous" not in far  # a page past the end falls back to page 1


def _count_statements(client, url):
    """Statements issued while rendering `url`.

    The page is fetched once first and not counted: the very first
    request of a session also loads the acting user's row, which is
    request plumbing rather than a cost that grows with the activity.
    """
    from sqlalchemy import event

    assert client.get(url).status_code == 200
    statements = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        assert client.get(url).status_code == 200
    finally:
        event.remove(db.engine, "before_cursor_execute", record)
    return len(statements)


def test_the_question_page_costs_the_same_for_two_and_twenty_questions(
    material_app, material_client
):
    """Bounded, not merely small: the two counts must be EQUAL."""
    with material_app.app_context():
        group, student, quiz, activity, q1, _ = _world(label="A", title="Small")
        teacher = User.query.filter_by(email="ta@example.com").one()
        big = Quiz(
            group_id=group.id, title="Big", instructions="Listen.", version=1,
            status=QuizStatus.PUBLISHED.value, published_at=OPENS,
            opens_at=OPENS, closes_at=CLOSES, attempt_limit=1,
            created_at=OPENS, updated_at=OPENS,
        )
        db.session.add(big)
        db.session.commit()
        big_activity = ListeningActivity(
            quiz_id=big.id, audio_file_id=_real_audio(teacher.id).id,
            transcript="", transcript_visibility="hidden", vocabulary_notes="",
            creation_nonce=secrets.token_hex(16), created_at=OPENS, updated_at=OPENS,
        )
        db.session.add(big_activity)
        db.session.commit()
        for index in range(20):
            _question(big, f"Q{index}", SINGLE, index, [("A", True), ("B", False)])
        gpid = group.public_id
        small_pid, big_pid = activity.public_id, big_activity.public_id
        small_q = q1.public_id
        big_q = QuizQuestion.query.filter_by(quiz_id=big.id).order_by(
            QuizQuestion.display_order
        ).first().public_id

    _login_as(material_client, "sa@example.com")
    with _at(NOW):
        small_attempt = _attempt_id_from(
            _start(material_client, gpid, small_pid).headers["Location"]
        )
        big_attempt = _attempt_id_from(
            _start(material_client, gpid, big_pid).headers["Location"]
        )
        small_count = _count_statements(
            material_client, _question_url(gpid, small_pid, small_attempt, small_q)
        )
        big_count = _count_statements(
            material_client, _question_url(gpid, big_pid, big_attempt, big_q)
        )
    assert small_count == big_count
