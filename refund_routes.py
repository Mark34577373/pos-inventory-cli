"""Simulated refund preview/confirmation; default app authentication and CSRF."""

import secrets

from flask import Blueprint, abort, current_app, g, redirect, render_template, request, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

from services.analytics import ReportLimitError
from services.auth import consume_rate_limit
from services.refunds import get_refund, get_sale, preview_refund, process_refund

bp = Blueprint("refunds", __name__)


def serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="tccp-refund-confirmation-v1")


@bp.route("/transactions/<int:transaction_id>/refund", methods=["GET", "POST"])
def refund_form(transaction_id):
    try:
        state = get_sale(transaction_id)
    except LookupError:
        abort(404)
    except ReportLimitError as error:
        abort(413, description=str(error))
    if request.method == "GET":
        return render_template("refund.html", **state)
    if not consume_rate_limit(current_app.secret_key, "refund", f"user:{g.user['id']}"):
        abort(429, description="Too many refund attempts. Try again in 15 minutes.")
    try:
        if request.form.get("confirmation"):
            try:
                payload = serializer().loads(request.form["confirmation"], max_age=3600)
            except BadSignature:
                raise ValueError("Refund confirmation expired. Preview it again.") from None
            if (
                payload["user"] != g.user["id"]
                or payload["store"] != g.store["id"]
                or payload["sale"] != transaction_id
            ):
                abort(403)
            refund_id = process_refund(
                transaction_id,
                {int(key): qty for key, qty in payload["quantities"].items()},
                payload["reason"],
                payload["restock"],
                payload["key"],
                expected_version=payload["version"],
                expected_total=payload["total_cents"],
            )
            return redirect(url_for("refunds.detail", refund_id=refund_id))
        quantities = {}
        if request.form.get("mode") == "full":
            quantities = {
                line["id"]: line["remaining_quantity"]
                for line in state["items"]
                if line["remaining_quantity"]
            }
        else:
            for field, value in request.form.items():
                if field.startswith("qty_"):
                    if not value.isascii() or not value.isdigit() or len(value) > 7:
                        raise ValueError(
                            "Enter whole-number quantities, using zero to omit an item."
                        )
                    if int(value):
                        try:
                            line_id = int(field[4:])
                        except ValueError:
                            raise ValueError("Choose eligible sale items.") from None
                        quantities[line_id] = int(value)
        quote = preview_refund(
            transaction_id,
            quantities,
            request.form.get("reason", ""),
            request.form.get("restock") == "on",
        )
        payload = {
            key: quote[key] for key in ("quantities", "reason", "restock", "version", "total_cents")
        }
        payload.update(
            user=g.user["id"],
            store=g.store["id"],
            sale=transaction_id,
            key=secrets.token_urlsafe(32),
        )
        return render_template(
            "refund.html", **state, quote=quote, confirmation=serializer().dumps(payload)
        )
    except (ValueError, PermissionError) as error:
        return render_template("refund.html", **state, error=str(error)), 400


@bp.get("/refunds/<int:refund_id>")
def detail(refund_id):
    try:
        data = get_refund(refund_id)
    except LookupError:
        abort(404)
    return render_template("refund_detail.html", **data)
