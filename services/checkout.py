# services/checkout.py
from models.cart import Cart
from services.inventory import find_product_by_id
from services.transaction_service import log_transaction
from database.database import get_db_connection

# Initialize a clean session cart instance
session_cart = Cart()

def add_to_cart():
    print("\n===================================")
    print("           ADD TO CART")
    print("===================================")
    try:
        product_id = int(input("Enter product ID to add to cart: "))
    except ValueError:
        print("Invalid input. Please enter a valid product ID.")
        return

    product = find_product_by_id(product_id)
    if not product:
        print(f"Product with ID {product_id} not found.")
        return

    current_cart_qty = session_cart.items.get(product_id, 0)
    if product["quantity"] <= current_cart_qty:
        print(f"Cannot add. Only {product['quantity']} available (already have {current_cart_qty} in cart).")
        return

    try:
        qty_to_add = int(input(f"Enter quantity of '{product['name']}' to add: "))
    except ValueError:
        print("Invalid quantity.")
        return

    if qty_to_add <= 0:
        print("Quantity must be greater than zero.")
        return

    if current_cart_qty + qty_to_add > product["quantity"]:
        print(f"Not enough stock. Total available: {product['quantity']}")
        return

    session_cart.add_item(product_id, qty_to_add)
    print(f"Added {qty_to_add}x '{product['name']}' to your cart.")

def view_cart():
    print("\n-------------------------")
    print("        YOUR CART")
    print("-------------------------")
    if not session_cart.items:
        print("Your cart is empty.")
        return 0

    total = 0.0
    for product_id, qty in session_cart.items.items():
        product = find_product_by_id(product_id)
        subtotal = product["price"] * qty
        total += subtotal
        print(f"{product['name']} x{qty} - ${subtotal:.2f}")
    
    print("-------------------------")
    print(f"Total: ${total:.2f}")
    return total

def checkout():
    total = view_cart()
    if total == 0:
        return

    confirm = input("\nProceed to checkout? (y/n): ").lower()
    if confirm != 'y':
        print("Checkout canceled.")
        return

    # 1. Build details array & lower stock in SQLite
    items_to_log = []
    conn = get_db_connection()
    cursor = conn.cursor()
    
    for product_id, qty in session_cart.items.items():
        product = find_product_by_id(product_id)
        items_to_log.append({
            "id": product["id"],
            "qty": qty,
            "price": product["price"]
        })
        
        # Deduct items from SQLite stock quantities directly
        new_qty = product["quantity"] - qty
        cursor.execute("UPDATE products SET quantity = ? WHERE id = ?", (new_qty, product_id))

    conn.commit()
    conn.close()

    # 2. Push details into database transaction logging tables
    tx_id = log_transaction(items_to_log, total)

    print("\n===================================")
    print("        RECEIPT GENERATED")
    print("===================================")
    print(f"Transaction ID: #{tx_id}")
    print("Thank you for your purchase!")
    
    session_cart.clear()
