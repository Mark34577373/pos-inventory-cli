"""Operational estimates from persisted sales, original costs and expense ledger.

No current acquisition cost is ever applied to a historical sale. Purchasing is
reported separately, never automatically expensed. All components share one read
snapshot so an export cannot mix a concurrently completed sale/refund/receiving.
"""

from contextlib import closing

from database.database import get_store_connection
from services.analytics import MAX_ITEMS, ReportLimitError, build_analytics
from services.expenses import expense_report
from services.purchasing import purchase_report


def financial_report(filters):
    if filters.product or filters.category or filters.payment:
        raise ValueError("Business financial reports support store-wide period filters only.")
    with closing(get_store_connection()) as conn:
        conn.execute("BEGIN")
        sales = build_analytics(filters, connection=conn)
        purchases = purchase_report(
            filters.start.isoformat(), filters.end.isoformat(), connection=conn
        )
        expenses = expense_report(
            dict(start=filters.start.isoformat(), end=filters.end.isoformat()), connection=conn
        )
        items = [
            dict(row)
            for row in conn.execute(
                """SELECT ti.*,t.timestamp FROM transaction_items ti JOIN transactions t
            ON t.id=ti.transaction_id AND t.store_id=ti.store_id WHERE ti.store_id=authorized_store_id()
            AND t.timestamp>=? AND t.timestamp<? ORDER BY t.timestamp,t.id,ti.id LIMIT ?""",
                (filters.lower, filters.upper, MAX_ITEMS + 1),
            )
        ]
        refunds = [
            dict(row)
            for row in conn.execute(
                """SELECT ri.quantity,r.restock,r.business_date,ri.total_cents,ri.tax_cents,
            ti.product_id,ti.product_name,ti.category_id_snapshot,ti.category_name_snapshot,ti.unit_cost_cents
            FROM refunds r JOIN refund_items ri ON ri.refund_id=r.id AND ri.store_id=r.store_id
            JOIN transaction_items ti ON ti.id=ri.transaction_item_id AND ti.store_id=ri.store_id AND ti.transaction_id=ri.transaction_id
            WHERE r.store_id=authorized_store_id() AND r.business_date BETWEEN ? AND ?
            ORDER BY r.business_date,r.id,ri.id LIMIT ?""",
                (filters.start.isoformat(), filters.end.isoformat(), MAX_ITEMS + 1),
            )
        ]
        if len(items) > MAX_ITEMS or len(refunds) > MAX_ITEMS:
            raise ReportLimitError(
                "Narrow the financial report to at most 50,000 sale/refund lines."
            )
    products, categories, periods = {}, {}, {}

    def buckets(item, day):
        product = products.setdefault(
            item["product_id"],
            dict(
                id=item["product_id"],
                name=item["product_name"] or "Historical product",
                revenue_cents=0,
                cogs_cents=0,
                unknown_units=0,
            ),
        )
        category_key = item["category_id_snapshot"]
        if category_key is None:
            category_key = "unknown" if item["category_name_snapshot"] is None else "uncategorized"
        category = categories.setdefault(
            category_key,
            dict(
                id=category_key,
                name=item["category_name_snapshot"] or "Unknown historical category",
                revenue_cents=0,
                cogs_cents=0,
                unknown_units=0,
            ),
        )
        period = periods.setdefault(
            day, dict(id=day, name=day, revenue_cents=0, cogs_cents=0, unknown_units=0)
        )
        return product, category, period

    for item in items:
        revenue = item["quantity"] * item["price_cents"] - item["discount_cents"]
        for row in buckets(item, item["timestamp"][:10]):
            row["revenue_cents"] += revenue
            if item["unit_cost_cents"] is None:
                row["unknown_units"] += item["quantity"]
            else:
                row["cogs_cents"] += item["unit_cost_cents"] * item["quantity"]
    for item in refunds:
        for row in buckets(item, item["business_date"]):
            row["revenue_cents"] -= item["total_cents"] - item["tax_cents"]
            if item["restock"]:
                if item["unit_cost_cents"] is None:
                    row["unknown_units"] += item["quantity"]
                else:
                    row["cogs_cents"] -= item["unit_cost_cents"] * item["quantity"]
    for rows in (products, categories, periods):
        for row in rows.values():
            row["gross_profit_cents"] = (
                None if row["unknown_units"] else row["revenue_cents"] - row["cogs_cents"]
            )
    summary = sales["summary"]
    tax_complete = all(tx["tax_verified"] for tx in sales["transactions"])
    if not tax_complete:
        for dimension in (products, categories, periods):
            for row in dimension.values():
                row["gross_profit_cents"] = None
    unknown = sum(row["unknown_units"] for row in products.values())
    cogs = sum(row["cogs_cents"] for row in products.values())
    tax = summary["verified_tax_cents"] - summary["refunded_tax_cents"]
    revenue = summary["net_sales_cents"] - tax if tax_complete else None
    profit = revenue - cogs if tax_complete and not unknown else None
    return dict(
        filters=filters,
        sales=sales,
        purchases=purchases,
        expenses=expenses,
        original_merchandise_cents=sum(item["quantity"] * item["price_cents"] for item in items),
        discount_cents=sum(item["discount_cents"] for item in items),
        tax_complete=tax_complete,
        unknown_cost_units=unknown,
        known_cogs_cents=cogs,
        net_tax_cents=tax,
        net_revenue_cents=revenue,
        gross_profit_cents=profit,
        operating_result_cents=None if profit is None else profit - expenses["total_cents"],
        products=list(products.values()),
        categories=list(categories.values()),
        periods=sorted(periods.values(), key=lambda row: row["id"]),
    )
