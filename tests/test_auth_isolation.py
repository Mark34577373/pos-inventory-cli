"""Day 14: real auth flows and hostile cross-store requests on disposable data."""

import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from auth_helpers import PASSWORD, login
from flask import g
from werkzeug.security import check_password_hash

from config import TAX_RATE, business_now
from database import database as db
from database.tenancy import current_store_id
from manage import seed_demo
from services.auth import assign_legacy_owner, create_login_session, load_identity, register_user
from services.inventory import (
    create_product,
    deactivate_product,
    find_product_by_id,
    find_products_by_ids,
    list_active_products,
    remove_product_by_name,
    search_active_products,
    set_product_quantity,
    update_product_details,
    update_product_record,
)
from services.money import calculate_tax_cents, from_cents
from services.transaction_service import complete_transaction
from ui.dashboard import get_dashboard_data
from ui.reports_view import get_today_sales_summary
from web_app import app, get_transactions_for_period


class AuthIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = db.DB_FILE
        self.original_config = app.config.copy()
        db.DB_FILE = str(Path(self.tmp.name) / "auth.db")
        db.initialize_database()
        app.config.update(TESTING=True, DEMO_READ_ONLY=False)
        self.client = app.test_client()

    def tearDown(self):
        app.config.clear()
        app.config.update(self.original_config)
        db.DB_FILE = self.original_db
        self.tmp.cleanup()

    def query(self, statement, params=()):
        with closing(db.get_db_connection()) as conn:
            return [dict(row) for row in conn.execute(statement, params)]

    def post(self, path, data=None, client=None, follow=False):
        client = client or self.client
        client.get("/login")
        with client.session_transaction() as state:
            token = state["csrf_token"]
        return client.post(path, data=dict(data or {}, csrf_token=token), follow_redirects=follow)

    def register(self, email="owner@example.com", name="Private store"):
        return self.post(
            "/register",
            {
                "email": email,
                "password": PASSWORD,
                "password_confirm": PASSWORD,
                "store_name": name,
            },
        )

    def setup_two_stores(self):
        self.a, self.store_a = register_user("a@example.com", PASSWORD, PASSWORD, "Alpha Private")
        self.b, self.store_b = register_user("b@example.com", PASSWORD, PASSWORD, "Bravo Private")
        with app.test_request_context("/"):
            g.store = {"id": self.store_b}
            g.is_demo = False
            self.product_b = create_product("Bravo Secret Product", "39.99", 3)
            self.total_b = 3999 + calculate_tax_cents(3999, TAX_RATE)
            self.tx_b = complete_transaction(
                [{"id": self.product_b, "qty": 1}],
                from_cents(self.total_b),
                "Card",
                checkout_token="bravo-token",
            )
        with app.test_request_context("/"):
            g.store = {"id": self.store_a}
            g.is_demo = False
            self.product_a = create_product("Alpha Product", "2", 5)
        login(self.client, "a@example.com")

    def test_registration_hashing_atomic_empty_store_and_email_normalization(self):
        self.assertEqual(self.register("  OWNER@EXAMPLE.COM  ").status_code, 302)
        users = self.query("SELECT * FROM users")
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["email"], "owner@example.com")
        self.assertNotEqual(users[0]["password_hash"], PASSWORD)
        self.assertTrue(users[0]["password_hash"].startswith("scrypt:32768:8:3$"))
        self.assertTrue(check_password_hash(users[0]["password_hash"], PASSWORD))
        store = self.query("SELECT store_id FROM store_memberships")[0]["store_id"]
        self.assertNotEqual(store, 1)
        self.assertFalse(self.query("SELECT * FROM products WHERE store_id=?", (store,)))
        self.assertEqual(len(self.query("SELECT * FROM products WHERE store_id=1")), 2)
        self.assertEqual(self.register("OWNER@example.com").status_code, 400)
        self.assertEqual(len(self.query("SELECT * FROM users")), 1)
        self.assertEqual(len(self.query("SELECT * FROM stores WHERE kind='private'")), 1)

    def test_registration_invalid_fields_and_csrf(self):
        self.assertEqual(self.client.post("/register", data={}).status_code, 400)
        cases = [
            dict(email="bad"),
            dict(email="x" * 255 + "@example.com"),
            dict(password="short", password_confirm="short"),
            dict(password_confirm="different"),
            dict(store_name="x" * 101),
            dict(password="x" * 129, password_confirm="x" * 129),
        ]
        for delta in cases:
            values = dict(
                email="valid@example.com",
                password=PASSWORD,
                password_confirm=PASSWORD,
                store_name="Store",
            )
            values.update(delta)
            with self.subTest(delta=list(delta)):
                self.assertEqual(self.post("/register", values).status_code, 400)
        self.assertFalse(self.query("SELECT * FROM users"))
        self.assertEqual(len(self.query("SELECT * FROM stores")), 1)
        self.assertEqual(self.register(name="<script>fictional</script>").status_code, 302)
        login(self.client, "owner@example.com")
        self.assertNotIn(b"<script>fictional</script>", self.client.get("/account").data)

    def test_concurrent_registration_uniqueness(self):
        def register(_):
            try:
                return register_user("race@example.com", PASSWORD, PASSWORD)[0]
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(register, range(2)))
        self.assertEqual(sum(item is not None for item in ids), 1)
        self.assertEqual(len(self.query("SELECT * FROM users")), 1)
        self.assertEqual(len(self.query("SELECT * FROM store_memberships")), 1)

    def test_atomic_registration_rolls_back_user_on_store_failure(self):
        with closing(db.get_db_connection()) as conn:
            conn.execute(
                "CREATE TRIGGER reject_store BEFORE INSERT ON stores BEGIN SELECT RAISE(ABORT,'test failure'); END"
            )
        with self.assertRaises(ValueError):
            register_user("fail@example.com", PASSWORD, PASSWORD)
        self.assertFalse(self.query("SELECT * FROM users"))

    def test_login_wrong_password_generic_and_rate_limit_persists(self):
        register_user("owner@example.com", PASSWORD, PASSWORD)
        for index in range(10):
            response = self.post("/login", {"email": "owner@example.com", "password": "wrong"})
            self.assertEqual(response.status_code, 400)
            self.assertIn(b"Invalid email or password", response.data)
        second_client = app.test_client()
        response = self.post(
            "/login", {"email": "OWNER@EXAMPLE.COM", "password": PASSWORD}, client=second_client
        )
        self.assertEqual(response.status_code, 429)
        self.assertFalse(self.query("SELECT * FROM auth_sessions"))
        buckets = self.query("SELECT bucket FROM auth_limits")
        self.assertTrue(
            all(
                "owner" not in row["bucket"] and "127.0.0.1" not in row["bucket"] for row in buckets
            )
        )
        with patch("services.auth.time.time", return_value=time.time() + 901):
            self.assertEqual(
                self.post(
                    "/login", {"email": "owner@example.com", "password": PASSWORD}
                ).status_code,
                302,
            )

    def test_unknown_user_and_bad_password_have_same_error(self):
        register_user("owner@example.com", PASSWORD, PASSWORD)
        for email in ("owner@example.com", "unknown@example.com", "invalid"):
            response = self.post("/login", dict(email=email, password="wrong"))
            self.assertEqual(response.status_code, 400)
            self.assertIn(b"Invalid email or password", response.data)
            self.assertNotIn(b"password_hash", response.data)

    def test_csrf_all_auth_mutations_and_get_logout_not_allowed(self):
        for path in ("/register", "/login", "/logout", "/demo"):
            self.assertEqual(self.client.post(path, data={}).status_code, 400)
        self.assertEqual(self.client.get("/logout").status_code, 405)

    def test_login_rotates_session_logout_revokes_replay_and_expiry(self):
        user, _ = register_user("owner@example.com", PASSWORD, PASSWORD)
        self.client.get("/login")
        with self.client.session_transaction() as state:
            state["sale_cart"] = {"1": 1}
            state["last_receipt_id"] = 1
            old_csrf = state["csrf_token"]
        login(self.client, "owner@example.com")
        with self.client.session_transaction() as state:
            token = state["login_token"]
            self.assertNotEqual(state["csrf_token"], old_csrf)
            self.assertNotIn("sale_cart", state)
            self.assertNotIn("last_receipt_id", state)
            state_copy = dict(state)
        stored = self.query("SELECT * FROM auth_sessions")[0]
        self.assertNotEqual(stored["token_hash"], token)
        self.assertNotIn("password", str(state_copy))
        self.assertEqual(self.post("/logout").status_code, 302)
        self.assertIsNone(load_identity(token))
        replay = app.test_client()
        with replay.session_transaction() as state:
            state.update(state_copy)
        self.assertEqual(replay.get("/").status_code, 302)
        token = create_login_session(user)
        with closing(db.get_db_connection()) as conn:
            with conn:
                conn.execute("UPDATE auth_sessions SET expires_at=0")
        self.assertIsNone(load_identity(token))

    def test_local_next_only(self):
        register_user("owner@example.com", PASSWORD, PASSWORD)
        values = (
            "https://evil.example",
            "//evil.example",
            "/\\evil.example",
            "/%2f%2fevil.example",
            "//[",
            "/logout",
            "/unknown",
            "/login",
            "/inventory\r\nX: bad",
        )
        for value in values:
            response = self.post(
                "/login", dict(email="owner@example.com", password=PASSWORD, next=value)
            )
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.location, "/")
        response = self.post(
            "/login", dict(email="owner@example.com", password=PASSWORD, next="/inventory?q=abc")
        )
        self.assertEqual(response.location, "/inventory?q=abc")

    def test_all_private_routes_block_anonymous_without_side_effects(self):
        for rule in app.url_map.iter_rules():
            if (
                rule.endpoint.startswith("auth.")
                and rule.endpoint != "auth.account"
                or rule.endpoint == "static"
            ):
                continue
            path = rule.rule.replace("<int:product_id>", "1").replace("<int:transaction_id>", "1").replace("<int:category_id>", "1").replace("<int:adjustment_id>", "1")
            if "GET" in rule.methods:
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 302)
                    self.assertIn("/login", response.location)
            if "POST" in rule.methods:
                self.assertEqual(self.client.post(path, data={"quantity": 0}).status_code, 302)
        self.assertFalse(self.query("SELECT * FROM transactions"))
        self.assertEqual(self.query("SELECT quantity FROM products WHERE id=1")[0]["quantity"], 5)

    def test_cross_store_product_idor_every_operation_and_mass_assignment(self):
        self.setup_two_stores()
        before = self.query("SELECT * FROM products WHERE id=?", (self.product_b,))[0]
        for path, data in (
            (f"/products/{self.product_b}/edit", dict(name="stolen", price="1", quantity=0)),
            (f"/products/{self.product_b}/quantity", dict(quantity=0)),
            (f"/products/{self.product_b}/deactivate", {}),
            ("/sale/add", dict(product_id=self.product_b, quantity=1)),
        ):
            response = self.post(path, data, follow=True)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(b"Bravo Secret Product", response.data)
        self.assertNotIn(
            b"Bravo Secret Product",
            self.client.get(f"/products/{self.product_b}/edit", follow_redirects=True).data,
        )
        self.assertEqual(
            self.query("SELECT * FROM products WHERE id=?", (self.product_b,))[0], before
        )
        self.post(
            "/products/new",
            dict(name="Own new", price="1", quantity=2, store_id=self.store_b, user_id=self.b),
        )
        self.assertEqual(
            self.query("SELECT store_id FROM products WHERE name='Own new'")[0]["store_id"],
            self.store_a,
        )

    def test_cross_store_transaction_receipt_dashboard_reports_search(self):
        self.setup_two_stores()
        for path in (
            "/",
            "/inventory",
            "/inventory?q=Bravo",
            "/sale?q=Bravo",
            "/transactions",
            "/transactions?q=" + str(self.tx_b),
            "/reports",
            f"/transactions/{self.tx_b}",
        ):
            response = self.client.get(path, follow_redirects=True)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(b"Bravo Secret Product", response.data)
            self.assertNotIn(b"Bravo Private", response.data)
            self.assertNotIn(b"43.29", response.data)
        with self.client.session_transaction() as state:
            state["last_receipt_id"] = self.tx_b
        self.assertEqual(self.client.get("/receipt").status_code, 302)
        with patch("web_app.render_template", return_value="ok") as render:
            self.client.get("/")
        data = render.call_args.kwargs
        self.assertEqual(data["today_sales_cents"], 0)
        self.assertFalse(data["recent_transactions"])
        self.assertEqual(sum(day["sales_cents"] for day in data["sales_chart"]), 0)
        with patch("web_app.render_template", return_value="ok") as render:
            self.client.get("/reports")
        self.assertEqual(render.call_args.kwargs["overview"]["revenue_cents"], 0)
        self.assertFalse(render.call_args.kwargs["top_product_rows"])

    def test_cross_store_cart_injection_checkout_cannot_deduct(self):
        self.setup_two_stores()
        self.client.get("/sale")
        with self.client.session_transaction() as state:
            state["sale_cart"] = {str(self.product_b): 1}
            checkout = state["checkout_token"]
        response = self.post(
            "/sale/checkout", dict(payment_method="Card", checkout_token=checkout), follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.query("SELECT quantity FROM products WHERE id=?", (self.product_b,))[0][
                "quantity"
            ],
            2,
        )
        self.assertFalse(self.query("SELECT * FROM transactions WHERE store_id=?", (self.store_a,)))

    def test_service_boundary_isolation_and_cross_store_idempotency(self):
        self.setup_two_stores()
        with app.test_request_context("/"):
            g.store = {"id": self.store_a}
            g.is_demo = False
            self.assertIsNone(find_product_by_id(self.product_b))
            self.assertFalse(find_products_by_ids([self.product_b]))
            self.assertFalse(search_active_products("Bravo"))
            self.assertEqual(len(list_active_products()), 1)
            for function, args in (
                (set_product_quantity, (self.product_b, 0)),
                (update_product_details, (self.product_b, "stolen", "1")),
                (update_product_record, (self.product_b, "stolen", "1", 0)),
                (deactivate_product, (self.product_b,)),
                (remove_product_by_name, ("Bravo Secret Product",)),
            ):
                with self.assertRaises(ValueError):
                    function(*args)
            with self.assertRaises(ValueError):
                complete_transaction(
                    [{"id": self.product_a, "qty": 1}, {"id": self.product_b, "qty": 1}],
                    from_cents(self.total_b),
                    "Card",
                )
            self.assertEqual(find_product_by_id(self.product_a)["quantity"], 5)
            # Reusing another store's token must never return its transaction ID.
            # A conflict fails atomically; it does not reveal a receipt or deduct.
            with self.assertRaises(sqlite3.IntegrityError):
                total = 200 + calculate_tax_cents(200, TAX_RATE)
                complete_transaction(
                    [{"id": self.product_a, "qty": 1}],
                    from_cents(total),
                    "Card",
                    checkout_token="bravo-token",
                )
            self.assertEqual(find_product_by_id(self.product_a)["quantity"], 5)
            self.assertFalse(
                get_transactions_for_period(business_now().date(), business_now().date())
            )
            self.assertEqual(get_dashboard_data()["daily_sales"]["total_revenue_cents"], 0)
            self.assertEqual(get_today_sales_summary()["total_revenue_cents"], 0)
        with app.test_request_context("/"):
            for function, args in (
                (list_active_products, ()),
                (create_product, ("bad", "1", 1)),
                (complete_transaction, ([{"id": 1, "qty": 1}], "1", "Card")),
            ):
                with self.assertRaises(PermissionError):
                    function(*args)
            with self.assertRaises(PermissionError):
                current_store_id()

    def test_switching_users_invalidates_cart_and_old_session(self):
        self.setup_two_stores()
        self.client.get("/sale")
        self.post("/sale/add", dict(product_id=self.product_a, quantity=1))
        with self.client.session_transaction() as state:
            old_token = state["login_token"]
            self.assertTrue(state["sale_cart"])
        login(self.client, "b@example.com")
        self.assertIsNone(load_identity(old_token))
        with self.client.session_transaction() as state:
            self.assertNotIn("sale_cart", state)
            self.assertEqual(state["cart_context"], f"{self.b}:{self.store_b}")
        self.assertIn(b"Bravo Secret Product", self.client.get("/inventory").data)
        self.assertNotIn(b"Alpha Product", self.client.get("/inventory").data)

    def test_database_cross_store_constraints_and_immutable_ownership(self):
        self.setup_two_stores()
        statements = [
            ("UPDATE products SET store_id=? WHERE id=?", (self.store_a, self.product_b)),
            ("UPDATE transactions SET store_id=? WHERE id=?", (self.store_a, self.tx_b)),
            ("UPDATE products SET store_id=NULL WHERE id=?", (self.product_a,)),
            (
                "INSERT INTO transaction_items(transaction_id,product_id,quantity,price,price_cents,store_id) VALUES (?,?,1,2,200,?)",
                (self.tx_b, self.product_a, self.store_a),
            ),
            (
                "INSERT INTO products(name,price,price_cents,quantity,store_id) VALUES ('bad',1,100,1,99999)",
                (),
            ),
        ]
        for statement, params in statements:
            with closing(db.get_db_connection()) as conn:
                with self.assertRaises(sqlite3.IntegrityError):
                    with conn:
                        conn.execute(statement, params)
        with closing(db.get_db_connection()) as conn:
            self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())

    def test_demo_is_separate_read_only_and_cannot_reach_private_accounts(self):
        self.setup_two_stores()
        seed_demo(allow_existing=True)
        response = self.post("/demo", follow=True)
        self.assertIn(b"Demo Notebook", response.data)
        self.assertNotIn(b"Alpha Product", response.data)
        with self.client.session_transaction() as state:
            self.assertNotIn("login_token", state)
            self.assertTrue(state["demo_mode"])
            state["last_receipt_id"] = self.tx_b
        self.assertEqual(self.client.get("/receipt").status_code, 302)
        self.assertEqual(self.client.get("/account").status_code, 302)
        before = self.query("SELECT * FROM products ORDER BY id")
        for path in (
            "/products/new",
            f"/products/{self.product_b}/quantity",
            "/sale/add",
            "/sale/cancel",
            "/sale/checkout",
        ):
            self.assertEqual(
                self.post(
                    path, dict(product_id=self.product_b, name="bad", price="1", quantity=0)
                ).status_code,
                403,
            )
        self.assertEqual(before, self.query("SELECT * FROM products ORDER BY id"))
        with app.test_request_context("/"):
            g.store = {"id": self.query("SELECT id FROM stores WHERE kind='demo'")[0]["id"]}
            g.is_demo = True
            with self.assertRaises(PermissionError):
                create_product("bad", "1", 1)
        login(self.client, "a@example.com")
        self.assertNotIn(b"Demo Notebook", self.client.get("/inventory").data)

    def test_legacy_assignment_is_explicit_and_idempotent_migration_preserves_data(self):
        before = self.query("SELECT * FROM products ORDER BY id")
        user, store = register_user("owner@example.com", PASSWORD, PASSWORD)
        self.assertNotEqual(store, 1)
        db.initialize_database(seed=False)
        db.initialize_database(seed=False)
        self.assertEqual(before, self.query("SELECT * FROM products ORDER BY id"))
        self.assertEqual(self.query("SELECT version FROM schema_migrations"), [{"version": 14}, {"version": 15}])
        login(self.client, "owner@example.com")
        self.assertNotIn(b"Mouse", self.client.get("/inventory").data)
        assign_legacy_owner("owner@example.com")
        self.assertEqual(self.client.get("/inventory").status_code, 302)
        login(self.client, "owner@example.com")
        self.assertIn(b"Mouse", self.client.get("/inventory").data)
        with self.assertRaises(ValueError):
            assign_legacy_owner("owner@example.com")
        self.assertEqual(before, self.query("SELECT * FROM products ORDER BY id"))

    def test_private_store_web_checkout_and_concurrent_stock_are_atomic(self):
        self.setup_two_stores()
        self.client.get("/sale")
        self.post("/sale/add", {"product_id": self.product_a, "quantity": 1})
        with self.client.session_transaction() as state:
            checkout = state["checkout_token"]
        response = self.post(
            "/sale/checkout",
            {"payment_method": "Cash", "cash_received": "5.00", "checkout_token": checkout},
            follow=True,
        )
        self.assertIn(b"Alpha Product", response.data)
        self.assertNotIn(b"Bravo Secret Product", response.data)
        row = self.query("SELECT * FROM transactions WHERE store_id=?", (self.store_a,))[0]
        self.assertEqual(row["total_cents"], 217)
        self.assertEqual(row["cash_received_cents"], 500)
        self.assertEqual(
            self.query(
                "SELECT store_id FROM transaction_items WHERE transaction_id=?", (row["id"],)
            ),
            [{"store_id": self.store_a}],
        )
        with app.test_request_context("/"):
            g.store = {"id": self.store_a}
            g.is_demo = False
            set_product_quantity(self.product_a, 1)

        def buy(index):
            with app.test_request_context("/"):
                g.store = {"id": self.store_a}
                g.is_demo = False
                try:
                    complete_transaction(
                        [{"id": self.product_a, "qty": 1}],
                        "2.17",
                        "Card",
                        checkout_token=f"alpha-race-{index}",
                    )
                    return True
                except ValueError:
                    return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(buy, range(2)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(
            self.query("SELECT quantity FROM products WHERE id=?", (self.product_a,))[0][
                "quantity"
            ],
            0,
        )
        self.assertEqual(
            self.query("SELECT quantity FROM products WHERE id=?", (self.product_b,))[0][
                "quantity"
            ],
            2,
        )

    def test_production_requires_migrated_database_without_creating_one(self):
        import os
        import subprocess
        import sys

        target = str(Path(self.tmp.name) / "not-created.db")
        env = dict(
            os.environ,
            TCCP_ENV="production",
            TCCP_DB_PATH=target,
            TCCP_SECRET_KEY="fictional-test-secret-" * 3,
        )
        result = subprocess.run(
            [sys.executable, "-B", "-c", "import web_app"], env=env, capture_output=True
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(Path(target).exists())
        self.assertIn(b"existing database migrated", result.stderr)

    def test_security_headers_and_password_fields(self):
        response = self.client.get("/register")
        for header in ("Content-Security-Policy", "X-Frame-Options", "X-Content-Type-Options"):
            self.assertIn(header, response.headers)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(response.data.count(b'type="password"'), 2)
        self.assertNotIn(PASSWORD.encode(), response.data)
        self.assertNotIn(b"password_hash", response.data)
