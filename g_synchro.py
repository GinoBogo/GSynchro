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
import stat
import shutil
import subprocess
import sys
import tempfile
import threading
from queue import Queue
from concurrent.futures import ThreadPoolExecutor, as_completed

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

from contextlib import contextmanager
from datetime import datetime
from typing import Optional, Iterator, cast, Union

import paramiko
from scp import SCPClient


# ============================================================================
# CONSTANTS
# ============================================================================

CONFIG_FILE = "g_synchro.json"
HISTORY_LENGTH = 10
CHUNK_SIZE = 4096
CHECKED_CHAR = "✓"
UNCHECKED_CHAR = "☐"
MIN_WINDOW_WIDTH = 1024
MIN_WINDOW_HEIGHT = 768


# ============================================================================
# CONNECTION MANAGER CLASS
# ============================================================================


class ConnectionManager:
    """Manages SSH connections with pooling."""

    def __init__(self, logger_func, pool_size=4):
        """Initialize the ConnectionManager.

        Args:
            logger_func: A function to call for logging messages.
            pool_size: Number of connections to maintain per server.
        """
        self._pools = {}  # {server_key: Queue of connections}
        self._pool_configs = {}  # {server_key: (host, user, password, port)}
        self._lock = threading.Lock()
        self.log = logger_func
        self.pool_size = pool_size

    def _get_server_key(self, host, user, port):
        """Generate a unique key for a server configuration.

        Args:
            host: SSH host
            user: SSH username
            port: SSH port

        Returns:
            Unique server key string
        """
        return f"{user}@{host}:{port}"

    def _create_connection(self, host, user, password, port):
        """Create a new SSH connection.

        Args:
            host: SSH host
            user: SSH username
            password: SSH password
            port: SSH port

        Returns:
            paramiko.SSHClient instance
        """
        self.log(f"Creating new SSH connection for {user}@{host}:{port}")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(host, username=user, password=password, port=port)
        return client

    def _initialize_pool(self, server_key, host, user, password, port):
        """Initialize a connection pool for a server.

        Args:
            server_key: Unique server key
            host: SSH host
            user: SSH username
            password: SSH password
            port: SSH port
        """
        if server_key not in self._pools:
            self._pools[server_key] = Queue()
            self._pool_configs[server_key] = (host, user, password, port)

            # Create initial connections
            for i in range(self.pool_size):
                try:
                    conn = self._create_connection(host, user, password, port)
                    self._pools[server_key].put(conn)
                except Exception as e:
                    self.log(
                        f"SSH connection {i + 1}/{self.pool_size} failed for {server_key}: {e}"
                    )

    @contextmanager
    def get_connection(self, host, user, password, port):
        """Get a connection from the pool as a context manager.

        Args:
            host: SSH host
            user: SSH username
            password: SSH password
            port: SSH port

        Yields:
            An active paramiko.SSHClient instance

        Raises:
            ConnectionError: If connection cannot be established
        """
        server_key = self._get_server_key(host, user, port)

        with self._lock:
            # Initialize pool if needed
            if server_key not in self._pools:
                self._initialize_pool(server_key, host, user, password, port)

        # Get connection from pool
        conn = None
        try:
            conn = self._pools[server_key].get(timeout=10)

            # Check if connection is still alive
            transport = conn.get_transport() if conn else None
            if not transport or not transport.is_active():
                self.log(f"Connection for {server_key} is dead, creating new one")
                conn = self._create_connection(host, user, password, port)

            yield conn

        except Exception as e:
            self.log(f"Error getting connection for {server_key}: {e}")
            # Try to create a new connection as fallback
            conn = self._create_connection(host, user, password, port)
            yield conn
        finally:
            # Return connection to pool
            if conn and server_key in self._pools:
                try:
                    # Check if connection is still good before returning
                    transport = conn.get_transport()
                    if transport and transport.is_active():
                        self._pools[server_key].put(conn, timeout=1)
                    else:
                        conn.close()
                        # Create a replacement connection
                        host, user, password, port = self._pool_configs[server_key]
                        new_conn = self._create_connection(host, user, password, port)
                        self._pools[server_key].put(new_conn, timeout=1)
                except Exception:
                    # If we can't return to pool, close it
                    try:
                        conn.close()
                    except Exception:
                        pass

    def get_pool_status(self):
        """Get status of all connection pools.

        Returns:
            Dictionary mapping server keys to pool sizes
        """
        status = {}
        with self._lock:
            for server_key, pool in self._pools.items():
                status[server_key] = pool.qsize()
        return status

    def close_all(self):
        """Close all managed SSH connections."""
        with self._lock:
            for server_key, pool in self._pools.items():
                self.log(f"Closing SSH pool {server_key}")
                while not pool.empty():
                    try:
                        conn = pool.get_nowait()
                        if conn:
                            conn.close()
                    except Exception:
                        pass
            self._pools.clear()
            self._pool_configs.clear()


# ============================================================================
# MAIN APPLICATION CLASS
# ============================================================================


class GSynchro:
    """Main application class for GSynchro file synchronization tool."""

    # ==========================================================================
    # INITIALIZATION METHODS
    # ==========================================================================

    def __init__(self, root: tk.Tk):
        """Initialize the GSynchro application.

        Args:
            root: The main Tkinter root window
        """
        self.root = root

        # Connection Manager
        self.connection_manager = ConnectionManager(self._log, pool_size=4)
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

        # Host histories: lists of dicts {'host','port','username'}
        self.hosts_a = []
        self.hosts_b = []

        # Sync States
        self.sync_states = {}

        # Status Variables
        self._context_menu_tree: Optional[ttk.Treeview] = None
        self._context_menu_item_id: Optional[str] = None

        self.status_a = tk.StringVar()
        self.status_b = tk.StringVar()

        self._load_config()
        self._init_window()
        self._setup_ui()

        # Bind Escape key to clear selection and focus
        self.root.bind("<Escape>", self._on_escape_key)

    def _init_window(self):
        """Initialize main window properties."""
        self.root.title("GSynchro - Synchronization Tool")
        self.root.minsize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    # ==========================================================================
    # CONFIGURATION METHODS
    # ==========================================================================

    def _load_config(self):
        """Load configuration from file."""
        if not os.path.exists(CONFIG_FILE):
            return

        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)

            # Window geometry
            if "WINDOW" in config and "geometry" in config["WINDOW"]:
                self.root.geometry(config["WINDOW"]["geometry"])

            # Panel A SSH
            if "SSH_A" in config:
                self.remote_host_a.set(config["SSH_A"].get("host", ""))
                self.remote_port_a.set(config["SSH_A"].get("port", "22"))
                self.remote_user_a.set(config["SSH_A"].get("username", ""))

            # Panel B SSH
            if "SSH_B" in config:
                self.remote_host_b.set(config["SSH_B"].get("host", ""))
                self.remote_port_b.set(config["SSH_B"].get("port", "22"))
                self.remote_user_b.set(config["SSH_B"].get("username", ""))

            # Load host histories (list of dicts)
            if "HOSTS_A" in config:
                self.hosts_a = config["HOSTS_A"] or []
                # if no explicit host set, pick most recent
                if not self.remote_host_a.get() and self.hosts_a:
                    self.remote_host_a.set(self.hosts_a[0].get("host", ""))
                    self.remote_port_a.set(
                        self.hosts_a[0].get("port", self.remote_port_a.get())
                    )
                    self.remote_user_a.set(
                        self.hosts_a[0].get("username", self.remote_user_a.get())
                    )

            if "HOSTS_B" in config:
                self.hosts_b = config["HOSTS_B"] or []
                if not self.remote_host_b.get() and self.hosts_b:
                    self.remote_host_b.set(self.hosts_b[0].get("host", ""))
                    self.remote_port_b.set(
                        self.hosts_b[0].get("port", self.remote_port_b.get())
                    )
                    self.remote_user_b.set(
                        self.hosts_b[0].get("username", self.remote_user_b.get())
                    )

            # Filter rules
            if "FILTERS" in config and "rules" in config["FILTERS"]:
                self._load_filter_rules(config["FILTERS"]["rules"])

            # Panel A History
            if "FOLDER_A_HISTORY" in config:
                self.folder_a_history = config["FOLDER_A_HISTORY"]
                if self.folder_a_history:
                    self.folder_a.set(self.folder_a_history[0])

            # Panel B History
            if "FOLDER_B_HISTORY" in config:
                self.folder_b_history = config["FOLDER_B_HISTORY"]
                if self.folder_b_history:
                    self.folder_b.set(self.folder_b_history[0])

        except json.JSONDecodeError:
            self._log(f"Warning: Could not parse {CONFIG_FILE}. Using defaults.")

    def _load_filter_rules(self, rules_data):
        """Load and validate filter rules.

        Args:
            rules_data: List of filter rules from config file
        """
        processed_rules = []
        for item in rules_data:
            if isinstance(item, str):
                processed_rules.append({"rule": item, "active": True})
            elif isinstance(item, dict) and "rule" in item and "active" in item:
                processed_rules.append(item)
            else:
                self._log(f"Warning: Invalid filter rule format: {item}. Skipping.")
        processed_rules.sort(key=lambda item: item["rule"])
        self.filter_rules = processed_rules

    def _save_config(self):
        """Save configuration to file."""
        # Update Panel A history
        current_folder_a = self.folder_a.get()
        if current_folder_a:
            if current_folder_a in self.folder_a_history:
                self.folder_a_history.remove(current_folder_a)
            self.folder_a_history.insert(0, current_folder_a)
            self.folder_a_history = self.folder_a_history[:HISTORY_LENGTH]

        # Update Panel B history
        current_folder_b = self.folder_b.get()
        if current_folder_b:
            if current_folder_b in self.folder_b_history:
                self.folder_b_history.remove(current_folder_b)
            self.folder_b_history.insert(0, current_folder_b)
            self.folder_b_history = self.folder_b_history[:HISTORY_LENGTH]

        # Ensure host histories include current entries
        self._update_host_history(
            "A",
            self.remote_host_a.get(),
            self.remote_port_a.get(),
            self.remote_user_a.get(),
        )
        self._update_host_history(
            "B",
            self.remote_host_b.get(),
            self.remote_port_b.get(),
            self.remote_user_b.get(),
        )

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
            "HOSTS_A": self.hosts_a,
            "HOSTS_B": self.hosts_b,
            "FILTERS": {"rules": self.filter_rules},
            "FOLDER_A_HISTORY": self.folder_a_history,
            "FOLDER_B_HISTORY": self.folder_b_history,
        }

        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)

    # ==========================================================================
    # UI CREATION METHODS
    # ==========================================================================

    def _setup_ui(self):
        """Set up the main user interface."""
        self._setup_styles()

        # Create main layout
        main_frame = self._create_main_frame()
        control_frame = self._create_control_frame(main_frame)
        panels_frame = self._create_panels_frame(main_frame)

        # Create UI components
        self._create_control_buttons(control_frame)
        self._create_panels(panels_frame)
        self._create_status_bar(main_frame)

        # Create context menu
        self._create_tree_context_menu()

        # Initial status
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
        style.configure("TTreeview", rowheight=20)
        style.map("TTreeview")  # Reset map to avoid conflicts

    def _create_main_frame(self) -> ttk.Frame:
        """Create the main application frame.

        Returns:
            Main frame widget
        """
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=tk.NSEW)

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(1, weight=1)

        return main_frame

    def _create_control_frame(self, main_frame: ttk.Frame) -> ttk.Frame:
        """Create control buttons frame.

        Args:
            main_frame: Parent main frame

        Returns:
            Control frame widget
        """
        control_frame = ttk.Frame(main_frame)
        control_frame.grid(row=0, column=0, columnspan=3, sticky=tk.EW, pady=5)
        return control_frame

    def _create_control_buttons(self, control_frame: ttk.Frame):
        """Create the main control buttons.

        Args:
            control_frame: Parent control frame
        """
        buttons_config = [
            ("Compare", self.compare_folders, None),
            ("Sync  ▶", lambda: self.synchronize("a_to_b"), "lightgreen"),
            ("◀  Sync", lambda: self.synchronize("b_to_a"), "lightblue"),
            ("Filters", self._show_filters_dialog, None),
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

    def _create_panels_frame(self, main_frame: ttk.Frame) -> ttk.Frame:
        """Create panels frame for displays.

        Args:
            main_frame: Parent main frame

        Returns:
            Panels frame widget
        """
        panels_frame = ttk.Frame(main_frame)
        panels_frame.grid(row=1, column=0, columnspan=3, sticky=tk.NSEW)

        panels_frame.columnconfigure(0, weight=1)
        panels_frame.columnconfigure(1, weight=1)
        panels_frame.rowconfigure(0, weight=1)

        return panels_frame

    def _create_panels(self, panels_frame: ttk.Frame):
        """Create both Panel A and Panel B.

        Args:
            panels_frame: Parent panels frame
        """
        panel_configs = [
            {
                "title": "Panel A",
                "column": 0,
                "padx": (0, 5),
                "button_color": "lightgreen",
                "folder_var": self.folder_a,
                "browse_command": self._browse_panel_a,
                "host_var": self.remote_host_a,
                "port_var": self.remote_port_a,
                "user_var": self.remote_user_a,
                "pass_var": self.remote_pass_a,
                "tree_attr": "tree_a",
                "folder_history": self.folder_a_history,
            },
            {
                "title": "Panel B",
                "column": 1,
                "padx": (5, 0),
                "button_color": "lightblue",
                "folder_var": self.folder_b,
                "browse_command": self._browse_panel_b,
                "host_var": self.remote_host_b,
                "port_var": self.remote_port_b,
                "user_var": self.remote_user_b,
                "pass_var": self.remote_pass_b,
                "tree_attr": "tree_b",
                "folder_history": self.folder_b_history,
            },
        ]
        for config in panel_configs:
            self._create_panel(panels_frame, config)

    def _create_panel(self, parent: ttk.Frame, config: dict):
        """Create an individual folder panel.

        Args:
            parent: Parent widget
            config: Configuration dictionary for the panel
        """
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
        # Use Combobox for Host so user can select previously saved host tuples
        panel_name = config["title"].split(" ")[1]
        host_list = self.hosts_a if panel_name == "A" else self.hosts_b
        host_values = [h.get("host", "") for h in host_list]
        host_combobox = ttk.Combobox(
            panel, textvariable=config["host_var"], values=host_values, width=15
        )
        host_combobox.grid(row=0, column=1, padx=5, pady=5, sticky=tk.EW)
        # When user selects a saved host, autofill port and username
        host_combobox.bind(
            "<<ComboboxSelected>>", lambda e, pn=panel_name: self._on_host_selected(pn)
        )

        ttk.Label(panel, text="Port:").grid(
            row=0, column=2, padx=5, pady=5, sticky=tk.E
        )
        port_entry = ttk.Entry(panel, textvariable=config["port_var"], width=8)
        port_entry.grid(row=0, column=3, padx=5, pady=5, sticky=tk.EW)

        ttk.Button(
            panel,
            text="Test",
            command=lambda: self._test_ssh(config["title"]),
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
                self._populate_single_panel(panel_name, folder_path)

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
        tree.bind("<Button-3>", self._on_tree_right_click)

        # Store tree reference
        if config["tree_attr"] == "tree_a":
            self.tree_a = tree
        else:
            self.tree_b = tree

    def _create_tree_view(self, parent: ttk.LabelFrame) -> ttk.Treeview:
        """Create file tree view.

        Args:
            parent: Parent widget

        Returns:
            Configured Treeview widget
        """
        tree = ttk.Treeview(
            parent,
            columns=("sync", "size", "modified", "status"),
            show="tree headings",
        )

        # Configure columns
        tree.heading("#0", text="Name")
        tree.column("#0", width=200, anchor="w")

        tree.heading("sync", text="Sync")
        tree.column("sync", width=20, anchor="center")

        tree.heading("size", text="Size")
        tree.column("size", width=80, anchor="e")

        tree.heading("modified", text="Modified")
        tree.column("modified", width=120, anchor="center")

        tree.heading("status", text="Status")
        tree.column("status", width=100, anchor="center")

        # Define a monospace font
        font_tuple = self._get_mono_font()

        # Define a larger font for the "Sync" column characters
        sync_font_family, sync_font_size = self._get_mono_font()
        sync_font_size += 2
        self.sync_font = tkfont.Font(family=sync_font_family, size=sync_font_size)

        # Configure tags for different status colors
        colors = {
            "green": "green",
            "orange": "orange",
            "blue": "blue",
            "red": "red",
            "magenta": "magenta",
            "black": "black",
        }
        for tag, color in colors.items():
            tree.tag_configure(tag, foreground=color, font=font_tuple)
        tree.tag_configure("sync_col_font", font=self.sync_font)

        return tree

    def _create_status_bar(self, parent: ttk.Frame):
        """Create status bar with progress indicator.

        Args:
            parent: Parent widget
        """
        status_frame = ttk.Frame(parent, relief="flat", padding="2")
        status_frame.grid(row=2, column=0, columnspan=3, sticky=tk.EW, pady=(5, 0))

        status_frame.columnconfigure(0, weight=1)
        status_frame.columnconfigure(1, weight=1)

        # Status labels
        self.status_label_a = ttk.Label(
            status_frame, textvariable=self.status_a, width=80, anchor=tk.W
        )
        self.status_label_a.grid(row=0, column=0, sticky=tk.EW, padx=0)

        self.status_label_b = ttk.Label(
            status_frame, textvariable=self.status_b, width=80, anchor=tk.W
        )
        self.status_label_b.grid(row=0, column=1, sticky=tk.EW, padx=0)

        # Progress bar
        self.progress_bar = ttk.Progressbar(
            status_frame, orient="horizontal", style="flat.Horizontal.TProgressbar"
        )
        self.progress_bar.grid(
            row=0, column=0, columnspan=3, sticky=tk.EW, padx=0, pady=(6, 0)
        )
        self.progress_bar.grid_remove()

    def _create_tree_context_menu(self):
        """Create context menu for tree views."""
        self.tree_context_menu = tk.Menu(self.root, tearoff=0)
        self.tree_context_menu.add_command(
            label="Open...", command=self._open_selected_item
        )
        self.tree_context_menu.add_command(
            label="Compare...", command=self._compare_selected_files
        )
        self.tree_context_menu.add_command(
            label="Delete", command=self._delete_selected_item
        )
        self.tree_context_menu.add_separator()
        self.tree_context_menu.add_command(
            label="Sync  ▶", command=self._sync_selected_a_to_b
        )
        self.tree_context_menu.add_command(
            label="◀  Sync", command=self._sync_selected_b_to_a
        )
        self.tree_context_menu.add_separator()
        self.tree_context_menu.add_command(label="Select All", command=self._select_all)
        self.tree_context_menu.add_command(
            label="Deselect All", command=self._deselect_all
        )

    # ==========================================================================
    # PANEL BROWSING METHODS
    # ==========================================================================

    def _browse_panel_a(self):
        """Browse for folder in Panel A."""
        self._browse_panel("A")

    def _browse_panel_b(self):
        """Browse for folder in Panel B."""
        self._browse_panel("B")

    def _browse_panel(self, panel_name: str):
        """Browse for folder in specified panel.

        Args:
            panel_name: Either "A" or "B" for the panel to browse
        """
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
            selected_path = self._browse_remote(
                folder_var, f"Panel {panel_name}", initial_path
            )
            if selected_path:
                self._populate_single_panel(panel_name, selected_path)
        else:
            folder = filedialog.askdirectory(initialdir=initial_path)
            if folder:
                self._update_panel_history(panel_name, folder_var, folder)
                folder_var.set(folder)
                self._populate_single_panel(panel_name, folder)

    # ==========================================================================
    # SSH METHODS
    # ==========================================================================

    @contextmanager
    def _create_ssh_for_panel(
        self, panel_name: str, optional: bool = False
    ) -> Iterator[Optional[paramiko.SSHClient]]:
        """Create SSH client for a panel.

        Args:
            panel_name: Either "A" or "B"
            optional: If True, don't raise error when SSH not configured

        Yields:
            SSH client or None if optional=True and SSH not configured
        """
        use_ssh = self._has_ssh_a() if panel_name == "A" else self._has_ssh_b()

        if not use_ssh:
            if optional:
                yield None
                return
            else:
                raise ValueError(f"SSH not configured for panel {panel_name}")

        host, user, password, port = (
            (
                self.remote_host_a.get(),
                self.remote_user_a.get(),
                self.remote_pass_a.get(),
                int(self.remote_port_a.get()),
            )
            if panel_name == "A"
            else (
                self.remote_host_b.get(),
                self.remote_user_b.get(),
                self.remote_pass_b.get(),
                int(self.remote_port_b.get()),
            )
        )

        with self.connection_manager.get_connection(
            host, user, password, port
        ) as client:
            try:
                yield client
            finally:
                pass

    def _test_ssh(self, panel_name: str):
        """Test SSH connection for specified panel.

        Args:
            panel_name: Panel name like "Panel A" or "Panel B"
        """
        if panel_name == "Panel A":
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

                self._log(f"Testing SSH {panel_name}...")
                with self._create_ssh_for_panel(panel_name.split(" ")[1]) as ssh_client:
                    if ssh_client is None:
                        raise ConnectionError("Failed to establish SSH connection.")

                # On successful connection, update host history so combobox remembers this tuple
                try:
                    self._update_host_history(
                        panel_name.split(" ")[1],
                        host_var.get(),
                        port_var.get(),
                        user_var.get(),
                    )
                except Exception:
                    pass

                self._log(f"✓ SSH {panel_name} connected")
                messagebox.showinfo(
                    "Success", f"SSH connection established for {panel_name}!"
                )
            except Exception as e:
                self._log(f"✗ SSH connection failed for {panel_name}: {str(e)}")
                messagebox.showerror("Error", f"SSH connection failed: {str(e)}")

        threading.Thread(target=test_thread, daemon=True).start()

    def _has_ssh_a(self) -> bool:
        """Check if Panel A has SSH credentials.

        Returns:
            True if all SSH credentials for Panel A are set
        """
        return all(
            [
                self.remote_host_a.get(),
                self.remote_user_a.get(),
                self.remote_pass_a.get(),
            ]
        )

    def _has_ssh_b(self) -> bool:
        """Check if Panel B has SSH credentials.

        Returns:
            True if all SSH credentials for Panel B are set
        """
        return all(
            [
                self.remote_host_b.get(),
                self.remote_user_b.get(),
                self.remote_pass_b.get(),
            ]
        )

    def _on_host_selected(self, panel_name: str):
        """Called when user selects a host from the combobox. Auto-fill port and username.

        Args:
            panel_name: Panel name "A" or "B"
        """
        if panel_name == "A":
            host = self.remote_host_a.get()
            for h in self.hosts_a:
                if h.get("host") == host:
                    self.remote_port_a.set(h.get("port", self.remote_port_a.get()))
                    self.remote_user_a.set(h.get("username", self.remote_user_a.get()))
                    return
        else:
            host = self.remote_host_b.get()
            for h in self.hosts_b:
                if h.get("host") == host:
                    self.remote_port_b.set(h.get("port", self.remote_port_b.get()))
                    self.remote_user_b.set(h.get("username", self.remote_user_b.get()))
                    return

    def _update_host_history(
        self, panel_name: str, host: str, port: str, username: str
    ):
        """Update host history list for a panel (most-recent-first, deduped).

        Args:
            panel_name: Panel name "A" or "B"
            host: SSH host
            port: SSH port
            username: SSH username
        """
        if not host:
            return
        entry = {"host": host, "port": port or "22", "username": username or ""}
        if panel_name == "A":
            # remove any existing with same host
            self.hosts_a = [h for h in self.hosts_a if h.get("host") != host]
            self.hosts_a.insert(0, entry)
            self.hosts_a = self.hosts_a[:HISTORY_LENGTH]
        else:
            self.hosts_b = [h for h in self.hosts_b if h.get("host") != host]
            self.hosts_b.insert(0, entry)
            self.hosts_b = self.hosts_b[:HISTORY_LENGTH]

    # ==========================================================================
    # REMOTE PANEL BROWSING METHODS
    # ==========================================================================

    def _browse_remote(
        self, folder_var: tk.StringVar, panel_name: str, initial_path: str = ""
    ) -> Optional[str]:
        """Browse remote folder via SSH.

        Args:
            folder_var: StringVar for the folder path
            panel_name: Name of the panel
            initial_path: Initial path to show

        Returns:
            Selected remote path or None if cancelled
        """
        try:
            with self._create_ssh_for_panel(panel_name.split(" ")[1]) as ssh_client:
                if ssh_client is None:
                    raise ConnectionError(
                        "Failed to establish SSH connection for remote browsing."
                    )

                current_path = initial_path or folder_var.get()
                stdin, stdout, stderr = ssh_client.exec_command("pwd")
                remote_path = stdout.read().decode().strip()

                if not current_path or not current_path.startswith(remote_path):
                    current_path = remote_path

                selected_path = self._show_remote_dialog(
                    ssh_client, folder_var, current_path, panel_name
                )
                if selected_path:
                    self._update_panel_history(
                        panel_name.split(" ")[1], folder_var, selected_path
                    )
                return selected_path
        except Exception as e:
            messagebox.showerror(
                "Error", f"Failed to connect to remote {panel_name}: {str(e)}"
            )
            return None

    def _show_remote_dialog(
        self,
        ssh_client: paramiko.SSHClient,
        folder_var: tk.StringVar,
        current_path: str,
        panel_name: str,
    ) -> str:
        """Show remote folder browser dialog.

        Args:
            ssh_client: SSH client to use
            folder_var: StringVar for the folder path
            current_path: Current remote path
            panel_name: Name of the panel

        Returns:
            Selected path or empty string if cancelled
        """
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

        def load_folders(path: str):
            """Load folders from remote path.

            Args:
                path: Remote path to load
            """
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

        def on_select(event: tk.Event):
            """Handle folder selection.

            Args:
                event: Tkinter event
            """
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
            """Handle folder selection confirmation."""
            result.set(path_var.get())
            dialog.destroy()

        def on_cancel():
            """Handle dialog cancellation."""
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

        return result.get()

    # ==========================================================================
    # FOLDER SCANNING METHODS
    # ==========================================================================

    def _populate_single_panel(
        self,
        panel: str,
        folder_path: str,
        ssh_client: Optional[paramiko.SSHClient] = None,
        active_rules: Optional[list] = None,
    ) -> threading.Thread:
        """Populate single panel tree view.

        Args:
            panel: Panel identifier ("A" or "B")
            folder_path: Path to scan
            ssh_client: Optional SSH client for remote scanning
            active_rules: Optional filter rules to apply

        Returns:
            Thread object that performs the scanning
        """

        def populate_thread_func():
            try:
                self.root.after(0, self._start_progress, panel)

                # Determine which panel to populate
                rules = (
                    self._get_active_filters() if active_rules is None else active_rules
                )
                use_ssh = (panel == "A" and self._has_ssh_a()) or (
                    panel == "B" and self._has_ssh_b()
                )

                files = self._scan_folder(
                    folder_path, use_ssh, ssh_client, panel, rules
                )

                target_files_dict = self.files_a if panel == "A" else self.files_b
                target_files_dict.update(files)
                self.root.after(0, lambda: self._update_status(panel, files))

                # Update tree view
                tree_structure = self._build_tree_structure(files)
                tree = getattr(self, f"tree_{panel.lower()}")

                def populate_and_adjust():
                    if tree:
                        self._batch_populate_tree(tree, tree_structure, rules)
                        self._adjust_tree_column_widths(tree)

                self.root.after(0, populate_and_adjust)

            except Exception as e:
                self._log(f"Error populating panel {panel}: {str(e)}")
                messagebox.showerror(
                    "Error", f"Failed to populate panel {panel}: {str(e)}"
                )
            finally:
                self.root.after(0, self._stop_progress)

        thread = threading.Thread(target=populate_thread_func, daemon=True)
        thread.start()
        return thread

    def _scan_folder(
        self,
        folder_path: str,
        use_ssh: bool,
        ssh_client: Optional[paramiko.SSHClient],
        panel_name: str,
        rules: Optional[list] = None,
    ) -> dict:
        """Scan folder (local or remote).

        Args:
            folder_path: Path to scan
            use_ssh: Whether to use SSH
            ssh_client: SSH client for remote scanning
            panel_name: Panel identifier
            rules: Filter rules to apply

        Returns:
            Dictionary of scanned files
        """
        if rules is None:
            rules = []

        if use_ssh:
            self._log(f"SSH scan panel {panel_name}")
            try:
                if ssh_client is None:
                    self._log(f"SSH client is None for panel {panel_name}")
                    return {}
                files = self._scan_remote(folder_path, ssh_client, rules)
                num_dirs = sum(1 for f in files.values() if f.get("type") == "dir")
                num_files = sum(1 for f in files.values() if f.get("type") == "file")
                self._log(
                    f"Found {num_dirs} folders and {num_files} files in panel {panel_name}"
                )
                return files
            except Exception as e:
                self._log(f"SSH connection failed for Panel {panel_name}: {str(e)}")
                return {}
        else:
            self._log(f"Using local folder scan for panel {panel_name}")
            files = self._scan_local(folder_path, rules)
            num_dirs = sum(1 for f in files.values() if f.get("type") == "dir")
            num_files = sum(1 for f in files.values() if f.get("type") == "file")
            self._log(
                f"Found {num_dirs} folders and {num_files} files in panel {panel_name}"
            )
            return files

    def _scan_local(self, folder_path: str, rules: Optional[list] = None) -> dict:
        """Scan a local folder.

        Args:
            folder_path: Path to scan
            rules: Filter rules to apply

        Returns:
            Dictionary of scanned files
        """
        files = {}
        if rules is None:
            rules = []

        try:
            for root, dirs, filenames in os.walk(
                folder_path, topdown=True, followlinks=True
            ):
                excluded_dirs = set()
                for d in dirs:
                    dir_rel_path = os.path.relpath(os.path.join(root, d), folder_path)
                    for pattern in rules:
                        if pattern.endswith("/") and fnmatch.fnmatch(
                            dir_rel_path.replace(os.sep, "/") + "/", pattern
                        ):
                            excluded_dirs.add(d)
                        elif fnmatch.fnmatch(
                            dir_rel_path.replace(os.sep, "/"), pattern
                        ):
                            excluded_dirs.add(d)
                        elif not pattern.endswith("/") and fnmatch.fnmatch(d, pattern):
                            excluded_dirs.add(d)

                dirs[:] = [d for d in dirs if d not in excluded_dirs]

                # Add directories
                for dirname in dirs:
                    full_path = os.path.join(root, dirname)
                    rel_path = os.path.relpath(full_path, folder_path)
                    if dirname not in excluded_dirs:
                        files[rel_path] = {
                            "type": "dir",
                            "full_path": full_path,
                        }

                # Add files
                for filename in filenames:
                    full_path = os.path.join(root, filename)
                    rel_path = os.path.relpath(full_path, folder_path)

                    if any(
                        fnmatch.fnmatch(rel_path.replace(os.sep, "/"), r) for r in rules
                    ):
                        continue

                    try:
                        stat_info = os.stat(full_path)
                        files[rel_path] = {
                            "size": stat_info.st_size,
                            "modified": stat_info.st_mtime,
                            "full_path": full_path,
                            "type": "file",
                        }
                    except OSError as e:
                        self._log(f"Error accessing {full_path}: {str(e)}")
        except Exception as e:
            self._log(f"Error scanning folder {folder_path}: {str(e)}")

        self._log(f"Local folder scan ended for {folder_path}")
        return files

    def _scan_remote(
        self,
        folder_path: str,
        ssh_client: paramiko.SSHClient,
        rules: Optional[list] = None,
    ) -> dict:
        """Scan remote folder using SSH.

        Args:
            folder_path: Remote path to scan
            ssh_client: SSH client to use
            rules: Filter rules to apply

        Returns:
            Dictionary of scanned files
        """
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

                        # Apply filtering logic
                        is_excluded = False
                        for pattern in rules:
                            if fnmatch.fnmatch(rel_path, pattern):
                                is_excluded = True
                                break
                            if any(
                                fnmatch.fnmatch(part, pattern)
                                for part in rel_path.split("/")
                            ):
                                is_excluded = True
                                break

                        if is_excluded:
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
            self._log(f"Error scanning remote folder {folder_path}: {str(e)}")

        self._log(f"Remote folder scan ended for {folder_path}")
        return files

    # ==========================================================================
    # TREE VIEW METHODS
    # ==========================================================================

    def _build_tree_structure(self, files: dict) -> dict:
        """Build hierarchical dictionary from flat file list.

        Args:
            files: Dictionary of files

        Returns:
            Hierarchical tree structure
        """
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

    def _batch_populate_tree(
        self,
        tree: Optional[ttk.Treeview],
        structure: dict,
        filter_rules: Optional[list] = None,
    ):
        """Populate treeview from hierarchical structure.

        Args:
            tree: Treeview widget to populate
            structure: Hierarchical file structure
            filter_rules: Filter rules to apply
        """
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
            parent_node: str,
            data: dict,
            filter_rules_for_insertion: list,
            current_path_prefix: str = "",
        ):
            """Recursively insert items into the tree.

            Args:
                parent_node: Parent node ID
                data: Data to insert
                filter_rules_for_insertion: Filter rules to apply
                current_path_prefix: Current path prefix
            """
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
                        values=(UNCHECKED_CHAR, "", "", ""),
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
                                UNCHECKED_CHAR,
                                self._format_size(content["size"]),
                                self._format_time(content["modified"]),
                                "",
                            ),
                            tags=("black",),
                        )

        insert_items("", structure, current_filter_rules, "")

    def _build_tree_map(
        self, tree: Optional[ttk.Treeview], parent_item: str = "", path: str = ""
    ) -> dict:
        """Build path to item ID map for a tree.

        Args:
            tree: Treeview widget
            parent_item: Parent item ID
            path: Current path

        Returns:
            Dictionary mapping paths to item IDs
        """
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

    def _update_tree_item(
        self,
        tree: Optional[ttk.Treeview],
        item_id: str,
        rel_path: str,
        status: str,
        status_color: str,
    ):
        """Update tree item with status.

        Args:
            tree: Treeview widget
            item_id: Item ID to update
            rel_path: Relative path of the item
            status: Status text to display
            status_color: Color for the status
        """
        if tree is None:
            return

        current_values = tree.item(item_id, "values")
        check_char = (
            CHECKED_CHAR if self.sync_states.get(rel_path, False) else UNCHECKED_CHAR
        )

        tree.item(
            item_id,
            values=(
                check_char,
                current_values[1] if len(current_values) > 1 else "",
                current_values[2] if len(current_values) > 2 else "",
                status,
            ),
            tags=(status_color, "sync_col_font"),
        )

    # ==========================================================================
    # COMPARISON METHODS
    # ==========================================================================

    def compare_folders(self):
        """Compare files between panels."""

        def compare_thread():
            self._log("Starting folder comparison...")

            path_a = self.folder_a.get()
            path_b = self.folder_b.get()

            if not path_a or not path_b:
                messagebox.showerror("Error", "Please select both folders to compare")
                return

            try:
                # Use existing data for comparison
                total_items = len(set(self.files_a.keys()) | set(self.files_b.keys()))
                self.root.after(
                    0, self._start_progress, None, total_items, "Comparing..."
                )

                # Use context managers for SSH connections during comparison
                with self._create_ssh_for_panel("A", optional=True) as ssh_a:
                    with self._create_ssh_for_panel("B", optional=True) as ssh_b:
                        use_ssh_a = ssh_a is not None
                        use_ssh_b = ssh_b is not None

                        self._update_trees_with_comparison(
                            self.files_a, self.files_b, use_ssh_a, use_ssh_b
                        )
            except Exception as e:
                self._log(f"Error during comparison: {str(e)}")
            finally:
                self.root.after(0, self._stop_progress)

        threading.Thread(target=compare_thread, daemon=True).start()

    def _prepare_comparison_data(self) -> tuple:
        """Prepare data structures needed for comparison.

        Returns:
            Tuple of (tree_a_map, tree_b_map, all_visible_paths)
        """
        tree_a_map = self._build_tree_map(self.tree_a)
        tree_b_map = self._build_tree_map(self.tree_b)
        all_visible_paths = set(tree_a_map.keys()) | set(tree_b_map.keys())
        self.sync_states.clear()
        return tree_a_map, tree_b_map, all_visible_paths

    def _calculate_item_statuses_parallel(
        self,
        all_visible_paths: set,
        files_a: dict,
        files_b: dict,
        use_ssh_a: bool,
        use_ssh_b: bool,
        ssh_client_a: Optional[paramiko.SSHClient] = None,
        ssh_client_b: Optional[paramiko.SSHClient] = None,
        max_workers: int = 4,
    ) -> tuple:
        """Calculate the status of all files and directories in parallel.

        Args:
            all_visible_paths: Set of all visible paths
            files_a: Files in Panel A
            files_b: Files in Panel B
            use_ssh_a: Whether Panel A uses SSH
            use_ssh_b: Whether Panel B uses SSH
            ssh_client_a: The SSH client for panel A (not used in parallel version)
            ssh_client_b: The SSH client for panel B (not used in parallel version)
            max_workers: Maximum number of parallel workers

        Returns:
            Tuple of (item_statuses, stats)
        """
        import time

        start_time = time.time()
        self._log(f"Parallel comparison: {max_workers} workers")

        item_statuses = {}
        dirty_folders = set()
        stats = {
            "identical": 0,
            "different": 0,
            "only_a": 0,
            "only_b": 0,
            "conflicts": 0,
        }

        # Separate files and directories for different processing
        file_paths = []
        dir_paths = []

        for rel_path in all_visible_paths:
            file_a_info = files_a.get(rel_path)
            file_b_info = files_b.get(rel_path)
            is_file = (file_a_info and file_a_info.get("type") == "file") or (
                file_b_info and file_b_info.get("type") == "file"
            )

            if is_file:
                file_paths.append(rel_path)
            else:
                dir_paths.append(rel_path)

        self._log(f"Processing {len(file_paths)} files, {len(dir_paths)} dirs")

        # Process files in parallel using connection pools
        def compare_single_file(rel_path: str) -> tuple:
            """Compare a single file using the connection pool.

            Args:
                rel_path: Relative path of the file

            Returns:
                Tuple of (rel_path, status, status_color)
            """
            file_a_info = files_a.get(rel_path)
            file_b_info = files_b.get(rel_path)

            # Use the connection pool for SSH connections only when needed
            if use_ssh_a and use_ssh_b:
                # Both sides are remote - use two connections
                with (
                    self.connection_manager.get_connection(
                        self.remote_host_a.get(),
                        self.remote_user_a.get(),
                        self.remote_pass_a.get(),
                        int(self.remote_port_a.get()),
                    ) as ssh_a,
                    self.connection_manager.get_connection(
                        self.remote_host_b.get(),
                        self.remote_user_b.get(),
                        self.remote_pass_b.get(),
                        int(self.remote_port_b.get()),
                    ) as ssh_b,
                ):
                    status, status_color = self._compare_files(
                        file_a_info, file_b_info, use_ssh_a, use_ssh_b, ssh_a, ssh_b
                    )
            elif use_ssh_a:
                # Only Panel A is remote - use one connection
                with self.connection_manager.get_connection(
                    self.remote_host_a.get(),
                    self.remote_user_a.get(),
                    self.remote_pass_a.get(),
                    int(self.remote_port_a.get()),
                ) as ssh_a:
                    status, status_color = self._compare_files(
                        file_a_info, file_b_info, use_ssh_a, False, ssh_a, None
                    )
            elif use_ssh_b:
                # Only Panel B is remote - use one connection
                with self.connection_manager.get_connection(
                    self.remote_host_b.get(),
                    self.remote_user_b.get(),
                    self.remote_pass_b.get(),
                    int(self.remote_port_b.get()),
                ) as ssh_b:
                    status, status_color = self._compare_files(
                        file_a_info, file_b_info, False, use_ssh_b, None, ssh_b
                    )
            else:
                # Both sides are local - no SSH needed
                status, status_color = self._compare_files(
                    file_a_info, file_b_info, False, False, None, None
                )

            return rel_path, status, status_color

        # Process files in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all file comparison tasks
            future_to_path = {
                executor.submit(compare_single_file, rel_path): rel_path
                for rel_path in file_paths
            }

            # Collect results as they complete
            for future in as_completed(future_to_path):
                rel_path, status, status_color = future.result()
                item_statuses[rel_path] = (status, status_color)

                # Update stats
                if status == "Identical":
                    stats["identical"] += 1
                    self.sync_states[rel_path] = False
                else:
                    if status == "Different":
                        stats["different"] += 1
                        dirty_folders.add(
                            os.path.dirname(rel_path)
                        )  # Add parent folder
                    elif status == "Conflict":
                        stats["conflicts"] += 1
                        dirty_folders.add(
                            os.path.dirname(rel_path)
                        )  # Add parent folder
                    elif status == "Only in A":
                        stats["only_a"] += 1
                        dirty_folders.add(os.path.dirname(rel_path))
                    elif status == "Only in B":
                        stats["only_b"] += 1
                        dirty_folders.add(os.path.dirname(rel_path))
                    self.sync_states[rel_path] = True

                # Update progress
                self.root.after(0, self._update_progress, 1)

        # Process directories (these are fast, no need for parallel)
        for rel_path in dir_paths:
            file_a_info = files_a.get(rel_path)
            file_b_info = files_b.get(rel_path)
            is_dir_in_a = file_a_info and file_a_info.get("type") == "dir"
            is_dir_in_b = file_b_info and file_b_info.get("type") == "dir"

            if is_dir_in_a and not is_dir_in_b:
                item_statuses[rel_path] = ("Only in A", "blue")
                stats["only_a"] += 1
                self.sync_states[rel_path] = True
                dirty_folders.add(os.path.dirname(rel_path))
            elif is_dir_in_b and not is_dir_in_a:
                item_statuses[rel_path] = ("Only in B", "red")
                stats["only_b"] += 1
                self.sync_states[rel_path] = True
                dirty_folders.add(os.path.dirname(rel_path))

        # Process shared directories
        for rel_path in sorted(dirty_folders):
            if (
                files_a.get(rel_path, {}).get("type") == "dir"
                and files_b.get(rel_path, {}).get("type") == "dir"
            ):
                if rel_path in dirty_folders:
                    status, status_color = "Different", "magenta"
                    self.sync_states[rel_path] = True
                else:
                    status, status_color = "Identical", "green"
                    self.sync_states[rel_path] = False
                item_statuses[rel_path] = (status, status_color)

        # Mark remaining shared directories as identical
        for rel_path in sorted(all_visible_paths):
            is_dir_in_both = (
                files_a.get(rel_path, {}).get("type") == "dir"
                and files_b.get(rel_path, {}).get("type") == "dir"
            )
            if is_dir_in_both and rel_path not in item_statuses:
                item_statuses[rel_path] = ("Identical", "green")

        elapsed_time = time.time() - start_time
        self._log(f"Parallel comparison done: {elapsed_time:.2f}s")

        return item_statuses, stats

    def _apply_comparison_to_ui(
        self,
        item_statuses: dict,
        stats: dict,
        tree_a_map: dict,
        tree_b_map: dict,
    ):
        """Update the UI with the results of the comparison.

        Args:
            item_statuses: Dictionary of item statuses
            stats: Statistics dictionary
            tree_a_map: Panel A tree map
            tree_b_map: Panel B tree map
        """
        for rel_path, (status, status_color) in item_statuses.items():
            self.root.after(0, self._update_progress, 1)
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
        status_summary += f"Conflicts: {stats['conflicts']}, "
        status_summary += f"Only in A: {stats['only_a']}, "
        status_summary += f"Only in B: {stats['only_b']}"
        self.status_a.set(status_summary)
        self.status_b.set("")

    def _update_trees_with_comparison(
        self, files_a: dict, files_b: dict, use_ssh_a: bool, use_ssh_b: bool
    ):
        """Update tree views with comparison results.

        Args:
            files_a: Files in Panel A
            files_b: Files in Panel B
            use_ssh_a: Whether Panel A uses SSH
            use_ssh_b: Whether Panel B uses SSH
        """
        tree_a_map, tree_b_map, all_visible_paths = self._prepare_comparison_data()

        # Choose comparison method based on SSH usage
        if use_ssh_a or use_ssh_b:
            self._log("Parallel comparison (remote)")
            item_statuses, stats = self._calculate_item_statuses_parallel(
                all_visible_paths,
                files_a,
                files_b,
                use_ssh_a,
                use_ssh_b,
                max_workers=4,
            )
        else:
            self._log("Parallel comparison (local)")
            item_statuses, stats = self._calculate_item_statuses_parallel(
                all_visible_paths,
                files_a,
                files_b,
                False,
                False,
                max_workers=4,
            )

        self._apply_comparison_to_ui(item_statuses, stats, tree_a_map, tree_b_map)

        # Configure tags
        for tree in [self.tree_a, self.tree_b]:
            if tree:
                tree.tag_configure("black", foreground="black")
                tree.tag_configure("green", foreground="green")
                tree.tag_configure("orange", foreground="orange")
                tree.tag_configure("blue", foreground="blue")
                tree.tag_configure("magenta", foreground="magenta")
                tree.tag_configure("red", foreground="red")

        # Adjust column widths
        if self.tree_a:
            self._adjust_tree_column_widths(self.tree_a)
        if self.tree_b:
            self._adjust_tree_column_widths(self.tree_b)

    def _compare_files(
        self,
        file_a: Optional[dict],
        file_b: Optional[dict],
        use_ssh_a: bool,
        use_ssh_b: bool,
        ssh_client_a: Optional[paramiko.SSHClient],
        ssh_client_b: Optional[paramiko.SSHClient],
    ) -> tuple:
        """Compare two files and return status.

        Args:
            file_a: File info from Panel A
            file_b: File info from Panel B
            use_ssh_a: Whether Panel A uses SSH
            use_ssh_b: Whether Panel B uses SSH
            ssh_client_a: The SSH client for panel A
            ssh_client_b: The SSH client for panel B

        Returns:
            Tuple of (status_text, color)
        """
        if file_a and file_b:
            is_a_file = file_a.get("type") == "file"
            is_b_file = file_b.get("type") == "file"

            if is_a_file != is_b_file:
                return "Conflict", "black"

            # If sizes differ, files cannot be identical.
            if file_a.get("size") != file_b.get("size"):
                return "Different", "orange"

            if (
                isinstance(file_a, dict)
                and "size" in file_a
                and isinstance(file_b, dict)
                and "size" in file_b
            ):
                try:
                    with (
                        self._open_file_handle(
                            file_a, use_ssh_a, ssh_client_a
                        ) as file_a_handle,
                        self._open_file_handle(
                            file_b, use_ssh_b, ssh_client_b
                        ) as file_b_handle,
                    ):
                        if not self._are_chunks_identical(file_a_handle, file_b_handle):
                            return "Different", "orange"

                    return "Identical", "green"

                except Exception as e:
                    self._log(f"Error during chunked file comparison: {e}")
                    return "Error", "black"
            else:
                # Fallback for items that exist in both but aren't comparable as files
                return "Different", "orange"
        elif file_a:
            return "Only in A", "blue"
        else:
            return "Only in B", "red"

    @contextmanager
    def _open_file_handle(
        self,
        file_info: dict,
        use_ssh: bool,
        ssh_client: Optional[paramiko.SSHClient],
    ) -> Iterator:
        """A context manager to open a file handle, local or remote.

        Args:
            file_info: File information dictionary
            use_ssh: Whether to use SSH
            ssh_client: SSH client for remote access

        Yields:
            File handle object

        Raises:
            ConnectionError: If SSH client is not connected
        """
        if use_ssh:
            if not ssh_client:
                raise ConnectionError("SSH client is not connected.")
            transport = ssh_client.get_transport()
            if not transport or not transport.is_active():
                raise ConnectionError("SSH client transport is not active.")
            sftp = ssh_client.open_sftp()
            file_handle = sftp.open(file_info["full_path"], "rb")
            try:
                yield file_handle
            finally:
                file_handle.close()
                sftp.close()
        else:
            with open(file_info["full_path"], "rb") as file_handle:
                yield file_handle

    def _are_chunks_identical(self, file_a_handle, file_b_handle) -> bool:
        """Compare two file handles chunk by chunk.

        Args:
            file_a_handle: First file handle
            file_b_handle: Second file handle

        Returns:
            True if files are identical, False otherwise
        """
        while True:
            chunk_a = file_a_handle.read(CHUNK_SIZE)
            chunk_b = file_b_handle.read(CHUNK_SIZE)

            if chunk_a != chunk_b:
                return False

            if not chunk_a:  # End of file, and all previous chunks matched
                return True

    # ==========================================================================
    # SYNCHRONIZATION METHODS
    # ==========================================================================

    def synchronize(self, direction: str):
        """Synchronize files between panels.

        Args:
            direction: Either "a_to_b" or "b_to_a"
        """

        def sync_thread():
            self._log(f"Starting synchronization: {direction}")

            # Determine source and target
            if direction == "a_to_b":
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

                # Get files to copy
                files_to_copy = self._get_files_to_copy(source_files_dict)

                if not files_to_copy:
                    self._log("No files selected for synchronization.")
                    messagebox.showinfo(
                        "Sync",
                        "No files are checked for synchronization or folders are already in sync.",
                    )
                    return

                # Start progress bar
                self.root.after(
                    0,
                    self._start_progress,
                    None,
                    len(files_to_copy),
                    "Synchronizing...",
                )

                # Determine source and target SSH connections
                if direction == "a_to_b":
                    source_ssh, target_ssh = (
                        self._get_ssh_client_for_panel("A"),
                        self._get_ssh_client_for_panel("B"),
                    )
                    source_use_ssh, target_use_ssh = use_ssh_a, use_ssh_b
                else:
                    source_ssh, target_ssh = (
                        self._get_ssh_client_for_panel("B"),
                        self._get_ssh_client_for_panel("A"),
                    )
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
                self._log("Synchronization completed. Refreshing view...")
                self._rescan_target_panel(
                    direction,
                    target_path,
                    use_ssh_a,
                    use_ssh_b,
                )

                # Trigger UI refresh on the main thread
                self.root.after(
                    0, lambda: self._refresh_ui_after_sync(use_ssh_a, use_ssh_b)
                )

                self._log("Synchronization completed")
                self.status_a.set("Synchronization completed successfully!")
                messagebox.showinfo(
                    "Success", "Synchronization completed successfully!"
                )

            except Exception as e:
                self._log(f"Synchronization failed: {str(e)}")
                messagebox.showerror("Error", f"Synchronization failed: {str(e)}")
            finally:
                self.root.after(0, self._stop_progress)

        threading.Thread(target=sync_thread, daemon=True).start()

    def _get_files_to_copy(self, source_files_dict: dict) -> list:
        """Get list of files to copy based on sync states.

        Args:
            source_files_dict: Dictionary of source files

        Returns:
            List of file paths to copy
        """
        files_to_sync = set()

        # Normalize helper to compare relative paths in a consistent manner
        def _norm(p: str) -> str:
            return p.replace(os.sep, "/")

        for rel_path, is_checked in self.sync_states.items():
            if not is_checked:
                continue

            source_item = source_files_dict.get(rel_path)
            if not source_item:
                continue

            if source_item.get("type") == "file":
                files_to_sync.add(rel_path)
            elif source_item.get("type") == "dir":
                # Only include files under this directory that are individually marked for sync.
                dir_prefix = _norm(rel_path.rstrip(os.sep)) + "/"
                for file_path, file_info in source_files_dict.items():
                    if file_info.get("type") != "file":
                        continue
                    if _norm(file_path).startswith(dir_prefix) and self.sync_states.get(
                        file_path, False
                    ):
                        files_to_sync.add(file_path)

        return sorted(files_to_sync)

    def _perform_sync(
        self,
        files_to_copy: list,
        source_files_dict: dict,
        target_path: str,
        source_ssh: Optional[paramiko.SSHClient],
        target_ssh: Optional[paramiko.SSHClient],
        source_use_ssh: bool,
        target_use_ssh: bool,
    ):
        """Perform file synchronization.

        Args:
            files_to_copy: List of files to copy
            source_files_dict: Dictionary of source files
            target_path: Target folder path
            source_ssh: Source SSH client
            target_ssh: Target SSH client
            source_use_ssh: Whether source uses SSH
            target_use_ssh: Whether target uses SSH

        Raises:
            ConnectionError: If SSH connections are required but not available
        """
        # Determine sync type based on source and target locations
        if source_use_ssh and target_use_ssh:  # Remote to Remote
            if source_ssh is None or target_ssh is None:
                raise ConnectionError(
                    "Both source and target SSH clients must be connected for remote-to-remote sync."
                )
            self._sync_remote_to_remote(
                files_to_copy, source_files_dict, target_path, source_ssh, target_ssh
            )
        elif source_use_ssh:  # Remote to Local
            if source_ssh is None:
                raise ConnectionError(
                    "Source SSH client must be connected for remote-to-local sync."
                )
            self._sync_remote_to_local(
                files_to_copy, source_files_dict, target_path, source_ssh
            )
        elif target_use_ssh:  # Local to Remote
            if target_ssh is None:
                raise ConnectionError(
                    "Target SSH client must be connected for local-to-remote sync."
                )
            self._sync_local_to_remote(
                files_to_copy, source_files_dict, target_path, target_ssh
            )
        else:  # Local to Local
            self._sync_local_to_local(files_to_copy, source_files_dict, target_path)

    def _rescan_target_panel(
        self,
        direction: str,
        target_path: str,
        use_ssh_a: bool,
        use_ssh_b: bool,
    ):
        """Rescan target panel after sync.

        Args:
            direction: Sync direction
            target_path: Target folder path
            use_ssh_a: Whether Panel A uses SSH
            use_ssh_b: Whether Panel B uses SSH
        """
        if direction == "a_to_b":
            self._log("Rescanning Panel B...")
            self.files_b = self._scan_folder(
                target_path, use_ssh_b, self._get_ssh_client_for_panel("B"), "B"
            )
            self._update_status("B", self.files_b)
        else:
            self._log("Rescanning Panel A...")
            self.files_a = self._scan_folder(
                target_path, use_ssh_a, self._get_ssh_client_for_panel("A"), "A"
            )
            self._update_status("A", self.files_a)

    def _sync_local_to_local(
        self, files_to_copy: list, source_files_dict: dict, target_path: str
    ):
        """Sync between local folders.

        Args:
            files_to_copy: List of files to copy
            source_files_dict: Dictionary of source files
            target_path: Target folder path
        """
        self._log(f"Syncing local files to {target_path}")

        for rel_path in files_to_copy:
            source_file = source_files_dict[rel_path]["full_path"]
            target_file = os.path.join(target_path, rel_path)

            # Create target directory if needed
            target_dir = os.path.dirname(target_file)
            os.makedirs(target_dir, exist_ok=True)

            # Ensure target is writable
            if os.path.exists(target_file) and not os.access(target_file, os.W_OK):
                if os.name == "posix":
                    # On Linux/Unix/macOS: add owner write bit
                    current_mode = os.stat(target_file).st_mode
                    os.chmod(target_file, current_mode | stat.S_IWUSR)
                elif os.name == "nt":
                    # On Windows: clear the read-only attribute
                    os.chmod(target_file, stat.S_IWRITE)
                else:
                    raise NotImplementedError(f"Unsupported OS: {os.name}")

            self._log(f"Copying: {rel_path}")
            try:
                shutil.copy2(source_file, target_file)
            except Exception as e:
                self._log(f"Error copying {rel_path}: {e}")
            finally:
                self.root.after(0, self._update_progress)

    def _sync_local_to_remote(
        self,
        files_to_copy: list,
        source_files_dict: dict,
        remote_path: str,
        ssh_client: Optional[paramiko.SSHClient],
    ):
        """Sync local to remote using SCP.

        Args:
            files_to_copy: List of files to copy
            source_files_dict: Dictionary of source files
            remote_path: Remote target path
            ssh_client: SSH client for remote access
        """
        if ssh_client is None:
            raise ConnectionError("SSH client for remote sync is not connected.")

        self._log(f"Syncing local files to remote {remote_path}")

        transport = ssh_client.get_transport()
        if not transport:
            raise ConnectionError("SSH client for remote sync is not connected.")

        with SCPClient(transport) as scp:
            for rel_path in files_to_copy:
                local_file = source_files_dict[rel_path]["full_path"]
                remote_file = os.path.join(remote_path, rel_path).replace(os.sep, "/")

                # Create remote directory
                remote_dir = os.path.dirname(remote_file).replace(os.sep, "/")
                try:
                    sftp = ssh_client.open_sftp()
                    sftp.stat(remote_dir)
                except FileNotFoundError:
                    self._log(f"Creating remote directory: {remote_dir}")
                    stdin, stdout, stderr = ssh_client.exec_command(
                        f"mkdir -p '{remote_dir}'"
                    )
                    stderr.read()

                scp.put(local_file, remote_file)
                self.root.after(0, self._update_progress)

    def _sync_remote_to_local(
        self,
        files_to_copy: list,
        source_files_dict: dict,
        local_path: str,
        ssh_client: Optional[paramiko.SSHClient],
    ):
        """Sync remote to local using SCP.

        Args:
            files_to_copy: List of files to copy
            source_files_dict: Dictionary of source files
            local_path: Local target path
            ssh_client: SSH client for remote access
        """
        if ssh_client is None:
            raise ConnectionError(
                "SSH client for remote-to-local sync is not connected."
            )

        self._log(f"Syncing remote files to local {local_path}")

        transport = ssh_client.get_transport()
        if not transport:
            raise ConnectionError(
                "SSH client for remote-to-local sync is not connected."
            )

        with SCPClient(transport) as scp:
            for rel_path in files_to_copy:
                remote_file = source_files_dict[rel_path]["full_path"]
                local_file = os.path.join(local_path, rel_path)

                # Create local directory
                local_dir = os.path.dirname(local_file)
                os.makedirs(local_dir, exist_ok=True)

                self._log(f"Downloading: {rel_path}")
                scp.get(remote_file, local_file)
                self.root.after(0, self._update_progress)

    def _sync_remote_to_remote(
        self,
        files_to_copy: list,
        source_files_dict: dict,
        target_path: str,
        source_ssh: Optional[paramiko.SSHClient],
        target_ssh: Optional[paramiko.SSHClient],
    ):
        """Sync between remote folders.

        Args:
            files_to_copy: List of files to copy
            source_files_dict: Dictionary of source files
            target_path: Target remote path
            source_ssh: Source SSH client
            target_ssh: Target SSH client
        """
        if source_ssh is None or target_ssh is None:
            raise ConnectionError(
                "Both source and target SSH clients must be connected for remote-to-remote sync."
            )

        self._log(f"Syncing remote files to remote {target_path}")

        for rel_path in files_to_copy:
            source_file_path = source_files_dict[rel_path]["full_path"]
            target_file_path = os.path.join(target_path, rel_path).replace(os.sep, "/")

            # Create target directory
            target_dir = os.path.dirname(target_file_path)
            target_ssh.exec_command(f"mkdir -p '{target_dir}'")

            source_transport = source_ssh.get_transport()
            target_transport = target_ssh.get_transport()
            if not source_transport or not target_transport:
                raise ConnectionError(
                    "SSH transport not available for remote-to-remote sync."
                )

            # Stream through local temp file
            with SCPClient(source_transport) as scp_source:
                with SCPClient(target_transport) as scp_target:
                    self._log(f"Copying remote-to-remote: {rel_path}")
                    with tempfile.NamedTemporaryFile() as temp_f:
                        scp_source.get(source_file_path, temp_f.name)
                        scp_target.put(temp_f.name, target_file_path)

            self.root.after(0, self._update_progress)

    # ==========================================================================
    # FILTER MANAGEMENT METHODS
    # ==========================================================================

    def _show_filters_dialog(self):
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
                    CHECKED_CHAR if item.get("active", True) else UNCHECKED_CHAR
                )
                filter_tree.insert("", "end", iid=i, values=(check_char, item["rule"]))

        def _create_rule_input_dialog(
            title: str, prompt_text: str, initial_value: str = ""
        ) -> Optional[str]:
            """Create a dialog to get a filter rule from the user.

            Args:
                title: Dialog title
                prompt_text: Prompt text for the user
                initial_value: Initial value for the input field

            Returns:
                User input or None if cancelled
            """
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
        def on_tree_click(event: tk.Event):
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

        def show_context_menu(event: tk.Event):
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
            self._log(f"Applying active filters: {active_rules}")

            # Clear file lists and trees
            self.files_a.clear()
            self.files_b.clear()
            self._update_status("A", self.files_a)
            self._update_status("B", self.files_b)
            if self.tree_a:
                self._batch_populate_tree(self.tree_a, {})
            if self.tree_b:
                self._batch_populate_tree(self.tree_b, {})

            def run_scans_and_compare():
                scan_threads = []
                if self.folder_a.get():
                    thread_a = self._populate_single_panel(
                        "A", self.folder_a.get(), active_rules=active_rules
                    )
                    scan_threads.append(thread_a)
                if self.folder_b.get():
                    thread_b = self._populate_single_panel(
                        "B", self.folder_b.get(), active_rules=active_rules
                    )
                    scan_threads.append(thread_b)

                # Wait for scanning threads
                for t in scan_threads:
                    t.join()

                # Run comparison
                self.root.after(0, self.compare_folders)

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
        self.root.wait_window(dialog)

    def _create_filter_tree(self, parent: tk.Toplevel) -> tuple:
        """Create tree view for filter dialog.

        Args:
            parent: Parent widget

        Returns:
            Tuple of (tree_frame, filter_tree)
        """
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

    def _get_active_filters(self) -> list:
        """Get active filter rule strings.

        Returns:
            List of active filter rules
        """
        return [
            item["rule"]
            for item in self.filter_rules
            if isinstance(item, dict) and item.get("active", True)
        ]

    # ==========================================================================
    # TREE EVENT HANDLERS
    # ==========================================================================

    def _on_tree_click(self, event: tk.Event):
        """Handle clicks to toggle checkboxes."""
        widget = event.widget
        if not isinstance(widget, ttk.Treeview):
            return
        tree = cast(ttk.Treeview, widget)

        region = tree.identify("region", event.x, event.y)
        if region != "cell":
            return

        column = tree.identify_column(event.x)
        if column == "#1":  # 'sync' column
            item_id = tree.identify_row(event.y)
            if item_id:
                rel_path = self._get_relative_path(tree, item_id)
                if rel_path is not None:
                    current_state = self.sync_states.get(rel_path, False)
                    new_state = not current_state
                    self.sync_states[rel_path] = new_state
                    char = CHECKED_CHAR if new_state else UNCHECKED_CHAR
                    current_values = list(tree.item(item_id, "values"))
                    current_values[0] = char
                    tree.item(item_id, values=current_values)

    def _on_tree_right_click(self, event: tk.Event):
        """Show context menu on right-click."""
        widget = event.widget
        if not isinstance(widget, ttk.Treeview):
            return
        tree = cast(ttk.Treeview, widget)

        item_id = tree.identify_row(event.y)

        if not item_id:
            return

        self._context_menu_tree = tree
        self._context_menu_item_id = item_id

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

        # Show/hide sync options based on the panel
        if tree is self.tree_a:
            self.tree_context_menu.entryconfig("Sync  ▶", state="normal")
            self.tree_context_menu.entryconfig("◀  Sync", state="disabled")
        elif tree is self.tree_b:
            self.tree_context_menu.entryconfig("Sync  ▶", state="disabled")
            self.tree_context_menu.entryconfig("◀  Sync", state="normal")
        else:
            self.tree_context_menu.entryconfig("Sync  ▶", state="disabled")
            self.tree_context_menu.entryconfig("◀  Sync", state="disabled")

        self.tree_context_menu.entryconfig("Delete", state="normal")

        # Enable/disable "Compare..." based on selections in both trees
        selected_a = self.tree_a.selection() if self.tree_a else ()
        selected_b = self.tree_b.selection() if self.tree_b else ()

        # Post the menu at the cursor's location
        self.tree_context_menu.tk_popup(event.x_root, event.y_root)

        if len(selected_a) == 1 and len(selected_b) == 1:
            self.tree_context_menu.entryconfig("Compare...", state="normal")
        else:
            self.tree_context_menu.entryconfig("Compare...", state="disabled")

    # ==========================================================================
    # CONTEXT MENU ACTIONS
    # ==========================================================================

    def _sync_selected_a_to_b(self):
        """Sync the selected item from Panel A to Panel B."""
        if not self.tree_a:
            return
        selected_items = self.tree_a.selection()
        if len(selected_items) != 1:
            messagebox.showwarning(
                "Sync Error", "Please select exactly one item to sync."
            )
            return
        rel_path = self._get_relative_path(self.tree_a, selected_items[0])
        if rel_path:
            self._sync_single_item(rel_path, "a_to_b")

    def _sync_selected_b_to_a(self):
        """Sync the selected item from Panel B to Panel A."""
        if not self.tree_b:
            return
        selected_items = self.tree_b.selection()
        if len(selected_items) != 1:
            messagebox.showwarning(
                "Sync Error", "Please select exactly one item to sync."
            )
            return
        rel_path = self._get_relative_path(self.tree_b, selected_items[0])
        if rel_path:
            self._sync_single_item(rel_path, "b_to_a")

    def _sync_single_item(self, rel_path: str, direction: str):
        """Handle the synchronization of a single file or directory.

        Args:
            rel_path: Relative path of the item
            direction: Sync direction ("a_to_b" or "b_to_a")
        """

        def sync_thread():
            try:
                if direction == "a_to_b":
                    source_files_dict = self.files_a
                    target_path = self.folder_b.get()
                    source_use_ssh, target_use_ssh = (
                        self._has_ssh_a(),
                        self._has_ssh_b(),
                    )
                else:  # b_to_a
                    source_files_dict = self.files_b
                    target_path = self.folder_a.get()
                    source_use_ssh, target_use_ssh = (
                        self._has_ssh_b(),
                        self._has_ssh_a(),
                    )

                source_item = source_files_dict.get(rel_path)
                if not source_item:
                    raise ValueError(f"Source item '{rel_path}' not found.")

                # Build files_to_copy:
                # - if a file was selected, sync that file
                # - if a directory was selected, only include files under it that are marked in sync_states
                files_to_copy = []
                if source_item.get("type") == "file":
                    files_to_copy = [rel_path]
                else:
                    # Directory: include only those child files that are marked for sync
                    dir_prefix = rel_path.rstrip(os.sep).replace(os.sep, "/") + "/"
                    for p, info in source_files_dict.items():
                        if info.get("type") != "file":
                            continue
                        if p.replace(os.sep, "/").startswith(
                            dir_prefix
                        ) and self.sync_states.get(p, False):
                            files_to_copy.append(p)

                self.root.after(
                    0,
                    self._start_progress,
                    None,
                    len(files_to_copy),
                    f"Syncing {rel_path}...",
                )

                # Correctly handle SSH clients
                with self._create_ssh_for_panel("A", optional=True) as ssh_a:
                    with self._create_ssh_for_panel("B", optional=True) as ssh_b:
                        if direction == "a_to_b":
                            ssh_src, ssh_tgt = ssh_a, ssh_b
                        else:
                            ssh_src, ssh_tgt = ssh_b, ssh_a

                        self._perform_sync(
                            files_to_copy,
                            source_files_dict,
                            target_path,
                            ssh_src,
                            ssh_tgt,
                            source_use_ssh,
                            target_use_ssh,
                        )

                self._log(f"Successfully synced '{rel_path}'. Refreshing view...")

                self.root.after(0, self._refresh_tree_after_sync, direction, rel_path)

            except Exception as e:
                self._log(f"Error syncing '{rel_path}': {e}")
                messagebox.showerror("Sync Error", f"Failed to sync item: {e}")
            finally:
                self.root.after(0, self._stop_progress)

        threading.Thread(target=sync_thread, daemon=True).start()

    def _refresh_tree_after_sync(self, direction: str, synced_item_rel_path: str):
        """Refresh the treeview after a single item has been synchronized.

        Args:
            direction: Sync direction
            synced_item_rel_path: Path of the synchronized item
        """
        self._log(f"Updating UI for synced item: {synced_item_rel_path}")

        # Determine source and destination data
        if direction == "a_to_b":
            source_files, dest_files = self.files_a, self.files_b
            tree_a, tree_b = self.tree_a, self.tree_b
        else:
            source_files, dest_files = self.files_b, self.files_a
            tree_a, tree_b = self.tree_b, self.tree_a

        source_item_info = source_files.get(synced_item_rel_path)
        if not source_item_info:
            self._log(
                f"Could not find source info for {synced_item_rel_path}, performing full refresh."
            )
            self.compare_folders()
            return

        # Update the destination file's metadata to match the source
        dest_files[synced_item_rel_path] = source_item_info.copy()

        # After sync, the item is no longer selected for sync
        self.sync_states[synced_item_rel_path] = False

        # Find the item in both trees and update its status
        if tree_a:
            tree_a_map = self._build_tree_map(tree_a)
            item_id_a = tree_a_map.get(synced_item_rel_path.replace(os.sep, "/"))
            if item_id_a:
                self._update_tree_item(
                    tree_a, item_id_a, synced_item_rel_path, "Identical", "green"
                )

        if tree_b:
            tree_b_map = self._build_tree_map(tree_b)
            item_id_b = tree_b_map.get(synced_item_rel_path.replace(os.sep, "/"))
            if item_id_b:
                self._update_tree_item(
                    tree_b, item_id_b, synced_item_rel_path, "Identical", "green"
                )

    def _select_all(self):
        """Select all different/new items."""
        tree = self.root.focus_get()
        if not isinstance(tree, ttk.Treeview) or tree not in (self.tree_a, self.tree_b):
            return

        diff_statuses = {
            "Different",
            "Only in A",
            "Only in B",
        }

        def traverse_and_select(item_id=""):
            for child_id in tree.get_children(item_id):
                status_values = tree.item(child_id, "values")
                status = status_values[3] if len(status_values) > 3 else ""
                if status in diff_statuses:
                    rel_path = self._get_relative_path(tree, child_id)
                    if rel_path is not None:
                        self.sync_states[rel_path] = True
                        current_values = list(status_values)
                        current_values[0] = CHECKED_CHAR
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
                    current_values[0] = UNCHECKED_CHAR
                    tree.item(child_id, values=tuple(current_values))

                # Recurse into children
                if tree.get_children(child_id):
                    traverse_and_deselect(child_id)

        traverse_and_deselect()

    def _compare_selected_files(self):
        """Launch g_compare.py with the two selected files."""
        if not self.tree_a or not self.tree_b:
            return

        selected_a = self.tree_a.selection()
        selected_b = self.tree_b.selection()

        if not (len(selected_a) == 1 and len(selected_b) == 1):
            messagebox.showwarning(
                "Selection Error", "Please select exactly one file in each panel."
            )
            return

        # Get file paths
        path_a = self._get_full_path_for_item(self.tree_a, selected_a[0], "A")
        path_b = self._get_full_path_for_item(self.tree_b, selected_b[0], "B")

        if not path_a or not path_b:
            messagebox.showerror(
                "Error", "Could not determine file paths for comparison."
            )
            return

        # Check if items are files
        rel_path_a = self._get_relative_path(self.tree_a, selected_a[0])
        rel_path_b = self._get_relative_path(self.tree_b, selected_b[0])

        is_file_a = self.files_a.get(rel_path_a, {}).get("type") == "file"
        is_file_b = self.files_b.get(rel_path_b, {}).get("type") == "file"

        if not (is_file_a and is_file_b):
            messagebox.showwarning(
                "Selection Error", "Please select files, not directories, to compare."
            )
            return

        # Launch g_compare.py in a new process
        try:
            g_compare_script_path = os.path.join(
                os.path.dirname(__file__), "g_compare.py"
            )
            if not os.path.exists(g_compare_script_path):
                messagebox.showerror(
                    "Error", f"Could not find g_compare.py at {g_compare_script_path}"
                )
                return

            command = [sys.executable, g_compare_script_path, path_a, path_b]
            self._log(f"Launching comparison: {' '.join(command)}")
            subprocess.Popen(command)

        except Exception as e:
            messagebox.showerror("Error", f"Failed to launch g_compare.py: {e}")
        finally:
            self._clear_context_menu_state()

    def _open_selected_item(self):
        """Open selected file with default app."""
        tree = self._context_menu_tree
        item_id = self._context_menu_item_id

        if tree is None or item_id is None:
            self._log("No item selected for opening via context menu.")
            self._clear_context_menu_state()
            return

        try:
            # This method handles downloading remote files to a temp location
            local_path = self._get_full_path_for_item(tree, item_id)

            if not local_path:
                self._log("Could not get a local path for the selected item.")
                return

            self._log(f"Opening file: {local_path}")
            if sys.platform == "win32":
                os.startfile(local_path)
            elif sys.platform == "darwin":  # macOS
                subprocess.Popen(["open", local_path])
            else:  # Linux and other Unix-like systems
                process = subprocess.Popen(
                    ["xdg-open", local_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                stdout, stderr = process.communicate()
                if process.returncode != 0:
                    error_message = stderr.decode().strip()
                    self._log(f"xdg-open error: {error_message}")
                    messagebox.showwarning(
                        "Warning", f"Could not open file: {error_message}"
                    )

        except Exception as e:
            messagebox.showerror("Error", f"Could not open file: {e}")
        finally:
            self._clear_context_menu_state()

    def _delete_selected_item(self):
        """Delete the selected file or directory."""
        tree = self._context_menu_tree
        item_id = self._context_menu_item_id

        if tree is None or item_id is None:
            self._log("No item selected for deletion via context menu.")
            self._clear_context_menu_state()
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
            try:
                self._log(f"Deleting item: {full_path}")
                if use_ssh:
                    with self._create_ssh_for_panel(panel) as ssh_client:
                        if ssh_client is None:
                            raise ConnectionError(
                                "Failed to establish SSH connection for deletion."
                            )

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

                        command = (
                            f"rm -rf '{full_path}'" if is_dir else f"rm '{full_path}'"
                        )
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

                self._log(f"Successfully deleted. Refreshing panel {panel}.")
                self._populate_single_panel(
                    panel, self.folder_a.get() if panel == "A" else self.folder_b.get()
                )
            except Exception as e:
                messagebox.showerror("Error", f"Failed to delete item: {e}")
                self._log(f"Error deleting {full_path}: {e}")
            finally:
                self._clear_context_menu_state()

        threading.Thread(target=delete_and_refresh, daemon=True).start()

    # ==========================================================================
    # UTILITY METHODS
    # ==========================================================================

    def _on_escape_key(self, event=None):
        """Handle Escape key press to clear selection and focus from trees."""
        # Hide context menu if it's visible
        self._clear_context_menu_state()
        self.tree_context_menu.unpost()

        widget = self.root.focus_get()
        if isinstance(widget, ttk.Treeview) and widget in (self.tree_a, self.tree_b):
            # Get current selection
            selection = widget.selection()
            # Deselect all items in the tree if there's a selection
            if selection:
                widget.selection_remove(selection)
            # Move focus away from the tree to the root window
            self.root.focus_set()

    def _clear_context_menu_state(self):
        """Clear the stored context menu tree and item ID."""
        self._context_menu_tree = None
        self._context_menu_item_id = None

    def _cleanup_temp_files(self):
        """Clean up temporary files created during the session."""
        for temp_file_path in self.temp_files_to_clean:
            try:
                os.remove(temp_file_path)
                self._log(f"Cleaned up temporary file: {temp_file_path}")
            except OSError as e:
                self._log(f"Error cleaning up temporary file {temp_file_path}: {e}")

    def _update_panel_history(
        self, panel_name: str, folder_var: tk.StringVar, new_path: str
    ):
        """Update and save panel history.

        Args:
            panel_name: Panel identifier ("A" or "B")
            folder_var: StringVar for the folder path
            new_path: New path to add to history
        """
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

        self._save_config()

    def _get_relative_path(
        self, tree: Optional[ttk.Treeview], item_id: str
    ) -> Optional[str]:
        """Construct relative path for item.

        Args:
            tree: Treeview widget
            item_id: Item ID

        Returns:
            Relative path or None
        """
        if tree is None or item_id is None:
            return None

        path_parts = []
        while item_id:
            text = tree.item(item_id, "text")
            path_parts.insert(0, text)
            item_id = tree.parent(item_id)

        if path_parts:
            return os.path.join(*path_parts)
        return None

    def _get_full_path_for_item(
        self,
        tree: Optional[ttk.Treeview],
        item_id: str,
        panel: Optional[str] = None,
    ) -> Optional[str]:
        """Get the full, possibly temporary, path for a tree item.

        Args:
            tree: Treeview widget
            item_id: Item ID
            panel: Optional panel identifier

        Returns:
            Full path or None
        """
        if tree is None:
            return None

        rel_path = self._get_relative_path(tree, item_id)
        if not rel_path:
            return None

        if panel is None:
            panel = "A" if tree is self.tree_a else "B"

        use_ssh = self._has_ssh_a() if panel == "A" else self._has_ssh_b()
        files_dict = self.files_a if panel == "A" else self.files_b
        full_path = files_dict.get(rel_path, {}).get("full_path")

        if not full_path:
            self._log(f"Could not determine full path for {rel_path}")
            return None

        if use_ssh:
            self._log(f"Downloading remote file: {full_path}")
            try:
                # Use the connection manager directly instead of _create_ssh_for_panel
                if panel == "A":
                    with self.connection_manager.get_connection(
                        self.remote_host_a.get(),
                        self.remote_user_a.get(),
                        self.remote_pass_a.get(),
                        int(self.remote_port_a.get()),
                    ) as ssh_client:
                        if not ssh_client:
                            raise ConnectionError("SSH client is not available.")

                        transport = ssh_client.get_transport()
                        if not transport or not transport.is_active():
                            raise ConnectionError("SSH transport is not available.")
                        with tempfile.NamedTemporaryFile(
                            delete=False, suffix=os.path.basename(rel_path)
                        ) as tmp:
                            with SCPClient(transport) as scp:
                                scp.get(full_path, tmp.name)
                            self.temp_files_to_clean.append(tmp.name)
                            return tmp.name
                else:  # Panel B
                    with self.connection_manager.get_connection(
                        self.remote_host_b.get(),
                        self.remote_user_b.get(),
                        self.remote_pass_b.get(),
                        int(self.remote_port_b.get()),
                    ) as ssh_client:
                        if not ssh_client:
                            raise ConnectionError("SSH client is not available.")

                        transport = ssh_client.get_transport()
                        if not transport or not transport.is_active():
                            raise ConnectionError("SSH transport is not available.")
                        with tempfile.NamedTemporaryFile(
                            delete=False, suffix=os.path.basename(rel_path)
                        ) as tmp:
                            with SCPClient(transport) as scp:
                                scp.get(full_path, tmp.name)
                            self.temp_files_to_clean.append(tmp.name)
                            return tmp.name
            except Exception as e:
                self._log(f"Failed to download remote file: {e}")
                return None
        return full_path

    def _adjust_tree_column_widths(self, tree: Optional[ttk.Treeview]):
        """Adjust column widths to fit content.

        Args:
            tree: Treeview widget to adjust
        """
        if tree is None:
            return

        try:
            # Ensure we measure with the same font
            font_family, font_size = self._get_mono_font()
            font = tkfont.Font(family=font_family, size=font_size)

            # Log message after font is determined
            panel_name = "A" if tree is self.tree_a else "B"
            self._log(
                f"Adjusting column widths for panel {panel_name} tree using "
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
            self._log(f"Could not adjust column widths: {e}")

    def _update_status(self, panel: str, files: dict):
        """Update the status bar text.

        Args:
            panel: Panel identifier ("A" or "B")
            files: Dictionary of files in the panel
        """
        num_dirs = sum(1 for f in files.values() if f.get("type") == "dir")
        num_files = sum(1 for f in files.values() if f.get("type") == "file")
        total_size = sum(f.get("size", 0) for f in files.values())
        status_text = f"Folders: {num_dirs}, Files: {num_files}, Size: {self._format_size(total_size)}"

        if panel == "A":
            self.status_a.set(status_text)
        else:
            self.status_b.set(status_text)

    def _start_progress(self, panel=None, max_value=0, text=""):
        """Show the progress bar.

        Args:
            panel: Panel identifier or None
            max_value: Maximum value for determinate progress
            text: Status text to display
        """
        if self.status_label_a:
            self.status_label_a.grid_remove()
        if self.status_label_b:
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

    def _update_progress(self, step=1):
        """Update the progress bar.

        Args:
            step: Step size to increment
        """
        self.progress_bar.step(step)

    def _stop_progress(self):
        """Hide the progress bar."""
        self.progress_bar.stop()
        self.progress_bar.grid_remove()
        if self.status_label_a:
            self.status_label_a.grid()
        if self.status_label_b:
            self.status_label_b.grid()

    def _refresh_ui_after_sync(self, use_ssh_a: bool, use_ssh_b: bool):
        """Refreshes both tree views and runs comparison after sync.

        Args:
            use_ssh_a: Whether Panel A uses SSH
            use_ssh_b: Whether Panel B uses SSH
        """
        rules = self._get_active_filters()

        # Clear existing trees
        if self.tree_a:
            self.root.after(0, lambda: self._batch_populate_tree(self.tree_a, {}))
        if self.tree_b:
            self.root.after(0, lambda: self._batch_populate_tree(self.tree_b, {}))

        try:
            self.files_a = self._scan_folder(
                self.folder_a.get(),
                use_ssh_a,
                self._get_ssh_client_for_panel("A"),
                "A",
                rules,
            )
            self.files_b = self._scan_folder(
                self.folder_b.get(),
                use_ssh_b,
                self._get_ssh_client_for_panel("B"),
                "B",
                rules,
            )

            tree_structure_a = self._build_tree_structure(self.files_a)
            if self.tree_a:
                self.root.after(
                    0,
                    lambda: self._batch_populate_tree(
                        self.tree_a, tree_structure_a, rules
                    ),
                )

            tree_structure_b = self._build_tree_structure(self.files_b)
            if self.tree_b:
                self.root.after(
                    0,
                    lambda: self._batch_populate_tree(
                        self.tree_b, tree_structure_b, rules
                    ),
                )

            self._update_trees_with_comparison(
                self.files_a, self.files_b, use_ssh_a, use_ssh_b
            )
        finally:
            pass  # Connections are now managed by the ConnectionManager

    # ==========================================================================
    # HELPER METHODS
    # ==========================================================================

    def _log(self, message: str):
        """Log message to console.

        Args:
            message: Message to log
        """
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def _format_size(self, size_bytes: Union[int, float]) -> str:
        """Format file size to be readable.

        Args:
            size_bytes: Size in bytes

        Returns:
            Formatted size string
        """
        for unit in [" B", "KB", "MB", "GB", "TB"]:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} PB"  # Beyond TB, it's Petabytes

    def _format_time(self, timestamp: float) -> str:
        """Format timestamp to a date string.

        Args:
            timestamp: Unix timestamp

        Returns:
            Formatted date string
        """
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

    def _center_dialog(
        self,
        dialog: tk.Toplevel,
        relative_to: Optional[Union[tk.Widget, tk.Toplevel]] = None,
    ):
        """Center a dialog on a parent window.

        Args:
            dialog: Dialog window to center
            relative_to: Parent window to center relative to
        """
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

    def _get_mono_font(self) -> tuple:
        """Returns a suitable monospace font family based on the current OS.

        Returns:
            Tuple of (font_family, font_size)
        """
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

    def _get_ssh_client_for_panel(
        self, panel_name: str
    ) -> Optional[paramiko.SSHClient]:
        """Get an SSH client for a panel using the connection manager.

        Args:
            panel_name: "A" or "B".

        Returns:
            A paramiko.SSHClient instance or None if not configured.
        """
        if panel_name == "A" and self._has_ssh_a():
            # For backward compatibility, get a connection and return it
            # Note: This connection won't be automatically returned to pool
            # TODO: Refactor callers to use context manager instead
            try:
                host = self.remote_host_a.get()
                user = self.remote_user_a.get()
                password = self.remote_pass_a.get()
                port = int(self.remote_port_a.get())

                # Get connection from pool (but don't use context manager)
                server_key = f"{user}@{host}:{port}"
                with self.connection_manager._lock:
                    if server_key not in self.connection_manager._pools:
                        self.connection_manager._initialize_pool(
                            server_key, host, user, password, port
                        )

                # Get a connection from pool
                conn = self.connection_manager._pools[server_key].get(timeout=10)

                # Check if connection is alive
                if (
                    not conn
                    or not conn.get_transport()
                    or not conn.get_transport().is_active()
                ):
                    conn = self.connection_manager._create_connection(
                        host, user, password, port
                    )

                # Log pool status for debugging
                pool_status = self.connection_manager.get_pool_status()
                self._log(f"Pool status {server_key}: {pool_status}")

                return conn
            except Exception as e:
                self._log(f"Error getting SSH client for panel A: {e}")
                return None

        if panel_name == "B" and self._has_ssh_b():
            try:
                host = self.remote_host_b.get()
                user = self.remote_user_b.get()
                password = self.remote_pass_b.get()
                port = int(self.remote_port_b.get())

                # Get connection from pool (but don't use context manager)
                server_key = f"{user}@{host}:{port}"
                with self.connection_manager._lock:
                    if server_key not in self.connection_manager._pools:
                        self.connection_manager._initialize_pool(
                            server_key, host, user, password, port
                        )

                # Get a connection from pool
                conn = self.connection_manager._pools[server_key].get(timeout=10)

                # Check if connection is alive
                if (
                    not conn
                    or not conn.get_transport()
                    or not conn.get_transport().is_active()
                ):
                    conn = self.connection_manager._create_connection(
                        host, user, password, port
                    )

                # Log pool status for debugging
                pool_status = self.connection_manager.get_pool_status()
                self._log(f"Pool status {server_key}: {pool_status}")

                return conn
            except Exception as e:
                self._log(f"Error getting SSH client for panel B: {e}")
                return None

        return None

    def _get_connection_pool_status(self) -> dict:
        """Get current connection pool status for debugging.

        Returns:
            Dictionary of pool statuses
        """
        return self.connection_manager.get_pool_status()

    # ==========================================================================
    # EVENT HANDLERS
    # ==========================================================================

    def _on_closing(self):
        """Handle window close event."""
        self._save_config()
        self._cleanup_temp_files()
        self.connection_manager.close_all()
        self.root.destroy()


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================


def main():
    """Main entry point for the application."""
    root = tk.Tk()
    GSynchro(root)
    root.mainloop()


if __name__ == "__main__":
    main()
