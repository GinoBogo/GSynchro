#!/usr/bin/env python3
"""
Generic dialog utilities for GSynchro.

Provides reusable dialog components for user input.

 Author: Gino Bogo
License: MIT
Version: 1.0
"""

# Standard library imports.
from typing import Optional
import tkinter as tk
from tkinter import ttk

# Personal library imports.
from libs.g_button import GButton
from libs.g_scaling import GScaling


DEFAULT_SPACING = 5


def ask_string_dialog(
    parent: tk.Widget,
    title: str,
    prompt: str,
    initial: str = "",
    colors: Optional[dict] = None,
) -> Optional[str]:
    """
    Generic modal dialog to get a single string from the user.
    """
    if colors is None:
        colors = {
            "buttons": {
                "primary": {"bg": "#4CAF50", "fg": "white"},
                "secondary": {"bg": "#f0f0f0", "fg": "black"},
                "default": {"bg": "#e0e0e0", "fg": "black"},
            }
        }

    result = None

    def on_ok():
        """Handle OK button click - capture entry value and close dialog."""
        nonlocal result
        result = entry_var.get()
        dialog.destroy()

    # Scale dialog size based on display DPI
    scale_factor = GScaling.get_scale_factor(parent)
    dialog_width = int(300 * scale_factor)
    dialog_height = int(120 * scale_factor)

    dialog = tk.Toplevel(parent)
    dialog.transient(parent)
    dialog.grab_set()
    dialog.title(title)
    dialog.minsize(dialog_width, dialog_height)
    dialog.maxsize(dialog_width, dialog_height)

    style = ttk.Style()
    dialog_bg = style.lookup("TFrame", "background")
    dialog.configure(bg=dialog_bg)

    dialog.rowconfigure(0, weight=1)
    dialog.columnconfigure(0, weight=1)

    # Scale pixels based on display DPI
    spacing = GScaling.scale_pixels(DEFAULT_SPACING, dialog)

    content_frame = ttk.Frame(dialog, padding=spacing * 2)
    content_frame.grid(row=0, column=0, sticky=tk.NSEW)
    content_frame.columnconfigure(0, weight=1)

    ttk.Label(content_frame, text=prompt).grid(
        row=0,
        column=0,
        sticky=tk.W,
        pady=(0, spacing),
    )

    entry_var = tk.StringVar(value=initial)
    entry = ttk.Entry(content_frame, textvariable=entry_var)
    entry.grid(row=1, column=0, sticky=tk.EW)
    entry.focus_set()
    entry.select_range(0, "end")
    entry.bind("<Return>", lambda e: on_ok())

    button_frame = ttk.Frame(dialog, padding=(spacing * 2, 0, spacing * 2, spacing * 2))
    button_frame.grid(row=1, column=0, sticky=tk.EW)
    button_frame.columnconfigure(0, weight=1)
    button_frame.columnconfigure(1, weight=0)
    button_frame.columnconfigure(2, weight=0)
    button_frame.columnconfigure(3, weight=1)

    GButton(
        button_frame,
        text="Cancel",
        command=dialog.destroy,
        width=80,
        height=34,
        **colors["buttons"]["secondary"],
    ).grid(row=0, column=1, padx=spacing)

    GButton(
        button_frame,
        text="OK",
        command=on_ok,
        width=80,
        height=34,
        **colors["buttons"]["primary"],
    ).grid(row=0, column=2, padx=spacing)

    parent.update_idletasks()
    dialog.update_idletasks()
    x = parent.winfo_rootx() + parent.winfo_width() // 2 - dialog.winfo_width() // 2
    y = parent.winfo_rooty() + parent.winfo_height() // 2 - dialog.winfo_height() // 2
    dialog.geometry(f"+{x}+{y}")
    dialog.wait_window()

    return result
