# services/transaction_service.py

from config import business_now
from database.database import get_store_connection as get_db_connection
from database.tenancy import require_store_write
from services.discounts import allocate_discount, discount_amount
from services.money import calculate_tax_cents, from_cents, to_cents
from services.settings import get_settings, receipt_snapshot


def complete_transaction(cart_items, total_amount, payment_method, *, checkout_token=None, cash_received_cents=None, customer_id=None, discount_kind="none", discount_value=""):
    """Validate and persist a complete sale atomically using integer cents."""
    require_store_write()
    payment_method = (
        payment_method.strip().title()
        if isinstance(payment_method, str)
        else ""
    )
    if payment_method not in ("Cash", "Card"):
        raise ValueError("Payment method must be Cash or Card.")
    if not cart_items:
        raise ValueError("Your cart is empty. Add an item before checking out.")

    submitted_total_cents = to_cents(total_amount)

    conn = get_db_connection()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.cursor()
            if checkout_token:
                prior = cursor.execute("SELECT id FROM transactions WHERE store_id=authorized_store_id() AND checkout_token=?", (checkout_token,)).fetchone()
                if prior:
                    return prior["id"]
            from services.customers import get_customer
            customer = get_customer(customer_id, conn, active=True) if customer_id is not None else None
            settings = get_settings(conn)
            subtotal_cents = 0
            transaction_items = []
            for item in cart_items:
                product_id = item.get("id")
                if isinstance(product_id, bool) or not isinstance(product_id, int) or not 0 < product_id <= 9223372036854775807:
                    raise ValueError("Invalid product ID.")
                quantity = item.get("qty")
                if (
                    isinstance(quantity, bool)
                    or not isinstance(quantity, int)
                    or not 0 < quantity <= 1_000_000
                ):
                    raise ValueError("Item quantity must be a positive whole number.")

                product = cursor.execute(
                    "SELECT name, quantity, price_cents, unit_cost_cents, category_id, (SELECT name FROM categories c WHERE c.id=products.category_id AND c.store_id=products.store_id) AS category_name FROM products "
                    "WHERE store_id=authorized_store_id() AND id = ? AND is_active = 1",
                    (product_id,),
                ).fetchone()
                if product is None:
                    raise ValueError(f"Product {product_id} is no longer available.")
                if product["quantity"] < quantity:
                    raise ValueError(
                        f"Only {product['quantity']} units of {product['name']} are available."
                    )

                price_cents = product["price_cents"]
                subtotal_cents += price_cents * quantity
                transaction_items.append(
                    (product_id, quantity, price_cents, product["name"], product["category_id"], product["category_name"] or "Uncategorized", product["unit_cost_cents"])
                )

            discount_lines = [dict(id=index, quantity=row[1], price_cents=row[2])
                              for index, row in enumerate(transaction_items)]
            discount_cents = discount_amount(discount_lines, discount_kind, discount_value)
            allocate_discount(discount_lines, discount_cents)
            subtotal_cents -= discount_cents
            tax_cents = calculate_tax_cents(subtotal_cents, settings['tax_rate'])
            total_cents = subtotal_cents + tax_cents
            if submitted_total_cents != total_cents:
                raise ValueError(
                    "Prices changed during checkout. Review the cart and try again."
                )

            if cash_received_cents is not None and (payment_method != "Cash" or isinstance(cash_received_cents, bool) or not isinstance(cash_received_cents, int) or cash_received_cents < total_cents):
                raise ValueError("Invalid or insufficient cash tender.")
            timestamp = business_now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(
                "INSERT INTO transactions "
                "(timestamp, total, total_cents, payment_method, checkout_token, cash_received_cents, tax_rate_percent, receipt_snapshot, customer_id, customer_name_snapshot, discount_cents, discount_kind, discount_value, store_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, authorized_store_id())",
                (timestamp, float(from_cents(total_cents)), total_cents, payment_method, checkout_token, cash_received_cents, str(settings['tax_percent']), receipt_snapshot(settings), customer_id, customer["name"] if customer else None, discount_cents, discount_kind, discount_value),
            )
            transaction_id = cursor.lastrowid

            conn.audit_context.update(type='checkout', reason='Simulated checkout', transaction_id=transaction_id)
            for original, allocation in zip(transaction_items, discount_lines, strict=True):
                product_id, quantity, price_cents, product_name, category_id, category_name, unit_cost = original
                cursor.execute(
                    "UPDATE products SET quantity=quantity-? WHERE store_id=authorized_store_id() AND id=? AND quantity>=? AND is_active=1",
                    (quantity, product_id, quantity))
                if cursor.rowcount != 1:
                    raise ValueError("Stock changed while processing checkout. Please retry.")
                cursor.execute(
                    "INSERT INTO transaction_items "
                    "(transaction_id, product_id, quantity, price, price_cents, product_name, category_id_snapshot, category_name_snapshot, discount_cents, discount_base_cents, discount_bonus_units, unit_cost_cents, store_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, authorized_store_id())",
                    (
                        transaction_id,
                        product_id,
                        quantity,
                        float(from_cents(price_cents)),
                        price_cents,
                        product_name,
                        category_id,
                        category_name,
                        allocation["discount_cents"],
                        allocation["discount_base_cents"],
                        allocation["discount_bonus_units"],
                        unit_cost,
                    ),
                )
    finally:
        conn.close()

    return transaction_id

def view_sales_report():
    """Show today's sales summary and the full transaction history."""
    now = business_now()
    today = now.strftime("%Y-%m-%d")
    display_date = now.strftime("%B %d, %Y").replace(" 0", " ")

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions WHERE store_id=authorized_store_id() ORDER BY id DESC")
        transactions = cursor.fetchall()

        today_transactions = [
            tx for tx in transactions
            if str(tx["timestamp"]).startswith(today)
        ]

        total_transactions = len(today_transactions)
        total_revenue = sum(from_cents(tx["total_cents"]) for tx in today_transactions)
        average_transaction = (
            total_revenue / total_transactions if total_transactions else 0.0
        )

        def payment_method_for(tx):
            value = tx["payment_method"]
            method = value.strip().title() if isinstance(value, str) else ""
            return method if method in ("Cash", "Card") else "Not recorded (legacy sale)"

        cash_revenue = sum(
            from_cents(tx["total_cents"]) for tx in today_transactions
            if payment_method_for(tx) == "Cash"
        )
        card_revenue = sum(
            from_cents(tx["total_cents"]) for tx in today_transactions
            if payment_method_for(tx) == "Card"
        )

        print("\n===================================")
        print("        DAILY SALES REPORT")
        print("===================================")
        print(f"Date: {display_date}")
        print(f"Total transactions:  {total_transactions}")
        print(f"Total revenue:       ${total_revenue:.2f}")
        print(f"Average transaction: ${average_transaction:.2f}")
        print(f"Cash revenue:        ${cash_revenue:.2f}")
        print(f"Card revenue:        ${card_revenue:.2f}")
        print("===================================")

        print("\n      OVERALL TRANSACTION HISTORY")
        print("===================================")

        if not transactions:
            print("No transactions recorded in the database yet.")
            return

        for tx in transactions:
            cursor.execute("""
                SELECT ti.quantity, ti.price_cents, ti.discount_cents, ti.product_name AS name
                FROM transaction_items ti
                WHERE ti.store_id=authorized_store_id() AND ti.transaction_id = ?
            """, (tx["id"],))
            items = cursor.fetchall()

            subtotal = sum(from_cents(item["quantity"] * item["price_cents"] - item["discount_cents"]) for item in items)
            tax = from_cents(tx["total_cents"]) - subtotal

            print("-----------------------------------")
            print(f"Transaction #{tx['id']}")
            print(f"Time: {tx['timestamp']}")
            print(f"Payment: {payment_method_for(tx)}")
            print("\nItems:")

            for item in items:
                line_total = from_cents(item["quantity"] * item["price_cents"] - item["discount_cents"])
                description = f"  {item['name']} x{item['quantity']}"
                print(f"{description:<28}${line_total:>7.2f}")

            print(f"\n{'Subtotal:':<28}${subtotal:>7.2f}")
            print(f"{'Tax:':<28}${tax:>7.2f}")
            print(f"{'Total:':<28}${from_cents(tx['total_cents']):>7.2f}")

        print("-----------------------------------")
    finally:
        conn.close()