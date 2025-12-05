#!/usr/bin/env python3
"""
GSynchro - GUI File Synchronization Tool

Graphical application for comparing and synchronizing files between local and
remote folders. Supports SSH-based remote operations with visual comparison.

Author: Gino Bogo
License: MIT
Version: 1.0
"""

from __future__ import annotations


import fnmatch
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Optional
from datetime import datetime
import tkinter.font as tkfont

import paramiko
from scp import SCPClient
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


CONFIG_FILE = "g_synchro.json"
HISTORY_LENGTH = 10
CHUNK_SIZE = 4096


class GSynchro:
    def __init__(self, root: tk.Tk):
        self.root = root
        self._init_window()

        # SSH Configuration
        self.ssh_client_a = None
        self.ssh_client_b = None

        self.remote_host_a = tk.StringVar()
        self.remote_user_a = tk.StringVar()
        self.remote_pass_a = tk.StringVar()
        self.remote_port_a = tk.StringVar(value="22")

        self.remote_host_b = tk.StringVar()
        self.remote_user_b = tk.StringVar()
        self.remote_pass_b = tk.StringVar()
        self.remote_port_b = tk.StringVar(value="22")

        # Folder Paths
        self.folder_a = tk.StringVar()
        self.folder_b = tk.StringVar()
        self.folder_a_history = []
        self.folder_b_history = []

        # UI Components
        self.tree_a: Optional[ttk.Treeview] = None
        self.tree_b: Optional[ttk.Treeview] = None

        # Data Storage
        self.files_a = {}
        self.files_b = {}
        self.filter_rules = []
        self.temp_files_to_clean = []

        # Sync States
        self.CHECKED_CHAR = "☑"
        self.UNCHECKED_CHAR = "☐"
        self.sync_states = {}

        # Status Variables
        self.status_a = tk.StringVar()
        self.status_b = tk.StringVar()

        self.load_config()
        self.setup_ui()

    # ==========================================================================
    # INITIALIZATION METHODS
    # ==========================================================================

    def _init_window(self):
        """Initialize main window properties."""
        self.root.title("GSynchro - Synchronization Tool")
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

        # Folder panels
        panels_frame = self._create_panels_frame(main_frame)
        self._create_folder_panels(panels_frame)

        # Status bar
        self._create_status_bar(main_frame)

        # Context menu
        self._create_tree_context_menu()

        # Initial status
        self.status_a.set("by Gino Bogo")

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

            # SSH Panel A
            if "SSH_A" in config:
                self.remote_host_a.set(config["SSH_A"].get("host", ""))
                self.remote_port_a.set(config["SSH_A"].get("port", "22"))
                self.remote_user_a.set(config["SSH_A"].get("username", ""))

            # SSH Panel B
            if "SSH_B" in config:
                self.remote_host_b.set(config["SSH_B"].get("host", ""))
                self.remote_port_b.set(config["SSH_B"].get("port", "22"))
                self.remote_user_b.set(config["SSH_B"].get("username", ""))

            # Filter rules
            if "FILTERS" in config and "rules" in config["FILTERS"]:
                self._load_filter_rules(config["FILTERS"]["rules"])

            # Folder A History
            if "FOLDER_A_HISTORY" in config:
                self.folder_a_history = config["FOLDER_A_HISTORY"]
                if self.folder_a_history:
                    self.folder_a.set(self.folder_a_history[0])

            # Folder B History
            if "FOLDER_B_HISTORY" in config:
                self.folder_b_history = config["FOLDER_B_HISTORY"]
                if self.folder_b_history:
                    self.folder_b.set(self.folder_b_history[0])

        except json.JSONDecodeError:
            self.log(f"Warning: Could not parse {CONFIG_FILE}. Using defaults.")

    def _load_filter_rules(self, rules_data):
        """Load and validate filter rules."""
        processed_rules = []
        for item in rules_data:
            if isinstance(item, str):
                processed_rules.append({"rule": item, "active": True})
            elif isinstance(item, dict) and "rule" in item and "active" in item:
                processed_rules.append(item)
            else:
                self.log(f"Warning: Invalid filter rule format: {item}. Skipping.")
        processed_rules.sort(key=lambda item: item["rule"])
        self.filter_rules = processed_rules

    def save_config(self):
        """Save configuration to file."""
        # Update folder A history
        current_folder_a = self.folder_a.get()

        if current_folder_a:
            if current_folder_a in self.folder_a_history:
                self.folder_a_history.remove(current_folder_a)
            self.folder_a_history.insert(0, current_folder_a)
            self.folder_a_history = self.folder_a_history[:HISTORY_LENGTH]

        # Update folder B history
        current_folder_b = self.folder_b.get()

        if current_folder_b:
            if current_folder_b in self.folder_b_history:
                self.folder_b_history.remove(current_folder_b)
            self.folder_b_history.insert(0, current_folder_b)
            self.folder_b_history = self.folder_b_history[:HISTORY_LENGTH]

        self.filter_rules.sort(key=lambda item: item["rule"])

        config = {
            "WINDOW": {"geometry": self.root.geometry()},
            "SSH_A": {
                "host": self.remote_host_a.get(),
                "port": self.remote_port_a.get(),
                "username": self.remote_user_a.get(),
            },
            "SSH_B": {
                "host": self.remote_host_b.get(),
                "port": self.remote_port_b.get(),
                "username": self.remote_user_b.get(),
            },
            "FILTERS": {"rules": self.filter_rules},
            "FOLDER_A_HISTORY": self.folder_a_history,
            "FOLDER_B_HISTORY": self.folder_b_history,
        }

        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)

    def on_closing(self):
        """Handle window close event."""
        self.save_config()

        # Clean up temporary files
        for temp_file_path in self.temp_files_to_clean:
            try:
                os.remove(temp_file_path)
                self.log(f"Cleaned up temporary file: {temp_file_path}")
            except OSError as e:
                self.log(f"Error cleaning up temporary file {temp_file_path}: {e}")
        self.root.destroy()

    # ==========================================================================
    # UI CREATION METHODS
    # ==========================================================================

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

        # Progress bar style
        style.configure(
            "flat.Horizontal.TProgressbar",
            troughcolor="#E0E0E0",
            background="dodgerblue",
            borderwidth=0,
            relief="flat",
        )

        # Configure Treeview heading font
        style.configure(
            "TTreeview.Heading", font=(self._get_mono_font()[0], 10, "bold")
        )

        # Explicitly set font for tags
        style.configure("TTreeview", rowheight=20)  # Adjust row height
        style.map("TTreeview")  # Reset map to avoid conflicts

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
            ("Compare", self.compare_folders, None),
            ("Sync  ▶", lambda: self.synchronize("left_to_right"), "lightgreen"),
            ("◀  Sync", lambda: self.synchronize("right_to_left"), "lightblue"),
            ("Filters", self.show_filters_dialog, None),
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

    def _create_folder_panels(self, panels_frame):
        """Create folder panels A and B."""
        # Panel A configuration
        panel_a_config = {
            "title": "Folder A",
            "column": 0,
            "padx": (0, 5),
            "button_color": "lightgreen",
            "folder_var": self.folder_a,
            "browse_command": self.browse_folder_a,
            "host_var": self.remote_host_a,
            "port_var": self.remote_port_a,
            "user_var": self.remote_user_a,
            "pass_var": self.remote_pass_a,
            "tree_attr": "tree_a",
            "folder_history": self.folder_a_history,
        }

        # Panel B configuration
        panel_b_config = {
            "title": "Folder B",
            "column": 1,
            "padx": (5, 0),
            "button_color": "lightblue",
            "folder_var": self.folder_b,
            "browse_command": self.browse_folder_b,
            "host_var": self.remote_host_b,
            "port_var": self.remote_port_b,
            "user_var": self.remote_user_b,
            "pass_var": self.remote_pass_b,
            "tree_attr": "tree_b",
            "folder_history": self.folder_b_history,
        }

        self._create_panel(panels_frame, panel_a_config)
        self._create_panel(panels_frame, panel_b_config)

    def _create_panel(self, parent, config):
        """Create an individual folder panel."""
        panel = ttk.LabelFrame(parent, text=config["title"], padding="5")
        panel.grid(
            row=0,
            column=config["column"],
            sticky=tk.NSEW,
            padx=config["padx"],
        )
        panel.columnconfigure(0, weight=0)
        panel.columnconfigure(1, weight=1)  # Make path entry expandable
        panel.rowconfigure(4, weight=1)

        # SSH settings widgets
        ttk.Frame(panel)
        ttk.Label(panel, text="Host:").grid(
            row=0, column=0, padx=5, pady=5, sticky=tk.E
        )
        host_entry = ttk.Entry(panel, textvariable=config["host_var"], width=15)
        host_entry.grid(row=0, column=1, padx=5, pady=5, sticky=tk.EW)

        ttk.Label(panel, text="Port:").grid(
            row=0, column=2, padx=5, pady=5, sticky=tk.E
        )
        port_entry = ttk.Entry(panel, textvariable=config["port_var"], width=8)
        port_entry.grid(row=0, column=3, padx=5, pady=5, sticky=tk.EW)

        ttk.Button(
            panel,
            text="Test",
            command=lambda: self.test_ssh(config["title"]),
            cursor="hand2",
            width=8,
            style=f"{config['button_color']}.TButton",
        ).grid(row=0, column=4, padx=5, pady=5)

        # Username and Password row
        ttk.Label(panel, text="Username:").grid(
            row=1, column=0, padx=5, pady=5, sticky=tk.E
        )
        user_entry = ttk.Entry(panel, textvariable=config["user_var"], width=15)
        user_entry.grid(row=1, column=1, padx=5, pady=5, sticky=tk.EW)

        ttk.Label(panel, text="Password:").grid(
            row=1, column=2, padx=5, pady=5, sticky=tk.E
        )
        pass_entry = ttk.Entry(
            panel, textvariable=config["pass_var"], show="*", width=15
        )
        pass_entry.grid(row=1, column=3, columnspan=2, padx=5, pady=5, sticky=tk.EW)

        # Folder controls
        ttk.Label(panel, text="Path:").grid(
            row=2, column=0, padx=5, pady=5, sticky=tk.E
        )
        path_combobox = ttk.Combobox(
            panel,
            textvariable=config["folder_var"],
            values=config["folder_history"],
            width=20,
        )
        path_combobox.grid(row=2, column=1, columnspan=2, padx=5, pady=5, sticky=tk.EW)

        def on_go():
            panel_name = config["title"].split(" ")[1]
            folder_path = config["folder_var"].get()
            if folder_path:
                self._populate_single_folder(panel_name, folder_path)

        ttk.Button(
            panel,
            text="Go",
            command=on_go,
            cursor="hand2",
            width=8,
            style=f"{config['button_color']}.TButton",
        ).grid(row=2, column=3, padx=5, pady=5)

        ttk.Button(
            panel,
            text="Browse",
            command=config["browse_command"],
            cursor="hand2",
            width=8,
            style=f"{config['button_color']}.TButton",
        ).grid(row=2, column=4, padx=5, pady=5)

        # Tree view
        tree = self._create_tree_view(panel)
        tree.grid(row=4, column=0, columnspan=5, pady=(10, 0), sticky=tk.NSEW)

        # Vertical Scrollbar
        v_scrollbar = ttk.Scrollbar(panel, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=v_scrollbar.set)
        v_scrollbar.grid(row=4, column=5, pady=(10, 0), sticky=tk.NS)

        # Horizontal Scrollbar
        h_scrollbar = ttk.Scrollbar(panel, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(xscrollcommand=h_scrollbar.set)
        h_scrollbar.grid(row=5, column=0, columnspan=5, sticky=tk.EW)

        # Bind events
        tree.bind("<Button-1>", self._on_tree_click)
        tree.bind("<Button-3>", self._show_tree_context_menu)

        # Store tree reference
        if config["tree_attr"] == "tree_a":
            self.tree_a = tree
        else:
            self.tree_b = tree

    def _create_tree_view(self, parent):
        """Create file tree view."""
        tree = ttk.Treeview(
            parent,
            columns=("sync", "size", "modified", "status"),
            show="tree headings",
        )

        # Configure columns
        tree.heading("#0", text="Name")
        tree.column("#0", width=200)

        tree.heading("sync", text="Sync")
        tree.column("sync", width=40, anchor="center")

        headings_config = [
            ("size", "Size", 80),
            ("modified", "Modified", 120),
            ("status", "Status", 100),
        ]

        for col, text, width in headings_config:
            tree.heading(col, text=text)
            tree.column(col, width=width, anchor=tk.E)

        # Define a monospace font
        font_tuple = self._get_mono_font()

        # Configure tags for different status colors
        colors = {
            "green": "green",
            "orange": "orange",
            "blue": "blue",
            "red": "red",
            "black": "black",
        }
        for tag, color in colors.items():
            tree.tag_configure(tag, foreground=color, font=font_tuple)

        return tree

    def _create_status_bar(self, parent):
        """Create status bar with progress indicator."""
        status_frame = ttk.Frame(parent, relief="flat", padding="2")
        status_frame.grid(row=2, column=0, columnspan=3, sticky=tk.EW, pady=(5, 0))

        status_frame.columnconfigure(0, weight=1)
        status_frame.columnconfigure(1, weight=1)

        # Status labels
        self.status_label_a = ttk.Label(
            status_frame, textvariable=self.status_a, anchor=tk.W
        )
        self.status_label_a.grid(row=0, column=0, sticky=tk.EW, padx=0)

        self.status_label_b = ttk.Label(
            status_frame, textvariable=self.status_b, anchor=tk.W
        )
        self.status_label_b.grid(row=0, column=1, sticky=tk.EW, padx=0)

        # Progress bar
        self.progress_bar = ttk.Progressbar(
            status_frame, orient="horizontal", style="flat.Horizontal.TProgressbar"
        )
        self.progress_bar.grid(row=0, column=0, columnspan=2, sticky=tk.EW, padx=0)
        self.progress_bar.grid_remove()

    def _create_tree_context_menu(self):
        """Create context menu for tree views."""
        self.tree_context_menu = tk.Menu(self.root, tearoff=0)
        self.tree_context_menu.add_command(
            label="Open...", command=self._open_selected_item
        )
        self.tree_context_menu.add_command(
            label="Delete", command=self._delete_selected_item
        )
        self.tree_context_menu.add_separator()
        self.tree_context_menu.add_command(label="Select All", command=self._select_all)
        self.tree_context_menu.add_command(
            label="Deselect All", command=self._deselect_all
        )

    # ==========================================================================
    # FOLDER BROWSING METHODS
    # ==========================================================================

    def browse_folder_a(self):
        """Browse for folder A."""
        self._browse_folder("A")

    def browse_folder_b(self):
        """Browse for folder B."""
        self._browse_folder("B")

    def _browse_folder(self, panel_name):
        """Browse for folder (local or remote)."""
        # Determine if remote browsing is needed
        if panel_name == "A":
            is_remote = self._has_ssh_a()
            folder_var = self.folder_a
            folder_history = self.folder_a_history
        else:
            is_remote = self._has_ssh_b()
            folder_var = self.folder_b
            folder_history = self.folder_b_history

        initial_path = folder_var.get()
        if not initial_path and folder_history:
            initial_path = folder_history[0]

        if is_remote:
            selected_path, ssh_client = self._browse_remote(
                folder_var, f"Panel {panel_name}", initial_path
            )
            if selected_path:
                self._populate_single_folder(panel_name, selected_path, ssh_client)
        else:
            folder = filedialog.askdirectory(initialdir=initial_path)
            if folder:
                self._update_folder_history(panel_name, folder_var, folder)
                folder_var.set(folder)
                self._populate_single_folder(panel_name, folder)

    # ==========================================================================
    # SSH METHODS
    # ==========================================================================

    def test_ssh(self, panel_name):
        """Test SSH connection for panel."""
        if panel_name == "Folder A":
            host_var, user_var, pass_var, port_var = (
                self.remote_host_a,
                self.remote_user_a,
                self.remote_pass_a,
                self.remote_port_a,
            )
        else:
            host_var, user_var, pass_var, port_var = (
                self.remote_host_b,
                self.remote_user_b,
                self.remote_pass_b,
                self.remote_port_b,
            )

        def test_thread():
            try:
                if not all(
                    [host_var.get(), user_var.get(), pass_var.get(), port_var.get()]
                ):
                    raise ValueError("Host, username, password, and port are required.")

                self.log(f"Testing SSH connection for {panel_name}...")
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(
                    host_var.get(),
                    username=user_var.get(),
                    password=pass_var.get(),
                    port=int(port_var.get()),
                )
                client.close()

                self.log(f"✓ SSH connection successful for {panel_name}")
                messagebox.showinfo(
                    "Success", f"SSH connection established for {panel_name}!"
                )
            except Exception as e:
                self.log(f"✗ SSH connection failed for {panel_name}: {str(e)}")
                messagebox.showerror(
                    "Error", f"SSH connection failed for {panel_name}: {str(e)}"
                )

        threading.Thread(target=test_thread, daemon=True).start()

    def _create_ssh_client(self, host, username, password, port):
        """Create an SSH client instance."""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(host, username=username, password=password, port=port)
        return client

    def _create_ssh_for_panel(self, panel_name) -> paramiko.SSHClient:
        """Create SSH client for a panel."""
        if panel_name == "A":
            return self._create_ssh_client(
                self.remote_host_a.get(),
                self.remote_user_a.get(),
                self.remote_pass_a.get(),
                int(self.remote_port_a.get()),
            )
        elif panel_name == "B":
            return self._create_ssh_client(
                self.remote_host_b.get(),
                self.remote_user_b.get(),
                self.remote_pass_b.get(),
                int(self.remote_port_b.get()),
            )
        else:
            raise ValueError(f"Invalid panel name: {panel_name}")

    def _has_ssh_a(self):
        """Check if Panel A has SSH credentials."""
        return all(
            [
                self.remote_host_a.get(),
                self.remote_user_a.get(),
                self.remote_pass_a.get(),
            ]
        )

    def _has_ssh_b(self):
        """Check if Panel B has SSH credentials."""
        return all(
            [
                self.remote_host_b.get(),
                self.remote_user_b.get(),
                self.remote_pass_b.get(),
            ]
        )

    def _close_ssh(self):
        """Close any open SSH connections."""
        if self.ssh_client_a:
            self.ssh_client_a.close()
            self.ssh_client_a = None
        if self.ssh_client_b:
            self.ssh_client_b.close()
            self.ssh_client_b = None

    # ==========================================================================
    # REMOTE FOLDER BROWSING
    # ==========================================================================

    def _browse_remote(self, folder_var, panel_name, initial_path=""):
        """Browse remote folder via SSH."""
        try:
            if panel_name == "Panel A":
                ssh_client = self._create_ssh_client(
                    self.remote_host_a.get(),
                    self.remote_user_a.get(),
                    self.remote_pass_a.get(),
                    int(self.remote_port_a.get()),
                )
            else:
                ssh_client = self._create_ssh_client(
                    self.remote_host_b.get(),
                    self.remote_user_b.get(),
                    self.remote_pass_b.get(),
                    int(self.remote_port_b.get()),
                )

            current_path = initial_path or folder_var.get()
            stdin, stdout, stderr = ssh_client.exec_command("pwd")
            remote_path = stdout.read().decode().strip()

            if not current_path or not current_path.startswith(remote_path):
                current_path = remote_path

            selected_path, keep_alive = self._show_remote_dialog(
                ssh_client, folder_var, current_path, panel_name
            )
            if selected_path:
                self._update_folder_history(
                    panel_name.split(" ")[1], folder_var, selected_path
                )
            return selected_path, ssh_client if keep_alive else None
        except Exception as e:
            messagebox.showerror(
                "Error", f"Failed to connect to remote {panel_name}: {str(e)}"
            )
            return None, None

    def _show_remote_dialog(self, ssh_client, folder_var, current_path, panel_name):
        """Show remote folder browser dialog."""
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Browse Remote Folder - {panel_name}")
        dialog.geometry("500x400")
        dialog.minsize(500, 400)
        dialog.transient(self.root)
        dialog.grab_set()

        # Main frame
        main_dialog_frame = ttk.Frame(dialog, padding="10")
        main_dialog_frame.pack(fill=tk.BOTH, expand=True)

        result = tk.StringVar()
        keep_ssh_alive = tk.BooleanVar(value=False)

        # Top: Path display and entry
        path_frame = ttk.Frame(main_dialog_frame)
        path_frame.pack(fill=tk.X, pady=(0, 5))

        path_var = tk.StringVar(value=current_path)
        ttk.Label(path_frame, text="Current Path:").pack(side=tk.LEFT)
        path_entry = ttk.Entry(path_frame, textvariable=path_var)
        path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        def go_to_path(event=None):
            load_folders(path_var.get())

        ttk.Button(path_frame, text="Go", command=go_to_path, cursor="hand2").pack(
            side=tk.LEFT, padx=(5, 0)
        )
        path_entry.bind("<Return>", go_to_path)

        # Middle: Main content
        content_frame = ttk.Frame(main_dialog_frame)
        content_frame.pack(fill=tk.BOTH, expand=True)

        listbox = tk.Listbox(content_frame)
        scrollbar = ttk.Scrollbar(
            content_frame, orient=tk.VERTICAL, command=listbox.yview
        )
        listbox.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        def load_folders(path):
            try:
                listbox.delete(0, tk.END)
                path_var.set(path)

                if path != "/":
                    listbox.insert(tk.END, "..")

                command = (
                    f"find '{path}' -maxdepth 1 -mindepth 1 -type d -printf '%f\\n'"
                )
                stdin, stdout, stderr = ssh_client.exec_command(command)
                error = stderr.read().decode().strip()
                if error:
                    raise Exception(error)

                for line in stdout:
                    listbox.insert(tk.END, line.strip())
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load folders: {str(e)}")

        def on_select(event):
            selection = listbox.curselection()
            if selection:
                selected = listbox.get(selection[0])
                if selected == "..":
                    parent_path = "/".join(path_var.get().split("/")[:-1]) or "/"
                    load_folders(parent_path)
                else:
                    new_path = path_var.get().rstrip("/") + "/" + selected
                    load_folders(new_path)

        def on_select_folder():
            keep_ssh_alive.set(True)
            result.set(path_var.get())
            dialog.destroy()

        def on_cancel():
            keep_ssh_alive.set(False)
            ssh_client.close()
            dialog.destroy()

        # Bottom: Buttons
        button_frame = ttk.Frame(main_dialog_frame)
        button_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(5, 0))

        button_container = ttk.Frame(button_frame)
        button_container.pack()

        ttk.Button(
            button_container, text="Cancel", command=on_cancel, cursor="hand2"
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            button_container, text="Select", command=on_select_folder, cursor="hand2"
        ).pack(side=tk.LEFT, padx=5)

        # Bind events and initial actions
        listbox.bind("<Double-Button-1>", on_select)
        load_folders(current_path)

        # Center dialog and wait
        self._center_dialog(dialog)
        self.root.wait_window(dialog)

        selected_path = result.get()
        folder_var.set(selected_path)
        return selected_path, keep_ssh_alive.get()

    # ==========================================================================
    # FOLDER SCANNING METHODS
    # ==========================================================================

    def _populate_single_folder(
        self, panel, folder_path, ssh_client=None, active_rules=None
    ):
        """Populate single folder tree view."""

        def populate_thread():
            try:
                self.root.after(0, self.start_progress, panel)

                # Determine which panel to populate
                if panel == "A":
                    use_ssh = self._has_ssh_a()
                    if use_ssh:
                        self.ssh_client_a = ssh_client or self._create_ssh_client(
                            self.remote_host_a.get(),
                            self.remote_user_a.get(),
                            self.remote_pass_a.get(),
                            int(self.remote_port_a.get()),
                        )
                    rules = self._get_active_filters()
                    files = self._scan_folder(
                        folder_path, use_ssh, self.ssh_client_a, "A", rules
                    )
                    self.files_a = files
                    self._update_status("A", files)
                else:
                    use_ssh = self._has_ssh_b()
                    if use_ssh:
                        self.ssh_client_b = ssh_client or self._create_ssh_client(
                            self.remote_host_b.get(),
                            self.remote_user_b.get(),
                            self.remote_pass_b.get(),
                            int(self.remote_port_b.get()),
                        )
                    rules = self._get_active_filters()
                    files = self._scan_folder(
                        folder_path, use_ssh, self.ssh_client_b, "B", rules
                    )
                    self.files_b = files
                    self._update_status("B", files)

                # Update tree view
                tree_structure = self._build_tree_structure(files)
                tree = getattr(self, f"tree_{panel.lower()}")  # type: ignore

                def populate_and_adjust():
                    self._batch_populate_tree(tree, tree_structure, rules)
                    self._adjust_tree_column_widths(tree)

                self.root.after(0, populate_and_adjust)

            except Exception as e:
                self.log(f"Error populating {panel} folder: {str(e)}")
                messagebox.showerror(
                    "Error", f"Failed to populate {panel} folder: {str(e)}"
                )
            finally:
                self.root.after(0, self.stop_progress)
                self._close_ssh()

        thread = threading.Thread(target=populate_thread, daemon=True)
        thread.start()
        return thread

    def _scan_folder(self, folder_path, use_ssh, ssh_client, panel_name, rules=None):
        """Scan folder (local or remote)."""
        if rules is None:
            rules = []

        if use_ssh:
            self.log(f"Using SSH for folder {panel_name} scan")
            try:
                files = self._scan_remote(folder_path, ssh_client, rules)
                self.log(f"Found {len(files)} files in folder {panel_name}")
                return files
            except Exception as e:
                self.log(f"SSH connection failed for Folder {panel_name}: {str(e)}")
                return {}
        else:
            self.log(f"Using local folder scan for folder {panel_name}")
            files = self._scan_local(folder_path, rules)
            self.log(f"Found {len(files)} files in folder {panel_name}")
            return files

    def _scan_local(self, folder_path, rules=None):
        """Scan a local folder."""
        files = {}
        if rules is None:
            rules = []

        try:
            for root, dirs, filenames in os.walk(
                folder_path, topdown=True, followlinks=True
            ):
                # Filter directories
                dirs[:] = [
                    d
                    for d in dirs
                    if not any(
                        fnmatch.fnmatch(
                            os.path.relpath(os.path.join(root, d), folder_path), pattern
                        )
                        or (
                            pattern.endswith("/")
                            and fnmatch.fnmatch(
                                os.path.relpath(os.path.join(root, d), folder_path),
                                pattern.rstrip("/"),
                            )
                        )
                        for pattern in rules
                    )
                ]

                # Add directories
                for dirname in dirs:
                    full_path = os.path.join(root, dirname)
                    rel_path = os.path.relpath(full_path, folder_path)
                    files[rel_path.replace(os.sep, "/")] = {"type": "dir"}

                # Add files
                for filename in filenames:
                    full_path = os.path.join(root, filename)
                    rel_path = os.path.relpath(full_path, folder_path)

                    if any(fnmatch.fnmatch(rel_path, r) for r in rules):
                        continue

                    try:
                        stat = os.stat(full_path)
                        files[rel_path] = {
                            "size": stat.st_size,
                            "modified": stat.st_mtime,
                            "full_path": full_path,
                            "type": "file",
                        }
                    except OSError as e:
                        self.log(f"Error accessing {full_path}: {str(e)}")
        except Exception as e:
            self.log(f"Error scanning folder {folder_path}: {str(e)}")

        self.log(f"Local folder scan ended for {folder_path}")
        return files

    def _scan_remote(self, folder_path, ssh_client, rules=None):
        """Scan remote folder using SSH."""
        files = {}
        if rules is None:
            rules = []

        try:
            stdin, stdout, stderr = ssh_client.exec_command(
                f"find '{folder_path}' -mindepth 1 -exec stat -c '%n|%F|%s|%Y' {{}} \\; 2>/dev/null"
            )

            for line in stdout:
                line = line.strip()
                if line:
                    try:
                        filepath, filetype, size, mtime = line.split("|")

                        if filepath.startswith(folder_path):
                            rel_path = filepath[len(folder_path) :].lstrip("/")
                        else:
                            continue

                        if any(fnmatch.fnmatch(rel_path, r) for r in rules):
                            continue

                        if "directory" in filetype:
                            files[rel_path] = {"type": "dir"}
                        else:
                            files[rel_path] = {
                                "size": int(size),
                                "modified": float(mtime),
                                "full_path": filepath,
                                "type": "file",
                            }
                    except ValueError:
                        continue
        except Exception as e:
            self.log(f"Error scanning remote folder {folder_path}: {str(e)}")

        self.log(f"Remote folder scan ended for {folder_path}")
        return files

    # ==========================================================================
    # TREE VIEW METHODS
    # ==========================================================================

    def _build_tree_structure(self, files):
        """Build hierarchical dictionary."""
        tree_structure = {}
        for filepath in sorted(files.keys()):
            parts = filepath.replace(os.sep, "/").split("/")
            current_level = tree_structure

            for part in parts[:-1]:
                node = current_level.get(part)
                is_dir_struct = isinstance(node, dict) and node.get("type") != "file"
                if not is_dir_struct:
                    current_level[part] = {".": node} if node else {}
                current_level = current_level[part]

            final_part = parts[-1]
            if final_part:
                current_level[final_part] = files[filepath]

        return tree_structure

    def _batch_populate_tree(self, tree, structure, filter_rules=None):
        """Populate treeview from structure."""
        if not tree:
            return

        # Clear existing items
        for item in tree.get_children():
            tree.delete(item)

        if filter_rules is None:
            current_filter_rules = []
        else:
            current_filter_rules = filter_rules

        def insert_items(
            parent_node, data, filter_rules_for_insertion, current_path_prefix=""
        ):
            items = sorted(data.items())
            for name, content in items:
                if name == ".":
                    continue

                # Apply filter rules
                if any(
                    fnmatch.fnmatch(
                        os.path.join(current_path_prefix, name).replace(os.sep, "/"),
                        pattern,
                    )
                    for pattern in filter_rules_for_insertion
                ):
                    continue

                if isinstance(content, dict) and "size" not in content:
                    # Directory
                    node = tree.insert(
                        parent_node,
                        "end",
                        text=name,
                        values=(self.UNCHECKED_CHAR, "", "", ""),
                        tags=("black",),
                        open=False,
                    )
                    insert_items(
                        node,
                        content,
                        filter_rules_for_insertion,
                        os.path.join(current_path_prefix, name),
                    )
                else:
                    # File
                    if content and "size" in content:
                        tree.insert(
                            parent_node,
                            "end",
                            text=name,
                            values=(
                                self.UNCHECKED_CHAR,
                                self._format_size(content["size"]),
                                self._format_time(content["modified"]),
                                "",
                            ),
                            tags=("black",),
                        )

        insert_items("", structure, current_filter_rules, "")

    def _build_tree_map(self, tree, parent_item="", path=""):
        """Build path to item ID map for a tree."""
        path_map = {}
        if not tree:
            return path_map

        for item_id in tree.get_children(parent_item):
            item_text = tree.item(item_id, "text")
            current_path = os.path.join(path, item_text)
            path_map[current_path] = item_id
            if tree.get_children(item_id):
                path_map.update(self._build_tree_map(tree, item_id, current_path))

        return path_map

    def _update_tree_item(self, tree, item_id, rel_path, status, status_color):
        """Update tree item with status."""
        if tree is None:
            return

        current_values = tree.item(item_id, "values")
        check_char = (
            self.CHECKED_CHAR
            if self.sync_states.get(rel_path, False)
            else self.UNCHECKED_CHAR
        )

        tree.item(
            item_id,
            values=(
                check_char,
                current_values[1],
                current_values[2],
                status,
            ),
            tags=(status_color,),
        )

    # ==========================================================================
    # COMPARISON METHODS
    # ==========================================================================

    def compare_folders(self, active_rules=None):
        """Compare files between folders."""

        def compare_thread():
            self.log("Starting folder comparison...")

            path_a = self.folder_a.get()
            path_b = self.folder_b.get()

            if not path_a or not path_b:
                messagebox.showerror("Error", "Please select both folders to compare")
                return

            try:
                # Use existing data for comparison
                total_items = len(set(self.files_a.keys()) | set(self.files_b.keys()))
                self.root.after(
                    0, self.start_progress, None, total_items, "Comparing..."
                )

                # Set up SSH connections if needed
                use_ssh_a = self._has_ssh_a()
                use_ssh_b = self._has_ssh_b()

                # Re-establish SSH clients if closed
                if use_ssh_a and (
                    self.ssh_client_a is None
                    or self.ssh_client_a.get_transport() is None
                ):
                    self.ssh_client_a = self._create_ssh_for_panel("A")
                if use_ssh_b and (
                    self.ssh_client_b is None
                    or self.ssh_client_b.get_transport() is None
                ):
                    self.ssh_client_b = self._create_ssh_for_panel("B")

                # Perform comparison
                self._update_trees_with_comparison(
                    self.files_a, self.files_b, use_ssh_a, use_ssh_b
                )
                self.log("Folder comparison completed")

            except Exception as e:
                self.log(f"Error during comparison: {str(e)}")
            finally:
                self.root.after(0, self.stop_progress)
                self._close_ssh()

        threading.Thread(target=compare_thread, daemon=True).start()

    def _prepare_comparison_data(self):
        """Prepare data structures needed for comparison."""
        tree_a_map = self._build_tree_map(self.tree_a)
        tree_b_map = self._build_tree_map(self.tree_b)
        all_visible_paths = set(tree_a_map.keys()) | set(tree_b_map.keys())
        self.sync_states.clear()
        return tree_a_map, tree_b_map, all_visible_paths

    def _calculate_item_statuses(
        self, all_visible_paths, files_a, files_b, use_ssh_a, use_ssh_b
    ):
        """Calculate the status of all files and directories."""
        item_statuses = {}
        dirty_folders = set()
        stats = {"identical": 0, "different": 0, "only_a": 0, "only_b": 0}

        # First pass: Determine file and unique directory statuses
        for rel_path in sorted(all_visible_paths):
            file_a_info = files_a.get(rel_path)
            file_b_info = files_b.get(rel_path)
            is_file = (file_a_info and file_a_info.get("type") == "file") or (
                file_b_info and file_b_info.get("type") == "file"
            )

            if is_file:
                status, status_color = self._compare_files(
                    file_a_info, file_b_info, use_ssh_a, use_ssh_b
                )
                item_statuses[rel_path] = (status, status_color)

                if status == "Identical":
                    stats["identical"] += 1
                    self.sync_states[rel_path] = False
                else:
                    if status == "Different":
                        stats["different"] += 1
                    elif status == "Only in Folder A":
                        stats["only_a"] += 1
                    elif status == "Only in Folder B":
                        stats["only_b"] += 1
                    self.sync_states[rel_path] = True
                    # Mark parent directories as dirty
                    current_parent = os.path.dirname(rel_path)
                    while current_parent and current_parent not in dirty_folders:
                        dirty_folders.add(current_parent)
                        current_parent = os.path.dirname(current_parent)
            else:  # It's a directory
                is_dir_in_a = file_a_info and file_a_info.get("type") == "dir"
                is_dir_in_b = file_b_info and file_b_info.get("type") == "dir"

                if is_dir_in_a and not is_dir_in_b:
                    item_statuses[rel_path] = ("Only in Folder A", "blue")
                    self.sync_states[rel_path] = True
                    dirty_folders.add(os.path.dirname(rel_path))
                elif is_dir_in_b and not is_dir_in_a:
                    item_statuses[rel_path] = ("Only in Folder B", "red")
                    self.sync_states[rel_path] = True
                    dirty_folders.add(os.path.dirname(rel_path))

        # Second pass: Determine status for shared directories
        for rel_path in sorted(all_visible_paths):
            if (
                files_a.get(rel_path, {}).get("type") == "dir"
                and files_b.get(rel_path, {}).get("type") == "dir"
            ):
                if rel_path in dirty_folders:
                    status, status_color = "Contains differences", "orange"
                    self.sync_states[rel_path] = True
                else:
                    status, status_color = "Identical", "green"
                    self.sync_states[rel_path] = False
                item_statuses[rel_path] = (status, status_color)

        return item_statuses, stats

    def _apply_comparison_to_ui(self, item_statuses, stats, tree_a_map, tree_b_map):
        """Update the UI with the results of the comparison."""
        for rel_path, (status, status_color) in item_statuses.items():
            self.root.after(0, self.update_progress, 1)
            if rel_path in tree_a_map:
                self._update_tree_item(
                    self.tree_a, tree_a_map[rel_path], rel_path, status, status_color
                )
            if rel_path in tree_b_map:
                self._update_tree_item(
                    self.tree_b, tree_b_map[rel_path], rel_path, status, status_color
                )

        status_summary = f"Identical: {stats['identical']}, "
        status_summary += f"Different: {stats['different']}, "
        status_summary += f"Only in A: {stats['only_a']}, "
        status_summary += f"Only in B: {stats['only_b']}"
        self.status_a.set(status_summary)
        self.status_b.set("")

    def _update_trees_with_comparison(self, files_a, files_b, use_ssh_a, use_ssh_b):
        """Update tree views with comparison results."""
        tree_a_map, tree_b_map, all_visible_paths = self._prepare_comparison_data()

        item_statuses, stats = self._calculate_item_statuses(
            all_visible_paths, files_a, files_b, use_ssh_a, use_ssh_b
        )

        self._apply_comparison_to_ui(item_statuses, stats, tree_a_map, tree_b_map)

        # Configure tags
        for tree in [self.tree_a, self.tree_b]:
            if tree:
                tree.tag_configure("black", foreground="black")
                tree.tag_configure("green", foreground="green")
                tree.tag_configure("orange", foreground="orange")
                tree.tag_configure("blue", foreground="blue")
                tree.tag_configure("red", foreground="red")

        # Adjust column widths
        if self.tree_a:
            self._adjust_tree_column_widths(self.tree_a)
        if self.tree_b:
            self._adjust_tree_column_widths(self.tree_b)

    def _compare_files(self, file_a, file_b, use_ssh_a, use_ssh_b):
        """Compare two files and return status."""
        if file_a and file_b:
            is_a_file = file_a.get("type") == "file"
            is_b_file = file_b.get("type") == "file"

            if not is_a_file or not is_b_file:
                return "Type conflict", "orange"
            elif (
                isinstance(file_a, dict)
                and "size" in file_a
                and isinstance(file_b, dict)
                and "size" in file_b
            ):
                # Files have same size, proceed with chunked comparison
                file_a_handle = None
                file_b_handle = None
                sftp_a = None
                sftp_b = None

                try:
                    # Open file A
                    if use_ssh_a:
                        if not self.ssh_client_a:
                            raise ConnectionError(
                                "SSH client for Panel A is not connected."
                            )
                        sftp_a = self.ssh_client_a.open_sftp()  # type: ignore
                        file_a_handle = sftp_a.open(file_a["full_path"], "rb")
                    else:
                        file_a_handle = open(file_a["full_path"], "rb")

                    # Open file B
                    if use_ssh_b:
                        if not self.ssh_client_b:
                            raise ConnectionError(
                                "SSH client for Panel B is not connected."
                            )
                        sftp_b = self.ssh_client_b.open_sftp()  # type: ignore
                        file_b_handle = sftp_b.open(file_b["full_path"], "rb")
                    else:
                        file_b_handle = open(file_b["full_path"], "rb")

                    while True:
                        chunk_a = file_a_handle.read(CHUNK_SIZE)
                        chunk_b = file_b_handle.read(CHUNK_SIZE)

                        if chunk_a != chunk_b:
                            return "Different", "orange"

                        if not chunk_a:  # Both chunks are empty, files are identical
                            return "Identical", "green"

                except Exception as e:
                    self.log(f"Error during chunked file comparison: {e}")
                    return "Error", "black"  # Indicate an error occurred
                finally:
                    if file_a_handle:
                        file_a_handle.close()
                    if file_b_handle:
                        file_b_handle.close()
                    if sftp_a:
                        sftp_a.close()
                    if sftp_b:
                        sftp_b.close()
            else:
                return "Different", "orange"
        elif file_a:
            return "Only in Folder A", "blue"
        else:
            return "Only in Folder B", "red"

    # ==========================================================================
    # SYNCHRONIZATION METHODS
    # ==========================================================================

    def synchronize(self, direction):
        """Synchronize files between folders."""

        def sync_thread():
            self.log(f"Starting synchronization: {direction}")

            # Determine source and target
            if direction == "left_to_right":
                source_path = self.folder_a.get()
                target_path = self.folder_b.get()
                source_files_dict = self.files_a
            else:
                source_path = self.folder_b.get()
                target_path = self.folder_a.get()
                source_files_dict = self.files_b

            if not source_path or not target_path:
                messagebox.showerror(
                    "Error", "Source and target folder paths must be set."
                )
                return

            try:
                # Set up SSH connections
                use_ssh_a = self._has_ssh_a()
                use_ssh_b = self._has_ssh_b()

                if use_ssh_a:
                    self.ssh_client_a = self._create_ssh_client(
                        self.remote_host_a.get(),
                        self.remote_user_a.get(),
                        self.remote_pass_a.get(),
                        int(self.remote_port_a.get()),
                    )

                if use_ssh_b:
                    self.ssh_client_b = self._create_ssh_client(
                        self.remote_host_b.get(),
                        self.remote_user_b.get(),
                        self.remote_pass_b.get(),
                        int(self.remote_port_b.get()),
                    )

                # Get files to copy
                files_to_copy = self._get_files_to_copy(source_files_dict)

                if not files_to_copy:
                    self.log("No files selected for synchronization.")
                    messagebox.showinfo(
                        "Sync",
                        "No files are checked for synchronization or folders are already in sync.",
                    )
                    return

                # Start progress bar
                self.root.after(
                    0,
                    self.start_progress,
                    None,
                    len(files_to_copy),
                    "Synchronizing...",
                )

                # Determine source and target SSH connections
                if direction == "left_to_right":
                    source_ssh, target_ssh = self.ssh_client_a, self.ssh_client_b
                    source_use_ssh, target_use_ssh = use_ssh_a, use_ssh_b
                else:
                    source_ssh, target_ssh = self.ssh_client_b, self.ssh_client_a
                    source_use_ssh, target_use_ssh = use_ssh_b, use_ssh_a

                # Perform synchronization
                self._perform_sync(
                    files_to_copy,
                    source_files_dict,
                    target_path,
                    source_ssh,
                    target_ssh,
                    source_use_ssh,
                    target_use_ssh,
                )

                # Rescan target folder
                self.log("Synchronization completed. Refreshing view...")
                self._rescan_target_folder(
                    direction,
                    target_path,
                    use_ssh_a,
                    use_ssh_b,
                )

                # Trigger UI refresh on the main thread
                self.root.after(
                    0, lambda: self._refresh_ui_after_sync(use_ssh_a, use_ssh_b)
                )

                self.log("Synchronization completed")
                self.status_a.set("Synchronization completed successfully!")
                messagebox.showinfo(
                    "Success", "Synchronization completed successfully!"
                )

            except Exception as e:
                self.log(f"Synchronization failed: {str(e)}")
                messagebox.showerror("Error", f"Synchronization failed: {str(e)}")
            finally:
                self.root.after(0, self.stop_progress)
                self._close_ssh()

        threading.Thread(target=sync_thread, daemon=True).start()

    def _get_files_to_copy(self, source_files_dict):
        """Get list of files to copy based on sync states."""
        files_to_sync = []
        for rel_path, is_checked in self.sync_states.items():
            if not is_checked:
                continue

            source_item = source_files_dict.get(rel_path)

            if source_item:
                if source_item.get("type") == "file":
                    files_to_sync.append(rel_path)
                elif source_item.get("type") == "dir":
                    # Add all files in directory
                    dir_prefix = rel_path.rstrip(os.sep) + os.sep
                    for file_path, file_info in source_files_dict.items():
                        if file_info.get("type") == "file" and file_path.startswith(
                            dir_prefix
                        ):
                            files_to_sync.append(file_path)
        return sorted(list(set(files_to_sync)))

    def _perform_sync(
        self,
        files_to_copy,
        source_files_dict,
        target_path,
        source_ssh,
        target_ssh,
        source_use_ssh,
        target_use_ssh,
    ):
        """Perform file synchronization."""
        # Determine sync type based on source and target locations
        if source_use_ssh and target_use_ssh:
            self._sync_remote_to_remote(
                files_to_copy, source_files_dict, target_path, source_ssh, target_ssh
            )
        elif source_use_ssh:
            self._sync_remote_to_local(
                files_to_copy, source_files_dict, target_path, source_ssh
            )
        elif target_use_ssh:
            self._sync_local_to_remote(
                files_to_copy, source_files_dict, target_path, target_ssh
            )
        else:
            self._sync_local_to_local(files_to_copy, source_files_dict, target_path)

    def _rescan_target_folder(self, direction, target_path, use_ssh_a, use_ssh_b):
        """Rescan target folder after sync."""
        if direction == "left_to_right":
            self.log("Rescanning Folder B...")
            self.files_b = self._scan_folder(
                target_path,
                use_ssh_b,
                self.ssh_client_b,
                "B",
            )
            self._update_status("B", self.files_b)
        else:
            self.log("Rescanning Folder A...")
            self.files_a = self._scan_folder(
                target_path,
                use_ssh_a,
                self.ssh_client_a,
                "A",
            )
            self._update_status("A", self.files_a)

    def _sync_local_to_local(self, files_to_copy, source_files_dict, target_path):
        """Sync between local folders."""
        self.log(f"Syncing local files to {target_path}")

        for rel_path in files_to_copy:
            source_file = source_files_dict[rel_path]["full_path"]
            target_file = os.path.join(target_path, rel_path)

            # Create target directory if needed
            target_dir = os.path.dirname(target_file)
            os.makedirs(target_dir, exist_ok=True)

            # Ensure target is writable
            if os.path.exists(target_file) and not os.access(target_file, os.W_OK):
                current_mode = os.stat(target_file).st_mode
                os.chmod(target_file, current_mode | 0o200)

            self.log(f"Copying: {rel_path}")
            shutil.copy2(source_file, target_file)
            self.root.after(0, self.update_progress)

    def _sync_local_to_remote(
        self, files_to_copy, source_files_dict, remote_path, ssh_client
    ):
        """Sync local to remote using SCP."""
        self.log(f"Syncing local files to remote {remote_path}")

        with SCPClient(ssh_client.get_transport()) as scp:
            for rel_path in files_to_copy:
                local_file = source_files_dict[rel_path]["full_path"]
                remote_file = os.path.join(remote_path, rel_path).replace(os.sep, "/")

                # Create remote directory
                remote_dir = os.path.dirname(remote_file).replace(os.sep, "/")
                try:
                    sftp = ssh_client.open_sftp()
                    sftp.stat(remote_dir)
                except FileNotFoundError:
                    self.log(f"Creating remote directory: {remote_dir}")
                    stdin, stdout, stderr = ssh_client.exec_command(
                        f"mkdir -p '{remote_dir}'"
                    )
                    stderr.read()

                scp.put(local_file, remote_file)
                self.root.after(0, self.update_progress)

    def _sync_remote_to_local(
        self, files_to_copy, source_files_dict, local_path, ssh_client
    ):
        """Sync remote to local using SCP."""
        self.log(f"Syncing remote files to local {local_path}")

        with SCPClient(ssh_client.get_transport()) as scp:
            for rel_path in files_to_copy:
                remote_file = source_files_dict[rel_path]["full_path"]
                local_file = os.path.join(local_path, rel_path)

                # Create local directory
                local_dir = os.path.dirname(local_file)
                os.makedirs(local_dir, exist_ok=True)

                self.log(f"Downloading: {rel_path}")
                scp.get(remote_file, local_file)
                self.root.after(0, self.update_progress)

    def _sync_remote_to_remote(
        self, files_to_copy, source_files_dict, target_path, source_ssh, target_ssh
    ):
        """Sync between remote folders."""
        self.log(f"Syncing remote files to remote {target_path}")

        for rel_path in files_to_copy:
            source_file_path = source_files_dict[rel_path]["full_path"]
            target_file_path = os.path.join(target_path, rel_path).replace(os.sep, "/")

            # Create target directory
            target_dir = os.path.dirname(target_file_path)
            target_ssh.exec_command(f"mkdir -p '{target_dir}'")

            # Stream through local temp file
            with SCPClient(source_ssh.get_transport()) as scp_source:
                with SCPClient(target_ssh.get_transport()) as scp_target:
                    self.log(f"Copying remote-to-remote: {rel_path}")
                    with tempfile.NamedTemporaryFile() as temp_f:
                        # Download from source
                        scp_source.get(source_file_path, temp_f.name)
                        # Upload to target
                        scp_target.put(temp_f.name, target_file_path)

            self.root.after(0, self.update_progress)

    # ==========================================================================
    # FILTER MANAGEMENT
    # ==========================================================================

    def show_filters_dialog(self):
        """Show filter rules dialog."""
        # Create a temporary copy to work with
        temp_filters = [dict(item) for item in self.filter_rules]

        # Create dialog window
        dialog = tk.Toplevel(self.root)
        dialog.title("Edit Filters")
        dialog.geometry("400x400")
        dialog.minsize(300, 300)
        dialog.transient(self.root)
        dialog.grab_set()

        # Style setup
        style = ttk.Style()
        dialog_bg = style.lookup("TFrame", "background")
        dialog.configure(bg=dialog_bg)

        # Setup context menu
        context_menu = tk.Menu(dialog, tearoff=0)

        # Layout
        dialog.rowconfigure(0, weight=1)
        dialog.columnconfigure(0, weight=1)

        # Tree view for filters
        tree_frame, filter_tree = self._create_filter_tree(dialog)
        tree_frame.grid(row=0, column=0, padx=10, pady=10, sticky=tk.NSEW)

        # Populate tree
        def populate_tree():
            for item in filter_tree.get_children():
                filter_tree.delete(item)
            for i, item in enumerate(temp_filters):
                check_char = (
                    self.CHECKED_CHAR
                    if item.get("active", True)
                    else self.UNCHECKED_CHAR
                )
                filter_tree.insert("", "end", iid=i, values=(check_char, item["rule"]))

        def _create_rule_input_dialog(title, prompt_text, initial_value=""):
            """Create a dialog to get a filter rule from the user."""
            entry_var = tk.StringVar(value=initial_value)
            result = None

            def on_ok():
                nonlocal result
                result = entry_var.get()
                input_dialog.destroy()

            input_dialog = tk.Toplevel(dialog)
            input_dialog.transient(dialog)
            input_dialog.grab_set()
            input_dialog.title(title)
            input_dialog.minsize(300, 120)
            input_dialog.maxsize(300, 120)
            input_dialog.configure(bg=dialog_bg)
            input_dialog.rowconfigure(0, weight=1)
            input_dialog.columnconfigure(0, weight=1)

            content_frame = ttk.Frame(input_dialog, padding=10)
            content_frame.grid(row=0, column=0, sticky=tk.NSEW)
            content_frame.columnconfigure(0, weight=1)

            ttk.Label(content_frame, text=prompt_text).grid(
                row=0, column=0, sticky=tk.W, pady=(0, 5)
            )

            entry = ttk.Entry(content_frame, textvariable=entry_var)
            entry.grid(row=1, column=0, sticky=tk.EW)
            entry.focus_set()
            entry.select_range(0, "end")

            button_frame = ttk.Frame(input_dialog, padding=(10, 0, 10, 10))
            button_frame.grid(row=1, column=0, sticky=tk.EW)
            button_frame.columnconfigure(0, weight=1)
            button_frame.columnconfigure(1, weight=0)
            button_frame.columnconfigure(2, weight=0)
            button_frame.columnconfigure(3, weight=1)

            ttk.Button(
                button_frame,
                text="Cancel",
                command=input_dialog.destroy,
                cursor="hand2",
            ).grid(row=0, column=1, padx=5)
            ttk.Button(button_frame, text="OK", command=on_ok, cursor="hand2").grid(
                row=0, column=2, padx=5
            )

            self._center_dialog(input_dialog, relative_to=dialog)
            input_dialog.wait_window()
            return result

        # Context menu functions
        def insert_rule():
            new_rule = _create_rule_input_dialog(
                "Insert Rule", "Enter new filter pattern:"
            )
            if new_rule and new_rule.strip():
                temp_filters.append({"rule": new_rule.strip(), "active": True})
                temp_filters.sort(key=lambda item: item["rule"])
                populate_tree()

        def edit_rule():
            selected_item = filter_tree.focus()
            if not selected_item:
                return

            index = int(selected_item)
            current_rule = temp_filters[index]["rule"]

            edited_rule = _create_rule_input_dialog(
                "Edit Rule", "Edit filter pattern:", initial_value=current_rule
            )

            if edited_rule and edited_rule.strip():
                temp_filters[index]["rule"] = edited_rule.strip()
                temp_filters.sort(key=lambda item: item["rule"])
                populate_tree()

        def remove_rule():
            selected_item = filter_tree.focus()
            if selected_item:
                # Custom confirmation dialog
                confirm_dialog = tk.Toplevel(dialog)
                confirm_dialog.transient(dialog)
                confirm_dialog.grab_set()
                confirm_dialog.title("Confirm Deletion")
                confirm_dialog.configure(bg=dialog_bg)
                ttk.Label(
                    confirm_dialog,
                    text="Are you sure you want to remove the selected rule?",
                    padding=20,
                ).pack()

                confirmed = False

                def on_yes():
                    nonlocal confirmed
                    confirmed = True
                    confirm_dialog.destroy()

                btn_frame = ttk.Frame(confirm_dialog, padding=10)
                btn_frame.pack(fill="x")
                ttk.Button(btn_frame, text="Yes", command=on_yes).pack(
                    side="right", padx=5
                )
                ttk.Button(btn_frame, text="No", command=confirm_dialog.destroy).pack(
                    side="right"
                )

                confirm_dialog.wait_window()

                if confirmed:
                    index = int(selected_item)
                    del temp_filters[index]
                    populate_tree()

        def select_all():
            for item in temp_filters:
                item["active"] = True
            populate_tree()

        def deselect_all():
            for item in temp_filters:
                item["active"] = False
            populate_tree()

        # Add commands to context menu
        context_menu.add_command(label="Insert Rule", command=insert_rule)
        context_menu.add_command(label="Edit Rule", command=edit_rule)
        context_menu.add_command(label="Remove Rule", command=remove_rule)
        context_menu.add_separator()
        context_menu.add_command(label="Select All", command=select_all)
        context_menu.add_command(label="Deselect All", command=deselect_all)

        # Event handlers
        def on_tree_click(event):
            region = filter_tree.identify("region", event.x, event.y)
            if region != "cell":
                return

            item_id = filter_tree.identify_row(event.y)
            if item_id:
                index = int(item_id)
                temp_filters[index]["active"] = not temp_filters[index].get(
                    "active", True
                )
                populate_tree()

        def show_context_menu(event):
            item_id = filter_tree.identify_row(event.y)
            if item_id:
                filter_tree.selection_set(item_id)
                filter_tree.focus(item_id)
                context_menu.entryconfig("Remove Rule", state="normal")
                context_menu.entryconfig("Edit Rule", state="normal")
            else:
                context_menu.entryconfig("Remove Rule", state="disabled")
                context_menu.entryconfig("Edit Rule", state="disabled")
            context_menu.post(event.x_root, event.y_root)

        def hide_context_menu_on_escape(event=None):
            """Hide the context menu when Escape is pressed."""
            context_menu.unpost()

        # Bind events
        filter_tree.bind("<Button-1>", on_tree_click)
        filter_tree.bind("<Button-3>", show_context_menu)
        dialog.bind("<Escape>", hide_context_menu_on_escape)

        # Initial population
        populate_tree()

        # Buttons
        def apply_filters():
            active_rules = [
                item["rule"] for item in temp_filters if item.get("active", True)
            ]
            self.log(f"Applying active filters: {active_rules}")

            # Clear file lists and trees
            self.files_a.clear()
            self.files_b.clear()
            self._update_status("A", self.files_a)
            self._update_status("B", self.files_b)
            self._batch_populate_tree(self.tree_a, {})
            self._batch_populate_tree(self.tree_b, {})

            def run_scans_and_compare():
                scan_threads = []
                if self.folder_a.get():
                    thread_a = self._populate_single_folder(
                        "A", self.folder_a.get(), active_rules=active_rules
                    )
                    scan_threads.append(thread_a)
                if self.folder_b.get():
                    thread_b = self._populate_single_folder(
                        "B", self.folder_b.get(), active_rules=active_rules
                    )
                    scan_threads.append(thread_b)

                # Wait for scanning threads
                for t in scan_threads:
                    t.join()

                # Run comparison
                self.root.after(0, self.compare_folders, active_rules)

            threading.Thread(target=run_scans_and_compare, daemon=True).start()

        def save_and_close():
            self.filter_rules = temp_filters
            self.filter_rules.sort(key=lambda item: item["rule"])
            apply_filters()
            dialog.destroy()

        # Create dialog buttons
        button_frame = ttk.Frame(dialog)
        button_frame.grid(row=1, column=0, padx=10, pady=(0, 10), sticky=tk.EW)

        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(4, weight=1)

        ttk.Button(
            button_frame, text="Save", command=save_and_close, cursor="hand2"
        ).grid(row=0, column=3, padx=5)
        ttk.Button(
            button_frame, text="Apply", command=apply_filters, cursor="hand2"
        ).grid(row=0, column=2, padx=5)
        ttk.Button(
            button_frame, text="Cancel", command=dialog.destroy, cursor="hand2"
        ).grid(row=0, column=1, padx=5)

        # Center dialog
        self._center_dialog(dialog)
        dialog.wait_window(self.root)

    def _create_filter_tree(self, parent):
        """Create tree view for filter dialog."""
        tree_frame = ttk.Frame(parent)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        filter_tree = ttk.Treeview(
            tree_frame, columns=("check", "rule"), show="headings"
        )
        filter_tree.heading("check", text="")
        filter_tree.column("check", width=40, anchor="center", stretch=False)

        filter_tree.heading("rule", text="Filter Rule")
        filter_tree.column("rule", anchor="w", stretch=True)

        filter_tree.grid(row=0, column=0, sticky=tk.NSEW)

        scrollbar = ttk.Scrollbar(tree_frame, command=filter_tree.yview)
        scrollbar.grid(row=0, column=1, sticky=tk.NS)
        filter_tree.config(yscrollcommand=scrollbar.set)

        return tree_frame, filter_tree

    def _get_active_filters(self):
        """Get active filter rule strings."""
        return [
            item["rule"]
            for item in self.filter_rules
            if isinstance(item, dict) and item.get("active", True)
        ]

    # ==========================================================================
    # UI EVENT HANDLERS
    # ==========================================================================

    def _on_tree_click(self, event):
        """Handle clicks to toggle checkboxes."""
        tree = event.widget
        region = tree.identify("region", event.x, event.y)
        if region != "cell":
            return

        column = tree.identify_column(event.x)
        if column == "#1":  # 'sync' column
            item_id = tree.identify_row(event.y)
            rel_path = self._get_relative_path(tree, item_id)
            if rel_path is not None:
                current_state = self.sync_states.get(rel_path, False)
                new_state = not current_state
                self.sync_states[rel_path] = new_state
                char = self.CHECKED_CHAR if new_state else self.UNCHECKED_CHAR
                current_values = list(tree.item(item_id, "values"))
                current_values[0] = char
                tree.item(item_id, values=current_values)

    def _show_tree_context_menu(self, event):
        """Show context menu on right-click."""
        tree = event.widget
        item_id = tree.identify_row(event.y)

        if not item_id:
            return

        tree.selection_set(item_id)
        tree.focus(item_id)

        rel_path = self._get_relative_path(tree, item_id)
        if not rel_path:
            return

        # Determine which file dictionary to use
        files_dict = self.files_a if tree is self.tree_a else self.files_b
        item_info = files_dict.get(rel_path)

        # Enable/disable menu items based on context
        if item_info and item_info.get("type") == "file":
            self.tree_context_menu.entryconfig("Open...", state="normal")
        else:
            self.tree_context_menu.entryconfig("Open...", state="disabled")

        self.tree_context_menu.entryconfig("Delete", state="normal")
        self.tree_context_menu.post(event.x_root, event.y_root)

    # ==========================================================================
    # CONTEXT MENU ACTIONS
    # ==========================================================================

    def _select_all(self):
        """Select all different/new items."""
        tree = self.root.focus_get()
        if not isinstance(tree, ttk.Treeview) or tree not in (self.tree_a, self.tree_b):
            return

        diff_statuses = {
            "Different",
            "Only in Folder A",
            "Only in Folder B",
            "Contains differences",
        }

        def traverse_and_select(item_id=""):
            for child_id in tree.get_children(item_id):
                status = tree.item(child_id, "values")[3]
                if status in diff_statuses:
                    rel_path = self._get_relative_path(tree, child_id)
                    if rel_path is not None:
                        self.sync_states[rel_path] = True
                        current_values = list(tree.item(child_id, "values"))
                        current_values[0] = self.CHECKED_CHAR
                        tree.item(child_id, values=tuple(current_values))

                # Recurse into children
                if tree.get_children(child_id):
                    traverse_and_select(child_id)

        traverse_and_select()

    def _deselect_all(self):
        """Deselect all items in the tree."""
        tree = self.root.focus_get()
        if not isinstance(tree, ttk.Treeview) or tree not in (self.tree_a, self.tree_b):
            return

        def traverse_and_deselect(item_id=""):
            for child_id in tree.get_children(item_id):
                rel_path = self._get_relative_path(tree, child_id)
                if rel_path is not None:
                    # Check if item is in sync_states
                    if rel_path in self.sync_states:
                        self.sync_states[rel_path] = False
                    current_values = list(tree.item(child_id, "values"))
                    current_values[0] = self.UNCHECKED_CHAR
                    tree.item(child_id, values=tuple(current_values))

                # Recurse into children
                if tree.get_children(child_id):
                    traverse_and_deselect(child_id)

        traverse_and_deselect()

    def _open_selected_item(self):
        """Open selected file with default app."""
        tree = self.root.focus_get()
        if tree is None or tree not in (self.tree_a, self.tree_b):
            return

        item_id = tree.focus()
        if not item_id:
            return

        rel_path = self._get_relative_path(tree, item_id)
        if not rel_path:
            return

        panel = "A" if tree is self.tree_a else "B"
        use_ssh = self._has_ssh_a() if panel == "A" else self._has_ssh_b()
        files_dict = self.files_a if panel == "A" else self.files_b
        full_path = files_dict.get(rel_path, {}).get("full_path")

        if not full_path:
            self.log(f"Could not determine full path for {rel_path}")
            return

        temp_ssh_client: Optional[paramiko.SSHClient] = None
        try:
            if use_ssh:
                self.log(f"Downloading remote file for opening: {full_path}")
                # Create a temporary SSH client
                temp_ssh_client = self._create_ssh_for_panel(panel)

                transport = temp_ssh_client.get_transport()
                if transport is None:
                    raise RuntimeError("SSH client transport is not available.")

                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=os.path.basename(rel_path)
                ) as tmp:
                    with SCPClient(transport) as scp:
                        scp.get(full_path, tmp.name)
                    full_path = tmp.name
                    self.temp_files_to_clean.append(full_path)

            self.log(f"Opening file: {full_path}")
            if sys.platform == "win32":
                os.startfile(full_path)
            elif sys.platform == "darwin":  # macOS
                subprocess.Popen(["open", full_path])
            else:
                # Linux and other Unix-like systems
                process = subprocess.Popen(
                    ["xdg-open", full_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                stdout, stderr = process.communicate()
                if process.returncode != 0:
                    error_message = stderr.decode().strip()
                    self.log(f"xdg-open error: {error_message}")
                    messagebox.showwarning(
                        "Warning",
                        f"Could not open file. xdg-open reported:\n{error_message}",
                    )

        except Exception as e:
            messagebox.showerror("Error", f"Could not open file: {e}")
            self.log(f"Error opening file {full_path}: {e}")
        finally:
            if temp_ssh_client:
                temp_ssh_client.close()

    def _delete_selected_item(self):
        """Delete the selected file or directory."""
        tree = self.root.focus_get()
        if tree is None or tree not in (self.tree_a, self.tree_b):
            return

        item_id = tree.focus()
        if not item_id:
            return

        rel_path = self._get_relative_path(tree, item_id)
        if not rel_path:
            return

        if not messagebox.askyesno(
            "Confirm Delete",
            f"Are you sure you want to permanently delete '{rel_path}'?",
        ):
            return

        panel = "A" if tree is self.tree_a else "B"
        use_ssh = self._has_ssh_a() if panel == "A" else self._has_ssh_b()
        files_dict = self.files_a if panel == "A" else self.files_b
        item_info = files_dict.get(rel_path)
        full_path = item_info.get("full_path") if item_info else None

        if not full_path:
            base_folder = self.folder_a.get() if panel == "A" else self.folder_b.get()
            full_path = os.path.join(base_folder, rel_path)

        def delete_and_refresh():
            ssh_client = None
            try:
                self.log(f"Deleting item: {full_path}")
                if use_ssh:
                    ssh_client = self._create_ssh_for_panel(panel)
                    is_dir = False
                    if item_info:
                        is_dir = item_info.get("type") == "dir"
                    else:
                        # Fallback: check remote system
                        stdin, stdout, stderr = ssh_client.exec_command(
                            f"if [ -d '{full_path}' ]; then echo 'dir'; fi"
                        )
                        if stdout.read().decode().strip() == "dir":
                            is_dir = True

                    command = f"rm -rf '{full_path}'" if is_dir else f"rm '{full_path}'"

                    stdin, stdout, stderr = ssh_client.exec_command(command)
                    error = stderr.read().decode()

                    if error:
                        raise Exception(error)
                else:
                    # Local deletion
                    is_dir = False
                    if item_info:
                        is_dir = item_info.get("type") == "dir"
                    elif os.path.isdir(full_path):
                        is_dir = True

                    if is_dir:
                        shutil.rmtree(full_path)
                    else:
                        os.remove(full_path)

                self.log(f"Successfully deleted. Refreshing panel {panel}.")
                self._populate_single_folder(
                    panel, self.folder_a.get() if panel == "A" else self.folder_b.get()
                )
            except Exception as e:
                messagebox.showerror("Error", f"Failed to delete item: {e}")
                self.log(f"Error deleting {full_path}: {e}")
            finally:
                if use_ssh and ssh_client:
                    ssh_client.close()

        threading.Thread(target=delete_and_refresh, daemon=True).start()

    # ==========================================================================
    # UTILITY METHODS
    # ==========================================================================

    def _update_folder_history(self, panel_name, folder_var, new_path):
        """Update and save folder history."""
        if not new_path:
            return

        history_list = (
            self.folder_a_history if panel_name == "A" else self.folder_b_history
        )

        if new_path in history_list:
            history_list.remove(new_path)
        history_list.insert(0, new_path)

        if panel_name == "A":
            self.folder_a_history = history_list[:HISTORY_LENGTH]
            self.folder_a.set(new_path)
        else:
            self.folder_b_history = history_list[:HISTORY_LENGTH]
            self.folder_b.set(new_path)

        self.save_config()

    def _get_relative_path(self, tree, item_id):
        """Construct relative path for item."""
        path_parts = []
        while item_id:
            text = tree.item(item_id, "text")
            path_parts.insert(0, text)
            item_id = tree.parent(item_id)

        if path_parts:
            return os.path.sep.join(path_parts)
        return None

    def _adjust_tree_column_widths(self, tree: ttk.Treeview):
        """Adjust column widths to fit content."""
        try:
            # Ensure we measure with the same font
            font_family, font_size = self._get_mono_font()
            font = tkfont.Font(family=font_family, size=font_size)

            # Log message after font is determined
            panel_name = "A" if tree is self.tree_a else "B"
            self.log(
                f"Adjusting column widths for folder {panel_name} tree using "
                f"font: {font_family}, size: {font_size}..."
            )

            # Adjust data columns
            columns = list(tree["columns"])
            columns.insert(0, "#0")  # Add 'Name' column to be processed
            for col in columns:
                # Start with the heading width
                heading_text = tree.heading(col, "text")
                max_width = font.measure(heading_text)

                def find_max_width(item_id=""):
                    nonlocal max_width
                    for child_id in tree.get_children(item_id):
                        if col == "#0":
                            # For 'Name' column, get item's text
                            cell_value = tree.item(child_id, "text")
                        else:
                            # For other columns, use tree.set()
                            cell_value = tree.set(child_id, col)
                        if isinstance(cell_value, str) and cell_value:
                            width = font.measure(cell_value)
                            if width > max_width:
                                max_width = width

                find_max_width()

                # Apply the new width with padding
                tree.column(col, width=max_width + 10)

        except Exception as e:
            self.log(f"Could not adjust column widths: {e}")

    def log(self, message):
        """Log message to console."""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def start_progress(self, panel=None, max_value=0, text=""):
        """Show the progress bar."""
        self.status_label_a.grid_remove()
        self.status_label_b.grid_remove()
        self.progress_bar.grid()

        # Determine which status variable to update
        if panel == "A":
            status_var = self.status_a
        elif panel == "B":
            status_var = self.status_b
        else:
            status_var = self.status_a

        if max_value > 0:
            self.progress_bar.config(mode="determinate", maximum=max_value, value=0)
            status_var.set(text)
        else:
            self.progress_bar.config(mode="indeterminate")
            self.progress_bar.start(10)
            status_var.set("Scanning...")

    def update_progress(self, step=1):
        """Update the progress bar."""
        self.progress_bar.step(step)

    def stop_progress(self):
        """Hide the progress bar."""
        self.progress_bar.stop()
        self.progress_bar.grid_remove()
        self.status_label_a.grid()
        self.status_label_b.grid()

    def _update_status(self, panel, files):
        """Update the status bar text."""
        num_files = len(files)
        total_size = sum(f.get("size", 0) for f in files.values())
        status_text = f"{num_files} files, {self._format_size(total_size)}"

        if panel == "A":
            self.status_a.set(status_text)
        else:
            self.status_b.set(status_text)

    def _format_size(self, size_bytes):
        """Format file size to be readable."""
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"

    def _format_time(self, timestamp):
        """Format timestamp to a date string."""
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

    def _center_dialog(self, dialog, relative_to=None):
        """Center a dialog on a parent window."""
        parent = relative_to or self.root
        dialog.update_idletasks()

        parent_x = parent.winfo_x()
        parent_y = parent.winfo_y()
        parent_width = parent.winfo_width()
        parent_height = parent.winfo_height()

        dialog_width = dialog.winfo_width()
        dialog_height = dialog.winfo_height()

        x = parent_x + (parent_width // 2) - (dialog_width // 2)
        y = parent_y + (parent_height // 2) - (dialog_height // 2)
        dialog.geometry(f"+{x}+{y}")

    def _get_mono_font(self):
        """Returns a suitable monospace font family based on the current OS."""
        font_families = tkfont.families()

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

    def _refresh_ui_after_sync(self, use_ssh_a, use_ssh_b):
        """Refreshes both tree views and runs comparison after sync."""
        rules = self._get_active_filters()

        # Clear existing trees
        self._batch_populate_tree(self.tree_a, {})
        self._batch_populate_tree(self.tree_b, {})

        # Re-populate both trees with the latest data
        tree_structure_a = self._build_tree_structure(self.files_a)
        self._batch_populate_tree(self.tree_a, tree_structure_a, rules)

        tree_structure_b = self._build_tree_structure(self.files_b)
        self._batch_populate_tree(self.tree_b, tree_structure_b, rules)

        # Re-establish SSH clients for comparison (if they were closed during sync)
        if use_ssh_a:
            self.ssh_client_a = self._create_ssh_for_panel("A")
        if use_ssh_b:
            self.ssh_client_b = self._create_ssh_for_panel("B")

        # Re-run comparison to apply correct statuses and adjust column widths
        self._update_trees_with_comparison(
            self.files_a,
            self.files_b,
            use_ssh_a,
            use_ssh_b,
        )


def main():
    """Main entry point for the application."""
    root = tk.Tk()
    GSynchro(root)
    root.mainloop()


if __name__ == "__main__":
    main()
