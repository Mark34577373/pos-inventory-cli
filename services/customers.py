"""Authorized customer directory and bounded, exact historical summaries."""

from contextlib import closing

from email_validator import EmailNotValidError, validate_email

from database.database import get_store_connection
from database.tenancy import require_store_write
from services.analytics import ReportLimitError
from services.refunds import valid_id
from services.validation import visible_text

FIELDS = {"name", "email", "phone", "notes"}
MAX_HISTORY = 10000


def customer_fields(values):
    if set(values) - FIELDS:
        raise ValueError("Unsupported customer fields.")
    result = {
        key: visible_text(
            values.get(key, ""),
            key.title(),
            maximum,
            optional=key != "name",
            multiline=key == "notes",
        )
        for key, maximum in (("name", 100), ("email", 254), ("phone", 80), ("notes", 1000))
    }
    if result["email"]:
        try:
            result["email"] = validate_email(result["email"], check_deliverability=False).normalized
        except EmailNotValidError:
            raise ValueError("Enter a valid email address or leave it blank.") from None
    return result


def get_customer(customer_id, conn=None, *, active=False):
    if not valid_id(customer_id):
        raise LookupError("Customer not found.")
    if conn is None:
        with closing(get_store_connection()) as connection:
            return get_customer(customer_id, connection, active=active)
    row = conn.execute(
        "SELECT * FROM customers WHERE store_id=authorized_store_id() AND id=?", (customer_id,)
    ).fetchone()
    if row is None or (active and not row["is_active"]):
        raise LookupError("Customer not found or unavailable.")
    return dict(row)


def save_customer(values, customer_id=None):
    require_store_write()
    fields = customer_fields(values)
    with closing(get_store_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if customer_id is None:
            return conn.execute(
                """INSERT INTO customers(store_id,name,email,phone,notes)
                VALUES(authorized_store_id(),?,?,?,?)""",
                tuple(fields.values()),
            ).lastrowid
        get_customer(customer_id, conn)
        conn.execute(
            """UPDATE customers SET name=?,email=?,phone=?,notes=?,
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE store_id=authorized_store_id() AND id=?""",
            (*fields.values(), customer_id),
        )
        return customer_id


def deactivate_customer(customer_id):
    require_store_write()
    with closing(get_store_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        get_customer(customer_id, conn)
        conn.execute(
            """UPDATE customers SET is_active=0,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE store_id=authorized_store_id() AND id=?""",
            (customer_id,),
        )


def directory_filters(values):
    q = visible_text(values.get("q", ""), "Search", 100, optional=True)
    state = values.get("state", "active")
    sort = values.get("sort", "name")
    page = values.get("page", "1")
    if state not in ("active", "inactive", "all") or sort not in ("name", "newest", "oldest"):
        raise ValueError("Choose supported customer filters.")
    if not str(page).isascii() or not str(page).isdigit() or not 1 <= int(page) <= 100000:
        raise ValueError("Choose a valid page.")
    return dict(q=q, state=state, sort=sort, page=int(page))


def list_customers(filters):
    filters = directory_filters(filters)
    clause = "store_id=authorized_store_id()"
    params = []
    if filters["state"] != "all":
        clause += " AND is_active=?"
        params.append(int(filters["state"] == "active"))
    if filters["q"]:
        clause += " AND (instr(lower(name),lower(?))>0 OR instr(lower(email),lower(?))>0 OR instr(phone,?)>0)"
        params.extend([filters["q"]] * 3)
    order = {"name": "name COLLATE NOCASE,id", "newest": "id DESC", "oldest": "id"}[filters["sort"]]
    with closing(get_store_connection()) as conn:
        conn.execute("BEGIN")
        count = conn.execute(f"SELECT COUNT(*) FROM customers WHERE {clause}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM customers WHERE {clause} ORDER BY {order} LIMIT 20 OFFSET ?",
            (*params, (filters["page"] - 1) * 20),
        ).fetchall()
    return dict(
        customers=[dict(row) for row in rows],
        count=count,
        page=filters["page"],
        pages=max(1, (count + 19) // 20),
    )


def customer_history(customer_id):
    with closing(get_store_connection()) as conn:
        conn.execute("BEGIN")
        customer = get_customer(customer_id, conn)
        transactions = [
            dict(row)
            for row in conn.execute(
                """SELECT id,timestamp,total_cents,
            customer_name_snapshot FROM transactions WHERE store_id=authorized_store_id()
            AND customer_id=? ORDER BY timestamp DESC,id DESC LIMIT ?""",
                (customer_id, MAX_HISTORY + 1),
            )
        ]
        refunds = [
            dict(row)
            for row in conn.execute(
                """SELECT r.id,r.transaction_id,r.business_date,r.total_cents
            FROM refunds r JOIN transactions t ON t.id=r.transaction_id AND t.store_id=r.store_id
            WHERE r.store_id=authorized_store_id() AND t.customer_id=?
            ORDER BY r.business_date DESC,r.id DESC LIMIT ?""",
                (customer_id, MAX_HISTORY + 1),
            )
        ]
        if len(transactions) > MAX_HISTORY or len(refunds) > MAX_HISTORY:
            raise ReportLimitError("Customer history exceeds the 10,000-record reporting limit.")
        return dict(
            customer=customer,
            transactions=transactions[:100],
            refunds=refunds[:100],
            transaction_count=len(transactions),
            total_cents=sum(t["total_cents"] for t in transactions),
            refunded_cents=sum(r["total_cents"] for r in refunds),
            last_purchase=transactions[0]["timestamp"] if transactions else None,
        )


def export_customers(filters):
    """One bounded read snapshot; no PII contacts and no per-customer queries."""
    filters = directory_filters(filters)
    with closing(get_store_connection()) as conn:
        conn.execute("BEGIN")
        clause = """c.store_id=authorized_store_id()
            AND (?='all' OR c.is_active=?)
            AND (?='' OR instr(lower(c.name),lower(?))>0 OR instr(lower(c.email),lower(?))>0 OR instr(c.phone,?)>0)"""
        params = (
            filters["state"],
            int(filters["state"] == "active"),
            filters["q"],
            filters["q"],
            filters["q"],
            filters["q"],
        )
        ordering = {"name": "c.name COLLATE NOCASE,c.id", "newest": "c.id DESC", "oldest": "c.id"}[
            filters["sort"]
        ]
        customers = [
            dict(row)
            for row in conn.execute(
                f"""SELECT c.id,c.name,c.is_active FROM customers c
            WHERE {clause} ORDER BY {ordering} LIMIT ?""",
                (*params, MAX_HISTORY + 1),
            )
        ]
        if len(customers) > MAX_HISTORY:
            raise ReportLimitError("Customer export exceeds the 10,000-customer limit.")
        selected = {
            row["id"]: dict(row, count=0, purchases=0, refunds=0, last_purchase=None)
            for row in customers
        }
        sales = conn.execute(
            f"""SELECT t.customer_id,t.timestamp,t.total_cents FROM transactions t
            JOIN customers c ON c.id=t.customer_id AND c.store_id=t.store_id
            WHERE t.store_id=authorized_store_id() AND {clause} LIMIT ?""",
            (*params, MAX_HISTORY + 1),
        ).fetchall()
        refunds = conn.execute(
            f"""SELECT t.customer_id,r.total_cents FROM refunds r
            JOIN transactions t ON t.id=r.transaction_id AND t.store_id=r.store_id
            JOIN customers c ON c.id=t.customer_id AND c.store_id=t.store_id
            WHERE r.store_id=authorized_store_id() AND {clause} LIMIT ?""",
            (*params, MAX_HISTORY + 1),
        ).fetchall()
        if len(sales) > MAX_HISTORY or len(refunds) > MAX_HISTORY:
            raise ReportLimitError(
                "Narrow exports require fewer than 10,000 linked sales and refunds."
            )
        for sale in sales:
            if sale["customer_id"] in selected:
                row = selected[sale["customer_id"]]
                row["count"] += 1
                row["purchases"] += sale["total_cents"]
                row["last_purchase"] = max(row["last_purchase"] or "", sale["timestamp"])
        for refund in refunds:
            if refund["customer_id"] in selected:
                selected[refund["customer_id"]]["refunds"] += refund["total_cents"]
        return list(selected.values())
