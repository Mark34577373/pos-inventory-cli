"""Durable held-sale snapshots, single active claims and atomic completion hooks."""

import hashlib
import json
import re
import secrets
from contextlib import closing

from database.database import get_store_connection
from database.tenancy import require_store_write
from services.pos_cart import clean_cart, order_notes, price_cart
from services.refunds import valid_id
from services.validation import visible_text


def digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", value):
        raise ValueError("Reload this sale before continuing.")
    return hashlib.sha256(value.encode()).hexdigest()


def get_held(identifier, conn=None):
    if not valid_id(identifier):
        raise LookupError("Held sale not found.")
    if conn is None:
        with closing(get_store_connection()) as connection:
            return get_held(identifier, connection)
    row = conn.execute(
        "SELECT * FROM held_sales WHERE store_id=authorized_store_id() AND id=?", (identifier,)
    ).fetchone()
    if row is None:
        raise LookupError("Held sale not found.")
    return dict(row)


def hold_sale(
    cart, customer_id, discount, notes, label, checkout_token, *, held_id=None, claim=None
):
    require_store_write()
    digest(checkout_token)
    cart = clean_cart(cart)
    notes = order_notes(notes)
    label = visible_text(label or "Held sale", "Sale label", 100)
    quote = price_cart(cart, customer_id, discount)
    if quote["unavailable_items"]:
        raise ValueError(
            "Resolve unavailable customer/products or invalid discounts before holding."
        )
    # A hold does not reserve inventory: quantities above current stock may be held and revalidated later.
    payload = json.dumps(
        dict(cart=cart, customer_id=customer_id, discount=quote["discount"], notes=notes),
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(payload) > 12000:
        raise ValueError("Held sale is too large.")
    fingerprint = hashlib.sha256((payload + "\n" + label).encode()).hexdigest()
    with closing(get_store_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute(
            "SELECT 1 FROM transactions WHERE store_id=authorized_store_id() AND checkout_token=?",
            (checkout_token,),
        ).fetchone():
            raise ValueError("This sale is already completed. Open its receipt.")
        prior = conn.execute(
            "SELECT * FROM held_sales WHERE store_id=authorized_store_id() AND checkout_token=?",
            (checkout_token,),
        ).fetchone()
        if prior:
            if prior["state"] == "held" and prior["payload_hash"] == fingerprint:
                return prior["id"]
            if (
                prior["id"] != held_id
                or prior["state"] != "resumed"
                or prior["claim_hash"] != digest(claim)
            ):
                raise ValueError("This held sale is no longer claimed by this window.")
            conn.execute(
                "UPDATE held_sales SET payload=?,payload_hash=?,label=?,state='held',claim_hash=NULL,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE store_id=authorized_store_id() AND id=?",
                (payload, fingerprint, label, held_id),
            )
            return held_id
        if held_id is not None:
            raise LookupError("Held sale not found.")
        if (
            conn.execute(
                "SELECT COUNT(*) FROM held_sales WHERE store_id=authorized_store_id() AND state IN ('held','resumed')"
            ).fetchone()[0]
            >= 200
        ):
            raise ValueError(
                "At most 200 open held sales are supported. Discard unneeded sales first."
            )
        return conn.execute(
            "INSERT INTO held_sales(store_id,label,payload,payload_hash,checkout_token) VALUES(authorized_store_id(),?,?,?,?)",
            (label, payload, fingerprint, checkout_token),
        ).lastrowid


def resume_sale(identifier):
    require_store_write()
    claim = secrets.token_urlsafe(32)
    with closing(get_store_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = get_held(identifier, conn)
        if row["state"] != "held":
            raise ValueError(
                "This sale is in use or closed. Release an interrupted claim before resuming."
            )
        conn.execute(
            "UPDATE held_sales SET state='resumed',claim_hash=?,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE store_id=authorized_store_id() AND id=?",
            (digest(claim), identifier),
        )
        return dict(
            json.loads(row["payload"]),
            held_id=identifier,
            claim=claim,
            checkout_token=row["checkout_token"],
        )


def update_held(identifier, action, label=None):
    require_store_write()
    with closing(get_store_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = get_held(identifier, conn)
        if row["state"] not in ("held", "resumed"):
            raise ValueError("This held sale is already closed.")
        if action == "rename":
            label = visible_text(label, "Sale label", 100)
            payload_hash = hashlib.sha256((row["payload"] + "\n" + label).encode()).hexdigest()
            conn.execute(
                "UPDATE held_sales SET label=?,payload_hash=?,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE store_id=authorized_store_id() AND id=?",
                (label, payload_hash, identifier),
            )
        elif action in ("release", "discard"):
            conn.execute(
                "UPDATE held_sales SET state=?,claim_hash=NULL,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE store_id=authorized_store_id() AND id=?",
                ("held" if action == "release" else "discarded", identifier),
            )
        else:
            raise ValueError("Choose a supported held-sale action.")


def cancel_claim(identifier, claim):
    require_store_write()
    with closing(get_store_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = get_held(identifier, conn)
        if row["state"] == "resumed" and row["claim_hash"] == digest(claim):
            conn.execute(
                "UPDATE held_sales SET state='discarded',claim_hash=NULL,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE store_id=authorized_store_id() AND id=?",
                (identifier,),
            )


def verify_claim(conn, checkout_token, identifier, claim):
    row = (
        conn.execute(
            "SELECT id,claim_hash,state FROM held_sales WHERE store_id=authorized_store_id() AND checkout_token=?",
            (checkout_token,),
        ).fetchone()
        if checkout_token
        else None
    )
    if identifier is not None or row:
        if (
            row is None
            or row["id"] != identifier
            or row["state"] != "resumed"
            or row["claim_hash"] != digest(claim)
        ):
            raise ValueError("This held-sale claim expired or was released. Reload held sales.")
    return row["id"] if row else None


def finish_claim(conn, identifier, transaction_id):
    if identifier is not None:
        conn.execute(
            "UPDATE held_sales SET state='completed',claim_hash=NULL,transaction_id=?,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE store_id=authorized_store_id() AND id=?",
            (transaction_id, identifier),
        )


def list_held(page=1):
    if type(page) is not int or not 1 <= page <= 10000:
        raise ValueError("Choose a valid held-sales page.")
    with closing(get_store_connection()) as conn:
        conn.execute("BEGIN")
        count = conn.execute(
            "SELECT COUNT(*) FROM held_sales WHERE store_id=authorized_store_id() AND state IN ('held','resumed')"
        ).fetchone()[0]
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT id,label,payload,state,created_at,updated_at FROM held_sales WHERE store_id=authorized_store_id() AND state IN ('held','resumed') ORDER BY updated_at DESC,id DESC LIMIT 20 OFFSET ?",
                ((page - 1) * 20,),
            )
        ]
    for row in rows:
        data = json.loads(row.pop("payload"))
        quote = price_cart(data["cart"], data["customer_id"], data["discount"])
        row.update(
            item_count=sum(data["cart"].values()),
            estimated_total_cents=quote["total_cents"],
            needs_review=quote["has_stock_problem"],
        )
    return dict(rows=rows, count=count, page=page, pages=max(1, (count + 19) // 20))
