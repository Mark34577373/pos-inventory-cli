"""Store-owned presentation settings. Prices/stock never enter configuration."""

import json
from contextlib import closing

from flask import g, has_request_context

from database.database import get_store_connection
from database.tenancy import require_store_write
from services.refunds import valid_id

COLORS = ("blue", "navy", "green", "amber", "purple")
PRESETS = ("hybrid", "quick", "custom")
DENSITIES = ("compact", "comfortable", "large")


def defaults():
    return dict(
        preset="hybrid",
        density="comfortable",
        show_images=True,
        show_categories=True,
        product_order=[],
        category_order=[],
        favorites=[],
        colors={},
    )


def require_owner():
    require_store_write()
    if has_request_context():
        user = getattr(g, "user", None)
        with closing(get_store_connection()) as conn:
            if (
                not user
                or not conn.execute(
                    "SELECT 1 FROM store_memberships WHERE user_id=? AND store_id=authorized_store_id() AND role='owner'",
                    (user["id"],),
                ).fetchone()
            ):
                raise PermissionError("The store owner is required.")


def validate_layout(value, conn):
    if not isinstance(value, dict) or set(value) != set(defaults()):
        raise ValueError("Unsupported layout fields. Reload the designer.")
    if (
        value["preset"] not in PRESETS
        or value["density"] not in DENSITIES
        or any(type(value[key]) is not bool for key in ("show_images", "show_categories"))
    ):
        raise ValueError("Choose a valid preset, density and navigation options.")
    for key, table, maximum in (
        ("product_order", "products", 100),
        ("favorites", "products", 100),
        ("category_order", "categories", 50),
    ):
        ids = value[key]
        if (
            not isinstance(ids, list)
            or len(ids) > maximum
            or any(not valid_id(i) for i in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError(f"Invalid {key.replace('_', ' ')} (maximum {maximum}, no duplicates).")
        if ids:
            rows = conn.execute(
                f"SELECT id FROM {table} WHERE store_id=authorized_store_id() AND id IN ({','.join('?' for _ in ids)})"
                + (" AND is_active=1" if table == "products" else ""),
                ids,
            ).fetchall()
            if len(rows) != len(ids):
                raise ValueError("Layout contains unavailable products or categories.")
    colors = value["colors"]
    if (
        not isinstance(colors, dict)
        or len(colors) > 100
        or any(
            not isinstance(k, str)
            or not k.isascii()
            or not k.isdigit()
            or int(k) not in value["product_order"]
            or v not in COLORS
            for k, v in colors.items()
        )
    ):
        raise ValueError("Choose supported colors for pinned products.")
    result = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if len(result) > 16000:
        raise ValueError("Layout is too large.")
    return result


def get_layout():
    with closing(get_store_connection()) as conn:
        row = conn.execute(
            "SELECT configuration,revision FROM pos_layouts WHERE store_id=authorized_store_id()"
        ).fetchone()
        config = json.loads(row["configuration"]) if row else defaults()
        # Deactivation/deletion never breaks the saved designer; stale references are pruned on read.
        for key, table in (
            ("product_order", "products"),
            ("favorites", "products"),
            ("category_order", "categories"),
        ):
            ids = config[key]
            if ids:
                live = {
                    r["id"]
                    for r in conn.execute(
                        f"SELECT id FROM {table} WHERE store_id=authorized_store_id() AND id IN ({','.join('?' for _ in ids)})"
                        + (" AND is_active=1" if table == "products" else ""),
                        ids,
                    )
                }
                config[key] = [identifier for identifier in ids if identifier in live]
        config["colors"] = {
            key: value
            for key, value in config["colors"].items()
            if int(key) in config["product_order"]
        }
        return dict(config=config, revision=row["revision"] if row else 0)


def save_layout(config, revision):
    require_owner()
    if type(revision) is not int or revision < 0:
        raise ValueError("Reload the designer before saving.")
    with closing(get_store_connection()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        payload = validate_layout(config, conn)
        conn.execute(
            "INSERT OR IGNORE INTO pos_layouts(store_id,configuration) VALUES(authorized_store_id(),?)",
            (json.dumps(defaults()),),
        )
        updated = conn.execute(
            "UPDATE pos_layouts SET configuration=?,revision=revision+1,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE store_id=authorized_store_id() AND revision=?",
            (payload, revision),
        )
        if updated.rowcount != 1:
            raise ValueError("Another window changed this layout. Reload before saving.")
