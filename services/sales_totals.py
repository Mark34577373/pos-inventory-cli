"""Bounded, exact gross-sale statistics shared by web and desktop readers."""

from fractions import Fraction

from services.analytics import MAX_TRANSACTIONS, ReportLimitError


def read_sales(conn, lower, upper):
    rows = conn.execute(
        """SELECT timestamp,total_cents,payment_method FROM transactions
        WHERE store_id=authorized_store_id() AND timestamp>=? AND timestamp<?
        ORDER BY timestamp,id LIMIT ?""",
        (lower, upper, MAX_TRANSACTIONS + 1),
    ).fetchall()
    if len(rows) > MAX_TRANSACTIONS:
        raise ReportLimitError(
            "Sales overview supports at most 10,000 sales. Use a narrower analytics period."
        )
    return rows


def summarize_sales(rows):
    count = len(rows)
    total = sum(row["total_cents"] for row in rows)
    whole, remainder = divmod(total, count) if count else (0, 0)
    return dict(
        transaction_count=count,
        total_revenue_cents=total,
        # Preserve the fractional-cent numeric statistic, now an exact rational.
        average_sale_cents=Fraction(total, count) if count else Fraction(0),
        average_sale_rounded_cents=whole + int(count > 0 and remainder * 2 >= count),
        cash_revenue_cents=sum(
            row["total_cents"] for row in rows if (row["payment_method"] or "").upper() == "CASH"
        ),
        card_revenue_cents=sum(
            row["total_cents"] for row in rows if (row["payment_method"] or "").upper() == "CARD"
        ),
    )
