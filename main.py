# main.py
import tkinter as tk
from ui.app import TCCPApplication

def main():
    root = tk.Tk()
    app = TCCPApplication(root)
    root.mainloop()

if __name__ == "__main__":
    main()
