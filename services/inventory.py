from contextlib import closing

from database.database import get_db_connection


def find_product_by_id(product_id):
    with closing(get_db_connection()) as conn:
        row = conn.execute(
            "SELECT * FROM products WHERE id = ?",
            (product_id,)
        ).fetchone()

    return dict(row) if row else None


def find_products_by_ids(product_ids):
    if not product_ids:
        return {}

    placeholders = ", ".join("?" for _ in product_ids)

    with closing(get_db_connection()) as conn:
        rows = conn.execute(
            f"SELECT * FROM products WHERE id IN ({placeholders})",
            product_ids
        ).fetchall()

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

    with closing(get_db_connection()) as conn:
        products = conn.execute("SELECT * FROM products").fetchall()

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

    name = input("Enter product name: ").strip()
    if not name:
        print("Product name cannot be empty.")
        return

    price = get_price("Enter product price: $")
    if price is None:
        return

    quantity = get_quantity("Enter product quantity: ")
    if quantity is None:
        return

    with closing(get_db_connection()) as conn:
        with conn:
            cursor = conn.execute(
                "INSERT INTO products (name, price, quantity) VALUES (?, ?, ?)",
                (name, price, quantity)
            )
            product_id = cursor.lastrowid

    print(f"\nProduct added successfully with ID {product_id}.")


def remove_product():
    try:
        product_id = int(input("Enter the product ID to remove: "))
    except ValueError:
        print("Invalid input. Please enter a valid product ID.")
        return

    product = find_product_by_id(product_id)
    if product is None:
        print(f"Product with ID {product_id} not found.")
        return

    with closing(get_db_connection()) as conn:
        with conn:
            conn.execute("DELETE FROM products WHERE id = ?", (product_id,))

    print(f"Product with ID {product_id} has been removed.")


def update_product():
    try:
        product_id = int(input("Enter the product ID to update: "))
    except ValueError:
        print("Invalid input. Please enter a valid product ID.")
        return

    product = find_product_by_id(product_id)
    if product is None:
        print(f"Product with ID {product_id} not found.")
        return

    name = input("Enter new product name (leave blank to keep current): ").strip()
    price_input = input(
        "Enter new product price (leave blank to keep current): "
    ).strip()
    quantity_input = input(
        "Enter new product quantity (leave blank to keep current): "
    ).strip()

    new_name = name or product["name"]
    new_price = product["price"]
    new_quantity = product["quantity"]

    if price_input:
        try:
            new_price = float(price_input)
        except ValueError:
            print("Invalid input. Please enter a valid price.")
            return

        if new_price < 0:
            print("Price cannot be negative. Please enter a valid price.")
            return

    if quantity_input:
        try:
            new_quantity = int(quantity_input)
        except ValueError:
            print("Invalid input. Please enter a valid quantity.")
            return

        if new_quantity < 0:
            print("Quantity cannot be negative. Please enter a valid quantity.")
            return

    with closing(get_db_connection()) as conn:
        with conn:
            conn.execute(
                "UPDATE products SET name = ?, price = ?, quantity = ? "
                "WHERE id = ?",
                (new_name, new_price, new_quantity, product_id)
            )

    print(f"Product with ID {product_id} has been updated.")


def search_product():
    search_term = input("Enter product name or ID to search: ").strip()

    with closing(get_db_connection()) as conn:
        if search_term.isdigit():
            row = conn.execute(
                "SELECT * FROM products WHERE id = ?",
                (int(search_term),)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM products WHERE LOWER(name) = ?",
                (search_term.lower(),)
            ).fetchone()

    if row:
        display_product(dict(row))
        return

    print(f"No product matching '{search_term}' was found.")