#!/usr/bin/env python3

import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox
import difflib


class GCompare:
    def __init__(self, root):
        self.root = root
        self._init_window()

        # File Paths
        self.file_a = tk.StringVar()
        self.file_b = tk.StringVar()

        # Text Content
        self.text_a = tk.StringVar()
        self.text_b = tk.StringVar()

        # UI Components
        self.text_area_a = None
        self.text_area_b = None

        self.setup_ui()

        # Load files from command line arguments
        if len(sys.argv) > 1:
            self.load_file_a(sys.argv[1])
        if len(sys.argv) > 2:
            self.load_file_b(sys.argv[2])

    def _init_window(self):
        """Initialize main window properties."""
        self.root.title("GCompare - Text Comparison Tool")
        self.root.minsize(1024, 768)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def setup_ui(self):
        """Set up the main user interface."""
        self._setup_styles()

        # Main container
        main_frame = self._create_main_frame()

        # Control panel
        control_frame = self._create_control_frame(main_frame)
        self._create_control_buttons(control_frame)

        # Text panels
        panels_frame = self._create_panels_frame(main_frame)
        self._create_text_panels(panels_frame)

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
            ("Compare", self.compare_files, None),
            ("Load File A", self.browse_file_a, "lightgreen"),
            ("Load File B", self.browse_file_b, "lightblue"),
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
        panels_frame.columnconfigure(1, weight=1)
        panels_frame.rowconfigure(0, weight=1)

        return panels_frame

    def _create_text_panels(self, panels_frame):
        """Create text panels A and B."""
        # Panel A configuration
        panel_a_config = {
            "title": "File A",
            "column": 0,
            "padx": (0, 5),
            "text_var": self.text_a,
            "file_var": self.file_a,
        }

        # Panel B configuration
        panel_b_config = {
            "title": "File B",
            "column": 1,
            "padx": (5, 0),
            "text_var": self.text_b,
            "file_var": self.file_b,
        }

        self._create_panel(panels_frame, panel_a_config)
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
        panel.rowconfigure(0, weight=1)

        # Text Area
        text_area = tk.Text(panel, wrap=tk.WORD)
        text_area.grid(row=0, column=0, sticky=tk.NSEW)

        # Scrollbars
        v_scrollbar = ttk.Scrollbar(panel, orient=tk.VERTICAL, command=text_area.yview)
        text_area.configure(yscrollcommand=v_scrollbar.set)
        v_scrollbar.grid(row=0, column=1, sticky=tk.NS)

        h_scrollbar = ttk.Scrollbar(panel, orient=tk.HORIZONTAL, command=text_area.xview)
        text_area.configure(xscrollcommand=h_scrollbar.set)
        h_scrollbar.grid(row=1, column=0, sticky=tk.EW)

        # Store text area reference
        if config["title"] == "File A":
            self.text_area_a = text_area
        else:
            self.text_area_b = text_area

    def browse_file_a(self):
        """Browse for file A."""
        self._browse_file("A")

    def browse_file_b(self):
        """Browse for file B."""
        self._browse_file("B")

    def _browse_file(self, panel_name):
        """Browse for a file."""
        file_path = filedialog.askopenfilename()
        if file_path:
            if panel_name == "A":
                self.load_file_a(file_path)
            else:
                self.load_file_b(file_path)

    def load_file_a(self, file_path):
        """Load file A content into the text area."""
        try:
            with open(file_path, "r", encoding="utf-8") as file:
                content = file.read()
                self.file_a.set(file_path)
                self.text_a.set(content)
                if self.text_area_a:
                    self.text_area_a.delete("1.0", tk.END)
                    self.text_area_a.insert("1.0", content)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load file: {e}")

    def load_file_b(self, file_path):
        """Load file B content into the text area."""
        try:
            with open(file_path, "r", encoding="utf-8") as file:
                content = file.read()
                self.file_b.set(file_path)
                self.text_b.set(content)
                if self.text_area_b:
                    self.text_area_b.delete("1.0", tk.END)
                    self.text_area_b.insert("1.0", content)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load file: {e}")

    def compare_files(self):
        """Compare the content of the two text areas and highlight differences."""
        if not self.text_area_a or not self.text_area_b:
            messagebox.showwarning("Warning", "Please load both files before comparing.")
            return

        text_a = self.text_area_a.get("1.0", tk.END)
        text_b = self.text_area_b.get("1.0", tk.END)

        # Clear existing tags
        self.text_area_a.tag_delete(*self.text_area_a.tag_names())
        self.text_area_b.tag_delete(*self.text_area_b.tag_names())

        # Configure tags for highlighting
        self.text_area_a.tag_configure("difference", background="lightblue")
        self.text_area_b.tag_configure("difference", background="lightcoral")

        # Perform comparison
        differ = difflib.Differ()
        diff = differ.compare(text_a.splitlines(), text_b.splitlines())

        a_index = 1
        b_index = 1

        for line in diff:
            code = line[0]
            if code == ' ':
                a_index += 1
                b_index += 1
            elif code == '-':
                self.text_area_a.tag_add(
                    "difference", f"{a_index}.0", f"{a_index}.end"
                )
                a_index += 1
            elif code == '+':
                self.text_area_b.tag_add(
                    "difference", f"{b_index}.0", f"{b_index}.end"
                )
                b_index += 1

    def on_closing(self):
        """Handle window close event."""
        self.root.destroy()

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
                return (font, 11)

        # Fallback to a generic monospace font
        return ("Courier", 11)


def main():
    """Main entry point for the application."""
    root = tk.Tk()
    GCompare(root)
    root.mainloop()


if __name__ == "__main__":
    main()