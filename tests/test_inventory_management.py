"""Day 15 integration tests: three accounts, real login/forms, scoped SQL."""

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from auth_helpers import PASSWORD, login
from flask import g

from database import database as db
from database.inventory_migration import migrate_inventory
from database.migrations import migrate_identity
from manage import check_database, seed_demo
from services.inventory import set_product_quantity
from services.inventory_management import (
    adjust_stock,
    query_inventory,
    stock_status,
)
from services.transaction_service import complete_transaction
from web_app import app


class InventoryManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_FILE
        self.old_config = app.config.copy()
        db.DB_FILE = str(Path(self.tmp.name) / "inventory.db")
        db.initialize_database()
        app.config.update(TESTING=True, DEMO_READ_ONLY=False)
        self.clients = []
        self.stores = []
        self.products = []
        for letter in ("a", "b", "c"):
            client = app.test_client()
            response = self.post(
                client,
                "/register",
                {
                    "email": f"{letter}@example.com",
                    "password": PASSWORD,
                    "password_confirm": PASSWORD,
                    "store_name": f"Store {letter.upper()}",
                },
            )
            self.assertEqual(response.status_code, 302)
            login(client, f"{letter}@example.com")
            self.clients.append(client)
            self.stores.append(
                self.query(
                    "SELECT m.store_id FROM store_memberships m JOIN users u ON u.id=m.user_id WHERE u.email=?",
                    (f"{letter}@example.com",),
                )[0]["store_id"]
            )
            response = self.post(
                client,
                "/products/new",
                {"name": f"Private Product {letter.upper()}", "price": "2.00", "quantity": "10"},
            )
            self.assertEqual(response.status_code, 302)
            self.products.append(
                self.query("SELECT id FROM products WHERE store_id=?", (self.stores[-1],))[0]["id"]
            )

    def tearDown(self):
        app.config.clear()
        app.config.update(self.old_config)
        db.DB_FILE = self.old_db
        self.tmp.cleanup()

    def query(self, sql, params=()):
        with closing(db.get_db_connection()) as conn:
            return [dict(row) for row in conn.execute(sql, params)]

    def post(self, client, path, data=None, follow=False):
        client.get("/login")
        with client.session_transaction() as state:
            csrf = state["csrf_token"]
        return client.post(path, data=dict(data or {}, csrf_token=csrf), follow_redirects=follow)

    def category(self, index=0, name="Accessories"):
        response = self.post(
            self.clients[index],
            "/inventory/categories",
            {"name": name, "store_id": self.stores[(index + 1) % 3]},
        )
        self.assertEqual(response.status_code, 302)
        return self.query(
            "SELECT id FROM categories WHERE store_id=? ORDER BY id DESC", (self.stores[index],)
        )[0]["id"]

    def edit(self, index=0, **extra):
        product = self.query("SELECT * FROM products WHERE id=?", (self.products[index],))[0]
        values = {
            "name": product["name"],
            "price": "2.00",
            "quantity": product["quantity"],
            "expected_quantity": product["quantity"],
        }
        values.update(extra)
        return self.post(self.clients[index], f"/products/{product['id']}/edit", values)

    def movement(self, index=0, kind="restock", amount=2, reason="Fictional delivery", **extra):
        values = {"type": kind, "amount": amount, "reason": reason}
        values.update(extra)
        return self.post(self.clients[index], f"/inventory/adjust/{self.products[index]}", values)

    def test_three_accounts_category_and_inventory_isolation(self):
        ids = [self.category(index) for index in range(3)]
        self.assertEqual(len(set(ids)), 3)
        for index, client in enumerate(self.clients):
            self.assertEqual(self.edit(index, category_id=ids[index]).status_code, 302)
            page = client.get("/inventory").data
            self.assertIn(f"Private Product {'ABC'[index]}".encode(), page)
            for other in set(range(3)) - {index}:
                self.assertNotIn(f"Private Product {'ABC'[other]}".encode(), page)
            self.assertIn(b"Accessories", page)
        self.assertEqual(
            [row["store_id"] for row in self.query("SELECT store_id FROM categories ORDER BY id")],
            self.stores,
        )

    def test_category_normalization_duplicates_rename_and_safe_removal(self):
        category = self.category(name="  Office   SUPPLIES ")
        for name in ("office supplies", "ＯＦＦＩＣＥ supplies"):
            response = self.post(self.clients[0], "/inventory/categories", {"name": name})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.edit(category_id=category).status_code, 302)
        self.assertEqual(
            self.post(
                self.clients[0],
                f"/inventory/categories/{category}/rename",
                {"name": "Desk supplies"},
            ).status_code,
            302,
        )
        self.assertIn(b"Desk supplies", self.clients[0].get("/inventory").data)
        history_before = self.query("SELECT * FROM stock_adjustments")
        self.assertEqual(
            self.post(self.clients[0], f"/inventory/categories/{category}/remove").status_code, 302
        )
        self.assertIsNone(
            self.query("SELECT category_id FROM products WHERE id=?", (self.products[0],))[0][
                "category_id"
            ]
        )
        self.assertEqual(history_before, self.query("SELECT * FROM stock_adjustments"))
        self.assertIn(b"Uncategorized", self.clients[0].get("/inventory").data)
        self.assertEqual(
            len(self.query("SELECT * FROM products WHERE id=?", (self.products[0],))), 1
        )

    def test_invalid_category_names_and_foreign_category_ids(self):
        category = self.category(1, name="Private B category")
        for name in ("", " " * 8, "x" * 81, "bad\x00name"):
            self.assertEqual(
                self.post(self.clients[0], "/inventory/categories", {"name": name}).status_code, 400
            )
        for operation in ("rename", "remove"):
            response = self.post(
                self.clients[0], f"/inventory/categories/{category}/{operation}", {"name": "stolen"}
            )
            self.assertEqual(response.status_code, 400)
            self.assertNotIn(b"Private B category", response.data)
        self.assertEqual(self.edit(category_id=category).status_code, 400)
        self.assertIsNone(
            self.query("SELECT category_id FROM products WHERE id=?", (self.products[0],))[0][
                "category_id"
            ]
        )
        self.assertEqual(
            self.query("SELECT name FROM categories WHERE id=?", (category,))[0]["name"],
            "Private B category",
        )
        with closing(db.get_db_connection()) as conn:
            with self.assertRaises(sqlite3.IntegrityError), conn:
                conn.execute(
                    "UPDATE products SET category_id=? WHERE id=?", (category, self.products[0])
                )

    def test_sku_validation_normalization_and_store_uniqueness(self):
        for index in range(3):
            self.assertEqual(self.edit(index, sku="abc-01").status_code, 302)
            self.assertIn(b"ABC-01", self.clients[index].get("/inventory?q=abc-01").data)
        response = self.post(
            self.clients[0],
            "/products/new",
            {"name": "Duplicate SKU", "price": "3", "quantity": 1, "sku": "ABC-01"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.query("SELECT * FROM products WHERE name='Duplicate SKU'"))
        for sku in ("has spaces", "<script>", "a" * 65):
            self.assertEqual(self.edit(sku=sku).status_code, 400)

    def test_combined_filters_status_category_sku_and_inactive(self):
        category = self.category()
        self.edit(category_id=category, sku="ONE", low_stock_threshold=10)
        with patch("web_app.render_template", return_value="ok") as render:
            self.clients[0].get(
                f"/inventory?q=ONE&category={category}&status=low&active=active&sort=quantity&direction=desc"
            )
        self.assertEqual([p["id"] for p in render.call_args.kwargs["products"]], [self.products[0]])
        self.assertEqual(render.call_args.kwargs["low_stock_count"], 1)
        self.assertEqual(self.clients[0].get("/inventory?q=Private%20Product%20B").status_code, 200)
        self.assertNotIn(
            b"Private Product B", self.clients[0].get("/inventory?status=in&sort=price").data
        )
        self.post(self.clients[0], f"/products/{self.products[0]}/deactivate", follow=True)
        self.assertNotIn(b"Private Product A", self.clients[0].get("/inventory").data)
        self.assertIn(b"Private Product A", self.clients[0].get("/inventory?active=inactive").data)
        self.assertIn(b"Private Product A", self.clients[0].get("/inventory?active=all").data)
        self.assertIn(
            b"Private Product A",
            self.clients[0].get(f"/inventory/history?product_id={self.products[0]}").data,
        )

    def test_sorting_pagination_preserves_filters_and_is_bounded(self):
        category = self.category()
        for number in range(25):
            response = self.post(
                self.clients[0],
                "/products/new",
                {
                    "name": f"Widget {number:02d}",
                    "price": str(number + 1),
                    "quantity": number,
                    "category_id": category,
                },
            )
            self.assertEqual(response.status_code, 302)
        path = f"/inventory?q=Widget&category={category}&sort=price&direction=desc"
        with patch("web_app.render_template", return_value="ok") as render:
            self.clients[0].get(path)
        data = render.call_args.kwargs
        self.assertEqual(data["total_products"], 25)
        self.assertEqual(len(data["products"]), 20)
        self.assertEqual(data["products"][0]["price_cents"], 2500)
        self.assertIn("q=Widget", data["pagination"]["next"])
        self.assertIn(f"category={category}", data["pagination"]["next"])
        with patch("web_app.render_template", return_value="ok") as render:
            self.clients[0].get(path + "&page=2")
        self.assertEqual(len(render.call_args.kwargs["products"]), 5)
        for sort in ("name", "quantity", "updated"):
            self.assertEqual(self.clients[0].get("/inventory?sort=" + sort).status_code, 200)

    def test_invalid_filters_sql_injection_and_wildcards(self):
        for query in (
            "sort=name;DROP TABLE products",
            "direction=desc--",
            "category=-1",
            "status=bad",
            "active=bad",
            "page=0",
            "page=nan",
            "q=" + "x" * 101,
        ):
            self.assertEqual(self.clients[0].get("/inventory?" + query).status_code, 400)
        with app.test_request_context("/"):
            g.store = {"id": self.stores[0]}
            with self.assertRaises(ValueError):
                query_inventory({"sort": "name; DROP TABLE products"})
        self.assertNotIn(b"Private Product A", self.clients[0].get("/inventory?q=%25").data)
        self.assertEqual(self.clients[0].get("/inventory?q=%27%20OR%201=1--").status_code, 200)
        self.assertEqual(len(self.query("SELECT * FROM products")), 5)

    def test_all_stock_events_chain_actor_and_utc(self):
        self.assertEqual(self.movement(kind="restock", amount=5).status_code, 302)
        self.assertEqual(
            self.movement(kind="damaged", amount=3, reason="Damaged packaging").status_code, 302
        )
        self.assertEqual(
            self.movement(kind="return", amount=1, reason="Returned unopened stock").status_code,
            302,
        )
        self.assertEqual(
            self.movement(
                kind="correction", amount=7, expected_quantity=13, reason="Counted seven units"
            ).status_code,
            302,
        )
        rows = self.query(
            "SELECT * FROM stock_adjustments WHERE product_id=? ORDER BY id", (self.products[0],)
        )
        self.assertEqual(
            [row["adjustment_type"] for row in rows],
            ["initial", "restock", "damaged", "return", "correction"],
        )
        quantity = 0
        for row in rows:
            self.assertEqual(row["quantity_before"], quantity)
            self.assertEqual(row["quantity_before"] + row["quantity_change"], row["quantity_after"])
            quantity = row["quantity_after"]
            self.assertEqual(row["store_id"], self.stores[0])
            self.assertIsNotNone(row["actor_user_id"])
            stamp = datetime.fromisoformat(row["recorded_at"].replace("Z", "+00:00"))
            self.assertEqual(stamp.utcoffset(), timezone.utc.utcoffset(stamp))
        self.assertEqual(quantity, 7)

    def test_checkout_audit_atomicity_and_retry(self):
        self.clients[0].get("/sale")
        self.post(self.clients[0], "/sale/add", {"product_id": self.products[0], "quantity": 2})
        with self.clients[0].session_transaction() as state:
            snapshot = dict(state)
            checkout = state["checkout_token"]
        self.assertEqual(
            self.post(
                self.clients[0],
                "/sale/checkout",
                {"payment_method": "Card", "checkout_token": checkout},
            ).status_code,
            302,
        )
        rows = self.query(
            "SELECT * FROM stock_adjustments WHERE store_id=? AND adjustment_type='checkout'",
            (self.stores[0],),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            (rows[0]["quantity_before"], rows[0]["quantity_change"], rows[0]["quantity_after"]),
            (10, -2, 8),
        )
        self.assertIsNotNone(rows[0]["transaction_id"])
        other = app.test_client()
        with other.session_transaction() as state:
            state.update(snapshot)
        self.post(other, "/sale/checkout", {"payment_method": "Card", "checkout_token": checkout})
        self.assertEqual(
            len(self.query("SELECT * FROM stock_adjustments WHERE adjustment_type='checkout'")), 1
        )
        self.assertEqual(
            self.query("SELECT quantity FROM products WHERE id=?", (self.products[0],))[0][
                "quantity"
            ],
            8,
        )
        self.assertIn(b"Private Product A", self.clients[0].get("/receipt").data)

    def test_audit_insert_failure_rolls_back_stock_edit_and_checkout(self):
        before = self.query("SELECT * FROM products WHERE id=?", (self.products[0],))[0]
        with closing(db.get_db_connection()) as conn:
            conn.execute(
                "CREATE TRIGGER reject_audit BEFORE INSERT ON stock_adjustments BEGIN SELECT RAISE(ABORT,'injected'); END"
            )
        self.assertEqual(self.movement(amount=2).status_code, 503)
        self.assertEqual(
            self.query("SELECT * FROM products WHERE id=?", (self.products[0],))[0], before
        )
        self.clients[0].get("/sale")
        self.post(self.clients[0], "/sale/add", {"product_id": self.products[0], "quantity": 1})
        with self.clients[0].session_transaction() as state:
            checkout = state["checkout_token"]
        self.post(
            self.clients[0],
            "/sale/checkout",
            {"payment_method": "Card", "checkout_token": checkout},
        )
        self.assertFalse(
            self.query("SELECT * FROM transactions WHERE store_id=?", (self.stores[0],))
        )
        self.assertEqual(
            self.query("SELECT quantity FROM products WHERE id=?", (self.products[0],))[0][
                "quantity"
            ],
            10,
        )

    def test_invalid_stock_changes_and_stale_corrections(self):
        before = self.query("SELECT * FROM stock_adjustments")
        for kind, amount, reason in [
            ("restock", -1, "bad"),
            ("damaged", 11, "bad"),
            ("return", "1.5", "bad"),
            ("correction", 1000001, "bad"),
            ("restock", 1, ""),
            ("checkout", 1, "bad"),
        ]:
            self.assertEqual(
                self.movement(kind=kind, amount=amount, reason=reason).status_code, 400
            )
        self.assertEqual(self.movement(kind="correction", amount=5).status_code, 400)
        self.assertEqual(self.query("SELECT * FROM stock_adjustments"), before)
        self.movement(amount=1)
        self.assertEqual(
            self.movement(kind="correction", amount=5, expected_quantity=10).status_code, 400
        )
        self.assertEqual(
            self.edit(quantity=8, expected_quantity=10, name="Should roll back").status_code, 400
        )
        self.assertEqual(
            self.query("SELECT name,quantity FROM products WHERE id=?", (self.products[0],))[0],
            {"name": "Private Product A", "quantity": 11},
        )

    def test_history_immutable_date_filters_and_idor(self):
        self.movement(reason="Private movement A")
        self.movement(1, reason="Private movement B")
        self.movement(2, reason="Private movement C")
        row = self.query("SELECT * FROM stock_adjustments WHERE reason='Private movement B'")[0]
        for client in (self.clients[0], self.clients[2]):
            self.assertEqual(client.get(f"/inventory/history/{row['id']}").status_code, 404)
            response = client.get(f"/inventory/history?product_id={self.products[1]}")
            self.assertNotIn(b"Private movement B", response.data)
            self.assertNotIn(b"Private Product B", response.data)
        today = datetime.now(timezone.utc).date().isoformat()
        response = self.clients[0].get(
            f"/inventory/history?type=restock&start_date={today}&end_date={today}&product_id={self.products[0]}"
        )
        self.assertIn(b"Private movement A", response.data)
        for query in (
            "type=bad",
            "product_id=-1",
            "start_date=bad",
            "start_date=2026-12-01&end_date=2026-01-01",
        ):
            self.assertEqual(self.clients[0].get("/inventory/history?" + query).status_code, 400)
        for statement in (
            "UPDATE stock_adjustments SET reason='changed'",
            "DELETE FROM stock_adjustments",
            "UPDATE inventory_baselines SET quantity=0",
            "DELETE FROM inventory_baselines",
        ):
            with closing(db.get_db_connection()) as conn:
                with self.assertRaises(sqlite3.IntegrityError), conn:
                    conn.execute(statement)
        self.assertEqual(
            self.post(
                self.clients[0], f"/inventory/history/{row['id']}", {"reason": "changed"}
            ).status_code,
            405,
        )

    def test_threshold_boundaries_dashboard_and_three_store_alerts(self):
        self.assertEqual(stock_status({"quantity": 0, "low_stock_threshold": 0}), "out")
        for quantity, threshold, expected in (
            (2, 3, "low"),
            (3, 3, "low"),
            (4, 3, "in"),
            (1, 0, "in"),
            (5, None, "low"),
        ):
            self.assertEqual(
                stock_status({"quantity": quantity, "low_stock_threshold": threshold}), expected
            )
        self.edit(0, low_stock_threshold=10)
        self.edit(1, low_stock_threshold=0)
        self.movement(2, kind="correction", amount=0, expected_quantity=10)
        for index, client in enumerate(self.clients):
            with patch("web_app.render_template", return_value="ok") as render:
                client.get("/")
            data = render.call_args.kwargs
            self.assertEqual(data["low_stock_count"], 1 if index == 0 else 0)
            self.assertEqual(data["out_of_stock_count"], 1 if index == 2 else 0)
            for product in data["low_stock_products"] + data["out_of_stock_products"]:
                self.assertEqual(product["store_id"], self.stores[index])
        for value in ("-1", "1.5", "1000001", "NaN"):
            self.assertEqual(self.edit(low_stock_threshold=value).status_code, 400)
        self.assertEqual(self.edit(low_stock_threshold="").status_code, 302)
        self.assertIsNone(
            self.query("SELECT low_stock_threshold FROM products WHERE id=?", (self.products[0],))[
                0
            ]["low_stock_threshold"]
        )

    def test_foreign_product_actions_and_filters_do_not_infer_stock(self):
        category = self.category(1)
        before = self.query("SELECT * FROM products WHERE store_id=?", (self.stores[1],))
        self.assertEqual(
            self.clients[0].get(f"/inventory/adjust/{self.products[1]}").status_code, 404
        )
        self.assertEqual(
            self.post(
                self.clients[0],
                f"/inventory/adjust/{self.products[1]}",
                {"type": "restock", "amount": 1, "reason": "bad"},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.post(
                self.clients[0],
                f"/products/{self.products[1]}/edit",
                {"name": "bad", "price": "1", "quantity": 0, "low_stock_threshold": 999},
            ).status_code,
            302,
        )
        with patch("web_app.render_template", return_value="ok") as render:
            self.clients[0].get(f"/inventory?category={category}&active=all&status=in")
        self.assertEqual(render.call_args.kwargs["total_products"], 0)
        self.assertEqual(
            before, self.query("SELECT * FROM products WHERE store_id=?", (self.stores[1],))
        )
        self.assertEqual(self.clients[0].get("/api/inventory").status_code, 404)

    def test_concurrent_adjustments_and_checkout_never_lose_history(self):
        with self.clients[0].session_transaction() as state:
            authenticated = dict(state)

        def restock(_):
            client = app.test_client()
            with client.session_transaction() as state:
                state.update(authenticated)
            response = self.post(client, f'/inventory/adjust/{self.products[0]}',
                                 {'type':'restock','amount':1,'reason':'Concurrent delivery'})
            self.assertEqual(response.status_code,302)

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(restock, range(8)))
        rows = self.query(
            "SELECT * FROM stock_adjustments WHERE product_id=? ORDER BY id", (self.products[0],)
        )
        self.assertEqual(len(rows), 9)
        self.assertEqual(rows[-1]["quantity_after"], 18)
        for before, after in zip(rows, rows[1:]):
            self.assertEqual(before["quantity_after"], after["quantity_before"])
        with app.test_request_context("/"):
            g.store = {"id": self.stores[0]}
            g.is_demo = False
            adjust_stock(self.products[0], 1, "correction", "Last unit")

        def checkout(index):
            with app.test_request_context("/"):
                g.store = {"id": self.stores[0]}
                g.is_demo = False
                try:
                    complete_transaction(
                        [{"id": self.products[0], "qty": 1}],
                        "2.17",
                        "Card",
                        checkout_token=f"race-{index}",
                    )
                    return True
                except ValueError:
                    return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(checkout, range(2))), 1)
        self.assertEqual(
            len(self.query("SELECT * FROM stock_adjustments WHERE adjustment_type='checkout'")), 1
        )
        self.assertEqual(
            self.query("SELECT quantity FROM products WHERE id=?", (self.products[0],))[0][
                "quantity"
            ],
            0,
        )

    def test_logout_account_switch_csrf_and_public_demo(self):
        category = self.category()
        self.assertEqual(
            self.clients[0].post("/inventory/categories", data={"name": "bad"}).status_code, 400
        )
        self.assertEqual(
            self.clients[0].post(f"/inventory/categories/{category}/remove", data={}).status_code,
            400,
        )
        self.clients[0].get("/sale")
        self.post(self.clients[0], "/sale/add", {"product_id": self.products[0], "quantity": 1})
        login(self.clients[0], "c@example.com")
        with self.clients[0].session_transaction() as state:
            self.assertNotIn("sale_cart", state)
        self.assertNotIn(b"Private Product A", self.clients[0].get("/inventory").data)
        seed_demo(allow_existing=True)
        self.post(self.clients[0], "/demo")
        for path in ("/inventory/categories", "/inventory/history", "/inventory?active=all"):
            self.assertNotIn(b"Private Product A", self.clients[0].get(path).data)
        before = self.query("SELECT * FROM stock_adjustments")
        for path, data in (
            ("/inventory/categories", {"name": "bad"}),
            (f"/inventory/categories/{category}/rename", {"name": "bad"}),
            (
                f"/inventory/adjust/{self.products[0]}",
                {"type": "restock", "amount": 1, "reason": "bad"},
            ),
        ):
            self.assertEqual(self.post(self.clients[0], path, data).status_code, 403)
        self.assertEqual(before, self.query("SELECT * FROM stock_adjustments"))
        self.post(self.clients[0], "/logout")
        self.assertEqual(self.clients[0].get("/inventory/history").status_code, 302)
        self.assertEqual(app.test_client().get("/inventory/categories").status_code, 302)
        self.assertEqual(app.test_client().post("/api/inventory", data={}).status_code, 400)

    def test_legacy_desktop_setter_and_raw_quantity_updates_are_audited(self):
        set_product_quantity(1, 7)
        with closing(db.get_db_connection()) as conn:
            with conn:
                conn.execute("UPDATE products SET quantity=8 WHERE id=1")
        rows = self.query("SELECT * FROM stock_adjustments WHERE product_id=1 ORDER BY id")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["quantity_before"], 5)
        self.assertEqual(rows[-1]["quantity_after"], 8)
        self.assertTrue(all(row["actor_user_id"] is None for row in rows))
        self.assertTrue(check_database()[0])

    def test_category_and_reason_xss_is_escaped(self):
        category = self.category(name="<script>alert(1)</script>")
        self.edit(category_id=category)
        self.assertNotIn(b"<script>alert(1)</script>", self.clients[0].get("/inventory").data)
        self.movement(reason="<script>alert(2)</script>")
        self.assertNotIn(
            b"<script>alert(2)</script>", self.clients[0].get("/inventory/history").data
        )

    def test_migration_from_day14_preserves_quantities_ownership_and_money(self):
        db.DB_FILE = str(Path(self.tmp.name) / "pre15.db")
        with closing(db.get_db_connection()) as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                db._initialize_schema(conn, True)
                migrate_identity(conn)
        with closing(db.get_db_connection()) as conn:
            before = [dict(row) for row in conn.execute("SELECT * FROM products ORDER BY id")]
            migrate_inventory(conn)
            conn.commit()
        db.initialize_database(seed=False)
        db.initialize_database(seed=False)
        after = self.query("SELECT * FROM products ORDER BY id")
        for old, new in zip(before, after):
            self.assertEqual(old, {key: new[key] for key in old})
        self.assertFalse(self.query("SELECT * FROM stock_adjustments"))
        self.assertEqual(
            [
                (row["product_id"], row["store_id"], row["quantity"])
                for row in self.query("SELECT * FROM inventory_baselines ORDER BY product_id")
            ],
            [(1, 1, 5), (2, 1, 0)],
        )
        self.assertTrue(check_database()[0])
