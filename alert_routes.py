"""Server-rendered notification center under the default auth/CSRF guard."""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from services.alerts import get_alert, list_alerts, mark_page_read, recommendation, update_alert
from services.analytics import ReportLimitError

bp = Blueprint("alerts", __name__, url_prefix="/notifications")


@bp.get("")
def overview():
    try:
        data = list_alerts(request.args)
    except ValueError as error:
        abort(400, description=str(error))

    def page_url(page):
        return url_for("alerts.overview", **dict(data["filters"], page=page))

    return render_template("notifications.html", **data, page_url=page_url)


@bp.get("/<int:alert_id>")
def detail(alert_id):
    try:
        alert = get_alert(alert_id)
        estimate = recommendation(alert["product_id"])
    except LookupError:
        abort(404)
    except ReportLimitError as error:
        abort(413, description=str(error))
    except ValueError as error:
        abort(400, description=str(error))
    return render_template("notification_detail.html", alert=alert, estimate=estimate)


@bp.post("/<int:alert_id>/<action>")
def action(alert_id, action):
    if any(key in request.form for key in ("store_id", "user_id", "product_id")):
        abort(400, description="Ownership fields are not accepted.")
    try:
        update_alert(alert_id, action)
    except LookupError:
        abort(404)
    except ValueError as error:
        abort(400, description=str(error))
    flash("Notification updated.", "success")
    return redirect(url_for("alerts.detail", alert_id=alert_id))


@bp.post("/read-page")
def read_page():
    if any(key in request.form for key in ("store_id", "user_id", "product_id")):
        abort(400)
    try:
        # One comma-delimited field respects the application's duplicate-field guard.
        mark_page_read(request.form.get("ids", "").split(","))
    except LookupError:
        abort(404)
    except ValueError as error:
        abort(400, description=str(error))
    flash("Selected notifications marked as read.", "success")
    return redirect(url_for("alerts.overview"))
