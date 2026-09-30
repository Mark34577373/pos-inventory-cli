import tkinter as tk


def create_modal_window(
    parent,
    title,
    width,
    height,
    background="#f8f9fa",
    resizable=(False, False),
):
    """Create a centered modal window owned by a parent widget."""
    owner = parent.winfo_toplevel()
    window = tk.Toplevel(owner)
    window.title(title)
    window.configure(bg=background)
    window.resizable(*resizable)
    window.transient(owner)

    x = owner.winfo_rootx() + (owner.winfo_width() - width) // 2
    y = owner.winfo_rooty() + (owner.winfo_height() - height) // 2
    window.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
    return window
