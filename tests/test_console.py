"""Day 26B presentation gates against actual authorized Flask/SQLite services."""
import unittest
from unittest.mock import patch

import test_customers as fixtures
from test_ui import Document

from services.analytics import resolve_filters
from services.financials import financial_report


class ConsoleTests(unittest.TestCase):
    setUp = fixtures.CustomerTests.setUp
    tearDown = fixtures.CustomerTests.tearDown
    context = fixtures.CustomerTests.context
    post = fixtures.CustomerTests.post
    sale = fixtures.CustomerTests.sale

    def test_dashboard_reconciles_reports_and_zero_baseline(self):
        self.sale()
        with self.context():
            expected = financial_report(resolve_filters({'preset': 'today'}))
        with patch('web_app.render_template', return_value='ok') as render:
            self.assertEqual(self.client.get('/?preset=today').status_code, 200)
        data = render.call_args.kwargs
        actual = data['report']
        self.assertEqual(actual['sales']['summary'], expected['sales']['summary'])
        for key in ('gross_profit_cents', 'net_revenue_cents', 'net_tax_cents', 'operating_result_cents'):
            self.assertEqual(actual[key], expected[key])
        self.assertFalse(actual['sales']['comparison']['has_baseline'])
        self.assertIsNone(actual['gross_profit_cents'])
        self.assertEqual(sum(row['revenue_cents'] for row in actual['sales']['series']), 217)
        self.assertIn('Unavailable', self.client.get('/?preset=today').text)

    def test_dashboard_periods_errors_and_empty_state(self):
        for preset in ('today', 'last7', 'last30', 'this_month'):
            response = self.client.get('/?preset=' + preset)
            self.assertEqual(response.status_code, 200)
            self.assertIn('No completed sales in this period', response.text)
            self.assertNotIn('<polyline', response.text)
        for query in ('preset=invalid', 'preset=custom&start_date=no', 'store_id=1',
                      'preset=custom&start_date=2020-01-01&end_date=2025-01-01'):
            self.assertEqual(self.client.get('/?' + query).status_code, 400)

    def test_three_store_dashboard_isolation_with_different_sales(self):
        # Existing fixture supplies three independent identities and owned products.
        for index in range(3):
            for _ in range(index + 1):
                self.sale(index)
        for index in range(3):
            with self.context(index):
                report = financial_report(resolve_filters({'preset': 'today'}))
                self.assertEqual(report['sales']['summary']['transaction_count'], index + 1)
                self.assertEqual(report['sales']['summary']['revenue_cents'], 217 * (index + 1))
                self.assertEqual({p['product_id'] for p in report['sales']['products']}, {self.products[index]})

    def test_compact_checkout_preserves_authorized_forms_and_semantics(self):
        self.client.get('/sale')
        self.post('/sale/add', {'product_id': self.products[0], 'quantity': 1})
        response = self.client.get('/sale')
        doc = Document(response.text)
        self.assertEqual(doc.errors, [])
        self.assertEqual(doc.stack, [])
        self.assertIn('workspace-pos', response.text)
        self.assertIn('Charge $1.08', response.text)
        for label in ('Guest customer', 'Order notes', 'Discount', 'Simulated payment'):
            self.assertIn(label, response.text)
        contexts = [n for n in doc.find('details') if 'cart-context' in n.attrs.get('class', '')]
        self.assertTrue(contexts)
        self.assertTrue(all('open' not in n.attrs for n in contexts))
        self.assertTrue(any(n.attrs.get('class') == 'payment-panel' for n in doc.find('details')))
        tile_forms = [n for n in doc.find('form') if 'tile-form' in n.attrs.get('class', '')]
        self.assertEqual(len(tile_forms), 1)
        self.assertFalse(any(n.attrs.get('type') == 'number' and self.belongs(n, tile_forms[0]) for n in doc.find('input')))
        self.assertIn('Add one Product 0', response.text)
        self.assertNotIn('name="card_number"', response.text)

    def test_workstation_components_and_scroll_regions(self):
        self.client.get('/sale')
        self.post('/sale/add', {'product_id': self.products[0], 'quantity': 1})
        doc = Document(self.client.get('/sale').text)
        self.assertEqual(doc.errors, [])
        categories = [n for n in doc.find('a') if 'pos-category-item' in n.attrs.get('class', '')]
        self.assertTrue(categories)
        self.assertTrue(all(any(svg.parent is n for svg in doc.find('svg')) for n in categories))
        self.assertTrue(any('cart-frame' in n.attrs.get('class', '') for n in doc.find('div')))
        dock = next(n for n in doc.find('div') if n.attrs.get('class') == 'checkout-dock')
        self.assertFalse(dock.inside('div', 'cart-scroll-region'))
        quantities = [n for n in doc.find('summary') if 'Choose quantity for' in n.attrs.get('aria-label', '')]
        self.assertEqual(len(quantities), 1)
        self.assertFalse(any(n.tag=='select' and n.attrs.get('id')=='sale-category' for n in doc.nodes))

    @staticmethod
    def belongs(node, parent):
        while node.parent:
            node = node.parent
            if node is parent:
                return True
        return False

    def test_business_modules_share_tables_headers_and_working_links(self):
        for path in ('/customers', '/suppliers', '/purchases', '/expenses', '/financials',
                     '/notifications', '/settings', '/pos/designer', '/pos/held'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                doc = Document(response.text)
                self.assertEqual(doc.errors, [])
                for table in doc.find('table'):
                    self.assertIn('data-table', table.attrs.get('class', ''))
                self.assertIn('console.css', response.text)
                for link in doc.find('a'):
                    href = link.attrs.get('href', '')
                    if href.startswith('/'):
                        self.assertEqual(self.client.get(href).status_code, 200, href)
