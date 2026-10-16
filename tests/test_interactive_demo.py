"""Security and exact-money gates for the isolated, nonpersistent simulation."""
import copy
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_customers as fixtures

import demo_routes
from database import database as db
from services.interactive_demo import (
    CATALOG,
    MAX_BUCKETS,
    MAX_SESSIONS,
    DemoCapacity,
    DemoMemory,
    DemoRateLimit,
    complete,
    empty_state,
    quote,
)
from web_app import app


class InteractiveDemoTests(unittest.TestCase):
    setUp_fixture = fixtures.CustomerTests.setUp
    tearDown = fixtures.CustomerTests.tearDown
    context = fixtures.CustomerTests.context

    def setUp(self):
        self.setUp_fixture()
        self.original_memory = demo_routes.memory
        demo_routes.memory = DemoMemory()
        self.addCleanup(setattr, demo_routes, 'memory', self.original_memory)
        app.config.update(DEMO_READ_ONLY=True, REGISTRATION_ENABLED=False)
        self.a, self.b = app.test_client(), app.test_client()

    def state(self, client):
        with client.session_transaction() as values:
            token = values['interactive_demo_token']
        return demo_routes.memory.states[token]

    def post(self, path, values=None, client=None):
        client = client or self.a
        client.get('/demo/pos')
        with client.session_transaction() as state:
            token = state['csrf_token']
        return client.post('/demo/' + path, data=dict(values or {}, csrf_token=token))

    def add(self, product='notebook', quantity='1', client=None):
        return self.post('cart', dict(product_id=product, quantity=quantity, action='add'), client)

    def checkout(self, method='Card', cash='', client=None, nonce=None):
        client = client or self.a
        return self.post('checkout', dict(payment_method=method, cash_received=cash,
                         checkout_token=nonce or self.state(client)['nonce']), client)

    def snapshot(self):
        with sqlite3.connect(db.DB_FILE) as conn:
            return '\n'.join(conn.iterdump())

    def test_anonymous_catalog_search_categories_and_session_isolation(self):
        response = self.a.get('/demo/pos')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Interactive demo — All payments', response.text)
        self.assertNotIn('alpha@example.com', response.text)
        self.assertIn('Demo Notebook', self.a.get('/demo/pos?q=notebook').text)
        self.assertNotIn('Demo Notebook', self.a.get('/demo/pos?category=Lighting').text)
        self.assertEqual(self.a.get('/demo/pos?category=private').status_code, 400)
        self.assertEqual(self.add().status_code, 303)
        self.assertEqual(self.b.get('/demo/pos').status_code, 200)
        self.assertEqual(self.state(self.a)['cart'], {'notebook': 1})
        self.assertEqual(self.state(self.b)['cart'], {})
        self.assertEqual(self.b.get('/demo/receipt').status_code, 404)

    def test_discount_cash_rounding_receipt_and_duplicate_checkout(self):
        self.add(quantity='2')
        self.assertEqual(self.post('discount', dict(kind='percent', value='10')).status_code, 303)
        expected = quote(self.state(self.a)['cart'], self.state(self.a)['discount'])
        self.assertEqual([expected[key] for key in ('subtotal_cents','discount_cents','tax_cents','total_cents')], [850,85,63,828])
        preview = self.post('cash-preview', dict(cash_received='10.00'))
        self.assertEqual(preview.json, {'total':'$8.28','change':'$1.72'})
        nonce = self.state(self.a)['nonce']
        self.assertEqual(self.checkout('Cash', '8.27', nonce=nonce).status_code, 400)
        self.assertEqual(self.checkout('Cash', '10.00', nonce=nonce).status_code, 303)
        first = copy.deepcopy(self.state(self.a)['receipt'])
        self.assertEqual(self.checkout('Cash', '10.00', nonce=nonce).status_code, 303)
        self.assertEqual(self.state(self.a)['receipt'], first)
        self.assertEqual(first['change_cents'], 172)
        self.assertEqual(self.checkout('Card', nonce=nonce).status_code, 400)
        response = self.a.get('/demo/receipt')
        self.assertIn(first['identifier'], response.text)
        self.assertIn('data-print', response.text)
        self.assertIn('Not proof of purchase', response.text)
        self.assertEqual(self.b.get('/demo/receipt').status_code, 404)
        self.assertEqual(self.b.get('/demo/receipt?id=' + first['identifier']).status_code, 400)
        self.assertEqual(self.state(self.a)['cart'], {})

    def test_mutations_limits_stock_and_forged_fields(self):
        for values in (
            dict(product_id='1', action='add', quantity='1'),
            dict(product_id='keyboard', action='add', quantity='1'),
            dict(product_id='notebook', action='add', quantity='-1'),
            dict(product_id='notebook', action='add', quantity='1.5'),
            dict(product_id='notebook', action='add', quantity='21'),
            dict(product_id='notebook', action='add', quantity='1', price_cents='1'),
            dict(product_id='notebook', action='add', quantity='1', store_id='1'),
        ):
            with self.subTest(values=values):
                self.assertEqual(self.post('cart', values).status_code, 400)
                self.assertEqual(self.state(self.a)['cart'], {})
        self.add()
        self.post('cart', dict(product_id='notebook', action='plus'))
        self.assertEqual(self.state(self.a)['cart']['notebook'], 2)
        self.post('cart', dict(product_id='notebook', action='minus'))
        self.post('cart', dict(product_id='notebook', action='set', quantity='5'))
        self.assertEqual(self.state(self.a)['cart']['notebook'], 5)
        self.post('cart', dict(product_id='notebook', action='remove'))
        self.assertEqual(self.state(self.a)['cart'], {})
        for product in CATALOG[:6]:
            self.assertEqual(self.add(product['id']).status_code, 303)
        self.assertEqual(self.add('lamp').status_code, 400)
        self.post('clear')
        self.assertEqual(self.add('stand', '20').status_code, 303)
        self.assertEqual(self.add('case', '10').status_code, 303)
        self.assertEqual(self.add('planner', '12').status_code, 303)
        stale = self.state(self.a)['nonce']
        self.assertEqual(self.add('cable', '20').status_code, 303)
        self.assertEqual(self.add('notebook', '20').status_code, 400)
        self.assertEqual(self.checkout(nonce=stale).status_code, 400)
        self.assertEqual(self.post('checkout', dict(checkout_token=self.state(self.a)['nonce'], payment_method='Card',total_cents='1')).status_code, 400)
        self.post('clear')
        self.add(quantity='3')
        self.assertEqual(self.post('discount', dict(kind='fixed', value='1.25')).status_code, 303)
        self.assertEqual(quote(self.state(self.a)['cart'], self.state(self.a)['discount'])['total_cents'], 1245)

    def test_business_database_unchanged_and_private_context_retained(self):
        before = self.snapshot()
        # Even an owner can use the demo without reading or clearing their identity.
        with self.client.session_transaction() as values:
            login_token = values['login_token']
        with patch('database.database.get_db_connection', side_effect=AssertionError('Demo touched SQLite')), patch('web_app.load_identity', side_effect=AssertionError('Demo loaded private identity')):
            self.assertEqual(self.client.get('/demo/pos').status_code, 200)
            self.assertEqual(self.add(client=self.client).status_code, 303)
            self.assertEqual(self.checkout(client=self.client).status_code, 303)
            self.assertEqual(self.client.get('/demo/receipt').status_code, 200)
            self.assertEqual(self.post('reset', client=self.client).status_code, 303)
        with self.client.session_transaction() as values:
            self.assertEqual(values['login_token'], login_token)
        self.assertEqual(self.snapshot(), before)
        for path in ('/inventory','/transactions','/customers','/suppliers','/reports'):
            self.assertEqual(self.a.get(path).status_code, 302, path)
        for path in ('/sale/add','/sale/checkout','/customers/new','/suppliers/new','/products/new','/inventory/1/adjust'):
            self.assertIn(self.a.post(path, data={}).status_code, (400,403,404), path)
        self.assertEqual(self.snapshot(), before)

    def test_csrf_payload_methods_and_no_card_collection(self):
        self.a.get('/demo/pos')
        for path in ('cart','checkout','discount','clear','reset','cash-preview'):
            self.assertEqual(self.a.post('/demo/' + path, data={}).status_code, 400)
            self.assertEqual(self.a.get('/demo/' + path).status_code, 405)
        self.assertEqual(self.a.post('/demo/cart', json={}).status_code, 415)
        self.assertEqual(self.a.post('/demo/cart', data={'x':'a'*70000}).status_code, 413)
        with self.a.session_transaction() as values:
            csrf = values['csrf_token']
        self.assertEqual(self.a.post('/demo/cart', data='csrf_token='+csrf+'&quantity=1&quantity=2', content_type='application/x-www-form-urlencoded').status_code, 400)
        self.add()
        self.assertEqual(self.post('checkout', dict(payment_method='Card', checkout_token=self.state(self.a)['nonce'], card_number='4111111111111111')).status_code, 400)
        self.assertEqual(self.post('checkout', dict(payment_method='Card', checkout_token='é')).status_code, 400)
        self.assertEqual(self.post('discount', dict(kind='percent', value='101')).status_code, 400)
        self.assertEqual(self.post('discount', dict(kind='fixed', value='0.001')).status_code, 400)
        self.assertEqual(self.post('cash-preview', dict(cash_received='NaN')).status_code, 400)
        self.assertNotIn('name="card_number"', self.a.get('/demo/pos').text)

    def test_expiration_restart_reset_and_checkout_rate_limit(self):
        now = [100.0]
        demo_routes.memory = DemoMemory(lambda: now[0])
        self.add()
        nonce = self.state(self.a)['nonce']
        self.checkout(nonce=nonce)
        self.post('reset')
        self.assertEqual(self.a.get('/demo/receipt').status_code, 404)
        self.assertEqual(self.checkout(nonce=nonce).status_code, 400)
        self.add()
        now[0] += 1801
        self.a.get('/demo/pos')
        self.assertEqual(self.state(self.a)['cart'], {})
        self.assertEqual(self.a.get('/demo/receipt').status_code, 404)
        self.add()
        demo_routes.memory = DemoMemory(lambda: now[0])
        self.assertEqual(self.a.get('/demo/pos').status_code, 200)
        self.assertEqual(self.state(self.a)['cart'], {})
        self.add()
        nonce = self.state(self.a)['nonce']
        self.assertEqual(self.checkout(nonce=nonce).status_code, 303)
        for _ in range(9):
            self.assertEqual(self.checkout(nonce=nonce).status_code, 303)
        self.assertEqual(self.checkout(nonce=nonce).status_code, 429)
        self.post('reset')
        self.assertEqual(self.checkout(nonce=nonce).status_code, 429)

    def test_concurrent_duplicate_completion_and_half_cent_rounding(self):
        self.assertEqual(quote({'notebook':8}, dict(kind='none',value=''))['tax_cents'], 281)
        state = empty_state(0)
        state['cart'] = {'pen':1}
        state['discount'] = dict(kind='percent', value='10')
        self.assertEqual(quote(state['cart'], state['discount'])['discount_cents'], 13)
        store = DemoMemory()
        with store.visitor(None, '127.0.0.1', 'test-secret') as (token, state):
            state['cart'] = {'notebook':1}
            nonce = state['nonce']
        def commit(_):
            with store.visitor(token, '127.0.0.1', 'test-secret', checkout=True) as (_, state):
                return complete(state, nonce, 'Card', '', 'Fictional test date')['identifier']
        with ThreadPoolExecutor(max_workers=2) as pool:
            identifiers = list(pool.map(commit, range(2)))
        self.assertEqual(len(set(identifiers)), 1)
        self.assertEqual(store.states[token]['cart'], {})

    def test_resource_capacity_and_request_limits_fail_closed(self):
        store = DemoMemory(lambda:100)
        store.states = {str(i):empty_state(100) for i in range(MAX_SESSIONS)}
        with self.assertRaises(DemoCapacity), store.visitor(None,'a','secret'):
            pass
        store = DemoMemory(lambda:100)
        store.buckets = {str(i):[0,200] for i in range(MAX_BUCKETS)}
        with self.assertRaises(DemoCapacity), store.visitor(None,'a','secret'):
            pass
        store = DemoMemory(lambda:100)
        for _ in range(20):
            with store.visitor(None,'a','secret'):
                pass
        with self.assertRaises(DemoRateLimit), store.visitor(None,'a','secret'):
            pass
        store = DemoMemory(lambda:100)
        with store.visitor(None,'a','secret') as (token, _):
            pass
        for _ in range(299):
            with store.visitor(token,'a','secret'):
                pass
        with self.assertRaises(DemoRateLimit), store.visitor(token,'a','secret'):
            pass
