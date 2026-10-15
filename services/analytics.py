"""Bounded, store-authorized analytics over committed sales; never writes data."""

import re
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from config import BUSINESS_TIMEZONE, business_now
from database.database import get_store_connection

MAX_DAYS = 366
MAX_TRANSACTIONS = 10_000
MAX_ITEMS = 50_000
PRESETS = ("today", "yesterday", "last7", "last30", "this_month", "previous_month", "custom")
GROUPS = ("daily", "weekly", "monthly")
RANKINGS = ("quantity", "revenue", "name")


class ReportLimitError(ValueError):
    """A report must be narrowed, rather than silently truncated."""


@dataclass(frozen=True)
class ReportFilters:
    start: date
    end: date  # inclusive business date; queries use next midnight exclusively
    preset: str = "custom"
    group: str = "daily"
    ranking: str = "quantity"
    top: int = 10

    @property
    def days(self):
        return (self.end - self.start).days + 1

    @property
    def lower(self):
        return self.start.isoformat() + " 00:00:00"

    @property
    def upper(self):
        return (self.end + timedelta(days=1)).isoformat() + " 00:00:00"

    @property
    def previous(self):
        return ReportFilters(
            self.start - timedelta(days=self.days),
            self.start - timedelta(days=1),
            group=self.group,
            ranking=self.ranking,
            top=self.top,
        )

    def query_args(self):
        return {
            "preset": "custom",
            "start_date": self.start.isoformat(),
            "end_date": self.end.isoformat(),
            "group": self.group,
            "ranking": self.ranking,
            "top": self.top,
        }

    def utc_boundaries(self):
        """For disclosure only: historical transaction values are local wall time."""
        zone = ZoneInfo(BUSINESS_TIMEZONE)
        return (
            datetime.combine(self.start, time.min, zone).astimezone(timezone.utc).isoformat(),
            datetime.combine(self.end + timedelta(days=1), time.min, zone)
            .astimezone(timezone.utc)
            .isoformat(),
        )


def strict_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Use dates in YYYY-MM-DD format.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("Choose a valid calendar date.") from None


def resolve_filters(args, today=None):
    today = today or business_now().date()
    preset = args.get(
        "preset", "custom" if args.get("start_date") or args.get("end_date") else "last30"
    )
    group = args.get("group", "daily")
    ranking = args.get("ranking", "quantity")
    top = str(args.get("top", "10"))
    if (
        preset not in PRESETS
        or group not in GROUPS
        or ranking not in RANKINGS
        or top not in ("5", "10")
    ):
        raise ValueError("Choose valid analytics filters.")
    if preset == "custom":
        start, end = strict_date(args.get("start_date", "")), strict_date(args.get("end_date", ""))
    elif preset == "today":
        start = end = today
    elif preset == "yesterday":
        start = end = today - timedelta(days=1)
    elif preset in ("last7", "last30"):
        end = today
        start = today - timedelta(days=6 if preset == "last7" else 29)
    elif preset == "this_month":
        start, end = today.replace(day=1), today
    else:
        end = today.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
    if start > end:
        raise ValueError("Start date cannot be later than end date.")
    if (end - start).days + 1 > MAX_DAYS:
        raise ValueError(f"Reports support at most {MAX_DAYS} calendar days.")
    # Keep both comparison and exclusive-end calculations representable.
    if start < date(3, 1, 1) or end > date(9998, 12, 31):
        raise ValueError("Choose dates between 0003-01-01 and 9998-12-31.")
    return ReportFilters(start, end, preset, group, ranking, int(top))


def _transactions(conn, filters):
    rows = conn.execute(
        """SELECT id,timestamp,total_cents,payment_method,tax_rate_percent
        FROM transactions WHERE store_id=authorized_store_id() AND timestamp>=? AND timestamp<?
        ORDER BY timestamp,id LIMIT ?""",
        (filters.lower, filters.upper, MAX_TRANSACTIONS + 1),
    ).fetchall()
    if len(rows) > MAX_TRANSACTIONS:
        raise ReportLimitError(f"Narrow the period to at most {MAX_TRANSACTIONS:,} transactions.")
    return [dict(row) for row in rows]


def _average(total, count):
    return (
        int((Decimal(total) / count).quantize(Decimal(1), rounding=ROUND_HALF_UP)) if count else 0
    )


def comparison(current, previous):
    delta = current - previous
    percent = (
        (Decimal(delta) * 100 / Decimal(previous)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if previous > 0
        else None
    )
    return {
        "previous_cents": previous,
        "change_cents": delta,
        "percent": percent,
        "has_baseline": previous > 0,
    }


def _bucket(day, group):
    if group == "weekly":
        return day - timedelta(days=day.weekday())
    if group == "monthly":
        return day.replace(day=1)
    return day


def _series(filters, transactions):
    buckets = {}
    for offset in range(filters.days):
        day = filters.start + timedelta(days=offset)
        key = _bucket(day, filters.group)
        row = buckets.setdefault(
            key,
            {
                "period_start": day.isoformat(),
                "period_end": day.isoformat(),
                "revenue_cents": 0,
                "transaction_count": 0,
            },
        )
        row["period_end"] = day.isoformat()
    for tx in transactions:
        key = _bucket(date.fromisoformat(tx["timestamp"][:10]), filters.group)
        buckets[key]["revenue_cents"] += tx["total_cents"]
        buckets[key]["transaction_count"] += 1
    rows = list(buckets.values())
    maximum = max((row["revenue_cents"] for row in rows), default=0)
    for row in rows:
        row["average_order_cents"] = _average(row["revenue_cents"], row["transaction_count"])
        row["bar_percent"] = f"{Decimal(row['revenue_cents']) * 100 / maximum:.4f}" if maximum else "0"
    return rows


def build_analytics(filters):
    # Validate dataclass instances too: internal callers cannot inject sorts or
    # accidentally bypass limits. The store always comes from the auth context.
    if filters.preset not in PRESETS:
        raise ValueError("Choose valid analytics filters.")
    filters = replace(resolve_filters(filters.query_args()), preset=filters.preset)
    with closing(get_store_connection()) as conn:
        conn.execute("BEGIN")  # One consistent snapshot for all metrics/exports.
        transactions = _transactions(conn, filters)
        previous = _transactions(conn, filters.previous)
        items = conn.execute(
            """SELECT ti.id,ti.transaction_id,ti.product_id,ti.product_name,
            ti.quantity,ti.price_cents,t.timestamp
            FROM transactions t JOIN transaction_items ti
              ON ti.transaction_id=t.id AND ti.store_id=t.store_id
            WHERE t.store_id=authorized_store_id() AND ti.store_id=authorized_store_id()
              AND t.timestamp>=? AND t.timestamp<?
            ORDER BY t.timestamp,t.id,ti.id LIMIT ?""",
            (filters.lower, filters.upper, MAX_ITEMS + 1),
        ).fetchall()
        if len(items) > MAX_ITEMS:
            raise ReportLimitError(f"Narrow the period to at most {MAX_ITEMS:,} line items.")
        store = dict(
            conn.execute("SELECT id,name FROM stores WHERE id=authorized_store_id()").fetchone()
        )
    # Bounded Python integer aggregation avoids SQLite SUM overflow and floating
    # averages. No current product prices/names/categories are joined here.
    products = {}
    item_totals = defaultdict(int)
    line_counts = defaultdict(int)
    for item in items:
        line = item["quantity"] * item["price_cents"]
        item_totals[item["transaction_id"]] += line
        line_counts[item["transaction_id"]] += 1
        row = products.setdefault(
            item["product_id"],
            {"product_id": item["product_id"], "units_sold": 0, "item_sales_cents": 0},
        )
        row["product_name"] = item["product_name"] or f"Historical product #{item['product_id']}"
        row["units_sold"] += item["quantity"]
        row["item_sales_cents"] += line
    orderings = {
        "quantity": lambda row: (-row["units_sold"], -row["item_sales_cents"], row["product_id"]),
        "revenue": lambda row: (-row["item_sales_cents"], -row["units_sold"], row["product_id"]),
        "name": lambda row: (row["product_name"].casefold(), row["product_id"]),
    }
    ranked = sorted(products.values(), key=orderings[filters.ranking])[: filters.top]
    maximum = max(
        (
            row["units_sold"] if filters.ranking == "quantity" else row["item_sales_cents"]
            for row in ranked
        ),
        default=0,
    )
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
        value = row["units_sold"] if filters.ranking == "quantity" else row["item_sales_cents"]
        row["bar_percent"] = f"{Decimal(value) * 100 / maximum:.4f}" if maximum else "0"
    payment = {}
    unverified = 0
    for tx in transactions:
        method = (
            tx["payment_method"]
            if tx["payment_method"] in ("Cash", "Card")
            else "Not recorded (legacy)"
        )
        row = payment.setdefault(
            method, {"payment_method": method, "transaction_count": 0, "revenue_cents": 0}
        )
        row["transaction_count"] += 1
        row["revenue_cents"] += tx["total_cents"]
        tx["item_sales_cents"] = item_totals[tx["id"]]
        tx["tax_remainder_cents"] = tx["total_cents"] - tx["item_sales_cents"]
        tx["payment_method"] = method
        tx["tax_verified"] = False
        if line_counts[tx["id"]] and tx["tax_rate_percent"] is not None:
            try:
                rate = Decimal(tx["tax_rate_percent"]) / 100
                if rate.is_finite() and 0 <= rate <= 1:
                    from services.money import calculate_tax_cents

                    tx["tax_verified"] = (
                        calculate_tax_cents(tx["item_sales_cents"], rate)
                        == tx["tax_remainder_cents"]
                    )
            except (ValueError, ArithmeticError):
                pass
        if not tx["tax_verified"]:
            unverified += 1
    total = sum(tx["total_cents"] for tx in transactions)
    item_sales = sum(item_totals.values())
    count = len(transactions)
    prior = sum(tx["total_cents"] for tx in previous)
    summary = {
        "revenue_cents": total,
        "item_sales_cents": item_sales,
        "tax_remainder_cents": total - item_sales,
        "transaction_count": count,
        "average_order_cents": _average(total, count),
        "units_sold": sum(row["units_sold"] for row in products.values()),
        "unverified_tax_transactions": unverified,
    }
    return {
        "filters": filters,
        "store": store,
        "timezone": BUSINESS_TIMEZONE,
        "summary": summary,
        "comparison": comparison(total, prior),
        "previous_count": len(previous),
        "series": _series(filters, transactions),
        "products": ranked,
        "product_count": len(products),
        "payment_rows": sorted(
            payment.values(), key=lambda row: (-row["revenue_cents"], row["payment_method"])
        ),
        "transactions": transactions,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
