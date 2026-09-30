# database/database.py
import sqlite3
import os

DB_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tccp.db",
)

def get_db_connection():
    """Establishes a connection to the SQLite database with row factory enabled."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row  # Allows accessing columns by name like dictionary keys
    return conn

def initialize_database():
    """Creates the necessary tables if they do not exist and seeds initial data."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Products Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        price REAL NOT NULL,
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
        FOREIGN KEY (transaction_id) REFERENCES transactions (id),
        FOREIGN KEY (product_id) REFERENCES products (id)
    )
    """)

    cursor.execute("PRAGMA table_info(products)")
    product_columns = [column["name"] for column in cursor.fetchall()]
    if "is_active" not in product_columns:
        cursor.execute(
            "ALTER TABLE products "
            "ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
        )

 # Add payment_method to existing databases if it doesn't exist
    cursor.execute("PRAGMA table_info(transactions)")
    transaction_columns = [column["name"] for column in cursor.fetchall()]

    if "payment_method" not in transaction_columns:
        cursor.execute(
            "ALTER TABLE transactions "
            "ADD COLUMN payment_method TEXT NOT NULL DEFAULT 'Unknown'"
        )

    # Seed initial data if the products table is completely empty

    # Seed initial data if the products table is completely empty
    cursor.execute("SELECT COUNT(*) FROM products")
    if cursor.fetchone()[0] == 0:
        initial_products = [
            ("Mouse", 10.99, 5),
            ("Keyboard", 29.99, 0)
        ]
        cursor.executemany("INSERT INTO products (name, price, quantity) VALUES (?, ?, ?)", initial_products)
        
    conn.commit()
    conn.close()
