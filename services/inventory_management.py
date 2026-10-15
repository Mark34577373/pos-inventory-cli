"""Store-scoped categories, inventory discovery, alerts, and stock history."""

import re
import sqlite3
import unicodedata
from contextlib import closing
from datetime import date

from database.database import get_store_connection
from database.tenancy import require_store_write

DEFAULT_THRESHOLD = 5
PAGE_SIZE = 20
ADJUSTMENT_TYPES = ("initial", "restock", "correction", "damaged", "return", "checkout")
SORTS = {
    "name": "p.name COLLATE NOCASE",
    "price": "p.price_cents",
    "quantity": "p.quantity",
    "updated": "p.updated_at",
}
UNSET = object()


def category_key(name):
    return " ".join(unicodedata.normalize("NFKC", name).split()).casefold()


def category_name(name):
    if not isinstance(name, str):
        raise ValueError("Enter a category name.")
    name = " ".join(unicodedata.normalize("NFKC", name).split())
    if not 1 <= len(name) <= 80 or any(unicodedata.category(char).startswith("C") for char in name):
        raise ValueError("Category names must contain 1–80 visible characters.")
    return name


def positive_id(value):
    if isinstance(value, bool):
        raise ValueError("Choose a valid identifier.")
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        raise ValueError("Choose a valid identifier.") from None
    if not 0 < parsed <= 9223372036854775807:
        raise ValueError("Choose a valid identifier.")
    return parsed


def whole_number(value, minimum=0, maximum=1_000_000):
    if isinstance(value, bool):
        raise ValueError("Enter a whole-number quantity.")
    try:
        text = str(value)
        if not re.fullmatch(r"-?[0-9]{1,7}", text):
            raise ValueError
        number = int(text)
    except (ValueError, TypeError):
        raise ValueError("Enter a whole-number quantity.") from None
    if not minimum <= number <= maximum:
        raise ValueError(f"Quantity must be between {minimum} and {maximum}.")
    return number


def stock_status(product):
    quantity = product["quantity"]
    threshold = product.get("low_stock_threshold")
    threshold = DEFAULT_THRESHOLD if threshold is None else threshold
    return "out" if quantity <= 0 else ("low" if quantity <= threshold else "in")


def list_categories():
    with closing(get_store_connection()) as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT id,name FROM categories WHERE store_id=authorized_store_id() ORDER BY normalized_name,id LIMIT 500"
            )
        ]


def save_category(name, category_id=None):
    require_store_write()
    name = category_name(name)
    if category_id is not None:
        category_id = positive_id(category_id)
    with closing(get_store_connection()) as conn:
        try:
            with conn:
                if category_id is None:
                    conn.execute("BEGIN IMMEDIATE")
                    if (
                        conn.execute(
                            "SELECT COUNT(*) FROM categories WHERE store_id=authorized_store_id()"
                        ).fetchone()[0]
                        >= 500
                    ):
                        raise ValueError("A store supports up to 500 categories.")
                    return conn.execute(
                        "INSERT INTO categories(store_id,name,normalized_name) VALUES (authorized_store_id(),?,?)",
                        (name, category_key(name)),
                    ).lastrowid
                result = conn.execute(
                    "UPDATE categories SET name=?,normalized_name=? WHERE id=? AND store_id=authorized_store_id()",
                    (name, category_key(name), category_id),
                )
                if result.rowcount != 1:
                    raise ValueError("Category is unavailable.")
                return category_id
        except sqlite3.IntegrityError:
            raise ValueError("That category name is unavailable in this store.") from None


def delete_category(category_id):
    require_store_write()
    category_id = positive_id(category_id)
    with closing(get_store_connection()) as conn:
        with conn:
            result = conn.execute(
                "DELETE FROM categories WHERE id=? AND store_id=authorized_store_id()",
                (category_id,),
            )
            if result.rowcount != 1:
                raise ValueError("Category is unavailable.")


def clean_metadata(conn, category_id=None, sku=None, threshold=None):
    if category_id in ("", None):
        category_id = None
    else:
        category_id = positive_id(category_id)
        if (
            conn.execute(
                "SELECT 1 FROM categories WHERE id=? AND store_id=authorized_store_id()",
                (category_id,),
            ).fetchone()
            is None
        ):
            raise ValueError("Category is unavailable.")
    if sku in ("", None):
        sku = None
    elif isinstance(sku, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", sku.strip()):
        sku = sku.strip().upper()
    else:
        raise ValueError("SKU must contain 1–64 letters, digits, dots, hyphens, or underscores.")
    threshold = None if threshold in ("", None) else whole_number(threshold)
    return category_id, sku, threshold


def inventory_filters(args):
    filters = {
        "q": args.get("q", "").strip(),
        "category": args.get("category", ""),
        "status": args.get("status", ""),
        "active": args.get("active", "active"),
        "sort": args.get("sort", "name"),
        "direction": args.get("direction", "asc"),
    }
    if len(filters["q"]) > 100:
        raise ValueError("Search must be 100 characters or fewer.")
    if filters["status"] not in ("", "in", "low", "out") or filters["active"] not in (
        "active",
        "inactive",
        "all",
    ):
        raise ValueError("Choose valid inventory filters.")
    if filters["sort"] not in SORTS or filters["direction"] not in ("asc", "desc"):
        raise ValueError("Choose a valid inventory sort order.")
    if filters["category"] not in ("", "uncategorized"):
        filters["category"] = str(positive_id(filters["category"]))
    filters["page"] = whole_number(args.get("page", "1"), 1, 100_000)
    return filters


def query_inventory(filters):
    filters = inventory_filters(filters)
    clauses = ["p.store_id=authorized_store_id()"]
    params = []
    if filters["active"] != "all":
        clauses.append("p.is_active=?")
        params.append(int(filters["active"] == "active"))
    if filters["q"]:
        # Escape LIKE wildcards so literal input cannot unexpectedly match all.
        pattern = filters["q"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append(
            "(p.name LIKE ? ESCAPE '\\' OR p.sku LIKE ? ESCAPE '\\' OR CAST(p.id AS TEXT)=?)"
        )
        params.extend([f"%{pattern}%", f"%{pattern}%", filters["q"]])
    category = filters["category"]
    if category == "uncategorized":
        clauses.append("p.category_id IS NULL")
    elif category:
        clauses.append("p.category_id=?")
        params.append(int(category))
    statuses = {
        "in": "p.quantity>COALESCE(p.low_stock_threshold,5)",
        "low": "p.quantity>0 AND p.quantity<=COALESCE(p.low_stock_threshold,5)",
        "out": "p.quantity=0",
    }
    if filters["status"]:
        clauses.append("(" + statuses[filters["status"]] + ")")
    where = " AND ".join(clauses)
    with closing(get_store_connection()) as conn:
        totals = dict(
            conn.execute(
                f"""SELECT COUNT(*) AS total_products,
            COALESCE(SUM(CASE WHEN p.quantity>0 AND p.quantity<=COALESCE(p.low_stock_threshold,5) THEN 1 ELSE 0 END),0) AS low_stock_count,
            COALESCE(SUM(CASE WHEN p.quantity=0 THEN 1 ELSE 0 END),0) AS out_of_stock_count,
            COALESCE(SUM(p.price_cents*p.quantity),0) AS inventory_value
            FROM products p WHERE {where}""",
                params,
            ).fetchone()
        )
        pages = max(1, (totals["total_products"] + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(filters["page"], pages)
        rows = conn.execute(
            f"""SELECT p.*,c.name AS category_name FROM products p
            LEFT JOIN categories c ON c.id=p.category_id AND c.store_id=p.store_id
            WHERE {where} ORDER BY {SORTS[filters["sort"]]} {filters["direction"]}, p.id {filters["direction"]} LIMIT ? OFFSET ?""",
            [*params, PAGE_SIZE, (page - 1) * PAGE_SIZE],
        ).fetchall()
    return {"products": [dict(row) for row in rows], "page": page, "pages": pages, **totals}


def adjust_stock(product_id, amount, adjustment_type, reason, *, expected_quantity=None):
    require_store_write()
    product_id = positive_id(product_id)
    if adjustment_type not in ("restock", "correction", "damaged", "return"):
        raise ValueError("Choose a supported stock adjustment type.")
    amount = whole_number(amount, 0 if adjustment_type == "correction" else 1)
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
        raise ValueError("An adjustment reason of 1–500 characters is required.")
    if expected_quantity is not None:
        expected_quantity = whole_number(expected_quantity)
    with closing(get_store_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            product = conn.execute(
                "SELECT quantity FROM products WHERE id=? AND store_id=authorized_store_id() AND is_active=1",
                (product_id,),
            ).fetchone()
            if product is None:
                raise ValueError("Product is unavailable.")
            before = product["quantity"]
            if expected_quantity is not None and before != expected_quantity:
                raise ValueError("Stock changed. Reload this form before applying a correction.")
            after = (
                amount
                if adjustment_type == "correction"
                else before + (-amount if adjustment_type == "damaged" else amount)
            )
            whole_number(after)
            conn.audit_context.update(type=adjustment_type, reason=reason.strip())
            conn.execute(
                "UPDATE products SET quantity=? WHERE id=? AND store_id=authorized_store_id()",
                (after, product_id),
            )
    return after


def history_filters(args):
    filters = {
        "product_id": args.get("product_id", ""),
        "type": args.get("type", ""),
        "start_date": args.get("start_date", ""),
        "end_date": args.get("end_date", ""),
    }
    if filters["product_id"]:
        filters["product_id"] = str(positive_id(filters["product_id"]))
    if filters["type"] and filters["type"] not in ADJUSTMENT_TYPES:
        raise ValueError("Choose a valid adjustment type.")
    try:
        for key in ("start_date", "end_date"):
            if filters[key]:
                filters[key] = date.fromisoformat(filters[key]).isoformat()
        if (
            filters["start_date"]
            and filters["end_date"]
            and filters["start_date"] > filters["end_date"]
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("Choose a valid history date range.") from None
    filters["page"] = whole_number(args.get("page", "1"), 1, 100_000)
    return filters


def stock_history(filters, adjustment_id=None):
    filters = history_filters(filters)
    clauses = ["store_id=authorized_store_id()"]
    params = []
    if adjustment_id is not None:
        clauses.append("id=?")
        params.append(positive_id(adjustment_id))
    if filters["product_id"]:
        clauses.append("product_id=?")
        params.append(int(filters["product_id"]))
    if filters["type"]:
        clauses.append("adjustment_type=?")
        params.append(filters["type"])
    if filters["start_date"]:
        clauses.append("recorded_at>=?")
        params.append(filters["start_date"] + "T00:00:00.000Z")
    if filters["end_date"]:
        clauses.append("substr(recorded_at,1,10)<=?")
        params.append(filters["end_date"])
    where = " AND ".join(clauses)
    with closing(get_store_connection()) as conn:
        count = conn.execute(
            f"SELECT COUNT(*) FROM stock_adjustments WHERE {where}", params
        ).fetchone()[0]
        pages = max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(filters["page"], pages)
        rows = [
            dict(row)
            for row in conn.execute(
                f"SELECT * FROM stock_adjustments WHERE {where} ORDER BY recorded_at DESC,id DESC LIMIT ? OFFSET ?",
                [*params, PAGE_SIZE, (page - 1) * PAGE_SIZE],
            )
        ]
        baselines = [
            dict(row)
            for row in conn.execute(
                "SELECT b.*,p.name FROM inventory_baselines b JOIN products p ON p.id=b.product_id AND p.store_id=b.store_id WHERE b.store_id=authorized_store_id()"
                + (" AND b.product_id=?" if filters["product_id"] else "")
                + " ORDER BY b.product_id LIMIT 20",
                (int(filters["product_id"]),) if filters["product_id"] else (),
            )
        ]
    return {
        "adjustments": rows,
        "count": count,
        "page": page,
        "pages": pages,
        "baselines": baselines,
    }
