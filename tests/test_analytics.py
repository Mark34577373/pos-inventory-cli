"""Day 16: financial reconciliation, secure exports, and three-store HTTP tests."""

import csv
import io
import tempfile
import time as clock
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta
from decimal import Decimal
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
from services.analytics import ReportLimitError, build_analytics, resolve_filters
from services.auth import register_user
from services.inventory import create_product, deactivate_product, update_product_details
from services.money import calculate_tax_cents
from services.sales_exports import spreadsheet_text
from services.transaction_service import complete_transaction
from web_app import app

TODAY = date(2026, 10, 15)


def csv_rows(response):
    return list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))


def table_rows(response, first_header):
    rows = csv_rows(response)
    position = next(index for index, row in enumerate(rows) if row and row[0] == first_header)
    return rows[position + 1 :]


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = db.DB_FILE
        self.original_config = app.config.copy()
        db.DB_FILE = str(Path(self.tmp.name) / "analytics.db")
        db.initialize_database()
        app.config.update(TESTING=True, DEMO_READ_ONLY=False)
        self.clients = []
        self.stores = []
        self.users = []
        self.products = []
        self.sale_ids = []
        for index, label in enumerate(("ALPHA", "BRAVO", "CHARLIE")):
            user, store = register_user(
                f"{label.lower()}@example.com", PASSWORD, PASSWORD, f"{label} Store"
            )
            self.users.append(user)
            self.stores.append(store)
            with app.test_request_context("/"):
                g.store = {"id": store}
                g.user = {"id": user}
                g.is_demo = False
                shared = create_product("Shared name", str(index + 1), 100)
                private = create_product(f"Only {label} Product", str((index + 1) * 2.5), 100)
            self.products.append((shared, private))
            client = app.test_client()
            login(client, f"{label.lower()}@example.com")
            self.clients.append(client)
            scale = index + 1
            self.insert_sale(
                index,
                "2026-10-14 23:59:59",
                [
                    (shared, 3, 100 * scale, "Shared name"),
                    (private, 2, 250 * scale, f"Only {label} Product"),
                ],
            )
            self.insert_sale(
                index, "2026-10-15 00:00:00", [(shared, 1, 100 * scale, "Shared name")]
            )
            self.insert_sale(
                index, "2026-10-15 23:59:59", [(private, 1, 250 * scale, f"Only {label} Product")]
            )
        self.frozen = patch(
            "services.analytics.business_now", return_value=datetime(2026, 10, 15, 12)
        )
        self.frozen.start()

    def tearDown(self):
        self.frozen.stop()
        app.config.clear()
        app.config.update(self.original_config)
        db.DB_FILE = self.original_db
        self.tmp.cleanup()

    def query(self, statement, params=()):
        with closing(db.get_db_connection()) as conn:
            return [dict(row) for row in conn.execute(statement, params)]

    def insert_sale(self, index, stamp, items, total=None, payment="Card", rate="8.25"):
        subtotal = sum(quantity * price for _, quantity, price, _ in items)
        total = subtotal + calculate_tax_cents(subtotal, TAX_RATE) if total is None else total
        with closing(db.get_db_connection()) as conn:
            with conn:
                tx = conn.execute(
                    "INSERT INTO transactions(timestamp,total,total_cents,payment_method,tax_rate_percent,store_id) VALUES (?,?,?,?,?,?)",
                    (stamp, total / 100, total, payment, rate, self.stores[index]),
                ).lastrowid
                for product, quantity, price, name in items:
                    conn.execute(
                        "INSERT INTO transaction_items(transaction_id,product_id,quantity,price,price_cents,product_name,store_id) VALUES (?,?,?,?,?,?,?)",
                        (tx, product, quantity, price / 100, price, name, self.stores[index]),
                    )
        self.sale_ids.append(tx)
        return tx

    def data(self, index=0, **args):
        with app.test_request_context("/"):
            g.store = {"id": self.stores[index]}
            g.user = {"id": self.users[index]}
            g.is_demo = False
            return build_analytics(resolve_filters(args, today=TODAY))

    def test_three_accounts_summaries_and_no_cross_store_response_data(self):
        for index, client in enumerate(self.clients):
            data = self.data(index, preset="today")
            expected = sum(
                row["total_cents"]
                for row in self.query(
                    "SELECT total_cents FROM transactions WHERE store_id=? AND timestamp>=? AND timestamp<?",
                    (self.stores[index], "2026-10-15 00:00:00", "2026-10-16 00:00:00"),
                )
            )
            self.assertEqual(data["summary"]["revenue_cents"], expected)
            self.assertEqual(data["summary"]["transaction_count"], 2)
            self.assertEqual(data["summary"]["item_sales_cents"], 350 * (index + 1))
            self.assertEqual(data["summary"]["unverified_tax_transactions"], 0)
            response = client.get("/analytics?preset=today")
            self.assertEqual(response.status_code, 200)
            self.assertIn(
                ("Only " + ("ALPHA", "BRAVO", "CHARLIE")[index] + " Product").encode(),
                response.data,
            )
            for other in set(range(3)) - {index}:
                self.assertNotIn(
                    ("Only " + ("ALPHA", "BRAVO", "CHARLIE")[other] + " Product").encode(),
                    response.data,
                )
                self.assertNotIn(
                    (("ALPHA", "BRAVO", "CHARLIE")[other] + " Store").encode(), response.data
                )

    def test_daily_weekly_monthly_zero_buckets_and_reconciliation(self):
        for group, count in (("daily", 7), ("weekly", 2), ("monthly", 1)):
            data = self.data(preset="last7", group=group)
            self.assertEqual(len(data["series"]), count)
            self.assertEqual(
                sum(row["revenue_cents"] for row in data["series"]),
                data["summary"]["revenue_cents"],
            )
            self.assertEqual(sum(row["transaction_count"] for row in data["series"]), 3)
            self.assertTrue(all(type(row["revenue_cents"]) is int for row in data["series"]))
        data = self.data(
            preset="custom", start_date="2026-09-28", end_date="2026-10-15", group="monthly"
        )
        self.assertEqual(data["series"][0]["revenue_cents"], 0)
        self.assertEqual(data["series"][0]["period_start"], "2026-09-28")
        self.assertEqual(data["series"][0]["period_end"], "2026-09-30")
        summary = data["summary"]
        self.assertEqual(
            summary["item_sales_cents"] + summary["tax_remainder_cents"], summary["revenue_cents"]
        )
        self.assertEqual(
            sum(row["item_sales_cents"] for row in data["products"]), summary["item_sales_cents"]
        )
        self.assertEqual(
            sum(row["revenue_cents"] for row in data["payment_rows"]), summary["revenue_cents"]
        )

    def test_average_uses_transaction_count_and_half_up(self):
        # Use a fresh date rather than altering existing history.
        self.insert_sale(0, "2026-08-01 10:00:00", [], total=100, rate=None)
        self.insert_sale(0, "2026-08-01 11:00:00", [], total=101, rate=None)
        data = self.data(preset="custom", start_date="2026-08-01", end_date="2026-08-01")
        self.assertEqual(data["summary"]["average_order_cents"], 101)
        self.assertEqual(data["summary"]["revenue_cents"], 201)
        self.assertEqual(data["summary"]["unverified_tax_transactions"], 2)
        self.assertEqual(data["summary"]["tax_remainder_cents"], 201)

    def test_empty_period_and_previous_comparison_without_baseline(self):
        data = self.data(preset="custom", start_date="2026-07-01", end_date="2026-07-07")
        self.assertEqual(data["summary"]["revenue_cents"], 0)
        self.assertEqual(data["summary"]["average_order_cents"], 0)
        self.assertEqual(len(data["series"]), 7)
        self.assertFalse(data["products"])
        self.assertIsNone(data["comparison"]["percent"])
        response = self.clients[0].get(
            "/analytics?preset=custom&start_date=2026-07-01&end_date=2026-07-07"
        )
        self.assertIn(b"No sales in this period", response.data)
        self.assertIn(b"No prior-period baseline", response.data)
        data = self.data(preset="today")
        self.assertEqual(data["comparison"]["previous_cents"], 866)
        self.assertEqual(data["comparison"]["change_cents"], 379 - 866)
        self.assertIsInstance(data["comparison"]["percent"], Decimal)

    def test_every_preset_and_strict_custom_ranges(self):
        expected = {
            "today": ("2026-10-15", "2026-10-15"),
            "yesterday": ("2026-10-14", "2026-10-14"),
            "last7": ("2026-10-09", "2026-10-15"),
            "last30": ("2026-09-16", "2026-10-15"),
            "this_month": ("2026-10-01", "2026-10-15"),
            "previous_month": ("2026-09-01", "2026-09-30"),
        }
        for preset, dates in expected.items():
            filters = resolve_filters({"preset": preset}, today=TODAY)
            self.assertEqual((filters.start.isoformat(), filters.end.isoformat()), dates)
            self.assertEqual(self.data(preset=preset)["filters"].preset, preset)
            self.assertEqual(self.clients[0].get("/analytics?preset=" + preset).status_code, 200)
        for query in (
            "preset=bad",
            "preset=custom&start_date=bad&end_date=2026-10-15",
            "preset=custom&start_date=2026-10-20&end_date=2026-10-15",
            "preset=custom&start_date=2020-01-01&end_date=2026-10-15",
            "preset=custom&start_date=20260201&end_date=2026-10-15",
            "preset=custom&start_date=0001-01-01&end_date=0001-01-02",
            "top=5000",
            "group=yearly",
            "ranking=revenue;DROP%20TABLE%20transactions",
        ):
            self.assertEqual(self.clients[0].get("/analytics?" + query).status_code, 400)
            self.assertEqual(
                self.clients[0].get("/analytics/export/summary.csv?" + query).status_code, 400
            )
        self.assertEqual(len(self.query("SELECT * FROM transactions")), 9)

    def test_midnight_and_dst_business_date_boundaries(self):
        for day in ("2026-03-08", "2026-11-01"):
            next_day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
            self.insert_sale(0, day + " 00:00:00", [], total=100, rate=None)
            self.insert_sale(0, day + " 01:30:00", [], total=200, rate=None)
            self.insert_sale(0, day + " 01:30:00", [], total=300, rate=None)
            self.insert_sale(0, day + " 23:59:59", [], total=400, rate=None)
            self.insert_sale(0, next_day + " 00:00:00", [], total=900, rate=None)
            data = self.data(preset="custom", start_date=day, end_date=day)
            self.assertEqual(data["summary"]["revenue_cents"], 1000)
            self.assertEqual(data["summary"]["transaction_count"], 4)
            utc_start, utc_end = data["filters"].utc_boundaries()
            hours = (
                datetime.fromisoformat(utc_end) - datetime.fromisoformat(utc_start)
            ).total_seconds() / 3600
            self.assertEqual(hours, 23 if day == "2026-03-08" else 25)

    def test_historical_prices_renames_deactivation_and_stable_product_identity(self):
        before = self.data(preset="last7")
        with app.test_request_context("/"):
            g.store = {"id": self.stores[0]}
            g.is_demo = False
            update_product_details(self.products[0][0], "Current unrelated name", "999")
            deactivate_product(self.products[0][0])
        after = self.data(preset="last7")
        self.assertEqual(before["summary"], after["summary"])
        self.assertEqual(before["products"], after["products"])
        self.insert_sale(
            0, "2026-10-15 18:00:00", [(self.products[0][0], 1, 123, "Latest historical snapshot")]
        )
        after = self.data(preset="last7")
        shared = [row for row in after["products"] if row["product_id"] == self.products[0][0]]
        self.assertEqual(len(shared), 1)
        # 23:59:59 is the other product; this product's latest recorded name is 18:00.
        self.assertEqual(shared[0]["product_name"], "Latest historical snapshot")
        self.assertEqual(shared[0]["item_sales_cents"], 523)
        self.assertNotIn("Current unrelated name", str(after["products"]))

    def test_quantity_revenue_rankings_ties_and_top_limits(self):
        quantity = self.data(preset="last7", ranking="quantity")["products"]
        revenue = self.data(preset="last7", ranking="revenue")["products"]
        self.assertEqual(quantity[0]["product_id"], self.products[0][0])
        self.assertEqual(revenue[0]["product_id"], self.products[0][1])
        with app.test_request_context("/"):
            g.store = {"id": self.stores[0]}
            g.is_demo = False
            ids = [create_product("Tie " + str(n), "1", 1) for n in range(11)]
        for product in ids:
            self.insert_sale(0, "2026-10-15 10:00:00", [(product, 10, 100, "Tie name")])
        data = self.data(preset="last7", top="5")
        self.assertEqual([row["product_id"] for row in data["products"]], ids[:5])
        self.assertEqual(len(self.data(preset="last7", top="10")["products"]), 10)
        self.assertEqual(self.clients[0].get("/analytics?ranking=name&top=5").status_code, 200)

    def test_all_csv_types_filters_metadata_mime_and_financial_totals(self):
        for index, client in enumerate(self.clients):
            data = self.data(index, preset="today")
            for kind, header in (
                ("summary", "Metric"),
                ("periods", "Period start (inclusive)"),
                ("products", "Rank"),
                ("transactions", "Transaction ID"),
            ):
                response = client.get(
                    f"/analytics/export/{kind}.csv?preset=today&group=daily&top=5"
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn("text/csv", response.content_type)
                self.assertIn("attachment;", response.headers["Content-Disposition"])
                self.assertIn("no-store", response.headers["Cache-Control"])
                self.assertIn("nosniff", response.headers["X-Content-Type-Options"])
                rows = csv_rows(response)
                self.assertIn(["Store ID", str(self.stores[index])], rows)
                self.assertIn(["Start date (inclusive)", "2026-10-15"], rows)
                for other in set(range(3)) - {index}:
                    self.assertNotIn(
                        (("ALPHA", "BRAVO", "CHARLIE")[other] + " Store").encode(), response.data
                    )
                    self.assertNotIn(
                        ("Only " + ("ALPHA", "BRAVO", "CHARLIE")[other] + " Product").encode(),
                        response.data,
                    )
                body = table_rows(response, header)
                if kind == "summary":
                    self.assertEqual(
                        dict(body)["Recorded sale totals USD (tax included)"],
                        f"{Decimal(data['summary']['revenue_cents']) / 100:.2f}",
                    )
                elif kind == "periods":
                    self.assertEqual(
                        sum(Decimal(row[2]) * 100 for row in body), data["summary"]["revenue_cents"]
                    )
                elif kind == "transactions":
                    self.assertEqual(
                        sum(Decimal(row[3]) * 100 for row in body), data["summary"]["revenue_cents"]
                    )
                    self.assertEqual(len(body), 2)
                else:
                    self.assertEqual(
                        sum(Decimal(row[4]) * 100 for row in body),
                        data["summary"]["item_sales_cents"],
                    )

    def test_csv_injection_quoting_unicode_and_no_unnecessary_private_fields(self):
        name = '  =HYPERLINK("https://example.com"), café\nfictional'
        with closing(db.get_db_connection()) as conn:
            with conn:
                conn.execute("UPDATE stores SET name=? WHERE id=?", ("@SUM(1,2)", self.stores[0]))
        self.insert_sale(0, "2026-10-15 13:00:00", [(self.products[0][0], 1, 100, name)])
        response = self.clients[0].get("/analytics/export/products.csv?preset=today")
        rows = csv_rows(response)
        self.assertIn(["Store name", "'@SUM(1,2)"], rows)
        names = [row[2] for row in table_rows(response, "Rank")]
        # Later line at midnight only precedes the 13:00 snapshot for this product.
        self.assertIn("'" + name, names)
        for value in ("=1+1", "+cmd", "-cmd", "@SUM(A1)", "\t=1", "\n=1", "\ufeff=1"):
            self.assertTrue(spreadsheet_text(value).startswith("'"))
        for forbidden in (b"password_hash", b"login_token", b"csrf_token", b"alpha@example.com"):
            self.assertNotIn(forbidden, response.data)
        self.assertNotIn("/Users/", response.headers["Content-Disposition"])

    def test_anonymous_downloads_and_unknown_formats(self):
        client = app.test_client()
        for path in (
            "/analytics",
            "/analytics/export/summary.csv",
            "/analytics/export/products.csv",
            "/analytics/export/transactions.csv",
            "/analytics/export/periods.csv",
        ):
            response = client.get(path)
            self.assertEqual(response.status_code, 302)
            self.assertIn("/login", response.location)
            self.assertNotIn(b"ALPHA", response.data)
        self.assertEqual(self.clients[0].get("/analytics/export/unknown.csv").status_code, 404)
        self.assertEqual(self.clients[0].post("/analytics", data={}).status_code, 400)

    def test_foreign_ids_and_combined_filters_cannot_override_store(self):
        baseline = self.data(preset="today")["summary"]
        query = f"preset=today&group=weekly&ranking=revenue&top=5&store_id={self.stores[1]}&product_id={self.products[1][0]}&transaction_id={self.sale_ids[3]}"
        response = self.clients[0].get("/analytics?" + query)
        self.assertNotIn(b"BRAVO", response.data)
        exported = self.clients[0].get("/analytics/export/summary.csv?" + query)
        self.assertIn(["Store ID", str(self.stores[0])], csv_rows(exported))
        self.assertEqual(
            dict(table_rows(exported, "Metric"))["Recorded sale totals USD (tax included)"],
            f"{Decimal(baseline['revenue_cents']) / 100:.2f}",
        )
        self.assertNotIn(
            b"Only BRAVO Product",
            self.clients[0].get(f"/transactions/{self.sale_ids[3]}", follow_redirects=True).data,
        )
        self.insert_sale(
            1, "2026-10-15 12:00:00", [(self.products[1][0], 50, 999, "Only BRAVO large sale")]
        )
        self.assertEqual(baseline, self.data(preset="today")["summary"])

    def test_switch_accounts_logout_and_demo_isolation(self):
        login(self.clients[0], "charlie@example.com")
        response = self.clients[0].get("/analytics/export/products.csv?preset=today")
        self.assertIn(b"Only CHARLIE Product", response.data)
        self.assertNotIn(b"Only ALPHA Product", response.data)
        seed_demo(allow_existing=True)
        client = self.clients[0]
        client.get("/login")
        with client.session_transaction() as state:
            csrf = state["csrf_token"]
        self.assertEqual(client.post("/demo", data={"csrf_token": csrf}).status_code, 302)
        response = client.get("/analytics")
        self.assertIn(b"Demo Notebook", response.data)
        self.assertNotIn(b"Only CHARLIE Product", response.data)
        self.assertNotIn(b"Only ALPHA Product", client.get("/analytics/export/products.csv").data)
        self.assertEqual(client.post("/sale/checkout", data={}).status_code, 403)
        client.get("/login")
        with client.session_transaction() as state:
            csrf = state["csrf_token"]
        client.post("/logout", data={"csrf_token": csrf})
        self.assertEqual(client.get("/analytics/export/summary.csv").status_code, 302)

    def test_dashboard_legacy_reports_and_analytics_reconcile(self):
        with patch("web_app.business_now", return_value=datetime(2026, 10, 15, 12)):
            with patch("web_app.render_template", return_value="ok") as render:
                self.clients[0].get("/")
            dashboard = render.call_args.kwargs
            data = self.data(preset="today")
            self.assertEqual(dashboard["today_sales_cents"], data["summary"]["revenue_cents"])
            self.assertEqual(
                dashboard["today_transaction_count"], data["summary"]["transaction_count"]
            )
            with patch("web_app.render_template", return_value="ok") as render:
                self.clients[0].get("/reports?start_date=2026-10-15&end_date=2026-10-15")
            self.assertEqual(
                render.call_args.kwargs["overview"]["revenue_cents"],
                data["summary"]["revenue_cents"],
            )
        with app.test_request_context("/"):
            with self.assertRaises(PermissionError):
                build_analytics(resolve_filters({"preset": "today"}, today=TODAY))

    def test_read_only_analytics_and_idempotent_checkout(self):
        with app.test_request_context("/"), patch("services.transaction_service.business_now", return_value=datetime(2026,10,15,12)):
            g.store = {"id": self.stores[0]}
            g.is_demo = False
            tx = complete_transaction(
                [{"id": self.products[0][0], "qty": 1}],
                "1.08",
                "Card",
                checkout_token="analytics-repeat",
            )
            self.assertEqual(
                complete_transaction(
                    [{"id": self.products[0][0], "qty": 1}],
                    "1.08",
                    "Card",
                    checkout_token="analytics-repeat",
                ),
                tx,
            )
        before = self.query("SELECT * FROM transactions ORDER BY id")
        items = self.query("SELECT * FROM transaction_items ORDER BY id")
        data = self.data(preset="today")
        self.assertEqual(data["summary"]["transaction_count"], 3)
        for path in (
            "/analytics?preset=today",
            "/analytics/export/summary.csv?preset=today",
            "/reports",
        ):
            self.assertEqual(self.clients[0].get(path).status_code, 200)
        self.assertEqual(before, self.query("SELECT * FROM transactions ORDER BY id"))
        self.assertEqual(items, self.query("SELECT * FROM transaction_items ORDER BY id"))

    def test_limits_fail_without_silent_truncation(self):
        with patch("services.analytics.MAX_TRANSACTIONS", 1):
            self.assertEqual(self.clients[0].get("/analytics?preset=last7").status_code, 413)
            self.assertEqual(
                self.clients[0].get("/analytics/export/transactions.csv?preset=last7").status_code,
                413,
            )
        with patch("services.analytics.MAX_ITEMS", 1):
            with self.assertRaises(ReportLimitError):
                self.data(preset="last7")

    def test_index_migration_repeatability_and_bounded_realistic_dataset(self):
        with closing(db.get_db_connection()) as conn:
            with conn:
                for index in range(1200):
                    tx = conn.execute(
                        "INSERT INTO transactions(timestamp,total,total_cents,payment_method,store_id) VALUES ('2026-10-15 12:00:00',1.08,108,'Card',?)",
                        (self.stores[0],),
                    ).lastrowid
                    conn.execute(
                        "INSERT INTO transaction_items(transaction_id,product_id,quantity,price,price_cents,product_name,store_id) VALUES (?,?,1,1,100,?,?)",
                        (tx, self.products[0][0], "Shared name", self.stores[0]),
                    )
            before = [dict(row) for row in conn.execute("SELECT * FROM transactions ORDER BY id")]
            migrate_analytics(conn)
            conn.commit()
            plan = " ".join(
                str(tuple(row))
                for row in conn.execute(
                    "EXPLAIN QUERY PLAN SELECT id FROM transactions WHERE store_id=? AND timestamp>=? AND timestamp<? ORDER BY timestamp,id",
                    (self.stores[0], "2026-10-15 00:00:00", "2026-10-16 00:00:00"),
                )
            )
            self.assertIn("transactions_store_time", plan)
            item_plan = " ".join(
                str(tuple(row))
                for row in conn.execute(
                    "EXPLAIN QUERY PLAN SELECT id FROM transaction_items WHERE store_id=? AND transaction_id=?",
                    (self.stores[0], self.sale_ids[0]),
                )
            )
            self.assertIn("items_store_transaction", item_plan)
        start = clock.perf_counter()
        data = self.data(preset="today")
        elapsed = clock.perf_counter() - start
        self.assertEqual(data["summary"]["transaction_count"], 1202)
        self.assertEqual(data["summary"]["revenue_cents"], 1200 * 108 + 379)
        self.assertEqual(before, self.query("SELECT * FROM transactions ORDER BY id"))
        self.assertLess(elapsed, 10)  # Generous regression guard, not a latency SLA.
        self.assertTrue(check_database()[0])

    def test_pre16_migration_preserves_every_financial_field_and_store(self):
        db.DB_FILE = str(Path(self.tmp.name) / "pre16.db")
        with closing(db.get_db_connection()) as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                db._initialize_schema(conn, True)
                migrate_identity(conn)
                migrate_inventory(conn)
            products = [dict(row) for row in conn.execute("SELECT * FROM products ORDER BY id")]
        db.initialize_database(seed=False)
        db.initialize_database(seed=False)
        self.assertEqual(products, self.query("SELECT * FROM products ORDER BY id"))
        self.assertEqual(
            [
                row["version"]
                for row in self.query("SELECT version FROM schema_migrations ORDER BY version")
            ],
            [14, 15, 16, 17, 20, 21],
        )
        self.assertTrue(check_database()[0])

    def test_xss_and_chart_asset_safe_rendering(self):
        self.insert_sale(
            0, "2026-10-15 16:00:00", [(self.products[0][0], 1, 100, "<script>alert(1)</script>")]
        )
        response = self.clients[0].get("/analytics?preset=today")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"<script>alert(1)</script>", response.data)
        self.assertIn(b"&lt;script&gt;", response.data)
        self.assertIn(b'aria-live="polite"', response.data)
        self.assertIn(b"aria-label=", response.data)
        self.assertIn(b"/static/js/analytics.js", response.data)
        self.assertIn("no-store", response.headers["Cache-Control"])
