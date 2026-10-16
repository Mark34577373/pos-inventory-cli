"""Day 21 access paths only; no financial or inventory records change."""


def migrate_advanced_analytics(conn):
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version=21").fetchone():
        return
    conn.execute(
        "CREATE INDEX adjustments_initial_history ON stock_adjustments(store_id,adjustment_type,product_id,recorded_at)"
    )
    conn.execute(
        "CREATE INDEX items_store_product_cohort ON transaction_items(store_id,product_id,transaction_id)"
    )
    conn.execute(
        "CREATE INDEX items_store_category_cohort ON transaction_items(store_id,category_id_snapshot,transaction_id)"
    )
    conn.execute("INSERT INTO schema_migrations(version) VALUES(21)")
