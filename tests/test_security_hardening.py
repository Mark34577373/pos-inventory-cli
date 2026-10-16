"""Day 19: adversarial HTTP and real SQLite maintenance/security checks."""

import os
import secrets
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

from auth_helpers import PASSWORD, login
from flask import g
from werkzeug.datastructures import MultiDict

from database import database as db
from database.analytics_migration import migrate_analytics
from database.inventory_migration import migrate_inventory
from database.migrations import migrate_identity
from database.validation import validate_integrity, validate_schema
from manage import backup_database, check_database, repair_missing_products, seed_demo
from services.auth import register_user
from services.inventory import create_product, find_product_by_id
from services.inventory_management import adjust_stock, positive_id, save_category
from services.refunds import get_sale, process_refund
from services.transaction_service import complete_transaction
from web_app import app


class SecurityHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = db.DB_FILE
        self.original_config = app.config.copy()
        db.DB_FILE = str(Path(self.tmp.name) / "security.db")
        db.initialize_database(seed=False)
        app.config.update(TESTING=True, DEMO_READ_ONLY=False)
        self.user, self.store = register_user(
            "security-alpha@example.com", PASSWORD, PASSWORD, "Alpha private store"
        )
        with self.context():
            self.category = save_category("Alpha category")
            self.product = create_product(
                "Alpha secret product", "1.00", 10, category_id=self.category
            )
        self.client = app.test_client()
        login(self.client, "security-alpha@example.com")
        self.client.get("/sale")
        self.post("/sale/add", {"product_id": self.product, "quantity": 2})
        with self.client.session_transaction() as state:
            token = state["checkout_token"]
        self.post("/sale/checkout", {"payment_method": "Card", "checkout_token": token})
        with self.client.session_transaction() as state:
            self.tx = state["last_receipt_id"]
        with self.context():
            self.line = get_sale(self.tx)["items"][0]["id"]

    def tearDown(self):
        db.DB_FILE = self.original_db
        app.config.clear()
        app.config.update(self.original_config)
        self.tmp.cleanup()

    @contextmanager
    def context(self, user=None, store=None):
        with app.test_request_context("/"):
            g.user = {"id": self.user if user is None else user}
            g.store = {"id": self.store if store is None else store}
            g.is_demo = False
            yield

    def csrf(self, client=None):
        with (client or self.client).session_transaction() as state:
            return state["csrf_token"]

    def post(self, path, data=None, client=None, **kwargs):
        client = client or self.client
        return client.post(path, data=dict(data or {}, csrf_token=self.csrf(client)), **kwargs)

    def query(self, sql, values=()):
        with closing(db.get_db_connection()) as conn:
            return [dict(row) for row in conn.execute(sql, values)]

    def test_missing_table_rejected_without_silent_recreation(self):
        with closing(db.get_db_connection()) as conn:
            conn.execute("DROP TABLE auth_limits")
        before = self.query("SELECT * FROM schema_migrations")
        with self.assertRaises(ValueError):
            db.initialize_database(seed=False)
        self.assertEqual(before, self.query("SELECT * FROM schema_migrations"))
        self.assertFalse(self.query("SELECT name FROM sqlite_master WHERE name='auth_limits'"))

    def test_missing_security_trigger_is_not_silently_accepted(self):
        with closing(db.get_db_connection()) as conn:
            conn.execute("DROP TRIGGER refund_amount_cap")
        with self.assertRaises(ValueError):
            db.initialize_database(seed=False)
        self.assertFalse(
            self.query("SELECT name FROM sqlite_master WHERE name='refund_amount_cap'")
        )

    def test_unknown_or_gapped_migration_markers_fail_closed(self):
        with closing(db.get_db_connection()) as conn:
            with conn:
                conn.execute("INSERT INTO schema_migrations(version) VALUES(999)")
        with self.assertRaises(ValueError):
            db.initialize_database(seed=False)
        with closing(db.get_db_connection()) as conn:
            with conn:
                conn.execute("DELETE FROM schema_migrations WHERE version IN (999,15)")
        with self.assertRaises(ValueError):
            db.initialize_database(seed=False)

    def test_failed_migration_rolls_back_ddl_and_version_record(self):
        original = db.DB_FILE
        db.DB_FILE = str(Path(self.tmp.name) / "pre17.db")
        try:
            with closing(db.get_db_connection()) as conn:
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    db._initialize_schema(conn, True)
                    migrate_identity(conn)
                    migrate_inventory(conn)
                    migrate_analytics(conn)
            before = self.query("SELECT * FROM products")
            from database.refund_migration import migrate_refunds

            def fail_after_ddl(conn):
                migrate_refunds(conn)
                raise RuntimeError("Injected migration failure")

            with (
                patch("database.refund_migration.migrate_refunds", side_effect=fail_after_ddl),
                self.assertRaises(RuntimeError),
            ):
                db.initialize_database(seed=False)
            self.assertEqual(before, self.query("SELECT * FROM products"))
            self.assertFalse(self.query("SELECT name FROM sqlite_master WHERE name='refunds'"))
            self.assertEqual(
                [row["version"] for row in self.query("SELECT version FROM schema_migrations")],
                [14, 15, 16],
            )
            db.initialize_database(seed=False)
            db.initialize_database(seed=False)
            self.assertEqual(before, self.query("SELECT * FROM products"))
            self.assertTrue(check_database()[0])
        finally:
            db.DB_FILE = original

    def test_every_application_connection_enforces_foreign_keys_and_timeout(self):
        for _ in range(2):
            with closing(db.get_db_connection()) as conn:
                self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
                with self.assertRaises(sqlite3.IntegrityError):
                    with conn:
                        conn.execute("INSERT INTO store_memberships VALUES(999999,999999,'owner')")

    def test_orphans_fail_migration_and_explicit_product_repair_preserves_items(self):
        before = self.query("SELECT * FROM transaction_items")
        with closing(sqlite3.connect(db.DB_FILE)) as raw:
            with raw:
                raw.execute("DELETE FROM products WHERE id=?", (self.product,))
        with self.assertRaises(ValueError):
            db.initialize_database(seed=False)
        self.assertFalse(check_database()[0])
        db.initialize_database(seed=False, allow_missing_products=True)
        self.assertEqual(repair_missing_products(), 1)
        self.assertEqual(before, self.query("SELECT * FROM transaction_items"))
        self.assertTrue(check_database()[0])

    def test_migration_cannot_run_inside_http_request(self):
        before = self.query("SELECT * FROM products")
        with self.context(), self.assertRaises(PermissionError):
            db.initialize_database()
        self.assertEqual(before, self.query("SELECT * FROM products"))

    def test_private_database_and_backup_creation_and_restore_to_new_path(self):
        backup = backup_database(Path(self.tmp.name) / "backup.db")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(Path(db.DB_FILE).stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        target = Path(self.tmp.name) / "restored.db"
        env = dict(os.environ, TCCP_DB_PATH=str(target))
        result = subprocess.run(
            [sys.executable, "manage.py", "restore", "--file", str(backup)],
            env=env,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertTrue(check_database(target)[0])
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        # Existing targets must never be overwritten, including at the OS level.
        before = target.read_bytes()
        result = subprocess.run(
            [sys.executable, "manage.py", "restore", "--file", str(backup)],
            env=env,
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, target.read_bytes())

    def test_json_and_unexpected_types_rejected_before_mutations(self):
        before = self.query("SELECT * FROM products")
        for path in (
            "/login",
            "/register",
            "/products/new",
            "/sale/add",
            f"/transactions/{self.tx}/refund",
        ):
            for body, content_type in (
                ("{bad json", "application/json"),
                ("[1,true,null]", "application/json"),
                ("name=No", "text/plain"),
            ):
                response = self.client.post(
                    path,
                    data=body,
                    content_type=content_type,
                    headers={"X-CSRF-Token": self.csrf()},
                )
                self.assertEqual(response.status_code, 415, (path, body))
                self.assertNotIn(b"Traceback", response.data)
        self.assertEqual(before, self.query("SELECT * FROM products"))

    def test_duplicate_fields_and_filters_rejected_without_side_effects(self):
        data = MultiDict([("csrf_token", self.csrf()), ("name", "First"), ("name", "Second")])
        self.assertEqual(self.client.post("/inventory/categories", data=data).status_code, 400)
        data = MultiDict([("csrf_token", self.csrf()), ("csrf_token", "wrong")])
        self.assertEqual(self.client.post("/sale/cancel", data=data).status_code, 400)
        self.assertEqual(self.client.get("/inventory?q=one&q=two").status_code, 400)
        self.assertEqual(len(self.query("SELECT * FROM categories")), 1)

    def test_all_unsafe_methods_require_csrf_before_any_side_effect(self):
        before = self.query("SELECT * FROM products")
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            response = self.client.open("/sale/cancel", method=method)
            self.assertEqual(response.status_code, 400, method)
        self.assertEqual(before, self.query("SELECT * FROM products"))

    def test_header_csrf_supported_and_conflicting_tokens_rejected(self):
        response = self.client.post(
            "/inventory/categories",
            data={"name": "Header token category"},
            headers={"X-CSRF-Token": self.csrf()},
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            "/inventory/categories",
            data={"name": "Rejected", "csrf_token": self.csrf()},
            headers={"X-CSRF-Token": "wrong"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.query("SELECT * FROM categories WHERE name='Rejected'"))

    def test_body_and_query_limits_fail_safely(self):
        self.assertEqual(
            self.post(
                "/products/new", {"name": "x" * 70000, "price": "1", "quantity": 1}
            ).status_code,
            413,
        )
        self.assertEqual(self.client.get("/inventory?q=" + "x" * 8193).status_code, 414)
        self.assertEqual(len(self.query("SELECT * FROM products")), 1)

    def test_strict_identifier_quantity_and_control_character_validation(self):
        for value in ("1_0", "+1", "1.0", "NaN", "9" * 100):
            with self.assertRaises(ValueError):
                positive_id(value)
        with self.context():
            self.assertIsNone(find_product_by_id(True))
            for name in ("\x00bad", "bad\x1bname"):
                with self.assertRaises(ValueError):
                    create_product(name, "1.00", 1)
            with self.assertRaises(ValueError):
                adjust_stock(self.product, 1, "restock", "bad\x00reason")
        before = self.query("SELECT quantity FROM products")
        for value in ("1_0", "+1", "1.1", "1000001", "-1"):
            self.post("/sale/add", {"product_id": self.product, "quantity": value})
        self.assertEqual(before, self.query("SELECT quantity FROM products"))
        with self.client.session_transaction() as state:
            self.assertFalse(state.get("sale_cart"))

    def test_strict_script_csp_and_external_print_handlers(self):
        for path in ("/", f"/transactions/{self.tx}/receipt", "/analytics"):
            response = self.client.get(path)
            csp = response.headers["Content-Security-Policy"]
            script = next(
                part.strip() for part in csp.split(";") if part.strip().startswith("script-src ")
            )
            self.assertEqual(script, "script-src 'self'")
            self.assertIn("script-src-attr 'none'", csp)
            self.assertIn("frame-ancestors 'none'", csp)
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertIn("payment=()", response.headers["Permissions-Policy"])
            self.assertNotIn("onclick=", response.text)
            self.assertNotIn("<script>", response.text)
        self.assertIn("data-print", self.client.get(f"/transactions/{self.tx}/receipt").text)

    def test_injection_strings_do_not_execute_sql_or_html(self):
        payload = "<img src=x onerror=alert(1)>"
        self.post("/products/new", {"name": payload, "price": "1.00", "quantity": 2})
        self.post("/inventory/categories", {"name": payload})
        for path in ("/inventory", "/inventory/categories", "/sale"):
            response = self.client.get(path)
            self.assertNotIn(payload, response.text)
            self.assertIn("&lt;img", response.text)
        for path in (
            "/inventory?q=%27%20OR%201=1--",
            "/transactions?sort=total;DROP%20TABLE%20users",
            "/analytics?group=DROP%20TABLE%20users",
        ):
            self.assertIn(self.client.get(path).status_code, (200, 400))
        self.assertEqual(len(self.query("SELECT * FROM users")), 1)
        with self.context():
            refund = process_refund(
                self.tx, {self.line: 1}, payload, False, secrets.token_urlsafe(32)
            )
        self.assertNotIn(payload, self.client.get(f"/refunds/{refund}").text)
        self.assertIn("&lt;img", self.client.get(f"/refunds/{refund}").text)
        self.assertTrue(check_database()[0])

    def test_production_host_cookie_schema_and_private_file_checks(self):
        env = dict(
            os.environ,
            TCCP_ENV="production",
            TCCP_DB_PATH=db.DB_FILE,
            TCCP_SECRET_KEY="fictional-security-test-secret-" * 2,
            TCCP_TRUSTED_HOSTS="localhost,good.example",
        )
        code = """from web_app import app
assert app.config['SESSION_COOKIE_SECURE'] and app.config['SESSION_COOKIE_HTTPONLY']
assert app.config['SESSION_COOKIE_SAMESITE']=='Lax' and not app.debug
client=app.test_client()
assert client.get('/login',base_url='https://good.example').status_code==200
assert client.get('/login',base_url='https://evil.example').status_code==400
assert 'max-age=' in client.get('/login',base_url='https://good.example').headers['Strict-Transport-Security']
"""
        result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        env["TCCP_SECRET_KEY"] = " " * 40
        result = subprocess.run(
            [sys.executable, "-c", "import web_app"], env=env, capture_output=True
        )
        self.assertNotEqual(result.returncode, 0)
        if os.name == "posix":
            env["TCCP_SECRET_KEY"] = "fictional-security-test-secret-" * 2
            Path(db.DB_FILE).chmod(0o644)
            result = subprocess.run(
                [sys.executable, "-c", "import web_app"], env=env, capture_output=True
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"private file permissions", result.stderr)
            Path(db.DB_FILE).chmod(0o600)

    def test_safe_checkout_logging_does_not_echo_exception_details(self):
        self.client.get("/sale")
        self.post("/sale/add", {"product_id": self.product, "quantity": 1})
        with self.client.session_transaction() as state:
            token = state["checkout_token"]
        with (
            patch(
                "web_app.complete_transaction",
                side_effect=sqlite3.OperationalError("SENSITIVE_DETAILS_NEVER_LOG"),
            ),
            self.assertLogs(app.logger, level="ERROR") as logs,
        ):
            response = self.post(
                "/sale/checkout",
                {"payment_method": "Card", "checkout_token": token},
                follow_redirects=True,
            )
        self.assertNotIn("SENSITIVE_DETAILS_NEVER_LOG", response.text)
        self.assertNotIn("SENSITIVE_DETAILS_NEVER_LOG", " ".join(logs.output))
        self.assertIn("OperationalError", " ".join(logs.output))
        self.assertEqual(len(self.query("SELECT * FROM transactions")), 1)

    def test_unexpected_errors_are_redacted_in_responses_and_application_logs(self):
        with (
            patch("web_app.get_sale_data", side_effect=RuntimeError("PRIVATE_EXCEPTION_PAYLOAD")),
            self.assertLogs(app.logger, level="ERROR") as logs,
        ):
            response = self.client.get("/sale")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("PRIVATE_EXCEPTION_PAYLOAD", response.text)
        self.assertNotIn("PRIVATE_EXCEPTION_PAYLOAD", " ".join(logs.output))
        self.assertIn("RuntimeError", " ".join(logs.output))
        self.assertIn("sale", " ".join(logs.output))

    def test_three_accounts_remain_isolated_under_hostile_requests(self):
        accounts = [(self.client, self.user, self.store, self.product, self.category, self.tx)]
        for label in ("Bravo", "Charlie"):
            user, store = register_user(
                label.lower() + "-security@example.com",
                PASSWORD,
                PASSWORD,
                label + " private store",
            )
            with self.context(user, store):
                category = save_category(label + " category")
                product = create_product(
                    label + " secret product", "1.00", 10, category_id=category
                )
                tx = complete_transaction([{"id": product, "qty": 1}], "1.08", "Card")
            client = app.test_client()
            login(client, label.lower() + "-security@example.com")
            accounts.append((client, user, store, product, category, tx))
        for client, user, store, product, category, tx in accounts:
            other = next(account for account in accounts if account[2] != store)
            before = self.query("SELECT * FROM products")
            self.post(
                "/products/new",
                {
                    "name": "Rejected assignment",
                    "price": "1.00",
                    "quantity": 1,
                    "category_id": other[4],
                },
                client,
            )
            self.post(f"/products/{other[3]}/quantity", {"quantity": 0}, client)
            self.assertEqual(
                self.post(
                    f"/transactions/{other[5]}/refund", {"mode": "full", "reason": "Attack"}, client
                ).status_code,
                404,
            )
            self.assertIn(client.get(f"/transactions/{other[5]}/receipt").status_code, (302, 404))
            self.assertEqual(before, self.query("SELECT * FROM products"))
            with self.context(user, store):
                with self.assertRaises(ValueError):
                    complete_transaction([{"id": other[3], "qty": 1}], "1.08", "Card")
                data = get_sale(tx)
                line = data["items"][0]["id"]
                refund = process_refund(
                    tx, {line: 1}, "Own simulated return", True, secrets.token_urlsafe(32)
                )
            self.assertEqual(client.get(f"/refunds/{refund}").status_code, 200)
            self.assertEqual(other[0].get(f"/refunds/{refund}").status_code, 404)
            for path in (
                "/",
                "/inventory",
                "/analytics",
                "/reports",
                "/analytics/export/refunds.csv",
            ):
                self.assertEqual(client.get(path).status_code, 200)
        seed_demo(allow_existing=True)
        demo = app.test_client()
        demo.get("/login")
        self.post("/demo", client=demo)
        demo.get("/")
        self.assertEqual(demo.get(f"/transactions/{self.tx}/refund").status_code, 404)
        self.assertEqual(self.post("/sale/cancel", client=demo).status_code, 403)
        self.assertTrue(check_database()[0])

    def test_post_migration_read_checks_are_idempotent(self):
        before = self.query("SELECT * FROM products")
        for _ in range(2):
            with closing(db.get_db_connection()) as conn:
                self.assertEqual(validate_schema(conn, require_current=True), (14, 15, 16, 17, 20, 21, 22))
                validate_integrity(conn)
        self.assertEqual(before, self.query("SELECT * FROM products"))
