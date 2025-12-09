#!/usr/bin/env python3
"""
GCompare - GUI File Comparison Tool

A graphical tool for side-by-side comparison of text files. It highlights
differences in a modern and graphical way, allowing for easy visualization of
changes between two files.

Author: Gino Bogo
License: MIT
Version: 1.0
"""

from __future__ import annotations

import difflib
import json
import os
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox


CONFIG_FILE = "g_compare.json"
HISTORY_LENGTH = 10


class GCompare:
    """Main application class for GCompare file comparison tool."""

    def __init__(self, root: tk.Tk):
        """Initialize the GCompare application.

        Args:
            root: The main Tkinter root window
        """
        self.root = root

        # File Paths
        self.file_a = tk.StringVar()
        self.file_b = tk.StringVar()
        self.file_a_history = []
        self.file_b_history = []

        # Text Content
        self.content_a = tk.StringVar()
        self.content_b = tk.StringVar()

        # UI Components
        self.file_view_a = None
        self.file_view_b = None
        self.panel_a = None
        self.panel_b = None
        self.scroll_marker_id = None
        self.diff_map_canvas = None
        self.v_scrollbar_a = None
        self.v_scrollbar_b = None
        self.h_scrollbar_a = None
        self.h_scrollbar_b = None

        # Status Variables
        self.status_a = tk.StringVar()
        self.status_b = tk.StringVar()

        self.load_config()
        self._init_window()  # Initialize window properties
        self._setup_ui()  # Set up the main UI components

        # Load files from command line arguments
        if len(sys.argv) > 1:
            self._load_file_a(sys.argv[1])
        if len(sys.argv) > 2:
            self._load_file_b(sys.argv[2])

        # If both files were provided on the command line, run the comparison
        if len(sys.argv) > 2:
            self._compare_files()

    # ==========================================================================
    # INITIALIZATION METHODS
    # ==========================================================================

    def _init_window(self):
        """Initialize main window properties."""
        self.root.title("GCompare - File Comparison Tool")
        self.root.minsize(1024, 768)
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _setup_ui(self):
        """Set up the main user interface."""
        self._setup_styles()

        # Main container
        main_frame = self._create_main_frame()

        # Control panel
        control_frame = self._create_control_frame(main_frame)
        self._create_control_buttons(control_frame)

        # Text panels
        panels_frame = self._create_panels_frame(main_frame)
        self._create_file_panels(panels_frame)

        # Status bar
        self._create_status_bar(main_frame)
        # Initial status
        self.status_a.set("by Gino Bogo")

        # Set up synchronized scrolling after all UI components are created
        self._setup_synchronized_scrolling()

    def _setup_styles(self):
        """Configure application styles."""
        style = ttk.Style()

        # Light green button style
        style.configure(
            "lightgreen.TButton",
            background="#90EE90",
            foreground="black",
            borderwidth=1,
            focuscolor="none",
            relief="raised",
        )
        style.map(
            "lightgreen.TButton",
            background=[("active", "#B6FFB6"), ("pressed", "#90EE90")],
        )

        # Light blue button style
        style.configure(
            "lightblue.TButton",
            background="#87CEFA",
            foreground="black",
            borderwidth=1,
            focuscolor="none",
            relief="raised",
        )
        style.map(
            "lightblue.TButton",
            background=[("active", "#ADD8E6"), ("pressed", "#87CEFA")],
        )

        # Configure Text heading font
        font_tuple = self._get_mono_font()
        style.configure("TText", font=font_tuple)

    def _create_main_frame(self):
        """Create the main application frame."""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=tk.NSEW)

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(1, weight=1)

        return main_frame

    def _create_control_frame(self, main_frame):
        """Create control buttons frame."""
        control_frame = ttk.Frame(main_frame)
        control_frame.grid(row=0, column=0, columnspan=3, sticky=tk.EW, pady=5)
        return control_frame

    def _create_control_buttons(self, control_frame):
        """Create the main control buttons."""
        buttons_config = [
            ("Compare", self._compare_files, None),
            ("Reload", self._reload_files, None),
        ]

        button_container = ttk.Frame(control_frame)
        button_container.pack(expand=True)

        for text, command, color in buttons_config:
            button_kwargs = {
                "text": text,
                "command": command,
                "cursor": "hand2",
                "width": 12,
            }
            if color:
                button_kwargs["style"] = f"{color}.TButton"

            ttk.Button(button_container, **button_kwargs).pack(
                side=tk.LEFT, padx=5, pady=5
            )

    def _create_panels_frame(self, main_frame):
        """Create panels frame for displays."""
        panels_frame = ttk.Frame(main_frame)
        panels_frame.grid(row=1, column=0, columnspan=3, sticky=tk.NSEW)

        panels_frame.columnconfigure(0, weight=1)
        panels_frame.columnconfigure(1, weight=0)  # For the diff map
        panels_frame.columnconfigure(2, weight=1)
        panels_frame.rowconfigure(0, weight=1)

        return panels_frame

    def _create_file_panels(self, panels_frame):
        """Create file panels A and B."""
        # Panel A configuration
        panel_a_config = {
            "title": "File A",
            "column": 0,
            "padx": (0, 2),
            "content_var": self.content_a,
            "file_var": self.file_a,
            "file_history": self.file_a_history,
            "open_command": self._open_file_a,
            "button_color": "lightgreen",
            "save_command": self._save_file_a,
        }

        # Panel B configuration
        panel_b_config = {
            "title": "File B",
            "column": 2,
            "padx": (2, 0),
            "content_var": self.content_b,
            "file_var": self.file_b,
            "file_history": self.file_b_history,
            "open_command": self._open_file_b,
            "button_color": "lightblue",
            "save_command": self._save_file_b,
        }

        self._create_panel(panels_frame, panel_a_config)

        # Diff Map Canvas
        self.diff_map_canvas = tk.Canvas(panels_frame, width=20, bg="#F0F0F0")
        self.diff_map_canvas.grid(row=0, column=1, sticky="ns", pady=(10, 0))
        self.scroll_marker_id = (
            self.diff_map_canvas.create_rectangle(  # Initial marker for scroll position
                1, 0, 19, 1, fill="", outline="black", tags="scroll_marker"
            )
        )
        self.diff_map_canvas.bind("<Configure>", self._compare_files)

        self._create_panel(panels_frame, panel_b_config)

    def _create_panel(self, parent, config):
        """Create an individual text panel."""
        panel = ttk.LabelFrame(parent, text=config["title"], padding="5")
        panel.grid(
            row=0,
            column=config["column"],
            sticky=tk.NSEW,
            padx=config["padx"],
        )
        panel.columnconfigure(0, weight=1)
        panel.columnconfigure(1, weight=0)  # For the buttons
        panel.columnconfigure(2, weight=0)
        panel.rowconfigure(1, weight=1)  # For the text area

        # File path combobox
        path_combobox = ttk.Combobox(
            panel,
            textvariable=config["file_var"],
            values=config["file_history"],
        )
        path_combobox.grid(row=0, column=0, padx=5, pady=5, sticky=tk.EW)

        # Load Button
        ttk.Button(
            panel,
            text="Open",
            command=config["open_command"],
            cursor="hand2",
            style=f"{config['button_color']}.TButton",
        ).grid(row=0, column=1, padx=5, pady=5, sticky=tk.E)

        # Save Button
        ttk.Button(
            panel,
            text="Save",
            command=config["save_command"],
            cursor="hand2",
            style=f"{config['button_color']}.TButton",
        ).grid(row=0, column=2, padx=(0, 5), pady=5, sticky=tk.E)

        # Text Area
        text_area = tk.Text(panel, wrap=tk.WORD, state=tk.NORMAL)
        text_area.grid(row=1, column=0, columnspan=3, pady=(10, 0), sticky=tk.NSEW)
        text_area.bind(
            "<<Modified>>",
            lambda e, p=panel, t=config["title"]: self._on_text_modified(e, p, t),
        )

        # Scrollbars
        v_scrollbar = ttk.Scrollbar(panel, orient=tk.VERTICAL, command=text_area.yview)
        text_area.configure(yscrollcommand=v_scrollbar.set)
        v_scrollbar.grid(row=1, column=3, pady=(10, 0), sticky=tk.NS)

        h_scrollbar = ttk.Scrollbar(
            panel, orient=tk.HORIZONTAL, command=text_area.xview
        )
        text_area.configure(xscrollcommand=h_scrollbar.set)
        h_scrollbar.grid(row=2, column=0, columnspan=3, sticky=tk.EW)

        # Store text area reference
        if config["title"] == "File A":
            self.file_view_a = text_area
            self.panel_a = panel
            self.v_scrollbar_a = v_scrollbar
            self.h_scrollbar_a = h_scrollbar
        else:
            self.file_view_b = text_area
            self.panel_b = panel
            self.v_scrollbar_b = v_scrollbar
            self.h_scrollbar_b = h_scrollbar

    def _create_status_bar(self, parent):
        """Create status bar."""
        status_frame = ttk.Frame(parent, relief="flat", padding="2")
        status_frame.grid(row=2, column=0, columnspan=3, sticky=tk.EW, pady=(5, 0))

        status_frame.columnconfigure(0, weight=1)
        status_frame.columnconfigure(1, weight=1)

        # Status labels
        status_label_a = ttk.Label(
            status_frame, textvariable=self.status_a, anchor=tk.W
        )
        status_label_a.grid(row=0, column=0, sticky=tk.EW, padx=0)

        status_label_b = ttk.Label(
            status_frame, textvariable=self.status_b, anchor=tk.W
        )
        status_label_b.grid(row=0, column=1, sticky=tk.EW, padx=0)

    # ==========================================================================
    # CONFIGURATION METHODS
    # ==========================================================================

    def load_config(self):
        """Load configuration from file."""
        if not os.path.exists(CONFIG_FILE):
            return

        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)

            # Window geometry
            if "WINDOW" in config and "geometry" in config["WINDOW"]:
                self.root.geometry(config["WINDOW"]["geometry"])

            # File A History
            if "FILE_A_HISTORY" in config:
                self.file_a_history = config["FILE_A_HISTORY"]
                if self.file_a_history:
                    self.file_a.set(self.file_a_history[0])

            # File B History
            if "FILE_B_HISTORY" in config:
                self.file_b_history = config["FILE_B_HISTORY"]
                if self.file_b_history:
                    self.file_b.set(self.file_b_history[0])

        except json.JSONDecodeError:
            print(f"Warning: Could not parse {CONFIG_FILE}. Using defaults.")

    def save_config(self):
        """Save configuration to file."""
        # Update file history
        self._update_file_history("A", self.file_a, self.file_a.get())
        self._update_file_history("B", self.file_b, self.file_b.get())

        config = {
            "WINDOW": {"geometry": self.root.geometry()},
            "FILE_A_HISTORY": self.file_a_history,
            "FILE_B_HISTORY": self.file_b_history,
        }

        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)

    def _update_file_history(self, panel_name, file_var, new_path):
        """Update and save file history."""
        if not new_path:
            return

        history_list = self.file_a_history if panel_name == "A" else self.file_b_history
        if new_path in history_list:
            history_list.remove(new_path)
        history_list.insert(0, new_path)

    # ==========================================================================
    # FILE OPERATIONS
    # ==========================================================================

    def _open_file_a(self):
        """Open a file for panel A."""
        self._open_file("A")

    def _open_file_b(self):
        """Open a file for panel B."""
        self._open_file("B")

    def _open_file(self, panel_name):
        """Open a file dialog and load the selected file."""
        file_path = filedialog.askopenfilename()
        if file_path:
            if panel_name == "A":
                self._load_file_a(file_path)
            else:
                self._load_file_b(file_path)

    def _reload_files(self):
        """Reload both files, prompting to save if there are changes."""
        # Check File A for unsaved changes
        if self.panel_a and self.panel_a.cget("text").endswith("*"):
            response = messagebox.askyesnocancel(
                "Unsaved Changes",
                "File A has unsaved changes. Do you want to save them before reloading?",
            )
            if response is True:  # Yes
                self._save_file_a()
            elif response is None:  # Cancel
                return  # Abort the entire reload operation

        # Check File B for unsaved changes
        if self.panel_b and self.panel_b.cget("text").endswith("*"):
            response = messagebox.askyesnocancel(
                "Unsaved Changes",
                "File B has unsaved changes. Do you want to save them before reloading?",
            )
            if response is True:  # Yes
                self._save_file_b()
            elif response is None:  # Cancel
                return  # Abort the entire reload operation

        # Proceed with reloading the files
        if self.file_a.get():
            self._load_file_a(self.file_a.get())
        if self.file_b.get():
            self._load_file_b(self.file_b.get())

    def _save_file_a(self):
        """Save content of File A panel to its file."""
        self._save_file(self.file_a.get(), self.file_view_a, "A")

    def _save_file_b(self):
        """Save content of File B panel to its file."""
        self._save_file(self.file_b.get(), self.file_view_b, "B")

    def _save_file(self, file_path, text_widget, panel_name):
        """Save the content of a text widget to a file."""
        if not file_path:
            messagebox.showwarning(
                "Save Error", f"No file path specified for Panel {panel_name}."
            )
            return

        if not text_widget:
            messagebox.showerror(
                "Save Error", f"Text view for Panel {panel_name} is not available."
            )
            return

        if not messagebox.askyesno(
            "Confirm Save", f"Are you sure you want to overwrite '{file_path}'?"
        ):
            return

        try:
            content = text_widget.get("1.0", tk.END)
            with open(file_path, "w", encoding="utf-8") as file:
                file.write(content)

            # Reset modified state
            panel_widget = self.panel_a if panel_name == "A" else self.panel_b
            if panel_widget:
                panel_widget.config(text=f"File {panel_name}")
            messagebox.showinfo("Success", f"File '{file_path}' saved successfully.")
        except Exception as e:
            messagebox.showerror(
                "Save Error", f"Failed to save file '{file_path}':\n{e}"
            )

    def _load_file_a(self, file_path):
        """Load file A content into the text area."""
        try:
            with open(file_path, "r", encoding="utf-8") as file:
                content = file.read()
                self._update_file_history("A", self.file_a, file_path)
                self.file_a.set(file_path)
                self.content_a.set(content)
                if self.file_view_a:
                    self.file_view_a.delete("1.0", tk.END)
                    self.file_view_a.insert("1.0", content)
                    self.file_view_a.edit_modified(False)
                if self.panel_a:
                    self.panel_a.config(text="File A")
                line_count = len(content.splitlines())
                char_count = len(content)
                self.status_a.set(f"{line_count} lines, {char_count} characters")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load file: {e}")

    def _load_file_b(self, file_path):
        """Load file B content into the text area."""
        try:
            with open(file_path, "r", encoding="utf-8") as file:
                content = file.read()
                self._update_file_history("B", self.file_b, file_path)
                self.file_b.set(file_path)
                self.content_b.set(content)
                if self.file_view_b:
                    self.file_view_b.delete("1.0", tk.END)
                    self.file_view_b.insert("1.0", content)
                    self.file_view_b.edit_modified(False)
                if self.panel_b:
                    self.panel_b.config(text="File B")
                line_count = len(content.splitlines())
                char_count = len(content)
                self.status_b.set(f"{line_count} lines, {char_count} characters")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load file: {e}")

    # ==========================================================================
    # TEXT AND COMPARISON OPERATIONS
    # ==========================================================================

    def _on_text_modified(self, event, panel_widget, original_title):
        """Handle text modification to mark file as dirty."""
        text_widget = event.widget
        # The flag is set by Tkinter when the text is modified.
        if panel_widget and text_widget.edit_modified():
            panel_widget.config(text=f"{original_title}*")
            # We must reset the flag manually to be able to catch the next change.
            text_widget.edit_modified(False)

    def _compare_files(self, event=None):  # event=None for manual calls
        """Compare the content of the two text areas and highlight differences."""
        if not self.file_view_a or not self.file_view_b:
            messagebox.showwarning(
                "Warning", "Please load both files before comparing."
            )
            return

        # Get content and split into lines
        lines_a = self.file_view_a.get("1.0", tk.END).splitlines()
        lines_b = self.file_view_b.get("1.0", tk.END).splitlines()

        # Clear existing tags
        # Only delete diff tags, preserve others like 'sel'.
        self.file_view_a.tag_remove("difference", "1.0", tk.END)
        self.file_view_b.tag_remove("difference", "1.0", tk.END)

        # Clear diff map canvas
        # Only delete diff lines, preserve the scroll marker.
        if self.diff_map_canvas:
            self.diff_map_canvas.delete("diff_line")
            # Update scroll marker position in case canvas height changed
            first, last = self.file_view_a.yview()
            self._update_scroll_marker(float(first), float(last))

        # Configure tags for highlighting
        self.file_view_a.tag_configure(
            "difference", background="lightcoral"
        )  # Changed to lightcoral for removed
        self.file_view_b.tag_configure("difference", background="lightcoral")

        # Perform comparison
        differ = difflib.Differ()
        diff = differ.compare(lines_a, lines_b)

        a_index = 1
        b_index = 1
        added_lines = 0
        removed_lines = 0

        total_lines = max(len(lines_a), len(lines_b))
        canvas_height = (
            self.diff_map_canvas.winfo_height() if self.diff_map_canvas else 0
        )

        for line in diff:
            code = line[0]
            if code == " ":
                a_index += 1
                b_index += 1
            elif code == "-":
                removed_lines += 1
                start_pos = f"{a_index}.0"
                end_pos = f"{a_index}.end"
                self.file_view_a.tag_add("difference", start_pos, end_pos)
                if self.diff_map_canvas and total_lines > 0 and canvas_height > 0:
                    y = (a_index / total_lines) * canvas_height
                    self.diff_map_canvas.create_line(
                        0, y, 20, y, fill="lightcoral", width=2, tags="diff_line"
                    )
                a_index += 1
            elif code == "+":
                added_lines += 1
                start_pos = f"{b_index}.0"
                end_pos = f"{b_index}.end"
                self.file_view_b.tag_add("difference", start_pos, end_pos)
                if self.diff_map_canvas and total_lines > 0 and canvas_height > 0:
                    y = (b_index / total_lines) * canvas_height
                    self.diff_map_canvas.create_line(
                        0, y, 20, y, fill="lightblue", width=2, tags="diff_line"
                    )
                b_index += 1

        self.status_a.set(f"Lines removed: {removed_lines}")
        self.status_b.set(f"Lines added: {added_lines}")

        # Ensure the scroll marker is always drawn on top of the diff lines
        if self.diff_map_canvas:
            self.diff_map_canvas.tag_raise("scroll_marker")

    # ==========================================================================
    # SCROLLING METHODS
    # ==========================================================================

    def _setup_synchronized_scrolling(self):
        """Link the scrollbars of the two text widgets for synchronized scrolling."""
        if not (
            self.file_view_a
            and self.file_view_b
            and self.v_scrollbar_a
            and self.v_scrollbar_b
            and self.h_scrollbar_a
            and self.h_scrollbar_b
        ):
            return

        # Assign local variables to avoid Pylance warnings about optional members
        file_view_a, file_view_b = self.file_view_a, self.file_view_b
        v_scrollbar_a, v_scrollbar_b = self.v_scrollbar_a, self.v_scrollbar_b
        h_scrollbar_a, h_scrollbar_b = self.h_scrollbar_a, self.h_scrollbar_b

        def _on_y_scroll(*args):
            """Handle all vertical scroll events."""
            file_view_a.yview(*args)
            file_view_b.yview(*args)

        def _on_y_view_change(*args):
            """Update scrollbars when text view changes."""
            v_scrollbar_a.set(*args)
            v_scrollbar_b.set(*args)
            self._update_scroll_marker(float(args[0]), float(args[1]))

        def _on_x_scroll(*args):
            """Handle all horizontal scroll events."""
            file_view_a.xview(*args)
            file_view_b.xview(*args)

        def _on_x_view_change(*args):
            """Update scrollbars when text view's horizontal position changes."""
            h_scrollbar_a.set(*args)
            h_scrollbar_b.set(*args)

        # Configure vertical scrolling
        v_scrollbar_a.config(command=_on_y_scroll)
        v_scrollbar_b.config(command=_on_y_scroll)
        file_view_a.config(yscrollcommand=_on_y_view_change)
        file_view_b.config(yscrollcommand=_on_y_view_change)

        # Configure horizontal scrolling
        h_scrollbar_a.config(command=_on_x_scroll)
        h_scrollbar_b.config(command=_on_x_scroll)
        file_view_a.config(xscrollcommand=_on_x_view_change)
        file_view_b.config(xscrollcommand=_on_x_view_change)

        # Bind mouse wheel to scroll both text widgets
        def _on_mouse_wheel(event):
            """Handle mouse wheel scrolling for both text widgets."""
            # Determine scroll direction and amount
            delta = -1 * (event.delta / 120) if event.delta != 0 else 0

            # Handle touchpad scrolling (some systems use event.num)
            if event.num in (4, 5):
                delta = -1 if event.num == 4 else 1

            # Scroll both text widgets
            file_view_a.yview_scroll(int(delta), "units")
            file_view_b.yview_scroll(int(delta), "units")

            # Prevent default behavior
            return "break"

        # Bind mouse wheel events to both text widgets
        for widget in [file_view_a, file_view_b]:
            widget.bind("<MouseWheel>", _on_mouse_wheel, add=True)
            widget.bind("<Button-4>", _on_mouse_wheel, add=True)  # Linux scroll up
            widget.bind("<Button-5>", _on_mouse_wheel, add=True)  # Linux scroll down

            # Also bind to the frame containing the text widget for when focus is elsewhere
            if widget.master:
                widget.master.bind("<MouseWheel>", _on_mouse_wheel, add=True)
                widget.master.bind("<Button-4>", _on_mouse_wheel, add=True)
                widget.master.bind("<Button-5>", _on_mouse_wheel, add=True)

        # Bind to the main window as well to catch events anywhere in the application
        self.root.bind("<MouseWheel>", _on_mouse_wheel, add=True)
        self.root.bind("<Button-4>", _on_mouse_wheel, add=True)
        self.root.bind("<Button-5>", _on_mouse_wheel, add=True)

    def _update_scroll_marker(self, first_visible_fraction, last_visible_fraction):
        """Updates the position and height of the scroll marker on the diff map."""
        if self.diff_map_canvas and self.scroll_marker_id:
            canvas_height = self.diff_map_canvas.winfo_height()
            if (
                canvas_height == 0
            ):  # Avoid division by zero or incorrect calculations if canvas is not yet rendered
                return

            y1 = first_visible_fraction * canvas_height
            y2 = last_visible_fraction * canvas_height

            # Ensure minimum height for visibility, e.g., 2 pixels
            if y2 - y1 < 2:
                y2 = y1 + 2
                if y2 > canvas_height:  # Adjust if marker goes past bottom
                    y1 = canvas_height - 2
            self.diff_map_canvas.coords(
                self.scroll_marker_id, 1, y1, 19, y2
            )  # Update marker coordinates

    # ==========================================================================
    # UTILITY METHODS
    # ==========================================================================

    def _get_mono_font(self):
        """Returns a suitable monospace font family based on the current OS."""
        font_families = tkfont.families()

        preferred_fonts = []

        if sys.platform == "win32":
            # Windows
            preferred_fonts = ["Consolas", "Courier New", "Lucida Console"]
        elif sys.platform == "darwin":
            # macOS
            preferred_fonts = ["Menlo", "Monaco", "Courier New"]
        else:
            # Linux and other Unix-like systems
            preferred_fonts = ["DejaVu Sans Mono", "Liberation Mono", "Courier New"]

        for font in preferred_fonts:
            if font in font_families:
                return (font, 12)

        # Fallback to a generic monospace font
        return ("Courier", 12)

    # ==========================================================================
    # EVENT HANDLERS
    # ==========================================================================

    def _on_closing(self):
        """Handle window close event."""
        self.save_config()
        self.root.destroy()


def main():
    """Main entry point for the application."""
    root = tk.Tk()
    GCompare(root)
    root.mainloop()


if __name__ == "__main__":
    main()
