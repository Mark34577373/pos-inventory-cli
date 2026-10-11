
import sqlite3
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from config import TAX_RATE
from database.database import initialize_database
from services.inventory import (
    create_product,
    deactivate_product,
    find_product_by_id,
    list_active_products,
    search_active_products,
    set_product_quantity,
    update_product_details,
)
from services.money import calculate_tax_cents, from_cents, to_cents
from services.transaction_service import complete_transaction

app = Flask(__name__)

# Local-development key only. Configure TCCP_SECRET_KEY in the environment
# before deploying anywhere beyond your own computer.
app.secret_key = "tccp-local-development-key"


def get_inventory_summary(products):
    """Calculate inventory metrics for the supplied product list."""
    return {
        "total_products": len(products),
        "low_stock_count": sum(
            1 for product in products
            if 0 < product["quantity"] <= 5
        ),
        "out_of_stock_count": sum(
            1 for product in products
            if product["quantity"] <= 0
        ),
        "inventory_value": sum(
            product["price_cents"] * product["quantity"]
            for product in products
        ),
    }


def read_product_form():
    """Validate submitted product fields and return cleaned values."""
    name = request.form.get("name", "").strip()
    price_text = request.form.get("price", "").strip()
    quantity_text = request.form.get("quantity", "").strip()

    if not name:
        raise ValueError("Product name is required.")

    if len(name) > 100:
        raise ValueError("Product name must be 100 characters or fewer.")

    try:
        price = Decimal(price_text)
    except (InvalidOperation, ValueError):
        raise ValueError("Enter a valid price, such as 12.99.") from None

    if not price.is_finite() or price <= 0:
        raise ValueError("Price must be greater than $0.00.")

    if price > Decimal("1000000"):
        raise ValueError("Price cannot exceed $1,000,000.00.")

    if price.as_tuple().exponent < -2:
        raise ValueError("Price can have no more than two decimal places.")

    try:
        quantity = int(quantity_text)
    except (ValueError, TypeError):
        raise ValueError("Quantity must be a whole number.") from None

    if quantity < 0:
        raise ValueError("Quantity cannot be negative.")

    if quantity > 1_000_000:
        raise ValueError("Quantity cannot exceed 1,000,000.")

    return name, price, quantity


def get_sale_data():
    """
    Build the current sale from database product data.

    The session stores only product IDs and quantities. Prices and stock
    are looked up again from the database whenever the sale is displayed.
    """
    saved_cart = session.get("sale_cart", {})
    items = []
    unavailable_items = []
    subtotal_cents = 0

    for raw_product_id, raw_quantity in saved_cart.items():
        try:
            product_id = int(raw_product_id)
            quantity = int(raw_quantity)
        except (TypeError, ValueError):
            unavailable_items.append("An invalid cart entry")
            continue

        if quantity <= 0:
            unavailable_items.append(f"Product #{product_id}")
            continue

        product = find_product_by_id(product_id)

        if product is None:
            unavailable_items.append(f"Product #{product_id}")
            continue

        price_cents = product["price_cents"]
        line_total_cents = price_cents * quantity
        subtotal_cents += line_total_cents

        items.append({
            "product": product,
            "quantity": quantity,
            "price_cents": price_cents,
            "line_total_cents": line_total_cents,
            "available_quantity": product["quantity"],
            "stock_ok": quantity <= product["quantity"],
        })

    tax_cents = calculate_tax_cents(subtotal_cents, TAX_RATE)
    total_cents = subtotal_cents + tax_cents

    return {
        "items": items,
        "unavailable_items": unavailable_items,
        "subtotal_cents": subtotal_cents,
        "tax_cents": tax_cents,
        "total_cents": total_cents,
        "tax_rate_percent": TAX_RATE * 100,
        "has_stock_problem": (
            bool(unavailable_items)
            or any(not item["stock_ok"] for item in items)
        ),
    }


def parse_quantity(value):
    """Parse a safe, whole-number quantity."""
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        raise ValueError("Quantity must be a whole number.") from None

    if not 1 <= quantity <= 1_000_000:
        raise ValueError("Quantity must be between 1 and 1,000,000.")

    return quantity


@app.route("/")
def dashboard():
    products = list_active_products()
    summary = get_inventory_summary(products)

    return render_template(
        "dashboard.html",
        products=products,
        tax_rate=TAX_RATE,
        **summary,
    )


@app.route("/inventory")
def inventory():
    search_term = request.args.get("q", "").strip()

    if search_term:
        products = search_active_products(search_term)
    else:
        products = list_active_products()

    summary = get_inventory_summary(products)

    return render_template(
        "inventory.html",
        products=products,
        search_term=search_term,
        **summary,
    )


@app.route("/products/new", methods=["GET", "POST"])
def add_product():
    if request.method == "POST":
        try:
            name, price, quantity = read_product_form()
            create_product(name, price, quantity)
        except ValueError as error:
            flash(str(error), "error")
            return render_template(
                "product_form.html",
                mode="create",
                product=None,
                form_values=request.form,
            ), 400

        flash(f'"{name}" was added to inventory.', "success")
        return redirect(url_for("inventory"))

    return render_template(
        "product_form.html",
        mode="create",
        product=None,
        form_values={},
    )


@app.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
def edit_product(product_id):
    product = find_product_by_id(product_id)

    if product is None:
        flash("That product could not be found.", "error")
        return redirect(url_for("inventory"))

    if request.method == "POST":
        try:
            name, price, quantity = read_product_form()
            update_product_details(product_id, name, price)
            set_product_quantity(product_id, quantity)
        except ValueError as error:
            flash(str(error), "error")
            return render_template(
                "product_form.html",
                mode="edit",
                product=product,
                form_values=request.form,
            ), 400

        flash(f'"{name}" was updated successfully.', "success")
        return redirect(url_for("inventory"))

    return render_template(
        "product_form.html",
        mode="edit",
        product=product,
        form_values={},
    )


@app.route("/products/<int:product_id>/quantity", methods=["POST"])
def update_quantity(product_id):
    product = find_product_by_id(product_id)

    if product is None:
        flash("That product could not be found.", "error")
        return redirect(url_for("inventory"))

    quantity_text = request.form.get("quantity", "").strip()

    try:
        quantity = int(quantity_text)
        if not 0 <= quantity <= 1_000_000:
            raise ValueError
        set_product_quantity(product_id, quantity)
    except ValueError:
        flash("Enter a whole-number quantity between 0 and 1,000,000.", "error")
        return redirect(url_for("inventory"))

    flash(f'Stock updated for "{product["name"]}".', "success")
    return redirect(url_for("inventory"))


@app.route("/products/<int:product_id>/deactivate", methods=["POST"])
def remove_product(product_id):
    product = find_product_by_id(product_id)

    if product is None:
        flash("That product could not be found.", "error")
        return redirect(url_for("inventory"))

    try:
        deactivate_product(product_id)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("inventory"))

    # Remove this product from the active browser cart as well.
    cart = session.get("sale_cart", {})
    cart.pop(str(product_id), None)
    session["sale_cart"] = cart

    flash(
        f'"{product["name"]}" was removed from active inventory.',
        "success",
    )
    return redirect(url_for("inventory"))


@app.route("/sale")
def sale():
    search_term = request.args.get("q", "").strip()
    products = (
        search_active_products(search_term)
        if search_term
        else list_active_products()
    )

    sale_data = get_sale_data()

    return render_template(
        "sale.html",
        products=products,
        search_term=search_term,
        **sale_data,
    )


@app.route("/sale/add", methods=["POST"])
def sale_add():
    try:
        product_id = int(request.form.get("product_id", ""))
        quantity_to_add = parse_quantity(request.form.get("quantity", "1"))
    except ValueError as error:
        flash(str(error) or "Choose a valid product and quantity.", "error")
        return redirect(url_for("sale"))

    product = find_product_by_id(product_id)
    if product is None:
        flash("That product is unavailable.", "error")
        return redirect(url_for("sale"))

    cart = session.get("sale_cart", {})
    key = str(product_id)

    try:
        current_quantity = int(cart.get(key, 0))
    except (TypeError, ValueError):
        current_quantity = 0

    new_quantity = current_quantity + quantity_to_add

    if new_quantity > product["quantity"]:
        flash(
            f'Only {product["quantity"]} unit(s) of "{product["name"]}" '
            "are currently in stock.",
            "error",
        )
        return redirect(url_for("sale"))

    cart[key] = new_quantity
    session["sale_cart"] = cart

    flash(f'Added {quantity_to_add} × "{product["name"]}" to the cart.', "success")
    return redirect(url_for("sale", q=request.form.get("return_q", "").strip()))


@app.route("/sale/update/<int:product_id>", methods=["POST"])
def sale_update(product_id):
    cart = session.get("sale_cart", {})
    key = str(product_id)

    if key not in cart:
        flash("That item is no longer in your cart.", "error")
        return redirect(url_for("sale"))

    try:
        quantity = int(request.form.get("quantity", ""))
    except (TypeError, ValueError):
        flash("Enter a valid whole-number quantity.", "error")
        return redirect(url_for("sale"))

    if quantity == 0:
        cart.pop(key, None)
        session["sale_cart"] = cart
        flash("Item removed from the cart.", "success")
        return redirect(url_for("sale"))

    if not 1 <= quantity <= 1_000_000:
        flash("Quantity must be between 1 and 1,000,000, or zero to remove.", "error")
        return redirect(url_for("sale"))

    product = find_product_by_id(product_id)
    if product is None:
        cart.pop(key, None)
        session["sale_cart"] = cart
        flash("That product is unavailable and was removed from the cart.", "error")
        return redirect(url_for("sale"))

    if quantity > product["quantity"]:
        flash(
            f'Only {product["quantity"]} unit(s) of "{product["name"]}" '
            "are currently in stock.",
            "error",
        )
        return redirect(url_for("sale"))

    cart[key] = quantity
    session["sale_cart"] = cart
    flash("Cart quantity updated.", "success")
    return redirect(url_for("sale"))


@app.route("/sale/remove/<int:product_id>", methods=["POST"])
def sale_remove(product_id):
    cart = session.get("sale_cart", {})
    cart.pop(str(product_id), None)
    session["sale_cart"] = cart
    flash("Item removed from the cart.", "success")
    return redirect(url_for("sale"))


@app.route("/sale/cancel", methods=["POST"])
def sale_cancel():
    session.pop("sale_cart", None)
    flash("Sale canceled. Inventory has not been changed.", "success")
    return redirect(url_for("sale"))


@app.route("/sale/checkout", methods=["POST"])
def sale_checkout():
    sale_data = get_sale_data()

    if not sale_data["items"]:
        flash("Your cart is empty. Add a product before checking out.", "error")
        return redirect(url_for("sale"))

    if sale_data["has_stock_problem"]:
        flash(
            "Your cart has unavailable items or quantities that exceed current "
            "stock. Review the cart before checking out.",
            "error",
        )
        return redirect(url_for("sale"))

    payment_method = request.form.get("payment_method", "").strip().title()
    if payment_method not in ("Cash", "Card"):
        flash("Choose Cash or Card as the payment method.", "error")
        return redirect(url_for("sale"))

    total_cents = sale_data["total_cents"]
    cash_received_cents = None
    change_cents = None

    if payment_method == "Cash":
        cash_text = request.form.get("cash_received", "").strip()

        try:
            cash_decimal = Decimal(cash_text)
            if not cash_decimal.is_finite() or cash_decimal < 0:
                raise ValueError
            cash_received_cents = to_cents(cash_decimal)
        except (InvalidOperation, ValueError, TypeError):
            flash("Enter a valid cash amount, such as 20.00.", "error")
            return redirect(url_for("sale"))

        if cash_received_cents < total_cents:
            flash(
                f"Cash received must be at least ${total_cents / 100:.2f}.",
                "error",
            )
            return redirect(url_for("sale"))

        change_cents = cash_received_cents - total_cents

    # complete_transaction re-reads current prices and stock from SQLite,
    # checks the submitted total, and commits the sale atomically.
    items_to_log = [
        {
            "id": item["product"]["id"],
            "qty": item["quantity"],
            "price": from_cents(item["price_cents"]),
        }
        for item in sale_data["items"]
    ]

    try:
        transaction_id = complete_transaction(
            items_to_log,
            from_cents(total_cents),
            payment_method,
        )
    except ValueError as error:
        flash(f"Checkout could not be completed: {error}", "error")
        return redirect(url_for("sale"))
    except sqlite3.Error:
        app.logger.exception("SQLite error while completing checkout")
        flash(
            "The sale could not be saved. No partial transaction was committed.",
            "error",
        )
        return redirect(url_for("sale"))

    # Keep a display snapshot for the receipt. Never use this session snapshot
    # to calculate future sales or change inventory.
    receipt_items = []
    for item in sale_data["items"]:
        receipt_items.append({
            "name": item["product"]["name"],
            "quantity": item["quantity"],
            "unit_price_cents": item["price_cents"],
            "line_total_cents": item["line_total_cents"],
        })

    receipt = {
        "transaction_id": transaction_id,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "items": receipt_items,
        "subtotal_cents": sale_data["subtotal_cents"],
        "tax_cents": sale_data["tax_cents"],
        "total_cents": total_cents,
        "tax_rate_percent": sale_data["tax_rate_percent"],
        "payment_method": payment_method,
        "cash_received_cents": cash_received_cents,
        "change_cents": change_cents,
    }

    session.pop("sale_cart", None)
    session["last_receipt"] = receipt

    return redirect(url_for("receipt"))


@app.route("/receipt")
def receipt():
    receipt_data = session.get("last_receipt")

    if not receipt_data:
        flash("There is no recent receipt to display.", "error")
        return redirect(url_for("sale"))

    return render_template("receipt.html", receipt=receipt_data)


@app.errorhandler(404)
def page_not_found(error):
    return render_template("404.html"), 404


if __name__ == "__main__":
    initialize_database()
    app.run(host="127.0.0.1", port=5000, debug=True)
