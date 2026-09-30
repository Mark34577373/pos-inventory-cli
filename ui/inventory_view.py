# ui/inventory_view.py
import sqlite3
import tkinter as tk
from tkinter import messagebox

from database.database import get_db_connection
from services.inventory import (
    create_product,
    remove_product_by_id,
    set_product_quantity,
)
from ui.dialogs import create_modal_window


def get_inventory_products():
    """Load the current product catalog directly from the SQLite database."""
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, price, quantity FROM products "
            "WHERE is_active = 1 ORDER BY id"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_stock_display(quantity):
    """Return the stock label, color, and font for a quantity."""
    if quantity <= 0:
        return "OUT OF STOCK", "#e74c3c", ("Arial", 10, "bold")
    return f"Stock: {quantity}", "#2c3e50", ("Arial", 11)


def _clear_inventory_view(parent_frame):
    """Remove the previous inventory widgets before rendering a fresh view."""
    for widget in parent_frame.winfo_children():
        widget.destroy()


def create_inventory_view(parent_frame):
    """Render live SQLite inventory as responsive product cards."""
    _clear_inventory_view(parent_frame)

    heading = tk.Frame(parent_frame, bg="#f8f9fa")
    heading.pack(fill=tk.X, pady=(0, 20))
    title = tk.Label(heading, text="Inventory Management", font=("Arial", 22, "bold"), bg="#f8f9fa", fg="#333333")
    title.pack(side=tk.LEFT, anchor=tk.W)
    actions = tk.Frame(heading, bg="#f8f9fa")
    actions.pack(side=tk.RIGHT)
    tk.Button(
        actions,
        text="Remove Product",
        command=lambda: open_remove_product_dialog(parent_frame),
        bg="#f3a7a3",
        fg="#000000",
        activebackground="#ec8882",
        activeforeground="#000000",
        relief=tk.FLAT,
        padx=14,
        pady=8,
        cursor="hand2",
    ).pack(side=tk.LEFT, padx=(0, 8))
    tk.Button(
        actions,
        text="Add Product",
        command=lambda: open_add_product_dialog(parent_frame),
        bg="#a8e6cf",
        fg="#000000",
        activebackground="#84d4b1",
        activeforeground="#000000",
        relief=tk.FLAT,
        padx=14,
        pady=8,
        cursor="hand2",
    ).pack(side=tk.LEFT)

    grid_frame = tk.Frame(parent_frame, bg="#f8f9fa")
    grid_frame.pack(fill=tk.BOTH, expand=True)

    products = get_inventory_products()

    if not products:
        no_items = tk.Label(grid_frame, text="No products found in database.", font=("Arial", 12), bg="#f8f9fa", fg="#7f8c8d")
        no_items.pack(anchor=tk.W)
        return

    available_width = max(parent_frame.winfo_width() - 40, 185)
    column_count = max(1, min(4, available_width // 205))
    for column in range(column_count):
        grid_frame.columnconfigure(column, weight=1, uniform="inventory-card")

    for index, product in enumerate(products):
        row, col = divmod(index, column_count)
        stock_text, stock_color, stock_font = get_stock_display(product["quantity"])

        # White base background card shape
        card = tk.Frame(grid_frame, bg="white", highlightbackground="#e0e0e0", highlightthickness=1, padx=15, pady=15, width=185, height=155)
        card.grid(row=row, column=col, padx=10, pady=10, sticky=tk.NSEW)
        card.grid_propagate(False)

        # Product card details
        tk.Label(
            card,
            text=f"ID: {product['id']}",
            font=("Arial", 9),
            bg="white",
            fg="#7f8c8d",
        ).pack(anchor=tk.W, pady=(0, 2))
        name_lbl = tk.Label(card, text=product["name"], font=("Arial", 12, "bold"), bg="white", fg="#2c3e50")
        name_lbl.pack(anchor=tk.W, pady=(0, 2))

        price_lbl = tk.Label(card, text=f"${product['price']:.2f}", font=("Arial", 11), bg="white", fg="#7f8c8d")
        price_lbl.pack(anchor=tk.W, pady=(0, 10))

        status_lbl = tk.Label(card, text=stock_text, font=stock_font, bg="white", fg=stock_color)
        status_lbl.pack(anchor=tk.W, pady=(0, 10))

        add_btn = tk.Label(
            card, text="Update Stock", font=("Arial", 10, "bold"),
            bg="#34495e", fg="white", padx=10, pady=4, cursor="hand2"
        )
        add_btn.pack(fill=tk.X, side=tk.BOTTOM)

        # Hover effect bindings
        add_btn.bind("<Enter>", lambda e, b=add_btn: b.config(bg="#1abc9c"))
        add_btn.bind("<Leave>", lambda e, b=add_btn: b.config(bg="#34495e"))
        add_btn.bind(
            "<Button-1>",
            lambda event, selected_product=dict(product): open_stock_dialog(
                parent_frame, selected_product
            ),
        )


def open_stock_dialog(parent_frame, product):
    """Open a dialog to set the selected product's stock quantity."""
    dialog_width = 360
    dialog_height = 250
    dialog = create_modal_window(
        parent_frame, "Update Stock", dialog_width, dialog_height
    )

    form = tk.Frame(dialog, bg="white", padx=24, pady=20)
    form.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)
    tk.Label(
        form,
        text=f"Update Stock: {product['name']}",
        font=("Arial", 14, "bold"),
        bg="white",
        fg="#2c3e50",
    ).pack(anchor=tk.W, pady=(0, 8))
    tk.Label(
        form,
        text=f"Current stock: {product['quantity']}",
        font=("Arial", 10),
        bg="white",
        fg="#7f8c8d",
    ).pack(anchor=tk.W, pady=(0, 12))
    tk.Label(
        form,
        text="New stock quantity",
        font=("Arial", 10, "bold"),
        bg="white",
        fg="#34495e",
    ).pack(anchor=tk.W, pady=(0, 5))

    quantity_entry = tk.Entry(form, font=("Arial", 11), relief=tk.SOLID, bd=1)
    quantity_entry.pack(fill=tk.X, ipady=5, pady=(0, 14))
    quantity_entry.insert(0, str(product["quantity"]))

    buttons = tk.Frame(form, bg="white")
    buttons.pack(fill=tk.X)
    tk.Button(
        buttons,
        text="Save Changes",
        command=lambda: _submit_stock_quantity(
            parent_frame, dialog, product["id"], quantity_entry
        ),
        bg="#1abc9c",
        fg="#000000",
        activebackground="#16a085",
        activeforeground="#000000",
        relief=tk.FLAT,
        font=("Arial", 10, "bold"),
        pady=9,
        cursor="hand2",
    ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
    tk.Button(
        buttons,
        text="Cancel",
        command=dialog.destroy,
        bg="#e9ece8",
        fg="#34495e",
        relief=tk.FLAT,
        font=("Arial", 10),
        pady=9,
        cursor="hand2",
    ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

    dialog.bind(
        "<Return>",
        lambda event: _submit_stock_quantity(
            parent_frame, dialog, product["id"], quantity_entry
        ),
    )
    dialog.bind("<Escape>", lambda event: dialog.destroy())
    dialog.grab_set()
    quantity_entry.focus_set()
    quantity_entry.selection_range(0, tk.END)


def _submit_stock_quantity(parent_frame, dialog, product_id, quantity_entry):
    """Validate and persist a stock quantity, then refresh the inventory cards."""
    try:
        quantity = int(quantity_entry.get())
    except ValueError:
        messagebox.showerror(
            "Invalid Quantity",
            "Enter a whole-number stock quantity.",
            parent=dialog,
        )
        quantity_entry.focus_set()
        return

    try:
        set_product_quantity(product_id, quantity)
    except ValueError as error:
        messagebox.showerror("Invalid Stock", str(error), parent=dialog)
        quantity_entry.focus_set()
        return
    except sqlite3.Error as error:
        messagebox.showerror(
            "Save Failed",
            f"The stock quantity could not be saved: {error}",
            parent=dialog,
        )
        return

    dialog.destroy()
    create_inventory_view(parent_frame)


def open_add_product_dialog(parent_frame):
    """Open a modal form for adding a product to the SQLite inventory."""
    dialog_width = 380
    dialog_height = 370
    dialog = create_modal_window(
        parent_frame, "Add Product", dialog_width, dialog_height
    )

    form = tk.Frame(dialog, bg="white", padx=28, pady=24)
    form.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

    tk.Label(
        form,
        text="Add Product",
        font=("Arial", 16, "bold"),
        bg="white",
        fg="#2c3e50",
    ).pack(anchor=tk.W, pady=(0, 20))

    entries = {}
    for label, key in (
        ("Product Name", "name"),
        ("Price", "price"),
        ("Quantity", "quantity"),
    ):
        tk.Label(
            form,
            text=label,
            font=("Arial", 10, "bold"),
            bg="white",
            fg="#34495e",
        ).pack(anchor=tk.W, pady=(0, 5))
        entry = tk.Entry(form, font=("Arial", 11), relief=tk.SOLID, bd=1)
        entry.pack(fill=tk.X, ipady=6, pady=(0, 14))
        entries[key] = entry

    buttons = tk.Frame(form, bg="white")
    buttons.pack(fill=tk.X, pady=(4, 0))
    tk.Button(
        buttons,
        text="Add Product",
        command=lambda: _submit_new_product(
            parent_frame,
            dialog,
            entries["name"],
            entries["price"],
            entries["quantity"],
        ),
        bg="#a8e6cf",
        fg="#000000",
        activebackground="#84d4b1",
        activeforeground="#000000",
        relief=tk.FLAT,
        font=("Arial", 11, "bold"),
        pady=12,
        cursor="hand2",
    ).pack(fill=tk.X, pady=(0, 8))
    tk.Button(
        buttons,
        text="Cancel",
        command=dialog.destroy,
        bg="#e9ece8",
        fg="#34495e",
        relief=tk.FLAT,
        font=("Arial", 10),
        pady=10,
        cursor="hand2",
    ).pack(fill=tk.X)

    dialog.bind(
        "<Return>",
        lambda event: _submit_new_product(
            parent_frame,
            dialog,
            entries["name"],
            entries["price"],
            entries["quantity"],
        ),
    )
    dialog.bind("<Escape>", lambda event: dialog.destroy())
    dialog.grab_set()
    entries["name"].focus_set()


def open_remove_product_dialog(parent_frame):
    """Open a dialog requiring both name and ID to remove a product."""
    dialog_width = 420
    dialog_height = 410
    dialog = create_modal_window(
        parent_frame, "Remove Product", dialog_width, dialog_height
    )

    form = tk.Frame(dialog, bg="white", padx=26, pady=20)
    form.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
    tk.Label(
        form,
        text="Remove Product",
        font=("Arial", 16, "bold"),
        bg="white",
        fg="#2c3e50",
    ).pack(anchor=tk.W, pady=(0, 12))
    tk.Label(
        form,
        text="Product Name",
        font=("Arial", 10, "bold"),
        bg="white",
        fg="#34495e",
    ).pack(anchor=tk.W, pady=(0, 5))
    name_entry = tk.Entry(form, font=("Arial", 11), relief=tk.SOLID, bd=1)
    name_entry.pack(fill=tk.X, ipady=6, pady=(0, 10))
    tk.Label(
        form,
        text="Product ID",
        font=("Arial", 10, "bold"),
        bg="white",
        fg="#34495e",
    ).pack(anchor=tk.W, pady=(0, 5))
    product_id_entry = tk.Entry(form, font=("Arial", 11), relief=tk.SOLID, bd=1)
    product_id_entry.pack(fill=tk.X, ipady=6, pady=(0, 14))

    buttons = tk.Frame(form, bg="white")
    buttons.pack(fill=tk.X)
    remove_button = tk.Label(
        buttons,
        text="Remove Product",
        bg="#f3a7a3",
        fg="#000000",
        font=("Arial", 12, "bold"),
        padx=14,
        pady=13,
        cursor="hand2",
    )
    remove_button.pack(fill=tk.X, pady=(0, 8))
    remove_button.bind(
        "<Button-1>",
        lambda event: _submit_remove_product(
            parent_frame, dialog, name_entry, product_id_entry
        ),
    )
    remove_button.bind("<Enter>", lambda event: remove_button.config(bg="#ec8882"))
    remove_button.bind("<Leave>", lambda event: remove_button.config(bg="#f3a7a3"))

    cancel_button = tk.Label(
        buttons,
        text="Cancel",
        bg="#e9ece8",
        fg="#34495e",
        font=("Arial", 12),
        padx=14,
        pady=15,
        cursor="hand2",
    )
    cancel_button.pack(fill=tk.X)
    cancel_button.bind("<Button-1>", lambda event: dialog.destroy())
    cancel_button.bind("<Enter>", lambda event: cancel_button.config(bg="#dde2de"))
    cancel_button.bind("<Leave>", lambda event: cancel_button.config(bg="#e9ece8"))

    dialog.bind(
        "<Return>",
        lambda event: _submit_remove_product(
            parent_frame, dialog, name_entry, product_id_entry
        ),
    )
    dialog.bind("<Escape>", lambda event: dialog.destroy())
    dialog.grab_set()
    name_entry.focus_set()


def _submit_remove_product(parent_frame, dialog, name_entry, product_id_entry):
    """Validate the product name and ID, then refresh active inventory."""
    try:
        product_id = int(product_id_entry.get())
    except ValueError:
        messagebox.showerror(
            "Invalid Product ID",
            "Enter the whole-number ID shown on the product card.",
            parent=dialog,
        )
        product_id_entry.focus_set()
        return

    try:
        remove_product_by_id(product_id, name_entry.get())
    except ValueError as error:
        messagebox.showerror("Cannot Remove Product", str(error), parent=dialog)
        name_entry.focus_set()
        return
    except sqlite3.Error as error:
        messagebox.showerror(
            "Remove Failed",
            f"The product could not be removed: {error}",
            parent=dialog,
        )
        return

    dialog.destroy()
    create_inventory_view(parent_frame)


def _submit_new_product(parent_frame, dialog, name_entry, price_entry, quantity_entry):
    """Validate form values, save them through the inventory service, and refresh."""
    name = name_entry.get()
    try:
        price = float(price_entry.get())
    except ValueError:
        messagebox.showerror("Invalid Price", "Enter a valid product price.", parent=dialog)
        price_entry.focus_set()
        return

    try:
        quantity = int(quantity_entry.get())
    except ValueError:
        messagebox.showerror("Invalid Quantity", "Enter a whole-number quantity.", parent=dialog)
        quantity_entry.focus_set()
        return

    try:
        product_id = create_product(name, price, quantity)
    except ValueError as error:
        messagebox.showerror("Invalid Product", str(error), parent=dialog)
        return
    except sqlite3.Error as error:
        messagebox.showerror(
            "Save Failed",
            f"The product could not be saved: {error}",
            parent=dialog,
        )
        return

    dialog.destroy()
    create_inventory_view(parent_frame)
    return product_id

