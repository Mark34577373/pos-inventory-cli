import hmac
import os
import secrets
import sqlite3
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from flask import (
    Flask,
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

from auth_routes import bp as auth_blueprint
from config import TAX_RATE, business_now
from database.database import get_store_connection as get_db_connection
from database.database import initialize_database
from services.auth import load_identity
from services.inventory import (
    create_product,
    deactivate_product,
    find_product_by_id,
    list_active_products,
    search_active_products,
    set_product_quantity,
    update_product_record,
)
from services.money import calculate_tax_cents, from_cents, parse_cash
from services.transaction_service import complete_transaction

app = Flask(__name__)
app.register_blueprint(auth_blueprint)

app.secret_key = os.environ.get("TCCP_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("TCCP_ENV") == "production",
    MAX_CONTENT_LENGTH=64 * 1024,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    SESSION_REFRESH_EACH_REQUEST=False,
    DEMO_READ_ONLY=os.environ.get("TCCP_READ_ONLY", "1" if os.environ.get("TCCP_ENV") == "production" else "0") == "1",
)
if os.environ.get("TCCP_ENV") == "production" and len(os.environ.get("TCCP_SECRET_KEY", "")) < 32:
    raise RuntimeError("Production requires TCCP_SECRET_KEY of at least 32 characters.")
if os.environ.get("TCCP_ENV") == "production" and not os.path.isabs(os.environ.get("TCCP_DB_PATH", "")):
    raise RuntimeError("Production requires an absolute persistent TCCP_DB_PATH.")
if os.environ.get("TCCP_ENV") == "production":
    from contextlib import closing
    from pathlib import Path
    try:
        with closing(sqlite3.connect(Path(os.environ["TCCP_DB_PATH"]).resolve().as_uri() + "?mode=ro", uri=True)) as startup_db:
            if startup_db.execute("SELECT 1 FROM schema_migrations WHERE version=14").fetchone() is None:
                raise RuntimeError("Run the explicit database migration before production startup.")
    except sqlite3.Error:
        raise RuntimeError("Production requires an existing database migrated with manage.py init.") from None



def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def protect_writes():
    g.user = None
    g.store = None
    g.is_demo = False
    public = {'static', 'auth.login', 'auth.register', 'auth.demo', 'auth.logout'}
    if request.endpoint == 'static':
        return None
    identity = load_identity(session.get('login_token'))
    if identity:
        g.user = {'id': identity['user_id'], 'email': identity['email']}
        g.store = {'id': identity['id'], 'name': identity['name'], 'kind': identity['kind']}
    elif session.get('login_token'):
        session.clear()
    elif session.get('demo_mode'):
        from contextlib import closing

        from database.database import get_db_connection as raw_connection
        with closing(raw_connection()) as conn:
            store = conn.execute("SELECT id,name,kind FROM stores WHERE kind='demo'").fetchone()
        if store:
            g.store = dict(store)
            g.is_demo = True
    if request.method == 'POST' and request.endpoint not in public and app.config['DEMO_READ_ONLY']:
        abort(403, description='This deployment is read-only.')
    if request.endpoint not in public and request.endpoint is not None and g.store is None:
        if request.path.startswith('/api/'):
            return jsonify(error='Authentication required'), 401
        return redirect(url_for('auth.login', next=request.path if request.method == 'GET' else '/'))
    if g.is_demo and request.endpoint == 'auth.account':
        return redirect(url_for('auth.login'))
    if g.store:
        context = f"{g.user['id'] if g.user else 'demo'}:{g.store['id']}"
        if session.get('cart_context') != context:
            for key in ('sale_cart', 'checkout_token', 'last_receipt_id', 'last_receipt'):
                session.pop(key, None)
            session['cart_context'] = context
    if request.method == "POST":
        if g.is_demo and request.endpoint not in public:
            abort(403, description="This public demo is read-only.")
        expected = session.get("csrf_token", "")
        submitted = request.form.get("csrf_token", "")
        if not expected or not hmac.compare_digest(expected.encode("utf-8"), submitted.encode("utf-8")):
            abort(400, description="Form expired. Refresh the page and try again.")


@app.errorhandler(sqlite3.Error)
def database_error(error):
    app.logger.error("Database operation failed (%s)", type(error).__name__)
    return render_template("error.html", message="The database is unavailable. Please retry shortly."), 503


@app.errorhandler(500)
def unexpected_error(error):
    return render_template("error.html", message="An unexpected error occurred. Please refresh and retry."), 500


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(413)
def request_error(error):
    return render_template("error.html", message=error.description), error.code


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store, private"
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    if os.environ.get('TCCP_ENV') == 'production':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'

    return response


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
    if not isinstance(saved_cart, dict):
        saved_cart = {}
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

        if not 0 < quantity <= 1_000_000:
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
            WHERE store_id=authorized_store_id() AND date(timestamp) >= ?
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

    # Use the same local dates and date(timestamp) filtering as Reports.
    today = business_now().date()
    start_date = today - timedelta(days=6)

    conn = get_db_connection()
    try:
        today_summary = conn.execute(
            """
            SELECT
                COALESCE(SUM(total_cents), 0) AS today_sales_cents,
                COUNT(*) AS today_transaction_count
            FROM transactions
            WHERE store_id=authorized_store_id() AND date(timestamp) = ?
            """,
            (today.isoformat(),),
        ).fetchone()

        recent_rows = conn.execute(
            """
            SELECT id, timestamp, total_cents, payment_method
            FROM transactions
            WHERE store_id=authorized_store_id()
            ORDER BY timestamp DESC, id DESC
            LIMIT 5
            """
        ).fetchall()

        daily_rows = conn.execute(
            """
            SELECT
                date(timestamp) AS sale_date,
                COUNT(*) AS transaction_count,
                COALESCE(SUM(total_cents), 0) AS sales_cents
            FROM transactions
            WHERE store_id=authorized_store_id() AND date(timestamp) >= ?
              AND date(timestamp) <= ?
            GROUP BY date(timestamp)
            ORDER BY sale_date ASC
            """,
            (start_date.isoformat(), today.isoformat()),
        ).fetchall()
    finally:
        conn.close()

    # Index the database results by date.
    daily_by_date = {
        row["sale_date"]: row
        for row in daily_rows
    }

    # Always build seven days, including days with zero sales.
    sales_chart = []

    for offset in range(7):
        day = start_date + timedelta(days=offset)
        day_key = day.isoformat()
        row = daily_by_date.get(day_key)

        sales_chart.append({
            "date": day_key,
            "label": day.strftime("%a"),
            "sales_cents": row["sales_cents"] if row else 0,
            "transaction_count": (
                row["transaction_count"] if row else 0
            ),
        })

    return render_template(
        "dashboard.html",
        products=products,
        tax_rate=TAX_RATE,
        **summary,
        today_sales_cents=today_summary["today_sales_cents"],
        today_transaction_count=today_summary["today_transaction_count"],
        recent_transactions=recent_rows,
        sales_chart=sales_chart,
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
            update_product_record(product_id, name, price, quantity)
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
    session.setdefault("checkout_token", secrets.token_urlsafe(32))
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

    if new_quantity > min(product["quantity"], 1_000_000):
        flash(
            f'Only {product["quantity"]} unit(s) of "{product["name"]}" '
            "are currently in stock.",
            "error",
        )
        return redirect(url_for("sale"))

    if key not in cart and len(cart) >= 50:
        flash("A sale supports at most 50 different products.", "error")
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
    session.pop("checkout_token", None)
    session.pop("sale_cart", None)
    flash("Sale canceled. Inventory has not been changed.", "success")
    return redirect(url_for("sale"))


@app.route("/sale/checkout", methods=["POST"])
def sale_checkout():
    token = request.form.get("checkout_token", "")
    if not token or token != session.get("checkout_token"):
        flash("Checkout expired. Review your cart and try again.", "error")
        return redirect(url_for("sale"))
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

    if payment_method == "Cash":
        try:
            cash_decimal = Decimal(request.form.get("cash_received", "").strip())
            if not cash_decimal.is_finite() or cash_decimal < 0:
                raise ValueError
            cash_received_cents = parse_cash(cash_decimal)
        except (InvalidOperation, ValueError, TypeError):
            flash("Enter a valid cash amount, such as 20.00.", "error")
            return redirect(url_for("sale"))

        if cash_received_cents < total_cents:
            flash(
                f"Cash received must be at least ${total_cents / 100:.2f}.",
                "error",
            )
            return redirect(url_for("sale"))


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
            checkout_token=token,
            cash_received_cents=cash_received_cents,
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

    session["last_receipt_id"] = transaction_id
    session.pop("last_receipt", None)
    session.pop("checkout_token", None)
    session.pop("sale_cart", None)
    return redirect(url_for("receipt"))


@app.route("/receipt")
def receipt():
    transaction_id = session.get("last_receipt_id")
    if not isinstance(transaction_id, int) or not 0 < transaction_id <= 9223372036854775807:
        return redirect(url_for("sale"))
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM transactions WHERE store_id=authorized_store_id() AND id=?", (transaction_id,)).fetchone()
        if row is None:
            return redirect(url_for("sale"))
        receipt_data = dict(row)
        receipt_data["items"] = [dict(item) for item in conn.execute(
            "SELECT product_name AS name, quantity, price_cents AS unit_price_cents, quantity*price_cents AS line_total_cents FROM transaction_items WHERE store_id=authorized_store_id() AND transaction_id=? ORDER BY id", (transaction_id,))]
    finally:
        conn.close()
    receipt_data["transaction_id"] = row["id"]
    receipt_data["subtotal_cents"] = sum(item["line_total_cents"] for item in receipt_data["items"])
    receipt_data["tax_cents"] = row["total_cents"] - receipt_data["subtotal_cents"]
    receipt_data["tax_rate_percent"] = Decimal(row["tax_rate_percent"] or "0")
    receipt_data["change_cents"] = None if row["cash_received_cents"] is None else row["cash_received_cents"] - row["total_cents"]
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

    conditions = ["store_id=authorized_store_id()"]
    params = []

    if search_text:
        if search_text.isdecimal():
            conditions.append("id = ?")
            params.append(int(search_text) if len(search_text) <= 19 and int(search_text) <= 9223372036854775807 else -1)
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
            WHERE store_id=authorized_store_id() AND {where_clause}
            """,
            params,
        ).fetchone()

        rows = conn.execute(
            f"""
            SELECT id, timestamp, total_cents, payment_method
            FROM transactions
            WHERE store_id=authorized_store_id() AND {where_clause}
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
    if transaction_id > 9223372036854775807:
        abort(404)
    conn = get_db_connection()
    try:
        transaction = conn.execute(
            """
            SELECT id, timestamp, total_cents, payment_method
            FROM transactions
            WHERE store_id=authorized_store_id() AND id = ?
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
                WHERE store_id=authorized_store_id() AND transaction_id = ?
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
    today = business_now().date()
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
            WHERE store_id=authorized_store_id() AND date(timestamp) >= ?
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
            WHERE store_id=authorized_store_id() AND date(timestamp) >= ?
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
            WHERE store_id=authorized_store_id() AND date(timestamp) >= ?
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
            WHERE t.store_id=authorized_store_id() AND ti.store_id=authorized_store_id() AND date(t.timestamp) >= ?
              AND date(t.timestamp) <= ?
            GROUP BY ti.product_id, ti.product_name
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
    if os.environ.get("TCCP_ENV") != "production":
        initialize_database()
    app.run(host="127.0.0.1", port=5000, debug=os.environ.get("TCCP_DEBUG") == "1" and os.environ.get("TCCP_ENV") != "production")
