"""Read-only checks around the existing ordered, transactional migrations."""

VERSIONS = (14, 15, 16, 17, 20, 21, 22)
COLUMNS = {
    14: {
        "products": "id name price price_cents quantity is_active store_id",
        "transactions": "id timestamp total total_cents payment_method checkout_token cash_received_cents tax_rate_percent store_id",
        "transaction_items": "id transaction_id product_id quantity price price_cents product_name store_id",
        "users": "id email password_hash created_at",
        "stores": "id name kind created_at",
        "store_memberships": "user_id store_id role",
        "auth_sessions": "token_hash user_id store_id expires_at",
        "auth_limits": "bucket attempts expires_at",
    },
    15: {
        "products": "category_id sku low_stock_threshold updated_at",
        "categories": "id store_id name normalized_name",
        "inventory_baselines": "product_id store_id quantity recorded_at",
        "stock_adjustments": "id store_id product_id product_name actor_user_id adjustment_type quantity_before quantity_change quantity_after reason recorded_at transaction_id",
    },
    16: {},
    17: {
        "refunds": "id store_id transaction_id actor_user_id recorded_at business_date reason status total_cents tax_cents restock idempotency_key request_hash",
        "refund_items": "id refund_id transaction_id store_id transaction_item_id quantity total_cents tax_cents",
        "stock_adjustments": "refund_id",
    },
}
INDEXES = {
    14: (
        "transactions_checkout_token",
        "products_id_store",
        "transactions_id_store",
        "transaction_items_id_store",
        "one_owner_per_store",
        "session_expiry",
    ),
    15: ("product_sku_store", "product_category_store", "adjustments_store_date"),
    16: ("transactions_store_time", "items_store_transaction"),
    17: ("items_sale_store", "refunds_store_date", "refunds_sale_store", "refund_items_original"),
}
TRIGGERS = {
    14: (
        "products_ownership_immutable",
        "transactions_ownership_immutable",
        "item_ownership_immutable",
        "item_store_consistency_insert",
        "item_store_consistency_update",
    ),
    15: (
        "product_quantity_audit",
        "product_initial_audit",
        "legacy_product_initial_audit",
        "product_inventory_validate_insert",
        "product_inventory_validate_update",
        "adjustment_actor_store",
        "stock_adjustments_immutable_update",
        "stock_adjustments_immutable_delete",
    ),
    16: (),
    17: (
        "refund_quantity_cap",
        "refund_amount_cap",
        "refunds_immutable_update",
        "refunds_immutable_delete",
        "refund_items_immutable_update",
        "refund_items_immutable_delete",
        "adjustment_refund_store",
        "refunded_sale_no_new_items",
        "transactions_refunded_immutable_update",
        "transaction_items_refunded_immutable_update",
    ),
}

COLUMNS[20] = {
    "store_settings": "store_id business_email phone address description receipt_name receipt_contact receipt_header receipt_footer thank_you tax_units low_stock_default report_period landing_view show_stock show_recent revision",
    "transactions": "receipt_snapshot",
    "transaction_items": "category_id_snapshot category_name_snapshot",
}
INDEXES[20] = ()
TRIGGERS[20] = ("store_settings_defaults", "settings_owner_immutable")

COLUMNS[21] = {}
INDEXES[21] = ("adjustments_initial_history", "items_store_product_cohort", "items_store_category_cohort")
TRIGGERS[21] = ()

COLUMNS[22] = {"inventory_alerts": "id store_id product_id product_name kind severity quantity threshold created_at updated_at read_at dismissed_at resolved_at"}
INDEXES[22] = ("alerts_active_product", "alerts_store_date", "alerts_unread", "alerts_store_kind", "alerts_product_history")
TRIGGERS[22] = ("alerts_product_insert", "alerts_product_update", "alerts_product_legacy", "alerts_default_threshold", "alerts_history_guard", "alerts_no_delete")

# Include every ownership/numeric/audit guard, including desktop compatibility.
TRIGGERS[14] += tuple(
    f"{table}_{suffix}"
    for table in ("products", "transactions", "transaction_items")
    for suffix in ("legacy_default", "store_insert", "store_update")
)
TRIGGERS[14] += tuple(
    f"validate_{table}_{operation}"
    for table in ("products", "transactions", "transaction_items")
    for operation in ("insert", "update")
)
TRIGGERS[15] += (
    "category_owner_immutable",
    "category_delete_unassign",
    "product_inventory_updated",
    "inventory_baselines_immutable_update",
    "inventory_baselines_immutable_delete",
)
TRIGGERS[17] += (
    "transactions_refunded_immutable_delete",
    "transaction_items_refunded_immutable_delete",
)


def validate_schema(conn, *, require_current=False):
    """Never repair a database whose version markers disagree with its schema."""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "schema_migrations" not in tables:
        if require_current:
            raise ValueError("Run the explicit database migration before startup.")
        # Identity tables without their version record are a partial migration.
        if tables.intersection({"users", "stores", "refunds", "categories"}):
            raise ValueError("Database migration metadata is missing. Restore a verified backup.")
        return ()
    versions = tuple(
        row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")
    )
    if versions != VERSIONS[: len(versions)] or (require_current and versions != VERSIONS):
        raise ValueError("Database migration versions are unknown, incomplete or outdated.")
    for version in versions:
        for table, names in COLUMNS[version].items():
            if table not in tables:
                raise ValueError("Database schema does not match its migration history.")
            # Identifiers originate only in this fixed source-code manifest.
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if not set(names.split()).issubset(columns):
                raise ValueError("Database columns do not match migration history.")
        for kind, manifest in (("index", INDEXES), ("trigger", TRIGGERS)):
            actual = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type=?", (kind,))
            }
            if not set(manifest[version]).issubset(actual):
                raise ValueError(
                    "Database integrity protections are missing; restore a verified backup."
                )
    return versions


def validate_integrity(conn, *, allow_missing_products=False):
    if [row[0] for row in conn.execute("PRAGMA quick_check")] != ["ok"]:
        raise ValueError(
            "Database integrity validation failed. Preserve the file and restore a verified backup."
        )
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    for row in violations:
        table, row_id, parent, _ = row
        allowed = False
        if (
            allow_missing_products
            and table in ("transaction_items", "stock_adjustments", "inventory_baselines", "inventory_alerts")
            and parent == "products"
        ):
            allowed = (
                conn.execute(
                    f"SELECT 1 FROM {table} WHERE rowid=? AND product_id NOT IN (SELECT id FROM products)",
                    (row_id,),
                ).fetchone()
                is not None
            )
        if not allowed:
            raise ValueError("Database foreign-key validation failed. No migration was committed.")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "store_memberships" in tables:
        mismatch = conn.execute("""SELECT 1 FROM transaction_items ti
            JOIN transactions t ON t.id=ti.transaction_id JOIN products p ON p.id=ti.product_id
            WHERE ti.store_id IS NULL OR ti.store_id!=t.store_id OR ti.store_id!=p.store_id LIMIT 1""").fetchone()
        if mismatch:
            raise ValueError(
                "Database contains inconsistent store ownership; records were not reassigned."
            )
    if (
        "categories" in tables
        and conn.execute(
            "SELECT 1 FROM products p JOIN categories c ON c.id=p.category_id WHERE p.store_id!=c.store_id LIMIT 1"
        ).fetchone()
    ):
        raise ValueError("Database contains an inconsistent category owner.")
