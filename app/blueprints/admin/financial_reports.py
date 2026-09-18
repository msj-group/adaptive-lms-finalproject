"""Administrator financial reports, with CSV and PDF exports (Phase 5 / M08).

Ten read-only GET rules, and nothing else::

    GET  /admin/financial-reports                              the three reports
    GET  /admin/financial-reports/collections                  collections (HTML)
    GET  /admin/financial-reports/collections.csv              ... as CSV
    GET  /admin/financial-reports/collections.pdf              ... as PDF
    GET  /admin/financial-reports/outstanding-invoices         outstanding invoices
    GET  /admin/financial-reports/outstanding-invoices.csv
    GET  /admin/financial-reports/outstanding-invoices.pdf
    GET  /admin/financial-reports/exceptions                   operational exceptions
    GET  /admin/financial-reports/exceptions.csv
    GET  /admin/financial-reports/exceptions.pdf

**Read-only.** No route creates, edits, confirms, rejects, reverses, cancels or
otherwise changes a financial row, writes an audit event or an export history,
stores a file, or commits anything. A POST, PUT, PATCH or DELETE is a 405, so
no CSRF token is needed or accepted.

**Administrator only.** Every rule is gated by ``roles_required``; a suspended
account no longer loads. There is no Student, Teacher, Researcher or public
report.

**One report per request.** The query arguments are validated once
(``app/services/financial_reports.py``) -- the ``group`` public id and, for
collections only, the ``start`` / ``end`` dates -- and the one report built
from them is rendered as HTML, CSV or PDF. An invalid filter is a 400 page
naming the problem; nothing is normalized into another report. Export links
carry the validated filters, so a download states what the page showed. The
HTML page may show one page of a long table; CSV and PDF always hold every row.

**Every response** -- page, download, refusal, redirect or error -- carries
``Cache-Control: private, no-store``, ``Vary: Cookie`` and
``X-Content-Type-Options: nosniff``. Downloads are attachments with fixed
filenames. Nothing a report shows is logged.
"""

from functools import wraps

from flask import (
    Response,
    after_this_request,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from app.blueprints.admin import admin_bp
from app.blueprints.admin.fee_plans import _tz_name
from app.models import UserRole
from app.security.decorators import roles_required
from app.services import financial_reports as reports
from app.services.fee_plan_queries import normalize_page
from app.services.financial_report_exports import (
    CSV_CONTENT_TYPE,
    PDF_CONTENT_TYPE,
    PdfTextUnsupported,
    display_text,
    generated_text,
    is_numeric,
    render_csv,
    render_pdf,
)

_ADMINISTRATOR = UserRole.ADMINISTRATOR.value

_HTML, _CSV, _PDF = "html", "csv", "pdf"

#: The HTML endpoint of each report; ``_csv`` and ``_pdf`` are its exports.
_ENDPOINTS = {
    reports.COLLECTIONS: "admin.financial_report_collections",
    reports.OUTSTANDING: "admin.financial_report_outstanding",
    reports.EXCEPTIONS: "admin.financial_report_exceptions",
}

#: Fixed download names: no date, filter or stored text ever reaches a header.
_FILENAMES = {
    reports.COLLECTIONS: "collections-report",
    reports.OUTSTANDING: "outstanding-invoices-report",
    reports.EXCEPTIONS: "operational-exceptions-report",
}

_PDF_UNSUPPORTED_MESSAGE = (
    "The PDF export can only show Latin-script text, and this report holds characters it "
    "cannot show (for example Arabic letters in a name). No PDF was produced and nothing was "
    "left out: use the CSV export or this page, which show every character."
)


def _report_response(view):
    """``Cache-Control: private, no-store``, ``Vary: Cookie`` and
    ``X-Content-Type-Options: nosniff`` on every response of `view` --
    including a 403, a login redirect or an error page, because the headers
    are attached before the role check runs."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        @after_this_request
        def _headers(response):
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.vary.add("Cookie")
            return response

        return view(*args, **kwargs)

    return wrapped


def _download(body, content_type, filename):
    response = Response(body, content_type=content_type)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _section_views(report, endpoint, filter_args, page):
    """What the page renders for each section: display text only, one page
    of a paginated table, and the totals of every row."""
    views = []
    for section in report.sections:
        rows = section.rows
        pagination = None
        if section.paginated and len(rows) > reports.HTML_PAGE_SIZE:
            pages = -(-len(rows) // reports.HTML_PAGE_SIZE)
            current = page if page <= pages else 1
            start = (current - 1) * reports.HTML_PAGE_SIZE
            rows = rows[start : start + reports.HTML_PAGE_SIZE]
            pagination = {
                "page": current,
                "pages": pages,
                "first": start + 1,
                "last": start + len(rows),
                "total": len(section.rows),
                "prev_url": url_for(endpoint, page=current - 1, **filter_args)
                if current > 1
                else None,
                "next_url": url_for(endpoint, page=current + 1, **filter_args)
                if current < pages
                else None,
            }
        views.append(
            {
                "key": section.key,
                "title": section.title,
                "description": section.description,
                "columns": [
                    {"label": column.label, "numeric": is_numeric(column)}
                    for column in section.columns
                ],
                "rows": [
                    [display_text(row.get(column.key)) for column in section.columns]
                    for row in rows
                ],
                "row_count": len(section.rows),
                "footer": [display_text(section.footer.get(column.key)) for column in section.columns]
                if section.footer
                else None,
                "empty_text": section.empty_text,
                "pagination": pagination,
            }
        )
    return views


def _render(report_key, report=None, filters=None, errors=(), status=200):
    spec = reports.REPORT_SPECS[report_key]
    endpoint = _ENDPOINTS[report_key]
    filter_args = filters.query_args() if filters is not None else {}
    if filters is not None:
        selected_group = filters.group.public_id if filters.group is not None else ""
        start_value = filters.start.isoformat() if filters.start is not None else ""
        end_value = filters.end.isoformat() if filters.end is not None else ""
    else:
        # The submitted text, shown back (escaped) so it can be corrected.
        selected_group = request.args.get(reports.GROUP_PARAM, "")
        start_value = request.args.get(reports.START_PARAM, "")
        end_value = request.args.get(reports.END_PARAM, "")
    context = {
        "spec": spec,
        "endpoint": endpoint,
        "report": report,
        "errors": list(errors),
        "group_choices": reports.group_choices(),
        "selected_group": selected_group,
        "start_value": start_value,
        "end_value": end_value,
        "all_groups_text": reports.ALL_GROUPS_TEXT,
        "tz_name": _tz_name(),
    }
    if report is not None:
        context.update(
            generated=generated_text(report),
            sections=_section_views(
                report, endpoint, filter_args, normalize_page(request.args.get("page"))
            ),
            csv_url=url_for(endpoint + "_csv", **filter_args),
            pdf_url=url_for(endpoint + "_pdf", **filter_args),
        )
    return render_template("admin/financial_reports/report.html", **context), status


def _serve(report_key, output):
    """Validate the filters once, build the one report, and render it as
    `output`."""
    tz_name = _tz_name()
    moment = reports.report_moment()
    filters, errors = reports.parse_report_filters(report_key, request.args, tz_name, moment)
    if errors:
        return _render(report_key, errors=errors, status=400)
    report = reports.build_report(report_key, filters, tz_name, moment)
    if output == _CSV:
        return _download(render_csv(report), CSV_CONTENT_TYPE, f"{_FILENAMES[report_key]}.csv")
    if output == _PDF:
        try:
            body = render_pdf(report)
        except PdfTextUnsupported:
            flash(_PDF_UNSUPPORTED_MESSAGE, "warning")
            return redirect(url_for(_ENDPOINTS[report_key], **filters.query_args()))
        return _download(body, PDF_CONTENT_TYPE, f"{_FILENAMES[report_key]}.pdf")
    return _render(report_key, report=report, filters=filters)


@admin_bp.get("/financial-reports")
@_report_response
@roles_required(_ADMINISTRATOR)
def financial_reports_index():
    return render_template(
        "admin/financial_reports/index.html",
        specs=[reports.REPORT_SPECS[key] for key in _ENDPOINTS],
        endpoints=_ENDPOINTS,
        notice=reports.NOTICE,
    )


@admin_bp.get("/financial-reports/collections")
@_report_response
@roles_required(_ADMINISTRATOR)
def financial_report_collections():
    return _serve(reports.COLLECTIONS, _HTML)


@admin_bp.get("/financial-reports/collections.csv")
@_report_response
@roles_required(_ADMINISTRATOR)
def financial_report_collections_csv():
    return _serve(reports.COLLECTIONS, _CSV)


@admin_bp.get("/financial-reports/collections.pdf")
@_report_response
@roles_required(_ADMINISTRATOR)
def financial_report_collections_pdf():
    return _serve(reports.COLLECTIONS, _PDF)


@admin_bp.get("/financial-reports/outstanding-invoices")
@_report_response
@roles_required(_ADMINISTRATOR)
def financial_report_outstanding():
    return _serve(reports.OUTSTANDING, _HTML)


@admin_bp.get("/financial-reports/outstanding-invoices.csv")
@_report_response
@roles_required(_ADMINISTRATOR)
def financial_report_outstanding_csv():
    return _serve(reports.OUTSTANDING, _CSV)


@admin_bp.get("/financial-reports/outstanding-invoices.pdf")
@_report_response
@roles_required(_ADMINISTRATOR)
def financial_report_outstanding_pdf():
    return _serve(reports.OUTSTANDING, _PDF)


@admin_bp.get("/financial-reports/exceptions")
@_report_response
@roles_required(_ADMINISTRATOR)
def financial_report_exceptions():
    return _serve(reports.EXCEPTIONS, _HTML)


@admin_bp.get("/financial-reports/exceptions.csv")
@_report_response
@roles_required(_ADMINISTRATOR)
def financial_report_exceptions_csv():
    return _serve(reports.EXCEPTIONS, _CSV)


@admin_bp.get("/financial-reports/exceptions.pdf")
@_report_response
@roles_required(_ADMINISTRATOR)
def financial_report_exceptions_pdf():
    return _serve(reports.EXCEPTIONS, _PDF)
