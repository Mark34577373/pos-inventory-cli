"""Store-authorized suppliers, purchase orders and atomic idempotent receiving."""

import hashlib
import json
import re
from contextlib import closing, nullcontext
from datetime import date

from flask import g, has_request_context

from config import business_now
from database.database import get_store_connection
from database.tenancy import require_store_write
from services.customers import customer_fields, directory_filters
from services.money import parse_cash
from services.refunds import valid_id
from services.validation import visible_text

SUPPLIER_FIELDS = {"name", "contact", "email", "phone", "address", "notes"}
MAX_LINES = 200


def get_supplier(identifier, conn=None, *, active=False):
    if not valid_id(identifier):
        raise LookupError("Supplier not found.")
    if conn is None:
        with closing(get_store_connection()) as connection:
            return get_supplier(identifier, connection, active=active)
    row = conn.execute(
        "SELECT * FROM suppliers WHERE store_id=authorized_store_id() AND id=?", (identifier,)
    ).fetchone()
    if row is None or (active and not row["is_active"]):
        raise LookupError("Supplier not found or unavailable.")
    return dict(row)


def save_supplier(values, identifier=None):
    require_store_write()
    if set(values) - SUPPLIER_FIELDS:
        raise ValueError("Unsupported supplier fields.")
    fields = customer_fields(
        {key: values.get(key, "") for key in ("name", "email", "phone", "notes")}
    )
    fields.update(
        contact=visible_text(values.get("contact", ""), "Contact", 100, optional=True),
        address=visible_text(
            values.get("address", ""), "Address", 500, optional=True, multiline=True
        ),
    )
    with closing(get_store_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if identifier is None:
            return conn.execute(
                """INSERT INTO suppliers(store_id,name,email,phone,notes,contact,address)
                VALUES(authorized_store_id(),?,?,?,?,?,?)""",
                tuple(fields.values()),
            ).lastrowid
        get_supplier(identifier, conn)
        conn.execute(
            """UPDATE suppliers SET name=?,email=?,phone=?,notes=?,contact=?,address=?,
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE store_id=authorized_store_id() AND id=?""",
            (*fields.values(), identifier),
        )
        return identifier


def deactivate_supplier(identifier):
    require_store_write()
    with closing(get_store_connection()) as conn, conn:
        get_supplier(identifier, conn)
        conn.execute(
            "UPDATE suppliers SET is_active=0,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE store_id=authorized_store_id() AND id=?",
            (identifier,),
        )


def suppliers(filters):
    filters = directory_filters(filters)
    clause, args = "store_id=authorized_store_id()", []
    if filters["state"] != "all":
        clause += " AND is_active=?"
        args.append(int(filters["state"] == "active"))
    if filters["q"]:
        clause += " AND (instr(lower(name),lower(?))>0 OR instr(lower(contact),lower(?))>0)"
        args.extend([filters["q"]] * 2)
    order = {"name": "name COLLATE NOCASE,id", "newest": "id DESC", "oldest": "id"}[filters["sort"]]
    with closing(get_store_connection()) as conn:
        conn.execute("BEGIN")
        count = conn.execute(f"SELECT COUNT(*) FROM suppliers WHERE {clause}", args).fetchone()[0]
        rows = [
            dict(row)
            for row in conn.execute(
                f"SELECT * FROM suppliers WHERE {clause} ORDER BY {order} LIMIT 20 OFFSET ?",
                (*args, (filters["page"] - 1) * 20),
            )
        ]
    return dict(rows=rows, count=count, page=filters["page"], pages=max(1, (count + 19) // 20))


def whole(value, label, *, minimum=1):
    text = str(value)
    if (
        isinstance(value, bool)
        or not text.isascii()
        or not text.isdigit()
        or not minimum <= int(text) <= 1_000_000
    ):
        raise ValueError(f"{label} must be a whole number from {minimum} to 1,000,000.")
    return int(text)


def cost(value):
    result = parse_cash(value)
    if not 0 <= result <= 100_000_000:
        raise ValueError("Unit cost must be between $0.00 and $1,000,000.00.")
    return result


def save_product_cost(identifier, value, supplier_id=None, supplier_sku=""):
    require_store_write()
    if not valid_id(identifier):
        raise LookupError("Product not found.")
    unit_cost = None if value == "" else cost(value)
    supplier_sku = visible_text(supplier_sku, "Supplier SKU", 64, optional=True)
    with closing(get_store_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if supplier_id is not None:
            get_supplier(supplier_id, conn, active=True)
        updated = conn.execute(
            """UPDATE products SET unit_cost_cents=?,supplier_id=?,supplier_sku=?
            WHERE store_id=authorized_store_id() AND id=? AND is_active=1""",
            (unit_cost, supplier_id, supplier_sku, identifier),
        )
        if updated.rowcount != 1:
            raise LookupError("Product not found or unavailable.")


def order_fields(expected_date, notes):
    expected_date = visible_text(expected_date, "Expected date", 10, optional=True)
    if expected_date:
        try:
            if date.fromisoformat(expected_date).isoformat() != expected_date:
                raise ValueError
        except ValueError:
            raise ValueError("Choose a valid expected date.") from None
    return expected_date, visible_text(notes, "Notes", 1000, optional=True, multiline=True)


def get_order(identifier, conn=None):
    if not valid_id(identifier):
        raise LookupError("Purchase order not found.")
    if conn is None:
        with closing(get_store_connection()) as connection:
            connection.execute("BEGIN")
            return get_order(identifier, connection)
    order = conn.execute(
        "SELECT * FROM purchase_orders WHERE store_id=authorized_store_id() AND id=?", (identifier,)
    ).fetchone()
    if order is None:
        raise LookupError("Purchase order not found.")
    lines = [
        dict(row)
        for row in conn.execute(
            """SELECT oi.*,
        COALESCE((SELECT SUM(quantity) FROM purchase_receipt_items ri WHERE ri.store_id=oi.store_id AND ri.order_item_id=oi.id),0) AS received
        FROM purchase_order_items oi WHERE oi.store_id=authorized_store_id() AND oi.order_id=? ORDER BY oi.id LIMIT ?""",
            (identifier, MAX_LINES + 1),
        )
    ]
    if len(lines) > MAX_LINES:
        raise ValueError("Purchase order exceeds its line limit.")
    for line in lines:
        line["remaining"] = line["quantity"] - line["received"]
        line["total_cents"] = line["quantity"] * line["unit_cost_cents"]
    receipts = [
        dict(row)
        for row in conn.execute(
            "SELECT id,business_date,recorded_at FROM purchase_receipts WHERE store_id=authorized_store_id() AND order_id=? ORDER BY id DESC LIMIT 100",
            (identifier,),
        )
    ]
    return dict(
        order=dict(order),
        lines=lines,
        receipts=receipts,
        total_cents=sum(line["total_cents"] for line in lines),
        received_cents=sum(line["received"] * line["unit_cost_cents"] for line in lines),
    )


def create_order(supplier_id, expected_date="", notes=""):
    require_store_write()
    expected_date, notes = order_fields(expected_date, notes)
    with closing(get_store_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        supplier = get_supplier(supplier_id, conn, active=True)
        return conn.execute(
            """INSERT INTO purchase_orders(store_id,supplier_id,supplier_name,expected_date,notes,business_date)
            VALUES(authorized_store_id(),?,?,?,?,?)""",
            (
                supplier_id,
                supplier["name"],
                expected_date,
                notes,
                business_now().date().isoformat(),
            ),
        ).lastrowid


def edit_order(identifier, expected_date, notes):
    require_store_write()
    expected_date, notes = order_fields(expected_date, notes)
    with closing(get_store_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        state = get_order(identifier, conn)
        if state["order"]["status"] != "draft":
            raise ValueError("Only draft orders can be edited.")
        conn.execute(
            "UPDATE purchase_orders SET expected_date=?,notes=? WHERE store_id=authorized_store_id() AND id=?",
            (expected_date, notes, identifier),
        )


def save_order_line(identifier, product_id, quantity, unit_cost, *, remove=False):
    require_store_write()
    if not valid_id(product_id) or not isinstance(remove, bool):
        raise ValueError("Choose an available product.")
    quantity = whole(quantity, "Ordered quantity")
    unit_cost = cost(unit_cost)
    with closing(get_store_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        state = get_order(identifier, conn)
        if state["order"]["status"] != "draft":
            raise ValueError("Only draft lines can be edited.")
        product = conn.execute(
            "SELECT name FROM products WHERE store_id=authorized_store_id() AND id=?"
            + ("" if remove else " AND is_active=1"),
            (product_id,),
        ).fetchone()
        if product is None:
            raise LookupError("Product not found or unavailable.")
        if remove:
            conn.execute(
                "DELETE FROM purchase_order_items WHERE store_id=authorized_store_id() AND order_id=? AND product_id=?",
                (identifier, product_id),
            )
            return
        if len(state["lines"]) >= MAX_LINES and product_id not in {
            row["product_id"] for row in state["lines"]
        }:
            raise ValueError("Purchase orders support at most 200 lines.")
        conn.execute(
            """INSERT INTO purchase_order_items(store_id,order_id,product_id,product_name,quantity,unit_cost_cents)
            VALUES(authorized_store_id(),?,?,?,?,?) ON CONFLICT(order_id,product_id)
            DO UPDATE SET quantity=excluded.quantity,unit_cost_cents=excluded.unit_cost_cents,product_name=excluded.product_name""",
            (identifier, product_id, product["name"], quantity, unit_cost),
        )


def transition_order(identifier, action):
    require_store_write()
    with closing(get_store_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        state = get_order(identifier, conn)
        status = state["order"]["status"]
        if action == "order" and status == "draft" and state["lines"]:
            get_supplier(state["order"]["supplier_id"], conn, active=True)
            active = conn.execute(
                """SELECT COUNT(*) FROM purchase_order_items oi JOIN products p
                ON p.id=oi.product_id AND p.store_id=oi.store_id
                WHERE oi.store_id=authorized_store_id() AND oi.order_id=? AND p.is_active=1""",
                (identifier,),
            ).fetchone()[0]
            if active != len(state["lines"]):
                raise ValueError("Remove inactive products from the draft before ordering.")
            target = "ordered"
        elif action == "cancel" and status in ("draft", "ordered") and not state["receipts"]:
            target = "cancelled"
        else:
            raise ValueError("This purchase order cannot perform that action.")
        conn.execute(
            "UPDATE purchase_orders SET status=? WHERE store_id=authorized_store_id() AND id=?",
            (target, identifier),
        )


def receive_order(identifier, quantities, key):
    require_store_write()
    if not valid_id(identifier):
        raise LookupError("Purchase order not found.")
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", key):
        raise ValueError("Reload the receiving form before confirming.")
    if (
        not isinstance(quantities, dict)
        or not 1 <= len(quantities) <= MAX_LINES
        or any(not valid_id(line) for line in quantities)
    ):
        raise ValueError("Select purchase lines to receive.")
    quantities = {line: whole(qty, "Received quantity") for line, qty in quantities.items()}
    fingerprint = hashlib.sha256(
        json.dumps([identifier, sorted(quantities.items())]).encode()
    ).hexdigest()
    with closing(get_store_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        prior = conn.execute(
            "SELECT id,request_hash FROM purchase_receipts WHERE store_id=authorized_store_id() AND idempotency_key=?",
            (key,),
        ).fetchone()
        if prior:
            if prior["request_hash"] != fingerprint:
                raise ValueError(
                    "This receiving confirmation was already used for different quantities."
                )
            return prior["id"]
        state = get_order(identifier, conn)
        if state["order"]["status"] not in ("ordered", "partially_received"):
            raise ValueError("Only outstanding ordered stock may be received.")
        available = {row["id"]: row for row in state["lines"]}
        for line, qty in quantities.items():
            if line not in available or qty > available[line]["remaining"]:
                raise ValueError("Receiving exceeds the remaining ordered quantity.")
        actor = g.user["id"] if has_request_context() and getattr(g, "user", None) else None
        receipt = conn.execute(
            """INSERT INTO purchase_receipts(store_id,order_id,actor_user_id,business_date,idempotency_key,request_hash)
            VALUES(authorized_store_id(),?,?,?,?,?)""",
            (identifier, actor, business_now().date().isoformat(), key, fingerprint),
        ).lastrowid
        conn.audit_context.update(
            type="restock",
            reason=f"Purchase order #{identifier}, receiving #{receipt}",
            purchase_receipt_id=receipt,
        )
        for line_id, qty in quantities.items():
            line = available[line_id]
            conn.execute(
                """INSERT INTO purchase_receipt_items(store_id,order_id,receipt_id,order_item_id,quantity,unit_cost_cents)
                VALUES(authorized_store_id(),?,?,?,?,?)""",
                (identifier, receipt, line_id, qty, line["unit_cost_cents"]),
            )
            changed = conn.execute(
                """UPDATE products SET quantity=quantity+? WHERE store_id=authorized_store_id()
                AND id=? AND is_active=1 AND quantity<=1000000-?""",
                (qty, line["product_id"], qty),
            )
            if changed.rowcount != 1:
                raise ValueError(
                    "Receiving requires an active product and stock within the inventory limit. Nothing was received."
                )
        target = (
            "received"
            if all(line["remaining"] == quantities.get(line["id"], 0) for line in state["lines"])
            else "partially_received"
        )
        conn.execute(
            "UPDATE purchase_orders SET status=? WHERE store_id=authorized_store_id() AND id=?",
            (target, identifier),
        )
        return receipt


def purchase_report(start=None, end=None, *, connection=None):
    """Bounded snapshot: PO creation dates and separate receiving business dates."""
    from services.analytics import ReportLimitError

    start, end = start or "0001-01-01", end or "9999-12-31"
    try:
        if (
            date.fromisoformat(start).isoformat() != start
            or date.fromisoformat(end).isoformat() != end
            or start > end
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("Choose a valid date range.") from None
    with closing(get_store_connection()) if connection is None else nullcontext(connection) as conn:
        if not conn.in_transaction:
            conn.execute("BEGIN")
        orders = [
            dict(row)
            for row in conn.execute(
                """SELECT * FROM purchase_orders WHERE store_id=authorized_store_id()
            AND business_date BETWEEN ? AND ? ORDER BY id DESC LIMIT 10001""",
                (start, end),
            )
        ]
        lines = conn.execute(
            """SELECT oi.*,o.supplier_id FROM purchase_order_items oi JOIN purchase_orders o ON o.id=oi.order_id AND o.store_id=oi.store_id
            WHERE oi.store_id=authorized_store_id() AND o.business_date BETWEEN ? AND ? LIMIT 50001""",
            (start, end),
        ).fetchall()
        received = conn.execute(
            """SELECT ri.quantity,ri.unit_cost_cents,r.order_id,o.supplier_id,o.supplier_name,r.business_date
            FROM purchase_receipts r JOIN purchase_receipt_items ri ON ri.receipt_id=r.id AND ri.store_id=r.store_id
            JOIN purchase_orders o ON o.id=r.order_id AND o.store_id=r.store_id
            WHERE r.store_id=authorized_store_id() AND r.business_date BETWEEN ? AND ? LIMIT 50001""",
            (start, end),
        ).fetchall()
        if len(orders) > 10000 or len(lines) > 50000 or len(received) > 50000:
            raise ReportLimitError(
                "Narrow the purchasing report: maximum 10,000 orders and 50,000 lines/receiving lines."
            )
        by_order = {order["id"]: dict(order, total_cents=0) for order in orders}
        for line in lines:
            by_order[line["order_id"]]["total_cents"] += line["quantity"] * line["unit_cost_cents"]
        by_supplier, trends = {}, {}
        for order in by_order.values():
            row = by_supplier.setdefault(
                order["supplier_id"],
                dict(
                    id=order["supplier_id"],
                    name=order["supplier_name"],
                    ordered_cents=0,
                    received_cents=0,
                ),
            )
            if order["status"] not in ("draft", "cancelled"):
                row["ordered_cents"] += order["total_cents"]
                trend = trends.setdefault(
                    order["business_date"],
                    dict(date=order["business_date"], ordered_cents=0, received_cents=0),
                )
                trend["ordered_cents"] += order["total_cents"]
        for receipt in received:
            row = by_supplier.setdefault(
                receipt["supplier_id"],
                dict(
                    id=receipt["supplier_id"],
                    name=receipt["supplier_name"],
                    ordered_cents=0,
                    received_cents=0,
                ),
            )
            value = receipt["quantity"] * receipt["unit_cost_cents"]
            row["received_cents"] += value
            trends.setdefault(
                receipt["business_date"],
                dict(date=receipt["business_date"], ordered_cents=0, received_cents=0),
            )["received_cents"] += value
        return dict(
            orders=list(by_order.values()),
            supplier_totals=list(by_supplier.values()),
            ordered_cents=sum(row["ordered_cents"] for row in by_supplier.values()),
            received_cents=sum(row["received_cents"] for row in by_supplier.values()),
            open_count=sum(
                order["status"] in ("ordered", "partially_received") for order in orders
            ),
            trends=sorted(trends.values(), key=lambda row: row["date"]),
            start=start,
            end=end,
        )


def supplier_profile(identifier):
    from services.analytics import ReportLimitError

    with closing(get_store_connection()) as conn:
        supplier = get_supplier(identifier, conn)
        products = [
            dict(row)
            for row in conn.execute(
                """SELECT id,name,unit_cost_cents,supplier_sku FROM products
            WHERE store_id=authorized_store_id() AND supplier_id=? ORDER BY id LIMIT 501""",
                (identifier,),
            )
        ]
        if len(products) > 500:
            raise ReportLimitError("Supplier profile exceeds the 500-linked-product display limit.")
    report = purchase_report()
    return dict(
        supplier=supplier,
        products=products,
        orders=[row for row in report["orders"] if row["supplier_id"] == identifier][:100],
        totals=next(
            (row for row in report["supplier_totals"] if row["id"] == identifier),
            dict(ordered_cents=0, received_cents=0),
        ),
    )
