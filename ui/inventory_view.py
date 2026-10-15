
import sqlite3
import tkinter as tk
from decimal import Decimal, InvalidOperation
from tkinter import messagebox

from services.inventory import (
    create_product,
    list_active_products,
    remove_product_by_id,
    remove_product_by_name,
    search_active_products,
    set_product_quantity,
    update_product_details,
)
from ui.dialogs import create_modal_window

BG = "#f8f9fa"
WHITE = "#ffffff"
TEXT = "#2c3e50"
MUTED = "#7f8c8d"
BORDER = "#e0e0e0"
GREEN = "#15803d"
AMBER = "#d97706"
RED = "#dc2626"
LOW_STOCK_THRESHOLD = 5


def get_inventory_products():
    """Load active products from SQLite."""
    return list_active_products()


def get_stock_display(quantity):
    """Return the stock label, color, and font for a quantity."""
    if quantity <= 0:
        return "OUT OF STOCK", RED, ("Arial", 10, "bold")

    if quantity <= LOW_STOCK_THRESHOLD:
        return (
            f"LOW STOCK · {quantity} left",
            AMBER,
            ("Arial", 10, "bold"),
        )

    return (
        f"IN STOCK · {quantity} available",
        GREEN,
        ("Arial", 10, "bold"),
    )


def _clear_inventory_view(parent_frame):
    """Remove the previous inventory widgets before rendering."""
    for widget in parent_frame.winfo_children():
        widget.destroy()


def create_inventory_view(parent_frame):
    """Render inventory cards, stock indicators, and inventory summaries."""
    _clear_inventory_view(parent_frame)

    heading = tk.Frame(parent_frame, bg=BG)
    heading.pack(fill=tk.X, pady=(0, 20))

    tk.Label(
        heading,
        text="Inventory Management",
        font=("Arial", 22, "bold"),
        bg=BG,
        fg="#333333",
    ).pack(side=tk.LEFT, anchor=tk.W)

    actions = tk.Frame(heading, bg=BG)
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
        text="+ Add Product",
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

    # Inventory summary cards
    summary_frame = tk.Frame(parent_frame, bg=BG)
    summary_frame.pack(fill=tk.X, pady=(0, 16))

    summary_labels = {}

    summary_items = (
        ("total", "Total Products", TEXT),
        ("low", "Low Stock", AMBER),
        ("out", "Out of Stock", RED),
    )

    for key, title, color in summary_items:
        card = tk.Frame(
            summary_frame,
            bg=WHITE,
            highlightbackground=BORDER,
            highlightthickness=1,
            padx=14,
            pady=10,
        )
        card.pack(
            side=tk.LEFT,
            fill=tk.X,
            expand=True,
            padx=(0, 10),
        )

        tk.Label(
            card,
            text=title,
            font=("Arial", 10),
            bg=WHITE,
            fg=MUTED,
        ).pack(anchor=tk.W)

        value_label = tk.Label(
            card,
            text="0",
            font=("Arial", 20, "bold"),
            bg=WHITE,
            fg=color,
        )
        value_label.pack(anchor=tk.W)

        summary_labels[key] = value_label

    # Search controls
    search_frame = tk.Frame(parent_frame, bg=BG)
    search_frame.pack(fill=tk.X, pady=(0, 15))

    search_entry = tk.Entry(
        search_frame,
        font=("Arial", 11),
        relief=tk.SOLID,
        bd=1,
    )
    search_entry.pack(
        side=tk.LEFT,
        fill=tk.X,
        expand=True,
        ipady=7,
    )

    placeholder = "Search by product name or ID..."
    search_entry.insert(0, placeholder)

    grid_frame = tk.Frame(parent_frame, bg=BG)
    grid_frame.pack(fill=tk.BOTH, expand=True)

    def update_summary():
        """Update stock totals using all active products."""
        all_products = get_inventory_products()

        summary_labels["total"].config(
            text=str(len(all_products))
        )

        summary_labels["low"].config(
            text=str(
                sum(
                    1
                    for product in all_products
                    if 0 < product["quantity"] <= LOW_STOCK_THRESHOLD
                )
            )
        )

        summary_labels["out"].config(
            text=str(
                sum(
                    1
                    for product in all_products
                    if product["quantity"] <= 0
                )
            )
        )

    def render_products(products):
        """Render supplied products as inventory cards."""
        for widget in grid_frame.winfo_children():
            widget.destroy()

        update_summary()

        if not products:
            tk.Label(
                grid_frame,
                text="No products match your search.",
                font=("Arial", 12),
                bg=BG,
                fg=MUTED,
            ).pack(anchor=tk.W, pady=10)
            return

        available_width = max(
            parent_frame.winfo_width() - 40,
            185,
        )

        column_count = max(
            1,
            min(4, available_width // 225),
        )

        for column in range(column_count):
            grid_frame.columnconfigure(
                column,
                weight=1,
                uniform="inventory-card",
            )

        for index, product in enumerate(products):
            row, col = divmod(index, column_count)

            stock_text, stock_color, stock_font = get_stock_display(
                product["quantity"]
            )

            card = tk.Frame(
                grid_frame,
                bg=WHITE,
                highlightbackground=BORDER,
                highlightthickness=1,
                padx=15,
                pady=15,
                width=205,
                height=190,
            )
            card.grid(
                row=row,
                column=col,
                padx=8,
                pady=8,
                sticky=tk.NSEW,
            )
            card.grid_propagate(False)

            tk.Label(
                card,
                text=f"PRODUCT ID: {product['id']}",
                font=("Arial", 8),
                bg=WHITE,
                fg=MUTED,
            ).pack(anchor=tk.W, pady=(0, 5))

            tk.Label(
                card,
                text=product["name"],
                font=("Arial", 12, "bold"),
                bg=WHITE,
                fg=TEXT,
                wraplength=170,
                justify=tk.LEFT,
            ).pack(anchor=tk.W, pady=(0, 5))

            tk.Label(
                card,
                text=f"${product['price']:.2f}",
                font=("Arial", 12),
                bg=WHITE,
                fg=TEXT,
            ).pack(anchor=tk.W, pady=(0, 8))

            # Stock status indicator
            tk.Label(
                card,
                text=stock_text,
                font=stock_font,
                bg=WHITE,
                fg=stock_color,
            ).pack(anchor=tk.W, pady=(0, 8))

            button_frame = tk.Frame(card, bg=WHITE)
            button_frame.pack(
                fill=tk.X,
                side=tk.BOTTOM,
            )

            update_btn = tk.Label(
                button_frame,
                text="Update Stock",
                font=("Arial", 9, "bold"),
                bg="#34495e",
                fg=WHITE,
                padx=5,
                pady=5,
                cursor="hand2",
            )
            update_btn.pack(
                side=tk.LEFT,
                fill=tk.X,
                expand=True,
                padx=(0, 4),
            )

            update_btn.bind(
                "<Enter>",
                lambda event, button=update_btn: button.config(
                    bg="#1abc9c"
                ),
            )
            update_btn.bind(
                "<Leave>",
                lambda event, button=update_btn: button.config(
                    bg="#34495e"
                ),
            )
            update_btn.bind(
                "<Button-1>",
                lambda event, selected=dict(product): open_stock_dialog(
                    parent_frame, selected
                ),
            )

            edit_btn = tk.Label(
                button_frame,
                text="Edit",
                font=("Arial", 9, "bold"),
                bg="#8e44ad",
                fg=WHITE,
                padx=5,
                pady=5,
                cursor="hand2",
            )
            edit_btn.pack(
                side=tk.LEFT,
                fill=tk.X,
                expand=True,
                padx=(4, 0),
            )

            edit_btn.bind(
                "<Enter>",
                lambda event, button=edit_btn: button.config(
                    bg="#9b59b6"
                ),
            )
            edit_btn.bind(
                "<Leave>",
                lambda event, button=edit_btn: button.config(
                    bg="#8e44ad"
                ),
            )
            edit_btn.bind(
                "<Button-1>",
                lambda event, selected=dict(product): open_edit_product_dialog(
                    parent_frame, selected
                ),
            )

    def perform_search():
        """Search active products by ID or partial name."""
        search_term = search_entry.get().strip()

        if search_term == placeholder:
            search_term = ""

        products = search_active_products(search_term)
        render_products(products)

    def clear_search():
        """Clear search and show all active products."""
        search_entry.delete(0, tk.END)
        search_entry.insert(0, placeholder)
        render_products(get_inventory_products())
        search_entry.focus_set()

    tk.Button(
        search_frame,
        text="Search",
        command=perform_search,
        bg="#34495e",
        fg=WHITE,
        activebackground="#1abc9c",
        activeforeground=WHITE,
        relief=tk.FLAT,
        padx=16,
        pady=7,
        cursor="hand2",
    ).pack(side=tk.LEFT, padx=(8, 0))

    tk.Button(
        search_frame,
        text="Clear",
        command=clear_search,
        bg="#e9ece8",
        fg=TEXT,
        activebackground="#dde2de",
        activeforeground=TEXT,
        relief=tk.FLAT,
        padx=16,
        pady=7,
        cursor="hand2",
    ).pack(side=tk.LEFT, padx=(8, 0))

    search_entry.bind(
        "<Return>",
        lambda event: perform_search(),
    )

    search_entry.bind(
        "<FocusIn>",
        lambda event: (
            search_entry.delete(0, tk.END)
            if search_entry.get() == placeholder
            else None
        ),
    )

    search_entry.bind(
        "<Escape>",
        lambda event: clear_search(),
    )

    render_products(get_inventory_products())


def open_stock_dialog(parent_frame, product):
    """Open a dialog to update a product's stock quantity."""
    dialog = create_modal_window(
        parent_frame,
        "Update Stock",
        360,
        250,
    )

    form = tk.Frame(
        dialog,
        bg=WHITE,
        padx=24,
        pady=20,
    )
    form.pack(
        fill=tk.BOTH,
        expand=True,
        padx=14,
        pady=14,
    )

    tk.Label(
        form,
        text=f"Update Stock: {product['name']}",
        font=("Arial", 14, "bold"),
        bg=WHITE,
        fg=TEXT,
        wraplength=280,
    ).pack(anchor=tk.W, pady=(0, 8))

    tk.Label(
        form,
        text=f"Current stock: {product['quantity']}",
        font=("Arial", 10),
        bg=WHITE,
        fg=MUTED,
    ).pack(anchor=tk.W, pady=(0, 12))

    tk.Label(
        form,
        text="New stock quantity",
        font=("Arial", 10, "bold"),
        bg=WHITE,
        fg=TEXT,
    ).pack(anchor=tk.W, pady=(0, 5))

    quantity_entry = tk.Entry(
        form,
        font=("Arial", 11),
        relief=tk.SOLID,
        bd=1,
    )
    quantity_entry.pack(
        fill=tk.X,
        ipady=5,
        pady=(0, 14),
    )
    quantity_entry.insert(0, str(product["quantity"]))

    buttons = tk.Frame(form, bg=WHITE)
    buttons.pack(fill=tk.X)

    tk.Button(
        buttons,
        text="Save Changes",
        command=lambda: _submit_stock_quantity(
            parent_frame,
            dialog,
            product["id"],
            quantity_entry,
        ),
        bg="#1abc9c",
        fg="#000000",
        activebackground="#16a085",
        activeforeground="#000000",
        relief=tk.FLAT,
        font=("Arial", 10, "bold"),
        pady=9,
        cursor="hand2",
    ).pack(
        side=tk.LEFT,
        fill=tk.X,
        expand=True,
        padx=(0, 6),
    )

    tk.Button(
        buttons,
        text="Cancel",
        command=dialog.destroy,
        bg="#e9ece8",
        fg=TEXT,
        relief=tk.FLAT,
        font=("Arial", 10),
        pady=9,
        cursor="hand2",
    ).pack(
        side=tk.LEFT,
        fill=tk.X,
        expand=True,
        padx=(6, 0),
    )

    dialog.bind(
        "<Return>",
        lambda event: _submit_stock_quantity(
            parent_frame,
            dialog,
            product["id"],
            quantity_entry,
        ),
    )
    dialog.bind("<Escape>", lambda event: dialog.destroy())

    dialog.grab_set()
    quantity_entry.focus_set()
    quantity_entry.selection_range(0, tk.END)


def _submit_stock_quantity(
    parent_frame,
    dialog,
    product_id,
    quantity_entry,
):
    """Validate and save stock, then refresh the inventory."""
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
        messagebox.showerror(
            "Invalid Stock",
            str(error),
            parent=dialog,
        )
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


def open_edit_product_dialog(parent_frame, product):
    """Open a dialog to edit a product's name and price."""
    dialog = create_modal_window(
        parent_frame,
        "Edit Product",
        380,
        330,
    )

    form = tk.Frame(
        dialog,
        bg=WHITE,
        padx=28,
        pady=24,
    )
    form.pack(
        fill=tk.BOTH,
        expand=True,
        padx=14,
        pady=14,
    )

    tk.Label(
        form,
        text="Edit Product",
        font=("Arial", 16, "bold"),
        bg=WHITE,
        fg=TEXT,
    ).pack(anchor=tk.W, pady=(0, 6))

    tk.Label(
        form,
        text=f"Product ID: {product['id']}",
        font=("Arial", 9),
        bg=WHITE,
        fg=MUTED,
    ).pack(anchor=tk.W, pady=(0, 18))

    tk.Label(
        form,
        text="Product Name",
        font=("Arial", 10, "bold"),
        bg=WHITE,
        fg=TEXT,
    ).pack(anchor=tk.W, pady=(0, 5))

    name_entry = tk.Entry(
        form,
        font=("Arial", 11),
        relief=tk.SOLID,
        bd=1,
    )
    name_entry.pack(fill=tk.X, ipady=6, pady=(0, 14))
    name_entry.insert(0, product["name"])

    tk.Label(
        form,
        text="Price",
        font=("Arial", 10, "bold"),
        bg=WHITE,
        fg=TEXT,
    ).pack(anchor=tk.W, pady=(0, 5))

    price_entry = tk.Entry(
        form,
        font=("Arial", 11),
        relief=tk.SOLID,
        bd=1,
    )
    price_entry.pack(fill=tk.X, ipady=6, pady=(0, 18))
    price_entry.insert(0, f"{product['price']:.2f}")

    buttons = tk.Frame(form, bg=WHITE)
    buttons.pack(fill=tk.X)

    tk.Button(
        buttons,
        text="Save Changes",
        command=lambda: _submit_product_edit(
            parent_frame,
            dialog,
            product["id"],
            name_entry,
            price_entry,
        ),
        bg="#8e44ad",
        fg=WHITE,
        activebackground="#9b59b6",
        activeforeground=WHITE,
        relief=tk.FLAT,
        font=("Arial", 10, "bold"),
        pady=10,
        cursor="hand2",
    ).pack(
        side=tk.LEFT,
        fill=tk.X,
        expand=True,
        padx=(0, 6),
    )

    tk.Button(
        buttons,
        text="Cancel",
        command=dialog.destroy,
        bg="#e9ece8",
        fg=TEXT,
        relief=tk.FLAT,
        font=("Arial", 10),
        pady=10,
        cursor="hand2",
    ).pack(
        side=tk.LEFT,
        fill=tk.X,
        expand=True,
        padx=(6, 0),
    )

    dialog.bind(
        "<Return>",
        lambda event: _submit_product_edit(
            parent_frame,
            dialog,
            product["id"],
            name_entry,
            price_entry,
        ),
    )
    dialog.bind("<Escape>", lambda event: dialog.destroy())

    dialog.grab_set()
    name_entry.focus_set()
    name_entry.selection_range(0, tk.END)


def _submit_product_edit(
    parent_frame,
    dialog,
    product_id,
    name_entry,
    price_entry,
):
    """Validate and save the edited product."""
    name = name_entry.get().strip()
    price_text = price_entry.get().strip()

    if not name:
        messagebox.showerror(
            "Missing Name",
            "Product name cannot be empty.",
            parent=dialog,
        )
        name_entry.focus_set()
        return

    if not price_text:
        messagebox.showerror(
            "Missing Price",
            "Product price cannot be empty.",
            parent=dialog,
        )
        price_entry.focus_set()
        return

    try:
        price = Decimal(price_text)
    except (ValueError, InvalidOperation):
        messagebox.showerror(
            "Invalid Price",
            "Enter a valid product price.",
            parent=dialog,
        )
        price_entry.focus_set()
        return

    try:
        update_product_details(product_id, name, price)
    except ValueError as error:
        messagebox.showerror(
            "Cannot Update Product",
            str(error),
            parent=dialog,
        )
        price_entry.focus_set()
        return
    except sqlite3.Error as error:
        messagebox.showerror(
            "Update Failed",
            f"The product could not be updated: {error}",
            parent=dialog,
        )
        return

    dialog.destroy()
    create_inventory_view(parent_frame)


def open_add_product_dialog(parent_frame):
    """Open a modal form for adding a product to inventory."""
    dialog = create_modal_window(
        parent_frame,
        "Add Product",
        380,
        370,
    )

    form = tk.Frame(
        dialog,
        bg=WHITE,
        padx=28,
        pady=24,
    )
    form.pack(
        fill=tk.BOTH,
        expand=True,
        padx=14,
        pady=14,
    )

    tk.Label(
        form,
        text="Add Product",
        font=("Arial", 16, "bold"),
        bg=WHITE,
        fg=TEXT,
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
            bg=WHITE,
            fg=TEXT,
        ).pack(anchor=tk.W, pady=(0, 5))

        entry = tk.Entry(
            form,
            font=("Arial", 11),
            relief=tk.SOLID,
            bd=1,
        )
        entry.pack(fill=tk.X, ipady=6, pady=(0, 14))
        entries[key] = entry

    buttons = tk.Frame(form, bg=WHITE)
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
        fg=TEXT,
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
    """Open a dialog to remove a product by ID or name."""
    dialog = create_modal_window(
        parent_frame,
        "Remove Product",
        420,
        300,
    )

    form = tk.Frame(
        dialog,
        bg=WHITE,
        padx=26,
        pady=20,
    )
    form.pack(
        fill=tk.BOTH,
        expand=True,
        padx=12,
        pady=12,
    )

    tk.Label(
        form,
        text="Remove Product",
        font=("Arial", 16, "bold"),
        bg=WHITE,
        fg=TEXT,
    ).pack(anchor=tk.W, pady=(0, 8))

    tk.Label(
        form,
        text="Enter product ID or name",
        font=("Arial", 10, "bold"),
        bg=WHITE,
        fg=TEXT,
    ).pack(anchor=tk.W, pady=(0, 5))

    product_entry = tk.Entry(
        form,
        font=("Arial", 11),
        relief=tk.SOLID,
        bd=1,
    )
    product_entry.pack(fill=tk.X, ipady=6, pady=(0, 8))

    tk.Label(
        form,
        text="Example: 3 or Mouse",
        font=("Arial", 9),
        bg=WHITE,
        fg=MUTED,
    ).pack(anchor=tk.W, pady=(0, 14))

    buttons = tk.Frame(form, bg=WHITE)
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
            parent_frame,
            dialog,
            product_entry,
        ),
    )
    remove_button.bind(
        "<Enter>",
        lambda event: remove_button.config(bg="#ec8882"),
    )
    remove_button.bind(
        "<Leave>",
        lambda event: remove_button.config(bg="#f3a7a3"),
    )

    cancel_button = tk.Label(
        buttons,
        text="Cancel",
        bg="#e9ece8",
        fg=TEXT,
        font=("Arial", 12),
        padx=14,
        pady=15,
        cursor="hand2",
    )
    cancel_button.pack(fill=tk.X)
    cancel_button.bind(
        "<Button-1>",
        lambda event: dialog.destroy(),
    )
    cancel_button.bind(
        "<Enter>",
        lambda event: cancel_button.config(bg="#dde2de"),
    )
    cancel_button.bind(
        "<Leave>",
        lambda event: cancel_button.config(bg="#e9ece8"),
    )

    dialog.bind(
        "<Return>",
        lambda event: _submit_remove_product(
            parent_frame,
            dialog,
            product_entry,
        ),
    )
    dialog.bind("<Escape>", lambda event: dialog.destroy())

    dialog.grab_set()
    product_entry.focus_set()


def _submit_remove_product(
    parent_frame,
    dialog,
    product_entry,
):
    """Remove a product by ID or exact name."""
    search_value = product_entry.get().strip()

    if not search_value:
        messagebox.showerror(
            "Missing Product",
            "Enter a product ID or product name.",
            parent=dialog,
        )
        product_entry.focus_set()
        return

    try:
        if search_value.isdigit():
            remove_product_by_id(int(search_value))
        else:
            remove_product_by_name(search_value)

    except ValueError as error:
        messagebox.showerror(
            "Cannot Remove Product",
            str(error),
            parent=dialog,
        )
        product_entry.focus_set()
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


def _submit_new_product(
    parent_frame,
    dialog,
    name_entry,
    price_entry,
    quantity_entry,
):
    """Validate and create a product in SQLite."""
    name = name_entry.get().strip()

    try:
        price = Decimal(price_entry.get())
    except (ValueError, InvalidOperation):
        messagebox.showerror(
            "Invalid Price",
            "Enter a valid product price.",
            parent=dialog,
        )
        price_entry.focus_set()
        return

    try:
        quantity = int(quantity_entry.get())
    except ValueError:
        messagebox.showerror(
            "Invalid Quantity",
            "Enter a whole-number quantity.",
            parent=dialog,
        )
        quantity_entry.focus_set()
        return

    try:
        product_id = create_product(name, price, quantity)
    except ValueError as error:
        messagebox.showerror(
            "Invalid Product",
            str(error),
            parent=dialog,
        )
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
