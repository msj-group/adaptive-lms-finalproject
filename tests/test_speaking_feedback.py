"""Teacher feedback on immutable Speaking recordings (Phase 4 / M06).

One shared record per recording, latest text only, no grade anywhere.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
REPEATABLE READ snapshot isolation, so the co-teacher race tests here are
**structural**: they exercise the signed-version rejection and the
post-lock rechecks by injecting a competing write at an exact transaction
boundary. They are **not** a demonstration of real InnoDB blocking.
"""

import re
from datetime import timedelta
from unittest.mock import patch

import pytest

from app import create_app
from app.blueprints.teacher import speaking as speaking_mod
from app.extensions import db
from app.models import (
    SPEAKING_FEEDBACK_MAX_LENGTH,
    AcademicStatus,
    Enrollment,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    SpeakingActivity,
    SpeakingFeedback,
    SpeakingSubmission,
    User,
    UserRole,
    UserStatus,
)
from tests.speaking_fixtures import (
    AFTER_DUE,
    NOW,
    Clock,
    assign_teacher,
    enroll,
    hidden_value,
    login_as,
    setup_group,
    speaking_activity,
    speaking_feedback,
    speaking_submission,
    student_detail,
    teacher_detail,
    user,
)


def _at(*moments):
    return patch.object(speaking_mod, "utc_reference_now", Clock(*moments))


def _student_at(*moments):
    from app.blueprints.student import speaking as student_speaking_mod

    return patch.object(student_speaking_mod, "utc_reference_now", Clock(*moments))


def _scene(app, published=True, group_status=None):
    """One published activity with one Student recording, and the URLs the
    feedback surface uses."""
    kwargs = {} if group_status is None else {"group_status": group_status}
    with app.app_context():
        teacher, group = setup_group(**kwargs)
        _assignment, activity = speaking_activity(group, published=published)
        student = enroll(group, name="Sami Speaker")
        submission = speaking_submission(activity, student)
        gpid = group.public_id
        spid = activity.public_id
        bpid = submission.public_id
    base = f"{teacher_detail(gpid, spid)}/submissions/{bpid}"
    return {
        "gpid": gpid,
        "spid": spid,
        "bpid": bpid,
        "submission_url": base,
        "feedback_url": base + "/feedback",
    }


def _save(client, scene, text, token=None, follow=True):
    if token is None:
        token = hidden_value(client, scene["feedback_url"], "feedback_state")
    return client.post(
        scene["feedback_url"],
        data={"feedback_state": token, "feedback_text": text},
        follow_redirects=follow,
    )


# ===========================================================================
# Authorization
# ===========================================================================


def test_anonymous_is_redirected_to_login(app, client):
    scene = _scene(app)
    resp = client.get(scene["feedback_url"])
    assert resp.status_code == 302 and "/auth/login" in resp.headers["Location"]


@pytest.mark.parametrize(
    "role",
    [UserRole.STUDENT.value, UserRole.ADMINISTRATOR.value, UserRole.RESEARCHER.value],
)
def test_non_teacher_roles_are_forbidden(app, client, role):
    scene = _scene(app)
    with app.app_context():
        user("other@example.com", role)
    login_as(client, "other@example.com")
    assert client.get(scene["feedback_url"]).status_code == 403


def test_an_unassigned_teacher_gets_a_non_disclosing_404(app, client):
    scene = _scene(app)
    with app.app_context():
        user("stranger@example.com", UserRole.TEACHER.value)
    login_as(client, "stranger@example.com")
    assert client.get(scene["feedback_url"]).status_code == 404


def test_a_removed_assignment_loses_write_access(app, client):
    scene = _scene(app)
    login_as(client, "teacher@example.com")
    token = hidden_value(client, scene["feedback_url"], "feedback_state")
    with app.app_context():
        GroupTeacherAssignment.query.one().status = (
            GroupTeacherAssignmentStatus.REMOVED.value
        )
        db.session.commit()
    resp = _save(client, scene, "Anything.", token=token, follow=False)
    assert resp.status_code == 404
    with app.app_context():
        assert SpeakingFeedback.query.count() == 0


def test_a_cross_activity_or_cross_group_recording_is_a_404(app, client):
    with app.app_context():
        _teacher, group = setup_group("A")
        _other, other_group = setup_group("B", teacher_email="other@example.com")
        _a1, first = speaking_activity(group, title="One", published=True)
        _a2, second = speaking_activity(group, title="Two", published=True)
        _a3, foreign = speaking_activity(other_group, published=True)
        student = enroll(group)
        outsider = enroll(other_group, "outsider@example.com")
        mine = speaking_submission(first, student)
        theirs = speaking_submission(foreign, outsider)
        gpid = group.public_id
        wrong_activity = teacher_detail(gpid, second.public_id)
        foreign_activity = teacher_detail(other_group.public_id, foreign.public_id)
        mine_pid, theirs_pid = mine.public_id, theirs.public_id
    login_as(client, "teacher@example.com")
    assert client.get(
        f"{wrong_activity}/submissions/{mine_pid}/feedback"
    ).status_code == 404
    assert client.get(
        f"{foreign_activity}/submissions/{theirs_pid}/feedback"
    ).status_code == 404


# ===========================================================================
# Create, revise, no-op
# ===========================================================================


def test_a_first_save_creates_version_one_and_attributes_it(app, client):
    scene = _scene(app)
    login_as(client, "teacher@example.com")
    with _at(NOW):
        resp = _save(client, scene, "  Clear and confident.  ")
    assert "Feedback saved" in resp.get_data(as_text=True)
    with app.app_context():
        row = SpeakingFeedback.query.one()
        assert row.feedback_text == "Clear and confident."  # stripped
        assert row.version == 1
        assert row.created_at == row.updated_at == NOW
        assert row.created_at.microsecond == 0
        assert row.speaking_submission_id == SpeakingSubmission.query.one().id
        assert row.reviewer_id == User.query.filter_by(
            email="teacher@example.com"
        ).one().id


def test_a_co_teacher_may_revise_the_same_record(app, client):
    scene = _scene(app)
    with app.app_context():
        assign_teacher(Group.query.one(), "second@example.com")
    login_as(client, "teacher@example.com")
    with _at(NOW):
        _save(client, scene, "First wording.")
    login_as(client, "second@example.com")
    with _at(NOW + timedelta(minutes=5)):
        resp = _save(client, scene, "Second wording.")
    assert "Feedback updated" in resp.get_data(as_text=True)
    with app.app_context():
        row = SpeakingFeedback.query.one()
        assert row.feedback_text == "Second wording."
        assert row.version == 2
        assert row.created_at == NOW
        assert row.updated_at == NOW + timedelta(minutes=5)
        assert row.reviewer_id == User.query.filter_by(
            email="second@example.com"
        ).one().id
    # Only the latest text is kept.
    with app.app_context():
        assert SpeakingFeedback.query.count() == 1


def test_saving_identical_text_is_a_no_op(app, client):
    scene = _scene(app)
    with app.app_context():
        assign_teacher(Group.query.one(), "second@example.com")
    login_as(client, "teacher@example.com")
    with _at(NOW):
        _save(client, scene, "Exactly this.")
    with app.app_context():
        before = SpeakingFeedback.query.one()
        version, updated, reviewer = before.version, before.updated_at, before.reviewer_id

    login_as(client, "second@example.com")
    with _at(NOW + timedelta(hours=1)):
        resp = _save(client, scene, "   Exactly this.   ")
    assert "unchanged, so nothing was saved" in resp.get_data(as_text=True)
    with app.app_context():
        after = SpeakingFeedback.query.one()
        assert (after.version, after.updated_at, after.reviewer_id) == (
            version, updated, reviewer,
        )


@pytest.mark.parametrize("text", ["", "   ", "\n\n"])
def test_empty_feedback_is_refused(app, client, text):
    scene = _scene(app)
    login_as(client, "teacher@example.com")
    resp = _save(client, scene, text, follow=False)
    assert resp.status_code == 200
    with app.app_context():
        assert SpeakingFeedback.query.count() == 0


def test_oversized_feedback_is_refused_before_trimming(app, client):
    scene = _scene(app)
    login_as(client, "teacher@example.com")
    padded = " " * 50 + "x" * SPEAKING_FEEDBACK_MAX_LENGTH + " " * 50
    resp = _save(client, scene, padded, follow=False)
    assert resp.status_code == 200
    with app.app_context():
        assert SpeakingFeedback.query.count() == 0


def test_feedback_at_the_exact_boundary_is_accepted(app, client):
    scene = _scene(app)
    login_as(client, "teacher@example.com")
    _save(client, scene, "x" * SPEAKING_FEEDBACK_MAX_LENGTH)
    with app.app_context():
        assert len(SpeakingFeedback.query.one().feedback_text) == (
            SPEAKING_FEEDBACK_MAX_LENGTH
        )


# ===========================================================================
# The signed state token
# ===========================================================================


def test_the_token_binds_every_required_object_and_carries_no_text(app, client):
    scene = _scene(app)
    login_as(client, "teacher@example.com")
    with _at(NOW):
        _save(client, scene, "Some private wording.")
        token = hidden_value(client, scene["feedback_url"], "feedback_state")
    with app.app_context():
        payload = speaking_mod._load_token(token, "speaking-feedback")
    assert set(payload) == {
        "purpose", "teacher_public_id", "group_public_id", "assignment_public_id",
        "speaking_public_id", "submission_public_id", "feedback_public_id", "version",
    }
    assert payload["speaking_public_id"] == scene["spid"]
    assert payload["submission_public_id"] == scene["bpid"]
    assert payload["group_public_id"] == scene["gpid"]
    assert payload["version"] == 1
    # No feedback text, and no internal id, is ever placed in a token.
    assert "Some private wording." not in token
    for value in payload.values():
        assert not isinstance(value, int) or value == 1


def test_an_absence_claim_is_signed_and_distinguishable_from_a_missing_token(app, client):
    scene = _scene(app)
    login_as(client, "teacher@example.com")
    token = hidden_value(client, scene["feedback_url"], "feedback_state")
    with app.app_context():
        payload = speaking_mod._load_token(token, "speaking-feedback")
        assert payload["feedback_public_id"] is None and payload["version"] is None
        # A missing/forged token yields no payload at all -- it is never
        # silently upgraded into an absence claim.
        assert speaking_mod._load_token("", "speaking-feedback") is None
        assert speaking_mod._load_token("garbage", "speaking-feedback") is None


@pytest.mark.parametrize("token", ["", "not-a-token", "eyJhIjoxfQ.bad.signature"])
def test_a_missing_or_forged_token_is_rejected_without_writing(app, client, token):
    scene = _scene(app)
    login_as(client, "teacher@example.com")
    resp = _save(client, scene, "Attempted.", token=token)
    assert "was changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert SpeakingFeedback.query.count() == 0


def test_a_token_from_another_m06_purpose_is_rejected(app, client):
    scene = _scene(app)
    login_as(client, "teacher@example.com")
    with app.app_context():
        wrong_purpose = speaking_mod._make_token(
            "speaking-publication",
            action="publish",
            teacher_public_id="x",
            group_public_id=scene["gpid"],
            speaking_public_id=scene["spid"],
            status="draft",
        )
    resp = _save(client, scene, "Attempted.", token=wrong_purpose)
    assert "was changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert SpeakingFeedback.query.count() == 0


def test_a_token_minted_for_another_teacher_is_rejected(app, client):
    scene = _scene(app)
    with app.app_context():
        assign_teacher(Group.query.one(), "second@example.com")
    login_as(client, "second@example.com")
    stolen = hidden_value(client, scene["feedback_url"], "feedback_state")
    login_as(client, "teacher@example.com")
    resp = _save(client, scene, "Attempted.", token=stolen)
    assert "was changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert SpeakingFeedback.query.count() == 0


def test_a_losing_co_teacher_is_told_to_reload_rather_than_overwriting(app, client):
    scene = _scene(app)
    with app.app_context():
        assign_teacher(Group.query.one(), "second@example.com")
    login_as(client, "teacher@example.com")
    stale = hidden_value(client, scene["feedback_url"], "feedback_state")

    # The co-teacher writes the FIRST feedback while that form is open.
    login_as(client, "second@example.com")
    with _at(NOW):
        _save(client, scene, "Theirs.")

    login_as(client, "teacher@example.com")
    resp = _save(client, scene, "Mine.", token=stale)
    assert "was changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        row = SpeakingFeedback.query.one()
        assert row.feedback_text == "Theirs."
        assert row.version == 1


def test_an_a_b_a_round_trip_still_invalidates_an_open_form(app, client):
    """The text is back to what the form was opened against, but the row
    changed twice. ``version`` -- not the text, and not a timestamp -- is
    what catches that."""
    scene = _scene(app)
    with app.app_context():
        assign_teacher(Group.query.one(), "second@example.com")
    login_as(client, "teacher@example.com")
    with _at(NOW):
        _save(client, scene, "A")
        stale = hidden_value(client, scene["feedback_url"], "feedback_state")
    login_as(client, "second@example.com")
    with _at(NOW + timedelta(minutes=1)):
        _save(client, scene, "B")
    with _at(NOW + timedelta(minutes=2)):
        _save(client, scene, "A")

    login_as(client, "teacher@example.com")
    resp = _save(client, scene, "C", token=stale)
    assert "was changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        row = SpeakingFeedback.query.one()
        assert row.feedback_text == "A"
        assert row.version == 3


def test_replaying_a_successful_save_writes_nothing_a_second_time(app, client):
    scene = _scene(app)
    login_as(client, "teacher@example.com")
    with _at(NOW):
        token = hidden_value(client, scene["feedback_url"], "feedback_state")
        _save(client, scene, "Once.", token=token)
    with _at(NOW + timedelta(minutes=1)):
        resp = _save(client, scene, "Twice.", token=token)
    assert "was changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        row = SpeakingFeedback.query.one()
        assert row.feedback_text == "Once."
        assert row.version == 1


def test_a_validation_failure_keeps_the_original_token(app, client):
    scene = _scene(app)
    login_as(client, "teacher@example.com")
    with _at(NOW):
        _save(client, scene, "Saved wording.")
        token = hidden_value(client, scene["feedback_url"], "feedback_state")
    resp = client.post(
        scene["feedback_url"],
        data={"feedback_state": token, "feedback_text": "   "},
    )
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert f'name="feedback_state" value="{token}"' in html
    with app.app_context():
        assert SpeakingFeedback.query.one().version == 1


# ===========================================================================
# Reviewer integrity -- fail closed
# ===========================================================================


def test_a_role_inconsistent_row_is_neither_shown_nor_overwritten(app, client):
    scene = _scene(app)
    login_as(client, "teacher@example.com")
    with _at(NOW):
        _save(client, scene, "Written by a teacher.")
    with app.app_context():
        reviewer = db.session.get(User, SpeakingFeedback.query.one().reviewer_id)
        reviewer.role = UserRole.RESEARCHER.value
        db.session.commit()
        assign_teacher(Group.query.one(), "second@example.com")

    login_as(client, "second@example.com")
    page = client.get(scene["feedback_url"]).get_data(as_text=True)
    assert "no longer a teacher" in page
    # The stored text is NOT rendered, and no form or token is offered.
    assert "Written by a teacher." not in page
    assert 'name="feedback_state"' not in page

    resp = _save(client, scene, "Repair attempt.", token="anything")
    assert "no longer a teacher" in resp.get_data(as_text=True)
    with app.app_context():
        assert SpeakingFeedback.query.one().feedback_text == "Written by a teacher."


def test_the_submission_list_reports_the_invalid_state_separately(app, client):
    scene = _scene(app)
    login_as(client, "teacher@example.com")
    with _at(NOW):
        _save(client, scene, "Some feedback.")
    with app.app_context():
        reviewer = db.session.get(User, SpeakingFeedback.query.one().reviewer_id)
        reviewer.role = UserRole.RESEARCHER.value
        db.session.commit()
        # Another Teacher must exist to read the page.
        assign_teacher(Group.query.one(), "second@example.com")
    login_as(client, "second@example.com")
    html = client.get(
        teacher_detail(scene["gpid"], scene["spid"]) + "/submissions"
    ).get_data(as_text=True)
    assert "Needs admin review" in html
    assert "Awaiting feedback" not in html


def test_a_student_cannot_read_feedback_whose_reviewer_is_no_longer_a_teacher(
    app, client
):
    scene = _scene(app)
    login_as(client, "teacher@example.com")
    with _at(NOW):
        _save(client, scene, "Hidden after corruption.")
    with app.app_context():
        reviewer = db.session.get(User, SpeakingFeedback.query.one().reviewer_id)
        reviewer.role = UserRole.RESEARCHER.value
        db.session.commit()
    login_as(client, "student@example.com")
    with _student_at(NOW):
        html = client.get(
            student_detail(scene["gpid"], scene["spid"]) + "/receipt"
        ).get_data(as_text=True)
    assert "Hidden after corruption." not in html
    assert "cannot be shown right now" in html


# ===========================================================================
# Lifecycle: reading is historical, writing is not
# ===========================================================================


def test_feedback_can_be_written_after_the_deadline(app, client):
    scene = _scene(app)
    login_as(client, "teacher@example.com")
    with _at(AFTER_DUE):
        resp = _save(client, scene, "Reviewed after the deadline.")
    assert "Feedback saved" in resp.get_data(as_text=True)
    with app.app_context():
        assert SpeakingFeedback.query.one().feedback_text == (
            "Reviewed after the deadline."
        )


def test_an_archived_chain_blocks_writing_but_not_reading(app, client):
    scene = _scene(app)
    login_as(client, "teacher@example.com")
    with _at(NOW):
        _save(client, scene, "Saved while active.")
    with app.app_context():
        Group.query.one().status = (
            AcademicStatus.ARCHIVED.value
        )
        db.session.commit()

    page = client.get(scene["feedback_url"]).get_data(as_text=True)
    assert "Saved while active." in page
    assert 'name="feedback_state"' not in page
    assert "Feedback cannot be written or changed right now" in page

    resp = _save(client, scene, "Attempted while archived.", token="anything")
    assert "can only be written or changed" in resp.get_data(as_text=True)
    with app.app_context():
        assert SpeakingFeedback.query.one().feedback_text == "Saved while active."


def test_a_withdrawn_students_recording_can_still_receive_feedback(app, client):
    scene = _scene(app)
    with app.app_context():
        Enrollment.query.one().status = EnrollmentStatus.WITHDRAWN.value
        db.session.commit()
    login_as(client, "teacher@example.com")
    with _at(NOW):
        resp = _save(client, scene, "Still reviewable.")
    assert "Feedback saved" in resp.get_data(as_text=True)


# ===========================================================================
# The Student side
# ===========================================================================


def test_a_student_reads_the_latest_feedback_on_their_own_receipt(app, client):
    scene = _scene(app)
    with app.app_context():
        assign_teacher(Group.query.one(), "second@example.com")
    login_as(client, "teacher@example.com")
    with _at(NOW):
        _save(client, scene, "First version.")
    login_as(client, "second@example.com")
    with _at(NOW + timedelta(minutes=1)):
        _save(client, scene, "Latest version.")

    login_as(client, "student@example.com")
    with _student_at(NOW + timedelta(hours=1)):
        html = client.get(
            student_detail(scene["gpid"], scene["spid"]) + "/receipt"
        ).get_data(as_text=True)
    assert "Latest version." in html
    assert "First version." not in html
    assert "not a grade or a pass mark" in html


def test_a_classmate_never_sees_another_students_feedback(app, client):
    with app.app_context():
        teacher, group = setup_group()
        _assignment, activity = speaking_activity(group, published=True)
        mine = enroll(group, "me@example.com")
        theirs = enroll(group, "them@example.com")
        my_submission = speaking_submission(activity, mine)
        their_submission = speaking_submission(activity, theirs)
        speaking_feedback(my_submission, teacher, text="About me.")
        speaking_feedback(their_submission, teacher, text="About them.")
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "me@example.com")
    with _student_at(NOW):
        html = client.get(student_detail(gpid, spid) + "/receipt").get_data(as_text=True)
    assert "About me." in html
    assert "About them." not in html


def test_a_student_has_no_write_path_to_feedback(app):
    rules = [
        (str(rule), rule.methods)
        for rule in app.url_map.iter_rules()
        if "/student/" in str(rule)
    ]
    assert not any("feedback" in path for path, _ in rules)


def test_there_is_no_feedback_delete_endpoint(app):
    rules = {str(rule) for rule in app.url_map.iter_rules()}
    assert not any("feedback" in rule and "delete" in rule for rule in rules)


# ===========================================================================
# Failure recovery and CSRF
# ===========================================================================


def test_an_integrity_failure_is_reported_generically(app, client):
    """Structural: a competing first feedback commits between this
    request's post-lock read and its own INSERT."""
    scene = _scene(app)
    with app.app_context():
        assign_teacher(Group.query.one(), "second@example.com")
    login_as(client, "teacher@example.com")
    token = hidden_value(client, scene["feedback_url"], "feedback_state")

    original = speaking_mod._write_moment
    fired = {}

    def racing():
        moment = original()
        if not fired:
            fired["done"] = True
            other = User.query.filter_by(email="second@example.com").one()
            speaking_feedback(
                SpeakingSubmission.query.one(), other, text="Theirs first."
            )
        return moment

    with patch.object(speaking_mod, "_write_moment", racing):
        resp = _save(client, scene, "Mine.")
    body = resp.get_data(as_text=True)
    assert "could not be saved" in body
    # No SQL, driver text, parameter or internal id reaches the page.
    for leak in ("IntegrityError", "UNIQUE constraint", "sqlite3", "sqlalchemy"):
        assert leak not in body
    with app.app_context():
        row = SpeakingFeedback.query.one()
        assert row.feedback_text == "Theirs first."
        assert row.version == 1


def test_feedback_requires_csrf_when_it_is_enabled(tmp_path):
    flask_app = create_app("testing", MATERIAL_STORAGE_ROOT=str(tmp_path / "materials"))
    flask_app.config["WTF_CSRF_ENABLED"] = True
    test_client = flask_app.test_client()
    with flask_app.app_context():
        db.create_all()
        try:
            _teacher, group = setup_group()
            _assignment, activity = speaking_activity(group, published=True)
            student = enroll(group)
            submission = speaking_submission(activity, student)
            url = (
                f"{teacher_detail(group.public_id, activity.public_id)}"
                f"/submissions/{submission.public_id}/feedback"
            )
            login_page = test_client.get("/auth/login").get_data(as_text=True)
            token = re.search(
                r'name="csrf_token"[^>]*value="([^"]+)"', login_page
            ).group(1)
            test_client.post(
                "/auth/login",
                data={
                    "email": "teacher@example.com",
                    "password": "Sup3rSecret!123",
                    "csrf_token": token,
                },
            )
            resp = test_client.post(
                url, data={"feedback_state": "x", "feedback_text": "Attempted."}
            )
            assert resp.status_code == 400
            assert SpeakingFeedback.query.count() == 0
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
