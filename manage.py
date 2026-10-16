"""Explicit database lifecycle commands; never run destructive reset at startup."""
import argparse
import os
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
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        with closing(sqlite3.connect(destination)) as target:
            target.execute('PRAGMA foreign_keys=ON')
            conn.backup(target)
    destination.chmod(0o600)
    return destination


def check_database(path=None):
    source = Path(path or database.DB_FILE).resolve()
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
    return integrity == ['ok'] and not foreign_keys, integrity, foreign_keys


def restore_database(source, destination):
    """Restore a validated SQLite snapshot to a new private file only.

    Incomplete or invalid output is removed; the original and existing targets
    are never replaced. Older supported schemas remain available for explicit init.
    """
    from database.validation import validate_integrity, validate_schema

    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists():
        raise ValueError('Restore target must be a new path; never overwrite a live database.')

    def validate(connection):
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'products', 'transactions', 'transaction_items'}.issubset(tables):
            raise ValueError('Restore source is not a supported TCCP database.')
        validate_schema(connection)
        validate_integrity(connection)

    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
        original.execute('PRAGMA foreign_keys=ON')
        validate(original)
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        created_inode = os.fstat(descriptor).st_ino
        os.close(descriptor)
        try:
            with closing(sqlite3.connect(destination)) as target:
                target.execute('PRAGMA foreign_keys=ON')
                original.backup(target)
                validate(target)
        except BaseException:
            # Delete only this invocation's newly created output, even on interruption.
            if destination.exists() and destination.stat().st_ino == created_inode:
                destination.unlink()
            raise
    return destination


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
            customer = conn.execute(
                'INSERT INTO customers(store_id,name,email,notes) VALUES (?,?,?,?)',
                (store, 'Alex Example (Fictional)', 'alex@example.invalid', 'Fictional portfolio demonstration only.'),
            ).lastrowid
            conn.execute(
                'INSERT INTO suppliers(store_id,name,email,notes) VALUES (?,?,?,?)',
                (store, 'Example Office Supply (Fictional)', 'supply@example.invalid', 'Fictional portfolio demonstration only.'),
            )
            ids = []
            for name, cents, quantity in (('Demo Notebook',425,50), ('Demo USB Cable',750,30), ('Demo Desk Lamp',2499,3), ('Demo Keyboard',2999,0)):
                ids.append(conn.execute('INSERT INTO products(name,price,price_cents,quantity,store_id) VALUES (?,?,?,?,?)',
                                        (name,float(from_cents(cents)),cents,quantity,store)).lastrowid)
            for offset in (6,5,3,2,1,0):
                total = 1175 + calculate_tax_cents(1175,TAX_RATE)
                tx = conn.execute('INSERT INTO transactions(timestamp,total,total_cents,payment_method,cash_received_cents,tax_rate_percent,store_id,customer_id,customer_name_snapshot) VALUES (?,?,?,?,?,?,?,?,?)',
                                  ((business_now()-timedelta(days=offset)).strftime('%Y-%m-%d 12:00:00'),float(from_cents(total)),total,'Card' if offset%2 else 'Cash',None if offset%2 else 2000,str(TAX_RATE*100),store,customer,'Alex Example (Fictional)')).lastrowid
                for product, name, cents in ((ids[0],'Demo Notebook',425),(ids[1],'Demo USB Cable',750)):
                    conn.execute('INSERT INTO transaction_items(transaction_id,product_id,quantity,price,price_cents,product_name,store_id) VALUES (?,?,1,?,?,?,?)',
                                 (tx,product,float(from_cents(cents)),cents,name,store))
                    conn.audit_context.update(type='checkout', reason='Fictional demonstration checkout', transaction_id=tx)
                    conn.execute('UPDATE products SET quantity=quantity-1 WHERE id=? AND store_id=?',(product,store))


def prepare_render_demo():
    """Opt-in bootstrap for a disposable, read-only Render Free demonstration."""
    import fcntl

    from database.validation import VERSIONS, validate_schema

    required = {'TCCP_ENV': 'production', 'TCCP_READ_ONLY': '1',
                'TCCP_ALLOW_REGISTRATION': '0', 'TCCP_EPHEMERAL_DEMO': '1'}
    if any(os.environ.get(key) != value for key, value in required.items()):
        raise ValueError('Render demo requires production, read-only, closed registration and explicit ephemeral-demo opt-in.')
    if len(os.environ.get('TCCP_SECRET_KEY', '').strip()) < 32:
        raise ValueError('Production requires TCCP_SECRET_KEY of at least 32 characters.')
    configured = os.environ.get('TCCP_DB_PATH', '')
    path = Path(configured)
    root = Path('/tmp').resolve()
    if (not path.is_absolute() or path.name != 'demo.db'
            or not path.resolve().is_relative_to(root)
            or path.resolve() == root / 'demo.db'
            or Path(database.DB_FILE).resolve() != path.resolve()
            or path.is_symlink() or path.parent.is_symlink()):
        raise ValueError('Ephemeral demo requires its own /tmp subdirectory and a demo.db filename.')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Serialize bootstrap processes; release before Gunicorn starts.
    with open(path.parent / '.bootstrap.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = False
        if path.exists():
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables and not {'products', 'transactions', 'transaction_items'}.issubset(tables):
                    raise ValueError('Refusing an unrelated database.')
                if 'users' in tables and conn.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                    raise ValueError('Refusing a database containing private accounts.')
                if 'stores' in tables and conn.execute("SELECT 1 FROM stores WHERE kind='private' LIMIT 1").fetchone():
                    raise ValueError('Refusing a database containing private stores.')
                for table in sorted(tables):
                    # Migrations create settings for the empty legacy store too.
                    # They are never selected by the public demo's store context.
                    if table == 'store_settings':
                        continue
                    # Quote metadata identifiers; never interpolate them unescaped.
                    identifier = '"' + table.replace('"', '""') + '"'
                    columns = {row[0] for row in conn.execute('SELECT name FROM pragma_table_info(?)', (table,))}
                    if 'stores' in tables and 'store_id' in columns:
                        unsafe = conn.execute(f"SELECT 1 FROM {identifier} r WHERE NOT EXISTS (SELECT 1 FROM stores s WHERE s.id=r.store_id AND s.kind='demo') LIMIT 1").fetchone()
                    elif table in ('products', 'transactions', 'transaction_items'):
                        unsafe = conn.execute(f'SELECT 1 FROM {identifier} LIMIT 1').fetchone()
                    else:
                        continue
                    if unsafe:
                        raise ValueError('Refusing non-demo business records; nothing was changed.')
                current = validate_schema(conn) == VERSIONS
            if not current:
                backup_database(path.with_name('demo.before-bootstrap.' + business_now().strftime('%Y%m%d%H%M%S%f') + '.backup'))
        if not current:
            database.initialize_database(seed=False)
        if not check_database()[0]:
            raise ValueError('Demo database integrity check failed.')
        with closing(database.get_db_connection()) as conn:
            has_demo = conn.execute("SELECT 1 FROM stores WHERE kind='demo'").fetchone() is not None
        if not has_demo:
            # Existing seeder is transactional and refuses duplicate demo stores.
            seed_demo()
        path.chmod(0o600)
        if not check_database()[0]:
            raise ValueError('Demo database integrity check failed after bootstrap.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init', 'check', 'backup', 'restore', 'repair-missing-products', 'seed-demo', 'add-public-demo', 'assign-legacy-owner', 'prepare-render-demo'])
    parser.add_argument('--file', help='Backup destination or restore source')
    parser.add_argument('--email', help='Existing registered owner for explicit legacy assignment')
    args = parser.parse_args()
    try:
        if args.command == 'prepare-render-demo':
            prepare_render_demo()
            print('Read-only fictional demo database ready.')
        elif args.command == 'check':
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
            restore_database(args.file, database.DB_FILE)
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
            database.initialize_database(seed=False, allow_missing_products=args.command=='repair-missing-products')
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
