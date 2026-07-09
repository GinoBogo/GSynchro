#!/usr/bin/env python3
"""
GMessagebox - HiDPI-Compatible Custom Messagebox

A custom messagebox implementation that properly scales on HiDPI displays,
using the GScaling utility and g_theme color palette for consistent styling.

 Author: Gino Bogo
License: MIT
Version: 1.0
"""

# Standard library imports.
import tkinter as tk
from tkinter import ttk
from typing import Optional

# Personal library imports.
from libs.g_button import GButton
from libs.g_scaling import GScaling
from libs.g_theme import get_theme_colors


class GMessagebox:
    """Custom messagebox with HiDPI support and theme integration."""

    @staticmethod
    def _get_icon_symbol(icon_type: str) -> str:
        """
        Return Unicode symbol for the icon type.

        This method maps standard messagebox icon types to appropriate
        Unicode emoji symbols for visual representation in the dialog.

        Args:
            icon_type (str): The type of icon requested (e.g., "info", "warning").

        Returns:
            str: Unicode symbol corresponding to the icon type, or empty string
                 if the type is not recognized.
        """
        icons = {
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "❌",
            "question": "❓",
        }
        return icons.get(icon_type, "")

    @staticmethod
    def _show_dialog(
        title: str,
        message: str,
        icon_type: str,
        button_type: str,
        parent: Optional[tk.Misc] = None,
        **kwargs,
    ) -> Optional[bool]:
        """
        Display a modal dialog with the specified parameters.

        This is the core method that creates and displays all types of message dialogs.
        It handles UI creation, scaling for HiDPI displays, theming, and user interaction.

        Args:
            title (str): The title to display in the dialog window's title bar.
            message (str): The message text to display in the dialog.
            icon_type (str): Type of icon to display ("info", "warning", "error", "question").
            button_type (str): Type of buttons to display ("ok" or "yes_no").
            parent (tk.Misc, optional): Parent widget for the dialog. If None, attempts
                                       to find the default root window.
            **kwargs: Additional keyword arguments (reserved for future use).

        Returns:
            bool or None: For "yes_no" dialogs, returns True if Yes was clicked,
                         False if No was clicked. For "ok" dialogs, returns None.
        """
        if parent is None:
            # Try to find the root window if no parent specified
            try:
                parent = tk._default_root
            except AttributeError:
                parent = None

        colors = get_theme_colors()

        # Scale dimensions based on display DPI
        scale_factor = GScaling.get_scale_factor(parent) if parent else 1.0
        dialog_width = int(400 * scale_factor)
        spacing = GScaling.scale_pixels(5, parent) if parent else 5

        dialog = tk.Toplevel(parent)
        dialog.title(title)
        dialog.transient(parent)
        dialog.grab_set()
        dialog.resizable(False, False)

        # Get dialog background
        style = ttk.Style()
        dialog_bg = style.lookup("TFrame", "background")
        dialog.configure(bg=dialog_bg)

        # Main content frame
        content_frame = ttk.Frame(dialog, padding=spacing * 2)
        content_frame.pack(fill=tk.BOTH, expand=True)

        # Icon and message row
        icon_message_frame = ttk.Frame(content_frame)
        icon_message_frame.pack(fill=tk.BOTH, expand=True, pady=(spacing, spacing * 2))

        # Icon label with Unicode symbol
        icon_symbol = GMessagebox._get_icon_symbol(icon_type)
        icon_label = ttk.Label(
            icon_message_frame,
            text=icon_symbol,
            font=("Segoe UI Emoji", 32),
        )
        icon_label.pack(side=tk.LEFT, padx=(0, spacing * 2))

        # Message label with wrapping
        message_label = ttk.Label(
            icon_message_frame,
            text=message,
            wraplength=int(dialog_width * 0.6),
            justify=tk.LEFT,
        )
        message_label.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Button frame
        button_frame = ttk.Frame(content_frame)
        button_frame.pack(fill=tk.X, pady=(spacing * 2, 0))

        result = None

        if button_type == "ok":

            def on_ok(*args):
                """
                Handle OK button click - close dialog.

                This inner function is called when the user clicks the OK button.
                It simply closes the dialog and returns None.
                """
                nonlocal result
                dialog.destroy()

            GButton(
                button_frame,
                text="OK",
                command=on_ok,
                width=100,
                height=34,
                **colors["buttons"]["primary"],
            ).pack(side=tk.RIGHT, padx=spacing)

        elif button_type == "yes_no":

            def on_yes():
                """
                Handle Yes button click - set result to True and close dialog.

                This inner function is called when the user clicks the Yes button
                in a yes/no dialog. It sets the result to True and closes the dialog.
                """
                nonlocal result
                result = True
                dialog.destroy()

            def on_no():
                """
                Handle No button click - set result to False and close dialog.

                This inner function is called when the user clicks the No button
                in a yes/no dialog. It sets the result to False and closes the dialog.
                """
                nonlocal result
                result = False
                dialog.destroy()

            GButton(
                button_frame,
                text="No",
                command=on_no,
                width=100,
                height=34,
                **colors["buttons"]["secondary"],
            ).pack(side=tk.RIGHT, padx=spacing)

            GButton(
                button_frame,
                text="Yes",
                command=on_yes,
                width=100,
                height=34,
                **colors["buttons"]["primary"],
            ).pack(side=tk.RIGHT, padx=spacing)

        # Center dialog relative to parent
        dialog.update_idletasks()
        if parent:
            parent_x = parent.winfo_rootx() + parent.winfo_width() // 2
            parent_y = parent.winfo_rooty() + parent.winfo_height() // 2
            dialog_x = parent_x - dialog.winfo_width() // 2
            dialog_y = parent_y - dialog.winfo_height() // 2
            dialog.geometry(f"+{dialog_x}+{dialog_y}")

        # Make dialog modal
        dialog.wait_window()

        return result

    @staticmethod
    def showinfo(
        title: str, message: str, parent: Optional[tk.Misc] = None, **kwargs
    ) -> None:
        """
        Display an information message box.

        Shows a modal dialog with an information icon and an OK button.
        This is typically used for informational messages that don't require
        any action from the user other than acknowledgment.

        Args:
            title (str): The title to display in the dialog window's title bar.
            message (str): The information message to display.
            parent (tk.Misc, optional): Parent widget for the dialog.
            **kwargs: Additional keyword arguments (reserved for future use).

        Example:
            GMessagebox.showinfo("Information", "Operation completed successfully")
        """
        GMessagebox._show_dialog(title, message, "info", "ok", parent, **kwargs)

    @staticmethod
    def showwarning(
        title: str, message: str, parent: Optional[tk.Misc] = None, **kwargs
    ) -> None:
        """
        Display a warning message box.

        Shows a modal dialog with a warning icon and an OK button.
        This is typically used for warning messages that inform the user
        about a potential issue or concern.

        Args:
            title (str): The title to display in the dialog window's title bar.
            message (str): The warning message to display.
            parent (tk.Misc, optional): Parent widget for the dialog.
            **kwargs: Additional keyword arguments (reserved for future use).

        Example:
            GMessagebox.showwarning("Warning", "This action cannot be undone")
        """
        GMessagebox._show_dialog(title, message, "warning", "ok", parent, **kwargs)

    @staticmethod
    def showerror(
        title: str, message: str, parent: Optional[tk.Misc] = None, **kwargs
    ) -> None:
        """
        Display an error message box.

        Shows a modal dialog with an error icon and an OK button.
        This is typically used for error messages that inform the user
        about a problem that occurred.

        Args:
            title (str): The title to display in the dialog window's title bar.
            message (str): The error message to display.
            parent (tk.Misc, optional): Parent widget for the dialog.
            **kwargs: Additional keyword arguments (reserved for future use).

        Example:
            GMessagebox.showerror("Error", "Failed to save file: Permission denied")
        """
        GMessagebox._show_dialog(title, message, "error", "ok", parent, **kwargs)

    @staticmethod
    def askyesno(
        title: str, message: str, parent: Optional[tk.Misc] = None, **kwargs
    ) -> bool:
        """
        Display a yes/no question dialog.

        Shows a modal dialog with a question icon and Yes/No buttons.
        This is typically used when the application needs to confirm an action
        or get a binary choice from the user.

        Args:
            title (str): The title to display in the dialog window's title bar.
            message (str): The question to ask the user.
            parent (tk.Misc, optional): Parent widget for the dialog.
            **kwargs: Additional keyword arguments (reserved for future use).

        Returns:
            bool: True if Yes is clicked, False if No is clicked.

        Example:
            if GMessagebox.askyesno("Confirm", "Are you sure you want to delete this file?"):
                # User clicked Yes
                delete_file()
            else:
                # User clicked No
                pass
        """
        return GMessagebox._show_dialog(
            title, message, "question", "yes_no", parent, **kwargs
        )
