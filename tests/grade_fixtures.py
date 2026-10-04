"""Shared fixtures for the Phase 4 / M08 Gradebook test modules.

Kept in one module -- like ``tests/attendance_fixtures.py`` and
``tests/speaking_fixtures.py`` -- so the model, calculation, teacher,
student, administrator and migration suites all build the same academic
chain, the same categories and the same items, and a change to the shape
of a gradebook cannot make six files disagree about what one is.

Most helpers here write rows **directly**, so a test about (say) the
roster freeze is not also a test of the create form. The route-driven
helpers at the bottom exist for the tests that deliberately want the
application's own write path, and they read the tokens out of the page
the server actually rendered rather than minting one in the test -- a
test that signed its own token would stop proving that the *page* carries
a usable one.

**Numbers.** Every points value here is a ``Decimal`` constructed from a
string, never a float, for the same reason the columns are
``DECIMAL(7, 2)``: a test that wrote ``0.1`` as a binary float would be
testing something the application refuses to do.
"""

import re
from datetime import date, datetime
from decimal import Decimal

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Assignment,
    AssignmentStatus,
    Course,
    Enrollment,
    EnrollmentStatus,
    GradeCategory,
    GradeItem,
    GradeRecord,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    Quiz,
    QuizStatus,
    SpeakingActivity,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password
from tests.conftest import login_path

PW = "Sup3rSecret!123"

#: The canonical reference instant every Gradebook test works from, as
#: naive UTC. Whole seconds throughout: the columns hold whole seconds,
#: and every write decision is made in them.
NOW = datetime(2026, 5, 13, 9, 0, 0)
LATER = datetime(2026, 5, 13, 10, 30, 0)

TERM_START = date(2026, 1, 1)
TERM_END = date(2026, 12, 31)
OPENS_AT = datetime(2026, 2, 1, 8, 0, 0)
DUE_AT = datetime(2026, 11, 30, 20, 0, 0)


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


# ---------------------------------------------------------------------------
# Academic chain and people
# ---------------------------------------------------------------------------


def user(email, role, status=UserStatus.ACTIVE.value, name=None):
    """One account, with its email normalised **exactly as the
    application normalises it**.

    ``auth.login`` looks the account up by ``email.strip().lower()``, so
    a fixture that stored a mixed-case address would create an account
    nobody could ever log in as -- and the test would then be failing for
    a reason having nothing to do with grades.
    """
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


def withdraw(group, student):
    row = Enrollment.query.filter_by(group_id=group.id, student_id=student.id).one()
    row.status = EnrollmentStatus.WITHDRAWN.value
    db.session.commit()
    return row


# ---------------------------------------------------------------------------
# Gradeable sources
# ---------------------------------------------------------------------------


def assignment_for(group, title="Assignment 1", published=True, opens=OPENS_AT, due=DUE_AT):
    row = Assignment(
        group_id=group.id,
        title=title,
        instructions="Write something.",
        opens_at=opens,
        due_at=due,
        status=(
            AssignmentStatus.PUBLISHED.value if published else AssignmentStatus.DRAFT.value
        ),
        published_at=NOW if published else None,
    )
    db.session.add(row)
    db.session.commit()
    return row


def quiz_for(group, title="Quiz 1", published=True):
    row = Quiz(
        group_id=group.id,
        title=title,
        instructions="Answer the questions.",
        status=QuizStatus.PUBLISHED.value if published else QuizStatus.DRAFT.value,
        opens_at=OPENS_AT if published else None,
        closes_at=DUE_AT if published else None,
        attempt_limit=1,
        published_at=NOW if published else None,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    db.session.add(row)
    db.session.commit()
    return row


def speaking_for(group, title="Speaking 1", published=True, nonce=None):
    """One Speaking activity: an Assignment plus its extension row.

    The publication state lives on the parent Assignment -- the extension
    has none of its own -- which is exactly why the release check reads
    it there.
    """
    parent = assignment_for(group, title=title, published=published)
    activity = SpeakingActivity(
        assignment_id=parent.id,
        creation_nonce=nonce or f"nonce-{parent.id}-{title}",
        created_at=NOW,
        updated_at=NOW,
    )
    db.session.add(activity)
    db.session.commit()
    return activity


# ---------------------------------------------------------------------------
# Gradebook rows, written directly
# ---------------------------------------------------------------------------


def category(group, title="Homework", weight=10000, version=1, created_at=NOW):
    row = GradeCategory(
        group_id=group.id,
        title=title,
        weight_basis_points=weight,
        version=version,
        created_at=created_at,
        updated_at=created_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def grade_item(
    grade_category,
    title="Item 1",
    source_kind="manual",
    max_points="20.00",
    assignment=None,
    quiz=None,
    speaking=None,
    released_at=None,
    version=1,
    created_at=NOW,
):
    row = GradeItem(
        category_id=grade_category.id,
        title=title,
        source_kind=source_kind,
        max_points=Decimal(max_points),
        assignment_id=None if assignment is None else assignment.id,
        quiz_id=None if quiz is None else quiz.id,
        speaking_activity_id=None if speaking is None else speaking.id,
        released_at=released_at,
        version=version,
        created_at=created_at,
        updated_at=created_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def grade_record(
    item,
    student,
    score=None,
    comment=None,
    graded_by=None,
    graded_at=None,
    version=1,
    created_at=NOW,
):
    row = GradeRecord(
        grade_item_id=item.id,
        student_id=student.id,
        score=None if score is None else Decimal(score),
        comment=comment,
        graded_by_id=None if graded_by is None else graded_by.id,
        graded_at=graded_at if graded_by is None or graded_at else LATER,
        version=version,
        created_at=created_at,
        updated_at=created_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def item_with_roster(
    grade_category, students, scores=(), released=False, **kwargs
):
    """One item plus one record per Student -- the shape the create route
    produces.

    `scores` is applied positionally to `students`; a shorter list leaves
    the rest ungraded, which is the ordinary partially-graded draft.
    """
    item = grade_item(
        grade_category, released_at=LATER if released else None, **kwargs
    )
    records = []
    for index, student in enumerate(students):
        score = scores[index] if index < len(scores) else None
        records.append(grade_record(item, student, score=score))
    return item, records


def full_gradebook(label="A", teacher_email="teacher@example.com"):
    """A complete, releasable gradebook: one Group, one Teacher, two
    Students, two categories totalling exactly 100%, and one fully scored
    draft item in each.

    The shape most route tests start from, so each of them only has to
    change the one thing it is about.
    """
    teacher, group = setup_group(label, teacher_email=teacher_email)
    alice = enroll(group, f"alice-{label}@example.com", name=f"Alice {label}")
    bob = enroll(group, f"bob-{label}@example.com", name=f"Bob {label}")
    homework = category(group, "Homework", 6000)
    speaking = category(group, "Speaking", 4000)
    hw_item, hw_records = item_with_roster(
        homework, [alice, bob], scores=("18.00", "15.50"), title="HW 1"
    )
    sp_item, sp_records = item_with_roster(
        speaking, [alice, bob], scores=("30.00", "25.00"), title="Talk 1",
        max_points="40.00",
    )
    return {
        # Plain scalars only, deliberately: an ORM row read outside the
        # app context it was loaded in raises ``DetachedInstanceError``,
        # and a test that tripped over that would be failing for a reason
        # having nothing to do with grades. A test that needs a row
        # re-queries it by id inside its own context.
        "teacher_id": teacher.id,
        "teacher_email": teacher.email,
        "group_id": group.id,
        "group_public_id": group.public_id,
        "student_ids": [alice.id, bob.id],
        "student_emails": [alice.email, bob.email],
        "category_ids": [homework.id, speaking.id],
        "category_public_ids": [homework.public_id, speaking.public_id],
        "item_ids": [hw_item.id, sp_item.id],
        "item_public_ids": [hw_item.public_id, sp_item.public_id],
        "record_ids": [
            [r.id for r in hw_records],
            [r.id for r in sp_records],
        ],
        "record_public_ids": [
            [r.public_id for r in hw_records],
            [r.public_id for r in sp_records],
        ],
    }


# ---------------------------------------------------------------------------
# request helpers
# ---------------------------------------------------------------------------


def teacher_base(gpid):
    return f"/teacher/groups/{gpid}/gradebook"


def category_new(gpid):
    return f"{teacher_base(gpid)}/categories/new"


def category_edit(gpid, cpid):
    return f"{teacher_base(gpid)}/categories/{cpid}/edit"


def item_new(gpid):
    return f"{teacher_base(gpid)}/items/new"


def item_detail(gpid, ipid):
    return f"{teacher_base(gpid)}/items/{ipid}"


def item_edit(gpid, ipid):
    return f"{teacher_base(gpid)}/items/{ipid}/edit"


def item_scores(gpid, ipid):
    return f"{teacher_base(gpid)}/items/{ipid}/scores"


def item_release(gpid, ipid):
    return f"{teacher_base(gpid)}/items/{ipid}/release"


def student_detail(gpid):
    return f"/student/grades/{gpid}"


def admin_detail(gpid):
    return f"/admin/groups/{gpid}/gradebook"


def extract_hidden(html, field="grade_state"):
    match = re.search(rf'name="{field}" value="([^"]*)"', html)
    return match.group(1) if match else ""


def token_from(client, url, field="grade_state"):
    """Read a hidden token's value out of the page the server actually
    rendered.

    Deliberately never mints one in the test: a test that signed its own
    token would stop proving that the *page* carries a usable one.
    """
    return extract_hidden(client.get(url).get_data(as_text=True), field)


def category_payload(client, gpid, title, weight, cpid=None, token=None):
    """A complete category create/edit POST body, with the token read
    from the rendered form unless one is supplied.

    ``token=""`` is a deliberate "send an empty token", not "use the real
    one" -- so the fallback is keyed on ``None``, never on falsiness.
    """
    url = category_new(gpid) if cpid is None else category_edit(gpid, cpid)
    return {
        "title": title,
        "weight": weight,
        "grade_state": token_from(client, url) if token is None else token,
    }


def item_payload(
    client,
    gpid,
    category_public_id,
    title="New item",
    source_kind="manual",
    max_points="20",
    assignment_source="",
    quiz_source="",
    speaking_source="",
    ipid=None,
    token=None,
):
    url = item_new(gpid) if ipid is None else item_edit(gpid, ipid)
    return {
        "category": category_public_id,
        "title": title,
        "source_kind": source_kind,
        "assignment_source": assignment_source,
        "quiz_source": quiz_source,
        "speaking_source": speaking_source,
        "max_points": max_points,
        "grade_state": token_from(client, url) if token is None else token,
    }


def record_public_ids(client, url):
    """The record public ids the score sheet rendered, ascending."""
    html = client.get(url).get_data(as_text=True)
    return sorted(set(re.findall(r'name="score__([0-9a-f-]{36})"', html)))


def rendered_comment(html, public_id):
    """The comment text a score sheet currently shows for one record."""
    import html as html_module

    match = re.search(
        rf'name="comment__{re.escape(public_id)}"[^>]*>(.*?)</textarea>', html, re.S
    )
    return html_module.unescape(match.group(1)) if match else ""


def rendered_score(html, public_id):
    match = re.search(
        rf'name="score__{re.escape(public_id)}"\s+value="([^"]*)"', html
    )
    return match.group(1) if match else ""


def score_payload(client, url, scores=None, comments=None, token=None):
    """Build a complete score-sheet POST body from the page the server
    rendered, so the field names -- and the values already on the form --
    are the ones it really emitted.

    With no overrides this reproduces the sheet exactly as rendered,
    which is what makes a "save again with nothing changed" test a
    genuine no-op rather than an accidental edit.
    """
    page = client.get(url).get_data(as_text=True)
    payload = {
        "grade_state": extract_hidden(page) if token is None else token
    }
    for public_id in sorted(set(re.findall(r'name="score__([0-9a-f-]{36})"', page))):
        payload[f"score__{public_id}"] = (scores or {}).get(
            public_id, rendered_score(page, public_id)
        )
        payload[f"comment__{public_id}"] = (comments or {}).get(
            public_id, rendered_comment(page, public_id)
        )
    return payload


def release_via_route(client, gpid, ipid):
    """Drive the real release flow: read the token off the detail page,
    then POST it with the confirmation ticked."""
    token = token_from(client, item_detail(gpid, ipid))
    return client.post(
        item_release(gpid, ipid),
        data={"grade_state": token, "confirm_release": "yes"},
        follow_redirects=True,
    )
