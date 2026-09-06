"""Student Assignment visibility (Phase 4 / M01): the SQL-scoped
effective-visibility formula, the time gate at ``opens_at``, past-due
persistence, bounded deterministic pagination, escaping, cache headers,
non-disclosure 404s, and the bounded dashboard deadline section.

Every Student read is gated on one injected reference moment, so these
tests move *time* rather than waiting: rows are written with explicit
naive-UTC windows around a fixed ``NOW``.
"""

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest
from sqlalchemy import event

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
    Schedule,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password
from app.services.assignment_queries import (
    DASHBOARD_DEADLINE_CAP,
    PAGE_SIZE,
    STATE_PAST_DUE,
    derived_state,
    student_assignment_detail,
    student_assignments_page,
    student_upcoming_deadlines,
)
from tests.conftest import login

PW = "Sup3rSecret!123"

#: The fixed naive-UTC reference every query in this module is given.
NOW = datetime(2026, 5, 10, 12, 0)

OPEN_WINDOW = (NOW - timedelta(days=2), NOW + timedelta(days=5))       # visible, open
FUTURE_WINDOW = (NOW + timedelta(days=1), NOW + timedelta(days=8))     # scheduled, hidden
PAST_WINDOW = (NOW - timedelta(days=9), NOW - timedelta(days=2))       # visible, past due


def _user(email, role, status=UserStatus.ACTIVE.value):
    u = User(email=email, password_hash=hash_password(PW),
             full_name=email.split("@")[0], role=role, status=status)
    db.session.add(u)
    db.session.commit()
    return u


def _hierarchy(term_status=AcademicStatus.ACTIVE.value, level_status=AcademicStatus.ACTIVE.value,
               course_status=AcademicStatus.ACTIVE.value, group_status=AcademicStatus.ACTIVE.value,
               group_name="Group A", course_title="English"):
    term = AcademicTerm(name=f"Term {group_name}", start_date=date(2026, 1, 1),
                        end_date=date(2026, 12, 31), status=term_status)
    level = Level(name=f"Level {group_name}", display_order=0, status=level_status)
    db.session.add_all([term, level])
    db.session.commit()
    course = Course(title=course_title, level_id=level.id, display_order=0, status=course_status)
    db.session.add(course)
    db.session.commit()
    group = Group(academic_term_id=term.id, course_id=course.id, name=group_name,
                  capacity=20, status=group_status)
    db.session.add(group)
    db.session.commit()
    return group


def _enroll(group, student, status=EnrollmentStatus.ACTIVE.value):
    row = Enrollment(student_id=student.id, group_id=group.id, status=status)
    db.session.add(row)
    db.session.commit()
    return row


def _assignment(group, title="Task", window=OPEN_WINDOW,
                status=AssignmentStatus.PUBLISHED.value, instructions="Do the work."):
    opens_at, due_at = window
    published_at = datetime(2026, 4, 1, 9, 0) if status == AssignmentStatus.PUBLISHED.value else None
    row = Assignment(group_id=group.id, title=title, instructions=instructions,
                     opens_at=opens_at, due_at=due_at, status=status,
                     published_at=published_at)
    db.session.add(row)
    db.session.commit()
    return row


def _setup(email="student@example.com", **hkw):
    """(student, group) with an active enrollment."""
    student = _user(email, UserRole.STUDENT.value)
    group = _hierarchy(**hkw)
    _enroll(group, student)
    return student, group


def _detail_url(gpid, apid):
    return f"/student/groups/{gpid}/assignments/{apid}"


# ---------------------------------------------------------------------------
# Structural public-id checks
#
# Searching the whole document for a numeric substring is unreliable in
# BOTH directions, so it is not used here:
#
# - false failure: `/assignments/2` matches the *prefix* of the UUID
#   `2e11d25f-...`, which happens for roughly one rendered id in sixteen;
# - false pass: it would equally miss a real numeric id sitting at the end
#   of a path, which is exactly the leak the assertion exists to catch.
#
# These helpers parse each rendered URL into path segments and prove
# *positively* that every identifier is a UUID public id.
# ---------------------------------------------------------------------------

_UUID_PATTERN = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)


def _is_uuid(value):
    """True only for a well-formed UUID -- shape-checked *and* parsed."""
    if not _UUID_PATTERN.match(value):
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _href_paths(html):
    """Every rendered `href`, reduced to its URL path (no query/fragment)."""
    return [urlsplit(href).path for href in re.findall(r'href="([^"]*)"', html)]


def _attr_values(html, attr):
    """Every value of `attr` rendered as a complete quoted attribute."""
    return re.findall(rf'{attr}="([^"]*)"', html)


def _assert_identifiers_are_public_uuids(html, internal_ids):
    """Prove structurally that every identifier this page renders is a
    UUID public id, and that no internal numeric id appears as an
    identifier anywhere.

    Rules, applied per rendered link after splitting its path:

    - **no path segment is a bare integer.** That single rule rules out
      an internal id anywhere in any URL, whichever id it might be, and
      it cannot be defeated by where in the path the id sits;
    - the segment following ``groups`` is a valid UUID;
    - the segment following ``assignments`` is a valid UUID -- unless
      ``assignments`` is the final segment, which is the
      ``/student/assignments`` collection route and carries no
      identifier;
    - no complete ``value="..."`` attribute equals an internal id.
    """
    forbidden = {str(i) for i in internal_ids}

    for path in _href_paths(html):
        segments = [seg for seg in path.split("/") if seg]
        for position, segment in enumerate(segments):
            assert not segment.isdigit(), (
                f"rendered path {path!r} carries a bare numeric segment {segment!r}"
            )
            if segment in ("groups", "assignments"):
                is_last = position == len(segments) - 1
                if segment == "assignments" and is_last:
                    continue  # the collection route -- no identifier follows
                assert not is_last, f"{segment!r} ends {path!r} with no identifier"
                identifier = segments[position + 1]
                assert _is_uuid(identifier), (
                    f"{path!r}: identifier after {segment!r} is {identifier!r}, "
                    "which is not a UUID public id"
                )
                assert identifier not in forbidden, (
                    f"{path!r} exposes internal id {identifier!r}"
                )

    for value in _attr_values(html, "value"):
        assert value not in forbidden, f'value="{value}" exposes an internal id'


# ===========================================================================
# Query layer -- the effective visibility formula, with injected time
# ===========================================================================


def _visible_titles(student_id, reference=NOW):
    rows, _ = student_assignments_page(student_id, reference, 1)
    return [row[0].title for row in rows]


def test_active_enrolled_student_sees_an_opened_published_assignment(app):
    with app.app_context():
        student, group = _setup()
        _assignment(group, title="Visible")
        assert _visible_titles(student.id) == ["Visible"]


def test_draft_is_never_disclosed(app):
    with app.app_context():
        student, group = _setup()
        _assignment(group, title="Draft", status=AssignmentStatus.DRAFT.value)
        assert _visible_titles(student.id) == []


def test_published_but_not_yet_open_is_never_disclosed(app):
    with app.app_context():
        student, group = _setup()
        _assignment(group, title="Scheduled", window=FUTURE_WINDOW)
        assert _visible_titles(student.id) == []


def test_becomes_visible_exactly_at_opens_at(app):
    """The gate is ``opens_at <= now``: invisible one second before,
    visible at the boundary itself."""
    with app.app_context():
        student, group = _setup()
        opens_at = NOW
        _assignment(group, title="Boundary", window=(opens_at, NOW + timedelta(days=1)))
        assert _visible_titles(student.id, opens_at - timedelta(seconds=1)) == []
        assert _visible_titles(student.id, opens_at) == ["Boundary"]


def test_stays_visible_at_and_after_due_at(app):
    """``due_at`` is informational in M01 -- it never withdraws the row."""
    with app.app_context():
        student, group = _setup()
        due_at = NOW
        _assignment(group, title="Deadline", window=(NOW - timedelta(days=1), due_at))
        assert _visible_titles(student.id, due_at - timedelta(seconds=1)) == ["Deadline"]
        assert _visible_titles(student.id, due_at) == ["Deadline"]
        assert _visible_titles(student.id, due_at + timedelta(days=365)) == ["Deadline"]


def test_withdrawn_enrollment_discloses_nothing(app):
    with app.app_context():
        student = _user("student@example.com", UserRole.STUDENT.value)
        group = _hierarchy()
        _enroll(group, student, status=EnrollmentStatus.WITHDRAWN.value)
        _assignment(group, title="Hidden")
        assert _visible_titles(student.id) == []


def test_absent_enrollment_discloses_nothing(app):
    with app.app_context():
        student = _user("student@example.com", UserRole.STUDENT.value)
        group = _hierarchy()
        _assignment(group, title="Hidden")
        assert _visible_titles(student.id) == []


def test_another_students_enrollment_grants_nothing(app):
    with app.app_context():
        enrolled, group = _setup("enrolled@example.com")
        outsider = _user("outsider@example.com", UserRole.STUDENT.value)
        _assignment(group, title="Theirs")
        assert _visible_titles(enrolled.id) == ["Theirs"]
        assert _visible_titles(outsider.id) == []


@pytest.mark.parametrize("archived", ["term", "level", "course", "group"])
def test_archived_ancestor_makes_it_unavailable_without_rewriting_the_row(app, archived):
    with app.app_context():
        student, group = _setup(**{f"{archived}_status": AcademicStatus.ARCHIVED.value})
        row = _assignment(group, title="Hidden")
        assert _visible_titles(student.id) == []
        # The Assignment row itself is untouched -- no cascade.
        db.session.refresh(row)
        assert row.status == AssignmentStatus.PUBLISHED.value
        assert row.published_at is not None


def test_a_non_student_account_row_receives_nothing(app):
    """A foreign key into ``users`` proves existence, never role. An
    Enrollment pointing at a Teacher account must still yield no rows."""
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy()
        _enroll(group, teacher)
        _assignment(group, title="Hidden")
        assert _visible_titles(teacher.id) == []


def test_a_suspended_student_account_receives_nothing(app):
    with app.app_context():
        student = _user("suspended@example.com", UserRole.STUDENT.value,
                        status=UserStatus.SUSPENDED.value)
        group = _hierarchy()
        _enroll(group, student)
        _assignment(group, title="Hidden")
        assert _visible_titles(student.id) == []


def test_visibility_does_not_require_a_schedule(app):
    with app.app_context():
        student, group = _setup()
        _assignment(group, title="No schedule")
        assert Schedule.query.count() == 0
        assert _visible_titles(student.id) == ["No schedule"]


def test_visibility_does_not_require_a_current_teacher_assignment(app):
    with app.app_context():
        student, group = _setup()
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        db.session.add(GroupTeacherAssignment(
            group_id=group.id, teacher_id=teacher.id,
            status=GroupTeacherAssignmentStatus.REMOVED.value,
        ))
        db.session.commit()
        _assignment(group, title="Still visible")
        assert _visible_titles(student.id) == ["Still visible"]


def test_open_rows_sort_by_nearest_deadline_first(app):
    with app.app_context():
        student, group = _setup()
        for days, title in ((5, "Later"), (1, "Sooner"), (3, "Middle")):
            _assignment(group, title=title,
                        window=(NOW - timedelta(days=1), NOW + timedelta(days=days)))
        assert _visible_titles(student.id) == ["Sooner", "Middle", "Later"]


def test_past_due_rows_sort_most_recent_first(app):
    with app.app_context():
        student, group = _setup()
        for days, title in ((5, "Oldest"), (1, "Newest"), (3, "Middle")):
            _assignment(group, title=title,
                        window=(NOW - timedelta(days=30), NOW - timedelta(days=days)))
        assert _visible_titles(student.id) == ["Newest", "Middle", "Oldest"]


def test_every_open_row_sorts_before_every_past_due_row(app):
    with app.app_context():
        student, group = _setup()
        # The past-due row has the *earliest* due_at of the three, so a
        # single `due_at ASC` sequence would have put it first.
        _assignment(group, title="History",
                    window=(NOW - timedelta(days=30), NOW - timedelta(days=10)))
        _assignment(group, title="Open late",
                    window=(NOW - timedelta(days=1), NOW + timedelta(days=9)))
        _assignment(group, title="Open soon",
                    window=(NOW - timedelta(days=1), NOW + timedelta(days=1)))
        assert _visible_titles(student.id) == ["Open soon", "Open late", "History"]


def test_a_full_page_of_history_cannot_push_open_work_off_page_one(app):
    """The defect this ordering exists to close: with at least PAGE_SIZE
    past-due rows, an approaching deadline used to land on page 2."""
    with app.app_context():
        student, group = _setup()
        for i in range(PAGE_SIZE + 5):
            _assignment(group, title=f"History {i:02d}",
                        window=(NOW - timedelta(days=400),
                                NOW - timedelta(days=i + 1)))
        _assignment(group, title="Due tomorrow",
                    window=(NOW - timedelta(days=1), NOW + timedelta(days=1)))
        page_one = _visible_titles(student.id)
        assert page_one[0] == "Due tomorrow"
        assert len(page_one) == PAGE_SIZE


def test_the_due_at_boundary_belongs_to_the_past_due_bucket(app):
    """``now == due_at`` is past due -- the same boundary the derived
    state uses, so the bucket and the badge can never disagree."""
    with app.app_context():
        student, group = _setup()
        _assignment(group, title="Exactly due",
                    window=(NOW - timedelta(days=1), NOW))
        _assignment(group, title="Still open",
                    window=(NOW - timedelta(days=1), NOW + timedelta(seconds=1)))
        assert _visible_titles(student.id) == ["Still open", "Exactly due"]
        assert derived_state(
            AssignmentStatus.PUBLISHED.value, NOW - timedelta(days=1), NOW, NOW
        ) == STATE_PAST_DUE


@pytest.mark.parametrize("bucket_offset", [timedelta(days=3), timedelta(days=-3)],
                         ids=["open", "past_due"])
def test_equal_deadlines_are_stable_across_repeated_reads(app, client, bucket_offset):
    """Two Assignments sharing a deadline keep one stable order in both
    buckets, page after page, via the ascending internal-id tie-break.

    The id orders the SQL only. That it never reaches a URL or the HTML is
    proven structurally in `test_no_internal_ids_appear_in_urls_or_html`,
    so this test does not restate that contract.
    """
    with app.app_context():
        student, group = _setup()
        shared_due = NOW + bucket_offset
        first = _assignment(group, title="Tie A",
                            window=(NOW - timedelta(days=10), shared_due))
        second = _assignment(group, title="Tie B",
                             window=(NOW - timedelta(days=10), shared_due))
        assert first.id < second.id  # the tie-break is ascending internal id
        assert _visible_titles(student.id) == ["Tie A", "Tie B"]
        # ... and repeating the read keeps the same order.
        assert _visible_titles(student.id) == ["Tie A", "Tie B"]
    login(client, "student@example.com")
    # The rendered order matches the query order, both times.
    for _ in range(2):
        html = client.get("/student/assignments").get_data(as_text=True)
        assert html.index("Tie A") < html.index("Tie B")


def test_the_bucketed_list_is_still_one_bounded_statement(app):
    """Bucketing happens in SQL -- it must not cost an extra query, a
    COUNT, or a broad fetch reordered in Python."""
    with app.app_context():
        student, group = _setup()
        for i in range(PAGE_SIZE + 5):
            _assignment(group, title=f"History {i:02d}",
                        window=(NOW - timedelta(days=400), NOW - timedelta(days=i + 1)))
        _assignment(group, title="Open",
                    window=(NOW - timedelta(days=1), NOW + timedelta(days=1)))
        student_id = student.id
        statements = []

        def _rec(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", _rec)
        try:
            rows, has_next = student_assignments_page(student_id, NOW, 1)
        finally:
            event.remove(db.engine, "before_cursor_execute", _rec)

        assert has_next is True and len(rows) == PAGE_SIZE
        assert len(statements) == 1, statements
        sql = statements[0].lower()
        assert "count(" not in sql
        assert "case when" in sql            # the bucketing is in the SQL
        assert "limit" in sql                # ... and so is the bound


def test_detail_requires_the_matching_group_public_id(app):
    with app.app_context():
        student, first = _setup()
        second = _hierarchy(group_name="Group B", course_title="Other")
        _enroll(second, student)
        row = _assignment(first, title="In first")
        assert student_assignment_detail(
            student.id, first.public_id, row.public_id, NOW
        ) is not None
        # Same Assignment, wrong Group -- no row.
        assert student_assignment_detail(
            student.id, second.public_id, row.public_id, NOW
        ) is None


# ===========================================================================
# Routes -- authorization and non-disclosure
# ===========================================================================


def test_anonymous_is_redirected_to_login(app, client):
    resp = client.get("/student/assignments", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


@pytest.mark.parametrize(
    "role", [UserRole.TEACHER.value, UserRole.ADMINISTRATOR.value, UserRole.RESEARCHER.value]
)
def test_non_student_roles_get_403(app, client, role):
    with app.app_context():
        _user(f"{role}@example.com", role)
    login(client, f"{role}@example.com")
    assert client.get("/student/assignments").status_code == 403


def test_list_and_detail_render_a_visible_assignment(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title="Visible", window=(datetime(2020, 1, 1), datetime(2099, 1, 1)))
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    listing = client.get("/student/assignments")
    assert listing.status_code == 200 and b"Visible" in listing.data
    detail = client.get(_detail_url(gpid, apid))
    assert detail.status_code == 200 and b"Do the work." in detail.data


def test_draft_detail_404s(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title="Draft", status=AssignmentStatus.DRAFT.value,
                          window=(datetime(2020, 1, 1), datetime(2099, 1, 1)))
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    assert client.get("/student/assignments").data.count(b"Draft") == 0
    assert client.get(_detail_url(gpid, apid)).status_code == 404


def test_not_yet_open_detail_404s(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title="Scheduled",
                          window=(datetime(2099, 1, 1), datetime(2099, 2, 1)))
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    assert b"Scheduled" not in client.get("/student/assignments").data
    assert client.get(_detail_url(gpid, apid)).status_code == 404


def test_withdrawn_enrollment_detail_404s(app, client):
    with app.app_context():
        student, group = _setup()
        row = _assignment(group, title="Gone", window=(datetime(2020, 1, 1), datetime(2099, 1, 1)))
        Enrollment.query.first().status = EnrollmentStatus.WITHDRAWN.value
        db.session.commit()
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    assert client.get(_detail_url(gpid, apid)).status_code == 404


@pytest.mark.parametrize("archived", ["term", "level", "course", "group"])
def test_archived_chain_detail_404s(app, client, archived):
    with app.app_context():
        _, group = _setup(**{f"{archived}_status": AcademicStatus.ARCHIVED.value})
        row = _assignment(group, title="Hidden", window=(datetime(2020, 1, 1), datetime(2099, 1, 1)))
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    assert client.get(_detail_url(gpid, apid)).status_code == 404


def test_every_unauthorized_shape_returns_the_same_404(app, client):
    with app.app_context():
        student, first = _setup()
        second = _hierarchy(group_name="Group B", course_title="Other")
        foreign = _assignment(second, title="Not mine",
                              window=(datetime(2020, 1, 1), datetime(2099, 1, 1)))
        mine = _assignment(first, title="Mine",
                           window=(datetime(2020, 1, 1), datetime(2099, 1, 1)))
        first_pid, second_pid = first.public_id, second.public_id
        foreign_pid, mine_pid = foreign.public_id, mine.public_id
    login(client, "student@example.com")
    codes = {
        # not enrolled in that Group at all
        client.get(_detail_url(second_pid, foreign_pid)).status_code,
        # a Group the Student IS in, but another Group's Assignment id
        client.get(_detail_url(first_pid, foreign_pid)).status_code,
        # own Assignment id under the wrong Group
        client.get(_detail_url(second_pid, mine_pid)).status_code,
        # nothing of the sort exists
        client.get(_detail_url(first_pid, "nope")).status_code,
        client.get(_detail_url("nope", "nope")).status_code,
    }
    assert codes == {404}


def test_no_internal_ids_appear_in_urls_or_html(app, client):
    """Every identifier the Student pages render is a UUID public id.

    Asserted positively -- each rendered URL is parsed and its identifier
    segments are required to *be* UUIDs -- rather than by hunting for the
    absence of a numeric substring, which is unreliable in both
    directions (see the helper's note).
    """
    with app.app_context():
        student, group = _setup()
        row = _assignment(group, title="Visible",
                          window=(datetime(2020, 1, 1), datetime(2099, 1, 1)))
        gpid, apid = group.public_id, row.public_id
        gid, aid, sid = group.id, row.id, student.id

    assert _is_uuid(gpid) and _is_uuid(apid)
    assert gpid != str(gid) and apid != str(aid)

    login(client, "student@example.com")
    list_html = client.get("/student/assignments").get_data(as_text=True)
    detail_html = client.get(_detail_url(gpid, apid)).get_data(as_text=True)

    for html in (list_html, detail_html):
        _assert_identifiers_are_public_uuids(html, {gid, aid, sid})

    # Positive: the pages really do render the expected public ids, and
    # the list really does link to the public-id detail URL -- so the
    # checks above ran against real identifier-bearing links rather than
    # vacuously passing on a page with none.
    list_paths = _href_paths(list_html)
    assert _detail_url(gpid, apid) in list_paths
    nested = [p for p in list_paths if "/assignments/" in p]
    assert nested, "the list rendered no nested Assignment link to validate"
    assert any(f"/groups/{gpid}/" in p for p in _href_paths(detail_html))


# ===========================================================================
# Escaping and safe rendering
# ===========================================================================


def test_malicious_title_and_instructions_stay_escaped(app, client):
    payload = "<script>alert('xss')</script>"
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title=f"T {payload}", instructions=f"I {payload}",
                          window=(datetime(2020, 1, 1), datetime(2099, 1, 1)))
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    for html in (
        client.get("/student/assignments").get_data(as_text=True),
        client.get(_detail_url(gpid, apid)).get_data(as_text=True),
    ):
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html


def test_instructions_preserve_line_breaks_without_safe(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title="T", instructions="Line one\nLine two",
                          window=(datetime(2020, 1, 1), datetime(2099, 1, 1)))
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    html = client.get(_detail_url(gpid, apid)).get_data(as_text=True)
    # Line breaks are preserved by CSS, not by injected <br> markup.
    assert "white-space: pre-wrap" in html
    assert "Line one\nLine two" in html
    assert "<br>" not in html.split("Instructions")[-1]


def test_templates_receive_plain_dicts_not_orm_rows(app, client):
    """Rendering must not be able to lazy-load: assert the view builders
    hand the template only plain values."""
    from app.services.assignment_queries import build_student_view

    with app.app_context():
        student, group = _setup()
        row = _assignment(group, title="T")
        internal_ids = {student.id, group.id, row.id}
        rows, _ = student_assignments_page(student.id, NOW, 1)
        view = build_student_view(rows, "UTC", NOW)
        assert all(isinstance(item, dict) for item in view)
        for item in view:
            assert all(
                not hasattr(value, "_sa_instance_state") for value in item.values()
            )
            assert "id" not in item  # only public ids escape the query layer
            # Stronger: no internal id reaches a template under ANY key.
            for key, value in item.items():
                assert value not in internal_ids, key
                if isinstance(value, str):
                    assert value not in {str(i) for i in internal_ids}, key


# ===========================================================================
# Pagination and cache headers
# ===========================================================================


def test_list_is_paginated_with_a_fixed_bound(app, client):
    with app.app_context():
        _, group = _setup()
        for i in range(PAGE_SIZE + 3):
            _assignment(group, title=f"Task {i:02d}",
                        window=(datetime(2020, 1, 1), datetime(2099, 1, 1) + timedelta(days=i)))
    login(client, "student@example.com")
    first = client.get("/student/assignments").get_data(as_text=True)
    assert first.count("<tr>") == PAGE_SIZE + 1  # + the header row
    assert "Next" in first
    second = client.get("/student/assignments?page=2").get_data(as_text=True)
    assert second.count("<tr>") == 3 + 1
    assert "Previous" in second


@pytest.mark.parametrize("value", ["0", "-4", "abc", "", "999999999", "1e9", "%20"])
def test_invalid_page_values_normalize_safely(app, client, value):
    with app.app_context():
        _, group = _setup()
        _assignment(group, title="Only", window=(datetime(2020, 1, 1), datetime(2099, 1, 1)))
    login(client, "student@example.com")
    resp = client.get(f"/student/assignments?page={value}")
    assert resp.status_code == 200
    assert b"Only" in resp.data


def test_page_past_the_end_falls_back_to_page_one(app, client):
    with app.app_context():
        _, group = _setup()
        _assignment(group, title="Only", window=(datetime(2020, 1, 1), datetime(2099, 1, 1)))
    login(client, "student@example.com")
    resp = client.get("/student/assignments?page=9")
    assert resp.status_code == 200 and b"Only" in resp.data and b"Previous" not in resp.data


def test_has_next_costs_no_count_query(app):
    """``limit + 1`` derives the flag, so no COUNT reaches the database."""
    with app.app_context():
        student, group = _setup()
        for i in range(PAGE_SIZE + 1):
            _assignment(group, title=f"T{i:02d}",
                        window=(datetime(2020, 1, 1), datetime(2099, 1, 1) + timedelta(days=i)))
        # Read the id BEFORE recording: the last commit expired `student`,
        # and refreshing it is an ORM artifact of the fixture, not part of
        # the query under test.
        student_id = student.id
        statements = []

        def _rec(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", _rec)
        try:
            rows, has_next = student_assignments_page(student_id, NOW, 1)
        finally:
            event.remove(db.engine, "before_cursor_execute", _rec)
        assert has_next is True and len(rows) == PAGE_SIZE
        assert len(statements) == 1
        assert "count(" not in statements[0].lower()


@pytest.mark.parametrize(
    "url_of",
    [
        lambda gpid, apid: "/student/assignments",
        lambda gpid, apid: f"/student/groups/{gpid}/assignments/{apid}",
    ],
    ids=["list", "detail"],
)
def test_responses_are_private_no_store_and_vary_on_cookie(app, client, url_of):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title="T", window=(datetime(2020, 1, 1), datetime(2099, 1, 1)))
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    resp = client.get(url_of(gpid, apid))
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in resp.headers["Vary"]


def test_list_page_states_the_real_ordering(app, client):
    with app.app_context():
        _, group = _setup()
        _assignment(group, title="T", window=(datetime(2020, 1, 1), datetime(2099, 1, 1)))
    login(client, "student@example.com")
    # The copy wraps across source lines, so compare on collapsed whitespace.
    text = " ".join(client.get("/student/assignments").get_data(as_text=True).split())
    assert "Open assignments are shown by nearest deadline." in text
    assert "Past-due assignments follow, most recent first." in text


def test_empty_state_is_explicit(app, client):
    with app.app_context():
        _setup()
    login(client, "student@example.com")
    assert b"No assignments yet" in client.get("/student/assignments").data


def test_portal_nav_carries_a_working_assignments_link(app, client):
    with app.app_context():
        _setup()
    login(client, "student@example.com")
    html = client.get("/student/dashboard").get_data(as_text=True)
    assert 'href="/student/assignments"' in html
    assert client.get("/student/assignments").status_code == 200


# ===========================================================================
# Dashboard "Upcoming assignment deadlines"
# ===========================================================================


def _deadline_titles(student_id, reference=NOW, cap=DASHBOARD_DEADLINE_CAP):
    return [row[0].title for row in student_upcoming_deadlines(student_id, reference, cap)]


def test_dashboard_shows_only_visible_not_yet_due_assignments(app):
    with app.app_context():
        student, group = _setup()
        _assignment(group, title="Open")                                   # counted
        _assignment(group, title="Draft", status=AssignmentStatus.DRAFT.value)
        _assignment(group, title="Scheduled", window=FUTURE_WINDOW)
        _assignment(group, title="Past due", window=PAST_WINDOW)
        assert _deadline_titles(student.id) == ["Open"]


def test_dashboard_excludes_withdrawn_and_archived_rows(app):
    with app.app_context():
        student, active = _setup()
        _assignment(active, title="Kept")

        withdrawn = _hierarchy(group_name="Withdrawn", course_title="C2")
        _enroll(withdrawn, student, status=EnrollmentStatus.WITHDRAWN.value)
        _assignment(withdrawn, title="Withdrawn row")

        archived = _hierarchy(group_name="Archived", course_title="C3",
                              group_status=AcademicStatus.ARCHIVED.value)
        _enroll(archived, student)
        _assignment(archived, title="Archived row")

        assert _deadline_titles(student.id) == ["Kept"]


def test_dashboard_orders_nearest_first_with_a_deterministic_tie_break(app):
    with app.app_context():
        student, group = _setup()
        shared = NOW + timedelta(days=4)
        first = _assignment(group, title="Tie A", window=(NOW - timedelta(days=1), shared))
        second = _assignment(group, title="Tie B", window=(NOW - timedelta(days=1), shared))
        _assignment(group, title="Sooner",
                    window=(NOW - timedelta(days=1), NOW + timedelta(days=2)))
        assert _deadline_titles(student.id) == ["Sooner", "Tie A", "Tie B"]
        assert first.id < second.id


def test_dashboard_respects_its_fixed_cap(app):
    with app.app_context():
        student, group = _setup()
        for i in range(DASHBOARD_DEADLINE_CAP + 4):
            _assignment(group, title=f"T{i:02d}",
                        window=(NOW - timedelta(days=1), NOW + timedelta(days=i + 1)))
        assert len(_deadline_titles(student.id)) == DASHBOARD_DEADLINE_CAP


def test_dashboard_section_links_to_the_nested_detail_route(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, title="Soon", window=(datetime(2020, 1, 1), datetime(2099, 1, 1)))
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    html = client.get("/student/dashboard").get_data(as_text=True)
    assert f'href="{_detail_url(gpid, apid)}"' in html
    assert client.get(_detail_url(gpid, apid)).status_code == 200


def test_dashboard_response_is_private_no_store_and_varies_on_cookie(app, client):
    """The dashboard now carries personal, time-gated Assignment data, so
    it needs the same cache protection as the two Assignment pages."""
    with app.app_context():
        _, group = _setup()
        _assignment(group, title="Soon", window=(datetime(2020, 1, 1), datetime(2099, 1, 1)))
    login(client, "student@example.com")
    resp = client.get("/student/dashboard")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in resp.headers["Vary"]


def test_dashboard_shows_an_honest_empty_state(app, client):
    with app.app_context():
        _, group = _setup()
        _assignment(group, title="Scheduled", window=(datetime(2099, 1, 1), datetime(2099, 2, 1)))
    login(client, "student@example.com")
    html = client.get("/student/dashboard").get_data(as_text=True)
    assert "No upcoming deadlines" in html
    assert "Scheduled" not in html


def test_dashboard_deadline_query_is_one_bounded_statement(app):
    with app.app_context():
        student, group = _setup()
        for i in range(12):
            _assignment(group, title=f"T{i:02d}",
                        window=(NOW - timedelta(days=1), NOW + timedelta(days=i + 1)))
        # See the note in test_has_next_costs_no_count_query.
        student_id = student.id
        statements = []

        def _rec(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", _rec)
        try:
            rows = student_upcoming_deadlines(student_id, NOW)
        finally:
            event.remove(db.engine, "before_cursor_execute", _rec)
        assert len(rows) == DASHBOARD_DEADLINE_CAP
        assert len(statements) == 1, statements


def test_dashboard_query_count_stays_bounded_with_many_groups(app, client):
    """No N+1: the deadline section costs one query no matter how many
    Groups or Assignments the Student has."""
    with app.app_context():
        student = _user("student@example.com", UserRole.STUDENT.value)
        for i in range(6):
            group = _hierarchy(group_name=f"G{i}", course_title=f"C{i}")
            _enroll(group, student)
            for j in range(4):
                _assignment(group, title=f"G{i} T{j}",
                            window=(datetime(2020, 1, 1), datetime(2099, 1, 1)))
    login(client, "student@example.com")

    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        resp = client.get("/student/dashboard")
    finally:
        event.remove(db.engine, "before_cursor_execute", _rec)

    assert resp.status_code == 200
    selects = [s for s in statements if s.strip().upper().startswith("SELECT")]
    # M09's bound was <= 7; the deadline section adds exactly one query.
    assert len(selects) <= 8, (len(selects), selects)


def test_existing_dashboard_schedule_behaviour_is_unchanged(app, client):
    """The M09 sections still render from the same injected local moment
    and are untouched by the new section."""
    with app.app_context():
        _, group = _setup()
        db.session.add(Schedule(
            group_id=group.id, day_of_week=2, start_time=__import__("datetime").time(9, 0),
            end_time=__import__("datetime").time(10, 30),
            effective_start_date=date(2026, 1, 1), effective_end_date=date(2026, 12, 31),
            location="Room 1", status=AcademicStatus.ACTIVE.value,
        ))
        db.session.commit()
    login(client, "student@example.com")
    html = client.get("/student/dashboard").get_data(as_text=True)
    assert "Next class" in html
    assert "Upcoming classes (next 7 days)" in html
    assert "Room 1" in html
    assert "Upcoming assignment deadlines" in html


def test_dashboard_reference_moment_is_shared_by_both_sections(app, client):
    """Both the local schedule clock and the UTC deadline gate come from
    one ``datetime.now`` call, so a request cannot straddle a deadline."""
    import app.blueprints.student.routes as mod

    calls = []
    real_now = datetime.now

    class _CountingDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            calls.append(tz)
            return real_now(tz)

    with app.app_context():
        _setup()
    login(client, "student@example.com")
    original = mod.datetime
    mod.datetime = _CountingDatetime
    try:
        assert client.get("/student/dashboard").status_code == 200
    finally:
        mod.datetime = original
    assert calls == [timezone.utc]
