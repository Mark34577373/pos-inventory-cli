# ui/dashboard.py
import tkinter as tk

def create_dashboard_view(parent_frame):
    """Draws the main dashboard home layout context panel."""
    # Screen Title
    title = tk.Label(parent_frame, text="Dashboard", font=("Arial", 22, "bold"), bg="#f8f9fa", fg="#333333")
    title.pack(anchor=tk.W, pady=(0, 5))
    
    subtitle = tk.Label(parent_frame, text="Welcome back. Here's what's happening with your store.", font=("Arial", 11), bg="#f8f9fa", fg="#666666")
    subtitle.pack(anchor=tk.W, pady=(0, 25))

    # Metric Cards Row Container
    cards_frame = tk.Frame(parent_frame, bg="#f8f9fa")
    cards_frame.pack(fill=tk.X, anchor=tk.W, pady=(0, 30))

    metrics = [
        ("Revenue", "$342.76", "#2ecc71"),
        ("Sales", "8", "#3498db"),
        ("Products", "12", "#e67e22")
    ]

    for label, val, color in metrics:
        card = tk.Frame(cards_frame, bg="white", highlightbackground="#e0e0e0", highlightthickness=1, width=160, height=90)
        card.pack(side=tk.LEFT, padx=(0, 15))
        card.pack_propagate(False)

        l_lbl = tk.Label(card, text=label, font=("Arial", 10, "bold"), fg="#7f8c8d", bg="white")
        l_lbl.pack(anchor=tk.W, padx=10, pady=(10, 2))
        
        v_lbl = tk.Label(card, text=val, font=("Arial", 18, "bold"), fg=color, bg="white")
        v_lbl.pack(anchor=tk.W, padx=10)

    # Recent Transactions Placeholder section
    rx_lbl = tk.Label(parent_frame, text="Recent Transactions", font=("Arial", 14, "bold"), bg="#f8f9fa", fg="#2c3e50")
    rx_lbl.pack(anchor=tk.W, pady=(0, 10))

    divider = tk.Frame(parent_frame, bg="#e0e0e0", height=1)
    divider.pack(fill=tk.X, pady=(0, 10))

    # Stub placeholder lines matching target specs
    t1 = tk.Label(parent_frame, text="#12    Mouse x2".ljust(30) + "$23.79".ljust(15) + "Cash", font=("Courier", 11), bg="#f8f9fa", fg="#333333")
    t1.pack(anchor=tk.W, pady=3)
    
    t2 = tk.Label(parent_frame, text="#11    Keyboard x1".ljust(30) + "$32.46".ljust(15) + "Card", font=("Courier", 11), bg="#f8f9fa", fg="#333333")
    t2.pack(anchor=tk.W, pady=3)
