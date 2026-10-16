"""Day 20 gate: real HTTP, three stores, exact tax and preserved history."""

import tempfile
import unittest
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from auth_helpers import PASSWORD, login
from flask import g

from database import database as db
from manage import seed_demo
from services.auth import register_user
from services.inventory import create_product
from services.inventory_management import query_inventory, stock_status
from services.money import calculate_tax_cents, from_cents
from services.settings import get_settings, save_settings
from services.transaction_service import complete_transaction
from web_app import app


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db, self.old_config = db.DB_FILE, app.config.copy()
        db.DB_FILE = str(Path(self.tmp.name) / "settings.db")
        db.initialize_database()
        app.config.update(TESTING=True, DEMO_READ_ONLY=False)
        self.accounts = [
            register_user(f"settings-{x}@example.com", PASSWORD, PASSWORD, f"Store {x}")
            for x in "abc"
        ]
        self.clients = [app.test_client() for _ in self.accounts]
        for client, x in zip(self.clients, "abc", strict=True):
            login(client, f"settings-{x}@example.com")

    def tearDown(self):
        app.config.clear()
        app.config.update(self.old_config)
        db.DB_FILE = self.old_db
        self.tmp.cleanup()

    def context(self, index=0):
        return app.test_request_context("/")

    def identity(self, index=0):
        user, store = self.accounts[index]
        g.user, g.store, g.is_demo = {"id": user}, {"id": store}, False

    def post(self, index, section, fields, csrf=True):
        client = self.clients[index]
        client.get("/settings")
        with client.session_transaction() as state:
            token = state["csrf_token"]
        with closing(db.get_db_connection()) as conn:
            revision = conn.execute(
                "SELECT revision FROM store_settings WHERE store_id=?", (self.accounts[index][1],)
            ).fetchone()[0]
        data = {"revision": str(revision), **fields}
        if csrf:
            data["csrf_token"] = token
        return client.post("/settings/" + section, data=data)

    def test_defaults_idempotency_and_constraints_preserve_legacy(self):
        with closing(db.get_db_connection()) as conn:
            products = [tuple(x) for x in conn.execute("SELECT * FROM products")]
        db.initialize_database()
        db.initialize_database()
        with closing(db.get_db_connection()) as conn:
            self.assertEqual(products, [tuple(x) for x in conn.execute("SELECT * FROM products")])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM store_settings").fetchone()[0], 4)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        with self.context():
            self.identity()
            self.assertEqual(get_settings()["tax_rate"], Decimal(".0825"))

    def test_three_accounts_independent_persistence_and_no_mass_assignment(self):
        for index in range(3):
            self.assertEqual(
                self.post(
                    index,
                    "business",
                    {"store_name": f"Unique {index}", "description": f"PRIVATE-{index}"},
                ).status_code,
                302,
            )
        for index, client in enumerate(self.clients):
            response = client.get("/settings")
            self.assertEqual(response.status_code, 200)
            self.assertIn(f"PRIVATE-{index}".encode(), response.data)
            for other in set(range(3)) - {index}:
                self.assertNotIn(f"PRIVATE-{other}".encode(), response.data)
            self.assertEqual(
                self.post(
                    index,
                    "tax",
                    {"tax_percent": "9", "store_id": str(self.accounts[(index + 1) % 3][1])},
                ).status_code,
                400,
            )
        with closing(db.get_db_connection()) as conn:
            self.assertEqual(
                {x[0] for x in conn.execute("SELECT tax_units FROM store_settings")}, {82500}
            )
        self.assertEqual(app.test_client().get("/settings").status_code, 302)
        self.assertEqual(
            app.test_client().post("/settings/tax", data={"tax_percent": "99"}).status_code, 302
        )

    def test_exact_tax_snapshot_history_and_stale_checkout(self):
        with self.context():
            self.identity()
            pid = create_product("Fictional fractional tax item", ".05", 10)
            first = complete_transaction([{"id": pid, "qty": 1}], ".05", "Card")
        receipt_fields = dict(
            receipt_name="<script>fictional</script>",
            receipt_contact="Example contact",
            receipt_header="Hello",
            receipt_footer="Goodbye",
            thank_you="Thanks",
        )
        self.assertEqual(self.post(0, "receipt", receipt_fields).status_code, 302)
        self.assertEqual(self.post(0, "tax", {"tax_percent": "10.0000"}).status_code, 302)
        with self.context():
            self.identity()
            self.assertEqual(calculate_tax_cents(5, get_settings()["tax_rate"]), 1)
            with self.assertRaises(ValueError):
                complete_transaction([{"id": pid, "qty": 1}], ".05", "Card")
            second = complete_transaction([{"id": pid, "qty": 1}], from_cents(6), "Card")
        self.assertEqual(
            self.post(
                0, "receipt", {**receipt_fields, "receipt_name": "Changed later"}
            ).status_code,
            302,
        )
        response = self.clients[0].get(f"/transactions/{second}/receipt")
        self.assertIn(b"&lt;script&gt;fictional&lt;/script&gt;", response.data)
        self.assertNotIn(b"<script>fictional</script>", response.data)
        self.assertNotIn(
            b"Changed later", response.data.split(b'<article class="receipt-paper">')[1]
        )
        with closing(db.get_db_connection()) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT total_cents FROM transactions WHERE id=?", (first,)
                ).fetchone()[0],
                5,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT tax_rate_percent FROM transactions WHERE id=?", (second,)
                ).fetchone()[0],
                "10",
            )
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 2)
            self.assertEqual(
                conn.execute("SELECT quantity FROM products WHERE id=?", (pid,)).fetchone()[0], 8
            )
        self.assertEqual(self.clients[1].get(f"/transactions/{second}/receipt").status_code, 302)

    def test_thresholds_override_and_no_stock_mutation(self):
        with self.context():
            self.identity()
            normal = create_product("Default", "1", 7)
            override = create_product("Override", "1", 7, low_stock_threshold=2)
        self.assertEqual(self.post(0, "inventory", {"low_stock_default": "8"}).status_code, 302)
        with self.context():
            self.identity()
            result = query_inventory({"status": "low"})
            self.assertEqual([p["id"] for p in result["products"]], [normal])
            self.assertEqual(stock_status({"quantity": 7, "low_stock_threshold": None}), "low")
            self.assertEqual(stock_status({"quantity": 7, "low_stock_threshold": 2}), "in")
        with closing(db.get_db_connection()) as conn:
            self.assertEqual(
                conn.execute("SELECT quantity FROM products WHERE id=?", (override,)).fetchone()[0],
                7,
            )

    def test_store_tax_defaults_widgets_and_service_rejects_ownership_fields(self):
        for index, percent in enumerate(("0", "8.875", "100")):
            self.assertEqual(self.post(index, "tax", {"tax_percent": percent}).status_code, 302)
            with self.context():
                self.identity(index)
                self.assertEqual(get_settings()["tax_percent"], Decimal(percent))
                revision = str(get_settings()["revision"])
                with self.assertRaises(ValueError):
                    save_settings(
                        "tax",
                        {
                            "tax_percent": "99",
                            "revision": revision,
                            "store_id": str(self.accounts[(index + 1) % 3][1]),
                        },
                    )
                self.assertEqual(get_settings()["tax_percent"], Decimal(percent))
        self.assertEqual(
            self.post(
                0, "analytics", {"report_period": "last7", "landing_view": "inventory"}
            ).status_code,
            302,
        )
        response = self.clients[0].get("/")
        self.assertNotIn(b'aria-label="Inventory alerts"', response.data)
        self.assertNotIn(b"Recent transactions</h2>", response.data)
        response = self.clients[1].get("/")
        self.assertIn(b'aria-label="Inventory alerts"', response.data)

    def test_validation_csrf_stale_revision_and_demo(self):
        for value in ("NaN", "Infinity", "-1", "100.0001", "1e1", "1.12345", "<script>", ""):
            with self.subTest(rate=value):
                self.assertEqual(self.post(0, "tax", {"tax_percent": value}).status_code, 400)
        self.assertEqual(self.post(0, "tax", {"tax_percent": "1"}, csrf=False).status_code, 400)
        with self.context():
            self.identity()
            save_settings("tax", {"tax_percent": "1", "revision": "0"})
            with self.assertRaises(ValueError):
                save_settings("tax", {"tax_percent": "2", "revision": "0"})
        seed_demo(allow_existing=True)
        demo = app.test_client()
        demo.get("/login")
        with demo.session_transaction() as state:
            token = state["csrf_token"]
        self.assertEqual(demo.post("/demo", data={"csrf_token": token}).status_code, 302)
        self.assertEqual(demo.get("/settings").status_code, 200)
        self.assertEqual(
            demo.post(
                "/settings/tax", data={"csrf_token": token, "tax_percent": "99", "revision": "0"}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.post(
                1,
                "analytics",
                {
                    "report_period": "today",
                    "landing_view": "products",
                    "show_stock": "0",
                    "show_recent": "0",
                },
            ).status_code,
            302,
        )
        with self.context():
            self.identity(1)
            self.assertEqual(get_settings()["report_period"], "today")
