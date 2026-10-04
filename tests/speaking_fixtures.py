"""Shared fixtures for the Phase 4 / M06 Speaking test modules.

Kept in one module -- like ``tests/file_fixtures.py`` and
``tests/notification_fixtures.py`` -- so the model, route, audio, feedback
and migration suites all build the same academic chain and the same
objects, and a change to the shape of a Speaking activity cannot make four
files disagree about what one is.

Nothing here goes through a route: these helpers write rows directly, so a
test about (say) the deadline is not also a test of the create form. The
route-driven helpers live beside the tests that use them.
"""

import re
import secrets
from datetime import date, datetime

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Assignment,
    AssignmentStatus,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    SpeakingActivity,
    SpeakingFeedback,
    SpeakingSubmission,
    UploadedFile,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password
from tests.conftest import login_path

PW = "Sup3rSecret!123"

#: Local wall-clock strings for the form, and the naive-UTC instants they
#: mean. This project's configured timezone is not UTC, so the two
#: genuinely differ; route-driven tests submit the LOCAL text and derive
#: their expectation through the same shared helper the form uses, so they
#: stay correct under any configured timezone.
OPENS_LOCAL = "2026-05-01T08:00:00"
DUE_LOCAL = "2026-06-01T08:00:00"


def utc_of(app, local_text):
    """The naive-UTC instant a submitted local wall-clock string means."""
    from app.services.schedule_occurrences import from_app_local

    return from_app_local(
        app.config["APP_TIMEZONE"],
        datetime.strptime(local_text, "%Y-%m-%dT%H:%M:%S"),
    )


def local_of(app, utc_moment):
    """The local wall-clock string a stored UTC instant renders as, with
    seconds -- the form renders ``%Y-%m-%dT%H:%M:%S``."""
    from app.services.schedule_occurrences import to_app_local

    return to_app_local(app.config["APP_TIMEZONE"], utc_moment).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )


#: The canonical window every Speaking test works in, and the moment that
#: sits inside it. Whole seconds throughout: the columns hold whole
#: seconds, and every acceptance decision is made in them.
OPENS = datetime(2026, 5, 1, 8, 0, 0)
DUE = datetime(2026, 6, 1, 8, 0, 0)
NOW = datetime(2026, 5, 10, 9, 0, 0)
BEFORE_OPEN = datetime(2026, 4, 20, 9, 0, 0)
AFTER_DUE = datetime(2026, 6, 2, 9, 0, 0)

TITLE = "Describe your home town"
INSTRUCTIONS = "Speak for about one minute about where you grew up."


class Clock:
    """A deterministic replacement for ``utc_reference_now``.

    Successive calls walk the supplied moments and then hold the last one,
    so a test can make the moment *after* the locks differ from the moment
    before them -- which is exactly how the late-arrival and
    waited-behind-a-lock cases are exercised.
    """

    def __init__(self, *moments):
        self.moments = list(moments) or [NOW]
        self.calls = 0

    def __call__(self, utc_now=None):
        moment = self.moments[min(self.calls, len(self.moments) - 1)]
        self.calls += 1
        return moment


def fresh_identity():
    """Drop Flask-Login's per-request user cache so a second ``login`` in
    the same app context really switches accounts."""
    from flask import g, has_app_context

    if has_app_context():
        g.pop("_login_user", None)


def login_as(client, email, password=PW):
    """Log in as `email`, switching accounts if one is already signed in.

    The login view redirects an already-authenticated visitor rather than
    re-authenticating them, so a plain second POST would silently leave
    the first account signed in -- and a test about a co-teacher would
    then quietly be a test about the first Teacher again.
    """
    fresh_identity()
    client.post("/auth/logout", follow_redirects=True)
    fresh_identity()
    return client.post(
        login_path(email), data={"email": email, "password": password},
        follow_redirects=True
    )


def logout(client):
    fresh_identity()
    return client.post("/auth/logout", follow_redirects=True)


def user(email, role, status=UserStatus.ACTIVE.value, name=None):
    row = User(
        email=email,
        password_hash=hash_password(PW),
        full_name=name or email.split("@")[0],
        role=role,
        status=status,
    )
    db.session.add(row)
    db.session.commit()
    return row


def hierarchy(label="A", **statuses):
    term = AcademicTerm(
        name=f"Term {label}",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        status=statuses.get("term_status", AcademicStatus.ACTIVE.value),
    )
    level = Level(
        name=f"Level {label}",
        display_order=0,
        status=statuses.get("level_status", AcademicStatus.ACTIVE.value),
    )
    db.session.add_all([term, level])
    db.session.commit()
    course = Course(
        title=f"Course {label}",
        level_id=level.id,
        display_order=0,
        status=statuses.get("course_status", AcademicStatus.ACTIVE.value),
    )
    db.session.add(course)
    db.session.commit()
    group = Group(
        academic_term_id=term.id,
        course_id=course.id,
        name=f"Group {label}",
        capacity=20,
        status=statuses.get("group_status", AcademicStatus.ACTIVE.value),
    )
    db.session.add(group)
    db.session.commit()
    return group


def setup_group(label="A", teacher_email="teacher@example.com", **statuses):
    """One Group with one actively assigned Teacher."""
    group = hierarchy(label, **statuses)
    teacher = user(teacher_email, UserRole.TEACHER.value)
    db.session.add(GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id))
    db.session.commit()
    return teacher, group


def assign_teacher(group, email, status=GroupTeacherAssignmentStatus.ACTIVE.value):
    teacher = user(email, UserRole.TEACHER.value)
    db.session.add(
        GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id, status=status)
    )
    db.session.commit()
    return teacher


def enroll(group, email="student@example.com", status=EnrollmentStatus.ACTIVE.value,
           account_status=UserStatus.ACTIVE.value, name=None):
    student = user(email, UserRole.STUDENT.value, status=account_status, name=name)
    db.session.add(
        Enrollment(group_id=group.id, student_id=student.id, status=status)
    )
    db.session.commit()
    return student


def speaking_activity(
    group, title=TITLE, instructions=INSTRUCTIONS, published=False,
    opens_at=OPENS, due_at=DUE, created_at=OPENS,
):
    """One Speaking activity built directly -- an Assignment row plus its
    extension row -- for tests that are not about the create route."""
    assignment = Assignment(
        group_id=group.id,
        title=title,
        instructions=instructions,
        opens_at=opens_at,
        due_at=due_at,
        status=(
            AssignmentStatus.PUBLISHED.value if published else AssignmentStatus.DRAFT.value
        ),
        published_at=OPENS if published else None,
    )
    db.session.add(assignment)
    db.session.commit()
    activity = SpeakingActivity(
        assignment_id=assignment.id,
        creation_nonce=secrets.token_hex(16),
        created_at=created_at,
        updated_at=created_at,
    )
    db.session.add(activity)
    db.session.commit()
    return assignment, activity


def ordinary_assignment(group, title="Plain assignment", published=True):
    """An ordinary Assignment -- deliberately with NO extension row."""
    assignment = Assignment(
        group_id=group.id,
        title=title,
        instructions="Write an answer.",
        opens_at=OPENS,
        due_at=DUE,
        status=(
            AssignmentStatus.PUBLISHED.value if published else AssignmentStatus.DRAFT.value
        ),
        published_at=OPENS if published else None,
    )
    db.session.add(assignment)
    db.session.commit()
    return assignment


def upload_row(uploader_id, category="audio", extension="webm", key=None,
               content_type="audio/webm"):
    row = UploadedFile(
        storage_key=key or secrets.token_hex(16),
        original_filename=f"speaking-recording.{extension}",
        extension=extension,
        category=category,
        content_type=content_type,
        byte_size=2048,
        sha256="a" * 64,
        uploaded_by_id=uploader_id,
    )
    db.session.add(row)
    db.session.commit()
    return row


def speaking_submission(activity, student, submitted_at=NOW, category="audio",
                        upload=None):
    """One final recording built directly."""
    uploaded = upload or upload_row(student.id, category=category)
    row = SpeakingSubmission(
        speaking_activity_id=activity.id,
        student_id=student.id,
        audio_file_id=uploaded.id,
        creation_nonce=secrets.token_hex(16),
        submitted_at=submitted_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def speaking_feedback(submission, reviewer, text="Clear and confident.", version=1,
                      created_at=NOW):
    row = SpeakingFeedback(
        speaking_submission_id=submission.id,
        reviewer_id=reviewer.id,
        feedback_text=text,
        version=version,
        created_at=created_at,
        updated_at=created_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


# ---------------------------------------------------------------------------
# request helpers
# ---------------------------------------------------------------------------


def teacher_base(gpid):
    return f"/teacher/groups/{gpid}/speaking"


def teacher_detail(gpid, spid):
    return f"{teacher_base(gpid)}/{spid}"


def student_detail(gpid, spid):
    return f"/student/groups/{gpid}/speaking/{spid}"


def hidden_value(client, url, field):
    """Read a hidden input's value out of a rendered page.

    Deliberately reads the page the server actually rendered rather than
    minting a token in the test: a test that signed its own token would
    stop proving that the *page* carries a usable one.
    """
    html = client.get(url).get_data(as_text=True)
    match = re.search(rf'name="{field}" value="([^"]*)"', html)
    return match.group(1) if match else ""


def serve_get(client, url, **kwargs):
    """Fetch a streamed file response and release its handle.

    ``send_file`` hands back an open file wrapper, and the Werkzeug test
    client only releases it once the body has been read and the response
    closed. Leaving that to the garbage collector raises an unraisable
    exception that the strict-warning suite turns into a failure -- so
    every audio fetch goes through this helper, exactly as the M05/M12
    serving tests already do.
    """
    response = client.get(url, buffered=True, **kwargs)
    response.get_data()
    response.close()
    return response


def stored_files(app):
    root = app.extensions["material_config"].storage_root
    if not root.exists():
        return []
    return sorted(path.name for path in root.iterdir() if path.is_file())
