# main.py
import Inventory as inventory
import pos

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
    print("8. Exit")
    choice = input("Enter your choice: ")
    return choice

def main():
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
            pos.add_to_cart()
        elif choice == "7":
            pos.checkout()
        elif choice == "8":
            print("Exiting TCCP System. Goodbye!")
            break
        else:
            print("Invalid choice. Please try again.")

if __name__ == "__main__":
    main()
