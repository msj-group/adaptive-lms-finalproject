"""Shared fixtures for the Phase 4 / M10 calendar test modules.

Kept in one module -- like ``tests/announcement_fixtures.py``,
``tests/grade_fixtures.py``, ``tests/attendance_fixtures.py`` and
``tests/speaking_fixtures.py`` -- so the model, student, teacher,
administrator and migration suites all build the same academic chain,
the same sources and the same center events, and a change to what a
calendar entry *is* cannot make five files disagree about it.

Most helpers write rows **directly**, so a test about (say) a withdrawn
enrollment hiding a class is not also a test of the schedule form. The
route-driven helpers at the bottom exist for the tests that deliberately
want the application's own write path, and they read the signed tokens
out of the page the server actually rendered rather than minting one in
the test -- a test that signed its own token would stop proving that the
*page* carries a usable one.

**Time.** Every date here is a real 2026 civil date, and
:data:`REFERENCE_MONDAY` really is a Monday, so ``day_of_week`` values
mean what they say. The UTC instants are whole seconds throughout: the
columns hold whole seconds, and every write decision is made in them.

The project's ``APP_TIMEZONE`` is deliberately **not** UTC, and the test
suite runs under the real configured one, so a suite must never assume
that a stored instant and its rendered civil date coincide. Every UTC
instant here is chosen to sit well inside its civil day at any real
offset (mid-morning to late evening), and any assertion that depends on
*which* local day an instant lands on goes through
:func:`local_date` / :func:`local_clock`, which apply the project's own
:func:`~app.services.schedule_occurrences.to_app_local`. That is the
same arrangement ``tests/attendance_fixtures.py``,
``tests/speaking_fixtures.py`` and ``tests/test_teacher_assignments.py``
already use.
"""

import re
from datetime import date, datetime, time

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Assignment,
    AssignmentStatus,
    CalendarEvent,
    CalendarEventStatus,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    ListeningActivity,
    Quiz,
    QuizStatus,
    Schedule,
    SpeakingActivity,
    UploadedFile,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password
from tests.conftest import login_path

PW = "Sup3rSecret!123"

#: 2026-05-11 is a Monday, so ``day_of_week=0`` occurrences land on it.
REFERENCE_MONDAY = date(2026, 5, 11)

#: The month every calendar test looks at, and its two ends.
MONTH_START = date(2026, 5, 1)
MONTH_END = date(2026, 5, 31)

TERM_START = date(2026, 1, 1)
TERM_END = date(2026, 12, 31)

#: A naive-UTC "now" inside the month, used as the injected reference
#: moment wherever a Student's "has it opened yet?" gate matters.
NOW = datetime(2026, 5, 13, 9, 0, 0)

_SEQUENCE = {"n": 0}


def _next():
    _SEQUENCE["n"] += 1
    return _SEQUENCE["n"]


def local_date(app, moment):
    """The local civil **date** a stored naive-UTC instant falls on, via
    the project's own conversion rather than a second implementation."""
    from app.services.schedule_occurrences import to_app_local

    return to_app_local(app.config["APP_TIMEZONE"], moment).date()


def local_clock(app, moment):
    """The local ``HH:MM`` a stored naive-UTC instant renders as."""
    from app.services.schedule_occurrences import to_app_local

    return to_app_local(app.config["APP_TIMEZONE"], moment).strftime("%H:%M")


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


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
    the first account signed in -- and a test about a Teacher would then
    quietly be a test about the Student again.
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


# ---------------------------------------------------------------------------
# Academic chain and people
# ---------------------------------------------------------------------------


def user(email, role, status=UserStatus.ACTIVE.value, name=None):
    """One account, with its email normalised **exactly as the
    application normalises it** -- ``auth.login`` looks an account up by
    ``email.strip().lower()``."""
    row = User(
        email=email.strip().lower(),
        password_hash=hash_password(PW),
        full_name=name or email.split("@")[0],
        role=role,
        status=status,
    )
    db.session.add(row)
    db.session.commit()
    return row


def admin(email="admin@example.com", **kwargs):
    return user(email, UserRole.ADMINISTRATOR.value, **kwargs)


def level(label=None, status=AcademicStatus.ACTIVE.value):
    tag = _next()
    row = Level(name=f"Level {label or tag}", display_order=tag, status=status)
    db.session.add(row)
    db.session.commit()
    return row


def term(label=None, status=AcademicStatus.ACTIVE.value):
    tag = _next()
    row = AcademicTerm(
        name=f"Term {label or tag}",
        start_date=TERM_START,
        end_date=TERM_END,
        status=status,
    )
    db.session.add(row)
    db.session.commit()
    return row


def course(owning_level, label=None, status=AcademicStatus.ACTIVE.value):
    tag = _next()
    row = Course(
        title=f"Course {label or tag}",
        level_id=owning_level.id,
        display_order=tag,
        status=status,
    )
    db.session.add(row)
    db.session.commit()
    return row


def group(owning_term, owning_course, label=None, status=AcademicStatus.ACTIVE.value):
    tag = _next()
    row = Group(
        academic_term_id=owning_term.id,
        course_id=owning_course.id,
        name=f"Group {label or tag}",
        capacity=20,
        status=status,
    )
    db.session.add(row)
    db.session.commit()
    return row


def hierarchy(label="A", **statuses):
    """One complete ``Term -> Level -> Course -> Group`` chain, returned
    as the Group. Each link's status can be set independently, which is
    how "one archived ancestor hides the entry" is exercised."""
    t = term(label, statuses.get("term_status", AcademicStatus.ACTIVE.value))
    lv = level(label, statuses.get("level_status", AcademicStatus.ACTIVE.value))
    c = course(lv, label, statuses.get("course_status", AcademicStatus.ACTIVE.value))
    return group(t, c, label, statuses.get("group_status", AcademicStatus.ACTIVE.value))


def enroll(
    owning_group,
    email="student@example.com",
    status=EnrollmentStatus.ACTIVE.value,
    account_status=UserStatus.ACTIVE.value,
    name=None,
    role=UserRole.STUDENT.value,
):
    student = user(email, role, status=account_status, name=name)
    db.session.add(
        Enrollment(group_id=owning_group.id, student_id=student.id, status=status)
    )
    db.session.commit()
    return student


def enroll_existing(owning_group, student, status=EnrollmentStatus.ACTIVE.value):
    db.session.add(
        Enrollment(group_id=owning_group.id, student_id=student.id, status=status)
    )
    db.session.commit()


def assign(
    owning_group,
    email="teacher@example.com",
    status=GroupTeacherAssignmentStatus.ACTIVE.value,
    account_status=UserStatus.ACTIVE.value,
    name=None,
    role=UserRole.TEACHER.value,
):
    teacher = user(email, role, status=account_status, name=name)
    db.session.add(
        GroupTeacherAssignment(
            group_id=owning_group.id, teacher_id=teacher.id, status=status
        )
    )
    db.session.commit()
    return teacher


def assign_existing(
    owning_group, teacher, status=GroupTeacherAssignmentStatus.ACTIVE.value
):
    db.session.add(
        GroupTeacherAssignment(
            group_id=owning_group.id, teacher_id=teacher.id, status=status
        )
    )
    db.session.commit()


def withdraw_enrollment(owning_group, student):
    row = Enrollment.query.filter_by(
        group_id=owning_group.id, student_id=student.id
    ).one()
    row.status = EnrollmentStatus.WITHDRAWN.value
    db.session.commit()
    return row


def remove_assignment(owning_group, teacher):
    row = GroupTeacherAssignment.query.filter_by(
        group_id=owning_group.id, teacher_id=teacher.id
    ).one()
    row.status = GroupTeacherAssignmentStatus.REMOVED.value
    db.session.commit()
    return row


def archive(*rows):
    for row in rows:
        row.status = AcademicStatus.ARCHIVED.value
    db.session.commit()


def setup_group(label="A", **statuses):
    """One Group with one actively enrolled Student and one actively
    assigned Teacher. Returns ``(group, student, teacher)``."""
    owning_group = hierarchy(label, **statuses)
    student = enroll(owning_group, f"student.{label.lower()}@example.com")
    teacher = assign(owning_group, f"teacher.{label.lower()}@example.com")
    return owning_group, student, teacher


# ---------------------------------------------------------------------------
# Calendar sources, written directly
# ---------------------------------------------------------------------------


def schedule(
    owning_group,
    day_of_week=0,
    start_time=time(9, 0),
    end_time=time(11, 0),
    effective_start_date=MONTH_START,
    effective_end_date=MONTH_END,
    location="Room 4",
    status=AcademicStatus.ACTIVE.value,
):
    row = Schedule(
        group_id=owning_group.id,
        day_of_week=day_of_week,
        start_time=start_time,
        end_time=end_time,
        effective_start_date=effective_start_date,
        effective_end_date=effective_end_date,
        location=location,
        status=status,
    )
    db.session.add(row)
    db.session.commit()
    return row


def assignment(
    owning_group,
    title=None,
    opens_at=datetime(2026, 5, 4, 8, 0),
    due_at=datetime(2026, 5, 20, 20, 0),
    status=AssignmentStatus.PUBLISHED.value,
    published_at=datetime(2026, 5, 1, 8, 0),
):
    """One ordinary Assignment. ``published_at`` defaults to whatever the
    requested ``status`` requires, so a test that only cares about "a
    published assignment" does not have to remember the CHECK."""
    if status == AssignmentStatus.DRAFT.value:
        published_at = None
    row = Assignment(
        group_id=owning_group.id,
        title=title or f"Assignment {_next()}",
        instructions="Do the work.",
        opens_at=opens_at,
        due_at=due_at,
        status=status,
        published_at=published_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def speaking_assignment(owning_group, title=None, **kwargs):
    """One Assignment carrying the M06 Speaking extension -- which is
    therefore a *Speaking activity* and deliberately **not** a calendar
    source."""
    parent = assignment(owning_group, title=title or f"Speaking {_next()}", **kwargs)
    db.session.add(
        SpeakingActivity(assignment_id=parent.id, creation_nonce=f"spk-{_next()}")
    )
    db.session.commit()
    return parent


def quiz(
    owning_group,
    title=None,
    opens_at=datetime(2026, 5, 6, 8, 0),
    closes_at=datetime(2026, 5, 22, 18, 0),
    status=QuizStatus.PUBLISHED.value,
    published_at=datetime(2026, 5, 1, 8, 0),
):
    """One ordinary Quiz (no Listening extension)."""
    if status == QuizStatus.DRAFT.value:
        published_at = None
    row = Quiz(
        group_id=owning_group.id,
        title=title or f"Quiz {_next()}",
        instructions="Answer the questions.",
        status=status,
        opens_at=opens_at,
        closes_at=closes_at,
        attempt_limit=1,
        published_at=published_at,
        version=1,
    )
    db.session.add(row)
    db.session.commit()
    return row


def audio_upload(uploader):
    row = UploadedFile(
        storage_key=f"audio/{_next()}.mp3",
        original_filename="recording.mp3",
        extension="mp3",
        category="audio",
        content_type="audio/mpeg",
        byte_size=2048,
        sha256=f"{_next():064d}",
        uploaded_by_id=uploader.id,
    )
    db.session.add(row)
    db.session.commit()
    return row


def listening(owning_group, uploader, title=None, **kwargs):
    """One Listening activity: a Quiz plus the M05 extension row.

    Returns ``(quiz, activity)``. The activity's own ``public_id`` is
    what the Listening routes address, so it is what a calendar entry for
    one must carry.
    """
    backing = quiz(owning_group, title=title or f"Listening {_next()}", **kwargs)
    activity = ListeningActivity(
        quiz_id=backing.id,
        audio_file_id=audio_upload(uploader).id,
        creation_nonce=f"lis-{_next()}",
    )
    db.session.add(activity)
    db.session.commit()
    return backing, activity


def event(
    creator,
    title=None,
    details=None,
    event_date=date(2026, 5, 18),
    start_time=None,
    end_time=None,
    location=None,
    status=CalendarEventStatus.SCHEDULED.value,
    cancelled_at=None,
    version=1,
    created_at=NOW,
    updated_at=None,
):
    """One center calendar event, written straight into the table.

    ``cancelled_at`` defaults to whatever the requested ``status``
    requires, so a test that only cares about "a cancelled event" does
    not have to remember the CHECK's truth table -- but it can still be
    supplied explicitly, which is how the constraint tests drive illegal
    shapes at the database on purpose.
    """
    if status == CalendarEventStatus.CANCELLED.value and cancelled_at is None:
        cancelled_at = datetime(2026, 5, 14, 10, 0, 0)
    row = CalendarEvent(
        created_by_id=creator.id,
        title=title or f"Center event {_next()}",
        details=details,
        event_date=event_date,
        start_time=start_time,
        end_time=end_time,
        location=location,
        status=status,
        cancelled_at=cancelled_at,
        version=version,
        created_at=created_at,
        updated_at=updated_at or created_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def timed_event(creator, **kwargs):
    kwargs.setdefault("start_time", time(17, 0))
    kwargs.setdefault("end_time", time(19, 0))
    return event(creator, **kwargs)


def cancelled_event(creator, **kwargs):
    return event(creator, status=CalendarEventStatus.CANCELLED.value, **kwargs)


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

STUDENT_CALENDAR = "/student/calendar"
TEACHER_CALENDAR = "/teacher/calendar"
ADMIN_CALENDAR = "/admin/calendar"
ADMIN_EVENT_NEW = "/admin/calendar/events/new"

#: The canonical query string for the month every test looks at.
MONTH_QUERY = f"?from={MONTH_START.isoformat()}&to={MONTH_END.isoformat()}"


def student_month():
    return STUDENT_CALENDAR + MONTH_QUERY


def teacher_month():
    return TEACHER_CALENDAR + MONTH_QUERY


def admin_month():
    return ADMIN_CALENDAR + MONTH_QUERY


def admin_event_detail(ep):
    return f"/admin/calendar/events/{ep}"


def admin_event_edit(ep):
    return f"/admin/calendar/events/{ep}/edit"


def admin_event_cancel(ep):
    return f"/admin/calendar/events/{ep}/cancel"


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------


def extract_hidden(html, field="event_state"):
    match = re.search(rf'name="{field}" value="([^"]*)"', html)
    return match.group(1) if match else ""


def token_from(client, url, field="event_state"):
    """Read a hidden token's value out of the page the server actually
    rendered.

    Deliberately never mints one in the test: a test that signed its own
    token would stop proving that the *page* carries a usable one.
    """
    return extract_hidden(client.get(url).get_data(as_text=True), field)


def public_id_from_redirect(response):
    """The public id of the row a create route just redirected to."""
    return response.headers["Location"].rstrip("/").split("/")[-1]


def event_form_data(
    title="Staff meeting",
    details="Everyone attends.",
    event_date="2026-05-18",
    start_time="",
    end_time="",
    location="Main hall",
    token="",
):
    return {
        "title": title,
        "details": details,
        "event_date": event_date,
        "start_time": start_time,
        "end_time": end_time,
        "location": location,
        "event_state": token,
    }


def create_event(client, **overrides):
    """Drive the Administrator create form the way a browser would, and
    return the new event's public id (or ``None`` if it was rejected)."""
    token = token_from(client, ADMIN_EVENT_NEW)
    response = client.post(
        ADMIN_EVENT_NEW,
        data=event_form_data(token=token, **overrides),
        follow_redirects=False,
    )
    if response.status_code != 302:
        return None
    public_id = public_id_from_redirect(response)
    return None if public_id == "new" else public_id


def edit_event(client, ep, **overrides):
    token = token_from(client, admin_event_edit(ep))
    return client.post(
        admin_event_edit(ep),
        data=event_form_data(token=token, **overrides),
        follow_redirects=False,
    )


def cancel_event(client, ep, confirm=True, token=None):
    if token is None:
        token = token_from(client, admin_event_detail(ep))
    data = {"event_state": token}
    if confirm:
        data["confirm"] = "yes"
    return client.post(admin_event_cancel(ep), data=data, follow_redirects=False)


def titles_in(html):
    """Every calendar entry title the page rendered, in page order.

    Read out of the card titles the shared macro emits, so a test asserts
    about what a person actually sees rather than about a template
    internal.
    """
    return re.findall(
        r'<div class="card__title" style="margin-bottom: var\(--space-1\)">\s*'
        r'(?:<a href="[^"]*">)?([^<\n]+)',
        html,
    )


def labels_in(html):
    """Every source badge label the page rendered, in page order."""
    return re.findall(r'<span class="badge badge--[a-z]+">([^<]+)</span>', html)
