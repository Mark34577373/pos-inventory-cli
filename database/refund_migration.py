"""Day 17: additive, immutable refund ledger; no historical sale rewrites."""


def migrate_refunds(conn):
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version=17").fetchone():
        return
    conn.execute(
        "CREATE UNIQUE INDEX items_sale_store ON transaction_items(id,transaction_id,store_id)"
    )
    conn.execute("""CREATE TABLE refunds(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id INTEGER NOT NULL REFERENCES stores(id),
        transaction_id INTEGER NOT NULL,
        actor_user_id INTEGER NOT NULL,
        recorded_at TEXT NOT NULL,
        business_date TEXT NOT NULL,
        reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 500),
        status TEXT NOT NULL DEFAULT 'completed' CHECK(status='completed'),
        total_cents INTEGER NOT NULL CHECK(typeof(total_cents)='integer' AND total_cents>0),
        tax_cents INTEGER NOT NULL CHECK(typeof(tax_cents)='integer' AND tax_cents>=0 AND tax_cents<=total_cents),
        restock INTEGER NOT NULL CHECK(restock IN (0,1)),
        idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 20 AND 128),
        request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
        UNIQUE(store_id,idempotency_key), UNIQUE(id,transaction_id,store_id),
        FOREIGN KEY(transaction_id,store_id) REFERENCES transactions(id,store_id),
        FOREIGN KEY(actor_user_id,store_id) REFERENCES store_memberships(user_id,store_id))""")
    conn.execute("""CREATE TABLE refund_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        refund_id INTEGER NOT NULL,
        transaction_id INTEGER NOT NULL,
        store_id INTEGER NOT NULL,
        transaction_item_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL CHECK(typeof(quantity)='integer' AND quantity>0),
        total_cents INTEGER NOT NULL CHECK(typeof(total_cents)='integer' AND total_cents>0),
        tax_cents INTEGER NOT NULL CHECK(typeof(tax_cents)='integer' AND tax_cents>=0 AND tax_cents<=total_cents),
        UNIQUE(refund_id,transaction_item_id),
        FOREIGN KEY(refund_id,transaction_id,store_id) REFERENCES refunds(id,transaction_id,store_id),
        FOREIGN KEY(transaction_item_id,transaction_id,store_id) REFERENCES transaction_items(id,transaction_id,store_id))""")
    conn.execute("CREATE INDEX refunds_store_date ON refunds(store_id,business_date,id)")
    conn.execute("CREATE INDEX refunds_sale_store ON refunds(store_id,transaction_id,id)")
    conn.execute(
        "CREATE INDEX refund_items_original ON refund_items(store_id,transaction_item_id,id)"
    )
    conn.execute("""CREATE TRIGGER refund_quantity_cap BEFORE INSERT ON refund_items
        WHEN NEW.quantity+COALESCE((SELECT SUM(quantity) FROM refund_items WHERE store_id=NEW.store_id AND transaction_item_id=NEW.transaction_item_id),0)
          >(SELECT quantity FROM transaction_items WHERE id=NEW.transaction_item_id AND store_id=NEW.store_id)
        BEGIN SELECT RAISE(ABORT,'Refund quantity exceeds sale'); END""")
    conn.execute("""CREATE TRIGGER refund_amount_cap BEFORE INSERT ON refunds
        WHEN NEW.total_cents+COALESCE((SELECT SUM(total_cents) FROM refunds WHERE store_id=NEW.store_id AND transaction_id=NEW.transaction_id),0)
          >(SELECT total_cents FROM transactions WHERE id=NEW.transaction_id AND store_id=NEW.store_id)
        BEGIN SELECT RAISE(ABORT,'Refund amount exceeds sale'); END""")
    for table in ("refunds", "refund_items"):
        for operation in ("UPDATE", "DELETE"):
            conn.execute(f"""CREATE TRIGGER {table}_immutable_{operation.lower()} BEFORE {operation} ON {table}
                BEGIN SELECT RAISE(ABORT,'Refund ledger is immutable'); END""")
    for table in ("transactions", "transaction_items"):
        sale_id = "OLD.id" if table == "transactions" else "OLD.transaction_id"
        for operation in ("UPDATE", "DELETE"):
            conn.execute(f"""CREATE TRIGGER {table}_refunded_immutable_{operation.lower()} BEFORE {operation} ON {table}
                WHEN EXISTS(SELECT 1 FROM refunds WHERE transaction_id={sale_id} AND store_id=OLD.store_id)
                BEGIN SELECT RAISE(ABORT,'Refunded sale is immutable'); END""")
    conn.execute("""CREATE TRIGGER refunded_sale_no_new_items BEFORE INSERT ON transaction_items
        WHEN EXISTS(SELECT 1 FROM refunds WHERE transaction_id=NEW.transaction_id AND store_id=COALESCE(NEW.store_id,1))
        BEGIN SELECT RAISE(ABORT,'Refunded sale is immutable'); END""")
    conn.execute(
        "ALTER TABLE stock_adjustments ADD COLUMN refund_id INTEGER REFERENCES refunds(id)"
    )
    conn.execute("""CREATE TRIGGER adjustment_refund_store BEFORE INSERT ON stock_adjustments
        WHEN NEW.refund_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM refunds WHERE id=NEW.refund_id AND store_id=NEW.store_id AND transaction_id=NEW.transaction_id AND restock=1)
        BEGIN SELECT RAISE(ABORT,'Invalid refund stock reference'); END""")
    conn.execute("DROP TRIGGER product_quantity_audit")
    conn.execute("""CREATE TRIGGER product_quantity_audit AFTER UPDATE OF quantity ON products
        WHEN NEW.quantity!=OLD.quantity BEGIN
        INSERT INTO stock_adjustments(store_id,product_id,product_name,actor_user_id,adjustment_type,quantity_before,quantity_change,quantity_after,reason,transaction_id,refund_id)
        VALUES(NEW.store_id,NEW.id,NEW.name,audit_actor_id(),audit_type(),OLD.quantity,NEW.quantity-OLD.quantity,NEW.quantity,audit_reason(),audit_transaction_id(),audit_refund_id()); END""")
    conn.execute("INSERT INTO schema_migrations(version) VALUES(17)")
