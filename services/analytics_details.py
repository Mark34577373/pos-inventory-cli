"""Advanced projections share the parent report's read transaction and cohort."""

from collections import defaultdict
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from config import BUSINESS_TIMEZONE


def _percent(numerator, denominator):
    return (
        (Decimal(numerator) * 100 / denominator).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
        if denominator
        else None
    )


def build_details(conn, filters, transactions, items, refunds):
    from services.analytics import MAX_ITEMS, ReportLimitError, _bucket, cohort_sql
    from services.settings import get_settings

    settings = get_settings(conn)
    inventory = [
        dict(row)
        for row in conn.execute("""SELECT p.id,p.name,p.quantity,p.is_active,p.category_id,p.low_stock_threshold,c.name AS category_name,
        b.recorded_at AS history_start FROM products p
        LEFT JOIN categories c ON c.id=p.category_id AND c.store_id=p.store_id
        LEFT JOIN inventory_baselines b ON b.product_id=p.id AND b.store_id=p.store_id
        WHERE p.store_id=authorized_store_id() ORDER BY p.id LIMIT 5001""")
    ]
    if len(inventory) > 5000:
        raise ReportLimitError("Advanced reports support at most 5,000 inventory products.")
    # New products have an initial stock audit rather than a migration baseline.
    openings = {
        r["product_id"]: r["recorded_at"]
        for r in conn.execute("""SELECT product_id,MIN(recorded_at) AS recorded_at
        FROM stock_adjustments WHERE store_id=authorized_store_id() AND adjustment_type='initial' GROUP BY product_id LIMIT 5001""")
    }
    cohort, params = cohort_sql(filters, "t")
    refund_lines = [
        dict(row)
        for row in conn.execute(
            """SELECT ri.quantity,ri.total_cents,ri.tax_cents,ti.product_id,ti.product_name,
        ti.category_id_snapshot,ti.category_name_snapshot,r.business_date,ri.refund_id
        FROM refunds r JOIN transactions t ON t.id=r.transaction_id AND t.store_id=r.store_id
        JOIN refund_items ri ON ri.refund_id=r.id AND ri.store_id=r.store_id
        JOIN transaction_items ti ON ti.id=ri.transaction_item_id AND ti.store_id=ri.store_id
        WHERE r.store_id=authorized_store_id() AND r.business_date>=? AND r.business_date<=?"""
            + cohort
            + " ORDER BY r.id,ri.id LIMIT ?",
            (filters.start.isoformat(), filters.end.isoformat(), *params, MAX_ITEMS + 1),
        )
    ]
    if len(refund_lines) > MAX_ITEMS:
        raise ReportLimitError("Narrow the period: too many refund line items.")
    # Cohort rates: denominator is units sold in this period; numerator is all
    # recorded refunds of precisely those original lines, regardless of refund date.
    cohort_refunds = {
        r["product_id"]: r["qty"]
        for r in conn.execute(
            """SELECT ti.product_id,SUM(ri.quantity) AS qty
        FROM transactions t JOIN transaction_items ti ON ti.transaction_id=t.id AND ti.store_id=t.store_id
        JOIN refund_items ri ON ri.transaction_item_id=ti.id AND ri.store_id=ti.store_id
        WHERE t.store_id=authorized_store_id() AND t.timestamp>=? AND t.timestamp<?"""
            + cohort
            + " GROUP BY ti.product_id LIMIT 5001",
            (filters.lower, filters.upper, *params),
        )
    }
    categories, products = {}, {}
    category_trends, product_trends = defaultdict(lambda: [0, 0, 0, 0]), defaultdict(lambda: [0, 0])

    def category(item):
        cid, name = item["category_id_snapshot"], item["category_name_snapshot"]
        key = "unknown" if name is None else ("uncategorized" if cid is None else str(cid))
        label = "Unknown (historical category not recorded)" if name is None else name
        row = categories.setdefault(
            key,
            dict(
                category=key,
                category_name=label,
                units_sold=0,
                units_refunded=0,
                item_sales_cents=0,
                item_refunds_cents=0,
            ),
        )
        row["category_name"] = label
        return key, row

    def product(item):
        return products.setdefault(
            item["product_id"],
            dict(
                product_id=item["product_id"],
                product_name=item["product_name"] or "Historical product",
                units_sold=0,
                units_refunded=0,
                item_sales_cents=0,
                item_refunds_cents=0,
                refund_ids=set(),
            ),
        )

    for item in items:
        cents = item["quantity"] * item["price_cents"]
        key, cat = category(item)
        prod = product(item)
        prod["product_name"] = item["product_name"] or prod["product_name"]
        for row in (cat, prod):
            row["units_sold"] += item["quantity"]
            row["item_sales_cents"] += cents
        day = date.fromisoformat(item["timestamp"][:10])
        bucket = _bucket(day, "daily" if filters.group == "hourly" else filters.group).isoformat()
        category_trends[key, bucket][0] += cents
        category_trends[key, bucket][2] += item["quantity"]
        product_trends[item["product_id"], bucket][0] += cents
        product_trends[item["product_id"], bucket][1] += item["quantity"]
    for item in refund_lines:
        key, cat = category(item)
        prod = product(item)
        for row in (cat, prod):
            row["units_refunded"] += item["quantity"]
            row["item_refunds_cents"] += item["total_cents"] - item["tax_cents"]
        prod["refund_ids"].add(item["refund_id"])
        day = date.fromisoformat(item["business_date"])
        bucket = _bucket(day, "daily" if filters.group == "hourly" else filters.group).isoformat()
        category_trends[key, bucket][1] += item["total_cents"] - item["tax_cents"]
        category_trends[key, bucket][3] += item["quantity"]
    total_items = sum(r["item_sales_cents"] for r in categories.values())
    for row in categories.values():
        row["share_percent"] = _percent(row["item_sales_cents"], total_items)
        row["net_item_sales_cents"] = row["item_sales_cents"] - row["item_refunds_cents"]
    for row in products.values():
        row["refund_count"] = len(row.pop("refund_ids"))
        row["cohort_refunded_units"] = cohort_refunds.get(row["product_id"], 0)
        row["refund_rate_percent"] = _percent(row["cohort_refunded_units"], row["units_sold"])
        row["net_item_sales_cents"] = row["item_sales_cents"] - row["item_refunds_cents"]
    product_rows = sorted(
        products.values(), key=lambda r: (-r["item_sales_cents"], r["product_id"])
    )
    if len(products) > 5000:
        raise ReportLimitError("Narrow the period to at most 5,000 historical products.")
    current_categories, intelligence = {}, []
    units_on_hand = low = out = 0
    for item in inventory:
        if not item["is_active"]:
            continue
        # Live inventory is explicitly separate from historical basket filtering.
        if filters.product and item["id"] != int(filters.product):
            continue
        if filters.category:
            if filters.category == "unknown":
                continue
            if filters.category == "uncategorized" and item["category_id"] is not None:
                continue
            if filters.category not in ("unknown", "uncategorized") and item["category_id"] != int(
                filters.category
            ):
                continue
        threshold = (
            item["low_stock_threshold"]
            if item["low_stock_threshold"] is not None
            else settings["low_stock_default"]
        )
        status = (
            "out" if item["quantity"] == 0 else ("low" if item["quantity"] <= threshold else "in")
        )
        units_on_hand += item["quantity"]
        low += status == "low"
        out += status == "out"
        key = str(item["category_id"]) if item["category_id"] else "uncategorized"
        row = current_categories.setdefault(
            key, dict(category_name=item["category_name"] or "Uncategorized", units=0, products=0)
        )
        row["units"] += item["quantity"]
        row["products"] += 1
        performance = products.get(item["id"], {})
        history = item["history_start"] or openings.get(item["id"])
        known = bool(
            history
            and datetime.fromisoformat(history).astimezone(ZoneInfo(BUSINESS_TIMEZONE)).date()
            <= filters.start
        )
        units = performance.get("units_sold", 0)
        # Forecast only with at least 7 complete covered days, no payment/cohort
        # restrictions, and actual demand. Zero sales is not missing history.
        complete = filters.end < datetime.now(ZoneInfo(BUSINESS_TIMEZONE)).date()
        eligible = (
            known
            and complete
            and filters.days >= 7
            and units > 0
            and not (filters.payment or filters.category or filters.product)
        )
        velocity = (
            (Decimal(units) / filters.days).quantize(Decimal(".0001"), rounding=ROUND_HALF_UP)
            if eligible
            else None
        )
        days = (
            (Decimal(item["quantity"]) * filters.days / units).quantize(
                Decimal(".1"), rounding=ROUND_HALF_UP
            )
            if eligible
            else None
        )
        intelligence.append(
            dict(
                product_id=item["id"],
                product_name=item["name"],
                quantity=item["quantity"],
                status=status,
                units_sold=units,
                history_known=known,
                slow_moving=known and units <= 1,
                velocity=velocity,
                days_remaining=days,
            )
        )
    category_rows = sorted(
        categories.values(), key=lambda r: (-r["item_sales_cents"], r["category"])
    )
    category_options = [
        dict(row)
        for row in conn.execute(
            "SELECT id,name FROM categories WHERE store_id=authorized_store_id() ORDER BY normalized_name LIMIT 500"
        )
    ]
    if (
        filters.category
        and filters.category not in ("unknown", "uncategorized")
        and not any(str(row["id"]) == filters.category for row in category_options)
    ):
        historic = conn.execute(
            "SELECT category_id_snapshot AS id,category_name_snapshot AS name FROM transaction_items WHERE store_id=authorized_store_id() AND category_id_snapshot=? ORDER BY transaction_items.id DESC LIMIT 1",
            (int(filters.category),),
        ).fetchone()
        if historic:
            category_options.append(dict(historic))
    return dict(
        category_rows=category_rows,
        performance_rows=product_rows,
        category_trends=[
            dict(
                category=key,
                category_name=categories[key]["category_name"],
                period=period,
                item_sales_cents=values[0],
                item_refunds_cents=values[1],
                units_sold=values[2],
                units_refunded=values[3],
            )
            for (key, period), values in sorted(category_trends.items())
        ],
        product_trends=[
            dict(
                product_id=pid,
                product_name=products[pid]["product_name"],
                period=period,
                item_sales_cents=values[0],
                units_sold=values[1],
            )
            for (pid, period), values in sorted(product_trends.items())
        ],
        units_refunded=sum(r["quantity"] for r in refund_lines),
        inventory_summary=dict(
            units_on_hand=units_on_hand, low_stock_count=low, out_of_stock_count=out
        ),
        inventory_intelligence=intelligence,
        stock_categories=list(current_categories.values()),
        product_options=[dict(id=r["id"], name=r["name"]) for r in inventory],
        category_options=category_options,
    )
