# services/transaction_service.py
from datetime import datetime
from database.database import get_db_connection

def complete_transaction(cart_items, total_amount, payment_method):
    """Deducts inventory and records a sale atomically in SQLite with payment details."""
    payment_method = (
        payment_method.strip().title()
        if isinstance(payment_method, str)
        else ""
    )
    if payment_method not in ("Cash", "Card"):
        raise ValueError("Payment method must be Cash or Card.")

    conn = get_db_connection()
    try:
        with conn:
            cursor = conn.cursor()

            for item in cart_items:
                cursor.execute(
                    "UPDATE products SET quantity = quantity - ? "
                    "WHERE id = ? AND quantity >= ?",
                    (item["qty"], item["id"], item["qty"])
                )
                if cursor.rowcount != 1:
                    raise ValueError(
                        f"insufficient stock for product ID {item['id']}"
                    )

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
           
            cursor.execute(
                "INSERT INTO transactions (timestamp, total, payment_method) VALUES (?, ?, ?)",
                (timestamp, total_amount, payment_method)
            )
            transaction_id = cursor.lastrowid

            for item in cart_items:
                cursor.execute(
                    "INSERT INTO transaction_items "
                    "(transaction_id, product_id, quantity, price) "
                    "VALUES (?, ?, ?, ?)",
                    (transaction_id, item["id"], item["qty"], item["price"])
                )
    finally:
        conn.close()

    return transaction_id

def view_sales_report():
    """Show today's sales summary and the full transaction history."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    display_date = now.strftime("%B %d, %Y").replace(" 0", " ")

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions ORDER BY id DESC")
        transactions = cursor.fetchall()

        today_transactions = [
            tx for tx in transactions
            if str(tx["timestamp"]).startswith(today)
        ]

        total_transactions = len(today_transactions)
        total_revenue = sum(tx["total"] for tx in today_transactions)
        average_transaction = (
            total_revenue / total_transactions if total_transactions else 0.0
        )

        def payment_method_for(tx):
            value = tx["payment_method"]
            method = value.strip().title() if isinstance(value, str) else ""
            return method if method in ("Cash", "Card") else "Not recorded (legacy sale)"

        cash_revenue = sum(
            tx["total"] for tx in today_transactions
            if payment_method_for(tx) == "Cash"
        )
        card_revenue = sum(
            tx["total"] for tx in today_transactions
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
                SELECT ti.quantity, ti.price, p.name
                FROM transaction_items ti
                JOIN products p ON p.id = ti.product_id
                WHERE ti.transaction_id = ?
            """, (tx["id"],))
            items = cursor.fetchall()

            subtotal = sum(item["quantity"] * item["price"] for item in items)
            tax = tx["total"] - subtotal

            print("-----------------------------------")
            print(f"Transaction #{tx['id']}")
            print(f"Time: {tx['timestamp']}")
            print(f"Payment: {payment_method_for(tx)}")
            print("\nItems:")

            for item in items:
                line_total = item["quantity"] * item["price"]
                description = f"  {item['name']} x{item['quantity']}"
                print(f"{description:<28}${line_total:>7.2f}")

            print(f"\n{'Subtotal:':<28}${subtotal:>7.2f}")
            print(f"{'Tax:':<28}${tax:>7.2f}")
            print(f"{'Total:':<28}${tx['total']:>7.2f}")

        print("-----------------------------------")
    finally:
        conn.close()