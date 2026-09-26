# services/transaction_service.py
from datetime import datetime
from database.database import get_db_connection

def complete_transaction(cart_items, total_amount, payment_method):
    """Deducts inventory and records a sale atomically in SQLite with payment details."""
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
            # Day 4 Tweak: Added payment_method column and value binding
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
    """Queries SQLite to generate a comprehensive historic sales report."""
    print("\n===================================")
    print("        DAILY SALES REPORT")
    print("===================================")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM transactions ORDER BY id DESC")
    transactions = cursor.fetchall()
    
    if not transactions:
        print("No transactions recorded in the database yet.")
        conn.close()
        return

    grand_total = 0.0
    for tx in transactions:
        # Day 4 Tweak: Added explicit payment method printing
        print(f"\nTx ID: #{tx['id']} | Time: {tx['timestamp']}")
        print(f"Payment Method: {tx['payment_method']}")
        print("Items purchased:")
        
        # Fetch the concrete items matching this transaction ID
        cursor.execute("""
            SELECT ti.quantity, ti.price, p.name 
            FROM transaction_items ti
            JOIN products p ON ti.product_id = p.id
            WHERE ti.transaction_id = ?
        """, (tx['id'],))
        
        items = cursor.fetchall()
        for item in items:
            print(f"  - {item['name']} x{item['quantity']} (${item['price']:.2f} each)")
            
        print(f"Total Sale: ${tx['total']:.2f}")
        print("-" * 35)
        grand_total += tx["total"]
        
    print(f"\nGRAND TOTAL SALES FOR ALL TIME: ${grand_total:.2f}")
    print("===================================")
    conn.close()

