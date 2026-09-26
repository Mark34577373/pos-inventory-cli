# models/transaction.py
class Transaction:
    def __init__(self, id, timestamp, total, items=None):
        self.id = id
        self.timestamp = timestamp
        self.total = total
        self.items = items if items else []  # List of items belonging to this receipt
