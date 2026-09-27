import services.inventory as inventory
import services.checkout as checkout
from database.database import initialize_database
from services.transaction_service import view_sales_report


def show_menu():
    print("\n===================================")
    print("          TCCP POINT OF SALE")
    print("===================================")
    print("1. Start Sale")
    print("2. View Inventory")
    print("3. Add Product")
    print("4. Remove Product")
    print("5. Update Product")
    print("6. Search Product")
    print("7. View Sales Report")
    print("8. Exit")
    print("-----------------------------------")

    return input("Enter your choice: ").strip()


def main():
    initialize_database()

    while True:
        choice = show_menu()

        if choice == "1":
            checkout.start_sale()

        elif choice == "2":
            inventory.view_inventory()

        elif choice == "3":
            inventory.add_product()

        elif choice == "4":
            inventory.remove_product()

        elif choice == "5":
            inventory.update_product()

        elif choice == "6":
            inventory.search_product()

        elif choice == "7":
            view_sales_report()

        elif choice == "8":
            print("\nExiting TCCP System. Goodbye!")
            break

        else:
            print("\nInvalid choice. Please try again.")


if __name__ == "__main__":
    main()