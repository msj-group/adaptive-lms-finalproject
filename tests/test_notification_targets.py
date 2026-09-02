"""M14 target safety: the central validator and the server-side builders
that are the only way a ``target_path`` is ever produced.

The validator is fail-closed, so most of this file is a negative matrix:
absolute and protocol-relative URLs, schemes, network locations,
backslashes, control characters, query strings, traversal segments, and
anything outside the recipient role's own namespace must all be rejected
outright rather than repaired.
"""

import pytest

from app.models import UserRole
from app.services.notification_targets import (
    MAX_TARGET_LENGTH,
    ROLE_NAMESPACES,
    role_dashboard_target,
    student_dashboard_target,
    student_lesson_target,
    student_material_target,
    teacher_dashboard_target,
    validate_notification_target,
)

_STUDENT = UserRole.STUDENT.value
_TEACHER = UserRole.TEACHER.value


# ===========================================================================
# Accepted shapes
# ===========================================================================


@pytest.mark.parametrize(
    "role, path",
    [
        (_STUDENT, "/student/dashboard"),
        (_STUDENT, "/student/search"),
        (_STUDENT, "/student/groups/abc-123/units"),
        (
            _STUDENT,
            "/student/groups/g-1/units/u-1/lessons/l-1#material-11112222-3333-4444-5555-666677778888",
        ),
        (_TEACHER, "/teacher/dashboard"),
        (_TEACHER, "/teacher/groups/g-1/units"),
    ],
)
def test_accepts_role_namespaced_relative_paths(role, path):
    assert validate_notification_target(role, path) == path


# ===========================================================================
# Rejected shapes -- external / open-redirect attempts
# ===========================================================================


@pytest.mark.parametrize(
    "candidate",
    [
        "https://evil.example/student/dashboard",
        "http://evil.example",
        "//evil.example/student/dashboard",
        "///evil.example/student/dashboard",
        "javascript:alert(1)",
        "data:text/html,<script>1</script>",
        "mailto:someone@example.org",
        "\\\\evil.example\\student",
        "/student/dashboard\\@evil.example",
        "/\\evil.example/student",
        "HTTPS://EVIL.EXAMPLE/student/dashboard",
    ],
)
def test_rejects_external_and_open_redirect_attempts(candidate):
    assert validate_notification_target(_STUDENT, candidate) is None
    assert validate_notification_target(_TEACHER, candidate) is None


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        None,
        "student/dashboard",
        "dashboard",
        "?next=/student/dashboard",
        "#material-1",
    ],
)
def test_rejects_values_that_are_not_absolute_local_paths(candidate):
    assert validate_notification_target(_STUDENT, candidate) is None


@pytest.mark.parametrize(
    "candidate",
    [
        "/student/dashboard\n",
        "/student/dash\rboard",
        "/student/\x00dashboard",
        "/student/dash\tboard",
        "/student/dashboard\x7f",
    ],
)
def test_rejects_control_characters(candidate):
    assert validate_notification_target(_STUDENT, candidate) is None


@pytest.mark.parametrize(
    "candidate",
    [
        "/student/../teacher/dashboard",
        "/student/./dashboard",
        "/student/groups/../../admin/students",
        "/student//dashboard",
        "/student/dashboard/",
    ],
)
def test_rejects_traversal_and_malformed_paths(candidate):
    assert validate_notification_target(_STUDENT, candidate) is None


@pytest.mark.parametrize(
    "candidate",
    [
        "/student/dashboard?next=/admin",
        "/student/search?q=x",
    ],
)
def test_rejects_a_query_string(candidate):
    assert validate_notification_target(_STUDENT, candidate) is None


@pytest.mark.parametrize(
    "candidate",
    [
        "/student/groups/g/units/u/lessons/l#material-a/b",
        "/student/groups/g/units/u/lessons/l#frag ment",
        "/student/groups/g/units/u/lessons/l#frag?x",
    ],
)
def test_rejects_an_unexpected_fragment(candidate):
    assert validate_notification_target(_STUDENT, candidate) is None


def test_rejects_a_value_longer_than_the_column(app):
    long_path = "/student/" + ("a" * MAX_TARGET_LENGTH)
    assert len(long_path) > MAX_TARGET_LENGTH
    assert validate_notification_target(_STUDENT, long_path) is None


# ===========================================================================
# Role namespacing
# ===========================================================================


def test_a_student_target_is_rejected_for_a_teacher_and_vice_versa():
    assert validate_notification_target(_TEACHER, "/student/dashboard") is None
    assert validate_notification_target(_STUDENT, "/teacher/dashboard") is None


@pytest.mark.parametrize(
    "candidate",
    [
        "/admin/students",
        "/admin/groups/g/members",
        "/auth/login",
        "/health",
        "/design-system",
        "/notifications",
    ],
)
def test_rejects_paths_outside_the_role_namespace(candidate):
    assert validate_notification_target(_STUDENT, candidate) is None
    assert validate_notification_target(_TEACHER, candidate) is None


@pytest.mark.parametrize("candidate", ["/students/dashboard", "/studentx/dashboard", "/student"])
def test_a_lookalike_prefix_is_not_the_namespace(candidate):
    assert validate_notification_target(_STUDENT, candidate) is None


@pytest.mark.parametrize(
    "role",
    [
        UserRole.ADMINISTRATOR.value,
        UserRole.RESEARCHER.value,
        "",
        None,
        "root",
    ],
)
def test_roles_without_an_inbox_can_never_validate_a_target(role):
    assert validate_notification_target(role, "/student/dashboard") is None
    assert validate_notification_target(role, "/teacher/dashboard") is None
    assert role_dashboard_target(role) is None


def test_only_student_and_teacher_have_a_namespace():
    assert set(ROLE_NAMESPACES) == {_STUDENT, _TEACHER}
    assert ROLE_NAMESPACES[_STUDENT] == "/student/"
    assert ROLE_NAMESPACES[_TEACHER] == "/teacher/"


# ===========================================================================
# Builders -- the only trusted source of a stored target
# ===========================================================================


def test_dashboard_builders_produce_validated_targets(app):
    with app.app_context():
        assert student_dashboard_target() == "/student/dashboard"
        assert teacher_dashboard_target() == "/teacher/dashboard"
        assert role_dashboard_target(_STUDENT) == "/student/dashboard"
        assert role_dashboard_target(_TEACHER) == "/teacher/dashboard"


def test_lesson_and_material_builders_use_public_ids_only(app):
    with app.app_context():
        lesson_target = student_lesson_target("gp-1", "up-1", "lp-1")
        assert lesson_target == "/student/groups/gp-1/units/up-1/lessons/lp-1"
        assert validate_notification_target(_STUDENT, lesson_target) == lesson_target

        material_target = student_material_target("gp-1", "up-1", "lp-1", "mp-1")
        assert material_target == lesson_target + "#material-mp-1"
        assert validate_notification_target(_STUDENT, material_target) == material_target


def test_a_builder_refuses_to_produce_an_unsafe_target(app):
    """A public id that is not a public id (a traversal attempt reaching
    a builder through some future bug) must raise rather than be stored."""
    with app.app_context():
        with pytest.raises(ValueError):
            student_lesson_target("gp-1", "up-1", "../../admin/students")
