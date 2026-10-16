"""Append-only business expenses. Corrections reverse and replace atomically."""

import hashlib
import json
import re
from contextlib import closing, nullcontext
from datetime import date

from flask import g, has_request_context

from config import business_now
from database.database import get_store_connection
from database.tenancy import require_store_write
from services.analytics import ReportLimitError
from services.money import parse_cash
from services.purchasing import get_supplier
from services.refunds import valid_id
from services.validation import visible_text

CATEGORIES = (
    "Rent",
    "Utilities",
    "Supplies",
    "Equipment",
    "Marketing",
    "Transportation",
    "Software",
    "Miscellaneous",
)
FIELDS = {"category", "description", "amount", "expense_date", "supplier_id", "notes"}
MAX_ROWS = 10000


def expense_fields(values):
    if set(values) - FIELDS or values.get("category") not in CATEGORIES:
        raise ValueError("Choose a supported expense category and fields.")
    description = visible_text(values.get("description", ""), "Description", 200)
    notes = visible_text(values.get("notes", ""), "Notes", 1000, optional=True, multiline=True)
    amount = parse_cash(values.get("amount", ""))
    if amount > 10_000_000_000:
        raise ValueError("Expense exceeds the $100,000,000 limit.")
    expense_date = values.get("expense_date", "")
    try:
        if (
            not isinstance(expense_date, str)
            or date.fromisoformat(expense_date).isoformat() != expense_date
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("Choose a valid expense date.") from None
    supplier = values.get("supplier_id") or None
    if supplier is not None and not valid_id(supplier):
        raise ValueError("Choose a valid supplier.")
    return dict(
        category=values["category"],
        description=description,
        amount_cents=amount,
        expense_date=expense_date,
        supplier_id=supplier,
        notes=notes,
    )


def get_expense(identifier, conn=None):
    if not valid_id(identifier):
        raise LookupError("Expense not found.")
    if conn is None:
        with closing(get_store_connection()) as connection:
            return get_expense(identifier, connection)
    row = conn.execute(
        """SELECT e.*,(SELECT id FROM expenses r WHERE r.store_id=e.store_id AND r.reverses_id=e.id) AS reversed_by
        FROM expenses e WHERE e.store_id=authorized_store_id() AND e.id=?""",
        (identifier,),
    ).fetchone()
    if row is None:
        raise LookupError("Expense not found.")
    return dict(row)


def _key_hash(key, payload):
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", key):
        raise ValueError("Reload the expense form before confirming.")
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _prior(conn, key, fingerprint):
    prior = conn.execute(
        "SELECT id,request_hash FROM expenses WHERE store_id=authorized_store_id() AND idempotency_key=?",
        (key,),
    ).fetchone()
    if prior:
        if prior["request_hash"] != fingerprint:
            raise ValueError("This expense confirmation was already used for different data.")
        return prior["id"]
    return None


def _insert(conn, values, key, fingerprint, *, reverses_id=None):
    actor = g.user["id"] if has_request_context() and getattr(g, "user", None) else None
    return conn.execute(
        """INSERT INTO expenses(store_id,category,description,amount_cents,expense_date,supplier_id,notes,
        actor_user_id,entry_kind,reverses_id,idempotency_key,request_hash)
        VALUES(authorized_store_id(),?,?,?,?,?,?,?,?,?,?,?)""",
        (
            values["category"],
            values["description"],
            values["amount_cents"],
            values["expense_date"],
            values["supplier_id"],
            values["notes"],
            actor,
            "reversal" if reverses_id else "expense",
            reverses_id,
            key,
            fingerprint,
        ),
    ).lastrowid


def create_expense(values, key):
    require_store_write()
    fields = expense_fields(values)
    fingerprint = _key_hash(key, ["create", fields])
    with closing(get_store_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        prior = _prior(conn, key, fingerprint)
        if prior:
            return prior
        if fields["supplier_id"] is not None:
            get_supplier(fields["supplier_id"], conn)
        return _insert(conn, fields, key, fingerprint)


def correct_expense(identifier, values, reason, key, *, reverse_only=False):
    require_store_write()
    if not isinstance(reverse_only, bool):
        raise ValueError("Choose a supported correction action.")
    reason = visible_text(reason, "Correction reason", 1000)
    fields = None if reverse_only else expense_fields(values)
    fingerprint = _key_hash(
        key, ["reverse" if reverse_only else "correct", identifier, fields, reason]
    )
    with closing(get_store_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        prior = _prior(conn, key, fingerprint)
        if prior:
            return prior
        source = get_expense(identifier, conn)
        if source["entry_kind"] != "expense" or source["reversed_by"]:
            raise ValueError("Only an unreversed expense can be corrected or reversed.")
        if fields and fields["supplier_id"] is not None:
            get_supplier(fields["supplier_id"], conn)
        reversal = dict(source, notes=reason, expense_date=business_now().date().isoformat())
        if reverse_only:
            return _insert(conn, reversal, key, fingerprint, reverses_id=identifier)
        reversal_key = hashlib.sha256(("reversal_" + key).encode()).hexdigest()
        _insert(conn, reversal, reversal_key, fingerprint, reverses_id=identifier)
        return _insert(conn, fields, key, fingerprint)


def expense_filters(values):
    start, end = values.get("start") or "0001-01-01", values.get("end") or "9999-12-31"
    try:
        if (
            date.fromisoformat(start).isoformat() != start
            or date.fromisoformat(end).isoformat() != end
            or start > end
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("Choose a valid date range.") from None
    category, sort = values.get("category", ""), values.get("sort", "newest")
    if category and category not in CATEGORIES or sort not in ("newest", "oldest", "amount"):
        raise ValueError("Choose supported expense filters.")
    page = str(values.get("page", "1"))
    if not page.isascii() or not page.isdigit() or not 1 <= int(page) <= 100000:
        raise ValueError("Choose a valid page.")
    return dict(
        start=start,
        end=end,
        category=category,
        sort=sort,
        page=int(page),
        q=visible_text(values.get("q", ""), "Search", 100, optional=True),
    )


def expense_report(filters, *, connection=None):
    filters = expense_filters(filters)
    clause, params = (
        "e.store_id=authorized_store_id() AND e.expense_date BETWEEN ? AND ?",
        [filters["start"], filters["end"]],
    )
    if filters["category"]:
        clause += " AND e.category=?"
        params.append(filters["category"])
    if filters["q"]:
        clause += " AND instr(lower(e.description),lower(?))>0"
        params.append(filters["q"])
    order = {
        "newest": "e.expense_date DESC,e.id DESC",
        "oldest": "e.expense_date,e.id",
        "amount": "e.amount_cents DESC,e.id DESC",
    }[filters["sort"]]
    with closing(get_store_connection()) if connection is None else nullcontext(connection) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                f"""SELECT e.*,
            (SELECT id FROM expenses r WHERE r.store_id=e.store_id AND r.reverses_id=e.id) AS reversed_by
            FROM expenses e WHERE {clause} ORDER BY {order} LIMIT ?""",
                (*params, MAX_ROWS + 1),
            )
        ]
    if len(rows) > MAX_ROWS:
        raise ReportLimitError("Narrow the expense report to at most 10,000 entries.")
    breakdown = {category: 0 for category in CATEGORIES}
    for row in rows:
        row["signed_cents"] = row["amount_cents"] * (-1 if row["entry_kind"] == "reversal" else 1)
        breakdown[row["category"]] += row["signed_cents"]
    count = len(rows)
    page = min(filters["page"], max(1, (count + 19) // 20))
    return dict(
        rows=rows,
        paged_rows=rows[(page - 1) * 20 : page * 20],
        count=count,
        page=page,
        pages=max(1, (count + 19) // 20),
        total_cents=sum(row["signed_cents"] for row in rows),
        breakdown=breakdown,
        filters=filters,
    )
