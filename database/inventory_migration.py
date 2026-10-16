"""Additive Day 15 migration. Never reconstruct pre-migration stock history."""


def migrate_inventory(conn):
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version=15").fetchone():
        return
    conn.execute("""CREATE TABLE categories(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id INTEGER NOT NULL REFERENCES stores(id),
        name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
        normalized_name TEXT NOT NULL CHECK(length(normalized_name)>0),
        UNIQUE(store_id,normalized_name), UNIQUE(id,store_id))""")
    conn.execute("ALTER TABLE products ADD COLUMN category_id INTEGER REFERENCES categories(id)")
    conn.execute("ALTER TABLE products ADD COLUMN sku TEXT")
    conn.execute("ALTER TABLE products ADD COLUMN low_stock_threshold INTEGER")
    conn.execute("ALTER TABLE products ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
    conn.execute("UPDATE products SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')")
    conn.execute(
        "CREATE UNIQUE INDEX product_sku_store ON products(store_id,sku) WHERE sku IS NOT NULL"
    )
    conn.execute("CREATE INDEX product_category_store ON products(store_id,category_id,is_active)")
    conn.execute("CREATE INDEX product_name_store ON products(store_id,name COLLATE NOCASE,id)")
    conn.execute("CREATE INDEX product_stock_store ON products(store_id,is_active,quantity,id)")
    conn.execute("CREATE INDEX product_updated_store ON products(store_id,updated_at,id)")
    conn.execute("""CREATE TABLE inventory_baselines(
        product_id INTEGER PRIMARY KEY,
        store_id INTEGER NOT NULL REFERENCES stores(id),
        quantity INTEGER NOT NULL CHECK(quantity>=0),
        recorded_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        FOREIGN KEY(product_id,store_id) REFERENCES products(id,store_id))""")
    conn.execute(
        "INSERT INTO inventory_baselines(product_id,store_id,quantity) SELECT id,store_id,quantity FROM products"
    )
    conn.execute("""CREATE TABLE stock_adjustments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id INTEGER NOT NULL REFERENCES stores(id),
        product_id INTEGER NOT NULL,
        product_name TEXT NOT NULL,
        actor_user_id INTEGER REFERENCES users(id),
        adjustment_type TEXT NOT NULL CHECK(adjustment_type IN ('initial','restock','correction','damaged','return','checkout')),
        quantity_before INTEGER NOT NULL CHECK(typeof(quantity_before)='integer' AND quantity_before>=0),
        quantity_change INTEGER NOT NULL CHECK(typeof(quantity_change)='integer'),
        quantity_after INTEGER NOT NULL CHECK(typeof(quantity_after)='integer' AND quantity_after BETWEEN 0 AND 1000000),
        reason TEXT NOT NULL CHECK(length(reason) BETWEEN 1 AND 500),
        recorded_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        transaction_id INTEGER,
        FOREIGN KEY(product_id,store_id) REFERENCES products(id,store_id),
        FOREIGN KEY(transaction_id,store_id) REFERENCES transactions(id,store_id),
        CHECK(quantity_before+quantity_change=quantity_after))""")
    conn.execute(
        "CREATE INDEX adjustments_store_date ON stock_adjustments(store_id,recorded_at,id)"
    )
    conn.execute(
        "CREATE INDEX adjustments_product_store ON stock_adjustments(store_id,product_id,id)"
    )
    conn.execute(
        "CREATE INDEX adjustments_type_store ON stock_adjustments(store_id,adjustment_type,id)"
    )
    for operation in ("INSERT", "UPDATE"):
        conn.execute(f"""CREATE TRIGGER product_inventory_validate_{operation.lower()} BEFORE {operation} ON products
            WHEN (NEW.category_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM categories WHERE id=NEW.category_id AND store_id=COALESCE(NEW.store_id,1)))
              OR (NEW.low_stock_threshold IS NOT NULL AND (typeof(NEW.low_stock_threshold)!='integer' OR NEW.low_stock_threshold NOT BETWEEN 0 AND 1000000))
              OR (NEW.sku IS NOT NULL AND (length(NEW.sku) NOT BETWEEN 1 AND 64 OR NEW.sku!=upper(NEW.sku)))
            BEGIN SELECT RAISE(ABORT,'Invalid inventory metadata'); END""")
    conn.execute("""CREATE TRIGGER category_owner_immutable BEFORE UPDATE OF store_id ON categories
        WHEN NEW.store_id!=OLD.store_id BEGIN SELECT RAISE(ABORT,'Category ownership is immutable'); END""")
    conn.execute("""CREATE TRIGGER category_delete_unassign BEFORE DELETE ON categories BEGIN
        UPDATE products SET category_id=NULL WHERE category_id=OLD.id AND store_id=OLD.store_id; END""")
    for table in ("stock_adjustments", "inventory_baselines"):
        for operation in ("UPDATE", "DELETE"):
            conn.execute(f"""CREATE TRIGGER {table}_immutable_{operation.lower()} BEFORE {operation} ON {table}
                BEGIN SELECT RAISE(ABORT,'Inventory history is immutable'); END""")
    conn.execute("""CREATE TRIGGER adjustment_actor_store BEFORE INSERT ON stock_adjustments
        WHEN NEW.actor_user_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM store_memberships WHERE user_id=NEW.actor_user_id AND store_id=NEW.store_id)
        BEGIN SELECT RAISE(ABORT,'Invalid adjustment actor'); END""")
    # Legacy clients omit store_id. Log their creation only AFTER Day 14's
    # normalization assigns store 1, so composite foreign keys remain valid.
    initial = """INSERT INTO stock_adjustments(store_id,product_id,product_name,actor_user_id,adjustment_type,quantity_before,quantity_change,quantity_after,reason)
        VALUES(NEW.store_id,NEW.id,NEW.name,audit_actor_id(),'initial',0,NEW.quantity,NEW.quantity,'Opening stock for new product');
        UPDATE products SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=NEW.id AND store_id=NEW.store_id;"""
    conn.execute(
        f"CREATE TRIGGER product_initial_audit AFTER INSERT ON products WHEN NEW.store_id IS NOT NULL BEGIN {initial} END"
    )
    conn.execute(
        f"CREATE TRIGGER legacy_product_initial_audit AFTER UPDATE OF store_id ON products WHEN OLD.store_id IS NULL AND NEW.store_id IS NOT NULL BEGIN {initial} END"
    )
    conn.execute("""CREATE TRIGGER product_quantity_audit AFTER UPDATE OF quantity ON products
        WHEN NEW.quantity!=OLD.quantity BEGIN
        INSERT INTO stock_adjustments(store_id,product_id,product_name,actor_user_id,adjustment_type,quantity_before,quantity_change,quantity_after,reason,transaction_id)
        VALUES(NEW.store_id,NEW.id,NEW.name,audit_actor_id(),audit_type(),OLD.quantity,NEW.quantity-OLD.quantity,NEW.quantity,audit_reason(),audit_transaction_id()); END""")
    conn.execute("""CREATE TRIGGER product_inventory_updated AFTER UPDATE OF name,price_cents,quantity,is_active,category_id,sku,low_stock_threshold ON products BEGIN
        UPDATE products SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=NEW.id AND store_id=NEW.store_id; END""")
    conn.execute("INSERT INTO schema_migrations(version) VALUES(15)")
