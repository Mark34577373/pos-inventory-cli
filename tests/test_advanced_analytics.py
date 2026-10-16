"""Deterministic Day 21 financial reconciliation and three-account real HTTP tests."""

import csv
import io
import secrets
import time
import unittest
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

import test_settings as settings_fixture
from auth_helpers import login
from flask import g

from database import database as db
from manage import seed_demo
from services.analytics import ReportLimitError, build_analytics, resolve_filters
from services.inventory import create_product, update_product_record
from services.inventory_management import delete_category, save_category
from services.money import calculate_tax_cents, from_cents
from services.refunds import get_sale, process_refund
from services.settings import get_settings, save_settings
from services.transaction_service import complete_transaction
from web_app import app


class AdvancedAnalyticsTests(unittest.TestCase):
    setUp = settings_fixture.SettingsTests.setUp
    tearDown = settings_fixture.SettingsTests.tearDown

    @contextmanager
    def context(self, index=0):
        with app.test_request_context("/"):
            user, store = self.accounts[index]
            g.user, g.store, g.is_demo = {"id": user}, {"id": store}, False
            yield

    def sale(self, index, timestamp, lines, method="Card"):
        with (
            self.context(index),
            patch(
                "services.transaction_service.business_now",
                return_value=datetime.fromisoformat(timestamp),
            ),
        ):
            with closing(db.get_db_connection()) as conn:
                subtotal = sum(
                    conn.execute(
                        "SELECT price_cents FROM products WHERE id=? AND store_id=?",
                        (pid, self.accounts[index][1]),
                    ).fetchone()[0]
                    * qty
                    for pid, qty in lines
                )
            total = subtotal + calculate_tax_cents(subtotal, get_settings()["tax_rate"])
            return complete_transaction(
                [{"id": pid, "qty": qty} for pid, qty in lines], from_cents(total), method
            )

    def data(self, index=0, **args):
        args = {"start_date": "2026-10-01", "end_date": "2026-10-31", **args}
        with self.context(index):
            return build_analytics(resolve_filters(args, today=date(2026, 10, 15)))

    def fixture(self, index=0):
        with self.context(index):
            save_settings("tax", {"revision": "0", "tax_percent": "10"})
            category = save_category(f"PRIVATE-CATEGORY-{index}")
            tiny = create_product(f"PRIVATE-TINY-{index}", ".05", 100, category_id=category)
            big = create_product(f"PRIVATE-BIG-{index}", "1.00", 100)
            slow = create_product(f"PRIVATE-SLOW-{index}", "5", 5)
        old = self.sale(index, "2026-09-30 23:59:59", [(tiny, 2)])
        new = self.sale(index, "2026-10-15 10:00:00", [(tiny, 1), (big, 1)])
        with self.context(index):
            line = get_sale(old)["items"][0]["id"]
            # Original two-unit tax allocation is 1 then 0 cents.
            with patch("services.refunds.business_now", return_value=datetime(2026, 10, 2)):
                process_refund(
                    old, {line: 1}, "Fictional partial one", False, secrets.token_urlsafe(24)
                )
            with patch("services.refunds.business_now", return_value=datetime(2026, 10, 15)):
                process_refund(
                    old, {line: 1}, "Fictional partial two", False, secrets.token_urlsafe(24)
                )
        return category, tiny, big, slow, old, new

    def test_financial_metrics_all_groupings_refund_dates_and_zero_baselines(self):
        self.fixture()
        for group in ("hourly", "daily", "weekly", "monthly"):
            with self.subTest(group=group):
                data = self.data(group=group)
                summary = data["summary"]
                self.assertEqual(
                    (
                        summary["revenue_cents"],
                        summary["refunded_cents"],
                        summary["net_sales_cents"],
                    ),
                    (116, 11, 105),
                )
                self.assertEqual(
                    (
                        summary["units_sold"],
                        summary["units_refunded"],
                        summary["average_order_cents"],
                    ),
                    (2, 2, 116),
                )
                self.assertEqual(
                    (summary["verified_tax_cents"], summary["refunded_tax_cents"]), (11, 1)
                )
                self.assertEqual(sum(row["revenue_cents"] for row in data["series"]), 116)
                self.assertEqual(sum(row["refunded_cents"] for row in data["series"]), 11)
                self.assertEqual(sum(row["net_sales_cents"] for row in data["series"]), 105)
                self.assertEqual(sum(row["item_sales_cents"] for row in data["category_rows"]), 105)
                self.assertEqual(
                    sum(row["item_refunds_cents"] for row in data["category_rows"]), 10
                )
                self.assertEqual(sum(row["units_refunded"] for row in data["performance_rows"]), 2)
                self.assertEqual(
                    sum(row["item_sales_cents"] for row in data["product_trends"]), 105
                )
                self.assertEqual(
                    sum(row["item_sales_cents"] for row in data["category_trends"]), 105
                )
                self.assertEqual(
                    sum(row["item_refunds_cents"] for row in data["category_trends"]), 10
                )
        hourly = self.data(group="hourly")
        self.assertEqual(len(hourly["series"]), 24)
        self.assertEqual(hourly["series"][10]["revenue_cents"], 116)
        # Older sale refunds count as movements, never a misleading >100% cohort rate.
        tiny = next(r for r in hourly["performance_rows"] if "TINY" in r["product_name"])
        self.assertEqual(tiny["refund_rate_percent"], Decimal(0))
        empty = self.data(start_date="2020-01-01", end_date="2020-01-01")
        self.assertEqual(empty["summary"]["net_sales_cents"], 0)
        self.assertTrue(all(r["percent"] is None for r in empty["comparisons"]))

    def test_historical_category_unknown_and_product_renames(self):
        category, tiny, *_ = self.fixture()
        before = self.data()["category_rows"]
        with self.context():
            other = save_category("NEW CATEGORY")
            update_product_record(tiny, "RENAMED CURRENT PRODUCT", ".50", 97, category_id=other)
            save_settings("tax", {"revision": "1", "tax_percent": "20"})
        self.assertEqual(before, self.data()["category_rows"])
        self.assertIn(
            "PRIVATE-TINY-0", [r["product_name"] for r in self.data()["performance_rows"]]
        )
        with closing(db.get_db_connection()) as conn, conn:
            tx = conn.execute(
                "INSERT INTO transactions(timestamp,total,total_cents,payment_method,store_id) VALUES('2026-10-20 00:00:00',1,100,'Unknown',?)",
                (self.accounts[0][1],),
            ).lastrowid
            conn.execute(
                "INSERT INTO transaction_items(transaction_id,product_id,quantity,price,price_cents,product_name,store_id) VALUES(?,?,1,1,100,?,?)",
                (tx, tiny, "Legacy name", self.accounts[0][1]),
            )
        unknown = self.data(category="unknown")
        self.assertEqual(unknown["summary"]["revenue_cents"], 100)
        self.assertEqual(unknown["category_rows"][0]["category"], "unknown")
        response = self.clients[0].get(f"/transactions/{tx}/receipt")
        self.assertIn(b"Original business profile was not recorded", response.data)
        self.assertEqual(self.data(category=str(category))["summary"]["refunded_cents"], 11)

    def test_deleted_category_remains_a_safe_historical_filter(self):
        category, *_ = self.fixture()
        before = self.data(category=str(category))
        with self.context():
            delete_category(category)
        after = self.data(category=str(category))
        self.assertEqual(before["category_rows"], after["category_rows"])
        self.assertIn(category, [row["id"] for row in after["category_options"]])
        response = self.clients[0].get(
            f"/analytics?start_date=2026-10-01&end_date=2026-10-31&category={category}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'value="{category}" selected'.encode(), response.data)
        self.assertEqual(self.clients[1].get(f"/analytics?category={category}").status_code, 400)

    def test_three_account_filters_exports_anonymous_and_no_side_effects(self):
        fixtures = [self.fixture(index) for index in range(3)]
        with closing(db.get_db_connection()) as conn:
            before = [tuple(r) for r in conn.execute("SELECT * FROM transactions")]
        kinds = (
            "summary",
            "periods",
            "products",
            "transactions",
            "refunds",
            "categories",
            "performance",
            "comparisons",
            "category-trends",
            "product-trends",
        )
        for index, client in enumerate(self.clients):
            for other in set(range(3)) - {index}:
                for key, foreign in (
                    ("product", fixtures[other][1]),
                    ("category", fixtures[other][0]),
                ):
                    self.assertEqual(client.get(f"/analytics?{key}={foreign}").status_code, 400)
                    self.assertEqual(
                        client.get(f"/analytics/export/categories.csv?{key}={foreign}").status_code,
                        400,
                    )
                    with self.context(index), self.assertRaises(ValueError):
                        build_analytics(resolve_filters({key: str(foreign)}))
            for kind in kinds:
                response = client.get(
                    f"/analytics/export/{kind}.csv?start_date=2026-10-01&end_date=2026-10-31&product={fixtures[index][1]}&store_id={self.accounts[(index + 1) % 3][1]}"
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"attachment;", response.headers["Content-Disposition"].encode())
                self.assertIn(b"Whole baskets", response.data)
                for other in set(range(3)) - {index}:
                    self.assertNotIn(f"PRIVATE-TINY-{other}".encode(), response.data)
                    self.assertNotIn(f"PRIVATE-CATEGORY-{other}".encode(), response.data)
            response = client.get(
                "/analytics?start_date=2026-10-01&end_date=2026-10-31&group=hourly"
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn(f"PRIVATE-TINY-{index}".encode(), response.data)
        for kind in kinds:
            self.assertEqual(
                app.test_client().get(f"/analytics/export/{kind}.csv").status_code, 302
            )
        with closing(db.get_db_connection()) as conn:
            self.assertEqual(before, [tuple(r) for r in conn.execute("SELECT * FROM transactions")])

    def test_filters_whole_basket_cohort_and_csv_reconciliation(self):
        category, tiny, *_ = self.fixture()
        baseline = self.data()
        for key, value in (
            ("product", str(tiny)),
            ("category", str(category)),
            ("payment", "Card"),
        ):
            data = self.data(**{key: value})
            self.assertEqual(data["summary"], baseline["summary"])
            self.assertEqual(data["filters"].previous.product, data["filters"].product)
            self.assertEqual(data["filters"].previous.category, data["filters"].category)
        self.assertEqual(self.data(payment="Cash")["summary"]["transaction_count"], 0)
        for kind, header, column, expected in (
            ("categories", "Historical category", 3, 105),
            ("performance", "Product ID", 4, 105),
            ("category-trends", "Historical category", 2, 105),
            ("product-trends", "Product ID", 3, 105),
        ):
            response = self.clients[0].get(
                f"/analytics/export/{kind}.csv?start_date=2026-10-01&end_date=2026-10-31"
            )
            rows = list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))
            start = next(i for i, row in enumerate(rows) if row and row[0] == header)
            self.assertEqual(
                sum(int(Decimal(row[column]) * 100) for row in rows[start + 1 :]), expected
            )
        for bad in (
            {"product": "-1"},
            {"category": "1 OR 1=1"},
            {"payment": "Wire"},
            {"group": "minute"},
            {"product": "true"},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                resolve_filters(bad)

    def test_calendar_comparisons_and_inventory_estimate_eligibility(self):
        category, tiny, big, slow, *_ = self.fixture()
        week = resolve_filters({"preset": "this_week"}, today=date(2026, 10, 15))
        self.assertEqual(
            (week.start, week.end, week.previous.start, week.previous.end),
            (date(2026, 10, 12), date(2026, 10, 18), date(2026, 10, 5), date(2026, 10, 11)),
        )
        month = resolve_filters({"preset": "calendar_month"}, today=date(2026, 10, 15))
        self.assertEqual(
            (month.start, month.end, month.previous.start, month.previous.end),
            (date(2026, 10, 1), date(2026, 10, 31), date(2026, 9, 1), date(2026, 9, 30)),
        )
        reconstructed = resolve_filters(month.query_args(), today=date(2027, 1, 1))
        self.assertEqual(
            (
                reconstructed.start,
                reconstructed.end,
                reconstructed.previous.start,
                reconstructed.previous.end,
            ),
            (month.start, month.end, month.previous.start, month.previous.end),
        )
        with self.context():
            report = build_analytics(month)
        self.assertEqual(report["filters"].previous.end, date(2026, 9, 30))
        from services.sales_exports import create_csv

        exported = create_csv(report, "comparisons")
        self.assertIn(b"Comparison end (inclusive),2026-09-30", exported)
        self.assertIn(b"Previous full calendar month", exported)
        # Fictional opening history is set at fixture creation, before recording sales.
        # Move the test clock forward instead of rewriting immutable audit history.
        with patch("services.analytics_details.datetime") as clock:
            clock.now.return_value = datetime(2027, 1, 1, tzinfo=timezone.utc)
            clock.fromisoformat.side_effect = datetime.fromisoformat
            data = self.data(start_date="2026-10-01", end_date="2026-10-07")
            self.assertTrue(all(row["velocity"] is None for row in data["inventory_intelligence"]))
        current = baseline = self.data()["inventory_summary"]
        self.assertEqual(current["units_on_hand"], 201)
        self.assertEqual(current["low_stock_count"], 1)
        filtered = self.data(product=str(tiny))
        self.assertTrue(all(row["velocity"] is None for row in filtered["inventory_intelligence"]))
        self.assertLess(filtered["inventory_summary"]["units_on_hand"], baseline["units_on_hand"])

    def test_forecasts_require_complete_known_history_and_actual_demand(self):
        with self.context():
            pid = create_product("Fictional forecast demand", "1", 100)
            idle = create_product("Fictional zero demand", "1", 5)
        with closing(db.get_db_connection()) as conn:
            opening = conn.execute(
                "SELECT recorded_at FROM stock_adjustments WHERE product_id=? AND adjustment_type='initial'",
                (pid,),
            ).fetchone()[0]
        start = datetime.fromisoformat(opening).astimezone(
            ZoneInfo("America/Chicago")
        ).date() + timedelta(days=1)
        end = start + timedelta(days=6)
        self.sale(0, start.isoformat() + " 10:00:00", [(pid, 7)])
        with patch("services.analytics_details.datetime") as clock:
            clock.now.return_value = datetime.combine(
                end + timedelta(days=1), datetime.min.time(), timezone.utc
            )
            clock.fromisoformat.side_effect = datetime.fromisoformat
            data = self.data(start_date=start.isoformat(), end_date=end.isoformat())
        rows = {r["product_id"]: r for r in data["inventory_intelligence"]}
        self.assertEqual(rows[pid]["velocity"], Decimal("1.00"))
        self.assertEqual(rows[pid]["days_remaining"], Decimal("93.0"))
        self.assertTrue(rows[idle]["history_known"])
        self.assertTrue(rows[idle]["slow_moving"])
        self.assertIsNone(rows[idle]["velocity"])

    def test_demo_and_account_switch_keep_new_exports_isolated(self):
        self.fixture(0)
        self.fixture(1)
        seed_demo(allow_existing=True)
        client = self.clients[0]
        login(client, "settings-b@example.com")
        response = client.get(
            "/analytics/export/performance.csv?start_date=2026-10-01&end_date=2026-10-31"
        )
        self.assertIn(b"PRIVATE-TINY-1", response.data)
        self.assertNotIn(b"PRIVATE-TINY-0", response.data)
        client.get("/login")
        with client.session_transaction() as state:
            token = state["csrf_token"]
        self.assertEqual(client.post("/demo", data={"csrf_token": token}).status_code, 302)
        for kind in (
            "categories",
            "performance",
            "comparisons",
            "category-trends",
            "product-trends",
        ):
            response = client.get(f"/analytics/export/{kind}.csv")
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(b"PRIVATE-", response.data)
        response = client.get("/settings")
        self.assertNotIn(b"PRIVATE-", response.data)
        self.assertEqual(
            client.post(
                "/settings/tax", data={"csrf_token": token, "tax_percent": "99", "revision": "0"}
            ).status_code,
            403,
        )

    def test_bounded_report_and_representative_performance(self):
        with self.context():
            pid = create_product("Fictional benchmark", "1", 10000)
        start = time.perf_counter()
        with closing(db.get_db_connection()) as conn, conn:
            for number in range(1200):
                tx = conn.execute(
                    "INSERT INTO transactions(timestamp,total,total_cents,payment_method,tax_rate_percent,store_id) VALUES('2026-10-15 10:00:00',1.08,108,'Card','8.25',?)",
                    (self.accounts[0][1],),
                ).lastrowid
                conn.execute(
                    "INSERT INTO transaction_items(transaction_id,product_id,quantity,price,price_cents,product_name,category_name_snapshot,store_id) VALUES(?,?,1,1,100,?,?,?)",
                    (tx, pid, "Fictional benchmark", "Uncategorized", self.accounts[0][1]),
                )
        data = self.data(product=str(pid), group="hourly")
        elapsed = time.perf_counter() - start
        self.assertEqual(data["summary"]["revenue_cents"], 129600)
        self.assertLess(elapsed, 10)
        print(f"Advanced analytics: insert + filtered report, 1200 sales: {elapsed:.3f}s")
        with patch("services.analytics.MAX_ITEMS", 1), self.assertRaises(ReportLimitError):
            self.data()
