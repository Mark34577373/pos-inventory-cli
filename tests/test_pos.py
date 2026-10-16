"""Day 26A actual Flask/SQLite acceptance, financial and authorization tests."""

import json
import secrets
import unittest
from concurrent.futures import ThreadPoolExecutor

import test_customers as fixtures
from auth_helpers import login
from flask import g
from test_ui import Document

from database import database as db
from services.customers import customer_history
from services.held_sales import get_held, hold_sale, list_held, resume_sale, update_held
from services.inventory import create_product, deactivate_product, update_product_details
from services.inventory_management import adjust_stock, save_category
from services.money import from_cents
from services.pos_cart import price_cart
from services.pos_layout import defaults, get_layout, save_layout
from services.refunds import get_sale, process_refund
from services.transaction_service import complete_transaction
from web_app import app


class POSTests(unittest.TestCase):
    setUp = fixtures.CustomerTests.setUp
    tearDown = fixtures.CustomerTests.tearDown
    context = fixtures.CustomerTests.context
    query = fixtures.CustomerTests.query
    post = fixtures.CustomerTests.post

    def hold(self, index=0):
        with self.context(index):
            return hold_sale(
                {str(self.products[index]): 2},
                self.ids[index],
                {"kind": "fixed", "value": ".10"},
                "Fictional note",
                "Fictional held sale",
                secrets.token_urlsafe(32),
            )

    def checkout_claim(self, data, **overrides):
        with self.context():
            quote = price_cart(data["cart"], data["customer_id"], data["discount"])
            kwargs = dict(
                checkout_token=data["checkout_token"],
                customer_id=data["customer_id"],
                discount_kind=data["discount"]["kind"],
                discount_value=data["discount"]["value"],
                order_notes=data["notes"],
                held_sale_id=data["held_id"],
                held_claim=data["claim"],
            )
            kwargs.update(overrides)
            return complete_transaction(
                [{"id": int(key), "qty": qty} for key, qty in data["cart"].items()],
                from_cents(quote["total_cents"]),
                "Card",
                **kwargs,
            )

    def test_presets_persist_revision_reset_and_financial_invariance(self):
        with self.context():
            extra = create_product("A first alphabetically", "2.00", 10)
            original = price_cart(
                {str(self.products[0]): 3}, self.ids[0], {"kind": "fixed", "value": ".50"}
            )["total_cents"]
            for revision, preset in enumerate(("hybrid", "quick", "custom")):
                config = dict(
                    defaults(),
                    preset=preset,
                    product_order=[self.products[0], extra],
                    favorites=[extra],
                    colors={str(extra): "green"},
                )
                save_layout(config, revision)
                self.assertEqual(get_layout()["config"], config)
                self.assertEqual(
                    price_cart(
                        {str(self.products[0]): 3}, self.ids[0], {"kind": "fixed", "value": ".50"}
                    )["total_cents"],
                    original,
                )
                self.assertEqual(self.client.get("/sale").status_code, 200)
            self.assertEqual(original, 271)
            with self.assertRaises(ValueError):
                save_layout(defaults(), 0)
            save_layout(defaults(), 3)
            self.assertEqual(get_layout()["config"], defaults())

    def test_configuration_allowlist_bounds_ownership_and_stale_references(self):
        with self.context():
            for override in (
                {"prices": {}},
                {"preset": "fake"},
                {"show_images": "yes"},
                {"product_order": [self.products[1]]},
                {"favorites": [self.products[0]] * 101},
                {"product_order": [self.products[0], self.products[0]]},
                {"category_order": [99999]},
                {"colors": {str(self.products[0]): "red; background:url(x)"}},
            ):
                config = dict(defaults(), **override)
                with self.assertRaises(ValueError):
                    save_layout(config, 0)
            config = dict(
                defaults(),
                preset="custom",
                product_order=[self.products[0]],
                favorites=[self.products[0]],
            )
            save_layout(config, 0)
            deactivate_product(self.products[0])
            self.assertEqual(get_layout()["config"]["product_order"], [])
            self.assertEqual(get_layout()["config"]["favorites"], [])
        self.assertEqual(self.client.get("/pos/designer").status_code, 200)
        self.assertEqual(self.client.get("/sale?favorites=1").status_code, 200)

    def test_custom_global_product_order_favorites_and_category_navigation(self):
        with self.context():
            category = save_category("Fictional category")
            target = create_product("ZZZ Pinned", "3.00", 2, category_id=category, sku="PIN-123")
            for number in range(22):
                create_product(f"AAA Item {number:02}", "1", 2)
            save_layout(
                dict(
                    defaults(),
                    preset="custom",
                    product_order=[target],
                    favorites=[target],
                    category_order=[category],
                    colors={str(target): "purple"},
                ),
                0,
            )
        page = self.client.get("/sale")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"ZZZ Pinned", page.data)
        self.assertIn(b"color-purple", page.data)
        self.assertIn(b"ZZZ Pinned", self.client.get("/sale?favorites=1").data)
        self.assertNotIn(b"AAA Item 00", self.client.get("/sale?favorites=1").data)
        self.assertIn(b"ZZZ Pinned", self.client.get("/sale?q=PIN-123").data)
        self.assertIn(b"Fictional category", self.client.get(f"/sale?category={category}").data)

    def test_held_http_revalidation_customer_notes_cash_receipt_and_refund(self):
        self.client.get("/sale")
        self.post("/sale/add", dict(product_id=self.products[0], quantity=3))
        self.post("/sale/customer", dict(customer_id=self.ids[0]))
        self.post("/sale/discount", dict(kind="fixed", value=".50"))
        self.post("/sale/notes", dict(notes="<Fictional pickup note>"))
        self.assertEqual(
            self.post("/sale/hold", dict(label="Fictional customer pickup")).status_code, 302
        )
        held = self.query("SELECT id FROM held_sales")[0]["id"]
        self.assertEqual(
            self.query("SELECT quantity FROM products WHERE id=?", (self.products[0],))[0][
                "quantity"
            ],
            20,
        )
        self.assertEqual(self.query("SELECT * FROM transactions"), [])
        with self.context():
            update_product_details(self.products[0], "Updated retail name", "2.00")
            adjust_stock(
                self.products[0],
                "2",
                "correction",
                "Fictional stock change",
                expected_quantity="20",
            )
        self.assertEqual(self.post(f"/pos/held/{held}/resume").status_code, 302)
        page = self.client.get("/sale")
        self.assertIn(b"Only 2 currently in stock", page.data)
        with self.client.session_transaction() as state:
            token = state["checkout_token"]
        self.post("/sale/checkout", dict(checkout_token=token, payment_method="Card"))
        self.assertEqual(self.query("SELECT * FROM transactions"), [])
        self.post(f"/sale/update/{self.products[0]}", dict(quantity=2))
        self.post(
            "/sale/checkout",
            dict(checkout_token=token, payment_method="Cash", cash_received="5.00"),
        )
        with self.client.session_transaction() as state:
            transaction = state["last_receipt_id"]
            self.assertNotIn("held_claim", state)
            self.assertNotIn("sale_notes", state)
        receipt = self.client.get(f"/transactions/{transaction}/receipt")
        self.assertIn(b"$3.79", receipt.data)
        self.assertIn(b"$1.21", receipt.data)
        self.assertIn(b"&lt;Fictional pickup note&gt;", receipt.data)
        self.assertNotIn(b"<Fictional pickup note>", receipt.data)
        with self.context():
            sale = get_sale(transaction)
            self.assertEqual(sale["transaction"]["customer_id"], self.ids[0])
            self.assertEqual(get_held(held)["state"], "completed")
            self.assertEqual(customer_history(self.ids[0])["total_cents"], 379)
            for _ in range(2):
                process_refund(
                    transaction,
                    {sale["items"][0]["id"]: 1},
                    "Fictional partial return",
                    True,
                    secrets.token_urlsafe(24),
                )
            self.assertEqual(get_sale(transaction)["refunded_cents"], 379)
        self.assertEqual(
            self.query("SELECT quantity FROM products WHERE id=?", (self.products[0],))[0][
                "quantity"
            ],
            2,
        )

    def test_simultaneous_resumes_and_checkout_complete_once(self):
        held = self.hold()

        def resume(_):
            with self.context():
                try:
                    return resume_sale(held)
                except ValueError:
                    return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(resume, range(2)))
        self.assertEqual(sum(claim is not None for claim in claims), 1)
        data = next(claim for claim in claims if claim)
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(lambda _: self.checkout_claim(data), range(2)))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(len(self.query("SELECT * FROM transactions")), 1)
        self.assertEqual(
            self.query("SELECT quantity FROM products WHERE id=?", (self.products[0],))[0][
                "quantity"
            ],
            18,
        )
        with self.context(), self.assertRaises(ValueError):
            resume_sale(held)

    def test_release_rehold_and_stale_claim_never_deduct_stock(self):
        held = self.hold()
        with self.context():
            first = resume_sale(held)
            update_held(held, "release")
            second = resume_sale(held)
        with self.assertRaises(ValueError):
            self.checkout_claim(first)
        with self.assertRaises(ValueError):
            self.checkout_claim(second, held_sale_id=None, held_claim=None)
        with self.context():
            reheld = hold_sale(
                second["cart"],
                second["customer_id"],
                second["discount"],
                second["notes"],
                "Reheld",
                second["checkout_token"],
                held_id=held,
                claim=second["claim"],
            )
            self.assertEqual(reheld, held)
            update_held(held, "rename", "Renamed sale")
            self.assertEqual(list_held()["rows"][0]["label"], "Renamed sale")
            update_held(held, "discard")
        with self.assertRaises(ValueError):
            self.checkout_claim(second)
        self.assertEqual(self.query("SELECT * FROM transactions"), [])
        self.assertEqual(
            self.query("SELECT quantity FROM products WHERE id=?", (self.products[0],))[0][
                "quantity"
            ],
            20,
        )

    def test_hold_idempotency_and_active_cart_not_overwritten(self):
        self.client.get("/sale")
        self.post("/sale/add", dict(product_id=self.products[0], quantity=1))
        with self.client.session_transaction() as state:
            token = state["checkout_token"]
        with self.context():
            first = hold_sale({str(self.products[0]): 1}, None, None, "", "Same", token)
            self.assertEqual(
                hold_sale({str(self.products[0]): 1}, None, None, "", "Same", token), first
            )
            with self.assertRaises(ValueError):
                hold_sale({str(self.products[0]): 2}, None, None, "", "Changed", token)
        self.assertEqual(self.post(f"/pos/held/{first}/resume").status_code, 400)
        self.assertEqual(len(self.query("SELECT * FROM held_sales")), 1)

    def test_three_account_layout_favorites_held_and_status_isolation(self):
        held = [self.hold(index) for index in range(3)]
        before = self.query("SELECT * FROM held_sales")
        for index, name in enumerate(("alpha", "beta", "gamma")):
            login(self.client, f"{name}@example.com")
            with self.context(index):
                save_layout(dict(defaults(), favorites=[self.products[index]]), 0)
                self.assertEqual(get_layout()["config"]["favorites"], [self.products[index]])
                for target in range(3):
                    if target == index:
                        continue
                    with self.assertRaises(LookupError):
                        resume_sale(held[target])
                    with self.assertRaises(LookupError):
                        update_held(held[target], "discard")
                    self.assertEqual(self.post(f"/pos/held/{held[target]}/resume").status_code, 404)
                    self.assertEqual(
                        self.post(
                            f"/pos/held/{held[target]}/manage", dict(action="release")
                        ).status_code,
                        404,
                    )
                    self.assertNotIn(
                        f"Product {target}".encode(), self.client.get("/sale?favorites=1").data
                    )
        self.assertEqual(self.query("SELECT * FROM held_sales"), before)

    def test_checkout_status_is_csrf_protected_scoped_and_authoritative(self):
        held = self.hold()
        with self.context():
            data = resume_sale(held)
        tx = self.checkout_claim(data)
        response = self.post(
            "/api/pos/checkout-status", dict(checkout_token=data["checkout_token"])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["state"], "completed")
        self.assertEqual(response.json["receipt_url"], f"/transactions/{tx}/receipt")
        self.assertEqual(app.test_client().post("/api/pos/checkout-status").status_code, 401)
        self.assertEqual(
            self.client.post(
                "/api/pos/checkout-status", data={"checkout_token": data["checkout_token"]}
            ).status_code,
            400,
        )
        login(self.client, "beta@example.com")
        response = self.post(
            "/api/pos/checkout-status", dict(checkout_token=data["checkout_token"])
        )
        self.assertEqual(response.json["state"], "not_found")
        self.assertNotIn("receipt_url", response.json)

    def test_notes_bounds_and_immutable_history(self):
        self.assertEqual(self.post("/sale/notes", dict(notes="x" * 501)).status_code, 400)
        self.assertEqual(self.post("/sale/notes", dict(notes="bad\x00")).status_code, 400)
        held = self.hold()
        with self.context():
            data = resume_sale(held)
        tx = self.checkout_claim(data)
        import sqlite3
        from contextlib import closing

        with closing(db.get_db_connection()) as conn, conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE transactions SET order_notes=? WHERE id=?", ("Changed", tx))

    def test_designer_forms_pages_reset_and_no_financial_side_effects(self):
        initial = self.query("SELECT * FROM products")
        config = dict(defaults(), preset="quick", favorites=[self.products[0]])
        response = self.post(
            "/pos/designer",
            dict(
                configuration=json.dumps(config),
                revision="0",
                action="save",
                preset="quick",
                density="large",
                show_images="1",
                show_categories="1",
            ),
        )
        self.assertEqual(response.status_code, 302)
        with self.context():
            self.assertEqual(get_layout()["config"]["preset"], "quick")
        for path in ("/pos/designer", "/pos/held", "/sale"):
            page = self.client.get(path)
            self.assertEqual(page.status_code, 200, path)
            document = Document(page.text)
            self.assertEqual(document.errors, [])
            self.assertEqual(document.stack, [])
            self.assertEqual(len(document.find("h1")), 1)
        self.assertEqual(
            self.post("/pos/designer", dict(action="reset", revision="1")).status_code, 302
        )
        self.assertEqual(self.query("SELECT * FROM products"), initial)
        self.assertEqual(self.query("SELECT * FROM transactions"), [])

    def test_cash_change_preview_uses_current_server_quote_without_writes(self):
        self.client.get("/sale")
        self.post("/sale/add", dict(product_id=self.products[0], quantity=3))
        self.post("/sale/discount", dict(kind="fixed", value=".50"))
        before = self.query("SELECT * FROM products")
        response = self.post("/api/pos/cash-quote", dict(cash_received="5.00"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["total_text"], "$2.71")
        self.assertEqual(response.json["change_text"], "$2.29")
        for value in ("1.00", "NaN", "-1", "5.001"):
            self.assertEqual(
                self.post("/api/pos/cash-quote", dict(cash_received=value)).status_code, 400
            )
        with self.context():
            update_product_details(self.products[0], "Changed price", "2")
        response = self.post("/api/pos/cash-quote", dict(cash_received="10.00"))
        self.assertEqual(response.json["total_text"], "$5.95")
        self.assertEqual(response.json["change_text"], "$4.05")
        self.assertEqual(self.query("SELECT * FROM transactions"), [])
        self.assertEqual(
            self.query("SELECT quantity FROM products WHERE id=?", (self.products[0],))[0][
                "quantity"
            ],
            before[0]["quantity"],
        )
        self.assertEqual(
            self.client.post("/api/pos/cash-quote", data={"cash_received": "10"}).status_code, 400
        )
        self.assertEqual(app.test_client().post("/api/pos/cash-quote").status_code, 401)

    def test_failed_pos_migration_rolls_back_ddl_and_history(self):
        from contextlib import closing
        from unittest.mock import patch

        from database.pos_migration import migrate_pos

        with closing(db.get_db_connection()) as conn, conn:
            conn.execute("DROP TABLE held_sales")
            conn.execute("DROP TABLE pos_layouts")
            conn.execute("DROP TRIGGER sale_notes_immutable")
            conn.execute("ALTER TABLE transactions DROP COLUMN order_notes")
            conn.execute("DELETE FROM schema_migrations WHERE version=27")
        before = self.query("SELECT * FROM products")

        def fail_after_ddl(conn):
            migrate_pos(conn)
            raise RuntimeError("Injected POS migration failure")

        with (
            patch("database.pos_migration.migrate_pos", side_effect=fail_after_ddl),
            self.assertRaises(RuntimeError),
        ):
            db.initialize_database(seed=False)
        self.assertEqual(
            self.query("SELECT name FROM sqlite_master WHERE name IN ('held_sales','pos_layouts')"),
            [],
        )
        self.assertEqual(self.query("SELECT version FROM schema_migrations WHERE version=27"), [])
        self.assertEqual(before, self.query("SELECT * FROM products"))
        db.initialize_database(seed=False)
        self.assertEqual(self.query("PRAGMA foreign_key_check"), [])

    def test_owner_permission_and_readonly_demo(self):
        from manage import seed_demo

        seed_demo(allow_existing=True)
        demo = app.test_client()
        demo.get("/login")
        with demo.session_transaction() as state:
            token = state["csrf_token"]
        demo.post("/demo", data={"csrf_token": token})
        for path in ("/sale", "/pos/designer", "/pos/held"):
            response = demo.get(path)
            self.assertEqual(response.status_code, 200)
            document = Document(response.text)
            actions = [
                form.attrs.get("action", path)
                for form in document.find("form")
                if form.attrs.get("method", "get").lower() == "post"
            ]
            self.assertEqual(actions, ["/logout"])
        with demo.session_transaction() as state:
            token = state["csrf_token"]
        for path in (
            "/pos/designer",
            "/sale/hold",
            "/sale/notes",
            "/pos/held/1/resume",
            "/api/pos/checkout-status",
            "/api/pos/cash-quote",
        ):
            self.assertEqual(demo.post(path, data={"csrf_token": token}).status_code, 403)
        with self.context():
            g.user = {"id": self.accounts[1][0]}
            with self.assertRaises(PermissionError):
                save_layout(defaults(), 0)

    def test_authorized_store_selection_rotates_login_and_clears_cart(self):
        from contextlib import closing

        # Explicit fictional second-store membership; registration never grants another store.
        with closing(db.get_db_connection()) as conn, conn:
            third = conn.execute(
                "INSERT INTO stores(name,kind) VALUES('Second authorized fictional store','private')"
            ).lastrowid
            conn.execute(
                "INSERT INTO store_memberships(user_id,store_id,role) VALUES(?,?,'owner')",
                (self.accounts[0][0], third),
            )
        self.client.get("/sale")
        self.post("/sale/add", dict(product_id=self.products[0], quantity=1))
        with self.client.session_transaction() as state:
            old = state["login_token"]
        self.assertEqual(self.post("/stores", dict(store_id=self.accounts[1][1])).status_code, 404)
        self.assertEqual(self.post("/stores", dict(store_id=third)).status_code, 302)
        self.client.get("/sale")
        with self.client.session_transaction() as state:
            self.assertNotEqual(state["login_token"], old)
            self.assertNotIn("sale_cart", state)
        self.assertNotIn(b"Product 0", self.client.get("/sale").data)
        from services.auth import load_identity

        self.assertIsNone(load_identity(old))

    def test_migration_idempotence_and_held_data_survive_reinitialization(self):
        held = self.hold()
        before = {
            table: self.query(f"SELECT * FROM {table}")
            for table in ("products", "customers", "held_sales", "transactions")
        }
        db.initialize_database(seed=False)
        db.initialize_database(seed=False)
        for table, rows in before.items():
            self.assertEqual(rows, self.query(f"SELECT * FROM {table}"))
        with self.context():
            self.assertEqual(get_held(held)["state"], "held")
        self.assertEqual(self.query("PRAGMA integrity_check"), [{"integrity_check": "ok"}])
        self.assertEqual(self.query("PRAGMA foreign_key_check"), [])
