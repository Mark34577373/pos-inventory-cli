
from flask import Flask, render_template, request

from config import TAX_RATE
from database.database import initialize_database
from services.inventory import (
    list_active_products,
    search_active_products,
)

app = Flask(__name__)


def get_inventory_summary(products):
    """Calculate inventory metrics using the supplied product list."""
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


@app.errorhandler(404)
def page_not_found(error):
    return (
        render_template("404.html"),
        404,
    )


if __name__ == "__main__":
    initialize_database()
    app.run(debug=True)
