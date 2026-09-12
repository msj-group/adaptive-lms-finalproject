"""Phase 4 / M09 -- announcements in the authorized Student search.

The M13 search gains a fifth result type. Its authorization is the M09
visibility rule rather than the M13 enrollment chain -- a Centre notice is
readable by any active Student, with or without an enrollment -- and the
clause is imported from the announcement query layer rather than restated,
so the search cannot become one row more generous than the feed.

What these tests are mostly about is the negative direction: a hidden
announcement must not influence the result list, the ``has_more`` notice,
the ranking, or a snippet, and must not be reachable through the Teacher
feed's own filter either.
"""

import pytest
from sqlalchemy import event

import tests.announcement_fixtures as fx
from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    AnnouncementStatus,
    Course,
    UserRole,
)
from app.services.search_queries import search_learning_content
from app.services.search_terms import normalize_content_type, normalize_query

SEARCH = "/student/search"

_WITHDRAWN = AnnouncementStatus.WITHDRAWN.value


def _author():
    return fx.user("author@example.com", UserRole.ADMINISTRATOR.value)


def _run(student, text, **kwargs):
    return search_learning_content(student.id, normalize_query(text), **kwargs)


def _titles(section):
    return [item["title"] for item in section["items"]]


# ===========================================================================
# The type itself
# ===========================================================================


def test_announcement_is_an_accepted_content_type():
    from app.services.search_queries import CONTENT_TYPE_ORDER
    from app.services.search_terms import CONTENT_TYPES

    assert CONTENT_TYPE_ORDER == ("course", "unit", "lesson", "material", "announcement")
    assert "announcement" in CONTENT_TYPES
    assert normalize_content_type("announcement") == "announcement"
    assert normalize_content_type("announcements") == "all"


def test_the_existing_four_types_are_untouched():
    """M13's own result types, ordering and filter names are a contract."""
    from app.services.search_queries import CONTENT_TYPE_ORDER

    assert CONTENT_TYPE_ORDER[:4] == ("course", "unit", "lesson", "material")


# ===========================================================================
# What a Student's search may find
# ===========================================================================


def test_a_published_center_announcement_is_found_without_any_enrollment(app):
    with app.app_context():
        student = fx.user("s@example.com", UserRole.STUDENT.value)
        fx.published_center(_author(), title="Holiday closure", body="Closed Monday.")
        section = _run(student, "holiday", content_type="announcement")["announcement"]
        assert _titles(section) == ["Holiday closure"]


def test_a_course_and_a_group_announcement_are_found_through_membership(app):
    with app.app_context():
        group = fx.hierarchy("A")
        student = fx.enroll(group, "s@example.com")
        author = _author()
        course = db.session.get(Course, group.course_id)
        fx.published_course(author, course, title="Exam course notice")
        fx.published_group(author, group, title="Exam group notice")
        section = _run(student, "exam", content_type="announcement")["announcement"]
        assert sorted(_titles(section)) == ["Exam course notice", "Exam group notice"]


def test_the_body_is_searched_and_may_appear_in_the_snippet(app):
    with app.app_context():
        group = fx.hierarchy("A")
        student = fx.enroll(group, "s@example.com")
        fx.published_group(
            _author(), group, title="Notice", body="The projector has been repaired."
        )
        section = _run(student, "projector", content_type="announcement")["announcement"]
        assert _titles(section) == ["Notice"]
        assert "projector" in section["items"][0]["snippet"].lower()


def test_a_draft_or_withdrawn_announcement_is_never_found(app):
    with app.app_context():
        group = fx.hierarchy("A")
        student = fx.enroll(group, "s@example.com")
        author = _author()
        fx.for_group(author, group, title="Exam draft", body="Secret draft body.")
        fx.for_group(
            author, group, status=_WITHDRAWN, title="Exam withdrawn",
            body="Withdrawn body.",
        )
        section = _run(student, "exam", content_type="announcement")["announcement"]
        assert section["items"] == []
        assert section["has_more"] is False
        # Not even through a word that only the hidden bodies contain.
        for word in ("secret", "withdrawn"):
            hidden = _run(student, word, content_type="announcement")["announcement"]
            assert hidden["items"] == []


def test_another_groups_or_courses_announcement_is_never_found(app):
    with app.app_context():
        mine = fx.hierarchy("A")
        theirs = fx.hierarchy("B")
        student = fx.enroll(mine, "s@example.com")
        author = _author()
        other_course = db.session.get(Course, theirs.course_id)
        fx.published_group(author, theirs, title="Exam elsewhere")
        fx.published_course(author, other_course, title="Exam other course")
        section = _run(student, "exam", content_type="announcement")["announcement"]
        assert section["items"] == []


def test_losing_the_enrollment_removes_it_from_the_search(app):
    with app.app_context():
        group = fx.hierarchy("A")
        student = fx.enroll(group, "s@example.com")
        fx.published_group(_author(), group, title="Exam notice")
        assert _titles(
            _run(student, "exam", content_type="announcement")["announcement"]
        ) == ["Exam notice"]
        fx.withdraw_enrollment(group, student)
        assert _run(student, "exam", content_type="announcement")["announcement"][
            "items"
        ] == []


@pytest.mark.parametrize(
    "archived", ["term_status", "level_status", "course_status", "group_status"]
)
def test_an_archived_link_removes_the_scoped_announcement_from_the_search(app, archived):
    with app.app_context():
        group = fx.hierarchy("A")
        student = fx.enroll(group, "s@example.com")
        author = _author()
        course = db.session.get(Course, group.course_id)
        fx.published_group(author, group, title="Exam group")
        fx.published_course(author, course, title="Exam course")
        fx.published_center(author, title="Exam centre")
        from app.models import Level

        row = {
            "term_status": AcademicTerm.query.one(),
            "level_status": Level.query.one(),
            "course_status": course,
            "group_status": group,
        }[archived]
        row.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
        found = _titles(_run(student, "exam", content_type="announcement")["announcement"])
        assert found == ["Exam centre"]


def test_one_course_announcement_is_one_result_for_a_student_in_several_groups(app):
    with app.app_context():
        term = fx.term("T")
        level = fx.level("L")
        course = fx.course(level, "C")
        groups = [fx.group(term, course, f"G{i}") for i in range(3)]
        student = fx.enroll(groups[0], "s@example.com")
        for group in groups[1:]:
            fx.enroll_existing(group, student)
        fx.published_course(_author(), course, title="Exam notice")
        section = _run(student, "exam", content_type="announcement")["announcement"]
        assert _titles(section) == ["Exam notice"]


def test_the_group_filter_narrows_to_that_group_and_its_course(app):
    with app.app_context():
        term = fx.term("T")
        level = fx.level("L")
        course_one = fx.course(level, "C1")
        course_two = fx.course(level, "C2")
        group_one = fx.group(term, course_one, "One")
        group_two = fx.group(term, course_two, "Two")
        student = fx.enroll(group_one, "s@example.com")
        fx.enroll_existing(group_two, student)
        author = _author()
        fx.published_group(author, group_one, title="Exam one group")
        fx.published_course(author, course_one, title="Exam one course")
        fx.published_group(author, group_two, title="Exam two group")
        fx.published_center(author, title="Exam centre")
        section = _run(
            student,
            "exam",
            content_type="announcement",
            group_public_id=group_one.public_id,
        )["announcement"]
        # The chosen Group's own notice and its Course's notice, and
        # nothing from the other Group. A centre notice belongs to no
        # Group, so a Group filter narrows it away rather than widening.
        assert sorted(_titles(section)) == ["Exam one course", "Exam one group"]


def test_the_snippet_and_result_count_reveal_nothing_hidden(app):
    """A hidden announcement is not filtered out of the results -- it is
    never selected, so it cannot influence the count, the ``has_more``
    flag, the ranking or a snippet."""
    with app.app_context():
        mine = fx.hierarchy("A")
        theirs = fx.hierarchy("B")
        student = fx.enroll(mine, "s@example.com")
        author = _author()
        for index in range(30):
            fx.published_group(
                author, theirs, title=f"Exam hidden {index:03d}", body="HIDDEN BODY"
            )
        fx.published_group(author, mine, title="Exam mine", body="Visible body")
        section = _run(student, "exam", content_type="announcement")["announcement"]
        assert _titles(section) == ["Exam mine"]
        assert section["has_more"] is False
        assert "HIDDEN" not in repr(section)


def test_a_literal_percent_or_underscore_matches_literally(app):
    with app.app_context():
        group = fx.hierarchy("A")
        student = fx.enroll(group, "s@example.com")
        author = _author()
        fx.published_group(author, group, title="Fees 50% off", body="Body one.")
        fx.published_group(author, group, title="Plain notice", body="Body two.")
        section = _run(student, "50%", content_type="announcement")["announcement"]
        assert _titles(section) == ["Fees 50% off"]


def test_every_token_must_match_somewhere(app):
    with app.app_context():
        group = fx.hierarchy("A")
        student = fx.enroll(group, "s@example.com")
        author = _author()
        fx.published_group(author, group, title="Exam timetable", body="Room four.")
        fx.published_group(author, group, title="Holiday", body="Room four.")
        section = _run(student, "exam room", content_type="announcement")["announcement"]
        assert _titles(section) == ["Exam timetable"]


def test_the_result_dict_carries_no_internal_id_and_no_author(app):
    with app.app_context():
        group = fx.hierarchy("A")
        student = fx.enroll(group, "s@example.com")
        author = fx.user("writer@example.com", UserRole.TEACHER.value)
        fx.published_group(author, group, title="Exam notice")
        section = _run(student, "exam", content_type="announcement")["announcement"]
        blob = repr(section)
        for forbidden in ("author", "'id'", "version", "writer@example.com", "status"):
            assert forbidden not in blob, forbidden


def test_the_search_stays_bounded_with_many_announcements(app):
    with app.app_context():
        group = fx.hierarchy("A")
        student = fx.enroll(group, "s@example.com")
        author = _author()
        for index in range(40):
            fx.published_group(author, group, title=f"Exam {index:03d}")
        # Resolve every scalar the call needs BEFORE recording starts, so a
        # refresh of the fixture's own ORM row is not mistaken for a search
        # query.
        student_id = student.id
        query_norm = normalize_query("exam")
        statements = []

        def _rec(conn, cur, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", _rec)
        try:
            search_learning_content(student_id, query_norm)
        finally:
            event.remove(db.engine, "before_cursor_execute", _rec)
        selects = [s for s in statements if s.strip().upper().startswith("SELECT")]
        # One query per requested content type, never one per row.
        assert len(selects) <= 5, (len(selects), selects)


# ===========================================================================
# The search page itself
# ===========================================================================


def test_the_page_offers_an_announcements_filter_and_section(app, client):
    with app.app_context():
        group = fx.hierarchy("A")
        fx.enroll(group, "s@example.com")
        fx.published_group(_author(), group, title="Exam timetable")
    fx.login_as(client, "s@example.com")
    html = client.get(SEARCH, query_string={"q": "exam"}).get_data(as_text=True)
    assert "<h2>Announcements</h2>" in html
    assert "Exam timetable" in html
    assert '<option value="announcement"' in html


def test_a_result_links_to_the_authorized_detail_page(app, client):
    with app.app_context():
        group = fx.hierarchy("A")
        fx.enroll(group, "s@example.com")
        ann = fx.published_group(_author(), group, title="Exam timetable")
        apid = ann.public_id
    fx.login_as(client, "s@example.com")
    html = client.get(SEARCH, query_string={"q": "exam"}).get_data(as_text=True)
    assert fx.student_detail(apid) in html
    assert client.get(fx.student_detail(apid)).status_code == 200


def test_the_type_filter_limits_the_page_to_announcements(app, client):
    with app.app_context():
        group = fx.hierarchy("A")
        fx.enroll(group, "s@example.com")
        fx.published_group(_author(), group, title="Exam timetable")
    fx.login_as(client, "s@example.com")
    html = client.get(
        SEARCH, query_string={"q": "exam", "type": "announcement"}
    ).get_data(as_text=True)
    assert "<h2>Announcements</h2>" in html
    assert "<h2>Lessons</h2>" not in html


def test_an_unauthorized_announcement_title_never_appears_on_the_page(app, client):
    with app.app_context():
        mine = fx.hierarchy("A")
        theirs = fx.hierarchy("B")
        fx.enroll(mine, "s@example.com")
        fx.published_group(_author(), theirs, title="Exam somewhere else")
    fx.login_as(client, "s@example.com")
    html = client.get(SEARCH, query_string={"q": "exam"}).get_data(as_text=True)
    assert "Exam somewhere else" not in html
    assert "No matching content" in html


def test_the_search_response_stays_private_and_non_cacheable(app, client):
    with app.app_context():
        group = fx.hierarchy("A")
        fx.enroll(group, "s@example.com")
        fx.published_group(_author(), group, title="Exam timetable")
    fx.login_as(client, "s@example.com")
    response = client.get(SEARCH, query_string={"q": "exam"})
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in response.headers["Vary"]


def test_there_is_still_no_teacher_or_administrator_global_search_page(app, client):
    """M13 made the global search Student-only. M09 adds announcements to
    it and changes nothing about who may open it."""
    with app.app_context():
        fx.user("t@example.com", UserRole.TEACHER.value)
        fx.user("a@example.com", UserRole.ADMINISTRATOR.value)
    for email in ("t@example.com", "a@example.com"):
        fx.login_as(client, email)
        assert client.get(SEARCH).status_code == 403
    assert not [
        str(rule)
        for rule in app.url_map.iter_rules()
        if str(rule) in ("/teacher/search", "/admin/search")
    ]
