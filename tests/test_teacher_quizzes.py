"""Teacher quiz drafts (Phase 4 / M04A): nested authorization,
draft-only semantics, bounded pagination, the signed version-bound edit
token, no-op saves, the lock order and single reset, post-lock rechecks,
``IntegrityError`` recovery, CSRF / method / cache-header behaviour, and
non-disclosure 404s.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
REPEATABLE READ snapshot isolation, so the concurrency tests here are
**structural**: they assert the *requested* reset and lock order and
exercise the post-lock recheck logic by injecting a state change at a
chosen transaction boundary. They are **not** a demonstration of real
InnoDB blocking. Time is injected rather than waited for, so "two edits
inside one whole second" is exact rather than probabilistic.

M04C now provides the Alembic revision for the accepted quiz aggregate.
These route tests still use SQLite and do not claim real MySQL execution.
"""

import re
from datetime import date, datetime
from unittest.mock import patch

import pytest

from app.blueprints.teacher import quizzes as quizzes_mod
from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Course,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    Level,
    Quiz,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password
from tests.conftest import login

PW = "Sup3rSecret!123"

NOW = datetime(2026, 5, 10, 9, 0, 0)
LATER = datetime(2026, 5, 11, 14, 30, 0)
#: Deliberately inside the SAME whole second as NOW, to prove `version`
#: -- not a timestamp -- is what separates two edits.
SAME_SECOND = datetime(2026, 5, 10, 9, 0, 0)


class _Clock:
    """A deterministic replacement for ``utc_reference_now``.

    Yields each supplied moment in turn and then repeats the last one, so
    "a co-teacher committed while this request waited on a lock" is an
    exact scenario rather than a race.
    """

    def __init__(self, *moments):
        self.moments = list(moments) or [NOW]
        self.calls = 0

    def __call__(self, utc_now=None):
        moment = self.moments[min(self.calls, len(self.moments) - 1)]
        self.calls += 1
        return moment


def _at(*moments):
    """Patch the quiz route module's clock for a ``with`` block."""
    return patch.object(quizzes_mod, "utc_reference_now", _Clock(*moments))


# ---------------------------------------------------------------------------
# identity helpers
# ---------------------------------------------------------------------------


def _fresh_identity():
    """Drop Flask-Login's per-app-context user cache.

    The ``app`` fixture keeps ONE app context open for the whole test, and
    Flask reuses an already-pushed app context rather than pushing a new
    one per test request -- so ``flask.g``, where Flask-Login caches the
    loaded user (``g._login_user``), survives from one request to the
    next. Without this, a second test client's request would silently run
    as the FIRST client's user, and every "two co-teachers" test would
    pass vacuously as one teacher acting twice.

    This is a harness artifact of the shared in-memory SQLite fixture, not
    application behaviour: in production each request gets its own app
    context and therefore its own ``g``.
    """
    from flask import g, has_app_context

    if has_app_context():
        g.pop("_login_user", None)


def _login_as(client, email):
    _fresh_identity()
    return login(client, email)


def _assert_authenticated_as(client, user_id):
    """The client really holds its OWN authenticated session cookie.

    ``User.get_id()`` is ``"<id>.<auth_version>"``, so the identity is the
    part before the dot. Without this, a "two co-teachers" scenario can
    silently degrade into one teacher acting twice.
    """
    with client.session_transaction() as session:
        assert session.get("_user_id", "").split(".")[0] == str(user_id)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _user(email, role, status=UserStatus.ACTIVE.value, full_name=None):
    row = User(
        email=email,
        password_hash=hash_password(PW),
        full_name=full_name or email.split("@")[0],
        role=role,
        status=status,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _hierarchy(
    term_status=AcademicStatus.ACTIVE.value,
    level_status=AcademicStatus.ACTIVE.value,
    course_status=AcademicStatus.ACTIVE.value,
    group_status=AcademicStatus.ACTIVE.value,
    group_name="Group A",
    course_title="English",
):
    term = AcademicTerm(
        name=f"Term {group_name}",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        status=term_status,
    )
    level = Level(name=f"Level {group_name}", display_order=0, status=level_status)
    db.session.add_all([term, level])
    db.session.commit()
    course = Course(
        title=course_title, level_id=level.id, display_order=0, status=course_status
    )
    db.session.add(course)
    db.session.commit()
    group = Group(
        academic_term_id=term.id,
        course_id=course.id,
        name=group_name,
        capacity=20,
        status=group_status,
    )
    db.session.add(group)
    db.session.commit()
    return group


def _assign(group, teacher, status=GroupTeacherAssignmentStatus.ACTIVE.value):
    row = GroupTeacherAssignment(
        group_id=group.id, teacher_id=teacher.id, status=status
    )
    db.session.add(row)
    db.session.commit()
    return row


def _quiz_row(
    group,
    title="Unit 1 check",
    instructions="Answer every question.",
    version=1,
    created_at=NOW,
    updated_at=NOW,
):
    row = Quiz(
        group_id=group.id,
        title=title,
        instructions=instructions,
        version=version,
        created_at=created_at,
        updated_at=updated_at,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _setup(email="teacher@example.com", **hkw):
    """(teacher, group) with an active teaching assignment."""
    teacher = _user(email, UserRole.TEACHER.value)
    group = _hierarchy(**hkw)
    _assign(group, teacher)
    return teacher, group


# ---------------------------------------------------------------------------
# request helpers
# ---------------------------------------------------------------------------


def _url(gpid):
    return f"/teacher/groups/{gpid}/quizzes"


def _detail_url(gpid, qpid):
    return f"{_url(gpid)}/{qpid}"


def _edit_url(gpid, qpid):
    return f"{_url(gpid)}/{qpid}/edit"


def _create(
    client, gpid, title="Draft A", instructions="Read carefully.", follow=True, **extra
):
    data = {"title": title, "instructions": instructions}
    data.update(extra)
    return client.post(f"{_url(gpid)}/new", data=data, follow_redirects=follow)


def _get_edit_token(client, gpid, qpid):
    html = client.get(_edit_url(gpid, qpid)).get_data(as_text=True)
    match = re.search(r'name="edit_state" value="([^"]*)"', html)
    return match.group(1) if match else ""


def _edit(
    client, gpid, qpid, token, title="Renamed", instructions="Rewritten.", follow=True, **extra
):
    data = {"title": title, "instructions": instructions, "edit_state": token}
    data.update(extra)
    return client.post(_edit_url(gpid, qpid), data=data, follow_redirects=follow)


# ===========================================================================
# Authorization -- role boundary
# ===========================================================================


def test_anonymous_is_redirected_to_login(app, client):
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group)
        gpid, qpid = group.public_id, quiz.public_id
    for url in (_url(gpid), f"{_url(gpid)}/new", _detail_url(gpid, qpid), _edit_url(gpid, qpid)):
        resp = client.get(url)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]


@pytest.mark.parametrize(
    "role",
    [UserRole.STUDENT.value, UserRole.ADMINISTRATOR.value, UserRole.RESEARCHER.value],
)
def test_non_teacher_roles_are_forbidden(app, client, role):
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group)
        gpid, qpid = group.public_id, quiz.public_id
        _user("other@example.com", role)
    _login_as(client, "other@example.com")
    for url in (_url(gpid), f"{_url(gpid)}/new", _detail_url(gpid, qpid), _edit_url(gpid, qpid)):
        assert client.get(url).status_code == 403
    assert client.post(f"{_url(gpid)}/new", data={"title": "X", "instructions": "Y"}).status_code == 403
    with app.app_context():
        assert Quiz.query.count() == 1


@pytest.mark.parametrize("assignment", ["none", "removed"])
def test_unassigned_or_removed_teacher_gets_a_non_disclosing_404(app, client, assignment):
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group)
        gpid, qpid = group.public_id, quiz.public_id
        outsider = _user("outsider@example.com", UserRole.TEACHER.value)
        if assignment == "removed":
            _assign(group, outsider, GroupTeacherAssignmentStatus.REMOVED.value)
    _login_as(client, "outsider@example.com")
    for url in (_url(gpid), f"{_url(gpid)}/new", _detail_url(gpid, qpid), _edit_url(gpid, qpid)):
        resp = client.get(url)
        # A non-disclosing 404 -- never a 403, and never a hint that the
        # group or the quiz exists.
        assert resp.status_code == 404, url
        assert b"Unit 1 check" not in resp.data, url
    assert _create(client, gpid, follow=False).status_code == 404
    with app.app_context():
        assert Quiz.query.count() == 1


def test_a_missing_group_and_a_forbidden_group_answer_identically(app, client):
    with app.app_context():
        _setup()
        other = _hierarchy(group_name="Group B", course_title="Writing")
        forbidden_gpid = other.public_id
    _login_as(client, "teacher@example.com")
    missing = client.get(_url("11111111-2222-3333-4444-555555555555"))
    forbidden = client.get(_url(forbidden_gpid))
    assert missing.status_code == forbidden.status_code == 404


# ===========================================================================
# Nested object authorization -- cross-Group tampering, numeric ids
# ===========================================================================


def test_a_quiz_from_another_group_404s_through_this_groups_url(app, client):
    with app.app_context():
        teacher, mine = _setup()
        theirs = _hierarchy(group_name="Group B", course_title="Writing")
        _assign(theirs, teacher)  # the teacher is assigned to BOTH groups
        foreign = _quiz_row(theirs, title="Their draft")
        gpid, theirs_gpid = mine.public_id, theirs.public_id
        foreign_qpid = foreign.public_id
    _login_as(client, "teacher@example.com")
    # Legitimate under its OWN group...
    assert client.get(_detail_url(theirs_gpid, foreign_qpid)).status_code == 200
    # ...and never through the other group's URL, even though this teacher
    # may read it elsewhere.
    assert client.get(_detail_url(gpid, foreign_qpid)).status_code == 404
    assert client.get(_edit_url(gpid, foreign_qpid)).status_code == 404
    assert _edit(client, gpid, foreign_qpid, "tok", follow=False).status_code == 404
    with app.app_context():
        assert Quiz.query.filter_by(public_id=foreign_qpid).one().title == "Their draft"


@pytest.mark.parametrize("bogus", ["1", "0", "-1", "9999", "abc"])
def test_a_numeric_or_bogus_quiz_identifier_404s(app, client, bogus):
    with app.app_context():
        _, group = _setup()
        _quiz_row(group)
        gpid = group.public_id
    _login_as(client, "teacher@example.com")
    assert client.get(_detail_url(gpid, bogus)).status_code == 404
    assert client.get(_edit_url(gpid, bogus)).status_code == 404


def test_no_internal_numeric_id_appears_in_quiz_markup(app, client):
    with app.app_context():
        teacher, group = _setup()
        quiz = _quiz_row(group)
        gpid, qpid = group.public_id, quiz.public_id
        gid, qid, tid = group.id, quiz.id, teacher.id
    _login_as(client, "teacher@example.com")
    for url in (_url(gpid), _detail_url(gpid, qpid), _edit_url(gpid, qpid)):
        html = client.get(url).get_data(as_text=True)
        # Structural, deliberately NOT a naive substring search. A UUID
        # public id can legitimately BEGIN with the same digit as a small
        # internal id, so `"/quizzes/1" in html` fires at random against a
        # perfectly correct `/quizzes/183674a7-...` link -- a ~1-in-16
        # false failure per rendered public id, and nothing to do with what
        # this test is about. What the rule actually says is that every
        # id-shaped path segment the page emits is a public id (or a
        # literal route word such as "new"), and that no form value is a
        # bare internal id.
        for segment in re.findall(
            r"/(?:groups|quizzes|questions)/([^\"'/?# ]+)", html
        ):
            assert not segment.isdigit(), (url, segment)
        values = set(re.findall(r'value="([^"]*)"', html))
        for internal in (gid, qid, tid):
            assert str(internal) not in values, (url, internal)


# ===========================================================================
# List -- empty state, ordering, pagination, bounded columns, headers
# ===========================================================================


def test_empty_state_is_shown_and_says_students_cannot_see_drafts(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    _login_as(client, "teacher@example.com")
    html = client.get(_url(gpid)).get_data(as_text=True)
    assert "No quizzes yet" in html
    assert "students" in html.lower()
    assert "Add Quiz" in html


def test_list_shows_drafts_newest_first(app, client):
    with app.app_context():
        _, group = _setup()
        _quiz_row(group, title="Oldest", created_at=datetime(2026, 5, 1, 8, 0))
        _quiz_row(group, title="Middle", created_at=datetime(2026, 5, 2, 8, 0))
        _quiz_row(group, title="Newest", created_at=datetime(2026, 5, 3, 8, 0))
        gpid = group.public_id
    _login_as(client, "teacher@example.com")
    html = client.get(_url(gpid)).get_data(as_text=True)
    assert html.index("Newest") < html.index("Middle") < html.index("Oldest")
    # Every row is labelled a draft and never visible to students.
    assert html.count("Not visible (draft)") == 3


def test_list_does_not_load_or_render_instructions(app, client):
    """The list query selects explicit columns and deliberately omits the
    body, so the page's cost does not grow with how much was written."""
    with app.app_context():
        _, group = _setup()
        _quiz_row(
            group, title="LISTED-TITLE-MARKER", instructions="SECRET-BODY-MARKER " * 20
        )
        gpid = group.public_id
    _login_as(client, "teacher@example.com")
    html = client.get(_url(gpid)).get_data(as_text=True)
    assert "LISTED-TITLE-MARKER" in html
    assert "SECRET-BODY-MARKER" not in html


def test_list_pagination_is_bounded_at_twenty_with_limit_plus_one(app, client):
    with app.app_context():
        _, group = _setup()
        for i in range(21):
            _quiz_row(
                group, title=f"Quiz {i:02d}", created_at=datetime(2026, 5, 1, 8, i)
            )
        gpid = group.public_id
    _login_as(client, "teacher@example.com")

    first = client.get(_url(gpid)).get_data(as_text=True)
    # Newest first: Quiz 20 down to Quiz 01 on page one, Quiz 00 on page two.
    assert "Quiz 20" in first and "Quiz 01" in first
    assert "Quiz 00" not in first
    assert "page=2" in first
    assert "Previous" not in first

    second = client.get(f"{_url(gpid)}?page=2").get_data(as_text=True)
    assert "Quiz 00" in second
    assert "Quiz 20" not in second
    assert "page=1" in second


@pytest.mark.parametrize("bad", ["0", "-3", "abc", "", "99999999", "1e5", "2.5"])
def test_invalid_page_values_normalize_to_page_one(app, client, bad):
    with app.app_context():
        _, group = _setup()
        _quiz_row(group, title="Only one")
        gpid = group.public_id
    _login_as(client, "teacher@example.com")
    resp = client.get(f"{_url(gpid)}?page={bad}")
    assert resp.status_code == 200
    assert "Only one" in resp.get_data(as_text=True)


def test_a_page_past_the_end_falls_back_to_page_one(app, client):
    with app.app_context():
        _, group = _setup()
        _quiz_row(group, title="Only one")
        gpid = group.public_id
    _login_as(client, "teacher@example.com")
    html = client.get(f"{_url(gpid)}?page=9").get_data(as_text=True)
    assert "Only one" in html
    assert "Previous" not in html


@pytest.mark.parametrize("page", ["list", "detail", "form_new", "form_edit"])
def test_content_bearing_responses_are_private_no_store_and_vary_on_cookie(
    app, client, page
):
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group)
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    url = {
        "list": _url(gpid),
        "detail": _detail_url(gpid, qpid),
        "form_new": f"{_url(gpid)}/new",
        "form_edit": _edit_url(gpid, qpid),
    }[page]
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in resp.headers.get("Vary", "")


def test_a_form_error_render_still_carries_the_cache_headers(app, client):
    """A re-rendered form carries the draft's title and instructions, so
    it must not be cacheable either."""
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    _login_as(client, "teacher@example.com")
    resp = _create(client, gpid, title="", follow=False)
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in resp.headers.get("Vary", "")


# ===========================================================================
# Create
# ===========================================================================


def test_create_stores_a_draft_at_version_one_with_equal_timestamps(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    _login_as(client, "teacher@example.com")
    with _at(NOW):
        resp = _create(client, gpid, title="Unit 1 check", instructions="Read it.")
    assert resp.status_code == 200
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.title == "Unit 1 check"
        assert quiz.instructions == "Read it."
        assert quiz.version == 1
        assert quiz.created_at == NOW
        assert quiz.updated_at == NOW
        assert quiz.group_id == Group.query.one().id


def test_create_flashes_that_the_new_quiz_is_a_draft(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    _login_as(client, "teacher@example.com")
    html = _create(client, gpid, title="Unit 1 check").get_data(as_text=True)
    assert "created as a draft" in html
    assert "Students cannot see it" in html


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", ""),
        ("title", "   "),
        ("title", "\n\n"),
        ("instructions", ""),
        ("instructions", "   "),
        ("instructions", "\t\n "),
    ],
)
def test_empty_or_whitespace_only_required_fields_are_rejected(app, client, field, value):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    _login_as(client, "teacher@example.com")
    resp = _create(client, gpid, **{field: value}, follow=False)
    assert resp.status_code == 200
    with app.app_context():
        assert Quiz.query.count() == 0


@pytest.mark.parametrize(
    "field,length,ok",
    [
        ("title", 150, True),
        ("title", 151, False),
        ("instructions", 10000, True),
        ("instructions", 10001, False),
    ],
)
def test_raw_length_limits_are_enforced(app, client, field, length, ok):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    _login_as(client, "teacher@example.com")
    _create(client, gpid, **{field: "x" * length}, follow=False)
    with app.app_context():
        assert Quiz.query.count() == (1 if ok else 0)


def test_length_is_measured_before_trimming(app, client):
    """A value that is only short enough *after* stripping must still be
    rejected: trimming can never be used to slip a longer body past the
    limit."""
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    _login_as(client, "teacher@example.com")
    padded = " " * 50 + "x" * 9990 + " " * 50  # 10090 raw, 9990 trimmed
    assert len(padded) > 10000 and len(padded.strip()) < 10000
    _create(client, gpid, instructions=padded, follow=False)
    with app.app_context():
        assert Quiz.query.count() == 0


def test_outer_whitespace_is_trimmed_but_inner_text_is_kept_exactly(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    _login_as(client, "teacher@example.com")
    _create(
        client,
        gpid,
        title="  Padded title  ",
        instructions="  line one\n\n   indented two  \r\nthree  ",
    )
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.title == "Padded title"
        assert quiz.instructions == "line one\n\n   indented two  \r\nthree"


def test_duplicate_title_in_the_same_group_is_rejected(app, client):
    with app.app_context():
        _, group = _setup()
        _quiz_row(group, title="Same")
        gpid = group.public_id
    _login_as(client, "teacher@example.com")
    resp = _create(client, gpid, title="Same", follow=False)
    assert resp.status_code == 200
    assert "already exists in this group" in resp.get_data(as_text=True)
    with app.app_context():
        assert Quiz.query.count() == 1


def test_the_same_title_is_allowed_in_another_group(app, client):
    with app.app_context():
        teacher, first = _setup()
        second = _hierarchy(group_name="Group B", course_title="Writing")
        _assign(second, teacher)
        _quiz_row(first, title="Same")
        second_gpid = second.public_id
    _login_as(client, "teacher@example.com")
    _create(client, second_gpid, title="Same")
    with app.app_context():
        assert Quiz.query.filter_by(title="Same").count() == 2


def test_forged_fields_in_the_create_body_have_nowhere_to_land(app, client):
    """The form carries only title and instructions. A forged status,
    publication time, version, group_id or public_id must be ignored --
    not stored, and not able to publish anything."""
    with app.app_context():
        teacher, group = _setup()
        other = _hierarchy(group_name="Group B", course_title="Writing")
        _assign(other, teacher)
        gpid, other_gid = group.public_id, other.id
        gid = group.id
    _login_as(client, "teacher@example.com")
    _create(
        client,
        gpid,
        title="Forged",
        status="published",
        published_at="2026-01-01T00:00",
        version="99",
        group_id=str(other_gid),
        public_id="forged-public-id",
        id="4242",
    )
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.group_id == gid
        assert quiz.version == 1
        assert quiz.public_id != "forged-public-id"
        assert quiz.id != 4242
        # Phase 4 / M04D: `status` and `published_at` are real columns now,
        # so the contract is no longer "they do not exist" -- it is that
        # this form cannot reach them. Publication is owned solely by its
        # own POST route, so a forged status in a create body publishes
        # nothing.
        assert quiz.status == "draft"
        assert quiz.published_at is None


def test_create_requires_csrf(app):
    csrf_app = __import__("app", fromlist=["create_app"]).create_app(
        "testing", WTF_CSRF_ENABLED=True
    )
    with csrf_app.app_context():
        db.create_all()
        _, group = _setup()
        gpid = group.public_id
        csrf_client = csrf_app.test_client()
        login(csrf_client, "teacher@example.com")
        resp = csrf_client.post(
            f"{_url(gpid)}/new", data={"title": "X", "instructions": "Y"}
        )
        assert resp.status_code == 400
        assert Quiz.query.count() == 0
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


# ===========================================================================
# Detail -- read only, escaped
# ===========================================================================


def test_detail_renders_the_draft_and_says_it_is_not_visible_to_students(app, client):
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group, title="Unit 1 check", instructions="Line one\nLine two")
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    html = client.get(_detail_url(gpid, qpid)).get_data(as_text=True)
    assert "Unit 1 check" in html
    assert "Line one\nLine two" in html
    assert "Draft" in html
    assert "Students cannot see it" in html
    # A draft with no questions is a legitimate state. Since Phase 4 / M04B
    # the page is the question-management surface, so it shows a normal
    # empty state with an Add Question action rather than nothing at all.
    assert "No questions yet" in html
    assert "Add Question" in html


def test_authored_text_is_escaped_never_rendered_as_html(app, client):
    payload = "<script>alert('xss')</script>"
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group, title=f"T {payload}", instructions=f"I {payload}")
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    for url in (_url(gpid), _detail_url(gpid, qpid), _edit_url(gpid, qpid)):
        html = client.get(url).get_data(as_text=True)
        assert payload not in html, url
        assert "&lt;script&gt;" in html, url


def test_detail_offers_no_delete_grade_or_override_control(app, client):
    """M04B added an Add Question action and M04D added publication and
    attempt-review controls, so ``publish`` and ``attempt`` are no longer
    forbidden words on this page.

    What it still forbids is unchanged: there is no way to delete a quiz,
    archive one, grade or re-grade anything, or override a score -- and no
    such route exists server-side either.
    """
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group)
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    html = client.get(_detail_url(gpid, qpid)).get_data(as_text=True).lower()
    for absent in (
        "delete", "archive quiz", "start quiz", "override", "re-grade",
        "regrade", "partial credit", "answer key",
    ):
        assert absent not in html, absent
    # An unready draft is not offered a Publish control at all.
    assert "not ready to publish yet" in html
    assert 'action="/teacher/groups/%s/quizzes/%s/publish"' % (gpid, qpid) not in html


# ===========================================================================
# Archived hierarchy -- reading stays, writing stops
# ===========================================================================


@pytest.mark.parametrize("archived", ["term", "level", "course", "group"])
def test_drafts_stay_readable_under_an_archived_chain(app, client, archived):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy(**{f"{archived}_status": AcademicStatus.ARCHIVED.value})
        _assign(group, teacher)
        quiz = _quiz_row(group, title="Historic draft")
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    listing = client.get(_url(gpid))
    detail = client.get(_detail_url(gpid, qpid))
    assert listing.status_code == detail.status_code == 200
    assert "Historic draft" in listing.get_data(as_text=True)
    assert "Historic draft" in detail.get_data(as_text=True)
    # ...but no write control is offered.
    assert "Add Quiz" not in listing.get_data(as_text=True)
    assert "Edit Quiz" not in detail.get_data(as_text=True)


@pytest.mark.parametrize("archived", ["term", "level", "course", "group"])
def test_create_and_edit_are_denied_under_an_archived_chain(app, client, archived):
    with app.app_context():
        teacher = _user("teacher@example.com", UserRole.TEACHER.value)
        group = _hierarchy(**{f"{archived}_status": AcademicStatus.ARCHIVED.value})
        _assign(group, teacher)
        quiz = _quiz_row(group, title="Historic draft")
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")

    created = _create(client, gpid, title="New one")
    assert "can only be created or edited" in created.get_data(as_text=True)
    edited = _edit(client, gpid, qpid, "any-token")
    assert "can only be created or edited" in edited.get_data(as_text=True)

    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.title == "Historic draft"
        assert quiz.version == 1
        assert Quiz.query.count() == 1


def test_archiving_a_group_neither_deletes_nor_rewrites_a_draft(app, client):
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group, title="Kept", instructions="Kept body")
        gid, before_version, before_updated = group.id, quiz.version, quiz.updated_at
        gpid, qpid = group.public_id, quiz.public_id
    with app.app_context():
        db.session.get(Group, gid).status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    _login_as(client, "teacher@example.com")
    assert client.get(_detail_url(gpid, qpid)).status_code == 200
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.title == "Kept" and quiz.instructions == "Kept body"
        assert quiz.version == before_version and quiz.updated_at == before_updated


# ===========================================================================
# Edit -- version, no-op, and the signed token
# ===========================================================================


def test_edit_form_renders_the_current_values_and_a_token(app, client):
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group, title="Original", instructions="Original body")
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    html = client.get(_edit_url(gpid, qpid)).get_data(as_text=True)
    assert 'value="Original"' in html
    assert "Original body" in html
    assert re.search(r'name="edit_state" value="[^"]+"', html)


def test_a_meaningful_edit_increments_version_exactly_once(app, client):
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group, title="Original", instructions="Body")
        gpid, qpid, created = group.public_id, quiz.public_id, quiz.created_at
    _login_as(client, "teacher@example.com")
    token = _get_edit_token(client, gpid, qpid)
    with _at(LATER):
        _edit(client, gpid, qpid, token, title="Renamed", instructions="Rewritten.")
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.title == "Renamed" and quiz.instructions == "Rewritten."
        assert quiz.version == 2
        assert quiz.updated_at == LATER
        # A revision is the same record, not a new one.
        assert quiz.created_at == created
        assert quiz.public_id == qpid


def test_an_unchanged_save_is_a_no_op(app, client):
    """version, updated_at and the stored values are all left alone: re-
    saving unchanged wording is not an edit."""
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group, title="Same", instructions="Same body")
        gpid, qpid, created, updated = (
            group.public_id, quiz.public_id, quiz.created_at, quiz.updated_at
        )
    _login_as(client, "teacher@example.com")
    token = _get_edit_token(client, gpid, qpid)
    with _at(LATER):
        resp = _edit(client, gpid, qpid, token, title="Same", instructions="Same body")
    assert "unchanged, so nothing was saved" in resp.get_data(as_text=True)
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.version == 1
        assert quiz.created_at == created and quiz.updated_at == updated


def test_a_save_that_only_adds_outer_whitespace_is_also_a_no_op(app, client):
    """The no-op is decided on the NORMALIZED values."""
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group, title="Same", instructions="Same body")
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _get_edit_token(client, gpid, qpid)
    _edit(client, gpid, qpid, token, title="  Same  ", instructions="\n Same body \n")
    with app.app_context():
        assert Quiz.query.one().version == 1


def test_edit_may_not_move_a_draft_to_another_group(app, client):
    with app.app_context():
        teacher, group = _setup()
        other = _hierarchy(group_name="Group B", course_title="Writing")
        _assign(other, teacher)
        quiz = _quiz_row(group)
        gpid, qpid, gid, other_gid = group.public_id, quiz.public_id, group.id, other.id
    _login_as(client, "teacher@example.com")
    token = _get_edit_token(client, gpid, qpid)
    _edit(client, gpid, qpid, token, title="Moved", group_id=str(other_gid))
    with app.app_context():
        assert Quiz.query.one().group_id == gid


def test_duplicate_title_on_edit_is_rejected_and_nothing_is_written(app, client):
    with app.app_context():
        _, group = _setup()
        _quiz_row(group, title="Taken")
        target = _quiz_row(group, title="Mine")
        gpid, qpid = group.public_id, target.public_id
    _login_as(client, "teacher@example.com")
    token = _get_edit_token(client, gpid, qpid)
    resp = _edit(client, gpid, qpid, token, title="Taken", follow=False)
    assert resp.status_code == 200
    assert "already exists in this group" in resp.get_data(as_text=True)
    with app.app_context():
        quiz = Quiz.query.filter_by(public_id=qpid).one()
        assert quiz.title == "Mine" and quiz.version == 1


def test_edit_requires_csrf(app):
    csrf_app = __import__("app", fromlist=["create_app"]).create_app(
        "testing", WTF_CSRF_ENABLED=True
    )
    with csrf_app.app_context():
        db.create_all()
        _, group = _setup()
        quiz = _quiz_row(group, title="Untouched")
        gpid, qpid = group.public_id, quiz.public_id
        csrf_client = csrf_app.test_client()
        login(csrf_client, "teacher@example.com")
        resp = csrf_client.post(
            _edit_url(gpid, qpid), data={"title": "X", "instructions": "Y"}
        )
        assert resp.status_code == 400
        assert Quiz.query.one().title == "Untouched"
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_unsupported_methods_are_rejected(app, client, method):
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group)
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    for url in (_url(gpid), _detail_url(gpid, qpid), _edit_url(gpid, qpid)):
        assert getattr(client, method)(url).status_code == 405, url


def test_the_list_and_detail_routes_accept_no_post(app, client):
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group)
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    assert client.post(_url(gpid)).status_code == 405
    assert client.post(_detail_url(gpid, qpid)).status_code == 405


# ===========================================================================
# Signed edit token -- shape, binding, staleness, replay
# ===========================================================================


def _mint(app, **overrides):
    """A token minted directly from the route module's own serializer, so
    a deliberately wrong-shaped payload can be exercised."""
    payload = {
        "purpose": quizzes_mod._QUIZ_EDIT_PURPOSE,
        "teacher_public_id": "t",
        "group_public_id": "g",
        "quiz_public_id": "q",
        "version": 1,
    }
    payload.update(overrides)
    with app.app_context():
        return quizzes_mod._edit_state_serializer().dumps(payload)


@pytest.mark.parametrize(
    "token",
    ["", "garbage", "a.b.c", "eyJhIjoxfQ.signature-nonsense"],
)
def test_a_missing_or_malformed_token_is_rejected(app, client, token):
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group, title="Untouched")
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    resp = _edit(client, gpid, qpid, token)
    assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.title == "Untouched" and quiz.version == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"purpose": "quiz-question-edit"},          # right salt, wrong purpose
        {"version": True},                          # bool is not an integer version
        {"version": 0},                             # not positive
        {"version": "1"},                           # not an int at all
        {"version": 1.0},                           # float is not an int
        {"teacher_public_id": 7},                   # not a string
    ],
)
def test_a_wrong_shaped_payload_is_rejected(app, client, overrides):
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group, title="Untouched")
        gpid, qpid = group.public_id, quiz.public_id
        tpid = User.query.filter_by(email="teacher@example.com").one().public_id
    base = {
        "teacher_public_id": tpid,
        "group_public_id": gpid,
        "quiz_public_id": qpid,
        "version": 1,
    }
    base.update(overrides)
    token = _mint(app, **base)
    _login_as(client, "teacher@example.com")
    resp = _edit(client, gpid, qpid, token)
    assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert Quiz.query.one().title == "Untouched"


def test_a_payload_with_extra_or_missing_keys_is_rejected(app, client):
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group, title="Untouched")
        gpid, qpid = group.public_id, quiz.public_id
        tpid = User.query.filter_by(email="teacher@example.com").one().public_id
        serializer = quizzes_mod._edit_state_serializer()
        extra = serializer.dumps(
            {
                "purpose": quizzes_mod._QUIZ_EDIT_PURPOSE,
                "teacher_public_id": tpid,
                "group_public_id": gpid,
                "quiz_public_id": qpid,
                "version": 1,
                "admin": True,
            }
        )
        missing = serializer.dumps({"purpose": quizzes_mod._QUIZ_EDIT_PURPOSE})
    _login_as(client, "teacher@example.com")
    for token in (extra, missing):
        resp = _edit(client, gpid, qpid, token)
        assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert Quiz.query.one().title == "Untouched"


def test_a_token_signed_with_another_salt_is_rejected(app, client):
    """The M04 salt is dedicated: a validly signed M03 feedback token
    carries no authority here."""
    from itsdangerous import URLSafeSerializer

    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group, title="Untouched")
        gpid, qpid = group.public_id, quiz.public_id
        tpid = User.query.filter_by(email="teacher@example.com").one().public_id
        foreign = URLSafeSerializer(
            app.config["SECRET_KEY"], salt="teacher.submission-feedback-state.phase4-m03.v1"
        ).dumps(
            {
                "purpose": quizzes_mod._QUIZ_EDIT_PURPOSE,
                "teacher_public_id": tpid,
                "group_public_id": gpid,
                "quiz_public_id": qpid,
                "version": 1,
            }
        )
    _login_as(client, "teacher@example.com")
    resp = _edit(client, gpid, qpid, foreign)
    assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert Quiz.query.one().title == "Untouched"


def test_a_token_bound_to_another_quiz_or_group_is_rejected(app, client):
    with app.app_context():
        teacher, group = _setup()
        other_group = _hierarchy(group_name="Group B", course_title="Writing")
        _assign(other_group, teacher)
        target = _quiz_row(group, title="Untouched")
        sibling = _quiz_row(group, title="Sibling")
        gpid, qpid = group.public_id, target.public_id
        sibling_qpid, other_gpid = sibling.public_id, other_group.public_id
        tpid = teacher.public_id
    cross_quiz = _mint(
        app, teacher_public_id=tpid, group_public_id=gpid,
        quiz_public_id=sibling_qpid, version=1,
    )
    cross_group = _mint(
        app, teacher_public_id=tpid, group_public_id=other_gpid,
        quiz_public_id=qpid, version=1,
    )
    _login_as(client, "teacher@example.com")
    for token in (cross_quiz, cross_group):
        resp = _edit(client, gpid, qpid, token)
        assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert Quiz.query.filter_by(public_id=qpid).one().title == "Untouched"


def test_a_stale_token_is_rejected_and_the_attempted_values_are_discarded(app, client):
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group, title="Original", instructions="Original body")
        gpid, qpid, qid = group.public_id, quiz.public_id, quiz.id
    _login_as(client, "teacher@example.com")
    token = _get_edit_token(client, gpid, qpid)

    # A co-teacher (or the same teacher in another tab) commits first.
    with app.app_context():
        row = db.session.get(Quiz, qid)
        row.title, row.instructions, row.version = "Theirs", "Their body", 2
        db.session.commit()

    resp = _edit(
        client, gpid, qpid, token,
        title="ATTEMPTED-TITLE-MARKER", instructions="ATTEMPTED-BODY-MARKER",
        follow=True,
    )
    html = resp.get_data(as_text=True)
    assert "changed by someone else" in html
    # The rejection reloads the CURRENT state; it never redisplays the
    # attempted values, which is exactly the bypass it exists to close.
    assert "Theirs" in html
    assert "ATTEMPTED-TITLE-MARKER" not in html
    assert "ATTEMPTED-BODY-MARKER" not in html
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.title == "Theirs" and quiz.version == 2


def test_a_stale_rejection_never_mints_a_fresh_token_paired_with_attempted_values(
    app, client
):
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group, title="Original")
        gpid, qpid, qid = group.public_id, quiz.public_id, quiz.id
    _login_as(client, "teacher@example.com")
    token = _get_edit_token(client, gpid, qpid)
    with app.app_context():
        row = db.session.get(Quiz, qid)
        row.title, row.version = "Theirs", 2
        db.session.commit()

    html = _edit(
        client, gpid, qpid, token, title="ATTEMPTED-TITLE-MARKER"
    ).get_data(as_text=True)
    fresh = re.search(r'name="edit_state" value="([^"]*)"', html)
    assert fresh is not None
    with app.app_context():
        payload = quizzes_mod._load_edit_state(fresh.group(1))
    # The token that comes back describes the CURRENT row (version 2),
    # and the form beside it shows the current title -- never the
    # attempted one.
    assert payload["version"] == 2
    assert 'value="Theirs"' in html


def test_replaying_a_successful_save_is_rejected_rather_than_applied_twice(app, client):
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group, title="Original")
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _get_edit_token(client, gpid, qpid)
    _edit(client, gpid, qpid, token, title="First", instructions="First body")
    with app.app_context():
        assert Quiz.query.one().version == 2

    replay = _edit(client, gpid, qpid, token, title="Second", instructions="Second body")
    assert "changed by someone else" in replay.get_data(as_text=True)
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.title == "First" and quiz.version == 2


def test_two_edits_inside_one_whole_second_are_still_distinguishable(app, client):
    """Timestamps are not the staleness mechanism -- ``version`` is. Both
    saves happen at the SAME whole second, so only the version can catch
    the second one."""
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group, title="Original", created_at=NOW, updated_at=NOW)
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    stale_token = _get_edit_token(client, gpid, qpid)

    with _at(SAME_SECOND):
        _edit(client, gpid, qpid, stale_token, title="First", instructions="First body")
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.version == 2 and quiz.updated_at == SAME_SECOND

    with _at(SAME_SECOND):
        resp = _edit(client, gpid, qpid, stale_token, title="Second", instructions="B")
    assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.title == "First" and quiz.version == 2


def test_an_a_b_a_round_trip_still_invalidates_an_open_form(app, client):
    """The values return to what the open form was written against, but
    the version has moved twice -- a value comparison would miss this."""
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group, title="A", instructions="A body")
        gpid, qpid, qid = group.public_id, quiz.public_id, quiz.id
    _login_as(client, "teacher@example.com")
    token = _get_edit_token(client, gpid, qpid)
    with app.app_context():
        row = db.session.get(Quiz, qid)
        row.title, row.instructions, row.version = "B", "B body", 2
        db.session.commit()
        row = db.session.get(Quiz, qid)
        row.title, row.instructions, row.version = "A", "A body", 3
        db.session.commit()

    resp = _edit(client, gpid, qpid, token, title="C", instructions="C body")
    assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.title == "A" and quiz.version == 3


def test_ordinary_validation_errors_keep_the_attempted_values_and_the_original_token(
    app, client
):
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group, title="Original", instructions="Original body")
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _get_edit_token(client, gpid, qpid)
    resp = _edit(
        client, gpid, qpid, token, title="Kept attempt", instructions="", follow=False
    )
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'value="Kept attempt"' in html
    # The ORIGINAL token is re-embedded, never a freshly minted one: the
    # expected version must not be silently refreshed underneath the
    # teacher.
    assert f'name="edit_state" value="{token}"' in html
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.title == "Original" and quiz.version == 1


# ===========================================================================
# Co-teacher collaboration
# ===========================================================================


def test_every_actively_assigned_co_teacher_may_read_and_edit_the_same_draft(app, client):
    with app.app_context():
        first, group = _setup("first@example.com")
        second = _user("second@example.com", UserRole.TEACHER.value)
        _assign(group, second)
        quiz = _quiz_row(group, title="Shared")
        gpid, qpid = group.public_id, quiz.public_id
        first_id, second_id = first.id, second.id

    first_client, second_client = app.test_client(), app.test_client()
    _login_as(first_client, "first@example.com")
    _assert_authenticated_as(first_client, first_id)
    _login_as(second_client, "second@example.com")
    _assert_authenticated_as(second_client, second_id)

    _fresh_identity()
    assert first_client.get(_detail_url(gpid, qpid)).status_code == 200
    _fresh_identity()
    assert second_client.get(_detail_url(gpid, qpid)).status_code == 200

    _fresh_identity()
    token = _get_edit_token(second_client, gpid, qpid)
    _fresh_identity()
    _edit(second_client, gpid, qpid, token, title="Edited by the co-teacher")
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.title == "Edited by the co-teacher"
        assert quiz.version == 2


def test_one_co_teachers_token_cannot_be_used_by_another(app, client):
    """The token is bound to the acting Teacher's public_id, so a token
    lifted from a colleague's form is worthless."""
    with app.app_context():
        first, group = _setup("first@example.com")
        second = _user("second@example.com", UserRole.TEACHER.value)
        _assign(group, second)
        quiz = _quiz_row(group, title="Untouched")
        gpid, qpid = group.public_id, quiz.public_id
        first_id, second_id = first.id, second.id

    first_client, second_client = app.test_client(), app.test_client()
    _login_as(first_client, "first@example.com")
    _assert_authenticated_as(first_client, first_id)
    _login_as(second_client, "second@example.com")
    _assert_authenticated_as(second_client, second_id)

    _fresh_identity()
    first_token = _get_edit_token(first_client, gpid, qpid)
    _fresh_identity()
    resp = _edit(second_client, gpid, qpid, first_token, title="Stolen")
    assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert Quiz.query.one().title == "Untouched"


# ===========================================================================
# Structural: lock order, single reset, post-lock rechecks
# ===========================================================================


def _capture_locks(fn):
    from sqlalchemy.orm import Query

    events = []
    original_rollback = db.session.rollback
    original_wfu = Query.with_for_update

    def rollback_spy(*a, **k):
        events.append("reset")
        return original_rollback(*a, **k)

    def wfu_spy(self, *a, **k):
        entity = self.column_descriptions[0]["entity"] if self.column_descriptions else None
        events.append(f"lock:{getattr(entity, '__name__', '?')}")
        return original_wfu(self, *a, **k)

    with patch.object(db.session, "rollback", side_effect=rollback_spy), patch.object(
        Query, "with_for_update", wfu_spy
    ):
        fn()
    return events


_PREFIX = [
    "lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group",
    "lock:User", "lock:GroupTeacherAssignment",
]


def test_create_locks_the_established_order_under_one_reset(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    _login_as(client, "teacher@example.com")
    events = _capture_locks(lambda: _create(client, gpid, title="Locked"))
    assert events.count("reset") == 1
    assert [e for e in events if e.startswith("lock:")] == _PREFIX


def test_edit_locks_the_established_order_including_the_quiz(app, client):
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group, title="T")
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _get_edit_token(client, gpid, qpid)
    events = _capture_locks(
        lambda: _edit(client, gpid, qpid, token, title="T2", instructions="B2")
    )
    assert events.count("reset") == 1
    assert [e for e in events if e.startswith("lock:")] == _PREFIX + ["lock:Quiz"]


def test_group_retarget_and_quiz_create_use_compatible_locks(app, client):
    """The Administrator Group edit and every quiz mutation both take
    AcademicTerm -> Level -> Course -> Group under one deliberate reset,
    so a same-Group retarget and a quiz create serialise on the shared
    Group lock -- which is what stops a new draft slipping past the
    identity freeze.

    The Administrator half runs in its own app context: Flask-Login caches
    the resolved user on the app context, which the test fixture keeps
    pushed for the whole test.
    """
    from tests.conftest import make_user

    with app.app_context():
        _, group = _setup()
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        gpid, term_id, course_id = group.public_id, group.academic_term_id, group.course_id
    _login_as(client, "teacher@example.com")
    quiz_events = _capture_locks(lambda: _create(client, gpid, title="X"))
    quiz_locks = [e for e in quiz_events if e.startswith("lock:")]

    with app.app_context():
        admin_client = app.test_client()
        _login_as(admin_client, "admin@example.com")
        html = admin_client.get(f"/admin/groups/{gpid}/edit").get_data(as_text=True)
        token = re.search(r'name="edit_snapshot" value="([^"]*)"', html).group(1)
        group_locks = [
            e
            for e in _capture_locks(
                lambda: admin_client.post(
                    f"/admin/groups/{gpid}/edit",
                    data={
                        "academic_term_id": term_id, "course_id": course_id,
                        "name": "Group A", "code": "", "capacity": 20,
                        "edit_snapshot": token,
                    },
                    follow_redirects=True,
                )
            )
            if e.startswith("lock:")
        ]

    shared = ["lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group"]
    assert quiz_locks[:4] == shared
    assert group_locks[:4] == shared
    assert quiz_events.count("reset") == 1


def _inject_before_group_lock(action):
    """Run `action` immediately before the Group row lock -- an exact
    transaction boundary, with no sleeps and no race. Stands in for a
    concurrent commit landing in the window between the unlocked preview
    read and the locks."""
    original = quizzes_mod.lock_group_in_open_transaction

    def side_effect(public_id):
        action()
        return original(public_id)

    return patch.object(
        quizzes_mod, "lock_group_in_open_transaction", side_effect=side_effect
    )


def test_post_lock_recheck_rejects_a_concurrently_removed_assignment(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
        row_id = GroupTeacherAssignment.query.one().id
    _login_as(client, "teacher@example.com")

    def remove():
        db.session.get(GroupTeacherAssignment, row_id).status = (
            GroupTeacherAssignmentStatus.REMOVED.value
        )
        db.session.commit()

    with _inject_before_group_lock(remove):
        resp = _create(client, gpid, title="Race", follow=False)
    assert resp.status_code == 404
    with app.app_context():
        assert Quiz.query.count() == 0


def test_post_lock_recheck_rejects_a_concurrently_suspended_teacher(app, client):
    with app.app_context():
        teacher, group = _setup()
        gpid, tid = group.public_id, teacher.id
    _login_as(client, "teacher@example.com")

    def suspend():
        db.session.get(User, tid).status = UserStatus.SUSPENDED.value
        db.session.commit()

    with _inject_before_group_lock(suspend):
        resp = _create(client, gpid, title="Race", follow=False)
    assert resp.status_code == 404
    with app.app_context():
        assert Quiz.query.count() == 0


def test_post_lock_recheck_rejects_a_concurrently_demoted_teacher(app, client):
    with app.app_context():
        teacher, group = _setup()
        gpid, tid = group.public_id, teacher.id
    _login_as(client, "teacher@example.com")

    def demote():
        db.session.get(User, tid).role = UserRole.RESEARCHER.value
        db.session.commit()

    with _inject_before_group_lock(demote):
        resp = _create(client, gpid, title="Race", follow=False)
    assert resp.status_code == 404
    with app.app_context():
        assert Quiz.query.count() == 0


@pytest.mark.parametrize("entity", ["group", "term", "level", "course"])
def test_post_lock_recheck_rejects_a_concurrently_archived_ancestor(app, client, entity):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
        target_id = {
            "group": (Group, group.id),
            "term": (AcademicTerm, group.academic_term_id),
            "course": (Course, group.course_id),
            "level": (Level, Course.query.one().level_id),
        }[entity]
    _login_as(client, "teacher@example.com")

    model, row_id = target_id

    def archive():
        db.session.get(model, row_id).status = AcademicStatus.ARCHIVED.value
        db.session.commit()

    with _inject_before_group_lock(archive):
        resp = _create(client, gpid, title="Race", follow=True)
    assert resp.status_code == 200
    assert "can only be created or edited" in resp.get_data(as_text=True)
    with app.app_context():
        assert Quiz.query.count() == 0


def test_post_lock_recheck_catches_a_duplicate_title_created_in_the_window(app, client):
    """The friendly pre-lock check saw no conflict; a co-teacher commits
    the same title before the locks. The authoritative post-lock check
    must catch it and write nothing."""
    with app.app_context():
        _, group = _setup()
        gpid, gid = group.public_id, group.id
    _login_as(client, "teacher@example.com")

    def create_conflict():
        db.session.add(
            Quiz(group_id=gid, title="Race", instructions="Theirs", version=1,
                 created_at=NOW, updated_at=NOW)
        )
        db.session.commit()

    with _inject_before_group_lock(create_conflict):
        resp = _create(client, gpid, title="Race", follow=False)
    assert resp.status_code == 200
    assert "already exists in this group" in resp.get_data(as_text=True)
    with app.app_context():
        rows = Quiz.query.all()
        assert len(rows) == 1
        assert rows[0].instructions == "Theirs"


def test_post_lock_recheck_catches_a_quiz_removed_in_the_window(app, client):
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group, title="Doomed")
        gpid, qpid, qid = group.public_id, quiz.public_id, quiz.id
    _login_as(client, "teacher@example.com")
    token = _get_edit_token(client, gpid, qpid)

    def remove_quiz():
        db.session.delete(db.session.get(Quiz, qid))
        db.session.commit()

    with _inject_before_group_lock(remove_quiz):
        resp = _edit(client, gpid, qpid, token, title="Ghost", follow=False)
    assert resp.status_code == 404
    with app.app_context():
        assert Quiz.query.count() == 0


def test_post_lock_recheck_catches_a_version_bump_in_the_window(app, client):
    """The pre-lock token check passed against the preview read; a
    co-teacher commits before the locks. The authoritative check runs
    against the LOCKED row."""
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group, title="Original")
        gpid, qpid, qid = group.public_id, quiz.public_id, quiz.id
    _login_as(client, "teacher@example.com")
    token = _get_edit_token(client, gpid, qpid)

    def bump():
        row = db.session.get(Quiz, qid)
        row.title, row.version = "Theirs", 2
        db.session.commit()

    with _inject_before_group_lock(bump):
        resp = _edit(client, gpid, qpid, token, title="Mine")
    assert "changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.title == "Theirs" and quiz.version == 2


# ===========================================================================
# IntegrityError -- rollback, generic message, safe recovery
# ===========================================================================


def _fail_commit_then(action=None):
    """Make the write raise ``IntegrityError``, optionally applying
    `action` first so the recovery path starts from the changed state."""
    from sqlalchemy.exc import IntegrityError

    real_rollback = db.session.rollback
    real_commit = db.session.commit

    def side_effect():
        real_rollback()
        if action is not None:
            action()
            real_commit()
        raise IntegrityError(
            "INSERT INTO quizzes (group_id, title) VALUES (?, ?)",
            {"group_id": 1, "title": "x"},
            Exception("UNIQUE constraint failed: quizzes.title"),
        )

    return patch.object(db.session, "commit", side_effect=side_effect)


def test_create_integrity_error_rolls_back_and_reports_generically(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    _login_as(client, "teacher@example.com")
    with _fail_commit_then():
        resp = _create(client, gpid, title="Never lands", follow=False)
    html = resp.get_data(as_text=True)
    assert "could not be saved" in html
    for leak in ("INSERT INTO", "IntegrityError", "UNIQUE constraint", "quizzes.title"):
        assert leak not in html, leak
    with app.app_context():
        assert Quiz.query.count() == 0


def test_edit_integrity_error_rolls_back_and_reports_generically(app, client):
    with app.app_context():
        _, group = _setup()
        quiz = _quiz_row(group, title="Untouched", instructions="Untouched body")
        gpid, qpid = group.public_id, quiz.public_id
    _login_as(client, "teacher@example.com")
    token = _get_edit_token(client, gpid, qpid)
    with _fail_commit_then():
        resp = _edit(client, gpid, qpid, token, title="Never lands", follow=False)
    html = resp.get_data(as_text=True)
    assert "could not be saved" in html
    for leak in ("INSERT INTO", "IntegrityError", "UNIQUE constraint"):
        assert leak not in html, leak
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.title == "Untouched" and quiz.version == 1


@pytest.mark.parametrize("change", ["unassign", "suspend", "demote"])
def test_integrity_recovery_re_authorizes_and_404s_when_access_ended(app, client, change):
    """A rolled-back read is not authorization evidence: the same
    concurrent change that caused the conflict may have ended access."""
    with app.app_context():
        teacher, group = _setup()
        quiz = _quiz_row(group, title="Untouched")
        gpid, qpid, tid, gid = group.public_id, quiz.public_id, teacher.id, group.id
    _login_as(client, "teacher@example.com")
    token = _get_edit_token(client, gpid, qpid)

    def revoke():
        if change == "unassign":
            GroupTeacherAssignment.query.filter_by(
                group_id=gid, teacher_id=tid
            ).one().status = GroupTeacherAssignmentStatus.REMOVED.value
        elif change == "suspend":
            db.session.get(User, tid).status = UserStatus.SUSPENDED.value
        else:
            db.session.get(User, tid).role = UserRole.RESEARCHER.value

    with _fail_commit_then(revoke):
        resp = _edit(client, gpid, qpid, token, title="Never lands", follow=False)
    assert resp.status_code == 404
    with app.app_context():
        quiz = Quiz.query.one()
        assert quiz.title == "Untouched" and quiz.version == 1


# ===========================================================================
# Group identity freeze -- the service-level helper
# ===========================================================================


def test_an_empty_draft_already_freezes_group_identity(app):
    """M04A has no questions at all, so waiting for them would leave every
    draft unprotected: the title and instructions were already written for
    this Course in this Term."""
    from app.services.quiz_queries import group_has_quiz_history

    with app.app_context():
        _, group = _setup()
        assert group_has_quiz_history(group.id) is False
        _quiz_row(group, title="Empty draft")
        assert group_has_quiz_history(group.id) is True


def test_quiz_history_is_scoped_to_its_own_group(app):
    from app.services.quiz_queries import group_has_quiz_history

    with app.app_context():
        _, first = _setup()
        second = _hierarchy(group_name="Group B", course_title="Writing")
        _quiz_row(first, title="Only here")
        assert group_has_quiz_history(first.id) is True
        assert group_has_quiz_history(second.id) is False


# ===========================================================================
# Surface boundaries -- no Student exposure, no M04B endpoints
# ===========================================================================


def test_every_quiz_route_is_group_scoped_under_its_own_role_prefix(app):
    """Phase 4 / M04D added the Student surface, so ``/teacher/...`` is no
    longer the only prefix. Every quiz route is still nested under a Group
    public identifier, and every one belongs to exactly one role's
    prefix."""
    rules = [str(r) for r in app.url_map.iter_rules() if "quiz" in str(r).lower()]
    assert rules, "the quiz routes must exist"
    teacher_prefix = "/teacher/groups/<group_public_id>/quizzes"
    student_prefixes = ("/student/groups/<group_public_id>/quizzes", "/student/quizzes")
    for rule in rules:
        assert rule.startswith(teacher_prefix) or rule.startswith(
            student_prefixes
        ), rule
    # Both surfaces really exist.
    assert any(r.startswith(teacher_prefix) for r in rules)
    assert any(r.startswith(student_prefixes) for r in rules)


def test_there_is_no_delete_grade_or_answer_key_endpoint(app):
    """M04B added question authoring and M04D added publication, attempts,
    submission and results, so those words are no longer forbidden path
    segments.

    What stays forbidden is what was never approved: deleting a quiz,
    question, option, attempt, answer or result; archiving any of them;
    manual or re-grading; overriding a score; and releasing the answer key.
    No route may accept DELETE.
    """
    for rule in app.url_map.iter_rules():
        text = str(rule).lower()
        if "quiz" not in text:
            continue
        for forbidden in (
            "delete", "remove", "archive", "grade", "regrade", "override",
            "answer-key", "release", "duplicate", "toggle",
        ):
            assert forbidden not in text, (text, forbidden)
        assert "DELETE" not in rule.methods
        assert rule.methods <= {"GET", "HEAD", "OPTIONS", "POST"}


def test_a_student_cannot_reach_a_draft_anywhere(app, client):
    """No Student list, detail, search result or dashboard section may
    expose a quiz draft in M04A."""
    from tests.conftest import make_user
    from app.models import Enrollment, EnrollmentStatus

    with app.app_context():
        _, group = _setup()
        student = make_user("s@example.com", UserRole.STUDENT.value)
        db.session.add(
            Enrollment(
                student_id=student.id,
                group_id=group.id,
                status=EnrollmentStatus.ACTIVE.value,
            )
        )
        db.session.commit()
        quiz = _quiz_row(group, title="INVISIBLE-DRAFT-MARKER")
        gpid, qpid = group.public_id, quiz.public_id

    _login_as(client, "s@example.com")
    for url in (
        "/student/dashboard",
        "/student/assignments",
        "/student/search?q=INVISIBLE",
        f"/student/groups/{gpid}/units",
    ):
        resp = client.get(url)
        assert b"INVISIBLE-DRAFT-MARKER" not in resp.data, url
    # And the Teacher routes themselves stay closed to a Student.
    assert client.get(_url(gpid)).status_code == 403
    assert client.get(_detail_url(gpid, qpid)).status_code == 403


# ===========================================================================
# Teacher dashboard integration
# ===========================================================================


def test_dashboard_card_links_to_the_quizzes_page(app, client):
    with app.app_context():
        _, group = _setup()
        gpid = group.public_id
    _login_as(client, "teacher@example.com")
    html = client.get("/teacher/dashboard").get_data(as_text=True)
    assert f'href="{_url(gpid)}"' in html
    assert "Manage Quizzes" in html
    # The link works -- no dead links.
    assert client.get(_url(gpid)).status_code == 200


def test_dashboard_gains_no_quiz_counts_or_publication_language(app, client):
    with app.app_context():
        _, group = _setup()
        _quiz_row(group)
    _login_as(client, "teacher@example.com")
    body = client.get("/teacher/dashboard").get_data(as_text=True).lower()
    for absent in ("quizzes to publish", "pending quizzes", "quiz results", "unpublished quizzes"):
        assert absent not in body, absent
