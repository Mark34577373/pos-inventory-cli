# ui/dashboard.py
import tkinter as tk
from database.database import get_db_connection
from ui.reports_view import get_today_sales_summary


def get_dashboard_data():
    """Load current daily metrics, active products, and recent transactions."""
    daily_sales = get_today_sales_summary()
    conn = get_db_connection()
    try:
        product_count = conn.execute(
            "SELECT COUNT(*) FROM products WHERE is_active = 1"
        ).fetchone()[0]
        recent_transactions = conn.execute(
            """
            SELECT
                t.id,
                t.total,
                t.payment_method,
                COALESCE((
                    SELECT GROUP_CONCAT(item_description, ', ')
                    FROM (
                        SELECT p.name || ' x' || ti.quantity AS item_description
                        FROM transaction_items ti
                        JOIN products p ON p.id = ti.product_id
                        WHERE ti.transaction_id = t.id
                        ORDER BY ti.id
                    )
                ), 'No item details') AS items
            FROM transactions t
            ORDER BY t.id DESC
            LIMIT 5
            """
        ).fetchall()
    finally:
        conn.close()

    return {
        "daily_sales": daily_sales,
        "product_count": product_count,
        "recent_transactions": [dict(row) for row in recent_transactions],
    }


def create_dashboard_view(parent_frame):
    """Render today's store metrics and the latest transactions."""
    data = get_dashboard_data()
    sales = data["daily_sales"]

    tk.Label(
        parent_frame,
        text="Dashboard",
        font=("Arial", 22, "bold"),
        bg="#f8f9fa",
        fg="#333333",
    ).pack(anchor=tk.W, pady=(0, 5))
    tk.Label(
        parent_frame,
        text="Today's store activity",
        font=("Arial", 11),
        bg="#f8f9fa",
        fg="#666666",
    ).pack(anchor=tk.W, pady=(0, 25))

    metrics_frame = tk.Frame(parent_frame, bg="#f8f9fa")
    metrics_frame.pack(fill=tk.X, pady=(0, 28))
    for column in range(3):
        metrics_frame.columnconfigure(column, weight=1, uniform="dashboard-metric")

    metrics = (
        ("Today's Revenue", f"${sales['total_revenue']:.2f}", "#25835f"),
        ("Transactions", str(sales["transaction_count"]), "#347fb6"),
        ("Products", str(data["product_count"]), "#d27a35"),
    )
    for column, (label, value, color) in enumerate(metrics):
        card = tk.Frame(
            metrics_frame,
            bg="white",
            highlightbackground="#e0e0e0",
            highlightthickness=1,
            width=180,
            height=100,
        )
        card.grid(row=0, column=column, sticky="ew", padx=(0, 12), pady=4)
        card.grid_propagate(False)
        tk.Label(
            card,
            text=label,
            font=("Arial", 10, "bold"),
            fg="#7f8c8d",
            bg="white",
        ).pack(anchor=tk.W, padx=12, pady=(12, 4))
        tk.Label(
            card,
            text=value,
            font=("Arial", 18, "bold"),
            fg=color,
            bg="white",
        ).pack(anchor=tk.W, padx=12)

    tk.Label(
        parent_frame,
        text="Recent Transactions",
        font=("Arial", 14, "bold"),
        bg="#f8f9fa",
        fg="#2c3e50",
    ).pack(anchor=tk.W, pady=(0, 10))
    tk.Frame(parent_frame, bg="#e0e0e0", height=1).pack(fill=tk.X, pady=(0, 10))

    if not data["recent_transactions"]:
        tk.Label(
            parent_frame,
            text="No transactions recorded yet.",
            font=("Arial", 11),
            bg="#f8f9fa",
            fg="#7f8c8d",
        ).pack(anchor=tk.W, pady=4)
        return

    for transaction in data["recent_transactions"]:
        row = tk.Frame(parent_frame, bg="#f8f9fa")
        row.pack(fill=tk.X, pady=5)
        receipt = tk.Frame(
            row,
            bg="white",
            highlightbackground="#e0e0e0",
            highlightthickness=1,
            padx=14,
            pady=11,
        )
        receipt.pack(fill=tk.X)
        heading = tk.Frame(receipt, bg="white")
        heading.pack(fill=tk.X, pady=(0, 5))
        tk.Label(
            heading,
            text=f"Transaction #{transaction['id']}",
            font=("Arial", 10, "bold"),
            bg="white",
            fg="#34495e",
        ).pack(side=tk.LEFT)
        tk.Label(
            heading,
            text=f"${transaction['total']:.2f}",
            font=("Arial", 11, "bold"),
            bg="white",
            fg="#25835f",
        ).pack(side=tk.RIGHT)
        tk.Label(
            receipt,
            text=transaction["items"],
            font=("Arial", 10),
            bg="white",
            fg="#333333",
            wraplength=600,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, fill=tk.X, pady=(0, 6))
        payment = transaction["payment_method"]
        if payment not in ("Cash", "Card"):
            payment = "Not recorded"
        tk.Label(
            receipt,
            text=f"Payment: {payment}",
            font=("Arial", 9, "bold"),
            bg="white",
            fg="#737b75",
        ).pack(anchor=tk.W)
