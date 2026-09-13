"""Phase 4 / M13 -- the Student dashboard's Continue Learning, Recently
Opened and Group progress: ordering, bounds, visibility, privacy, empty
states, no writes, and query counts that do not grow with the data.
"""

from datetime import timedelta

import tests.lesson_progress_fixtures as fx
from app.extensions import db
from app.models import Group, Lesson, Unit
from app.services import lesson_progress_queries as queries


def _html(response):
    return response.get_data(as_text=True)


def _student(email="student@example.com"):
    return fx.user(email, fx.STUDENT)


def _group(student, label):
    group = fx.hierarchy(label)
    fx.enroll(group, student)
    return group


# ===========================================================================
# Continue Learning
# ===========================================================================


def test_continue_learning_prefers_the_most_recently_opened_incomplete_lesson(app):
    with app.app_context():
        student = _student()
        alpha, beta = _group(student, "A"), _group(student, "B")
        unit_a, unit_b = fx.unit(alpha), fx.unit(beta)
        first = fx.lesson(unit_a, "First", 0)
        done = fx.lesson(unit_a, "Done", 1)
        latest = fx.lesson(unit_b, "Latest", 0)
        fx.progress(student, alpha, first, last_opened_at=fx.NOW)
        fx.progress(student, alpha, done, last_opened_at=fx.LATEST, completed_at=fx.LATEST)
        fx.progress(student, beta, latest, last_opened_at=fx.LATER)

        result = queries.continue_learning(student.id)
        assert result == {
            "lesson_public_id": latest.public_id,
            "lesson_title": "Latest",
            "unit_public_id": unit_b.public_id,
            "unit_title": "Unit 1",
            "group_public_id": beta.public_id,
            "group_name": "Group B",
            "course_title": "Course B",
            "resume": True,
        }
        row = fx.progress_of(student.id, beta.id, latest.id)
        row.completed_at, row.version = fx.LATEST, 2
        db.session.commit()
        assert queries.continue_learning(student.id)["lesson_public_id"] == first.public_id


def test_continue_learning_falls_back_to_the_earliest_incomplete_lesson_in_teaching_order(app):
    with app.app_context():
        student = _student()
        # Group B is created first; name order still puts Group A first.
        beta, alpha = _group(student, "B"), _group(student, "A")
        fx.lesson(fx.unit(beta), "Beta lesson", 0)
        later_unit = fx.unit(alpha, "Second unit", 5)
        early_unit = fx.unit(alpha, "First unit", 1)
        fx.lesson(later_unit, "Later unit lesson", 0)
        third = fx.lesson(early_unit, "Third", 9)
        second = fx.lesson(early_unit, "Second", 2)
        first = fx.lesson(early_unit, "First", 2)  # same order, higher id than Second
        opened_elsewhere = fx.lesson(early_unit, "Opened but completed", 0)
        fx.progress(student, alpha, opened_elsewhere, last_opened_at=fx.LATEST,
                    completed_at=fx.LATEST)

        result = queries.continue_learning(student.id)
        assert result["lesson_public_id"] == second.public_id
        assert result["resume"] is False
        fx.progress(student, alpha, second, completed_at=fx.NOW)
        assert queries.continue_learning(student.id)["lesson_public_id"] == first.public_id
        fx.progress(student, alpha, first, completed_at=fx.NOW)
        assert queries.continue_learning(student.id)["lesson_public_id"] == third.public_id


def test_continue_learning_breaks_same_second_ties_by_teaching_order(app):
    with app.app_context():
        student = _student()
        group = _group(student, "A")
        unit = fx.unit(group)
        late = fx.lesson(unit, "Late", 3)
        early = fx.lesson(unit, "Early", 1)
        fx.progress(student, group, late, last_opened_at=fx.NOW)
        fx.progress(student, group, early, last_opened_at=fx.NOW)
        assert queries.continue_learning(student.id)["lesson_public_id"] == early.public_id


def test_continue_learning_never_names_an_unavailable_lesson(app):
    with app.app_context():
        student = _student()
        alpha, beta, gamma = _group(student, "A"), _group(student, "B"), _group(student, "C")
        unit_a = fx.unit(alpha)
        archived_unit = fx.unit(alpha, "Archived unit", 1)
        fallback = fx.lesson(unit_a, "Fallback", 0)
        drafted = fx.lesson(unit_a, "Drafted", 1)
        in_archived_unit = fx.lesson(archived_unit, "In archived unit", 0)
        withdrawn_lesson = fx.lesson(fx.unit(beta), "Withdrawn group lesson", 0)
        archived_group_lesson = fx.lesson(fx.unit(gamma), "Archived group lesson", 0)
        fx.progress(student, alpha, drafted, last_opened_at=fx.LATEST)
        fx.progress(student, alpha, in_archived_unit, last_opened_at=fx.LATEST)
        fx.progress(student, beta, withdrawn_lesson, last_opened_at=fx.LATEST)
        fx.progress(student, gamma, archived_group_lesson, last_opened_at=fx.LATEST)
        # A row bound to Group A for Group B's lesson is never a destination.
        fx.progress(student, alpha, withdrawn_lesson, last_opened_at=fx.LATEST + timedelta(hours=1))
        fx.progress(student, alpha, fallback, last_opened_at=fx.NOW)

        fx.unpublish(drafted)
        fx.set_status(archived_unit, fx.ARCHIVED)
        fx.set_status(fx.enrollment_of(beta, student), fx.WITHDRAWN)
        fx.set_status(gamma, fx.ARCHIVED)
        assert queries.continue_learning(student.id)["lesson_public_id"] == fallback.public_id

        row = fx.progress_of(student.id, alpha.id, fallback.id)
        row.completed_at, row.version = fx.LATEST, 2
        db.session.commit()
        assert queries.continue_learning(student.id) is None


def test_continue_learning_is_private_to_the_student(app):
    with app.app_context():
        student, classmate = _student(), _student("classmate@example.com")
        group = _group(student, "A")
        fx.enroll(group, classmate)
        unit = fx.unit(group)
        mine = fx.lesson(unit, "Mine", 0)
        theirs = fx.lesson(unit, "Theirs", 1)
        fx.progress(classmate, group, theirs, last_opened_at=fx.LATEST)
        fx.progress(classmate, group, mine, completed_at=fx.LATEST)
        assert queries.continue_learning(student.id)["lesson_public_id"] == mine.public_id
        assert queries.continue_learning(classmate.id)["lesson_public_id"] == theirs.public_id


# ===========================================================================
# Recently Opened
# ===========================================================================


def test_recently_opened_is_newest_first_bounded_and_private(app):
    with app.app_context():
        student, classmate = _student(), _student("classmate@example.com")
        group = _group(student, "A")
        fx.enroll(group, classmate)
        unit = fx.unit(group)
        lessons = [fx.lesson(unit, f"L{index}", index) for index in range(8)]
        for index, lesson in enumerate(lessons[:7]):
            fx.progress(student, group, lesson, last_opened_at=fx.NOW + timedelta(minutes=index),
                        completed_at=fx.NOW if index == 5 else None)
        # Completed without being opened: not an opening.
        fx.progress(student, group, lessons[7], completed_at=fx.LATEST)
        fx.progress(classmate, group, lessons[0], last_opened_at=fx.LATEST + timedelta(days=1))

        view = queries.build_recent_view(queries.recently_opened(student.id), "UTC")
        assert [item["lesson_title"] for item in view] == ["L6", "L5", "L4", "L3", "L2"]
        assert len(view) == queries.RECENTLY_OPENED_LIMIT
        assert [item["is_completed"] for item in view] == [False, True, False, False, False]
        assert view[0]["opened_local"] == fx.NOW + timedelta(minutes=6)
        assert set(view[0]) == {"lesson_public_id", "lesson_title", "unit_public_id",
                                "unit_title", "group_public_id", "group_name", "opened_local",
                                "is_completed"}


def test_recently_opened_breaks_ties_by_newest_row_and_skips_unavailable_lessons(app):
    with app.app_context():
        student = _student()
        alpha, beta = _group(student, "A"), _group(student, "B")
        unit = fx.unit(alpha)
        older_row = fx.lesson(unit, "Older row", 0)
        newer_row = fx.lesson(unit, "Newer row", 1)
        drafted = fx.lesson(unit, "Drafted", 2)
        foreign = fx.lesson(fx.unit(beta), "Other group", 0)
        fx.progress(student, alpha, older_row, last_opened_at=fx.NOW)
        fx.progress(student, alpha, newer_row, last_opened_at=fx.NOW)
        fx.progress(student, alpha, drafted, last_opened_at=fx.LATEST)
        fx.progress(student, alpha, foreign, last_opened_at=fx.LATEST)
        fx.unpublish(drafted)
        titles = [row[1] for row in queries.recently_opened(student.id)]
        assert titles == ["Newer row", "Older row"]
        fx.set_status(fx.enrollment_of(alpha, student), fx.WITHDRAWN)
        assert queries.recently_opened(student.id) == []


# ===========================================================================
# Group progress
# ===========================================================================


def test_group_progress_counts_only_visible_lessons_and_this_groups_rows(app):
    with app.app_context():
        student = _student()
        alpha, beta = _group(student, "A"), _group(student, "B")
        unit = fx.unit(alpha)
        archived_unit = fx.unit(alpha, "Archived", 1, status=fx.ARCHIVED)
        done = fx.lesson(unit, "Done", 0)
        fx.lesson(unit, "Open", 1)
        draft = fx.lesson(unit, "Draft", 2, status=fx.DRAFT)
        hidden = fx.lesson(archived_unit, "Hidden", 0)
        beta_lesson = fx.lesson(fx.unit(beta), "Beta", 0)
        fx.progress(student, alpha, done, completed_at=fx.NOW)
        fx.progress(student, alpha, draft, completed_at=fx.NOW)
        fx.progress(student, alpha, hidden, completed_at=fx.NOW)
        fx.progress(student, alpha, beta_lesson, completed_at=fx.NOW)  # bound to the wrong Group
        classmate = _student("classmate@example.com")
        fx.enroll(alpha, classmate)
        fx.progress(classmate, alpha, fx.lesson(unit, "Classmate", 3), completed_at=fx.NOW)

        groups, truncated = queries.student_group_progress(student.id)
        assert truncated is False
        assert [(g["group_name"], g["completed"], g["lesson_total"]) for g in groups] == [
            ("Group A", 1, 3), ("Group B", 0, 1)
        ]
        assert set(groups[0]) == {"group_public_id", "group_name", "course_title", "level_name",
                                  "lesson_total", "completed"}


def test_group_progress_lists_only_currently_open_groups_in_name_order_and_is_bounded(app):
    with app.app_context():
        student = _student()
        charlie, alpha, beta = _group(student, "C"), _group(student, "A"), _group(student, "B")
        withdrawn, archived = _group(student, "D"), _group(student, "E")
        fx.set_status(fx.enrollment_of(withdrawn, student), fx.WITHDRAWN)
        fx.set_status(archived, fx.ARCHIVED)
        other_student = _student("other@example.com")
        _group(other_student, "F")

        groups, truncated = queries.student_group_progress(student.id)
        assert [g["group_name"] for g in groups] == ["Group A", "Group B", "Group C"]
        assert truncated is False
        groups, truncated = queries.student_group_progress(student.id, limit=2)
        assert [g["group_name"] for g in groups] == ["Group A", "Group B"]
        assert truncated is True
        assert charlie and alpha and beta


# ===========================================================================
# The dashboard page
# ===========================================================================


def _dashboard_world():
    student, _teacher, group = fx.classroom("A")
    unit = fx.unit(group)
    opened = fx.lesson(unit, "Greetings", 0)
    fx.lesson(unit, "Numbers", 1)
    fx.progress(student, group, opened, last_opened_at=fx.NOW)
    return {"gp": group.public_id, "up": unit.public_id, "lp": opened.public_id}


def test_the_dashboard_renders_continue_learning_progress_and_recent_lessons(app, client):
    with app.app_context():
        ids = _dashboard_world()
    fx.login_as(client, "student@example.com")
    response = client.get(fx.STUDENT_DASHBOARD)
    html = _html(response)
    assert response.headers["Cache-Control"] == "private, no-store"
    lesson_href = f'href="{fx.lesson_url(ids["gp"], ids["up"], ids["lp"])}"'
    continue_section = html.split('id="continue-learning"', 1)[1].split('class="dash-section"', 1)[0]
    assert lesson_href in continue_section and "Continue lesson" in continue_section
    progress_section = html.split('id="lesson-progress"', 1)[1].split('id="recently-opened"', 1)[0]
    assert "0 of 2" in progress_section
    assert f'href="{fx.outline_url(ids["gp"])}"' in progress_section
    recent_section = html.split('id="recently-opened"', 1)[1].split("<h2>Your groups</h2>", 1)[0]
    assert "Greetings" in recent_section and lesson_href in recent_section
    assert "Not completed" in recent_section


def test_the_dashboard_shows_honest_empty_states(app, client):
    with app.app_context():
        student, _teacher, group = fx.classroom("A")
        fx.lesson(fx.unit(group), "Only lesson")
        lesson = Lesson.query.one()
        fx.progress(student, group, lesson, completed_at=fx.NOW)
        group_id = group.id
    fx.login_as(client, "student@example.com")
    html = _html(client.get(fx.STUDENT_DASHBOARD))
    assert "Nothing to continue right now" in html
    assert "No recently opened lessons" in html
    assert "1 of 1" in html

    with app.app_context():
        fx.set_status(db.session.get(Group, group_id), fx.ARCHIVED)
    html = _html(client.get(fx.STUDENT_DASHBOARD))
    assert "No lesson progress to show" in html
    assert "Nothing to continue right now" in html


def test_a_student_without_enrollments_gets_no_progress_sections(app, client):
    with app.app_context():
        _student()
    fx.login_as(client, "student@example.com")
    html = _html(client.get(fx.STUDENT_DASHBOARD))
    assert "No active enrollments" in html
    assert 'id="continue-learning"' not in html


def test_the_dashboard_never_writes_and_shows_nothing_of_another_student(app, client):
    with app.app_context():
        ids = _dashboard_world()
        group = Group.query.filter_by(public_id=ids["gp"]).one()
        classmate = _student("classmate@example.com")
        fx.enroll(group, classmate)
        secret = fx.lesson(Unit.query.filter_by(public_id=ids["up"]).one(), "Classmate secret", 9)
        fx.progress(classmate, group, secret, last_opened_at=fx.LATEST)
        before = fx.rows()
    fx.login_as(client, "student@example.com")
    response, _selects, writes = fx.select_count(client, fx.STUDENT_DASHBOARD)
    assert writes == []
    html = _html(response)
    recent_section = html.split('id="recently-opened"', 1)[1].split("<h2>Your groups</h2>", 1)[0]
    assert "Classmate secret" not in recent_section
    assert "classmate@example.com" not in html
    with app.app_context():
        assert fx.rows() == before


def test_the_dashboard_query_count_does_not_grow_with_groups_lessons_or_progress(app, client):
    with app.app_context():
        _dashboard_world()
    fx.login_as(client, "student@example.com")
    _response, small, _writes = fx.select_count(client, fx.STUDENT_DASHBOARD)

    with app.app_context():
        from app.models import User
        student = User.query.filter_by(email="student@example.com").one()
        for index in range(6):
            group = _group(student, f"Extra{index}")
            for unit_index in range(3):
                unit = fx.unit(group, f"Unit {unit_index}", unit_index)
                for position in range(4):
                    lesson = fx.lesson(unit, f"L{position}", position)
                    fx.progress(student, group, lesson,
                                last_opened_at=fx.NOW + timedelta(minutes=position),
                                completed_at=fx.NOW if position % 2 else None)
    response, large, _writes = fx.select_count(client, fx.STUDENT_DASHBOARD)
    assert response.status_code == 200
    assert large == small, (small, large)
    assert large <= 10, large
