"""Additive purchasing ledger and future-only reference-cost snapshots."""


def migrate_purchasing(conn):
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version=25").fetchone():
        return
    conn.execute("""CREATE TABLE suppliers(
        id INTEGER PRIMARY KEY,store_id INTEGER NOT NULL REFERENCES stores(id),
        name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 100),
        contact TEXT NOT NULL DEFAULT '' CHECK(length(contact)<=100),
        email TEXT NOT NULL DEFAULT '' CHECK(length(email)<=254),
        phone TEXT NOT NULL DEFAULT '' CHECK(length(phone)<=80),
        address TEXT NOT NULL DEFAULT '' CHECK(length(address)<=500),
        notes TEXT NOT NULL DEFAULT '' CHECK(length(notes)<=1000),
        is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        UNIQUE(id,store_id))""")
    conn.execute("CREATE INDEX suppliers_directory ON suppliers(store_id,is_active,name,id)")
    conn.execute(
        "ALTER TABLE products ADD COLUMN unit_cost_cents INTEGER CHECK(unit_cost_cents IS NULL OR (typeof(unit_cost_cents)='integer' AND unit_cost_cents BETWEEN 0 AND 100000000))"
    )
    conn.execute("ALTER TABLE products ADD COLUMN supplier_id INTEGER REFERENCES suppliers(id)")
    conn.execute(
        "ALTER TABLE products ADD COLUMN supplier_sku TEXT NOT NULL DEFAULT '' CHECK(length(supplier_sku)<=64)"
    )
    conn.execute("CREATE INDEX products_supplier ON products(store_id,supplier_id,id)")
    conn.execute(
        "ALTER TABLE transaction_items ADD COLUMN unit_cost_cents INTEGER CHECK(unit_cost_cents IS NULL OR (typeof(unit_cost_cents)='integer' AND unit_cost_cents BETWEEN 0 AND 100000000))"
    )
    conn.execute("""CREATE TABLE purchase_orders(
        id INTEGER PRIMARY KEY,store_id INTEGER NOT NULL REFERENCES stores(id),supplier_id INTEGER NOT NULL,
        supplier_name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft'
          CHECK(status IN ('draft','ordered','partially_received','received','cancelled')),
        expected_date TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '' CHECK(length(notes)<=1000),
        business_date TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        UNIQUE(id,store_id),FOREIGN KEY(supplier_id,store_id) REFERENCES suppliers(id,store_id))""")
    conn.execute("""CREATE TABLE purchase_order_items(
        id INTEGER PRIMARY KEY,store_id INTEGER NOT NULL,order_id INTEGER NOT NULL,product_id INTEGER NOT NULL,
        product_name TEXT NOT NULL,quantity INTEGER NOT NULL CHECK(typeof(quantity)='integer' AND quantity BETWEEN 1 AND 1000000),
        unit_cost_cents INTEGER NOT NULL CHECK(typeof(unit_cost_cents)='integer' AND unit_cost_cents BETWEEN 0 AND 100000000),
        UNIQUE(order_id,product_id),UNIQUE(id,order_id,store_id),
        FOREIGN KEY(order_id,store_id) REFERENCES purchase_orders(id,store_id),
        FOREIGN KEY(product_id,store_id) REFERENCES products(id,store_id))""")
    conn.execute("""CREATE TABLE purchase_receipts(
        id INTEGER PRIMARY KEY,store_id INTEGER NOT NULL,order_id INTEGER NOT NULL,actor_user_id INTEGER,
        business_date TEXT NOT NULL,recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 20 AND 128),
        request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
        UNIQUE(store_id,idempotency_key),UNIQUE(id,order_id,store_id),
        FOREIGN KEY(order_id,store_id) REFERENCES purchase_orders(id,store_id),
        FOREIGN KEY(actor_user_id,store_id) REFERENCES store_memberships(user_id,store_id))""")
    conn.execute("""CREATE TABLE purchase_receipt_items(
        id INTEGER PRIMARY KEY,store_id INTEGER NOT NULL,order_id INTEGER NOT NULL,receipt_id INTEGER NOT NULL,
        order_item_id INTEGER NOT NULL,quantity INTEGER NOT NULL CHECK(typeof(quantity)='integer' AND quantity BETWEEN 1 AND 1000000),
        unit_cost_cents INTEGER NOT NULL CHECK(typeof(unit_cost_cents)='integer' AND unit_cost_cents BETWEEN 0 AND 100000000),
        UNIQUE(receipt_id,order_item_id),
        FOREIGN KEY(receipt_id,order_id,store_id) REFERENCES purchase_receipts(id,order_id,store_id),
        FOREIGN KEY(order_item_id,order_id,store_id) REFERENCES purchase_order_items(id,order_id,store_id))""")
    for name, table, columns in (
        ("orders_store_date", "purchase_orders", "store_id,business_date,id"),
        ("orders_supplier", "purchase_orders", "store_id,supplier_id,id"),
        ("order_items_store", "purchase_order_items", "store_id,order_id,id"),
        ("receipts_store_date", "purchase_receipts", "store_id,business_date,id"),
        ("receipt_items_order", "purchase_receipt_items", "store_id,order_item_id,id"),
    ):
        conn.execute(f"CREATE INDEX {name} ON {table}({columns})")
    for table in ("suppliers", "purchase_orders"):
        conn.execute(f"""CREATE TRIGGER {table}_owner_immutable BEFORE UPDATE OF store_id ON {table}
            WHEN NEW.store_id!=OLD.store_id BEGIN SELECT RAISE(ABORT,'Ownership is immutable'); END""")
        conn.execute(f"""CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
            BEGIN SELECT RAISE(ABORT,'Historical references cannot be deleted'); END""")
    for operation in ("INSERT", "UPDATE"):
        conn.execute(f"""CREATE TRIGGER product_supplier_{operation.lower()} BEFORE {operation} ON products
            WHEN NEW.supplier_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM suppliers WHERE id=NEW.supplier_id AND store_id=NEW.store_id)
            BEGIN SELECT RAISE(ABORT,'Supplier belongs to another store'); END""")
    for operation in ("INSERT", "UPDATE", "DELETE"):
        row = "NEW" if operation == "INSERT" else "OLD"
        conn.execute(f"""CREATE TRIGGER purchase_line_draft_{operation.lower()} BEFORE {operation} ON purchase_order_items
            WHEN NOT EXISTS(SELECT 1 FROM purchase_orders WHERE id={row}.order_id AND store_id={row}.store_id AND status='draft')
            BEGIN SELECT RAISE(ABORT,'Only draft lines may change'); END""")
    conn.execute("""CREATE TRIGGER purchase_line_identity BEFORE UPDATE ON purchase_order_items
        WHEN NEW.id!=OLD.id OR NEW.store_id!=OLD.store_id OR NEW.order_id!=OLD.order_id OR NEW.product_id!=OLD.product_id
        BEGIN SELECT RAISE(ABORT,'Purchase line identity is immutable'); END""")
    conn.execute("""CREATE TRIGGER purchase_order_transition BEFORE UPDATE ON purchase_orders
        WHEN NEW.supplier_id!=OLD.supplier_id OR NEW.supplier_name!=OLD.supplier_name OR NEW.business_date!=OLD.business_date
        OR NEW.created_at!=OLD.created_at
        OR (OLD.status!='draft' AND (NEW.notes!=OLD.notes OR NEW.expected_date!=OLD.expected_date))
        OR (NEW.status!=OLD.status AND NOT (
             (OLD.status='draft' AND NEW.status IN ('ordered','cancelled'))
          OR (OLD.status='ordered' AND NEW.status IN ('partially_received','received','cancelled'))
          OR (OLD.status='partially_received' AND NEW.status IN ('partially_received','received'))))
        BEGIN SELECT RAISE(ABORT,'Invalid purchase order transition'); END""")
    conn.execute("""CREATE TRIGGER receiving_cap BEFORE INSERT ON purchase_receipt_items
        WHEN NEW.quantity+COALESCE((SELECT SUM(quantity) FROM purchase_receipt_items WHERE store_id=NEW.store_id AND order_item_id=NEW.order_item_id),0)
        >(SELECT quantity FROM purchase_order_items WHERE id=NEW.order_item_id AND store_id=NEW.store_id)
        OR NEW.unit_cost_cents!=(SELECT unit_cost_cents FROM purchase_order_items WHERE id=NEW.order_item_id AND store_id=NEW.store_id)
        OR NOT EXISTS(SELECT 1 FROM purchase_orders WHERE id=NEW.order_id AND store_id=NEW.store_id AND status IN ('ordered','partially_received'))
        BEGIN SELECT RAISE(ABORT,'Invalid receiving quantity or cost'); END""")
    for table in ("purchase_receipts", "purchase_receipt_items"):
        for operation in ("UPDATE", "DELETE"):
            conn.execute(f"""CREATE TRIGGER {table}_{operation.lower()}_immutable BEFORE {operation} ON {table}
                BEGIN SELECT RAISE(ABORT,'Receiving history is immutable'); END""")
    conn.execute(
        "ALTER TABLE stock_adjustments ADD COLUMN purchase_receipt_id INTEGER REFERENCES purchase_receipts(id)"
    )
    conn.execute("""CREATE TRIGGER adjustment_receipt_store BEFORE INSERT ON stock_adjustments
        WHEN NEW.purchase_receipt_id IS NOT NULL AND (NEW.adjustment_type!='restock' OR NOT EXISTS(
        SELECT 1 FROM purchase_receipts r JOIN purchase_receipt_items ri ON ri.receipt_id=r.id AND ri.store_id=r.store_id
        JOIN purchase_order_items oi ON oi.id=ri.order_item_id AND oi.store_id=ri.store_id
        WHERE r.id=NEW.purchase_receipt_id AND r.store_id=NEW.store_id AND oi.product_id=NEW.product_id AND ri.quantity=NEW.quantity_change))
        BEGIN SELECT RAISE(ABORT,'Invalid receiving stock reference'); END""")
    conn.execute("DROP TRIGGER product_quantity_audit")
    conn.execute("""CREATE TRIGGER product_quantity_audit AFTER UPDATE OF quantity ON products
        WHEN NEW.quantity!=OLD.quantity BEGIN
        INSERT INTO stock_adjustments(store_id,product_id,product_name,actor_user_id,adjustment_type,quantity_before,quantity_change,quantity_after,reason,transaction_id,refund_id,purchase_receipt_id)
        VALUES(NEW.store_id,NEW.id,NEW.name,audit_actor_id(),audit_type(),OLD.quantity,NEW.quantity-OLD.quantity,NEW.quantity,audit_reason(),audit_transaction_id(),audit_refund_id(),audit_purchase_receipt_id()); END""")
    conn.execute("""CREATE TRIGGER sale_cost_immutable BEFORE UPDATE OF unit_cost_cents ON transaction_items
        WHEN NEW.unit_cost_cents IS NOT OLD.unit_cost_cents
        BEGIN SELECT RAISE(ABORT,'Original acquisition cost is immutable'); END""")
    conn.execute("INSERT INTO schema_migrations(version) VALUES(25)")
