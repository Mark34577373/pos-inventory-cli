products = [
    {
        "id": 1,
        "name": "Mouse",
        "price": 10.99,
        "quantity": 5
    
    },
    {
        "id": 2,
        "name": "Keyboard",
        "price": 29.99,
        "quantity": 0

    }
]

next_product_id = max(product["id"] for product in products) + 1

def view_inventory():
    print("-------------------------")
    print("        INVENTORY")
    print("-------------------------")

    if len(products) == 0:
        print("No products in inventory.")
        return
    
    for product in products:
        print(f"\nProduct ID: {product['id']}")
        print(f"Product: {product['name']}")
        print(f"Price: ${product['price']:.2f}")
        print(f"Quantity: {product['quantity']}")
        
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
    try:
        price = float(input("Enter product price:$ "))
        quantity = int(input("Enter product quantity: "))
    except ValueError:
        print("Invalid input. Please enter a valid number for price and quantity.")
        return
    if price < 0:
        print("Price cannot be negative. Please enter a valid price.")
        return
    if quantity < 0:
        print("Quantity cannot be negative. Please enter a valid quantity.")
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


def show_menu():
    print(" POS INVENTORY SYSTEM")
    print("--------------------")
    print("1. View Inventory")
    print("2. Add Product")
    print("3. Exit")
    choice = input("Enter your choice: ")
    return choice

def main():
    while True:
        choice = show_menu()

        if choice == "1":
            view_inventory()
        elif choice == "2":
            add_product()
        elif choice == "3":
            print("Exiting...")
            break
        else:
            print("Invalid choice. Please try again.")


if __name__ == "__main__":
    main()