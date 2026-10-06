"""HTTP rendering/export of general-account reports."""
from flask import flash, make_response, redirect, render_template, request, url_for
from app.services.general_financial_reports import build_general_report
from app.services.financial_report_exports import display_text, render_csv, render_pdf, PdfGlyphUnavailable


def reports_page(key="accounts", output="html"):
    try:
        report, page, has_next = build_general_report(key, request.args, export=output != "html")
        if output != "html":
            body = render_csv(report) if output == "csv" else render_pdf(report)
            response = make_response(body)
            response.headers["Content-Type"] = "text/csv; charset=utf-8" if output == "csv" else "application/pdf"
            response.headers["Content-Disposition"] = f'attachment; filename="student-{key}.{output}"'
            return response
    except (ValueError, PdfGlyphUnavailable) as exc:
        flash(str(exc) if isinstance(exc, ValueError) else "This text cannot be rendered in the report font. Use CSV or the page.", "danger")
        return redirect(url_for("admin.financial_report_outstanding" if key == "accounts" else "admin.financial_report_exceptions" if key == "exceptions" else "admin.financial_report_collections"))
    return render_template("admin/general_finance/report.html", report=report, page=page, has_next=has_next,
        display=display_text, key=key, filters=request.args)
