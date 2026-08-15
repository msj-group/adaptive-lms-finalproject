from html.parser import HTMLParser

from app.extensions import db
from app.models import User, UserRole, UserStatus
from tests.conftest import login, make_user

AJAX_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


class _ElementCollector(HTMLParser):
    """Collects every start tag with its attributes (stdlib-only, no new
    project dependency) so tests can assert on a specific element's
    attributes instead of scanning raw HTML text for substrings, which can
    produce false positives (e.g. matching inside an unrelated attribute
    or a comment).
    """

    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_startendtag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def _parse_elements(html):
    parser = _ElementCollector()
    parser.feed(html)
    return parser.elements


def _find_one(elements, attr_name):
    matches = [(tag, attrs) for tag, attrs in elements if attr_name in attrs]
    assert len(matches) == 1, f"expected exactly one element with [{attr_name}], found {len(matches)}"
    return matches[0]


def _make_admin():
    return make_user("admin@example.com", UserRole.ADMINISTRATOR.value)


def _make_student(email, full_name, status=UserStatus.ACTIVE.value):
    return make_user(email, UserRole.STUDENT.value, status=status, full_name=full_name)


def _seed_students(app):
    with app.app_context():
        _make_admin()
        _make_student("alice@example.com", "Alice Wonderland", UserStatus.ACTIVE.value)
        _make_student("bob@example.com", "Bob Marley", UserStatus.SUSPENDED.value)
        _make_student("carol@example.com", "Carol Danvers", UserStatus.ACTIVE.value)


# ======================================================================
# PARTIAL RESPONSE SHAPE
# ======================================================================


def test_ajax_request_returns_fragment_not_full_page(app, client):
    _seed_students(app)
    login(client, "admin@example.com")

    resp = client.get("/admin/students", headers=AJAX_HEADERS)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "<html" not in html.lower()
    assert "admin-shell" not in html
    assert "Alice Wonderland" in html


def test_normal_request_returns_full_page(app, client):
    _seed_students(app)
    login(client, "admin@example.com")

    resp = client.get("/admin/students")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "<html" in html.lower()
    assert "admin-shell" in html
    assert 'id="student-results"' in html

    elements = _parse_elements(html)
    script_tags = [
        attrs for tag, attrs in elements
        if tag == "script" and attrs.get("src") == "/static/js/admin_live_search.js"
    ]
    assert len(script_tags) == 1, "expected exactly one <script src='.../admin_live_search.js'>"
    assert "defer" in script_tags[0]


def test_normal_request_includes_filter_form_and_status_region(app, client):
    _seed_students(app)
    login(client, "admin@example.com")

    resp = client.get("/admin/students")
    html = resp.get_data(as_text=True)
    assert 'id="student-filter-form"' in html
    assert 'role="status"' in html
    assert 'aria-live="polite"' in html


def test_page_provides_required_live_search_configuration(app, client):
    """The shared script hard-codes nothing resource-specific, so the page
    itself must supply every piece of the configuration contract -- checked
    against the actual parsed elements and their attributes, not raw
    substrings of the response body.
    """
    _seed_students(app)
    login(client, "admin@example.com")

    resp = client.get("/admin/students")
    html = resp.get_data(as_text=True)
    elements = _parse_elements(html)

    _, root_attrs = _find_one(elements, "data-live-search")
    assert root_attrs.get("data-list-url") == "/admin/students"
    assert root_attrs.get("data-singular") == "student"
    assert root_attrs.get("data-plural") == "students"
    assert root_attrs.get("data-status-form-selector") == ".student-status-form[data-confirm]"

    form_tag, _ = _find_one(elements, "data-live-search-form")
    assert form_tag == "form"

    input_tag, input_attrs = _find_one(elements, "data-live-search-input")
    assert input_tag == "input"
    assert input_attrs.get("type") == "search"

    select_tag, _ = _find_one(elements, "data-live-search-status")
    assert select_tag == "select"

    results_tag, results_attrs = _find_one(elements, "data-live-search-results")
    assert results_tag == "div"
    assert results_attrs.get("id") == "student-results"

    status_region_tag, status_region_attrs = _find_one(elements, "data-live-search-status-region")
    assert status_region_tag == "div"
    assert status_region_attrs.get("role") == "status"
    assert status_region_attrs.get("aria-live") == "polite"


# ======================================================================
# FILTERING BEHAVIOUR (same for both AJAX and normal requests)
# ======================================================================


def test_name_search(app, client):
    _seed_students(app)
    login(client, "admin@example.com")

    resp = client.get("/admin/students?q=Alice", headers=AJAX_HEADERS)
    html = resp.get_data(as_text=True)
    assert "Alice Wonderland" in html
    assert "Bob Marley" not in html
    assert "Carol Danvers" not in html


def test_email_search(app, client):
    _seed_students(app)
    login(client, "admin@example.com")

    resp = client.get("/admin/students?q=bob@example.com", headers=AJAX_HEADERS)
    html = resp.get_data(as_text=True)
    assert "Bob Marley" in html
    assert "Alice Wonderland" not in html


def test_status_filtering(app, client):
    _seed_students(app)
    login(client, "admin@example.com")

    resp = client.get("/admin/students?status=suspended", headers=AJAX_HEADERS)
    html = resp.get_data(as_text=True)
    assert "Bob Marley" in html
    assert "Alice Wonderland" not in html
    assert "Carol Danvers" not in html


def test_combined_search_and_status_filtering(app, client):
    _seed_students(app)
    login(client, "admin@example.com")

    # Alice is active; searching "Alice" while filtering to suspended must
    # exclude her even though the name matches.
    resp = client.get("/admin/students?q=Alice&status=suspended", headers=AJAX_HEADERS)
    html = resp.get_data(as_text=True)
    assert "Alice Wonderland" not in html
    assert "No students match your filters" in html

    resp2 = client.get("/admin/students?q=Bob&status=suspended", headers=AJAX_HEADERS)
    html2 = resp2.get_data(as_text=True)
    assert "Bob Marley" in html2


def test_empty_query_returns_all_students(app, client):
    _seed_students(app)
    login(client, "admin@example.com")

    resp = client.get("/admin/students?q=", headers=AJAX_HEADERS)
    html = resp.get_data(as_text=True)
    assert "Alice Wonderland" in html
    assert "Bob Marley" in html
    assert "Carol Danvers" in html


def test_no_results_state(app, client):
    _seed_students(app)
    login(client, "admin@example.com")

    resp = client.get("/admin/students?q=zzz-no-such-student", headers=AJAX_HEADERS)
    html = resp.get_data(as_text=True)
    assert "No students match your filters" in html
    assert "Alice Wonderland" not in html


def test_invalid_status_handling(app, client):
    _seed_students(app)
    login(client, "admin@example.com")

    resp = client.get("/admin/students?status=not-a-real-status", headers=AJAX_HEADERS)
    html = resp.get_data(as_text=True)
    # Same STEP-18 behaviour: an invalid status is ignored, not treated as a
    # real (empty) filter -- all students remain visible.
    assert resp.status_code == 200
    assert "Alice Wonderland" in html
    assert "Bob Marley" in html
    assert "Carol Danvers" in html


# ======================================================================
# AUTHORIZATION
# ======================================================================


def test_administrator_only_access_ajax(app, client):
    with app.app_context():
        make_user("teacher@example.com", UserRole.TEACHER.value)
    login(client, "teacher@example.com")

    resp = client.get("/admin/students", headers=AJAX_HEADERS)
    assert resp.status_code == 403


def test_anonymous_denied_ajax(app, client):
    resp = client.get("/admin/students", headers=AJAX_HEADERS)
    assert resp.status_code in (302, 401)


def test_student_role_scoping_not_affected_by_search(app, client):
    with app.app_context():
        _make_admin()
        make_user("teacher.smith@example.com", UserRole.TEACHER.value, full_name="Teacher Smith")
        _make_student("student.smith@example.com", "Student Smith")
    login(client, "admin@example.com")

    # Searching "Smith" must only ever surface the student, never the
    # teacher, even though both names match.
    resp = client.get("/admin/students?q=Smith", headers=AJAX_HEADERS)
    html = resp.get_data(as_text=True)
    assert "Student Smith" in html
    assert "Teacher Smith" not in html


# ======================================================================
# ESCAPING
# ======================================================================


def test_html_in_name_is_escaped_in_live_results(app, client):
    with app.app_context():
        _make_admin()
        _make_student("evil@example.com", "<script>alert(1)</script>")
    login(client, "admin@example.com")

    # "<script" is the literal beginning of the name, so it is still a valid
    # prefix match under the new matching rules.
    resp = client.get("/admin/students?q=%3Cscript", headers=AJAX_HEADERS)
    html = resp.get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_quotes_in_name_do_not_break_confirm_attribute(app, client):
    with app.app_context():
        _make_admin()
        _make_student("quote@example.com", 'Weird "Quoted" Name')
    login(client, "admin@example.com")

    # "Weird" is the first word of the name, so it is a valid prefix match;
    # the goal here is just to confirm the quote inside the name renders
    # safely in the data-confirm attribute once this student is returned.
    resp = client.get("/admin/students?q=Weird", headers=AJAX_HEADERS)
    html = resp.get_data(as_text=True)
    assert "data-confirm=" in html
    assert '&#34;Quoted&#34;' in html or "&#34;" in html


# ======================================================================
# EXISTING STUDENT ACTIONS PRESERVED IN LIVE RESULTS
# ======================================================================


def test_live_results_preserve_detail_edit_and_status_actions(app, client):
    with app.app_context():
        _make_admin()
        student = _make_student("target@example.com", "Target Student")
        public_id = student.public_id
    login(client, "admin@example.com")

    resp = client.get("/admin/students?q=Target", headers=AJAX_HEADERS)
    html = resp.get_data(as_text=True)

    assert f"/admin/students/{public_id}" in html  # detail link
    assert f"/admin/students/{public_id}/edit" in html  # edit link
    assert f"/admin/students/{public_id}/toggle-status" in html  # suspend/reactivate form
    assert "csrf_token" in html
    assert "Suspend" in html
    assert "signed out of any active session" in html  # confirmation copy


def test_live_results_show_reactivate_for_suspended_student(app, client):
    with app.app_context():
        _make_admin()
        _make_student("suspended.target@example.com", "Suspended Target", UserStatus.SUSPENDED.value)
    login(client, "admin@example.com")

    resp = client.get("/admin/students?status=suspended", headers=AJAX_HEADERS)
    html = resp.get_data(as_text=True)
    assert "Reactivate" in html
    assert "data-confirm" not in html  # reactivate needs no confirmation


def test_suspend_action_still_works_after_live_filtered_response(app, client):
    with app.app_context():
        _make_admin()
        student = _make_student("suspend.me@example.com", "Suspend Me")
        public_id = student.public_id
    login(client, "admin@example.com")

    # Simulate the admin having just seen this student via a live search...
    resp = client.get("/admin/students?q=Suspend", headers=AJAX_HEADERS)
    assert "Suspend Me" in resp.get_data(as_text=True)

    # ...then actually suspending them via the same form action shown there.
    resp2 = client.post(f"/admin/students/{public_id}/toggle-status", follow_redirects=True)
    assert resp2.status_code == 200
    assert b"is now suspended" in resp2.data


# ======================================================================
# PREFIX MATCHING (corrected search rules)
# ======================================================================


def _seed_prefix_target(app):
    with app.app_context():
        _make_admin()
        _make_student("aj@gmail.com", "abdulalgadeer Joha")
        _make_student("someone.else@example.com", "Someone Else")


def test_prefix_matches_start_of_full_name(app, client):
    _seed_prefix_target(app)
    login(client, "admin@example.com")

    resp = client.get("/admin/students?q=abd", headers=AJAX_HEADERS)
    html = resp.get_data(as_text=True)
    assert "abdulalgadeer Joha" in html
    assert "Someone Else" not in html


def test_prefix_matches_start_of_second_word(app, client):
    _seed_prefix_target(app)
    login(client, "admin@example.com")

    resp = client.get("/admin/students?q=joh", headers=AJAX_HEADERS)
    html = resp.get_data(as_text=True)
    assert "abdulalgadeer Joha" in html
    assert "Someone Else" not in html


def test_prefix_matches_start_of_email(app, client):
    _seed_prefix_target(app)
    login(client, "admin@example.com")

    resp = client.get("/admin/students?q=aj%40", headers=AJAX_HEADERS)  # "aj@"
    html = resp.get_data(as_text=True)
    assert "abdulalgadeer Joha" in html
    assert "Someone Else" not in html


def test_mid_word_substring_does_not_match(app, client):
    _seed_prefix_target(app)
    login(client, "admin@example.com")

    # "ade" only occurs mid-word inside "gadeer" -- must NOT match, unlike
    # the old plain substring search.
    resp = client.get("/admin/students?q=ade", headers=AJAX_HEADERS)
    html = resp.get_data(as_text=True)
    assert "abdulalgadeer Joha" not in html
    assert "No students match your filters" in html


def test_prefix_matching_is_case_insensitive(app, client):
    _seed_prefix_target(app)
    login(client, "admin@example.com")

    resp = client.get("/admin/students?q=ABD", headers=AJAX_HEADERS)
    html = resp.get_data(as_text=True)
    assert "abdulalgadeer Joha" in html


def test_empty_query_after_prefix_change_shows_all_students(app, client):
    _seed_prefix_target(app)
    login(client, "admin@example.com")

    resp = client.get("/admin/students?q=", headers=AJAX_HEADERS)
    html = resp.get_data(as_text=True)
    assert "abdulalgadeer Joha" in html
    assert "Someone Else" in html


def test_percent_sign_is_treated_as_literal_character(app, client):
    with app.app_context():
        _make_admin()
        _make_student("promo@example.com", "50% Off Deal")
        _make_student("other@example.com", "Regular Student")
    login(client, "admin@example.com")

    resp = client.get("/admin/students?q=50%25", headers=AJAX_HEADERS)  # literal "50%"
    html = resp.get_data(as_text=True)
    assert "50% Off Deal" in html
    assert "Regular Student" not in html


def test_underscore_is_treated_as_literal_character(app, client):
    with app.app_context():
        _make_admin()
        _make_student("underscore@example.com", "Off_Deal Student")
        _make_student("other2@example.com", "OffXDeal Student")
    login(client, "admin@example.com")

    # If "_" were an unescaped SQL wildcard it would also match "OffXDeal"
    # (any single character in that position); it must not.
    resp = client.get("/admin/students?q=Off_", headers=AJAX_HEADERS)
    html = resp.get_data(as_text=True)
    assert "Off_Deal Student" in html
    assert "OffXDeal Student" not in html


def test_prefix_matching_preserves_combined_status_filter(app, client):
    _seed_prefix_target(app)
    with app.app_context():
        student = User.query.filter_by(email="aj@gmail.com").first()
        student.status = UserStatus.SUSPENDED.value
        db.session.commit()
    login(client, "admin@example.com")

    resp = client.get("/admin/students?q=abd&status=active", headers=AJAX_HEADERS)
    html = resp.get_data(as_text=True)
    assert "abdulalgadeer Joha" not in html  # name matches but status doesn't

    resp2 = client.get("/admin/students?q=abd&status=suspended", headers=AJAX_HEADERS)
    html2 = resp2.get_data(as_text=True)
    assert "abdulalgadeer Joha" in html2


# ======================================================================
# RENDERED CONTROLS: no visible Clear/Filter, <noscript> fallback present
# ======================================================================


def test_no_visible_clear_or_filter_button_in_full_page(app, client):
    _seed_students(app)
    login(client, "admin@example.com")

    resp = client.get("/admin/students")
    html = resp.get_data(as_text=True)
    assert 'id="student-clear-filters"' not in html
    outside_noscript = html.split("<noscript>")[0] + html.split("</noscript>")[-1]
    assert ">Filter<" not in outside_noscript


def test_noscript_filter_button_present_as_fallback(app, client):
    _seed_students(app)
    login(client, "admin@example.com")

    resp = client.get("/admin/students")
    html = resp.get_data(as_text=True)
    assert "<noscript>" in html
    assert "</noscript>" in html
    noscript_block = html.split("<noscript>")[1].split("</noscript>")[0]
    assert "Filter" in noscript_block
    assert 'type="submit"' in noscript_block


def test_search_input_is_native_type_search_for_builtin_clear_affordance(app, client):
    _seed_students(app)
    login(client, "admin@example.com")

    resp = client.get("/admin/students")
    html = resp.get_data(as_text=True)
    assert 'type="search"' in html
    assert 'name="q"' in html


def test_shared_live_search_script_is_accessible(client):
    resp = client.get("/static/js/admin_live_search.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers.get("Content-Type", "")


def test_shared_live_search_script_contains_required_behavior(client):
    resp = client.get("/static/js/admin_live_search.js")
    js = resp.get_data(as_text=True)

    # Debounce
    assert "350" in js
    assert "setTimeout" in js
    # AbortController + stale-response sequence guard
    assert "AbortController" in js
    assert "requestSequence" in js
    assert "AbortError" in js
    # history.replaceState() + popstate restoration
    assert "history.replaceState" in js
    assert "popstate" in js
    # Accessible role="status" feedback
    assert "Searching..." in js
    # Delegated Suspend/Reactivate confirmation
    assert "data-confirm" in js
    assert "window.confirm" in js
    # Redirect handling for expired sessions
    assert "response.redirected" in js
    # Server-side round trip marker
    assert "X-Requested-With" in js

    # The shared file must not hard-code Student specifics as behavior
    # (only allowed as illustrative doc-comment examples).
    assert "/admin/students" not in js
    assert "student-status-form" not in js
    assert 'id="student' not in js


def test_status_confirmation_installed_before_feature_detection_gate(client):
    """Regression guard: the delegated Suspend/Reactivate confirmation must
    be wired up before the fetch/AbortController/History feature-detection
    check, so it still works in a browser lacking those APIs -- only live
    search itself should be skipped there. There is no JavaScript runtime
    available in this test suite (no Node/browser), so this is a narrowly
    scoped structural assertion over the actual shipped source rather than
    an executed behavioural test.
    """
    resp = client.get("/static/js/admin_live_search.js")
    source = resp.get_data(as_text=True)

    foreach_marker = 'document.querySelectorAll("[data-live-search]").forEach(function (root) {'
    assert foreach_marker in source
    callback_body = source[source.index(foreach_marker):]

    confirm_call_index = callback_body.index("installStatusConfirmation(root)")
    feature_check_index = callback_body.index("hasLiveSearchSupport()")

    assert confirm_call_index < feature_check_index, (
        "installStatusConfirmation(root) must run before the "
        "hasLiveSearchSupport() gate inside the per-root forEach callback"
    )
