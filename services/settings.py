"""Store settings are resolved exclusively from the authorized server context."""

import json
import re
from contextlib import closing
from decimal import Decimal

from flask import g, has_request_context

from database.database import get_store_connection
from database.tenancy import current_store_id, require_store_write
from services.auth import normalize_email
from services.validation import visible_text

PERIODS = ("today", "yesterday", "last7", "last30", "this_month", "previous_month")
VIEWS = ("overview", "products", "inventory")
RECEIPT_FIELDS = (
    "receipt_name",
    "receipt_contact",
    "receipt_header",
    "receipt_footer",
    "thank_you",
)


def get_settings(conn=None):
    if conn is None:
        with closing(get_store_connection()) as connection:
            return get_settings(connection)
    row = conn.execute("""SELECT s.*,t.name AS store_name FROM store_settings s
        JOIN stores t ON t.id=s.store_id WHERE s.store_id=authorized_store_id()""").fetchone()
    if row is None:
        raise ValueError("Store settings are unavailable. Run the documented migration.")
    result = dict(row)
    result["tax_rate"] = Decimal(result["tax_units"]) / 1_000_000
    result["tax_percent"] = Decimal(result["tax_units"]) / 10_000
    return result


def tax_rate():
    return get_settings()["tax_rate"]


def default_threshold():
    # One presentation default per authorized request, not a financial/report cache.
    # The store key also protects trusted tests/internal context switches.
    if has_request_context():
        store = current_store_id()
        cached = getattr(g, "_stock_threshold", None)
        if cached is not None and cached[0] == store:
            return cached[1]
        value = get_settings()["low_stock_default"]
        g._stock_threshold = (store, value)
        return value
    return get_settings()["low_stock_default"]


def receipt_snapshot(settings):
    return json.dumps({field: settings[field] for field in RECEIPT_FIELDS}, ensure_ascii=True)


def read_receipt_snapshot(value):
    if not value:
        return None
    try:
        result = json.loads(value)
        if isinstance(result, dict) and all(isinstance(result.get(k), str) for k in RECEIPT_FIELDS):
            return {k: result[k] for k in RECEIPT_FIELDS}
    except (ValueError, TypeError):
        pass
    return None


def save_settings(section, fields):
    require_store_write()
    if any(key in fields for key in ("store_id", "user_id", "role")):
        raise ValueError("Ownership fields cannot be changed.")
    updates = {}
    store_name = None
    if section == "business":
        store_name = visible_text(fields.get("store_name", ""), "Store name", 100)
        email = visible_text(fields.get("business_email", ""), "Business email", 254, optional=True)
        updates["business_email"] = normalize_email(email) if email else ""
        for key, limit in (("phone", 80), ("address", 500), ("description", 1000)):
            updates[key] = visible_text(
                fields.get(key, ""), key.title(), limit, optional=True, multiline=key != "phone"
            )
    elif section == "tax":
        value = fields.get("tax_percent", "")
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,4})?", value):
            raise ValueError("Tax must be 0–100 percent with at most four decimal places.")
        rate = Decimal(value)
        if rate > 100:
            raise ValueError("Tax must be between 0 and 100 percent.")
        updates["tax_units"] = int(rate * 10_000)
    elif section == "receipt":
        for key in RECEIPT_FIELDS:
            limit = 100 if key == "receipt_name" else (200 if key == "thank_you" else 500)
            updates[key] = visible_text(
                fields.get(key, ""),
                key.replace("_", " ").title(),
                limit,
                optional=key != "receipt_name",
                multiline=key not in ("receipt_name", "thank_you"),
            )
    elif section == "inventory":
        value = fields.get("low_stock_default", "")
        if (
            not isinstance(value, str)
            or not re.fullmatch(r"[0-9]{1,7}", value)
            or int(value) > 1_000_000
        ):
            raise ValueError("Default threshold must be a whole number between 0 and 1000000.")
        updates["low_stock_default"] = int(value)
    elif section == "analytics":
        if fields.get("report_period") not in PERIODS or fields.get("landing_view") not in VIEWS:
            raise ValueError("Choose valid reporting preferences.")
        updates.update(report_period=fields["report_period"], landing_view=fields["landing_view"])
        for key in ("show_stock", "show_recent"):
            if fields.get(key, "0") not in ("0", "1"):
                raise ValueError("Choose valid dashboard preferences.")
            updates[key] = int(fields.get(key, "0"))
    else:
        raise ValueError("Unknown settings section.")
    revision = fields.get("revision", "")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9]{1,15}", revision):
        raise ValueError("Reload settings before saving.")
    with closing(get_store_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        # Column identifiers come only from the section allowlist above.
        result = conn.execute(
            "UPDATE store_settings SET "
            + ",".join(f"{key}=?" for key in updates)
            + ",revision=revision+1 WHERE store_id=authorized_store_id() AND revision=?",
            (*updates.values(), int(revision)),
        )
        if result.rowcount != 1:
            raise ValueError("Settings changed in another window. Reload before saving.")
        if store_name is not None:
            conn.execute("UPDATE stores SET name=? WHERE id=authorized_store_id()", (store_name,))
    if "low_stock_default" in updates and has_request_context():
        g.pop("_stock_threshold", None)
