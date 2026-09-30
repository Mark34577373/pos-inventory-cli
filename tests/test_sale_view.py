import io
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import database.database as database
from config import TAX_RATE
from services.checkout import session_cart
from services.inventory import create_product, find_product_by_id
from services.transaction_service import view_sales_report
from ui import sale_view


class SaleViewCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_file = database.DB_FILE
        database.DB_FILE = f"{self.temp_dir.name}/test.db"
        session_cart.clear()
        database.initialize_database()
        self.product = find_product_by_id(1)

    def tearDown(self):
        session_cart.clear()
        database.DB_FILE = self.original_db_file
        self.temp_dir.cleanup()

    def _run_checkout(self, choice, cash_amount=None):
        session_cart.add_item(self.product["id"], 1)
        dialog_values = [choice]
        if cash_amount is not None:
            dialog_values.append(cash_amount)

        with (
            patch.object(sale_view.simpledialog, "askstring", side_effect=dialog_values),
            patch.object(sale_view.simpledialog, "askfloat", side_effect=dialog_values[1:]),
            patch.object(sale_view, "show_receipt_window") as show_receipt,
            patch.object(sale_view.messagebox, "showerror") as showerror,
        ):
            sale_view.handle_ui_checkout()

        showerror.assert_not_called()
        show_receipt.assert_called_once()
        return show_receipt.call_args.kwargs

    def test_add_button_tracks_quantity_and_blocks_out_of_stock_product(self):
        sale_view.handle_add_item(self.product["id"])
        sale_view.handle_add_item(self.product["id"])
        self.assertEqual(session_cart.get_items()[self.product["id"]], 2)

        conn = database.get_db_connection()
        try:
            conn.execute(
                "UPDATE products SET quantity = 0 WHERE id = ?",
                (self.product["id"],),
            )
            conn.commit()
        finally:
            conn.close()

        with patch.object(sale_view.messagebox, "showwarning") as showwarning:
            sale_view.handle_add_item(self.product["id"])

        showwarning.assert_called_once()
        self.assertEqual(session_cart.get_items()[self.product["id"]], 2)

    def test_remove_cart_item_removes_only_selected_product_without_stock_change(self):
        other_product_id = create_product("Notebook", 4.5, 3)
        session_cart.add_item(self.product["id"], 2)
        session_cart.add_item(other_product_id, 1)

        sale_view.handle_remove_cart_item(self.product["id"])

        self.assertEqual(session_cart.get_items(), {other_product_id: 1})
        self.assertEqual(
            find_product_by_id(self.product["id"])["quantity"],
            self.product["quantity"],
        )
        self.assertEqual(find_product_by_id(other_product_id)["quantity"], 3)

    def test_card_checkout_persists_payment_and_deducts_inventory(self):
        receipt = self._run_checkout("2")

        conn = database.get_db_connection()
        try:
            transaction = conn.execute(
                "SELECT total, payment_method FROM transactions"
            ).fetchone()
            product = conn.execute(
                "SELECT quantity FROM products WHERE id = ?",
                (self.product["id"],),
            ).fetchone()
        finally:
            conn.close()

        expected_total = self.product["price"] * (1 + TAX_RATE)
        self.assertEqual(transaction["payment_method"], "Card")
        self.assertAlmostEqual(transaction["total"], expected_total)
        self.assertEqual(product["quantity"], self.product["quantity"] - 1)
        self.assertEqual(receipt["payment_method"], "Card")
        self.assertAlmostEqual(receipt["subtotal"], self.product["price"])
        self.assertAlmostEqual(receipt["tax"], self.product["price"] * TAX_RATE)
        self.assertAlmostEqual(receipt["total"], expected_total)
        self.assertEqual(
            receipt["items"],
            [{"name": self.product["name"], "quantity": 1, "total": self.product["price"]}],
        )
        self.assertTrue(session_cart.is_empty())

        report = io.StringIO()
        with redirect_stdout(report):
            view_sales_report()
        self.assertIn("Payment: Card", report.getvalue())

    def test_cash_checkout_persists_payment_and_shows_change(self):
        total_due = self.product["price"] * (1 + TAX_RATE)
        receipt = self._run_checkout("1", total_due + 5)

        conn = database.get_db_connection()
        try:
            transaction = conn.execute(
                "SELECT payment_method FROM transactions"
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(transaction["payment_method"], "Cash")
        self.assertEqual(receipt["payment_method"], "Cash")
        self.assertAlmostEqual(receipt["cash_received"], total_due + 5)
        self.assertAlmostEqual(receipt["change"], 5)
        self.assertEqual(len(receipt["items"]), 1)
        self.assertTrue(session_cart.is_empty())

        report = io.StringIO()
        with redirect_stdout(report):
            view_sales_report()
        self.assertIn("Payment: Cash", report.getvalue())


if __name__ == "__main__":
    unittest.main()