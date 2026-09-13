"""Phase 4 / M12 -- the Teacher and Student discussion routes.

Access control and non-disclosure, topic creation, replies, locking and
reopening, signed state and replay defence, CSRF and HTTP methods,
rendering safety, bounded pagination, navigation and the private-page
response headers.
"""

import re
import time
import uuid
from datetime import timedelta

import pytest
from flask import g, render_template
from flask_login import login_user
from itsdangerous.timed import TimestampSigner
from sqlalchemy import event

import tests.discussion_fixtures as fx
from app import create_app
from app.extensions import db
from app.models import (
    DiscussionReply,
    DiscussionTopic,
    EnrollmentStatus,
    Group,
    GroupTeacherAssignmentStatus,
    Notification,
    User,
    UserStatus,
)
from app.services import discussion_tokens

_UNVERIFIED = "This form could not be verified"
_CHANGED = "was locked or reopened after the page was opened"
_LOCKED_FLASH = "This topic is locked. It can still be read"
_MODERATION_UNVERIFIED = "That action could not be verified"
_MODERATION_STALE = "locked or reopened by someone else"

_TITLE_LINK = re.compile(
    r'href="/(?:teacher|student)/groups/[0-9a-f-]{36}/discussions/[0-9a-f-]{36}">([^<]*)</a>'
)
_TOPIC_BODY = re.compile(r'data-topic-body style="[^"]*">([^<]*)</div>')
_REPLY_BODY = re.compile(r'data-reply-body style="[^"]*">([^<]*)</div>')
_ACTIVE_NAV = re.compile(r'portal-nav__link--active"\s+href="([^"]+)"')
_CSRF_INPUT = re.compile(r'<input[^>]*name="csrf_token"[^>]*>')


def _html(response):
    return response.get_data(as_text=True)


def _titles(html):
    return _TITLE_LINK.findall(html)


def _replies(html):
    return _REPLY_BODY.findall(html)


def _portal_nav(html):
    start = html.index('<nav class="portal-nav"')
    return html[start: html.index("</nav>", start)]


def _nav_hrefs(html):
    return re.findall(r'href="([^"]+)"', _portal_nav(html))


def _active(html):
    return _ACTIVE_NAV.findall(_portal_nav(html))


def _csrf(html):
    return re.search(r'value="([^"]+)"', _CSRF_INPUT.search(html).group(0)).group(1)


def _standard():
    """A classroom with one open topic, as plain ids."""
    teacher, student, group = fx.classroom(teacher_name="Tina Teacher",
                                           student_name="Sam Student")
    row = fx.topic(group, teacher)
    return {
        "teacher": teacher.id,
        "student": student.id,
        "teacher_public_id": teacher.public_id,
        "student_public_id": student.public_id,
        "group": group.id,
        "gp": group.public_id,
        "topic": row.id,
        "tp": row.public_id,
    }


def _email(role):
    return f"{role}@example.com"


def _actor_public_id(ids, role):
    return ids["teacher_public_id"] if role == fx.TEACHER else ids["student_public_id"]


def _teacher_gets(gp, tp):
    return [fx.TEACHER_OVERVIEW, fx.teacher_list(gp), fx.teacher_new(gp), fx.teacher_topic(gp, tp)]


def _teacher_posts(gp, tp):
    return [fx.teacher_new(gp), fx.teacher_reply(gp, tp), fx.teacher_lock(gp, tp),
            fx.teacher_reopen(gp, tp)]


def _student_gets(gp, tp):
    return [fx.STUDENT_OVERVIEW, fx.student_list(gp), fx.student_topic(gp, tp)]


def _student_posts(gp, tp):
    return [fx.student_reply(gp, tp)]


_UNTOUCHED = {"topics": 1, "replies": 0, "notifications": 0}


# ===========================================================================
# Access control and non-disclosure
# ===========================================================================


def test_anonymous_visitors_follow_the_login_redirect(app, client):
    with app.app_context():
        ids = _standard()
    gp, tp = ids["gp"], ids["tp"]
    for url in _teacher_gets(gp, tp) + _student_gets(gp, tp):
        response = client.get(url)
        assert response.status_code == 302, url
        assert "/auth/login" in response.headers["Location"], url
    for url in _teacher_posts(gp, tp) + _student_posts(gp, tp):
        response = client.post(url, data={"title": "T", "body": "B"})
        assert response.status_code == 302, url
        assert "/auth/login" in response.headers["Location"], url
    with app.app_context():
        assert fx.counts() == _UNTOUCHED


@pytest.mark.parametrize("role", [fx.ADMIN, fx.RESEARCHER])
def test_administrators_and_researchers_are_forbidden_everywhere(app, client, role):
    with app.app_context():
        ids = _standard()
        fx.user("other@example.com", role)
    fx.login_as(client, "other@example.com")
    gp, tp = ids["gp"], ids["tp"]
    for url in _teacher_gets(gp, tp) + _student_gets(gp, tp):
        assert client.get(url).status_code == 403, url
    for url in _teacher_posts(gp, tp) + _student_posts(gp, tp):
        assert client.post(url, data={"title": "T", "body": "B"}).status_code == 403, url
    with app.app_context():
        assert fx.counts() == _UNTOUCHED


def test_each_portal_role_is_forbidden_from_the_other_roles_surface(app, client):
    with app.app_context():
        ids = _standard()
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, _email(fx.STUDENT))
    for url in _teacher_gets(gp, tp):
        assert client.get(url).status_code == 403, url
    for url in _teacher_posts(gp, tp):
        assert client.post(url, data={"title": "T", "body": "B"}).status_code == 403, url
    fx.login_as(client, _email(fx.TEACHER))
    for url in _student_gets(gp, tp):
        assert client.get(url).status_code == 403, url
    for url in _student_posts(gp, tp):
        assert client.post(url, data={"body": "B"}).status_code == 403, url
    with app.app_context():
        assert fx.counts() == _UNTOUCHED


def test_current_members_open_their_own_discussion_pages(app, client):
    with app.app_context():
        ids = _standard()
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, _email(fx.TEACHER))
    for url in _teacher_gets(gp, tp):
        assert client.get(url).status_code == 200, url
    fx.login_as(client, _email(fx.STUDENT))
    for url in _student_gets(gp, tp):
        assert client.get(url).status_code == 200, url


@pytest.mark.parametrize("role", [fx.TEACHER, fx.STUDENT])
def test_malformed_unknown_and_non_canonical_ids_are_the_same_404(app, client, role):
    with app.app_context():
        ids = _standard()
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, _email(role))
    bodies = set()
    for bad in ("not-a-uuid", str(uuid.uuid4()), "12", gp.upper(), tp):
        for url in (fx.list_url(role, bad), fx.topic_url(role, bad, tp)):
            response = client.get(url)
            assert response.status_code == 404, url
            bodies.add(_html(response))
        response = client.post(fx.reply_url(role, bad, tp), data={"body": "x"})
        assert response.status_code == 404
    for bad in ("not-a-uuid", str(uuid.uuid4()), "12", tp.upper(), gp):
        response = client.get(fx.topic_url(role, gp, bad))
        assert response.status_code == 404, bad
        bodies.add(_html(response))
        assert client.post(fx.reply_url(role, gp, bad), data={"body": "x"}).status_code == 404
        if role == fx.TEACHER:
            assert client.post(fx.teacher_lock(gp, bad)).status_code == 404
            assert client.post(fx.teacher_reopen(gp, bad)).status_code == 404
    if role == fx.TEACHER:
        for bad in ("not-a-uuid", gp.upper()):
            assert client.get(fx.teacher_new(bad)).status_code == 404
            assert client.post(fx.teacher_new(bad), data={"title": "T"}).status_code == 404
    assert len(bodies) == 1
    with app.app_context():
        assert fx.counts() == _UNTOUCHED


def test_a_topic_of_another_group_is_404_even_to_a_member_of_both(app, client):
    with app.app_context():
        teacher, student, group_a = fx.classroom("A")
        group_b = fx.hierarchy("B")
        fx.assign(group_b, teacher)
        fx.enroll(group_b, student)
        topic_b = fx.topic(group_b, teacher, title="Only in B")
        ga, gb, tb = group_a.public_id, group_b.public_id, topic_b.public_id
    for role in (fx.TEACHER, fx.STUDENT):
        fx.login_as(client, _email(role))
        token = fx.page_state(client, fx.topic_url(role, gb, tb), "reply_state")
        assert token
        assert client.get(fx.topic_url(role, ga, tb)).status_code == 404
        response = client.post(fx.reply_url(role, ga, tb),
                               data={"body": "Smuggled", "reply_state": token})
        assert response.status_code == 404
        if role == fx.TEACHER:
            moderation = fx.page_state(client, fx.teacher_topic(gb, tb), "moderation_state")
            assert client.post(fx.teacher_lock(ga, tb),
                               data={"moderation_state": moderation}).status_code == 404
    with app.app_context():
        assert DiscussionReply.query.count() == 0
        assert DiscussionTopic.query.one().status == fx.OPEN


def test_people_outside_the_group_receive_404_and_never_see_it_listed(app, client):
    with app.app_context():
        ids = _standard()
        elsewhere = fx.hierarchy("Elsewhere")
        fx.assign(elsewhere, fx.user("outsider-teacher@example.com", fx.TEACHER))
        fx.enroll(elsewhere, fx.user("outsider-student@example.com", fx.STUDENT))
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, "outsider-teacher@example.com")
    for url in (fx.teacher_list(gp), fx.teacher_new(gp), fx.teacher_topic(gp, tp)):
        assert client.get(url).status_code == 404, url
    for url in _teacher_posts(gp, tp):
        assert client.post(url, data={"title": "T", "body": "B"}).status_code == 404, url
    overview = _html(client.get(fx.TEACHER_OVERVIEW))
    assert fx.teacher_list(gp) not in overview and "Group Elsewhere" in overview
    fx.login_as(client, "outsider-student@example.com")
    for url in (fx.student_list(gp), fx.student_topic(gp, tp)):
        assert client.get(url).status_code == 404, url
    assert client.post(fx.student_reply(gp, tp), data={"body": "B"}).status_code == 404
    overview = _html(client.get(fx.STUDENT_OVERVIEW))
    assert fx.student_list(gp) not in overview and "Group Elsewhere" in overview
    with app.app_context():
        assert fx.counts() == _UNTOUCHED


def _end_access(kind, ids):
    group = db.session.get(Group, ids["group"])
    term, level, course = fx.ancestors(group)
    if kind == "enrollment_withdrawn":
        student = db.session.get(User, ids["student"])
        fx.set_status(fx.enrollment_of(group, student), EnrollmentStatus.WITHDRAWN.value)
    elif kind == "assignment_removed":
        teacher = db.session.get(User, ids["teacher"])
        fx.set_status(fx.assignment_of(group, teacher),
                      GroupTeacherAssignmentStatus.REMOVED.value)
    else:
        row = {"group": group, "course": course, "level": level, "term": term}[kind]
        fx.set_status(row, fx.ARCHIVED)


_ENDINGS = [(fx.STUDENT, "enrollment_withdrawn"), (fx.TEACHER, "assignment_removed")] + [
    (role, kind) for role in (fx.TEACHER, fx.STUDENT) for kind in ("group", "course", "level",
                                                                   "term")
]


@pytest.mark.parametrize("role, kind", _ENDINGS)
def test_access_ends_immediately_with_the_relationship_and_the_rows_stay(app, client, role,
                                                                         kind):
    with app.app_context():
        ids = _standard()
        fx.reply(db.session.get(DiscussionTopic, ids["topic"]),
                 db.session.get(User, ids["student"]))
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, _email(role))
    reply_token = fx.page_state(client, fx.topic_url(role, gp, tp), "reply_state")
    assert reply_token
    if role == fx.TEACHER:
        moderation_token = fx.page_state(client, fx.teacher_topic(gp, tp), "moderation_state")
        create_token = fx.page_state(client, fx.teacher_new(gp), "topic_state")
    with app.app_context():
        _end_access(kind, ids)
        before = fx.counts()
    for url in (fx.list_url(role, gp), fx.topic_url(role, gp, tp)):
        assert client.get(url).status_code == 404, url
    response = client.post(fx.reply_url(role, gp, tp),
                           data={"body": "Too late", "reply_state": reply_token})
    assert response.status_code == 404
    overview = fx.TEACHER_OVERVIEW if role == fx.TEACHER else fx.STUDENT_OVERVIEW
    assert fx.list_url(role, gp) not in _html(client.get(overview))
    if role == fx.TEACHER:
        assert client.get(fx.teacher_new(gp)).status_code == 404
        assert client.post(fx.teacher_new(gp), data={
            "title": "T", "body": "B", "topic_state": create_token}).status_code == 404
        assert client.post(fx.teacher_lock(gp, tp),
                           data={"moderation_state": moderation_token}).status_code == 404
    with app.app_context():
        assert fx.counts() == before == {"topics": 1, "replies": 1, "notifications": 0}
        row = db.session.get(DiscussionTopic, ids["topic"])
        assert (row.status, row.version) == (fx.OPEN, 1)


def test_a_withdrawn_student_does_not_end_the_teachers_access(app, client):
    with app.app_context():
        ids = _standard()
        _end_access("enrollment_withdrawn", ids)
    fx.login_as(client, _email(fx.TEACHER))
    assert client.get(fx.teacher_topic(ids["gp"], ids["tp"])).status_code == 200


@pytest.mark.parametrize("role", [fx.TEACHER, fx.STUDENT])
def test_a_suspended_account_loses_discussion_access_immediately(app, client, role):
    with app.app_context():
        ids = _standard()
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, _email(role))
    token = fx.page_state(client, fx.topic_url(role, gp, tp), "reply_state")
    with app.app_context():
        fx.set_status(db.session.get(User, ids[role]), UserStatus.SUSPENDED.value)
    fx.fresh_identity()
    response = client.get(fx.topic_url(role, gp, tp))
    assert response.status_code == 302 and "/auth/login" in response.headers["Location"]
    fx.fresh_identity()
    response = client.post(fx.reply_url(role, gp, tp), data={"body": "B", "reply_state": token})
    assert response.status_code == 302 and "/auth/login" in response.headers["Location"]
    with app.app_context():
        assert fx.counts() == _UNTOUCHED


def test_restoring_the_enrollment_restores_access(app, client):
    with app.app_context():
        ids = _standard()
        _end_access("enrollment_withdrawn", ids)
    fx.login_as(client, _email(fx.STUDENT))
    assert client.get(fx.student_topic(ids["gp"], ids["tp"])).status_code == 404
    with app.app_context():
        group = db.session.get(Group, ids["group"])
        student = db.session.get(User, ids["student"])
        fx.set_status(fx.enrollment_of(group, student), EnrollmentStatus.ACTIVE.value)
    assert client.get(fx.student_topic(ids["gp"], ids["tp"])).status_code == 200


# ===========================================================================
# Endpoints, methods and CSRF
# ===========================================================================


def test_the_discussion_endpoints_are_exactly_these(app):
    rules = {
        (rule.rule, frozenset(rule.methods - {"HEAD", "OPTIONS"}))
        for rule in app.url_map.iter_rules()
        if "discussion" in rule.rule
    }
    topic = "/teacher/groups/<group_public_id>/discussions/<topic_public_id>"
    student_topic = "/student/groups/<group_public_id>/discussions/<topic_public_id>"
    get, post = frozenset({"GET"}), frozenset({"POST"})
    assert rules == {
        ("/teacher/discussions", get),
        ("/teacher/groups/<group_public_id>/discussions", get),
        ("/teacher/groups/<group_public_id>/discussions/new", get),
        ("/teacher/groups/<group_public_id>/discussions/new", post),
        (topic, get),
        (topic + "/reply", post),
        (topic + "/lock", post),
        (topic + "/reopen", post),
        ("/student/discussions", get),
        ("/student/groups/<group_public_id>/discussions", get),
        (student_topic, get),
        (student_topic + "/reply", post),
    }
    assert not any(rule.rule.startswith("/admin") and "discussion" in rule.rule
                   for rule in app.url_map.iter_rules())


def test_a_student_has_no_creation_or_moderation_endpoint(app, client):
    with app.app_context():
        ids = _standard()
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, _email(fx.STUDENT))
    assert client.get(f"/student/groups/{gp}/discussions/new").status_code == 404
    assert client.post(f"/student/groups/{gp}/discussions/new",
                       data={"title": "T", "body": "B"}).status_code == 405
    assert client.post(fx.student_list(gp), data={"title": "T", "body": "B"}).status_code == 405
    for suffix in ("/lock", "/reopen", "/edit", "/delete", "/hide", "/pin"):
        assert client.post(fx.student_topic(gp, tp) + suffix).status_code == 404, suffix
    html = _html(client.get(fx.student_topic(gp, tp)))
    assert "moderation_state" not in html
    assert "/lock" not in html and "/reopen" not in html
    assert "Lock topic" not in html and "Reopen topic" not in html
    assert "New topic" not in _html(client.get(fx.student_list(gp)))
    assert client.post(fx.teacher_lock(gp, tp)).status_code == 403
    with app.app_context():
        assert fx.counts() == _UNTOUCHED
        assert DiscussionTopic.query.one().status == fx.OPEN


def test_unsupported_methods_return_405(app, client):
    with app.app_context():
        ids = _standard()
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, _email(fx.TEACHER))
    for url in (fx.teacher_reply(gp, tp), fx.teacher_lock(gp, tp), fx.teacher_reopen(gp, tp)):
        assert client.get(url).status_code == 405, url
    for url in (fx.TEACHER_OVERVIEW, fx.teacher_list(gp), fx.teacher_topic(gp, tp)):
        assert client.post(url, data={"body": "B"}).status_code == 405, url
    for method in ("PUT", "PATCH", "DELETE"):
        for url in (fx.teacher_topic(gp, tp), fx.teacher_new(gp), fx.teacher_lock(gp, tp)):
            assert client.open(url, method=method).status_code == 405, (method, url)
    fx.login_as(client, _email(fx.STUDENT))
    assert client.get(fx.student_reply(gp, tp)).status_code == 405
    for url in (fx.STUDENT_OVERVIEW, fx.student_topic(gp, tp)):
        assert client.post(url, data={"body": "B"}).status_code == 405, url
    for method in ("PUT", "PATCH", "DELETE"):
        assert client.open(fx.student_topic(gp, tp), method=method).status_code == 405
    with app.app_context():
        assert fx.counts() == _UNTOUCHED


def test_csrf_is_required_on_every_discussion_post():
    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = True
    teacher_client, student_client = app.test_client(), app.test_client()
    with app.app_context():
        db.create_all()
        try:
            teacher, student, group = fx.classroom()
            row = fx.topic(group, teacher)
            gp, tp = group.public_id, row.public_id

            def request(client, method, url, **kwargs):
                # Both clients share this test's app context, so its `g`
                # would otherwise carry one session's Flask-Login user and
                # Flask-WTF token into the other session's request.
                fx.fresh_identity()
                g.pop("csrf_token", None)
                return client.open(url, method=method, **kwargs)

            def login(client, email):
                page = request(client, "GET", "/auth/login")
                request(client, "POST", "/auth/login", data={
                    "email": email, "password": fx.PW, "csrf_token": _csrf(_html(page))})

            login(teacher_client, _email(fx.TEACHER))
            login(student_client, _email(fx.STUDENT))
            new_html = _html(request(teacher_client, "GET", fx.teacher_new(gp)))
            teacher_topic_html = _html(request(teacher_client, "GET", fx.teacher_topic(gp, tp)))
            student_topic_html = _html(request(student_client, "GET", fx.student_topic(gp, tp)))
            attempts = [
                (teacher_client, fx.teacher_new(gp), new_html,
                 {"title": "T", "body": "B",
                  "topic_state": fx.state_from(new_html, "topic_state")}),
                (student_client, fx.student_reply(gp, tp), student_topic_html,
                 {"body": "R", "reply_state": fx.state_from(student_topic_html, "reply_state")}),
                (teacher_client, fx.teacher_reply(gp, tp), teacher_topic_html,
                 {"body": "R", "reply_state": fx.state_from(teacher_topic_html, "reply_state")}),
                (teacher_client, fx.teacher_lock(gp, tp), teacher_topic_html,
                 {"moderation_state": fx.state_from(teacher_topic_html, "moderation_state")}),
            ]
            for client, url, _page, data in attempts:
                assert request(client, "POST", url, data=data).status_code == 400, url
                bad = dict(data, csrf_token="not-a-real-csrf-token")
                assert request(client, "POST", url, data=bad).status_code == 400, url
            assert request(teacher_client, "POST", fx.teacher_reopen(gp, tp), data={
                "moderation_state": "x"}).status_code == 400
            assert fx.counts() == _UNTOUCHED

            # The same submissions with the page's CSRF token succeed, so the
            # 400s above were the CSRF defence and nothing else.
            for client, url, page, data in attempts:
                response = request(client, "POST", url, data=dict(data, csrf_token=_csrf(page)))
                assert response.status_code == 302, url
            assert fx.counts() == {"topics": 2, "replies": 2, "notifications": 1}
            assert db.session.get(DiscussionTopic, row.id).status == fx.LOCKED
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


# ===========================================================================
# Topic creation
# ===========================================================================


def test_a_teacher_creates_an_open_topic_with_normalised_text(app, client):
    with app.app_context():
        teacher, student, group = fx.classroom()
        gp, teacher_id, student_id, group_id = group.public_id, teacher.id, student.id, group.id
    fx.login_as(client, _email(fx.TEACHER))
    page = client.get(fx.teacher_new(gp))
    assert page.status_code == 200 and fx.state_from(_html(page), "topic_state")
    response = fx.create_via_route(client, gp, title="  Our   class   trip  ",
                                   body="\r\n  Where should we go?\r\nWhen? \n")
    assert response.status_code == 302
    tp = fx.location_public_id(response)
    assert response.headers["Location"].endswith(fx.teacher_topic(gp, tp))
    with app.app_context():
        row = DiscussionTopic.query.one()
        assert (row.public_id, row.group_id, row.author_id, row.title, row.body, row.status,
                row.version) == (tp, group_id, teacher_id, "Our class trip",
                                 "Where should we go?\nWhen?", fx.OPEN, 1)
        assert row.created_at == row.updated_at and row.created_at.microsecond == 0
        assert re.fullmatch(r"[0-9a-f]{64}", row.creation_nonce)
        assert [n.recipient_id for n in Notification.query.all()] == [student_id]
    html = _html(client.get(response.headers["Location"]))
    assert "Topic created." in html
    assert _TOPIC_BODY.findall(html) == ["Where should we go?\nWhen?"]
    assert '<span class="badge badge--success">Open</span>' in html
    assert "Our class trip" in _titles(_html(client.get(fx.teacher_list(gp))))


def test_creation_text_errors_re_render_with_the_typed_text_and_the_same_nonce(app, client):
    with app.app_context():
        ids = _standard()
    gp = ids["gp"]
    fx.login_as(client, _email(fx.TEACHER))
    token = fx.page_state(client, fx.teacher_new(gp), "topic_state")
    original = discussion_tokens.load_create_token(token, ids["teacher_public_id"], gp)["nonce"]
    cases = [
        ({"title": "   ", "body": "Kept body"}, "Enter a title."),
        ({"title": "Kept title", "body": " \r\n "}, "Enter the topic text."),
        ({"title": "Bell\x07title", "body": "Kept body"},
         "The title contains characters that cannot be used."),
        ({"title": "Kept title", "body": "Escape\x1bbody"},
         "The topic text contains characters that cannot be used."),
        ({"title": "x" * 151, "body": "Kept body"}, "The title must be at most 150 characters."),
        ({"title": "Kept title", "body": "x" * 5001},
         "The topic text must be at most 5000 characters."),
    ]
    for data, message in cases:
        response = client.post(fx.teacher_new(gp), data=dict(data, topic_state=token))
        html = _html(response)
        assert response.status_code == 200 and message in html, message
        assert "Kept title" in html or "Kept body" in html
        again = fx.state_from(html, "topic_state")
        assert discussion_tokens.load_create_token(
            again, ids["teacher_public_id"], gp)["nonce"] == original
    with app.app_context():
        assert fx.counts() == _UNTOUCHED


def test_state_rotates_after_a_successful_create(app, client):
    with app.app_context():
        ids = _standard()
    gp = ids["gp"]
    fx.login_as(client, _email(fx.TEACHER))
    first = fx.page_state(client, fx.teacher_new(gp), "topic_state")
    assert fx.create_via_route(client, gp, token=first).status_code == 302
    second = fx.page_state(client, fx.teacher_new(gp), "topic_state")
    load = discussion_tokens.load_create_token
    assert load(first, ids["teacher_public_id"], gp)["nonce"] != load(
        second, ids["teacher_public_id"], gp)["nonce"]


def _assert_create_rejected(app, client, gp, token):
    response = client.post(fx.teacher_new(gp), data={
        "title": "Kept title", "body": "Kept body", "topic_state": token})
    html = _html(response)
    assert response.status_code == 200 and _UNVERIFIED in html
    assert "Kept title" in html and "Kept body" in html
    with app.app_context():
        assert fx.counts()["topics"] == 1


def test_missing_tampered_and_expired_creation_tokens_are_rejected(app, client, monkeypatch):
    with app.app_context():
        ids = _standard()
    gp = ids["gp"]
    fx.login_as(client, _email(fx.TEACHER))
    _assert_create_rejected(app, client, gp, "")
    token = fx.page_state(client, fx.teacher_new(gp), "topic_state")
    _assert_create_rejected(app, client, gp, token[:-3] + "abc")
    past = int(time.time()) - discussion_tokens.TOKEN_MAX_AGE_SECONDS - 60
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: past)
    expired = fx.page_state(client, fx.teacher_new(gp), "topic_state")
    monkeypatch.undo()
    _assert_create_rejected(app, client, gp, expired)


def test_cross_user_cross_group_and_cross_purpose_creation_tokens_are_rejected(app, client):
    with app.app_context():
        ids = _standard()
        group = db.session.get(Group, ids["group"])
        fx.assign(group, fx.user("co@example.com", fx.TEACHER))
        other = fx.hierarchy("Other")
        fx.assign(other, db.session.get(User, ids["teacher"]))
        other_gp = other.public_id
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, "co@example.com")
    co_token = fx.page_state(client, fx.teacher_new(gp), "topic_state")
    fx.login_as(client, _email(fx.TEACHER))
    _assert_create_rejected(app, client, gp, co_token)
    _assert_create_rejected(app, client, gp,
                            fx.page_state(client, fx.teacher_new(other_gp), "topic_state"))
    _assert_create_rejected(app, client, gp,
                            fx.page_state(client, fx.teacher_topic(gp, tp), "reply_state"))
    _assert_create_rejected(app, client, gp,
                            fx.page_state(client, fx.teacher_topic(gp, tp), "moderation_state"))


def test_a_duplicate_creation_submission_creates_one_topic_and_one_notification_set(app, client):
    with app.app_context():
        _, _, group = fx.classroom()
        fx.enroll(group, fx.user("second@example.com", fx.STUDENT))
        gp = group.public_id
    fx.login_as(client, _email(fx.TEACHER))
    token = fx.page_state(client, fx.teacher_new(gp), "topic_state")
    first = fx.create_via_route(client, gp, token=token)
    second = fx.create_via_route(client, gp, token=token)
    assert first.status_code == second.status_code == 302
    assert first.headers["Location"] == second.headers["Location"]
    assert "This topic was already created." in _html(client.get(second.headers["Location"]))
    with app.app_context():
        assert fx.counts() == {"topics": 1, "replies": 0, "notifications": 2}


def test_a_replayed_creation_after_losing_access_is_404_not_a_redirect(app, client):
    with app.app_context():
        ids = _standard()
    gp = ids["gp"]
    fx.login_as(client, _email(fx.TEACHER))
    token = fx.page_state(client, fx.teacher_new(gp), "topic_state")
    assert fx.create_via_route(client, gp, token=token).status_code == 302
    with app.app_context():
        _end_access("assignment_removed", ids)
        before = fx.counts()
    assert fx.create_via_route(client, gp, token=token).status_code == 404
    with app.app_context():
        assert fx.counts() == before


# ===========================================================================
# Replies
# ===========================================================================


@pytest.mark.parametrize("role", [fx.TEACHER, fx.STUDENT])
def test_group_members_reply_to_an_open_topic(app, client, role):
    with app.app_context():
        ids = _standard()
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, _email(role))
    response = fx.reply_via_route(client, role, gp, tp, body="  My answer\r\nline two ")
    assert response.status_code == 302
    location = response.headers["Location"]
    assert location.split("#")[0].endswith(fx.topic_url(role, gp, tp))
    with app.app_context():
        reply = DiscussionReply.query.one()
        assert (reply.topic_id, reply.author_id, reply.body) == (
            ids["topic"], ids[role], "My answer\nline two")
        assert reply.created_at.microsecond == 0
        assert location.endswith(f"#reply-{reply.public_id}")
        assert Notification.query.count() == 0
        topic = DiscussionTopic.query.one()
        assert (topic.status, topic.version, topic.updated_at) == (fx.OPEN, 1, fx.NOW)
    html = _html(client.get(location))
    assert "Reply posted." in html
    assert _replies(html) == ["My answer\nline two"]
    label = "Teacher" if role == fx.TEACHER else "Student"
    assert f"({label}, you)" in html


@pytest.mark.parametrize("role", [fx.TEACHER, fx.STUDENT])
def test_reply_body_errors_re_render_with_the_draft_and_the_same_nonce(app, client, role):
    with app.app_context():
        ids = _standard()
    gp, tp = ids["gp"], ids["tp"]
    actor = _actor_public_id(ids, role)
    fx.login_as(client, _email(role))
    token = fx.page_state(client, fx.topic_url(role, gp, tp), "reply_state")
    original = discussion_tokens.load_reply_token(token, actor, gp, tp)["nonce"]
    for body, message in (
        ("  \r\n ", "Enter a reply."),
        ("Keep this draft\x07", "The reply contains characters that cannot be used."),
        ("x" * 5001, "The reply must be at most 5000 characters."),
    ):
        response = client.post(fx.reply_url(role, gp, tp),
                               data={"body": body, "reply_state": token})
        html = _html(response)
        assert response.status_code == 200 and message in html
        again = fx.state_from(html, "reply_state")
        assert discussion_tokens.load_reply_token(again, actor, gp, tp)["nonce"] == original
    assert "Keep this draft" in _html(client.post(
        fx.reply_url(role, gp, tp), data={"body": "Keep this draft\x07", "reply_state": token}))
    with app.app_context():
        assert fx.counts() == _UNTOUCHED


def _assert_reply_rejected(app, client, url, token):
    response = client.post(url, data={"body": "Draft reply", "reply_state": token})
    html = _html(response)
    assert response.status_code == 200 and _UNVERIFIED in html
    assert "Draft reply" in html
    with app.app_context():
        assert DiscussionReply.query.count() == 0


@pytest.mark.parametrize("role", [fx.TEACHER, fx.STUDENT])
def test_missing_tampered_and_expired_reply_tokens_are_rejected(app, client, monkeypatch, role):
    with app.app_context():
        ids = _standard()
    gp, tp = ids["gp"], ids["tp"]
    url = fx.reply_url(role, gp, tp)
    fx.login_as(client, _email(role))
    _assert_reply_rejected(app, client, url, "")
    token = fx.page_state(client, fx.topic_url(role, gp, tp), "reply_state")
    _assert_reply_rejected(app, client, url, "x" + token[1:])
    past = int(time.time()) - discussion_tokens.TOKEN_MAX_AGE_SECONDS - 60
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: past)
    expired = fx.page_state(client, fx.topic_url(role, gp, tp), "reply_state")
    monkeypatch.undo()
    _assert_reply_rejected(app, client, url, expired)


def test_cross_user_cross_topic_cross_group_and_cross_purpose_reply_tokens_are_rejected(
        app, client):
    with app.app_context():
        teacher, student, group_a = fx.classroom("A")
        first = fx.topic(group_a, teacher, title="First")
        second = fx.topic(group_a, teacher, title="Second")
        group_b = fx.hierarchy("B")
        fx.assign(group_b, teacher)
        fx.enroll(group_b, student)
        in_b = fx.topic(group_b, teacher, title="In B")
        ga, gb = group_a.public_id, group_b.public_id
        t1, t2, tb = first.public_id, second.public_id, in_b.public_id
    target = fx.student_reply(ga, t1)
    fx.login_as(client, _email(fx.TEACHER))
    teacher_token = fx.page_state(client, fx.teacher_topic(ga, t1), "reply_state")
    create_token = fx.page_state(client, fx.teacher_new(ga), "topic_state")
    moderation_token = fx.page_state(client, fx.teacher_topic(ga, t1), "moderation_state")
    fx.login_as(client, _email(fx.STUDENT))
    _assert_reply_rejected(app, client, target, teacher_token)
    _assert_reply_rejected(app, client, target,
                           fx.page_state(client, fx.student_topic(ga, t2), "reply_state"))
    _assert_reply_rejected(app, client, target,
                           fx.page_state(client, fx.student_topic(gb, tb), "reply_state"))
    _assert_reply_rejected(app, client, target, create_token)
    _assert_reply_rejected(app, client, target, moderation_token)


@pytest.mark.parametrize("role", [fx.TEACHER, fx.STUDENT])
def test_a_duplicate_reply_submission_appends_one_reply(app, client, role):
    with app.app_context():
        ids = _standard()
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, _email(role))
    token = fx.page_state(client, fx.topic_url(role, gp, tp), "reply_state")
    first = fx.reply_via_route(client, role, gp, tp, body="Once", token=token)
    second = fx.reply_via_route(client, role, gp, tp, body="Once", token=token)
    assert first.status_code == second.status_code == 302
    assert first.headers["Location"] == second.headers["Location"]
    assert "This reply was already posted." in _html(client.get(second.headers["Location"]))
    with app.app_context():
        assert fx.counts() == {"topics": 1, "replies": 1, "notifications": 0}
    assert fx.page_state(client, fx.topic_url(role, gp, tp), "reply_state") != token


@pytest.mark.parametrize("role", [fx.TEACHER, fx.STUDENT])
def test_a_locked_topic_stays_readable_with_an_obvious_state_and_no_reply_form(
        app, client, role):
    with app.app_context():
        ids = _standard()
        row = db.session.get(DiscussionTopic, ids["topic"])
        fx.reply(row, db.session.get(User, ids["student"]), body="Said before the lock")
        fx.set_topic_state(row, fx.LOCKED)
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, _email(role))
    html = _html(client.get(fx.topic_url(role, gp, tp)))
    assert '<span class="badge badge--warning">Locked</span>' in html
    assert "This topic is locked" in html
    assert _TOPIC_BODY.findall(html) == ["What did you read this weekend?"]
    assert _replies(html) == ["Said before the lock"]
    assert 'name="reply_state"' not in html and 'name="body"' not in html
    assert "/reply" not in html
    if role == fx.TEACHER:
        assert "Reopen topic" in html and "Lock topic" not in html
        assert fx.teacher_reopen(gp, tp) in html
    else:
        assert "moderation_state" not in html and "Reopen topic" not in html
    assert '<span class="badge badge--warning">Locked</span>' in _html(
        client.get(fx.list_url(role, gp)))


@pytest.mark.parametrize("role", [fx.TEACHER, fx.STUDENT])
def test_a_reply_form_opened_before_a_lock_is_refused_by_the_post(app, client, role):
    with app.app_context():
        ids = _standard()
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, _email(role))
    token = fx.page_state(client, fx.topic_url(role, gp, tp), "reply_state")
    with app.app_context():
        fx.set_topic_state(db.session.get(DiscussionTopic, ids["topic"]), fx.LOCKED)
    response = fx.reply_via_route(client, role, gp, tp, body="Too late", token=token)
    assert response.status_code == 302
    assert response.headers["Location"].endswith(fx.topic_url(role, gp, tp))
    assert _LOCKED_FLASH in _html(client.get(response.headers["Location"]))
    with app.app_context():
        assert DiscussionReply.query.count() == 0


@pytest.mark.parametrize("role", [fx.TEACHER, fx.STUDENT])
def test_a_reply_form_opened_before_a_lock_and_reopen_is_stale(app, client, role):
    with app.app_context():
        ids = _standard()
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, _email(role))
    token = fx.page_state(client, fx.topic_url(role, gp, tp), "reply_state")
    with app.app_context():
        row = db.session.get(DiscussionTopic, ids["topic"])
        fx.set_topic_state(row, fx.LOCKED)
        fx.set_topic_state(row, fx.OPEN)
    response = fx.reply_via_route(client, role, gp, tp, body="Still relevant?", token=token)
    html = _html(response)
    assert response.status_code == 200 and _CHANGED in html
    assert "Still relevant?" in html
    fresh = fx.state_from(html, "reply_state")
    payload = discussion_tokens.load_reply_token(fresh, _actor_public_id(ids, role), gp, tp)
    assert payload["topic_version"] == 3
    with app.app_context():
        assert DiscussionReply.query.count() == 0
    assert fx.reply_via_route(client, role, gp, tp, token=fresh).status_code == 302
    with app.app_context():
        assert DiscussionReply.query.count() == 1


def test_a_reply_posted_just_before_a_lock_replays_to_that_reply(app, client):
    with app.app_context():
        ids = _standard()
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, _email(fx.STUDENT))
    token = fx.page_state(client, fx.student_topic(gp, tp), "reply_state")
    first = fx.reply_via_route(client, fx.STUDENT, gp, tp, token=token)
    with app.app_context():
        fx.set_topic_state(db.session.get(DiscussionTopic, ids["topic"]), fx.LOCKED)
    again = fx.reply_via_route(client, fx.STUDENT, gp, tp, token=token)
    assert again.status_code == 302
    assert again.headers["Location"] == first.headers["Location"]
    assert "This reply was already posted." in _html(client.get(again.headers["Location"]))
    with app.app_context():
        assert DiscussionReply.query.count() == 1


def test_a_new_reply_redirects_to_the_page_that_shows_it(app, client):
    with app.app_context():
        ids = _standard()
        row = db.session.get(DiscussionTopic, ids["topic"])
        teacher = db.session.get(User, ids["teacher"])
        for i in range(50):
            fx.reply(row, teacher, body=f"Earlier {i}", created_at=fx.NOW)
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, _email(fx.STUDENT))
    response = fx.reply_via_route(client, fx.STUDENT, gp, tp, body="Newest")
    location = response.headers["Location"]
    assert "?page=2#reply-" in location
    assert _replies(_html(client.get(location))) == ["Newest"]


# ===========================================================================
# Lock and reopen
# ===========================================================================


def test_a_teacher_locks_and_reopens_a_topic(app, client):
    with app.app_context():
        ids = _standard()
        fx.reply(db.session.get(DiscussionTopic, ids["topic"]),
                 db.session.get(User, ids["student"]))
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, _email(fx.TEACHER))
    assert "Lock topic" in _html(client.get(fx.teacher_topic(gp, tp)))
    response = fx.moderate_via_route(client, gp, tp, "lock")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(fx.teacher_topic(gp, tp))
    with app.app_context():
        row = DiscussionTopic.query.one()
        assert (row.status, row.version) == (fx.LOCKED, 2)
        assert row.updated_at > row.created_at and row.updated_at.microsecond == 0
    html = _html(client.get(response.headers["Location"]))
    assert "Topic locked." in html and "Reopen topic" in html
    assert 'name="reply_state"' not in html
    response = fx.moderate_via_route(client, gp, tp, "reopen")
    assert response.status_code == 302
    with app.app_context():
        row = DiscussionTopic.query.one()
        assert (row.status, row.version) == (fx.OPEN, 3)
        assert fx.counts() == {"topics": 1, "replies": 1, "notifications": 0}
    html = _html(client.get(fx.teacher_topic(gp, tp)))
    assert "Topic reopened." in html and "Lock topic" in html and 'name="reply_state"' in html
    fx.login_as(client, _email(fx.STUDENT))
    student_html = _html(client.get(fx.student_topic(gp, tp)))
    assert 'name="reply_state"' in student_html and "moderation_state" not in student_html


def test_every_assigned_teacher_moderates_equally(app, client):
    with app.app_context():
        ids = _standard()
        fx.assign(db.session.get(Group, ids["group"]), fx.user("co@example.com", fx.TEACHER))
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, "co@example.com")
    assert fx.moderate_via_route(client, gp, tp, "lock").status_code == 302
    fx.login_as(client, _email(fx.TEACHER))
    assert fx.moderate_via_route(client, gp, tp, "reopen").status_code == 302
    with app.app_context():
        row = DiscussionTopic.query.one()
        assert (row.status, row.version) == (fx.OPEN, 3)


def test_a_double_submitted_lock_is_an_authorized_no_op(app, client):
    with app.app_context():
        ids = _standard()
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, _email(fx.TEACHER))
    token = fx.page_state(client, fx.teacher_topic(gp, tp), "moderation_state")
    assert fx.moderate_via_route(client, gp, tp, "lock", token=token).status_code == 302
    second = fx.moderate_via_route(client, gp, tp, "lock", token=token)
    assert second.status_code == 302
    assert "This topic is already locked. Nothing was changed." in _html(
        client.get(second.headers["Location"]))
    with app.app_context():
        row = DiscussionTopic.query.one()
        assert (row.status, row.version) == (fx.LOCKED, 2)


def test_a_stale_moderation_form_changes_nothing(app, client):
    with app.app_context():
        ids = _standard()
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, _email(fx.TEACHER))
    token = fx.page_state(client, fx.teacher_topic(gp, tp), "moderation_state")
    with app.app_context():
        row = db.session.get(DiscussionTopic, ids["topic"])
        fx.set_topic_state(row, fx.LOCKED)
        fx.set_topic_state(row, fx.OPEN)
    response = fx.moderate_via_route(client, gp, tp, "lock", token=token)
    assert response.status_code == 302
    assert _MODERATION_STALE in _html(client.get(response.headers["Location"]))
    with app.app_context():
        row = DiscussionTopic.query.one()
        assert (row.status, row.version) == (fx.OPEN, 3)


def test_moderation_tokens_are_bound_to_action_teacher_topic_and_expiry(app, client,
                                                                        monkeypatch):
    with app.app_context():
        ids = _standard()
        group = db.session.get(Group, ids["group"])
        fx.assign(group, fx.user("co@example.com", fx.TEACHER))
        second = fx.topic(group, db.session.get(User, ids["teacher"]), title="Second").public_id
    gp, tp = ids["gp"], ids["tp"]

    def rejected(url, token):
        response = client.post(url, data={"moderation_state": token})
        assert response.status_code == 302
        assert _MODERATION_UNVERIFIED in _html(client.get(response.headers["Location"]))

    fx.login_as(client, _email(fx.TEACHER))
    lock_token = fx.page_state(client, fx.teacher_topic(gp, tp), "moderation_state")
    rejected(fx.teacher_reopen(gp, tp), lock_token)
    rejected(fx.teacher_lock(gp, tp),
             fx.page_state(client, fx.teacher_topic(gp, second), "moderation_state"))
    rejected(fx.teacher_lock(gp, tp), "")
    rejected(fx.teacher_lock(gp, tp),
             fx.page_state(client, fx.teacher_topic(gp, tp), "reply_state"))
    past = int(time.time()) - discussion_tokens.TOKEN_MAX_AGE_SECONDS - 60
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: past)
    expired = fx.page_state(client, fx.teacher_topic(gp, tp), "moderation_state")
    monkeypatch.undo()
    rejected(fx.teacher_lock(gp, tp), expired)
    fx.login_as(client, "co@example.com")
    rejected(fx.teacher_lock(gp, tp), lock_token)
    with app.app_context():
        assert {(row.status, row.version) for row in DiscussionTopic.query.all()} == {
            (fx.OPEN, 1)}


# ===========================================================================
# Ordering, pagination and bounded queries
# ===========================================================================


@pytest.mark.parametrize("role", [fx.TEACHER, fx.STUDENT])
def test_topic_lists_page_twenty_newest_first_with_deterministic_ties(app, client, role):
    with app.app_context():
        teacher, _, group = fx.classroom()
        records = []
        for i in range(21):
            created = fx.NOW + timedelta(minutes=i // 3)
            row = fx.topic(group, teacher, title=f"Topic {i:02d}", created_at=created)
            records.append((created, row.id, row.title))
        expected = [title for _, _, title in sorted(records, reverse=True)]
        gp = group.public_id
    fx.login_as(client, _email(role))
    url = fx.list_url(role, gp)
    first = _html(client.get(url))
    assert _titles(first) == expected[:20]
    assert "Older topics" in first and "Newer topics" not in first
    second = _html(client.get(url + "?page=2"))
    assert _titles(second) == expected[20:]
    assert "Newer topics" in second and "Older topics" not in second
    for bad in ("abc", "0", "-1", "3", "99999", "1.5", ""):
        assert _titles(_html(client.get(f"{url}?page={bad}"))) == expected[:20], bad


@pytest.mark.parametrize("role", [fx.TEACHER, fx.STUDENT])
def test_the_reply_timeline_pages_fifty_in_reading_order_with_deterministic_ties(
        app, client, role):
    with app.app_context():
        ids = _standard()
        row = db.session.get(DiscussionTopic, ids["topic"])
        teacher = db.session.get(User, ids["teacher"])
        student = db.session.get(User, ids["student"])
        records = []
        for position, i in enumerate(sorted(range(51), key=lambda n: (n * 7) % 51)):
            created = fx.NOW + timedelta(minutes=i // 4)
            fx.reply(row, teacher if i % 2 else student, body=f"R{i:02d}", created_at=created)
            records.append((created, position, f"R{i:02d}"))
        expected = [body for _, _, body in sorted(records)]
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, _email(role))
    url = fx.topic_url(role, gp, tp)
    first = _html(client.get(url))
    assert _replies(first) == expected[:50]
    assert "Later replies" in first and "Earlier replies" not in first
    second = _html(client.get(url + "?page=2"))
    assert _replies(second) == expected[50:]
    assert "Earlier replies" in second and "Later replies" not in second
    for bad in ("abc", "0", "-5", "3", "99999"):
        assert _replies(_html(client.get(f"{url}?page={bad}"))) == expected[:50], bad


def _select_count(client, url):
    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", _rec)
    try:
        assert client.get(url).status_code == 200, url
    finally:
        event.remove(db.engine, "before_cursor_execute", _rec)
    return len([s for s in statements if s.lstrip().upper().startswith("SELECT")])


def test_query_counts_do_not_grow_with_topics_replies_or_groups(app, client):
    with app.app_context():
        ids = _standard()
        fx.reply(db.session.get(DiscussionTopic, ids["topic"]),
                 db.session.get(User, ids["student"]))
    gp, tp = ids["gp"], ids["tp"]
    pages = {
        fx.TEACHER: [fx.TEACHER_OVERVIEW, fx.teacher_list(gp), fx.teacher_topic(gp, tp)],
        fx.STUDENT: [fx.STUDENT_OVERVIEW, fx.student_list(gp), fx.student_topic(gp, tp)],
    }
    small = {}
    for role, urls in pages.items():
        fx.login_as(client, _email(role))
        small.update({url: _select_count(client, url) for url in urls})
    with app.app_context():
        group = db.session.get(Group, ids["group"])
        teacher = db.session.get(User, ids["teacher"])
        student = db.session.get(User, ids["student"])
        row = db.session.get(DiscussionTopic, ids["topic"])
        for i in range(30):
            extra = fx.topic(group, teacher, title=f"Extra {i}", created_at=fx.LATER)
            fx.reply(extra, student, body=f"Extra reply {i}")
        for i in range(70):
            fx.reply(row, teacher if i % 2 else student, body=f"More {i}")
        for i in range(6):
            more = fx.hierarchy(f"More{i}")
            fx.assign(more, teacher)
            fx.enroll(more, student)
            fx.topic(more, teacher)
    large = {}
    for role, urls in pages.items():
        fx.login_as(client, _email(role))
        large.update({url: _select_count(client, url) for url in urls})
    assert large == small


def test_get_requests_never_write(app, client):
    with app.app_context():
        ids = _standard()
        fx.reply(db.session.get(DiscussionTopic, ids["topic"]),
                 db.session.get(User, ids["student"]))
    gp, tp = ids["gp"], ids["tp"]
    statements = []

    def _rec(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    for role, urls in ((fx.TEACHER, _teacher_gets(gp, tp) + [fx.teacher_topic(gp, tp) + "?page=4",
                                                             "/teacher/dashboard"]),
                       (fx.STUDENT, _student_gets(gp, tp) + [fx.student_list(gp) + "?page=9",
                                                             "/student/dashboard"])):
        fx.login_as(client, _email(role))
        event.listen(db.engine, "before_cursor_execute", _rec)
        try:
            for url in urls:
                assert client.get(url).status_code == 200, url
        finally:
            event.remove(db.engine, "before_cursor_execute", _rec)
    writes = [s for s in statements
              if s.strip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
    assert writes == []
    with app.app_context():
        row = DiscussionTopic.query.one()
        assert (row.status, row.version, row.updated_at) == (fx.OPEN, 1, fx.NOW)


# ===========================================================================
# Rendering safety and privacy
# ===========================================================================


def test_hostile_text_is_escaped_everywhere(app, client):
    with app.app_context():
        group = fx.hierarchy("<i>G</i>")
        teacher = fx.user("teacher@example.com", fx.TEACHER, name="<b>Bold</b> Teacher")
        student = fx.user("student@example.com", fx.STUDENT, name="<u>Evil</u> Student")
        fx.assign(group, teacher)
        fx.enroll(group, student)
        gp = group.public_id
    fx.login_as(client, _email(fx.TEACHER))
    response = fx.create_via_route(client, gp, title="<script>alert('t')</script>",
                                   body="<img src=x onerror=alert(1)>\nSecond <b>line</b>")
    tp = fx.location_public_id(response)
    assert fx.reply_via_route(client, fx.TEACHER, gp, tp,
                              body="<svg onload=alert(2)>").status_code == 302
    pages = [_html(client.get(url)) for url in (
        fx.TEACHER_OVERVIEW, fx.teacher_list(gp), fx.teacher_topic(gp, tp), "/teacher/dashboard")]
    fx.login_as(client, _email(fx.STUDENT))
    assert fx.reply_via_route(client, fx.STUDENT, gp, tp,
                              body="</div><script>x</script>").status_code == 302
    student_pages = [_html(client.get(url)) for url in (
        fx.STUDENT_OVERVIEW, fx.student_list(gp), fx.student_topic(gp, tp), "/student/dashboard",
        "/notifications")]
    for html in pages + student_pages:
        for raw in ("<script>alert", "<img src=x", "<svg onload", "<b>Bold</b>", "<u>Evil</u>",
                    "<i>G</i>", "</div><script>", "<b>line</b>"):
            assert raw not in html, raw
    topic_html = student_pages[2]
    assert "&lt;script&gt;alert(&#39;t&#39;)&lt;/script&gt;" in topic_html
    assert _TOPIC_BODY.findall(topic_html) == [
        "&lt;img src=x onerror=alert(1)&gt;\nSecond &lt;b&gt;line&lt;/b&gt;"]
    assert _replies(topic_html) == [
        "&lt;svg onload=alert(2)&gt;", "&lt;/div&gt;&lt;script&gt;x&lt;/script&gt;"]
    assert "&lt;script&gt;alert(&#39;t&#39;)&lt;/script&gt;" in student_pages[4]


def test_multiline_plain_text_keeps_its_line_breaks(app, client):
    with app.app_context():
        teacher, student, group = fx.classroom()
        row = fx.topic(group, teacher, body="First line\n\n\tIndented third")
        fx.reply(row, student, body="A\n\n  B")
        gp, tp = group.public_id, row.public_id
    fx.login_as(client, _email(fx.STUDENT))
    html = _html(client.get(fx.student_topic(gp, tp)))
    assert _TOPIC_BODY.findall(html) == ["First line\n\n\tIndented third"]
    assert _replies(html) == ["A\n\n  B"]


def test_no_internal_id_email_or_password_hash_reaches_the_markup(app, client):
    with app.app_context():
        for i in range(12):
            fx.user(f"filler{i}@example.com", fx.ADMIN)
        filler_teacher = fx.user("filler-teacher@example.com", fx.TEACHER)
        filler_student = fx.user("filler-student@example.com", fx.STUDENT)
        for i in range(12):
            filler_group = fx.hierarchy(f"F{i}")
            fx.assign(filler_group, filler_teacher)
            fx.enroll(filler_group, filler_student)
            fx.reply(fx.topic(filler_group, filler_teacher, title="Filler"), filler_teacher)
        teacher, student, group = fx.classroom()
        row = fx.topic(group, teacher)
        reply = fx.reply(row, student)
        term, level, course = fx.ancestors(group)
        ids = {teacher.id, student.id, group.id, row.id, reply.id, term.id, level.id, course.id,
               fx.enrollment_of(group, student).id, fx.assignment_of(group, teacher).id}
        assert min(ids) > 11
        secrets = {teacher.password_hash, student.password_hash, row.creation_nonce,
                   reply.creation_nonce}
        gp, tp = group.public_id, row.public_id
    fx.login_as(client, _email(fx.TEACHER))
    pages = [_html(client.get(url)) for url in _teacher_gets(gp, tp) + ["/teacher/dashboard"]]
    fx.login_as(client, _email(fx.STUDENT))
    pages += [_html(client.get(url)) for url in _student_gets(gp, tp) + [
        "/student/dashboard", "/notifications"]]
    for html in pages:
        assert "@example.com" not in html
        for secret in secrets:
            assert secret not in html
        for value in re.findall(r'(?:href|value|action|id)="([^"]*)"', html):
            segments = re.split(r"[/?=&#]", value)
            for internal in ids:
                assert str(internal) not in segments, value


def test_every_discussion_page_carries_the_private_headers(app, client):
    with app.app_context():
        ids = _standard()
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, _email(fx.TEACHER))
    responses = [client.get(url) for url in _teacher_gets(gp, tp)]
    responses.append(fx.create_via_route(client, gp, token=""))
    responses.append(fx.reply_via_route(client, fx.TEACHER, gp, tp, token=""))
    fx.login_as(client, _email(fx.STUDENT))
    responses += [client.get(url) for url in _student_gets(gp, tp)]
    responses.append(fx.reply_via_route(client, fx.STUDENT, gp, tp, token=""))
    assert len(responses) == 10
    for response in responses:
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "private, no-store"
        assert "Cookie" in response.headers.get("Vary", "")


# ===========================================================================
# Navigation
# ===========================================================================


_STUDENT_NAV = [
    "/student/dashboard",
    "/student/assignments",
    "/student/quizzes",
    "/student/listening",
    "/student/speaking",
    "/student/attendance",
    "/student/grades",
    "/student/announcements",
    "/student/calendar",
    "/student/search",
    "/messages",
    "/student/discussions",
    "/notifications",
]

_TEACHER_DASHBOARD_NAV = [
    "/teacher/dashboard",
    "/teacher/calendar",
    "/teacher/announcements",
    "/teacher/discussions",
    "/messages",
    "/notifications",
]

_TEACHER_DISCUSSION_NAV = ["/teacher/dashboard", "/teacher/discussions", "/messages",
                           "/notifications"]


def test_the_student_navigation_names_discussions_right_after_messages(app, client):
    with app.app_context():
        ids = _standard()
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, _email(fx.STUDENT))
    expected_active = {
        "/student/dashboard": ["/student/dashboard"],
        "/student/calendar": ["/student/calendar"],
        "/messages": ["/messages"],
        "/notifications": ["/notifications"],
        fx.STUDENT_OVERVIEW: ["/student/discussions"],
        fx.student_list(gp): ["/student/discussions"],
        fx.student_topic(gp, tp): ["/student/discussions"],
    }
    for url, active in expected_active.items():
        html = _html(client.get(url))
        assert _nav_hrefs(html) == _STUDENT_NAV, url
        assert _active(html) == active, url
        assert _portal_nav(html).count(">Discussions</a>") == 1, url


def test_the_teacher_dashboard_names_discussions_right_after_announcements(app, client):
    with app.app_context():
        ids = _standard()
    gp, tp = ids["gp"], ids["tp"]
    fx.login_as(client, _email(fx.TEACHER))
    dashboard = _html(client.get("/teacher/dashboard"))
    assert _nav_hrefs(dashboard) == _TEACHER_DASHBOARD_NAV
    assert _active(dashboard) == ["/teacher/dashboard"]
    for url in (fx.TEACHER_OVERVIEW, fx.teacher_list(gp), fx.teacher_new(gp),
                fx.teacher_topic(gp, tp)):
        html = _html(client.get(url))
        assert _nav_hrefs(html) == _TEACHER_DISCUSSION_NAV, url
        assert _active(html) == ["/teacher/discussions"], url
    for url in ("/teacher/dashboard", "/teacher/calendar", "/messages", "/notifications"):
        assert "/student/discussions" not in _html(client.get(url)), url


def test_group_cards_offer_discussions_only_where_the_page_can_open(app, client):
    with app.app_context():
        teacher, student, live = fx.classroom("Live")
        past = fx.hierarchy("Past", group_status=fx.ARCHIVED)
        fx.assign(past, teacher)
        fx.enroll(past, student)
        live_gp, past_gp = live.public_id, past.public_id
    fx.login_as(client, _email(fx.TEACHER))
    html = _html(client.get("/teacher/dashboard"))
    assert f'href="{fx.teacher_list(live_gp)}">Discussions</a>' in html
    assert fx.teacher_list(past_gp) not in html
    assert client.get(fx.teacher_list(past_gp)).status_code == 404
    fx.login_as(client, _email(fx.STUDENT))
    html = _html(client.get("/student/dashboard"))
    assert f'href="{fx.student_list(live_gp)}">Discussions</a>' in html
    assert fx.student_list(past_gp) not in html
    assert client.get(fx.student_list(past_gp)).status_code == 404


@pytest.mark.parametrize("role, expected", [(fx.STUDENT, 1), (fx.TEACHER, 0), (fx.ADMIN, 0),
                                            (fx.RESEARCHER, 0)])
def test_the_portal_header_offers_student_discussions_only_to_students(app, role, expected):
    with app.app_context():
        user_id = fx.user("someone@example.com", role).id
    with app.test_request_context("/"):
        login_user(db.session.get(User, user_id))
        html = render_template("layouts/portal_base.html")
    assert html.count('href="/student/discussions"') == expected
    assert "portal-nav__link--active" not in html


def test_the_administrator_dashboard_gains_no_discussions_link(app, client):
    with app.app_context():
        fx.user("admin@example.com", fx.ADMIN)
    fx.login_as(client, "admin@example.com")
    response = client.get("/admin/dashboard")
    assert response.status_code == 200
    assert "/discussions" not in _html(response)
