# ui/sale_view.py
import tkinter as tk
from database.database import get_db_connection
from services.checkout import session_cart, add_to_cart, view_cart

def create_sale_view(parent_frame):
    """Phase 7 Layout Fix: Builds a reliable side-by-side structure 
    that guarantees totals and product listings stay fully visible on Mac.
    """
    # 1. Main Heading
    title = tk.Label(parent_frame, text="New Sale", font=("Arial", 22, "bold"), bg="#f8f9fa", fg="#333333")
    title.pack(anchor=tk.W, pady=(0, 20))

    # 2. Main Split Container (Holds Left and Right Content Panel Boxes)
    split_container = tk.Frame(parent_frame, bg="#f8f9fa")
    split_container.pack(fill=tk.BOTH, expand=True)

    # =========================================================================
    # LEFT PANEL: Products Catalog Viewport
    # =========================================================================
    products_panel = tk.Frame(split_container, bg="#f8f9fa")
    products_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 20))
    
    prod_title = tk.Label(products_panel, text="Products", font=("Arial", 14, "bold"), bg="#f8f9fa", fg="#2c3e50")
    prod_title.pack(anchor=tk.W, pady=(0, 10))

    prod_grid = tk.Frame(products_panel, bg="#f8f9fa")
    prod_grid.pack(fill=tk.BOTH, expand=True)

    # Fetch and render items from SQLite onto Left Grid Panel Row Column balances
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM products")
    products = cursor.fetchall()
    conn.close()

    g_row, g_col = 0, 0
    for product_row in products:
        product = dict(product_row)
        
        card = tk.Frame(prod_grid, bg="white", highlightbackground="#e0e0e0", highlightthickness=1, padx=12, pady=12, width=150, height=120)
        card.grid(row=g_row, column=g_col, padx=8, pady=8)
        card.grid_propagate(False)

        n_lbl = tk.Label(card, text=product["name"], font=("Arial", 11, "bold"), bg="white", fg="#2c3e50")
        n_lbl.pack(anchor=tk.W)
        p_lbl = tk.Label(card, text=f"${product['price']:.2f}", font=("Arial", 10), bg="white", fg="#7f8c8d")
        p_lbl.pack(anchor=tk.W, pady=(0, 15))

        btn = tk.Label(
            card, text="[ Add ]", font=("Arial", 9, "bold"),
            bg="#34495e", fg="white", padx=8, pady=3, cursor="hand2"
        )
        btn.pack(side=tk.BOTTOM, fill=tk.X)
        btn.bind("<Enter>", lambda e, b=btn: b.config(bg="#3498db"))
        btn.bind("<Leave>", lambda e, b=btn: b.config(bg="#34495e"))

        g_col += 1
        if g_col > 2:
            g_col = 0
            g_row += 1

    # =========================================================================
    # RIGHT PANEL: Adaptive Current Cart Checkout Dashboard Panel Container
    # =========================================================================
    cart_panel = tk.Frame(split_container, bg="white", width=320, highlightbackground="#e0e0e0", highlightthickness=1, padx=20, pady=20)
    cart_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=False)
    cart_panel.pack_propagate(False)

    cart_title = tk.Label(cart_panel, text="Current Cart", font=("Arial", 14, "bold"), bg="white", fg="#2c3e50")
    cart_title.pack(anchor=tk.W, pady=(0, 15))

    # =========================================================================
    # Cart Totals Sticky Footer Frame Container Component (Forced to Bottom Side)
    # =========================================================================
    totals_frame = tk.Frame(cart_panel, bg="white")
    totals_frame.pack(fill=tk.X, side=tk.BOTTOM)

    # Checkout Button Element Link
    ch_btn = tk.Label(
        totals_frame, text="[ CHECKOUT ]", font=("Arial", 11, "bold"),
        bg="#1abc9c", fg="white", pady=12, cursor="hand2", anchor=tk.CENTER
    )
    ch_btn.pack(fill=tk.X, side=tk.BOTTOM)
    ch_btn.bind("<Enter>", lambda e: ch_btn.config(bg="#16a085"))
    ch_btn.bind("<Leave>", lambda e: ch_btn.config(bg="#1abc9c"))

    # inner numerical grid layout module container
    totals_grid = tk.Frame(totals_frame, bg="white")
    totals_grid.pack(fill=tk.X, side=tk.BOTTOM, pady=(0, 12))
    
    totals_grid.columnconfigure(0, weight=1)
    totals_grid.columnconfigure(1, weight=1)

    # Row 1: Subtotal
    tk.Label(totals_grid, text="Subtotal", font=("Arial", 11), bg="white", fg="#666666").grid(row=0, column=0, sticky=tk.W, pady=2)
    tk.Label(totals_grid, text="$51.97", font=("Arial", 11), bg="white", fg="#666666").grid(row=0, column=1, sticky=tk.E, pady=2)
    
    # Row 2: Tax
    tk.Label(totals_grid, text="Tax", font=("Arial", 11), bg="white", fg="#666666").grid(row=1, column=0, sticky=tk.W, pady=2)
    tk.Label(totals_grid, text="$4.29", font=("Arial", 11), bg="white", fg="#666666").grid(row=1, column=1, sticky=tk.E, pady=2)
    
    # Row 3: Total (Forced grid alignment)
    tk.Label(totals_grid, text="Total", font=("Arial", 11, "bold"), bg="white", fg="#333333").grid(row=2, column=0, sticky=tk.W, pady=(2, 0))
    tk.Label(totals_grid, text="$56.26", font=("Arial", 11, "bold"), bg="white", fg="#333333").grid(row=2, column=1, sticky=tk.E, pady=(2, 0))

    divider = tk.Frame(totals_frame, bg="#e0e0e0", height=1)
    divider.pack(fill=tk.X, side=tk.BOTTOM, pady=(0, 12))

    # =========================================================================
    # Inner Active Item Listing Box Area (Takes remaining top middle space)
    # =========================================================================
    cart_items_box = tk.Frame(cart_panel, bg="white")
    cart_items_box.pack(fill=tk.BOTH, expand=True, side=tk.TOP)
    
    # Render layout items with regular Arial for a clean look matching numbers grid
    i1 = tk.Frame(cart_items_box, bg="white")
    i1.pack(fill=tk.X, pady=4)
    tk.Label(i1, text="Mouse x2", font=("Arial", 11), bg="white", fg="#333333").pack(side=tk.LEFT)
    tk.Label(i1, text="$21.98", font=("Arial", 11), bg="white", fg="#333333").pack(side=tk.RIGHT)

    i2 = tk.Frame(cart_items_box, bg="white")
    i2.pack(fill=tk.X, pady=4)
    tk.Label(i2, text="Keyboard x1", font=("Arial", 11), bg="white", fg="#333333").pack(side=tk.LEFT)
    tk.Label(i2, text="$29.99", font=("Arial", 11), bg="white", fg="#333333").pack(side=tk.RIGHT)
