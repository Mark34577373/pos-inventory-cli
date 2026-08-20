products = [
    {
        "name": "Product 1",
        "price": 10.99,
        "quantity": 5
    
    },
    {
        "name": "Product 2",
        "price": 15.99,
        "quantity": 0

    }
]
def view_inventory():
    print("Inventory:")
    for product in products:
        quantity = product["quantity"]
    
        print(
            product["name"],
            "$"  + str(product["price"]),
            "Qty",
            quantity
        )
        if quantity <= 0:
            print(product["name"], "is out of stock.")
        else:
            print(product["name"], "is in stock.")
        print("--------------------")

def main():
    while True:
        print(" POS INVENTORY SYSTEM")
        print("--------------------")
        print("1. View Inventory")
        print("2. Add Product")
        print("3. Exit")
        choice = input("Enter your choice: ")

        if choice == "1":
            view_inventory()
        elif choice == "2":
            print("adding product...")
        elif choice == "3":
            print("Exiting...")
            break
        else:
            print("Invalid choice. Please try again.")


if __name__ == "__main__":
    main()