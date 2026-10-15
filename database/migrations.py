"""Additive Day 14 migration; called inside initialize_database's transaction."""


def migrate_identity(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version=14").fetchone():
        return
    statements = (
        """CREATE TABLE users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE CHECK(length(email) BETWEEN 3 AND 254 AND email=lower(email)),
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE stores(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 100),
            kind TEXT NOT NULL DEFAULT 'private' CHECK(kind IN ('legacy','private','demo')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE UNIQUE INDEX one_legacy_store ON stores(kind) WHERE kind='legacy'""",
        """CREATE UNIQUE INDEX one_demo_store ON stores(kind) WHERE kind='demo'""",
        """CREATE TABLE store_memberships(
            user_id INTEGER NOT NULL REFERENCES users(id),
            store_id INTEGER NOT NULL REFERENCES stores(id),
            role TEXT NOT NULL CHECK(role IN ('owner')),
            PRIMARY KEY(user_id,store_id))""",
        """CREATE UNIQUE INDEX one_owner_per_store ON store_memberships(store_id) WHERE role='owner'""",
        """CREATE TABLE auth_sessions(
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            store_id INTEGER NOT NULL REFERENCES stores(id),
            expires_at INTEGER NOT NULL,
            FOREIGN KEY(user_id,store_id) REFERENCES store_memberships(user_id,store_id))""",
        """CREATE INDEX session_expiry ON auth_sessions(expires_at)""",
        """CREATE TABLE auth_limits(
            bucket TEXT PRIMARY KEY, attempts INTEGER NOT NULL CHECK(attempts>0),
            expires_at INTEGER NOT NULL)""",
        """INSERT INTO stores(id,name,kind) VALUES(1,'Unclaimed legacy store','legacy')""",
    )
    for statement in statements:
        conn.execute(statement)
    for table in ("products", "transactions", "transaction_items"):
        # SQLite permits additive REFERENCES columns only with NULL defaults.
        # Backfill existing rows and normalize omitted ownership to the fixed
        # legacy desktop store inside the same INSERT statement (never a user).
        conn.execute(f"ALTER TABLE {table} ADD COLUMN store_id INTEGER REFERENCES stores(id)")
        conn.execute(f"UPDATE {table} SET store_id=1")
        conn.execute(f"""CREATE TRIGGER {table}_legacy_default AFTER INSERT ON {table}
            WHEN NEW.store_id IS NULL BEGIN UPDATE {table} SET store_id=1 WHERE id=NEW.id; END""")
        conn.execute(f"CREATE INDEX {table}_store ON {table}(store_id)")
        conn.execute(f"CREATE UNIQUE INDEX {table}_id_store ON {table}(id,store_id)")
        for operation in ("INSERT", "UPDATE"):
            conn.execute(f"""CREATE TRIGGER {table}_store_{operation.lower()}
                BEFORE {operation} ON {table}
                WHEN (NEW.store_id IS NULL AND '{operation}'='UPDATE') OR (NEW.store_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM stores WHERE id=NEW.store_id))
                BEGIN SELECT RAISE(ABORT,'Invalid store reference'); END""")
    for operation in ("INSERT", "UPDATE"):
        conn.execute(f"""CREATE TRIGGER item_store_consistency_{operation.lower()}
            BEFORE {operation} ON transaction_items
            WHEN NOT EXISTS(SELECT 1 FROM products WHERE id=NEW.product_id AND store_id=COALESCE(NEW.store_id,1))
              OR NOT EXISTS(SELECT 1 FROM transactions WHERE id=NEW.transaction_id AND store_id=COALESCE(NEW.store_id,1))
            BEGIN SELECT RAISE(ABORT,'Cross-store line item'); END""")
    for table in ("products", "transactions"):
        conn.execute(f"""CREATE TRIGGER {table}_ownership_immutable BEFORE UPDATE OF store_id ON {table}
            WHEN OLD.store_id != NEW.store_id
            BEGIN SELECT RAISE(ABORT,'Store ownership is immutable'); END""")
    conn.execute(
        "CREATE TRIGGER item_ownership_immutable BEFORE UPDATE OF store_id ON transaction_items WHEN OLD.store_id!=NEW.store_id BEGIN SELECT RAISE(ABORT,'Store ownership is immutable'); END"
    )
    conn.execute("INSERT INTO schema_migrations(version) VALUES(14)")
