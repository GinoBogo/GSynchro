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
import tempfile
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox
from typing import Dict, List, Tuple, Optional, cast


# ============================================================================
# CONSTANTS
# ============================================================================

CONFIG_FILE = "g_compare.json"
HISTORY_LENGTH = 10
SCROLL_MARKER_WIDTH = 40
MIN_WINDOW_WIDTH = 1024
MIN_WINDOW_HEIGHT = 768


# ============================================================================
# MAIN APPLICATION CLASS
# ============================================================================


class GCompare:
    """Main application class for GCompare file comparison tool."""

    # ========================================================================
    # INITIALIZATION METHODS
    # ========================================================================

    def __init__(self, root: tk.Tk):
        """Initialize the GCompare application.

        Args:
            root: The main Tkinter root window
        """
        self.root = root

        # File variables
        self.file_a = tk.StringVar()
        self.file_b = tk.StringVar()
        self.file_a_history: List[str] = []
        self.file_b_history: List[str] = []

        # Content variables
        self.content_a = tk.StringVar()
        self.content_b = tk.StringVar()

        # UI components
        self.file_view_a: Optional[tk.Text] = None
        self.file_view_b: Optional[tk.Text] = None
        self.panel_a: Optional[ttk.LabelFrame] = None
        self.panel_b: Optional[ttk.LabelFrame] = None
        self.diff_map_canvas: Optional[tk.Canvas] = None
        self.scroll_marker_id: Optional[int] = None
        self.v_scrollbar_a: Optional[ttk.Scrollbar] = None
        self.v_scrollbar_b: Optional[ttk.Scrollbar] = None
        self.h_scrollbar_a: Optional[ttk.Scrollbar] = None
        self.h_scrollbar_b: Optional[ttk.Scrollbar] = None

        # Status variables
        self.status_a = tk.StringVar()
        self.status_b = tk.StringVar()

        self._font_families: Optional[Tuple[str, ...]] = None
        # Variables to manage scroll marker dragging
        self._marker_drag_start_y: Optional[float] = None
        self._marker_initial_scroll_fraction = 0.0

        # Initialize application
        self.load_config()
        self._init_window()
        self._setup_ui()

        # Load files from command line arguments
        if len(sys.argv) > 1:
            self._load_file_a(sys.argv[1])
        if len(sys.argv) > 2:
            self._load_file_b(sys.argv[2])

        # Compare files if both were provided via command line
        if len(sys.argv) > 2:
            self._compare_files()

    def _init_window(self):
        """Initialize main window properties."""
        self.root.title("GCompare - File Comparison Tool")
        self.root.minsize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _setup_ui(self):
        """Set up the main user interface."""
        self._setup_styles()

        # Create main layout
        main_frame = self._create_main_frame()
        control_frame = self._create_control_frame(main_frame)
        panels_frame = self._create_panels_frame(main_frame)

        # Create UI components
        self._create_control_buttons(control_frame)
        self._create_file_panels(panels_frame)
        self._create_status_bar(main_frame)

        # Setup synchronized scrolling
        self._setup_synchronized_scrolling()

        # Set initial status
        self.status_a.set("by Gino Bogo")

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

        # Configure monospace font
        font_tuple = self._get_mono_font()
        style.configure("TText", font=font_tuple)

    # ========================================================================
    # CONFIGURATION METHODS
    # ========================================================================

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

            # File A history
            if "FILE_A_HISTORY" in config:
                self.file_a_history = config["FILE_A_HISTORY"]
                if self.file_a_history:
                    self.file_a.set(self.file_a_history[0])

            # File B history
            if "FILE_B_HISTORY" in config:
                self.file_b_history = config["FILE_B_HISTORY"]
                if self.file_b_history:
                    self.file_b.set(self.file_b_history[0])

        except json.JSONDecodeError:
            print(f"Warning: Could not parse {CONFIG_FILE}. Using defaults.")

    def save_config(self):
        """Save configuration to file."""
        # Update file history
        if self.file_a.get():
            self._update_file_history("A", self.file_a.get())
        if self.file_b.get():
            self._update_file_history("B", self.file_b.get())

        config = {
            "WINDOW": {"geometry": self.root.geometry()},
            "FILE_A_HISTORY": self.file_a_history,
            "FILE_B_HISTORY": self.file_b_history,
        }

        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)

    def _is_temporary_path(self, path: str) -> bool:
        """Check if a path is a temporary file or directory.

        Args:
            path: Path to check

        Returns:
            True if path appears to be temporary
        """
        if not path:
            return False

        # Check for common temporary directory patterns
        temp_patterns = [
            "/tmp/",
            "\\tmp\\",
            "/temp/",
            "\\temp\\",
            tempfile.gettempdir(),
        ]

        path_normalized = os.path.normpath(path)
        for pattern in temp_patterns:
            if pattern in path_normalized:
                return True

        # Check for tempfile.NamedTemporaryFile patterns
        if "tmp" in path_normalized and (
            path_normalized.startswith("/tmp/")
            or path_normalized.startswith("\\tmp\\")
            or "tmp" in os.path.basename(path_normalized)
        ):
            return True

        return False

    def _update_file_history(self, panel_name: str, new_path: str):
        """Update recent files list for specified panel.

        Args:
            panel_name: Either "A" or "B"
            new_path: Path to add to history
        """
        if not new_path or self._is_temporary_path(new_path):
            return

        history_list = self.file_a_history if panel_name == "A" else self.file_b_history

        # Remove duplicate if exists
        if new_path in history_list:
            history_list.remove(new_path)

        # Add to beginning of list
        history_list.insert(0, new_path)

        # Trim to max history length
        del history_list[HISTORY_LENGTH:]

    # ========================================================================
    # UI CREATION METHODS
    # ========================================================================

    def _create_main_frame(self) -> ttk.Frame:
        """Create the main application frame."""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=tk.NSEW)

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(1, weight=1)

        return main_frame

    def _create_control_frame(self, main_frame: ttk.Frame) -> ttk.Frame:
        """Create control buttons frame."""
        control_frame = ttk.Frame(main_frame)
        control_frame.grid(row=0, column=0, columnspan=3, sticky=tk.EW, pady=5)
        return control_frame

    def _create_control_buttons(self, control_frame: ttk.Frame):
        """Create the main control buttons."""
        button_container = ttk.Frame(control_frame)
        button_container.pack(expand=True)

        # Button definitions
        buttons = [
            ("Compare", self._compare_files, None),
            ("Reload", self._reload_files, None),
        ]

        for text, command, color in buttons:
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

    def _create_panels_frame(self, main_frame: ttk.Frame) -> ttk.Frame:
        """Create panels container."""
        panels_frame = ttk.Frame(main_frame)
        panels_frame.grid(row=1, column=0, columnspan=3, sticky=tk.NSEW)

        panels_frame.columnconfigure(0, weight=1)
        panels_frame.columnconfigure(1, weight=0)  # For diff map
        panels_frame.columnconfigure(2, weight=1)
        panels_frame.rowconfigure(0, weight=1)

        return panels_frame

    def _create_file_panels(self, panels_frame: ttk.Frame):
        """Create both file panels and diff map."""
        # Panel A
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

        # Panel B
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

        # Create panel A
        self._create_single_panel(panels_frame, panel_a_config)

        # Create diff map canvas
        self.diff_map_canvas = tk.Canvas(
            panels_frame, width=SCROLL_MARKER_WIDTH, bg="#FFFFFF"
        )
        self.diff_map_canvas.grid(row=0, column=1, sticky="ns", pady=(10, 0))

        # Create scroll marker
        self.scroll_marker_id = self.diff_map_canvas.create_rectangle(
            2,
            2,
            SCROLL_MARKER_WIDTH - 1,
            3,
            fill="#808080",
            outline="black",
            width=1,
            stipple="gray12",
            tags="scroll_marker",
        )

        # Bind events to the scroll marker for dragging functionality
        if self.scroll_marker_id:
            self.diff_map_canvas.tag_bind(
                "scroll_marker", "<ButtonPress-1>", self._on_marker_press
            )
            self.diff_map_canvas.tag_bind(
                "scroll_marker", "<B1-Motion>", self._on_marker_drag
            )
            self.diff_map_canvas.tag_bind(
                "scroll_marker", "<ButtonRelease-1>", self._on_marker_release
            )
            self.diff_map_canvas.tag_bind(
                "scroll_marker", "<Enter>", self._on_marker_enter
            )
            self.diff_map_canvas.tag_bind(
                "scroll_marker", "<Leave>", self._on_marker_leave
            )

        self.diff_map_canvas.bind("<Configure>", self._compare_files)

        # Create panel B
        self._create_single_panel(panels_frame, panel_b_config)

    def _create_single_panel(self, parent: ttk.Frame, config: Dict):
        """Create a single file panel.

        Args:
            parent: Parent widget
            config: Dictionary containing panel configuration
        """
        panel = ttk.LabelFrame(parent, text=config["title"], padding="5")
        panel.grid(
            row=0,
            column=config["column"],
            sticky=tk.NSEW,
            padx=config["padx"],
        )

        panel.columnconfigure(0, weight=0)  # For Path label
        panel.columnconfigure(1, weight=1)  # For combobox
        panel.columnconfigure(2, weight=0)  # For Open button
        panel.columnconfigure(3, weight=0)  # For Save button
        panel.columnconfigure(4, weight=0)  # For vertical scrollbar
        panel.rowconfigure(1, weight=1)  # For text area

        # Path label
        ttk.Label(panel, text="Path:").grid(
            row=0, column=0, padx=5, pady=5, sticky=tk.W
        )

        # File path combobox
        path_combobox = ttk.Combobox(
            panel,
            textvariable=config["file_var"],
            values=config["file_history"],
        )
        path_combobox.grid(row=0, column=1, padx=5, pady=5, sticky=tk.EW)

        # Load button
        ttk.Button(
            panel,
            text="Open",
            command=config["open_command"],
            cursor="hand2",
            style=f"{config['button_color']}.TButton",
        ).grid(row=0, column=2, padx=5, pady=5, sticky=tk.E)

        # Save button
        ttk.Button(
            panel,
            text="Save",
            command=config["save_command"],
            cursor="hand2",
            style=f"{config['button_color']}.TButton",
        ).grid(row=0, column=3, padx=5, pady=5, sticky=tk.E)

        # Text area
        text_area = tk.Text(panel, wrap=tk.NONE, state=tk.NORMAL)
        text_area.grid(row=1, column=0, columnspan=4, pady=(10, 0), sticky=tk.NSEW)

        # Bind modified event
        text_area.bind(
            "<<Modified>>",
            lambda e, p=panel, t=config["title"]: self._on_text_modified(e, p, t),
        )

        # Scrollbars
        v_scrollbar = ttk.Scrollbar(panel, orient=tk.VERTICAL, command=text_area.yview)
        text_area.configure(yscrollcommand=v_scrollbar.set)
        v_scrollbar.grid(row=1, column=4, pady=(10, 0), sticky=tk.NS)

        h_scrollbar = ttk.Scrollbar(
            panel, orient=tk.HORIZONTAL, command=text_area.xview
        )
        text_area.configure(xscrollcommand=h_scrollbar.set)
        h_scrollbar.grid(row=2, column=0, columnspan=4, sticky=tk.EW)

        # Store references
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

    def _create_status_bar(self, parent: ttk.Frame):
        """Create status bar with legends."""
        status_frame = ttk.Frame(parent, relief="flat", padding="2")
        status_frame.grid(row=2, column=0, columnspan=3, sticky=tk.EW, pady=(5, 0))

        status_frame.columnconfigure(0, weight=1)
        status_frame.columnconfigure(1, weight=1)

        # Left status (File A)
        left_status_container = ttk.Frame(status_frame)
        left_status_container.grid(row=0, column=0, sticky=tk.W, padx=0)

        # Removed lines legend
        removed_square = tk.Label(
            left_status_container,
            bg="lightcoral",
            width=2,
            height=1,
            relief="solid",
            bd=1,
        )
        removed_square.pack(side=tk.LEFT, padx=(6, 4))

        # Removed empty lines legend
        empty_square = tk.Label(
            left_status_container,
            bg="yellow",
            width=2,
            height=1,
            relief="solid",
            bd=1,
        )
        empty_square.pack(side=tk.LEFT, padx=(4, 4))

        status_label_left = ttk.Label(
            left_status_container, textvariable=self.status_a, anchor=tk.W
        )
        status_label_left.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Right status (File B)
        right_status_container = ttk.Frame(status_frame)
        right_status_container.grid(row=0, column=1, sticky=tk.E, padx=0)

        status_label_right = ttk.Label(
            right_status_container, textvariable=self.status_b, anchor=tk.E
        )
        status_label_right.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Added lines legend
        added_square = tk.Label(
            right_status_container,
            bg="lightblue",
            width=2,
            height=1,
            relief="solid",
            bd=1,
        )
        added_square.pack(side=tk.LEFT, padx=(4, 6))

        # Added empty lines legend
        empty_square_b = tk.Label(
            right_status_container,
            bg="yellow",
            width=2,
            height=1,
            relief="solid",
            bd=1,
        )
        empty_square_b.pack(side=tk.LEFT, padx=(4, 4))

    # ========================================================================
    # FILE OPERATIONS
    # ========================================================================

    def _open_file_a(self):
        """Open file dialog for File A."""
        self._open_file("A")

    def _open_file_b(self):
        """Open file dialog for File B."""
        self._open_file("B")

    def _open_file(self, panel_name: str):
        """Open file dialog and load file.

        Args:
            panel_name: Either "A" or "B"
        """
        initial_dir = None
        current_path = ""

        if panel_name == "A":
            current_path = self.file_a.get()
        else:
            current_path = self.file_b.get()

        if current_path:
            if os.path.isdir(current_path):
                initial_dir = current_path
            else:
                initial_dir = os.path.dirname(current_path)

        file_path = filedialog.askopenfilename(initialdir=initial_dir)
        if file_path:
            if panel_name == "A":
                self._load_file_a(file_path)
            else:
                self._load_file_b(file_path)

    def _reload_files(self):
        """Reload both files (prompt save if dirty)."""
        # Check File A for unsaved changes
        if self.panel_a and self.panel_a.cget("text").endswith("*"):
            response = messagebox.askyesnocancel(
                "Unsaved Changes",
                "File A has unsaved changes. Do you want to save them before reloading?",
            )
            if response is True:  # Yes
                self._save_file_a()
            elif response is None:  # Cancel
                return

        # Check File B for unsaved changes
        if self.panel_b and self.panel_b.cget("text").endswith("*"):
            response = messagebox.askyesnocancel(
                "Unsaved Changes",
                "File B has unsaved changes. Do you want to save them before reloading?",
            )
            if response is True:  # Yes
                self._save_file_b()
            elif response is None:  # Cancel
                return

        # Clear the diff map visualization
        self._clear_diff_map()

        # Reload files
        if self.file_a.get():
            self._load_file_a(self.file_a.get())
        if self.file_b.get():
            self._load_file_b(self.file_b.get())

    def _save_file_a(self):
        """Save File A."""
        if self.file_view_a:
            self._save_file(self.file_a.get(), self.file_view_a, "A")

    def _save_file_b(self):
        """Save File B."""
        if self.file_view_b:
            self._save_file(self.file_b.get(), self.file_view_b, "B")

    def _save_file(self, file_path: str, text_widget: tk.Text, panel_name: str):
        """Write text widget content to disk.

        Args:
            file_path: Path to save to
            text_widget: Text widget containing content
            panel_name: Either "A" or "B"
        """
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

        # Save confirmation dialog - RESTORED
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

    def _load_file_a(self, file_path: str):
        """Load file into File A view.

        Args:
            file_path: Path to file to load
        """
        self._load_file(
            file_path,
            "A",
            self.file_a,
            self.content_a,
            self.file_view_a,
            self.panel_a,
            self.status_a,
        )

    def _load_file_b(self, file_path: str):
        """Load file into File B view.

        Args:
            file_path: Path to file to load
        """
        self._load_file(
            file_path,
            "B",
            self.file_b,
            self.content_b,
            self.file_view_b,
            self.panel_b,
            self.status_b,
        )

    def _load_file(
        self,
        file_path: str,
        panel_name: str,
        file_var: tk.StringVar,
        content_var: tk.StringVar,
        text_view: Optional[tk.Text],
        panel_widget: Optional[ttk.LabelFrame],
        status_var: tk.StringVar,
    ):
        """Load file content into specified panel.

        Args:
            file_path: Path to file to load
            panel_name: Either "A" or "B"
            file_var: StringVar to store file path
            content_var: StringVar to store content
            text_view: Text widget to display content
            panel_widget: Panel widget to update title
            status_var: Status variable to update
        """
        try:
            with open(file_path, "r", encoding="utf-8") as file:
                content = file.read()

                # Update history
                self._update_file_history(panel_name, file_path)

                # Update variables
                file_var.set(file_path)
                content_var.set(content)

                # Update text view
                if text_view:
                    text_view.delete("1.0", tk.END)
                    text_view.insert("1.0", content)
                    text_view.edit_modified(False)

                # Update panel title
                if panel_widget:
                    panel_widget.config(text=f"File {panel_name}")

                # Update status
                line_count = len(content.splitlines())
                char_count = len(content)
                status_var.set(f"{line_count} lines, {char_count} characters")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load file: {e}")

    # ========================================================================
    # TEXT AND COMPARISON METHODS
    # ========================================================================

    def _on_text_modified(
        self, event: tk.Event, panel_widget: ttk.LabelFrame, original_title: str
    ):
        """Mark panel as modified when its text changes.

        Args:
            event: Tkinter event
            panel_widget: Panel to mark as modified
            original_title: Original panel title
        """
        try:
            # Cast the widget to Text since we know it's a Text widget
            text_widget = cast(tk.Text, event.widget)
        except (AttributeError, TypeError):
            return

        if panel_widget and text_widget.edit_modified():
            panel_widget.config(text=f"{original_title}*")
            text_widget.edit_modified(False)

    def _compare_files(self, event=None):
        """Compare the two files and highlight differences.

        Args:
            event: Optional Tk event (for bindings)
        """
        if not self.file_view_a or not self.file_view_b:
            messagebox.showwarning(
                "Warning", "Please load both files before comparing."
            )
            return

        # Compute differences
        diff_result = self._compute_diff()

        # Apply visual changes
        self._apply_highlights(diff_result)
        self._update_diff_map(diff_result)
        self._update_status(diff_result)

    def _compute_diff(self) -> Dict:
        """Compute differences between the two files.

        Returns:
            dict: Contains diff lines, line counts, and content information
        """
        # Get content
        lines_a = (
            self.file_view_a.get("1.0", tk.END).splitlines() if self.file_view_a else []
        )
        lines_b = (
            self.file_view_b.get("1.0", tk.END).splitlines() if self.file_view_b else []
        )

        # Perform comparison
        differ = difflib.Differ()
        diff_lines = list(differ.compare(lines_a, lines_b))

        # Initialize counters
        a_index = 1
        b_index = 1

        # Prepare diff information
        diff_info = {
            "lines_a": lines_a,
            "lines_b": lines_b,
            "diff_lines": diff_lines,
            "added_lines": 0,
            "removed_lines": 0,
            "added_empty_lines": 0,
            "removed_empty_lines": 0,
            "total_lines": max(len(lines_a), len(lines_b)),
            "changes": [],  # List of (type, line_num, is_empty) tuples
        }

        # Helper function to check if line is empty (only whitespace)
        def is_empty_line(line: str) -> bool:
            """Check if a line is empty or contains only whitespace.

            Args:
                line: Line to check

            Returns:
                True if line is empty or contains only whitespace
            """
            return len(line.strip()) == 0

        # Process diff results
        for line in diff_lines:
            if not line:
                continue

            code = line[0]
            line_content = line[2:] if len(line) > 2 else ""
            is_empty = is_empty_line(line_content)

            if code == " ":
                a_index += 1
                b_index += 1
            elif code == "-":
                diff_info["removed_lines"] += 1
                if is_empty:
                    diff_info["removed_empty_lines"] += 1
                    diff_info["changes"].append(("removed_empty", a_index, True))
                else:
                    diff_info["changes"].append(("removed", a_index, False))
                a_index += 1
            elif code == "+":
                diff_info["added_lines"] += 1
                if is_empty:
                    diff_info["added_empty_lines"] += 1
                    diff_info["changes"].append(("added_empty", b_index, True))
                else:
                    diff_info["changes"].append(("added", b_index, False))
                b_index += 1

        return diff_info

    def _apply_highlights(self, diff_result: Dict):
        """Apply highlighting to the text widgets based on diff results.

        Args:
            diff_result: Dictionary containing diff information
        """
        # Clear existing tags
        if self.file_view_a:
            self.file_view_a.tag_remove("removed", "1.0", tk.END)
            self.file_view_a.tag_remove("removed_empty", "1.0", tk.END)
        if self.file_view_b:
            self.file_view_b.tag_remove("added", "1.0", tk.END)
            self.file_view_b.tag_remove("added_empty", "1.0", tk.END)

        # Configure highlight tags
        if self.file_view_a:
            self.file_view_a.tag_configure("removed", background="lightcoral")
            self.file_view_a.tag_configure("removed_empty", background="yellow")
        if self.file_view_b:
            self.file_view_b.tag_configure("added", background="lightblue")
            self.file_view_b.tag_configure("added_empty", background="yellow")

        # Apply highlights based on diff results
        for change_info in diff_result["changes"]:
            change_type, line_num, is_empty = change_info

            if change_type in ("removed", "removed_empty") and self.file_view_a:
                start_pos = f"{line_num}.0"
                end_pos = f"{line_num}.end"
                tag_name = (
                    "removed_empty" if change_type == "removed_empty" else "removed"
                )
                self.file_view_a.tag_add(tag_name, start_pos, end_pos)
            elif change_type in ("added", "added_empty") and self.file_view_b:
                start_pos = f"{line_num}.0"
                end_pos = f"{line_num}.end"
                tag_name = "added_empty" if change_type == "added_empty" else "added"
                self.file_view_b.tag_add(tag_name, start_pos, end_pos)

    def _update_diff_map(self, diff_result: Dict):
        """Update the diff map visualization.

        Args:
            diff_result: Dictionary containing diff information
        """
        if not self.diff_map_canvas or not self.file_view_a:
            return

        # Clear existing diff map
        self.diff_map_canvas.delete("diff_line")

        # Update scroll marker
        first, last = self.file_view_a.yview()
        self._update_scroll_marker(float(first), float(last))

        # Check if we have content to visualize
        total_lines = diff_result["total_lines"]
        canvas_height = self.diff_map_canvas.winfo_height()

        if total_lines <= 0 or canvas_height <= 0:
            return

        # Draw diff indicators
        canvas_width = self.diff_map_canvas.winfo_width()
        half_width = canvas_width / 2

        for change_info in diff_result["changes"]:
            change_type, line_num, is_empty = change_info

            if line_num <= total_lines:
                y_start = ((line_num - 1) / total_lines) * canvas_height
                line_height = max(1, canvas_height / total_lines)
                y_end = y_start + line_height

                # Determine color based on change type
                if change_type in ("removed", "removed_empty"):
                    fill_color = (
                        "yellow" if change_type == "removed_empty" else "lightcoral"
                    )
                    self.diff_map_canvas.create_rectangle(
                        2,
                        y_start,
                        half_width,
                        y_end,
                        fill=fill_color,
                        outline="",
                        tags="diff_line",
                    )
                elif change_type in ("added", "added_empty"):
                    fill_color = (
                        "yellow" if change_type == "added_empty" else "lightblue"
                    )
                    self.diff_map_canvas.create_rectangle(
                        half_width,
                        y_start,
                        canvas_width - 2,
                        y_end,
                        fill=fill_color,
                        outline="",
                        tags="diff_line",
                    )

        # Ensure scroll marker is on top
        if self.scroll_marker_id:
            self.diff_map_canvas.tag_raise("scroll_marker")

    def _update_status(self, diff_result: Dict):
        """Update the status bar with diff information.

        Args:
            diff_result: Dictionary containing diff information
        """
        # Calculate non-empty changes
        non_empty_removed = (
            diff_result["removed_lines"] - diff_result["removed_empty_lines"]
        )
        non_empty_added = diff_result["added_lines"] - diff_result["added_empty_lines"]

        # Build concise status strings
        if diff_result["removed_lines"] > 0:
            if diff_result["removed_empty_lines"] > 0:
                self.status_a.set(
                    f"Removed {non_empty_removed} lines / {diff_result['removed_empty_lines']} empty lines"
                )
            else:
                self.status_a.set(f"Removed {non_empty_removed} lines")
        else:
            self.status_a.set("File A")

        if diff_result["added_lines"] > 0:
            if diff_result["added_empty_lines"] > 0:
                self.status_b.set(
                    f"Added {non_empty_added} lines / {diff_result['added_empty_lines']} empty lines"
                )
            else:
                self.status_b.set(f"Added {non_empty_added} lines")
        else:
            self.status_b.set("File B")

    # ========================================================================
    # SCROLLING METHODS
    # ========================================================================

    def _setup_synchronized_scrolling(self):
        """Link scrolling between both panels."""
        if not (
            self.file_view_a
            and self.file_view_b
            and self.v_scrollbar_a
            and self.v_scrollbar_b
            and self.h_scrollbar_a
            and self.h_scrollbar_b
        ):
            return

        # Local references for clarity
        file_view_a, file_view_b = self.file_view_a, self.file_view_b
        v_scrollbar_a, v_scrollbar_b = self.v_scrollbar_a, self.v_scrollbar_b
        h_scrollbar_a, h_scrollbar_b = self.h_scrollbar_a, self.h_scrollbar_b

        def _on_y_scroll(*args):
            """Handle vertical scroll events."""
            file_view_a.yview(*args)
            file_view_b.yview(*args)

        def _on_y_view_change(*args):
            """Update scrollbars when vertical view changes."""
            v_scrollbar_a.set(*args)
            v_scrollbar_b.set(*args)
            if self.file_view_a:
                first, last = self.file_view_a.yview()
                self._update_scroll_marker(float(first), float(last))

        def _on_x_scroll(*args):
            """Handle horizontal scroll events."""
            file_view_a.xview(*args)
            file_view_b.xview(*args)

        def _on_x_view_change(*args):
            """Update scrollbars when horizontal view changes."""
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

        # Bind mouse wheel events
        def _on_mouse_wheel(event: tk.Event):
            """Handle mouse wheel scrolling.

            Args:
                event: Mouse wheel event
            """
            # Determine scroll direction
            delta = -1 * (event.delta / 120) if event.delta != 0 else 0

            # Handle touchpad scrolling
            if event.num in (4, 5):
                delta = -1 if event.num == 4 else 1

            # Scroll both text widgets
            file_view_a.yview_scroll(int(delta), "units")
            file_view_b.yview_scroll(int(delta), "units")

            return "break"

        # Bind to text widgets
        for widget in [file_view_a, file_view_b]:
            if widget:
                widget.bind("<MouseWheel>", _on_mouse_wheel, add=True)
                widget.bind("<Button-4>", _on_mouse_wheel, add=True)  # Linux scroll up
                widget.bind(
                    "<Button-5>", _on_mouse_wheel, add=True
                )  # Linux scroll down

                # Bind to parent frames
                if widget.master:
                    widget.master.bind("<MouseWheel>", _on_mouse_wheel, add=True)
                    widget.master.bind("<Button-4>", _on_mouse_wheel, add=True)
                    widget.master.bind("<Button-5>", _on_mouse_wheel, add=True)

        # Bind to root window
        self.root.bind("<MouseWheel>", _on_mouse_wheel, add=True)
        self.root.bind("<Button-4>", _on_mouse_wheel, add=True)
        self.root.bind("<Button-5>", _on_mouse_wheel, add=True)

    def _update_scroll_marker(
        self, first_visible_fraction: float, last_visible_fraction: float
    ):
        """Update diff map scroll marker position.

        Args:
            first_visible_fraction: Fraction of document at top of viewport
            last_visible_fraction: Fraction of document at bottom of viewport
        """
        if self.diff_map_canvas and self.scroll_marker_id:
            canvas_height = self.diff_map_canvas.winfo_height()
            if canvas_height == 0:
                return

            y1 = first_visible_fraction * canvas_height
            y2 = last_visible_fraction * canvas_height

            # Ensure minimum height
            if y2 - y1 < 4:
                y2 = y1 + 4
                if y2 > canvas_height:
                    y1 = canvas_height - 4

            self.diff_map_canvas.coords(
                self.scroll_marker_id, 2, y1 + 2, SCROLL_MARKER_WIDTH - 1, y2 - 3
            )

    def _on_marker_press(self, event: tk.Event):
        """Handle mouse button press on the scroll marker.
        Stores the initial drag position and current scroll fraction.

        Args:
            event: Mouse event
        """
        if not self.diff_map_canvas:
            return

        if not self.file_view_a:
            return

        self._marker_drag_start_y = event.y
        # Get the current scroll fraction of the text widgets
        self._marker_initial_scroll_fraction = self.file_view_a.yview()[0]
        self.diff_map_canvas.config(cursor="hand2")  # Change cursor to a grabbing hand

    def _on_marker_drag(self, event: tk.Event):
        """Handle mouse drag motion on the scroll marker.
        Calculates new scroll position and updates text widgets.

        Args:
            event: Mouse event
        """
        if self._marker_drag_start_y is None:
            return

        if not self.diff_map_canvas:
            return

        dy = event.y - self._marker_drag_start_y
        canvas_height = self.diff_map_canvas.winfo_height()

        if canvas_height == 0:  # Avoid division by zero
            return

        # Calculate the new scroll fraction based on drag movement
        new_fraction = self._marker_initial_scroll_fraction + (dy / canvas_height)

        # Clamp the fraction between 0 and 1 to stay within bounds
        new_fraction = max(0.0, min(1.0, new_fraction))

        # Apply the new scroll position to both text widgets
        if self.file_view_a:
            self.file_view_a.yview_moveto(new_fraction)
        if self.file_view_b:
            self.file_view_b.yview_moveto(new_fraction)

    def _on_marker_release(self, event: tk.Event):
        """Handle mouse button release on the scroll marker.
        Resets the drag state and cursor.

        Args:
            event: Mouse event
        """
        if not self.diff_map_canvas:
            return

        self._marker_drag_start_y = None  # Reset drag state
        self.diff_map_canvas.config(cursor="")  # Reset cursor to default

    def _on_marker_enter(self, event: tk.Event):
        """Change cursor to a hand when entering the scroll marker.

        Args:
            event: Mouse event
        """
        if self.diff_map_canvas:
            self.diff_map_canvas.config(cursor="hand2")

    def _on_marker_leave(self, event: tk.Event):
        """Reset cursor when leaving the scroll marker.

        Args:
            event: Mouse event
        """
        if self.diff_map_canvas:
            self.diff_map_canvas.config(cursor="")

    # ========================================================================
    # UTILITY METHODS
    # ========================================================================

    def _get_mono_font(self) -> Tuple[str, int]:
        """Return a suitable monospace font tuple for the platform.

        Returns:
            Tuple[str, int]: (font_family, font_size)
        """
        if self._font_families is None:
            self._font_families = tkfont.families()
        font_families = self._font_families

        preferred_fonts = []

        if sys.platform == "win32":
            preferred_fonts = ["Consolas", "Courier New", "Lucida Console"]
        elif sys.platform == "darwin":
            preferred_fonts = ["Menlo", "Monaco", "Courier New"]
        else:
            preferred_fonts = ["DejaVu Sans Mono", "Liberation Mono", "Courier New"]

        for font in preferred_fonts:
            if font in font_families:
                return (font, 12)

        # Fallback
        return ("Courier", 12)

    def _clear_diff_map(self):
        """Clear the diff map visualization."""
        if self.diff_map_canvas:
            # Clear all diff lines
            self.diff_map_canvas.delete("diff_line")

            # Reset status to default
            if self.status_a:
                self.status_a.set("by Gino Bogo")
            if self.status_b:
                self.status_b.set("")

            # Clear any text highlighting
            if self.file_view_a:
                self.file_view_a.tag_remove("removed", "1.0", tk.END)
                self.file_view_a.tag_remove("removed_empty", "1.0", tk.END)
            if self.file_view_b:
                self.file_view_b.tag_remove("added", "1.0", tk.END)
                self.file_view_b.tag_remove("added_empty", "1.0", tk.END)

    # ========================================================================
    # EVENT HANDLERS
    # ========================================================================

    def _on_closing(self):
        """Handle window close event."""
        self.save_config()
        self.root.destroy()


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================


def main():
    """Main entry point for the application."""
    root = tk.Tk()
    GCompare(root)
    root.mainloop()


if __name__ == "__main__":
    main()
