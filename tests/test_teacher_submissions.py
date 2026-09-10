"""Teacher read-only submission views and the Assignment edit freeze
(Phase 4 / M02).

Two GET-only Teacher pages -- an Assignment's paginated submission list
and one submission's detail -- plus the rule that any submission history
at all freezes an Assignment's title, instructions and time window while
leaving publish/unpublish untouched.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
REPEATABLE READ snapshot isolation, so the structural tests here prove
only the *requested* lock order and the post-lock recheck logic -- never
that a real InnoDB lock blocks a concurrent transaction.
"""

import re
from datetime import date, datetime, timedelta
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest

import app.blueprints.teacher.assignments as teacher_mod
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
    Submission,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password
from app.services.assignment_queries import PAGE_SIZE
from tests.conftest import login

PW = "Sup3rSecret!123"

OPENS_UTC = datetime(2026, 5, 1, 6, 0)
DUE_UTC = datetime(2026, 5, 8, 21, 59)
SUBMITTED = datetime(2026, 5, 3, 10, 30)

#: Local wall-clock strings for the Teacher edit form (the form owns the
#: local -> UTC conversion; see tests/test_assignment_timezone.py).
OPENS_LOCAL = "2026-05-01T08:00"
DUE_LOCAL = "2026-05-08T23:59"

_UUID = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _user(email, role, status=UserStatus.ACTIVE.value, full_name=None):
    row = User(email=email, password_hash=hash_password(PW),
               full_name=full_name or email.split("@")[0], role=role, status=status)
    db.session.add(row)
    db.session.commit()
    return row


def _hierarchy(term_status=AcademicStatus.ACTIVE.value, level_status=AcademicStatus.ACTIVE.value,
               course_status=AcademicStatus.ACTIVE.value, group_status=AcademicStatus.ACTIVE.value,
               group_name="Group A"):
    term = AcademicTerm(name=f"Term {group_name}", start_date=date(2026, 1, 1),
                        end_date=date(2026, 12, 31), status=term_status)
    level = Level(name=f"Level {group_name}", display_order=0, status=level_status)
    db.session.add_all([term, level])
    db.session.commit()
    course = Course(title=f"English {group_name}", level_id=level.id, display_order=0,
                    status=course_status)
    db.session.add(course)
    db.session.commit()
    group = Group(academic_term_id=term.id, course_id=course.id, name=group_name,
                  capacity=20, status=group_status)
    db.session.add(group)
    db.session.commit()
    return group


def _assign(group, teacher, status=GroupTeacherAssignmentStatus.ACTIVE.value):
    row = GroupTeacherAssignment(group_id=group.id, teacher_id=teacher.id, status=status)
    db.session.add(row)
    db.session.commit()
    return row


def _assignment(group, title="Task 1", opens_at=OPENS_UTC, due_at=DUE_UTC,
                status=AssignmentStatus.PUBLISHED.value, instructions="Do the work."):
    published_at = datetime(2026, 4, 1, 9, 0) if status == AssignmentStatus.PUBLISHED.value else None
    row = Assignment(group_id=group.id, title=title, instructions=instructions,
                     opens_at=opens_at, due_at=due_at, status=status,
                     published_at=published_at)
    db.session.add(row)
    db.session.commit()
    return row


def _enrolled_student(group, email, status=EnrollmentStatus.ACTIVE.value,
                      account=UserStatus.ACTIVE.value, role=UserRole.STUDENT.value,
                      full_name=None):
    student = _user(email, role, status=account, full_name=full_name)
    db.session.add(Enrollment(student_id=student.id, group_id=group.id, status=status))
    db.session.commit()
    return student


def _submit(assignment, student, answer="An answer.", submitted_at=SUBMITTED):
    row = Submission(assignment_id=assignment.id, student_id=student.id,
                     answer_text=answer, submitted_at=submitted_at)
    db.session.add(row)
    db.session.commit()
    return row


def _setup(email="teacher@example.com", **hkw):
    """(teacher, group) with an active teaching assignment."""
    teacher = _user(email, UserRole.TEACHER.value)
    group = _hierarchy(**hkw)
    _assign(group, teacher)
    return teacher, group


def _list_url(gpid, apid):
    return f"/teacher/groups/{gpid}/assignments/{apid}/submissions"


def _detail_url(gpid, apid, spid):
    return f"{_list_url(gpid, apid)}/{spid}"


def _assignments_url(gpid):
    return f"/teacher/groups/{gpid}/assignments"


# ---------------------------------------------------------------------------
# Effective request identity
# ---------------------------------------------------------------------------
#
# The `app` fixture keeps ONE app context open for the whole test, and Flask
# reuses an already-pushed app context per test request instead of pushing a
# new one. `flask.g` therefore survives between requests -- including
# Flask-Login's `g._login_user` cache. Without clearing it, a second test
# client's request silently runs as the FIRST client's user, and every
# "two teachers" assertion passes vacuously. This is a fixture artifact, not
# application behaviour: in production each request gets its own app context.
# It is corrected here in the tests, never by changing authentication.


def _fresh_identity():
    """Drop Flask-Login's per-app-context user cache before a request."""
    from flask import g, has_app_context

    if has_app_context():
        g.pop("_login_user", None)


def _login_as(client, email):
    _fresh_identity()
    return login(client, email)


def _get(client, url):
    _fresh_identity()
    return client.get(url)


def _assert_authenticated_as(client, user_id):
    """The client really holds its OWN authenticated session cookie.

    User.get_id() is "<id>.<auth_version>", so the identity is the
    part before the dot; the version suffix is not what this asserts.
    """
    with client.session_transaction() as session:
        stored = session.get("_user_id")
        assert stored is not None, "the client is not authenticated at all"
        assert stored.split(".")[0] == str(user_id), stored


def _assert_anonymous(client):
    with client.session_transaction() as session:
        assert session.get("_user_id") is None, session.get("_user_id")


def _capture_actor_ids():
    """Record the EFFECTIVE authenticated user id *inside* each request.

    Wraps the object-authorization helper the nested Teacher routes call,
    so what is asserted is the identity the request actually ran as -- not
    merely that two client objects were constructed.
    """
    seen = []
    original = teacher_mod._teacher_group_or_404

    def spy(group_public_id):
        from flask_login import current_user

        seen.append(getattr(current_user, "id", None))
        return original(group_public_id)

    return seen, patch.object(teacher_mod, "_teacher_group_or_404", side_effect=spy)


def _get_edit_snapshot(client, gpid, apid):
    html = client.get(f"{_assignments_url(gpid)}/{apid}/edit").get_data(as_text=True)
    match = re.search(r'name="edit_snapshot" value="([^"]*)"', html)
    return match.group(1) if match else ""


def _edit(client, gpid, apid, snapshot, title="Renamed", instructions="Body",
          opens=OPENS_LOCAL, due=DUE_LOCAL, follow=True):
    return client.post(
        f"{_assignments_url(gpid)}/{apid}/edit",
        data={"title": title, "instructions": instructions, "opens_at": opens,
              "due_at": due, "edit_snapshot": snapshot},
        follow_redirects=follow,
    )


# ===========================================================================
# Authorization
# ===========================================================================


def test_an_actively_assigned_teacher_can_list_and_read(app, client):
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        student = _enrolled_student(group, "s@example.com", full_name="Sara Student")
        submission = _submit(assignment, student, answer="Her answer.")
        gpid, apid, spid = group.public_id, assignment.public_id, submission.public_id

    login(client, "teacher@example.com")
    listing = client.get(_list_url(gpid, apid))
    assert listing.status_code == 200
    assert "Sara Student" in listing.get_data(as_text=True)

    detail = client.get(_detail_url(gpid, apid, spid))
    assert detail.status_code == 200
    body = detail.get_data(as_text=True)
    assert "Her answer." in body and "Sara Student" in body


def test_all_active_co_teachers_have_equal_read_access(app, client):
    """Two DIFFERENT actively assigned Teachers can each list and read the
    same Submission.

    Asserted three ways, because the fixture artifact described above once
    made this test pass while every request ran as Teacher #1: each client
    holds its own authenticated session cookie, each request is observed
    from inside to have run as the expected user id, and an unassigned
    Teacher plus an anonymous client act as negative controls that cannot
    inherit the preceding client's identity.
    """
    with app.app_context():
        first, group = _setup()
        second = _user("co@example.com", UserRole.TEACHER.value)
        _assign(group, second)
        outsider = _user("outsider@example.com", UserRole.TEACHER.value)  # unassigned
        assignment = _assignment(group)
        student = _enrolled_student(group, "s@example.com", full_name="Sara Student")
        submission = _submit(assignment, student)
        gpid, apid, spid = group.public_id, assignment.public_id, submission.public_id
        first_id, second_id, outsider_id = first.id, second.id, outsider.id

    assert first_id != second_id != outsider_id

    seen, capture = _capture_actor_ids()
    with capture:
        for email, expected_id in (
            ("teacher@example.com", first_id),
            ("co@example.com", second_id),
        ):
            fresh = app.test_client()
            _login_as(fresh, email)
            _assert_authenticated_as(fresh, expected_id)
            assert _get(fresh, _list_url(gpid, apid)).status_code == 200
            assert _get(fresh, _detail_url(gpid, apid, spid)).status_code == 200

        # Negative control 1: an authenticated Teacher with no assignment
        # to this Group. It must run as ITSELF and be refused.
        stranger = app.test_client()
        _login_as(stranger, "outsider@example.com")
        _assert_authenticated_as(stranger, outsider_id)
        assert _get(stranger, _list_url(gpid, apid)).status_code == 404
        assert _get(stranger, _detail_url(gpid, apid, spid)).status_code == 404

        # Negative control 2: an anonymous client, which must not inherit
        # the identity of any client used above.
        anonymous = app.test_client()
        _assert_anonymous(anonymous)
        _fresh_identity()
        resp = anonymous.get(_list_url(gpid, apid))
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]
        _assert_anonymous(anonymous)

    # The identity each request ACTUALLY ran as. The anonymous request is
    # refused by the role guard before object authorization, so it
    # contributes no entry.
    assert seen == [
        first_id, first_id,
        second_id, second_id,
        outsider_id, outsider_id,
    ]


def test_anonymous_is_redirected_to_login(app, client):
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        gpid, apid = group.public_id, assignment.public_id
    resp = client.get(_list_url(gpid, apid), follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


@pytest.mark.parametrize(
    "role", [UserRole.STUDENT.value, UserRole.ADMINISTRATOR.value, UserRole.RESEARCHER.value]
)
def test_non_teacher_roles_get_403(app, client, role):
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        gpid, apid = group.public_id, assignment.public_id
        _user(f"{role}@example.com", role)
    login(client, f"{role}@example.com")
    assert client.get(_list_url(gpid, apid)).status_code == 403


@pytest.mark.parametrize(
    "state", ["unassigned", "removed"]
)
def test_a_teacher_without_an_active_assignment_gets_404(app, client, state):
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        student = _enrolled_student(group, "s@example.com")
        submission = _submit(assignment, student)
        gpid, apid, spid = group.public_id, assignment.public_id, submission.public_id
        outsider = _user("outsider@example.com", UserRole.TEACHER.value)
        if state == "removed":
            _assign(group, outsider, status=GroupTeacherAssignmentStatus.REMOVED.value)

    login(client, "outsider@example.com")
    assert client.get(_list_url(gpid, apid)).status_code == 404
    assert client.get(_detail_url(gpid, apid, spid)).status_code == 404


def test_a_teacher_removed_after_reading_loses_access(app, client):
    with app.app_context():
        teacher, group = _setup()
        assignment = _assignment(group)
        student = _enrolled_student(group, "s@example.com")
        _submit(assignment, student)
        gpid, apid, tid, gid = group.public_id, assignment.public_id, teacher.id, group.id
    login(client, "teacher@example.com")
    assert client.get(_list_url(gpid, apid)).status_code == 200

    with app.app_context():
        GroupTeacherAssignment.query.filter_by(group_id=gid, teacher_id=tid).one().status = (
            GroupTeacherAssignmentStatus.REMOVED.value
        )
        db.session.commit()
    assert client.get(_list_url(gpid, apid)).status_code == 404


def test_cross_group_and_cross_assignment_pairings_all_404(app, client):
    with app.app_context():
        teacher, first = _setup(group_name="First")
        second = _hierarchy(group_name="Second")
        _assign(second, teacher)
        mine = _assignment(first, title="Mine")
        other = _assignment(second, title="Other")
        student = _enrolled_student(first, "s@example.com")
        submission = _submit(mine, student)
        other_student = _enrolled_student(second, "s2@example.com")
        other_submission = _submit(other, other_student)

        first_pid, second_pid = first.public_id, second.public_id
        mine_pid, other_pid = mine.public_id, other.public_id
        spid, other_spid = submission.public_id, other_submission.public_id

    login(client, "teacher@example.com")
    # A Group the Teacher IS assigned to, but the other Group's Assignment.
    assert client.get(_list_url(first_pid, other_pid)).status_code == 404
    assert client.get(_list_url(second_pid, mine_pid)).status_code == 404
    # Right Group and Assignment, but a Submission from elsewhere.
    assert client.get(_detail_url(first_pid, mine_pid, other_spid)).status_code == 404
    assert client.get(_detail_url(second_pid, other_pid, spid)).status_code == 404
    assert client.get(_detail_url(first_pid, mine_pid, "nope")).status_code == 404
    # Positive control: the correct triple really does work, so the 404s
    # above are not passing vacuously.
    assert client.get(_detail_url(first_pid, mine_pid, spid)).status_code == 200


def test_the_two_submission_read_routes_are_still_get_only(app):
    """M02's two read pages never gained a write method. Phase 4 / M03
    added exactly one nested Teacher route under an Assignment's
    ``/submissions`` that accepts POST -- the feedback editor -- and
    nothing else did.

    Scoped to the **Assignment** surface (Phase 4 / M06). M06 introduced a
    parallel Speaking recording surface whose paths also contain
    ``/submissions``; it is a different aggregate with its own routes, and
    the test below asserts its shape separately rather than letting it
    silently widen this one.
    """
    rules = {
        r.endpoint: r for r in app.url_map.iter_rules()
        if "/assignments/" in str(r)
        and "/submissions" in str(r)
        and r.endpoint.startswith("teacher.")
    }
    assert set(rules) == {
        "teacher.assignment_submissions",
        "teacher.submission_detail",
        "teacher.submission_feedback",
    }
    for endpoint in ("teacher.assignment_submissions", "teacher.submission_detail"):
        assert rules[endpoint].methods & {"POST", "PUT", "PATCH", "DELETE"} == set()
    # The one writer accepts POST, and still never PUT/PATCH/DELETE:
    # feedback cannot be deleted in this milestone.
    feedback_methods = rules["teacher.submission_feedback"].methods
    assert "POST" in feedback_methods
    assert feedback_methods & {"PUT", "PATCH", "DELETE"} == set()


def test_the_speaking_recording_surface_has_the_same_shape(app):
    """Phase 4 / M06's parallel surface, asserted explicitly so the
    Assignment contract above stays exact rather than broadened: the two
    read routes are GET only, the feedback editor is the one writer, the
    two audio routes are GET only, and nothing anywhere accepts PUT, PATCH
    or DELETE."""
    rules = {
        r.endpoint: r for r in app.url_map.iter_rules()
        if "/speaking/" in str(r)
        and "/submissions" in str(r)
        and r.endpoint.startswith("teacher.")
    }
    assert set(rules) == {
        "teacher.speaking_submissions",
        "teacher.speaking_submission_detail",
        "teacher.speaking_submission_feedback",
        "teacher.speaking_submission_audio",
        "teacher.speaking_submission_audio_download",
    }
    read_only = set(rules) - {"teacher.speaking_submission_feedback"}
    for endpoint in read_only:
        assert rules[endpoint].methods & {"POST", "PUT", "PATCH", "DELETE"} == set()
    feedback_methods = rules["teacher.speaking_submission_feedback"].methods
    assert "POST" in feedback_methods
    assert feedback_methods & {"PUT", "PATCH", "DELETE"} == set()


# ===========================================================================
# History stays readable; current eligibility is not a filter
# ===========================================================================


@pytest.mark.parametrize("archived", ["term", "level", "course", "group"])
def test_history_stays_readable_under_an_archived_chain(app, client, archived):
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        student = _enrolled_student(group, "s@example.com", full_name="Sara Student")
        submission = _submit(assignment, student, answer="Archived-chain answer.")
        gpid, apid, spid, gid = (group.public_id, assignment.public_id,
                                 submission.public_id, group.id)
    with app.app_context():
        group = db.session.get(Group, gid)
        target = {"term": group.academic_term, "level": group.course.level,
                  "course": group.course, "group": group}[archived]
        target.status = AcademicStatus.ARCHIVED.value
        db.session.commit()

    login(client, "teacher@example.com")
    assert "Sara Student" in client.get(_list_url(gpid, apid)).get_data(as_text=True)
    assert "Archived-chain answer." in client.get(
        _detail_url(gpid, apid, spid)
    ).get_data(as_text=True)


def test_history_stays_readable_after_unpublishing_and_after_the_deadline(app, client):
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group, due_at=datetime(2020, 1, 1, 0, 0),
                                 opens_at=datetime(2019, 1, 1, 0, 0))
        student = _enrolled_student(group, "s@example.com", full_name="Sara Student")
        submission = _submit(assignment, student, answer="Long ago.",
                             submitted_at=datetime(2019, 6, 1, 9, 0))
        assignment.status = AssignmentStatus.DRAFT.value
        assignment.published_at = None
        db.session.commit()
        gpid, apid, spid = group.public_id, assignment.public_id, submission.public_id

    login(client, "teacher@example.com")
    assert "Sara Student" in client.get(_list_url(gpid, apid)).get_data(as_text=True)
    assert "Long ago." in client.get(_detail_url(gpid, apid, spid)).get_data(as_text=True)


@pytest.mark.parametrize("change", ["withdrawn", "suspended"])
def test_withdrawn_and_suspended_students_history_stays_readable(app, client, change):
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        student = _enrolled_student(
            group, "s@example.com", full_name="Sara Student",
            status=(EnrollmentStatus.WITHDRAWN.value if change == "withdrawn"
                    else EnrollmentStatus.ACTIVE.value),
            account=(UserStatus.SUSPENDED.value if change == "suspended"
                     else UserStatus.ACTIVE.value),
        )
        submission = _submit(assignment, student, answer="Still readable.")
        gpid, apid, spid = group.public_id, assignment.public_id, submission.public_id

    login(client, "teacher@example.com")
    assert "Sara Student" in client.get(_list_url(gpid, apid)).get_data(as_text=True)
    assert "Still readable." in client.get(
        _detail_url(gpid, apid, spid)
    ).get_data(as_text=True)


def test_a_row_whose_user_is_not_a_student_is_not_displayed(app, client):
    """Conditional role integrity: a foreign key into ``users`` proves the
    row exists, never that it is a Student's work."""
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        good = _enrolled_student(group, "s@example.com", full_name="Sara Student")
        broken = _user("broken@example.com", UserRole.TEACHER.value, full_name="Not A Student")
        _submit(assignment, good, answer="Genuine work.")
        bad = _submit(assignment, broken, answer="CORRUPTED ROW ANSWER.")
        gpid, apid = group.public_id, assignment.public_id
        good_spid, bad_spid = Submission.query.filter_by(
            student_id=good.id).one().public_id, bad.public_id

    login(client, "teacher@example.com")
    listing = client.get(_list_url(gpid, apid)).get_data(as_text=True)
    assert "Sara Student" in listing
    assert "Not A Student" not in listing
    assert client.get(_detail_url(gpid, apid, bad_spid)).status_code == 404
    # Positive control: the genuine row IS reachable, so the 404 above is
    # about the role and not about the URL shape.
    assert client.get(_detail_url(gpid, apid, good_spid)).status_code == 200


# ===========================================================================
# List behaviour: bounds, ordering, empty state, escaping, caching
# ===========================================================================


def test_the_empty_state_is_honest(app, client):
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        gpid, apid = group.public_id, assignment.public_id
    login(client, "teacher@example.com")
    html = client.get(_list_url(gpid, apid)).get_data(as_text=True)
    assert "No submissions yet" in html


def test_the_list_is_ordered_newest_first_with_a_deterministic_tie_break(app, client):
    from app.services.submission_queries import teacher_submissions_page

    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        # Two share a timestamp on purpose: MySQL's DATETIME is second
        # precision, so ties are ordinary and must still be stable.
        first = _submit(assignment, _enrolled_student(group, "a@example.com"),
                        submitted_at=SUBMITTED)
        second = _submit(assignment, _enrolled_student(group, "b@example.com"),
                         submitted_at=SUBMITTED)
        newest = _submit(assignment, _enrolled_student(group, "c@example.com"),
                         submitted_at=SUBMITTED + timedelta(hours=1))
        # The tie-break is `id DESC`, so the LATER-inserted of the two
        # tied rows comes first.
        assert second.id > first.id

        rows, has_next = teacher_submissions_page(assignment.id, 1)
        assert has_next is False
        # Rows are metadata projections, so identity is asserted on the
        # PUBLIC id -- the internal id orders the SQL and never leaves it.
        assert [row.public_id for row in rows] == [
            newest.public_id, second.public_id, first.public_id
        ]


def test_pagination_is_bounded_and_pages_deterministically(app, client):
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        for index in range(PAGE_SIZE + 3):
            _submit(assignment, _enrolled_student(group, f"s{index}@example.com",
                                                  full_name=f"Student {index:02d}"),
                    submitted_at=SUBMITTED + timedelta(minutes=index))
        gpid, apid = group.public_id, assignment.public_id

    login(client, "teacher@example.com")
    first = client.get(_list_url(gpid, apid)).get_data(as_text=True)
    second = client.get(f"{_list_url(gpid, apid)}?page=2").get_data(as_text=True)

    assert first.count("Read answer") == PAGE_SIZE
    assert second.count("Read answer") == 3
    assert "Next" in first and "Previous" in second
    # Newest first: the highest-numbered student leads page 1, and the
    # lowest trails page 2. No name appears on both pages.
    assert f"Student {PAGE_SIZE + 2:02d}" in first
    assert "Student 00" in second
    assert "Student 00" not in first


@pytest.mark.parametrize("value", ["0", "-3", "abc", "", "99999999"])
def test_out_of_range_page_values_fall_back_to_page_one(app, client, value):
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        student = _enrolled_student(group, "s@example.com", full_name="Sara Student")
        _submit(assignment, student)
        gpid, apid = group.public_id, assignment.public_id
    login(client, "teacher@example.com")
    resp = client.get(f"{_list_url(gpid, apid)}?page={value}")
    assert resp.status_code == 200
    assert "Sara Student" in resp.get_data(as_text=True)


def test_a_page_past_the_end_shows_page_one(app, client):
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        student = _enrolled_student(group, "s@example.com", full_name="Sara Student")
        _submit(assignment, student)
        gpid, apid = group.public_id, assignment.public_id
    login(client, "teacher@example.com")
    html = client.get(f"{_list_url(gpid, apid)}?page=9").get_data(as_text=True)
    assert "Sara Student" in html
    assert "Previous" not in html


def test_the_list_select_does_not_fetch_answer_bodies_or_user_secrets(app, client):
    """Query-level, not HTML-level: the list renders a name, a time and a
    link, so its SELECT must not pull ``answer_text`` or the whole
    ``users`` row along for every row of every page.

    Asserted against the statement the database actually executed, so a
    projection that merely *defers* a column (and would lazy-load it on
    first touch) cannot pass this.
    """
    from sqlalchemy import event

    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        _submit(assignment, _enrolled_student(group, "s@example.com"),
                answer="THE ANSWER BODY")
        gpid, apid = group.public_id, assignment.public_id

    login(client, "teacher@example.com")
    statements = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", record)
        try:
            assert client.get(_list_url(gpid, apid)).status_code == 200
        finally:
            event.remove(db.engine, "before_cursor_execute", record)

    listing = [st for st in statements if "FROM submissions" in st and "JOIN users" in st]
    assert len(listing) == 1, listing
    select_list = listing[0].split("FROM")[0]

    for forbidden in ("answer_text", "password_hash", "auth_version",
                      "users.email", "users.status", "users.role AS"):
        assert forbidden not in select_list, (forbidden, select_list)

    # Positive: it really does fetch exactly what the page renders, so the
    # assertions above are not passing on an empty or unrelated statement.
    for required in ("submissions.public_id", "submissions.submitted_at",
                     "users.full_name"):
        assert required in select_list, (required, select_list)

    # The answer body is never fetched anywhere during a list render.
    assert not any("answer_text" in st for st in statements), statements


def test_the_detail_select_fetches_the_answer_but_still_no_user_secrets(app, client):
    """The detail page is the only read that needs a body -- and it needs
    exactly one row's worth."""
    from sqlalchemy import event

    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        submission = _submit(assignment, _enrolled_student(group, "s@example.com"),
                             answer="THE ANSWER BODY")
        gpid, apid, spid = group.public_id, assignment.public_id, submission.public_id

    login(client, "teacher@example.com")
    statements = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", record)
        try:
            html = client.get(_detail_url(gpid, apid, spid)).get_data(as_text=True)
        finally:
            event.remove(db.engine, "before_cursor_execute", record)

    assert "THE ANSWER BODY" in html
    answer_reads = [st for st in statements if "answer_text" in st]
    assert len(answer_reads) == 1, answer_reads
    select_list = answer_reads[0].split("FROM")[0]
    for forbidden in ("password_hash", "auth_version", "users.email"):
        assert forbidden not in select_list, (forbidden, select_list)


def test_the_list_rows_carry_no_answer_and_no_internal_id(app, client):
    """The projection itself, at the service boundary."""
    from app.services.submission_queries import teacher_submissions_page

    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        _submit(assignment, _enrolled_student(group, "s@example.com", full_name="Sara"),
                answer="THE ANSWER BODY")

        rows, _has_next = teacher_submissions_page(assignment.id, 1)
        assert len(rows) == 1
        keys = set(rows[0]._mapping.keys())
        assert keys == {"public_id", "submitted_at", "student_name"}
        assert rows[0].student_name == "Sara"
        assert "answer_text" not in keys
        assert "id" not in keys


def test_the_list_carries_no_answer_bodies(app, client):
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        student = _enrolled_student(group, "s@example.com")
        _submit(assignment, student, answer="THE ANSWER BODY")
        gpid, apid = group.public_id, assignment.public_id
    login(client, "teacher@example.com")
    assert "THE ANSWER BODY" not in client.get(_list_url(gpid, apid)).get_data(as_text=True)


def test_a_malicious_answer_and_name_stay_escaped(app, client):
    payload = "<script>alert('xss')</script>"
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        student = _enrolled_student(group, "s@example.com", full_name=f"N {payload}")
        submission = _submit(assignment, student, answer=f"A {payload}")
        gpid, apid, spid = group.public_id, assignment.public_id, submission.public_id
    login(client, "teacher@example.com")
    for url in (_list_url(gpid, apid), _detail_url(gpid, apid, spid)):
        html = client.get(url).get_data(as_text=True)
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html


def test_answer_line_breaks_are_preserved_by_css_not_injected_markup(app, client):
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        student = _enrolled_student(group, "s@example.com")
        submission = _submit(assignment, student, answer="Line one\nLine two")
        gpid, apid, spid = group.public_id, assignment.public_id, submission.public_id
    login(client, "teacher@example.com")
    html = client.get(_detail_url(gpid, apid, spid)).get_data(as_text=True)
    assert "white-space: pre-wrap" in html
    assert "Line one\nLine two" in html
    assert "<br>" not in html.split("Answer")[-1]


def test_both_pages_are_private_no_store_and_vary_on_cookie(app, client):
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        student = _enrolled_student(group, "s@example.com")
        submission = _submit(assignment, student)
        gpid, apid, spid = group.public_id, assignment.public_id, submission.public_id
    login(client, "teacher@example.com")
    for url in (_list_url(gpid, apid), _detail_url(gpid, apid, spid)):
        resp = client.get(url)
        assert resp.headers["Cache-Control"] == "private, no-store"
        assert "Cookie" in resp.headers["Vary"]


def test_no_grade_delete_or_approval_control_is_rendered(app, client):
    """Both M02 read pages stay read-only. Phase 4 / M03 added a feedback
    *indicator* to the list and a feedback panel plus a LINK to the
    separate editor on the detail page -- neither page gained a form of
    its own, and neither gained a grade, score, approval, publish or
    delete control, because none of those exists server-side."""
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        student = _enrolled_student(group, "s@example.com")
        submission = _submit(assignment, student)
        gpid, apid, spid = group.public_id, assignment.public_id, submission.public_id
    login(client, "teacher@example.com")
    for url in (_list_url(gpid, apid), _detail_url(gpid, apid, spid)):
        html = client.get(url).get_data(as_text=True)
        # Still no form at all on either page beyond the shared header's
        # logout: the M03 editor is its own page, reached by a link.
        forms = re.findall(r'<form[^>]*action="([^"]*)"', html)
        assert all("logout" in action for action in forms), forms
        for word in ("Grade", "Score", "Approve", "Delete this", "Resubmit"):
            assert word not in html, word


def test_the_list_renders_only_public_uuid_identifiers(app, client):
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        student = _enrolled_student(group, "s@example.com")
        submission = _submit(assignment, student)
        gpid, apid = group.public_id, assignment.public_id
        forbidden = {str(i) for i in (group.id, assignment.id, student.id, submission.id)}
    login(client, "teacher@example.com")
    html = client.get(_list_url(gpid, apid)).get_data(as_text=True)

    checked = 0
    for href in re.findall(r'href="([^"]*)"', html):
        segments = [seg for seg in urlsplit(href).path.split("/") if seg]
        for position, segment in enumerate(segments):
            assert not segment.isdigit(), href
            if segment in ("groups", "assignments", "submissions") and position < len(segments) - 1:
                identifier = segments[position + 1]
                assert _UUID.match(identifier), (href, identifier)
                assert identifier not in forbidden
                checked += 1
    assert checked, "the page rendered no identifier-bearing link to validate"


def test_the_list_does_not_lazy_load_a_user_per_row(app, client):
    """The Student display name comes from the same joined statement, so
    the query count does not grow with the number of rows."""
    from sqlalchemy import event

    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        for index in range(10):
            _submit(assignment, _enrolled_student(group, f"s{index}@example.com"),
                    submitted_at=SUBMITTED + timedelta(minutes=index))
        gpid, apid = group.public_id, assignment.public_id

    login(client, "teacher@example.com")

    statements = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", record)
        try:
            assert client.get(_list_url(gpid, apid)).status_code == 200
        finally:
            event.remove(db.engine, "before_cursor_execute", record)

    submission_reads = [s for s in statements if "FROM submissions" in s]
    user_reads = [s for s in statements if "FROM users" in s and "submissions" not in s]
    assert len(submission_reads) == 1, submission_reads
    # The only standalone `users` read is Flask-Login's session user.
    assert len(user_reads) <= 1, user_reads


# ===========================================================================
# The Assignment list link and the freeze badge
# ===========================================================================


def test_the_assignment_list_links_to_each_assignments_submissions(app, client):
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        gpid, apid = group.public_id, assignment.public_id
    login(client, "teacher@example.com")
    html = client.get(_assignments_url(gpid)).get_data(as_text=True)
    assert f'href="{_list_url(gpid, apid)}"' in html


def test_the_assignment_list_replaces_edit_with_a_locked_badge_once_submitted(app, client):
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group)
        gpid, apid = group.public_id, assignment.public_id
    login(client, "teacher@example.com")

    before = client.get(_assignments_url(gpid)).get_data(as_text=True)
    assert f'href="{_assignments_url(gpid)}/{apid}/edit"' in before
    assert "Locked (submitted)" not in before

    with app.app_context():
        assignment = Assignment.query.one()
        _submit(assignment, _enrolled_student(Group.query.one(), "s@example.com"))

    after = client.get(_assignments_url(gpid)).get_data(as_text=True)
    assert f'href="{_assignments_url(gpid)}/{apid}/edit"' not in after
    assert "Locked (submitted)" in after


def test_the_freeze_flag_costs_one_query_for_a_whole_page(app, client):
    from sqlalchemy import event

    with app.app_context():
        _, group = _setup()
        for index in range(PAGE_SIZE):
            _assignment(group, title=f"Task {index:02d}",
                        due_at=DUE_UTC + timedelta(days=index))
        student = _enrolled_student(group, "s@example.com")
        _submit(Assignment.query.first(), student)
        gpid = group.public_id

    login(client, "teacher@example.com")
    statements = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", record)
        try:
            assert client.get(_assignments_url(gpid)).status_code == 200
        finally:
            event.remove(db.engine, "before_cursor_execute", record)

    submission_reads = [s for s in statements if "FROM submissions" in s]
    assert len(submission_reads) == 1, submission_reads


# ===========================================================================
# The Assignment edit freeze
# ===========================================================================


def test_editing_still_works_before_any_submission_exists(app, client):
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group, title="Original")
        gpid, apid = group.public_id, assignment.public_id
    login(client, "teacher@example.com")
    snapshot = _get_edit_snapshot(client, gpid, apid)
    assert snapshot, "the edit form was not offered at all"
    resp = _edit(client, gpid, apid, snapshot, title="Renamed")
    assert "updated" in resp.get_data(as_text=True).lower()
    with app.app_context():
        assert Assignment.query.one().title == "Renamed"


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "A different title"),
        ("instructions", "Completely different instructions."),
        ("opens_at", "2026-04-20T08:00"),
        ("due_at", "2026-06-30T23:00"),
    ],
)
def test_every_protected_field_is_frozen_after_the_first_submission(app, client, field, value):
    """The Teacher opens the edit form BEFORE the submission arrives, so
    the token is genuinely valid -- only the freeze stops the write."""
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group, title="Original", instructions="Original body.")
        gpid, apid, aid, gid = (group.public_id, assignment.public_id,
                                assignment.id, group.id)
    login(client, "teacher@example.com")
    snapshot = _get_edit_snapshot(client, gpid, apid)
    assert snapshot

    with app.app_context():
        student = _enrolled_student(db.session.get(Group, gid), "s@example.com")
        _submit(db.session.get(Assignment, aid), student)
        before = db.session.get(Assignment, aid)
        original = (before.title, before.instructions, before.opens_at,
                    before.due_at, before.status, before.published_at, before.updated_at)

    payload = {"title": "Original", "instructions": "Original body.",
               "opens": OPENS_LOCAL, "due": DUE_LOCAL}
    payload[{"title": "title", "instructions": "instructions",
             "opens_at": "opens", "due_at": "due"}[field]] = value
    resp = _edit(client, gpid, apid, snapshot, **payload)

    assert "student submissions" in resp.get_data(as_text=True)
    with app.app_context():
        after = db.session.get(Assignment, aid)
        assert (after.title, after.instructions, after.opens_at, after.due_at,
                after.status, after.published_at, after.updated_at) == original


def test_a_previously_opened_edit_form_cannot_bypass_the_freeze(app, client):
    """The whole point of the authoritative post-lock check: the form and
    its token were both minted while editing was legitimate."""
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group, title="Original")
        gpid, apid, aid, gid = (group.public_id, assignment.public_id,
                                assignment.id, group.id)
    login(client, "teacher@example.com")
    snapshot = _get_edit_snapshot(client, gpid, apid)

    with app.app_context():
        _submit(db.session.get(Assignment, aid),
                _enrolled_student(db.session.get(Group, gid), "s@example.com"))

    resp = _edit(client, gpid, apid, snapshot, title="Sneaked in")
    assert "student submissions" in resp.get_data(as_text=True)
    with app.app_context():
        assert Assignment.query.one().title == "Original"


def test_the_edit_form_itself_is_refused_once_submissions_exist(app, client):
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group, title="Original")
        _submit(assignment, _enrolled_student(group, "s@example.com"))
        gpid, apid = group.public_id, assignment.public_id
    login(client, "teacher@example.com")
    resp = client.get(f"{_assignments_url(gpid)}/{apid}/edit", follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert "student submissions" in html
    assert 'name="edit_snapshot"' not in html


@pytest.mark.parametrize("history", ["withdrawn", "suspended", "invalid_role"])
def test_historical_rows_freeze_regardless_of_current_eligibility(app, client, history):
    """The freeze asks whether the Assignment was ever answered, not
    whether the answerer is still eligible today."""
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group, title="Original")
        if history == "invalid_role":
            author = _user("broken@example.com", UserRole.TEACHER.value)
        else:
            author = _enrolled_student(
                group, "s@example.com",
                status=(EnrollmentStatus.WITHDRAWN.value if history == "withdrawn"
                        else EnrollmentStatus.ACTIVE.value),
                account=(UserStatus.SUSPENDED.value if history == "suspended"
                         else UserStatus.ACTIVE.value),
            )
        _submit(assignment, author)
        gpid, apid = group.public_id, assignment.public_id

    login(client, "teacher@example.com")
    resp = client.get(f"{_assignments_url(gpid)}/{apid}/edit", follow_redirects=True)
    assert "student submissions" in resp.get_data(as_text=True)
    with app.app_context():
        assert Assignment.query.one().title == "Original"


def test_an_unpublished_assignment_stays_frozen(app, client):
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group, title="Original")
        _submit(assignment, _enrolled_student(group, "s@example.com"))
        assignment.status = AssignmentStatus.DRAFT.value
        assignment.published_at = None
        db.session.commit()
        gpid, apid = group.public_id, assignment.public_id

    login(client, "teacher@example.com")
    resp = client.get(f"{_assignments_url(gpid)}/{apid}/edit", follow_redirects=True)
    assert "student submissions" in resp.get_data(as_text=True)


def test_a_submission_arriving_between_preview_and_lock_still_blocks_the_edit(app, client):
    """Serialization: the first submission and the Teacher edit both lock
    the same Group and the same Assignment row, so whichever commits
    first is what the other sees. Here the submission lands after the
    Teacher's pre-lock check has already passed."""
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group, title="Original")
        gpid, apid, aid, gid = (group.public_id, assignment.public_id,
                                assignment.id, group.id)
        student = _enrolled_student(group, "s@example.com")
        sid = student.id
    login(client, "teacher@example.com")
    snapshot = _get_edit_snapshot(client, gpid, apid)

    original = teacher_mod.lock_group_in_open_transaction

    def submit_then_lock(public_id):
        db.session.add(Submission(assignment_id=aid, student_id=sid,
                                  answer_text="Just in time.", submitted_at=SUBMITTED))
        db.session.commit()
        return original(public_id)

    with patch.object(teacher_mod, "lock_group_in_open_transaction",
                      side_effect=submit_then_lock):
        resp = _edit(client, gpid, apid, snapshot, title="Too late")

    assert "student submissions" in resp.get_data(as_text=True)
    with app.app_context():
        assert Assignment.query.one().title == "Original"
        assert Submission.query.count() == 1


def test_the_freeze_does_not_replace_the_stale_edit_snapshot(app, client):
    """Both protections still apply to an unfrozen Assignment."""
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group, title="Original")
        gpid, apid, aid = group.public_id, assignment.public_id, assignment.id
    login(client, "teacher@example.com")
    snapshot = _get_edit_snapshot(client, gpid, apid)

    with app.app_context():
        db.session.get(Assignment, aid).title = "Changed by a co-teacher"
        db.session.commit()

    resp = _edit(client, gpid, apid, snapshot, title="Mine")
    assert "was changed since this form was opened" in resp.get_data(as_text=True)
    with app.app_context():
        assert Assignment.query.one().title == "Changed by a co-teacher"


# ===========================================================================
# Publication lifecycle is untouched by the freeze
# ===========================================================================


@pytest.mark.parametrize("start", [AssignmentStatus.PUBLISHED.value,
                                   AssignmentStatus.DRAFT.value])
def test_publish_and_unpublish_still_work_and_keep_every_submission(app, client, start):
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group, title="Original", status=start)
        _submit(assignment, _enrolled_student(group, "s@example.com"),
                answer="Never removed.")
        gpid, apid = group.public_id, assignment.public_id

    login(client, "teacher@example.com")
    resp = client.post(
        f"{_assignments_url(gpid)}/{apid}/toggle-publication", follow_redirects=True
    )
    assert resp.status_code == 200
    with app.app_context():
        after = Assignment.query.one()
        expected = (AssignmentStatus.DRAFT.value if start == AssignmentStatus.PUBLISHED.value
                    else AssignmentStatus.PUBLISHED.value)
        assert after.status == expected
        assert Submission.query.one().answer_text == "Never removed."


def test_a_publication_toggle_keeps_the_assignment_frozen(app, client):
    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group, title="Original")
        _submit(assignment, _enrolled_student(group, "s@example.com"))
        gpid, apid = group.public_id, assignment.public_id

    login(client, "teacher@example.com")
    client.post(f"{_assignments_url(gpid)}/{apid}/toggle-publication", follow_redirects=True)
    client.post(f"{_assignments_url(gpid)}/{apid}/toggle-publication", follow_redirects=True)

    resp = client.get(f"{_assignments_url(gpid)}/{apid}/edit", follow_redirects=True)
    assert "student submissions" in resp.get_data(as_text=True)
    with app.app_context():
        assert Assignment.query.one().title == "Original"


def test_creating_a_new_assignment_is_not_blocked_by_another_ones_history(app, client):
    with app.app_context():
        _, group = _setup()
        frozen = _assignment(group, title="Frozen")
        _submit(frozen, _enrolled_student(group, "s@example.com"))
        gpid = group.public_id

    login(client, "teacher@example.com")
    resp = client.post(
        f"{_assignments_url(gpid)}/new",
        data={"title": "Brand new", "instructions": "Body",
              "opens_at": OPENS_LOCAL, "due_at": DUE_LOCAL},
        follow_redirects=True,
    )
    assert "created as a draft" in resp.get_data(as_text=True)
    with app.app_context():
        assert Assignment.query.filter_by(title="Brand new").count() == 1


def test_the_edit_freeze_check_runs_inside_the_locked_transaction(app, client):
    """Structural: when the submission arrives too late for the early
    check to see it, the edit still requests the canonical lock order
    under exactly one transaction reset, and the freeze is enforced by a
    current read taken while those locks are held.
    """
    from sqlalchemy.orm import Query

    with app.app_context():
        _, group = _setup()
        assignment = _assignment(group, title="Original")
        student = _enrolled_student(group, "s@example.com")
        gpid, apid, aid, sid = (group.public_id, assignment.public_id,
                                assignment.id, student.id)
    login(client, "teacher@example.com")
    snapshot = _get_edit_snapshot(client, gpid, apid)
    assert snapshot, "the edit form was not offered before the submission existed"

    events = []
    original_rollback = db.session.rollback
    original_wfu = Query.with_for_update
    original_lock = teacher_mod.lock_group_in_open_transaction

    def rollback_spy(*a, **k):
        events.append("reset")
        return original_rollback(*a, **k)

    def wfu_spy(self, *a, **k):
        entity = self.column_descriptions[0]["entity"] if self.column_descriptions else None
        events.append(f"lock:{getattr(entity, '__name__', '?')}")
        return original_wfu(self, *a, **k)

    def submit_then_lock(public_id):
        # Lands after the early check has already passed.
        db.session.add(Submission(assignment_id=aid, student_id=sid,
                                  answer_text="Just in time.", submitted_at=SUBMITTED))
        db.session.commit()
        return original_lock(public_id)

    with patch.object(db.session, "rollback", side_effect=rollback_spy), patch.object(
        Query, "with_for_update", wfu_spy
    ), patch.object(
        teacher_mod, "lock_group_in_open_transaction", side_effect=submit_then_lock
    ):
        resp = _edit(client, gpid, apid, snapshot, title="Blocked")

    assert "student submissions" in resp.get_data(as_text=True)
    assert [e for e in events if e.startswith("lock:")] == [
        "lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group",
        "lock:User", "lock:GroupTeacherAssignment", "lock:Assignment",
    ]
    # One deliberate reset to open the transaction; the second is the
    # rejection rolling it back without having written anything.
    assert events.count("reset") == 2
    assert events.index("reset") == 0
    with app.app_context():
        assert Assignment.query.one().title == "Original"
        assert Submission.query.count() == 1
