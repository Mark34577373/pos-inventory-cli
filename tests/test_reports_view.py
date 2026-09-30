import tempfile
import unittest
from datetime import datetime

import database.database as database
from ui.reports_view import get_today_sales_summary


class ReportsSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_file = database.DB_FILE
        database.DB_FILE = f"{self.temp_dir.name}/reports.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self.original_db_file
        self.temp_dir.cleanup()

    def _insert_transaction(self, timestamp, total, payment_method):
        conn = database.get_db_connection()
        try:
            conn.execute(
                "INSERT INTO transactions (timestamp, total, payment_method) "
                "VALUES (?, ?, ?)",
                (timestamp, total, payment_method),
            )
            conn.commit()
        finally:
            conn.close()

    def test_summary_counts_only_today_and_splits_payment_revenue(self):
        self._insert_transaction("2026-09-30 00:00:00", 100.0, "Cash")
        self._insert_transaction("2026-09-30 23:59:59", 50.0, "Card")
        self._insert_transaction("2026-09-30 14:30:00", 25.0, "Unknown")
        self._insert_transaction("2026-09-29 23:59:59", 500.0, "Cash")
        self._insert_transaction("2026-10-01 00:00:00", 700.0, "Card")

        summary = get_today_sales_summary(datetime(2026, 9, 30, 12, 0))

        self.assertEqual(summary["transaction_count"], 3)
        self.assertEqual(summary["total_revenue"], 175.0)
        self.assertAlmostEqual(summary["average_sale"], 175.0 / 3)
        self.assertEqual(summary["cash_revenue"], 100.0)
        self.assertEqual(summary["card_revenue"], 50.0)

    def test_empty_day_returns_zero_for_every_metric(self):
        summary = get_today_sales_summary(datetime(2026, 9, 30, 12, 0))

        self.assertEqual(summary["transaction_count"], 0)
        self.assertEqual(summary["total_revenue"], 0.0)
        self.assertEqual(summary["average_sale"], 0.0)
        self.assertEqual(summary["cash_revenue"], 0.0)
        self.assertEqual(summary["card_revenue"], 0.0)


if __name__ == "__main__":
    unittest.main()
