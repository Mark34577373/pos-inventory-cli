"""Private purchasing workspace; all writes pass the shared auth/CSRF/demo guard."""

import csv
import io
import secrets
from contextlib import closing

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for

from database.database import get_store_connection
from inventory_routes import pagination_links
from services.analytics import ReportLimitError
from services.customers import directory_filters
from services.inventory import find_product_by_id
from services.purchasing import (
    SUPPLIER_FIELDS,
    create_order,
    deactivate_supplier,
    edit_order,
    get_order,
    get_supplier,
    purchase_report,
    receive_order,
    save_order_line,
    save_product_cost,
    save_supplier,
    supplier_profile,
    suppliers,
    transition_order,
)
from services.refunds import valid_id
from services.sales_exports import amount, spreadsheet_text
from services.validation import visible_text

bp = Blueprint("purchasing", __name__)


@bp.errorhandler(ValueError)
def invalid(error):
    return render_template("error.html", message=str(error)), 400


@bp.errorhandler(LookupError)
def missing(error):
    return render_template("error.html", message="Record not found."), 404


@bp.errorhandler(ReportLimitError)
def too_large(error):
    return render_template("error.html", message=str(error)), 413


def fields(allowed):
    if set(request.form) - set(allowed) - {"csrf_token"}:
        abort(400, description="Unsupported form fields.")


def identifier(value):
    try:
        if not str(value).isascii() or not str(value).isdigit():
            raise ValueError
        result = int(value)
        if not valid_id(result):
            raise ValueError
        return result
    except (ValueError, TypeError):
        raise ValueError("Choose a valid record identifier.") from None


@bp.get("/suppliers")
def directory():
    filters = directory_filters(request.args)
    data = suppliers(filters)
    return render_template(
        "suppliers.html",
        filters=filters,
        pagination=pagination_links("purchasing.directory", filters, data),
        **data,
    )


@bp.route("/suppliers/new", methods=["GET", "POST"])
@bp.route("/suppliers/<int:supplier_id>/edit", methods=["GET", "POST"])
def supplier_edit(supplier_id=None):
    supplier = get_supplier(supplier_id) if supplier_id is not None else {}
    if request.method == "POST":
        fields(SUPPLIER_FIELDS)
        try:
            saved = save_supplier(
                {key: request.form.get(key, "") for key in SUPPLIER_FIELDS}, supplier_id
            )
        except ValueError as error:
            return render_template(
                "supplier_form.html", supplier=request.form, error=str(error)
            ), 400
        return redirect(url_for("purchasing.supplier_detail", supplier_id=saved))
    return render_template("supplier_form.html", supplier=supplier)


@bp.get("/suppliers/<int:supplier_id>")
def supplier_detail(supplier_id):
    return render_template("supplier_detail.html", **supplier_profile(supplier_id))


@bp.post("/suppliers/<int:supplier_id>/deactivate")
def supplier_deactivate(supplier_id):
    fields([])
    deactivate_supplier(supplier_id)
    return redirect(url_for("purchasing.supplier_detail", supplier_id=supplier_id))


@bp.route("/inventory/<int:product_id>/cost", methods=["GET", "POST"])
def product_cost(product_id):
    product = find_product_by_id(product_id)
    if product is None:
        abort(404)
    if request.method == "POST":
        fields(["unit_cost", "supplier_id", "supplier_sku"])
        supplier_id = (
            identifier(request.form["supplier_id"]) if request.form.get("supplier_id") else None
        )
        save_product_cost(
            product_id,
            request.form.get("unit_cost", ""),
            supplier_id,
            request.form.get("supplier_sku", ""),
        )
        flash(
            "Future-sale reference cost saved. Historical costs and selling price are unchanged.",
            "success",
        )
        return redirect(url_for("purchasing.product_cost", product_id=product_id))
    supplier = get_supplier(product["supplier_id"]) if product["supplier_id"] else None
    return render_template(
        "product_cost.html",
        product=product,
        supplier=supplier,
        cost_value=amount(product["unit_cost_cents"])
        if product["unit_cost_cents"] is not None
        else "",
    )


@bp.get("/purchases")
def orders():
    report = purchase_report(request.args.get("start") or None, request.args.get("end") or None)
    q = visible_text(request.args.get("q", ""), "Search", 100, optional=True)
    status = request.args.get("status", "all")
    if status not in ("all", "draft", "ordered", "partially_received", "received", "cancelled"):
        raise ValueError("Choose a supported purchase order status.")
    page = identifier(request.args.get("page", "1"))
    if page > 100000:
        raise ValueError("Choose a valid page.")
    matching = [
        row
        for row in report["orders"]
        if (status == "all" or row["status"] == status)
        and (not q or q.casefold() in row["supplier_name"].casefold() or q == str(row["id"]))
    ]
    count = len(matching)
    pages = max(1, (count + 19) // 20)
    args = dict(
        q=q, status=status, start=request.args.get("start", ""), end=request.args.get("end", "")
    )
    pagination = {
        "previous": url_for("purchasing.orders", **args, page=page - 1) if page > 1 else None,
        "next": url_for("purchasing.orders", **args, page=page + 1) if page < pages else None,
    }
    return render_template(
        "purchases.html",
        report=report,
        rows=matching[(page - 1) * 20 : page * 20],
        count=count,
        page=page,
        pages=pages,
        pagination=pagination,
        filters=args,
    )


@bp.route("/purchases/new", methods=["GET", "POST"])
def order_new():
    if request.method == "POST":
        fields(["supplier_id", "expected_date", "notes"])
        saved = create_order(
            identifier(request.form.get("supplier_id")),
            request.form.get("expected_date", ""),
            request.form.get("notes", ""),
        )
        return redirect(url_for("purchasing.order_detail", order_id=saved))
    return render_template("purchase_new.html")


@bp.get("/purchases/<int:order_id>")
def order_detail(order_id):
    return render_template(
        "purchase_detail.html", key=secrets.token_urlsafe(32), **get_order(order_id)
    )


@bp.post("/purchases/<int:order_id>/edit")
def order_edit(order_id):
    fields(["expected_date", "notes"])
    edit_order(order_id, request.form.get("expected_date", ""), request.form.get("notes", ""))
    return redirect(url_for("purchasing.order_detail", order_id=order_id))


@bp.post("/purchases/<int:order_id>/line")
def order_line(order_id):
    fields(["product_id", "quantity", "unit_cost", "remove"])
    if request.form.get("remove", "") not in ("", "1"):
        raise ValueError("Choose a valid draft-line action.")
    save_order_line(
        order_id,
        identifier(request.form.get("product_id")),
        request.form.get("quantity", ""),
        request.form.get("unit_cost", ""),
        remove=request.form.get("remove") == "1",
    )
    return redirect(url_for("purchasing.order_detail", order_id=order_id))


@bp.post("/purchases/<int:order_id>/action")
def order_action(order_id):
    fields(["action"])
    transition_order(order_id, request.form.get("action", ""))
    return redirect(url_for("purchasing.order_detail", order_id=order_id))


@bp.post("/purchases/<int:order_id>/receive")
def order_receive(order_id):
    quantities = {}
    for key, value in request.form.items():
        if key in ("csrf_token", "key"):
            continue
        if not key.startswith("qty_"):
            abort(400)
        if value and value != "0":
            quantities[identifier(key[4:])] = value
    receipt = receive_order(order_id, quantities, request.form.get("key", ""))
    flash(f"Receiving #{receipt} recorded. Stock and alerts updated.", "success")
    return redirect(url_for("purchasing.order_detail", order_id=order_id))


@bp.get("/purchases/export.csv", endpoint="order_export")
@bp.get("/suppliers/export.csv", endpoint="supplier_export")
def export():
    report = purchase_report(request.args.get("start") or None, request.args.get("end") or None)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    if request.path.startswith("/suppliers"):
        writer.writerow(
            [
                "Supplier ID",
                "Current supplier name",
                "Ordered USD (PO creation date)",
                "Received USD (receiving date)",
            ]
        )
        filters = directory_filters(request.args)
        ordering = {"name": "name COLLATE NOCASE,id", "newest": "id DESC", "oldest": "id"}[
            filters["sort"]
        ]
        with closing(get_store_connection()) as conn:
            source = [
                dict(row)
                for row in conn.execute(
                    f"""SELECT id,name,is_active FROM suppliers
                WHERE store_id=authorized_store_id() AND (?='all' OR is_active=?)
                AND (?='' OR instr(lower(name),lower(?))>0 OR instr(lower(contact),lower(?))>0)
                ORDER BY {ordering} LIMIT 10001""",
                    (
                        filters["state"],
                        int(filters["state"] == "active"),
                        filters["q"],
                        filters["q"],
                        filters["q"],
                    ),
                )
            ]
        if len(source) > 10000:
            raise ReportLimitError("Supplier export exceeds the 10,000-supplier limit.")
        totals = {row["id"]: row for row in report["supplier_totals"]}
        for supplier in source:
            row = totals.get(supplier["id"], dict(ordered_cents=0, received_cents=0))
            writer.writerow(
                [
                    supplier["id"],
                    spreadsheet_text(supplier["name"]),
                    amount(row["ordered_cents"]),
                    amount(row["received_cents"]),
                ]
            )
    else:
        q = visible_text(request.args.get("q", ""), "Search", 100, optional=True)
        status = request.args.get("status", "all")
        if status not in ("all", "draft", "ordered", "partially_received", "received", "cancelled"):
            raise ValueError("Choose a supported purchase order status.")
        writer.writerow(
            [
                "PO ID",
                "Supplier snapshot",
                "Created business date",
                "Status",
                "Order value USD (not payment)",
            ]
        )
        for row in report["orders"]:
            if (status == "all" or row["status"] == status) and (
                not q or q.casefold() in row["supplier_name"].casefold() or q == str(row["id"])
            ):
                writer.writerow(
                    [
                        row["id"],
                        spreadsheet_text(row["supplier_name"]),
                        row["business_date"],
                        row["status"],
                        amount(row["total_cents"]),
                    ]
                )
    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="purchasing-summary.csv"'},
    )
