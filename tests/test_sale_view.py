import io
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import database.database as database
from services.checkout import session_cart
from services.inventory import create_product, find_product_by_id
from services.money import calculate_tax_cents, from_cents
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
                "SELECT total_cents, payment_method FROM transactions"
            ).fetchone()
            product = conn.execute(
                "SELECT quantity FROM products WHERE id = ?",
                (self.product["id"],),
            ).fetchone()
        finally:
            conn.close()

        subtotal_cents = self.product["price_cents"]
        tax_cents = calculate_tax_cents(subtotal_cents, 0.0825)
        expected_total_cents = subtotal_cents + tax_cents
        self.assertEqual(transaction["payment_method"], "Card")
        self.assertEqual(transaction["total_cents"], expected_total_cents)
        self.assertEqual(product["quantity"], self.product["quantity"] - 1)
        self.assertEqual(receipt["payment_method"], "Card")
        self.assertEqual(receipt["subtotal"], from_cents(subtotal_cents))
        self.assertEqual(receipt["tax"], from_cents(tax_cents))
        self.assertEqual(receipt["total"], from_cents(expected_total_cents))
        self.assertEqual(
            receipt["items"],
            [{
                "name": self.product["name"],
                "quantity": 1,
                "total": from_cents(self.product["price_cents"]),
            }],
        )
        self.assertTrue(session_cart.is_empty())

        report = io.StringIO()
        with redirect_stdout(report):
            view_sales_report()
        self.assertIn("Payment: Card", report.getvalue())

    def test_cash_checkout_persists_payment_and_shows_change(self):
        subtotal_cents = self.product["price_cents"]
        total_due_cents = subtotal_cents + calculate_tax_cents(subtotal_cents, 0.0825)
        total_due = from_cents(total_due_cents)
        receipt = self._run_checkout("1", str(from_cents(total_due_cents + 500)))

        conn = database.get_db_connection()
        try:
            transaction = conn.execute(
                "SELECT payment_method, total_cents FROM transactions"
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(transaction["payment_method"], "Cash")
        self.assertEqual(transaction["total_cents"], total_due_cents)
        self.assertEqual(receipt["payment_method"], "Cash")
        self.assertEqual(receipt["cash_received"], total_due + 5)
        self.assertEqual(receipt["change"], from_cents(500))
        self.assertEqual(len(receipt["items"]), 1)
        self.assertTrue(session_cart.is_empty())

        report = io.StringIO()
        with redirect_stdout(report):
            view_sales_report()
        self.assertIn("Payment: Cash", report.getvalue())


if __name__ == "__main__":
    unittest.main()