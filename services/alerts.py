"""Authorized notification reads/actions; condition generation lives in SQLite."""

import re
from contextlib import closing
from datetime import timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from config import BUSINESS_TIMEZONE, business_now
from database.database import get_store_connection
from database.tenancy import require_store_write
from services.inventory_management import positive_id

PAGE_SIZE = 20
KINDS = ("low", "out", "restored")
SEVERITIES = ("info", "warning", "critical")


def filters_from(args):
    values = {k: args.get(k, "") for k in ("kind", "severity", "read", "state")}
    values["sort"] = args.get("sort", "newest")
    page = str(args.get("page", "1"))
    if (
        values["kind"] not in ("", *KINDS)
        or values["severity"] not in ("", *SEVERITIES)
        or values["read"] not in ("", "unread", "read")
        or values["state"] not in ("", "active", "resolved", "dismissed")
        or values["sort"] not in ("newest", "oldest")
        or not re.fullmatch(r"[0-9]{1,5}", page)
        or not 1 <= int(page) <= 10000
    ):
        raise ValueError("Choose valid notification filters.")
    values["page"] = int(page)
    return values


def notification_summary():
    with closing(get_store_connection()) as conn:
        conn.execute("BEGIN")
        count = conn.execute(
            "SELECT COUNT(*) FROM inventory_alerts WHERE store_id=authorized_store_id() AND read_at IS NULL AND dismissed_at IS NULL"
        ).fetchone()[0]
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT a.* FROM inventory_alerts a JOIN products p ON p.id=a.product_id AND p.store_id=a.store_id WHERE a.store_id=authorized_store_id() AND a.dismissed_at IS NULL AND p.is_active=1 ORDER BY a.id DESC LIMIT 5"
            )
        ]
    return dict(unread=count, recent=rows)


def list_alerts(args):
    filters = filters_from(args)
    conditions, params = ["store_id=authorized_store_id()"], []
    for key in ("kind", "severity"):
        if filters[key]:
            conditions.append(f"{key}=?")
            params.append(filters[key])
    if filters["read"]:
        conditions.append(
            "read_at IS NULL AND dismissed_at IS NULL"
            if filters["read"] == "unread"
            else "read_at IS NOT NULL"
        )
    if filters["state"] == "active":
        conditions.append("resolved_at IS NULL AND dismissed_at IS NULL")
    elif filters["state"] == "resolved":
        conditions.append("resolved_at IS NOT NULL")
    elif filters["state"] == "dismissed":
        conditions.append("dismissed_at IS NOT NULL")
    where = " AND ".join(conditions)
    order = "DESC" if filters["sort"] == "newest" else "ASC"
    with closing(get_store_connection()) as conn:
        conn.execute("BEGIN")
        count = conn.execute(
            f"SELECT COUNT(*) FROM inventory_alerts WHERE {where}", params
        ).fetchone()[0]
        pages = max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(filters["page"], pages)
        rows = [
            dict(r)
            for r in conn.execute(
                f"SELECT * FROM inventory_alerts WHERE {where} ORDER BY id {order} LIMIT ? OFFSET ?",
                (*params, PAGE_SIZE, (page - 1) * PAGE_SIZE),
            )
        ]
    return dict(alerts=rows, total=count, page=page, pages=pages, filters=filters)


def get_alert(alert_id):
    alert_id = positive_id(alert_id)
    with closing(get_store_connection()) as conn:
        row = conn.execute(
            "SELECT * FROM inventory_alerts WHERE id=? AND store_id=authorized_store_id()",
            (alert_id,),
        ).fetchone()
    if row is None:
        raise LookupError("Notification not found.")
    return dict(row)


def update_alert(alert_id, action):
    require_store_write()
    alert_id = positive_id(alert_id)
    if action not in ("read", "dismiss"):
        raise ValueError("Choose a supported notification action.")
    column = "read_at" if action == "read" else "dismissed_at"
    with closing(get_store_connection()) as conn, conn:
        result = conn.execute(
            f"UPDATE inventory_alerts SET {column}=COALESCE({column},strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE id=? AND store_id=authorized_store_id()",
            (alert_id,),
        )
        if result.rowcount != 1:
            raise LookupError("Notification not found.")


def mark_page_read(ids):
    require_store_write()
    if not isinstance(ids, (list, tuple)) or not 1 <= len(ids) <= PAGE_SIZE:
        raise ValueError("Choose 1–20 notifications.")
    ids = list(set(positive_id(value) for value in ids))
    placeholders = ",".join("?" for _ in ids)
    with closing(get_store_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        count = conn.execute(
            f"SELECT COUNT(*) FROM inventory_alerts WHERE store_id=authorized_store_id() AND id IN ({placeholders})",
            ids,
        ).fetchone()[0]
        if count != len(ids):
            raise LookupError("Notification not found.")
        conn.execute(
            f"UPDATE inventory_alerts SET read_at=COALESCE(read_at,strftime('%Y-%m-%dT%H:%M:%fZ','now')) WHERE store_id=authorized_store_id() AND id IN ({placeholders})",
            ids,
        )


def recommendation(product_id, *, today=None):
    """30 complete days of physical depletion; recommendations never write stock."""
    from datetime import datetime

    from services.analytics import MAX_ITEMS, ReportLimitError

    today = today or business_now().date()
    start, week = today - timedelta(days=30), today - timedelta(days=7)
    product_id = positive_id(product_id)
    with closing(get_store_connection()) as conn:
        conn.execute("BEGIN")
        row = conn.execute(
            """SELECT p.*,COALESCE(p.low_stock_threshold,s.low_stock_default) AS effective_threshold,
            COALESCE(b.recorded_at,(SELECT MIN(a.recorded_at) FROM stock_adjustments a WHERE a.store_id=p.store_id AND a.product_id=p.id AND a.adjustment_type='initial')) AS opening
            FROM products p JOIN store_settings s ON s.store_id=p.store_id
            LEFT JOIN inventory_baselines b ON b.product_id=p.id AND b.store_id=p.store_id
            WHERE p.id=? AND p.store_id=authorized_store_id()""",
            (product_id,),
        ).fetchone()
        if row is None:
            raise LookupError("Product not found.")
        product = dict(row)
        sales = conn.execute(
            """SELECT ti.quantity,t.timestamp FROM transactions t JOIN transaction_items ti ON ti.transaction_id=t.id AND ti.store_id=t.store_id
            WHERE t.store_id=authorized_store_id() AND ti.product_id=? AND t.timestamp>=? AND t.timestamp<? ORDER BY t.timestamp,t.id,ti.id LIMIT ?""",
            (
                product_id,
                start.isoformat() + " 00:00:00",
                today.isoformat() + " 00:00:00",
                MAX_ITEMS + 1,
            ),
        ).fetchall()
        returns = conn.execute(
            """SELECT ri.quantity FROM refunds r JOIN refund_items ri ON ri.refund_id=r.id AND ri.store_id=r.store_id
            JOIN transaction_items ti ON ti.id=ri.transaction_item_id AND ti.store_id=ri.store_id
            WHERE r.store_id=authorized_store_id() AND ti.product_id=? AND r.restock=1 AND r.business_date>=? AND r.business_date<? ORDER BY r.id,ri.id LIMIT ?""",
            (product_id, start.isoformat(), today.isoformat(), MAX_ITEMS + 1),
        ).fetchall()
    if len(sales) > MAX_ITEMS or len(returns) > MAX_ITEMS:
        raise ReportLimitError("Recommendation history exceeds the bounded line limit.")
    units30 = sum(r["quantity"] for r in sales)
    units7 = sum(r["quantity"] for r in sales if r["timestamp"][:10] >= week.isoformat())
    restocked = sum(r["quantity"] for r in returns)
    depletion = max(0, units30 - restocked)
    covered = bool(
        product["opening"]
        and datetime.fromisoformat(product["opening"])
        .astimezone(ZoneInfo(BUSINESS_TIMEZONE))
        .date()
        <= start
    )
    eligible = covered and depletion > 0 and bool(product["is_active"])
    daily = Decimal(depletion) / 30 if eligible else None
    target = (
        max(
            product["effective_threshold"] + 1,
            int((daily * 14).to_integral_value(rounding=ROUND_CEILING)),
        )
        if eligible
        else None
    )
    return dict(
        product=product,
        start=start,
        end=today - timedelta(days=1),
        units7=units7,
        units30=units30,
        restocked30=restocked,
        depletion=depletion,
        covered=covered,
        daily=None if daily is None else daily.quantize(Decimal(".0001"), rounding=ROUND_HALF_UP),
        days_remaining=None
        if daily is None
        else (Decimal(product["quantity"]) / daily).quantize(Decimal(".1"), rounding=ROUND_HALF_UP),
        target=target,
        suggested=None
        if target is None or target > 1_000_000
        else max(0, target - product["quantity"]),
    )
