import tempfile
import unittest
from datetime import datetime

import database.database as database
from ui.dashboard import get_dashboard_data


class DashboardDataTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_file = database.DB_FILE
        database.DB_FILE = f"{self.temp_dir.name}/dashboard.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self.original_db_file
        self.temp_dir.cleanup()

    def test_dashboard_uses_zero_metrics_when_no_sales_exist(self):
        data = get_dashboard_data()

        self.assertEqual(data["daily_sales"]["total_revenue"], 0.0)
        self.assertEqual(data["daily_sales"]["transaction_count"], 0)
        self.assertEqual(data["product_count"], 2)
        self.assertEqual(data["recent_transactions"], [])

    def test_dashboard_loads_daily_metrics_and_recent_items_from_database(self):
        conn = database.get_db_connection()
        try:
            products = {
                row["name"]: row["id"]
                for row in conn.execute(
                    "SELECT id, name FROM products WHERE name IN (?, ?)",
                    ("Mouse", "Keyboard"),
                )
            }
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor = conn.execute(
                "INSERT INTO transactions (timestamp, total, payment_method) "
                "VALUES (?, ?, ?)",
                (timestamp, 56.26, "Cash"),
            )
            transaction_id = cursor.lastrowid
            conn.execute(
                "INSERT INTO transaction_items "
                "(transaction_id, product_id, quantity, price) "
                "VALUES (?, ?, ?, ?)",
                (transaction_id, products["Mouse"], 2, 10.99),
            )
            conn.execute(
                "INSERT INTO transaction_items "
                "(transaction_id, product_id, quantity, price) "
                "VALUES (?, ?, ?, ?)",
                (transaction_id, products["Keyboard"], 1, 29.99),
            )
            conn.commit()
        finally:
            conn.close()

        data = get_dashboard_data()
        receipt = data["recent_transactions"][0]

        self.assertEqual(data["daily_sales"]["transaction_count"], 1)
        self.assertEqual(data["daily_sales"]["total_revenue"], 56.26)
        self.assertEqual(receipt["id"], transaction_id)
        self.assertEqual(receipt["total"], 56.26)
        self.assertEqual(receipt["items"], "Mouse x2, Keyboard x1")
        self.assertEqual(receipt["payment_method"], "Cash")


if __name__ == "__main__":
    unittest.main()
