"""Discount allocation properties and actual discounted checkout/refund integration."""

import secrets
import unittest
from decimal import Decimal

import test_customers as fixtures

from config import business_now
from services.analytics import build_analytics, resolve_filters
from services.discounts import (
    allocate_discount,
    discount_amount,
    refund_segments,
    refund_unit_range,
)
from services.money import calculate_tax_cents, from_cents
from services.refunds import get_sale, process_refund
from services.transaction_service import complete_transaction


class DiscountCalculationTests(unittest.TestCase):
    def test_exhaustive_small_allocations_and_partial_refund_reconciliation(self):
        for price in range(1, 12):
            for total in range((price - 1) * 3 + 5):
                lines = [
                    dict(id=1, price_cents=price, quantity=3),
                    dict(id=2, price_cents=3, quantity=2),
                ]
                allocate_discount(lines, total)
                net = price * 3 + 6 - total
                tax = calculate_tax_cents(net, Decimal(".0825"))
                refund_segments(lines, net, tax)
                partials = [
                    refund_unit_range(line, start, 1)
                    for line in lines
                    for start in range(line["quantity"])
                ]
                self.assertEqual(sum(row[0] for row in partials), net)
                self.assertEqual(sum(row[1] for row in partials), tax)
                self.assertTrue(all(row[0] >= 1 for row in partials))
                self.assertEqual(sum(line["discount_cents"] for line in lines), total)

    def test_percentage_fixed_bounds_and_large_quantity(self):
        lines = [dict(id=1, price_cents=5, quantity=3)]
        self.assertEqual(discount_amount(lines, "percent", "10"), 2)
        self.assertEqual(discount_amount(lines, "fixed", ".02"), 2)
        for kind, value in [
            ("fixed", "1"),
            ("fixed", ".001"),
            ("fixed", "-1"),
            ("fixed", "NaN"),
            ("percent", "100"),
            ("percent", "101"),
            ("percent", "1.12345"),
            ("other", "1"),
        ]:
            with self.subTest(kind=kind, value=value), self.assertRaises(ValueError):
                discount_amount(lines, kind, value)
        huge = [dict(id=1, price_cents=123, quantity=1_000_000)]
        allocate_discount(huge, 1_234_567)
        net = 123_000_000 - 1_234_567
        tax = calculate_tax_cents(net, Decimal(".0825"))
        refund_segments(huge, net, tax)
        self.assertEqual(len(huge[0]["paid_segments"]), 2)
        self.assertEqual(refund_unit_range(huge[0], 0, 1_000_000), (net, tax))


class DiscountIntegrationTests(unittest.TestCase):
    setUp = fixtures.CustomerTests.setUp
    tearDown = fixtures.CustomerTests.tearDown
    context = fixtures.CustomerTests.context
    query = fixtures.CustomerTests.query
    post = fixtures.CustomerTests.post

    def test_discounted_http_and_refunds(self):
        self.client.get("/sale")
        self.post("/sale/add", {"product_id": self.products[0], "quantity": 3})
        self.assertEqual(
            self.post("/sale/discount", {"kind": "fixed", "value": ".50"}).status_code, 302
        )
        self.post("/sale/customer", {"customer_id": self.ids[0]})
        page = self.client.get("/sale")
        self.assertIn(b"$2.71", page.data)
        with self.client.session_transaction() as state:
            key = state["checkout_token"]
        self.post("/sale/checkout", {"checkout_token": key, "payment_method": "Card"})
        with self.client.session_transaction() as state:
            tx = state["last_receipt_id"]
            self.assertNotIn("sale_discount", state)
        self.assertIn(b"Before-tax discount", self.client.get(f"/transactions/{tx}/receipt").data)
        with self.context():
            state = get_sale(tx)
            self.assertTrue(state["eligible"])
            self.assertEqual(state["transaction"]["discount_cents"], 50)
            self.assertEqual(state["transaction"]["total_cents"], 271)
            for _ in range(3):
                process_refund(
                    tx,
                    {state["items"][0]["id"]: 1},
                    "Fictional discounted return",
                    True,
                    secrets.token_urlsafe(24),
                )
            self.assertEqual(get_sale(tx)["refunded_cents"], 271)
            day = business_now().date().isoformat()
            report = build_analytics(resolve_filters({"start": day, "end": day}))
            self.assertEqual(report["summary"]["item_sales_cents"], 250)
            self.assertEqual(report["summary"]["net_sales_cents"], 0)
        self.assertEqual(
            self.query("SELECT quantity FROM products WHERE id=?", (self.products[0],))[0][
                "quantity"
            ],
            20,
        )

    def test_server_total_idempotency_and_rollback(self):
        with self.context():
            args = ([{"id": self.products[0], "qty": 3}], from_cents(271), "Card")
            tx = complete_transaction(
                *args,
                discount_kind="fixed",
                discount_value=".50",
                checkout_token="discount-idempotency-test",
            )
            self.assertEqual(
                complete_transaction(
                    *args,
                    discount_kind="fixed",
                    discount_value=".50",
                    checkout_token="discount-idempotency-test",
                ),
                tx,
            )
            with self.assertRaises(ValueError):
                complete_transaction(
                    [{"id": self.products[0], "qty": 1}],
                    from_cents(108),
                    "Card",
                    discount_kind="fixed",
                    discount_value=".50",
                )
            self.assertEqual(get_sale(tx)["transaction"]["total_cents"], 271)
        self.assertEqual(len(self.query("SELECT * FROM transactions")), 1)
        self.assertEqual(
            self.client.post("/sale/discount", data={"kind": "fixed", "value": "1"}).status_code,
            400,
        )
