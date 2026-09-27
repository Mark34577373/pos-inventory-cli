# ui/inventory_view.py
import tkinter as tk
from database.database import get_db_connection

def create_inventory_view(parent_frame):
    """Phase 6: Reads live data from SQLite and renders them as grid cards."""
    # 1. Heading Title
    title = tk.Label(parent_frame, text="Inventory Management", font=("Arial", 22, "bold"), bg="#f8f9fa", fg="#333333")
    title.pack(anchor=tk.W, pady=(0, 20))

    # 2. Grid Container for Cards
    grid_frame = tk.Frame(parent_frame, bg="#f8f9fa")
    grid_frame.pack(fill=tk.BOTH, expand=True)

    # 3. Pull Live Products from SQLite
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM products")
    products = cursor.fetchall()
    conn.close()

    if not products:
        no_items = tk.Label(grid_frame, text="No products found in database.", font=("Arial", 12), bg="#f8f9fa", fg="#7f8c8d")
        no_items.pack(anchor=tk.W)
        return

    # 4. Render product lists as structural cards in a grid row layout
    row, col = 0, 0
    for product_row in products:
        product = dict(product_row)
        
        # Determine inventory context states
        is_out = product["quantity"] <= 0
        stock_text = "OUT OF STOCK" if is_out else f"Stock: {product['quantity']}"
        stock_color = "#e74c3c" if is_out else ("#e67e22" if product["quantity"] <= 5 else "#27ae60")

        # White base background card shape
        card = tk.Frame(grid_frame, bg="white", highlightbackground="#e0e0e0", highlightthickness=1, padx=15, pady=15, width=180, height=140)
        card.grid(row=row, column=col, padx=10, pady=10)
        card.grid_propagate(False)

        # Inner Card Elements
        name_lbl = tk.Label(card, text=product["name"], font=("Arial", 12, "bold"), bg="white", fg="#2c3e50")
        name_lbl.pack(anchor=tk.W, pady=(0, 2))

        price_lbl = tk.Label(card, text=f"${product['price']:.2f}", font=("Arial", 11), bg="white", fg="#7f8c8d")
        price_lbl.pack(anchor=tk.W, pady=(0, 10))

        status_lbl = tk.Label(card, text=stock_text, font=("Arial", 10, "bold"), bg="white", fg=stock_color)
        status_lbl.pack(anchor=tk.W, pady=(0, 10))

        # Mac-Friendly Label Button for [Add Stock]
        add_btn = tk.Label(
            card, text="[ Add Stock ]", font=("Arial", 10, "bold"), 
            bg="#34495e", fg="white", padx=10, pady=4, cursor="hand2"
        )
        add_btn.pack(fill=tk.X, side=tk.BOTTOM)
        
        # Hover effect bindings
        add_btn.bind("<Enter>", lambda e, b=add_btn: b.config(bg="#1abc9c"))
        add_btn.bind("<Leave>", lambda e, b=add_btn: b.config(bg="#34495e"))

        # Advance layout positioning grid indexes
        col += 1
        if col > 3:  # Max 4 columns wide row wrapping balances
            col = 0
            row += 1
