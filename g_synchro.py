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

# Standard library imports.
import atexit
import fnmatch
import json
import os
import posixpath
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import tkinter.font as tkfont
import shlex

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime
from queue import Queue
from typing import Optional, Iterator, cast, Union
from tkinter import filedialog, messagebox, ttk

from libs.g_button import GButton
from libs.g_theme import get_theme_colors

# Third-party imports.
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
DEFAULT_FONT_FAMILY = "Courier New"
DEFAULT_FONT_SIZE = 11


# ============================================================================
# HELPER UTILITIES (for remote path handling)
# ============================================================================


def _posix_quote(path: str) -> str:
    """Return a POSIX-shell-quoted version of `path` for safe exec_command use.

    This uses `shlex.quote` which is suitable for POSIX shells on remote hosts.
    """
    return shlex.quote(path)


def _posix_join(*parts: str) -> str:
    """Join path components using POSIX semantics for remote path construction."""
    return posixpath.join(*parts)


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
        self._pools = {}  # {server_key: Queue of connections}.
        self._pool_configs = {}  # {server_key: (host, user, password, port)}.
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

            # Create initial connections.
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
            # Initialize pool if needed.
            if server_key not in self._pools:
                self._initialize_pool(server_key, host, user, password, port)

        # Get connection from pool.
        conn = None
        try:
            conn = self._pools[server_key].get(timeout=10)

            # Check if connection is still alive.
            transport = conn.get_transport() if conn else None
            if not transport or not transport.is_active():
                self.log(f"Connection for {server_key} is dead, creating new one")
                conn = self._create_connection(host, user, password, port)

            yield conn

        except Exception as e:
            self.log(f"Error getting connection for {server_key}: {e}")
            # Try to create a new connection as fallback.
            conn = self._create_connection(host, user, password, port)
            yield conn
        finally:
            # Return connection to pool.
            if conn and server_key in self._pools:
                try:
                    # Check if connection is still good before returning.
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
                    # If we can't return to pool, close it.
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
        with self._lock:  # noqa: B007
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
# COMPARER CLASS
# ============================================================================


class Comparer:
    """Handles the logic for comparing file and directory structures."""

    def __init__(self, logger_func, connection_manager, root_widget):
        """Initialize the Comparer.

        Args:
            logger_func: A function to call for logging messages.
            connection_manager: An instance of ConnectionManager.
            root_widget: The root Tkinter widget for scheduling UI updates.
        """
        self.log = logger_func
        self.connection_manager = connection_manager
        self.root = root_widget

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

            if is_a_file and not is_b_file:
                return "Conflict", "black"
            if not is_a_file and is_b_file:
                return "Conflict", "black"
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
                    self.log(f"Error during chunked file comparison: {e}")
                    return "Different", "orange"
            else:
                # Fallback for items that exist in both but aren't comparable as
                # files.
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

            if not chunk_a:  # End of file, and all previous chunks matched.
                return True


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

        # Connection Manager.
        self.connection_manager = ConnectionManager(self._log, pool_size=4)

        # Comparer instance.
        self.comparer = Comparer(self._log, self.connection_manager, self.root)
        self.remote_host_a = tk.StringVar()
        self.remote_user_a = tk.StringVar()
        self.remote_pass_a = tk.StringVar()
        self.remote_port_a = tk.StringVar(value="22")

        self.remote_host_b = tk.StringVar()
        self.remote_user_b = tk.StringVar()
        self.remote_pass_b = tk.StringVar()
        self.remote_port_b = tk.StringVar(value="22")

        # Folder Paths.
        self.folder_a = tk.StringVar()
        self.folder_b = tk.StringVar()
        self.folder_a_history = []
        self.folder_b_history = []

        # UI Components.
        self.tree_a: Optional[ttk.Treeview] = None
        self.tree_b: Optional[ttk.Treeview] = None

        # Data Storage.
        self.files_a = {}
        self.files_b = {}
        self.filter_rules = []
        self.temp_files_to_clean = []

        # Options for fonts.
        self.options = {
            "font_family": DEFAULT_FONT_FAMILY,
            "font_size": DEFAULT_FONT_SIZE,
        }

        # Host histories: lists of dicts {'host','port','username'}.
        self.hosts_a = []
        self.hosts_b = []

        # Sync States.
        self.sync_states = {}

        # Status Variables.
        self._context_menu_tree: Optional[ttk.Treeview] = None
        self._context_menu_item_id: Optional[str] = None

        self.status_a = tk.StringVar()
        self.status_b = tk.StringVar()

        # Threading lock for progress bar updates.
        self._progress_lock = threading.Lock()

        self.colors = get_theme_colors()
        self._load_config()
        self._init_window()
        self._setup_ui()

        # Bind Escape key to clear selection and focus.
        self.root.bind("<Escape>", self._on_escape_key)

        # Ensure temporary files are cleaned up on exit.
        atexit.register(self._cleanup_temp_files)

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

            # Window geometry.
            if "WINDOW" in config and "geometry" in config["WINDOW"]:
                self.root.geometry(config["WINDOW"]["geometry"])

            # Panel A SSH.
            if "SSH_A" in config:
                self.remote_host_a.set(config["SSH_A"].get("host", ""))
                self.remote_port_a.set(config["SSH_A"].get("port", "22"))
                self.remote_user_a.set(config["SSH_A"].get("username", ""))

            # Panel B SSH.
            if "SSH_B" in config:
                self.remote_host_b.set(config["SSH_B"].get("host", ""))
                self.remote_port_b.set(config["SSH_B"].get("port", "22"))
                self.remote_user_b.set(config["SSH_B"].get("username", ""))

            # Filter rules.
            if "FILTERS" in config and "rules" in config["FILTERS"]:
                self._load_filter_rules(config["FILTERS"]["rules"])

            # Load options.
            if "OPTIONS" in config:
                self.options.update(config["OPTIONS"])

            # Panel A History.
            if "FOLDER_A_HISTORY" in config:
                self.folder_a_history = config["FOLDER_A_HISTORY"]
                if self.folder_a_history:
                    self.folder_a.set(self.folder_a_history[0])

            # Panel B History.
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
        # Update Panel A history.
        current_folder_a = self.folder_a.get()
        if current_folder_a:
            if current_folder_a in self.folder_a_history:
                self.folder_a_history.remove(current_folder_a)
            self.folder_a_history.insert(0, current_folder_a)
            self.folder_a_history = self.folder_a_history[:HISTORY_LENGTH]

        # Update Panel B history.
        current_folder_b = self.folder_b.get()
        if current_folder_b:
            if current_folder_b in self.folder_b_history:
                self.folder_b_history.remove(current_folder_b)
            self.folder_b_history.insert(0, current_folder_b)
            self.folder_b_history = self.folder_b_history[:HISTORY_LENGTH]

        # Ensure host histories include current entries.
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
            "OPTIONS": self.options,
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
        style = ttk.Style()

        # Progress bar style.
        style.configure(
            "flat.Horizontal.TProgressbar",
            troughcolor=self.colors["progress"]["trough"],
            background=self.colors["progress"]["background"],
            borderwidth=0,
            relief="flat",
        )

        # Configure Treeview heading font.
        style.configure(
            "TTreeview.Heading",
            font=(self.options["font_family"], self.options["font_size"], "bold"),
        )

        # Configure treeview font - row height is determined by the font on the
        # tags.
        style.configure(
            "TTreeview", font=(self.options["font_family"], self.options["font_size"])
        )

        # Reset map to avoid conflicts.
        style.map("TTreeview")

        # Create main layout.
        main_frame = self._create_main_frame()
        control_frame = self._create_control_frame(main_frame)
        panels_frame = self._create_panels_frame(main_frame)

        # Create UI components.
        self._create_control_buttons(control_frame)
        self._create_panels(panels_frame)
        self._create_status_bar(main_frame)

        # Create context menu.
        self._create_tree_context_menu()

        # Initial status.
        self.status_a.set("by Gino Bogo")

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
            ("Compare", self.compare_folders, "secondary"),
            ("Sync  ▶", lambda: self.synchronize("a_to_b"), "lightgreen"),
            ("◀  Sync", lambda: self.synchronize("b_to_a"), "lightblue"),
            ("Options", self._show_options_dialog, "secondary"),
        ]

        button_container = ttk.Frame(control_frame)
        button_container.pack(expand=True)

        for text, command, color in buttons_config:
            btn_colors = self.colors["buttons"].get(
                color, self.colors["buttons"]["default"]
            )
            GButton(
                button_container,
                text=text,
                command=command,
                width=100,
                height=34,
                **btn_colors,
            ).pack(side=tk.LEFT, padx=5, pady=5)

    def _create_panels_frame(self, main_frame: ttk.Frame) -> ttk.PanedWindow:
        """Create panels frame for displays.

        Args:
            main_frame: Parent main frame

        Returns:
            PanedWindow widget for the two main panels
        """
        panels_frame = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        panels_frame.grid(row=1, column=0, columnspan=3, sticky=tk.NSEW)
        return panels_frame

    def _create_panels(self, panels_frame: ttk.PanedWindow):
        """Create both Panel A and Panel B.

        Args:
            panels_frame: Parent panels frame
        """
        panel_configs = [
            {
                "title": "Panel A",
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

    def _create_panel(self, parent: ttk.PanedWindow, panel_config: dict):
        """Create an individual folder panel.

        Args:
            parent: Parent widget
            panel_config: Configuration dictionary for the panel
        """
        title = panel_config["title"]
        folder_var = panel_config["folder_var"]
        folder_history = panel_config["folder_history"]
        browse_command = panel_config["browse_command"]
        host_var = panel_config["host_var"]
        port_var = panel_config["port_var"]
        user_var = panel_config["user_var"]
        pass_var = panel_config["pass_var"]
        button_color = panel_config["button_color"]
        tree_attr = panel_config["tree_attr"]

        btn_colors = self.colors["buttons"].get(
            button_color, self.colors["buttons"]["default"]
        )

        panel_frame = ttk.Frame(parent, padding=0)
        panel = ttk.LabelFrame(panel_frame, text=title, padding="5")
        panel.pack(fill=tk.BOTH, expand=True)
        panel.columnconfigure(0, weight=0)
        panel.columnconfigure(1, weight=1)
        panel.rowconfigure(4, weight=1)

        # SSH settings widgets.
        ttk.Frame(panel)
        ttk.Label(panel, text="Host:").grid(
            row=0, column=0, padx=5, pady=5, sticky=tk.E
        )

        # Use Combobox for Host so user can select previously saved host tuples.
        panel_name = title.split(" ")[1]
        host_list = self.hosts_a if panel_name == "A" else self.hosts_b
        host_values = [h.get("host", "") for h in host_list]
        host_combobox = ttk.Combobox(
            panel, textvariable=host_var, values=host_values, width=15
        )
        host_combobox.grid(row=0, column=1, padx=5, pady=5, sticky=tk.EW)
        # When user selects a saved host, autofill port and username.
        host_combobox.bind(
            "<<ComboboxSelected>>", lambda e, pn=panel_name: self._on_host_selected(pn)
        )

        ttk.Label(panel, text="Port:").grid(
            row=0, column=2, padx=5, pady=5, sticky=tk.E
        )
        port_entry = ttk.Entry(panel, textvariable=port_var, width=8)
        port_entry.grid(row=0, column=3, padx=5, pady=5, sticky=tk.EW)

        GButton(
            panel,
            text="Test",
            command=lambda: self._test_ssh(title),
            width=60,
            height=30,
            **btn_colors,
        ).grid(row=0, column=4, padx=5, pady=5)

        # Username and Password row.
        ttk.Label(panel, text="Username:").grid(
            row=1, column=0, padx=5, pady=5, sticky=tk.E
        )
        user_entry = ttk.Entry(panel, textvariable=user_var, width=15)
        user_entry.grid(row=1, column=1, padx=5, pady=5, sticky=tk.EW)

        ttk.Label(panel, text="Password:").grid(
            row=1, column=2, padx=5, pady=5, sticky=tk.E
        )
        pass_entry = ttk.Entry(panel, textvariable=pass_var, show="*", width=15)
        pass_entry.grid(row=1, column=3, columnspan=2, padx=5, pady=5, sticky=tk.EW)

        # Folder controls.
        ttk.Label(panel, text="Path:").grid(
            row=2, column=0, padx=5, pady=5, sticky=tk.E
        )
        path_combobox = ttk.Combobox(
            panel, textvariable=folder_var, values=folder_history, width=20
        )
        path_combobox.grid(row=2, column=1, columnspan=2, padx=5, pady=5, sticky=tk.EW)

        def on_go():
            panel_name = title.split(" ")[1]
            folder_path = folder_var.get()
            if folder_path:
                self._populate_single_panel(panel_name, folder_path)

        GButton(
            panel, text="Go", command=on_go, width=60, height=30, **btn_colors
        ).grid(row=2, column=3, padx=5, pady=5)

        GButton(
            panel,
            text="Browse",
            command=browse_command,
            width=70,
            height=30,
            **btn_colors,
        ).grid(row=2, column=4, padx=5, pady=5)

        # Tree view.
        tree = self._create_tree_view(panel)
        tree.grid(row=4, column=0, columnspan=5, pady=(10, 0), sticky=tk.NSEW)

        # Vertical Scrollbar.
        v_scrollbar = ttk.Scrollbar(panel, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=v_scrollbar.set)
        v_scrollbar.grid(row=4, column=5, pady=(10, 0), sticky=tk.NS)

        # Horizontal Scrollbar.
        h_scrollbar = ttk.Scrollbar(panel, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(xscrollcommand=h_scrollbar.set)
        h_scrollbar.grid(row=5, column=0, columnspan=5, sticky=tk.EW)

        # Bind events.
        tree.bind("<Button-1>", self._on_tree_click)
        tree.bind("<Button-3>", self._on_tree_right_click)
        tree.bind("<Double-1>", self._on_tree_header_double_click)

        # Store tree reference.
        if tree_attr == "tree_a":
            self.tree_a = tree
        else:
            self.tree_b = tree

        parent.add(panel_frame, weight=1)

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

        # Configure columns.
        tree.heading("#0", text="Name")
        tree.column("#0", width=200, anchor="w", stretch=True)

        tree.heading("sync", text="Sync")
        tree.column("sync", width=50, anchor="center", stretch=False)

        tree.heading("size", text="Size")
        tree.column("size", width=80, anchor="e", stretch=False)

        tree.heading("modified", text="Modified")
        tree.column("modified", width=120, anchor="center", stretch=False)

        tree.heading("status", text="Status")
        tree.column("status", width=100, anchor="center", stretch=False)

        # Configure tags for different status colors.
        colors = self.colors["status"]
        for tag, color in colors.items():
            tree.tag_configure(tag, foreground=color)  # Font is applied later.

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

        # Status labels.
        self.status_label_a = ttk.Label(
            status_frame, textvariable=self.status_a, width=80, anchor=tk.W
        )
        self.status_label_a.grid(row=0, column=0, sticky=tk.EW, padx=0)

        self.status_label_b = ttk.Label(
            status_frame, textvariable=self.status_b, width=80, anchor=tk.W
        )
        self.status_label_b.grid(row=0, column=1, sticky=tk.EW, padx=0)

        # Progress bar.
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
            label="Open Folder", command=self._open_selected_folder
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

                # On successful connection, update host history so combobox
                # remembers this tuple.
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
            return  # noqa: B012
        entry = {"host": host, "port": port or "22", "username": username or ""}
        if panel_name == "A":
            # remove any existing with same host.
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

        # Main frame.
        main_dialog_frame = ttk.Frame(dialog, padding="10")
        main_dialog_frame.pack(fill=tk.BOTH, expand=True)

        result = tk.StringVar()

        # Top: Path display and entry.
        path_frame = ttk.Frame(main_dialog_frame)
        path_frame.pack(fill=tk.X, pady=(0, 5))

        path_var = tk.StringVar(value=current_path)
        ttk.Label(path_frame, text="Current Path:").pack(side=tk.LEFT)
        path_entry = ttk.Entry(path_frame, textvariable=path_var)
        path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        def go_to_path(event=None):
            load_folders(path_var.get())

        GButton(
            path_frame,
            text="Go",
            command=go_to_path,
            width=50,
            height=30,
            **self.colors["buttons"]["default"],
        ).pack(side=tk.LEFT, padx=(5, 0))
        path_entry.bind("<Return>", go_to_path)

        # Middle: Main content.
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

                # Use a more portable find command without -printf for BusyBox
                # compatibility. Quote the remote path for safety.
                command = f"find {_posix_quote(path)} -maxdepth 1 -mindepth 1 -type d"  # noqa: E501
                stdin, stdout, stderr = ssh_client.exec_command(command)
                error = stderr.read().decode().strip()
                if error:
                    raise Exception(error)

                for line in stdout:
                    full_dir_path = line.strip()
                    dir_name = full_dir_path.split("/")[-1]
                    listbox.insert(tk.END, dir_name)
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

        # Bottom: Buttons.
        button_frame = ttk.Frame(main_dialog_frame)
        button_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(5, 0))

        button_container = ttk.Frame(button_frame)
        button_container.pack()

        GButton(
            button_container,
            text="Cancel",
            command=on_cancel,
            width=100,
            height=34,
            **self.colors["buttons"]["default"],
        ).pack(side=tk.LEFT, padx=5)

        GButton(
            button_container,
            text="Select",
            command=on_select_folder,
            width=100,
            height=34,
            **self.colors["buttons"]["primary"],
        ).pack(side=tk.LEFT, padx=5)

        # Bind events and initial actions.
        listbox.bind("<Double-Button-1>", on_select)
        load_folders(current_path)

        # Center dialog and wait.
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

                # Determine which panel to populate.
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

                # Update tree view.
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
            # If an ssh_client is not provided, get one from the pool.
            if ssh_client:
                return self._scan_remote(folder_path, ssh_client, rules)
            else:
                try:
                    with self._create_ssh_for_panel(panel_name) as new_ssh_client:
                        if new_ssh_client is None:
                            self._log(
                                f"Failed to acquire SSH client for panel {panel_name}"
                            )
                            return {}
                        files = self._scan_remote(folder_path, new_ssh_client, rules)
                        num_dirs = sum(
                            1 for f in files.values() if f.get("type") == "dir"
                        )
                        num_files = sum(
                            1 for f in files.values() if f.get("type") == "file"
                        )
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

                # Add directories.
                for dirname in dirs:
                    full_path = os.path.join(root, dirname)
                    rel_path = os.path.relpath(full_path, folder_path)
                    if dirname not in excluded_dirs:
                        files[rel_path] = {
                            "type": "dir",
                            "full_path": full_path,
                        }

                # Add files.
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

        # Determine the correct stat command format (GNU, BusyBox, or BSD).
        try:
            # 1. Check for GNU stat.
            stdin, stdout, stderr = ssh_client.exec_command(
                "stat --version > /dev/null 2>&1"
            )
            if stdout.channel.recv_exit_status() == 0:
                stat_command = "stat -c '%n|%F|%s|%Y'"
                is_busybox = False
                self._log("Remote system uses GNU stat.")
            else:
                # 2. Check for BusyBox stat.
                stdin, stdout, stderr = ssh_client.exec_command(
                    "stat --help 2>&1 | grep -q BusyBox"
                )
                if stdout.channel.recv_exit_status() == 0:
                    # BusyBox stat. We get type separately.
                    stat_command = "stat -c '%n|%s|%Y'"
                    is_busybox = True
                    self._log("Remote system uses BusyBox stat.")
                else:
                    # 3. Fallback to BSD stat.
                    stat_command = "stat -f '%N|%HT|%z|%m'"
                    is_busybox = False
                    self._log("Remote system uses BSD stat.")

            # Construct the full find command; quote the remote folder_path Use
            # a raw f-string so the backslash-semicolon sequence is preserved
            # without triggering Python's invalid-escape warnings.
            find_command = rf"find {_posix_quote(folder_path)} -mindepth 1 -exec {stat_command} {{}} \; 2>/dev/null"

            stdin, stdout, stderr = ssh_client.exec_command(find_command)

            for line in stdout.readlines():
                line = line.strip()
                if not line:
                    continue

                try:
                    filepath = ""
                    if is_busybox:
                        filepath, size, mtime = line.split("|")  # noqa: B007
                        # For BusyBox, we determine type with a separate check.
                        is_dir_stdin, is_dir_stdout, _ = ssh_client.exec_command(
                            f"if [ -d {_posix_quote(filepath)} ]; then echo 'dir'; fi"
                        )
                        filetype = (
                            "directory"
                            if is_dir_stdout.read().decode().strip() == "dir"
                            else "regular file"
                        )
                    else:
                        filepath, filetype, size, mtime = line.split("|")

                    if not filepath.startswith(folder_path):
                        continue

                    rel_path = filepath[len(folder_path) :].lstrip("/")

                    # Apply filtering logic (simplified for clarity).
                    if any(fnmatch.fnmatch(rel_path, r) for r in rules) or any(
                        fnmatch.fnmatch(part, r)
                        for r in rules
                        for part in rel_path.split("/")
                    ):
                        continue

                    if "directory" in filetype.lower():
                        files[rel_path] = {"type": "dir", "full_path": filepath}
                    else:
                        files[rel_path] = {
                            "size": int(size),
                            "modified": float(mtime),
                            "full_path": filepath,
                            "type": "file",
                        }
                except (ValueError, IndexError):
                    self._log(f"Warning: Could not parse stat line: '{line}'")

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

        # If populating with data, disable stretching on the Name column to
        # enable horizontal scroll.
        if structure:
            tree.column("#0", stretch=False)

        # Clear existing items.
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

                # Apply filter rules.
                if any(
                    fnmatch.fnmatch(
                        os.path.join(current_path_prefix, name).replace(os.sep, "/"),
                        pattern,
                    )
                    for pattern in filter_rules_for_insertion
                ):
                    continue

                if isinstance(content, dict) and "size" not in content:
                    # Directory.
                    node = tree.insert(
                        parent_node,
                        "end",
                        text=name,
                        values=(UNCHECKED_CHAR, "", "", ""),
                        tags=("black", "custom_font"),
                        open=False,
                    )
                    insert_items(
                        node,
                        content,
                        filter_rules_for_insertion,
                        os.path.join(current_path_prefix, name),
                    )
                else:
                    # File.
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
                            tags=("black", "custom_font"),
                        )

        insert_items("", structure, current_filter_rules, "")

        # Configure the custom_font tag with current font settings.
        font_family = self.options["font_family"]  # noqa: B007
        font_size = self.options["font_size"]
        tree.tag_configure("custom_font", font=(font_family, font_size))

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
            tags=(status_color, "custom_font"),
        )

    # ==========================================================================
    # COMPARISON METHODS
    # ==========================================================================

    def compare_folders(self):
        """Compare files between panels."""
        # Prepare UI-related data on the main thread before starting the
        # background thread.
        folder_a_path = self.folder_a.get()
        folder_b_path = self.folder_b.get()

        if not folder_a_path or not folder_b_path:
            messagebox.showerror("Error", "Please select both folders to compare")
            return

        def compare_thread():
            self._log("Starting folder comparison...")

            try:
                # Start progress bar for scanning.
                self.root.after(0, self._start_progress, None, 0, "Scanning folders...")

                # Step 1: Scan folders in parallel.
                use_ssh_a = self._has_ssh_a()
                use_ssh_b = self._has_ssh_b()
                rules = self._get_active_filters()

                with ThreadPoolExecutor(max_workers=2) as executor:
                    future_a = executor.submit(
                        self._scan_folder, folder_a_path, use_ssh_a, None, "A", rules
                    )
                    future_b = executor.submit(
                        self._scan_folder, folder_b_path, use_ssh_b, None, "B", rules
                    )
                    self.files_a = future_a.result()
                    self.files_b = future_b.result()

                # Step 2: Prepare for comparison (still in background thread).
                total_items = len(set(self.files_a.keys()) | set(self.files_b.keys()))
                self.root.after(
                    0, self._start_progress, None, total_items, "Comparing files..."
                )

                # Step 3: Run the comparison logic (still in background thread).
                item_statuses, stats = self._run_comparison_logic(
                    use_ssh_a, use_ssh_b, self.files_a, self.files_b
                )

                # Step 4: Schedule final UI updates on the main thread.
                def final_ui_update():
                    """This function runs on the main thread to update the UI safely."""
                    # Populate trees with scanned data.
                    tree_structure_a = self._build_tree_structure(self.files_a)
                    tree_structure_b = self._build_tree_structure(self.files_b)
                    if self.tree_a:
                        self._batch_populate_tree(self.tree_a, tree_structure_a, rules)
                    if self.tree_b:
                        self._batch_populate_tree(self.tree_b, tree_structure_b, rules)

                    # Rebuild tree maps AFTER trees are populated with new items.
                    fresh_tree_a_map = self._build_tree_map(self.tree_a)
                    fresh_tree_b_map = self._build_tree_map(self.tree_b)

                    # Apply comparison results to the UI.
                    self._apply_comparison_to_ui(
                        item_statuses, stats, fresh_tree_a_map, fresh_tree_b_map
                    )

                    # Adjust column widths after applying comparison results.
                    if self.tree_a:
                        self._adjust_tree_column_widths(self.tree_a)
                    if self.tree_b:
                        self._adjust_tree_column_widths(self.tree_b)

                self.root.after(0, final_ui_update)

            except Exception as e:
                self._log(f"Error during comparison: {str(e)}")
            finally:
                self.root.after(0, self._stop_progress)

        threading.Thread(target=compare_thread, daemon=True).start()

    def _run_comparison_logic(
        self, use_ssh_a: bool, use_ssh_b: bool, files_a: dict, files_b: dict
    ) -> tuple:
        """
        Executes the file comparison logic. This method is designed to be run
        in a background thread.

        Args:
            use_ssh_a: Whether Panel A uses SSH.
            use_ssh_b: Whether Panel B uses SSH.
            files_a: The file dictionary for panel A.
            files_b: The file dictionary for panel B.

        Returns:
            A tuple containing (item_statuses, stats).
        """
        all_paths = set(files_a.keys()) | set(files_b.keys())
        if use_ssh_a or use_ssh_b:
            self._log("Parallel comparison (remote)")
            ssh_config_a = self._get_ssh_config_for_panel("A")
            ssh_config_b = self._get_ssh_config_for_panel("B")
            item_statuses, stats, dirty_folders = (
                self._calculate_item_statuses_parallel(
                    all_paths,
                    self.files_a,
                    self.files_b,
                    use_ssh_a,
                    use_ssh_b,
                    ssh_config_a,
                    ssh_config_b,
                )
            )
        else:
            self._log("Parallel comparison (local)")
            item_statuses, stats, dirty_folders = (
                self._calculate_item_statuses_parallel(
                    all_paths,
                    self.files_a,
                    self.files_b,
                    False,
                    False,
                    {},
                    {},
                    max_workers=os.cpu_count() or 4,
                )
            )

        # Propagate the dirty status up the hierarchy.
        self._propagate_dirty_folders(item_statuses, dirty_folders)

        return item_statuses, stats

    def _propagate_dirty_folders(self, item_statuses: dict, dirty_folders: set):
        """
        Recursively mark all parent directories of dirty folders as "Different".

        Args:
            item_statuses: The dictionary of item statuses to update.
            dirty_folders: The set of folders initially marked as dirty.
        """
        # This set will store the full paths of all parent directories that
        # contain a change.
        parents_to_mark_different = set()
        for path in dirty_folders:
            # A folder that contains changes is itself different.
            parents_to_mark_different.add(path)

            # Start from the immediate parent of the changed item.
            current_path = os.path.dirname(path)

            # Traverse up the directory tree to the root.
            while current_path and current_path != ".":
                parents_to_mark_different.add(current_path)
                parent = os.path.dirname(current_path)
                current_path = parent

        # If any item caused a "dirty" folder, the root directory is also
        # considered different.
        if dirty_folders:
            parents_to_mark_different.add(".")

        # Now, apply the 'Different' status only to the collected parent
        # directories. This avoids incorrectly overwriting the status of unique
        # items themselves.
        for path in parents_to_mark_different:
            # Only mark a path as 'Different' if it's not already a unique item.
            # This ensures unique folders containing other unique items remain
            # 'Only in A/B'.
            if item_statuses.get(path, (None,))[0] not in ("Only in A", "Only in B"):
                item_statuses[path] = ("Different", "magenta")

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

    def _get_ssh_config_for_panel(self, panel_name: str) -> dict:
        """Get SSH configuration for a given panel."""
        if panel_name == "A":
            return {
                "host": self.remote_host_a.get(),
                "user": self.remote_user_a.get(),
                "password": self.remote_pass_a.get(),
                "port": int(self.remote_port_a.get()),
            }
        else:
            return {
                "host": self.remote_host_b.get(),
                "user": self.remote_user_b.get(),
                "password": self.remote_pass_b.get(),
                "port": int(self.remote_port_b.get()),
            }

    def _calculate_item_statuses_parallel(
        self,
        all_visible_paths: set,
        files_a: dict,
        files_b: dict,
        use_ssh_a: bool,
        use_ssh_b: bool,
        ssh_config_a: dict,
        ssh_config_b: dict,
        max_workers: int = 4,
    ) -> tuple:
        """Calculate the status of all files and dirs in parallel.

        Args:
            all_visible_paths: Set of all visible paths
            files_a: Files in Panel A
            files_b: Files in Panel B
            use_ssh_a: Whether Panel A uses SSH
            use_ssh_b: Whether Panel B uses SSH
            ssh_config_a: SSH configuration for panel A
            ssh_config_b: SSH configuration for panel B
            max_workers: Maximum number of parallel workers

        Returns:
            Tuple of (item_statuses, stats, dirty_folders)
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

        # Separate files and directories for different processing.
        file_paths = []
        dir_paths = []

        for rel_path in all_visible_paths:
            file_a_info = files_a.get(rel_path)
            file_b_info = files_b.get(rel_path)

            is_file_a = file_a_info and file_a_info.get("type") == "file"
            is_dir_a = file_a_info and file_a_info.get("type") == "dir"
            is_file_b = file_b_info and file_b_info.get("type") == "file"
            is_dir_b = file_b_info and file_b_info.get("type") == "dir"

            # Handle file vs. directory conflicts.
            if (is_file_a and is_dir_b) or (is_dir_a and is_file_b):
                item_statuses[rel_path] = ("Conflict", "black")
                stats["conflicts"] += 1
                self.sync_states[rel_path] = True
                dirty_folders.add(os.path.dirname(rel_path))
            # If it's a file on at least one side (and not a conflict).
            elif is_file_a or is_file_b:
                file_paths.append(rel_path)
            else:
                # It's a directory on both sides, or only on one (and not a
                # conflict).
                dir_paths.append(rel_path)

        self._log(f"Processing {len(file_paths)} files, {len(dir_paths)} dirs")

        # Process files in parallel using connection pools.
        def compare_single_file(rel_path: str) -> tuple:
            """Compare a single file using the connection pool.

            Args:
                rel_path: Relative path of the file

            Returns:
                Tuple of (rel_path, status, status_color)
            """
            file_a_info = files_a.get(rel_path)
            file_b_info = files_b.get(rel_path)

            # Use the connection pool for SSH connections only when needed.
            if use_ssh_a and use_ssh_b:
                # Both sides are remote - use two connections.
                with (
                    self.connection_manager.get_connection(**ssh_config_a) as ssh_a,
                    self.connection_manager.get_connection(**ssh_config_b) as ssh_b,
                ):
                    status, status_color = self.comparer._compare_files(
                        file_a_info,
                        file_b_info,
                        use_ssh_a,
                        use_ssh_b,
                        ssh_a,
                        ssh_b,  # noqa: E501
                    )
            elif use_ssh_a:
                # Only Panel A is remote - use one connection.
                with self.connection_manager.get_connection(**ssh_config_a) as ssh_a:
                    status, status_color = self.comparer._compare_files(
                        file_a_info, file_b_info, use_ssh_a, False, ssh_a, None
                    )
            elif use_ssh_b:
                # Only Panel B is remote - use one connection.
                with self.connection_manager.get_connection(**ssh_config_b) as ssh_b:
                    status, status_color = self.comparer._compare_files(
                        file_a_info, file_b_info, False, use_ssh_b, None, ssh_b
                    )
            else:
                # Both sides are local - no SSH needed.
                status, status_color = self.comparer._compare_files(
                    file_a_info, file_b_info, False, False, None, None
                )

            return rel_path, status, status_color

        # Process files in parallel.
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all file comparison tasks.
            future_to_path = {
                executor.submit(compare_single_file, rel_path): rel_path
                for rel_path in file_paths
            }

            # Collect results as they complete.
            for future in as_completed(future_to_path):
                rel_path, status, status_color = future.result()
                item_statuses[rel_path] = (status, status_color)

                # Update stats.
                if status == "Identical":
                    stats["identical"] += 1
                    self.sync_states[rel_path] = False
                else:
                    if status == "Different":
                        stats["different"] += 1
                        dirty_folders.add(os.path.dirname(rel_path))
                    elif status == "Conflict":
                        stats["conflicts"] += 1
                        dirty_folders.add(os.path.dirname(rel_path))
                    elif status == "Only in A":
                        stats["only_a"] += 1
                        dirty_folders.add(os.path.dirname(rel_path))
                    elif status == "Only in B":
                        stats["only_b"] += 1
                        dirty_folders.add(os.path.dirname(rel_path))

                    self.sync_states[rel_path] = True

                # Update progress.
                self.root.after(0, self._update_progress, 1)

        # Process directories (these are fast, no need for parallel).
        for rel_path in dir_paths:  # noqa: B007
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

        # Mark remaining shared directories as identical.
        for rel_path in sorted(all_visible_paths):  # noqa: B007
            is_dir_in_both = (
                files_a.get(rel_path, {}).get("type") == "dir"
                and files_b.get(rel_path, {}).get("type") == "dir"
            )
            if is_dir_in_both and rel_path not in item_statuses:
                item_statuses[rel_path] = ("Identical", "green")

        elapsed_time = time.time() - start_time
        self._log(f"Parallel comparison done: {elapsed_time:.2f}s")

        return item_statuses, stats, dirty_folders

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
        # Process items and apply status only to the panels where they exist.
        for rel_path, (status, status_color) in item_statuses.items():
            self.root.after(0, self._update_progress, 1)

            # Update Panel A if the item exists in its tree.
            if rel_path in tree_a_map:
                self._update_tree_item(
                    self.tree_a, tree_a_map[rel_path], rel_path, status, status_color
                )

            # Update Panel B if the item exists in its tree.
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

            # Determine source and target.
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
                # Set up SSH connections.
                use_ssh_a = self._has_ssh_a()
                use_ssh_b = self._has_ssh_b()

                # Get files to copy.
                files_to_copy = self._get_files_to_copy(source_files_dict)
                target_files_dict = (
                    self.files_b if direction == "a_to_b" else self.files_a
                )

                if not files_to_copy:
                    self._log("No files selected for synchronization.")
                    messagebox.showinfo(
                        "Sync",
                        "No files are checked for synchronization or folders are already in sync.",
                    )
                    return

                # Start progress bar.
                self.root.after(
                    0,
                    self._start_progress,
                    None,
                    len(files_to_copy),
                    "Synchronizing...",
                )

                # Determine source and target SSH connections.
                if direction == "a_to_b":
                    source_use_ssh, target_use_ssh = use_ssh_a, use_ssh_b
                else:
                    source_use_ssh, target_use_ssh = use_ssh_b, use_ssh_a

                # Perform synchronization.
                with (  # noqa: B007
                    self._create_ssh_for_panel("A", optional=True) as ssh_a,
                    self._create_ssh_for_panel("B", optional=True) as ssh_b,
                ):
                    ssh_src, ssh_tgt = (
                        (ssh_a, ssh_b) if direction == "a_to_b" else (ssh_b, ssh_a)
                    )
                    self._perform_sync(
                        files_to_copy,
                        source_files_dict,
                        target_path,
                        ssh_src,
                        ssh_tgt,
                        source_use_ssh,
                        target_use_ssh,
                        target_files_dict,
                    )

                # Rescan target folder.
                self._log("Synchronization completed. Refreshing comparison...")

                # Trigger UI refresh on the main thread After a sync, a full
                # comparison is the cleanest way to update the UI state.
                self.root.after(0, self.compare_folders)

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
            with self._create_ssh_for_panel("B", optional=True) as ssh_b:
                self._log("Rescanning Panel B...")
                self.files_b = self._scan_folder(target_path, use_ssh_b, ssh_b, "B")
                self._update_status("B", self.files_b)
        else:
            with self._create_ssh_for_panel("A", optional=True) as ssh_a:
                self._log("Rescanning Panel A...")
                self.files_a = self._scan_folder(target_path, use_ssh_a, ssh_a, "A")
                self._update_status("A", self.files_a)

    def _get_files_to_copy(self, source_files_dict: dict) -> list:
        """Get list of files to copy based on sync states.

        Args:
            source_files_dict: Dictionary of source files

        Returns:
            List of file paths to copy
        """
        files_to_sync = set()

        # Normalize helper to compare relative paths in a consistent manner.
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
                # Only include files under this directory that are individually
                # marked for sync.
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
        target_files_dict: dict,
    ):
        """Perform file synchronization."""
        # Determine sync type based on source and target locations.

        if source_use_ssh and target_use_ssh:  # Remote to Remote.
            self._sync_remote_to_remote(
                files_to_copy,
                source_files_dict,
                target_path,
                source_ssh,
                target_ssh,
                target_files_dict,
            )
        elif source_use_ssh:  # Remote to Local.
            self._sync_remote_to_local(
                files_to_copy,
                source_files_dict,
                target_path,
                source_ssh,
                target_files_dict,
            )
        elif target_use_ssh:  # Local to Remote.
            self._sync_local_to_remote(
                files_to_copy,
                source_files_dict,
                target_path,
                target_ssh,
                target_files_dict,
            )
        else:  # Local to Local.
            self._sync_local_to_local(
                files_to_copy, source_files_dict, target_path, target_files_dict
            )

    def _sync_local_to_local(
        self,
        files_to_copy: list,
        source_files_dict: dict,
        target_path: str,
        target_files_dict: dict,
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

            # Create target directory if needed.
            target_dir = os.path.dirname(target_file)
            os.makedirs(target_dir, exist_ok=True)

            # Ensure target is writable.
            if os.path.exists(target_file) and not os.access(target_file, os.W_OK):
                if os.name == "posix":
                    # On Linux/Unix/macOS: add owner write bit.
                    current_mode = os.stat(target_file).st_mode
                    os.chmod(target_file, current_mode | stat.S_IWUSR)
                elif os.name == "nt":
                    # On Windows: clear the read-only attribute.
                    os.chmod(target_file, stat.S_IWRITE)
                else:
                    raise NotImplementedError(f"Unsupported OS: {os.name}")

            # Resolve conflicts by deleting target if it's a directory.
            target_item = target_files_dict.get(rel_path)
            if target_item and target_item.get("type") == "dir":
                shutil.rmtree(target_file)

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
        target_files_dict: dict,
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
                remote_file = _posix_join(remote_path, rel_path)

                # Create remote directory.
                remote_dir = posixpath.dirname(remote_file)
                try:
                    sftp = ssh_client.open_sftp()
                    sftp.stat(remote_dir)
                except FileNotFoundError:
                    self._log(f"Creating remote directory: {remote_dir}")
                    stdin, stdout, stderr = ssh_client.exec_command(
                        f"mkdir -p {_posix_quote(remote_dir)}"
                    )
                    stderr.read()

                # Resolve conflicts by deleting target if it's a directory.
                target_item = target_files_dict.get(rel_path)
                if target_item and target_item.get("type") == "dir":
                    stdin, stdout, stderr = ssh_client.exec_command(
                        f"rm -rf {_posix_quote(remote_file)}"
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
        target_files_dict: dict,
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

                # Create local directory.
                local_dir = os.path.dirname(local_file)
                os.makedirs(local_dir, exist_ok=True)

                # Resolve conflicts by deleting target if it's a directory.
                target_item = target_files_dict.get(rel_path)
                if target_item and target_item.get("type") == "dir":
                    shutil.rmtree(local_file)

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
        target_files_dict: dict,
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
            target_file_path = _posix_join(target_path, rel_path)

            # Create target directory
            target_dir = posixpath.dirname(target_file_path)
            target_ssh.exec_command(f"mkdir -p {_posix_quote(target_dir)}")

            source_transport = source_ssh.get_transport()
            target_transport = target_ssh.get_transport()
            if not source_transport or not target_transport:
                raise ConnectionError(
                    "SSH transport not available for remote-to-remote sync."
                )

            # Stream through local temp file.
            with SCPClient(source_transport) as scp_source:
                with SCPClient(target_transport) as scp_target:
                    self._log(f"Copying remote-to-remote: {rel_path}")

                    # Use a NamedTemporaryFile with delete=False and close it
                    # before calling external tools (SCP) so the file is
                    # accessible on platforms like Windows where an open file
                    # can be locked by the creating process.
                    temp_f = tempfile.NamedTemporaryFile(delete=False)
                    temp_name = temp_f.name
                    try:
                        temp_f.close()
                        scp_source.get(source_file_path, temp_name)

                        # Resolve conflicts by deleting target if it's a directory.
                        target_item = target_files_dict.get(rel_path)
                        if target_item and target_item.get("type") == "dir":
                            target_ssh.exec_command(
                                f"rm -rf {_posix_quote(target_file_path)}"
                            )

                        scp_target.put(temp_name, target_file_path)
                    finally:  # noqa: B007
                        try:
                            os.remove(temp_name)
                        except Exception:
                            # Best-effort cleanup; log and continue.
                            self._log(
                                f"Warning: could not remove temp file {temp_name}"
                            )

            self.root.after(0, self._update_progress)

    # ==========================================================================
    # FILTER MANAGEMENT METHODS
    # ==========================================================================

    def _show_filters_dialog(self):
        """Show filter rules dialog."""
        # Create a temporary copy to work with.
        temp_filters = [dict(item) for item in self.filter_rules]

        # Create dialog window.
        dialog = tk.Toplevel(self.root)
        dialog.title("Edit Filters")
        dialog.geometry("400x400")
        dialog.minsize(300, 300)
        dialog.transient(self.root)
        dialog.grab_set()

        # Style setup.
        style = ttk.Style()
        dialog_bg = style.lookup("TFrame", "background")
        dialog.configure(bg=dialog_bg)

        # Setup context menu.
        context_menu = tk.Menu(dialog, tearoff=0)

        # Layout.
        dialog.rowconfigure(0, weight=1)
        dialog.columnconfigure(0, weight=1)

        # Tree view for filters.
        tree_frame, filter_tree = self._create_filter_tree(dialog)
        tree_frame.grid(row=0, column=0, padx=10, pady=10, sticky=tk.NSEW)

        # Populate tree.
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

            GButton(
                button_frame,
                text="Cancel",
                command=input_dialog.destroy,
                width=80,
                height=34,
                **self.colors["buttons"]["default"],
            ).grid(row=0, column=1, padx=5)
            GButton(
                button_frame,
                text="OK",
                command=on_ok,
                width=80,
                height=34,
                **self.colors["buttons"]["primary"],
            ).grid(row=0, column=2, padx=5)

            self._center_dialog(input_dialog, relative_to=dialog)
            input_dialog.wait_window()
            return result

        # Context menu functions.
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
                # Custom confirmation dialog.
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
                GButton(
                    btn_frame,
                    text="Yes",
                    command=on_yes,
                    width=60,
                    height=30,
                    **self.colors["buttons"]["primary"],
                ).pack(side="right", padx=5)
                GButton(
                    btn_frame,
                    text="No",
                    command=confirm_dialog.destroy,
                    width=60,
                    height=30,
                    **self.colors["buttons"]["default"],
                ).pack(side="right")

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

        # Add commands to context menu.
        context_menu.add_command(label="Insert Rule", command=insert_rule)
        context_menu.add_command(label="Edit Rule", command=edit_rule)
        context_menu.add_command(label="Remove Rule", command=remove_rule)
        context_menu.add_separator()
        context_menu.add_command(label="Select All", command=select_all)
        context_menu.add_command(label="Deselect All", command=deselect_all)

        # Event handlers.
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
            context_menu.tk_popup(event.x_root, event.y_root)

        def hide_context_menu_on_escape(event=None):
            """Hide the context menu when Escape is pressed."""
            context_menu.unpost()

        # Bind events.
        filter_tree.bind("<Button-1>", on_tree_click)
        filter_tree.bind("<Button-3>", show_context_menu)
        dialog.bind("<Escape>", hide_context_menu_on_escape)

        # Initial population.
        populate_tree()

        # Buttons.
        def apply_filters():
            active_rules = [
                item["rule"] for item in temp_filters if item.get("active", True)
            ]
            self._log(f"Applying active filters: {active_rules}")

            # Clear file lists and trees.
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

                # Wait for scanning threads.
                for t in scan_threads:
                    t.join()

                # Run comparison.
                self.root.after(0, self.compare_folders)

            threading.Thread(target=run_scans_and_compare, daemon=True).start()

        def save_and_close():
            self.filter_rules = temp_filters
            self.filter_rules.sort(key=lambda item: item["rule"])
            apply_filters()
            dialog.destroy()

        # Create dialog buttons.
        button_frame = ttk.Frame(dialog)
        button_frame.grid(row=1, column=0, padx=10, pady=(0, 10), sticky=tk.EW)

        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(4, weight=1)

        GButton(
            button_frame,
            text="Save",
            command=save_and_close,
            width=80,
            height=34,
            **self.colors["buttons"]["primary"],
        ).grid(row=0, column=3, padx=5)
        GButton(
            button_frame,
            text="Apply",
            command=apply_filters,
            width=80,
            height=34,
            **self.colors["buttons"]["default"],
        ).grid(row=0, column=2, padx=5)
        GButton(
            button_frame,
            text="Cancel",
            command=dialog.destroy,
            width=80,
            height=34,
            **self.colors["buttons"]["default"],
        ).grid(row=0, column=1, padx=5)

        # Center dialog.
        self._center_dialog(dialog)
        self.root.wait_window(dialog)

    def _show_options_dialog(self):
        """Show the GSynchro options configuration dialog."""
        dialog = tk.Toplevel(self.root)
        dialog.title("GSynchro Options")
        dialog.transient(self.root)
        dialog.grab_set()

        # Center the dialog relative to parent window.
        def center_dialog():
            """Center the dialog after it's fully mapped."""
            dialog.update_idletasks()

            # Get parent window center.
            parent_x = self.root.winfo_rootx() + self.root.winfo_width() // 2
            parent_y = self.root.winfo_rooty() + self.root.winfo_height() // 2

            # Get dialog dimensions (including decorations).
            dialog_width = dialog.winfo_width()
            dialog_height = dialog.winfo_height()

            # Calculate final position to center the dialog.
            dialog_x = parent_x - dialog_width // 2
            dialog_y = parent_y - dialog_height // 2

            dialog.geometry(f"+{dialog_x}+{dialog_y}")

        # Schedule centering after dialog is mapped.
        dialog.after(100, center_dialog)

        # Prevent resizing.
        dialog.resizable(False, False)

        # Create main frame with notebook for tabs.
        main_frame = ttk.Frame(dialog, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)

        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        # Force equal-width tabs using style.
        style = ttk.Style()
        style.configure("TNotebook", tabmargins=[0, 5, 0, 0])
        style.configure("TNotebook.Tab", padding=[60, 5])

        # Filters tab.
        filters_frame = ttk.Frame(notebook, padding="10")
        notebook.add(filters_frame, text="Filters")

        # Create a temporary copy to work with.
        temp_filters = [dict(item) for item in self.filter_rules]

        # Tree view for filters.
        tree_frame, filter_tree = self._create_filter_tree(filters_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 0))

        # Populate tree.
        def populate_tree():
            for item in filter_tree.get_children():
                filter_tree.delete(item)
            for i, item in enumerate(temp_filters):  # noqa: B007
                check_char = (
                    CHECKED_CHAR if item.get("active", True) else UNCHECKED_CHAR
                )
                filter_tree.insert("", "end", iid=i, values=(check_char, item["rule"]))

        def _create_rule_input_dialog(
            title: str,
            prompt_text: str,
            initial_value: str = "",  # type: ignore
        ) -> Optional[str]:
            """Create a dialog to get a filter rule from the user."""
            entry_var = tk.StringVar(value=initial_value)
            result = None

            rule_dialog = tk.Toplevel(dialog)
            rule_dialog.title(title)
            rule_dialog.transient(dialog)
            rule_dialog.grab_set()
            rule_dialog.resizable(False, False)

            # Center the dialog.
            def center_rule_dialog():
                rule_dialog.update_idletasks()
                parent_x = dialog.winfo_rootx() + dialog.winfo_width() // 2
                parent_y = dialog.winfo_rooty() + dialog.winfo_height() // 2
                dialog_width = rule_dialog.winfo_width()
                dialog_height = rule_dialog.winfo_height()
                dialog_x = parent_x - dialog_width // 2
                dialog_y = parent_y - dialog_height // 2
                rule_dialog.geometry(f"+{dialog_x}+{dialog_y}")

            rule_dialog.after(100, center_rule_dialog)

            main_frame = ttk.Frame(rule_dialog, padding="20")
            main_frame.pack()

            ttk.Label(main_frame, text=prompt_text).pack(anchor=tk.W, pady=(0, 5))
            entry = ttk.Entry(main_frame, textvariable=entry_var, width=40)
            entry.pack(pady=(0, 10))
            entry.select_range(0, tk.END)
            entry.focus()

            button_frame = ttk.Frame(main_frame)
            button_frame.pack()

            def on_ok():
                nonlocal result
                result = entry_var.get().strip()
                rule_dialog.destroy()

            def on_cancel():
                rule_dialog.destroy()

            GButton(
                button_frame,
                text="OK",
                command=on_ok,
                width=80,
                height=34,
                **self.colors["buttons"]["primary"],
            ).pack(side=tk.LEFT, padx=5)
            GButton(
                button_frame,
                text="Cancel",
                command=on_cancel,
                width=80,
                height=34,
                **self.colors["buttons"]["default"],
            ).pack(side=tk.LEFT)

            rule_dialog.bind("<Return>", lambda e: on_ok())
            rule_dialog.bind("<Escape>", lambda e: on_cancel())
            entry.focus()

            rule_dialog.wait_window()
            return result

        def insert_rule():
            new_rule = _create_rule_input_dialog(
                "Add Filter Rule", "Enter filter pattern:"
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

        def select_all_rules():
            for item in temp_filters:
                item["active"] = True
            populate_tree()

        def deselect_all_rules():
            for item in temp_filters:
                item["active"] = False
            populate_tree()

        def remove_rules():
            selected_items = filter_tree.selection()  # noqa: B007
            if selected_items:  # noqa: B007
                # Delete in reverse order to preserve indices.
                indices = sorted(
                    [int(item_id) for item_id in selected_items], reverse=True
                )
                for index in indices:
                    del temp_filters[index]
                populate_tree()

        def toggle_rules():
            # This function is also bound to double-click, so it needs to handle
            # both single-item and multi-item selection.
            selected_items = filter_tree.selection()
            if selected_items:
                for item_id in selected_items:
                    index = int(item_id)
                    temp_filters[index]["active"] = not temp_filters[index].get(
                        "active", True
                    )
            populate_tree()

        # Create context menu for filter tree.
        filter_context_menu = tk.Menu(filters_frame, tearoff=0)
        filter_context_menu.add_command(label="Add Rule", command=insert_rule)
        filter_context_menu.add_command(label="Edit Rule", command=edit_rule)
        filter_context_menu.add_command(label="Remove Rule", command=remove_rules)
        filter_context_menu.add_separator()
        filter_context_menu.add_command(label="Toggle Active", command=toggle_rules)
        filter_context_menu.add_separator()
        filter_context_menu.add_command(label="Select All", command=select_all_rules)
        filter_context_menu.add_command(
            label="Deselect All", command=deselect_all_rules
        )

        def show_filter_context_menu(event: tk.Event):
            item_id = filter_tree.identify_row(event.y)
            if item_id:
                filter_tree.selection_set(item_id)
                filter_tree.focus(item_id)
                filter_context_menu.entryconfig("Edit Rule", state="normal")
                filter_context_menu.entryconfig("Remove Rule", state="normal")
                filter_context_menu.entryconfig("Toggle Active", state="normal")
            else:
                # If clicked on empty space, disable edit/remove/toggle for
                # specific items.
                filter_tree.selection_remove(filter_tree.selection())
                filter_tree.focus("")
                filter_context_menu.entryconfig("Edit Rule", state="disabled")
                filter_context_menu.entryconfig("Remove Rule", state="disabled")
                filter_context_menu.entryconfig("Toggle Active", state="disabled")

            # Always enable Add, Select All, Deselect All.
            filter_context_menu.entryconfig("Add Rule", state="normal")
            filter_context_menu.entryconfig("Select All", state="normal")
            filter_context_menu.entryconfig("Deselect All", state="normal")
            filter_context_menu.tk_popup(event.x_root, event.y_root)

        # Bind double-click to toggle.
        filter_tree.bind("<Double-1>", lambda e: toggle_rules())

        # Initialize filter tree.
        populate_tree()

        filter_tree.bind("<Button-3>", show_filter_context_menu)
        # Font tab.
        font_frame = ttk.Frame(notebook, padding="10")
        notebook.add(font_frame, text="Font")

        # Font family.
        ttk.Label(font_frame, text="Font Family:").grid(
            row=0, column=0, sticky=tk.E, padx=(0, 5), pady=5
        )

        # Get available font families.
        font_families = tkfont.families()

        # Filter to monospace fonts (simplified check).
        mono_fonts = sorted(
            set(
                f
                for f in font_families
                if any(
                    mono in f.lower()
                    for mono in ["mono", "consolas", "courier", "fixedsys", "terminal"]
                )
            )
        )
        if not mono_fonts:  # Fallback to all fonts.
            mono_fonts = sorted(set(font_families))

        font_family_var = tk.StringVar(value=self.options["font_family"])
        font_family_combo = ttk.Combobox(
            font_frame, textvariable=font_family_var, values=mono_fonts, width=30
        )
        font_family_combo.grid(row=0, column=1, sticky=tk.W, padx=(0, 10), pady=5)

        # Tree font size (using main font_size).
        ttk.Label(font_frame, text="Font Size:").grid(
            row=1, column=0, sticky=tk.E, padx=(0, 5), pady=5
        )
        font_size_var = tk.IntVar(value=self.options["font_size"])
        font_size_spinbox = tk.Spinbox(
            font_frame, from_=8, to=20, textvariable=font_size_var, width=5
        )
        font_size_spinbox.grid(row=1, column=1, sticky=tk.W, pady=5)

        # Font example.
        ttk.Label(font_frame, text="Example:").grid(
            row=2, column=0, sticky=tk.E, pady=(10, 5), padx=(0, 5)
        )
        font_example_label = ttk.Label(
            font_frame,
            text="ABCDEFGHIJKLMNOPQRSTUVWXYZ\nabcdefghijklmnopqrstuvwxyz\n0123456789\n!@#$%^&*()[]{}_+",
        )
        font_example_label.grid(
            row=3, column=0, columnspan=2, sticky=tk.W, pady=(10, 5)
        )

        # Button frame.
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))

        def apply_options():
            """Apply selected options."""
            # Store old values to check for changes.
            old_font_family = self.options["font_family"]
            old_font_size = self.options["font_size"]
            old_filters = [dict(item) for item in self.filter_rules]

            # Get new values from dialog.
            new_font_family = font_family_var.get()
            new_font_size = font_size_var.get()
            new_filters = temp_filters

            # Determine what has changed.
            font_changed = (  # noqa: B007
                new_font_family != old_font_family or new_font_size != old_font_size
            )
            other_options_changed = new_filters != old_filters

            # Update options dictionary with all new values.
            self.options.update(
                {
                    "font_family": new_font_family,
                    "font_size": new_font_size,
                }
            )
            self.filter_rules = new_filters
            self.filter_rules.sort(key=lambda item: item["rule"])

            # Apply font changes to styles and tags.
            self._update_tree_fonts()

            # Save config and close dialog.
            self._save_config()
            dialog.destroy()

            # Decide whether to do a full refresh or just a font update.
            if other_options_changed:
                self._log("Filters or other options changed, performing full refresh.")
                if self.folder_a.get() and self.folder_b.get():
                    self.compare_folders()
            elif font_changed:
                self._log(
                    "Only font changed, adjusting column widths for new font size."
                )
                self._adjust_tree_column_widths(self.tree_a)
                self._adjust_tree_column_widths(self.tree_b)

        def update_font_example(*args):
            """Update the font example when font family or size changes."""
            font_family = font_family_var.get()
            font_size = font_size_var.get()
            if font_family and font_size:
                font_example_label.configure(font=(font_family, font_size))

        # Bind font changes to update example.
        font_family_var.trace("w", update_font_example)
        font_size_var.trace("w", update_font_example)

        # Initialize font example.
        update_font_example()

        def reset_options():
            """Reset options to default values."""
            font_family_var.set(DEFAULT_FONT_FAMILY)
            font_size_var.set(DEFAULT_FONT_SIZE)

        # Buttons - centered.
        button_center_frame = ttk.Frame(button_frame)
        button_center_frame.pack(expand=True)

        button_row_frame = ttk.Frame(button_center_frame)
        button_row_frame.pack()

        GButton(
            button_row_frame,
            text="Apply",
            command=apply_options,
            width=100,
            height=34,
            **self.colors["buttons"]["primary"],
        ).pack(side=tk.LEFT, padx=5)

        GButton(
            button_row_frame,
            text="Reset",
            command=reset_options,
            width=100,
            height=34,
            **self.colors["buttons"]["secondary"],
        ).pack(side=tk.LEFT, padx=5)

        GButton(
            button_row_frame,
            text="Cancel",
            command=dialog.destroy,
            width=100,
            height=34,
            **self.colors["buttons"]["secondary"],
        ).pack(side=tk.LEFT, padx=5)

    def _update_tree_fonts(self):
        """Update tree fonts based on current options."""
        font_family = self.options["font_family"]
        font_size = self.options["font_size"]

        # Update treeview style and heading font.
        style = ttk.Style()
        style.configure("TTreeview", font=(font_family, font_size))

        style.configure("TTreeview.Heading", font=(font_family, font_size, "bold"))

        # Re-configure the custom_font tag on existing trees to apply the new
        # font This is crucial for making the font change visible without a full
        # refresh.
        if self.tree_a:
            self.tree_a.tag_configure("custom_font", font=(font_family, font_size))
        if self.tree_b:
            self.tree_b.tag_configure("custom_font", font=(font_family, font_size))

        # Save config.
        self._save_config()

    def _refresh_tree_views_after_font_change(self):
        """Refresh tree views after font change - using a different approach."""
        # We need to completely rebuild the trees.
        if self.tree_a and self.folder_a.get():
            # Store current folder.
            folder = self.folder_a.get()
            # Repopulate with current filters.
            self._populate_single_panel(
                "A", folder, active_rules=self._get_active_filters()
            )

        if self.tree_b and self.folder_b.get():
            # Store current folder.
            folder = self.folder_b.get()
            # Repopulate with current filters.
            self._populate_single_panel(
                "B", folder, active_rules=self._get_active_filters()
            )

        # Run comparison again if needed.
        if self.files_a and self.files_b:  # noqa: B007
            self.compare_folders()

    def _create_filter_tree(self, parent: Union[tk.Toplevel, tk.Widget]) -> tuple:
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

        # Store context menu tree reference.
        self._context_menu_tree = tree
        self._context_menu_item_id = item_id

        if item_id:
            # If right-clicking on an item that is not already part of the
            # current selection, clear the old selection and select only the new
            # item.
            if item_id not in tree.selection():
                tree.selection_set(item_id)
            tree.focus(item_id)
        else:  # Click was on empty space.
            tree.selection_remove(tree.selection())
            tree.set("")

        selected_items = tree.selection()

        # Only get item info if we have a single item selected..
        if len(selected_items) == 1:
            item_id = selected_items[0]
            rel_path = self._get_relative_path(tree, item_id)
            if not rel_path:
                return

            # Determine which file dictionary to use.
            files_dict = self.files_a if tree is self.tree_a else self.files_b
            item_info = files_dict.get(rel_path)
        else:
            # Multiple or no items selected - set default states.
            item_info = None
            rel_path = None

        # Enable/disable menu items based on context.
        if item_info and item_info.get("type") == "file":
            self.tree_context_menu.entryconfig("Open...", state="normal")
            self.tree_context_menu.entryconfig("Open Folder", state="normal")
        else:
            self.tree_context_menu.entryconfig("Open...", state="disabled")
            self.tree_context_menu.entryconfig(
                "Open Folder", state="normal" if item_id else "disabled"
            )

        # Show/hide sync options based on the panel and whether an item is
        # selected.
        if item_id:
            if tree is self.tree_a:
                self.tree_context_menu.entryconfig("Sync  ▶", state="normal")
                self.tree_context_menu.entryconfig("◀  Sync", state="disabled")
            elif tree is self.tree_b:
                self.tree_context_menu.entryconfig("Sync  ▶", state="disabled")
                self.tree_context_menu.entryconfig("◀  Sync", state="normal")
            else:
                self.tree_context_menu.entryconfig("Sync  ▶", state="disabled")
                self.tree_context_menu.entryconfig("◀  Sync", state="disabled")
        else:
            # No item selected - disable sync options.
            self.tree_context_menu.entryconfig("Sync  ▶", state="disabled")
            self.tree_context_menu.entryconfig("◀  Sync", state="disabled")

        # Enable/disable Delete menu item based on whether an item is selected.
        if item_id:
            self.tree_context_menu.entryconfig("Delete", state="normal")
        else:
            self.tree_context_menu.entryconfig("Delete", state="disabled")

        # Enable/disable Select All and Deselect All based on whether comparison
        # has been performed.
        if self.sync_states:
            self.tree_context_menu.entryconfig("Select All", state="normal")
            self.tree_context_menu.entryconfig("Deselect All", state="normal")
        else:
            self.tree_context_menu.entryconfig("Select All", state="disabled")
            self.tree_context_menu.entryconfig("Deselect All", state="disabled")

        # Enable/disable "Compare..." based on selections in both trees.
        selected_a = self.tree_a.selection() if self.tree_a else ()
        selected_b = self.tree_b.selection() if self.tree_b else ()

        # Check if tree has children - if not, don't show context menu.
        if not tree.get_children():
            return

        # Post the menu at the cursor's location.
        self.tree_context_menu.tk_popup(event.x_root, event.y_root)  # noqa: B007

        if len(selected_a) == 1 and len(selected_b) == 1:
            self.tree_context_menu.entryconfig("Compare...", state="normal")
        else:
            self.tree_context_menu.entryconfig("Compare...", state="disabled")

    def _on_tree_header_double_click(self, event: tk.Event):
        """Handle double-click on a treeview header to resize the column."""
        widget = event.widget
        if not isinstance(widget, ttk.Treeview):
            return
        tree = cast(ttk.Treeview, widget)

        region = tree.identify("region", event.x, event.y)
        if region != "heading":
            return

        column_id = tree.identify_column(event.x)
        if column_id:
            self._log(f"Adjusting width for column {column_id}")
            self._adjust_single_column_width(tree, column_id)

    def _adjust_single_column_width(self, tree: ttk.Treeview, column_id: str):
        """Adjust the width of a single column to fit its content.

        Args:
            tree: The treeview widget.
            column_id: The identifier of the column to resize (e.g., '#0', 'size').
        """
        if not tree:
            return

        try:
            font_family = self.options["font_family"]
            font_size = self.options["font_size"]
            font = tkfont.Font(family=font_family, size=font_size)

            # Start with the width of the header text.
            max_width = font.measure(tree.heading(column_id, "text"))

            def find_max_width_recursive(item_id=""):
                """Recursively find the maximum content width in the column."""
                nonlocal max_width
                for child_id in tree.get_children(item_id):
                    if column_id == "#0":  # The 'Name' column.
                        cell_value = tree.item(child_id, "text")  # noqa: B007
                    else:
                        cell_value = tree.set(child_id, column_id)

                    if isinstance(cell_value, str):
                        max_width = max(max_width, font.measure(cell_value))

                    find_max_width_recursive(child_id)

            find_max_width_recursive()

            tree.column(column_id, width=max_width + 20)
        except Exception as e:
            self._log(f"Could not adjust column width for {column_id}: {e}")

    # ==========================================================================
    # CONTEXT MENU ACTIONS
    # ==========================================================================

    def _sync_selected_a_to_b(self):
        """Sync the selected item from Panel A to Panel B."""
        if not self.tree_a:
            return
        selected_items = self.tree_a.selection()
        if not selected_items:
            messagebox.showwarning(
                "Sync Error", "Please select one or more items to sync."
            )
            return
        rel_paths = [
            self._get_relative_path(self.tree_a, item) for item in selected_items
        ]
        self._sync_items([p for p in rel_paths if p], "a_to_b")

    def _sync_selected_b_to_a(self):
        """Sync the selected item from Panel B to Panel A."""
        if not self.tree_b:
            return
        selected_items = self.tree_b.selection()
        if not selected_items:
            messagebox.showwarning(
                "Sync Error", "Please select one or more items to sync."
            )
            return
        rel_paths = [
            self._get_relative_path(self.tree_b, item) for item in selected_items
        ]
        self._sync_items([p for p in rel_paths if p], "b_to_a")

    def _sync_single_item(self, rel_path: str, direction: str):
        """Handle the synchronization of a single file or directory.

        Args:
            rel_path: Relative path of the item
            direction: Sync direction ("a_to_b" or "b_to_a")
        """
        self._sync_items([rel_path], direction)

    def _sync_items(self, rel_paths: list[str], direction: str):
        """Handle the synchronization of multiple files or directories.

        Args:
            rel_paths: List of relative paths of the items.
            direction: Sync direction ("a_to_b" or "b_to_a").
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

                files_to_copy = []
                for rel_path in rel_paths:
                    source_item = source_files_dict.get(rel_path)
                    if not source_item:
                        self._log(
                            f"Warning: Source item '{rel_path}' not found, skipping."
                        )
                        continue

                    # Build files_to_copy:.
                    # - if a file was selected, sync that file.
                    # - if a directory was selected, sync all files under it.
                    if source_item.get("type") == "file":
                        files_to_copy.append(rel_path)
                    else:
                        # Directory: include all child files.
                        dir_prefix = rel_path.rstrip(os.sep).replace(os.sep, "/") + "/"
                        for p, info in source_files_dict.items():
                            if info.get("type") != "file":
                                continue
                            if p.replace(os.sep, "/").startswith(dir_prefix):
                                files_to_copy.append(p)

                # Remove duplicates.
                files_to_copy = sorted(list(set(files_to_copy)))
                target_files_dict = (
                    self.files_b if direction == "a_to_b" else self.files_a
                )

                self.root.after(
                    0,
                    self._start_progress,
                    None,
                    len(files_to_copy),
                    f"Syncing {len(files_to_copy)} items...",
                )

                # Correctly handle SSH clients.
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
                            target_files_dict,
                        )

                self._log("Successfully synced items. Refreshing view...")

                self.root.after(0, self.compare_folders)

            except Exception as e:
                self._log(f"Error syncing items: {e}")
                messagebox.showerror("Sync Error", f"Failed to sync items: {e}")
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

        # Determine source and destination data.
        rules = self._get_active_filters()
        if direction == "a_to_b":
            source_files, dest_files = self.files_a, self.files_b
            dest_tree = self.tree_b
        else:
            source_files, dest_files = self.files_b, self.files_a
            dest_tree = self.tree_a

        source_item_info = source_files.get(synced_item_rel_path)
        if not source_item_info:
            self._log(
                f"Could not find source info for {synced_item_rel_path}, performing full refresh."
            )
            self.root.after(0, self.compare_folders)
            return

        # Update the destination file's metadata to match the source.
        dest_files[synced_item_rel_path] = source_item_info.copy()

        # Rebuild and repopulate the destination tree.
        tree_structure = self._build_tree_structure(dest_files)
        if dest_tree:
            self._batch_populate_tree(dest_tree, tree_structure, rules)

        # After sync, the item is no longer selected for sync.
        self.sync_states[synced_item_rel_path] = False

        # Find the item in both trees and update its status.
        self.root.after(0, self.compare_folders)

    def _select_all(self):
        """Select all different/new items."""
        # Use stored context menu tree if available, otherwise fall back to
        # focus.
        tree = getattr(self, "_context_menu_tree", None)
        if not tree:
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

                # Recurse into children.
                if tree.get_children(child_id):
                    traverse_and_select(child_id)

        traverse_and_select()

    def _deselect_all(self):
        """Deselect all items in the tree."""
        # Use stored context menu tree if available, otherwise fall back to
        # focus.
        tree = getattr(self, "_context_menu_tree", None)
        if not tree:
            tree = self.root.focus_get()

        if not isinstance(tree, ttk.Treeview) or tree not in (self.tree_a, self.tree_b):
            return

        def traverse_and_deselect(item_id=""):
            for child_id in tree.get_children(item_id):
                rel_path = self._get_relative_path(tree, child_id)
                if rel_path is not None:
                    # Check if item is in sync_states.
                    if rel_path in self.sync_states:
                        self.sync_states[rel_path] = False
                    current_values = list(tree.item(child_id, "values"))
                    current_values[0] = UNCHECKED_CHAR
                    tree.item(child_id, values=tuple(current_values))

                # Recurse into children.
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

        # Get file paths.
        path_a = self._get_full_path_for_item(self.tree_a, selected_a[0], "A")
        path_b = self._get_full_path_for_item(self.tree_b, selected_b[0], "B")

        if not path_a or not path_b:
            messagebox.showerror(
                "Error", "Could not determine file paths for comparison."
            )
            return

        # Check if items are files.
        rel_path_a = self._get_relative_path(self.tree_a, selected_a[0])
        rel_path_b = self._get_relative_path(self.tree_b, selected_b[0])

        is_file_a = self.files_a.get(rel_path_a, {}).get("type") == "file"
        is_file_b = self.files_b.get(rel_path_b, {}).get("type") == "file"

        if not (is_file_a and is_file_b):
            messagebox.showwarning(
                "Selection Error", "Please select files, not directories, to compare."
            )
            return

        # Launch g_compare.py in a new process.
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
            # This method handles downloading remote files to a temp location.
            local_path = self._get_full_path_for_item(tree, item_id)

            if not local_path:
                self._log("Could not get a local path for the selected item.")
                return

            self._log(f"Opening file: {local_path}")
            if sys.platform == "win32":
                os.startfile(local_path)
            elif sys.platform == "darwin":  # macOS
                subprocess.Popen(["open", local_path])
            else:  # Linux and other Unix-like systems.
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

    def _open_selected_folder(self):
        """Open the folder containing the selected item."""
        tree = self._context_menu_tree
        item_id = self._context_menu_item_id

        if tree is None or item_id is None:
            self._log("No item selected for opening folder via context menu.")
            self._clear_context_menu_state()
            return

        try:
            rel_path = self._get_relative_path(tree, item_id)
            if not rel_path:
                return

            panel = "A" if tree is self.tree_a else "B"
            files_dict = self.files_a if panel == "A" else self.files_b
            item_info = files_dict.get(rel_path)

            if not item_info:
                return

            # Determine the path to open.
            if item_info.get("type") == "dir":
                folder_path = item_info.get("full_path")
            else:
                folder_path = os.path.dirname(item_info.get("full_path", ""))

            if not folder_path:
                self._log(f"Could not determine folder path for {rel_path}")
                return

            self._log(f"Opening folder: {folder_path}")
            if sys.platform == "win32":
                os.startfile(folder_path)
            elif sys.platform == "darwin":  # macOS
                subprocess.Popen(["open", folder_path])
            else:  # Linux and other Unix-like systems.
                process = subprocess.Popen(
                    ["xdg-open", folder_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                stdout, stderr = process.communicate()
                if process.returncode != 0:
                    error_message = stderr.decode().strip()
                    self._log(f"xdg-open error: {error_message}")
                    messagebox.showwarning(
                        "Warning", f"Could not open folder: {error_message}"
                    )
        except Exception as e:
            messagebox.showerror("Error", f"Could not open folder: {e}")
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
                if use_ssh:  # noqa: B007
                    # Remote deletion.
                    with self._create_ssh_for_panel(panel) as ssh_client:
                        if ssh_client is None:
                            raise ConnectionError(
                                f"Could not connect to Panel {panel} for deletion."
                            )
                        is_dir = False  # noqa: B007
                        if item_info:
                            is_dir = item_info.get("type") == "dir"
                        else:
                            # Fallback: check remote system.
                            stdin, stdout, stderr = ssh_client.exec_command(
                                f"if [ -d {_posix_quote(full_path)} ]; then echo 'dir'; fi"
                            )
                            if stdout.read().decode().strip() == "dir":
                                is_dir = True
                        command = (
                            f"rm -rf {_posix_quote(full_path)}"
                            if is_dir
                            else f"rm {_posix_quote(full_path)}"
                        )
                        stdin, stdout, stderr = ssh_client.exec_command(command)
                        error = stderr.read().decode()
                        if error:
                            raise Exception(error)
                else:
                    # Local deletion.
                    is_dir = False  # noqa: B007
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
        # Hide context menu if it's visible.
        self._clear_context_menu_state()
        self.tree_context_menu.unpost()

        widget = self.root.focus_get()
        if isinstance(widget, ttk.Treeview) and widget in (self.tree_a, self.tree_b):
            # Get current selection.
            selection = widget.selection()
            # Deselect all items in the tree if there's a selection.
            if selection:
                widget.selection_remove(selection)
            # Move focus away from the tree to the root window.
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

    def _is_temporary_path(self, path: str) -> bool:
        """Check if a path is a temporary file or directory.

        Args:
            path: Path to check

        Returns:
            True if path appears to be temporary
        """
        if not path:
            return False

        # Check for common temporary directory patterns.
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

        # Check for tempfile.NamedTemporaryFile patterns.
        if "tmp" in path_normalized and (
            path_normalized.startswith("/tmp/")
            or path_normalized.startswith("\\tmp\\")
            or "tmp" in os.path.basename(path_normalized)
        ):
            return True

        return False

    def _update_panel_history(
        self, panel_name: str, folder_var: tk.StringVar, new_path: str
    ):
        """Update and save panel history.

        Args:
            panel_name: Panel identifier ("A" or "B")
            folder_var: StringVar for the folder path
            new_path: New path to add to history
        """
        if not new_path or self._is_temporary_path(new_path):
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
            self._log(f"Downloading remote file: {full_path}")  # noqa: B007

            # Determine connection details based on the panel.
            if panel == "A":
                host, user, password, port = (
                    self.remote_host_a.get(),
                    self.remote_user_a.get(),
                    self.remote_pass_a.get(),
                    int(self.remote_port_a.get()),
                )
            else:  # Panel B is the only other option.
                host, user, password, port = (
                    self.remote_host_b.get(),
                    self.remote_user_b.get(),
                    self.remote_pass_b.get(),
                    int(self.remote_port_b.get()),
                )

            try:
                with self.connection_manager.get_connection(
                    host, user, password, port
                ) as ssh_client:
                    transport = ssh_client.get_transport() if ssh_client else None
                    if not transport or not transport.is_active():
                        raise ConnectionError(
                            "SSH client or transport is not available."
                        )

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
            # Ensure we measure with the same font.
            font_family = self.options["font_family"]
            font_size = self.options["font_size"]
            font = tkfont.Font(family=font_family, size=font_size)

            # Create a dictionary to hold the max width for each column.
            col_widths = {
                col: font.measure(tree.heading(col, "text"))
                for col in list(tree["columns"]) + ["#0"]
            }

            def find_max_widths_recursive(item_id=""):
                """Recursively traverse the tree to find the max width for each column."""
                for child_id in tree.get_children(item_id):
                    # Check the 'Name' column (#0).
                    text = tree.item(child_id, "text")
                    col_widths["#0"] = max(col_widths["#0"], font.measure(text))

                    # Check other data columns.
                    for col in tree["columns"]:
                        cell_value = tree.set(child_id, col)
                        if isinstance(cell_value, str):
                            col_widths[col] = max(
                                col_widths[col], font.measure(cell_value)
                            )

                    # Recurse.
                    find_max_widths_recursive(child_id)

            find_max_widths_recursive()

            # Apply the calculated widths with some padding.
            for col, width in col_widths.items():
                tree.column(col, width=width + 20, minwidth=40)

        except Exception as e:
            self._log(
                f"Could not adjust column widths due to potential race condition: {e}"
            )

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

        # Determine which status variable to update.
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
        with self._progress_lock:
            self.progress_bar.step(step)

    def _stop_progress(self):
        """Hide the progress bar."""
        self.progress_bar.stop()
        self.progress_bar.grid_remove()
        if self.status_label_a:
            self.status_label_a.grid()

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
