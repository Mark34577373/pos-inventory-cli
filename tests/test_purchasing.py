"""Stage B: real receiving transactions, durable retries and three-store isolation."""

import secrets
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import test_customers as fixtures

from services.inventory import deactivate_product
from services.purchasing import (
    create_order,
    deactivate_supplier,
    edit_order,
    get_order,
    get_supplier,
    purchase_report,
    receive_order,
    save_order_line,
    save_product_cost,
    save_supplier,
    supplier_profile,
    suppliers,
    transition_order,
)
from services.refunds import get_sale
from web_app import app


class PurchasingTests(unittest.TestCase):
    setUp = fixtures.CustomerTests.setUp
    tearDown = fixtures.CustomerTests.tearDown
    context = fixtures.CustomerTests.context
    query = fixtures.CustomerTests.query
    post = fixtures.CustomerTests.post
    sale = fixtures.CustomerTests.sale

    def order(self, index=0):
        with self.context(index):
            supplier = save_supplier({"name": f"Supplier {index}"})
            order = create_order(supplier, "2026-12-15", "Fictional delivery")
            save_order_line(order, self.products[index], 5, "1.50")
            line = get_order(order)["lines"][0]["id"]
            return supplier, order, line

    def test_supplier_crud_search_pagination_and_validation(self):
        with self.context():
            supplier = save_supplier(
                {
                    "name": "<Supplier>",
                    "email": "supplier@example.com",
                    "address": "Fictional address",
                }
            )
            save_supplier({"name": "Renamed", "contact": "Person"}, supplier)
            self.assertEqual(get_supplier(supplier)["name"], "Renamed")
            self.assertEqual(suppliers({"q": "Person"})["count"], 1)
            for number in range(22):
                save_supplier({"name": f"Paged {number:02}"})
            self.assertEqual(len(suppliers({"q": "Paged", "page": "2"})["rows"]), 2)
            for values in (
                {"name": ""},
                {"name": "x", "email": "bad"},
                {"name": "x", "store_id": 99},
                {"name": "x", "address": "x" * 501},
            ):
                with self.assertRaises(ValueError):
                    save_supplier(values)
            deactivate_supplier(supplier)
            self.assertFalse(get_supplier(supplier)["is_active"])
            with self.assertRaises(LookupError):
                create_order(supplier)

    def test_draft_edit_transitions_and_no_stock_until_receiving(self):
        supplier, order, line = self.order()
        with self.context():
            edit_order(order, "2026-12-16", "Updated")
            save_order_line(order, self.products[0], 4, "1.25")
            self.assertEqual(get_order(order)["total_cents"], 500)
            self.assertEqual(purchase_report()["ordered_cents"], 0)
            transition_order(order, "order")
            self.assertEqual(purchase_report()["ordered_cents"], 500)
            for action in (
                lambda: edit_order(order, "", "bad"),
                lambda: save_order_line(order, self.products[0], 1, "1"),
                lambda: transition_order(order, "order"),
            ):
                with self.assertRaises(ValueError):
                    action()
            self.assertEqual(get_supplier(supplier)["name"], "Supplier 0")
            self.assertEqual(get_order(order)["order"]["status"], "ordered")
        self.assertEqual(
            self.query("SELECT quantity FROM products WHERE id=?", (self.products[0],))[0][
                "quantity"
            ],
            20,
        )

    def test_partial_full_receiving_idempotency_and_audit(self):
        supplier, order, line = self.order()
        with self.context():
            transition_order(order, "order")
            key = secrets.token_urlsafe(24)
            receipt = receive_order(order, {line: 2}, key)
            self.assertEqual(receive_order(order, {line: 2}, key), receipt)
            self.assertEqual(get_order(order)["order"]["status"], "partially_received")
            with self.assertRaises(ValueError):
                receive_order(order, {line: 3}, key)
            with self.assertRaises(ValueError):
                receive_order(order, {line: 4}, secrets.token_urlsafe(24))
            with self.assertRaises(ValueError):
                transition_order(order, "cancel")
            receive_order(order, {line: 3}, secrets.token_urlsafe(24))
            self.assertEqual(get_order(order)["order"]["status"], "received")
            self.assertEqual(get_order(order)["received_cents"], 750)
            self.assertEqual(purchase_report()["received_cents"], 750)
            self.assertEqual(supplier_profile(supplier)["totals"]["received_cents"], 750)
            save_supplier({"name": "Changed later"}, supplier)
            self.assertEqual(get_order(order)["order"]["supplier_name"], "Supplier 0")
        self.assertEqual(
            self.query("SELECT quantity FROM products WHERE id=?", (self.products[0],))[0][
                "quantity"
            ],
            25,
        )
        audit = self.query(
            "SELECT purchase_receipt_id,quantity_change FROM stock_adjustments WHERE purchase_receipt_id IS NOT NULL ORDER BY id"
        )
        self.assertEqual([row["quantity_change"] for row in audit], [2, 3])
        self.assertEqual(len(self.query("SELECT * FROM purchase_receipts")), 2)
        self.assertEqual(self.query("PRAGMA foreign_key_check"), [])

    def test_receiving_rollback_inactive_and_stock_capacity(self):
        _, order, line = self.order()
        with self.context():
            save_order_line(order, self.products[0], 5, "1.50")
            transition_order(order, "order")
            deactivate_product(self.products[0])
            with self.assertRaises(ValueError):
                receive_order(order, {line: 2}, secrets.token_urlsafe(24))
            self.assertEqual(get_order(order)["received_cents"], 0)
        self.assertEqual(self.query("SELECT * FROM purchase_receipts"), [])
        self.assertEqual(self.query("SELECT * FROM purchase_receipt_items"), [])
        self.assertEqual(
            self.query("SELECT * FROM stock_adjustments WHERE purchase_receipt_id IS NOT NULL"), []
        )

    def test_simultaneous_same_receipt_commits_once(self):
        _, order, line = self.order()
        with self.context():
            transition_order(order, "order")
        key = secrets.token_urlsafe(24)

        def worker():
            with self.context():
                return receive_order(order, {line: 5}, key)

        with ThreadPoolExecutor(max_workers=2) as executor:
            ids = list(executor.map(lambda _: worker(), range(2)))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(len(self.query("SELECT * FROM purchase_receipts")), 1)
        self.assertEqual(
            self.query("SELECT quantity FROM products WHERE id=?", (self.products[0],))[0][
                "quantity"
            ],
            25,
        )

    def test_three_account_isolation_routes_services_and_sql(self):
        owned = [self.order(index) for index in range(3)]
        before = self.query("SELECT * FROM purchase_orders")
        for supplier, order, line in owned[1:]:
            with self.context():
                for action in (
                    lambda: get_supplier(supplier),
                    lambda: get_order(order),
                    lambda: edit_order(order, "", "attack"),
                    lambda: transition_order(order, "cancel"),
                    lambda: deactivate_supplier(supplier),
                    lambda: save_product_cost(self.products[0], ".50", supplier),
                    lambda: create_order(supplier),
                    lambda: receive_order(order, {line: 1}, secrets.token_urlsafe(24)),
                ):
                    with self.assertRaises(LookupError):
                        action()
            for path in (
                f"/suppliers/{supplier}",
                f"/suppliers/{supplier}/edit",
                f"/purchases/{order}",
                f"/inventory/{self.products[1]}/cost",
            ):
                self.assertEqual(self.client.get(path).status_code, 404)
            self.assertEqual(
                self.post(f"/purchases/{order}/action", {"action": "cancel"}).status_code, 404
            )
            self.assertEqual(
                self.post(
                    f"/purchases/{owned[0][1]}/line",
                    {"product_id": self.products[1], "quantity": 1, "unit_cost": "1"},
                ).status_code,
                404,
            )
        self.assertEqual(before, self.query("SELECT * FROM purchase_orders"))
        self.assertEqual(self.query("SELECT * FROM purchase_receipts"), [])
        with self.context():
            self.assertEqual(len(purchase_report()["orders"]), 1)
        self.assertNotIn(b"Supplier 1", self.client.get("/purchases").data)
        self.assertNotIn(b"Supplier 1", self.client.get("/purchases/export.csv").data)
        with closing(db_connection()) as conn, conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE products SET supplier_id=? WHERE id=?", (owned[1][0], self.products[0])
                )

    def test_cost_snapshots_unknown_zero_and_no_retail_price_changes(self):
        original = self.sale()
        supplier, order, line = self.order()
        with self.context():
            save_product_cost(self.products[0], ".75", supplier, "SUP-123")
        future = self.sale()
        with self.context():
            save_product_cost(self.products[0], "0", supplier)
            self.assertIsNone(get_sale(original)["items"][0]["unit_cost_cents"])
            self.assertEqual(get_sale(future)["items"][0]["unit_cost_cents"], 75)
            transition_order(order, "order")
            receive_order(order, {line: 1}, secrets.token_urlsafe(24))
        row = self.query(
            "SELECT price_cents,unit_cost_cents FROM products WHERE id=?", (self.products[0],)
        )[0]
        self.assertEqual(row, {"price_cents": 100, "unit_cost_cents": 0})

    def test_http_pages_csrf_mass_assignment_and_read_only(self):
        supplier, order, line = self.order()
        for path in (
            "/suppliers",
            "/suppliers/new",
            f"/suppliers/{supplier}",
            f"/suppliers/{supplier}/edit",
            "/purchases",
            "/purchases/new",
            f"/purchases/{order}",
            f"/inventory/{self.products[0]}/cost",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, (path, response.data[:300]))
            self.assertEqual(app.test_client().get(path).status_code, 302)
        self.assertEqual(
            self.client.post("/purchases/new", data={"supplier_id": supplier}).status_code, 400
        )
        self.assertEqual(
            self.post(
                "/purchases/new", {"supplier_id": supplier, "store_id": self.accounts[1][1]}
            ).status_code,
            400,
        )
        app.config["DEMO_READ_ONLY"] = True
        self.assertEqual(
            self.post(f"/purchases/{order}/action", {"action": "order"}).status_code, 403
        )
        self.assertNotIn(b"Confirm receiving", self.client.get(f"/purchases/{order}").data)
        with self.context(), self.assertRaises(PermissionError):
            receive_order(order, {line: 1}, secrets.token_urlsafe(24))

    def test_receiving_alerts_and_multi_line_rollback(self):
        from services.inventory import create_product
        from services.inventory_management import adjust_stock

        _, order, line = self.order()
        with self.context():
            adjust_stock(
                self.products[0],
                "0",
                "correction",
                "Fictional baseline correction",
                expected_quantity="20",
            )
            self.assertEqual(
                self.query(
                    "SELECT kind FROM inventory_alerts WHERE product_id=? AND resolved_at IS NULL",
                    (self.products[0],),
                )[0]["kind"],
                "out",
            )
            second = create_product("Second receiving product", "2", 1_000_000)
            save_order_line(order, second, 2, "1")
            transition_order(order, "order")
            state = get_order(order)
            second_line = next(row["id"] for row in state["lines"] if row["product_id"] == second)
            with self.assertRaises(ValueError):
                receive_order(order, {line: 2, second_line: 1}, secrets.token_urlsafe(24))
            self.assertEqual(get_order(order)["received_cents"], 0)
            self.assertEqual(
                self.query("SELECT quantity FROM products WHERE id=?", (self.products[0],))[0][
                    "quantity"
                ],
                0,
            )
            self.assertEqual(self.query("SELECT * FROM purchase_receipts"), [])
            receive_order(order, {line: 2}, secrets.token_urlsafe(24))
            self.assertEqual(
                self.query(
                    "SELECT kind FROM inventory_alerts WHERE product_id=? AND resolved_at IS NULL",
                    (self.products[0],),
                )[0]["kind"],
                "low",
            )

    def test_inactive_draft_line_policy(self):
        _, order, _ = self.order()
        with self.context():
            deactivate_product(self.products[0])
            with self.assertRaises(ValueError):
                transition_order(order, "order")
            save_order_line(order, self.products[0], 1, "1", remove=True)
            self.assertEqual(get_order(order)["lines"], [])


def db_connection():
    from database.database import get_db_connection

    return get_db_connection()
