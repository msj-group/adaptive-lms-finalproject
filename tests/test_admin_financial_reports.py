"""Phase 5 / M08: Administrator financial reports, CSV and PDF exports.

Three read-only reports -- collections, current outstanding invoices and
operational exceptions -- each as an HTML page, a CSV download and a PDF
download, built by one service from one set of validated filters. The tests
prove who may read them, what each report counts and excludes, that the three
outputs state the same facts, that filters are validated rather than
normalized, that nothing sensitive or internal leaks, and that no report
request changes any financial row.
"""

import re
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

import tests.fee_assignment_fixtures as fees
import tests.financial_report_fixtures as rx
import tests.payment_fixtures as px
import tests.payment_intent_fixtures as ix
import tests.webhook_fixtures as wx
from app.extensions import db
from app.models import Group, PaymentIntent, User, UserRole, UserStatus

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SEPT = "start=2026-09-01&end=2026-09-30"


@pytest.fixture
def app():
    app = rx.make_app()
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def mock_app():
    app = rx.make_mock_app()
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def _label(owning_group):
    from app.models import AcademicTerm, Course

    course = db.session.get(Course, owning_group.course_id)
    term = db.session.get(AcademicTerm, owning_group.academic_term_id)
    label = f"{owning_group.name} — {course.title} — {term.name}"
    return label if owning_group.status == "active" else label + " (archived)"


def _get(client, url, query=""):
    return client.get(f"{url}?{query}" if query else url)


def _assert_private(response):
    assert "no-store" in response.headers["Cache-Control"]
    assert "private" in response.headers["Cache-Control"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "Cookie" in response.headers.get("Vary", "")


def _html(client, url, query=""):
    response = _get(client, url, query)
    assert response.status_code == 200, response.status_code
    _assert_private(response)
    return response.get_data(as_text=True)


def _csv(client, url, query=""):
    response = _get(client, url + ".csv", query)
    assert response.status_code == 200, response.status_code
    return rx.csv_rows(response)


def _pdf(client, url, query=""):
    response = _get(client, url + ".pdf", query)
    assert response.status_code == 200, response.status_code
    body = response.get_data()
    rx.pdf_objects_valid(body)
    return body


# ===========================================================================
# Routes, authorization and methods
# ===========================================================================


def test_the_route_inventory_is_exact_get_only_and_carries_no_identifier(app, client):
    rules = {
        (rule.rule, frozenset(rule.methods - {"HEAD", "OPTIONS"}))
        for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith("admin.financial_report")
    }
    assert rules == {(url, frozenset({"GET"})) for url in rx.ALL_URLS}
    # Phase 5 / M10's Student Accounts record and Deleted Records pages carry
    # "financial" in their names; they have their own inventory in
    # tests/test_admin_financial_workspaces.py.
    workspaces = {"admin.student_financial_record", "admin.deleted_financial_records",
                  "admin.deleted_financial_record"}
    for rule in app.url_map.iter_rules():
        if rule.endpoint in workspaces:
            continue
        if "financial" in rule.rule or "financial" in rule.endpoint:
            assert rule.endpoint.startswith("admin.financial_report"), rule.rule
            assert "<" not in rule.rule, rule.rule
    px.login_world(app, client)
    with app.app_context():
        before = rx.everything()
    for url in rx.ALL_URLS:
        assert client.post(url).status_code == 405, url
        assert client.put(url).status_code == 405, url
        assert client.patch(url).status_code == 405, url
        assert client.delete(url).status_code == 405, url
    with app.app_context():
        assert rx.everything() == before


@pytest.mark.parametrize(
    "role", [UserRole.TEACHER.value, UserRole.STUDENT.value, UserRole.RESEARCHER.value]
)
def test_every_other_role_is_refused_every_report_and_export(app, client, role):
    px.world(app)
    with app.app_context():
        rx.user(f"{role}@example.com", role)
    rx.login_as(client, f"{role}@example.com")
    for url in rx.ALL_URLS:
        response = client.get(url)
        assert response.status_code == 403, url
        _assert_private(response)
        assert not response.get_data().startswith(b"%PDF")
        assert "attachment" not in response.headers.get("Content-Disposition", "")


def test_anonymous_and_suspended_administrators_are_sent_to_log_in(app, client):
    px.world(app)
    for url in rx.ALL_URLS:
        response = client.get(url)
        assert response.status_code == 302, url
        assert "/auth/login" in response.headers["Location"]
        _assert_private(response)
    rx.login_as(client, "admin@example.com")
    assert client.get(rx.INDEX_URL).status_code == 200
    with app.app_context():
        account = User.query.filter_by(email="admin@example.com").one()
        account.status = UserStatus.SUSPENDED.value
        db.session.commit()
    px.fresh_identity()
    for url in rx.ALL_URLS:
        response = client.get(url)
        assert response.status_code == 302, url
        assert "/auth/login" in response.headers["Location"]


def test_the_navigation_and_index_lead_to_the_three_reports(app, client):
    px.login_world(app, client)
    html = rx.page(client, "/admin/dashboard")
    assert re.search(r'href="/admin/financial-reports">Financial reports</a>', html)
    # Phase 6 / M01: Research is a real link now, and still a separate entry
    # after every financial workspace.
    assert re.search(r'href="/admin/research">Research</a>', html)
    index = _html(client, rx.INDEX_URL)
    for url, title in ((rx.COLLECTIONS_URL, "Collections report"),
                       (rx.OUTSTANDING_URL, "Outstanding invoices report"),
                       (rx.EXCEPTIONS_URL, "Operational exceptions report")):
        assert f'href="{url}">{title}</a>' in index
    assert rx.NOTICE_TEXT in index
    assert "admin-nav__link--active" in index


# ===========================================================================
# Collections
# ===========================================================================


def _collections_world(app):
    """Confirmed cash, bank-transfer and online collections and reversals in
    September 2026, a pending and a rejected transfer, and one movement each
    in August and October."""
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        plan = rx.plan_of(w)
        home = rx.group_of(w)
        second, _ = rx.invoice_in_group(actor, plan, student_name="Student Two", owning_group=home)
        third, _ = rx.invoice_in_group(actor, plan, student_name="Student Three", owning_group=home)
        cash = rx.transaction(owner, actor, "100.000", at=datetime(2026, 9, 3, 8, 0))
        rx.transaction(owner, actor, "200.500", method="bank_transfer",
                       recorded_at=datetime(2026, 9, 2, 8, 0), at=datetime(2026, 9, 4, 8, 0))
        rx.transaction(owner, actor, "50.000", at=datetime(2026, 9, 6, 8, 0))
        rx.transaction(owner, actor, "100.000", kind="reversal", reversal_of=cash,
                       at=datetime(2026, 9, 7, 8, 0))
        online, _ = rx.online_collection(second, actor, at=datetime(2026, 9, 8, 8, 0))
        rx.transaction(second, actor, "1250.5000", method="online", kind="reversal",
                       reversal_of=online, at=datetime(2026, 9, 9, 8, 0))
        rx.online_collection(third, actor, at=datetime(2026, 9, 12, 8, 0))
        rx.transaction(owner, actor, "300.000", method="bank_transfer", status="pending",
                       at=datetime(2026, 9, 5, 8, 0))
        rx.transaction(owner, actor, "400.000", method="bank_transfer", status="rejected",
                       recorded_at=datetime(2026, 9, 5, 9, 0), at=datetime(2026, 9, 6, 9, 0))
        rx.transaction(owner, actor, "7.000", at=datetime(2026, 8, 20, 8, 0))
        rx.transaction(owner, actor, "9.000", at=datetime(2026, 10, 2, 8, 0))
        w.update(
            numbers=[owner.invoice_number, second.invoice_number, third.invoice_number],
            group_name=home.name,
        )
    return w


_TOTALS = [
    ["Cash", "2", "150.000", "1", "-100.000", "50.000"],
    ["Bank transfer", "1", "200.500", "0", "0.000", "200.500"],
    ["Online", "2", "2501.000", "1", "-1250.500", "1250.500"],
]
_ALL_METHODS = ["All methods", "5", "2851.500", "2", "-1350.500", "1501.000"]


def _expected_movements(w):
    a, b, c = w["numbers"]
    g = w["group_name"]
    return [
        ["2026-09-03 10:00", "Collection", "Cash", "100.000", a, "Student One", g],
        ["2026-09-04 10:00", "Collection", "Bank transfer", "200.500", a, "Student One", g],
        ["2026-09-06 10:00", "Collection", "Cash", "50.000", a, "Student One", g],
        ["2026-09-07 10:00", "Reversal", "Cash", "-100.000", a, "Student One", g],
        ["2026-09-08 10:00", "Collection", "Online", "1250.500", b, "Student Two", g],
        ["2026-09-09 10:00", "Reversal", "Online", "-1250.500", b, "Student Two", g],
        ["2026-09-12 10:00", "Collection", "Online", "1250.500", c, "Student Three", g],
    ]


def _plain_rows(rows):
    return [[rx.plain(cell) for cell in row] for row in rows]


def test_collections_totals_are_exact_signed_and_by_method(app, client):
    w = _collections_world(app)
    rx.login_as(client, "admin@example.com")
    tables = rx.html_tables(_html(client, rx.COLLECTIONS_URL, _SEPT))
    assert _plain_rows(tables["totals"]["rows"]) == _TOTALS
    assert _plain_rows([tables["totals"]["footer"]]) == [_ALL_METHODS]
    assert tables["totals"]["rows"][2][2] == "2,501.000"
    assert _plain_rows(tables["movements"]["rows"]) == _expected_movements(w)
    assert tables["movements"]["footer"] == ["Net total (7 movements)", "", "", "1,501.000",
                                             "", "", ""]
    assert tables["movements"]["header"] == ["Confirmed (Africa/Tripoli)", "Type", "Method",
                                             "Amount (LYD)", "Invoice", "Student", "Group"]


def test_collections_csv_and_pdf_state_the_same_rows_and_totals(app, client, monkeypatch):
    w = _collections_world(app)
    rx.fixed_clock(monkeypatch, datetime(2026, 9, 18, 10, 0, 0))
    rx.login_as(client, "admin@example.com")
    html = _html(client, rx.COLLECTIONS_URL, _SEPT)
    tables = rx.html_tables(html)
    rows = _csv(client, rx.COLLECTIONS_URL, _SEPT)
    meta = rx.csv_meta(rows)
    assert meta["Report"] == "Collections report"
    assert meta["Generated"] == "2026-09-18 12:00:00 (Africa/Tripoli)"
    assert meta["Currency"] == "LYD"
    assert meta["Filter: Confirmation date (Africa/Tripoli)"] == "2026-09-01 to 2026-09-30, inclusive"
    assert meta["Filter: Group"] == "All groups (center-wide)"
    assert meta["Notice"] == rx.NOTICE_TEXT
    header, data = rx.csv_section(rows, "Totals by method")
    assert header == ["Method", "Collections", "Collected (LYD)", "Reversals", "Reversed (LYD)",
                      "Net (LYD)"]
    assert data == _TOTALS + [_ALL_METHODS]
    header, data = rx.csv_section(rows, "Confirmed movements")
    assert header == tables["movements"]["header"]
    assert data[:-1] == _expected_movements(w)
    assert data[-1] == ["Net total (7 movements)", "", "", "1501.000", "", "", ""]
    assert _plain_rows(tables["movements"]["rows"]) == data[:-1]
    for text in ("2026-09-18 12:00:00 (Africa/Tripoli)", "LYD", "2026-09-01 to 2026-09-30"):
        assert text in html
    texts = rx.pdf_texts(_pdf(client, rx.COLLECTIONS_URL, _SEPT))
    joined = "\n".join(texts)
    assert "Collections report" in texts
    assert "Generated: 2026-09-18 12:00:00 (Africa/Tripoli)" in texts
    assert "Currency: LYD" in texts
    assert "Group: All groups (center-wide)" in texts
    assert rx.NOTICE_TEXT in joined
    expected = []
    for row in tables["totals"]["rows"] + [tables["totals"]["footer"]] + tables["movements"]["rows"] + [
            tables["movements"]["footer"]]:
        expected += [cell for cell in row if cell]
    _assert_subsequence(expected, texts)


def _assert_subsequence(expected, texts):
    position = 0
    for text in expected:
        while position < len(texts) and texts[position] != text:
            position += 1
        assert position < len(texts), f"{text!r} missing from the PDF in report order"
        position += 1


def test_the_real_payment_paths_are_totaled_by_method(mock_app):
    """Cash, a confirmed bank transfer, a reversal, and an online collection
    confirmed by the sandbox's signed webhook -- recorded through the
    application's own routes -- with a pending and a rejected transfer that
    count for nothing."""
    client = mock_app.test_client()
    w = px.login_world(mock_app, client)
    with mock_app.app_context():
        owner, actor = rx.rows_of(w)
        second, _ = rx.invoice_in_group(actor, rx.plan_of(w), student_name="Student Two",
                                        owning_group=rx.group_of(w))
        from app.models import Enrollment, StudentFeeAssignment

        assignment = db.session.get(StudentFeeAssignment, second.student_fee_assignment_id)
        enrollment = db.session.get(Enrollment, assignment.enrollment_id)
        w2 = dict(w, ep=enrollment.public_id, ap=assignment.public_id, ip=second.public_id,
                  invoice_id=second.id)
    assert px.record_cash(client, w, amount="100").status_code == 302
    confirmed = px.pending_transfer(client, w, amount="200")
    assert px.confirm(client, w, confirmed).status_code == 302
    px.pending_transfer(client, w, amount="300")
    rejected = px.pending_transfer(client, w, amount="150")
    assert px.reject(client, w, rejected).status_code == 302
    with mock_app.app_context():
        cash = [row for row in px.stored_payments(w) if row.method == "cash"][0].public_id
    assert px.reverse(client, w, cash).status_code == 302
    wx.confirmed_intent(client, w2)
    query = "start=2000-01-01&end=9998-12-31"
    with mock_app.app_context():
        before = rx.everything()
    tables = rx.html_tables(_html(client, rx.COLLECTIONS_URL, query))
    assert _plain_rows(tables["totals"]["rows"]) == [
        ["Cash", "1", "100.000", "1", "-100.000", "0.000"],
        ["Bank transfer", "1", "200.000", "0", "0.000", "200.000"],
        ["Online", "1", "1250.500", "0", "0.000", "1250.500"],
    ]
    assert _plain_rows([tables["totals"]["footer"]]) == [
        ["All methods", "3", "1550.500", "1", "-100.000", "1450.500"]]
    _, data = rx.csv_section(_csv(client, rx.COLLECTIONS_URL, query), "Totals by method")
    assert data[-1] == ["All methods", "3", "1550.500", "1", "-100.000", "1450.500"]
    assert "1,450.500" in rx.pdf_texts(_pdf(client, rx.COLLECTIONS_URL, query))
    with mock_app.app_context():
        assert rx.everything() == before


def test_collections_use_the_tripoli_confirmation_date_inclusively(app, client):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        rx.transaction(owner, actor, "1.000", at=datetime(2026, 8, 31, 21, 59, 59))
        rx.transaction(owner, actor, "2.000", at=datetime(2026, 8, 31, 22, 0, 0))
        rx.transaction(owner, actor, "3.000", at=datetime(2026, 9, 30, 21, 59, 59))
        rx.transaction(owner, actor, "4.000", at=datetime(2026, 9, 30, 22, 0, 0))
    rx.login_as(client, "admin@example.com")
    rows = rx.html_tables(_html(client, rx.COLLECTIONS_URL, _SEPT))["movements"]["rows"]
    assert [(row[0], row[3]) for row in rows] == [("2026-09-01 00:00", "2.000"),
                                                  ("2026-09-30 23:59", "3.000")]
    one_day = rx.html_tables(_html(client, rx.COLLECTIONS_URL, "start=2026-09-01&end=2026-09-01"))
    assert [row[3] for row in one_day["movements"]["rows"]] == ["2.000"]
    august = rx.html_tables(_html(client, rx.COLLECTIONS_URL, "start=2026-08-31&end=2026-08-31"))
    assert [row[3] for row in august["movements"]["rows"]] == ["1.000"]
    october = rx.html_tables(_html(client, rx.COLLECTIONS_URL, "start=2026-10-01&end=2026-10-31"))
    assert [row[3] for row in october["movements"]["rows"]] == ["4.000"]


@pytest.mark.parametrize(
    "clock, start, end",
    [
        (datetime(2026, 9, 18, 10, 0), "2026-09-01", "2026-09-30"),
        (datetime(2026, 8, 31, 22, 30), "2026-09-01", "2026-09-30"),
        (datetime(2026, 9, 30, 21, 30), "2026-09-01", "2026-09-30"),
        (datetime(2026, 9, 30, 22, 30), "2026-10-01", "2026-10-31"),
        (datetime(2026, 12, 31, 12, 0), "2026-12-01", "2026-12-31"),
        (datetime(2028, 2, 10, 12, 0), "2028-02-01", "2028-02-29"),
    ],
)
def test_the_default_range_is_the_current_tripoli_month(app, client, monkeypatch, clock, start,
                                                        end):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        rx.transaction(owner, actor, "5.000", at=datetime(2026, 9, 15, 8, 0))
    rx.fixed_clock(monkeypatch, clock)
    rx.login_as(client, "admin@example.com")
    for query in ("", "start=&end="):
        html = _html(client, rx.COLLECTIONS_URL, query)
        assert f"{start} to {end}, inclusive (the current month, by default)" in html
        assert f'href="/admin/financial-reports/collections.csv?start={start}&amp;end={end}"' in html
        movements = rx.html_tables(html)["movements"]
        assert [row[3] for row in movements["rows"]] == (
            ["5.000"] if start == "2026-09-01" else [])
        if start != "2026-09-01":
            assert movements["empty"] == "No confirmed collection or reversal in this range."
    meta = rx.csv_meta(_csv(client, rx.COLLECTIONS_URL))
    assert meta["Filter: Confirmation date (Africa/Tripoli)"].startswith(f"{start} to {end}")


# ===========================================================================
# Filters are validated, never normalized
# ===========================================================================

_BAD_DATES = [
    "start=2026-09-01",
    "end=2026-09-30",
    "start=2026-09-01&end=",
    "start=2026-9-1&end=2026-09-30",
    "start=2026-02-30&end=2026-03-01",
    "start=abc&end=2026-09-30",
    "start=2026-09-01T00:00&end=2026-09-30",
    "start=%202026-09-01&end=2026-09-30",
    "start=2026-10-01&end=2026-09-30",
    "start=1999-12-31&end=2026-09-30",
    "start=2026-09-01&end=9999-12-31",
    "start=2026-09-01&start=2026-09-02&end=2026-09-30",
    "start=%D9%A2%D9%A0%D9%A2%D9%A6-09-01&end=2026-09-30",
]


def _assert_refused(response):
    assert response.status_code == 400
    _assert_private(response)
    assert response.mimetype == "text/html"
    html = response.get_data(as_text=True)
    assert rx.NO_REPORT_TEXT in html
    assert "Download CSV" not in html
    assert 'id="section-' not in html
    return html


@pytest.mark.parametrize("query", _BAD_DATES)
def test_malformed_incomplete_or_reversed_dates_are_refused(app, client, query):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        rx.transaction(owner, actor, "5.000")
        before = rx.everything()
    rx.login_as(client, "admin@example.com")
    for suffix in ("", ".csv", ".pdf"):
        _assert_refused(_get(client, rx.COLLECTIONS_URL + suffix, query))
    with app.app_context():
        assert rx.everything() == before


def test_the_refusals_name_the_problem(app, client):
    px.login_world(app, client)
    cases = {
        "start=2026-09-01": "Enter both a start date and an end date",
        "start=2026-10-01&end=2026-09-30": "The start date is after the end date",
        "start=2026-9-1&end=2026-09-30": "Enter the start date as YYYY-MM-DD",
        "start=2026-09-01&end=2026-02-30": "Enter the end date as YYYY-MM-DD",
        "start=2026-09-01&start=2026-09-01&end=2026-09-30": "Each filter can be given only once.",
    }
    for query, text in cases.items():
        assert text in _assert_refused(_get(client, rx.COLLECTIONS_URL, query)), query


@pytest.mark.parametrize("url", [rx.OUTSTANDING_URL, rx.EXCEPTIONS_URL])
def test_current_state_reports_take_no_date_range(app, client, url):
    px.login_world(app, client)
    for suffix in ("", ".csv", ".pdf"):
        for query in (_SEPT, "start=2026-09-01", "end=2026-09-30"):
            html = _assert_refused(_get(client, url + suffix, query))
            assert "takes no date range" in html


def test_a_group_filter_must_be_one_known_public_identifier(app, client):
    w = px.login_world(app, client)
    with app.app_context():
        internal = str(w["group_id"])
    bad = [
        f"group={internal}",
        "group=00000000-0000-0000-0000-000000000000",
        f"group={w['gp'].upper()}",
        f"group=%20{w['gp']}",
        "group=abc",
        f"group={w['gp']}&group={w['gp']}",
    ]
    for url in rx.REPORT_URLS:
        for suffix in ("", ".csv", ".pdf"):
            for query in bad:
                html = _assert_refused(_get(client, url + suffix, query))
                if "&group=" not in query:
                    assert "That Group filter is not valid" in html


# ===========================================================================
# The Group filter
# ===========================================================================


def _two_groups(app):
    """One of every reportable row in the world's Group and in a second
    Group."""
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        other, other_group = rx.invoice_in_group(actor, rx.plan_of(w), group_name="Evening B",
                                                 student_name="Student Two")
        for invoice in (owner, other):
            rx.transaction(invoice, actor, "10.000")
            rx.transaction(invoice, actor, "20.000", method="bank_transfer", status="pending")
            rx.transaction(invoice, actor, "30.000", method="bank_transfer", status="rejected")
            active = ix.intent(invoice, actor, status="pending")
            rx.provider_event(active)
        w.update(other_gp=other_group.public_id, other_number=other.invoice_number,
                 number=owner.invoice_number, other_group_id=other_group.id)
    return w


def _numbers(rows, column):
    return sorted({row[column] for row in rows})


def test_the_group_filter_limits_every_report_to_that_group(app, client):
    w = _two_groups(app)
    rx.login_as(client, "admin@example.com")
    both = sorted([w["number"], w["other_number"]])
    tables = rx.html_tables(_html(client, rx.COLLECTIONS_URL, _SEPT))
    assert _numbers(tables["movements"]["rows"], 4) == both
    outstanding = rx.html_tables(_html(client, rx.OUTSTANDING_URL))
    assert _numbers(outstanding["invoices"]["rows"], 0) == both
    exceptions = rx.html_tables(_html(client, rx.EXCEPTIONS_URL))
    for key in ("pending", "rejected", "intents", "reconciliation"):
        column = 2 if key != "intents" and key != "reconciliation" else len(
            exceptions[key]["header"]) - 3
        assert _numbers(exceptions[key]["rows"], column) == both, key

    for gp, number in ((w["gp"], w["number"]), (w["other_gp"], w["other_number"])):
        query = f"group={gp}&{_SEPT}"
        html = _html(client, rx.COLLECTIONS_URL, query)
        with app.app_context():
            label = _label(Group.query.filter_by(public_id=gp).one())
        assert f'<option value="{gp}" selected>' in html
        tables = rx.html_tables(html)
        assert _numbers(tables["movements"]["rows"], 4) == [number]
        assert rx.csv_meta(_csv(client, rx.COLLECTIONS_URL, query))["Filter: Group"] == label
        _, data = rx.csv_section(_csv(client, rx.COLLECTIONS_URL, query), "Confirmed movements")
        assert {row[4] for row in data[:-1]} == {number}
        assert f"Group: {label}" in rx.pdf_texts(_pdf(client, rx.COLLECTIONS_URL, query))
        outstanding = rx.html_tables(_html(client, rx.OUTSTANDING_URL, f"group={gp}"))
        assert _numbers(outstanding["invoices"]["rows"], 0) == [number]
        _, data = rx.csv_section(_csv(client, rx.OUTSTANDING_URL, f"group={gp}"),
                                 "Outstanding invoices")
        assert [row[0] for row in data[:-1]] == [number]
        exceptions = rx.html_tables(_html(client, rx.EXCEPTIONS_URL, f"group={gp}"))
        for key in ("pending", "rejected", "intents", "reconciliation"):
            assert len(exceptions[key]["rows"]) == 1, key
            assert number in exceptions[key]["rows"][0], key
        summary = exceptions["summary"]["rows"]
        assert [row[1] for row in summary] == ["1", "1", "1", "1"]


def test_an_archived_group_stays_reportable(app, client):
    w = _two_groups(app)
    with app.app_context():
        archived = db.session.get(Group, w["other_group_id"])
        archived.status = "archived"
        db.session.commit()
        label = _label(archived)
    assert label.endswith("(archived)")
    rx.login_as(client, "admin@example.com")
    html = _html(client, rx.OUTSTANDING_URL, f"group={w['other_gp']}")
    assert f'<option value="{w["other_gp"]}" selected>{label}</option>' in html
    assert _numbers(rx.html_tables(html)["invoices"]["rows"], 0) == [w["other_number"]]


def test_group_choices_offer_public_identifiers_only(app, client):
    w = _two_groups(app)
    rx.login_as(client, "admin@example.com")
    html = _html(client, rx.EXCEPTIONS_URL)
    values = re.findall(r'<option value="([^"]*)"', html)
    assert values[0] == ""
    assert set(values[1:]) == {w["gp"], w["other_gp"]}
    assert all(_UUID.fullmatch(value) for value in values[1:])


# ===========================================================================
# Outstanding invoices
# ===========================================================================


def test_outstanding_invoices_follow_the_payment_balance_rules(app, client, monkeypatch):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        plan, home = rx.plan_of(w), rx.group_of(w)

        def new(status="issued", name=None):
            return rx.invoice_in_group(actor, plan, status=status, owning_group=home,
                                       student_name=name)[0]

        rx.transaction(owner, actor, "100.000")
        rx.transaction(owner, actor, "300.000", method="bank_transfer", status="pending")
        rx.transaction(owner, actor, "400.000", method="bank_transfer", status="rejected")
        reopened = new(name="Student Reversed")
        cash = rx.transaction(reopened, actor, "100.000")
        rx.transaction(reopened, actor, "100.000", kind="reversal", reversal_of=cash)
        settled = new(name="Student Settled")
        rx.transaction(settled, actor, "1250.5000")
        settled_online = new(name="Student Online")
        rx.online_collection(settled_online, actor)
        draft = new(status="draft", name="Student Draft")
        cancelled = new(status="cancelled", name="Student Cancelled")
        with_removed = new(name="Student Removed Line")
        px.fx.line(with_removed, label="Old fee", amount="999.000", status="removed",
                   removed_by=actor)
        hidden = [settled.invoice_number, settled_online.invoice_number,
                  cancelled.invoice_number]
        g = home.name
        expected = [
            [owner.invoice_number, "Student One", g, "2026-06-02 11:00", "1250.500", "100.000",
             "1150.500"],
            [reopened.invoice_number, "Student Reversed", g, "2026-06-02 11:00", "1250.500",
             "0.000", "1250.500"],
            [with_removed.invoice_number, "Student Removed Line", g, "2026-06-02 11:00",
             "1250.500", "0.000", "1250.500"],
        ]
        assert draft.invoice_number is None
    rx.fixed_clock(monkeypatch, datetime(2026, 9, 18, 10, 0, 0))
    rx.login_as(client, "admin@example.com")
    html = _html(client, rx.OUTSTANDING_URL)
    tables = rx.html_tables(html)
    assert _plain_rows(tables["invoices"]["rows"]) == expected
    assert _plain_rows([tables["invoices"]["footer"]]) == [
        ["Total (3 invoices)", "", "", "", "3751.500", "100.000", "3651.500"]]
    for number in hidden:
        assert number not in html
    assert "Student Draft" not in html and "Student Cancelled" not in html
    assert "The current state when this report was generated" in html
    header, data = rx.csv_section(_csv(client, rx.OUTSTANDING_URL), "Outstanding invoices")
    assert header == ["Invoice", "Student", "Group", "Issued (Africa/Tripoli)", "Total (LYD)",
                      "Paid (LYD)", "Outstanding (LYD)"]
    assert data == expected + [["Total (3 invoices)", "", "", "", "3751.500", "100.000",
                                "3651.500"]]
    texts = rx.pdf_texts(_pdf(client, rx.OUTSTANDING_URL))
    _assert_subsequence([cell for row in tables["invoices"]["rows"] for cell in row]
                        + ["Total (3 invoices)", "3,751.500", "100.000", "3,651.500"], texts)


def test_an_invoice_whose_balance_is_unavailable_is_listed_apart_and_not_totaled(app, client):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        broken, _ = rx.invoice_in_group(actor, rx.plan_of(w), student_name="Student Broken",
                                        owning_group=rx.group_of(w))
        rx.transaction(broken, actor, "99999.000")
    rx.login_as(client, "admin@example.com")
    html = _html(client, rx.OUTSTANDING_URL)
    tables = rx.html_tables(html)
    assert [row[1] for row in tables["invoices"]["rows"]] == ["Student One"]
    assert tables["invoices"]["footer"][-1] == "1,250.500"
    assert [row[1] for row in tables["unavailable"]["rows"]] == ["Student Broken"]
    assert "1 issued invoice(s) have payment records that do not describe a valid balance" in html
    rows = _csv(client, rx.OUTSTANDING_URL)
    _, data = rx.csv_section(rows, "Invoices whose balance is unavailable")
    assert [row[1] for row in data[:-1]] == ["Student Broken"]
    assert "Student Broken" in rx.pdf_texts(_pdf(client, rx.OUTSTANDING_URL))


# ===========================================================================
# Operational exceptions
# ===========================================================================


def _exceptions_world(app):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        plan, home = rx.plan_of(w), rx.group_of(w)
        rx.transaction(owner, actor, "300.000", method="bank_transfer", status="pending",
                       reference="PENDING-REF-4471", at=datetime(2026, 9, 5, 8, 0))
        rx.transaction(owner, actor, "400.000", method="bank_transfer", status="rejected",
                       reference="REJECTED-REF-8812", reason="Reason must not leak",
                       recorded_at=datetime(2026, 9, 5, 9, 0), at=datetime(2026, 9, 6, 9, 0))
        rx.transaction(owner, actor, "200.000", method="bank_transfer",
                       reference="CONFIRMED-REF-1234", at=datetime(2026, 9, 7, 9, 0))
        second, _ = rx.invoice_in_group(actor, plan, student_name="Student Two", owning_group=home)
        third, _ = rx.invoice_in_group(actor, plan, student_name="Student Three", owning_group=home)
        fourth, _ = rx.invoice_in_group(actor, plan, student_name="Student Four", owning_group=home)
        pending = ix.intent(second, actor, status="pending", created_at=datetime(2026, 7, 5, 9, 0))
        ix.intent(second, actor, status="provider_failed")
        ix.intent(second, actor, status="cancelled")
        succeeded = ix.intent(third, actor, status="provider_succeeded",
                              created_at=datetime(2026, 7, 5, 9, 30))
        _, confirmed = rx.online_collection(fourth, actor)
        event = rx.provider_event(confirmed, event_type="payment.failed",
                                  received_at=datetime(2026, 9, 9, 9, 0))
        rx.provider_event(confirmed, outcome="duplicate", received_at=datetime(2026, 9, 9, 10, 0))
        rx.provider_event(succeeded, outcome="ignored_terminal", event_type="payment.failed",
                          received_at=datetime(2026, 9, 9, 11, 0))
        secrets_ = [
            "PENDING-REF-4471", "REJECTED-REF-8812", "CONFIRMED-REF-1234", "Reason must not leak",
            event.provider_event_id, event.payload_digest, event.public_id,
            ix.TEST_WEBHOOK_SECRET,
        ]
        for intent in PaymentIntent.query.all():
            secrets_ += [intent.provider_reference, intent.idempotency_key, intent.public_id]
        w.update(
            secrets=secrets_,
            numbers=[owner.invoice_number, second.invoice_number, third.invoice_number,
                     fourth.invoice_number],
            group_name=home.name,
            pending_intent=pending.public_id,
        )
    return w


def test_every_exception_category_is_shown_and_nothing_else(app, client):
    w = _exceptions_world(app)
    rx.login_as(client, "admin@example.com")
    a, b, c, d = w["numbers"]
    g = w["group_name"]
    tables = rx.html_tables(_html(client, rx.EXCEPTIONS_URL))
    assert _plain_rows(tables["summary"]["rows"]) == [
        ["Pending bank transfers", "1", "300.000"],
        ["Rejected bank transfers", "1", "400.000"],
        ["Active online payment intents", "2", "2501.000"],
        ["Provider events requiring reconciliation", "1", "1250.500"],
    ]
    assert _plain_rows(tables["pending"]["rows"]) == [
        ["2026-09-05 10:00", "300.000", a, "Student One", g]]
    assert tables["pending"]["footer"] == ["Total (1)", "300.000", "", "", ""]
    assert _plain_rows(tables["rejected"]["rows"]) == [
        ["2026-09-06 11:00", "400.000", a, "Student One", g]]
    assert _plain_rows(tables["intents"]["rows"]) == [
        ["2026-07-05 11:00", "Pending", "1250.500", b, "Student Two", g],
        ["2026-07-05 11:30", "Browser-observed success (awaiting signed webhook)", "1250.500", c,
         "Student Three", g],
    ]
    assert _plain_rows(tables["reconciliation"]["rows"]) == [
        ["2026-09-09 11:00", "Payment failed", "Confirmed by signed webhook", "1250.500", d,
         "Student Four", g]]
    rows = _csv(client, rx.EXCEPTIONS_URL)
    for key, title in (("pending", "Pending bank transfers"), ("rejected", "Rejected bank transfers"),
                       ("intents", "Active online payment intents"),
                       ("reconciliation", "Provider events requiring reconciliation")):
        header, data = rx.csv_section(rows, title)
        assert header == tables[key]["header"]
        assert data[:-1] == _plain_rows(tables[key]["rows"])
        assert data[-1] == _plain_rows([tables[key]["footer"]])[0]
    texts = rx.pdf_texts(_pdf(client, rx.EXCEPTIONS_URL))
    for key in ("summary", "pending", "rejected", "intents", "reconciliation"):
        _assert_subsequence([cell for row in tables[key]["rows"] for cell in row if cell], texts)


def test_no_output_exposes_references_reasons_events_keys_or_internal_ids(app, client):
    w = _exceptions_world(app)
    rx.login_as(client, "admin@example.com")
    outputs = []
    for url in rx.REPORT_URLS:
        query = "start=2000-01-01&end=9998-12-31" if url == rx.COLLECTIONS_URL else ""
        outputs.append(_html(client, url, query))
        outputs.append(_get(client, url + ".csv", query).get_data().decode("utf-8-sig"))
        outputs.append("\n".join(rx.pdf_texts(_pdf(client, url, query))))
    for text in outputs:
        for secret in w["secrets"]:
            assert secret not in text, secret
        for fragment in ("payload", "digest", "signature", "evt_mock_", "mock_pi_", "BANKREF"):
            assert fragment not in text, fragment
    with app.app_context():
        from app.services import financial_reports as reports

        moment = datetime(2026, 9, 18, 10, 0)
        for key in (reports.COLLECTIONS, reports.OUTSTANDING, reports.EXCEPTIONS):
            args = {"start": ["2000-01-01"], "end": ["9998-12-31"]} if key == reports.COLLECTIONS else {}
            filters, errors = reports.parse_report_filters(key, _Args(args), rx.TZ, moment)
            assert errors == []
            report = reports.build_report(key, filters, rx.TZ, moment)
            for section in report.sections:
                keys = {column.key for column in section.columns}
                assert not any(key == "id" or key.endswith("_id") for key in keys)
                for row in section.rows + ((section.footer,) if section.footer else ()):
                    assert set(row) <= keys
                    for value in row.values():
                        assert isinstance(value, (str, int, Decimal, datetime)), value


class _Args(dict):
    def getlist(self, name):
        return list(self.get(name, []))


# ===========================================================================
# CSV and PDF downloads
# ===========================================================================


@pytest.mark.parametrize("url", rx.REPORT_URLS)
def test_downloads_are_attachments_with_fixed_names_and_safe_headers(app, client, url):
    _two_groups(app)
    rx.login_as(client, "admin@example.com")
    for query in ("", "group=" + rx_first_group(app)):
        response = _get(client, url + ".csv", query)
        assert response.status_code == 200
        assert response.headers["Content-Type"] == "text/csv; charset=utf-8"
        assert response.headers["Content-Disposition"] == (
            f'attachment; filename="{rx.FILENAMES[url]}.csv"')
        _assert_private(response)
        assert response.get_data().startswith(b"\xef\xbb\xbf")
        response = _get(client, url + ".pdf", query)
        assert response.status_code == 200
        assert response.headers["Content-Type"] == "application/pdf"
        assert response.headers["Content-Disposition"] == (
            f'attachment; filename="{rx.FILENAMES[url]}.pdf"')
        _assert_private(response)
        body = response.get_data()
        assert rx.pdf_objects_valid(body) > 0
        assert rx.pdf_embeds_dejavu(body)
        assert rx.pdf_active_names(body) == set()


def rx_first_group(app):
    with app.app_context():
        return Group.query.order_by(Group.id).first().public_id


def test_csv_neutralizes_formulas_and_keeps_amounts_numeric(app, client):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        names = ['=HYPERLINK("http://example.com","x")', "+SUM(1,1)", "-2+3", "@cmd"]
        students = []
        for name in names:
            invoice, _ = rx.invoice_in_group(actor, rx.plan_of(w), student_name=name,
                                             group_name="=1+1")
            students.append(invoice)
            original = rx.transaction(invoice, actor, "100.000")
            rx.transaction(invoice, actor, "100.000", kind="reversal", reversal_of=original)
    rx.login_as(client, "admin@example.com")
    rows = _csv(client, rx.COLLECTIONS_URL, _SEPT)
    _, data = rx.csv_section(rows, "Confirmed movements")
    students_seen = {row[5] for row in data[:-1]}
    assert students_seen == {"'" + name for name in names}
    assert {row[6] for row in data[:-1]} == {"'=1+1"}
    assert sorted({row[3] for row in data[:-1]}) == ["-100.000", "100.000"]
    for row in rows:
        for cell in row:
            assert not cell.startswith(("=", "+", "@", "\t", "\r")), cell
            if cell.startswith("-"):
                assert re.fullmatch(r"-[0-9]+\.[0-9]{3,4}", cell), cell
    html = _html(client, rx.COLLECTIONS_URL, _SEPT)
    assert "=HYPERLINK(&#34;http://example.com&#34;,&#34;x&#34;)" in html
    texts = rx.pdf_texts(_pdf(client, rx.COLLECTIONS_URL, _SEPT))
    assert '=HYPERLINK("http://example.com","x")' in texts


def test_pdf_text_is_drawn_literally_and_never_interpreted(app, client):
    hostile = '<a href="javascript:alert(1)">x</a> (Evil) Tj /JavaScript \\ %c'
    markup = "<b>bold</b> <font color=red>r</font> &amp; <br/>"
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        invoice, _ = rx.invoice_in_group(actor, rx.plan_of(w), student_name=hostile,
                                         group_name=markup)
        rx.transaction(invoice, actor, "100.000")
    rx.login_as(client, "admin@example.com")
    body = _pdf(client, rx.COLLECTIONS_URL, _SEPT)
    drawn = " ".join(rx.pdf_texts(body))  # a long cell wraps at its spaces
    assert hostile in drawn and markup in drawn
    assert rx.pdf_active_names(body) == set()
    html = _html(client, rx.COLLECTIONS_URL, _SEPT)
    assert "&lt;a href=&#34;javascript:alert(1)&#34;&gt;" in html
    assert "<b>bold</b>" not in html


def test_a_long_report_spans_pdf_pages_with_repeated_headers_and_html_pages(app, client,
                                                                            monkeypatch):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        for minute in range(65):
            rx.transaction(owner, actor, f"{minute + 1}.000",
                           at=datetime(2026, 9, 10, 8, 0) + timedelta(minutes=minute))
    rx.fixed_clock(monkeypatch, datetime(2026, 9, 18, 10, 0, 0))
    rx.login_as(client, "admin@example.com")
    body = _pdf(client, rx.COLLECTIONS_URL, _SEPT)
    per_page = rx.pdf_page_texts(body)
    pages = len(per_page)
    assert pages >= 3
    spanned = [texts for texts in per_page if "Confirmed movements" in texts]
    assert len(spanned) >= 2
    for texts in spanned:  # the section title and its column header, on every page
        assert texts.count("Confirmed movements") == 1
        assert texts.count("Confirmed (Africa/Tripoli)") == 1
    for number, texts in enumerate(per_page, start=1):
        assert ("Collections report (continued)" in texts) == (number > 1)
        assert f"Collections report | Page {number} of {pages} | {rx.NOTICE_TEXT}" in texts
    assert {"Net total (65 movements)", "2,145.000"} <= set(per_page[-1])
    texts = rx.pdf_texts(body)
    _assert_subsequence([f"{n}.000" for n in range(1, 66)] + ["Net total (65 movements)",
                                                               "2,145.000"], texts)
    first = rx.html_tables(_html(client, rx.COLLECTIONS_URL, _SEPT))
    second_html = _html(client, rx.COLLECTIONS_URL, _SEPT + "&page=2")
    second = rx.html_tables(second_html)
    assert len(first["movements"]["rows"]) == 50 and len(second["movements"]["rows"]) == 15
    assert first["movements"]["footer"] == second["movements"]["footer"]
    assert first["movements"]["footer"][3] == "2,145.000"
    assert "Rows 51&ndash;65 of 65" in second_html or "Rows 51–65 of 65" in second_html
    beyond = rx.html_tables(_html(client, rx.COLLECTIONS_URL, _SEPT + "&page=9"))
    assert beyond["movements"]["rows"] == first["movements"]["rows"]
    _, data = rx.csv_section(_csv(client, rx.COLLECTIONS_URL, _SEPT + "&page=2"),
                             "Confirmed movements")
    assert len(data) == 66
    assert [row[3] for row in data[:-1]] == [f"{n}.000" for n in range(1, 66)]


def test_ordering_is_deterministic_and_repeated_exports_are_identical(app, client, monkeypatch):
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        for amount in ("70.000", "50.000", "60.000"):
            rx.transaction(owner, actor, amount, at=datetime(2026, 9, 10, 8, 0))
    rx.fixed_clock(monkeypatch, datetime(2026, 9, 18, 10, 0, 0))
    rx.login_as(client, "admin@example.com")
    rows = rx.html_tables(_html(client, rx.COLLECTIONS_URL, _SEPT))["movements"]["rows"]
    assert [row[3] for row in rows] == ["70.000", "50.000", "60.000"]
    for suffix in (".csv", ".pdf"):
        first = _get(client, rx.COLLECTIONS_URL + suffix, _SEPT).get_data()
        assert _get(client, rx.COLLECTIONS_URL + suffix, _SEPT).get_data() == first


def _assert_drawn_subsequence(expected, texts):
    """Like :func:`_assert_subsequence`, comparing each cell as the PDF draws
    it: Arabic shaped and in visual order."""
    position = 0
    for text in expected:
        while position < len(texts) and not rx.drawn_as(texts[position], text):
            position += 1
        assert position < len(texts), f"{text!r} missing from the PDF in report order"
        position += 1


_ARABIC_NAMES = ("محمد علي", "Ali علي Hassan", "عبدالله الطرابلسي")
_ARABIC_GROUP = "مجموعة الصباح"


def _arabic_world(app):
    """Three Students with Arabic and mixed names in a Group with an Arabic
    name: a confirmed cash payment each, a pending transfer and an active
    intent for the first."""
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        owning = fees.group(name=_ARABIC_GROUP)
        invoices = []
        for index, name in enumerate(_ARABIC_NAMES):
            invoice, _ = rx.invoice_in_group(actor, rx.plan_of(w), student_name=name,
                                             owning_group=owning)
            rx.transaction(invoice, actor, f"{index + 1}00.000",
                           at=datetime(2026, 9, 10 + index, 8, 0))
            invoices.append(invoice)
        rx.transaction(invoices[0], actor, "20.000", method="bank_transfer", status="pending")
        ix.intent(invoices[1], actor, status="pending")
        w.update(arabic_gp=owning.public_id, arabic_label=_label(owning))
    return w


def test_arabic_and_mixed_names_print_in_every_pdf_with_the_html_and_csv_rows(app, client):
    w = _arabic_world(app)
    with app.app_context():
        before = rx.everything()
    rx.login_as(client, "admin@example.com")
    cases = (
        (rx.COLLECTIONS_URL, f"group={w['arabic_gp']}&{_SEPT}", ("totals", "movements"),
         {"totals": "Totals by method", "movements": "Confirmed movements"}),
        (rx.OUTSTANDING_URL, f"group={w['arabic_gp']}", ("invoices",),
         {"invoices": "Outstanding invoices"}),
        (rx.EXCEPTIONS_URL, f"group={w['arabic_gp']}", ("summary", "pending", "intents"),
         {"summary": "Summary", "pending": "Pending bank transfers",
          "intents": "Active online payment intents"}),
    )
    for url, query, keys, titles in cases:
        response = _get(client, url + ".pdf", query)
        assert response.status_code == 200, url
        assert response.mimetype == "application/pdf"
        _assert_private(response)
        body = response.get_data()
        assert rx.pdf_embeds_dejavu(body) and rx.pdf_active_names(body) == set()
        texts = rx.pdf_texts(body)
        assert any(rx.drawn_as(text, f"Group: {w['arabic_label']}") for text in texts), url
        tables = rx.html_tables(_html(client, url, query))
        rows = _csv(client, url, query)
        expected = []
        for key in keys:
            html_rows = tables[key]["rows"] + ([tables[key]["footer"]] if tables[key]["footer"]
                                               else [])
            _, data = rx.csv_section(rows, titles[key])
            assert data == _plain_rows(html_rows), key
            expected += [cell for row in html_rows for cell in row if cell]
        _assert_drawn_subsequence(expected, texts)
    movements = rx.html_tables(_html(client, rx.COLLECTIONS_URL,
                                     f"group={w['arabic_gp']}&{_SEPT}"))["movements"]["rows"]
    assert [row[5] for row in movements] == list(_ARABIC_NAMES)
    assert {row[6] for row in movements} == {_ARABIC_GROUP}
    with app.app_context():
        assert rx.everything() == before


def test_a_pdf_the_font_cannot_show_is_refused_explicitly_and_csv_keeps_it(app, client):
    unsupported = "李小龙"
    w = px.world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        invoice, owning = rx.invoice_in_group(actor, rx.plan_of(w), student_name=unsupported)
        rx.transaction(invoice, actor, "100.000")
        gp = owning.public_id
        before = rx.everything()
    rx.login_as(client, "admin@example.com")
    for url, query, title in ((rx.COLLECTIONS_URL, _SEPT, "Confirmed movements"),
                              (rx.OUTSTANDING_URL, f"group={gp}", "Outstanding invoices")):
        response = _get(client, url + ".pdf", query)
        assert response.status_code == 302
        _assert_private(response)
        location = response.headers["Location"]
        assert location.startswith(url + "?") and ".pdf" not in location
        html = rx.page(client, location)
        assert rx.PDF_REFUSED_TEXT in html
        assert unsupported in html
        _, data = rx.csv_section(_csv(client, url, query), title)
        assert unsupported in {cell for row in data for cell in row}
    assert _get(client, rx.EXCEPTIONS_URL + ".pdf").status_code == 200  # the name is in none
    with app.app_context():
        assert rx.everything() == before


# ===========================================================================
# Read-only
# ===========================================================================


def test_no_report_request_changes_any_financial_row_or_audit_event(app, client):
    w = _exceptions_world(app)
    with app.app_context():
        owner, actor = rx.rows_of(w)
        cash = rx.transaction(owner, actor, "10.000")
        rx.transaction(owner, actor, "10.000", kind="reversal", reversal_of=cash)
        px.fx.event(owner, actor, 1)
        before = rx.everything()
    rx.login_as(client, "admin@example.com")
    for url in rx.ALL_URLS:
        for query in ("", _SEPT, f"group={w['gp']}", "group=bad"):
            client.get(f"{url}?{query}")
            client.head(f"{url}?{query}")
    with app.app_context():
        assert rx.everything() == before
