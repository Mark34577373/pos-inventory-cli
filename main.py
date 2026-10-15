
import tkinter as tk

from ui.app import TCCPApplication


def main():
    root = tk.Tk()
    _app = TCCPApplication(root)
    root.mainloop()

if __name__ == "__main__":
    main()
