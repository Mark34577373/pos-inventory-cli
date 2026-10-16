"""Schema 26 supports Days 24–25 expense ledgers; not a Day 26 UI redesign."""


def migrate_expenses(conn):
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version=26").fetchone():
        return
    conn.execute("""CREATE TABLE expenses(
        id INTEGER PRIMARY KEY,store_id INTEGER NOT NULL REFERENCES stores(id),
        category TEXT NOT NULL CHECK(category IN ('Rent','Utilities','Supplies','Equipment','Marketing','Transportation','Software','Miscellaneous')),
        description TEXT NOT NULL CHECK(length(description) BETWEEN 1 AND 200),
        amount_cents INTEGER NOT NULL CHECK(typeof(amount_cents)='integer' AND amount_cents BETWEEN 0 AND 10000000000),
        expense_date TEXT NOT NULL,supplier_id INTEGER,notes TEXT NOT NULL DEFAULT '' CHECK(length(notes)<=1000),
        entry_kind TEXT NOT NULL DEFAULT 'expense' CHECK(entry_kind IN ('expense','reversal')),
        reverses_id INTEGER,actor_user_id INTEGER,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 20 AND 128),
        request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
        UNIQUE(store_id,idempotency_key),UNIQUE(id,store_id),UNIQUE(reverses_id),
        CHECK((entry_kind='expense' AND reverses_id IS NULL) OR (entry_kind='reversal' AND reverses_id IS NOT NULL)),
        FOREIGN KEY(reverses_id,store_id) REFERENCES expenses(id,store_id),
        FOREIGN KEY(supplier_id,store_id) REFERENCES suppliers(id,store_id),
        FOREIGN KEY(actor_user_id,store_id) REFERENCES store_memberships(user_id,store_id))""")
    conn.execute("CREATE INDEX expenses_store_date ON expenses(store_id,expense_date,id)")
    conn.execute(
        "CREATE INDEX expenses_store_category ON expenses(store_id,category,expense_date,id)"
    )
    conn.execute("""CREATE TRIGGER expense_reversal_guard BEFORE INSERT ON expenses
        WHEN NEW.entry_kind='reversal' AND NOT EXISTS(SELECT 1 FROM expenses e
            WHERE e.id=NEW.reverses_id AND e.store_id=NEW.store_id AND e.entry_kind='expense'
            AND e.amount_cents=NEW.amount_cents AND e.category=NEW.category
            AND e.description=NEW.description AND e.supplier_id IS NEW.supplier_id)
        BEGIN SELECT RAISE(ABORT,'Invalid expense reversal'); END""")
    for operation in ("UPDATE", "DELETE"):
        conn.execute(f"""CREATE TRIGGER expenses_{operation.lower()}_immutable BEFORE {operation} ON expenses
            BEGIN SELECT RAISE(ABORT,'Expense history is immutable; append a correction'); END""")
    conn.execute("INSERT INTO schema_migrations(version) VALUES(26)")
