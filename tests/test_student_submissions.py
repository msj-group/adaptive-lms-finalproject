"""Student text submissions (Phase 4 / M02): the one POST endpoint, the
immutable receipt, the deadline boundary and its post-lock authoritative
moment, duplicate no-ops, the signed submission-context snapshot, the
route-specific lock order, and the non-disclosing failure behaviour M01
established.

Time is **injected**, never waited for: every test that cares about a
moment patches ``utc_reference_now`` in the route module, so the deadline
boundary and the "the request waited past the deadline while locking"
case are exact rather than probabilistic. There are no sleeps and no
races.

SQLite (the test backend) has no ``SELECT ... FOR UPDATE`` and no
REPEATABLE READ snapshot isolation, so the structural lock tests here
prove only the *requested* reset and lock order -- never that a real
InnoDB lock blocks a concurrent transaction.
"""

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from itsdangerous import URLSafeSerializer
from sqlalchemy.exc import IntegrityError

import app.blueprints.student.assignments as student_mod
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
    Level,
    Submission,
    User,
    UserRole,
    UserStatus,
)
from app.security.passwords import hash_password
from tests.conftest import login

PW = "Sup3rSecret!123"

#: The fixed naive-UTC moment every injected clock in this module returns.
NOW = datetime(2026, 5, 10, 12, 0)

OPENS = NOW - timedelta(days=2)
DUE = NOW + timedelta(days=5)


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


def _enroll(group, student, status=EnrollmentStatus.ACTIVE.value):
    row = Enrollment(student_id=student.id, group_id=group.id, status=status)
    db.session.add(row)
    db.session.commit()
    return row


def _assignment(group, title="Task", opens_at=OPENS, due_at=DUE,
                status=AssignmentStatus.PUBLISHED.value, instructions="Do the work."):
    published_at = datetime(2026, 4, 1, 9, 0) if status == AssignmentStatus.PUBLISHED.value else None
    row = Assignment(group_id=group.id, title=title, instructions=instructions,
                     opens_at=opens_at, due_at=due_at, status=status,
                     published_at=published_at)
    db.session.add(row)
    db.session.commit()
    return row


def _setup(email="student@example.com", **hkw):
    """(student, group, assignment) -- enrolled, published, open at NOW."""
    student = _user(email, UserRole.STUDENT.value)
    group = _hierarchy(**hkw)
    _enroll(group, student)
    return student, group


def _detail_url(gpid, apid):
    return f"/student/groups/{gpid}/assignments/{apid}"


def _submit_url(gpid, apid):
    return f"{_detail_url(gpid, apid)}/submit"


class _Clock:
    """An injectable ``utc_reference_now`` replacement.

    Called with no argument it returns the next value in `moments`,
    repeating the last one for ever. That is what makes "the request
    arrived in time but the locks did not release until after the
    deadline" an exact, deterministic scenario: the pre-lock preview gets
    the first moment and the post-lock authoritative read gets the
    second.
    """

    def __init__(self, *moments):
        self.moments = list(moments) or [NOW]
        self.calls = 0

    def __call__(self, utc_now=None):
        moment = self.moments[min(self.calls, len(self.moments) - 1)]
        self.calls += 1
        return moment


def _at(*moments):
    """Patch the Student route module's clock for the duration of a
    ``with`` block."""
    return patch.object(student_mod, "utc_reference_now", _Clock(*moments))


def _context_token(client, gpid, apid):
    """The signed submission-context token the detail page embeds, or ""."""
    html = client.get(_detail_url(gpid, apid)).get_data(as_text=True)
    match = re.search(r'name="submission_context" value="([^"]*)"', html)
    return match.group(1) if match else ""


def _post(client, gpid, apid, answer="My answer.", token=None, follow=True, **extra):
    data = {"answer_text": answer}
    if token is not None:
        data["submission_context"] = token
    data.update(extra)
    return client.post(_submit_url(gpid, apid), data=data, follow_redirects=follow)


def _submit_once(app, client, gpid, apid, answer="My answer.", at=(NOW, NOW)):
    """Fetch a fresh token and post it -- the ordinary happy path."""
    with _at(*at):
        token = _context_token(client, gpid, apid)
        return _post(client, gpid, apid, answer=answer, token=token)


# ===========================================================================
# The form: shown only when it can actually be used
# ===========================================================================


def test_open_and_unsubmitted_shows_the_answer_form_and_a_finality_warning(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    with _at(NOW):
        html = client.get(_detail_url(gpid, apid)).get_data(as_text=True)
    assert f'action="{_submit_url(gpid, apid)}"' in html
    assert 'name="answer_text"' in html
    assert 'name="submission_context"' in html
    assert "cannot be edited" in html
    assert "Not submitted" in html


def test_past_due_and_unsubmitted_is_readable_but_offers_no_form(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, due_at=NOW - timedelta(days=1))
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    with _at(NOW):
        resp = client.get(_detail_url(gpid, apid))
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Do the work." in html          # still readable
    assert "Not submitted" in html
    assert "deadline for this assignment has passed" in html
    assert 'name="answer_text"' not in html
    assert _submit_url(gpid, apid) not in html


def test_a_get_never_creates_or_changes_a_submission(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    with _at(NOW):
        for _ in range(3):
            assert client.get(_detail_url(gpid, apid)).status_code == 200
    with app.app_context():
        assert Submission.query.count() == 0


def test_the_submit_endpoint_is_post_only(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    assert client.get(_submit_url(gpid, apid)).status_code == 405
    with app.app_context():
        assert Submission.query.count() == 0


def test_there_is_no_update_delete_or_resubmit_route(app):
    submission_rules = [r for r in app.url_map.iter_rules() if "submissions" in str(r)
                        or str(r).endswith("/submit")]
    assert submission_rules, "no submission routes registered at all"
    for rule in submission_rules:
        assert "DELETE" not in rule.methods
        assert "PUT" not in rule.methods
        assert "PATCH" not in rule.methods
        assert not any(word in str(rule) for word in ("/edit", "/delete", "/resubmit"))


# ===========================================================================
# The happy path and the immutable receipt
# ===========================================================================


def test_first_submission_is_stored_and_redirects_to_its_receipt(app, client):
    with app.app_context():
        student, group = _setup()
        row = _assignment(group)
        gpid, apid, sid, aid = group.public_id, row.public_id, student.id, row.id
    login(client, "student@example.com")

    with _at(NOW):
        token = _context_token(client, gpid, apid)
        resp = _post(client, gpid, apid, answer="My final answer.", token=token, follow=False)

    # Post/Redirect/Get, back to the nested detail page.
    assert resp.status_code == 302
    assert urlsplit(resp.headers["Location"]).path == _detail_url(gpid, apid)

    with app.app_context():
        rows = Submission.query.all()
        assert len(rows) == 1
        assert rows[0].assignment_id == aid
        assert rows[0].student_id == sid
        assert rows[0].answer_text == "My final answer."
        assert rows[0].submitted_at == NOW      # the server's authoritative moment
        assert uuid.UUID(rows[0].public_id).version == 4


def test_the_receipt_shows_the_answer_the_time_and_no_form(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    _submit_once(app, client, gpid, apid, answer="Answer body here.")

    with _at(NOW):
        html = client.get(_detail_url(gpid, apid)).get_data(as_text=True)
    assert "Answer body here." in html
    assert "Submitted" in html
    assert "final" in html
    assert 'name="answer_text"' not in html
    assert _submit_url(gpid, apid) not in html


def test_the_receipt_time_is_rendered_in_the_center_timezone(app, client):
    from app.services.schedule_occurrences import to_app_local

    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    _submit_once(app, client, gpid, apid)

    with app.app_context():
        expected = to_app_local(app.config["APP_TIMEZONE"], NOW).strftime("%Y-%m-%d %H:%M")
    with _at(NOW):
        html = client.get(_detail_url(gpid, apid)).get_data(as_text=True)
    assert expected in html
    assert app.config["APP_TIMEZONE"] in html
    # Positive control: the stored UTC value really does differ from the
    # rendered local one, so this is not passing on a UTC deployment.
    assert expected != NOW.strftime("%Y-%m-%d %H:%M")


# ===========================================================================
# Answer validation and normalization
# ===========================================================================


@pytest.mark.parametrize("answer", ["", "   ", "\n\n", "\t \r\n "])
def test_blank_and_whitespace_only_answers_are_rejected(app, client, answer):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, gpid, apid)
        resp = _post(client, gpid, apid, answer=answer, token=token)
    assert resp.status_code == 200
    with app.app_context():
        assert Submission.query.count() == 0


def test_an_overlong_answer_is_rejected(app, client):
    from app.models import ANSWER_MAX_LENGTH

    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, gpid, apid)
        resp = _post(client, gpid, apid, answer="x" * (ANSWER_MAX_LENGTH + 1), token=token)
    assert resp.status_code == 200
    with app.app_context():
        assert Submission.query.count() == 0


def test_an_answer_at_exactly_the_limit_is_accepted(app, client):
    from app.models import ANSWER_MAX_LENGTH

    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    _submit_once(app, client, gpid, apid, answer="x" * ANSWER_MAX_LENGTH)
    with app.app_context():
        assert len(Submission.query.one().answer_text) == ANSWER_MAX_LENGTH


def test_the_length_limit_applies_before_trimming(app, client):
    """Padding an over-limit answer with whitespace must not let it in --
    the ``Length`` validator sees the raw value."""
    from app.models import ANSWER_MAX_LENGTH

    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, gpid, apid)
        _post(client, gpid, apid, answer="   " + "x" * ANSWER_MAX_LENGTH + "   ", token=token)
    with app.app_context():
        assert Submission.query.count() == 0


def test_edge_whitespace_is_trimmed_and_internal_formatting_is_preserved(app, client):
    """The documented normalization: a plain ``.strip()`` at the edges,
    nothing at all inside."""
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    _submit_once(app, client, gpid, apid,
                 answer="\n\n  First line\n\n\tIndented second\n  Third  \n\n ")
    with app.app_context():
        assert Submission.query.one().answer_text == (
            "First line\n\n\tIndented second\n  Third"
        )


def test_unicode_survives_the_round_trip(app, client):
    answer = "أهلاً — ✓ naïve café"
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    _submit_once(app, client, gpid, apid, answer=answer)
    with app.app_context():
        assert Submission.query.one().answer_text == answer
    with _at(NOW):
        assert answer in client.get(_detail_url(gpid, apid)).get_data(as_text=True)


def test_a_malicious_answer_is_escaped_and_never_rendered_as_html(app, client):
    payload = "<script>alert('xss')</script>"
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    _submit_once(app, client, gpid, apid, answer=f"Answer {payload}")

    with _at(NOW):
        html = client.get(_detail_url(gpid, apid)).get_data(as_text=True)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
    with app.app_context():
        # Stored verbatim: escaping is a rendering concern, not a storage one.
        assert Submission.query.one().answer_text == f"Answer {payload}"


def test_answer_line_breaks_are_preserved_by_css_not_injected_markup(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    _submit_once(app, client, gpid, apid, answer="Line one\nLine two")
    with _at(NOW):
        html = client.get(_detail_url(gpid, apid)).get_data(as_text=True)
    assert "white-space: pre-wrap" in html
    assert "Line one\nLine two" in html
    assert "<br>" not in html.split("Your Answer")[-1]


# ===========================================================================
# The deadline boundary and the post-lock authoritative moment
# ===========================================================================


def test_a_submission_at_exactly_opens_at_is_accepted(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, opens_at=NOW, due_at=NOW + timedelta(days=1))
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    _submit_once(app, client, gpid, apid)
    with app.app_context():
        assert Submission.query.count() == 1


def test_a_first_submission_at_exactly_due_at_is_rejected(app, client):
    """``opens_at <= now < due_at``: the deadline boundary itself has
    passed."""
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, opens_at=NOW - timedelta(days=1), due_at=NOW)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")

    # One second before the deadline the form is offered; the token is
    # minted then, exactly as a real Student's browser would hold it.
    just_before = NOW - timedelta(seconds=1)
    with _at(just_before):
        token = _context_token(client, gpid, apid)
    with _at(NOW):
        resp = _post(client, gpid, apid, token=token)
    assert "deadline for this assignment has passed" in resp.get_data(as_text=True)
    with app.app_context():
        assert Submission.query.count() == 0


def test_one_second_before_the_deadline_is_still_accepted(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, opens_at=NOW - timedelta(days=1), due_at=NOW)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    just_before = NOW - timedelta(seconds=1)
    _submit_once(app, client, gpid, apid, at=(just_before, just_before))
    with app.app_context():
        assert Submission.query.one().submitted_at == just_before


def test_a_request_that_waited_past_the_deadline_while_locking_is_rejected(app, client):
    """The whole reason the acceptance moment is read AFTER the locks.

    The pre-lock preview sees a moment comfortably inside the window; by
    the time the locks are held the deadline has passed. Reusing the
    request-arrival timestamp would accept this; reading the clock after
    the locks rejects it.
    """
    due_at = NOW + timedelta(minutes=1)
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, opens_at=OPENS, due_at=due_at)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")

    with _at(NOW):
        token = _context_token(client, gpid, apid)

    # 1st call = the pre-lock preview (in time); 2nd = post-lock (too late).
    clock = _Clock(NOW, due_at + timedelta(seconds=1))
    with patch.object(student_mod, "utc_reference_now", clock):
        resp = _post(client, gpid, apid, token=token)

    assert clock.calls >= 2, "the route must read the clock again after locking"
    assert "deadline for this assignment has passed" in resp.get_data(as_text=True)
    with app.app_context():
        assert Submission.query.count() == 0


def test_the_persisted_timestamp_is_the_post_lock_moment_not_the_preview(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")

    with _at(NOW):
        token = _context_token(client, gpid, apid)
    later = NOW + timedelta(minutes=7)
    with patch.object(student_mod, "utc_reference_now", _Clock(NOW, later)):
        _post(client, gpid, apid, token=token)

    with app.app_context():
        assert Submission.query.one().submitted_at == later

# ===========================================================================
# Canonical second precision for the acceptance moment
#
# `submissions.submitted_at` is a plain `DateTime`, i.e. MySQL
# `DATETIME` with fractional precision 0 -- and MySQL ROUNDS an excess
# fraction rather than truncating it. An acceptance decided at
# 11:59:59.900000 against a 12:00:00 deadline would therefore be judged
# "in time" in Python and then persisted AT the deadline. The route
# truncates the authoritative moment to whole seconds before both the
# comparisons and the write, so the instant that decided acceptance is
# the instant stored.
# ===========================================================================


def _due_at_boundary(app, group_name="Group A"):
    """(gpid, apid, due_at) for an Assignment whose deadline is NOW."""
    _, group = _setup(group_name=group_name)
    row = _assignment(group, opens_at=NOW - timedelta(days=1), due_at=NOW)
    return group.public_id, row.public_id, row.due_at


@pytest.mark.parametrize("micro", [1, 100000, 500000, 900000, 999999])
def test_a_fractional_instant_in_the_final_second_is_accepted_and_truncated(
    app, client, micro
):
    """The whole final second before the deadline stays usable, and what
    is written back is the truncated whole second -- never a fraction
    MySQL would round up onto the deadline itself."""
    with app.app_context():
        gpid, apid, due_at = _due_at_boundary(app)
    login(client, "student@example.com")

    fractional = due_at - timedelta(seconds=1) + timedelta(microseconds=micro)
    assert fractional.microsecond == micro
    assert fractional < due_at

    _submit_once(app, client, gpid, apid, answer="In the final second.",
                 at=(fractional, fractional))

    with app.app_context():
        stored = Submission.query.one()
        assert stored.answer_text == "In the final second."
        # The canonical contract: whole seconds, and still strictly
        # before the deadline once stored.
        assert stored.submitted_at.microsecond == 0
        assert stored.submitted_at == fractional.replace(microsecond=0)
        assert stored.submitted_at < due_at


def test_the_rounding_hazard_is_gone_for_the_worst_case_fraction(app, client):
    """The exact reproduction: 11:59:59.900000 against a 12:00:00
    deadline. Rounded to `DATETIME(0)` that value becomes 12:00:00, which
    is the moment this project defines as past due."""
    with app.app_context():
        gpid, apid, due_at = _due_at_boundary(app)
    login(client, "student@example.com")

    hazard = due_at - timedelta(microseconds=100000)
    naive_round = hazard.replace(microsecond=0) + timedelta(seconds=1)
    assert naive_round == due_at, "the hazard this test exists for must be real"

    _submit_once(app, client, gpid, apid, at=(hazard, hazard))

    with app.app_context():
        stored = Submission.query.one()
        assert stored.submitted_at == due_at - timedelta(seconds=1)
        assert stored.submitted_at != due_at
        assert stored.submitted_at < due_at


@pytest.mark.parametrize("micro", [0, 1, 500000, 999999])
def test_a_first_submission_at_or_after_the_deadline_is_rejected(app, client, micro):
    """Equality at `due_at` and every fractional instant after it are all
    refused -- truncation must not turn 12:00:00.999999 into "in time"."""
    with app.app_context():
        gpid, apid, due_at = _due_at_boundary(app)
    login(client, "student@example.com")

    just_before = due_at - timedelta(seconds=1)
    with _at(just_before):
        token = _context_token(client, gpid, apid)

    at_or_after = due_at + timedelta(microseconds=micro)
    with _at(at_or_after):
        resp = _post(client, gpid, apid, token=token)
    assert "deadline for this assignment has passed" in resp.get_data(as_text=True)
    with app.app_context():
        assert Submission.query.count() == 0


def test_truncation_floors_and_never_advances_past_the_opening_time(app, client):
    """Truncation must FLOOR, so it can only ever make a request earlier.
    At `opens_at` plus a fraction the Assignment is open and the stored
    moment is exactly `opens_at`."""
    opens_at = NOW
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, opens_at=opens_at, due_at=NOW + timedelta(days=1))
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")

    fractional = opens_at + timedelta(microseconds=750000)
    _submit_once(app, client, gpid, apid, at=(fractional, fractional))

    with app.app_context():
        stored = Submission.query.one()
        assert stored.submitted_at == opens_at
        assert stored.submitted_at.microsecond == 0


def test_a_fractional_instant_before_the_opening_time_is_still_refused(app, client):
    """Flooring is fail-closed at the other boundary too: one microsecond
    before `opens_at` truncates to a whole second BEFORE it, so the
    Assignment is not yet visible."""
    opens_at = NOW
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, opens_at=opens_at, due_at=NOW + timedelta(days=1))
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")

    before = opens_at - timedelta(microseconds=1)
    with _at(before):
        assert _post(client, gpid, apid, token="x", follow=False).status_code == 404
    with app.app_context():
        assert Submission.query.count() == 0


def test_waiting_past_the_deadline_while_locking_is_still_rejected_with_fractions(
    app, client
):
    """Correction 1 must not weaken the post-lock clock placement: the
    preview moment is comfortably in time, the post-lock moment is not,
    and truncation does not rescue it."""
    with app.app_context():
        gpid, apid, due_at = _due_at_boundary(app)
    login(client, "student@example.com")

    preview = due_at - timedelta(minutes=5)
    with _at(preview):
        token = _context_token(client, gpid, apid)

    clock = _Clock(preview, due_at + timedelta(microseconds=1))
    with patch.object(student_mod, "utc_reference_now", clock):
        resp = _post(client, gpid, apid, token=token)

    assert clock.calls >= 2, "the route must read the clock again after locking"
    assert "deadline for this assignment has passed" in resp.get_data(as_text=True)
    with app.app_context():
        assert Submission.query.count() == 0


def test_the_route_truncates_rather_than_the_shared_clock(app, client):
    """The shared `utc_reference_now` keeps its microseconds for every
    other caller; only this route's authoritative moment is normalized."""
    from app.services.schedule_occurrences import utc_reference_now

    precise = datetime(2026, 5, 10, 11, 59, 59, 123456, tzinfo=timezone.utc)
    assert utc_reference_now(precise).microsecond == 123456

    with app.app_context():
        assert student_mod._acceptance_moment.__module__ == student_mod.__name__
    with patch.object(student_mod, "utc_reference_now",
                      lambda utc_now=None: datetime(2026, 5, 10, 11, 59, 59, 123456)):
        with app.test_request_context():
            assert student_mod._acceptance_moment() == datetime(2026, 5, 10, 11, 59, 59)


def test_a_browser_supplied_timestamp_is_ignored(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, gpid, apid)
        _post(client, gpid, apid, token=token,
              submitted_at="2020-01-01T00:00:00", created_at="2020-01-01T00:00:00")
    with app.app_context():
        assert Submission.query.one().submitted_at == NOW


def test_a_past_due_receipt_stays_readable(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, opens_at=OPENS, due_at=NOW + timedelta(hours=1))
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    _submit_once(app, client, gpid, apid, answer="Submitted in time.")

    long_after = NOW + timedelta(days=400)
    with _at(long_after):
        html = client.get(_detail_url(gpid, apid)).get_data(as_text=True)
    assert "Submitted in time." in html
    assert "Past due" in html


# ===========================================================================
# Ownership is never taken from the request
# ===========================================================================


def test_forged_ownership_fields_are_ignored(app, client):
    with app.app_context():
        student, group = _setup()
        other = _user("other@example.com", UserRole.STUDENT.value)
        _enroll(group, other)
        row = _assignment(group)
        other_row = _assignment(group, title="Other task")
        gpid, apid = group.public_id, row.public_id
        sid, oid, aid, other_aid = student.id, other.id, row.id, other_row.id

    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, gpid, apid)
        _post(client, gpid, apid, token=token,
              student_id=str(oid), assignment_id=str(other_aid),
              public_id="forged-public-id", group_id="999", status="draft")

    with app.app_context():
        stored = Submission.query.one()
        assert stored.student_id == sid
        assert stored.assignment_id == aid
        assert stored.public_id != "forged-public-id"
        assert uuid.UUID(stored.public_id).version == 4


def test_another_students_answer_is_never_exposed(app, client):
    with app.app_context():
        student, group = _setup()
        other = _user("other@example.com", UserRole.STUDENT.value)
        _enroll(group, other)
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
        db.session.add(Submission(assignment_id=row.id, student_id=other.id,
                                  answer_text="SECRET OTHER ANSWER", submitted_at=NOW))
        db.session.commit()

    login(client, "student@example.com")
    with _at(NOW):
        html = client.get(_detail_url(gpid, apid)).get_data(as_text=True)
    assert "SECRET OTHER ANSWER" not in html
    # Positive: this Student is told they have NOT submitted, and is
    # offered their own form -- the other row did not stand in for theirs.
    assert "Not submitted" in html
    assert 'name="answer_text"' in html


def test_each_student_keeps_their_own_receipt(app, client):
    with app.app_context():
        _, group = _setup()
        other = _user("other@example.com", UserRole.STUDENT.value)
        _enroll(group, other)
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id

    login(client, "student@example.com")
    _submit_once(app, client, gpid, apid, answer="MINE")
    client.post("/auth/logout")

    login(client, "other@example.com")
    _submit_once(app, client, gpid, apid, answer="THEIRS")
    with _at(NOW):
        html = client.get(_detail_url(gpid, apid)).get_data(as_text=True)
    assert "THEIRS" in html and "MINE" not in html
    with app.app_context():
        assert Submission.query.count() == 2


# ===========================================================================
# Authorization and non-disclosing failures
# ===========================================================================


def test_anonymous_post_is_redirected_to_login_and_writes_nothing(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    resp = _post(client, gpid, apid, token="anything", follow=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]
    with app.app_context():
        assert Submission.query.count() == 0


@pytest.mark.parametrize(
    "role", [UserRole.TEACHER.value, UserRole.ADMINISTRATOR.value, UserRole.RESEARCHER.value]
)
def test_non_student_roles_cannot_submit(app, client, role):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
        _user(f"{role}@example.com", role)
    login(client, f"{role}@example.com")
    assert _post(client, gpid, apid, token="x", follow=False).status_code == 403
    with app.app_context():
        assert Submission.query.count() == 0


def test_submission_requires_csrf(app, client):
    csrf_app = __import__("app", fromlist=["create_app"]).create_app(
        "testing", WTF_CSRF_ENABLED=True
    )
    with csrf_app.app_context():
        db.create_all()
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
        csrf_client = csrf_app.test_client()
        login(csrf_client, "student@example.com")
        resp = csrf_client.post(_submit_url(gpid, apid), data={"answer_text": "x"})
        assert resp.status_code == 400
        assert Submission.query.count() == 0
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


@pytest.mark.parametrize("archived", ["term", "level", "course", "group"])
def test_an_archived_ancestor_hides_the_assignment_and_blocks_submission(app, client, archived):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid, gid = group.public_id, row.public_id, group.id
    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, gpid, apid)

    with app.app_context():
        group = db.session.get(Group, gid)
        target = {"term": group.academic_term, "level": group.course.level,
                  "course": group.course, "group": group}[archived]
        target.status = AcademicStatus.ARCHIVED.value
        db.session.commit()

    with _at(NOW):
        assert client.get(_detail_url(gpid, apid)).status_code == 404
        assert _post(client, gpid, apid, token=token, follow=False).status_code == 404
    with app.app_context():
        assert Submission.query.count() == 0


def test_a_draft_assignment_cannot_be_submitted(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, status=AssignmentStatus.DRAFT.value)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    with _at(NOW):
        assert _post(client, gpid, apid, token="x", follow=False).status_code == 404
    with app.app_context():
        assert Submission.query.count() == 0


def test_a_not_yet_open_assignment_cannot_be_submitted(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, opens_at=NOW + timedelta(days=1),
                          due_at=NOW + timedelta(days=8))
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    with _at(NOW):
        assert _post(client, gpid, apid, token="x", follow=False).status_code == 404
    with app.app_context():
        assert Submission.query.count() == 0


@pytest.mark.parametrize("enrollment", ["withdrawn", "absent"])
def test_without_an_active_enrollment_nothing_can_be_submitted(app, client, enrollment):
    with app.app_context():
        student = _user("student@example.com", UserRole.STUDENT.value)
        group = _hierarchy()
        if enrollment == "withdrawn":
            _enroll(group, student, status=EnrollmentStatus.WITHDRAWN.value)
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    with _at(NOW):
        assert client.get(_detail_url(gpid, apid)).status_code == 404
        assert _post(client, gpid, apid, token="x", follow=False).status_code == 404
    with app.app_context():
        assert Submission.query.count() == 0


def test_cross_group_and_cross_assignment_identifiers_all_404(app, client):
    with app.app_context():
        student, first = _setup(group_name="First")
        second = _hierarchy(group_name="Second")
        mine = _assignment(first, title="Mine")
        foreign = _assignment(second, title="Foreign")
        first_pid, second_pid = first.public_id, second.public_id
        mine_pid, foreign_pid = mine.public_id, foreign.public_id
    login(client, "student@example.com")
    with _at(NOW):
        codes = {
            _post(client, second_pid, foreign_pid, token="x", follow=False).status_code,
            _post(client, first_pid, foreign_pid, token="x", follow=False).status_code,
            _post(client, second_pid, mine_pid, token="x", follow=False).status_code,
            _post(client, first_pid, "nope", token="x", follow=False).status_code,
            _post(client, "nope", "nope", token="x", follow=False).status_code,
        }
    assert codes == {404}
    with app.app_context():
        assert Submission.query.count() == 0


def test_a_row_with_a_broken_student_role_never_grants_visibility(app, client):
    """A foreign key into ``users`` proves the row exists, never that it
    belongs to a Student -- so the Enrollment's User role is re-proved."""
    with app.app_context():
        student, group = _setup()
        row = _assignment(group)
        gpid, apid, sid = group.public_id, row.public_id, student.id
    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, gpid, apid)

    with app.app_context():
        db.session.get(User, sid).role = UserRole.TEACHER.value
        db.session.commit()

    with _at(NOW):
        # `roles_required` sees the changed role first; either way nothing
        # is written and nothing about the Assignment is disclosed.
        assert _post(client, gpid, apid, token=token, follow=False).status_code in (403, 404)
    with app.app_context():
        assert Submission.query.count() == 0


# ===========================================================================
# Withdrawal / unpublish / archive hide access without deleting history
# ===========================================================================


@pytest.mark.parametrize("change", ["withdraw", "unpublish", "archive"])
def test_losing_access_hides_the_page_but_keeps_the_stored_row(app, client, change):
    with app.app_context():
        student, group = _setup()
        row = _assignment(group)
        gpid, apid, gid, sid = group.public_id, row.public_id, group.id, student.id
    login(client, "student@example.com")
    _submit_once(app, client, gpid, apid, answer="Kept for ever.")
    with app.app_context():
        original = Submission.query.one()
        original_id, original_public_id = original.id, original.public_id
        original_stamp = original.submitted_at

    with app.app_context():
        if change == "withdraw":
            Enrollment.query.filter_by(student_id=sid, group_id=gid).one().status = (
                EnrollmentStatus.WITHDRAWN.value
            )
        elif change == "unpublish":
            assignment = Assignment.query.one()
            assignment.status = AssignmentStatus.DRAFT.value
            assignment.published_at = None
        else:
            db.session.get(Group, gid).status = AcademicStatus.ARCHIVED.value
        db.session.commit()

    with _at(NOW):
        assert client.get(_detail_url(gpid, apid)).status_code == 404
        assert _post(client, gpid, apid, token="x", follow=False).status_code == 404

    with app.app_context():
        stored = Submission.query.one()
        assert stored.id == original_id
        assert stored.public_id == original_public_id
        assert stored.answer_text == "Kept for ever."
        assert stored.submitted_at == original_stamp


# ===========================================================================
# Duplicates -- an authorized no-op, never a rewrite
# ===========================================================================


def test_a_double_post_creates_exactly_one_row(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, gpid, apid)
        _post(client, gpid, apid, answer="First", token=token)
        resp = _post(client, gpid, apid, answer="First", token=token)
    assert "already submitted" in resp.get_data(as_text=True).lower()
    with app.app_context():
        assert Submission.query.count() == 1
        assert Submission.query.one().answer_text == "First"


def test_a_replayed_duplicate_with_different_text_never_overwrites(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, gpid, apid)
        _post(client, gpid, apid, answer="ORIGINAL", token=token)
    with app.app_context():
        first = Submission.query.one()
        first_id, first_public_id, first_stamp = first.id, first.public_id, first.submitted_at

    later = NOW + timedelta(hours=2)
    with _at(later):
        resp = _post(client, gpid, apid, answer="REPLACEMENT", token=token, follow=False)

    assert resp.status_code == 302
    assert urlsplit(resp.headers["Location"]).path == _detail_url(gpid, apid)
    with app.app_context():
        stored = Submission.query.one()
        assert stored.id == first_id
        assert stored.public_id == first_public_id
        assert stored.answer_text == "ORIGINAL"
        assert stored.submitted_at == first_stamp


def test_a_duplicate_after_the_deadline_is_an_authorized_no_op(app, client):
    """Not an error: the Student already submitted in time, so a late
    replay must land on their receipt rather than a deadline warning."""
    due_at = NOW + timedelta(hours=1)
    with app.app_context():
        _, group = _setup()
        row = _assignment(group, opens_at=OPENS, due_at=due_at)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, gpid, apid)
        _post(client, gpid, apid, answer="In time", token=token)

    after = due_at + timedelta(days=3)
    with _at(after):
        resp = _post(client, gpid, apid, answer="Late replay", token=token)
    text = resp.get_data(as_text=True)
    assert "already submitted" in text.lower()
    assert "deadline for this assignment has passed, so it can no longer" not in text
    with app.app_context():
        assert Submission.query.one().answer_text == "In time"


def test_a_duplicate_with_a_stale_token_still_lands_on_the_receipt(app, client):
    """The existing-submission check runs BEFORE first-insert validation,
    so a Teacher edit after the fact cannot turn a settled submission
    into an error."""
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid, aid = group.public_id, row.public_id, row.id
    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, gpid, apid)
        _post(client, gpid, apid, answer="Done", token=token)

    with app.app_context():
        db.session.get(Assignment, aid).title = "Rewritten title"
        db.session.commit()

    with _at(NOW):
        resp = _post(client, gpid, apid, answer="Again", token=token)
    assert "already submitted" in resp.get_data(as_text=True).lower()
    with app.app_context():
        assert Submission.query.one().answer_text == "Done"


def test_a_duplicate_does_not_bypass_current_visibility(app, client):
    with app.app_context():
        student, group = _setup()
        row = _assignment(group)
        gpid, apid, gid, sid = group.public_id, row.public_id, group.id, student.id
    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, gpid, apid)
        _post(client, gpid, apid, answer="Done", token=token)

    with app.app_context():
        Enrollment.query.filter_by(student_id=sid, group_id=gid).one().status = (
            EnrollmentStatus.WITHDRAWN.value
        )
        db.session.commit()

    with _at(NOW):
        assert _post(client, gpid, apid, token=token, follow=False).status_code == 404
    with app.app_context():
        assert Submission.query.one().answer_text == "Done"


def test_a_duplicate_still_requires_csrf(app, client):
    csrf_app = __import__("app", fromlist=["create_app"]).create_app(
        "testing", WTF_CSRF_ENABLED=True
    )
    with csrf_app.app_context():
        db.create_all()
        student, group = _setup()
        row = _assignment(group)
        db.session.add(Submission(assignment_id=row.id, student_id=student.id,
                                  answer_text="Done", submitted_at=NOW))
        db.session.commit()
        gpid, apid = group.public_id, row.public_id
        csrf_client = csrf_app.test_client()
        login(csrf_client, "student@example.com")
        assert csrf_client.post(_submit_url(gpid, apid),
                                data={"answer_text": "x"}).status_code == 400
        assert Submission.query.one().answer_text == "Done"
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def test_a_concurrent_first_submission_resolves_to_the_existing_receipt(app, client):
    """The database constraint as the final defense: another transaction
    inserted the same pair after this request's own existence check
    passed. The route must catch the IntegrityError, roll back, PROVE a
    row now exists, and report it as already submitted."""
    with app.app_context():
        student, group = _setup()
        row = _assignment(group)
        gpid, apid, aid, sid = group.public_id, row.public_id, row.id, student.id
    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, gpid, apid)

    original_commit = db.session.commit
    state = {"raced": False}

    def commit_after_a_race():
        if not state["raced"]:
            state["raced"] = True
            db.session.rollback()
            db.session.add(Submission(assignment_id=aid, student_id=sid,
                                      answer_text="WON THE RACE", submitted_at=NOW))
            original_commit()
            raise IntegrityError("insert", {}, Exception("duplicate key"))
        return original_commit()

    with _at(NOW), patch.object(db.session, "commit", side_effect=commit_after_a_race):
        resp = _post(client, gpid, apid, answer="LOST THE RACE", token=token)

    assert "already submitted" in resp.get_data(as_text=True).lower()
    with app.app_context():
        assert Submission.query.one().answer_text == "WON THE RACE"


def test_a_non_duplicate_integrity_error_is_not_reported_as_success(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, gpid, apid)

    def always_fail():
        db.session.rollback()
        raise IntegrityError("insert", {}, Exception("some other constraint"))

    with _at(NOW), patch.object(db.session, "commit", side_effect=always_fail):
        resp = _post(client, gpid, apid, token=token)

    text = resp.get_data(as_text=True)
    assert "could not be submitted" in text
    assert "already submitted" not in text.lower()
    assert "was submitted" not in text
    # No SQL, driver text, parameters or internal ids leak out.
    for leak in ("IntegrityError", "constraint", "INSERT", "sqlalchemy", "sqlite"):
        assert leak not in text, leak
    with app.app_context():
        assert Submission.query.count() == 0

# ===========================================================================
# IntegrityError recovery must RE-AUTHORIZE
#
# A rolled-back read is not authorization evidence. The concurrent change
# that caused the conflict may also have ended this Student's access, so
# the recovery path re-proves the whole visibility formula with a fresh
# reference moment and the original nested public identifiers before any
# row is allowed to influence the response.
#
# These are SQLite simulations of a race, injected at a chosen point.
# They are NOT a demonstration of real concurrent InnoDB blocking.
# ===========================================================================

#: What the competing transaction wrote. Any of these strings appearing in
#: a response would be a disclosure.
COMPETING_ANSWER = "COMPETING ANSWER FROM THE OTHER TRANSACTION"


def _stored(app):
    """The single stored Submission, re-read from the database.

    ``expire_all`` first: the SQLite ``StaticPool`` backend makes the test
    share the request's session, so without it these assertions could be
    reading the identity map rather than what was actually persisted.
    """
    with app.app_context():
        db.session.expire_all()
        return Submission.query.one()


def _flashes(client):
    with client.session_transaction() as session:
        return list(session.get("_flashes") or [])


def _post_losing_a_race(app, client, gpid, apid, token, aid, sid, mutate=None,
                        duplicate=True, answer="MY ANSWER"):
    """POST a first submission that loses a race.

    The patched commit rolls back this request's pending INSERT, applies
    the competing write (and any `mutate` state change) in one committed
    step, then raises ``IntegrityError`` -- exactly the shape the route's
    recovery path has to cope with. With `duplicate=False` no competing
    row is written, so the error is a non-duplicate one.
    """
    original_commit = db.session.commit
    state = {"raced": False}

    def racing_commit():
        if state["raced"]:
            return original_commit()
        state["raced"] = True
        db.session.rollback()
        if duplicate:
            db.session.add(Submission(assignment_id=aid, student_id=sid,
                                      answer_text=COMPETING_ANSWER, submitted_at=NOW))
        if mutate is not None:
            mutate()
        original_commit()
        raise IntegrityError("insert", {}, Exception("duplicate key"))

    with _at(NOW), patch.object(db.session, "commit", side_effect=racing_commit):
        resp = _post(client, gpid, apid, answer=answer, token=token, follow=False)
    assert state["raced"], "the injected race never fired"
    return resp


def _race_fixture(app, client, **hkw):
    """(gpid, apid, aid, sid, gid, token) for a Student ready to submit."""
    with app.app_context():
        student, group = _setup(**hkw)
        row = _assignment(group)
        ids = (group.public_id, row.public_id, row.id, student.id, group.id)
    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, ids[0], ids[1])
    assert token
    return ids + (token,)


def test_recovery_still_authorized_is_a_duplicate_no_op(app, client):
    """The one case that may end in a receipt: the competing submission
    landed and the Student is still fully authorized."""
    gpid, apid, aid, sid, _gid, token = _race_fixture(app, client)
    resp = _post_losing_a_race(app, client, gpid, apid, token, aid, sid)

    assert resp.status_code == 302
    assert urlsplit(resp.headers["Location"]).path == _detail_url(gpid, apid)
    assert any("already submitted" in message.lower()
               for _category, message in _flashes(client))

    stored = _stored(app)
    assert stored.answer_text == COMPETING_ANSWER   # never replaced
    assert stored.submitted_at == NOW
    with app.app_context():
        assert Submission.query.count() == 1


@pytest.mark.parametrize(
    "loss",
    ["enrollment_withdrawn", "assignment_unpublished", "term_archived",
     "level_archived", "course_archived", "group_archived", "account_suspended"],
)
def test_recovery_refuses_non_disclosingly_when_access_was_lost(app, client, loss):
    """Whatever the competing transaction also did, a Student who can no
    longer see the Assignment gets the established non-disclosing 404 --
    never an "already submitted" message, which would confirm both that
    the Assignment exists and that work was submitted for it."""
    gpid, apid, aid, sid, gid, token = _race_fixture(app, client)

    def mutate():
        group = db.session.get(Group, gid)
        if loss == "enrollment_withdrawn":
            Enrollment.query.filter_by(student_id=sid, group_id=gid).one().status = (
                EnrollmentStatus.WITHDRAWN.value
            )
        elif loss == "assignment_unpublished":
            assignment = db.session.get(Assignment, aid)
            assignment.status = AssignmentStatus.DRAFT.value
            assignment.published_at = None
        elif loss == "account_suspended":
            db.session.get(User, sid).status = UserStatus.SUSPENDED.value
        else:
            target = {
                "term_archived": group.academic_term,
                "level_archived": group.course.level,
                "course_archived": group.course,
                "group_archived": group,
            }[loss]
            target.status = AcademicStatus.ARCHIVED.value

    resp = _post_losing_a_race(app, client, gpid, apid, token, aid, sid, mutate=mutate)

    assert resp.status_code == 404
    body = resp.get_data(as_text=True)
    assert COMPETING_ANSWER not in body
    assert "already submitted" not in body.lower()
    for _category, message in _flashes(client):
        assert "already submitted" not in message.lower()
        assert "was submitted" not in message

    # The competing row is intact and no second row was created.
    stored = _stored(app)
    assert stored.answer_text == COMPETING_ANSWER
    assert stored.submitted_at == NOW
    with app.app_context():
        assert Submission.query.count() == 1


def test_recovery_refuses_when_the_student_role_became_invalid(app, client):
    """A foreign key into ``users`` proves the row exists, never that it
    is still a Student's. Whether the role guard or the re-authorization
    query catches it, the result is a refusal that discloses nothing and
    writes nothing."""
    gpid, apid, aid, sid, _gid, token = _race_fixture(app, client)

    def mutate():
        db.session.get(User, sid).role = UserRole.TEACHER.value

    resp = _post_losing_a_race(app, client, gpid, apid, token, aid, sid, mutate=mutate)

    assert resp.status_code in (403, 404)
    body = resp.get_data(as_text=True)
    assert COMPETING_ANSWER not in body
    assert "already submitted" not in body.lower()
    for _category, message in _flashes(client):
        assert "already submitted" not in message.lower()

    stored = _stored(app)
    assert stored.answer_text == COMPETING_ANSWER
    with app.app_context():
        assert Submission.query.count() == 1


def test_recovery_keeps_the_original_public_id_and_timestamp(app, client):
    """Nothing on the recovery path writes, overwrites or deletes."""
    gpid, apid, aid, sid, _gid, token = _race_fixture(app, client)

    original_commit = db.session.commit
    with app.app_context():
        pass

    resp = _post_losing_a_race(app, client, gpid, apid, token, aid, sid,
                               answer="MINE SHOULD NEVER LAND")
    assert resp.status_code == 302

    stored = _stored(app)
    assert stored.answer_text == COMPETING_ANSWER
    assert stored.submitted_at == NOW
    assert uuid.UUID(stored.public_id).version == 4

    # And the receipt the Student is redirected to shows the persisted
    # row, not their rejected attempt.
    with _at(NOW):
        html = client.get(_detail_url(gpid, apid)).get_data(as_text=True)
    assert COMPETING_ANSWER in html
    assert "MINE SHOULD NEVER LAND" not in html
    assert original_commit is not None  # the spy was fully unwound


def test_recovery_reports_a_non_duplicate_integrity_error_generically(app, client):
    """Re-authorization succeeding does not mean success: with no row
    proven, the response stays the generic safe failure."""
    gpid, apid, aid, sid, _gid, token = _race_fixture(app, client)
    resp = _post_losing_a_race(app, client, gpid, apid, token, aid, sid,
                               duplicate=False)

    assert resp.status_code == 302
    messages = [message for _category, message in _flashes(client)]
    assert any("could not be submitted" in message for message in messages)
    assert not any("already submitted" in message.lower() for message in messages)
    assert not any("was submitted" in message for message in messages)
    for message in messages:
        for leak in ("IntegrityError", "constraint", "INSERT", "sqlalchemy", "sqlite"):
            assert leak not in message, leak
    with app.app_context():
        db.session.expire_all()
        assert Submission.query.count() == 0


def test_recovery_refuses_a_non_duplicate_error_when_access_was_also_lost(app, client):
    """Authorization is re-established BEFORE the row question is even
    asked, so a lost-access recovery fails identically whether or not a
    competing row exists."""
    gpid, apid, aid, sid, gid, token = _race_fixture(app, client)

    def mutate():
        Enrollment.query.filter_by(student_id=sid, group_id=gid).one().status = (
            EnrollmentStatus.WITHDRAWN.value
        )

    resp = _post_losing_a_race(app, client, gpid, apid, token, aid, sid,
                               mutate=mutate, duplicate=False)
    assert resp.status_code == 404
    messages = [message for _category, message in _flashes(client)]
    assert not any("could not be submitted" in message for message in messages)
    assert not any("already submitted" in message.lower() for message in messages)
    with app.app_context():
        db.session.expire_all()
        assert Submission.query.count() == 0


def test_recovery_uses_a_fresh_query_rather_than_pre_rollback_state(app, client):
    """Structural: after the rollback the route re-runs the full
    SQL-scoped visibility query before consulting `submissions` at all."""
    gpid, apid, aid, sid, gid, token = _race_fixture(app, client)

    calls = {"visibility": 0, "receipt": 0, "order": []}
    original_detail = student_mod.student_assignment_detail
    original_receipt = student_mod.student_submission

    def detail_spy(*args, **kwargs):
        calls["visibility"] += 1
        calls["order"].append("visibility")
        return original_detail(*args, **kwargs)

    def receipt_spy(*args, **kwargs):
        calls["receipt"] += 1
        calls["order"].append("receipt")
        return original_receipt(*args, **kwargs)

    with patch.object(student_mod, "student_assignment_detail", side_effect=detail_spy), \
         patch.object(student_mod, "student_submission", side_effect=receipt_spy):
        resp = _post_losing_a_race(app, client, gpid, apid, token, aid, sid)

    assert resp.status_code == 302
    # One pre-lock preview + one recovery re-authorization, and the
    # recovery visibility query precedes the recovery receipt lookup.
    assert calls["visibility"] == 2
    assert calls["receipt"] >= 1
    assert calls["order"][-2:] == ["visibility", "receipt"]


# ===========================================================================
# The signed submission-context snapshot
# ===========================================================================


def _forged_token(app, payload, salt=None):
    serializer = URLSafeSerializer(
        app.config["SECRET_KEY"], salt=salt or student_mod._SUBMISSION_CONTEXT_SALT
    )
    return serializer.dumps(payload)


@pytest.mark.parametrize("token", ["", "not-a-token", "abc.def.ghi"])
def test_a_missing_or_malformed_token_is_rejected(app, client, token):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    with _at(NOW):
        resp = _post(client, gpid, apid, token=token)
    assert "was changed since this page was opened" in resp.get_data(as_text=True)
    with app.app_context():
        assert Submission.query.count() == 0


def test_a_token_signed_with_the_wrong_secret_is_rejected(app, client):
    with app.app_context():
        student, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
        payload = {
            "student_public_id": student.public_id,
            "group_public_id": gpid,
            "assignment_public_id": apid,
            "title": row.title,
            "instructions": row.instructions,
            "opens_at": row.opens_at.strftime("%Y-%m-%dT%H:%M:%S"),
            "due_at": row.due_at.strftime("%Y-%m-%dT%H:%M:%S"),
        }
    forged = URLSafeSerializer(
        "a-completely-different-secret", salt=student_mod._SUBMISSION_CONTEXT_SALT
    ).dumps(payload)

    login(client, "student@example.com")
    with _at(NOW):
        resp = _post(client, gpid, apid, token=forged)
    assert "was changed since this page was opened" in resp.get_data(as_text=True)
    with app.app_context():
        assert Submission.query.count() == 0


def test_a_token_signed_with_another_salt_is_rejected(app, client):
    """The M02 salt is dedicated: a Teacher edit-snapshot token cannot be
    replayed here even though both use the same SECRET_KEY."""
    with app.app_context():
        student, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
        payload = {
            "student_public_id": student.public_id,
            "group_public_id": gpid,
            "assignment_public_id": apid,
            "title": row.title,
            "instructions": row.instructions,
            "opens_at": row.opens_at.strftime("%Y-%m-%dT%H:%M:%S"),
            "due_at": row.due_at.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        wrong_salt = _forged_token(
            app, payload, salt="teacher.assignment-edit-snapshot.phase4-m01.v1"
        )
    login(client, "student@example.com")
    with _at(NOW):
        resp = _post(client, gpid, apid, token=wrong_salt)
    assert "was changed since this page was opened" in resp.get_data(as_text=True)
    with app.app_context():
        assert Submission.query.count() == 0


@pytest.mark.parametrize("shape", ["missing_field", "extra_field", "not_a_dict"])
def test_a_wrong_shaped_payload_is_rejected(app, client, shape):
    with app.app_context():
        student, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
        payload = {
            "student_public_id": student.public_id,
            "group_public_id": gpid,
            "assignment_public_id": apid,
            "title": row.title,
            "instructions": row.instructions,
            "opens_at": row.opens_at.strftime("%Y-%m-%dT%H:%M:%S"),
            "due_at": row.due_at.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if shape == "missing_field":
            payload.pop("due_at")
        elif shape == "extra_field":
            payload["surprise"] = "value"
        else:
            payload = ["not", "a", "dict"]
        token = _forged_token(app, payload)
    login(client, "student@example.com")
    with _at(NOW):
        resp = _post(client, gpid, apid, token=token)
    assert "was changed since this page was opened" in resp.get_data(as_text=True)
    with app.app_context():
        assert Submission.query.count() == 0


def test_another_students_token_cannot_be_used(app, client):
    with app.app_context():
        _, group = _setup()
        other = _user("other@example.com", UserRole.STUDENT.value)
        _enroll(group, other)
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id

    login(client, "other@example.com")
    with _at(NOW):
        others_token = _context_token(client, gpid, apid)
    client.post("/auth/logout")

    login(client, "student@example.com")
    with _at(NOW):
        resp = _post(client, gpid, apid, token=others_token)
    assert "was changed since this page was opened" in resp.get_data(as_text=True)
    with app.app_context():
        assert Submission.query.count() == 0


@pytest.mark.parametrize("mismatch", ["group", "assignment"])
def test_a_cross_group_or_cross_assignment_token_cannot_be_used(app, client, mismatch):
    with app.app_context():
        student, first = _setup(group_name="First")
        second = _hierarchy(group_name="Second")
        _enroll(second, student)
        target = _assignment(first, title="Target")
        gpid, apid = first.public_id, target.public_id
        payload = {
            "student_public_id": student.public_id,
            "group_public_id": second.public_id if mismatch == "group" else gpid,
            "assignment_public_id": (
                apid if mismatch == "group" else _assignment(second, title="Other").public_id
            ),
            "title": target.title,
            "instructions": target.instructions,
            "opens_at": target.opens_at.strftime("%Y-%m-%dT%H:%M:%S"),
            "due_at": target.due_at.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        token = _forged_token(app, payload)
    login(client, "student@example.com")
    with _at(NOW):
        resp = _post(client, gpid, apid, token=token)
    assert "was changed since this page was opened" in resp.get_data(as_text=True)
    with app.app_context():
        assert Submission.query.count() == 0


@pytest.mark.parametrize("field", ["title", "instructions", "opens_at", "due_at"])
def test_a_teacher_edit_to_any_bound_field_invalidates_an_open_form(app, client, field):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid, aid = group.public_id, row.public_id, row.id
    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, gpid, apid)

    with app.app_context():
        assignment = db.session.get(Assignment, aid)
        if field == "title":
            assignment.title = "A different task"
        elif field == "instructions":
            assignment.instructions = "Completely different instructions."
        elif field == "opens_at":
            assignment.opens_at = OPENS - timedelta(hours=3)
        else:
            assignment.due_at = DUE + timedelta(days=2)
        db.session.commit()

    with _at(NOW):
        resp = _post(client, gpid, apid, token=token)
    assert "was changed since this page was opened" in resp.get_data(as_text=True)
    with app.app_context():
        assert Submission.query.count() == 0


def test_a_publication_toggle_alone_does_not_invalidate_an_open_form(app, client):
    """``status`` and ``published_at`` are deliberately outside the bound
    payload: an unpublish/republish changes nothing the Student read."""
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid, aid = group.public_id, row.public_id, row.id
    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, gpid, apid)

    with app.app_context():
        assignment = db.session.get(Assignment, aid)
        assignment.status = AssignmentStatus.DRAFT.value
        assignment.published_at = None
        db.session.commit()
    with app.app_context():
        assignment = db.session.get(Assignment, aid)
        assignment.status = AssignmentStatus.PUBLISHED.value
        assignment.published_at = datetime(2026, 5, 9, 8, 0)
        db.session.commit()

    with _at(NOW):
        resp = _post(client, gpid, apid, answer="Accepted anyway", token=token)
    assert "was submitted" in resp.get_data(as_text=True)
    with app.app_context():
        assert Submission.query.one().answer_text == "Accepted anyway"


def test_the_token_is_compared_against_the_locked_row_not_the_preview(app, client):
    """The edit lands *between* the pre-lock preview and the locks. Only
    an authoritative post-lock comparison catches it."""
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid, aid = group.public_id, row.public_id, row.id
    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, gpid, apid)

    original = student_mod.lock_group_in_open_transaction

    def edit_then_lock(public_id):
        db.session.get(Assignment, aid).title = "Retitled mid-request"
        db.session.commit()
        return original(public_id)

    with _at(NOW), patch.object(
        student_mod, "lock_group_in_open_transaction", side_effect=edit_then_lock
    ):
        resp = _post(client, gpid, apid, token=token)

    assert "was changed since this page was opened" in resp.get_data(as_text=True)
    with app.app_context():
        assert Submission.query.count() == 0


def test_a_stale_rejection_discards_the_attempted_answer(app, client):
    """Never pair a fresh token with an answer written against the old
    wording -- that is exactly the bypass this rejection closes."""
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid, aid = group.public_id, row.public_id, row.id
    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, gpid, apid)
    with app.app_context():
        db.session.get(Assignment, aid).title = "A different task"
        db.session.commit()

    with _at(NOW):
        resp = _post(client, gpid, apid, answer="ANSWER TO THE OLD TASK", token=token)

    html = resp.get_data(as_text=True)
    assert "ANSWER TO THE OLD TASK" not in html
    # It is a real PRG onto a freshly loaded form, not a re-rendered POST.
    assert "A different task" in html
    assert 'name="answer_text"' in html
    with app.app_context():
        assert Submission.query.count() == 0


def test_an_ordinary_validation_error_preserves_the_answer_and_the_token(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, gpid, apid)
        resp = _post(client, gpid, apid, answer="x" * 10001, token=token)

    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert f'name="submission_context" value="{token}"' in html
    assert "x" * 10001 in html
    with app.app_context():
        assert Submission.query.count() == 0

    # And the preserved token still works once the answer is fixed.
    with _at(NOW):
        _post(client, gpid, apid, answer="Short enough.", token=token)
    with app.app_context():
        assert Submission.query.one().answer_text == "Short enough."


# ===========================================================================
# Cache headers
# ===========================================================================


def test_every_personalized_response_is_private_no_store_and_varies_on_cookie(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")

    with _at(NOW):
        token = _context_token(client, gpid, apid)
        form_page = client.get(_detail_url(gpid, apid))
        error_page = _post(client, gpid, apid, answer="", token=token)
        _post(client, gpid, apid, answer="Done", token=token)
        receipt = client.get(_detail_url(gpid, apid))

    for resp in (form_page, error_page, receipt):
        assert resp.headers["Cache-Control"] == "private, no-store"
        assert "Cookie" in resp.headers["Vary"]


# ===========================================================================
# Structural: single reset + the route-specific lock order
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


#: AcademicTerm -> Level -> Course -> Group -> Student User -> Enrollment
#: -> Assignment -> the existing Submission for this Assignment + Student.
_EXPECTED_LOCKS = [
    "lock:AcademicTerm", "lock:Level", "lock:Course", "lock:Group",
    "lock:User", "lock:Enrollment", "lock:Assignment", "lock:Submission",
]


def test_a_successful_submission_uses_one_reset_and_the_canonical_lock_order(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, gpid, apid)
        events = _capture_locks(lambda: _post(client, gpid, apid, token=token))

    assert events.count("reset") == 1
    assert [e for e in events if e.startswith("lock:")] == _EXPECTED_LOCKS
    with app.app_context():
        assert Submission.query.count() == 1


def test_the_same_lock_order_is_requested_for_a_duplicate(app, client):
    with app.app_context():
        student, group = _setup()
        row = _assignment(group)
        db.session.add(Submission(assignment_id=row.id, student_id=student.id,
                                  answer_text="Done", submitted_at=NOW))
        db.session.commit()
        gpid, apid = group.public_id, row.public_id
    login(client, "student@example.com")
    with _at(NOW):
        events = _capture_locks(lambda: _post(client, gpid, apid, token="x"))
    assert [e for e in events if e.startswith("lock:")] == _EXPECTED_LOCKS


def test_a_membership_change_between_preview_and_lock_is_caught(app, client):
    """The post-lock recheck, not the preview, is what authorizes the
    write."""
    with app.app_context():
        student, group = _setup()
        row = _assignment(group)
        gpid, apid, gid, sid = group.public_id, row.public_id, group.id, student.id
    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, gpid, apid)

    original = student_mod.lock_group_in_open_transaction

    def withdraw_then_lock(public_id):
        Enrollment.query.filter_by(student_id=sid, group_id=gid).one().status = (
            EnrollmentStatus.WITHDRAWN.value
        )
        db.session.commit()
        return original(public_id)

    with _at(NOW), patch.object(
        student_mod, "lock_group_in_open_transaction", side_effect=withdraw_then_lock
    ):
        resp = _post(client, gpid, apid, token=token, follow=False)

    assert resp.status_code == 404
    with app.app_context():
        assert Submission.query.count() == 0


def test_an_unpublish_between_preview_and_lock_is_caught(app, client):
    with app.app_context():
        _, group = _setup()
        row = _assignment(group)
        gpid, apid, aid = group.public_id, row.public_id, row.id
    login(client, "student@example.com")
    with _at(NOW):
        token = _context_token(client, gpid, apid)

    original = student_mod.lock_group_in_open_transaction

    def unpublish_then_lock(public_id):
        assignment = db.session.get(Assignment, aid)
        assignment.status = AssignmentStatus.DRAFT.value
        assignment.published_at = None
        db.session.commit()
        return original(public_id)

    with _at(NOW), patch.object(
        student_mod, "lock_group_in_open_transaction", side_effect=unpublish_then_lock
    ):
        resp = _post(client, gpid, apid, token=token, follow=False)

    assert resp.status_code == 404
    with app.app_context():
        assert Submission.query.count() == 0


def test_no_internal_ids_reach_the_rendered_page(app, client):
    """Asserted positively -- every identifier segment of every rendered
    URL is parsed and required to BE a UUID -- rather than by hunting for
    a numeric substring, which a legitimate UUID prefix can match."""
    uuid_pattern = re.compile(
        r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
    )

    with app.app_context():
        student, group = _setup()
        row = _assignment(group)
        gpid, apid = group.public_id, row.public_id
        gid, aid, sid = group.id, row.id, student.id
    login(client, "student@example.com")
    _submit_once(app, client, gpid, apid)
    with app.app_context():
        submission = Submission.query.one()
        spid, submission_id = submission.public_id, submission.id

    with _at(NOW):
        html = client.get(_detail_url(gpid, apid)).get_data(as_text=True)

    forbidden = {str(i) for i in (gid, aid, sid, submission_id)}
    paths = [urlsplit(href).path for href in re.findall(r'href="([^"]*)"', html)]
    checked = 0
    for path in paths:
        segments = [seg for seg in path.split("/") if seg]
        for position, segment in enumerate(segments):
            assert not segment.isdigit(), f"{path!r} carries a bare numeric segment"
            if segment in ("groups", "assignments") and position < len(segments) - 1:
                identifier = segments[position + 1]
                assert uuid_pattern.match(identifier), (path, identifier)
                assert identifier not in forbidden
                checked += 1
    assert checked, "the page rendered no identifier-bearing link to validate"

    # The Student receipt needs no Submission identifier at all, and the
    # internal one certainly never appears -- neither in a link nor in a
    # form value.
    assert uuid_pattern.match(spid)
    assert spid != str(submission_id)
    for value in re.findall(r'value="([^"]*)"', html):
        assert value not in forbidden
