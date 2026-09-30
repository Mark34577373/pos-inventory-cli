# ui/app.py
import tkinter as tk
from database.database import initialize_database
from ui.dashboard import create_dashboard_view
from ui.inventory_view import create_inventory_view
from ui.sale_view import create_sale_view
from ui.reports_view import create_reports_view


class TCCPApplication:
    def __init__(self, root):
        self.root = root
        self.root.title("TCCP Point of Sale")
        self.root.geometry("980x680")
        self.root.minsize(860, 580)
        initialize_database()
        self.create_sidebar()
        self.create_viewport()
        self.show_dashboard()

    def create_sidebar(self):
        sidebar = tk.Frame(self.root, bg="#2c3e50", width=200)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)
        brand = tk.Label(sidebar, text="TCCP", font=("Arial", 18, "bold"), fg="#ecf0f1", bg="#2c3e50", pady=20)
        brand.pack(anchor=tk.W, padx=20)
        routes = [
            ("🏠 Dashboard", self.show_dashboard),
            ("🛒 New Sale", self.show_sale),
            ("📦 Inventory", self.show_inventory),
            ("📊 Reports", self.show_reports)
        ]
        for label, action in routes:
            btn = tk.Label(sidebar, text=label, font=("Arial", 12), bg="#2c3e50", fg="#ecf0f1", anchor=tk.W, padx=20, pady=12, cursor="hand2")
            btn.pack(fill=tk.X)
            btn.bind("<Button-1>", lambda event, act=action: act())
            btn.bind("<Enter>", lambda event, b=btn: b.config(bg="#34495e"))
            btn.bind("<Leave>", lambda event, b=btn: b.config(bg="#2c3e50"))

    def create_viewport(self):
        self.viewport = tk.Frame(self.root, bg="#f8f9fa", padx=30, pady=30)
        self.viewport.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

    def clear_viewport(self):
        for widget in self.viewport.winfo_children():
            widget.destroy()

    def show_dashboard(self):
        self.clear_viewport()
        create_dashboard_view(self.viewport)

    def show_sale(self):
        """Show the sale and checkout screen."""
        self.clear_viewport()
        create_sale_view(self.viewport)

    def show_inventory(self):
        self.clear_viewport()
        create_inventory_view(self.viewport)

    def show_reports(self):
        """Load the live sales reporting dashboard."""
        self.clear_viewport()
        create_reports_view(self.viewport)
