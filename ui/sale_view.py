import sqlite3
import tkinter as tk
from tkinter import messagebox, simpledialog

from config import business_now
from services.checkout import session_cart
from services.inventory import (
    find_product_by_id,
    find_products_by_ids,
    list_active_products,
    search_active_products,
)
from services.money import calculate_tax_cents, format_cents, from_cents, parse_cash
from services.settings import default_threshold, tax_rate
from services.transaction_service import complete_transaction
from ui.dialogs import create_modal_window

_cart_items_box = None
_totals_grid = None
_products_grid = None
_products_search_entry = None


LOW_STOCK_THRESHOLD = 5


BG = "#f8f9fa"
WHITE = "#ffffff"
TEXT = "#333333"
MUTED = "#7f8c8d"
BORDER = "#e0e0e0"
GREEN = "#27ae60"
AMBER = "#d68910"
RED = "#e74c3c"
BUTTON = "#34495e"
BUTTON_HOVER = "#3498db"


def create_sale_view(parent_frame):
    """Render the product catalog and current-cart checkout screen."""
    global _cart_items_box
    global _totals_grid
    global _products_grid
    global _products_search_entry

    title = tk.Label(
        parent_frame,
        text="New Sale",
        font=("Arial", 22, "bold"),
        bg=BG,
        fg=TEXT,
    )
    title.pack(anchor=tk.W, pady=(0, 20))

    split_container = tk.Frame(parent_frame, bg=BG)
    split_container.pack(fill=tk.BOTH, expand=True)

    # ---------------------------------------------------------
    # Product panel
    # ---------------------------------------------------------

    products_panel = tk.Frame(split_container, bg=BG)
    products_panel.pack(
        side=tk.LEFT,
        fill=tk.BOTH,
        expand=True,
        padx=(0, 20),
    )

    products_title = tk.Label(
        products_panel,
        text="Products",
        font=("Arial", 14, "bold"),
        bg=BG,
        fg="#2c3e50",
    )
    products_title.pack(anchor=tk.W, pady=(0, 10))

    # ---------------------------------------------------------
    # Product search
    # ---------------------------------------------------------

    search_frame = tk.Frame(products_panel, bg=BG)
    search_frame.pack(fill=tk.X, pady=(0, 10))

    _products_search_entry = tk.Entry(
        search_frame,
        font=("Arial", 10),
        relief=tk.SOLID,
        bd=1,
    )
    _products_search_entry.pack(
        side=tk.LEFT,
        fill=tk.X,
        expand=True,
        ipady=6,
    )

    placeholder_text = "Search products by name or ID..."
    _products_search_entry.insert(0, placeholder_text)
    _products_search_entry.config(fg="#95a5a6")

    def clear_placeholder(event=None):
        if _products_search_entry.get() == placeholder_text:
            _products_search_entry.delete(0, tk.END)
            _products_search_entry.config(fg=TEXT)

    def restore_placeholder(event=None):
        if not _products_search_entry.get().strip():
            _products_search_entry.delete(0, tk.END)
            _products_search_entry.insert(0, placeholder_text)
            _products_search_entry.config(fg="#95a5a6")

    def perform_product_search(event=None):
        search_term = _products_search_entry.get().strip()

        if search_term == placeholder_text:
            search_term = ""

        if not search_term:
            refresh_ui_product_display()
            return

        products = search_active_products(search_term)
        refresh_ui_product_display(products)

    def clear_product_search():
        _products_search_entry.delete(0, tk.END)
        _products_search_entry.insert(0, placeholder_text)
        _products_search_entry.config(fg="#95a5a6")
        refresh_ui_product_display()

    _products_search_entry.bind("<FocusIn>", clear_placeholder)
    _products_search_entry.bind("<FocusOut>", restore_placeholder)
    _products_search_entry.bind("<Return>", perform_product_search)
    _products_search_entry.bind(
        "<Escape>",
        lambda event: clear_product_search(),
    )

    search_button = tk.Button(
        search_frame,
        text="Search",
        command=perform_product_search,
        font=("Arial", 9, "bold"),
        bg=BUTTON,
        fg=WHITE,
        activebackground=BUTTON_HOVER,
        activeforeground=WHITE,
        relief=tk.FLAT,
        padx=12,
        pady=5,
        cursor="hand2",
    )
    search_button.pack(side=tk.LEFT, padx=(8, 4))

    clear_button = tk.Button(
        search_frame,
        text="Clear",
        command=clear_product_search,
        font=("Arial", 9),
        bg="#ecf0f1",
        fg=TEXT,
        activebackground="#d5dbdb",
        relief=tk.FLAT,
        padx=12,
        pady=5,
        cursor="hand2",
    )
    clear_button.pack(side=tk.LEFT)

    # ---------------------------------------------------------
    # Product grid
    # ---------------------------------------------------------

    _products_grid = tk.Frame(products_panel, bg=BG)
    _products_grid.pack(fill=tk.BOTH, expand=True)

    # ---------------------------------------------------------
    # Cart panel
    # ---------------------------------------------------------

    cart_panel = tk.Frame(
        split_container,
        bg=WHITE,
        width=320,
        highlightbackground=BORDER,
        highlightthickness=1,
        padx=20,
        pady=20,
    )
    cart_panel.pack(
        side=tk.RIGHT,
        fill=tk.BOTH,
        expand=False,
    )
    cart_panel.pack_propagate(False)

    cart_title = tk.Label(
        cart_panel,
        text="Current Cart",
        font=("Arial", 14, "bold"),
        bg=WHITE,
        fg="#2c3e50",
    )
    cart_title.pack(anchor=tk.W, pady=(0, 15))

    # ---------------------------------------------------------
    # Cart totals and actions
    # ---------------------------------------------------------

    totals_frame = tk.Frame(cart_panel, bg=WHITE)
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
        "<Button-1>",
        lambda event: handle_ui_checkout(cart_panel),
    )
    checkout_button.bind(
        "<Enter>",
        lambda event: checkout_button.config(bg="#84d4b1"),
    )
    checkout_button.bind(
        "<Leave>",
        lambda event: checkout_button.config(bg="#a8e6cf"),
    )

    clear_cart_button = tk.Button(
        totals_frame,
        text="Clear Cart",
        command=handle_clear_cart,
        font=("Arial", 9, "bold"),
        bg="#f3a7a3",
        fg="#000000",
        activebackground="#ec8882",
        relief=tk.FLAT,
        padx=12,
        pady=7,
        cursor="hand2",
    )
    clear_cart_button.pack(
        fill=tk.X,
        side=tk.BOTTOM,
        pady=(8, 8),
    )

    _totals_grid = tk.Frame(totals_frame, bg=WHITE)
    _totals_grid.pack(
        fill=tk.X,
        side=tk.BOTTOM,
        pady=(0, 12),
    )

    _totals_grid.columnconfigure(0, weight=1)
    _totals_grid.columnconfigure(1, weight=1)

    tk.Frame(
        totals_frame,
        bg=BORDER,
        height=1,
    ).pack(
        fill=tk.X,
        side=tk.BOTTOM,
        pady=(0, 12),
    )

    # ---------------------------------------------------------
    # Cart items
    # ---------------------------------------------------------

    _cart_items_box = tk.Frame(cart_panel, bg=WHITE)
    _cart_items_box.pack(
        fill=tk.BOTH,
        expand=True,
        side=tk.TOP,
    )

    refresh_ui_product_display()
    refresh_ui_cart_display()


def refresh_ui_product_display(products=None):
    """Render product cards with clear stock-status indicators."""
    if _products_grid is None or not _products_grid.winfo_exists():
        return

    for widget in _products_grid.winfo_children():
        widget.destroy()

    if products is None:
        products = list_active_products()

    products = [dict(product) for product in products]

    if not products:
        tk.Label(
            _products_grid,
            text="No products found.",
            font=("Arial", 11, "italic"),
            bg=BG,
            fg="#95a5a6",
        ).pack(anchor=tk.CENTER, pady=40)
        return

    for index, product in enumerate(products):
        quantity = product["quantity"]

        if quantity <= 0:
            stock_text = "OUT OF STOCK"
            stock_color = RED
            button_text = "Unavailable"
            button_color = "#bdc3c7"
        elif quantity <= (product["low_stock_threshold"] if product["low_stock_threshold"] is not None else default_threshold()):
            stock_text = f"LOW STOCK · {quantity} left"
            stock_color = AMBER
            button_text = "Add to Cart"
            button_color = BUTTON
        else:
            stock_text = f"IN STOCK · {quantity} available"
            stock_color = GREEN
            button_text = "Add to Cart"
            button_color = BUTTON

        row, column = divmod(index, 3)

        card = tk.Frame(
            _products_grid,
            bg=WHITE,
            highlightbackground=BORDER,
            highlightthickness=1,
            padx=12,
            pady=12,
            width=180,
            height=135,
        )
        card.grid(
            row=row,
            column=column,
            padx=8,
            pady=8,
            sticky="nsew",
        )
        card.grid_propagate(False)

        tk.Label(
            card,
            text=product["name"],
            font=("Arial", 11, "bold"),
            bg=WHITE,
            fg="#2c3e50",
            wraplength=150,
            justify=tk.LEFT,
        ).pack(anchor=tk.W)

        # Use integer cents for consistent money formatting.
        tk.Label(
            card,
            text=format_cents(product["price_cents"]),
            font=("Arial", 10),
            bg=WHITE,
            fg=MUTED,
        ).pack(anchor=tk.W, pady=(3, 2))

        tk.Label(
            card,
            text=stock_text,
            font=("Arial", 8, "bold"),
            bg=WHITE,
            fg=stock_color,
        ).pack(anchor=tk.W, pady=(2, 7))

        add_button = tk.Label(
            card,
            text=button_text,
            font=("Arial", 9, "bold"),
            bg=button_color,
            fg=WHITE,
            padx=8,
            pady=4,
            cursor="hand2" if quantity > 0 else "arrow",
        )
        add_button.pack(side=tk.BOTTOM, fill=tk.X)


        if quantity > 0:
            add_button.bind(
                "<Button-1>",
                lambda event, product_id=product["id"]:
                    handle_add_item(product_id),
            )
            add_button.bind(
                "<Enter>",
                lambda event, button=add_button:
                    button.config(bg=BUTTON_HOVER),
            )
            add_button.bind(
                "<Leave>",
                lambda event, button=add_button:
                    button.config(bg=BUTTON),
            )

    for column in range(3):
        _products_grid.grid_columnconfigure(column, weight=1)


def handle_add_item(product_id):
    """Add one unit only when live inventory has sufficient stock."""
    product = find_product_by_id(product_id)

    if not product:
        messagebox.showwarning(
            "Product Unavailable",
            "This product is no longer available. "
            "The catalog will refresh.",
        )
        refresh_ui_product_display()
        return

    current_quantity = session_cart.get_items().get(product_id, 0)
    available_quantity = product["quantity"]

    if available_quantity <= 0:
        messagebox.showwarning(
            "Out of Stock",
            f"{product['name']} is currently out of stock.",
        )
        refresh_ui_product_display()
        return

    if current_quantity >= available_quantity:
        messagebox.showwarning(
            "Maximum Quantity",
            (
                f"You can only add {available_quantity} "
                f"unit(s) of {product['name']}."
            ),
        )
        refresh_ui_product_display()
        return

    session_cart.add_item(product_id, 1)
    refresh_ui_cart_display()


def handle_increase_cart_item(product_id):
    """Increase a cart item's quantity by one if stock allows it."""
    product = find_product_by_id(product_id)

    if not product:
        messagebox.showwarning(
            "Product Unavailable",
            "This product is no longer available.",
        )
        session_cart.get_items().pop(product_id, None)
        refresh_ui_cart_display()
        refresh_ui_product_display()
        return

    current_quantity = session_cart.get_items().get(product_id, 0)
    available_quantity = product["quantity"]

    if current_quantity >= available_quantity:
        messagebox.showwarning(
            "Maximum Quantity",
            (
                f"You can only add {available_quantity} "
                f"unit(s) of {product['name']}."
            ),
        )
        refresh_ui_product_display()
        return

    session_cart.add_item(product_id, 1)
    refresh_ui_cart_display()


def handle_decrease_cart_item(product_id):
    """Decrease a cart item's quantity by one."""
    items = session_cart.get_items()
    current_quantity = items.get(product_id, 0)

    if current_quantity <= 1:
        items.pop(product_id, None)
    else:
        items[product_id] = current_quantity - 1

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
            bg=WHITE,
            fg="#95a5a6",
            wraplength=240,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=10)

        render_totals_grid(0, 0, 0)
        return

    products = find_products_by_ids(list(items))
    subtotal_cents = 0

    for product_id, quantity in list(items.items()):
        product = products.get(product_id)

        if product is None:
            continue

        line_total_cents = product["price_cents"] * quantity
        subtotal_cents += line_total_cents

        row = tk.Frame(_cart_items_box, bg=WHITE)
        row.pack(fill=tk.X, pady=6)

        item_details = tk.Frame(row, bg=WHITE)
        item_details.pack(fill=tk.X)

        tk.Label(
            item_details,
            text=product["name"],
            font=("Arial", 11, "bold"),
            bg=WHITE,
            fg=TEXT,
            wraplength=180,
            justify=tk.LEFT,
        ).pack(
            side=tk.LEFT,
            fill=tk.X,
            expand=True,
            anchor=tk.W,
        )

        tk.Label(
            item_details,
            text=format_cents(line_total_cents),
            font=("Arial", 11),
            bg=WHITE,
            fg=TEXT,
        ).pack(side=tk.RIGHT)


        controls = tk.Frame(row, bg=WHITE)
        controls.pack(fill=tk.X, pady=(6, 0))

        decrease_button = tk.Label(
            controls,
            text="−",
            font=("Arial", 12, "bold"),
            bg="#ecf0f1",
            fg=TEXT,
            width=3,
            pady=2,
            cursor="hand2",
        )
        decrease_button.pack(side=tk.LEFT)

        tk.Label(
            controls,
            text=str(quantity),
            font=("Arial", 10, "bold"),
            bg=WHITE,
            fg=TEXT,
            width=5,
            pady=3,
        ).pack(side=tk.LEFT)

        increase_button = tk.Label(
            controls,
            text="+",
            font=("Arial", 12, "bold"),
            bg="#ecf0f1",
            fg=TEXT,
            width=3,
            pady=2,
            cursor="hand2",
        )
        increase_button.pack(side=tk.LEFT)

        decrease_button.bind(
            "<Button-1>",
            lambda event, item_id=product_id:
                handle_decrease_cart_item(item_id),
        )
        increase_button.bind(
            "<Button-1>",
            lambda event, item_id=product_id:
                handle_increase_cart_item(item_id),
        )

        decrease_button.bind(
            "<Enter>",
            lambda event, button=decrease_button:
                button.config(bg="#d5dbdb"),
        )
        decrease_button.bind(
            "<Leave>",
            lambda event, button=decrease_button:
                button.config(bg="#ecf0f1"),
        )
        increase_button.bind(
            "<Enter>",
            lambda event, button=increase_button:
                button.config(bg="#d5dbdb"),
        )
        increase_button.bind(
            "<Leave>",
            lambda event, button=increase_button:
                button.config(bg="#ecf0f1"),
        )


        remove_button = tk.Label(
            controls,
            text="Remove",
            font=("Arial", 9, "bold"),
            bg="#f3a7a3",
            fg="#000000",
            padx=7,
            pady=3,
            cursor="hand2",
        )
        remove_button.pack(side=tk.RIGHT)

        remove_button.bind(
            "<Button-1>",
            lambda event, item_id=product_id:
                handle_remove_cart_item(item_id),
        )
        remove_button.bind(
            "<Enter>",
            lambda event, button=remove_button:
                button.config(bg="#ec8882"),
        )
        remove_button.bind(
            "<Leave>",
            lambda event, button=remove_button:
                button.config(bg="#f3a7a3"),
        )

    tax_cents = calculate_tax_cents(subtotal_cents, tax_rate())

    render_totals_grid(
        subtotal_cents,
        tax_cents,
        subtotal_cents + tax_cents,
    )


def handle_remove_cart_item(product_id):
    """Remove a product and its full quantity from the active cart."""
    session_cart.get_items().pop(product_id, None)
    refresh_ui_cart_display()


def handle_clear_cart():
    """Clear the entire active cart after confirmation."""
    if session_cart.is_empty():
        return

    confirmed = messagebox.askyesno(
        "Clear Cart",
        "Are you sure you want to remove all items from the cart?",
    )

    if not confirmed:
        return

    session_cart.clear()
    refresh_ui_cart_display()


def render_totals_grid(subtotal, tax, total):
    """Display subtotal, configured tax, and total in integer cents."""
    labels = (
        ("Subtotal", subtotal, False),
        (f"Tax ({tax_rate() * 100:.4f}%)", tax, False),
        ("Total", total, True),
    )

    for row, (label, amount, emphasized) in enumerate(labels):
        font = (
            ("Arial", 11, "bold")
            if emphasized
            else ("Arial", 11)
        )

        color = TEXT if emphasized else "#666666"

        tk.Label(
            _totals_grid,
            text=label,
            font=font,
            bg=WHITE,
            fg=color,
        ).grid(
            row=row,
            column=0,
            sticky=tk.W,
            pady=2,
        )

        tk.Label(
            _totals_grid,
            text=format_cents(amount),
            font=font,
            bg=WHITE,
            fg=color,
        ).grid(
            row=row,
            column=1,
            sticky=tk.E,
            pady=2,
        )


def handle_ui_checkout(parent_frame=None):
    """Validate the cart and open payment selection."""
    if session_cart.is_empty():
        messagebox.showwarning(
            "Empty Cart",
            "Cannot checkout an empty shopping cart.",
        )
        return

    cart_items = dict(session_cart.get_items())
    products = find_products_by_ids(list(cart_items))

    if len(products) != len(cart_items):
        messagebox.showerror(
            "Checkout Failed",
            "A product in the cart is no longer available. "
            "Please review the cart.",
        )
        refresh_ui_product_display()
        refresh_ui_cart_display()
        return


    stock_errors = []

    for product_id, quantity in cart_items.items():
        product = products.get(product_id)

        if product is None:
            continue

        available = product["quantity"]

        if quantity > available:
            stock_errors.append(
                f"{product['name']}: {quantity} in cart, "
                f"{available} available"
            )

    if stock_errors:
        messagebox.showwarning(
            "Inventory Changed",
            "Please update your cart before checkout:\n\n"
            + "\n".join(stock_errors),
        )
        refresh_ui_product_display()
        refresh_ui_cart_display()
        return

    subtotal_cents = sum(
        products[product_id]["price_cents"] * quantity
        for product_id, quantity in cart_items.items()
    )

    tax_cents = calculate_tax_cents(subtotal_cents, tax_rate())
    total_cents = subtotal_cents + tax_cents

    payment_window = create_modal_window(
        parent_frame,
        "Payment",
        400,
        300,
        background=BG,
        resizable=(False, False),
    )

    tk.Label(
        payment_window,
        text="Complete Payment",
        font=("Arial", 18, "bold"),
        bg=BG,
        fg="#2c3e50",
    ).pack(pady=(25, 5))

    tk.Label(
        payment_window,
        text=f"Total Due: {format_cents(total_cents)}",
        font=("Arial", 16, "bold"),
        bg=BG,
        fg="#263b35",
    ).pack(pady=(0, 20))

    tk.Label(
        payment_window,
        text="Select Payment Method",
        font=("Arial", 10),
        bg=BG,
        fg="#666666",
    ).pack(pady=(0, 10))

    buttons_frame = tk.Frame(payment_window, bg=BG)
    buttons_frame.pack(fill=tk.X, padx=35)

    def close_payment_window():
        payment_window.destroy()

    def process_payment(payment_method):
        """Validate payment and record the transaction."""
        cash_received_cents = None
        change_cents = None

        if payment_method == "Cash":
            cash_input = simpledialog.askstring(
                "Cash Payment",
                (
                    f"Total due: {format_cents(total_cents)}\n"
                    "Enter cash received:"
                ),
                parent=payment_window,
            )

            if cash_input is None:
                return

            try:
                cash_received_cents = parse_cash(cash_input)
            except ValueError:
                messagebox.showerror(
                    "Invalid Cash Amount",
                    "Enter a valid non-negative amount.",
                    parent=payment_window,
                )
                return

            if cash_received_cents < total_cents:
                messagebox.showerror(
                    "Insufficient Cash",
                    (
                        "Cash received must be at least "
                        f"{format_cents(total_cents)}."
                    ),
                    parent=payment_window,
                )
                return

            change_cents = cash_received_cents - total_cents

        items_to_log = [
            {
                "id": products[product_id]["id"],
                "qty": quantity,
            }
            for product_id, quantity in cart_items.items()
        ]

        try:
            transaction_id = complete_transaction(
                items_to_log,
                from_cents(total_cents),
                payment_method,
                cash_received_cents=cash_received_cents,
            )

        except ValueError as error:
            messagebox.showerror(
                "Checkout Failed",
                str(error),
                parent=payment_window,
            )
            refresh_ui_product_display()
            refresh_ui_cart_display()
            return

        except sqlite3.Error:
            messagebox.showerror(
                "Checkout Failed",
                (
                    "The sale could not be recorded. "
                    "No inventory changes were saved."
                ),
                parent=payment_window,
            )
            return

        receipt_items = [
            {
                "name": products[product_id]["name"],
                "quantity": quantity,
                "total": from_cents(
                    products[product_id]["price_cents"] * quantity
                ),
            }
            for product_id, quantity in cart_items.items()
        ]

        session_cart.clear()
        payment_window.destroy()

        refresh_ui_product_display()
        refresh_ui_cart_display()

        show_receipt_window(
            parent=parent_frame,
            transaction_id=transaction_id,
            timestamp=business_now(),
            items=receipt_items,
            subtotal=from_cents(subtotal_cents),
            tax=from_cents(tax_cents),
            total=from_cents(total_cents),
            payment_method=payment_method,
            cash_received=(
                from_cents(cash_received_cents)
                if cash_received_cents is not None
                else None
            ),
            change=(
                from_cents(change_cents)
                if change_cents is not None
                else None
            ),
        )

    cash_button = tk.Button(
        buttons_frame,
        text="Cash",
        command=lambda: process_payment("Cash"),
        font=("Arial", 11, "bold"),
        bg="#a8e6cf",
        fg="#000000",
        activebackground="#84d4b1",
        relief=tk.FLAT,
        padx=20,
        pady=12,
        cursor="hand2",
    )
    cash_button.pack(
        side=tk.LEFT,
        fill=tk.X,
        expand=True,
        padx=(0, 8),
    )

    card_button = tk.Button(
        buttons_frame,
        text="Card",
        command=lambda: process_payment("Card"),
        font=("Arial", 11, "bold"),
        bg=BUTTON,
        fg=WHITE,
        activebackground=BUTTON_HOVER,
        activeforeground=WHITE,
        relief=tk.FLAT,
        padx=20,
        pady=12,
        cursor="hand2",
    )
    card_button.pack(
        side=tk.LEFT,
        fill=tk.X,
        expand=True,
        padx=(8, 0),
    )

    tk.Button(
        payment_window,
        text="Cancel",
        command=close_payment_window,
        font=("Arial", 9),
        bg="#ecf0f1",
        fg=TEXT,
        activebackground="#d5dbdb",
        relief=tk.FLAT,
        padx=15,
        pady=7,
        cursor="hand2",
    ).pack(pady=(20, 0))

    payment_window.grab_set()
    payment_window.focus_set()


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
    """Open a modal receipt for the completed transaction."""
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

    paper = tk.Frame(
        receipt_window,
        bg=WHITE,
        padx=28,
        pady=22,
    )
    paper.pack(
        fill=tk.BOTH,
        expand=True,
        padx=14,
        pady=14,
    )

    tk.Label(
        paper,
        text="TCCP RECEIPT",
        font=("Arial", 15, "bold"),
        bg=WHITE,
        fg="#263b35",
    ).pack(pady=(0, 5))

    tk.Frame(
        paper,
        bg="#d9ded9",
        height=1,
    ).pack(fill=tk.X, pady=(0, 12))

    metadata = tk.Frame(paper, bg=WHITE)
    metadata.pack(fill=tk.X, pady=(0, 12))

    tk.Label(
        metadata,
        text=f"Transaction #{transaction_id}",
        font=("Arial", 10, "bold"),
        bg=WHITE,
        fg=TEXT,
    ).pack(anchor=tk.W)

    tk.Label(
        metadata,
        text=timestamp.strftime("%B %d, %Y at %I:%M %p").replace(" 0", " "),
        font=("Arial", 9),
        bg=WHITE,
        fg="#737b75",
    ).pack(anchor=tk.W, pady=(3, 0))

    tk.Label(
        paper,
        text="ITEMS",
        font=("Arial", 9, "bold"),
        bg=WHITE,
        fg="#737b75",
    ).pack(anchor=tk.W, pady=(0, 5))

    items_area = tk.Frame(paper, bg=WHITE)
    items_area.pack(fill=tk.BOTH, expand=True, pady=(0, 12))

    items_canvas = tk.Canvas(
        items_area,
        bg=WHITE,
        highlightthickness=0,
    )

    items_scrollbar = tk.Scrollbar(
        items_area,
        orient=tk.VERTICAL,
        command=items_canvas.yview,
    )

    items_canvas.configure(yscrollcommand=items_scrollbar.set)

    items_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    items_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    items_frame = tk.Frame(items_canvas, bg=WHITE)

    items_window = items_canvas.create_window(
        (0, 0),
        window=items_frame,
        anchor="nw",
    )

    items_frame.bind(
        "<Configure>",
        lambda event: items_canvas.configure(
            scrollregion=items_canvas.bbox("all")
        ),
    )

    items_canvas.bind(
        "<Configure>",
        lambda event: items_canvas.itemconfigure(
            items_window,
            width=event.width,
        ),
    )

    for item in items:
        item_row = tk.Frame(items_frame, bg=WHITE)
        item_row.pack(fill=tk.X, pady=4)

        tk.Label(
            item_row,
            text=f"{item['name']} x{item['quantity']}",
            font=("Arial", 10),
            bg=WHITE,
            fg=TEXT,
        ).pack(side=tk.LEFT)

        tk.Label(
            item_row,
            text=f"${item['total']:.2f}",
            font=("Arial", 10),
            bg=WHITE,
            fg=TEXT,
        ).pack(side=tk.RIGHT)

    footer = tk.Frame(paper, bg=WHITE)
    footer.pack(fill=tk.X, side=tk.BOTTOM)

    tk.Frame(
        footer,
        bg="#d9ded9",
        height=1,
    ).pack(fill=tk.X, pady=(0, 8))

    for label, amount, emphasized in (
        ("Subtotal", subtotal, False),
        ("Tax recorded", tax, False),
        ("TOTAL", total, True),
    ):
        row = tk.Frame(footer, bg=WHITE)
        row.pack(fill=tk.X, pady=3)

        font = (
            ("Arial", 11, "bold")
            if emphasized
            else ("Arial", 10)
        )

        foreground = "#263b35" if emphasized else "#666d68"

        tk.Label(
            row,
            text=label,
            font=font,
            bg=WHITE,
            fg=foreground,
        ).pack(side=tk.LEFT)

        tk.Label(
            row,
            text=f"${amount:.2f}",
            font=font,
            bg=WHITE,
            fg=foreground,
        ).pack(side=tk.RIGHT)

    tk.Frame(
        footer,
        bg="#d9ded9",
        height=1,
    ).pack(fill=tk.X, pady=8)

    tk.Label(
        footer,
        text=f"Payment: {payment_method}",
        font=("Arial", 10, "bold"),
        bg=WHITE,
        fg=TEXT,
    ).pack(anchor=tk.W, pady=(0, 4))

    if payment_method == "Cash":
        for label, amount in (
            ("Cash received", cash_received),
            ("Change", change),
        ):
            row = tk.Frame(footer, bg=WHITE)
            row.pack(fill=tk.X, pady=2)

            tk.Label(
                row,
                text=label,
                font=("Arial", 10),
                bg=WHITE,
                fg="#666d68",
            ).pack(side=tk.LEFT)

            tk.Label(
                row,
                text=f"${amount:.2f}",
                font=("Arial", 10),
                bg=WHITE,
                fg="#666d68",
            ).pack(side=tk.RIGHT)

    tk.Label(
        footer,
        text="Thank you for your purchase!",
        font=("Arial", 10, "italic"),
        bg=WHITE,
        fg="#737b75",
    ).pack(pady=(10, 12))

    tk.Button(
        footer,
        text="Done",
        command=receipt_window.destroy,
        bg="#263b35",
        fg=WHITE,
        activebackground="#38574d",
        activeforeground=WHITE,
        relief=tk.FLAT,
        padx=16,
        pady=7,
        cursor="hand2",
    ).pack(fill=tk.X)

    receipt_window.grab_set()
    receipt_window.focus_set()
