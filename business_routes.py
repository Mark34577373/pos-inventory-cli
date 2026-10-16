"""Private expense ledger and period-based operational financial reports."""

import csv
import io
import secrets

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for

from config import business_now
from inventory_routes import pagination_links
from services.analytics import ReportLimitError, resolve_filters
from services.expenses import (
    CATEGORIES,
    FIELDS,
    correct_expense,
    create_expense,
    expense_filters,
    expense_report,
    get_expense,
)
from services.financials import financial_report
from services.refunds import valid_id
from services.sales_exports import amount, spreadsheet_text
from services.settings import get_settings

bp = Blueprint("business", __name__)


@bp.errorhandler(ValueError)
def invalid(error):
    return render_template("error.html", message=str(error)), 400


@bp.errorhandler(LookupError)
def missing(error):
    return render_template("error.html", message="Record not found."), 404


@bp.errorhandler(ReportLimitError)
def too_large(error):
    return render_template("error.html", message=str(error)), 413


def submitted_fields(extra=()):
    if set(request.form) - FIELDS - {"key", "csrf_token"} - set(extra):
        raise ValueError("Unsupported expense fields.")
    fields = {key: request.form.get(key, "") for key in FIELDS}
    supplier = fields["supplier_id"]
    if supplier:
        try:
            supplier = int(supplier)
            if not valid_id(supplier):
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("Choose a valid supplier ID.") from None
    fields["supplier_id"] = supplier or None
    return fields


@bp.get("/expenses")
def expenses():
    filters = expense_filters(request.args)
    data = expense_report(filters)
    pagination = pagination_links("business.expenses", filters, data)
    return render_template("expenses.html", categories=CATEGORIES, pagination=pagination, **data)


@bp.route("/expenses/new", methods=["GET", "POST"])
@bp.route("/expenses/<int:expense_id>/correct", methods=["GET", "POST"])
def expense_form(expense_id=None):
    original = get_expense(expense_id) if expense_id is not None else None
    if (
        request.method == "GET"
        and original
        and (original["entry_kind"] != "expense" or original["reversed_by"])
    ):
        raise ValueError("Only an unreversed expense can be corrected.")
    values = dict(
        original or {},
        amount=amount(original["amount_cents"]) if original else "",
        expense_date=business_now().date().isoformat(),
    )
    key = secrets.token_urlsafe(32)
    if request.method == "POST":
        try:
            fields = submitted_fields({"reason"})
            if original:
                saved = correct_expense(
                    expense_id, fields, request.form.get("reason", ""), request.form.get("key", "")
                )
            else:
                saved = create_expense(fields, request.form.get("key", ""))
        except ValueError as error:
            return render_template(
                "expense_form.html",
                values=request.form,
                original=original,
                categories=CATEGORIES,
                key=request.form.get("key", "") or key,
                error=str(error),
            ), 400
        flash("Expense ledger entry recorded. Original records are preserved.", "success")
        return redirect(url_for("business.expense_detail", expense_id=saved))
    return render_template(
        "expense_form.html", values=values, original=original, categories=CATEGORIES, key=key
    )


@bp.get("/expenses/<int:expense_id>")
def expense_detail(expense_id):
    return render_template(
        "expense_detail.html", expense=get_expense(expense_id), key=secrets.token_urlsafe(32)
    )


@bp.post("/expenses/<int:expense_id>/reverse")
def expense_reverse(expense_id):
    if set(request.form) - {"csrf_token", "key", "reason"}:
        raise ValueError("Unsupported reversal fields.")
    saved = correct_expense(
        expense_id,
        {},
        request.form.get("reason", ""),
        request.form.get("key", ""),
        reverse_only=True,
    )
    return redirect(url_for("business.expense_detail", expense_id=saved))


def csv_response(output, filename):
    return Response(
        "\ufeff" + output.getvalue(),
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )


@bp.get("/expenses/export.csv")
def expense_export():
    data = expense_report(expense_filters(request.args))
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(
        [
            "Entry ID",
            "Business date",
            "Category",
            "Description",
            "Entry kind",
            "Amount USD (signed)",
            "Reverses entry",
        ]
    )
    for row in data["rows"]:
        writer.writerow(
            [
                row["id"],
                row["expense_date"],
                row["category"],
                spreadsheet_text(row["description"]),
                row["entry_kind"],
                amount(row["signed_cents"]),
                row["reverses_id"] or "",
            ]
        )
    writer.writerow(["Total expenses USD", amount(data["total_cents"])])
    return csv_response(output, "expense-ledger")


def financial_data():
    if set(request.args) - {"preset", "start_date", "end_date"}:
        raise ValueError("Financial reports support period filters only.")
    args = request.args.to_dict()
    if not any(key in args for key in ("preset", "start_date", "end_date")):
        args["preset"] = get_settings()["report_period"]
    return financial_report(resolve_filters(args))


def metrics(data):
    summary = data["sales"]["summary"]
    return [
        ("Recorded gross sales (after discounts, tax included)", summary["revenue_cents"]),
        ("Original merchandise (before discounts/tax)", data["original_merchandise_cents"]),
        ("Before-tax discounts", data["discount_cents"]),
        ("Refunds (tax included, refund date)", summary["refunded_cents"]),
        ("Net sales (tax included)", summary["net_sales_cents"]),
        ("Net verified sales tax", data["net_tax_cents"] if data["tax_complete"] else None),
        ("Net revenue excluding tax", data["net_revenue_cents"]),
        ("Known-snapshot estimated COGS (may be incomplete)", data["known_cogs_cents"]),
        ("Estimated gross profit", data["gross_profit_cents"]),
        ("Net operating expenses", data["expenses"]["total_cents"]),
        ("Estimated operating result", data["operating_result_cents"]),
        ("Purchases ordered (PO creation date, not payment)", data["purchases"]["ordered_cents"]),
        ("Purchases received (receiving date, not payment)", data["purchases"]["received_cents"]),
    ]


@bp.get("/financials")
def financials():
    data = financial_data()
    return render_template("financials.html", metrics=metrics(data), **data)


@bp.get("/financials/export/<kind>.csv")
def financial_export(kind):
    if kind not in ("overview", "products", "categories", "periods"):
        abort(404)
    data = financial_data()
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(
        [
            "Start date",
            data["filters"].start.isoformat(),
            "End date",
            data["filters"].end.isoformat(),
        ]
    )
    writer.writerow(
        [
            "Cost method",
            "Manual reference acquisition-cost snapshots; estimates only; unknown historical costs are not backfilled",
        ]
    )
    writer.writerow(
        ["Unknown cost units", data["unknown_cost_units"], "Tax complete", data["tax_complete"]]
    )
    if kind == "overview":
        writer.writerow(["Metric", "USD (blank = incomplete)"])
        for label, value in metrics(data):
            writer.writerow([label, "" if value is None else amount(value)])
    else:
        writer.writerow(
            [
                "ID",
                "Name / period",
                "Merchandise revenue excluding tax USD (after discounts/refunds)",
                "Known-snapshot estimated COGS USD",
                "Unknown cost units",
                "Estimated gross profit USD (blank = incomplete)",
            ]
        )
        for row in data[kind]:
            writer.writerow(
                [
                    row["id"] if row["id"] is not None else "",
                    spreadsheet_text(row["name"]),
                    amount(row["revenue_cents"]),
                    amount(row["cogs_cents"]),
                    row["unknown_units"],
                    "" if row["gross_profit_cents"] is None else amount(row["gross_profit_cents"]),
                ]
            )
    return csv_response(output, f"business-{kind}")
