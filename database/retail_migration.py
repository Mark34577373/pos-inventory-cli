"""Additive retail identity migration. Historical customers remain unassigned."""


def migrate_retail(conn):
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version=24").fetchone():
        return
    conn.execute("""CREATE TABLE customers (
        id INTEGER PRIMARY KEY, store_id INTEGER NOT NULL REFERENCES stores(id),
        name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 100),
        email TEXT NOT NULL DEFAULT '' CHECK(length(email)<=254),
        phone TEXT NOT NULL DEFAULT '' CHECK(length(phone)<=80),
        notes TEXT NOT NULL DEFAULT '' CHECK(length(notes)<=1000),
        is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        UNIQUE(id,store_id)
    )""")
    conn.execute("CREATE INDEX customers_directory ON customers(store_id,is_active,name,id)")
    conn.execute("""CREATE TRIGGER customers_owner_immutable BEFORE UPDATE OF store_id ON customers
        WHEN NEW.store_id!=OLD.store_id BEGIN SELECT RAISE(ABORT,'Customer ownership is immutable'); END""")
    conn.execute("""CREATE TRIGGER customers_no_delete BEFORE DELETE ON customers
        BEGIN SELECT RAISE(ABORT,'Deactivate customers to preserve history'); END""")
    conn.execute("ALTER TABLE transactions ADD COLUMN customer_id INTEGER REFERENCES customers(id)")
    conn.execute("ALTER TABLE transactions ADD COLUMN customer_name_snapshot TEXT")
    conn.execute(
        "CREATE INDEX transactions_customer ON transactions(store_id,customer_id,timestamp,id)"
    )
    for operation in ("INSERT", "UPDATE"):
        conn.execute(f"""CREATE TRIGGER transaction_customer_{operation.lower()} BEFORE {operation} ON transactions
            WHEN NEW.customer_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM customers WHERE id=NEW.customer_id AND store_id=NEW.store_id)
            BEGIN SELECT RAISE(ABORT,'Customer belongs to another store'); END""")
    conn.execute("""CREATE TRIGGER transaction_customer_immutable BEFORE UPDATE OF customer_id,customer_name_snapshot ON transactions
        WHEN NEW.customer_id IS NOT OLD.customer_id OR NEW.customer_name_snapshot IS NOT OLD.customer_name_snapshot
        BEGIN SELECT RAISE(ABORT,'Historical customer association is immutable'); END""")
    conn.execute(
        "ALTER TABLE transactions ADD COLUMN discount_cents INTEGER NOT NULL DEFAULT 0 CHECK(typeof(discount_cents)='integer' AND discount_cents>=0)"
    )
    conn.execute(
        "ALTER TABLE transactions ADD COLUMN discount_kind TEXT NOT NULL DEFAULT 'none' CHECK(discount_kind IN ('none','fixed','percent'))"
    )
    conn.execute("ALTER TABLE transactions ADD COLUMN discount_value TEXT NOT NULL DEFAULT ''")
    conn.execute(
        "ALTER TABLE transaction_items ADD COLUMN discount_cents INTEGER NOT NULL DEFAULT 0 CHECK(typeof(discount_cents)='integer' AND discount_cents>=0)"
    )
    conn.execute(
        "ALTER TABLE transaction_items ADD COLUMN discount_base_cents INTEGER NOT NULL DEFAULT 0 CHECK(typeof(discount_base_cents)='integer' AND discount_base_cents>=0)"
    )
    conn.execute(
        "ALTER TABLE transaction_items ADD COLUMN discount_bonus_units INTEGER NOT NULL DEFAULT 0 CHECK(typeof(discount_bonus_units)='integer' AND discount_bonus_units>=0)"
    )
    for operation in ("INSERT", "UPDATE"):
        conn.execute(f"""CREATE TRIGGER line_discount_{operation.lower()} BEFORE {operation} ON transaction_items
            WHEN NEW.discount_cents!=NEW.discount_base_cents*NEW.quantity+NEW.discount_bonus_units
            OR NEW.discount_bonus_units>NEW.quantity
            OR NEW.discount_base_cents>=NEW.price_cents
            OR (NEW.discount_base_cents=NEW.price_cents-1 AND NEW.discount_bonus_units>0)
            BEGIN SELECT RAISE(ABORT,'Invalid discount snapshot'); END""")
    conn.execute("""CREATE TRIGGER sale_discount_immutable BEFORE UPDATE OF discount_cents,discount_kind,discount_value ON transactions
        WHEN NEW.discount_cents!=OLD.discount_cents OR NEW.discount_kind!=OLD.discount_kind OR NEW.discount_value!=OLD.discount_value
        BEGIN SELECT RAISE(ABORT,'Original discounts are immutable'); END""")
    conn.execute("""CREATE TRIGGER line_discount_immutable BEFORE UPDATE OF discount_cents,discount_base_cents,discount_bonus_units ON transaction_items
        WHEN NEW.discount_cents!=OLD.discount_cents OR NEW.discount_base_cents!=OLD.discount_base_cents OR NEW.discount_bonus_units!=OLD.discount_bonus_units
        BEGIN SELECT RAISE(ABORT,'Original discounts are immutable'); END""")
    conn.execute("INSERT INTO schema_migrations(version) VALUES(24)")
