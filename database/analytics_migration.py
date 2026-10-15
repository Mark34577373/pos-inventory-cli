"""Day 16 adds access-path indexes only; no historical financial data changes."""


def migrate_analytics(conn):
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version=16").fetchone():
        return
    conn.execute("CREATE INDEX transactions_store_time ON transactions(store_id,timestamp,id)")
    conn.execute(
        "CREATE INDEX items_store_transaction ON transaction_items(store_id,transaction_id,id)"
    )
    conn.execute("INSERT INTO schema_migrations(version) VALUES(16)")
