"""One cart read/quote interface; no JavaScript financial calculations."""

from contextlib import closing

from database.database import get_store_connection
from services.customers import get_customer
from services.discounts import discount_amount
from services.money import calculate_tax_cents, from_cents
from services.refunds import valid_id
from services.settings import get_settings
from services.validation import visible_text


def clean_cart(cart):
    if not isinstance(cart, dict) or not 1 <= len(cart) <= 50:
        raise ValueError("A sale needs 1–50 distinct products.")
    result = {}
    for key, quantity in cart.items():
        if (
            not isinstance(key, str)
            or not key.isascii()
            or not key.isdigit()
            or str(int(key)) != key
            or not valid_id(int(key))
            or type(quantity) is not int
            or not 1 <= quantity <= 1_000_000
        ):
            raise ValueError("Invalid cart entry.")
        result[key] = quantity
    return result


def price_cart(cart, customer_id=None, discount=None):
    discount = discount or dict(kind="none", value="")
    items, unavailable = [], []
    try:
        cart = clean_cart(cart) if cart else {}
    except ValueError:
        cart = {}
        unavailable.append("An invalid cart entry")
    with closing(get_store_connection()) as conn:
        conn.execute("BEGIN")
        rows = (
            {
                row["id"]: dict(row)
                for row in conn.execute(
                    f"SELECT * FROM products WHERE store_id=authorized_store_id() AND is_active=1 AND id IN ({','.join('?' for _ in cart)})",
                    tuple(int(key) for key in cart),
                )
            }
            if cart
            else {}
        )
        for key, quantity in cart.items():
            product = rows.get(int(key))
            if product is None:
                unavailable.append(f"Product #{key}")
                continue
            product["price"] = from_cents(product["price_cents"])
            items.append(
                dict(
                    product=product,
                    quantity=quantity,
                    price_cents=product["price_cents"],
                    line_total_cents=product["price_cents"] * quantity,
                    available_quantity=product["quantity"],
                    stock_ok=quantity <= product["quantity"],
                )
            )
        customer = None
        if customer_id is not None:
            try:
                customer = get_customer(customer_id, conn, active=True)
            except LookupError:
                unavailable.append(
                    "Selected customer is no longer available; choose Guest or another customer"
                )
        settings = get_settings(conn)
        subtotal = sum(item["line_total_cents"] for item in items)
        discount_cents = 0
        try:
            discount_cents = discount_amount(items, discount["kind"], discount["value"])
        except (ValueError, KeyError, TypeError) as error:
            unavailable.append(
                str(error)
                if isinstance(error, ValueError)
                else "Invalid discount. Remove or replace it."
            )
        tax = calculate_tax_cents(subtotal - discount_cents, settings["tax_rate"])
    return dict(
        items=items,
        unavailable_items=unavailable,
        customer=customer,
        discount=discount,
        subtotal_cents=subtotal,
        discount_cents=discount_cents,
        tax_cents=tax,
        total_cents=subtotal - discount_cents + tax,
        tax_rate_percent=settings["tax_percent"],
        has_stock_problem=bool(unavailable) or any(not item["stock_ok"] for item in items),
    )


def order_notes(value):
    return visible_text(value, "Order notes", 500, optional=True, multiline=True)
