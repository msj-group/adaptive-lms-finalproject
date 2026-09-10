"""Teacher Speaking authoring, publication, freezes and recording review
(Phase 4 / M06).

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
REPEATABLE READ snapshot isolation, so the concurrency tests here are
**structural**: they assert the *requested* order and exercise the
post-lock rechecks by injecting a state change at an exact transaction
boundary. They are **not** a demonstration of real InnoDB blocking. No
browser, microphone, codec or audio-track verification is performed
anywhere.
"""

import re
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import event

from app import create_app
from app.blueprints.teacher import speaking as speaking_mod
from app.extensions import db
from app.models import (
    AcademicStatus,
    Assignment,
    AssignmentStatus,
    FileAccessAction,
    FileAccessLog,
    GroupTeacherAssignment,
    GroupTeacherAssignmentStatus,
    SpeakingActivity,
    SpeakingFeedback,
    SpeakingSubmission,
    UploadedFile,
    UserRole,
    UserStatus,
)
from tests.speaking_fixtures import (
    AFTER_DUE,
    DUE,
    INSTRUCTIONS,
    NOW,
    OPENS,
    TITLE,
    DUE_LOCAL,
    OPENS_LOCAL,
    Clock,
    assign_teacher,
    enroll,
    hidden_value,
    local_of,
    login_as,
    ordinary_assignment,
    serve_get,
    setup_group,
    speaking_activity,
    speaking_feedback,
    speaking_submission,
    stored_files,
    teacher_base,
    teacher_detail,
    upload_row,
    user,
    utc_of,
)


def _at(*moments):
    """Patch the Teacher Speaking blueprint's clock."""
    return patch.object(speaking_mod, "utc_reference_now", Clock(*moments))


def _create_payload(token, **overrides):
    payload = {
        "speaking_state": token,
        "title": TITLE,
        "instructions": INSTRUCTIONS,
        "opens_at": OPENS_LOCAL,
        "due_at": DUE_LOCAL,
    }
    payload.update(overrides)
    return payload


def _post_create(client, gpid, token=None, follow=False, **overrides):
    url = teacher_base(gpid) + "/new"
    if token is None:
        token = hidden_value(client, url, "speaking_state")
    return client.post(
        url, data=_create_payload(token, **overrides), follow_redirects=follow
    )


def _created_public_id(response):
    return response.headers["Location"].rsplit("/", 1)[-1]


# ===========================================================================
# Role, account and assignment authorization
# ===========================================================================


def test_anonymous_is_redirected_to_login(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        gpid, spid = group.public_id, activity.public_id
    for url in (
        teacher_base(gpid),
        teacher_base(gpid) + "/new",
        teacher_detail(gpid, spid),
        teacher_detail(gpid, spid) + "/submissions",
    ):
        resp = client.get(url)
        assert resp.status_code == 302 and "/auth/login" in resp.headers["Location"]


@pytest.mark.parametrize(
    "role",
    [UserRole.STUDENT.value, UserRole.ADMINISTRATOR.value, UserRole.RESEARCHER.value],
)
def test_non_teacher_roles_are_forbidden(app, client, role):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        gpid, spid = group.public_id, activity.public_id
        user("other@example.com", role)
    login_as(client, "other@example.com")
    assert client.get(teacher_base(gpid)).status_code == 403
    assert client.get(teacher_detail(gpid, spid)).status_code == 403
    assert client.get(teacher_detail(gpid, spid) + "/submissions").status_code == 403


def test_an_unassigned_teacher_gets_a_non_disclosing_404(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        gpid, spid = group.public_id, activity.public_id
        user("stranger@example.com", UserRole.TEACHER.value)
    login_as(client, "stranger@example.com")
    assert client.get(teacher_base(gpid)).status_code == 404
    assert client.get(teacher_detail(gpid, spid)).status_code == 404


def test_a_removed_assignment_loses_access(app, client):
    with app.app_context():
        teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        gpid, spid = group.public_id, activity.public_id
        row = GroupTeacherAssignment.query.filter_by(
            group_id=group.id, teacher_id=teacher.id
        ).one()
        row.status = GroupTeacherAssignmentStatus.REMOVED.value
        db.session.commit()
    login_as(client, "teacher@example.com")
    assert client.get(teacher_detail(gpid, spid)).status_code == 404


def test_a_co_teacher_is_an_equal_collaborator(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        assign_teacher(group, "second@example.com")
        _assignment, activity = speaking_activity(group)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "second@example.com")
    assert client.get(teacher_detail(gpid, spid)).status_code == 200


def test_another_groups_activity_is_a_404(app, client):
    with app.app_context():
        _teacher, group = setup_group("A")
        other_teacher, other_group = setup_group("B", teacher_email="other@example.com")
        _assignment, activity = speaking_activity(other_group)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")
    assert client.get(teacher_detail(gpid, spid)).status_code == 404


# ===========================================================================
# The two surfaces cannot reach each other
# ===========================================================================


def test_an_ordinary_assignment_public_id_is_a_404_on_every_speaking_route(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        ordinary = ordinary_assignment(group)
        gpid, apid = group.public_id, ordinary.public_id
    login_as(client, "teacher@example.com")
    for suffix in ("", "/edit", "/submissions"):
        assert client.get(teacher_detail(gpid, apid) + suffix).status_code == 404
    for suffix in ("/publish", "/withdraw"):
        assert client.post(teacher_detail(gpid, apid) + suffix, data={}).status_code == 404


def test_a_speaking_activity_is_a_404_on_every_ordinary_assignment_route(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        assignment, activity = speaking_activity(group)
        gpid, apid = group.public_id, assignment.public_id
        # The extension's own public id is a different value entirely.
        assert activity.public_id != assignment.public_id
    login_as(client, "teacher@example.com")
    base = f"/teacher/groups/{gpid}/assignments/{apid}"
    assert client.get(base + "/edit").status_code == 404
    assert client.get(base + "/submissions").status_code == 404
    assert client.post(base + "/toggle-publication", data={}).status_code == 404


def test_the_ordinary_assignment_list_excludes_speaking_activities(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        speaking_activity(group, title="Spoken task")
        ordinary_assignment(group, title="Written task")
        gpid = group.public_id
    login_as(client, "teacher@example.com")
    html = client.get(f"/teacher/groups/{gpid}/assignments").get_data(as_text=True)
    assert "Written task" in html
    assert "Spoken task" not in html


def test_the_speaking_list_excludes_ordinary_assignments(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        speaking_activity(group, title="Spoken task")
        ordinary_assignment(group, title="Written task")
        gpid = group.public_id
    login_as(client, "teacher@example.com")
    html = client.get(teacher_base(gpid)).get_data(as_text=True)
    assert "Spoken task" in html
    assert "Written task" not in html


def test_the_speaking_activity_public_id_never_appears_as_an_assignment_id(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        assignment, activity = speaking_activity(group)
        gpid, spid, apid = group.public_id, activity.public_id, assignment.public_id
    login_as(client, "teacher@example.com")
    html = client.get(teacher_detail(gpid, spid)).get_data(as_text=True)
    assert spid in html
    # The BACKING Assignment's own public id is never placed in a URL here.
    assert apid not in html


# ===========================================================================
# Create
# ===========================================================================


def test_create_renders_a_form_with_a_signed_state_token(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        gpid = group.public_id
    login_as(client, "teacher@example.com")
    resp = client.get(teacher_base(gpid) + "/new")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'name="speaking_state"' in html
    assert 'name="csrf_token"' in html
    # A Speaking activity carries no audio of its own: no file input here.
    assert 'type="file"' not in html


def test_a_created_activity_is_a_draft_with_both_rows(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        gpid = group.public_id
    login_as(client, "teacher@example.com")
    resp = _post_create(client, gpid)
    assert resp.status_code == 302
    with app.app_context():
        activity = SpeakingActivity.query.one()
        assignment = Assignment.query.one()
        assert activity.assignment_id == assignment.id
        assert assignment.title == TITLE
        assert assignment.instructions == INSTRUCTIONS
        assert assignment.status == AssignmentStatus.DRAFT.value
        assert assignment.published_at is None
        assert assignment.opens_at == utc_of(app, OPENS_LOCAL)
        assert assignment.due_at == utc_of(app, DUE_LOCAL)
        assert assignment.opens_at.tzinfo is None
        assert activity.created_at == activity.updated_at
        assert activity.created_at.microsecond == 0
        assert _created_public_id(resp) == activity.public_id


def test_a_forged_status_or_ownership_field_has_nowhere_to_land(app, client):
    with app.app_context():
        _teacher, group = setup_group("A")
        _other_teacher, other_group = setup_group("B", teacher_email="o@example.com")
        gpid, other_id = group.public_id, other_group.id
    login_as(client, "teacher@example.com")
    _post_create(
        client, gpid,
        status="published", published_at="2020-01-01T00:00:00",
        group_id=str(other_id), public_id="forged", creation_nonce="forged",
        assignment_id="99", speaking_activity_id="99",
    )
    with app.app_context():
        assignment = Assignment.query.one()
        activity = SpeakingActivity.query.one()
        assert assignment.status == AssignmentStatus.DRAFT.value
        assert assignment.published_at is None
        assert assignment.group_id != other_id
        assert activity.public_id != "forged"
        assert activity.creation_nonce != "forged"


def test_replaying_the_same_create_token_creates_nothing_new(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        gpid = group.public_id
    login_as(client, "teacher@example.com")
    token = hidden_value(client, teacher_base(gpid) + "/new", "speaking_state")
    first = _post_create(client, gpid, token=token)
    second = _post_create(client, gpid, token=token)
    assert first.headers["Location"] == second.headers["Location"]
    with app.app_context():
        assert SpeakingActivity.query.count() == 1
        assert Assignment.query.count() == 1


def test_a_changed_payload_replay_still_returns_the_first_activity(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        gpid = group.public_id
    login_as(client, "teacher@example.com")
    token = hidden_value(client, teacher_base(gpid) + "/new", "speaking_state")
    first = _post_create(client, gpid, token=token)
    second = _post_create(client, gpid, token=token, title="A different title")
    assert first.headers["Location"] == second.headers["Location"]
    with app.app_context():
        assert Assignment.query.one().title == TITLE


def test_a_missing_or_forged_create_token_is_refused(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        gpid = group.public_id
    login_as(client, "teacher@example.com")
    for token in ("", "not-a-token", "eyJhIjoxfQ.invalid.signature"):
        resp = _post_create(client, gpid, token=token)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/speaking/new")
    with app.app_context():
        assert SpeakingActivity.query.count() == 0


def test_a_create_token_minted_for_another_teacher_is_refused(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        assign_teacher(group, "second@example.com")
        gpid = group.public_id
    login_as(client, "second@example.com")
    token = hidden_value(client, teacher_base(gpid) + "/new", "speaking_state")
    login_as(client, "teacher@example.com")
    resp = _post_create(client, gpid, token=token)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/speaking/new")
    with app.app_context():
        assert SpeakingActivity.query.count() == 0


def test_a_create_token_from_another_module_fails_the_signature(app, client):
    """Every M06 salt is its own. An M01/M03/M05 token is worthless here."""
    from itsdangerous import URLSafeSerializer

    with app.app_context():
        _teacher, group = setup_group()
        gpid = group.public_id
        forged = URLSafeSerializer(
            app.config["SECRET_KEY"], salt="teacher.listening-create.phase4-m05.v1"
        ).dumps(
            {
                "purpose": "listening-create",
                "teacher_public_id": "x",
                "group_public_id": gpid,
                "nonce": "n",
            }
        )
    login_as(client, "teacher@example.com")
    resp = _post_create(client, gpid, token=forged)
    assert resp.status_code == 302
    with app.app_context():
        assert SpeakingActivity.query.count() == 0


def test_a_duplicate_title_is_refused_against_an_ordinary_assignment(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        ordinary_assignment(group, title=TITLE)
        gpid = group.public_id
    login_as(client, "teacher@example.com")
    resp = _post_create(client, gpid)
    assert resp.status_code == 200
    assert "already exists in this group" in resp.get_data(as_text=True)
    with app.app_context():
        assert SpeakingActivity.query.count() == 0


def _winner_with_nonce(nonce, title="Winner"):
    """Commit a competing co-teacher's activity carrying the SAME create
    nonce, in this request's own session but at a point where nothing of
    the losing request is pending -- the closest a single-connection
    SQLite test can come to a genuinely concurrent winner."""
    from app.models import Group

    _assignment, activity = speaking_activity(Group.query.one(), title=title)
    activity.creation_nonce = nonce
    db.session.commit()
    return activity.public_id


def test_a_concurrent_replay_is_resolved_after_the_locks(app, client):
    """Structural: a competing request commits the SAME nonce between this
    one's pre-lock replay check and its post-lock one. The post-lock check
    finds it and returns the existing activity -- nothing is inserted."""
    with app.app_context():
        _teacher, group = setup_group()
        gpid = group.public_id
    login_as(client, "teacher@example.com")
    token = hidden_value(client, teacher_base(gpid) + "/new", "speaking_state")
    nonce = speaking_mod._load_token(token, "speaking-create")["nonce"]

    original = speaking_mod._lock_speaking_chain
    winner = {}

    def racing(*args, **kwargs):
        result = original(*args, **kwargs)
        if not winner:
            winner["public_id"] = _winner_with_nonce(nonce)
        return result

    with patch.object(speaking_mod, "_lock_speaking_chain", racing):
        resp = _post_create(client, gpid, token=token)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(winner["public_id"])
    with app.app_context():
        assert SpeakingActivity.query.count() == 1


def test_a_uniqueness_failure_at_commit_resolves_to_the_existing_activity(app, client):
    """Structural: the competing winner commits AFTER this request's
    post-lock replay check, so the INSERT itself raises. The recovery path
    rolls back, re-reads, and returns the winner rather than a second
    row or a leaked driver message."""
    with app.app_context():
        _teacher, group = setup_group()
        gpid = group.public_id
    login_as(client, "teacher@example.com")
    token = hidden_value(client, teacher_base(gpid) + "/new", "speaking_state")
    nonce = speaking_mod._load_token(token, "speaking-create")["nonce"]

    original = speaking_mod._write_moment
    winner = {}

    def racing():
        moment = original()
        if not winner:
            winner["public_id"] = _winner_with_nonce(nonce)
        return moment

    with patch.object(speaking_mod, "_write_moment", racing):
        resp = _post_create(client, gpid, token=token, title="Loser title")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(winner["public_id"])
    with app.app_context():
        assert SpeakingActivity.query.count() == 1
        assert Assignment.query.count() == 1
        assert Assignment.query.one().title != "Loser title"


def test_creation_is_blocked_while_the_chain_is_archived(app, client):
    with app.app_context():
        _teacher, group = setup_group(group_status=AcademicStatus.ARCHIVED.value)
        gpid = group.public_id
    login_as(client, "teacher@example.com")
    resp = client.get(teacher_base(gpid) + "/new", follow_redirects=True)
    assert "Speaking activities can only be created or edited" in resp.get_data(
        as_text=True
    )
    with app.app_context():
        assert SpeakingActivity.query.count() == 0


# ===========================================================================
# Edit
# ===========================================================================


def _edit_url(gpid, spid):
    return teacher_detail(gpid, spid) + "/edit"


def _edit_payload(token, **overrides):
    payload = {
        "speaking_state": token,
        "title": "Revised title",
        "instructions": "Revised instructions.",
        "opens_at": OPENS_LOCAL,
        "due_at": DUE_LOCAL,
    }
    payload.update(overrides)
    return payload


def test_a_draft_can_be_edited(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")
    token = hidden_value(client, _edit_url(gpid, spid), "speaking_state")
    resp = client.post(_edit_url(gpid, spid), data=_edit_payload(token))
    assert resp.status_code == 302
    with app.app_context():
        assignment = Assignment.query.one()
        assert assignment.title == "Revised title"
        assert assignment.instructions == "Revised instructions."
        assert assignment.status == AssignmentStatus.DRAFT.value


def test_an_unchanged_save_is_a_no_op(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        gpid, spid = group.public_id, activity.public_id
        before = activity.updated_at
    login_as(client, "teacher@example.com")
    token = hidden_value(client, _edit_url(gpid, spid), "speaking_state")
    with app.app_context():
        unchanged = {
            "title": TITLE,
            "instructions": INSTRUCTIONS,
            "opens_at": local_of(app, OPENS),
            "due_at": local_of(app, DUE),
        }
    resp = client.post(
        _edit_url(gpid, spid),
        data=_edit_payload(token, **unchanged),
        follow_redirects=True,
    )
    assert "unchanged, so nothing was saved" in resp.get_data(as_text=True)
    with app.app_context():
        assert SpeakingActivity.query.one().updated_at == before


def test_a_stale_edit_snapshot_is_rejected_without_writing(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        assign_teacher(group, "second@example.com")
        _assignment, activity = speaking_activity(group)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")
    token = hidden_value(client, _edit_url(gpid, spid), "speaking_state")

    # A co-teacher changes the activity while this form is open.
    login_as(client, "second@example.com")
    other_token = hidden_value(client, _edit_url(gpid, spid), "speaking_state")
    client.post(_edit_url(gpid, spid), data=_edit_payload(other_token, title="Theirs"))

    login_as(client, "teacher@example.com")
    resp = client.post(
        _edit_url(gpid, spid), data=_edit_payload(token, title="Mine"),
        follow_redirects=True,
    )
    assert "was changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert Assignment.query.one().title == "Theirs"


def test_a_published_activity_is_read_only(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group, published=True)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")
    resp = client.get(_edit_url(gpid, spid), follow_redirects=True)
    assert "is published, so its title" in resp.get_data(as_text=True)
    resp = client.post(
        _edit_url(gpid, spid), data=_edit_payload("anything"), follow_redirects=True
    )
    assert "is published, so its title" in resp.get_data(as_text=True)
    with app.app_context():
        assert Assignment.query.one().title == TITLE


def test_a_recording_freezes_the_activity_permanently(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group, published=True)
        student = enroll(group)
        speaking_submission(activity, student)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")
    resp = client.get(_edit_url(gpid, spid), follow_redirects=True)
    text = resp.get_data(as_text=True)
    assert "Students have already recorded answers" in text
    # The stronger, permanent freeze is reported -- never "withdraw it first".
    assert "Withdraw it first" not in text


def test_the_edit_freeze_is_enforced_after_the_locks(app, client):
    """Structural: a recording lands between the pre-lock preview and the
    locked re-check, and the write is refused rather than applied."""
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        gpid, spid = group.public_id, activity.public_id
        group_id, activity_id = group.id, activity.id
    login_as(client, "teacher@example.com")
    token = hidden_value(client, _edit_url(gpid, spid), "speaking_state")

    original = speaking_mod._lock_speaking_chain
    fired = {"done": False}

    def racing(*args, **kwargs):
        result = original(*args, **kwargs)
        if not fired["done"]:
            fired["done"] = True
            from app.models import Group

            student = enroll(db.session.get(Group, group_id), "late@example.com")
            speaking_submission(db.session.get(SpeakingActivity, activity_id), student)
        return result

    with patch.object(speaking_mod, "_lock_speaking_chain", racing):
        resp = client.post(
            _edit_url(gpid, spid), data=_edit_payload(token), follow_redirects=True
        )
    assert "Students have already recorded answers" in resp.get_data(as_text=True)
    with app.app_context():
        assert Assignment.query.one().title == TITLE


def test_an_edit_cannot_take_a_title_already_used_in_the_group(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        ordinary_assignment(group, title="Taken")
        _assignment, activity = speaking_activity(group)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")
    token = hidden_value(client, _edit_url(gpid, spid), "speaking_state")
    resp = client.post(_edit_url(gpid, spid), data=_edit_payload(token, title="Taken"))
    assert resp.status_code == 200
    assert "already exists in this group" in resp.get_data(as_text=True)
    with app.app_context():
        assert SpeakingActivity.query.one().assignment.title == TITLE


def test_an_edit_with_a_reversed_window_is_refused(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")
    token = hidden_value(client, _edit_url(gpid, spid), "speaking_state")
    resp = client.post(
        _edit_url(gpid, spid),
        data=_edit_payload(token, opens_at="2026-06-01T08:00:00",
                           due_at="2026-05-01T08:00:00"),
    )
    assert resp.status_code == 200
    assert "must be after the opening time" in resp.get_data(as_text=True)
    with app.app_context():
        assert Assignment.query.one().opens_at == OPENS


# ===========================================================================
# Publication
# ===========================================================================


def _publish(client, gpid, spid, token=None, action="publish", follow=True):
    if token is None:
        field = "speaking_state"
        html = client.get(teacher_detail(gpid, spid)).get_data(as_text=True)
        form = html.split(f"/{action}")[1] if f"/{action}" in html else ""
        match = re.search(rf'name="{field}" value="([^"]*)"', form)
        token = match.group(1) if match else ""
    return client.post(
        teacher_detail(gpid, spid) + f"/{action}",
        data={"speaking_state": token},
        follow_redirects=follow,
    )


def test_publishing_stamps_a_fresh_whole_second_moment(app, client):
    stamp = datetime(2026, 5, 10, 9, 30, 0)
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")
    with _at(stamp.replace(microsecond=500000)):
        resp = _publish(client, gpid, spid)
    assert "Speaking activity published" in resp.get_data(as_text=True)
    with app.app_context():
        assignment = Assignment.query.one()
        assert assignment.status == AssignmentStatus.PUBLISHED.value
        assert assignment.published_at == stamp
        assert assignment.published_at.microsecond == 0


def test_publishing_is_refused_while_a_readiness_rule_fails(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        assignment, activity = speaking_activity(group)
        # Corrupt the authored content behind the form's back.
        assignment.instructions = "   "
        db.session.commit()
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")
    resp = _publish(client, gpid, spid)
    assert "needs instructions" in resp.get_data(as_text=True)
    with app.app_context():
        assert Assignment.query.one().status == AssignmentStatus.DRAFT.value


def test_the_readiness_panel_and_the_publish_rule_agree(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        assignment, activity = speaking_activity(group)
        assignment.instructions = ""
        db.session.commit()
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")
    panel = client.get(teacher_detail(gpid, spid)).get_data(as_text=True)
    assert "Not ready to publish yet" in panel
    assert "needs instructions" in panel


def test_publishing_twice_is_reported_not_repeated(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")
    token = hidden_value(client, teacher_detail(gpid, spid), "speaking_state")
    _publish(client, gpid, spid, token=token)
    with app.app_context():
        first_stamp = Assignment.query.one().published_at
    resp = _publish(client, gpid, spid, token=token)
    # The token is now stale (the status it bound has changed).
    assert "was changed by someone else" in resp.get_data(as_text=True)
    with app.app_context():
        assert Assignment.query.one().published_at == first_stamp


def test_withdrawing_returns_the_activity_to_draft(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group, published=True)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")
    resp = _publish(client, gpid, spid, action="withdraw")
    assert "Speaking activity withdrawn" in resp.get_data(as_text=True)
    with app.app_context():
        assignment = Assignment.query.one()
        assert assignment.status == AssignmentStatus.DRAFT.value
        assert assignment.published_at is None


def test_withdrawal_is_refused_once_a_recording_exists(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group, published=True)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")
    token = hidden_value(client, teacher_detail(gpid, spid), "speaking_state")
    with app.app_context():
        from app.models import Group

        student = enroll(Group.query.one())
        speaking_submission(SpeakingActivity.query.one(), student)
    resp = _publish(client, gpid, spid, token=token, action="withdraw")
    assert "Students have already recorded answers" in resp.get_data(as_text=True)
    with app.app_context():
        assert Assignment.query.one().status == AssignmentStatus.PUBLISHED.value


def test_the_withdraw_control_disappears_once_a_recording_exists(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group, published=True)
        student = enroll(group)
        speaking_submission(activity, student)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")
    html = client.get(teacher_detail(gpid, spid)).get_data(as_text=True)
    assert "/withdraw" not in html
    assert "It can no longer be withdrawn." in html


def test_publication_requires_an_operational_chain(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")
    token = hidden_value(client, teacher_detail(gpid, spid), "speaking_state")
    with app.app_context():
        from app.models import Group

        Group.query.one().status = AcademicStatus.ARCHIVED.value
        db.session.commit()
    resp = _publish(client, gpid, spid, token=token)
    assert "can only be published or withdrawn" in resp.get_data(as_text=True)
    with app.app_context():
        assert Assignment.query.one().status == AssignmentStatus.DRAFT.value


@pytest.mark.parametrize("action", ["publish", "withdraw"])
def test_a_publication_token_of_the_wrong_action_is_stale(app, client, action):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group, published=(action == "publish"))
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")
    token = hidden_value(client, teacher_detail(gpid, spid), "speaking_state")
    resp = _publish(client, gpid, spid, token=token, action=action)
    body = resp.get_data(as_text=True)
    assert "was changed by someone else" in body or "already" in body


# ===========================================================================
# Recording review
# ===========================================================================


def test_the_submission_list_shows_identity_time_and_feedback_state(app, client):
    with app.app_context():
        teacher, group = setup_group()
        _assignment, activity = speaking_activity(group, published=True)
        first = enroll(group, "a@example.com", name="Amina Q")
        second = enroll(group, "b@example.com", name="Basim R")
        submission = speaking_submission(activity, first, submitted_at=NOW)
        speaking_submission(
            activity, second, submitted_at=NOW + timedelta(minutes=5)
        )
        speaking_feedback(submission, teacher)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")
    html = client.get(teacher_detail(gpid, spid) + "/submissions").get_data(as_text=True)
    assert "Amina Q" in html and "Basim R" in html
    assert "Feedback provided" in html and "Awaiting feedback" in html
    # Newest first.
    assert html.index("Basim R") < html.index("Amina Q")


def test_the_submission_list_is_bounded_and_never_counts(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group, published=True)
        for index in range(22):
            student = enroll(group, f"s{index}@example.com", name=f"Student {index:02d}")
            speaking_submission(
                activity, student, submitted_at=NOW + timedelta(minutes=index)
            )
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")

    statements = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", record)
    try:
        html = client.get(
            teacher_detail(gpid, spid) + "/submissions"
        ).get_data(as_text=True)
    finally:
        with app.app_context():
            event.remove(db.engine, "before_cursor_execute", record)

    assert html.count("Listen</a>") == 20
    assert "Next" in html
    listing = [s for s in statements if "FROM speaking_submissions" in s]
    assert any("LIMIT" in s for s in listing)
    assert not any("count(" in s.lower() for s in listing)
    # One page-level feedback-state query, never one per row.
    assert len([s for s in statements if "speaking_feedback" in s]) == 1


def test_a_page_past_the_end_falls_back_to_page_one(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group, published=True)
        student = enroll(group, name="Only Student")
        speaking_submission(activity, student)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")
    html = client.get(
        teacher_detail(gpid, spid) + "/submissions?page=9"
    ).get_data(as_text=True)
    assert "Only Student" in html
    assert "Previous" not in html


def test_a_recording_from_another_activity_is_a_404(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _a1, first = speaking_activity(group, title="One", published=True)
        _a2, second = speaking_activity(group, title="Two", published=True)
        student = enroll(group)
        submission = speaking_submission(first, student)
        gpid = group.public_id
        wrong = teacher_detail(gpid, second.public_id)
        bpid = submission.public_id
    login_as(client, "teacher@example.com")
    assert client.get(f"{wrong}/submissions/{bpid}").status_code == 404
    assert serve_get(client, f"{wrong}/submissions/{bpid}/audio").status_code == 404


def test_a_role_inconsistent_recording_is_not_presented_as_student_work(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group, published=True)
        student = enroll(group, name="Was A Student")
        submission = speaking_submission(activity, student)
        student.role = UserRole.RESEARCHER.value
        db.session.commit()
        gpid, spid, bpid = group.public_id, activity.public_id, submission.public_id
    login_as(client, "teacher@example.com")
    html = client.get(teacher_detail(gpid, spid) + "/submissions").get_data(as_text=True)
    assert "Was A Student" not in html
    assert client.get(f"{teacher_detail(gpid, spid)}/submissions/{bpid}").status_code == 404
    assert serve_get(
        client, f"{teacher_detail(gpid, spid)}/submissions/{bpid}/audio"
    ).status_code == 404


def test_a_withdrawn_students_recording_stays_reviewable(app, client):
    from app.models import Enrollment, EnrollmentStatus

    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group, published=True)
        student = enroll(group, name="Left The Course")
        speaking_submission(activity, student)
        Enrollment.query.one().status = EnrollmentStatus.WITHDRAWN.value
        student.status = UserStatus.SUSPENDED.value
        db.session.commit()
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")
    html = client.get(teacher_detail(gpid, spid) + "/submissions").get_data(as_text=True)
    assert "Left The Course" in html


# ===========================================================================
# Authorized audio serving
# ===========================================================================


def _submission_urls(gpid, spid, bpid):
    base = f"{teacher_detail(gpid, spid)}/submissions/{bpid}"
    return base, base + "/audio", base + "/audio/download"


def test_a_teacher_can_play_and_download_a_recording(material_app, material_client):
    from app.services.speaking_audio import store_speaking_audio
    from tests.file_fixtures import minimal_webm
    import io

    class _Upload:
        def __init__(self, data, filename, mimetype):
            self.stream = io.BytesIO(data)
            self.filename = filename
            self.mimetype = mimetype

    with material_app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group, published=True)
        student = enroll(group, name="Speaker")
        stored = store_speaking_audio(
            material_app.extensions["material_config"],
            _Upload(minimal_webm(), "speaking-recording.webm", "audio/webm"),
            "speaking-recording.webm",
        )
        upload = UploadedFile(
            storage_key=stored.storage_key,
            original_filename=stored.original_filename,
            extension=stored.extension,
            category=stored.category,
            content_type=stored.content_type,
            byte_size=stored.byte_size,
            sha256=stored.sha256,
            uploaded_by_id=student.id,
        )
        db.session.add(upload)
        db.session.commit()
        submission = speaking_submission(activity, student, upload=upload)
        gpid, spid, bpid = group.public_id, activity.public_id, submission.public_id
        storage_key, digest = stored.storage_key, stored.sha256

    login_as(material_client, "teacher@example.com")
    detail, inline, download = _submission_urls(gpid, spid, bpid)

    page = material_client.get(detail).get_data(as_text=True)
    assert inline in page
    assert download in page
    # Nothing about the physical file is disclosed.
    assert storage_key not in page and digest not in page

    resp = serve_get(material_client, inline)
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("audio/webm")
    assert resp.headers["Cache-Control"] == "private, no-store, max-age=0"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert "attachment" not in resp.headers.get("Content-Disposition", "")

    resp = serve_get(material_client, download)
    assert resp.status_code == 200
    assert "attachment" in resp.headers["Content-Disposition"]

    with material_app.app_context():
        actions = [row.action for row in FileAccessLog.query.all()]
        assert FileAccessAction.INLINE.value in actions
        assert FileAccessAction.DOWNLOAD.value in actions


def test_a_non_audio_association_fails_closed(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group, published=True)
        student = enroll(group)
        upload = upload_row(
            student.id, category="document", extension="pdf",
            content_type="application/pdf",
        )
        submission = speaking_submission(activity, student, upload=upload)
        gpid, spid, bpid = group.public_id, activity.public_id, submission.public_id
    login_as(client, "teacher@example.com")
    detail, inline, download = _submission_urls(gpid, spid, bpid)
    assert client.get(detail).status_code == 404
    assert serve_get(client, inline).status_code == 404
    assert serve_get(client, download).status_code == 404


def test_a_missing_physical_file_is_a_safe_404(material_app, material_client):
    with material_app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group, published=True)
        student = enroll(group)
        submission = speaking_submission(activity, student)  # key names no real file
        gpid, spid, bpid = group.public_id, activity.public_id, submission.public_id
    login_as(material_client, "teacher@example.com")
    _detail, inline, _download = _submission_urls(gpid, spid, bpid)
    resp = serve_get(material_client, inline)
    assert resp.status_code == 404
    body = resp.get_data(as_text=True)
    assert "storage" not in body.lower()
    assert stored_files(material_app) == []


def test_another_groups_teacher_cannot_reach_a_recording(app, client):
    with app.app_context():
        _teacher, group = setup_group("A")
        setup_group("B", teacher_email="other@example.com")
        _assignment, activity = speaking_activity(group, published=True)
        student = enroll(group)
        submission = speaking_submission(activity, student)
        gpid, spid, bpid = group.public_id, activity.public_id, submission.public_id
    login_as(client, "other@example.com")
    detail, inline, download = _submission_urls(gpid, spid, bpid)
    assert client.get(detail).status_code == 404
    assert serve_get(client, inline).status_code == 404
    assert serve_get(client, download).status_code == 404


# ===========================================================================
# Response behaviour
# ===========================================================================


@pytest.mark.parametrize("suffix", ["", "/submissions"])
def test_content_bearing_pages_are_private_and_vary_on_cookie(app, client, suffix):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group, published=True)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")
    resp = client.get(teacher_detail(gpid, spid) + suffix)
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in resp.headers.get("Vary", "")


def test_the_speaking_list_is_private_and_varies_on_cookie(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        gpid = group.public_id
    login_as(client, "teacher@example.com")
    resp = client.get(teacher_base(gpid))
    assert resp.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in resp.headers.get("Vary", "")


def test_authored_fields_are_escaped(app, client):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(
            group,
            title="<script>alert('t')</script>",
            instructions="<img src=x onerror=alert('i')>",
        )
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")
    html = client.get(teacher_detail(gpid, spid)).get_data(as_text=True)
    assert "<script>alert('t')</script>" not in html
    assert "&lt;script&gt;" in html
    assert "<img src=x onerror" not in html


@pytest.mark.parametrize(
    "method,suffix",
    [
        ("post", ""),
        ("post", "/submissions"),
        ("get", "/publish"),
        ("get", "/withdraw"),
        ("delete", ""),
        ("put", "/edit"),
    ],
)
def test_unsupported_methods_fail_safely(app, client, method, suffix):
    with app.app_context():
        _teacher, group = setup_group()
        _assignment, activity = speaking_activity(group)
        gpid, spid = group.public_id, activity.public_id
    login_as(client, "teacher@example.com")
    resp = getattr(client, method)(teacher_detail(gpid, spid) + suffix)
    assert resp.status_code == 405


def test_there_is_no_delete_or_grade_endpoint(app):
    rules = {str(rule) for rule in app.url_map.iter_rules()}
    for forbidden in ("delete", "remove", "grade", "score", "mark", "archive"):
        assert not any(
            forbidden in rule and "speaking" in rule for rule in rules
        ), forbidden


def test_publication_requires_csrf_when_it_is_enabled(tmp_path):
    flask_app = create_app("testing", MATERIAL_STORAGE_ROOT=str(tmp_path / "materials"))
    flask_app.config["WTF_CSRF_ENABLED"] = True
    test_client = flask_app.test_client()
    with flask_app.app_context():
        db.create_all()
        try:
            _teacher, group = setup_group()
            _assignment, activity = speaking_activity(group)
            gpid, spid = group.public_id, activity.public_id

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
                teacher_detail(gpid, spid) + "/publish", data={"speaking_state": "x"}
            )
            assert resp.status_code == 400
            assert Assignment.query.one().status == AssignmentStatus.DRAFT.value
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
