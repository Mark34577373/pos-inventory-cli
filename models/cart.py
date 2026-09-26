# models/cart.py
class Cart:
    def __init__(self):
        self.items = {}  # Format: { product_id: quantity }

    def add_item(self, product_id, quantity):
        self.items[product_id] = self.items.get(product_id, 0) + quantity

    def clear(self):
        self.items.clear()
