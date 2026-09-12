"""Shared fixtures for the Phase 4 / M09 Announcement test modules.

Kept in one module -- like ``tests/grade_fixtures.py``,
``tests/attendance_fixtures.py`` and ``tests/speaking_fixtures.py`` -- so
the model, teacher, student, administrator, notification, search and
migration suites all build the same academic chain and the same
announcements, and a change to what an announcement *is* cannot make
seven files disagree about it.

Most helpers write rows **directly**, so a test about (say) withdrawal
hiding a notice is not also a test of the publish form. The route-driven
helpers at the bottom exist for the tests that deliberately want the
application's own write path, and they read the signed tokens out of the
page the server actually rendered rather than minting one in the test --
a test that signed its own token would stop proving that the *page*
carries a usable one.
"""

import re
from datetime import date, datetime

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Announcement,
    AnnouncementScope,
    AnnouncementStatus,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password

PW = "Sup3rSecret!123"

#: The canonical reference instants every Announcement test works from, as
#: naive UTC. Whole seconds throughout: the columns hold whole seconds,
#: and every write decision is made in them.
NOW = datetime(2026, 5, 13, 9, 0, 0)
LATER = datetime(2026, 5, 13, 10, 30, 0)
LATEST = datetime(2026, 5, 13, 12, 0, 0)

TERM_START = date(2026, 1, 1)
TERM_END = date(2026, 12, 31)

_SEQUENCE = {"n": 0}


def _next():
    _SEQUENCE["n"] += 1
    return _SEQUENCE["n"]


class Clock:
    """A deterministic replacement for ``utc_reference_now``.

    Successive calls walk the supplied moments and then hold the last
    one, so a test can make the moment *after* the locks differ from the
    moment before them -- which is how the waited-behind-a-lock case is
    exercised.
    """

    def __init__(self, *moments):
        self.moments = list(moments) or [NOW]
        self.calls = 0

    def __call__(self, utc_now=None):
        moment = self.moments[min(self.calls, len(self.moments) - 1)]
        self.calls += 1
        return moment


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
    how "one archived ancestor hides the notice" is exercised."""
    tag = label
    t = term(tag, statuses.get("term_status", AcademicStatus.ACTIVE.value))
    lv = level(tag, statuses.get("level_status", AcademicStatus.ACTIVE.value))
    c = course(lv, tag, statuses.get("course_status", AcademicStatus.ACTIVE.value))
    return group(t, c, tag, statuses.get("group_status", AcademicStatus.ACTIVE.value))


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


def setup_group(label="A", teacher_email="teacher@example.com", **statuses):
    """One Group with one actively assigned Teacher."""
    owning_group = hierarchy(label, **statuses)
    teacher = assign(owning_group, teacher_email)
    return teacher, owning_group


# ---------------------------------------------------------------------------
# Announcements, written directly
# ---------------------------------------------------------------------------


def announcement(
    author,
    scope=AnnouncementScope.CENTER.value,
    target_course=None,
    target_group=None,
    title=None,
    body="This is the announcement body.",
    status=AnnouncementStatus.DRAFT.value,
    published_at=None,
    withdrawn_at=None,
    version=1,
    created_at=NOW,
    updated_at=None,
):
    """One announcement, written straight into the table.

    The two publication timestamps default to whatever the requested
    ``status`` requires, so a test that only cares about "a published
    centre notice" does not have to remember the CHECK's truth table --
    but either can still be supplied explicitly, which is how the
    constraint tests drive illegal shapes at the database on purpose.
    """
    if status == AnnouncementStatus.PUBLISHED.value and published_at is None:
        published_at = LATER
    if status == AnnouncementStatus.WITHDRAWN.value:
        if published_at is None:
            published_at = LATER
        if withdrawn_at is None:
            withdrawn_at = LATEST
    row = Announcement(
        author_id=author.id,
        scope=scope,
        course_id=None if target_course is None else target_course.id,
        group_id=None if target_group is None else target_group.id,
        title=title or f"Announcement {_next()}",
        body=body,
        status=status,
        published_at=published_at,
        withdrawn_at=withdrawn_at,
        version=version,
        created_at=created_at,
        updated_at=updated_at or created_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def center(author, **kwargs):
    return announcement(author, scope=AnnouncementScope.CENTER.value, **kwargs)


def for_course(author, target_course, **kwargs):
    return announcement(
        author,
        scope=AnnouncementScope.COURSE.value,
        target_course=target_course,
        **kwargs,
    )


def for_group(author, target_group, **kwargs):
    return announcement(
        author,
        scope=AnnouncementScope.GROUP.value,
        target_group=target_group,
        **kwargs,
    )


def published_center(author, **kwargs):
    return center(author, status=AnnouncementStatus.PUBLISHED.value, **kwargs)


def published_course(author, target_course, **kwargs):
    return for_course(
        author, target_course, status=AnnouncementStatus.PUBLISHED.value, **kwargs
    )


def published_group(author, target_group, **kwargs):
    return for_group(
        author, target_group, status=AnnouncementStatus.PUBLISHED.value, **kwargs
    )


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

STUDENT_FEED = "/student/announcements"
TEACHER_FEED = "/teacher/announcements"
ADMIN_OVERVIEW = "/admin/announcements"
ADMIN_NEW = "/admin/announcements/new"


def student_detail(ap):
    return f"/student/announcements/{ap}"


def teacher_detail(ap):
    return f"/teacher/announcements/{ap}"


def group_list(gp):
    return f"/teacher/groups/{gp}/announcements"


def group_new(gp):
    return f"/teacher/groups/{gp}/announcements/new"


def group_detail(gp, ap):
    return f"/teacher/groups/{gp}/announcements/{ap}"


def group_edit(gp, ap):
    return f"/teacher/groups/{gp}/announcements/{ap}/edit"


def group_publish(gp, ap):
    return f"/teacher/groups/{gp}/announcements/{ap}/publish"


def group_withdraw(gp, ap):
    return f"/teacher/groups/{gp}/announcements/{ap}/withdraw"


def admin_detail(ap):
    return f"/admin/announcements/{ap}"


def admin_edit(ap):
    return f"/admin/announcements/{ap}/edit"


def admin_publish(ap):
    return f"/admin/announcements/{ap}/publish"


def admin_withdraw(ap):
    return f"/admin/announcements/{ap}/withdraw"


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------


def extract_hidden(html, field="announcement_state"):
    match = re.search(rf'name="{field}" value="([^"]*)"', html)
    return match.group(1) if match else ""


def token_from(client, url, field="announcement_state"):
    """Read a hidden token's value out of the page the server actually
    rendered.

    Deliberately never mints one in the test: a test that signed its own
    token would stop proving that the *page* carries a usable one.
    """
    return extract_hidden(client.get(url).get_data(as_text=True), field)


def public_id_from_redirect(response):
    """The public id of the row a create route just redirected to."""
    return response.headers["Location"].rstrip("/").split("/")[-1]


def create_group_draft(client, gp, title="Room change", body="We move to room 4."):
    """Drive the Teacher create form the way a browser would, and return
    the new announcement's public id (or ``None`` if it was rejected)."""
    token = token_from(client, group_new(gp))
    response = client.post(
        group_new(gp),
        data={"title": title, "body": body, "announcement_state": token},
        follow_redirects=False,
    )
    if response.status_code != 302:
        return None
    return public_id_from_redirect(response)


def publish_group_draft(client, gp, ap):
    token = token_from(client, group_detail(gp, ap))
    return client.post(
        group_publish(gp, ap),
        data={"announcement_state": token},
        follow_redirects=False,
    )


def withdraw_group_announcement(client, gp, ap, confirm=True):
    token = token_from(client, group_detail(gp, ap))
    data = {"announcement_state": token}
    if confirm:
        data["confirm"] = "yes"
    return client.post(group_withdraw(gp, ap), data=data, follow_redirects=False)


def create_admin_draft(client, scope, target="", title="Holiday", body="Closed Monday."):
    """Drive the Administrator create form and return the new
    announcement's public id (or ``None`` if it was rejected)."""
    token = token_from(client, ADMIN_NEW)
    data = {
        "scope": scope,
        "course": target if scope == AnnouncementScope.COURSE.value else "",
        "group": target if scope == AnnouncementScope.GROUP.value else "",
        "title": title,
        "body": body,
        "announcement_state": token,
    }
    response = client.post(ADMIN_NEW, data=data, follow_redirects=False)
    if response.status_code != 302:
        return None
    public_id = public_id_from_redirect(response)
    return None if public_id == "new" else public_id


def publish_admin_draft(client, ap):
    token = token_from(client, admin_detail(ap))
    return client.post(
        admin_publish(ap), data={"announcement_state": token}, follow_redirects=False
    )


def withdraw_admin_announcement(client, ap, confirm=True):
    token = token_from(client, admin_detail(ap))
    data = {"announcement_state": token}
    if confirm:
        data["confirm"] = "yes"
    return client.post(admin_withdraw(ap), data=data, follow_redirects=False)
