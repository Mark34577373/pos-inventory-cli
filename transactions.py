# transactions.py
from datetime import datetime

# Global list to store transaction records while the program is running
transaction_history = []
next_transaction_id = 1001

def log_transaction(cart_items, total_amount):
    """Saves a completed sale to the history log with a timestamp."""
    global next_transaction_id
    
    # Structure the items for the log
    items_snapshot = []
    for item in cart_items:
        items_snapshot.append({
            "name": item["name"],
            "qty": item["qty"],
            "price": item["price"]
        })

    # Create the complete record
    record = {
        "tx_id": next_transaction_id,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "items": items_snapshot,
        "total": total_amount
    }
    
    transaction_history.append(record)
    next_transaction_id += 1
    return record["tx_id"]

def view_sales_report():
    """Displays a summary of all sales made during this session."""
    print("\n===================================")
    print("        DAILY SALES REPORT")
    print("===================================")
    
    if not transaction_history:
        print("No transactions recorded yet today.")
        return

    grand_total = 0.0
    for tx in transaction_history:
        print(f"\nTx ID: #{tx['tx_id']} | Time: {tx['timestamp']}")
        print("Items purchased:")
        for item in tx["items"]:
            print(f"  - {item['name']} x{item['qty']} (${item['price']:.2f} each)")
        print(f"Total Sale: ${tx['total']:.2f}")
        print("-" * 35)
        grand_total += tx["total"]
        
    print(f"\nGRAND TOTAL SALES: ${grand_total:.2f}")
    print("===================================")
