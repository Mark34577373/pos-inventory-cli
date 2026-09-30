import tkinter as tk
from datetime import datetime, timedelta

from database.database import get_db_connection


def get_today_sales_summary(now=None):
    """Return today's transaction count and revenue split from SQLite."""
    current_time = now or datetime.now()
    start_of_day = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_next_day = start_of_day + timedelta(days=1)

    conn = get_db_connection()
    try:
        summary = conn.execute(
            """
            SELECT
                COUNT(*) AS transaction_count,
                COALESCE(SUM(total), 0.0) AS total_revenue,
                COALESCE(AVG(total), 0.0) AS average_sale,
                COALESCE(SUM(
                    CASE WHEN UPPER(payment_method) = 'CASH' THEN total ELSE 0 END
                ), 0.0) AS cash_revenue,
                COALESCE(SUM(
                    CASE WHEN UPPER(payment_method) = 'CARD' THEN total ELSE 0 END
                ), 0.0) AS card_revenue
            FROM transactions
            WHERE timestamp >= ? AND timestamp < ?
            """,
            (
                start_of_day.strftime("%Y-%m-%d %H:%M:%S"),
                start_of_next_day.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        ).fetchone()
        return dict(summary)
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

    summary = get_today_sales_summary()
    metrics = (
        ("Today's Revenue", f"${summary['total_revenue']:.2f}", "#25835f"),
        ("Transactions", str(summary["transaction_count"]), "#347fb6"),
        ("Average Sale", f"${summary['average_sale']:.2f}", "#34495e"),
        ("Cash", f"${summary['cash_revenue']:.2f}", "#d27a35"),
        ("Card", f"${summary['card_revenue']:.2f}", "#5c6b73"),
    )

    metrics_frame = tk.Frame(parent_frame, bg="#f8f9fa")
    metrics_frame.pack(fill=tk.X, anchor=tk.W)
    for column in range(3):
        metrics_frame.columnconfigure(column, weight=1, uniform="report-metric")

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
        ).pack(anchor=tk.W, padx=12, pady=(12, 4))
        tk.Label(
            card,
            text=value,
            font=("Arial", 16, "bold"),
            fg=color,
            bg="white",
        ).pack(anchor=tk.W, padx=12)
