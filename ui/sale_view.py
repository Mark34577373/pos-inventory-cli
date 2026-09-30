from datetime import datetime
import sqlite3
import tkinter as tk
from tkinter import messagebox, simpledialog

from config import TAX_RATE
from database.database import get_db_connection
from services.checkout import session_cart
from services.inventory import find_product_by_id, find_products_by_ids
from services.transaction_service import complete_transaction
from ui.dialogs import create_modal_window

_cart_items_box = None
_totals_grid = None
_products_grid = None


def create_sale_view(parent_frame):
    """Render the live product catalog and session cart checkout screen."""
    global _cart_items_box, _totals_grid, _products_grid

    title = tk.Label(
        parent_frame,
        text="New Sale",
        font=("Arial", 22, "bold"),
        bg="#f8f9fa",
        fg="#333333",
    )
    title.pack(anchor=tk.W, pady=(0, 20))

    split_container = tk.Frame(parent_frame, bg="#f8f9fa")
    split_container.pack(fill=tk.BOTH, expand=True)

    products_panel = tk.Frame(split_container, bg="#f8f9fa")
    products_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 20))

    products_title = tk.Label(
        products_panel,
        text="Products",
        font=("Arial", 14, "bold"),
        bg="#f8f9fa",
        fg="#2c3e50",
    )
    products_title.pack(anchor=tk.W, pady=(0, 10))

    _products_grid = tk.Frame(products_panel, bg="#f8f9fa")
    _products_grid.pack(fill=tk.BOTH, expand=True)

    cart_panel = tk.Frame(
        split_container,
        bg="white",
        width=320,
        highlightbackground="#e0e0e0",
        highlightthickness=1,
        padx=20,
        pady=20,
    )
    cart_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=False)
    cart_panel.pack_propagate(False)

    cart_title = tk.Label(
        cart_panel,
        text="Current Cart",
        font=("Arial", 14, "bold"),
        bg="white",
        fg="#2c3e50",
    )
    cart_title.pack(anchor=tk.W, pady=(0, 15))

    totals_frame = tk.Frame(cart_panel, bg="white")
    totals_frame.pack(fill=tk.X, side=tk.BOTTOM)

    checkout_button = tk.Label(
        totals_frame,
        text="Checkout",
        font=("Arial", 11, "bold"),
        bg="#a8e6cf",
        fg="#000000",
        pady=12,
        cursor="hand2",
        anchor=tk.CENTER,
    )
    checkout_button.pack(fill=tk.X, side=tk.BOTTOM)
    checkout_button.bind(
        "<Button-1>", lambda event: handle_ui_checkout(parent_frame)
    )
    checkout_button.bind(
        "<Enter>", lambda event: checkout_button.config(bg="#84d4b1")
    )
    checkout_button.bind(
        "<Leave>", lambda event: checkout_button.config(bg="#a8e6cf")
    )

    _totals_grid = tk.Frame(totals_frame, bg="white")
    _totals_grid.pack(fill=tk.X, side=tk.BOTTOM, pady=(0, 12))
    _totals_grid.columnconfigure(0, weight=1)
    _totals_grid.columnconfigure(1, weight=1)

    divider = tk.Frame(totals_frame, bg="#e0e0e0", height=1)
    divider.pack(fill=tk.X, side=tk.BOTTOM, pady=(0, 12))

    _cart_items_box = tk.Frame(cart_panel, bg="white")
    _cart_items_box.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

    refresh_ui_product_display()
    refresh_ui_cart_display()


def refresh_ui_product_display():
    """Reload product cards and their available stock from SQLite."""
    if _products_grid is None or not _products_grid.winfo_exists():
        return

    for widget in _products_grid.winfo_children():
        widget.destroy()

    conn = get_db_connection()
    try:
        products = conn.execute(
            "SELECT * FROM products WHERE is_active = 1 ORDER BY name"
        ).fetchall()
    finally:
        conn.close()

    for index, product_row in enumerate(products):
        product = dict(product_row)
        row, column = divmod(index, 3)
        card = tk.Frame(
            _products_grid,
            bg="white",
            highlightbackground="#e0e0e0",
            highlightthickness=1,
            padx=12,
            pady=12,
            width=150,
            height=120,
        )
        card.grid(row=row, column=column, padx=8, pady=8, sticky="nsew")
        card.grid_propagate(False)

        tk.Label(
            card,
            text=product["name"],
            font=("Arial", 11, "bold"),
            bg="white",
            fg="#2c3e50",
        ).pack(anchor=tk.W)
        tk.Label(
            card,
            text=f"${product['price']:.2f}",
            font=("Arial", 10),
            bg="white",
            fg="#7f8c8d",
        ).pack(anchor=tk.W)
        tk.Label(
            card,
            text=f"Stock: {product['quantity']}",
            font=("Arial", 9),
            bg="white",
            fg="#7f8c8d",
        ).pack(anchor=tk.W, pady=(2, 5))

        add_button = tk.Label(
            card,
            text="Add to Cart",
            font=("Arial", 9, "bold"),
            bg="#34495e",
            fg="white",
            padx=8,
            pady=3,
            cursor="hand2",
        )
        add_button.pack(side=tk.BOTTOM, fill=tk.X)
        add_button.bind(
            "<Button-1>",
            lambda event, product_id=product["id"]: handle_add_item(product_id),
        )
        add_button.bind("<Enter>", lambda event, button=add_button: button.config(bg="#3498db"))
        add_button.bind("<Leave>", lambda event, button=add_button: button.config(bg="#34495e"))


def handle_add_item(product_id):
    """Add one unit when live inventory has sufficient available stock."""
    product = find_product_by_id(product_id)
    if not product:
        messagebox.showwarning(
            "Product Unavailable",
            "This product is no longer available. The catalog will refresh.",
        )
        refresh_ui_product_display()
        return

    current_quantity = session_cart.get_items().get(product_id, 0)
    if product["quantity"] <= current_quantity:
        messagebox.showwarning(
            "Out of Stock",
            f"All available units ({product['quantity']}) are already in the cart.",
        )
        return

    session_cart.add_item(product_id, 1)
    refresh_ui_cart_display()


def refresh_ui_cart_display():
    """Redraw cart rows and totals from the current session cart."""
    if (
        _cart_items_box is None
        or _totals_grid is None
        or not _cart_items_box.winfo_exists()
        or not _totals_grid.winfo_exists()
    ):
        return

    for widget in _cart_items_box.winfo_children():
        widget.destroy()
    for widget in _totals_grid.winfo_children():
        widget.destroy()

    items = session_cart.get_items()
    if not items:
        tk.Label(
            _cart_items_box,
            text="Your cart is empty. Use Add to Cart on a product.",
            font=("Arial", 10, "italic"),
            bg="white",
            fg="#95a5a6",
        ).pack(anchor=tk.W, pady=10)
        render_totals_grid(0.0, 0.0, 0.0)
        return

    products = find_products_by_ids(list(items))
    subtotal = 0.0

    for product_id, quantity in items.items():
        product = products.get(product_id)
        if product is None:
            continue

        line_total = product["price"] * quantity
        subtotal += line_total
        row = tk.Frame(_cart_items_box, bg="white")
        row.pack(fill=tk.X, pady=4)
        item_details = tk.Frame(row, bg="white")
        item_details.pack(fill=tk.X)
        tk.Label(
            item_details,
            text=f"{product['name']} x{quantity}",
            font=("Arial", 11),
            bg="white",
            fg="#333333",
            wraplength=180,
            justify=tk.LEFT,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, anchor=tk.W)
        tk.Label(
            item_details,
            text=f"${line_total:.2f}",
            font=("Arial", 11),
            bg="white",
            fg="#333333",
        ).pack(side=tk.RIGHT)
        remove_button = tk.Label(
            row,
            text="Remove",
            font=("Arial", 9, "bold"),
            bg="#f3a7a3",
            fg="#000000",
            padx=7,
            pady=3,
            cursor="hand2",
        )
        remove_button.pack(fill=tk.X, pady=(4, 0))
        remove_button.bind(
            "<Button-1>",
            lambda event, item_id=product_id: handle_remove_cart_item(item_id),
        )
        remove_button.bind(
            "<Enter>",
            lambda event, button=remove_button: button.config(bg="#ec8882"),
        )
        remove_button.bind(
            "<Leave>",
            lambda event, button=remove_button: button.config(bg="#f3a7a3"),
        )

    tax = subtotal * TAX_RATE
    render_totals_grid(subtotal, tax, subtotal + tax)


def handle_remove_cart_item(product_id):
    """Remove a product and its full quantity from the active cart."""
    session_cart.get_items().pop(product_id, None)
    refresh_ui_cart_display()


def render_totals_grid(subtotal, tax, total):
    """Display the current subtotal, configured tax, and total."""
    labels = (
        ("Subtotal", subtotal, False),
        (f"Tax ({TAX_RATE * 100:.2f}%)", tax, False),
        ("Total", total, True),
    )
    for row, (label, amount, emphasized) in enumerate(labels):
        font = ("Arial", 11, "bold") if emphasized else ("Arial", 11)
        color = "#333333" if emphasized else "#666666"
        tk.Label(
            _totals_grid,
            text=label,
            font=font,
            bg="white",
            fg=color,
        ).grid(row=row, column=0, sticky=tk.W, pady=2)
        tk.Label(
            _totals_grid,
            text=f"${amount:.2f}",
            font=font,
            bg="white",
            fg=color,
        ).grid(row=row, column=1, sticky=tk.E, pady=2)


def handle_ui_checkout(parent_frame=None):
    """Collect payment details and persist checkout through the transaction service."""
    if session_cart.is_empty():
        messagebox.showwarning("Empty Cart", "Cannot checkout an empty shopping cart.")
        return

    payment_choice = simpledialog.askstring(
        "Payment Method", "Choose Payment Method:\n1. Cash\n2. Card"
    )
    if payment_choice is None:
        return

    payment_choice = payment_choice.strip()
    if payment_choice not in ("1", "2"):
        messagebox.showwarning("Invalid Payment", "Choose 1 for Cash or 2 for Card.")
        return

    payment_method = "Cash" if payment_choice == "1" else "Card"
    cart_items = dict(session_cart.get_items())
    products = find_products_by_ids(list(cart_items))
    if len(products) != len(cart_items):
        messagebox.showerror(
            "Checkout Failed",
            "A product in the cart is no longer available. Please review the cart.",
        )
        refresh_ui_cart_display()
        return

    subtotal = sum(
        products[product_id]["price"] * quantity
        for product_id, quantity in cart_items.items()
    )
    tax = subtotal * TAX_RATE
    total = subtotal + tax

    cash_received = None
    change = None
    if payment_method == "Cash":
        while True:
            cash_received = simpledialog.askfloat(
                "Cash Payment",
                f"Total due: ${total:.2f}\nEnter cash received:",
                minvalue=0.0,
            )
            if cash_received is None:
                return
            if cash_received < total:
                messagebox.showerror(
                    "Insufficient Cash",
                    f"Cash received must be at least ${total:.2f}.",
                )
                continue
            change = cash_received - total
            break

    items_to_log = [
        {
            "id": products[product_id]["id"],
            "qty": quantity,
            "price": products[product_id]["price"],
        }
        for product_id, quantity in cart_items.items()
    ]

    try:
        transaction_id = complete_transaction(items_to_log, total, payment_method)
    except ValueError as error:
        messagebox.showerror("Checkout Failed", str(error))
        refresh_ui_product_display()
        return
    except sqlite3.Error as error:
        messagebox.showerror(
            "Checkout Failed",
            "The sale could not be recorded. No inventory changes were saved.",
        )
        return

    receipt_items = [
        {
            "name": products[product_id]["name"],
            "quantity": quantity,
            "total": products[product_id]["price"] * quantity,
        }
        for product_id, quantity in cart_items.items()
    ]

    session_cart.clear()
    refresh_ui_product_display()
    refresh_ui_cart_display()
    show_receipt_window(
        parent=parent_frame,
        transaction_id=transaction_id,
        timestamp=datetime.now(),
        items=receipt_items,
        subtotal=subtotal,
        tax=tax,
        total=total,
        payment_method=payment_method,
        cash_received=cash_received,
        change=change,
    )


def show_receipt_window(
    parent,
    transaction_id,
    timestamp,
    items,
    subtotal,
    tax,
    total,
    payment_method,
    cash_received=None,
    change=None,
):
    """Open a modal, print-style receipt for the completed transaction."""
    screen_height = parent.winfo_screenheight()
    window_height = min(620, screen_height - 80)
    window_width = 390
    receipt_window = create_modal_window(
        parent,
        "TCCP Receipt",
        window_width,
        window_height,
        background="#e9ece8",
        resizable=(False, True),
    )

    paper = tk.Frame(receipt_window, bg="white", padx=28, pady=22)
    paper.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

    tk.Label(
        paper,
        text="TCCP RECEIPT",
        font=("Arial", 15, "bold"),
        bg="white",
        fg="#263b35",
    ).pack(pady=(0, 5))
    tk.Frame(paper, bg="#d9ded9", height=1).pack(fill=tk.X, pady=(0, 12))

    metadata = tk.Frame(paper, bg="white")
    metadata.pack(fill=tk.X, pady=(0, 12))
    tk.Label(
        metadata,
        text=f"Transaction #{transaction_id}",
        font=("Arial", 10, "bold"),
        bg="white",
        fg="#333333",
    ).pack(anchor=tk.W)
    tk.Label(
        metadata,
        text=timestamp.strftime("%B %d, %Y at %I:%M %p").replace(" 0", " "),
        font=("Arial", 9),
        bg="white",
        fg="#737b75",
    ).pack(anchor=tk.W, pady=(3, 0))

    tk.Label(
        paper,
        text="ITEMS",
        font=("Arial", 9, "bold"),
        bg="white",
        fg="#737b75",
    ).pack(anchor=tk.W, pady=(0, 5))

    items_area = tk.Frame(paper, bg="white")
    items_area.pack(fill=tk.BOTH, expand=True, pady=(0, 12))
    items_canvas = tk.Canvas(items_area, bg="white", highlightthickness=0)
    items_scrollbar = tk.Scrollbar(
        items_area, orient=tk.VERTICAL, command=items_canvas.yview
    )
    items_canvas.configure(yscrollcommand=items_scrollbar.set)
    items_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    items_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    items_frame = tk.Frame(items_canvas, bg="white")
    items_window = items_canvas.create_window(
        (0, 0), window=items_frame, anchor="nw"
    )
    items_frame.bind(
        "<Configure>",
        lambda event: items_canvas.configure(
            scrollregion=items_canvas.bbox("all")
        ),
    )
    items_canvas.bind(
        "<Configure>",
        lambda event: items_canvas.itemconfigure(items_window, width=event.width),
    )

    for item in items:
        item_row = tk.Frame(items_frame, bg="white")
        item_row.pack(fill=tk.X, pady=4)
        tk.Label(
            item_row,
            text=f"{item['name']} x{item['quantity']}",
            font=("Arial", 10),
            bg="white",
            fg="#333333",
        ).pack(side=tk.LEFT)
        tk.Label(
            item_row,
            text=f"${item['total']:.2f}",
            font=("Arial", 10),
            bg="white",
            fg="#333333",
        ).pack(side=tk.RIGHT)

    footer = tk.Frame(paper, bg="white")
    footer.pack(fill=tk.X, side=tk.BOTTOM)
    tk.Frame(footer, bg="#d9ded9", height=1).pack(fill=tk.X, pady=(0, 8))

    for label, amount, emphasized in (
        ("Subtotal", subtotal, False),
        (f"Tax ({TAX_RATE * 100:.2f}%)", tax, False),
        ("TOTAL", total, True),
    ):
        row = tk.Frame(footer, bg="white")
        row.pack(fill=tk.X, pady=3)
        font = ("Arial", 11, "bold") if emphasized else ("Arial", 10)
        foreground = "#263b35" if emphasized else "#666d68"
        tk.Label(
            row, text=label, font=font, bg="white", fg=foreground
        ).pack(side=tk.LEFT)
        tk.Label(
            row, text=f"${amount:.2f}", font=font, bg="white", fg=foreground
        ).pack(side=tk.RIGHT)

    tk.Frame(footer, bg="#d9ded9", height=1).pack(fill=tk.X, pady=8)
    tk.Label(
        footer,
        text=f"Payment: {payment_method}",
        font=("Arial", 10, "bold"),
        bg="white",
        fg="#333333",
    ).pack(anchor=tk.W, pady=(0, 4))

    if payment_method == "Cash":
        for label, amount in (("Cash received", cash_received), ("Change", change)):
            row = tk.Frame(footer, bg="white")
            row.pack(fill=tk.X, pady=2)
            tk.Label(
                row, text=label, font=("Arial", 10), bg="white", fg="#666d68"
            ).pack(side=tk.LEFT)
            tk.Label(
                row,
                text=f"${amount:.2f}",
                font=("Arial", 10),
                bg="white",
                fg="#666d68",
            ).pack(side=tk.RIGHT)

    tk.Label(
        footer,
        text="Thank you for your purchase!",
        font=("Arial", 10, "italic"),
        bg="white",
        fg="#737b75",
    ).pack(pady=(10, 12))
    tk.Button(
        footer,
        text="Done",
        command=receipt_window.destroy,
        bg="#263b35",
        fg="white",
        activebackground="#38574d",
        activeforeground="white",
        relief=tk.FLAT,
        padx=16,
        pady=7,
        cursor="hand2",
    ).pack(fill=tk.X)

    receipt_window.grab_set()
    receipt_window.focus_set()
