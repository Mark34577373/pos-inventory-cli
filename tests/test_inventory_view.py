import tempfile
import unittest
import sqlite3
from unittest.mock import patch

import database.database as database
from services.inventory import (
    create_product,
    find_product_by_id,
    remove_product_by_id,
    remove_product_by_name,
    set_product_quantity,
)
from services.transaction_service import complete_transaction
from ui.inventory_view import (
    _submit_new_product,
    _submit_remove_product,
    _submit_stock_quantity,
    _clear_inventory_view,
    get_inventory_products,
    get_stock_display,
)


class FakeEntry:
    def __init__(self, value):
        self.value = value
        self.focused = False

    def get(self):
        return self.value

    def focus_set(self):
        self.focused = True


class FakeDialog:
    def __init__(self):
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


class FakeContainer:
    def __init__(self, children):
        self.children = children

    def winfo_children(self):
        return self.children


class InventoryViewTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_file = database.DB_FILE
        database.DB_FILE = f"{self.temp_dir.name}/inventory.db"
        database.initialize_database()

    def tearDown(self):
        database.DB_FILE = self.original_db_file
        self.temp_dir.cleanup()

    def test_product_cards_use_current_sqlite_values(self):
        conn = database.get_db_connection()
        try:
            conn.execute(
                "UPDATE products SET quantity = ? WHERE name = ?",
                (3, "Mouse"),
            )
            conn.execute(
                "INSERT INTO products (name, price, quantity) VALUES (?, ?, ?)",
                ("USB Cable", 7.5, 9),
            )
            conn.commit()
        finally:
            conn.close()

        products = get_inventory_products()

        self.assertEqual(
            [(product["name"], product["price"], product["quantity"]) for product in products],
            [
                ("Mouse", 10.99, 3),
                ("Keyboard", 29.99, 0),
                ("USB Cable", 7.5, 9),
            ],
        )

    def test_out_of_stock_status_is_red_and_in_stock_status_shows_quantity(self):
        self.assertEqual(
            get_stock_display(0),
            ("OUT OF STOCK", "#e74c3c", ("Arial", 10, "bold")),
        )
        self.assertEqual(
            get_stock_display(-1),
            ("OUT OF STOCK", "#e74c3c", ("Arial", 10, "bold")),
        )
        self.assertEqual(
            get_stock_display(5),
            ("Stock: 5", "#2c3e50", ("Arial", 11)),
        )

    def test_form_submission_saves_product_and_refreshes_inventory(self):
        dialog = FakeDialog()
        with patch("ui.inventory_view.create_inventory_view") as refresh:
            product_id = _submit_new_product(
                "inventory-parent",
                dialog,
                FakeEntry("  Notebook  "),
                FakeEntry("4.25"),
                FakeEntry("12"),
            )

        product = next(
            product for product in get_inventory_products()
            if product["id"] == product_id
        )
        self.assertEqual(product["name"], "Notebook")
        self.assertEqual(product["price"], 4.25)
        self.assertEqual(product["quantity"], 12)
        self.assertTrue(dialog.destroyed)
        refresh.assert_called_once_with("inventory-parent")

    def test_stock_form_persists_quantity_and_refreshes_inventory(self):
        product = next(
            product for product in get_inventory_products()
            if product["name"] == "Mouse"
        )
        dialog = FakeDialog()

        with patch("ui.inventory_view.create_inventory_view") as refresh:
            _submit_stock_quantity(
                "inventory-parent",
                dialog,
                product["id"],
                FakeEntry("17"),
            )

        updated_product = next(
            item for item in get_inventory_products()
            if item["id"] == product["id"]
        )
        self.assertEqual(updated_product["quantity"], 17)
        self.assertTrue(dialog.destroyed)
        refresh.assert_called_once_with("inventory-parent")

    def test_remove_form_hides_product_but_preserves_sales_history(self):
        mouse = next(
            product for product in get_inventory_products()
            if product["name"] == "Mouse"
        )
        conn = database.get_db_connection()
        try:
            cursor = conn.execute(
                "INSERT INTO transactions (timestamp, total, payment_method) "
                "VALUES (?, ?, ?)",
                ("2026-09-30 12:00:00", 10.99, "Cash"),
            )
            transaction_id = cursor.lastrowid
            conn.execute(
                "INSERT INTO transaction_items "
                "(transaction_id, product_id, quantity, price) "
                "VALUES (?, ?, ?, ?)",
                (transaction_id, mouse["id"], 1, mouse["price"]),
            )
            conn.commit()
        finally:
            conn.close()

        dialog = FakeDialog()
        with patch("ui.inventory_view.create_inventory_view") as refresh:
            _submit_remove_product(
                "inventory-parent",
                dialog,
                FakeEntry("  mOuSe  "),
                FakeEntry(str(mouse["id"])),
            )

        self.assertNotIn(mouse["id"], {p["id"] for p in get_inventory_products()})
        self.assertIsNone(find_product_by_id(mouse["id"]))
        self.assertTrue(dialog.destroyed)
        refresh.assert_called_once_with("inventory-parent")

        conn = database.get_db_connection()
        try:
            retained = conn.execute(
                "SELECT p.name FROM transaction_items ti "
                "JOIN products p ON p.id = ti.product_id "
                "WHERE ti.transaction_id = ?",
                (transaction_id,),
            ).fetchone()
            product = conn.execute(
                "SELECT is_active FROM products WHERE id = ?",
                (mouse["id"],),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(retained["name"], "Mouse")
        self.assertEqual(product["is_active"], 0)

        with self.assertRaisesRegex(ValueError, "insufficient stock"):
            complete_transaction(
                [{"id": mouse["id"], "qty": 1, "price": mouse["price"]}],
                10.99,
                "Card",
            )

    def test_remove_by_name_rejects_ambiguous_products(self):
        create_product("Duplicate", 2.0, 1)
        create_product("duplicate", 3.0, 1)

        with self.assertRaisesRegex(ValueError, "More than one"):
            remove_product_by_name("DUPLICATE")

    def test_remove_requires_matching_product_name_and_id(self):
        mouse = next(
            product for product in get_inventory_products()
            if product["name"] == "Mouse"
        )

        with self.assertRaisesRegex(ValueError, "does not match ID"):
            remove_product_by_id(mouse["id"], "Keyboard")

        self.assertIsNotNone(find_product_by_id(mouse["id"]))

    def test_database_migration_marks_existing_products_active(self):
        legacy_path = f"{self.temp_dir.name}/legacy.db"
        conn = sqlite3.connect(legacy_path)
        try:
            conn.execute(
                "CREATE TABLE products ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name TEXT NOT NULL, price REAL NOT NULL, quantity INTEGER NOT NULL)"
            )
            conn.execute(
                "INSERT INTO products (name, price, quantity) VALUES (?, ?, ?)",
                ("Legacy Item", 8.5, 4),
            )
            conn.commit()
        finally:
            conn.close()

        database.DB_FILE = legacy_path
        database.initialize_database()

        products = get_inventory_products()
        self.assertEqual(
            [(product["name"], product["quantity"]) for product in products],
            [("Legacy Item", 4)],
        )

    def test_invalid_stock_form_value_does_not_update_inventory(self):
        product = next(
            product for product in get_inventory_products()
            if product["name"] == "Mouse"
        )
        dialog = FakeDialog()
        quantity_entry = FakeEntry("-2")

        with patch("ui.inventory_view.messagebox.showerror") as showerror:
            _submit_stock_quantity(
                "inventory-parent",
                dialog,
                product["id"],
                quantity_entry,
            )

        current_product = next(
            item for item in get_inventory_products()
            if item["id"] == product["id"]
        )
        self.assertEqual(current_product["quantity"], product["quantity"])
        self.assertFalse(dialog.destroyed)
        showerror.assert_called_once()
        self.assertTrue(quantity_entry.focused)

    def test_stock_service_rejects_non_whole_quantities(self):
        product = get_inventory_products()[0]
        with self.assertRaisesRegex(ValueError, "whole number"):
            set_product_quantity(product["id"], 3.5)

        self.assertEqual(get_inventory_products()[0]["quantity"], product["quantity"])

    def test_view_refresh_destroys_previous_widgets(self):
        previous_widgets = [FakeDialog(), FakeDialog()]

        _clear_inventory_view(FakeContainer(previous_widgets))

        self.assertTrue(all(widget.destroyed for widget in previous_widgets))

    def test_invalid_product_is_rejected_without_database_insert(self):
        with self.assertRaisesRegex(ValueError, "finite non-negative"):
            create_product("Invalid", float("nan"), 2)

        with self.assertRaisesRegex(ValueError, "whole number"):
            create_product("Invalid", 1.0, 2.5)

        products = get_inventory_products()
        self.assertEqual(len(products), 2)


if __name__ == "__main__":
    unittest.main()
