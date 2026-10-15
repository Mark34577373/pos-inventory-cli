"""Explicit database lifecycle commands; never run destructive reset at startup."""
import argparse
from contextlib import closing
from datetime import timedelta
from pathlib import Path
import sqlite3

import database.database as database
from config import business_now
from services.inventory import create_product
from services.money import calculate_tax_cents, from_cents
from services.transaction_service import complete_transaction
from config import TAX_RATE


def backup_database(destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("Backup destination already exists; choose a new filename.")
    source = Path(database.DB_FILE).resolve()
    if not source.exists():
        raise ValueError("Source database does not exist.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as conn:
        with closing(sqlite3.connect(destination)) as target:
            conn.backup(target)
    destination.chmod(0o600)
    return destination


def check_database(path=None):
    source = Path(path or database.DB_FILE).resolve()
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as conn:
        integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
    return integrity == ['ok'] and not foreign_keys, integrity, foreign_keys


def repair_missing_products():
    """Restore referential integrity with explicit inactive unknown placeholders.

    Historical line items and snapshots are never changed or inferred.
    Caller must back up the database before invoking this operation.
    """
    with closing(database.get_db_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            missing = conn.execute("SELECT DISTINCT product_id FROM transaction_items WHERE product_id NOT IN (SELECT id FROM products)").fetchall()
            for row in missing:
                conn.execute("INSERT INTO products (id,name,price,price_cents,quantity,is_active) VALUES (?,?,0.01,1,0,0)",
                             (row['product_id'], f"Unavailable historical product #{row['product_id']}"))
    return len(missing)


def seed_demo():
    """Populate an empty, explicitly selected database with fictional demo sales."""
    database.initialize_database(seed=False)
    with closing(database.get_db_connection()) as conn:
        if conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] or conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]:
            raise ValueError("Demo seeding requires an empty database. Choose a new TCCP_DB_PATH.")
    notebook = create_product("Demo Notebook", "4.25", 50)
    cable = create_product("Demo USB Cable", "7.50", 30)
    create_product("Demo Desk Lamp", "24.99", 3)
    create_product("Demo Keyboard", "29.99", 0)
    for offset in (6, 5, 3, 2, 1, 0):
        subtotal = 425 + 750
        total = subtotal + calculate_tax_cents(subtotal, TAX_RATE)
        tx = complete_transaction([{'id': notebook, 'qty': 1}, {'id': cable, 'qty': 1}], from_cents(total), "Card" if offset % 2 else "Cash",
                                  cash_received_cents=None if offset % 2 else 2000)
        with closing(database.get_db_connection()) as conn:
            with conn:
                conn.execute("UPDATE transactions SET timestamp=? WHERE id=?", ((business_now() - timedelta(days=offset)).strftime('%Y-%m-%d 12:00:00'), tx))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init', 'check', 'backup', 'restore', 'repair-missing-products', 'seed-demo'])
    parser.add_argument('--file', help='Backup destination or restore source')
    args = parser.parse_args()
    try:
        if args.command == 'check':
            ok, integrity, foreign_keys = check_database()
            print('Integrity:', integrity, 'Foreign-key violations:', foreign_keys)
            return 0 if ok else 1
        if args.command == 'backup':
            if not args.file:
                parser.error('--file is required')
            print(backup_database(args.file))
        elif args.command == 'restore':
            if not args.file:
                parser.error('--file is required')
            target = Path(database.DB_FILE).resolve()
            if target.exists():
                raise ValueError('Restore target must be a new path; never overwrite a live database.')
            if not check_database(args.file)[0]:
                raise ValueError('Backup fails integrity checks.')
            target.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(Path(args.file).resolve().as_uri() + '?mode=ro', uri=True)) as source:
                with closing(sqlite3.connect(target)) as destination:
                    source.backup(destination)
            target.chmod(0o600)
        elif args.command == 'seed-demo':
            if not __import__('os').environ.get('TCCP_DB_PATH'):
                raise ValueError('Set TCCP_DB_PATH to a separate demo database first.')
            seed_demo()
        elif args.command in ('init', 'repair-missing-products'):
            if Path(database.DB_FILE).exists():
                backup = Path(database.DB_FILE).with_name(Path(database.DB_FILE).name + '.' + business_now().strftime('%Y%m%d%H%M%S%f') + '.backup')
                print('Backup:', backup_database(backup))
            database.initialize_database(seed=False)
            if args.command == 'repair-missing-products':
                print('Inactive unknown placeholders created:', repair_missing_products())
            ok, integrity, foreign_keys = check_database()
            print('Integrity:', integrity, 'Foreign-key violations:', foreign_keys)
            return 0 if ok else 1
    except (ValueError, sqlite3.Error, OSError) as error:
        print('Operation failed:', error)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
