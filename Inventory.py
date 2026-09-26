# inventory.py

# 1. Shared Global Data
products = [
    {"id": 1, "name": "Mouse", "price": 10.99, "quantity": 5},
    {"id": 2, "name": "Keyboard", "price": 29.99, "quantity": 0}
]

next_product_id = 3  # Hardcoded setup helper for your starting data

# 2. Helper Functions
def find_product_by_id(product_id):
    for product in products:
        if product["id"] == product_id:
            return product
    return None

def get_price(prompt):
    try:
        price = float(input(prompt))
    except ValueError:
        print("Invalid input. Please enter a valid price.")
        return None
    if price < 0:
        print("Price cannot be negative. Please enter a valid price.")
        return None
    return price

def get_quantity(prompt):
    try:
        quantity = int(input(prompt))
    except ValueError:
        print("Invalid input. Please enter a valid quantity.")
        return None
    if quantity < 0:
        print("Quantity cannot be negative. Please enter a valid quantity.")
        return None
    return quantity

def display_product(product):
    print(f"\nProduct ID: {product['id']}")
    print(f"Product: {product['name']}")
    print(f"Price: ${product['price']:.2f}")
    print(f"Quantity: {product['quantity']}")

# 3. Core Action Functions
def view_inventory():
    print("-------------------------")
    print("        INVENTORY")
    print("-------------------------")
    if not products:
        print("No products in inventory.")
        return
    
    for product in products:
        display_product(product)
        if product["quantity"] <= 0:
            print("Status: is out of stock.")
        elif product["quantity"] <= 5:
            print("Status: is low in stock.")
        else:
            print("Status: is in stock.")

def add_product():
    print("\n===================================")
    print("            ADD PRODUCT")
    print("===================================")
    global next_product_id
    name = input("Enter product name: ")
    price = get_price("Enter product price:$ ")
    if price is None:
        return
    quantity = get_quantity("Enter product quantity: ")
    if quantity is None:
        return

    product = {
        "id": next_product_id,
        "name": name,
        "price": price,
        "quantity": quantity
    }
    products.append(product)
    next_product_id += 1
    print(f"\nProduct added successfully with ID {product['id']}.")

def remove_product():
    try:
        product_id = int(input("Enter the product ID to remove: "))
    except ValueError:
        print("Invalid input. Please enter a valid product ID.")
        return

    product = find_product_by_id(product_id)
    if product is not None:
        products.remove(product)
        print(f"Product with ID {product_id} has been removed.")
        return
    print(f"Product with ID {product_id} not found.")

def update_product():
    try:
        product_id = int(input("Enter the product ID to update: "))
    except ValueError:
        print("Invalid input. Please enter a valid product ID.")
        return

    product = find_product_by_id(product_id)
    if product is not None:
        name = input("Enter new product name (leave blank to keep current): ")
        price_input = input("Enter new product price (leave blank to keep current): ")
        quantity_input = input("Enter new product quantity (leave blank to keep current): ")

        if name:
            product["name"] = name
        if price_input.strip():
            try:
                price = float(price_input)
            except ValueError:
                print("Invalid input. Please enter a valid price.")
                return
            if price < 0:
                print("Price cannot be negative. Please enter a valid price.")
                return
            product["price"] = price
        if quantity_input.strip():
            try:
                quantity = int(quantity_input)
            except ValueError:
                print("Invalid input. Please enter a valid quantity.")
                return
            if quantity < 0:
                print("Quantity cannot be negative. Please enter a valid quantity.")
                return
            product["quantity"] = quantity

        print(f"Product with ID {product_id} has been updated.")
        return
    print(f"Product with ID {product_id} not found.")

def search_product():
    search_term = input("Enter product name or ID to search: ")
    if search_term.isdigit():
        product = find_product_by_id(int(search_term))
    else:
        product = next(
            (p for p in products if p["name"].lower() == search_term.lower()),
            None
        )

    if product is not None:
        display_product(product)
        return
    print(f"No product matching '{search_term}' was found.")
