"""Day 13 integration checks against isolated SQLite databases."""
import re
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from auth_helpers import legacy_owner, login

import database.database as db
from config import TAX_RATE
from manage import backup_database, check_database, repair_missing_products, seed_demo
from services.inventory import create_product, find_product_by_id, update_product_record
from services.money import calculate_tax_cents, from_cents, parse_cash, to_cents
from services.transaction_service import complete_transaction
from web_app import app


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = db.DB_FILE
        db.DB_FILE = str(Path(self.tmp.name) / 'test.db')
        db.initialize_database()
        self.config = app.config.copy()
        app.config.update(TESTING=True, DEMO_READ_ONLY=False)
        self.client = app.test_client()
        legacy_owner(self.client)

    def tearDown(self):
        app.config.clear()
        app.config.update(self.config)
        db.DB_FILE = self.original
        self.tmp.cleanup()

    def query(self, sql, params=()):
        with closing(db.get_db_connection()) as conn:
            return [dict(row) for row in conn.execute(sql, params)]

    def post(self, path, data=None, client=None):
        client = client or self.client
        client.get('/sale')
        with client.session_transaction() as session:
            values = {'csrf_token': session['csrf_token'], 'checkout_token': session['checkout_token']}
        values.update(data or {})
        return client.post(path, data=values, follow_redirects=True)

    def add(self):
        return self.post('/sale/add', {'product_id': 1, 'quantity': 1})

    def total(self):
        return from_cents(1099 + calculate_tax_cents(1099, TAX_RATE))

    def test_pages_and_empty_states(self):
        for path in ('/', '/inventory', '/products/new', '/products/1/edit', '/sale', '/transactions', '/reports'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.get('/missing').status_code, 404)
        self.assertEqual(self.client.get('/receipt').status_code, 302)
        self.assertIn(b'No transactions yet', self.client.get('/').data)

    def test_huge_ids_are_safe(self):
        huge = '9' * 100
        for path in ('/inventory?q=', '/transactions?q=', '/sale?q=', '/transactions/'):
            self.assertIn(self.client.get(path + huge).status_code, (200, 404))
        self.assertEqual(self.post('/sale/add', {'product_id': huge, 'quantity': 1}).status_code, 200)

    def test_csrf_and_read_only_demo(self):
        self.assertEqual(self.client.post('/products/new', data={'name': 'Bad'}).status_code, 400)
        self.client.get('/sale')
        self.assertEqual(self.client.post('/sale/cancel', data={'csrf_token': 'wrong'}).status_code, 400)
        app.config['DEMO_READ_ONLY'] = True
        self.assertEqual(self.post('/sale/cancel').status_code, 403)
        self.assertEqual(self.query('SELECT COUNT(*) AS n FROM transactions')[0]['n'], 0)

    def test_every_post_form_has_csrf(self):
        self.add()
        for path in ('/inventory', '/products/new', '/products/1/edit', '/sale'):
            html = self.client.get(path).data.decode()
            for form in re.findall(r'<form\b.*?</form>', html, re.S):
                if re.search(r'method=["\']post', form, re.I):
                    self.assertIn('name="csrf_token"', form)

    def test_card_receipt_is_committed_and_small_session(self):
        self.add()
        response = self.post('/sale/checkout', {'payment_method': 'Card'})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'11.90', response.data)
        self.assertEqual(find_product_by_id(1)['quantity'], 4)
        with self.client.session_transaction() as session:
            self.assertNotIn('last_receipt', session)
            self.assertNotIn('sale_cart', session)
            self.assertIsInstance(session['last_receipt_id'], int)
        update_product_record(1, 'Renamed', '20.00', 4)
        self.assertIn(b'Mouse', self.client.get('/receipt').data)
        self.assertNotIn(b'Renamed', self.client.get('/receipt').data)

    def test_cash_change_is_persisted(self):
        self.add()
        response = self.post('/sale/checkout', {'payment_method': 'Cash', 'cash_received': '20.00'})
        self.assertIn(b'8.10', response.data)
        self.assertEqual(self.query('SELECT cash_received_cents FROM transactions')[0]['cash_received_cents'], 2000)

    def test_invalid_checkout_preserves_cart_and_stock(self):
        self.add()
        for data in ({'payment_method': 'Other'}, {'payment_method': 'Cash', 'cash_received': '1.00'},
                     {'payment_method': 'Cash', 'cash_received': 'NaN'}, {'payment_method': 'Cash', 'cash_received': '1e999'},
                     {'payment_method': 'Cash', 'cash_received': '20.001'}):
            self.post('/sale/checkout', data)
            self.assertEqual(self.query('SELECT COUNT(*) AS n FROM transactions')[0]['n'], 0)
            self.assertEqual(find_product_by_id(1)['quantity'], 5)
        self.post('/sale/cancel')
        self.assertEqual(find_product_by_id(1)['quantity'], 5)

    def test_unavailable_cart_can_be_cleared(self):
        self.add()
        with closing(db.get_db_connection()) as conn:
            with conn:
                conn.execute('UPDATE products SET is_active=0 WHERE id=1')
        self.assertIn(b'Clear cart', self.client.get('/sale').data)
        self.post('/sale/cancel')
        self.assertNotIn(b'Unavailable cart entries', self.client.get('/sale').data)

    def test_invalid_product_form_and_atomic_edit(self):
        for price in ('NaN', '-1', '0', '1.001', '1e999'):
            self.assertEqual(self.post('/products/new', {'name': 'Bad', 'price': price, 'quantity': 1}).status_code, 400)
        with self.assertRaises(ValueError):
            update_product_record(1, 'Changed', '20', -1)
        self.assertEqual(find_product_by_id(1)['price_cents'], 1099)
        self.post('/products/1/edit', {'name': 'Changed', 'price': '20', 'quantity': 8})
        self.assertEqual(find_product_by_id(1)['price_cents'], 2000)
        self.assertEqual(find_product_by_id(1)['quantity'], 8)

    def test_product_name_cannot_enter_confirmation_script(self):
        create_product("');alert(1);//", '1', 2)
        response = self.client.get('/inventory').data.decode()
        for handler in re.findall(r'onsubmit="([^"]*)"', response):
            self.assertNotIn('alert', handler)

    def test_checkout_rolls_back_every_table_on_insert_failure(self):
        with closing(db.get_db_connection()) as conn:
            conn.execute("CREATE TRIGGER fail_items BEFORE INSERT ON transaction_items BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(sqlite3.Error):
            complete_transaction([{'id': 1, 'qty': 1}], self.total(), 'Card')
        self.assertEqual(find_product_by_id(1)['quantity'], 5)
        self.assertFalse(self.query('SELECT * FROM transactions'))
        self.assertFalse(self.query('SELECT * FROM transaction_items'))

    def test_price_and_late_stock_failure_roll_back(self):
        for items, total in (([{'id': 1, 'qty': 1}], '1'), ([{'id': 1, 'qty': 1}, {'id': 2, 'qty': 1}], self.total())):
            with self.assertRaises(ValueError):
                complete_transaction(items, total, 'Card')
            self.assertEqual(find_product_by_id(1)['quantity'], 5)
            self.assertFalse(self.query('SELECT * FROM transactions'))

    def test_duplicate_token_is_one_transaction_under_concurrency(self):
        def buy(_):
            return complete_transaction([{'id': 1, 'qty': 1}], self.total(), 'Card', checkout_token='same-request')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(buy, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(find_product_by_id(1)['quantity'], 4)
        self.assertEqual(len(self.query('SELECT * FROM transactions')), 1)

    def test_two_buyers_cannot_oversell_last_unit(self):
        update_product_record(1, 'Mouse', '10.99', 1)
        def buy(index):
            try:
                complete_transaction([{'id': 1, 'qty': 1}], self.total(), 'Card', checkout_token=str(index))
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(buy, range(2)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(find_product_by_id(1)['quantity'], 0)
        self.assertEqual(len(self.query('SELECT * FROM transactions')), 1)

    def test_sessions_are_isolated(self):
        self.add()
        other = app.test_client()
        login(other)
        self.assertIn(b'Your cart is empty', other.get('/sale').data)
        self.assertNotIn(b'Your cart is empty', self.client.get('/sale').data)

    def test_chart_seven_days_and_report_totals(self):
        self.add()
        self.post('/sale/checkout', {'payment_method': 'Card'})
        with patch('web_app.render_template', return_value='ok') as render:
            self.client.get('/')
            values = render.call_args.kwargs
        self.assertEqual(len(values['sales_chart']), 7)
        self.assertEqual(sum(day['sales_cents'] for day in values['sales_chart']), 1190)
        self.assertEqual(values['today_sales_cents'], 1190)
        with patch('web_app.render_template', return_value='ok') as render:
            self.client.get('/reports')
        self.assertEqual(render.call_args.kwargs['overview']['revenue_cents'], 1190)
        for suffix in ('?start_date=bad', '?start_date=2026-10-20&end_date=2026-10-01'):
            self.assertEqual(self.client.get('/reports' + suffix).status_code, 200)

    def test_database_constraints_and_repeated_migration(self):
        db.initialize_database()
        for statement in ('UPDATE products SET quantity=-1 WHERE id=1', 'UPDATE products SET price_cents=1.5 WHERE id=1',
                          "INSERT INTO transaction_items(transaction_id,product_id,quantity,price,price_cents) VALUES (999,1,1,1,100)"):
            with closing(db.get_db_connection()) as conn:
                with self.assertRaises(sqlite3.IntegrityError):
                    with conn:
                        conn.execute(statement)
        self.assertTrue(check_database()[0])

    def test_backup_restore_and_orphan_repair_preserve_history(self):
        complete_transaction([{'id': 1, 'qty': 1}], self.total(), 'Card')
        with closing(sqlite3.connect(db.DB_FILE)) as conn:
            conn.execute('DELETE FROM products WHERE id=1')
            conn.commit()
        before = self.query('SELECT * FROM transaction_items')
        self.assertFalse(check_database()[0])
        backup = backup_database(Path(self.tmp.name) / 'before.db')
        self.assertTrue(backup.exists())
        self.assertEqual(repair_missing_products(), 1)
        self.assertTrue(check_database()[0])
        self.assertEqual(before, self.query('SELECT * FROM transaction_items'))
        self.assertIsNone(find_product_by_id(1))
        with self.assertRaises(ValueError):
            backup_database(backup)
        healthy = backup_database(Path(self.tmp.name) / 'healthy.db')
        with closing(sqlite3.connect(healthy)) as source:
            with closing(sqlite3.connect(Path(self.tmp.name) / 'restored.db')) as target:
                source.backup(target)
        self.assertTrue(check_database(Path(self.tmp.name) / 'restored.db')[0])

    def test_database_failure_is_friendly(self):
        with patch('web_app.list_active_products', side_effect=sqlite3.OperationalError('locked')):
            with self.assertLogs(app.logger, level='ERROR'):
                response = self.client.get('/')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b'Traceback', response.data)

    def test_fictional_seed_requires_empty_database(self):
        with self.assertRaises(ValueError):
            seed_demo()
        db.DB_FILE = str(Path(self.tmp.name) / 'demo.db')
        seed_demo()
        self.assertTrue(check_database()[0])
        self.assertEqual(len(self.query('SELECT * FROM transactions')), 6)

    def test_production_configuration_requires_secret_and_is_read_only(self):
        import os
        import subprocess
        import sys
        env = dict(os.environ, TCCP_ENV='production', TCCP_DB_PATH=db.DB_FILE)
        env.pop('TCCP_SECRET_KEY', None)
        env.pop('TCCP_READ_ONLY', None)
        result = subprocess.run([sys.executable, '-B', '-c', 'import web_app'], env=env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Production requires', result.stderr)
        env['TCCP_SECRET_KEY'] = 'fictional-test-secret-' * 3
        result = subprocess.run([sys.executable, '-B', '-c',
            'from web_app import app; assert app.config["DEMO_READ_ONLY"]; assert app.config["SESSION_COOKIE_SECURE"]; assert app.test_client().post("/sale/cancel").status_code == 403'],
            env=env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_business_day_boundaries_and_empty_chart(self):
        with patch('web_app.business_now', return_value=datetime(2026, 10, 15, 12)):
            with patch('web_app.render_template', return_value='ok') as render:
                self.client.get('/')
            self.assertEqual([day['sales_cents'] for day in render.call_args.kwargs['sales_chart']], [0]*7)
            for stamp in ('2026-10-14 23:59:59', '2026-10-15 00:00:00', '2026-10-15 23:59:59', '2026-10-16 00:00:00'):
                with closing(db.get_db_connection()) as conn:
                    with conn:
                        conn.execute("INSERT INTO transactions(timestamp,total,total_cents,payment_method) VALUES (?,1,100,'Cash')", (stamp,))
            with patch('web_app.render_template', return_value='ok') as render:
                self.client.get('/')
            self.assertEqual(render.call_args.kwargs['today_sales_cents'], 200)
            with patch('web_app.render_template', return_value='ok') as render:
                self.client.get('/reports?start_date=2026-10-15&end_date=2026-10-15')
            self.assertEqual(render.call_args.kwargs['overview']['revenue_cents'], 200)

    def test_legacy_migration_preserves_snapshots_and_is_repeatable(self):
        db.DB_FILE = str(Path(self.tmp.name) / 'legacy.db')
        with closing(sqlite3.connect(db.DB_FILE)) as conn:
            conn.executescript("""
                CREATE TABLE products(id INTEGER PRIMARY KEY, name TEXT NOT NULL, price REAL NOT NULL, quantity INTEGER NOT NULL);
                CREATE TABLE transactions(id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, total REAL NOT NULL);
                CREATE TABLE transaction_items(id INTEGER PRIMARY KEY,transaction_id INTEGER NOT NULL,product_id INTEGER NOT NULL,quantity INTEGER NOT NULL,price REAL NOT NULL,
                    FOREIGN KEY(transaction_id) REFERENCES transactions(id), FOREIGN KEY(product_id) REFERENCES products(id));
                INSERT INTO products VALUES(1,'Legacy',4.25,5);
                INSERT INTO transactions VALUES(1,'2026-10-15 12:00:00',4.60);
                INSERT INTO transaction_items VALUES(1,1,1,1,4.25);
            """)
        db.initialize_database(seed=False)
        db.initialize_database(seed=False)
        self.assertEqual(self.query('SELECT total_cents,payment_method FROM transactions')[0], {'total_cents':460,'payment_method':'Unknown'})
        self.assertEqual(self.query('SELECT price_cents,product_name FROM transaction_items')[0], {'price_cents':425,'product_name':'Legacy'})
        self.assertTrue(check_database()[0])

    def test_unicode_inputs_and_repeated_web_checkout_are_safe(self):
        self.assertEqual(self.client.get('/inventory?q=²').status_code, 200)
        self.assertEqual(self.client.get('/transactions?q=²').status_code, 200)
        self.assertEqual(self.client.post('/sale/cancel', data={'csrf_token':'é'}).status_code, 400)
        self.add()
        with self.client.session_transaction() as session:
            prior = dict(session)
        self.post('/sale/checkout', {'payment_method':'Card'})
        other = app.test_client()
        with other.session_transaction() as session:
            session.update(prior)
        self.post('/sale/checkout', {'payment_method':'Card'}, client=other)
        self.assertEqual(len(self.query('SELECT * FROM transactions')), 1)
        self.assertEqual(find_product_by_id(1)['quantity'], 4)

    def test_cli_checkout_and_price_mirror(self):
        from services.checkout import checkout, session_cart
        from services.inventory import update_product
        session_cart.clear()
        try:
            with patch('builtins.input', side_effect=['1', '', '12.50', '']):
                update_product()
            self.assertEqual(find_product_by_id(1)['price_cents'], 1250)
            session_cart.add_item(1, 1)
            with patch('builtins.input', side_effect=['y', '2']):
                self.assertTrue(checkout())
            self.assertEqual(find_product_by_id(1)['quantity'], 4)
        finally:
            session_cart.clear()


class MoneyTests(unittest.TestCase):
    def test_half_up_and_exact_cents(self):
        self.assertEqual(to_cents('1.005'), 101)
        self.assertEqual(calculate_tax_cents(100, '0.005'), 1)
        self.assertEqual(from_cents(1099), Decimal('10.99'))

    def test_invalid_and_extreme_money(self):
        for value in ('NaN', 'Infinity', '1e999', 'bad', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                to_cents(value)
        for value in ('-1', '1.001', '1e999'):
            with self.assertRaises(ValueError):
                parse_cash(value)
