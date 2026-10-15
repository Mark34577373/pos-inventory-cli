"""Private sales analytics and downloads use the app's default auth guard."""

from flask import Blueprint, Response, abort, render_template, request, url_for

from services.analytics import ReportLimitError, build_analytics, resolve_filters
from services.sales_exports import KINDS, create_csv

bp = Blueprint("analytics", __name__, url_prefix="/analytics")


@bp.get("")
def overview():
    try:
        filters = resolve_filters(request.args)
        data = build_analytics(filters)
    except ReportLimitError as error:
        return render_template("error.html", message=str(error)), 413
    except ValueError as error:
        return render_template("error.html", message=str(error)), 400
    export_links = {
        kind: url_for("analytics.download", kind=kind, **filters.query_args()) for kind in KINDS
    }
    return render_template("analytics.html", **data, export_links=export_links)


@bp.get("/export/<kind>.csv")
def download(kind):
    if kind not in KINDS:
        abort(404)
    try:
        filters = resolve_filters(request.args)
        data = build_analytics(filters)
    except ReportLimitError as error:
        return render_template("error.html", message=str(error)), 413
    except ValueError as error:
        return render_template("error.html", message=str(error)), 400
    response = Response(create_csv(data, kind), content_type="text/csv; charset=utf-8")
    response.headers["Content-Disposition"] = (
        f'attachment; filename="tccp-{kind}-{filters.start.isoformat()}-{filters.end.isoformat()}.csv"'
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response
