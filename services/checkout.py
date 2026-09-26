# services/checkout.py
import sqlite3

from models.cart import Cart
from services.inventory import find_product_by_id, find_products_by_ids
from services.transaction_service import complete_transaction

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

    current_cart_qty = session_cart.get_items().get(product_id, 0)
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

def remove_from_cart():
    print("\n===================================")
    print("        REMOVE FROM CART")
    print("===================================")
    if session_cart.is_empty():
        print("Your cart is already empty.")
        return
        
    try:
        product_id = int(input("Enter product ID to remove from cart: "))
    except ValueError:
        print("Invalid input. Please enter a valid product ID.")
        return
        
    session_cart.remove_item(product_id)

def change_cart_quantity():
    print("\n===================================")
    print("        UPDATE CART QUANTITY")
    print("===================================")
    if session_cart.is_empty():
        print("Your cart is empty.")
        return
        
    try:
        product_id = int(input("Enter product ID to update: "))
        product = find_product_by_id(product_id)
        if not product:
            print("Product not found.")
            return
            
        new_qty = int(input(f"Enter new quantity for '{product['name']}': "))
    except ValueError:
        print("Invalid input. Quantity must be an integer.")
        return

    if new_qty > product["quantity"]:
        print(f"Not enough stock. Max available: {product['quantity']}")
        return
        
    session_cart.update_quantity(product_id, new_qty)

def view_cart(products=None):
    print("\n-------------------------")
    print("        YOUR CART")
    print("-------------------------")
    if session_cart.is_empty():
        print("Your cart is empty.")
        return 0

    if products is None:
        products = find_products_by_ids(session_cart.get_items())

    total = 0.0
    for product_id, qty in session_cart.get_items().items():
        product = products.get(product_id)
        if product is None:
            print(f"Product with ID {product_id} is no longer available.")
            return 0
        subtotal = product["price"] * qty
        total += subtotal
        print(f"{product['name']} x{qty} - ${subtotal:.2f}")
    
    print("-------------------------")
    print(f"Total: ${total:.2f}")
    return total

def checkout():
    """Processes transaction and returns True if successful, False if failed."""
    products = find_products_by_ids(session_cart.get_items())
    total = view_cart(products)
    if total == 0:
        return False

    confirm = input("\nProceed to checkout? (y/n): ").lower()
    if confirm != 'y':
        print("Checkout canceled.")
        return False

    print("\nPayment method:")
    print("1. Cash")
    print("2. Card")
    payment_choice = input("Choose payment method: ").strip()

    payment_methods = {"1": "Cash", "2": "Card"}
    payment_method = payment_methods.get(payment_choice)
    if payment_method is None:
        print("Invalid payment method. Choose 1 for Cash or 2 for Card.")
        return False

    #Handle Cash Change Calculations
    cash_received = None
    change = None

    if payment_method == "Cash":
        while True:
            try:
                cash_received = float(input(f"Enter cash received (minimum ${total:.2f}): $"))
            except ValueError:
                print("Invalid amount. Please enter a valid number.")
                continue

            if cash_received < total:
                print(f"Insufficient cash. You need at least ${total:.2f}.")
                continue

            change = cash_received - total
            break

    items_to_log = []
    for product_id, qty in session_cart.get_items().items():
        product = products[product_id]
        items_to_log.append({
            "id": product["id"],
            "qty": qty,
            "price": product["price"]
        })

    try:
        tx_id = complete_transaction(items_to_log, total, payment_method)
    except ValueError as error:
        print(f"Checkout failed: {error}. No changes were saved.")
        return False
    except sqlite3.Error:
        print("Checkout failed because the sale could not be recorded. No changes were saved.")
        return False

    print("\n===================================")
    print("        RECEIPT GENERATED")
    print("===================================")
    print(f"Transaction ID: #{tx_id}")
    print(f"Payment Method: {payment_method}")

    # Day 4 Tweak: Render calculated change breakdown on physical receipt
    if payment_method == "Cash":
        print(f"Cash Received: ${cash_received:.2f}")
        print(f"Change: ${change:.2f}")

    print("Thank you for your purchase!")
    
    session_cart.clear()
    return True


def cancel_sale():
    """Clears the active cart session if confirmed by the user. Returns True if canceled."""
    if session_cart.is_empty():
        print("\nNo active sale session to cancel.")
        return False
        
    confirm = input("\nAre you sure you want to cancel this entire sale? (y/n): ").lower()
    if confirm == 'y':
        session_cart.clear()
        print("Sale canceled successfully. Cart cleared. Inventory unchanged.")
        return True
    else:
        print("Resuming current sale session.")
        return False

def start_sale():
    """Handles the nested interactive loop for an active sales session."""
    while True:
        print("\n===================================")
        print("           NEW SALE")
        print("===================================")
        print("1. Add Item")
        print("2. View Cart")
        print("3. Remove Item")
        print("4. Change Quantity")
        print("5. Checkout")
        print("6. Cancel Sale")
        print("7. Exit Sale")
        print("-----------------------------------")
        
        choice = input("Choose an option: ")

        if choice == "1":
            add_to_cart()
        elif choice == "2":
            view_cart()
        elif choice == "3":
            remove_from_cart()
        elif choice == "4":
            change_cart_quantity()
        elif choice == "5":
            if session_cart.is_empty():
                print("\nYour cart is empty. Cannot checkout.")
            else:
                success = checkout()
                if success:
                    break  # Safe transaction complete: exit sale menu loop
        elif choice == "6":
            if session_cart.is_empty():
                print("\nNo active sale to cancel.")
            else:
                was_canceled = cancel_sale()
                if was_canceled:
                    break  # Sale explicitly canceled: exit sale menu loop
        elif choice == "7":
            if session_cart.is_empty():
                print("\nExiting sale.")
                break
            else:
                print("\nYou have items in your cart.")
                print("Please checkout or cancel the sale first.")
        else:
            print("\nInvalid option. Please choose 1-7.")
