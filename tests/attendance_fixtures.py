"""Shared fixtures for the Phase 4 / M07 Attendance test modules.

Kept in one module -- like ``tests/speaking_fixtures.py`` and
``tests/file_fixtures.py`` -- so the model, route, administrator, student
and migration suites all build the same academic chain, the same
Schedule and the same sessions, and a change to the shape of an
attendance session cannot make five files disagree about what one is.

Nothing here goes through a route: these helpers write rows directly, so a
test about (say) the roster freeze is not also a test of the create form.
The route-driven helpers live beside the tests that use them.

**Time.** ``session_date`` / ``start_time`` / ``end_time`` are LOCAL civil
values (the M08 convention this milestone reuses), while the clock the
blueprint reads is naive UTC. The project's configured timezone is not
UTC, so the two genuinely differ; every moment below is therefore stated
as UTC and the local date it means is derived through the same helper the
application uses, so the fixtures stay correct under any configured
timezone.
"""

import html as html_module
import re
from datetime import date, datetime, time, timedelta

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    AttendanceRecord,
    AttendanceSession,
    AttendanceStatus,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    Schedule,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password

PW = "Sup3rSecret!123"

#: The canonical reference instant every Attendance test works from, as
#: naive UTC. Whole seconds throughout: the columns hold whole seconds,
#: and every write decision is made in them.
NOW = datetime(2026, 5, 13, 9, 0, 0)
LATER = datetime(2026, 5, 13, 10, 30, 0)

#: A real past occurrence, a week earlier, and the next one after today.
SESSION_DATE = date(2026, 5, 12)
NEXT_WEEK = SESSION_DATE + timedelta(days=7)
PREVIOUS_WEEK = SESSION_DATE - timedelta(days=7)

START_TIME = time(18, 0)
END_TIME = time(20, 0)
LOCATION = "Room 3"

TERM_START = date(2026, 1, 1)
TERM_END = date(2026, 12, 31)
EFFECTIVE_START = date(2026, 2, 1)
EFFECTIVE_END = date(2026, 11, 30)


def today_local(app, moment=NOW):
    """The local civil date `moment` (naive UTC) falls on, resolved the
    same way the application resolves it."""
    from app.services.schedule_occurrences import to_app_local

    return to_app_local(app.config["APP_TIMEZONE"], moment).date()


class Clock:
    """A deterministic replacement for ``utc_reference_now``.

    Successive calls walk the supplied moments and then hold the last one,
    so a test can make the moment *after* the locks differ from the moment
    before them -- which is how the waited-behind-a-lock case is
    exercised.
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
        "/auth/login", data={"email": email, "password": password}, follow_redirects=True
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
        start_date=TERM_START,
        end_date=TERM_END,
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


def enroll(
    group,
    email="student@example.com",
    status=EnrollmentStatus.ACTIVE.value,
    account_status=UserStatus.ACTIVE.value,
    name=None,
    role=UserRole.STUDENT.value,
):
    student = user(email, role, status=account_status, name=name)
    db.session.add(Enrollment(group_id=group.id, student_id=student.id, status=status))
    db.session.commit()
    return student


def schedule_for(
    group,
    on_date=SESSION_DATE,
    start=START_TIME,
    end=END_TIME,
    location=LOCATION,
    status=AcademicStatus.ACTIVE.value,
    effective_start=EFFECTIVE_START,
    effective_end=EFFECTIVE_END,
):
    """One Schedule whose weekday is exactly `on_date`'s, so `on_date` is
    a genuine occurrence of it."""
    row = Schedule(
        group_id=group.id,
        day_of_week=on_date.weekday(),
        start_time=start,
        end_time=end,
        effective_start_date=effective_start,
        effective_end_date=effective_end,
        location=location,
        status=status,
    )
    db.session.add(row)
    db.session.commit()
    return row


def attendance_session(
    group,
    schedule,
    session_date=SESSION_DATE,
    finalized_at=None,
    version=1,
    created_at=NOW,
):
    """One attendance session built directly, for tests that are not about
    the create route."""
    row = AttendanceSession(
        group_id=group.id,
        schedule_id=schedule.id,
        session_date=session_date,
        start_time=schedule.start_time,
        end_time=schedule.end_time,
        location=schedule.location,
        version=version,
        finalized_at=finalized_at,
        created_at=created_at,
        updated_at=created_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def attendance_record(
    session,
    student,
    status=AttendanceStatus.ABSENT.value,
    note=None,
    version=1,
    created_at=NOW,
):
    row = AttendanceRecord(
        attendance_session_id=session.id,
        student_id=student.id,
        status=status,
        note=note,
        version=version,
        created_at=created_at,
        updated_at=created_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def full_session(group, schedule, students, session_date=SESSION_DATE, finalized_at=None):
    """One session plus one record per Student, the shape the create route
    produces."""
    session = attendance_session(
        group, schedule, session_date=session_date, finalized_at=finalized_at
    )
    records = [attendance_record(session, student) for student in students]
    return session, records


# ---------------------------------------------------------------------------
# request helpers
# ---------------------------------------------------------------------------


def teacher_base(gpid):
    return f"/teacher/groups/{gpid}/attendance"


def teacher_detail(gpid, spid):
    return f"{teacher_base(gpid)}/{spid}"


def teacher_mark(gpid, spid):
    return f"{teacher_base(gpid)}/{spid}/mark"


def teacher_finalize(gpid, spid):
    return f"{teacher_base(gpid)}/{spid}/finalize"


def admin_detail(gpid, spid):
    return f"/admin/groups/{gpid}/attendance/{spid}"


def hidden_value(client, url, field):
    """Read a hidden input's value out of a rendered page.

    Deliberately reads the page the server actually rendered rather than
    minting a token in the test: a test that signed its own token would
    stop proving that the *page* carries a usable one.
    """
    html = client.get(url).get_data(as_text=True)
    return _extract_hidden(html, field)


def _extract_hidden(html, field):
    match = re.search(rf'name="{field}" value="([^"]*)"', html)
    return match.group(1) if match else ""


def create_session_via_routes(client, group_public_id, schedule_public_id, session_date):
    """Drive the real two-step create flow and return the final response.

    Step one posts the chooser and yields the confirmation page; step two
    posts that page's signed token. Used by tests that want a session the
    application itself produced rather than one written directly.
    """
    base = teacher_base(group_public_id)
    confirm_page = client.post(
        base + "/new",
        data={
            "schedule": schedule_public_id,
            "session_date": session_date.isoformat(),
        },
    )
    token = _extract_hidden(confirm_page.get_data(as_text=True), "attendance_state")
    return client.post(base + "/new/confirm", data={"attendance_state": token})


def marking_payload(client, url, statuses=None, notes=None, token=None):
    """Build a complete marking POST body from the page the server
    rendered, so the field names -- and the values already on the form --
    are the ones it really emitted.

    With no overrides this reproduces the form exactly as rendered, which
    is what makes a "save again with nothing changed" test a genuine
    no-op rather than an accidental edit.
    """
    page = client.get(url).get_data(as_text=True)
    # `token=""` is a deliberate "send an empty token", not "use the real
    # one" -- so the fallback is keyed on None, never on falsiness.
    payload = {
        "attendance_state": (
            _extract_hidden(page, "attendance_state") if token is None else token
        )
    }
    for public_id in sorted(set(re.findall(r'name="status__([^"]+)"', page))):
        checked = re.search(
            rf'name="status__{re.escape(public_id)}"\s+value="([^"]+)"\s+checked',
            page,
        )
        payload[f"status__{public_id}"] = (statuses or {}).get(
            public_id, checked.group(1) if checked else AttendanceStatus.ABSENT.value
        )
        payload[f"note__{public_id}"] = (notes or {}).get(
            public_id, rendered_note(page, public_id)
        )
    return payload


def rendered_note(page, public_id):
    """The note text a marking page currently shows for one record."""
    match = re.search(
        rf'name="note__{re.escape(public_id)}"[^>]*>(.*?)</textarea>', page, re.S
    )
    return html_module.unescape(match.group(1)) if match else ""


def record_public_ids(client, url):
    """The record public ids the marking page rendered, ascending."""
    html = client.get(url).get_data(as_text=True)
    return sorted(set(re.findall(r'name="status__([^"]+)"', html)))
