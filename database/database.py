# database/database.py
import sqlite3
import os

DB_FILE = os.environ.get("TCCP_DB_PATH") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tccp.db",
)

def get_db_connection():
    """Establishes a connection to the SQLite database with row factory enabled."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row  # Allows accessing columns by name like dictionary keys
    conn.execute("PRAGMA foreign_keys = ON")
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


def initialize_database(seed=True):
    """Creates the necessary tables if they do not exist and seeds initial data."""
    from contextlib import closing
    with closing(get_db_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            _initialize_schema(conn, seed)


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
