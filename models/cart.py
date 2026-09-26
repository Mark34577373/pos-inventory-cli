# models/cart.py
class Cart:
    def __init__(self):
        self.items = {}  # Format: { product_id: quantity }

    def add_item(self, product_id, quantity):
        self.items[product_id] = self.items.get(product_id, 0) + quantity

    def remove_item(self, product_id):
        """Safely removes a product completely from the cart if it exists."""
        if product_id in self.items:
            del self.items[product_id]
            print(f"Product ID {product_id} removed from cart.")
        else:
            print(f"Product ID {product_id} was not found in your cart.")

    def update_quantity(self, product_id, quantity):
        """Updates the quantity of a product in the cart. Removes it if quantity is 0 or less."""
        if product_id not in self.items:
            print(f"Product ID {product_id} is not in your cart.")
            return

        if quantity <= 0:
            del self.items[product_id]
            print(f"Product ID {product_id} removed from cart (quantity set to 0).")
        else:
            self.items[product_id] = quantity
            print(f"Updated Product ID {product_id} quantity to {quantity} in cart.")

    def is_empty(self):
        """Returns True if the cart has no items, otherwise False."""
        return not self.items

    def get_items(self):
        """Returns the dictionary of items (product_id: quantity)."""
        return self.items

    def get_total(self, find_product_by_id_func):
        """Calculates the total price of all items in the cart."""
        total = 0.0
        for product_id, qty in self.items.items():
            product = find_product_by_id_func(product_id)
            if product:
                total += product["price"] * qty
        return total

    def clear(self):
        self.items.clear()
