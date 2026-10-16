"""POS presentation and durable saved-sale actions; shared application guards apply."""

import json
from contextlib import closing

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from database.database import get_store_connection
from inventory_routes import pagination_links
from services.held_sales import digest, hold_sale, list_held, resume_sale, update_held
from services.inventory_management import (
    inventory_filters,
    list_categories,
    positive_id,
    query_inventory,
)
from services.pos_cart import order_notes
from services.pos_layout import COLORS, defaults, get_layout, save_layout

bp = Blueprint("pos", __name__)
CART_KEYS = (
    "sale_cart",
    "sale_customer_id",
    "sale_discount",
    "sale_notes",
    "held_sale_id",
    "held_claim",
    "checkout_token",
)


@bp.errorhandler(ValueError)
def invalid(error):
    return render_template("error.html", message=str(error)), 400


@bp.errorhandler(LookupError)
def missing(error):
    return render_template("error.html", message="Saved sale not found."), 404


def fields(allowed):
    if set(request.form) - set(allowed) - {"csrf_token"}:
        abort(400, description="Unsupported POS fields.")


def clear_cart():
    for key in CART_KEYS:
        session.pop(key, None)


@bp.route("/pos/designer", methods=["GET", "POST"])
def designer():
    state = get_layout()
    if request.method == "POST":
        fields(
            (
                "configuration",
                "revision",
                "action",
                "preset",
                "density",
                "show_images",
                "show_categories",
            )
        )
        try:
            revision = int(request.form.get("revision", "-1"))
            action = request.form.get("action", "save")
            if action not in ("save", "reset"):
                raise ValueError("Choose save or reset.")
            config = (
                defaults()
                if action == "reset"
                else json.loads(request.form.get("configuration", ""))
            )
            if action == "save" and isinstance(config, dict):
                config.update(
                    preset=request.form.get("preset"),
                    density=request.form.get("density"),
                    show_images=request.form.get("show_images") == "1",
                    show_categories=request.form.get("show_categories") == "1",
                )
            save_layout(config, revision)
        except (ValueError, TypeError, RecursionError) as error:
            return render_template(
                "error.html",
                message=str(error)
                if not isinstance(error, json.JSONDecodeError)
                else "Invalid layout configuration. Reload the designer.",
            ), 400
        flash("POS layout saved. Cart contents and financial settings are unchanged.", "success")
        return redirect(url_for("pos.designer"))
    filters = inventory_filters(dict(request.args, active="active"))
    products = query_inventory(filters)
    ids = state["config"]["product_order"]
    with closing(get_store_connection()) as conn:
        pinned = (
            {
                row["id"]: dict(row)
                for row in conn.execute(
                    f"SELECT id,name FROM products WHERE store_id=authorized_store_id() AND is_active=1 AND id IN ({','.join('?' for _ in ids)})",
                    ids,
                )
            }
            if ids
            else {}
        )
    return render_template(
        "pos_designer.html",
        **state,
        products=products["products"],
        pinned=[pinned[i] for i in ids if i in pinned],
        categories=list_categories(),
        colors=COLORS,
        pagination=pagination_links("pos.designer", filters, products),
    )


@bp.post("/sale/notes")
def notes():
    fields(("notes",))
    session["sale_notes"] = order_notes(request.form.get("notes", ""))
    return redirect(url_for("sale"))


@bp.post("/sale/hold")
def hold():
    fields(("label",))
    identifier = hold_sale(
        session.get("sale_cart"),
        session.get("sale_customer_id"),
        session.get("sale_discount"),
        session.get("sale_notes", ""),
        request.form.get("label", ""),
        session.get("checkout_token"),
        held_id=session.get("held_sale_id"),
        claim=session.get("held_claim"),
    )
    clear_cart()
    flash(f"Sale #{identifier} held. Stock is not reserved.", "success")
    return redirect(url_for("pos.held"))


@bp.get("/pos/held")
def held():
    page = positive_id(request.args.get("page", "1"))
    data = list_held(page)
    return render_template(
        "held_sales.html", **data, pagination=pagination_links("pos.held", {}, data)
    )


@bp.post("/pos/held/<int:held_id>/resume")
def resume(held_id):
    fields(())
    # Never overwrite a different active sale; hold or cancel it first.
    if session.get("sale_cart") or session.get("held_sale_id"):
        raise ValueError("Hold or clear the current sale before resuming another.")
    data = resume_sale(held_id)
    clear_cart()
    session.update(
        sale_cart=data["cart"],
        sale_customer_id=data["customer_id"],
        sale_discount=data["discount"],
        sale_notes=data["notes"],
        held_sale_id=data["held_id"],
        held_claim=data["claim"],
        checkout_token=data["checkout_token"],
    )
    flash(
        "Sale resumed. Current prices, tax and stock have been revalidated; review totals before checkout.",
        "success",
    )
    return redirect(url_for("sale"))


@bp.post("/pos/held/<int:held_id>/manage")
def manage(held_id):
    fields(("action", "label"))
    update_held(held_id, request.form.get("action"), request.form.get("label"))
    return redirect(url_for("pos.held"))


@bp.post("/api/pos/checkout-status")
def checkout_status():
    fields(("checkout_token",))
    token = request.form.get("checkout_token", "")
    digest(token)
    with closing(get_store_connection()) as conn:
        # Serialize with in-flight checkouts. A busy database stays uncertain (503).
        conn.execute("BEGIN IMMEDIATE")
        prior = conn.execute(
            "SELECT id FROM transactions WHERE store_id=authorized_store_id() AND checkout_token=?",
            (token,),
        ).fetchone()
        if prior:
            return jsonify(
                context=f"{g.user['id']}:{g.store['id']}",
                state="completed",
                receipt_url=url_for("transaction_receipt", transaction_id=prior["id"]),
            )
        return jsonify(
            state="not_found", retry="same_token_only", context=f"{g.user['id']}:{g.store['id']}"
        )


@bp.post("/api/pos/cash-quote")
def cash_quote():
    fields(("cash_received",))
    from services.money import format_cents, parse_cash
    from services.pos_cart import price_cart

    try:
        quote = price_cart(
            session.get("sale_cart", {}),
            session.get("sale_customer_id"),
            session.get("sale_discount"),
        )
        tender = parse_cash(request.form.get("cash_received", ""))
        if not quote["items"] or quote["has_stock_problem"]:
            raise ValueError("Resolve cart availability before previewing change.")
        if tender < quote["total_cents"]:
            raise ValueError("Cash received must cover the current total.")
    except ValueError as error:
        return jsonify(error=str(error)), 400
    return jsonify(
        context=f"{g.user['id']}:{g.store['id']}",
        total_text=format_cents(quote["total_cents"]),
        change_text=format_cents(tender - quote["total_cents"]),
    )
