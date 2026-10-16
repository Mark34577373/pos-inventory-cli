"""Authorized, exact-cent simulated refunds. Original sale rows never change."""

import hashlib
import json
import re
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal

from flask import g, has_request_context

from config import business_now
from database.database import get_store_connection
from database.tenancy import require_store_write
from services.analytics import ReportLimitError
from services.money import calculate_tax_cents
from services.validation import visible_text

MAX_LINES = 1000


def valid_id(value):
    return (
        isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 9223372036854775807
    )


def sale_state(conn, transaction_id):
    if not valid_id(transaction_id):
        raise LookupError("Transaction not found.")
    sale = conn.execute(
        "SELECT * FROM transactions WHERE store_id=authorized_store_id() AND id=?",
        (transaction_id,),
    ).fetchone()
    if sale is None:
        raise LookupError("Transaction not found.")
    sale = dict(sale)
    lines = [
        dict(row)
        for row in conn.execute(
            """SELECT ti.*,
        COALESCE((SELECT SUM(ri.quantity) FROM refund_items ri WHERE ri.store_id=ti.store_id AND ri.transaction_item_id=ti.id),0) AS refunded_quantity
        FROM transaction_items ti WHERE ti.store_id=authorized_store_id() AND ti.transaction_id=? ORDER BY ti.id LIMIT ?""",
            (transaction_id, MAX_LINES + 1),
        )
    ]
    if len(lines) > MAX_LINES:
        raise ReportLimitError("This sale exceeds the 1,000-line display/refund limit.")
    summary = conn.execute(
        """SELECT COUNT(*) AS count,COALESCE(MAX(id),0) AS version,COALESCE(SUM(total_cents),0) AS refunded_cents
        FROM refunds WHERE store_id=authorized_store_id() AND transaction_id=?""",
        (transaction_id,),
    ).fetchone()
    history = [
        dict(row)
        for row in conn.execute(
            "SELECT id,recorded_at,reason,total_cents,tax_cents,restock,status FROM refunds WHERE store_id=authorized_store_id() AND transaction_id=? ORDER BY id DESC LIMIT 100",
            (transaction_id,),
        )
    ]
    eligible = (
        bool(lines)
        and isinstance(sale["total_cents"], int)
        and sale["total_cents"] > 0
        and sale["payment_method"] in ("Cash", "Card")
        and all(
            isinstance(line["quantity"], int)
            and 1 <= line["quantity"] <= 1_000_000
            and isinstance(line["price_cents"], int)
            and line["price_cents"] > 0
            and 0 <= line["refunded_quantity"] <= line["quantity"]
            for line in lines
        )
    )
    subtotal = sum(line["quantity"] * line["price_cents"] for line in lines)
    tax = sale["total_cents"] - subtotal
    try:
        rate = Decimal(sale["tax_rate_percent"]) / 100
        eligible = (
            eligible
            and rate.is_finite()
            and 0 <= rate <= 1
            and tax >= 0
            and calculate_tax_cents(subtotal, rate) == tax
        )
    except (ValueError, TypeError, ArithmeticError):
        eligible = False
    # Allocate per original unit without expanding up to millions of units.
    # Equal remainders resolve by line ID, then unit ordinal within that line.
    if eligible:
        remaining = tax
        for line in lines:
            line["unit_tax"], line["remainder"] = divmod(tax * line["price_cents"], subtotal)
            line["bonus_units"] = 0
            remaining -= line["unit_tax"] * line["quantity"]
        for line in sorted(lines, key=lambda row: (-row["remainder"], row["id"])):
            line["bonus_units"] = min(remaining, line["quantity"])
            remaining -= line["bonus_units"]
        assert remaining == 0
    for line in lines:
        line["remaining_quantity"] = line["quantity"] - line["refunded_quantity"]
        line["line_total_cents"] = line["quantity"] * line["price_cents"]
    status = (
        "completed"
        if not summary["count"]
        else ("refunded" if summary["refunded_cents"] == sale["total_cents"] else "partial")
    )
    return dict(
        transaction=sale,
        items=lines,
        eligible=bool(eligible),
        history=history,
        status=status,
        **dict(summary),
    )


def get_sale(transaction_id):
    with closing(get_store_connection()) as conn:
        conn.execute("BEGIN")
        return sale_state(conn, transaction_id)


def normalize_request(quantities, reason, restock):
    if not isinstance(quantities, dict) or not 1 <= len(quantities) <= MAX_LINES:
        raise ValueError("Select eligible items to refund.")
    if any(
        not valid_id(key)
        or isinstance(qty, bool)
        or not isinstance(qty, int)
        or not 1 <= qty <= 1_000_000
        for key, qty in quantities.items()
    ):
        raise ValueError("Refund quantities must be positive whole numbers.")
    reason = visible_text(reason, "Refund reason", 500, multiline=True)
    if not isinstance(restock, bool):
        raise ValueError("Choose whether to restock.")
    return {str(key): quantities[key] for key in sorted(quantities)}, reason.strip(), restock


def quote_refund(state, quantities):
    if not state["eligible"]:
        raise ValueError(
            "This historical sale cannot be reconciled and is not eligible for refunds."
        )
    available = {line["id"]: line for line in state["items"]}
    result = []
    for line_id, qty in quantities.items():
        line = available.get(int(line_id))
        if line is None or qty > line["remaining_quantity"]:
            raise ValueError("Refund quantity exceeds the eligible remaining quantity.")
        start = line["refunded_quantity"]
        tax = (
            line["unit_tax"] * qty
            + min(start + qty, line["bonus_units"])
            - min(start, line["bonus_units"])
        )
        result.append(
            dict(
                transaction_item_id=line["id"],
                product_id=line["product_id"],
                product_name=line["product_name"],
                quantity=qty,
                tax_cents=tax,
                total_cents=qty * line["price_cents"] + tax,
            )
        )
    return dict(
        items=result,
        total_cents=sum(row["total_cents"] for row in result),
        tax_cents=sum(row["tax_cents"] for row in result),
        version=state["version"],
    )


def preview_refund(transaction_id, quantities, reason, restock=False):
    require_store_write()
    quantities, reason, restock = normalize_request(quantities, reason, restock)
    state = get_sale(transaction_id)
    return dict(
        quote_refund(state, quantities), quantities=quantities, reason=reason, restock=restock
    )


def process_refund(
    transaction_id, quantities, reason, restock, key, *, expected_version=None, expected_total=None
):
    require_store_write()
    if not has_request_context() or not getattr(g, "user", None):
        raise PermissionError("An authenticated owner is required for refunds.")
    if not valid_id(transaction_id):
        raise LookupError("Transaction not found.")
    quantities, reason, restock = normalize_request(quantities, reason, restock)
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", key):
        raise ValueError("Reload the refund form before submitting.")
    fingerprint = hashlib.sha256(
        json.dumps([transaction_id, quantities, reason, restock], sort_keys=True).encode()
    ).hexdigest()
    with closing(get_store_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute(
                "SELECT id,request_hash FROM refunds WHERE store_id=authorized_store_id() AND idempotency_key=?",
                (key,),
            ).fetchone()
            if prior:
                if prior["request_hash"] != fingerprint:
                    raise ValueError("This confirmation was already used for a different refund.")
                return prior["id"]
            state = sale_state(conn, transaction_id)
            quote = quote_refund(state, quantities)
            if (expected_version is not None and expected_version != state["version"]) or (
                expected_total is not None and expected_total != quote["total_cents"]
            ):
                raise ValueError("Refund eligibility changed. Preview the refund again.")
            refund_id = conn.execute(
                """INSERT INTO refunds(store_id,transaction_id,actor_user_id,recorded_at,business_date,reason,total_cents,tax_cents,restock,idempotency_key,request_hash)
                VALUES(authorized_store_id(),?,?,?,?,?,?,?,?,?,?)""",
                (
                    transaction_id,
                    g.user["id"],
                    datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                    business_now().date().isoformat(),
                    reason,
                    quote["total_cents"],
                    quote["tax_cents"],
                    int(restock),
                    key,
                    fingerprint,
                ),
            ).lastrowid
            conn.audit_context.update(
                type="return",
                reason=f"Simulated refund #{refund_id}: {reason}"[:500],
                transaction_id=transaction_id,
                refund_id=refund_id,
            )
            for line in quote["items"]:
                conn.execute(
                    """INSERT INTO refund_items(refund_id,transaction_id,store_id,transaction_item_id,quantity,total_cents,tax_cents)
                    VALUES(?,?,authorized_store_id(),?,?,?,?)""",
                    (
                        refund_id,
                        transaction_id,
                        line["transaction_item_id"],
                        line["quantity"],
                        line["total_cents"],
                        line["tax_cents"],
                    ),
                )
                if restock:
                    cursor = conn.execute(
                        "UPDATE products SET quantity=quantity+? WHERE store_id=authorized_store_id() AND id=? AND quantity<=1000000-?",
                        (line["quantity"], line["product_id"], line["quantity"]),
                    )
                    if cursor.rowcount != 1:
                        raise ValueError("Stock cannot be restored safely. No refund was recorded.")
            return refund_id


def get_refund(refund_id):
    if not valid_id(refund_id):
        raise LookupError("Refund not found.")
    with closing(get_store_connection()) as conn:
        conn.execute("BEGIN")
        row = conn.execute(
            "SELECT id,transaction_id,recorded_at,reason,total_cents,tax_cents,restock,status FROM refunds WHERE store_id=authorized_store_id() AND id=?",
            (refund_id,),
        ).fetchone()
        if row is None:
            raise LookupError("Refund not found.")
        items = [
            dict(item)
            for item in conn.execute(
                """SELECT ri.quantity,ri.total_cents,ri.tax_cents,ti.product_name FROM refund_items ri
            JOIN transaction_items ti ON ti.id=ri.transaction_item_id AND ti.store_id=ri.store_id
            WHERE ri.store_id=authorized_store_id() AND ri.refund_id=? ORDER BY ri.id LIMIT ?""",
                (refund_id, MAX_LINES),
            )
        ]
        return dict(refund=dict(row), items=items)
