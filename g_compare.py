#!/usr/bin/env python3
"""
GCompare - GUI File Comparison Tool

A graphical tool for side-by-side comparison of text files. It highlights
differences in a modern and graphical way, allowing for easy visualization of
changes between two files.

 Author: Gino Bogo
License: MIT
Version: 1.2
"""

from __future__ import annotations

# Standard library imports.
import difflib
import json
import os
import sys
import tempfile

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

from typing import Dict, List, Optional, Tuple

# Personal library imports.
from libs.g_button import GButton
from libs.g_scaling import GScaling
from libs.g_theme import get_theme_colors

# ============================================================================
# CONSTANTS
# ============================================================================

APP_VERSION = "1.2"
CONFIG_FILE = "g_compare.json"
HISTORY_LENGTH = 10
SCROLL_MARKER_WIDTH = 40
MIN_WINDOW_WIDTH = 1024
MIN_WINDOW_HEIGHT = 768
DEFAULT_FONT_FAMILY = "Courier New"
DEFAULT_FONT_SIZE = 12
DEFAULT_SPACING = 5
BUTTON_WIDTH_MAIN = 100
BUTTON_HEIGHT_MAIN = 34
BUTTON_WIDTH_PANEL = 70
BUTTON_HEIGHT_PANEL = 30
LINE_NUMBERS_MIN_WIDTH = 4
MARKER_MIN_HEIGHT = 4
MARKER_PAD_TOP = 2
MARKER_PAD_BOTTOM = 2
CONFIGURE_DEBOUNCE_MS = 150


# ============================================================================
# MAIN APPLICATION CLASS
# ============================================================================


class GCompare:
    """Main application class for GCompare file comparison tool."""

    # =======================================================================
    # INITIALIZATION METHODS
    # =======================================================================

    def __init__(self, root: tk.Tk):
        """Initialize the GCompare application.

        Args:
            root: The main Tkinter root window
        """
        self.root = root

        # File variables.
        self.file_a = tk.StringVar()
        self.file_b = tk.StringVar()
        self.file_a_history: List[str] = []
        self.file_b_history: List[str] = []

        # UI components.
        self.text_view_a: Optional[tk.Text] = None
        self.text_view_b: Optional[tk.Text] = None
        self.panel_a: Optional[ttk.LabelFrame] = None
        self.panel_b: Optional[ttk.LabelFrame] = None
        self.diff_map_canvas: Optional[tk.Canvas] = None
        self.scroll_marker_id: Optional[int] = None
        self.v_scrollbar_a: Optional[ttk.Scrollbar] = None
        self.v_scrollbar_b: Optional[ttk.Scrollbar] = None
        self.h_scrollbar_a: Optional[ttk.Scrollbar] = None
        self.h_scrollbar_b: Optional[ttk.Scrollbar] = None
        self.line_numbers_a: Optional[tk.Text] = None
        self.line_numbers_b: Optional[tk.Text] = None
        self.path_combobox_a: Optional[ttk.Combobox] = None
        self.path_combobox_b: Optional[ttk.Combobox] = None

        # Status variables.
        self.status_a = tk.StringVar()
        self.status_b = tk.StringVar()

        self._font_families: Optional[Tuple[str, ...]] = None
        self._configure_after_id: Optional[str] = None

        # Options.
        self.options = {
            "font_family": DEFAULT_FONT_FAMILY,
            "font_size": DEFAULT_FONT_SIZE,
            "show_line_numbers": True,
            "wrap_lines": False,
            "tab_size": 4,
            "auto_compare": True,
            "ignore_whitespace": False,
            "ignore_case": False,
            "highlight_current_line": False,
        }

        # Variables to manage scroll marker dragging.
        self._marker_drag_start_y: Optional[float] = None
        self._marker_initial_scroll_fraction = 0.0

        # Diff navigation state.
        self._diff_changes: List[Tuple[str, int, int, bool]] = []
        self._diff_blocks: List[Tuple[int, int]] = []
        self._diff_block_index = -1
        self._diff_len_a = 0
        self._diff_len_b = 0

        self.colors = get_theme_colors()

        # Initialize application.
        self.load_config()
        self._init_window()
        self._setup_ui()

        # Load files from command line arguments.
        if len(sys.argv) > 1:
            self.load_file_a(sys.argv[1])
        if len(sys.argv) > 2:
            self.load_file_b(sys.argv[2])

        # Compare files if both were provided via command line.
        if len(sys.argv) > 2:
            self.compare_files()

    def _init_window(self):
        """Initialize main window properties."""
        self.root.title(f"GCompare - File Comparison Tool {APP_VERSION}")
        self.root.minsize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _setup_ui(self):
        """Set up the main user interface."""
        self._update_font_style()
        # Create main layout.
        main_frame = self._create_main_frame()
        control_frame = self._create_control_frame(main_frame)
        panels_frame = self._create_panels_frame(main_frame)

        # Create UI components.
        self._create_control_buttons(control_frame)
        self._create_file_panels(panels_frame)
        self._create_status_bar(main_frame)

        # Setup synchronized scrolling.
        self._setup_synchronized_scrolling()

        # Set initial status.
        self.status_a.set("by Gino Bogo")

    def _create_main_frame(self) -> ttk.Frame:
        """Create the main application frame.

        Returns:
            Main application frame
        """
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=tk.NSEW)

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(1, weight=1)

        return main_frame

    def _create_control_frame(self, parent: ttk.Frame) -> ttk.Frame:
        """Create control buttons frame.

        Args:
            parent: Parent frame

        Returns:
            Control frame
        """
        control_frame = ttk.Frame(parent)
        control_frame.grid(
            row=0,
            column=0,
            columnspan=3,
            sticky=tk.EW,
            pady=GScaling.scale_pixels(DEFAULT_SPACING, parent),
        )
        return control_frame

    def _create_control_buttons(self, parent: ttk.Frame):
        """Create the main control buttons.

        Args:
            parent: Parent frame
        """
        button_container = ttk.Frame(parent)
        button_container.pack(expand=True)

        # Button definitions.
        buttons = [
            ("Compare", self.compare_files, "secondary"),
            ("Reload", self.reload_files, "secondary"),
            ("Prev ▲", self._go_to_prev_change, "lightgray"),
            ("Next ▼", self._go_to_next_change, "lightgold"),
            ("Options", self.show_options_dialog, "secondary"),
        ]

        spacing = GScaling.scale_pixels(DEFAULT_SPACING, self.root)

        for text, command, color in buttons:
            btn_colors = self.colors["buttons"].get(
                color, self.colors["buttons"]["default"]
            )
            GButton(
                button_container,
                text=text,
                command=command,
                width=BUTTON_WIDTH_MAIN,
                height=BUTTON_HEIGHT_MAIN,
                **btn_colors,
            ).pack(
                side=tk.LEFT,
                padx=spacing,
                pady=spacing,
            )

    def _go_to_next_change(self):
        """Move both text views to the next change block."""
        if not self._diff_blocks:
            return

        if self._diff_block_index >= len(self._diff_blocks) - 1:
            self._diff_block_index = 0
        else:
            self._diff_block_index += 1

        self._goto_change(self._diff_block_index)

    def _go_to_prev_change(self):
        """Move both text views to the previous change block."""
        if not self._diff_blocks:
            return

        if self._diff_block_index <= 0:
            self._diff_block_index = len(self._diff_blocks) - 1
        else:
            self._diff_block_index -= 1

        self._goto_change(self._diff_block_index)

    def _goto_change(self, block_index: int):
        """Scroll both text views to the change block at `block_index`.

        Each block is a contiguous group of changes (a hunk) in the diff.
        We scroll to show the first change in the block with some context.
        """
        blocks = self._diff_blocks
        if not blocks or block_index < 0 or block_index >= len(blocks):
            return

        # Get the first change in this block.
        start_idx, _ = blocks[block_index]
        change_type, line_a, line_b, _ = self._diff_changes[start_idx]

        len_a = max(1, self._diff_len_a)
        len_b = max(1, self._diff_len_b)

        # For removals, line_b is the corresponding position in B.
        # For additions, line_a is the corresponding position in A.
        # We scroll both views to show the change in context.
        target_a = max(1, min(line_a, len_a))
        target_b = max(1, min(line_b, len_b))

        # Compute scroll fraction, trying to center the target line.
        def compute_scroll_fraction(target: int, length: int) -> float:
            """Compute scroll fraction to show target line near top with context."""
            if length <= 0:
                return 0.0
            # Show target line with a few lines of context above.
            context_lines = 3
            frac = max(0.0, (target - 1 - context_lines) / length)
            return min(frac, 1.0)

        frac_a = compute_scroll_fraction(target_a, len_a)
        frac_b = compute_scroll_fraction(target_b, len_b)

        if self.text_view_a and len_a > 0:
            self.text_view_a.yview_moveto(frac_a)

        if self.text_view_b and len_b > 0:
            self.text_view_b.yview_moveto(frac_b)

        # Highlight the current block by selecting the first changed line.
        self._highlight_current_block(block_index)

    def _highlight_current_block(self, block_index: int):
        """Temporarily highlight the current diff block for visibility."""
        if not self.text_view_a or not self.text_view_b:
            return

        # Clear previous selection highlight.
        self.text_view_a.tag_remove("current_block", "1.0", tk.END)
        self.text_view_b.tag_remove("current_block", "1.0", tk.END)

        # Configure highlight tag.
        self.text_view_a.tag_configure("current_block", background="#ffff99")
        self.text_view_b.tag_configure("current_block", background="#ffff99")

        start_idx, end_idx = self._diff_blocks[block_index]
        for i in range(start_idx, end_idx):
            change_type, line_a, line_b, _ = self._diff_changes[i]
            if change_type in ("removed", "removed_empty") and line_a > 0:
                self.text_view_a.tag_add(
                    "current_block", f"{line_a}.0", f"{line_a}.end"
                )
            elif change_type in ("added", "added_empty") and line_b > 0:
                self.text_view_b.tag_add(
                    "current_block", f"{line_b}.0", f"{line_b}.end"
                )

    def _create_panels_frame(self, parent: ttk.Frame) -> ttk.Frame:
        """Create panels container.

        Args:
            parent: Parent frame

        Returns:
            Panels frame
        """
        panels_frame = ttk.Frame(parent)
        panels_frame.grid(row=1, column=0, columnspan=3, sticky=tk.NSEW)

        panels_frame.columnconfigure(0, weight=1)
        panels_frame.columnconfigure(1, weight=0)
        panels_frame.columnconfigure(2, weight=1)
        panels_frame.rowconfigure(0, weight=1)

        return panels_frame

    def _create_file_panels(self, parent: ttk.Frame):
        """Create both file panels and diff map.

        Args:
            parent: Parent frame
        """
        # Panel A configuration.
        panel_a_config = {
            "title": "File A",
            "column": 0,
            "padx": (0, 2),
            "file_var": self.file_a,
            "file_history": self.file_a_history,
            "open_command": self.open_file_a,
            "button_color": "lightgreen",
            "save_command": self.save_file_a,
        }

        # Panel B configuration.
        panel_b_config = {
            "title": "File B",
            "column": 2,
            "padx": (2, 0),
            "file_var": self.file_b,
            "file_history": self.file_b_history,
            "open_command": self.open_file_b,
            "button_color": "lightblue",
            "save_command": self.save_file_b,
        }

        # Create panel A.
        self._create_single_panel(parent, panel_a_config)

        # Create diff map canvas.
        self.diff_map_canvas = tk.Canvas(
            parent, width=SCROLL_MARKER_WIDTH, bg=self.colors["diff"]["canvas_bg"]
        )
        self.diff_map_canvas.grid(row=0, column=1, sticky="ns", pady=(10, 0))

        # Create scroll marker.
        self.scroll_marker_id = self.diff_map_canvas.create_rectangle(
            2,
            2,
            SCROLL_MARKER_WIDTH - 1,
            6,
            fill=self.colors["diff"]["marker_fill"],
            outline=self.colors["diff"]["marker_outline"],
            width=1,
            stipple="gray12",
            tags="scroll_marker",
        )

        # Bind events to the scroll marker for dragging functionality.
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

        self.diff_map_canvas.bind("<Configure>", self._on_configure)

        # Create panel B.
        self._create_single_panel(parent, panel_b_config)

    def _on_configure(self, event: Optional[tk.Event] = None):
        """Debounced handler for canvas configure events.

        Args:
            event: Optional Tkinter event
        """
        if self._configure_after_id:
            self.root.after_cancel(self._configure_after_id)
        self._configure_after_id = self.root.after(
            CONFIGURE_DEBOUNCE_MS, self.compare_files
        )

    def _create_single_panel(
        self,
        parent: ttk.Frame,
        config: Dict,
    ):
        """Create a single file panel.

        Args:
            parent: Parent widget
            config: Dictionary containing panel configuration
        """
        title = config["title"]
        file_var = config["file_var"]
        file_history = config["file_history"]
        open_command = config["open_command"]
        save_command = config["save_command"]
        button_color = config["button_color"]

        btn_colors = self.colors["buttons"].get(
            button_color, self.colors["buttons"]["default"]
        )

        panel = ttk.LabelFrame(parent, text=title, padding="5")
        panel.grid(
            row=0,
            column=config["column"],
            sticky=tk.NSEW,
            padx=config["padx"],
        )
        panel.columnconfigure(0, weight=0)
        panel.columnconfigure(1, weight=1)
        panel.columnconfigure(2, weight=0)
        panel.columnconfigure(3, weight=0)
        panel.columnconfigure(4, weight=0)
        panel.rowconfigure(1, weight=1)

        spacing = GScaling.scale_pixels(DEFAULT_SPACING, panel)

        # Path label.
        ttk.Label(panel, text="Path:").grid(
            row=0,
            column=0,
            padx=spacing,
            pady=spacing,
            sticky=tk.W,
        )

        # File path combobox.
        path_combobox = ttk.Combobox(
            panel,
            textvariable=file_var,
            values=file_history,
        )
        path_combobox.grid(
            row=0,
            column=1,
            padx=spacing,
            pady=spacing,
            sticky=tk.EW,
        )

        # Open button.
        GButton(
            panel,
            text="Open",
            command=open_command,
            width=BUTTON_WIDTH_PANEL,
            height=BUTTON_HEIGHT_PANEL,
            **btn_colors,
        ).grid(
            row=0,
            column=2,
            padx=spacing,
            pady=spacing,
            sticky=tk.E,
        )

        # Save button.
        GButton(
            panel,
            text="Save",
            command=save_command,
            width=BUTTON_WIDTH_PANEL,
            height=BUTTON_HEIGHT_PANEL,
            **btn_colors,
        ).grid(
            row=0,
            column=3,
            padx=spacing,
            pady=spacing,
            sticky=tk.E,
        )

        # Define font tuple.
        font_tuple = (self.options["font_family"], self.options["font_size"])

        # Get button background color.
        style = ttk.Style()
        button_bg = style.lookup("TButton", "background")

        # Line numbers widget (initially hidden) - placed on the left.
        line_numbers = tk.Text(
            panel,
            width=LINE_NUMBERS_MIN_WIDTH,
            wrap=tk.NONE,
            state=tk.DISABLED,
            font=font_tuple,
            bg=button_bg,
            fg="#666666",
            relief="flat",
            takefocus=False,
            highlightthickness=0,
            highlightbackground=button_bg,
            highlightcolor=button_bg,
        )
        line_numbers.grid(row=1, column=0, pady=(10, 0), sticky=tk.NS)

        # Configure right alignment for line numbers.
        line_numbers.tag_configure("right", justify="right")

        # Initially hide line numbers if option is False.
        if not self.options["show_line_numbers"]:
            line_numbers.grid_remove()

        # Text area with current font.
        wrap_option = tk.WORD if self.options["wrap_lines"] else tk.NONE
        text_area = tk.Text(panel, wrap=wrap_option, state=tk.NORMAL, font=font_tuple)

        # Set initial layout based on line numbers option.
        if self.options["show_line_numbers"]:
            text_area.grid(row=1, column=1, columnspan=3, pady=(10, 0), sticky=tk.NSEW)
        else:
            text_area.grid(row=1, column=0, columnspan=4, pady=(10, 0), sticky=tk.NSEW)

        # Unified text modification handler.
        def on_text_modified(event: Optional[tk.Event] = None):
            """Handle text modification: update line numbers, mark dirty, auto-compare."""
            # Update line numbers first.
            self._update_line_numbers(line_numbers, text_area)

            # Check if content actually changed (not just flag reset).
            if text_area.edit_modified():
                panel.config(text=f"{title}*")
                text_area.edit_modified(False)

                # Auto compare if enabled and both files are loaded.
                if (
                    self.options["auto_compare"]
                    and self.file_a.get()
                    and self.file_b.get()
                ):
                    self.compare_files()

        # Bind text modification event.
        text_area.bind("<<Modified>>", lambda e: on_text_modified())

        # Also update line numbers on key release for responsive typing feedback.
        text_area.bind(
            "<KeyRelease>",
            lambda e: self._update_line_numbers(line_numbers, text_area),
        )

        # Update line numbers on resize.
        text_area.bind(
            "<Configure>",
            lambda e: self._update_line_numbers(line_numbers, text_area),
        )

        # Scrollbars.
        v_scrollbar = ttk.Scrollbar(panel, orient=tk.VERTICAL)
        text_area.configure(yscrollcommand=v_scrollbar.set)
        v_scrollbar.grid(row=1, column=4, pady=(10, 0), sticky=tk.NS)

        h_scrollbar = ttk.Scrollbar(
            panel, orient=tk.HORIZONTAL, command=text_area.xview
        )
        text_area.configure(xscrollcommand=h_scrollbar.set)
        h_scrollbar.grid(row=2, column=0, columnspan=5, sticky=tk.EW)

        # Disable mouse wheel scrolling on line numbers.
        def disable_mouse_wheel(event):
            """Disable mouse wheel events."""
            return "break"

        line_numbers.bind("<MouseWheel>", disable_mouse_wheel)
        line_numbers.bind("<Button-4>", disable_mouse_wheel)
        line_numbers.bind("<Button-5>", disable_mouse_wheel)

        # Store references.
        if title == "File A":
            self.text_view_a = text_area
            self.panel_a = panel
            self.v_scrollbar_a = v_scrollbar
            self.h_scrollbar_a = h_scrollbar
            self.line_numbers_a = line_numbers
            self.path_combobox_a = path_combobox
        else:
            self.text_view_b = text_area
            self.panel_b = panel
            self.v_scrollbar_b = v_scrollbar
            self.h_scrollbar_b = h_scrollbar
            self.line_numbers_b = line_numbers
            self.path_combobox_b = path_combobox

    def _create_status_bar(self, parent: ttk.Frame):
        """Create status bar with legends.

        Args:
            parent: Parent frame
        """
        status_frame = ttk.Frame(parent, relief="flat", padding="2")
        status_frame.grid(row=2, column=0, columnspan=3, sticky=tk.EW, pady=(5, 0))

        status_frame.columnconfigure(0, weight=1)
        status_frame.columnconfigure(1, weight=1)

        # Left status (File A).
        left_status_container = ttk.Frame(status_frame)
        left_status_container.grid(row=0, column=0, sticky=tk.W, padx=0)

        # Removed lines legend.
        removed_square = tk.Label(
            left_status_container,
            bg=self.colors["diff"]["removed"],
            width=2,
            height=1,
            relief="solid",
            bd=1,
        )
        removed_square.pack(side=tk.LEFT, padx=(6, 4))

        # Removed empty lines legend.
        empty_square = tk.Label(
            left_status_container,
            bg=self.colors["diff"]["removed_empty"],
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

        # Right status (File B).
        right_status_container = ttk.Frame(status_frame)
        right_status_container.grid(row=0, column=1, sticky=tk.E, padx=0)

        status_label_right = ttk.Label(
            right_status_container, textvariable=self.status_b, anchor=tk.E
        )
        status_label_right.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Added lines legend.
        added_square = tk.Label(
            right_status_container,
            bg=self.colors["diff"]["added"],
            width=2,
            height=1,
            relief="solid",
            bd=1,
        )
        added_square.pack(side=tk.LEFT, padx=(4, 6))

        # Added empty lines legend.
        empty_square_b = tk.Label(
            right_status_container,
            bg=self.colors["diff"]["added_empty"],
            width=2,
            height=1,
            relief="solid",
            bd=1,
        )
        empty_square_b.pack(side=tk.LEFT, padx=(4, 4))

    # =======================================================================
    # OPTIONS DIALOG
    # =======================================================================

    def show_options_dialog(self):
        """Show the options configuration dialog."""
        dialog = tk.Toplevel(self.root)
        dialog.title("GCompare Options")
        dialog.transient(self.root)
        dialog.grab_set()

        # Center the dialog relative to parent window.
        def center_dialog():
            """Center the dialog after it's fully mapped."""
            dialog.update_idletasks()

            parent_x = self.root.winfo_rootx() + self.root.winfo_width() // 2
            parent_y = self.root.winfo_rooty() + self.root.winfo_height() // 2

            dialog_width = dialog.winfo_width()
            dialog_height = dialog.winfo_height()

            dialog_x = parent_x - dialog_width // 2
            dialog_y = parent_y - dialog_height // 2

            dialog.geometry(f"+{dialog_x}+{dialog_y}")

        dialog.after(100, center_dialog)
        dialog.resizable(False, False)

        main_frame = ttk.Frame(dialog, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Font options
        font_frame = ttk.LabelFrame(main_frame, text="Font", padding="10")
        font_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(font_frame, text="Family:").grid(
            row=0, column=0, sticky=tk.E, padx=(0, 5)
        )

        if self._font_families is None:
            self._font_families = tkfont.families()

        mono_fonts = sorted(
            set(
                f
                for f in self._font_families
                if any(
                    mono in f.lower()
                    for mono in ["mono", "consolas", "courier", "fixedsys", "terminal"]
                )
            )
        )
        if not mono_fonts:
            mono_fonts = sorted(set(self._font_families))

        font_family_var = tk.StringVar(value=self.options["font_family"])
        font_family_combo = ttk.Combobox(
            font_frame, textvariable=font_family_var, values=mono_fonts, width=30
        )
        font_family_combo.grid(row=0, column=1, sticky=tk.W, padx=(0, 10))

        ttk.Label(font_frame, text="Size:").grid(
            row=0, column=2, sticky=tk.W, padx=(0, 5)
        )
        font_size_var = tk.IntVar(value=self.options["font_size"])
        font_size_spinbox = tk.Spinbox(
            font_frame, from_=8, to=24, textvariable=font_size_var, width=5
        )
        font_size_spinbox.grid(row=0, column=3, sticky=tk.W)

        ttk.Label(font_frame, text="Example:").grid(
            row=1, column=0, sticky=tk.E, pady=(5, 0), padx=(0, 5)
        )
        font_example_label = ttk.Label(font_frame, text="AaBbCc 123")
        font_example_label.grid(row=1, column=1, sticky=tk.W, pady=(5, 0))

        def update_font_example(*args):
            """Update the font example when font family or size changes."""
            font_family = font_family_var.get()
            font_size = font_size_var.get()
            if font_family and font_size:
                font_example_label.configure(font=(font_family, font_size))

        font_family_var.trace("w", update_font_example)
        font_size_var.trace("w", update_font_example)
        update_font_example()

        # Display and Comparison options.
        options_frame = ttk.LabelFrame(main_frame, text="Options", padding="10")
        options_frame.pack(fill=tk.X, pady=(0, 10))

        line_numbers_var = tk.BooleanVar(value=self.options["show_line_numbers"])
        line_numbers_check = ttk.Checkbutton(
            options_frame, text="Show Line Numbers", variable=line_numbers_var
        )
        line_numbers_check.grid(row=0, column=0, sticky=tk.W, padx=(0, 20))

        wrap_lines_var = tk.BooleanVar(value=self.options["wrap_lines"])
        wrap_lines_check = ttk.Checkbutton(
            options_frame, text="Wrap Lines", variable=wrap_lines_var
        )
        wrap_lines_check.grid(row=0, column=1, sticky=tk.W, pady=(5, 0))

        ttk.Label(options_frame, text="Tab Size:").grid(
            row=0, column=2, sticky=tk.W, padx=(20, 5), pady=(5, 0)
        )
        tab_size_var = tk.IntVar(value=self.options["tab_size"])
        tab_size_spinbox = tk.Spinbox(
            options_frame, from_=2, to=8, textvariable=tab_size_var, width=5
        )
        tab_size_spinbox.grid(row=0, column=3, sticky=tk.W, pady=(5, 0))

        auto_compare_var = tk.BooleanVar(value=self.options["auto_compare"])
        auto_compare_check = ttk.Checkbutton(
            options_frame, text="Compare on Change", variable=auto_compare_var
        )
        auto_compare_check.grid(row=1, column=0, sticky=tk.W, pady=(5, 0))

        ignore_whitespace_var = tk.BooleanVar(value=self.options["ignore_whitespace"])
        ignore_whitespace_check = ttk.Checkbutton(
            options_frame, text="Ignore Whitespace", variable=ignore_whitespace_var
        )
        ignore_whitespace_check.grid(row=1, column=1, sticky=tk.W, pady=(5, 0))

        ignore_case_var = tk.BooleanVar(value=self.options["ignore_case"])
        ignore_case_check = ttk.Checkbutton(
            options_frame, text="Ignore Case", variable=ignore_case_var
        )
        ignore_case_check.grid(row=1, column=2, sticky=tk.W, padx=(20, 0), pady=(5, 0))

        highlight_line_var = tk.BooleanVar(
            value=self.options.get("highlight_current_line", False)
        )
        highlight_line_check = ttk.Checkbutton(
            options_frame,
            text="Highlight Current Line",
            variable=highlight_line_var,
        )
        highlight_line_check.grid(row=2, column=0, sticky=tk.W, pady=(5, 0))

        # Button frame.
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))

        def apply_options():
            """Apply the selected options."""
            self.options.update(
                {
                    "font_family": font_family_var.get(),
                    "font_size": font_size_var.get(),
                    "show_line_numbers": line_numbers_var.get(),
                    "wrap_lines": wrap_lines_var.get(),
                    "tab_size": tab_size_var.get(),
                    "auto_compare": auto_compare_var.get(),
                    "ignore_whitespace": ignore_whitespace_var.get(),
                    "ignore_case": ignore_case_var.get(),
                    "highlight_current_line": highlight_line_var.get(),
                }
            )

            self._update_font_style()

            wrap_option = tk.WORD if self.options["wrap_lines"] else tk.NONE
            if self.text_view_a:
                self.text_view_a.configure(wrap=wrap_option)
            if self.text_view_b:
                self.text_view_b.configure(wrap=wrap_option)

            self._toggle_line_numbers(self.options["show_line_numbers"])

            self.save_config()
            dialog.destroy()

            if self.file_a.get() and self.file_b.get():
                self.compare_files()

        def reset_options():
            """Reset options to default values."""
            font_family_var.set(DEFAULT_FONT_FAMILY)
            font_size_var.set(DEFAULT_FONT_SIZE)
            line_numbers_var.set(True)
            wrap_lines_var.set(False)
            tab_size_var.set(4)
            auto_compare_var.set(True)
            ignore_whitespace_var.set(False)
            ignore_case_var.set(False)
            highlight_line_var.set(False)

        button_center_frame = ttk.Frame(button_frame)
        button_center_frame.pack(expand=True)

        button_row_frame = ttk.Frame(button_center_frame)
        button_row_frame.pack()

        btn_spacing = GScaling.scale_pixels(DEFAULT_SPACING, dialog)

        GButton(
            button_row_frame,
            text="Apply",
            command=apply_options,
            width=BUTTON_WIDTH_MAIN,
            height=BUTTON_HEIGHT_MAIN,
            **self.colors["buttons"]["primary"],
        ).pack(side=tk.LEFT, padx=btn_spacing)

        GButton(
            button_row_frame,
            text="Reset",
            command=reset_options,
            width=BUTTON_WIDTH_MAIN,
            height=BUTTON_HEIGHT_MAIN,
            **self.colors["buttons"]["secondary"],
        ).pack(side=tk.LEFT, padx=btn_spacing)

        GButton(
            button_row_frame,
            text="Cancel",
            command=dialog.destroy,
            width=BUTTON_WIDTH_MAIN,
            height=BUTTON_HEIGHT_MAIN,
            **self.colors["buttons"]["secondary"],
        ).pack(side=tk.LEFT, padx=btn_spacing)

    # =======================================================================
    # CONFIGURATION METHODS
    # =======================================================================

    def load_config(self):
        """Load configuration from file."""
        if not os.path.exists(CONFIG_FILE):
            return

        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)

            if "WINDOW" in config and "geometry" in config["WINDOW"]:
                self.root.geometry(config["WINDOW"]["geometry"])

            if "FILE_A_HISTORY" in config:
                self.file_a_history = config["FILE_A_HISTORY"]
                if self.file_a_history:
                    self.file_a.set(self.file_a_history[0])

            if "FILE_B_HISTORY" in config:
                self.file_b_history = config["FILE_B_HISTORY"]
                if self.file_b_history:
                    self.file_b.set(self.file_b_history[0])

            if "OPTIONS" in config:
                self.options.update(config["OPTIONS"])

        except json.JSONDecodeError:
            print(f"Warning: Could not parse {CONFIG_FILE}. Using defaults.")

    def save_config(self):
        """Save configuration to file."""
        config = {
            "WINDOW": {"geometry": self.root.geometry()},
            "FILE_A_HISTORY": self.file_a_history,
            "FILE_B_HISTORY": self.file_b_history,
            "OPTIONS": self.options,
        }

        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)

    def _update_font_style(self):
        """Update the font style based on current options."""
        style = ttk.Style()
        font_tuple = (self.options["font_family"], self.options["font_size"])
        style.configure("TText", font=font_tuple)
        style.configure(
            "TLabelframe.Label",
            font=(self.options["font_family"], self.options["font_size"], "bold"),
        )

        if self.text_view_a:
            self.text_view_a.configure(font=font_tuple)
        if self.text_view_b:
            self.text_view_b.configure(font=font_tuple)

        if self.line_numbers_a:
            self.line_numbers_a.configure(font=font_tuple)
        if self.line_numbers_b:
            self.line_numbers_b.configure(font=font_tuple)

        if self.options["show_line_numbers"]:
            if self.line_numbers_a and self.text_view_a:
                self._update_line_numbers(self.line_numbers_a, self.text_view_a)
            if self.line_numbers_b and self.text_view_b:
                self._update_line_numbers(self.line_numbers_b, self.text_view_b)

    def _update_line_numbers(self, line_numbers_widget: tk.Text, text_widget: tk.Text):
        """Update line numbers to match the text widget.

        Args:
            line_numbers_widget: Line numbers widget to update
            text_widget: Text widget to sync with
        """
        if (
            not self.options["show_line_numbers"]
            or not line_numbers_widget
            or not text_widget
        ):
            return

        text_content = text_widget.get("1.0", tk.END)
        lines = text_content.splitlines()

        # Dynamically adjust width based on line count.
        max_line = len(lines)
        width = max(LINE_NUMBERS_MIN_WIDTH, len(str(max_line)) + 1)
        line_numbers_widget.configure(width=width)

        line_numbers_text = "\n".join(str(i) for i in range(1, len(lines) + 1))

        line_numbers_widget.config(state=tk.NORMAL)
        line_numbers_widget.delete("1.0", tk.END)
        line_numbers_widget.insert("1.0", line_numbers_text)
        line_numbers_widget.tag_add("right", "1.0", "end")
        line_numbers_widget.config(state=tk.DISABLED)

        # Synchronize scrolling.
        first, _ = text_widget.yview()
        line_numbers_widget.yview_moveto(first)

    def _toggle_line_numbers(self, show: bool):
        """Toggle line numbers visibility and adjust text area layout.

        Args:
            show: Whether to show line numbers
        """
        if self.line_numbers_a:
            if show:
                self.line_numbers_a.grid()
                if self.text_view_a:
                    self.text_view_a.grid(
                        row=1, column=1, columnspan=3, pady=(10, 0), sticky=tk.NSEW
                    )
                    self._update_line_numbers(self.line_numbers_a, self.text_view_a)
            else:
                self.line_numbers_a.grid_remove()
                if self.text_view_a:
                    self.text_view_a.grid(
                        row=1, column=0, columnspan=4, pady=(10, 0), sticky=tk.NSEW
                    )

        if self.line_numbers_b:
            if show:
                self.line_numbers_b.grid()
                if self.text_view_b:
                    self.text_view_b.grid(
                        row=1, column=1, columnspan=3, pady=(10, 0), sticky=tk.NSEW
                    )
                    self._update_line_numbers(self.line_numbers_b, self.text_view_b)
            else:
                self.line_numbers_b.grid_remove()
                if self.text_view_b:
                    self.text_view_b.grid(
                        row=1, column=0, columnspan=4, pady=(10, 0), sticky=tk.NSEW
                    )

    def _update_file_history(self, panel_name: str, new_path: str):
        """Update recent files list for specified panel.

        Args:
            panel_name: Either "A" or "B"
            new_path: Path to add to history
        """
        if not new_path or self._is_temporary_path(new_path):
            return

        history_list = self.file_a_history if panel_name == "A" else self.file_b_history

        if new_path in history_list:
            history_list.remove(new_path)

        history_list.insert(0, new_path)
        del history_list[HISTORY_LENGTH:]

        # Update combobox values dynamically.
        combobox = self.path_combobox_a if panel_name == "A" else self.path_combobox_b
        if combobox:
            combobox.configure(values=history_list)

    # =======================================================================
    # FILE OPERATIONS
    # =======================================================================

    def open_file_a(self):
        """Open file dialog for File A."""
        self.open_file("A")

    def open_file_b(self):
        """Open file dialog for File B."""
        self.open_file("B")

    def open_file(self, panel_name: str):
        """Open file dialog and load file.

        Args:
            panel_name: Either "A" or "B"
        """
        initial_dir = None
        current_path = self.file_a.get() if panel_name == "A" else self.file_b.get()

        if current_path:
            if os.path.isdir(current_path):
                initial_dir = current_path
            else:
                initial_dir = os.path.dirname(current_path)

        file_path = filedialog.askopenfilename(initialdir=initial_dir)
        if file_path:
            if panel_name == "A":
                self.load_file_a(file_path)
            else:
                self.load_file_b(file_path)

    def reload_files(self):
        """Reload both files (prompt save if dirty)."""
        # Check File A for unsaved changes.
        if self.panel_a and self.panel_a.cget("text").endswith("*"):
            response = messagebox.askyesnocancel(
                "Unsaved Changes",
                "File A has unsaved changes. Do you want to save them before reloading?",
            )
            if response is True:
                if not self.save_file_a():
                    return
            elif response is None:
                return

        # Check File B for unsaved changes.
        if self.panel_b and self.panel_b.cget("text").endswith("*"):
            response = messagebox.askyesnocancel(
                "Unsaved Changes",
                "File B has unsaved changes. Do you want to save them before reloading?",
            )
            if response is True:
                if not self.save_file_b():
                    return
            elif response is None:
                return

        self._clear_diff_map()

        if self.file_a.get():
            self.load_file_a(self.file_a.get())
        if self.file_b.get():
            self.load_file_b(self.file_b.get())

    def save_file_a(self) -> bool:
        """Save File A.

        Returns:
            True if saved successfully, False otherwise
        """
        if self.text_view_a:
            return self.save_file(self.file_a.get(), self.text_view_a, "A")
        return False

    def save_file_b(self) -> bool:
        """Save File B.

        Returns:
            True if saved successfully, False otherwise
        """
        if self.text_view_b:
            return self.save_file(self.file_b.get(), self.text_view_b, "B")
        return False

    def save_file(self, file_path: str, text_widget: tk.Text, panel_name: str) -> bool:
        """Write text widget content to disk.

        Args:
            file_path: Path to save to
            text_widget: Text widget containing content
            panel_name: Either "A" or "B"

        Returns:
            True if saved successfully, False otherwise
        """
        if not file_path:
            messagebox.showwarning(
                "Save Error", f"No file path specified for Panel {panel_name}."
            )
            return False

        if not text_widget:
            messagebox.showerror(
                "Save Error", f"Text view for Panel {panel_name} is not available."
            )
            return False

        try:
            content = text_widget.get("1.0", tk.END)
            with open(file_path, "w", encoding="utf-8") as file:
                file.write(content)

            panel_widget = self.panel_a if panel_name == "A" else self.panel_b
            if panel_widget:
                panel_widget.config(text=f"File {panel_name}")

            messagebox.showinfo("Success", f"File '{file_path}' saved successfully.")
            return True
        except Exception as e:
            messagebox.showerror(
                "Save Error", f"Failed to save file '{file_path}':\n{e}"
            )
            return False

    def load_file_a(self, file_path: str):
        """Load file into File A view.

        Args:
            file_path: Path to file to load
        """
        self.load_file(
            file_path,
            "A",
            self.file_a,
            self.text_view_a,
            self.panel_a,
            self.status_a,
        )

    def load_file_b(self, file_path: str):
        """Load file into File B view.

        Args:
            file_path: Path to file to load
        """
        self.load_file(
            file_path,
            "B",
            self.file_b,
            self.text_view_b,
            self.panel_b,
            self.status_b,
        )

    def load_file(
        self,
        file_path: str,
        panel_name: str,
        file_var: tk.StringVar,
        text_view: Optional[tk.Text],
        panel_widget: Optional[ttk.LabelFrame],
        status_var: tk.StringVar,
    ):
        """Load file content into specified panel.

        Args:
            file_path: Path to file to load
            panel_name: Either "A" or "B"
            file_var: StringVar to store file path
            text_view: Text widget to display content
            panel_widget: Panel widget to update title
            status_var: Status variable to update
        """
        try:
            with open(file_path, "r", encoding="utf-8") as file:
                content = file.read()

            self._update_file_history(panel_name, file_path)

            file_var.set(file_path)

            if text_view:
                text_view.delete("1.0", tk.END)
                text_view.insert("1.0", content)
                text_view.edit_modified(False)

            if panel_widget:
                panel_widget.config(text=f"File {panel_name}")

            line_count = len(content.splitlines())
            char_count = len(content)
            status_var.set(f"{line_count} lines, {char_count} characters")

            if self.options["show_line_numbers"] and text_view:
                if panel_name == "A" and self.line_numbers_a:
                    self._update_line_numbers(self.line_numbers_a, text_view)
                elif panel_name == "B" and self.line_numbers_b:
                    self._update_line_numbers(self.line_numbers_b, text_view)

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load file: {e}")

    # =======================================================================
    # TEXT AND COMPARISON METHODS
    # =======================================================================

    def compare_files(self, event=None):
        """Compare the two files and highlight differences.

        Args:
            event: Optional Tk event (for bindings)
        """
        if not self.text_view_a or not self.text_view_b:
            messagebox.showwarning(
                "Warning", "Please load both files before comparing."
            )
            return

        diff_result = self._compute_diff()

        self._diff_changes = diff_result.get("changes", [])
        self._diff_blocks = diff_result.get("blocks", [])
        self._diff_len_a = len(diff_result.get("lines_a", []))
        self._diff_len_b = len(diff_result.get("lines_b", []))
        self._diff_block_index = -1

        self._apply_highlights(diff_result)
        self._update_diff_map(diff_result)
        self._update_status(diff_result)

    def _compute_diff(self) -> Dict:
        """Compute differences between the two files.

        Returns:
            dict: Contains diff lines, line counts, and content information.
        """
        lines_a = (
            self.text_view_a.get("1.0", tk.END).splitlines() if self.text_view_a else []
        )
        lines_b = (
            self.text_view_b.get("1.0", tk.END).splitlines() if self.text_view_b else []
        )

        compare_a = lines_a.copy()
        compare_b = lines_b.copy()

        if self.options["ignore_whitespace"]:
            compare_a = [line.rstrip() for line in compare_a]
            compare_b = [line.rstrip() for line in compare_b]

        if self.options["ignore_case"]:
            compare_a = [line.lower() for line in compare_a]
            compare_b = [line.lower() for line in compare_b]

        differ = difflib.Differ()
        diff_lines = list(differ.compare(compare_a, compare_b))

        a_index = 1
        b_index = 1

        diff_info = {
            "lines_a": lines_a,
            "lines_b": lines_b,
            "diff_lines": diff_lines,
            "added_lines": 0,
            "removed_lines": 0,
            "added_empty_lines": 0,
            "removed_empty_lines": 0,
            "total_lines": max(len(lines_a), len(lines_b)),
            "changes": [],
            "blocks": [],
        }

        def is_empty_line(line: str) -> bool:
            return len(line.strip()) == 0

        current_block_start = None

        for line in diff_lines:
            if not line:
                continue

            code = line[0]
            line_content = line[2:] if len(line) > 2 else ""
            is_empty = is_empty_line(line_content)

            if code == " ":
                if current_block_start is not None:
                    diff_info["blocks"].append(
                        (current_block_start, len(diff_info["changes"]))
                    )
                    current_block_start = None
                a_index += 1
                b_index += 1
            elif code == "-":
                if current_block_start is None:
                    current_block_start = len(diff_info["changes"])
                diff_info["removed_lines"] += 1
                # For removed lines, line_b shows where we are in B (before advance).
                change_type = "removed_empty" if is_empty else "removed"
                diff_info["changes"].append((change_type, a_index, b_index, is_empty))
                if is_empty:
                    diff_info["removed_empty_lines"] += 1
                a_index += 1
            elif code == "+":
                if current_block_start is None:
                    current_block_start = len(diff_info["changes"])
                diff_info["added_lines"] += 1
                # For added lines, line_a shows where we are in A (before advance).
                change_type = "added_empty" if is_empty else "added"
                diff_info["changes"].append((change_type, a_index, b_index, is_empty))
                if is_empty:
                    diff_info["added_empty_lines"] += 1
                b_index += 1

        if current_block_start is not None:
            diff_info["blocks"].append((current_block_start, len(diff_info["changes"])))

        return diff_info

    def _apply_highlights(self, diff_result: Dict):
        """Apply highlighting to the text widgets based on diff results.

        Args:
            diff_result: Dictionary containing diff information
        """
        if self.text_view_a:
            self.text_view_a.tag_remove("removed", "1.0", tk.END)
            self.text_view_a.tag_remove("removed_empty", "1.0", tk.END)
            self.text_view_a.tag_remove("current_block", "1.0", tk.END)
        if self.text_view_b:
            self.text_view_b.tag_remove("added", "1.0", tk.END)
            self.text_view_b.tag_remove("added_empty", "1.0", tk.END)
            self.text_view_b.tag_remove("current_block", "1.0", tk.END)

        if self.text_view_a:
            self.text_view_a.tag_configure(
                "removed", background=self.colors["diff"]["removed"]
            )
            self.text_view_a.tag_configure(
                "removed_empty", background=self.colors["diff"]["removed_empty"]
            )
        if self.text_view_b:
            self.text_view_b.tag_configure(
                "added", background=self.colors["diff"]["added"]
            )
            self.text_view_b.tag_configure(
                "added_empty", background=self.colors["diff"]["added_empty"]
            )

        for change_info in diff_result["changes"]:
            change_type, line_a, line_b, is_empty = change_info

            if change_type in ("removed", "removed_empty") and self.text_view_a:
                start_pos = f"{line_a}.0"
                end_pos = f"{line_a}.end"
                tag_name = (
                    "removed_empty" if change_type == "removed_empty" else "removed"
                )
                self.text_view_a.tag_add(tag_name, start_pos, end_pos)
            elif change_type in ("added", "added_empty") and self.text_view_b:
                start_pos = f"{line_b}.0"
                end_pos = f"{line_b}.end"
                tag_name = "added_empty" if change_type == "added_empty" else "added"
                self.text_view_b.tag_add(tag_name, start_pos, end_pos)

    def _update_diff_map(self, diff_result: Dict):
        """Update the diff map visualization.

        Args:
            diff_result: Dictionary containing diff information
        """
        if not self.diff_map_canvas or not self.text_view_a:
            return

        self.diff_map_canvas.delete("diff_line")

        first, last = self.text_view_a.yview()
        self._update_scroll_marker(float(first), float(last))

        canvas_height = self.diff_map_canvas.winfo_height()
        if canvas_height <= 0:
            return

        canvas_width = self.diff_map_canvas.winfo_width()
        half_width = canvas_width / 2

        for change_info in diff_result["changes"]:
            change_type, line_a, line_b, is_empty = change_info

            if change_type in ("removed", "removed_empty"):
                line_num = line_a
                total = max(1, len(diff_result.get("lines_a", [])))
            else:
                line_num = line_b
                total = max(1, len(diff_result.get("lines_b", [])))

            if 1 <= line_num <= total:
                y_start = ((line_num - 1) / total) * canvas_height
                line_height = max(1, canvas_height / total)
                y_end = y_start + line_height

                if change_type in ("removed", "removed_empty"):
                    fill_color = (
                        self.colors["diff"]["removed_empty"]
                        if change_type == "removed_empty"
                        else self.colors["diff"]["removed"]
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
                        self.colors["diff"]["added_empty"]
                        if change_type == "added_empty"
                        else self.colors["diff"]["added"]
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

        if self.scroll_marker_id:
            self.diff_map_canvas.tag_raise("scroll_marker")

    def _update_status(self, diff_result: Dict):
        """Update the status bar with diff information.

        Args:
            diff_result: Dictionary containing diff information
        """
        non_empty_removed = (
            diff_result["removed_lines"] - diff_result["removed_empty_lines"]
        )
        non_empty_added = diff_result["added_lines"] - diff_result["added_empty_lines"]

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

    # =======================================================================
    # SCROLLING METHODS
    # =======================================================================

    def _setup_synchronized_scrolling(self):
        """Link scrolling between both panels."""
        if not (
            self.text_view_a
            and self.text_view_b
            and self.v_scrollbar_a
            and self.v_scrollbar_b
            and self.h_scrollbar_a
            and self.h_scrollbar_b
        ):
            return

        text_view_a, text_view_b = self.text_view_a, self.text_view_b
        v_scrollbar_a, v_scrollbar_b = self.v_scrollbar_a, self.v_scrollbar_b
        h_scrollbar_a, h_scrollbar_b = self.h_scrollbar_a, self.h_scrollbar_b

        def _on_y_scroll(*args):
            """Handle vertical scroll events."""
            text_view_a.yview(*args)
            text_view_b.yview(*args)

        def _on_y_view_change(*args):
            """Update scrollbars when vertical view changes."""
            v_scrollbar_a.set(*args)
            v_scrollbar_b.set(*args)
            if self.text_view_a:
                first, last = self.text_view_a.yview()
                self._update_scroll_marker(float(first), float(last))

            if (
                self.options["show_line_numbers"]
                and self.line_numbers_a
                and self.line_numbers_b
                and self.text_view_a
                and self.text_view_b
            ):
                self._update_line_numbers(self.line_numbers_a, self.text_view_a)
                self._update_line_numbers(self.line_numbers_b, self.text_view_b)

        def _on_x_scroll(*args):
            """Handle horizontal scroll events."""
            text_view_a.xview(*args)
            text_view_b.xview(*args)

        def _on_x_view_change(*args):
            """Update scrollbars when horizontal view changes."""
            h_scrollbar_a.set(*args)
            h_scrollbar_b.set(*args)

        v_scrollbar_a.config(command=_on_y_scroll)
        v_scrollbar_b.config(command=_on_y_scroll)
        text_view_a.config(yscrollcommand=_on_y_view_change)
        text_view_b.config(yscrollcommand=_on_y_view_change)

        h_scrollbar_a.config(command=_on_x_scroll)
        h_scrollbar_b.config(command=_on_x_scroll)
        text_view_a.config(xscrollcommand=_on_x_view_change)
        text_view_b.config(xscrollcommand=_on_x_view_change)

    def _update_scroll_marker(
        self, first_visible_fraction: float, last_visible_fraction: float
    ):
        """Update diff map scroll marker position.

        Args:
            first_visible_fraction: Fraction of document at top of viewport
            last_visible_fraction: Fraction of document at bottom of viewport
        """
        if not self.diff_map_canvas or not self.scroll_marker_id:
            return

        canvas_height = self.diff_map_canvas.winfo_height()
        if canvas_height <= 0:
            return

        y1 = first_visible_fraction * canvas_height
        y2 = last_visible_fraction * canvas_height

        # Ensure minimum height.
        if y2 - y1 < MARKER_MIN_HEIGHT:
            y2 = y1 + MARKER_MIN_HEIGHT

        # Clamp to canvas bounds.
        if y2 > canvas_height:
            y2 = canvas_height
            y1 = max(0.0, y2 - MARKER_MIN_HEIGHT)

        # Apply padding with bounds checking to ensure positive height.
        y1_draw = min(y1 + MARKER_PAD_TOP, canvas_height - 1)
        y2_draw = max(y1_draw + 1, y2 - MARKER_PAD_BOTTOM)
        y2_draw = min(y2_draw, canvas_height)

        self.diff_map_canvas.coords(
            self.scroll_marker_id,
            2,
            y1_draw,
            SCROLL_MARKER_WIDTH - 1,
            y2_draw,
        )

    def _on_marker_press(self, event: tk.Event):
        """Handle mouse button press on the scroll marker.

        Args:
            event: Mouse event
        """
        if not self.diff_map_canvas or not self.text_view_a:
            return

        self._marker_drag_start_y = event.y
        self._marker_initial_scroll_fraction = self.text_view_a.yview()[0]
        self.diff_map_canvas.config(cursor="hand2")

    def _on_marker_drag(self, event: tk.Event):
        """Handle mouse drag motion on the scroll marker.

        Args:
            event: Mouse event
        """
        if self._marker_drag_start_y is None:
            return

        if not self.diff_map_canvas:
            return

        dy = event.y - self._marker_drag_start_y
        canvas_height = self.diff_map_canvas.winfo_height()

        if canvas_height <= 0:
            return

        new_fraction = self._marker_initial_scroll_fraction + (dy / canvas_height)
        new_fraction = max(0.0, min(1.0, new_fraction))

        if self.text_view_a:
            self.text_view_a.yview_moveto(new_fraction)
        if self.text_view_b:
            self.text_view_b.yview_moveto(new_fraction)

    def _on_marker_release(self, event: tk.Event):
        """Handle mouse button release on the scroll marker.

        Args:
            event: Mouse event
        """
        if not self.diff_map_canvas:
            return

        self._marker_drag_start_y = None
        self.diff_map_canvas.config(cursor="")

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

    # =======================================================================
    # UTILITY METHODS
    # =======================================================================

    def _is_temporary_path(self, path: str) -> bool:
        """Check if a path is a temporary file or directory.

        Args:
            path: Path to check

        Returns:
            True if path appears to be temporary
        """
        if not path:
            return False

        path_normalized = os.path.normpath(path)
        temp_dir = os.path.normpath(tempfile.gettempdir())

        # Check if path is within the system temp directory.
        if path_normalized.startswith(temp_dir + os.sep):
            return True

        # Check common temp prefixes.
        common_temp_prefixes = [
            os.sep + "tmp" + os.sep,
            os.sep + "temp" + os.sep,
        ]
        for prefix in common_temp_prefixes:
            if path_normalized.startswith(prefix):
                return True

        return False

    def _clear_diff_map(self):
        """Clear the diff map visualization."""
        if self.diff_map_canvas:
            self.diff_map_canvas.delete("diff_line")

            if self.status_a:
                self.status_a.set("by Gino Bogo")
            if self.status_b:
                self.status_b.set("")

            if self.text_view_a:
                self.text_view_a.tag_remove("removed", "1.0", tk.END)
                self.text_view_a.tag_remove("removed_empty", "1.0", tk.END)
                self.text_view_a.tag_remove("current_block", "1.0", tk.END)
            if self.text_view_b:
                self.text_view_b.tag_remove("added", "1.0", tk.END)
                self.text_view_b.tag_remove("added_empty", "1.0", tk.END)
                self.text_view_b.tag_remove("current_block", "1.0", tk.END)

    # =======================================================================
    # EVENT HANDLERS
    # =======================================================================

    def on_closing(self):
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
