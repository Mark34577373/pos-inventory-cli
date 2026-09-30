from contextlib import closing
import math

from database.database import get_db_connection


def find_product_by_id(product_id):
    with closing(get_db_connection()) as conn:
        row = conn.execute(
            "SELECT * FROM products WHERE id = ? AND is_active = 1",
            (product_id,)
        ).fetchone()

    return dict(row) if row else None


def find_products_by_ids(product_ids):
    if not product_ids:
        return {}

    placeholders = ", ".join("?" for _ in product_ids)

    with closing(get_db_connection()) as conn:
        rows = conn.execute(
            f"SELECT * FROM products WHERE id IN ({placeholders}) "
            "AND is_active = 1",
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


def create_product(name, price, quantity):
    """Validate and persist one product, returning its new database ID."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Product name cannot be empty.")

    try:
        price = float(price)
    except (TypeError, ValueError) as error:
        raise ValueError("Price must be a valid number.") from error
    if not math.isfinite(price) or price < 0:
        raise ValueError("Price must be a finite non-negative number.")

    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise ValueError("Quantity must be a whole number.")
    if quantity < 0:
        raise ValueError("Quantity cannot be negative.")

    with closing(get_db_connection()) as conn:
        with conn:
            cursor = conn.execute(
                "INSERT INTO products (name, price, quantity) VALUES (?, ?, ?)",
                (name.strip(), price, quantity),
            )
            return cursor.lastrowid


def set_product_quantity(product_id, quantity):
    """Set a product's stock quantity and return its ID."""
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise ValueError("Quantity must be a whole number.")
    if quantity < 0:
        raise ValueError("Quantity cannot be negative.")

    with closing(get_db_connection()) as conn:
        with conn:
            cursor = conn.execute(
                "UPDATE products SET quantity = ? "
                "WHERE id = ? AND is_active = 1",
                (quantity, product_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Product no longer exists in inventory.")

    return product_id


def deactivate_product(product_id):
    """Remove a product from active inventory without deleting sales history."""
    with closing(get_db_connection()) as conn:
        with conn:
            cursor = conn.execute(
                "UPDATE products SET is_active = 0 "
                "WHERE id = ? AND is_active = 1",
                (product_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError("Product no longer exists in active inventory.")

    return product_id


def remove_product_by_name(name):
    """Deactivate one active product by exact, case-insensitive name."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Product name cannot be empty.")

    with closing(get_db_connection()) as conn:
        matches = conn.execute(
            "SELECT id FROM products "
            "WHERE is_active = 1 AND name = ? COLLATE NOCASE "
            "ORDER BY id",
            (name.strip(),),
        ).fetchall()

    if not matches:
        raise ValueError(f'No active product named "{name.strip()}" was found.')
    if len(matches) > 1:
        raise ValueError(
            f'More than one active product is named "{name.strip()}". '
            "Rename products so the name is unique before removing one."
        )

    return deactivate_product(matches[0]["id"])


def remove_product_by_id(product_id, name):
    """Deactivate a product only when its ID and name both match."""
    if isinstance(product_id, bool) or not isinstance(product_id, int):
        raise ValueError("Product ID must be a whole number.")
    if product_id <= 0:
        raise ValueError("Product ID must be greater than zero.")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Product name cannot be empty.")

    product = find_product_by_id(product_id)
    if product is None:
        raise ValueError(f"No active product with ID {product_id} was found.")
    if product["name"].casefold() != name.strip().casefold():
        raise ValueError(f"Product name does not match ID {product_id}.")

    return deactivate_product(product_id)


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
        products = conn.execute(
            "SELECT * FROM products WHERE is_active = 1"
        ).fetchall()

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

    try:
        product_id = create_product(name, price, quantity)
    except ValueError as error:
        print(error)
        return

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

    try:
        deactivate_product(product_id)
    except ValueError as error:
        print(error)
        return

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
                "WHERE id = ? AND is_active = 1",
                (new_name, new_price, new_quantity, product_id)
            )

    print(f"Product with ID {product_id} has been updated.")


def search_product():
    search_term = input("Enter product name or ID to search: ").strip()

    with closing(get_db_connection()) as conn:
        if search_term.isdigit():
            row = conn.execute(
                "SELECT * FROM products WHERE id = ? AND is_active = 1",
                (int(search_term),)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM products "
                "WHERE LOWER(name) = ? AND is_active = 1",
                (search_term.lower(),)
            ).fetchone()

    if row:
        display_product(dict(row))
        return

    print(f"No product matching '{search_term}' was found.")