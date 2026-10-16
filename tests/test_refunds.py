"""Day 17: real SQLite/Flask checkout, ledger, stock, and isolation tests."""

import csv
import io
import re
import secrets
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from auth_helpers import PASSWORD, login
from flask import g

from config import TAX_RATE
from database import database as db
from database.analytics_migration import migrate_analytics
from database.inventory_migration import migrate_inventory
from database.migrations import migrate_identity
from manage import check_database, seed_demo
from refund_routes import serializer
from services.analytics import build_analytics, resolve_filters
from services.auth import register_user
from services.inventory import create_product, deactivate_product, update_product_details
from services.money import calculate_tax_cents, from_cents
from services.refunds import get_refund, get_sale, preview_refund, process_refund
from services.transaction_service import complete_transaction
from web_app import app


class RefundTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = db.DB_FILE
        self.original_config = app.config.copy()
        db.DB_FILE = str(Path(self.tmp.name) / "day17.db")
        db.initialize_database(seed=False)
        app.config.update(TESTING=True, DEMO_READ_ONLY=False)
        self.user, self.store = register_user(
            "alpha@example.com", PASSWORD, PASSWORD, "Alpha <Store>"
        )
        self.client = app.test_client()
        login(self.client, "alpha@example.com")
        with self.context():
            self.product = create_product("Five cent <item>", "0.05", 20)
        self.tx = self.checkout(self.client, self.product, 2)
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

    def query(self, sql, values=()):
        with closing(db.get_db_connection()) as conn:
            return [dict(row) for row in conn.execute(sql, values)]

    def post(self, path, data=None, client=None, follow=False):
        client = client or self.client
        with client.session_transaction() as state:
            token = state["csrf_token"]
        return client.post(path, data=dict(data or {}, csrf_token=token), follow_redirects=follow)

    def checkout(self, client, product, quantity):
        client.get("/sale")
        self.assertEqual(
            self.post(
                "/sale/add", {"product_id": product, "quantity": quantity}, client
            ).status_code,
            302,
        )
        with client.session_transaction() as state:
            token = state["checkout_token"]
        response = self.post(
            "/sale/checkout", {"checkout_token": token, "payment_method": "Card"}, client
        )
        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as state:
            return state["last_receipt_id"]

    def refund(self, quantities=None, restock=False, key=None, **kwargs):
        with self.context():
            return process_refund(
                self.tx,
                quantities or {self.line: 1},
                "Fictional return",
                restock,
                key or secrets.token_urlsafe(32),
                **kwargs,
            )

    def preview(self, values=None):
        return self.post(
            f"/transactions/{self.tx}/refund",
            values or {f"qty_{self.line}": "1", "reason": "Fictional return"},
        )

    def confirmation(self, response):
        match = re.search(r'name="confirmation" value="([^"]+)"', response.data.decode())
        self.assertIsNotNone(match)
        return match[1]

    def test_partial_then_remaining_exact_tax_and_full_status(self):
        first = self.refund()
        second = self.refund()
        with self.context():
            self.assertEqual(get_refund(first)["refund"]["total_cents"], 6)
            self.assertEqual(get_refund(second)["refund"]["total_cents"], 5)
            state = get_sale(self.tx)
            self.assertEqual((state["refunded_cents"], state["status"]), (11, "refunded"))
            with self.assertRaises(ValueError):
                process_refund(
                    self.tx, {self.line: 1}, "Too much", False, secrets.token_urlsafe(32)
                )
        self.assertEqual(self.query("SELECT quantity FROM products")[0]["quantity"], 18)
        self.assertEqual(self.query("SELECT total_cents FROM transactions")[0]["total_cents"], 11)

    def test_full_refund_restock_links_audit_and_thresholds(self):
        refund_id = self.refund({self.line: 2}, restock=True)
        self.assertEqual(self.query("SELECT quantity FROM products")[0]["quantity"], 20)
        audit = self.query("SELECT * FROM stock_adjustments WHERE adjustment_type='return'")
        self.assertEqual(len(audit), 1)
        self.assertEqual(
            (
                audit[0]["refund_id"],
                audit[0]["transaction_id"],
                audit[0]["quantity_change"],
                audit[0]["actor_user_id"],
            ),
            (refund_id, self.tx, 2, self.user),
        )
        self.assertTrue(check_database()[0])

    def test_no_restock_damaged_reason_does_not_change_stock(self):
        with self.context():
            process_refund(
                self.tx, {self.line: 2}, "Damaged; discard", False, secrets.token_urlsafe(32)
            )
        self.assertEqual(self.query("SELECT quantity FROM products")[0]["quantity"], 18)
        self.assertFalse(
            self.query("SELECT * FROM stock_adjustments WHERE adjustment_type='return'")
        )

    def test_deactivated_product_can_restock_without_reactivation(self):
        with self.context():
            deactivate_product(self.product)
        self.refund(restock=True)
        product = self.query("SELECT quantity,is_active FROM products")[0]
        self.assertEqual(product, dict(quantity=19, is_active=0))

    def test_idempotent_retry_and_conflicting_key(self):
        key = secrets.token_urlsafe(32)
        first = self.refund(restock=True, key=key)
        self.assertEqual(self.refund(restock=True, key=key), first)
        with self.assertRaises(ValueError):
            self.refund({self.line: 2}, restock=True, key=key)
        self.assertEqual(len(self.query("SELECT * FROM refunds")), 1)
        self.assertEqual(self.query("SELECT quantity FROM products")[0]["quantity"], 19)

    def test_concurrent_duplicate_refund_restocks_once(self):
        key = secrets.token_urlsafe(32)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(lambda _: self.refund({self.line: 2}, restock=True, key=key), range(2))
            )
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.query("SELECT quantity FROM products")[0]["quantity"], 20)
        self.assertEqual(len(self.query("SELECT * FROM refunds")), 1)

    def test_concurrent_over_refund_only_one_commits(self):
        def attempt(_):
            try:
                return self.refund({self.line: 2}, restock=True)
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.query("SELECT quantity FROM products")[0]["quantity"], 20)

    def test_rollback_when_audit_write_fails(self):
        with closing(db.get_db_connection()) as conn:
            with conn:
                conn.execute(
                    "CREATE TRIGGER reject_return BEFORE INSERT ON stock_adjustments WHEN NEW.adjustment_type='return' BEGIN SELECT RAISE(ABORT,'test rollback'); END"
                )
        with self.assertRaises(sqlite3.IntegrityError):
            self.refund({self.line: 2}, restock=True)
        self.assertFalse(self.query("SELECT * FROM refunds"))
        self.assertFalse(self.query("SELECT * FROM refund_items"))
        self.assertEqual(self.query("SELECT quantity FROM products")[0]["quantity"], 18)

    def test_restock_overflow_rolls_back_everything(self):
        with closing(db.get_db_connection()) as conn:
            with conn:
                conn.execute("UPDATE products SET quantity=1000000 WHERE id=?", (self.product,))
        before = self.query("SELECT * FROM stock_adjustments")
        with self.assertRaises(ValueError):
            self.refund(restock=True)
        self.assertFalse(self.query("SELECT * FROM refunds"))
        self.assertEqual(self.query("SELECT * FROM stock_adjustments"), before)

    def test_signed_preview_confirmation_and_replay(self):
        response = self.preview(
            {f"qty_{self.line}": "1", "reason": "Returned", "restock": "on", "total_cents": 1}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"$0.06", response.data)
        self.assertFalse(self.query("SELECT * FROM refunds"))
        token = self.confirmation(response)
        path = f"/transactions/{self.tx}/refund"
        first = self.post(path, {"confirmation": token})
        self.assertEqual(first.status_code, 302)
        second = self.post(path, {"confirmation": token})
        self.assertEqual(second.location, first.location)
        detail = self.client.get(first.location)
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"No real money", detail.data)
        self.assertEqual(self.query("SELECT quantity FROM products")[0]["quantity"], 19)

    def test_tampered_expired_and_stale_confirmation(self):
        token = self.confirmation(self.preview())
        path = f"/transactions/{self.tx}/refund"
        self.assertEqual(self.post(path, {"confirmation": token + "tampered"}).status_code, 400)
        with app.app_context():
            signer = serializer()
            payload = signer.loads(token)
            with patch(
                "itsdangerous.timed.TimestampSigner.get_timestamp",
                return_value=int(time.time()) - 3601,
            ):
                expired = signer.dumps(payload)
        self.assertEqual(self.post(path, {"confirmation": expired}).status_code, 400)
        self.refund()
        self.assertEqual(self.post(path, {"confirmation": token}).status_code, 400)
        self.assertEqual(len(self.query("SELECT * FROM refunds")), 1)

    def test_bad_quantities_foreign_line_and_reason_rejected(self):
        for qty in ("-1", "1.5", "1000001", "NaN", "0"):
            with self.subTest(qty=qty):
                self.assertEqual(
                    self.preview({f"qty_{self.line}": qty, "reason": "Return"}).status_code, 400
                )
        for quantities, reason in (
            ({self.line: True}, "Return"),
            ({self.line: 3}, "Return"),
            ({999999: 1}, "Return"),
            ({self.line: 1}, ""),
            ({self.line: 1}, "x" * 501),
        ):
            with self.context(), self.assertRaises(ValueError):
                preview_refund(self.tx, quantities, reason)
        self.assertFalse(self.query("SELECT * FROM refunds"))

    def test_legacy_missing_or_inconsistent_details_blocked(self):
        for rate in (None, "NaN", "0", "broken"):
            with closing(db.get_db_connection()) as conn:
                with conn:
                    conn.execute(
                        "UPDATE transactions SET tax_rate_percent=? WHERE id=?", (rate, self.tx)
                    )
            with self.context():
                self.assertFalse(get_sale(self.tx)["eligible"])
            with self.assertRaises(ValueError):
                self.refund()
        self.assertFalse(self.query("SELECT * FROM refunds"))

    def test_immutable_ledgers_and_original_refunded_sale(self):
        self.refund()
        for sql in (
            'UPDATE refunds SET reason="changed"',
            "DELETE FROM refunds",
            "UPDATE refund_items SET quantity=2",
            "DELETE FROM refund_items",
            "UPDATE transactions SET total_cents=12",
            "UPDATE transaction_items SET price_cents=6",
            "DELETE FROM transaction_items",
        ):
            with closing(db.get_db_connection()) as conn, self.assertRaises(sqlite3.IntegrityError):
                with conn:
                    conn.execute(sql)
        self.assertTrue(check_database()[0])

    def test_receipt_historical_prices_names_store_escaping_and_refunds(self):
        self.refund()
        with self.context():
            update_product_details(self.product, "Current name", "9.99")
        for path in ("/receipt", f"/transactions/{self.tx}/receipt"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Alpha &lt;Store&gt;", response.data)
            self.assertIn(b"Five cent &lt;item&gt;", response.data)
            self.assertNotIn(b"Current name", response.data)
            self.assertIn(b"$0.11", response.data)
            self.assertIn(b"$0.06", response.data)
            self.assertIn("no-store", response.headers["Cache-Control"])

    def test_cart_increment_decrement_exact_update_and_authoritative_totals(self):
        self.client.get("/sale")
        self.post("/sale/add", {"product_id": self.product, "quantity": 1, "price": "0"})
        self.post(f"/sale/update/{self.product}", {"quantity": 1, "adjust": "plus"})
        response = self.client.get("/sale")
        self.assertIn(b"$0.11", response.data)
        self.post(f"/sale/update/{self.product}", {"quantity": 2, "adjust": "minus"})
        with self.client.session_transaction() as state:
            self.assertEqual(state["sale_cart"][str(self.product)], 1)
        for value in ("-1", "1.2", "1000001"):
            self.post(f"/sale/update/{self.product}", {"quantity": value})
        with self.client.session_transaction() as state:
            self.assertEqual(state["sale_cart"][str(self.product)], 1)
        self.post(f"/sale/update/{self.product}", {"quantity": "0"})
        self.assertIn(b"Your cart is empty", self.client.get("/sale").data)

    def test_transaction_sort_status_pagination_filters_and_invalid_inputs(self):
        self.refund()
        response = self.client.get("/transactions?status=partial&sort=total_asc")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Partial", response.data)
        self.assertIn(b"$0.06", response.data)
        for index in range(24):
            with closing(db.get_db_connection()) as conn:
                with conn:
                    conn.execute(
                        "INSERT INTO transactions(timestamp,total,total_cents,payment_method,store_id) VALUES(?,1,100,?,?)",
                        (f"2026-10-15 12:00:{index:02}", "Cash", self.store),
                    )
        response = self.client.get("/transactions?sort=total_desc&page=2")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Page 2 of 2", response.data)
        for query in (
            "status=invalid",
            "sort=total;DROP TABLE users",
            "page=-1",
            "payment_method=wire",
            "start_date=bad",
        ):
            self.assertEqual(self.client.get("/transactions?" + query).status_code, 400)
        self.assertEqual(self.client.get("/transactions?q=" + "9" * 100).status_code, 200)

    def test_analytics_refund_date_negative_net_and_csv_reconcile(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        today = date.today().isoformat()
        with closing(db.get_db_connection()) as conn:
            with conn:
                conn.execute(
                    "UPDATE transactions SET timestamp=? WHERE id=?",
                    (yesterday + " 12:00:00", self.tx),
                )
        with patch(
            "services.refunds.business_now",
            return_value=datetime.fromisoformat(today + " 12:00:00"),
        ):
            self.refund({self.line: 2})
        with self.context():
            for group in ("daily", "weekly", "monthly"):
                data = build_analytics(
                    resolve_filters(
                        {"preset": "custom", "start_date": today, "end_date": today, "group": group}
                    )
                )
                self.assertEqual(
                    (
                        data["summary"]["revenue_cents"],
                        data["summary"]["refunded_cents"],
                        data["summary"]["net_sales_cents"],
                    ),
                    (0, 11, -11),
                )
                self.assertEqual(sum(row["net_sales_cents"] for row in data["series"]), -11)
        query = f"?preset=custom&start_date={today}&end_date={today}"
        for kind in ("summary", "periods", "refunds"):
            response = self.client.get("/analytics/export/" + kind + ".csv" + query)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"0.11", response.data)
        self.assertIn(b"-0.11", self.client.get("/analytics/export/summary.csv" + query).data)

    def test_three_accounts_checkout_refund_receipt_export_and_cart_isolation(self):
        identities = [(self.user, self.store, self.product, self.tx, self.client, self.line)]
        for label in ("Bravo", "Charlie"):
            user, store = register_user(
                label.lower() + "@example.com", PASSWORD, PASSWORD, label + " Store"
            )
            with self.context(user, store):
                product = create_product(label + " Secret", "1.00", 10)
            client = app.test_client()
            login(client, label.lower() + "@example.com")
            sale = self.checkout(client, product, 1)
            with self.context(user, store):
                line = get_sale(sale)["items"][0]["id"]
            identities.append((user, store, product, sale, client, line))
        before = self.query("SELECT * FROM products")
        for user, store, product, tx, client, line in identities:
            foreign = next(row for row in identities if row[1] != store)
            self.post(
                "/sale/add",
                {"product_id": foreign[2], "quantity": 1, "store_id": foreign[1]},
                client,
            )
            with client.session_transaction() as state:
                self.assertNotIn(str(foreign[2]), state.get("sale_cart", {}))
            for path in (
                f"/transactions/{foreign[3]}/receipt",
                f"/transactions/{foreign[3]}/refund",
            ):
                self.assertIn(client.get(path).status_code, (302, 404))
                self.assertNotIn(b"Secret", client.get(path).data)
            self.assertEqual(
                self.post(
                    f"/transactions/{foreign[3]}/refund",
                    {"mode": "full", "reason": "Attack", "store_id": foreign[1]},
                    client,
                ).status_code,
                404,
            )
            with self.context(user, store):
                with self.assertRaises(LookupError):
                    process_refund(
                        foreign[3], {foreign[5]: 1}, "Attack", True, secrets.token_urlsafe(32)
                    )
                with self.assertRaises(ValueError):
                    complete_transaction([{"id": foreign[2], "qty": 1}], "1.08", "Card")
                with self.assertRaises(ValueError):
                    process_refund(tx, {foreign[5]: 1}, "Attack", True, secrets.token_urlsafe(32))
        self.assertEqual(before, self.query("SELECT * FROM products"))
        self.assertFalse(self.query("SELECT * FROM refunds"))
        for user, store, product, tx, client, line in identities:
            with self.context(user, store):
                refund_id = process_refund(
                    tx, {line: 1}, "Own return", True, secrets.token_urlsafe(32)
                )
            for other in identities:
                response = other[4].get(f"/refunds/{refund_id}")
                self.assertEqual(response.status_code, 200 if other[1] == store else 404)
            summary = client.get("/analytics/export/refunds.csv?preset=today")
            self.assertEqual(summary.status_code, 200)
            rows = list(csv.reader(io.StringIO(summary.data.decode("utf-8-sig"))))
            header = next(index for index, row in enumerate(rows) if row and row[0] == "Refund ID")
            self.assertEqual([int(row[0]) for row in rows[header + 1 :]], [refund_id])
            with self.context(user, store):
                data = build_analytics(resolve_filters({"preset": "today"}))
                self.assertEqual([row["id"] for row in data["refunds"]], [refund_id])
        self.assertTrue(check_database()[0])

    def test_account_switch_invalidates_cart_and_signed_refund(self):
        token = self.confirmation(self.preview())
        self.client.get("/sale")
        self.post("/sale/add", {"product_id": self.product, "quantity": 1})
        register_user("bravo@example.com", PASSWORD, PASSWORD, "Bravo")
        login(self.client, "bravo@example.com")
        with self.client.session_transaction() as state:
            self.assertNotIn("sale_cart", state)
            self.assertNotIn("last_receipt_id", state)
        self.assertEqual(
            self.post(f"/transactions/{self.tx}/refund", {"confirmation": token}).status_code, 404
        )
        self.assertFalse(self.query("SELECT * FROM refunds"))

    def test_csrf_anonymous_demo_and_readonly_no_side_effects(self):
        path = f"/transactions/{self.tx}/refund"
        self.assertEqual(
            self.client.post(path, data={"mode": "full", "reason": "Attack"}).status_code, 400
        )
        self.assertEqual(app.test_client().post(path).status_code, 302)
        app.config["DEMO_READ_ONLY"] = True
        self.assertEqual(self.preview().status_code, 403)
        app.config["DEMO_READ_ONLY"] = False
        seed_demo(allow_existing=True)
        demo = app.test_client()
        demo.get("/login")
        self.post("/demo", client=demo)
        demo.get("/")
        self.assertEqual(
            self.post(path, {"mode": "full", "reason": "Attack"}, demo).status_code, 403
        )
        self.assertEqual(demo.get(f"/transactions/{self.tx}/receipt").status_code, 302)
        self.assertFalse(self.query("SELECT * FROM refunds"))

    def test_refund_rate_limit_persists_between_clients(self):
        for _ in range(10):
            self.preview({})
        self.assertEqual(self.preview().status_code, 429)
        another = app.test_client()
        login(another, "alpha@example.com")
        self.assertEqual(
            self.post(
                f"/transactions/{self.tx}/refund", {"mode": "full", "reason": "Return"}, another
            ).status_code,
            429,
        )

    def test_largest_remainder_multi_line_allocation_independent_of_refund_order(self):
        with self.context():
            product2 = create_product("Second five cent item", "0.05", 10)
            tx = complete_transaction(
                [{"id": self.product, "qty": 1}, {"id": product2, "qty": 1}], "0.11", "Card"
            )
            lines = get_sale(tx)["items"]
            # Equal remainders: earlier original line gets the one tax cent,
            # even when the later line is refunded first.
            later = process_refund(
                tx, {lines[1]["id"]: 1}, "Later first", False, secrets.token_urlsafe(32)
            )
            earlier = process_refund(
                tx, {lines[0]["id"]: 1}, "Earlier last", False, secrets.token_urlsafe(32)
            )
            self.assertEqual(get_refund(later)["refund"]["total_cents"], 5)
            self.assertEqual(get_refund(earlier)["refund"]["total_cents"], 6)
            self.assertEqual(get_sale(tx)["status"], "refunded")

    def test_mixed_prices_multiple_units_refunds_exactly_reconcile(self):
        with self.context():
            second = create_product("Mixed price", "0.07", 20)
            subtotal = 5 * 3 + 7 * 4
            total = subtotal + calculate_tax_cents(subtotal, TAX_RATE)
            tx = complete_transaction(
                [{"id": self.product, "qty": 3}, {"id": second, "qty": 4}],
                from_cents(total),
                "Card",
            )
            lines = get_sale(tx)["items"]
            amounts = []
            for line in reversed(lines):
                for _ in range(line["quantity"]):
                    refund = process_refund(
                        tx, {line["id"]: 1}, "One unit return", False, secrets.token_urlsafe(32)
                    )
                    amounts.append(get_refund(refund)["refund"])
            self.assertEqual(sum(row["total_cents"] for row in amounts), total)
            self.assertEqual(sum(row["tax_cents"] for row in amounts), total - subtotal)
            self.assertEqual(get_sale(tx)["status"], "refunded")

    def test_concurrent_checkout_and_refund_preserve_stock_and_audits(self):
        def return_goods():
            return self.refund({self.line: 2}, restock=True)

        def sale_goods():
            with self.context():
                return complete_transaction(
                    [{"id": self.product, "qty": 1}],
                    "0.05",
                    "Card",
                    checkout_token=secrets.token_urlsafe(32),
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(return_goods)
            second = pool.submit(sale_goods)
            self.assertIsInstance(first.result(), int)
            self.assertIsInstance(second.result(), int)
        self.assertEqual(self.query("SELECT quantity FROM products")[0]["quantity"], 19)
        movements = self.query("SELECT quantity_change FROM stock_adjustments")
        self.assertEqual(sum(row["quantity_change"] for row in movements), 19)
        self.assertTrue(check_database()[0])

    def test_full_refund_ui_and_cash_receipt_change(self):
        self.client.get("/sale")
        self.post("/sale/add", {"product_id": self.product, "quantity": 2})
        with self.client.session_transaction() as state:
            checkout_token = state["checkout_token"]
        response = self.post(
            "/sale/checkout",
            {"checkout_token": checkout_token, "payment_method": "Cash", "cash_received": "1.00"},
            follow=True,
        )
        self.assertIn(b"$0.89", response.data)
        quote = self.preview({"mode": "full", "reason": "Full remaining return"})
        self.assertIn(b"$0.11", quote.data)
        token = self.confirmation(quote)
        self.assertEqual(
            self.post(f"/transactions/{self.tx}/refund", {"confirmation": token}).status_code, 302
        )
        self.assertIn(b"Fully refunded", self.client.get("/transactions?status=refunded").data)

    def test_restock_updates_low_stock_threshold_indicator(self):
        with closing(db.get_db_connection()) as conn:
            with conn:
                conn.execute(
                    "UPDATE products SET quantity=0,low_stock_threshold=1 WHERE id=?",
                    (self.product,),
                )
        self.assertIn(b"Five cent &lt;item&gt;", self.client.get("/inventory?status=out").data)
        self.refund({self.line: 2}, restock=True)
        self.assertNotIn(b"Five cent &lt;item&gt;", self.client.get("/inventory?status=out").data)
        self.assertNotIn(b"Five cent &lt;item&gt;", self.client.get("/inventory?status=low").data)
        self.assertIn(b"Five cent &lt;item&gt;", self.client.get("/inventory?status=in").data)

    def test_database_caps_and_membership_constraints_reject_invalid_refunds(self):
        self.refund()
        original = self.query("SELECT * FROM refunds")[0]
        columns = [key for key in original if key != "id"]
        values = dict(original)
        values["idempotency_key"] = secrets.token_urlsafe(32)
        values["total_cents"] = 11
        statement = (
            "INSERT INTO refunds("
            + ",".join(columns)
            + ") VALUES("
            + ",".join("?" for _ in columns)
            + ")"
        )
        with closing(db.get_db_connection()) as conn, self.assertRaises(sqlite3.IntegrityError):
            with conn:
                conn.execute(statement, [values[key] for key in columns])
        values["total_cents"] = 5
        values["actor_user_id"] = 99999
        with closing(db.get_db_connection()) as conn, self.assertRaises(sqlite3.IntegrityError):
            with conn:
                conn.execute(statement, [values[key] for key in columns])
        self.assertEqual(len(self.query("SELECT * FROM refunds")), 1)
        self.assertTrue(check_database()[0])

    def test_zero_value_legacy_sale_is_unverified_without_arithmetic_error(self):
        source = str(Path(self.tmp.name) / "zero-legacy.db")
        original = db.DB_FILE
        db.DB_FILE = source
        try:
            with closing(db.get_db_connection()) as conn:
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    db._initialize_schema(conn, False)
                    migrate_identity(conn)
                    migrate_inventory(conn)
                    migrate_analytics(conn)
                    conn.execute(
                        "INSERT INTO products(id,name,price,price_cents,quantity,store_id) VALUES(1,'Legacy product',0.05,5,1,1)"
                    )
                    conn.execute(
                        "INSERT INTO transactions(id,timestamp,total,total_cents,payment_method,tax_rate_percent,store_id) VALUES(1,'2026-10-15 12:00:00',0,0,'Cash','0',1)"
                    )
                    # Simulate a preexisting invalid historical item, then restore
                    # the validation before the migration/service under test.
                    conn.execute("DROP TRIGGER validate_transaction_items_insert")
                    conn.execute(
                        "INSERT INTO transaction_items(id,transaction_id,product_id,quantity,price,price_cents,store_id) VALUES(1,1,1,1,0,0,1)"
                    )
                    db._initialize_schema(conn, False)
            db.initialize_database(seed=False)
            with self.context(store=1):
                self.assertFalse(get_sale(1)["eligible"])
        finally:
            db.DB_FILE = original

    def test_migration_copy_idempotency_and_every_original_value_preserved(self):
        source = str(Path(self.tmp.name) / "pre17.db")
        original = db.DB_FILE
        db.DB_FILE = source
        try:
            with closing(db.get_db_connection()) as conn:
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    db._initialize_schema(conn, True)
                    migrate_identity(conn)
                    migrate_inventory(conn)
                    migrate_analytics(conn)
            tables = (
                "products",
                "transactions",
                "transaction_items",
                "inventory_baselines",
                "stock_adjustments",
                "stores",
            )
            before = {table: self.query("SELECT * FROM " + table) for table in tables}
            db.initialize_database(seed=False)
            db.initialize_database(seed=False)
            for table, rows in before.items():
                after = self.query("SELECT * FROM " + table)
                for row in after:
                    if table == "stock_adjustments":
                        self.assertIsNone(row.pop("refund_id"))
                self.assertEqual(rows, after)
            self.assertEqual(
                [row["version"] for row in self.query("SELECT version FROM schema_migrations")],
                [14, 15, 16, 17, 20, 21, 22],
            )
            self.assertTrue(check_database()[0])
        finally:
            db.DB_FILE = original
