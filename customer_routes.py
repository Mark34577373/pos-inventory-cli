"""Customer pages use the application's default authentication and CSRF guard."""

import csv
import io

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from inventory_routes import pagination_links
from services.analytics import ReportLimitError
from services.customers import (
    FIELDS,
    customer_history,
    deactivate_customer,
    directory_filters,
    get_customer,
    list_customers,
    save_customer,
)
from services.sales_exports import amount, spreadsheet_text

bp = Blueprint("customers", __name__)


def form_fields():
    if set(request.form) - FIELDS - {"csrf_token"}:
        raise ValueError("Unsupported customer fields.")
    return {key: request.form.get(key, "") for key in FIELDS}


@bp.get("/customers")
def directory():
    try:
        filters = directory_filters(request.args)
        data = list_customers(filters)
    except ValueError as error:
        abort(400, description=str(error))
    return render_template(
        "customers.html",
        filters=filters,
        pagination=pagination_links("customers.directory", filters, data),
        **data,
    )


@bp.route("/customers/new", methods=["GET", "POST"])
@bp.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
def edit(customer_id=None):
    try:
        customer = get_customer(customer_id) if customer_id is not None else {}
        if request.method == "POST":
            try:
                saved_id = save_customer(form_fields(), customer_id)
            except ValueError as error:
                return render_template(
                    "customer_form.html", customer=request.form, error=str(error)
                ), 400
            flash("Customer saved.", "success")
            return redirect(url_for("customers.detail", customer_id=saved_id))
    except LookupError:
        abort(404)
    return render_template("customer_form.html", customer=customer)


@bp.get("/customers/<int:customer_id>")
def detail(customer_id):
    try:
        data = customer_history(customer_id)
    except LookupError:
        abort(404)
    except ReportLimitError as error:
        abort(413, description=str(error))
    return render_template("customer_detail.html", **data)


@bp.get("/customers/export.csv")
def export():
    from services.customers import export_customers

    try:
        rows = export_customers(directory_filters(request.args))
    except ReportLimitError as error:
        abort(413, description=str(error))
    except ValueError as error:
        abort(400, description=str(error))
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(
        [
            "Customer ID",
            "Display name",
            "Active",
            "Transactions",
            "Purchases USD (tax included)",
            "Refunds USD (tax included)",
            "Last purchase",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row["id"],
                spreadsheet_text(row["name"]),
                row["is_active"],
                row["count"],
                amount(row["purchases"]),
                amount(row["refunds"]),
                row["last_purchase"] or "",
            ]
        )
    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="customer-summaries.csv"'},
    )


@bp.post("/customers/<int:customer_id>/deactivate")
def deactivate(customer_id):
    if set(request.form) - {"csrf_token"}:
        abort(400)
    try:
        deactivate_customer(customer_id)
    except LookupError:
        abort(404)
    flash("Customer deactivated. Purchase history is preserved.", "success")
    return redirect(url_for("customers.detail", customer_id=customer_id))


@bp.post("/sale/customer")
def select_customer():
    if set(request.form) - {"csrf_token", "customer_id"}:
        abort(400)
    value = request.form.get("customer_id", "")
    if value:
        try:
            if not value.isascii() or not value.isdigit():
                raise LookupError
            customer = get_customer(int(value), active=True)
        except LookupError:
            abort(404)
        session["sale_customer_id"] = customer["id"]
    else:
        session.pop("sale_customer_id", None)
    return redirect(url_for("sale"))
