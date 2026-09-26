# main.py
import services.inventory as inventory
import services.checkout as checkout
from database.database import initialize_database
from services.transaction_service import view_sales_report

def show_menu():
    print("\n==========================")
    print("   TCCP POS & INVENTORY   ")
    print("==========================")
    print("1. View Inventory")
    print("2. Add Product")
    print("3. Remove Product")
    print("4. Update Product")
    print("5. Search Product")
    print("--------------------------")
    print("6. Add Item to Cart")
    print("7. View Cart & Checkout")
    print("--------------------------")
    print("8. View Sales Report")  # <-- ADD THIS OPTION
    print("9. Exit")
    choice = input("Enter your choice: ")
    return choice

def main():
    initialize_database()

    while True:
        choice = show_menu()

        if choice == "1":
            inventory.view_inventory()
        elif choice == "2":
            inventory.add_product()
        elif choice == "3":
            inventory.remove_product()
        elif choice == "4":
            inventory.update_product()
        elif choice == "5":
            inventory.search_product()
        elif choice == "6":
            checkout.add_to_cart()
        elif choice == "7":
            checkout.checkout()
        elif choice == "8":
            view_sales_report()
        elif choice == "9":
            print("Exiting TCCP System. Goodbye!")
            break
        else:
            print("Invalid choice. Please try again.")

if __name__ == "__main__":
    main()
