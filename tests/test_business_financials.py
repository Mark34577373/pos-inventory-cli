"""Stage C exact ledger math, original cost snapshots and protected real routes."""

import csv
import io
import secrets
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch

import test_customers as fixtures

from config import business_now
from services.analytics import resolve_filters
from services.expenses import correct_expense, create_expense, expense_report, get_expense
from services.financials import financial_report
from services.money import from_cents
from services.purchasing import save_product_cost, save_supplier
from services.refunds import get_sale, process_refund
from services.transaction_service import complete_transaction
from web_app import app


class BusinessFinancialTests(unittest.TestCase):
    setUp = fixtures.CustomerTests.setUp
    tearDown = fixtures.CustomerTests.tearDown
    context = fixtures.CustomerTests.context
    query = fixtures.CustomerTests.query
    post = fixtures.CustomerTests.post
    sale = fixtures.CustomerTests.sale

    def values(self, **overrides):
        return dict(
            category="Software",
            description="Fictional monthly service",
            amount="10.25",
            expense_date=business_now().date().isoformat(),
            notes="",
            **overrides,
        )

    def report(self):
        return financial_report(resolve_filters({"preset": "today"}))

    def test_expense_create_retry_validation_filters_and_pagination(self):
        with self.context():
            key = secrets.token_urlsafe(24)
            identifier = create_expense(self.values(), key)
            self.assertEqual(create_expense(self.values(), key), identifier)
            with self.assertRaises(ValueError):
                create_expense(dict(self.values(), amount="11"), key)
            self.assertEqual(get_expense(identifier)["amount_cents"], 1025)
            for invalid in (
                dict(self.values(), amount="-1"),
                dict(self.values(), amount=".001"),
                dict(self.values(), expense_date="bad"),
                dict(self.values(), store_id=99),
                dict(self.values(), category="Inventory"),
                dict(self.values(), description=""),
            ):
                with self.assertRaises(ValueError):
                    create_expense(invalid, secrets.token_urlsafe(24))
            for number in range(22):
                create_expense(
                    dict(self.values(), description=f"Paged {number:02}", amount="0.05"),
                    secrets.token_urlsafe(24),
                )
            data = expense_report({"q": "Paged", "page": "2"})
            self.assertEqual(
                (data["count"], len(data["paged_rows"]), data["total_cents"]), (22, 2, 110)
            )
            self.assertEqual(data["breakdown"]["Software"], 110)
            self.assertEqual(expense_report({"category": "Rent"})["count"], 0)

    def test_correction_reversal_atomic_retry_audit_and_sql_guards(self):
        with self.context():
            original = create_expense(self.values(), secrets.token_urlsafe(24))
            key = secrets.token_urlsafe(24)
            corrected = correct_expense(
                original, dict(self.values(), amount="5.50"), "Fictional correction", key
            )
            self.assertEqual(
                correct_expense(
                    original, dict(self.values(), amount="5.50"), "Fictional correction", key
                ),
                corrected,
            )
            report = expense_report({})
            self.assertEqual((report["count"], report["total_cents"]), (3, 550))
            self.assertEqual(get_expense(original)["amount_cents"], 1025)
            self.assertIsNotNone(get_expense(original)["reversed_by"])
            with self.assertRaises(ValueError):
                correct_expense(
                    original, {}, "Second reversal", secrets.token_urlsafe(24), reverse_only=True
                )
            reversal = correct_expense(
                corrected,
                {},
                "Fictional cancellation",
                secrets.token_urlsafe(24),
                reverse_only=True,
            )
            self.assertEqual(get_expense(reversal)["entry_kind"], "reversal")
            self.assertEqual(expense_report({})["total_cents"], 0)
        from database.database import get_db_connection

        with closing(get_db_connection()) as conn, conn:
            for sql in (
                "UPDATE expenses SET amount_cents=1 WHERE id=?",
                "DELETE FROM expenses WHERE id=?",
            ):
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(sql, (original,))

    def test_correction_injected_failure_rolls_back_reversal_and_replacement(self):
        import services.expenses as service

        with self.context():
            original = create_expense(self.values(), secrets.token_urlsafe(24))
            before = self.query("SELECT * FROM expenses")
            insert = service._insert
            calls = []

            def fail_second(*args, **kwargs):
                calls.append(1)
                if len(calls) == 2:
                    raise sqlite3.OperationalError("Injected test failure")
                return insert(*args, **kwargs)

            with (
                patch.object(service, "_insert", side_effect=fail_second),
                self.assertRaises(sqlite3.Error),
            ):
                correct_expense(
                    original,
                    dict(self.values(), amount="1"),
                    "Rollback test",
                    secrets.token_urlsafe(24),
                )
            self.assertEqual(self.query("SELECT * FROM expenses"), before)

    def test_financial_discount_tax_cost_profit_and_physical_refund_reconcile(self):
        with self.context():
            save_product_cost(self.products[0], ".40")
            tx = complete_transaction(
                [{"id": self.products[0], "qty": 3}],
                from_cents(271),
                "Card",
                discount_kind="fixed",
                discount_value=".50",
            )
            create_expense(dict(self.values(), amount=".10"), secrets.token_urlsafe(24))
            report = self.report()
            self.assertEqual(report["original_merchandise_cents"], 300)
            self.assertEqual(report["discount_cents"], 50)
            self.assertEqual(report["net_tax_cents"], 21)
            self.assertEqual(report["net_revenue_cents"], 250)
            self.assertEqual(report["known_cogs_cents"], 120)
            self.assertEqual(report["gross_profit_cents"], 130)
            self.assertEqual(report["operating_result_cents"], 120)
            self.assertEqual(report["purchases"]["received_cents"], 0)
            state = get_sale(tx)
            process_refund(
                tx,
                {state["items"][0]["id"]: 1},
                "Fictional restocked return",
                True,
                secrets.token_urlsafe(24),
            )
            partial = self.report()
            self.assertEqual(partial["known_cogs_cents"], 80)
            refund = self.query("SELECT total_cents,tax_cents FROM refunds")[0]
            self.assertEqual(
                partial["net_revenue_cents"], 250 - refund["total_cents"] + refund["tax_cents"]
            )
            process_refund(
                tx,
                {state["items"][0]["id"]: 2},
                "Fictional non-restocked return",
                False,
                secrets.token_urlsafe(24),
            )
            final = self.report()
            self.assertEqual(final["net_revenue_cents"], 0)
            self.assertEqual(final["known_cogs_cents"], 80)
            self.assertEqual(final["gross_profit_cents"], -80)
            for dimension in ("products", "categories", "periods"):
                self.assertEqual(sum(row["gross_profit_cents"] for row in final[dimension]), -80)
        export = self.client.get("/financials/export/overview.csv?preset=today")
        self.assertEqual(export.status_code, 200)
        self.assertIn(b"-0.80", export.data)

    def test_unknown_historical_cost_is_never_invented(self):
        tx = self.sale()
        with self.context():
            save_product_cost(self.products[0], ".01")
            report = self.report()
            self.assertIsNone(report["gross_profit_cents"])
            self.assertIsNone(report["operating_result_cents"])
            self.assertEqual(report["unknown_cost_units"], 2)
            self.assertIsNone(get_sale(tx)["items"][0]["unit_cost_cents"])
        self.assertIn(
            b"Incomplete historical data", self.client.get("/financials?preset=today").data
        )

    def test_three_store_isolation_routes_exports_services_no_side_effects(self):
        ids = []
        for index in range(3):
            with self.context(index):
                supplier = save_supplier({"name": f"Supplier private {index}"})
                ids.append(
                    create_expense(
                        dict(
                            self.values(),
                            description=f"Private expense {index}",
                            supplier_id=supplier,
                        ),
                        secrets.token_urlsafe(24),
                    )
                )
            self.sale(index)
        before = self.query("SELECT * FROM expenses")
        for identifier in ids[1:]:
            with self.context():
                for action in (
                    lambda: get_expense(identifier),
                    lambda: correct_expense(
                        identifier, {}, "Intruder", secrets.token_urlsafe(24), reverse_only=True
                    ),
                ):
                    with self.assertRaises(LookupError):
                        action()
            for path in (f"/expenses/{identifier}", f"/expenses/{identifier}/correct"):
                self.assertEqual(self.client.get(path).status_code, 404)
            self.assertEqual(
                self.post(
                    f"/expenses/{identifier}/reverse",
                    {"key": secrets.token_urlsafe(24), "reason": "Intruder"},
                ).status_code,
                404,
            )
        for path in (
            "/expenses",
            "/expenses/export.csv",
            "/financials?preset=today",
            "/financials/export/products.csv?preset=today",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(b"Private expense 1", response.data)
            self.assertNotIn(b"Product 1", response.data)
            self.assertEqual(app.test_client().get(path).status_code, 302)
        self.assertEqual(self.query("SELECT * FROM expenses"), before)
        with self.context():
            self.assertEqual(self.report()["sales"]["summary"]["transaction_count"], 1)
        self.assertEqual(self.client.get("/financials?store_id=999").status_code, 400)

    def test_http_creation_correction_csv_csrf_and_demo_restrictions(self):
        key = secrets.token_urlsafe(24)
        response = self.post(
            "/expenses/new", dict(self.values(), description="=FICTIONAL()", key=key)
        )
        self.assertEqual(response.status_code, 302)
        identifier = self.query("SELECT id FROM expenses")[0]["id"]
        for path in (
            "/expenses",
            "/expenses/new",
            f"/expenses/{identifier}",
            f"/expenses/{identifier}/correct",
            "/financials",
        ):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        export = self.client.get("/expenses/export.csv?q=FICTIONAL")
        rows = list(csv.reader(io.StringIO(export.data.decode("utf-8-sig"))))
        self.assertEqual(rows[1][3], "'=FICTIONAL()")
        self.assertEqual(self.client.post("/expenses/new", data=self.values()).status_code, 400)
        self.assertEqual(
            self.post(
                "/expenses/new", dict(self.values(), key=secrets.token_urlsafe(24), store_id=999)
            ).status_code,
            400,
        )
        app.config["DEMO_READ_ONLY"] = True
        self.assertEqual(
            self.post(
                "/expenses/new", dict(self.values(), key=secrets.token_urlsafe(24))
            ).status_code,
            403,
        )
        with self.context(), self.assertRaises(PermissionError):
            create_expense(self.values(), secrets.token_urlsafe(24))

    def test_unverified_tax_and_original_cost_guards(self):
        from database.database import get_db_connection

        with self.context():
            save_product_cost(self.products[0], ".25")
        tx = self.sale()
        with closing(get_db_connection()) as conn, conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE transaction_items SET unit_cost_cents=99 WHERE transaction_id=?", (tx,)
                )
            conn.execute("UPDATE transactions SET tax_rate_percent=NULL WHERE id=?", (tx,))
        with self.context():
            report = self.report()
            self.assertFalse(report["tax_complete"])
            self.assertIsNone(report["net_revenue_cents"])
            self.assertIsNone(report["gross_profit_cents"])
            self.assertTrue(all(row["gross_profit_cents"] is None for row in report["products"]))

    def test_expense_migration_rollback(self):
        from database import database as db
        from database.expenses_migration import migrate_expenses

        with closing(db.get_db_connection()) as conn, conn:
            conn.execute("DROP TABLE expenses")  # Disposable fixture; no expense rows exist.
            conn.execute("DELETE FROM schema_migrations WHERE version=26")
        before = self.query("SELECT * FROM customers")

        def fail_after_ddl(conn):
            migrate_expenses(conn)
            raise RuntimeError("Injected expense migration failure")

        with (
            patch("database.expenses_migration.migrate_expenses", side_effect=fail_after_ddl),
            self.assertRaises(RuntimeError),
        ):
            db.initialize_database(seed=False)
        self.assertEqual(self.query("SELECT name FROM sqlite_master WHERE name='expenses'"), [])
        self.assertEqual(self.query("SELECT version FROM schema_migrations WHERE version=26"), [])
        self.assertEqual(self.query("SELECT * FROM customers"), before)
        db.initialize_database(seed=False)
        db.initialize_database(seed=False)
        self.assertEqual(self.query("PRAGMA integrity_check"), [{"integrity_check": "ok"}])
