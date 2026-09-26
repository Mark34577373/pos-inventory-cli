# services/inventory.py
from database.database import get_db_connection

def find_product_by_id(product_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def find_products_by_ids(product_ids):
    """Fetch several products with one query and one database connection."""
    product_ids = list(product_ids)
    if not product_ids:
        return {}

    placeholders = ", ".join("?" for _ in product_ids)
    conn = get_db_connection()
    try:
        rows = conn.execute(
            f"SELECT * FROM products WHERE id IN ({placeholders})",
            product_ids
        ).fetchall()
    finally:
        conn.close()

    return {row["id"]: dict(row) for row in rows}

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

def view_inventory():
    print("-------------------------")
    print("        INVENTORY")
    print("-------------------------")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM products")
    products = cursor.fetchall()
    conn.close()

    if not products:
        print("No products in inventory.")
        return
    
    for row in products:
        product = dict(row)
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
    name = input("Enter product name: ")
    price = get_price("Enter product price:$ ")
    if price is None:
        return
    quantity = get_quantity("Enter product quantity: ")
    if quantity is None:
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO products (name, price, quantity) VALUES (?, ?, ?)",
        (name, price, quantity)
    )
    product_id = cursor.lastrowid
    conn.commit()
    conn.close()
    print(f"\nProduct added successfully with ID {product_id}.")

def remove_product():
    try:
        product_id = int(input("Enter the product ID to remove: "))
    except ValueError:
        print("Invalid input. Please enter a valid product ID.")
        return

    product = find_product_by_id(product_id)
    if product is not None:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
        conn.commit()
        conn.close()
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

        new_name = name if name else product["name"]
        
        new_price = product["price"]
        if price_input.strip():
            try:
                price = float(price_input)
            except ValueError:
                print("Invalid input. Please enter a valid price.")
                return
            if price < 0:
                print("Price cannot be negative. Please enter a valid price.")
                return
            new_price = price

        new_quantity = product["quantity"]
        if quantity_input.strip():
            try:
                quantity = int(quantity_input)
            except ValueError:
                print("Invalid input. Please enter a valid quantity.")
                return
            if quantity < 0:
                print("Quantity cannot be negative. Please enter a valid quantity.")
                return
            new_quantity = quantity

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE products SET name = ?, price = ?, quantity = ? WHERE id = ?",
            (new_name, new_price, new_quantity, product_id)
        )
        conn.commit()
        conn.close()
        print(f"Product with ID {product_id} has been updated.")
        return

    print(f"Product with ID {product_id} not found.")

def search_product():
    search_term = input("Enter product name or ID to search: ")
    conn = get_db_connection()
    cursor = conn.cursor()
    if search_term.isdigit():
        cursor.execute("SELECT * FROM products WHERE id = ?", (int(search_term),))
    else:
        cursor.execute("SELECT * FROM products WHERE LOWER(name) = ?", (search_term.lower(),))
    
    row = cursor.fetchone()
    conn.close()

    if row:
        display_product(dict(row))
        return
    print(f"No product matching '{search_term}' was found.")
