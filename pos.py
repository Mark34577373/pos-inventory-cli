# pos.py
import Inventory as inventory

cart = {}  # Format: { product_id: quantity }

def add_to_cart():
    print("\n===================================")
    print("           ADD TO CART")
    print("===================================")
    try:
        product_id = int(input("Enter product ID to add to cart: "))
    except ValueError:
        print("Invalid input. Please enter a valid product ID.")
        return

    product = inventory.find_product_by_id(product_id)
    if not product:
        print(f"Product with ID {product_id} not found.")
        return

    current_cart_qty = cart.get(product_id, 0)
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

    cart[product_id] = current_cart_qty + qty_to_add
    print(f"Added {qty_to_add}x '{product['name']}' to your cart.")

def view_cart():
    print("\n-------------------------")
    print("        YOUR CART")
    print("-------------------------")
    if not cart:
        print("Your cart is empty.")
        return 0

    total = 0.0
    for product_id, qty in cart.items():
        product = inventory.find_product_by_id(product_id)
        subtotal = product["price"] * qty
        total += subtotal
        print(f"{product['name']} x{qty} - ${subtotal:.2f}")
    
    print("-------------------------")
    print(f"Total: ${total:.2f}")
    return total

def checkout():
    global cart
    total = view_cart()
    if total == 0:
        return

    confirm = input("\nProceed to checkout? (y/n): ").lower()
    if confirm != 'y':
        print("Checkout canceled.")
        return

    # Safely loops through and updates inventory quantities directly
    for product_id, qty in cart.items():
        product = inventory.find_product_by_id(product_id)
        product["quantity"] -= qty

    print("\n===================================")
    print("        RECEIPT GENERATED")
    print("===================================")
    print("Thank you for your purchase!")
    
    cart.clear()
