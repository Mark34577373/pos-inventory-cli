"""Day 15 forms, protected by the application's default auth/CSRF guard."""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from services.inventory import find_product_by_id
from services.inventory_management import (
    ADJUSTMENT_TYPES,
    adjust_stock,
    delete_category,
    history_filters,
    list_categories,
    save_category,
    stock_history,
)

bp = Blueprint("inventory_tools", __name__, url_prefix="/inventory")


@bp.route("/categories", methods=["GET", "POST"])
def categories():
    if request.method == "POST":
        try:
            save_category(request.form.get("name", ""))
        except ValueError as error:
            return render_template(
                "categories.html", categories=list_categories(), error=str(error)
            ), 400
        flash("Category created.", "success")
        return redirect(url_for("inventory_tools.categories"))
    return render_template("categories.html", categories=list_categories())


@bp.post("/categories/<int:category_id>/rename")
def rename_category(category_id):
    try:
        save_category(request.form.get("name", ""), category_id)
    except ValueError as error:
        return render_template(
            "categories.html", categories=list_categories(), error=str(error)
        ), 400
    flash("Category renamed.", "success")
    return redirect(url_for("inventory_tools.categories"))


@bp.post("/categories/<int:category_id>/remove")
def remove_category(category_id):
    try:
        delete_category(category_id)
    except ValueError as error:
        return render_template(
            "categories.html", categories=list_categories(), error=str(error)
        ), 400
    flash("Category removed. Its products are now Uncategorized.", "success")
    return redirect(url_for("inventory_tools.categories"))


@bp.route("/adjust/<int:product_id>", methods=["GET", "POST"])
def adjust(product_id):
    product = find_product_by_id(product_id)
    if product is None:
        abort(404)
    if request.method == "POST":
        try:
            kind = request.form.get("type", "")
            if kind == "correction" and "expected_quantity" not in request.form:
                raise ValueError("Reload the stock correction form before saving.")
            adjust_stock(
                product_id,
                request.form.get("amount", ""),
                kind,
                request.form.get("reason", ""),
                expected_quantity=request.form.get("expected_quantity")
                if kind == "correction"
                else None,
            )
        except ValueError as error:
            return render_template("stock_adjustment.html", product=product, error=str(error)), 400
        flash("Stock adjusted and recorded in history.", "success")
        return redirect(url_for("inventory_tools.history", product_id=product_id))
    return render_template("stock_adjustment.html", product=product)


def pagination_links(endpoint, filters, data):
    args = {key: value for key, value in filters.items() if key != "page"}
    return {
        "previous": url_for(endpoint, **args, page=data["page"] - 1) if data["page"] > 1 else None,
        "next": url_for(endpoint, **args, page=data["page"] + 1)
        if data["page"] < data["pages"]
        else None,
    }


@bp.get("/history")
def history():
    try:
        filters = history_filters(request.args)
    except ValueError as error:
        return render_template("error.html", message=str(error)), 400
    data = stock_history(filters)
    return render_template(
        "stock_history.html",
        filters=filters,
        types=ADJUSTMENT_TYPES,
        pagination=pagination_links("inventory_tools.history", filters, data),
        **data,
    )


@bp.get("/history/<int:adjustment_id>")
def history_detail(adjustment_id):
    try:
        filters = history_filters({})
        data = stock_history(filters, adjustment_id)
    except ValueError:
        abort(404)
    if not data["adjustments"]:
        abort(404)
    return render_template(
        "stock_history.html", filters=filters, types=ADJUSTMENT_TYPES, pagination={}, **data
    )
