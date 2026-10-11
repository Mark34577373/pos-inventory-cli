
from decimal import Decimal, InvalidOperation

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
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

app = Flask(__name__)

# Development-only fallback. Set TCCP_SECRET_KEY in the environment
# before using this app outside your local development environment.
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

    # Keep product prices within a sensible demo limit.
    if price > Decimal("1000000"):
        raise ValueError("Price cannot exceed $1,000,000.00.")

    # This project stores prices in integer cents.
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
            product_id = create_product(name, price, quantity)
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

            # Update product details and quantity through the existing
            # service layer, preserving its validation and persistence.
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

    flash(
        f'"{product["name"]}" was removed from active inventory.',
        "success",
    )
    return redirect(url_for("inventory"))


@app.errorhandler(404)
def page_not_found(error):
    return render_template("404.html"), 404


if __name__ == "__main__":
    initialize_database()
    app.run(host="127.0.0.1", port=5000, debug=True)
