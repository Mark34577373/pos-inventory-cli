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
from werkzeug.exceptions import HTTPException, SecurityError

from alert_routes import bp as alerts_blueprint
from analytics_routes import bp as analytics_blueprint
from auth_routes import bp as auth_blueprint
from business_routes import bp as business_blueprint
from config import BUSINESS_TIMEZONE, business_now
from customer_routes import bp as customers_blueprint
from database.database import get_store_connection as get_db_connection
from database.database import initialize_database
from inventory_routes import bp as inventory_blueprint
from inventory_routes import pagination_links
from pos_routes import bp as pos_blueprint
from purchasing_routes import bp as purchasing_blueprint
from refund_routes import bp as refund_blueprint
from services.analytics import ReportLimitError, build_analytics, resolve_filters
from services.auth import load_identity
from services.inventory import (
    create_product,
    deactivate_product,
    find_product_by_id,
    list_active_products,
    set_product_quantity,
    update_product_record,
)
from services.inventory_management import (
    UNSET,
    inventory_filters,
    list_categories,
    positive_id,
    query_inventory,
    stock_status,
    whole_number,
)
from services.money import format_cents, from_cents, parse_cash
from services.refunds import get_sale
from services.sales_totals import read_sales, summarize_sales
from services.settings import get_settings, tax_rate
from services.transaction_history import query_transactions
from services.transaction_service import complete_transaction
from settings_routes import bp as settings_blueprint

app = Flask(__name__)

app.register_blueprint(pos_blueprint)
app.register_blueprint(business_blueprint)
app.register_blueprint(purchasing_blueprint)
app.register_blueprint(customers_blueprint)
app.register_blueprint(alerts_blueprint)
app.register_blueprint(settings_blueprint)
app.register_blueprint(auth_blueprint)
app.register_blueprint(analytics_blueprint)
app.register_blueprint(inventory_blueprint)
app.register_blueprint(refund_blueprint)

_trusted_hosts = [host.strip() for host in os.environ.get('TCCP_TRUSTED_HOSTS', '').split(',') if host.strip()]
if not _trusted_hosts and os.environ.get('TCCP_ENV') == 'production':
    _trusted_hosts = ['localhost', '127.0.0.1', '[::1]']

def environment_flag(name, default):
    """Reject misspelled security flags instead of enabling writes accidentally."""
    value = os.environ.get(name, default)
    if value not in ("0", "1"):
        raise RuntimeError(f"{name} must be 0 or 1.")
    return value == "1"


if os.environ.get("TCCP_ENV", "development") not in ("development", "testing", "production"):
    raise RuntimeError("TCCP_ENV must be development, testing, or production.")


app.secret_key = os.environ.get("TCCP_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("TCCP_ENV") == "production",
    MAX_CONTENT_LENGTH=64 * 1024,
    MAX_FORM_MEMORY_SIZE=64 * 1024,
    MAX_FORM_PARTS=1100,
    TRUSTED_HOSTS=_trusted_hosts or None,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    SESSION_REFRESH_EACH_REQUEST=False,
    DEMO_READ_ONLY=environment_flag("TCCP_READ_ONLY", "1" if os.environ.get("TCCP_ENV") == "production" else "0"),
    REGISTRATION_ENABLED=environment_flag("TCCP_ALLOW_REGISTRATION", "0" if os.environ.get("TCCP_ENV") == "production" else "1"),
)
if os.environ.get("TCCP_ENV") == "production" and len(os.environ.get("TCCP_SECRET_KEY", "").strip()) < 32:
    raise RuntimeError("Production requires TCCP_SECRET_KEY of at least 32 characters.")
if os.environ.get("TCCP_ENV") == "production" and not os.path.isabs(os.environ.get("TCCP_DB_PATH", "")):
    raise RuntimeError("Production requires an absolute persistent TCCP_DB_PATH.")
if os.environ.get("TCCP_ENV") == "production":
    from contextlib import closing
    from pathlib import Path
    if Path(os.environ["TCCP_DB_PATH"]).resolve().is_relative_to(Path(app.static_folder).resolve()):
        raise RuntimeError("Production database must be outside the public static directory.")
    try:
        with closing(sqlite3.connect(Path(os.environ["TCCP_DB_PATH"]).resolve().as_uri() + "?mode=ro", uri=True)) as startup_db:
            startup_db.execute('PRAGMA foreign_keys=ON')
            if startup_db.execute("SELECT 1 FROM schema_migrations WHERE version=27").fetchone() is None:
                raise RuntimeError("Run the explicit database migration before production startup.")
            from database.validation import validate_integrity, validate_schema
            validate_schema(startup_db, require_current=True)
            validate_integrity(startup_db)
            if os.name == 'posix' and Path(os.environ['TCCP_DB_PATH']).stat().st_mode & 0o077:
                raise RuntimeError('Production database must have private file permissions (0600).')
    except (sqlite3.Error, ValueError):
        raise RuntimeError("Production requires an existing database migrated with manage.py init.") from None



def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = csrf_token
app.jinja_env.filters["money"] = format_cents


@app.before_request
def protect_writes():
    g.user = None
    g.store = None
    g.is_demo = False
    unsafe = request.method not in ('GET', 'HEAD', 'OPTIONS')
    public = {'static', 'auth.login', 'auth.register', 'auth.demo', 'auth.logout', 'auth.stores'}
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
    if unsafe and request.endpoint not in public and app.config['DEMO_READ_ONLY']:
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
            for key in ('sale_cart', 'sale_customer_id', 'sale_discount', 'sale_notes', 'held_sale_id', 'held_claim', 'checkout_token', 'last_receipt_id', 'last_receipt'):
                session.pop(key, None)
            session['cart_context'] = context
    if len(request.query_string) > 8192:
        abort(414, description='The query is too long. Use shorter filters.')
    if any(len(request.args.getlist(key)) != 1 for key in request.args):
        abort(400, description='Duplicate query parameters are not supported.')
    if unsafe:
        # All current mutations are HTML form endpoints, not JSON APIs.
        if request.mimetype not in ('application/x-www-form-urlencoded', 'multipart/form-data', ''):
            abort(415, description='Submit this action using its HTML form.')
        if request.files or any(len(request.form.getlist(key)) != 1 for key in request.form):
            abort(400, description='Duplicate form fields and file uploads are not supported.')
        if g.is_demo and request.endpoint not in public:
            abort(403, description="This public demo is read-only.")
        expected = session.get("csrf_token", "")
        submitted = request.form.get("csrf_token", "")
        header_token = request.headers.get('X-CSRF-Token', '')
        if header_token and submitted and header_token != submitted:
            abort(400, description='Conflicting form tokens. Reload and retry.')
        submitted = header_token or submitted
        if not expected or not hmac.compare_digest(expected.encode("utf-8"), submitted.encode("utf-8")):
            abort(400, description="Form expired. Refresh the page and try again.")


@app.errorhandler(sqlite3.Error)
def database_error(error):
    app.logger.error("Database operation failed (%s)", type(error).__name__)
    return render_template("error.html", message="The database is unavailable. Please retry shortly.", status_code=503), 503


@app.errorhandler(Exception)
def safe_unhandled_error(error):
    if isinstance(error, HTTPException):
        return error
    if app.debug and os.environ.get('TCCP_ENV') != 'production':
        raise error
    app.logger.error('Unhandled request failure at %s (%s)', request.endpoint or 'unmatched', type(error).__name__)
    return render_template('error.html', message='An unexpected error occurred. Please refresh and retry.', status_code=500), 500


@app.errorhandler(500)
def unexpected_error(error):
    return render_template("error.html", message="An unexpected error occurred. Please refresh and retry.", status_code=500), 500


@app.errorhandler(409)
@app.errorhandler(414)
@app.errorhandler(415)
@app.errorhandler(401)
@app.errorhandler(405)
@app.errorhandler(429)
@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(413)
def request_error(error):
    response = error.get_response()
    # Invalid hosts have no URL adapter; a template calling url_for would fail.
    if error.code == 400:
        try:
            request.host
        except SecurityError:
            response.set_data('The request host is not permitted.')
            response.mimetype = 'text/plain'
            return response
    response.set_data(render_template("error.html", message=error.description, status_code=error.code))
    response.content_type = "text/html; charset=utf-8"
    return response


@app.context_processor
def store_preferences():
    if not getattr(g, "store", None):
        return {}
    try:
        preferences = get_settings()
    except (sqlite3.Error, ValueError):
        # Error pages must render even when settings storage is unavailable.
        # Financial services still fail closed; this supplies no tax fallback.
        preferences = None
    return {"store_preferences": preferences, "business_timezone": BUSINESS_TIMEZONE}


@app.context_processor
def notification_context():
    if not getattr(g, 'store', None):
        return {}
    from services.alerts import notification_summary
    try:
        return {'notifications': notification_summary()}
    except sqlite3.Error:
        # Error templates must still render when the database is unavailable.
        return {'notifications': None}


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store, private"
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; script-src-attr 'none'; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'; connect-src 'self'; worker-src 'none'"
    if os.environ.get('TCCP_ENV') == 'production':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'

    return response


def get_inventory_summary(products):
    """Calculate inventory metrics for the supplied product list."""
    return {
        "total_products": len(products),
        "low_stock_count": sum(
            1 for product in products
            if stock_status(product) == "low"
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
        quantity = whole_number(quantity_text)
    except (ValueError, TypeError):
        raise ValueError("Quantity must be a whole number.") from None

    if not 0 <= quantity <= 1_000_000:
        raise ValueError("Quantity must be between 0 and 1,000,000.")

    return name, price, quantity


def get_sale_data():
    from services.pos_cart import price_cart
    return dict(price_cart(session.get('sale_cart', {}), session.get('sale_customer_id'), session.get('sale_discount')),
                order_notes=session.get('sale_notes',''))


def parse_quantity(value):
    """Parse a safe, positive whole-number quantity."""
    return whole_number(value, 1)


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
        conn.execute("BEGIN")
        try:
            week_sales = read_sales(conn, start_date.isoformat()+' 00:00:00', (today+timedelta(days=1)).isoformat()+' 00:00:00')
        except ReportLimitError as error:
            abort(413,description=str(error))
        totals = summarize_sales([row for row in week_sales if row['timestamp'][:10]==today.isoformat()])
        today_summary = {'today_sales_cents':totals['total_revenue_cents'], 'today_transaction_count':totals['transaction_count']}
        recent_rows = conn.execute(
            """
            SELECT id, timestamp, total_cents, payment_method
            FROM transactions
            WHERE store_id=authorized_store_id()
            ORDER BY timestamp DESC, id DESC
            LIMIT 5
            """
        ).fetchall()

        daily = {}
        for row in week_sales:
            key = row['timestamp'][:10]
            bucket = daily.setdefault(key, {'sale_date':key,'transaction_count':0,'sales_cents':0})
            bucket['sales_cents'] += row['total_cents']
            bucket['transaction_count'] += 1
        daily_rows = list(daily.values())
        refund_rows = conn.execute("SELECT total_cents FROM refunds WHERE store_id=authorized_store_id() AND business_date=? ORDER BY id LIMIT 10001", (today.isoformat(),)).fetchall()
        if len(refund_rows) > 10000:
            abort(413, description='Narrow reporting to at most 10,000 refunds.')
        today_refunded = sum(row['total_cents'] for row in refund_rows)
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

    from services.dashboard_presentation import dashboard_report
    try:
        dashboard_data = dashboard_report(request.args.to_dict())
    except ReportLimitError as error:
        return render_template("error.html", message=str(error)), 413
    except ValueError as error:
        return render_template("error.html", message=str(error)), 400
    return render_template(
        "dashboard.html",
        **dashboard_data,
        products=products,
        tax_rate=tax_rate(),
        **summary,
        today_sales_cents=today_summary["today_sales_cents"],
        today_transaction_count=today_summary["today_transaction_count"],
        today_refunded_cents=today_refunded,
        today_net_sales_cents=today_summary["today_sales_cents"]-today_refunded,
        recent_transactions=recent_rows,
        sales_chart=sales_chart,
        low_stock_products=query_inventory(inventory_filters({'status':'low'}))['products'][:10],
        out_of_stock_products=query_inventory(inventory_filters({'status':'out'}))['products'][:10],
    )


@app.route("/inventory")
def inventory():
    try:
        filters=inventory_filters(request.args)
        data=query_inventory(filters)
    except ValueError as error:
        return render_template('error.html',message=str(error)),400
    return render_template('inventory.html',filters=filters,categories=list_categories(),search_term=filters['q'],
                           pagination=pagination_links('inventory',filters,data),**data)


@app.context_processor
def ui_context():
    # Presentation only. Routes and services remain the authorization boundary.
    return {'ui_can_write': bool(getattr(g, 'user', None) and getattr(g, 'store', None)
                                and not getattr(g, 'is_demo', False)
                                and not app.config['DEMO_READ_ONLY'])}


@app.context_processor
def product_form_context():
    return {'categories':list_categories()} if request.endpoint in ('add_product','edit_product') and getattr(g,'store',None) else {}


def product_metadata(edit=False):
    default=UNSET if edit else None
    return {'category_id':request.form.get('category_id',default), 'sku':request.form.get('sku',default),
            'low_stock_threshold':request.form.get('low_stock_threshold',default)}


app.jinja_env.globals['stock_status']=stock_status


@app.route("/products/new", methods=["GET", "POST"])
def add_product():
    if request.method == "POST":
        try:
            name, price, quantity = read_product_form()
            create_product(name, price, quantity, **product_metadata())
        except (ValueError, sqlite3.IntegrityError) as error:
            flash(str(error) if isinstance(error,ValueError) else "SKU is unavailable in this store.", "error")
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
            update_product_record(product_id, name, price, quantity, **product_metadata(edit=True),
                                  expected_quantity=request.form.get("expected_quantity"),
                                  reason=request.form.get("reason","Stock corrected during product edit"))
        except (ValueError, sqlite3.IntegrityError) as error:
            flash(str(error) if isinstance(error,ValueError) else "SKU is unavailable in this store.", "error")
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
        set_product_quantity(product_id, quantity, expected_quantity=request.form.get("expected_quantity"),
                             reason=request.form.get("reason","Quick inventory correction"))
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
    from services.pos_layout import get_layout
    layout = get_layout()["config"]
    favorite_filter = request.args.get("favorites", "")
    if favorite_filter not in ("", "1"):
        abort(400)
    search_term = request.args.get("q", "").strip()
    try:
        filters = inventory_filters(dict(request.args, active='active'))
        product_data = query_inventory(filters, pos_layout=layout, favorites_only=favorite_filter=="1")
    except ValueError as error:
        abort(400, description=str(error))
    products = product_data['products']
    from services.customers import list_customers
    try:
        customer_results = list_customers({'q': request.args.get('customer_q', '')})
    except ValueError as error:
        abort(400, description=str(error))

    categories = list_categories()
    rank = {identifier:index for index,identifier in enumerate(layout['category_order'])}
    categories.sort(key=lambda row:(rank.get(row['id'],len(rank)),row['name'].casefold(),row['id']))
    pagination = pagination_links('sale',dict(filters,favorites=favorite_filter),product_data)
    return render_template(
        "sale.html",
        products=products,
        customer_results=customer_results,
        search_term=search_term,
        categories=categories,
        pos_layout=layout,
        favorite_filter=favorite_filter,
        selected_category=request.args.get("category", ""),
        pagination=pagination,
        **get_sale_data(),
    )


@app.post("/sale/discount")
def sale_discount():
    if set(request.form) - {"csrf_token", "kind", "value"}:
        abort(400)
    from services.discounts import discount_amount

    kind, value = request.form.get("kind", "none"), request.form.get("value", "")
    if kind == "none":
        value = ""
    try:
        data = get_sale_data()
        discount_amount(
            [
                dict(price_cents=row["price_cents"], quantity=row["quantity"])
                for row in data["items"]
            ],
            kind,
            value,
        )
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("sale"))
    session["sale_discount"] = dict(kind=kind, value=value)
    return redirect(url_for("sale"))


@app.route("/sale/add", methods=["POST"])
def sale_add():
    try:
        product_id = positive_id(request.form.get("product_id", ""))
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
        quantity = whole_number(request.form.get("quantity", ""))
    except (TypeError, ValueError):
        flash("Enter a valid whole-number quantity.", "error")
        return redirect(url_for("sale"))

    adjustment = request.form.get('adjust', '')
    if adjustment:
        if adjustment not in ('plus', 'minus'):
            abort(400)
        quantity = int(cart[key]) + (1 if adjustment == 'plus' else -1)

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
    if session.get('held_sale_id'):
        from services.held_sales import cancel_claim
        cancel_claim(session['held_sale_id'],session.get('held_claim'))
    for key in ('sale_notes','held_sale_id','held_claim'):
        session.pop(key,None)
    session.pop("sale_discount", None)
    session.pop("sale_customer_id", None)
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
            customer_id=session.get("sale_customer_id"),
            discount_kind=sale_data["discount"]["kind"],
            discount_value=sale_data["discount"]["value"],
            order_notes=session.get("sale_notes", ""),
            held_sale_id=session.get("held_sale_id"),
            held_claim=session.get("held_claim"),
        )
    except (ValueError, LookupError) as error:
        flash(f"Checkout could not be completed: {error}", "error")
        return redirect(url_for("sale"))
    except sqlite3.Error as error:
        app.logger.error("Database operation failed during simulated checkout (%s)", type(error).__name__)
        flash(
            "The sale could not be saved. No partial transaction was committed.",
            "error",
        )
        return redirect(url_for("sale"))

    session.pop("sale_discount", None)
    session.pop("sale_customer_id", None)
    for key in ("sale_notes","held_sale_id","held_claim"):
        session.pop(key,None)
    session["last_receipt_id"] = transaction_id
    session.pop("last_receipt", None)
    session.pop("checkout_token", None)
    session.pop("sale_cart", None)
    return redirect(url_for("receipt"))


@app.route("/receipt")
def receipt():
    transaction_id = session.get("last_receipt_id")
    if not isinstance(transaction_id, int):
        return redirect(url_for("sale"))
    return transaction_receipt(transaction_id)


@app.get("/transactions/<int:transaction_id>/receipt")
def transaction_receipt(transaction_id):
    try:
        state = get_sale(transaction_id)
    except LookupError:
        return redirect(url_for('sale'))
    except ReportLimitError as error:
        abort(413, description=str(error))
    row = state['transaction']
    receipt_data = dict(row)
    from services.settings import read_receipt_snapshot
    receipt_data['profile'] = read_receipt_snapshot(receipt_data.get('receipt_snapshot'))
    receipt_data['items'] = [dict(name=item['product_name'], quantity=item['quantity'], unit_price_cents=item['price_cents'], line_total_cents=item['line_total_cents']) for item in state['items']]
    receipt_data['transaction_id'] = row['id']
    receipt_data['subtotal_cents'] = sum(item['line_total_cents'] for item in state['items'])
    receipt_data['tax_cents'] = row['total_cents']-receipt_data['subtotal_cents']
    receipt_data['tax_verified'] = state['eligible']
    receipt_data['change_cents'] = None if row['cash_received_cents'] is None else row['cash_received_cents']-row['total_cents']
    return render_template('receipt.html', receipt=receipt_data, refund_state=state)


# -------------------------------------------------------------------
# DAY 11: TRANSACTION HISTORY
# -------------------------------------------------------------------

@app.route("/transactions")
def transactions():
    try:
        data = query_transactions(request.args)
    except ReportLimitError as error:
        abort(413, description=str(error))
    except ValueError as error:
        abort(400, description=str(error))
    filters = dict(request.args)
    filters.pop('page', None)
    pagination = pagination_links('transactions', filters, data)
    return render_template('transactions.html', **data, pagination=pagination)


@app.route("/transactions/<int:transaction_id>")
def transaction_detail(transaction_id):
    if transaction_id > 9223372036854775807:
        abort(404)
    try:
        state = get_sale(transaction_id)
    except LookupError:
        flash(f'Transaction #{transaction_id} was not found.', 'error')
        return redirect(url_for('transactions'))
    except ReportLimitError as error:
        abort(413, description=str(error))
    subtotal = sum(item['line_total_cents'] for item in state['items'])
    return render_template('transaction_detail.html', transaction=state['transaction'], items=state['items'], subtotal_cents=subtotal, tax_cents=state['transaction']['total_cents']-subtotal, refund_state=state)


# -------------------------------------------------------------------
# DAY 11: SALES REPORTS
# -------------------------------------------------------------------

@app.route("/reports")
def reports():
    today=business_now().date()
    try:
        filters=resolve_filters(request.args,today=today)
    except ValueError as error:
        # Preserve the existing Reports page's forgiving date-picker behavior.
        # The new /analytics and downloads reject malformed ranges with 400.
        flash(str(error),'error')
        filters=resolve_filters({},today=today)
    try:
        data=build_analytics(filters)
    except ReportLimitError as error:
        return render_template('error.html',message=str(error)),413
    overview=dict(data['summary'],average_sale_cents=data['summary']['average_order_cents'])
    daily_filters=resolve_filters(dict(filters.query_args(),group='daily'))
    # The shared service's daily series is already available when Reports uses
    # the default daily grouping; only aggregate again for an explicit grouping.
    daily=data['series'] if filters.group=='daily' else build_analytics(daily_filters)['series']
    return render_template('reports.html', start_date=filters.start.isoformat(),end_date=filters.end.isoformat(),
        overview=overview,payment_rows=data['payment_rows'],
        daily_rows=[dict(row,sale_date=row['period_start']) for row in reversed(daily) if row['transaction_count']],
        top_product_rows=[dict(row,revenue_cents=row['item_sales_cents']) for row in data['products']])


@app.errorhandler(404)
def page_not_found(error):
    return render_template("404.html"), 404


if __name__ == "__main__":
    if os.environ.get("TCCP_ENV") != "production":
        initialize_database()
    app.run(host="127.0.0.1", port=5000, debug=os.environ.get("TCCP_DEBUG") == "1" and os.environ.get("TCCP_ENV") != "production")
