# database/database.py
import os
import sqlite3

DB_FILE = os.environ.get("TCCP_DB_PATH") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tccp.db",
)

class InventoryConnection(sqlite3.Connection):
    """A connection carries audit metadata only for its own transaction."""


def get_db_connection():
    """Establishes a connection to the SQLite database with row factory enabled."""
    if str(DB_FILE) != ':memory:':
        from pathlib import Path
        path = Path(DB_FILE)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
    conn = sqlite3.connect(DB_FILE, factory=InventoryConnection, timeout=5)
    from database.tenancy import current_store_id
    conn.create_function("authorized_store_id", 0, current_store_id)
    from flask import g, has_request_context
    conn.audit_context = {}
    conn.create_function("audit_actor_id", 0, lambda: g.user['id'] if has_request_context() and getattr(g, 'user', None) else None)
    conn.create_function("audit_type", 0, lambda: conn.audit_context.get('type', 'correction'))
    conn.create_function("audit_reason", 0, lambda: conn.audit_context.get('reason', 'Stock correction by local client or maintenance'))
    conn.create_function("audit_transaction_id", 0, lambda: conn.audit_context.get('transaction_id'))
    conn.create_function("audit_refund_id", 0, lambda: conn.audit_context.get("refund_id"))
    conn.row_factory = sqlite3.Row  # Allows accessing columns by name like dictionary keys
    conn.execute("PRAGMA foreign_keys = ON")
    if conn.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
        conn.close()
        raise RuntimeError('SQLite foreign-key enforcement is required.')
    conn.execute('PRAGMA busy_timeout=5000')
    return conn


def _ensure_column(cursor, table, column_name, declaration):
    cursor.execute(f"PRAGMA table_info({table})")
    existing_columns = {column["name"] for column in cursor.fetchall()}
    if column_name in existing_columns:
        return False

    cursor.execute(
        f"ALTER TABLE {table} ADD COLUMN {column_name} {declaration}"
    )
    return True


def initialize_database(seed=True, *, allow_missing_products=False):
    """Creates the necessary tables if they do not exist and seeds initial data."""
    from contextlib import closing

    from flask import has_request_context

    from database.validation import validate_integrity, validate_schema
    if has_request_context():
        raise PermissionError('Database migrations require a trusted local maintenance command.')
    with closing(get_db_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            validate_schema(conn)
            validate_integrity(conn, allow_missing_products=allow_missing_products)
            _initialize_schema(conn, seed)
            from database.migrations import migrate_identity
            migrate_identity(conn)
            from database.inventory_migration import migrate_inventory
            migrate_inventory(conn)
            from database.analytics_migration import migrate_analytics
            migrate_analytics(conn)
            from database.refund_migration import migrate_refunds
            migrate_refunds(conn)
            from database.settings_migration import migrate_settings
            migrate_settings(conn)
            from database.advanced_analytics_migration import migrate_advanced_analytics
            migrate_advanced_analytics(conn)
            validate_schema(conn, require_current=True)
            validate_integrity(conn, allow_missing_products=allow_missing_products)


def _initialize_schema(conn, seed):
    cursor = conn.cursor()

    # 1. Products Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        price_cents INTEGER NOT NULL DEFAULT 0,
        quantity INTEGER NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1
    )
    """)

    # 2. Transactions Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        total REAL NOT NULL,
        total_cents INTEGER NOT NULL DEFAULT 0,
        payment_method TEXT NOT NULL
    )
    """)

    # 3. Transaction Items Table (Line items for each receipt)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS transaction_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL,
        price REAL NOT NULL,
        price_cents INTEGER NOT NULL DEFAULT 0,
        product_name TEXT NOT NULL DEFAULT '',
        FOREIGN KEY (transaction_id) REFERENCES transactions (id),
        FOREIGN KEY (product_id) REFERENCES products (id)
    )
    """)

    if _ensure_column(cursor, "products", "is_active", "INTEGER NOT NULL DEFAULT 1"):
        pass
    if _ensure_column(cursor, "products", "price_cents", "INTEGER NOT NULL DEFAULT 0"):
        cursor.execute(
            "UPDATE products SET price_cents = CAST(ROUND(price * 100) AS INTEGER)"
        )

    if _ensure_column(cursor, "transactions", "payment_method", "TEXT NOT NULL DEFAULT 'Unknown'"):
        pass
    if _ensure_column(cursor, "transactions", "total_cents", "INTEGER NOT NULL DEFAULT 0"):
        cursor.execute(
            "UPDATE transactions SET total_cents = CAST(ROUND(total * 100) AS INTEGER)"
        )
    if _ensure_column(cursor, "transaction_items", "price_cents", "INTEGER NOT NULL DEFAULT 0"):
        cursor.execute(
            "UPDATE transaction_items "
            "SET price_cents = CAST(ROUND(price * 100) AS INTEGER)"
        )
    if _ensure_column(cursor, "transaction_items", "product_name", "TEXT NOT NULL DEFAULT ''"):
        cursor.execute(
            "UPDATE transaction_items "
            "SET product_name = COALESCE(("
            "SELECT name FROM products WHERE products.id = transaction_items.product_id"
            "), '')"
        )

    for column, declaration in (
        ("checkout_token", "TEXT"), ("cash_received_cents", "INTEGER"),
        ("tax_rate_percent", "TEXT"),
    ):
        _ensure_column(cursor, "transactions", column, declaration)
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS transactions_checkout_token ON transactions(checkout_token)")
    cursor.execute("CREATE INDEX IF NOT EXISTS transactions_timestamp ON transactions(timestamp)")
    cursor.execute("CREATE INDEX IF NOT EXISTS items_transaction ON transaction_items(transaction_id)")
    # Triggers apply equally to fresh and legacy schemas without rewriting history.
    rules = {
        "products": "typeof(NEW.price_cents) != 'integer' OR NEW.price_cents <= 0 OR typeof(NEW.quantity) != 'integer' OR NEW.quantity < 0 OR NEW.quantity > 1000000 OR NEW.is_active NOT IN (0,1)",
        "transactions": "typeof(NEW.total_cents) != 'integer' OR NEW.total_cents < 0",
        "transaction_items": "typeof(NEW.price_cents) != 'integer' OR NEW.price_cents <= 0 OR typeof(NEW.quantity) != 'integer' OR NEW.quantity <= 0",
    }
    for table, condition in rules.items():
        for operation in ("INSERT", "UPDATE"):
            cursor.execute(f"CREATE TRIGGER IF NOT EXISTS validate_{table}_{operation.lower()} BEFORE {operation} ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT, 'Invalid numeric database value'); END")

    cursor.execute("SELECT COUNT(*) FROM products")
    if cursor.fetchone()[0] == 0 and seed:
        initial_products = [
            ("Mouse", 10.99, 1099, 5),
            ("Keyboard", 29.99, 2999, 0)
        ]
        cursor.executemany(
            "INSERT INTO products (name, price, price_cents, quantity) "
            "VALUES (?, ?, ?, ?)",
            initial_products,
        )


def get_store_connection():
    """Only available after authorization (or to the fixed local desktop)."""
    from database.tenancy import current_store_id
    current_store_id()
    return get_db_connection()
