
import sqlite3
from datetime import date, datetime, timedelta
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
from database.database import get_db_connection, initialize_database
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

# Local-development key only. Use an environment variable and a strong,
# private secret before deploying beyond your own computer.
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

    if not 0 <= quantity <= 1_000_000:
        raise ValueError("Quantity must be between 0 and 1,000,000.")

    return name, price, quantity


def get_sale_data():
    """Build the current sale from current database product data."""
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
    """Parse a safe, positive whole-number quantity."""
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        raise ValueError("Quantity must be a whole number.") from None

    if not 1 <= quantity <= 1_000_000:
        raise ValueError("Quantity must be between 1 and 1,000,000.")

    return quantity


def parse_iso_date(value):
    """Return a date from YYYY-MM-DD input, or None for blank input."""
    if not value:
        return None

    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def get_transactions_for_period(start_date, end_date):
    """Return transaction summaries for an inclusive date range."""
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT
                id,
                timestamp,
                total_cents,
                payment_method
            FROM transactions
            WHERE date(timestamp) >= ?
              AND date(timestamp) <= ?
            ORDER BY timestamp DESC, id DESC
            """,
            (start_date.isoformat(), end_date.isoformat()),
        ).fetchall()
        return rows
    finally:
        conn.close()


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
    products = (
        search_active_products(search_term)
        if search_term
        else list_active_products()
    )
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

    try:
        quantity = int(request.form.get("quantity", "").strip())
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

    return render_template(
        "sale.html",
        products=products,
        search_term=search_term,
        **get_sale_data(),
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
        try:
            cash_decimal = Decimal(request.form.get("cash_received", "").strip())
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

    receipt_items = [
        {
            "name": item["product"]["name"],
            "quantity": item["quantity"],
            "unit_price_cents": item["price_cents"],
            "line_total_cents": item["line_total_cents"],
        }
        for item in sale_data["items"]
    ]

    session["last_receipt"] = {
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
    return redirect(url_for("receipt"))


@app.route("/receipt")
def receipt():
    receipt_data = session.get("last_receipt")

    if not receipt_data:
        flash("There is no recent receipt to display.", "error")
        return redirect(url_for("sale"))

    return render_template("receipt.html", receipt=receipt_data)


# -------------------------------------------------------------------
# DAY 11: TRANSACTION HISTORY
# -------------------------------------------------------------------

@app.route("/transactions")
def transactions():
    search_text = request.args.get("q", "").strip()
    payment_method = request.args.get("payment_method", "").strip().title()
    start_text = request.args.get("start_date", "").strip()
    end_text = request.args.get("end_date", "").strip()

    if payment_method not in ("", "Cash", "Card"):
        payment_method = ""

    start_date = parse_iso_date(start_text) if start_text else None
    end_date = parse_iso_date(end_text) if end_text else None

    if start_text and start_date is None:
        flash("The start date is invalid. Use the date picker.", "error")
    if end_text and end_date is None:
        flash("The end date is invalid. Use the date picker.", "error")

    if start_date and end_date and start_date > end_date:
        flash("Start date cannot be later than end date.", "error")
        start_date = None
        end_date = None

    conditions = []
    params = []

    if search_text:
        if search_text.isdigit():
            conditions.append("id = ?")
            params.append(int(search_text))
        else:
            conditions.append("(CAST(id AS TEXT) LIKE ? OR timestamp LIKE ?)")
            params.extend([f"%{search_text}%", f"%{search_text}%"])

    if payment_method:
        conditions.append("payment_method = ?")
        params.append(payment_method)

    if start_date:
        conditions.append("date(timestamp) >= ?")
        params.append(start_date.isoformat())

    if end_date:
        conditions.append("date(timestamp) <= ?")
        params.append(end_date.isoformat())

    where_clause = " AND ".join(conditions) if conditions else "1 = 1"

    conn = get_db_connection()
    try:
        summary = conn.execute(
            f"""
            SELECT
                COUNT(*) AS transaction_count,
                COALESCE(SUM(total_cents), 0) AS total_cents
            FROM transactions
            WHERE {where_clause}
            """,
            params,
        ).fetchone()

        rows = conn.execute(
            f"""
            SELECT id, timestamp, total_cents, payment_method
            FROM transactions
            WHERE {where_clause}
            ORDER BY timestamp DESC, id DESC
            LIMIT 200
            """,
            params,
        ).fetchall()
    finally:
        conn.close()

    return render_template(
        "transactions.html",
        transactions=rows,
        search_text=search_text,
        payment_method=payment_method,
        start_date=start_text if start_date else "",
        end_date=end_text if end_date else "",
        transaction_count=summary["transaction_count"],
        total_cents=summary["total_cents"],
    )


@app.route("/transactions/<int:transaction_id>")
def transaction_detail(transaction_id):
    conn = get_db_connection()
    try:
        transaction = conn.execute(
            """
            SELECT id, timestamp, total_cents, payment_method
            FROM transactions
            WHERE id = ?
            """,
            (transaction_id,),
        ).fetchone()

        if transaction is None:
            transaction_items = []
        else:
            transaction_items = conn.execute(
                """
                SELECT
                    product_name,
                    quantity,
                    price_cents,
                    quantity * price_cents AS line_total_cents
                FROM transaction_items
                WHERE transaction_id = ?
                ORDER BY rowid
                """,
                (transaction_id,),
            ).fetchall()
    finally:
        conn.close()

    if transaction is None:
        flash(f"Transaction #{transaction_id} was not found.", "error")
        return redirect(url_for("transactions"))

    subtotal_cents = sum(
        item["line_total_cents"] for item in transaction_items
    )
    tax_cents = transaction["total_cents"] - subtotal_cents

    return render_template(
        "transaction_detail.html",
        transaction=transaction,
        items=transaction_items,
        subtotal_cents=subtotal_cents,
        tax_cents=tax_cents,
    )


# -------------------------------------------------------------------
# DAY 11: SALES REPORTS
# -------------------------------------------------------------------

@app.route("/reports")
def reports():
    today = date.today()
    default_start = today - timedelta(days=29)

    start_text = request.args.get(
        "start_date", default_start.isoformat()
    ).strip()
    end_text = request.args.get(
        "end_date", today.isoformat()
    ).strip()

    start_date = parse_iso_date(start_text)
    end_date = parse_iso_date(end_text)

    if start_date is None or end_date is None:
        flash("Choose valid start and end dates.", "error")
        start_date = default_start
        end_date = today
    elif start_date > end_date:
        flash("Start date cannot be later than end date.", "error")
        start_date = default_start
        end_date = today

    start_value = start_date.isoformat()
    end_value = end_date.isoformat()

    conn = get_db_connection()
    try:
        overview = conn.execute(
            """
            SELECT
                COUNT(*) AS transaction_count,
                COALESCE(SUM(total_cents), 0) AS revenue_cents,
                COALESCE(AVG(total_cents), 0) AS average_sale_cents
            FROM transactions
            WHERE date(timestamp) >= ?
              AND date(timestamp) <= ?
            """,
            (start_value, end_value),
        ).fetchone()

        payment_rows = conn.execute(
            """
            SELECT
                payment_method,
                COUNT(*) AS transaction_count,
                COALESCE(SUM(total_cents), 0) AS revenue_cents
            FROM transactions
            WHERE date(timestamp) >= ?
              AND date(timestamp) <= ?
            GROUP BY payment_method
            ORDER BY revenue_cents DESC
            """,
            (start_value, end_value),
        ).fetchall()

        daily_rows = conn.execute(
            """
            SELECT
                date(timestamp) AS sale_date,
                COUNT(*) AS transaction_count,
                COALESCE(SUM(total_cents), 0) AS revenue_cents
            FROM transactions
            WHERE date(timestamp) >= ?
              AND date(timestamp) <= ?
            GROUP BY date(timestamp)
            ORDER BY sale_date DESC
            """,
            (start_value, end_value),
        ).fetchall()

        top_product_rows = conn.execute(
            """
            SELECT
                ti.product_name,
                SUM(ti.quantity) AS units_sold,
                SUM(ti.quantity * ti.price_cents) AS revenue_cents
            FROM transaction_items AS ti
            JOIN transactions AS t
              ON t.id = ti.transaction_id
            WHERE date(t.timestamp) >= ?
              AND date(t.timestamp) <= ?
            GROUP BY ti.product_name
            ORDER BY units_sold DESC, revenue_cents DESC
            LIMIT 10
            """,
            (start_value, end_value),
        ).fetchall()
    finally:
        conn.close()

    return render_template(
        "reports.html",
        start_date=start_value,
        end_date=end_value,
        overview=overview,
        payment_rows=payment_rows,
        daily_rows=daily_rows,
        top_product_rows=top_product_rows,
    )


@app.errorhandler(404)
def page_not_found(error):
    return render_template("404.html"), 404


if __name__ == "__main__":
    initialize_database()
    app.run(host="127.0.0.1", port=5000, debug=True)
