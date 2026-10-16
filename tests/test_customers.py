"""Real SQLite and HTTP customer tests with three fictional store owners."""

import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path

from auth_helpers import PASSWORD, login
from flask import g

from database import database as db
from services.auth import register_user
from services.customers import (
    customer_history,
    deactivate_customer,
    directory_filters,
    get_customer,
    list_customers,
    save_customer,
)
from services.inventory import create_product
from services.money import from_cents
from services.refunds import get_sale, process_refund
from services.transaction_service import complete_transaction
from web_app import app


class CustomerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous_db, self.previous_config = db.DB_FILE, app.config.copy()
        db.DB_FILE = str(Path(self.tmp.name) / "customers.db")
        db.initialize_database(seed=False)
        app.config.update(TESTING=True, DEMO_READ_ONLY=False)
        self.accounts = [
            register_user(f"{name}@example.com", PASSWORD, PASSWORD, f"Store {name}")
            for name in ("alpha", "beta", "gamma")
        ]
        self.ids = []
        self.products = []
        for index in range(3):
            with self.context(index):
                self.ids.append(
                    save_customer(
                        {"name": f"Customer {index}", "email": f"person{index}@example.com"}
                    )
                )
                self.products.append(create_product(f"Product {index}", "1.00", 20))
        self.client = app.test_client()
        login(self.client, "alpha@example.com")

    def tearDown(self):
        db.DB_FILE = self.previous_db
        app.config.clear()
        app.config.update(self.previous_config)
        self.tmp.cleanup()

    @contextmanager
    def context(self, index=0):
        with app.test_request_context("/"):
            user, store = self.accounts[index]
            g.user, g.store, g.is_demo = {"id": user}, {"id": store}, False
            yield

    def query(self, sql, params=()):
        with closing(db.get_db_connection()) as conn:
            return [dict(row) for row in conn.execute(sql, params)]

    def post(self, path, values=None):
        with self.client.session_transaction() as state:
            token = state["csrf_token"]
        return self.client.post(path, data=dict(values or {}, csrf_token=token))

    def sale(self, index=0, customer=True):
        with self.context(index):
            return complete_transaction(
                [{"id": self.products[index], "qty": 2}],
                from_cents(217),
                "Card",
                customer_id=self.ids[index] if customer else None,
            )

    def test_crud_validation_duplicate_contacts_and_pagination(self):
        with self.context():
            identifier = save_customer({"name": "<Fictional>", "phone": "555-0100"})
            duplicate = save_customer({"name": "Another", "phone": "555-0100"})
            self.assertNotEqual(identifier, duplicate)
            save_customer({"name": "Updated", "notes": "Optional\nnotes"}, identifier)
            self.assertEqual(get_customer(identifier)["name"], "Updated")
            for invalid in (
                {},
                {"name": "x" * 101},
                {"name": "x", "email": "bad"},
                {"name": "x", "store_id": self.accounts[1][1]},
                {"name": "\x00"},
            ):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    save_customer(invalid)
            for number in range(23):
                save_customer({"name": f"Paged {number:02}"})
            data = list_customers({"q": "Paged", "page": "2"})
            self.assertEqual((data["count"], len(data["customers"])), (23, 3))
            self.assertEqual(list_customers({"q": "555-0100"})["count"], 1)
            deactivate_customer(identifier)
            self.assertFalse(get_customer(identifier)["is_active"])
            with self.assertRaises(LookupError):
                get_customer(identifier, active=True)
            self.assertEqual(list_customers({"state": "inactive"})["count"], 1)
            for bad in ({"page": "-1"}, {"sort": "DROP TABLE"}, {"state": "invalid"}):
                with self.assertRaises(ValueError):
                    directory_filters(bad)

    def test_customer_snapshot_guest_and_refund_history(self):
        tx = self.sale()
        guest = self.sale(customer=False)
        with self.context():
            save_customer({"name": "Renamed"}, self.ids[0])
            state = get_sale(tx)
            self.assertEqual(state["transaction"]["customer_name_snapshot"], "Customer 0")
            self.assertIsNone(get_sale(guest)["transaction"]["customer_id"])
            process_refund(
                tx,
                {state["items"][0]["id"]: 1},
                "Fictional return",
                False,
                "customer-test-return-key-123",
            )
            history = customer_history(self.ids[0])
            self.assertEqual((history["transaction_count"], history["total_cents"]), (1, 217))
            self.assertIn(history["refunded_cents"], (108, 109))
            deactivate_customer(self.ids[0])
            self.assertEqual(customer_history(self.ids[0])["transaction_count"], 1)
        self.assertIn(b"Customer 0", self.client.get(f"/transactions/{tx}").data)
        receipt = self.client.get(f"/transactions/{tx}/receipt").data
        self.assertNotIn(b"person0@example.com", receipt)

    def test_three_account_service_route_and_database_isolation(self):
        before = self.query("SELECT * FROM customers")
        with self.context():
            for identifier in self.ids[1:]:
                for action in (
                    lambda: get_customer(identifier),
                    lambda: customer_history(identifier),
                    lambda: save_customer({"name": "Intruder"}, identifier),
                    lambda: deactivate_customer(identifier),
                ):
                    with self.assertRaises(LookupError):
                        action()
                for path in (f"/customers/{identifier}", f"/customers/{identifier}/edit"):
                    self.assertEqual(self.client.get(path).status_code, 404)
                self.assertEqual(self.post(f"/customers/{identifier}/deactivate").status_code, 404)
                self.assertEqual(
                    self.post("/sale/customer", {"customer_id": identifier}).status_code, 404
                )
            self.assertEqual(list_customers({})["count"], 1)
            for identifier in self.ids[1:]:
                with self.assertRaises(LookupError):
                    complete_transaction(
                        [{"id": self.products[0], "qty": 1}],
                        from_cents(108),
                        "Card",
                        customer_id=identifier,
                    )
        self.assertEqual(before, self.query("SELECT * FROM customers"))
        self.assertEqual(self.query("SELECT * FROM transactions"), [])
        with closing(db.get_db_connection()) as conn, conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """INSERT INTO transactions(store_id,timestamp,total,total_cents,payment_method,customer_id)
                    VALUES(?, '2026-01-01', 1,100,'Card',?)""",
                    (self.accounts[0][1], self.ids[1]),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM customers WHERE id=?", (self.ids[0],))
        for name in ("beta", "gamma"):
            login(self.client, f"{name}@example.com")
            self.assertNotIn(b"Customer 0", self.client.get("/customers").data)

    def test_http_checkout_selection_expiration_and_csrf(self):
        self.client.get("/sale")
        self.assertEqual(self.post("/sale/customer", {"customer_id": self.ids[0]}).status_code, 302)
        self.post("/sale/add", {"product_id": self.products[0], "quantity": 2})
        self.assertIn(b"Customer 0", self.client.get("/sale").data)
        with self.client.session_transaction() as state:
            key = state["checkout_token"]
        self.assertEqual(
            self.post(
                "/sale/checkout", {"checkout_token": key, "payment_method": "Card"}
            ).status_code,
            302,
        )
        self.assertEqual(
            self.query("SELECT customer_id FROM transactions"), [{"customer_id": self.ids[0]}]
        )
        with self.client.session_transaction() as state:
            self.assertNotIn("sale_customer_id", state)
        self.assertEqual(
            self.client.post("/customers/new", data={"name": "No CSRF"}).status_code, 400
        )
        self.assertEqual(
            self.post(
                "/customers/new", {"name": "Intruder", "store_id": self.accounts[1][1]}
            ).status_code,
            400,
        )
        self.post("/sale/customer", {"customer_id": self.ids[0]})
        with self.context():
            deactivate_customer(self.ids[0])
        self.post("/sale/add", {"product_id": self.products[0], "quantity": 1})
        self.assertIn(b"Selected customer is no longer available", self.client.get("/sale").data)
        login(self.client, "beta@example.com")
        with self.client.session_transaction() as state:
            self.assertNotIn("sale_customer_id", state)
            self.assertNotIn("sale_cart", state)

    def test_read_only_anonymous_and_pages(self):
        for path in (
            "/customers",
            "/customers/new",
            f"/customers/{self.ids[0]}",
            f"/customers/{self.ids[0]}/edit",
            "/sale",
        ):
            self.assertEqual(self.client.get(path).status_code, 200)
            self.assertEqual(app.test_client().get(path).status_code, 302)
        app.config["DEMO_READ_ONLY"] = True
        self.assertEqual(self.post("/customers/new", {"name": "Blocked"}).status_code, 403)
        self.assertEqual(self.post("/sale/customer", {"customer_id": self.ids[0]}).status_code, 403)
        with self.context(), self.assertRaises(PermissionError):
            save_customer({"name": "Blocked service"})
        html = self.client.get("/customers/new").data
        self.assertNotIn(b"Save customer", html)

    def test_idempotent_migration_preserves_legacy_and_financial_rows(self):
        tx = self.sale()
        old = {
            table: self.query(f"SELECT * FROM {table}")
            for table in ("customers", "products", "transactions", "transaction_items")
        }
        db.initialize_database(seed=False)
        db.initialize_database(seed=False)
        for table, rows in old.items():
            self.assertEqual(rows, self.query(f"SELECT * FROM {table}"))
        with self.context():
            self.assertEqual(get_sale(tx)["transaction"]["total_cents"], 217)
        self.assertEqual(self.query("PRAGMA integrity_check"), [{"integrity_check": "ok"}])
        self.assertEqual(self.query("PRAGMA foreign_key_check"), [])
