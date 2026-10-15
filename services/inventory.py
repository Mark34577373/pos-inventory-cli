from contextlib import closing
from decimal import Decimal, InvalidOperation

from database.database import get_db_connection
from services.money import from_cents, to_cents


def _product_from_row(row):
    product = dict(row)
    product["price_cents"] = int(product["price_cents"])
    product["price"] = from_cents(product["price_cents"])
    return product


def list_active_products():
    """Return active products with exact cent and Decimal price values."""
    with closing(get_db_connection()) as conn:
        rows = conn.execute(
            "SELECT * FROM products WHERE is_active = 1 ORDER BY id"
        ).fetchall()

    return [_product_from_row(row) for row in rows]


def search_active_products(search_term):
    """Return active products matching an ID or partial product name."""
    if not isinstance(search_term, str):
        return []

    search_term = search_term.strip()

    if not search_term:
        return list_active_products()

    if search_term.isdecimal() and (len(search_term) > 19 or int(search_term) > 9223372036854775807):
        return []

    with closing(get_db_connection()) as conn:
        if search_term.isdecimal():
            rows = conn.execute(
                "SELECT * FROM products "
                "WHERE id = ? AND is_active = 1 "
                "ORDER BY id",
                (int(search_term),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM products "
                "WHERE name LIKE ? COLLATE NOCASE "
                "AND is_active = 1 "
                "ORDER BY id",
                (f"%{search_term}%",),
            ).fetchall()

    return [_product_from_row(row) for row in rows]


def find_product_by_id(product_id):
    if not isinstance(product_id, int) or not 0 < product_id <= 9223372036854775807:
        return None
    with closing(get_db_connection()) as conn:
        row = conn.execute(
            "SELECT * FROM products WHERE id = ? AND is_active = 1",
            (product_id,),
        ).fetchone()

    return _product_from_row(row) if row else None


def find_products_by_ids(product_ids):
    product_ids = list(product_ids)
    if not product_ids:
        return {}

    placeholders = ", ".join("?" for _ in product_ids)

    with closing(get_db_connection()) as conn:
        rows = conn.execute(
            f"SELECT * FROM products WHERE id IN ({placeholders}) "
            "AND is_active = 1",
            product_ids,
        ).fetchall()

    return {
        row["id"]: _product_from_row(row)
        for row in rows
    }


def get_price(prompt):
    try:
        price = Decimal(input(prompt))
    except (ValueError, InvalidOperation):
        print("Invalid input. Please enter a valid price.")
        return None

    if not price.is_finite() or not Decimal("0") < price <= Decimal("1000000"):
        print("Price cannot be negative. Please enter a valid price.")
        return None

    return price


def get_quantity(prompt):
    try:
        quantity = int(input(prompt))
    except ValueError:
        print("Invalid input. Please enter a valid quantity.")
        return None

    if not 0 <= quantity <= 1_000_000:
        print("Quantity cannot be negative. Please enter a valid quantity.")
        return None

    return quantity


def create_product(name, price, quantity):
    """Validate and persist one product, returning its new database ID."""
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError("Product name cannot be empty.")

    try:
        price_cents = to_cents(price)
    except ValueError as error:
        raise ValueError(
            "Price must be a valid finite number."
        ) from error

    if not 0 < price_cents <= 100_000_000:
        raise ValueError("Price must be greater than $0.")

    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise ValueError("Quantity must be a whole number.")

    if not 0 <= quantity <= 1_000_000:
        raise ValueError("Quantity cannot be negative.")

    with closing(get_db_connection()) as conn:
        with conn:
            cursor = conn.execute(
                "INSERT INTO products "
                "(name, price, price_cents, quantity) "
                "VALUES (?, ?, ?, ?)",
                (
                    name.strip(),
                    price_cents / 100,
                    price_cents,
                    quantity,
                ),
            )

            return cursor.lastrowid


def update_product_details(product_id, name, price):
    """Update an active product's name and exact cent-denominated price."""
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError("Product name cannot be empty.")

    try:
        price_cents = to_cents(price)
    except ValueError as error:
        raise ValueError(
            "Price must be a valid finite number."
        ) from error

    if not 0 < price_cents <= 100_000_000:
        raise ValueError("Price must be greater than $0.")

    with closing(get_db_connection()) as conn:
        with conn:
            cursor = conn.execute(
                "UPDATE products "
                "SET name = ?, price = ?, price_cents = ? "
                "WHERE id = ? AND is_active = 1",
                (
                    name.strip(),
                    price_cents / 100,
                    price_cents,
                    product_id,
                ),
            )

            if cursor.rowcount != 1:
                raise ValueError(
                    "Product no longer exists in inventory."
                )


def set_product_quantity(product_id, quantity):
    """Set a product's stock quantity and return its ID."""
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise ValueError("Quantity must be a whole number.")

    if not 0 <= quantity <= 1_000_000:
        raise ValueError("Quantity cannot be negative.")

    with closing(get_db_connection()) as conn:
        with conn:
            cursor = conn.execute(
                "UPDATE products "
                "SET quantity = ? "
                "WHERE id = ? AND is_active = 1",
                (
                    quantity,
                    product_id,
                ),
            )

            if cursor.rowcount != 1:
                raise ValueError(
                    "Product no longer exists in inventory."
                )

    return product_id


def deactivate_product(product_id):
    """Remove a product from active inventory without deleting sales history."""
    with closing(get_db_connection()) as conn:
        with conn:
            cursor = conn.execute(
                "UPDATE products "
                "SET is_active = 0 "
                "WHERE id = ? AND is_active = 1",
                (product_id,),
            )

            if cursor.rowcount != 1:
                raise ValueError(
                    "Product no longer exists in active inventory."
                )

    return product_id


def remove_product_by_id(product_id):
    """Deactivate one active product by ID."""
    if isinstance(product_id, bool) or not isinstance(product_id, int):
        raise ValueError("Product ID must be a whole number.")

    if product_id <= 0:
        raise ValueError("Product ID must be greater than zero.")

    product = find_product_by_id(product_id)

    if product is None:
        raise ValueError(
            f"No active product with ID {product_id} was found."
        )

    return deactivate_product(product_id)


def remove_product_by_name(name):
    """Deactivate one active product by exact, case-insensitive name."""
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError("Product name cannot be empty.")

    with closing(get_db_connection()) as conn:
        matches = conn.execute(
            "SELECT id FROM products "
            "WHERE is_active = 1 "
            "AND name = ? COLLATE NOCASE "
            "ORDER BY id",
            (name.strip(),),
        ).fetchall()

    if not matches:
        raise ValueError(
            f'No active product named "{name.strip()}" was found.'
        )

    if len(matches) > 1:
        raise ValueError(
            f'More than one active product is named "{name.strip()}". '
            "Rename products so the name is unique before removing one."
        )

    return deactivate_product(matches[0]["id"])


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
        product = _product_from_row(row)
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
        product_id = create_product(
            name,
            price,
            quantity,
        )
    except ValueError as error:
        print(error)
        return

    print(f"\nProduct added successfully with ID {product_id}.")


def remove_product():
    try:
        product_id = int(
            input("Enter the product ID to remove: ")
        )
    except ValueError:
        print(
            "Invalid input. Please enter a valid product ID."
        )
        return

    product = find_product_by_id(product_id)

    if product is None:
        print(
            f"Product with ID {product_id} not found."
        )
        return

    try:
        remove_product_by_id(product_id)
    except ValueError as error:
        print(error)
        return

    print(
        f"Product with ID {product_id} has been removed."
    )


def update_product():
    try:
        product_id = int(
            input("Enter the product ID to update: ")
        )
    except ValueError:
        print(
            "Invalid input. Please enter a valid product ID."
        )
        return

    product = find_product_by_id(product_id)

    if product is None:
        print(
            f"Product with ID {product_id} not found."
        )
        return

    name = input(
        "Enter new product name "
        "(leave blank to keep current): "
    ).strip()

    price_input = input(
        "Enter new product price "
        "(leave blank to keep current): "
    ).strip()

    quantity_input = input(
        "Enter new product quantity "
        "(leave blank to keep current): "
    ).strip()

    new_name = name or product["name"]
    new_price = product["price"]
    new_quantity = product["quantity"]

    if price_input:
        try:
            new_price = Decimal(price_input)
        except (ValueError, InvalidOperation):
            print(
                "Invalid input. Please enter a valid price."
            )
            return

        if not new_price.is_finite() or not Decimal("0") < new_price <= Decimal("1000000"):
            print(
                "Price cannot be negative. "
                "Please enter a valid price."
            )
            return

    if quantity_input:
        try:
            new_quantity = int(quantity_input)
        except ValueError:
            print(
                "Invalid input. Please enter a valid quantity."
            )
            return

        if new_quantity < 0:
            print(
                "Quantity cannot be negative. "
                "Please enter a valid quantity."
            )
            return

    try:
        update_product_record(product_id, new_name, new_price, new_quantity)
    except ValueError as error:
        print(error)
        return

    print(
        f"Product with ID {product_id} has been updated."
    )


def search_product():
    search_term = input(
        "Enter product name or ID to search: "
    ).strip()

    products = search_active_products(search_term)
    if products:
        for product in products:
            display_product(product)
        return
    print(f"No product matching '{search_term}' was found.")


def update_product_record(product_id, name, price, quantity):
    """Save an entire edit atomically, including legacy mirror values."""
    cents = to_cents(price)
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError("Product name must contain 1–100 characters.")
    if not 0 < cents <= 100_000_000:
        raise ValueError("Price must be between $0.01 and $1,000,000.00.")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or not 0 <= quantity <= 1_000_000:
        raise ValueError("Quantity must be a whole number between 0 and 1,000,000.")
    with closing(get_db_connection()) as conn:
        with conn:
            result = conn.execute("UPDATE products SET name=?, price=?, price_cents=?, quantity=? WHERE id=? AND is_active=1",
                                  (name.strip(), float(from_cents(cents)), cents, quantity, product_id))
            if result.rowcount != 1:
                raise ValueError("Product no longer exists in inventory.")
