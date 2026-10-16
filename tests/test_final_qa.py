"""Day 23 gaps: request-scoped defaults, failures, schema guards and endpoints."""

import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch

import test_settings as fixtures
from flask import g

from database import database as db
from database.database import get_store_connection
from services.alerts import (
    filters_from,
    get_alert,
    mark_page_read,
    notification_summary,
    recommendation,
    update_alert,
)
from services.inventory import create_product
from services.inventory_management import stock_status
from services.settings import default_threshold, save_settings
from web_app import app


class FinalQATests(unittest.TestCase):
    setUp = fixtures.SettingsTests.setUp
    tearDown = fixtures.SettingsTests.tearDown

    def identify(self, index=0):
        user, store = self.accounts[index]
        g.user, g.store, g.is_demo = {"id": user}, {"id": store}, False

    def test_threshold_memoization_is_bounded_invalidated_and_store_keyed(self):
        with app.test_request_context("/"):
            self.identify()
            with patch(
                "services.settings.get_store_connection", wraps=get_store_connection
            ) as read:
                for _ in range(100):
                    self.assertEqual(
                        stock_status({"quantity": 6, "low_stock_threshold": None}), "in"
                    )
                self.assertEqual(read.call_count, 1)
                save_settings("inventory", {"revision": "0", "low_stock_default": "7"})
                self.assertEqual(default_threshold(), 7)
                self.assertEqual(read.call_count, 3)
                self.identify(1)
                self.assertEqual(default_threshold(), 5)
                self.assertEqual(read.call_count, 4)
                self.identify()
                self.assertEqual(default_threshold(), 7)
                self.assertEqual(read.call_count, 5)
        with app.test_request_context("/"):
            self.identify()
            self.assertEqual(default_threshold(), 7)

    def test_alert_service_validation_and_missing_products_are_safe(self):
        with app.test_request_context("/"):
            self.identify()
            for bad in ([], list(range(1, 22)), ["not an id"], [True]):
                with self.subTest(ids=bad), self.assertRaises(ValueError):
                    mark_page_read(bad)
            with self.assertRaises(ValueError):
                update_alert(1, "delete")
            with self.assertRaises(LookupError):
                get_alert(999999)
            with self.assertRaises(LookupError):
                recommendation(999999)
            for bad in (
                {"page": "NaN"},
                {"read": "0"},
                {"kind": "x"},
                {"state": "sql"},
                {"severity": "high"},
                {"sort": "DROP"},
            ):
                with self.subTest(filters=bad), self.assertRaises(ValueError):
                    filters_from(bad)
        with app.test_request_context("/"), self.assertRaises(PermissionError):
            notification_summary()

    def test_missing_alert_guard_stops_migration_and_schema_is_not_repaired(self):
        with closing(db.get_db_connection()) as conn, conn:
            before = [tuple(r) for r in conn.execute("SELECT * FROM products")]
            conn.execute("DROP TRIGGER alerts_product_update")
        with self.assertRaises(ValueError):
            db.initialize_database()
        with closing(db.get_db_connection()) as conn:
            self.assertEqual(before, [tuple(r) for r in conn.execute("SELECT * FROM products")])
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='alerts_product_update'"
                ).fetchone()
            )

    def test_notification_lock_errors_are_redacted_and_no_side_effects(self):
        with app.test_request_context("/"):
            self.identify()
            pid = create_product("Fictional lock test", "1", 0)
        with closing(db.get_db_connection()) as conn:
            aid = conn.execute(
                "SELECT id FROM inventory_alerts WHERE product_id=?", (pid,)
            ).fetchone()[0]
        client = self.clients[0]
        with client.session_transaction() as state:
            csrf = state["csrf_token"]
        with patch(
            "alert_routes.update_alert",
            side_effect=sqlite3.OperationalError("database locked: PRIVATE fictional marker"),
        ):
            with self.assertLogs(app.logger, level="ERROR") as logged:
                response = client.post(f"/notifications/{aid}/dismiss", data={"csrf_token": csrf})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b"PRIVATE fictional marker", response.data)
        self.assertNotIn("PRIVATE fictional marker", "".join(logged.output))
        with app.test_request_context("/"):
            self.identify()
            self.assertIsNone(get_alert(aid)["dismissed_at"])

    def test_notification_error_codes_and_readonly_forms(self):
        with app.test_request_context("/"):
            self.identify()
            pid = create_product("Fictional action test", "1", 0)
            aid = notification_summary()["recent"][0]["id"]
        client = self.clients[0]
        with client.session_transaction() as state:
            csrf = state["csrf_token"]
        self.assertEqual(
            client.post(f"/notifications/{aid}/delete", data={"csrf_token": csrf}).status_code, 400
        )
        self.assertEqual(client.get(f"/notifications/{aid}/dismiss").status_code, 405)
        self.assertEqual(
            client.post(
                "/notifications/read-page", data={"csrf_token": csrf, "ids": ""}
            ).status_code,
            400,
        )
        self.assertEqual(
            client.post(
                "/notifications/read-page",
                data={"csrf_token": csrf, "ids": str(aid), "user_id": "999"},
            ).status_code,
            400,
        )
        self.assertEqual(client.get("/notifications/999999").status_code, 404)
        with patch("alert_routes.recommendation", side_effect=ValueError("Invalid recommendation")):
            self.assertEqual(client.get(f"/notifications/{aid}").status_code, 400)
        from services.alerts import list_alerts

        with app.test_request_context("/"):
            self.identify()
            self.assertEqual(list_alerts({"state": "active"})["total"], 1)
            update_alert(aid, "dismiss")
            self.assertEqual(list_alerts({"state": "dismissed"})["total"], 1)
            self.assertEqual(list_alerts({"read": "unread"})["total"], 0)
            self.assertEqual(list_alerts({"state": "resolved"})["total"], 0)
        from services.analytics import ReportLimitError

        with patch("alert_routes.recommendation", side_effect=ReportLimitError("Bounded history")):
            self.assertEqual(client.get(f"/notifications/{aid}").status_code, 413)
        app.config["DEMO_READ_ONLY"] = True
        self.assertEqual(
            client.post(
                "/notifications/read-page", data={"csrf_token": csrf, "ids": str(aid)}
            ).status_code,
            403,
        )
        self.assertEqual(client.get(f"/notifications/{aid}").status_code, 200)
        self.assertEqual(self.query_quantity(pid), 0)

    def test_dashboard_large_legacy_totals_do_not_overflow_sqlite_sum(self):
        with closing(db.get_db_connection()) as conn, conn:
            for _ in range(2):
                conn.execute(
                    "INSERT INTO transactions(timestamp,total,total_cents,payment_method,store_id) VALUES('2026-10-15 12:00:00',1,9000000000000000000,'Cash',?)",
                    (self.accounts[0][1],),
                )
        from datetime import datetime

        with (
            patch("web_app.business_now", return_value=datetime(2026, 10, 15, 12)),
            patch("web_app.render_template", return_value="ok") as render,
        ):
            response = self.clients[0].get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(render.call_args.kwargs["today_sales_cents"], 18000000000000000000)
        self.assertEqual(
            sum(r["sales_cents"] for r in render.call_args.kwargs["sales_chart"]),
            18000000000000000000,
        )

    def test_desktop_average_uses_exact_half_up_cent_rounding(self):
        from datetime import datetime

        from ui.reports_view import get_today_sales_summary

        with closing(db.get_db_connection()) as conn, conn:
            for cents in (100, 101):
                conn.execute(
                    "INSERT INTO transactions(timestamp,total,total_cents,payment_method,store_id) VALUES('2026-10-15 12:00:00',1,?,'Cash',?)",
                    (cents, self.accounts[0][1]),
                )
        with app.test_request_context("/"):
            self.identify()
            summary = get_today_sales_summary(datetime(2026, 10, 15, 12))
        self.assertEqual(summary["average_sale_cents"], 100.5)
        self.assertEqual(summary["average_sale_rounded_cents"], 101)
        from services.analytics import ReportLimitError

        with patch("services.sales_totals.MAX_TRANSACTIONS", 1), app.test_request_context("/"):
            self.identify()
            with self.assertRaises(ReportLimitError):
                get_today_sales_summary(datetime(2026, 10, 15, 12))
        with (
            patch("services.sales_totals.MAX_TRANSACTIONS", 1),
            patch("web_app.business_now", return_value=datetime(2026, 10, 15, 12)),
        ):
            self.assertEqual(self.clients[0].get("/").status_code, 413)

    def query_quantity(self, pid):
        with closing(db.get_db_connection()) as conn:
            return conn.execute("SELECT quantity FROM products WHERE id=?", (pid,)).fetchone()[0]
