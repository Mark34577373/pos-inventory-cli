import tkinter as tk
from datetime import timedelta

from config import business_now
from database.database import get_store_connection as get_db_connection
from services.analytics import ReportLimitError
from services.money import format_cents
from services.sales_totals import read_sales, summarize_sales


def get_today_sales_summary(now=None):
    """Return today's transaction count and revenue split from SQLite."""
    current_time = now or business_now()
    start_of_day = current_time.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    start_of_next_day = start_of_day + timedelta(days=1)

    conn = get_db_connection()
    try:
        rows = read_sales(
            conn,
            start_of_day.strftime("%Y-%m-%d %H:%M:%S"),
            start_of_next_day.strftime("%Y-%m-%d %H:%M:%S"),
        )
        return summarize_sales(rows)
    finally:
        conn.close()


def get_today_best_seller(now=None):
    """Return today's best-selling product by units sold."""
    current_time = now or business_now()
    start_of_day = current_time.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    start_of_next_day = start_of_day + timedelta(days=1)

    conn = get_db_connection()
    try:
        result = conn.execute(
            """
            SELECT
                product_name,
                SUM(quantity) AS units_sold
            FROM transaction_items
            JOIN transactions
                ON transaction_items.transaction_id = transactions.id
            WHERE transactions.store_id=authorized_store_id() AND transaction_items.store_id=authorized_store_id() AND transactions.timestamp >= ?
              AND transactions.timestamp < ?
            GROUP BY product_name
            ORDER BY units_sold DESC
            LIMIT 1
            """,
            (
                start_of_day.strftime("%Y-%m-%d %H:%M:%S"),
                start_of_next_day.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        ).fetchone()

        if result is None:
            return None

        return dict(result)
    finally:
        conn.close()


def create_reports_view(parent_frame):
    """Display live sales totals for the current local day."""
    title = tk.Label(
        parent_frame,
        text="Reports",
        font=("Arial", 22, "bold"),
        bg="#f8f9fa",
        fg="#333333",
    )
    title.pack(anchor=tk.W, pady=(0, 20))

    try:
        summary = get_today_sales_summary()
    except ReportLimitError as error:
        tk.Label(parent_frame, text=str(error), wraplength=500, fg="#b91c1c").pack(anchor=tk.W)
        return
    best_seller = get_today_best_seller()

    best_seller_text = (
        f"{best_seller['product_name']} ({best_seller['units_sold']} sold)"
        if best_seller
        else "No sales yet"
    )

    metrics = (
        (
            "Today's Revenue",
            format_cents(summary["total_revenue_cents"]),
            "#25835f",
        ),
        (
            "Transactions",
            str(summary["transaction_count"]),
            "#347fb6",
        ),
        (
            "Average Sale",
            format_cents(summary["average_sale_rounded_cents"]),
            "#34495e",
        ),
        (
            "Cash",
            format_cents(summary["cash_revenue_cents"]),
            "#d27a35",
        ),
        (
            "Card",
            format_cents(summary["card_revenue_cents"]),
            "#5c6b73",
        ),
        (
            "Best Seller",
            best_seller_text,
            "#8e44ad",
        ),
    )

    metrics_frame = tk.Frame(
        parent_frame,
        bg="#f8f9fa",
    )
    metrics_frame.pack(
        fill=tk.X,
        anchor=tk.W,
    )

    for column in range(3):
        metrics_frame.columnconfigure(
            column,
            weight=1,
            uniform="report-metric",
        )

    for index, (label, value, color) in enumerate(metrics):
        row, column = divmod(index, 3)

        card = tk.Frame(
            metrics_frame,
            bg="white",
            highlightbackground="#e0e0e0",
            highlightthickness=1,
            width=170,
            height=100,
        )

        right_padding = 12 if column < 2 else 0

        card.grid(
            row=row,
            column=column,
            sticky="ew",
            padx=(0, right_padding),
            pady=8,
        )

        card.grid_propagate(False)

        tk.Label(
            card,
            text=label,
            font=("Arial", 10, "bold"),
            fg="#7f8c8d",
            bg="white",
        ).pack(
            anchor=tk.W,
            padx=12,
            pady=(12, 4),
        )

        tk.Label(
            card,
            text=value,
            font=("Arial", 16, "bold"),
            fg=color,
            bg="white",
        ).pack(
            anchor=tk.W,
            padx=12,
        )
