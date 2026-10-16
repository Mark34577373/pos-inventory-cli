"""Release gates using disposable SQLite, real services and hostile HTTP requests."""
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import test_customers as fixtures
from auth_helpers import PASSWORD, login

from config import business_now
from database import database as db
from manage import backup_database, check_database, restore_database
from services.analytics import resolve_filters
from services.dashboard_presentation import dashboard_report
from services.expenses import correct_expense, create_expense
from services.financials import financial_report
from services.held_sales import get_held, hold_sale, resume_sale
from services.pos_layout import defaults, save_layout
from services.purchasing import (
    create_order,
    get_order,
    receive_order,
    save_order_line,
    save_product_cost,
    save_supplier,
    transition_order,
)
from services.refunds import get_sale, process_refund
from services.transaction_service import complete_transaction
from web_app import app


class Day27Tests(unittest.TestCase):
    setUp = fixtures.CustomerTests.setUp
    tearDown = fixtures.CustomerTests.tearDown
    context = fixtures.CustomerTests.context
    query = fixtures.CustomerTests.query
    post = fixtures.CustomerTests.post

    def test_registration_closed_has_no_identity_or_throttle_writes(self):
        app.config['REGISTRATION_ENABLED'] = False
        public = app.test_client()
        public.get('/login')
        with public.session_transaction() as state:
            csrf = state['csrf_token']
        before = {table: self.query(f'SELECT * FROM {table}') for table in ('users', 'stores', 'auth_limits')}
        with patch('auth_routes.register_user') as register:
            self.assertEqual(public.get('/register').status_code, 403)
            result = public.post('/register', data=dict(csrf_token=csrf, email='new@example.com', password=PASSWORD, password_confirm=PASSWORD))
            self.assertEqual(result.status_code, 403)
            register.assert_not_called()
        self.assertNotIn('href="/register"', public.get('/login').text)
        for table, rows in before.items():
            self.assertEqual(self.query(f'SELECT * FROM {table}'), rows)
        # Existing identities and the read-only demo entry remain available.
        self.assertIn('Explore the read-only demo', public.get('/login').text)
        login(public, 'alpha@example.com')
        self.assertEqual(public.get('/account').status_code, 200)

    def production(self, **overrides):
        env = dict(os.environ, TCCP_ENV='production', TCCP_DB_PATH=db.DB_FILE,
                   TCCP_SECRET_KEY='fictional-day27-production-test-secret')
        env.pop('TCCP_READ_ONLY', None)
        env.pop('TCCP_ALLOW_REGISTRATION', None)
        env.update(overrides)
        return subprocess.run([sys.executable, '-B', '-c',
            'import json; from web_app import app; print(json.dumps({k:app.config[k] for k in ("DEMO_READ_ONLY","REGISTRATION_ENABLED","SESSION_COOKIE_SECURE","DEBUG")}))'],
            env=env, capture_output=True, text=True)

    def test_production_defaults_and_explicit_opt_in(self):
        result = self.production()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), dict(DEMO_READ_ONLY=True, REGISTRATION_ENABLED=False, SESSION_COOKIE_SECURE=True, DEBUG=False))
        result = self.production(TCCP_READ_ONLY='0', TCCP_ALLOW_REGISTRATION='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['DEMO_READ_ONLY'])
        self.assertTrue(json.loads(result.stdout)['REGISTRATION_ENABLED'])

    def test_invalid_security_flags_fail_before_opening_database(self):
        for key in ('TCCP_READ_ONLY', 'TCCP_ALLOW_REGISTRATION'):
            for value in ('true', 'yes', '', '2'):
                with self.subTest(key=key, value=value):
                    result = self.production(**{key: value, 'TCCP_DB_PATH': '/does/not/exist/day27.db'})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(key + ' must be 0 or 1', result.stderr)

    def test_misspelled_production_environment_cannot_start_in_development(self):
        result = self.production(TCCP_ENV='prod')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('TCCP_ENV must be', result.stderr)

    def test_production_rejects_database_in_public_static_directory(self):
        unsafe = str(Path(app.static_folder) / 'never-created-day27.sqlite')
        result = self.production(TCCP_DB_PATH=unsafe)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('outside the public static directory', result.stderr)
        self.assertFalse(Path(unsafe).exists())

    def test_restore_rejects_unsupported_or_guardless_sources(self):
        unrelated = Path(self.tmp.name) / 'unrelated.db'
        with sqlite3.connect(unrelated) as conn:
            conn.execute('CREATE TABLE unrelated(id INTEGER)')
        destination = Path(self.tmp.name) / 'invalid-restore.db'
        with self.assertRaises(ValueError):
            restore_database(unrelated, destination)
        self.assertFalse(destination.exists())
        backup = backup_database(Path(self.tmp.name) / 'guardless.db')
        with sqlite3.connect(backup) as conn:
            conn.execute('DROP TRIGGER products_ownership_immutable')
        with self.assertRaises(ValueError):
            restore_database(backup, destination)
        self.assertFalse(destination.exists())

    def test_failed_restore_removes_only_new_output_and_preserves_source(self):
        backup = backup_database(Path(self.tmp.name) / 'source.db')
        before = backup.read_bytes()
        destination = Path(self.tmp.name) / 'interrupted.db'
        # First validation is source; second is copied output. Simulate target I/O failure.
        from database.validation import validate_integrity
        count = 0

        def interrupted(connection):
            nonlocal count
            count += 1
            if count == 2:
                raise sqlite3.OperationalError('Injected disk failure')
            return validate_integrity(connection)

        with patch('database.validation.validate_integrity', side_effect=interrupted):
            with self.assertRaises(sqlite3.OperationalError):
                restore_database(backup, destination)
        self.assertFalse(destination.exists())
        self.assertEqual(before, backup.read_bytes())
        restore_database(backup, destination)
        self.assertTrue(check_database(destination)[0])
        restored = destination.read_bytes()
        with self.assertRaises(ValueError):
            restore_database(backup, destination)
        self.assertEqual(restored, destination.read_bytes())
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_three_store_purchase_held_sale_refund_expense_and_restore_reconcile(self):
        records = []
        day = business_now().date().isoformat()
        for index in range(3):
            with self.context(index):
                pid = self.products[index]
                supplier = save_supplier({'name': f'Day27 supplier {index}'})
                save_product_cost(pid, '.40', supplier)
                order = create_order(supplier)
                save_order_line(order, pid, 5, '.40')
                line = get_order(order)['lines'][0]['id']
                transition_order(order, 'order')
                key = secrets.token_urlsafe(24)
                received = receive_order(order, {line: 2}, key)
                self.assertEqual(receive_order(order, {line: 2}, key), received)
                self.assertEqual(get_order(order)['order']['status'], 'partially_received')
                receive_order(order, {line: 3}, secrets.token_urlsafe(24))
                self.assertEqual(get_order(order)['order']['status'], 'received')
                save_layout(dict(defaults(), preset='custom', favorites=[pid], product_order=[pid]), 0)
                held = hold_sale({str(pid): 3}, self.ids[index], {'kind':'fixed','value':'.50'}, 'Fictional note', f'Day27 hold {index}', secrets.token_urlsafe(24))
                claim = resume_sale(held)
                args = dict(checkout_token=claim['checkout_token'], cash_received_cents=500,
                            customer_id=self.ids[index], discount_kind='fixed', discount_value='.50',
                            held_sale_id=held, held_claim=claim['claim'], order_notes=claim['notes'])
                tx = complete_transaction([{'id':pid,'qty':3}], '2.71', 'Cash', **args)
                self.assertEqual(complete_transaction([{'id':pid,'qty':3}], '2.71', 'Cash', **args), tx)
                receipt = get_sale(tx)
                self.assertEqual(receipt['transaction']['total_cents'], 271)
                self.assertEqual(receipt['transaction']['customer_id'], self.ids[index])
                self.assertEqual(get_held(held)['state'], 'completed')
                item = receipt['items'][0]['id']
                refund_key = secrets.token_urlsafe(24)
                refund = process_refund(tx, {item:1}, 'Fictional return', True, refund_key)
                self.assertEqual(process_refund(tx, {item:1}, 'Fictional return', True, refund_key), refund)
                process_refund(tx, {item:2}, 'Fictional remaining return', True, secrets.token_urlsafe(24))
                values = dict(category='Software', description=f'Day27 expense {index}', amount='.10', expense_date=day)
                expense = create_expense(values, secrets.token_urlsafe(24))
                replacement = correct_expense(expense, dict(values, amount='.20'), 'Fictional correction', secrets.token_urlsafe(24))
                report = financial_report(resolve_filters({'preset':'today'}))
                self.assertEqual(report['sales']['summary']['revenue_cents'], 271)
                self.assertEqual(report['sales']['summary']['refunded_cents'], 271)
                self.assertEqual(report['net_revenue_cents'], 0)
                self.assertEqual(report['known_cogs_cents'], 0)
                self.assertEqual(report['expenses']['total_cents'], 20)
                self.assertEqual(report['operating_result_cents'], -20)
                self.assertEqual(report['purchases']['received_cents'], 200)
                self.assertEqual(dashboard_report({'preset':'today'})['report']['sales']['summary'], report['sales']['summary'])
                records.append((supplier, order, tx, replacement, held))
        for index, email in enumerate(('alpha','beta','gamma')):
            client = app.test_client()
            login(client, email + '@example.com')
            own = client.get('/financials/export/overview.csv?preset=today')
            self.assertEqual(own.status_code, 200)
            self.assertIn(b'-0.20', own.data)
            for other, (supplier, order, tx, expense, held) in enumerate(records):
                if index == other:
                    continue
                for path in (f'/suppliers/{supplier}', f'/purchases/{order}', f'/expenses/{expense}'):
                    self.assertEqual(client.get(path).status_code, 404, path)
                for path, location in ((f'/transactions/{tx}', '/transactions'), (f'/transactions/{tx}/receipt', '/sale')):
                    rejected = client.get(path)
                    self.assertEqual(rejected.status_code, 302)
                    self.assertEqual(rejected.headers['Location'], location)
                    self.assertNotIn(f'Product {other}', client.get(location).text)
                with self.context(index), self.assertRaises(LookupError):
                    get_sale(tx)
                with self.context(index), self.assertRaises(LookupError):
                    get_held(held)
        # Restore the complete combined ledger into a separate private target.
        backup = backup_database(Path(self.tmp.name) / 'combined.db')
        restored = restore_database(backup, Path(self.tmp.name) / 'combined-restored.db')
        with closing(sqlite3.connect(restored)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0], 3)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM refunds').fetchone()[0], 6)
            self.assertEqual([r[0] for r in conn.execute('SELECT quantity FROM products ORDER BY id')], [25,25,25])
        self.assertTrue(check_database(restored)[0])
