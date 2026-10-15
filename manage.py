"""Explicit database lifecycle commands; never run destructive reset at startup."""
import argparse
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import database.database as database
from config import TAX_RATE, business_now
from services.money import calculate_tax_cents, from_cents


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
            missing = conn.execute("SELECT DISTINCT product_id,store_id FROM transaction_items WHERE product_id NOT IN (SELECT id FROM products)").fetchall()
            for row in missing:
                conn.execute("INSERT INTO products (id,name,price,price_cents,quantity,is_active,store_id) VALUES (?,?,0.01,1,0,0,?)",
                             (row['product_id'], f"Unavailable historical product #{row['product_id']}", row['store_id']))
    return len(missing)


def seed_demo(allow_existing=False):
    """Create a separate fictional demo store; never copy any private records."""
    database.initialize_database(seed=False)
    with closing(database.get_db_connection()) as conn:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            if not allow_existing and (conn.execute('SELECT COUNT(*) FROM products').fetchone()[0] or conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]):
                raise ValueError('Demo seeding requires an empty database. Use add-public-demo to add a separate fictional store.')
            if conn.execute("SELECT 1 FROM stores WHERE kind='demo'").fetchone():
                raise ValueError('A public demo store already exists.')
            store = conn.execute("INSERT INTO stores(name,kind) VALUES ('TCCP Fictional Demo','demo')").lastrowid
            ids = []
            for name, cents, quantity in (('Demo Notebook',425,50), ('Demo USB Cable',750,30), ('Demo Desk Lamp',2499,3), ('Demo Keyboard',2999,0)):
                ids.append(conn.execute('INSERT INTO products(name,price,price_cents,quantity,store_id) VALUES (?,?,?,?,?)',
                                        (name,float(from_cents(cents)),cents,quantity,store)).lastrowid)
            for offset in (6,5,3,2,1,0):
                total = 1175 + calculate_tax_cents(1175,TAX_RATE)
                tx = conn.execute('INSERT INTO transactions(timestamp,total,total_cents,payment_method,cash_received_cents,tax_rate_percent,store_id) VALUES (?,?,?,?,?,?,?)',
                                  ((business_now()-timedelta(days=offset)).strftime('%Y-%m-%d 12:00:00'),float(from_cents(total)),total,'Card' if offset%2 else 'Cash',None if offset%2 else 2000,str(TAX_RATE*100),store)).lastrowid
                for product, name, cents in ((ids[0],'Demo Notebook',425),(ids[1],'Demo USB Cable',750)):
                    conn.execute('INSERT INTO transaction_items(transaction_id,product_id,quantity,price,price_cents,product_name,store_id) VALUES (?,?,1,?,?,?,?)',
                                 (tx,product,float(from_cents(cents)),cents,name,store))
                    conn.execute('UPDATE products SET quantity=quantity-1 WHERE id=? AND store_id=?',(product,store))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init', 'check', 'backup', 'restore', 'repair-missing-products', 'seed-demo', 'add-public-demo', 'assign-legacy-owner'])
    parser.add_argument('--file', help='Backup destination or restore source')
    parser.add_argument('--email', help='Existing registered owner for explicit legacy assignment')
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
        elif args.command in ('seed-demo', 'add-public-demo'):
            if not __import__('os').environ.get('TCCP_DB_PATH'):
                raise ValueError('Set TCCP_DB_PATH to a separate demo database first.')
            seed_demo(allow_existing=args.command == 'add-public-demo')
        elif args.command == 'assign-legacy-owner':
            if not args.email:
                parser.error('--email is required')
            from services.auth import assign_legacy_owner
            backup = Path(database.DB_FILE).with_name(Path(database.DB_FILE).name + '.' + business_now().strftime('%Y%m%d%H%M%S%f') + '.backup')
            print('Backup:', backup_database(backup))
            database.initialize_database(seed=False)
            assign_legacy_owner(args.email)
            print('Legacy ownership assigned. Sign in again; existing records were not moved.')
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
