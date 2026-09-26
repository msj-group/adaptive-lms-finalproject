"""A Student's own released grades (Phase 4 / M08).

The whole surface is two ``GET`` routes, so most of this suite is about
what a Student can **not** reach: a draft, a draft score, a draft
comment, another Student's anything, the roster, the Teacher's identity,
an administrative report, and any write path at all.
"""

import re
from decimal import Decimal

import pytest

from app.extensions import db
from app.models import (
    EnrollmentStatus,
    GradeItem,
    GradeRecord,
    UserRole,
    UserStatus,
)
from tests import grade_fixtures as fx
from tests import structural_checks as sc
from tests.conftest import make_user

LIST = "/student/grades"


def _released_group(label="A"):
    """One Group whose gradebook is fully configured and whose Homework
    item has been released; the Speaking item stays a draft."""
    teacher, group = fx.setup_group(label, teacher_email=f"t-{label}@example.com")
    alice = fx.enroll(group, f"alice-{label}@example.com", name="Alice")
    bob = fx.enroll(group, f"bob-{label}@example.com", name="Bob")
    homework = fx.category(group, "Homework", 6000)
    speaking = fx.category(group, "Speaking", 4000)
    released, released_records = fx.item_with_roster(
        homework,
        [alice, bob],
        scores=("18.00", "15.50"),
        released=True,
        title="HW 1",
        max_points="20.00",
    )
    released_records[0].comment = "Well argued."
    released_records[1].comment = "Please reread the brief."
    db.session.commit()
    draft, _ = fx.item_with_roster(
        speaking,
        [alice, bob],
        scores=("30.00", "25.00"),
        title="Secret draft",
        max_points="40.00",
    )
    return {
        "group_public_id": group.public_id,
        "group_id": group.id,
        "teacher_email": teacher.email,
        "alice_email": alice.email,
        "bob_email": bob.email,
        "alice_id": alice.id,
        "released_item_id": released.id,
        "draft_item_id": draft.id,
        "speaking_category_id": speaking.id,
    }


def _detail(client, gpid):
    return client.get(fx.student_detail(gpid)).get_data(as_text=True)


# ===========================================================================
# Access control
# ===========================================================================


def test_anonymous_is_redirected_to_login(app, client):
    with app.app_context():
        data = _released_group()
    for url in (LIST, fx.student_detail(data["group_public_id"])):
        resp = client.get(url)
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]


def test_a_teacher_and_an_administrator_are_refused_the_student_routes(app, client):
    with app.app_context():
        data = _released_group()
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        gpid = data["group_public_id"]
    for email in (data["teacher_email"], "admin@example.com"):
        fx.login_as(client, email)
        assert client.get(LIST).status_code == 403
        assert client.get(fx.student_detail(gpid)).status_code == 403


def test_a_student_has_no_write_path_at_all(app, client):
    with app.app_context():
        data = _released_group()
        gpid = data["group_public_id"]
    fx.login_as(client, data["alice_email"])
    for method in ("post", "put", "patch", "delete"):
        for url in (LIST, fx.student_detail(gpid)):
            assert getattr(client, method)(url).status_code == 405, (method, url)


def test_a_student_cannot_reach_the_teacher_or_administrator_surfaces(app, client):
    with app.app_context():
        data = _released_group()
        gpid = data["group_public_id"]
    fx.login_as(client, data["alice_email"])
    assert client.get(fx.teacher_base(gpid)).status_code == 403
    assert client.get(fx.admin_detail(gpid)).status_code == 403
    assert client.get("/admin/grades").status_code == 403


def test_the_student_pages_are_private_no_store_and_vary_on_cookie(app, client):
    with app.app_context():
        data = _released_group()
        gpid = data["group_public_id"]
    fx.login_as(client, data["alice_email"])
    for url in (LIST, fx.student_detail(gpid)):
        resp = client.get(url)
        assert resp.status_code == 200, url
        assert resp.headers["Cache-Control"] == "private, no-store", url
        assert "Cookie" in resp.headers.get("Vary", ""), url


# ===========================================================================
# Isolation -- the whole point
# ===========================================================================


def test_a_student_sees_only_their_own_score_and_comment(app, client):
    with app.app_context():
        data = _released_group()
        gpid = data["group_public_id"]
    fx.login_as(client, data["alice_email"])
    html = _detail(client, gpid)
    assert "18.00" in html
    assert "Well argued." in html
    # Bob's score, Bob's comment and Bob's name are simply not there. The
    # signed CSRF value is random base64 and can spell "Bob" by chance.
    assert "15.50" not in html
    assert "Please reread the brief." not in html
    assert "Bob" not in sc.redact_signed_values(html)


def test_the_other_student_sees_their_own_and_only_their_own(app, client):
    with app.app_context():
        data = _released_group()
        gpid = data["group_public_id"]
    fx.login_as(client, data["bob_email"])
    html = _detail(client, gpid)
    assert "15.50" in html
    assert "Please reread the brief." in html
    assert "18.00" not in html
    assert "Well argued." not in html
    assert "Alice" not in html


def test_a_draft_item_is_invisible_in_every_respect(app, client):
    with app.app_context():
        data = _released_group()
        gpid = data["group_public_id"]
    fx.login_as(client, data["alice_email"])
    html = _detail(client, gpid)
    assert "Secret draft" not in html
    assert "30.00" not in html
    # The draft's category still appears -- a Student is told that part of
    # their grade has not been released yet, which is not a leak.
    assert "Speaking" in html


def test_a_student_of_another_group_gets_a_non_disclosing_404(app, client):
    with app.app_context():
        a = _released_group("A")
        b = _released_group("B")
        gpid_b = b["group_public_id"]
    fx.login_as(client, a["alice_email"])
    assert client.get(fx.student_detail(gpid_b)).status_code == 404
    assert b["group_public_id"] not in client.get(LIST).get_data(as_text=True)


def test_a_group_with_nothing_released_is_indistinguishable_from_a_missing_one(
    app, client
):
    with app.app_context():
        teacher, group = fx.setup_group()
        alice = fx.enroll(group, "alice@example.com", name="Alice")
        cat = fx.category(group, "Homework", 10000)
        fx.item_with_roster(cat, [alice], scores=("10.00",), title="Still a draft")
        gpid, email = group.public_id, alice.email
    fx.login_as(client, email)
    assert client.get(fx.student_detail(gpid)).status_code == 404
    missing = fx.student_detail("00000000-0000-0000-0000-000000000000")
    assert client.get(missing).status_code == 404
    assert "Still a draft" not in client.get(LIST).get_data(as_text=True)


def test_no_internal_id_teacher_name_or_version_reaches_the_student(app, client):
    with app.app_context():
        data = _released_group()
        gpid = data["group_public_id"]
        ids = [data["group_id"], data["released_item_id"], data["alice_id"]]
    fx.login_as(client, data["alice_email"])
    html = _detail(client, gpid)
    hrefs = re.findall(r'href="([^"]*)"', html)
    for value in ids:
        assert not any(h.rstrip("/").endswith(f"/{value}") for h in hrefs), value
    assert "t-A@example.com" not in html
    assert "t-a@example.com" not in html


# ===========================================================================
# The numbers a Student is shown
# ===========================================================================


def test_a_students_own_percentage_is_shown_per_item_and_per_category(app, client):
    with app.app_context():
        data = _released_group()
        gpid = data["group_public_id"]
    fx.login_as(client, data["alice_email"])
    html = _detail(client, gpid)
    # 18 / 20 = 90.00%, both for the item and for its one-item category.
    assert "90.00%" in html
    assert "20.00" in html


def test_the_overall_grade_is_unavailable_while_a_category_has_nothing_released(
    app, client
):
    with app.app_context():
        data = _released_group()
        gpid = data["group_public_id"]
    fx.login_as(client, data["alice_email"])
    html = _detail(client, gpid)
    assert "Not available yet" in html
    assert "Some parts of this group" in html
    # No partial weighted total is shown anywhere.
    assert "54.00%" not in html


def test_the_overall_grade_appears_once_every_category_has_a_release(app, client):
    with app.app_context():
        data = _released_group()
        gpid = data["group_public_id"]
        item = db.session.get(GradeItem, data["draft_item_id"])
        item.released_at = fx.LATER
        db.session.commit()
    fx.login_as(client, data["alice_email"])
    html = _detail(client, gpid)
    # 90% * 0.60 + 75% * 0.40 = 54 + 30 = 84.00
    assert "84.00%" in html
    assert "Not available yet" not in html


def test_the_overall_grade_is_unavailable_while_the_weights_are_incomplete(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        alice = fx.enroll(group, "alice@example.com", name="Alice")
        cat = fx.category(group, "Homework", 6000)
        fx.item_with_roster(
            cat, [alice], scores=("10.00",), released=True, max_points="10.00"
        )
        gpid, email = group.public_id, alice.email
    fx.login_as(client, email)
    html = _detail(client, gpid)
    assert "still setting up" in html
    assert "100.00%" in html  # their own item percentage, not an overall grade
    assert "Not available yet" in html


def test_a_student_missing_one_released_score_is_told_so_without_naming_anybody(
    app, client
):
    """A Student who enrolled after an item was created holds no record
    for it, so no overall grade is invented for them."""
    with app.app_context():
        teacher, group = fx.setup_group()
        early = fx.enroll(group, "early@example.com", name="Early")
        cat = fx.category(group, "Homework", 10000)
        first, _ = fx.item_with_roster(
            cat, [early], scores=("10.00",), released=True, title="First", max_points="10.00"
        )
        late = fx.enroll(group, "late@example.com", name="Late")
        second, _ = fx.item_with_roster(
            cat, [early, late], scores=("8.00", "9.00"), released=True, title="Second",
            max_points="10.00",
        )
        gpid = group.public_id
        early_email, late_email = early.email, late.email
    fx.login_as(client, early_email)
    html = _detail(client, gpid)
    assert "90.00%" in html  # 18 of 20 across both items
    assert "Not available yet" not in html

    fx.login_as(client, late_email)
    html = _detail(client, gpid)
    assert "Not available yet" in html
    assert "do not have a result for every released item" in html
    assert "1 of 2 released" in html
    assert "Early" not in html


def test_a_teacher_correction_after_release_reaches_only_that_student(app, client):
    with app.app_context():
        data = _released_group()
        gpid = data["group_public_id"]
        item_id = data["released_item_id"]
    fx.login_as(client, data["alice_email"])
    assert "18.00" in _detail(client, gpid)

    with app.app_context():
        alice_record = (
            GradeRecord.query.filter_by(grade_item_id=item_id)
            .filter_by(student_id=data["alice_id"])
            .one()
        )
        alice_record.score = Decimal("19.50")
        alice_record.comment = "Corrected upward."
        db.session.commit()

    fx.login_as(client, data["alice_email"])
    html = _detail(client, gpid)
    assert "19.50" in html
    assert "Corrected upward." in html

    fx.login_as(client, data["bob_email"])
    html = _detail(client, gpid)
    assert "19.50" not in html
    assert "Corrected upward." not in html
    assert "15.50" in html


# ===========================================================================
# History
# ===========================================================================


def test_a_withdrawn_student_keeps_reading_their_own_released_grades(app, client):
    with app.app_context():
        data = _released_group()
        gpid = data["group_public_id"]
        from app.models import Enrollment

        row = Enrollment.query.filter_by(
            group_id=data["group_id"], student_id=data["alice_id"]
        ).one()
        row.status = EnrollmentStatus.WITHDRAWN.value
        db.session.commit()
    fx.login_as(client, data["alice_email"])
    html = _detail(client, gpid)
    assert "18.00" in html
    assert "Well argued." in html
    # And still nobody else's.
    assert "15.50" not in html


def test_an_archived_group_keeps_its_released_grades_readable(app, client):
    with app.app_context():
        data = _released_group()
        gpid = data["group_public_id"]
        from app.models import AcademicStatus, Group

        group = db.session.get(Group, data["group_id"])
        group.status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    fx.login_as(client, data["alice_email"])
    assert "18.00" in _detail(client, gpid)


def test_a_suspended_account_can_no_longer_read_its_grades(app, client):
    """A foreign key proves a row exists, never that it is still an
    active Student's -- so the join re-proves role and status."""
    with app.app_context():
        data = _released_group()
        gpid = data["group_public_id"]
    fx.login_as(client, data["alice_email"])
    assert client.get(fx.student_detail(gpid)).status_code == 200
    with app.app_context():
        from app.models import User

        row = db.session.get(User, data["alice_id"])
        row.status = UserStatus.SUSPENDED.value
        db.session.commit()
    # The suspended account cannot even hold a session any more.
    fx.fresh_identity()
    assert client.get(fx.student_detail(gpid)).status_code in (302, 404)


# ===========================================================================
# The list page
# ===========================================================================


def test_the_list_shows_only_groups_with_something_released(app, client):
    with app.app_context():
        data = _released_group("A")
        # A second Group where this same Student has only drafts.
        teacher_b, group_b = fx.setup_group("B", teacher_email="t-b@example.com")
        alice = db.session.get(
            __import__("app.models", fromlist=["User"]).User, data["alice_id"]
        )
        db.session.add(
            __import__("app.models", fromlist=["Enrollment"]).Enrollment(
                group_id=group_b.id, student_id=alice.id
            )
        )
        db.session.commit()
        cat_b = fx.category(group_b, "Homework", 10000)
        fx.item_with_roster(cat_b, [alice], scores=("5.00",), title="Draft only")
        gpid_b = group_b.public_id
    fx.login_as(client, data["alice_email"])
    html = client.get(LIST).get_data(as_text=True)
    assert data["group_public_id"] in html
    assert gpid_b not in html
    assert "Draft only" not in html


def test_an_empty_list_says_so_rather_than_failing(app, client):
    with app.app_context():
        teacher, group = fx.setup_group()
        alice = fx.enroll(group, "alice@example.com", name="Alice")
        email = alice.email
    fx.login_as(client, email)
    resp = client.get(LIST)
    assert resp.status_code == 200
    assert b"No released grades yet" in resp.data


def test_a_page_past_the_end_falls_back_to_page_one(app, client):
    with app.app_context():
        data = _released_group()
    fx.login_as(client, data["alice_email"])
    resp = client.get(LIST + "?page=999")
    assert resp.status_code == 200
    assert data["group_public_id"] in resp.get_data(as_text=True)


@pytest.mark.parametrize("value", ["0", "-1", "abc", "", "99999999"])
def test_a_malformed_page_argument_is_normalised_rather_than_reaching_sql(
    app, client, value
):
    with app.app_context():
        data = _released_group()
    fx.login_as(client, data["alice_email"])
    assert client.get(f"{LIST}?page={value}").status_code == 200
