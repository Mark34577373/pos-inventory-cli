"""Day 22 gate: real SQLite transitions, real HTTP and three fictional stores."""

import secrets
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

import test_settings as fixtures
from auth_helpers import login
from flask import g

from config import BUSINESS_TIMEZONE
from database import database as db
from manage import seed_demo
from services.alerts import (
    get_alert,
    list_alerts,
    mark_page_read,
    notification_summary,
    recommendation,
    update_alert,
)
from services.inventory import (
    create_product,
    deactivate_product,
    set_product_quantity,
    update_product_record,
)
from services.inventory_management import adjust_stock
from services.money import from_cents
from services.refunds import get_sale, process_refund
from services.settings import get_settings, save_settings
from services.transaction_service import complete_transaction
from web_app import app


class AlertTests(unittest.TestCase):
    setUp = fixtures.SettingsTests.setUp
    tearDown = fixtures.SettingsTests.tearDown

    @contextmanager
    def context(self, index=0):
        with app.test_request_context("/"):
            user, store = self.accounts[index]
            g.user, g.store, g.is_demo = {"id": user}, {"id": store}, False
            yield

    def query(self, sql, values=()):
        with closing(db.get_db_connection()) as conn:
            return [dict(r) for r in conn.execute(sql, values)]

    def post(self, path, data=None, index=0, csrf=True):
        client = self.clients[index]
        with client.session_transaction() as state:
            token = state["csrf_token"]
        fields = dict(data or {})
        if csrf:
            fields["csrf_token"] = token
        return client.post(path, data=fields)

    def product(self, index=0, quantity=4, threshold=None):
        with self.context(index):
            return create_product(
                f"PRIVATE-ALERT-{index}", ".05", quantity, low_stock_threshold=threshold
            )

    def history(self, pid):
        return self.query("SELECT * FROM inventory_alerts WHERE product_id=? ORDER BY id", (pid,))

    def test_lifecycle_dedup_severity_restoration_and_reactivation(self):
        pid = self.product()
        with self.context():
            alert = list_alerts({})["alerts"][0]
            self.assertEqual(
                (alert["kind"], alert["severity"], alert["quantity"], alert["threshold"]),
                ("low", "warning", 4, 5),
            )
            update_alert(alert["id"], "read")
            update_alert(alert["id"], "dismiss")
            set_product_quantity(pid, 3)
            set_product_quantity(pid, 3)
            self.assertEqual(notification_summary()["unread"], 0)
            self.assertEqual(list_alerts({"read": "unread"})["total"], 0)
            set_product_quantity(pid, 0)
            self.assertEqual(notification_summary()["unread"], 1)
            set_product_quantity(pid, 2)
            set_product_quantity(pid, 10)
            set_product_quantity(pid, 11)
            set_product_quantity(pid, 4)
        rows = self.history(pid)
        self.assertEqual([r["kind"] for r in rows], ["low", "out", "low", "restored", "low"])
        self.assertTrue(rows[0]["read_at"] and rows[0]["dismissed_at"])
        self.assertEqual(rows[0]["quantity"], 3)
        self.assertTrue(all(r["resolved_at"] for r in rows[:-1]))
        self.assertIsNone(rows[-1]["resolved_at"])

        zero = self.product(quantity=1, threshold=0)
        self.assertEqual(self.history(zero), [])
        with self.context():
            set_product_quantity(zero, 0)
            set_product_quantity(zero, 1)
        self.assertEqual([r["kind"] for r in self.history(zero)], ["out", "restored"])

    def test_threshold_inheritance_overrides_rename_and_deactivation(self):
        pid = self.product(quantity=7)
        override = self.product(quantity=7, threshold=2)
        with self.context():
            self.assertEqual(list_alerts({})["total"], 0)
            save_settings("inventory", {"revision": "0", "low_stock_default": "8"})
            self.assertEqual(list_alerts({})["total"], 1)
            update_product_record(pid, "Renamed fictional stock", ".05", 7, low_stock_threshold=1)
            self.assertEqual([r["kind"] for r in self.history(pid)], ["low", "restored"])
            set_product_quantity(pid, 0)
            deactivate_product(pid)
            save_settings("inventory", {"revision": "1", "low_stock_default": "100"})
        rows = self.history(pid)
        self.assertTrue(all(r["resolved_at"] for r in rows))
        self.assertEqual(self.history(override), [])
        self.assertEqual(
            self.query("SELECT quantity FROM products WHERE id=?", (pid,))[0]["quantity"], 0
        )
        self.assertEqual(
            self.query("SELECT low_stock_threshold FROM products WHERE id=?", (override,))[0][
                "low_stock_threshold"
            ],
            2,
        )

        with closing(db.get_db_connection()) as conn, conn:
            conn.execute(
                "UPDATE products SET is_active=1 WHERE id=? AND store_id=?",
                (pid, self.accounts[0][1]),
            )
        self.assertEqual(self.history(pid)[-1]["kind"], "out")
        self.assertIsNone(self.history(pid)[-1]["resolved_at"])

    def test_checkout_refund_restock_and_retry_are_atomic(self):
        pid = self.product(quantity=2, threshold=1)
        with self.context():
            tx = complete_transaction(
                [{"id": pid, "qty": 2}], ".11", "Card", checkout_token="fictional-alert-checkout"
            )
            self.assertEqual(
                complete_transaction(
                    [{"id": pid, "qty": 2}],
                    ".11",
                    "Card",
                    checkout_token="fictional-alert-checkout",
                ),
                tx,
            )
            self.assertEqual([r["kind"] for r in self.history(pid)], ["out"])
            line = get_sale(tx)["items"][0]["id"]
            key = secrets.token_urlsafe(24)
            refund = process_refund(tx, {line: 2}, "Fictional restock", True, key)
            self.assertEqual(process_refund(tx, {line: 2}, "Fictional restock", True, key), refund)
        self.assertEqual([r["kind"] for r in self.history(pid)], ["out", "restored"])
        self.assertEqual(
            self.query("SELECT quantity FROM products WHERE id=?", (pid,))[0]["quantity"], 2
        )
        self.assertEqual(
            len(
                self.query(
                    "SELECT * FROM stock_adjustments WHERE product_id=? AND adjustment_type='return'",
                    (pid,),
                )
            ),
            1,
        )
        self.assertEqual(
            self.query("SELECT total_cents FROM transactions WHERE id=?", (tx,))[0]["total_cents"],
            11,
        )

    def test_failed_checkout_and_forced_refund_failures_rollback_alerts(self):
        pid = self.product(quantity=2, threshold=1)
        with self.context():
            with self.assertRaises(ValueError):
                complete_transaction([{"id": pid, "qty": 3}], ".16", "Card")
            self.assertEqual(self.history(pid), [])
            tx = complete_transaction([{"id": pid, "qty": 2}], ".11", "Card")
            line = get_sale(tx)["items"][0]["id"]
            before = self.history(pid)
            with closing(db.get_db_connection()) as conn, conn:
                conn.execute(
                    "CREATE TRIGGER fictional_alert_failure BEFORE INSERT ON inventory_alerts WHEN NEW.kind='restored' BEGIN SELECT RAISE(ABORT,'Injected failure'); END"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                process_refund(tx, {line: 2}, "Fictional return", True, secrets.token_urlsafe(24))
        self.assertEqual(before, self.history(pid))
        self.assertEqual(self.query("SELECT * FROM refunds"), [])
        self.assertEqual(
            self.query("SELECT quantity FROM products WHERE id=?", (pid,))[0]["quantity"], 0
        )

    def test_alert_failure_rolls_back_checkout_and_threshold_change(self):
        pid = self.product(quantity=2, threshold=1)
        with closing(db.get_db_connection()) as conn, conn:
            conn.execute(
                "CREATE TRIGGER fictional_alert_checkout_failure BEFORE INSERT ON inventory_alerts WHEN NEW.kind='out' BEGIN SELECT RAISE(ABORT,'Injected alert failure'); END"
            )
        with self.context():
            with self.assertRaises(sqlite3.IntegrityError):
                complete_transaction([{"id": pid, "qty": 2}], ".11", "Card")
        self.assertEqual(self.query("SELECT * FROM transactions"), [])
        self.assertEqual(self.history(pid), [])
        self.assertEqual(
            self.query("SELECT quantity FROM products WHERE id=?", (pid,))[0]["quantity"], 2
        )
        inherited = self.product(quantity=6)
        with closing(db.get_db_connection()) as conn, conn:
            conn.execute(
                "CREATE TRIGGER fictional_alert_threshold_failure BEFORE INSERT ON inventory_alerts WHEN NEW.kind='low' BEGIN SELECT RAISE(ABORT,'Injected threshold failure'); END"
            )
        with self.context():
            with self.assertRaises(sqlite3.IntegrityError):
                save_settings("inventory", {"revision": "0", "low_stock_default": "7"})
            self.assertEqual(get_settings()["low_stock_default"], 5)
        self.assertEqual(self.history(inherited), [])
        self.assertEqual(
            self.query("SELECT quantity FROM products WHERE id=?", (inherited,))[0]["quantity"], 6
        )

    def test_manual_stock_concurrency_and_failure_roll_back(self):
        pid = self.product(quantity=6)

        def decrease():
            with self.context():
                adjust_stock(pid, 1, "damaged", "Fictional damage")

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: decrease(), range(2)))
        self.assertEqual(
            self.query("SELECT quantity FROM products WHERE id=?", (pid,))[0]["quantity"], 4
        )
        self.assertEqual(len(self.history(pid)), 1)
        with self.context():
            with self.assertRaises(ValueError):
                adjust_stock(pid, 100, "damaged", "Too many")
        self.assertEqual(len(self.history(pid)), 1)
        self.assertEqual(
            len(
                self.query(
                    "SELECT * FROM stock_adjustments WHERE product_id=? AND adjustment_type='damaged'",
                    (pid,),
                )
            ),
            2,
        )

    def test_three_store_idor_csrf_mass_assignment_and_no_side_effects(self):
        products = [self.product(i) for i in range(3)]
        alerts = [self.history(pid)[0]["id"] for pid in products]
        before = self.query("SELECT * FROM inventory_alerts ORDER BY id")
        for index, client in enumerate(self.clients):
            response = client.get("/notifications")
            self.assertEqual(response.status_code, 200)
            self.assertIn(f"PRIVATE-ALERT-{index}".encode(), response.data)
            for other in set(range(3)) - {index}:
                self.assertNotIn(f"PRIVATE-ALERT-{other}".encode(), response.data)
                self.assertEqual(client.get(f"/notifications/{alerts[other]}").status_code, 404)
                for action in ("read", "dismiss"):
                    self.assertEqual(
                        self.post(
                            f"/notifications/{alerts[other]}/{action}", index=index
                        ).status_code,
                        404,
                    )
                with self.context(index), self.assertRaises(LookupError):
                    get_alert(alerts[other])
                with self.context(index), self.assertRaises(LookupError):
                    update_alert(alerts[other], "dismiss")
                with self.context(index), self.assertRaises(LookupError):
                    mark_page_read([alerts[index], alerts[other]])
                self.assertEqual(
                    self.post(
                        "/notifications/read-page",
                        {"ids": f"{alerts[index]},{alerts[other]}"},
                        index=index,
                    ).status_code,
                    404,
                )
            self.assertEqual(
                self.post(
                    f"/notifications/{alerts[index]}/read",
                    {"store_id": self.accounts[(index + 1) % 3][1]},
                    index=index,
                ).status_code,
                400,
            )
            self.assertEqual(
                self.post(
                    f"/notifications/{alerts[index]}/read", index=index, csrf=False
                ).status_code,
                400,
            )
        self.assertEqual(before, self.query("SELECT * FROM inventory_alerts ORDER BY id"))
        self.assertEqual(app.test_client().get("/notifications").status_code, 302)
        self.assertEqual(
            app.test_client().post(f"/notifications/{alerts[0]}/dismiss").status_code, 302
        )

    def test_filters_pagination_actions_and_get_is_read_only(self):
        with self.context():
            for number in range(25):
                create_product(f"Fictional alert {number}", "1", 0)
            data = list_alerts({"kind": "out", "severity": "critical", "sort": "oldest"})
            self.assertEqual((data["total"], len(data["alerts"]), data["pages"]), (25, 20, 2))
            second = list_alerts({"page": "2"})
            self.assertEqual(len(second["alerts"]), 5)
            ids = [r["id"] for r in data["alerts"]]
        before = self.query("SELECT * FROM inventory_alerts ORDER BY id")
        self.assertEqual(self.clients[0].get(f"/notifications/{ids[0]}").status_code, 200)
        self.assertEqual(before, self.query("SELECT * FROM inventory_alerts ORDER BY id"))
        self.assertEqual(
            self.post("/notifications/read-page", {"ids": ",".join(map(str, ids))}).status_code, 302
        )
        with self.context():
            self.assertEqual(notification_summary()["unread"], 5)
            self.assertEqual(list_alerts({"read": "read"})["total"], 20)
        self.assertEqual(self.post(f"/notifications/{ids[0]}/dismiss").status_code, 302)
        self.assertEqual(self.post(f"/notifications/{ids[0]}/dismiss").status_code, 302)
        for query in (
            "page=-1",
            "page=0",
            "kind=sql",
            "sort=id;DROP",
            "severity=urgent",
            "read=false",
            "state=unknown",
        ):
            self.assertEqual(self.clients[0].get("/notifications?" + query).status_code, 400)
        self.assertEqual(self.clients[1].get("/notifications").status_code, 200)
        self.assertIn(b"No matching notifications", self.clients[1].get("/notifications").data)

    def test_demo_read_only_account_switch_and_alert_xss(self):
        pid = self.product()
        other = self.product(1)
        with self.context():
            update_product_record(pid, "<script>fictional</script>", ".05", 4)
        aid = self.history(pid)[0]["id"]
        response = self.clients[0].get("/notifications")
        self.assertIn(b"&lt;script&gt;fictional&lt;/script&gt;", response.data)
        self.assertNotIn(b"<script>fictional</script>", response.data)
        login(self.clients[0], "settings-b@example.com")
        self.assertEqual(self.clients[0].get(f"/notifications/{aid}").status_code, 404)
        self.assertIn(b"PRIVATE-ALERT-1", self.clients[0].get("/notifications").data)
        seed_demo(allow_existing=True)
        client = self.clients[0]
        with client.session_transaction() as state:
            token = state["csrf_token"]
        self.assertEqual(client.post("/demo", data={"csrf_token": token}).status_code, 302)
        self.assertNotIn(b"PRIVATE-", client.get("/notifications").data)
        self.assertEqual(
            client.get(f"/notifications/{self.history(other)[0]['id']}").status_code, 404
        )
        self.assertEqual(
            client.post(f"/notifications/{aid}/dismiss", data={"csrf_token": token}).status_code,
            403,
        )
        with self.context():
            app.config["DEMO_READ_ONLY"] = True
            with self.assertRaises(PermissionError):
                update_alert(aid, "read")

    def test_migration_integrity_legacy_insert_and_history_guards(self):
        before = self.query("SELECT * FROM products ORDER BY id")
        db.initialize_database()
        db.initialize_database()
        self.assertEqual(before, self.query("SELECT * FROM products ORDER BY id"))
        with closing(db.get_db_connection()) as conn, conn:
            pid = conn.execute(
                "INSERT INTO products(name,price,price_cents,quantity) VALUES('Fictional desktop',1,100,0)"
            ).lastrowid
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.history(pid)[0]["store_id"], 1)
        alert = self.history(pid)[0]["id"]
        for sql in (
            "DELETE FROM inventory_alerts WHERE id=?",
            "UPDATE inventory_alerts SET store_id=999 WHERE id=?",
            "UPDATE inventory_alerts SET kind='low' WHERE id=?",
        ):
            with (
                closing(db.get_db_connection()) as conn,
                conn,
                self.assertRaises(sqlite3.IntegrityError),
            ):
                conn.execute(sql, (alert,))

        with (
            closing(db.get_db_connection()) as conn,
            conn,
            self.assertRaises(sqlite3.IntegrityError),
        ):
            conn.execute(
                "INSERT INTO inventory_alerts(store_id,product_id,product_name,kind,severity,quantity,threshold) SELECT store_id,product_id,product_name,kind,severity,quantity,threshold FROM inventory_alerts WHERE id=?",
                (alert,),
            )
        with (
            closing(db.get_db_connection()) as conn,
            conn,
            self.assertRaises(sqlite3.IntegrityError),
        ):
            conn.execute(
                "INSERT INTO inventory_alerts(store_id,product_id,product_name,kind,severity,quantity,threshold) VALUES(?,?,?,'out','critical',0,5)",
                (self.accounts[0][1], pid, "Foreign product"),
            )

    def test_reorder_zero_sales_insufficient_history_and_known_demand(self):
        pid = self.product(quantity=100)
        with self.context():
            opening = self.query(
                "SELECT recorded_at FROM stock_adjustments WHERE product_id=? AND adjustment_type='initial'",
                (pid,),
            )[0]["recorded_at"]
            first = datetime.fromisoformat(opening).astimezone(
                ZoneInfo(BUSINESS_TIMEZONE)
            ).date() + timedelta(days=1)
            today = first + timedelta(days=30)
            self.assertIsNone(recommendation(pid, today=today)["suggested"])
            with patch(
                "services.transaction_service.business_now",
                return_value=datetime.combine(first + timedelta(days=27), datetime.min.time()),
            ):
                tx = complete_transaction([{"id": pid, "qty": 60}], from_cents(325), "Card")
            line = get_sale(tx)["items"][0]["id"]
            with patch(
                "services.refunds.business_now",
                return_value=datetime.combine(first + timedelta(days=28), datetime.min.time()),
            ):
                process_refund(tx, {line: 30}, "Fictional restock", True, secrets.token_urlsafe(24))
                process_refund(
                    tx, {line: 10}, "Fictional discard", False, secrets.token_urlsafe(24)
                )
            result = recommendation(pid, today=today)
            self.assertEqual(
                (result["units7"], result["units30"], result["restocked30"], result["depletion"]),
                (60, 60, 30, 30),
            )
            self.assertEqual(result["daily"], Decimal("1"))
            self.assertEqual((result["target"], result["suggested"]), (14, 0))
            self.assertEqual(result["days_remaining"], Decimal("70"))
            self.assertIsNone(recommendation(pid, today=first + timedelta(days=1))["suggested"])
            self.assertEqual(
                self.query("SELECT quantity FROM products WHERE id=?", (pid,))[0]["quantity"], 70
            )

        with self.context():
            update_product_record(pid, "PRIVATE-ALERT-0", ".05", 70, low_stock_threshold=1_000_000)
            oversized = recommendation(pid, today=today)
            self.assertEqual(oversized["target"], 1_000_001)
            self.assertIsNone(oversized["suggested"])
            from services.analytics import ReportLimitError

            with patch(
                "services.transaction_service.business_now",
                return_value=datetime.combine(first + timedelta(days=29), datetime.min.time()),
            ):
                complete_transaction([{"id": pid, "qty": 1}], ".05", "Card")
            with patch("services.analytics.MAX_ITEMS", 1), self.assertRaises(ReportLimitError):
                recommendation(pid, today=today)
