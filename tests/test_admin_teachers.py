import re
from html.parser import HTMLParser

from app.models import UserRole, UserStatus
from tests.conftest import login, make_user

AJAX_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


def _make_teacher(email, full_name="Teacher", status=UserStatus.ACTIVE.value):
    return make_user(email, UserRole.TEACHER.value, status=status, full_name=full_name)


def _assert_no_clear_or_filter_controls(html):
    """No custom Clear control exists at all -- the search input's native
    browser-provided "x" (type=search) covers that job -- and no visible
    Filter button either; it only exists inside <noscript> as a no-JS
    fallback. Same contract as the Students page.
    """
    assert 'id="teacher-clear-filters"' not in html
    assert re.search(r"<noscript>\s*<button[^>]*>Filter</button>\s*</noscript>", html) is not None
    outside_noscript = re.sub(r"<noscript>.*?</noscript>", "", html, flags=re.DOTALL)
    assert ">Filter<" not in outside_noscript


class _ElementCollector(HTMLParser):
    """Collects every start tag with its attributes (stdlib-only, no new
    project dependency) so tests can assert on a specific element's
    attributes instead of scanning raw HTML text for substrings.
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


# ======================================================================
# AUTHORIZATION
# ======================================================================


def test_administrator_can_access_teacher_list(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.get("/admin/teachers")
    assert resp.status_code == 200


def test_anonymous_user_is_redirected_to_login(client):
    resp = client.get("/admin/teachers")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_teacher_cannot_access(app, client):
    with app.app_context():
        _make_teacher("teacher@example.com")
    login(client, "teacher@example.com")
    assert client.get("/admin/teachers").status_code == 403


def test_student_cannot_access(app, client):
    with app.app_context():
        make_user("student@example.com", UserRole.STUDENT.value)
    login(client, "student@example.com")
    assert client.get("/admin/teachers").status_code == 403


def test_researcher_cannot_access(app, client):
    with app.app_context():
        make_user("researcher@example.com", UserRole.RESEARCHER.value)
    login(client, "researcher@example.com")
    assert client.get("/admin/teachers").status_code == 403


def test_anonymous_denied_ajax(app, client):
    resp = client.get("/admin/teachers", headers=AJAX_HEADERS)
    assert resp.status_code in (302, 401)


def test_teacher_denied_ajax(app, client):
    with app.app_context():
        _make_teacher("teacher2@example.com")
    login(client, "teacher2@example.com")
    assert client.get("/admin/teachers", headers=AJAX_HEADERS).status_code == 403


def test_student_denied_ajax(app, client):
    with app.app_context():
        make_user("student2@example.com", UserRole.STUDENT.value)
    login(client, "student2@example.com")
    assert client.get("/admin/teachers", headers=AJAX_HEADERS).status_code == 403


def test_researcher_denied_ajax(app, client):
    with app.app_context():
        make_user("researcher2@example.com", UserRole.RESEARCHER.value)
    login(client, "researcher2@example.com")
    assert client.get("/admin/teachers", headers=AJAX_HEADERS).status_code == 403


# ======================================================================
# ROLE ISOLATION
# ======================================================================


def test_only_teacher_role_users_appear(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_teacher("alice.teacher@example.com", full_name="Alice Teacher")
    login(client, "admin@example.com")

    html = client.get("/admin/teachers").get_data(as_text=True)
    assert "Alice Teacher" in html


def test_other_roles_do_not_appear(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        make_user("student3@example.com", UserRole.STUDENT.value, full_name="Student Person")
        make_user("researcher3@example.com", UserRole.RESEARCHER.value, full_name="Researcher Person")
        _make_teacher("teacher3@example.com", full_name="Teacher Person")
    login(client, "admin@example.com")

    html = client.get("/admin/teachers").get_data(as_text=True)
    assert "Teacher Person" in html
    assert "Student Person" not in html
    assert "Researcher Person" not in html
    # The Administrator account itself must never appear either.
    assert "admin@example.com" not in html


def test_role_isolation_holds_during_search(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        make_user("student.smith@example.com", UserRole.STUDENT.value, full_name="Student Smith")
        _make_teacher("teacher.smith@example.com", full_name="Teacher Smith")
    login(client, "admin@example.com")

    html = client.get("/admin/teachers?q=Smith", headers=AJAX_HEADERS).get_data(as_text=True)
    assert "Teacher Smith" in html
    assert "Student Smith" not in html


def test_role_isolation_holds_during_status_filter(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        make_user("student.active@example.com", UserRole.STUDENT.value, full_name="Student Active",
                   status=UserStatus.ACTIVE.value)
        _make_teacher("teacher.active@example.com", full_name="Teacher Active", status=UserStatus.ACTIVE.value)
    login(client, "admin@example.com")

    html = client.get("/admin/teachers?status=active", headers=AJAX_HEADERS).get_data(as_text=True)
    assert "Teacher Active" in html
    assert "Student Active" not in html


# ======================================================================
# SEARCH AND FILTERING
# ======================================================================


def _seed_prefix_target(app):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_teacher("aj@gmail.com", "abdulalgadeer Joha")
        _make_teacher("someone.else@example.com", "Someone Else")


def test_prefix_matches_start_of_full_name(app, client):
    _seed_prefix_target(app)
    login(client, "admin@example.com")

    html = client.get("/admin/teachers?q=abd", headers=AJAX_HEADERS).get_data(as_text=True)
    assert "abdulalgadeer Joha" in html
    assert "Someone Else" not in html


def test_prefix_matches_start_of_second_word(app, client):
    _seed_prefix_target(app)
    login(client, "admin@example.com")

    html = client.get("/admin/teachers?q=joh", headers=AJAX_HEADERS).get_data(as_text=True)
    assert "abdulalgadeer Joha" in html
    assert "Someone Else" not in html


def test_prefix_matches_start_of_email(app, client):
    _seed_prefix_target(app)
    login(client, "admin@example.com")

    html = client.get("/admin/teachers?q=aj%40", headers=AJAX_HEADERS).get_data(as_text=True)  # "aj@"
    assert "abdulalgadeer Joha" in html
    assert "Someone Else" not in html


def test_matching_is_case_insensitive(app, client):
    _seed_prefix_target(app)
    login(client, "admin@example.com")

    html = client.get("/admin/teachers?q=ABD", headers=AJAX_HEADERS).get_data(as_text=True)
    assert "abdulalgadeer Joha" in html


def test_internal_substring_does_not_match(app, client):
    _seed_prefix_target(app)
    login(client, "admin@example.com")

    # "ade" only occurs mid-word inside "gadeer" -- must not match.
    html = client.get("/admin/teachers?q=ade", headers=AJAX_HEADERS).get_data(as_text=True)
    assert "abdulalgadeer Joha" not in html
    assert "No teachers match your filters" in html


def test_literal_percent_sign(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_teacher("promo@example.com", "50% Off Teacher")
        _make_teacher("other@example.com", "Regular Teacher")
    login(client, "admin@example.com")

    html = client.get("/admin/teachers?q=50%25", headers=AJAX_HEADERS).get_data(as_text=True)  # literal "50%"
    assert "50% Off Teacher" in html
    assert "Regular Teacher" not in html


def test_literal_underscore(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_teacher("underscore@example.com", "Off_Deal Teacher")
        _make_teacher("other2@example.com", "OffXDeal Teacher")
    login(client, "admin@example.com")

    # If "_" were an unescaped SQL wildcard it would also match "OffXDeal".
    html = client.get("/admin/teachers?q=Off_", headers=AJAX_HEADERS).get_data(as_text=True)
    assert "Off_Deal Teacher" in html
    assert "OffXDeal Teacher" not in html


def test_literal_backslash(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_teacher("backslash@example.com", "Back\\Slash Teacher")
    login(client, "admin@example.com")

    # "Back\Slash" (literal backslash) is the prefix of the full name; if
    # the backslash were mishandled as an escape character instead of a
    # literal, this prefix match would silently break.
    html = client.get("/admin/teachers?q=Back%5CSlash", headers=AJAX_HEADERS).get_data(as_text=True)
    assert "Back\\Slash Teacher" in html


def test_active_status_filter(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_teacher("active.t@example.com", full_name="Active Teacher", status=UserStatus.ACTIVE.value)
        _make_teacher("suspended.t@example.com", full_name="Suspended Teacher", status=UserStatus.SUSPENDED.value)
    login(client, "admin@example.com")

    html = client.get("/admin/teachers?status=active", headers=AJAX_HEADERS).get_data(as_text=True)
    assert "Active Teacher" in html
    assert "Suspended Teacher" not in html


def test_suspended_status_filter(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_teacher("active.t2@example.com", full_name="Active Teacher Two", status=UserStatus.ACTIVE.value)
        _make_teacher("suspended.t2@example.com", full_name="Suspended Teacher Two", status=UserStatus.SUSPENDED.value)
    login(client, "admin@example.com")

    html = client.get("/admin/teachers?status=suspended", headers=AJAX_HEADERS).get_data(as_text=True)
    assert "Suspended Teacher Two" in html
    assert "Active Teacher Two" not in html


def test_combined_search_and_status_filtering(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_teacher("k1@example.com", full_name="Karim Active", status=UserStatus.ACTIVE.value)
        _make_teacher("k2@example.com", full_name="Karim Suspended", status=UserStatus.SUSPENDED.value)
        _make_teacher("k3@example.com", full_name="Other Active", status=UserStatus.ACTIVE.value)
    login(client, "admin@example.com")

    html = client.get("/admin/teachers?q=Karim&status=active", headers=AJAX_HEADERS).get_data(as_text=True)
    assert "Karim Active" in html
    assert "Karim Suspended" not in html
    assert "Other Active" not in html


def test_invalid_status_ignored_safely(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_teacher("s1@example.com", full_name="Some Teacher", status=UserStatus.ACTIVE.value)
    login(client, "admin@example.com")

    for bad_status in ["deleted", "'; DROP TABLE users; --", "<script>alert(1)</script>", "ACTIVE"]:
        resp = client.get(f"/admin/teachers?status={bad_status}")
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert "Some Teacher" in html
        _assert_no_clear_or_filter_controls(html)


def test_empty_query_returns_all_teachers(app, client):
    _seed_prefix_target(app)
    login(client, "admin@example.com")

    html = client.get("/admin/teachers?q=", headers=AJAX_HEADERS).get_data(as_text=True)
    assert "abdulalgadeer Joha" in html
    assert "Someone Else" in html


# ======================================================================
# RESPONSE AND UI CONTRACT
# ======================================================================


def test_normal_request_returns_full_page(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_teacher("t@example.com", full_name="A Teacher")
    login(client, "admin@example.com")

    resp = client.get("/admin/teachers")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "<html" in html.lower()
    assert "admin-shell" in html
    assert 'id="teacher-results"' in html

    elements = _parse_elements(html)
    script_tags = [
        attrs for tag, attrs in elements
        if tag == "script" and attrs.get("src") == "/static/js/admin_live_search.js"
    ]
    assert len(script_tags) == 1, "expected exactly one <script src='.../admin_live_search.js'>"
    assert "defer" in script_tags[0]


def test_ajax_request_returns_fragment_not_full_page(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_teacher("t2@example.com", full_name="Fragment Teacher")
    login(client, "admin@example.com")

    resp = client.get("/admin/teachers", headers=AJAX_HEADERS)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "<html" not in html.lower()
    assert "admin-shell" not in html
    assert "Fragment Teacher" in html


def test_page_provides_required_live_search_configuration(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    resp = client.get("/admin/teachers")
    html = resp.get_data(as_text=True)
    elements = _parse_elements(html)

    _, root_attrs = _find_one(elements, "data-live-search")
    assert root_attrs.get("data-list-url") == "/admin/teachers"
    assert root_attrs.get("data-singular") == "teacher"
    assert root_attrs.get("data-plural") == "teachers"

    form_tag, _ = _find_one(elements, "data-live-search-form")
    assert form_tag == "form"

    input_tag, input_attrs = _find_one(elements, "data-live-search-input")
    assert input_tag == "input"
    assert input_attrs.get("type") == "search"

    select_tag, _ = _find_one(elements, "data-live-search-status")
    assert select_tag == "select"

    results_tag, results_attrs = _find_one(elements, "data-live-search-results")
    assert results_tag == "div"
    assert results_attrs.get("id") == "teacher-results"

    status_region_tag, status_region_attrs = _find_one(elements, "data-live-search-status-region")
    assert status_region_tag == "div"
    assert status_region_attrs.get("role") == "status"
    assert status_region_attrs.get("aria-live") == "polite"


def test_no_visible_clear_or_filter_controls(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    html = client.get("/admin/teachers").get_data(as_text=True)
    _assert_no_clear_or_filter_controls(html)


def test_search_placeholder_and_status_options(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    html = client.get("/admin/teachers").get_data(as_text=True)
    assert 'placeholder="Full name or email"' in html
    assert ">All Statuses<" in html
    assert ">Active<" in html
    assert ">Suspended<" in html


def test_empty_state_no_teachers_yet(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
    login(client, "admin@example.com")

    html = client.get("/admin/teachers").get_data(as_text=True)
    assert "No teachers yet" in html


def test_empty_state_no_teachers_match_filters(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_teacher("real@example.com", full_name="Real Teacher")
    login(client, "admin@example.com")

    html = client.get("/admin/teachers?q=NoSuchPerson", headers=AJAX_HEADERS).get_data(as_text=True)
    assert "No teachers match your filters" in html
    assert "No teachers yet" not in html
    assert 'href="/admin/teachers"' in html  # clear-all-filters link


def test_table_fields_and_status_badge(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher(
            "jane.doe@example.com", full_name="Jane Doe", status=UserStatus.SUSPENDED.value
        )
        created_date = teacher.created_at.strftime("%Y-%m-%d")
    login(client, "admin@example.com")

    html = client.get("/admin/teachers").get_data(as_text=True)
    assert "Jane Doe" in html
    assert "jane.doe@example.com" in html
    assert "Suspended" in html
    assert created_date in html


def test_html_in_teacher_name_is_escaped(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_teacher("xss@example.com", full_name="<script>alert('xss')</script>")
    login(client, "admin@example.com")

    html = client.get("/admin/teachers").get_data(as_text=True)
    assert "<script>alert('xss')</script>" not in html
    assert "&lt;script&gt;" in html


def test_password_hash_and_internal_id_not_rendered(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        teacher = _make_teacher("secret.holder@example.com", full_name="Secret Holder")
        teacher_id = teacher.id
        password_hash = teacher.password_hash
    login(client, "admin@example.com")

    html = client.get("/admin/teachers").get_data(as_text=True)
    assert password_hash not in html
    assert "$argon2" not in html
    assert f">{teacher_id}<" not in html


def test_no_actions_column_no_new_teacher_button_no_nonfunctional_links(app, client):
    with app.app_context():
        make_user("admin@example.com", UserRole.ADMINISTRATOR.value)
        _make_teacher("plain@example.com", full_name="Plain Teacher")
    login(client, "admin@example.com")

    html = client.get("/admin/teachers").get_data(as_text=True)
    assert ">Actions<" not in html
    assert "New Teacher" not in html
    assert ">Edit<" not in html
    assert ">Suspend<" not in html  # not a substring check -- "Suspended" (status text) legitimately appears
    assert ">Reactivate<" not in html
    assert ">Reset Password<" not in html
    assert "toggle-status" not in html  # no status-toggle form/action exists yet

    # The teacher's name must be plain text, not a link (no detail page yet).
    elements = _parse_elements(html)
    anchor_hrefs = [attrs.get("href") for tag, attrs in elements if tag == "a"]
    assert not any(href and "teachers/" in href and href != "/admin/teachers" for href in anchor_hrefs)
