"""M13 read-only query service -- SQL-scoped authorization, the M11/M12
effective-visibility formula, Group-contextual Course results,
deterministic ranking, non-disclosing ``has_more``, bounded query count,
and field coverage. Exercises ``app/services/search_queries.py`` directly
(no HTTP / login) for speed and precision.
"""

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import (
    AcademicStatus,
    AcademicTerm,
    Course,
    Enrollment,
    EnrollmentStatus,
    Group,
    Lesson,
    LessonStatus,
    Level,
    Material,
    MaterialKind,
    Unit,
    UploadedFile,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password
from app.services.search_queries import (
    authorized_search_groups,
    search_learning_content,
)
from app.services.search_terms import normalize_query

PW = "Sup3rSecret!123"
ACTIVE = AcademicStatus.ACTIVE.value


def _student(email="stud@example.com"):
    u = User(email=email, password_hash=hash_password(PW), full_name=email.split("@")[0],
             role=UserRole.STUDENT.value, status=UserStatus.ACTIVE.value)
    db.session.add(u)
    db.session.commit()
    return u


def _hierarchy(name="A", term_status=ACTIVE, level_status=ACTIVE, course_status=ACTIVE,
               group_status=ACTIVE, course_title="General English", course_code="ENG101",
               course_description="Grammar and vocabulary practice"):
    term = AcademicTerm(name=f"Term {name}", start_date=date(2026, 1, 1),
                        end_date=date(2026, 12, 31), status=term_status)
    level = Level(name=f"Level {name}", display_order=0, status=level_status)
    db.session.add_all([term, level])
    db.session.commit()
    course = Course(title=course_title, code=course_code, description=course_description,
                    level_id=level.id, display_order=0, status=course_status)
    db.session.add(course)
    db.session.commit()
    group = Group(academic_term_id=term.id, course_id=course.id, name=f"Group {name}",
                  capacity=30, status=group_status)
    db.session.add(group)
    db.session.commit()
    return group


def _enroll(group, student, status=EnrollmentStatus.ACTIVE.value):
    db.session.add(Enrollment(student_id=student.id, group_id=group.id, status=status))
    db.session.commit()


def _unit(group, title="Present Tenses", status=ACTIVE, display_order=0, keywords=None):
    u = Unit(group_id=group.id, title=title, display_order=display_order, status=status,
             search_keywords=keywords)
    db.session.add(u)
    db.session.commit()
    return u


def _lesson(unit, title="Present Perfect", status=LessonStatus.PUBLISHED.value,
            display_order=0, keywords=None, description=None):
    published_at = datetime.now(timezone.utc) if status == LessonStatus.PUBLISHED.value else None
    lsn = Lesson(unit_id=unit.id, title=title, display_order=display_order, status=status,
                 published_at=published_at, search_keywords=keywords, description=description)
    db.session.add(lsn)
    db.session.commit()
    return lsn


def _material(lesson, title="Homework", kind=MaterialKind.EXTERNAL_LINK.value, status=ACTIVE,
              display_order=1, keywords=None, external_url="https://example.com/x",
              nonce=None, filename=None):
    kwargs = dict(lesson_id=lesson.id, title=title, kind=kind, status=status,
                  display_order=display_order, search_keywords=keywords,
                  creation_nonce=nonce or f"n-{title}-{lesson.id}")
    if kind == MaterialKind.EXTERNAL_LINK.value:
        kwargs["external_url"] = external_url
    elif kind == MaterialKind.RICH_TEXT.value:
        kwargs["content_html"] = "<p>body</p>"
    elif kind == MaterialKind.FILE.value:
        uf = UploadedFile(storage_key=f"key-{title}-{lesson.id}", original_filename=filename or "notes.pdf",
                          extension="pdf", category="document", content_type="application/pdf",
                          byte_size=10, sha256="0" * 64, uploaded_by_id=_uploader_id())
        db.session.add(uf)
        db.session.commit()
        kwargs["uploaded_file_id"] = uf.id
    m = Material(**kwargs)
    db.session.add(m)
    db.session.commit()
    return m


def _uploader_id():
    u = User.query.filter_by(email="uploader@example.com").first()
    if u is None:
        u = User(email="uploader@example.com", password_hash=hash_password(PW),
                 full_name="Up", role=UserRole.TEACHER.value, status=UserStatus.ACTIVE.value)
        db.session.add(u)
        db.session.commit()
    return u.id


def _run(student, q, **kw):
    return search_learning_content(student.id, normalize_query(q), **kw)


def _titles(section):
    return [item["title"] for item in section["items"]]


# ===========================================================================
# authorized_search_groups
# ===========================================================================


def test_authorized_groups_only_active_enrollment_and_hierarchy(app):
    with app.app_context():
        s = _student()
        good = _hierarchy("Good")
        _enroll(good, s)
        withdrawn = _hierarchy("WD")
        _enroll(withdrawn, s, status=EnrollmentStatus.WITHDRAWN.value)
        archived = _hierarchy("Arch", group_status=AcademicStatus.ARCHIVED.value)
        _enroll(archived, s)
        groups = authorized_search_groups(s.id)
        assert [g["name"] for g in groups] == ["Group Good"]
        assert groups[0]["course_title"] == "General English"


# ===========================================================================
# Field coverage -- each type found via each of its searchable fields
# ===========================================================================


def test_course_found_by_title_code_description(app):
    with app.app_context():
        s = _student()
        g = _hierarchy("A", course_title="Business Writing", course_code="BW200",
                       course_description="memos and reports")
        _enroll(g, s)
        assert _titles(_run(s, "business")["course"]) == ["Business Writing"]
        assert _titles(_run(s, "bw200")["course"]) == ["Business Writing"]
        assert _titles(_run(s, "memos")["course"]) == ["Business Writing"]


def test_unit_found_by_description_and_keywords(app):
    with app.app_context():
        s = _student()
        g = _hierarchy("A")
        _enroll(g, s)
        _unit(g, title="Grammar Basics", keywords="tenses, verb forms")
        db.session.query(Unit).first().description = "covers articles and prepositions"
        db.session.commit()
        assert _titles(_run(s, "prepositions")["unit"]) == ["Grammar Basics"]
        assert _titles(_run(s, "verb forms")["unit"]) == ["Grammar Basics"]


def test_lesson_found_by_keywords(app):
    with app.app_context():
        s = _student()
        g = _hierarchy("A")
        _enroll(g, s)
        u = _unit(g)
        _lesson(u, title="Irregular Verbs", keywords="conjugation, past participle")
        assert _titles(_run(s, "participle")["lesson"]) == ["Irregular Verbs"]


def test_material_found_by_keywords_url_and_filename(app):
    with app.app_context():
        s = _student()
        g = _hierarchy("A")
        _enroll(g, s)
        u = _unit(g)
        lsn = _lesson(u)
        _material(lsn, title="Link One", external_url="https://grammarhub.example.com/unit1",
                  keywords="drills")
        _material(lsn, title="Doc Two", kind=MaterialKind.FILE.value,
                  filename="present-perfect-worksheet.pdf", display_order=2)
        assert _titles(_run(s, "grammarhub")["material"]) == ["Link One"]
        assert _titles(_run(s, "drills")["material"]) == ["Link One"]
        assert _titles(_run(s, "worksheet")["material"]) == ["Doc Two"]


# ===========================================================================
# Visibility formula
# ===========================================================================


def test_withdrawn_enrollment_sees_nothing(app):
    with app.app_context():
        s = _student()
        g = _hierarchy("A")
        _enroll(g, s, status=EnrollmentStatus.WITHDRAWN.value)
        u = _unit(g)
        _lesson(u)
        results = _run(s, "present")
        assert all(section["items"] == [] for section in results.values())


@pytest.mark.parametrize("archived", ["term_status", "level_status", "course_status", "group_status"])
def test_archived_ancestor_hides_everything(app, archived):
    with app.app_context():
        s = _student()
        g = _hierarchy("A", **{archived: AcademicStatus.ARCHIVED.value})
        _enroll(g, s)
        u = _unit(g)
        lsn = _lesson(u)
        _material(lsn)
        results = _run(s, "present")
        assert all(section["items"] == [] for section in results.values())


def test_archived_unit_hides_unit_lesson_material_but_not_course(app):
    with app.app_context():
        s = _student()
        g = _hierarchy("A", course_title="Present Studies")
        _enroll(g, s)
        u = _unit(g, title="Present Tenses", status=AcademicStatus.ARCHIVED.value)
        lsn = _lesson(u)
        _material(lsn)
        results = _run(s, "present")
        assert _titles(results["course"]) == ["Present Studies"]
        assert results["unit"]["items"] == []
        assert results["lesson"]["items"] == []
        assert results["material"]["items"] == []


def test_draft_lesson_hidden_and_its_materials_hidden(app):
    with app.app_context():
        s = _student()
        g = _hierarchy("A")
        _enroll(g, s)
        u = _unit(g)
        lsn = _lesson(u, title="Present Draft", status=LessonStatus.DRAFT.value)
        _material(lsn, title="Present Material")
        results = _run(s, "present")
        assert results["lesson"]["items"] == []
        assert results["material"]["items"] == []


def test_archived_material_hidden(app):
    with app.app_context():
        s = _student()
        g = _hierarchy("A")
        _enroll(g, s)
        u = _unit(g)
        lsn = _lesson(u)
        _material(lsn, title="Present Archived", status=AcademicStatus.ARCHIVED.value)
        assert _run(s, "present")["material"]["items"] == []


def test_cross_student_isolation(app):
    with app.app_context():
        mine = _student("mine@example.com")
        other = _student("other@example.com")
        g = _hierarchy("A")
        _enroll(g, other)
        u = _unit(g)
        _lesson(u)
        assert all(sec["items"] == [] for sec in _run(mine, "present").values())


def test_no_schedule_or_assignment_dependency(app):
    with app.app_context():
        s = _student()
        g = _hierarchy("A")
        _enroll(g, s)
        u = _unit(g)
        _lesson(u)
        # no Schedule row, no GroupTeacherAssignment row anywhere
        assert _titles(_run(s, "present")["lesson"]) == ["Present Perfect"]


# ===========================================================================
# Group-contextual Course results
# ===========================================================================


def test_course_appears_once_per_authorized_group_context(app):
    with app.app_context():
        s = _student()
        term = AcademicTerm(name="Shared Term", start_date=date(2026, 1, 1),
                            end_date=date(2026, 12, 31))
        level = Level(name="Shared Level", display_order=0)
        db.session.add_all([term, level])
        db.session.commit()
        course = Course(title="Shared English", code="SH1", level_id=level.id, display_order=0)
        db.session.add(course)
        db.session.commit()
        g1 = Group(academic_term_id=term.id, course_id=course.id, name="Group One", capacity=30)
        g2 = Group(academic_term_id=term.id, course_id=course.id, name="Group Two", capacity=30)
        db.session.add_all([g1, g2])
        db.session.commit()
        _enroll(g1, s)
        _enroll(g2, s)
        items = _run(s, "shared english")["course"]["items"]
        assert len(items) == 2
        assert {i["group_public_id"] for i in items} == {g1.public_id, g2.public_id}


# ===========================================================================
# Ranking / tokens / literals / bounds
# ===========================================================================


def test_deterministic_ranking_exact_prefix_contains_description(app):
    with app.app_context():
        s = _student()
        g = _hierarchy("A")
        _enroll(g, s)
        u = _unit(g)
        _lesson(u, title="alpha extra", display_order=1)                 # prefix
        _lesson(u, title="zzz alpha zzz", display_order=2)               # contains
        _lesson(u, title="alpha", display_order=3)                       # exact
        _lesson(u, title="Other", display_order=4, description="an alpha note")  # description only
        assert _titles(_run(s, "alpha")["lesson"]) == [
            "alpha", "alpha extra", "zzz alpha zzz", "Other"
        ]


def test_multi_token_and_across_tokens_or_across_fields(app):
    with app.app_context():
        s = _student()
        g = _hierarchy("A")
        _enroll(g, s)
        u = _unit(g)
        _lesson(u, title="Reading practice", keywords="fluency", display_order=1)
        _lesson(u, title="Reading only", display_order=2)
        # "reading" hits title, "fluency" hits keywords -> AND satisfied via OR-of-fields
        assert _titles(_run(s, "reading fluency")["lesson"]) == ["Reading practice"]


def test_literal_percent_and_underscore(app):
    with app.app_context():
        s = _student()
        g = _hierarchy("A")
        _enroll(g, s)
        u = _unit(g)
        _lesson(u, title="100% effort", display_order=1)
        _lesson(u, title="50 pass rate", display_order=2)
        _lesson(u, title="snake_case rules", display_order=3)
        _lesson(u, title="snakeXcase decoy", display_order=4)
        # '%' is literal: "100%" must not also match "50 pass rate"
        assert _titles(_run(s, "100%")["lesson"]) == ["100% effort"]
        # '_' is literal, not a single-char wildcard: "snake_case" must not
        # match "snakeXcase".
        assert _titles(_run(s, "snake_case")["lesson"]) == ["snake_case rules"]


def test_literal_backslash_in_query_is_escaped_not_a_like_escape(app):
    """The configured LIKE escape character (``\\``) is neutralised by
    ``escape_like`` inside the *actual* query: a literal backslash in the
    search text matches a row that literally contains it, and is never
    consumed as an uncontrolled LIKE escape or turned into a wildcard.
    """
    with app.app_context():
        s = _student()
        outsider = _student("outsider@example.com")
        g = _hierarchy("A")
        _enroll(g, s)
        u = _unit(g)
        _lesson(u, title=r"path C:\notes archive", display_order=1)
        _lesson(u, title="path Cnotes archive", display_order=2)        # backslash removed
        _lesson(u, title=r"code disc\%ount final", display_order=3)
        _lesson(u, title="code discANYount final", display_order=4)     # '\%' -> arbitrary run
        _lesson(u, title=r"windows path D:\ ok", display_order=5)       # trailing backslash

        # a literal backslash matches only the row that literally contains
        # it -- not the backslash-free decoy.
        assert _titles(_run(s, r"c:\notes")["lesson"]) == [r"path C:\notes archive"]

        # backslash + '%' stays a two-literal-character match: it hits the
        # '\%' row only, never the wildcard-style decoy.
        assert _titles(_run(s, r"disc\%ount")["lesson"]) == [r"code disc\%ount final"]

        # a lone trailing backslash query neither raises nor turns into a
        # dangling escape.
        assert r"windows path D:\ ok" in _titles(_run(s, "d:\\")["lesson"])

        # result shape unchanged -- plain dict, public ids only.
        item = _run(s, r"c:\notes")["lesson"]["items"][0]
        assert set(item) == {"type", "title", "badge", "breadcrumb", "snippet",
                             "group_public_id", "unit_public_id", "lesson_public_id"}
        for forbidden in ("id", "lesson_id", "unit_id", "group_id"):
            assert forbidden not in item

        # authorization unchanged: a non-enrolled student still sees nothing.
        assert _run(outsider, r"c:\notes")["lesson"]["items"] == []


def test_has_more_is_non_disclosing_and_capped_at_20(app):
    with app.app_context():
        s = _student()
        g = _hierarchy("A")
        _enroll(g, s)
        u = _unit(g)
        for i in range(25):
            _lesson(u, title=f"Present Lesson {i:02d}", display_order=i)
        section = _run(s, "present")["lesson"]
        assert len(section["items"]) == 20
        assert section["has_more"] is True


def test_content_type_filter_runs_only_that_type(app):
    with app.app_context():
        s = _student()
        g = _hierarchy("A", course_title="Present Studies")
        _enroll(g, s)
        u = _unit(g, title="Present Tenses")
        _lesson(u)
        results = _run(s, "present", content_type="unit")
        assert set(results) == {"unit"}
        assert _titles(results["unit"]) == ["Present Tenses"]


def test_material_kind_filter(app):
    with app.app_context():
        s = _student()
        g = _hierarchy("A")
        _enroll(g, s)
        u = _unit(g)
        lsn = _lesson(u)
        _material(lsn, title="Present Link", kind=MaterialKind.EXTERNAL_LINK.value, display_order=1)
        _material(lsn, title="Present File", kind=MaterialKind.FILE.value, display_order=2)
        got = _run(s, "present", material_kind="file")["material"]
        assert _titles(got) == ["Present File"]


def test_group_filter_restricts_results(app):
    with app.app_context():
        s = _student()
        g1 = _hierarchy("One", course_title="Present One")
        g2 = _hierarchy("Two", course_title="Present Two")
        _enroll(g1, s)
        _enroll(g2, s)
        got = _run(s, "present", group_public_id=g1.public_id)["course"]
        assert _titles(got) == ["Present One"]


def test_bounded_query_count_with_many_rows(app):
    with app.app_context():
        s = _student()
        g = _hierarchy("A")
        _enroll(g, s)
        for i in range(8):
            u = _unit(g, title=f"Present U{i}", display_order=i)
            for j in range(6):
                lsn = _lesson(u, title=f"Present U{i} L{j}", display_order=j)
                _material(lsn, title=f"Present U{i} L{j} M", display_order=1)
        statements = []
        ev = lambda conn, cur, st, p, ctx, em: statements.append(st)
        event.listen(db.engine, "before_cursor_execute", ev)
        try:
            authorized_search_groups(s.id)
            _run(s, "present")
        finally:
            event.remove(db.engine, "before_cursor_execute", ev)
        selects = [x for x in statements if x.strip().upper().startswith("SELECT")]
        # One query per requested content type, never one per row. Phase 4 /
        # M09 added a fifth type (announcements), so the bound is one higher
        # than M13's -- and is still a constant, not a function of how many
        # rows exist.
        assert len(selects) <= 7, (len(selects), selects)


def test_result_dicts_carry_no_internal_ids(app):
    with app.app_context():
        s = _student()
        g = _hierarchy("A")
        _enroll(g, s)
        u = _unit(g)
        lsn = _lesson(u)
        m = _material(lsn, kind=MaterialKind.FILE.value, filename="secret-notes.pdf")
        results = _run(s, "present notes homework general", content_type="all")
        blob = repr(results)
        for forbidden in ("storage_key", "sha256", "uploaded_file_id", "'id'", "creation_nonce"):
            assert forbidden not in blob
