#!/usr/bin/env python3
"""
GSynchro - GUI File Synchronization Tool

Graphical application for comparing and synchronizing files between local and
remote folders. Supports SSH-based remote operations with visual comparison.

 Author: Gino Bogo
License: MIT
Version: 1.0 (initial stable version)
Version: 1.1 (robust remote scanner)
Version: 1.2 (display scaling support)
"""

from __future__ import annotations

# Standard library imports.
import atexit
import hashlib
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

# Personal library imports.
from libs.g_button import GButton
from libs.g_scaling import GScaling
from libs.g_theme import get_theme_colors

# Third-party library imports.
import paramiko
from scp import SCPClient

# ============================================================================
# CONSTANTS
# ============================================================================
APP_VERSION = "1.2"
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
    """Return a POSIX-shell-quoted version of `path` for safe exec_command use."""
    return shlex.quote(path)


def _posix_join(*parts: str) -> str:
    """Join path components using POSIX semantics for remote path construction."""
    return posixpath.join(*parts)


# ============================================================================
# GENERIC HELPER: modal string input dialog
# ============================================================================
def _ask_string_dialog(
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

    dialog = tk.Toplevel(parent)
    dialog.transient(parent)
    dialog.grab_set()
    dialog.title(title)
    dialog.minsize(300, 120)
    dialog.maxsize(300, 120)

    style = ttk.Style()
    dialog_bg = style.lookup("TFrame", "background")
    dialog.configure(bg=dialog_bg)

    dialog.rowconfigure(0, weight=1)
    dialog.columnconfigure(0, weight=1)

    content_frame = ttk.Frame(dialog, padding=10)
    content_frame.grid(row=0, column=0, sticky=tk.NSEW)
    content_frame.columnconfigure(0, weight=1)

    ttk.Label(content_frame, text=prompt).grid(
        row=0, column=0, sticky=tk.W, pady=(0, 5)
    )

    entry_var = tk.StringVar(value=initial)
    entry = ttk.Entry(content_frame, textvariable=entry_var)
    entry.grid(row=1, column=0, sticky=tk.EW)
    entry.focus_set()
    entry.select_range(0, "end")
    entry.bind("<Return>", lambda e: on_ok())

    button_frame = ttk.Frame(dialog, padding=(10, 0, 10, 10))
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
    ).grid(row=0, column=1, padx=5)

    GButton(
        button_frame,
        text="OK",
        command=on_ok,
        width=80,
        height=34,
        **colors["buttons"]["primary"],
    ).grid(row=0, column=2, padx=5)

    parent.update_idletasks()
    dialog.update_idletasks()
    x = parent.winfo_rootx() + parent.winfo_width() // 2 - dialog.winfo_width() // 2
    y = parent.winfo_rooty() + parent.winfo_height() // 2 - dialog.winfo_height() // 2
    dialog.geometry(f"+{x}+{y}")
    dialog.wait_window()

    return result


# ============================================================================
# CONNECTION MANAGER CLASS
# ============================================================================
class ConnectionManager:
    """Manages SSH connections with pooling."""

    def __init__(self, logger_func, pool_size=4):
        """Initialize connection manager with logger and pool size."""
        self._pools = {}
        self._pool_configs = {}
        self._lock = threading.Lock()
        self.log = logger_func
        self.pool_size = pool_size

    def _get_server_key(self, host, user, port):
        """Generate unique server key for connection pooling."""
        return f"{user}@{host}:{port}"

    def _create_connection(self, host, user, password, port):
        """Create a new SSH connection to the specified server."""
        self.log(f"Creating new SSH connection for {user}@{host}:{port}")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(host, username=user, password=password, port=port)
        return client

    def _initialize_pool(self, server_key, host, user, password, port):
        """Initialize connection pool for a server with multiple connections."""
        if server_key not in self._pools:
            self._pools[server_key] = Queue()
            self._pool_configs[server_key] = (host, user, password, port)
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
        """Context manager to get a connection from the pool, creating if needed."""
        server_key = self._get_server_key(host, user, port)
        with self._lock:
            if server_key not in self._pools:
                self._initialize_pool(server_key, host, user, password, port)

            conn = None
            try:
                conn = self._pools[server_key].get(timeout=10)
                transport = conn.get_transport() if conn else None
                if not transport or not transport.is_active():
                    self.log(f"Connection for {server_key} is dead, creating new one")
                    conn = self._create_connection(host, user, password, port)
                yield conn
            except Exception as e:
                self.log(f"Error getting connection for {server_key}: {e}")
                conn = self._create_connection(host, user, password, port)
                yield conn
            finally:
                if conn and server_key in self._pools:
                    try:
                        transport = conn.get_transport()
                        if transport and transport.is_active():
                            self._pools[server_key].put(conn, timeout=1)
                        else:
                            conn.close()
                            host, user, password, port = self._pool_configs[server_key]
                            new_conn = self._create_connection(
                                host, user, password, port
                            )
                            self._pools[server_key].put(new_conn, timeout=1)
                    except Exception:
                        try:
                            conn.close()
                        except Exception:
                            pass

    def get_pool_status(self):
        """Return status of all connection pools (server key -> available connections)."""
        status = {}
        with self._lock:
            for server_key, pool in self._pools.items():
                status[server_key] = pool.qsize()
        return status

    def close_all(self):
        """Close all SSH connections in all pools."""
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
# COMPARER CLASS
# ============================================================================
class Comparer:
    """Handles the logic for comparing file and directory structures."""

    def __init__(
        self, logger_func, connection_manager, root_widget, options, state_lock
    ):
        """Initialize Comparer with logger, connection manager, and UI components."""
        self.log = logger_func
        self.connection_manager = connection_manager
        self.root = root_widget
        self.options = options
        self.state_lock = state_lock

    def _compare_files(
        self,
        file_a: Optional[dict],
        file_b: Optional[dict],
        use_ssh_a: bool,
        use_ssh_b: bool,
        ssh_client_a: Optional[paramiko.SSHClient],
        ssh_client_b: Optional[paramiko.SSHClient],
    ) -> tuple:
        """Compare two files and return status and color tuple."""
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
                with self.state_lock:
                    compare_method = self.options.get("compare_method", "block")
                    if compare_method == "md5":
                        try:
                            hash_a = self._get_md5_hash(file_a, use_ssh_a, ssh_client_a)
                            hash_b = self._get_md5_hash(file_b, use_ssh_b, ssh_client_b)
                            if hash_a != hash_b:
                                return "Different", "orange"
                        except Exception as e:
                            self.log(f"Error during MD5 comparison: {e}")
                            return "Different", "orange"
                    else:
                        try:
                            with (
                                self._open_file_handle(
                                    file_a, use_ssh_a, ssh_client_a
                                ) as file_a_handle,
                                self._open_file_handle(
                                    file_b, use_ssh_b, ssh_client_b
                                ) as file_b_handle,
                            ):
                                if not self._are_chunks_identical(
                                    file_a_handle, file_b_handle
                                ):
                                    return "Different", "orange"
                        except Exception as e:
                            self.log(f"Error during block file comparison: {e}")
                            return "Different", "orange"
                return "Identical", "green"
            else:
                return "Different", "orange"
        elif file_a:
            return "Only in A", "blue"
        else:
            return "Only in B", "red"

    def _get_md5_hash(
        self,
        file_info: dict,
        use_ssh: bool,
        ssh_client: Optional[paramiko.SSHClient],
    ) -> str:
        """Calculate MD5 hash of a file, locally or via SSH."""
        if use_ssh:
            if not ssh_client:
                raise ConnectionError("SSH client not connected for MD5 calculation.")
            for cmd_template in ["md5sum {}", "md5 -q {}"]:
                command = cmd_template.format(_posix_quote(file_info["full_path"]))
                stdin, stdout, stderr = ssh_client.exec_command(command)
                exit_status = stdout.channel.recv_exit_status()
                if exit_status == 0:
                    output = stdout.read().decode().strip()
                    return output.split()[0]
            error_msg = f"Could not execute md5sum or md5 on remote host for {file_info['full_path']}"
            self.log(error_msg)
            raise IOError(error_msg)
        else:
            hasher = hashlib.md5()
            try:
                with open(file_info["full_path"], "rb") as f:
                    while chunk := f.read(CHUNK_SIZE):
                        hasher.update(chunk)
                return hasher.hexdigest()
            except FileNotFoundError:
                self.log(
                    f"File not found for MD5 calculation: {file_info['full_path']}"
                )
                raise

    @contextmanager
    def _open_file_handle(
        self,
        file_info: dict,
        use_ssh: bool,
        ssh_client: Optional[paramiko.SSHClient],
    ) -> Iterator:
        """Context manager to open file handle for reading, local or via SSH."""
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
        """Compare two file handles chunk by chunk to determine if files are identical."""
        while True:
            chunk_a = file_a_handle.read(CHUNK_SIZE)
            chunk_b = file_b_handle.read(CHUNK_SIZE)
            if chunk_a != chunk_b:
                return False
            if not chunk_a:
                return True


# ============================================================================
# OPTIONS DIALOG CLASS
# ============================================================================
class OptionsDialog(tk.Toplevel):
    """Dialog for configuring application options."""

    def __init__(self, parent, app):
        """Initialize options dialog with parent window and app reference."""
        super().__init__(parent)
        self.app = app
        self.colors = app.colors
        self.title("GSynchro Options")
        self.transient(parent)
        self.grab_set()
        self.after(100, self._center_dialog)
        self.resizable(False, False)
        self._init_ui()

    def _center_dialog(self):
        """Center the dialog window relative to its parent."""
        self.update_idletasks()
        parent = self.master
        parent_x = parent.winfo_rootx() + parent.winfo_width() // 2
        parent_y = parent.winfo_rooty() + parent.winfo_height() // 2
        dialog_width = self.winfo_width()
        dialog_height = self.winfo_height()
        dialog_x = parent_x - dialog_width // 2
        dialog_y = parent_y - dialog_height // 2
        self.geometry(f"+{dialog_x}+{dialog_y}")

    def _init_ui(self):
        """Initialize the UI components of the options dialog."""
        main_frame = ttk.Frame(self, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        style = ttk.Style()
        style.configure("TNotebook", tabmargins=[0, 5, 0, 0])

        # Scale tab padding based on display DPI
        scale_factor = GScaling.get_scale_factor(self)
        tab_padding = [int(60 * scale_factor), int(5 * scale_factor)]
        style.configure("TNotebook.Tab", padding=tab_padding)

        filters_frame = ttk.Frame(notebook, padding="10")
        notebook.add(filters_frame, text="Filters")
        self.temp_filters = [dict(item) for item in self.app.filter_rules]
        tree_frame, self.filter_tree = self.app._create_filter_tree(filters_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 0))
        self.filter_tree.bind("<Double-1>", lambda e: self._toggle_rules())
        self.filter_tree.bind("<Button-3>", self._show_filter_context_menu)
        self._populate_tree()

        compare_frame = ttk.Frame(notebook, padding="10")
        notebook.add(compare_frame, text="Compare")
        self.show_diff_only_var = tk.BooleanVar(
            value=self.app.options.get("show_diff_only", False)
        )
        ttk.Checkbutton(
            compare_frame,
            text=" Show difference only ",
            variable=self.show_diff_only_var,
        ).grid(row=0, column=0, sticky=tk.W, pady=5)

        compare_method_frame = ttk.LabelFrame(
            compare_frame, text="File Compare Method", padding="10"
        )
        compare_method_frame.grid(
            row=1, column=0, columnspan=2, sticky=tk.EW, pady=(10, 5)
        )
        self.compare_method_var = tk.StringVar(
            value=self.app.options.get("compare_method", "block")
        )
        ttk.Radiobutton(
            compare_method_frame,
            text=" Block compare ",
            variable=self.compare_method_var,
            value="block",
        ).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(
            compare_method_frame,
            text=" MD5 compare ",
            variable=self.compare_method_var,
            value="md5",
        ).pack(side=tk.LEFT, padx=5)

        font_frame = ttk.Frame(notebook, padding="10")
        notebook.add(font_frame, text="Font")
        ttk.Label(font_frame, text="Font Family:").grid(
            row=0, column=0, sticky=tk.E, padx=(0, 5), pady=5
        )
        font_families = tkfont.families()
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
        if not mono_fonts:
            mono_fonts = sorted(set(font_families))
        self.font_family_var = tk.StringVar(value=self.app.options["font_family"])
        font_family_combo = ttk.Combobox(
            font_frame, textvariable=self.font_family_var, values=mono_fonts, width=30
        )
        font_family_combo.grid(row=0, column=1, sticky=tk.W, padx=(0, 10), pady=5)

        ttk.Label(font_frame, text="Font Size:").grid(
            row=1, column=0, sticky=tk.E, padx=(0, 5), pady=5
        )
        self.font_size_var = tk.IntVar(value=self.app.options["font_size"])
        font_size_spinbox = tk.Spinbox(
            font_frame, from_=8, to=20, textvariable=self.font_size_var, width=5
        )
        font_size_spinbox.grid(row=1, column=1, sticky=tk.W, pady=5)

        ttk.Label(font_frame, text="Example:").grid(
            row=2, column=0, sticky=tk.E, pady=(10, 5), padx=(0, 5)
        )
        self.font_example_label = ttk.Label(
            font_frame,
            text="ABCDEFGHIJKLMNOPQRSTUVWXYZ\nabcdefghijklmnopqrstuvwxyz\n0123456789\n!@#$%^&*()[]{}_+",
        )
        self.font_example_label.grid(
            row=3, column=0, columnspan=2, sticky=tk.W, pady=(10, 5)
        )
        self.font_family_var.trace_add("write", self._update_font_example)
        self.font_size_var.trace_add("write", self._update_font_example)
        self._update_font_example()

        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))
        button_center_frame = ttk.Frame(button_frame)
        button_center_frame.pack(expand=True)
        button_row_frame = ttk.Frame(button_center_frame)
        button_row_frame.pack()
        GButton(
            button_row_frame,
            text="Apply",
            command=self._apply_options,
            width=100,
            height=34,
            **self.colors["buttons"]["primary"],
        ).pack(side=tk.LEFT, padx=5)
        GButton(
            button_row_frame,
            text="Reset",
            command=self._reset_options,
            width=100,
            height=34,
            **self.colors["buttons"]["secondary"],
        ).pack(side=tk.LEFT, padx=5)
        GButton(
            button_row_frame,
            text="Cancel",
            command=self.destroy,
            width=100,
            height=34,
            **self.colors["buttons"]["secondary"],
        ).pack(side=tk.LEFT, padx=5)

    def _populate_tree(self):
        """Populate the filter tree with current filter rules."""
        for item in self.filter_tree.get_children():
            self.filter_tree.delete(item)
        for i, item in enumerate(self.temp_filters):
            check_char = CHECKED_CHAR if item.get("active", True) else UNCHECKED_CHAR
            self.filter_tree.insert("", "end", iid=i, values=(check_char, item["rule"]))

    def _update_font_example(self, *args):
        """Update the font example label to show current font selection."""
        font_family = self.font_family_var.get()
        font_size = self.font_size_var.get()
        if font_family and font_size:
            self.font_example_label.configure(font=(font_family, font_size))

    def _apply_options(self):
        """Apply selected options to the application and save config."""
        with self.app.state_lock:
            old_font_family = self.app.options["font_family"]
            old_font_size = self.app.options["font_size"]
            old_filters = [dict(item) for item in self.app.filter_rules]
            old_show_diff_only = self.app.options.get("show_diff_only", False)
            old_compare_method = self.app.options.get("compare_method", "block")

        new_font_family = self.font_family_var.get()
        new_font_size = self.font_size_var.get()
        new_filters = self.temp_filters
        new_show_diff_only = self.show_diff_only_var.get()
        new_compare_method = self.compare_method_var.get()

        font_changed = (
            new_font_family != old_font_family or new_font_size != old_font_size
        )
        other_options_changed = (
            new_filters != old_filters
            or new_show_diff_only != old_show_diff_only
            or new_compare_method != old_compare_method
        )

        with self.app.state_lock:
            self.app.options.update(
                {
                    "font_family": new_font_family,
                    "font_size": new_font_size,
                    "show_diff_only": new_show_diff_only,
                    "compare_method": new_compare_method,
                }
            )
            self.app.filter_rules = new_filters
            self.app.filter_rules.sort(key=lambda item: item["rule"])

        self.app._update_tree_fonts()
        self.app._save_config()
        self.destroy()

        if other_options_changed:
            if self.app.folder_a.get() and self.app.folder_b.get():
                self.app.compare_folders()
        elif font_changed:
            pass

    def _reset_options(self):
        """Reset all options to their default values."""
        self.font_family_var.set(DEFAULT_FONT_FAMILY)
        self.font_size_var.set(DEFAULT_FONT_SIZE)
        self.show_diff_only_var.set(False)
        self.compare_method_var.set("block")

    def _toggle_rules(self):
        """Toggle active state of selected filter rules."""
        selected_items = self.filter_tree.selection()
        if selected_items:
            for item_id in selected_items:
                index = int(item_id)
                self.temp_filters[index]["active"] = not self.temp_filters[index].get(
                    "active", True
                )
            self._populate_tree()

    def _show_filter_context_menu(self, event):
        """Show context menu for filter tree operations."""

        def insert_rule():
            """Insert a new filter rule into the list."""
            new_rule = _ask_string_dialog(
                self, "Insert Rule", "Enter new filter pattern:", colors=self.colors
            )
            if new_rule and new_rule.strip():
                self.temp_filters.append({"rule": new_rule.strip(), "active": True})
                self.temp_filters.sort(key=lambda item: item["rule"])
                self._populate_tree()

        def edit_rule():
            """Edit the selected filter rule."""
            selected_item = self.filter_tree.focus()
            if not selected_item:
                return
            index = int(selected_item)
            current_rule = self.temp_filters[index]["rule"]
            edited_rule = _ask_string_dialog(
                self,
                "Edit Rule",
                "Edit filter pattern:",
                initial=current_rule,
                colors=self.colors,
            )
            if edited_rule and edited_rule.strip():
                self.temp_filters[index]["rule"] = edited_rule.strip()
                self.temp_filters.sort(key=lambda item: item["rule"])
                self._populate_tree()

        def remove_rule():
            """Remove the selected filter rule after confirmation."""
            selected_item = self.filter_tree.focus()
            if not selected_item:
                return
            confirm_dialog = tk.Toplevel(self)
            confirm_dialog.transient(self)
            confirm_dialog.grab_set()
            confirm_dialog.title("Confirm Deletion")
            style = ttk.Style()
            dialog_bg = style.lookup("TFrame", "background")
            confirm_dialog.configure(bg=dialog_bg)
            ttk.Label(
                confirm_dialog,
                text="Are you sure you want to remove the selected rule?",
                padding=20,
            ).pack()
            confirmed = False

            def on_yes():
                """Handle yes button in confirmation dialog."""
                nonlocal confirmed
                confirmed = True
                confirm_dialog.destroy()

            btn_frame = ttk.Frame(confirm_dialog, padding=10)
            btn_frame.pack(fill="x")
            GButton(
                btn_frame,
                text="Yes",
                command=on_yes,
                width=70,
                height=30,
                **self.colors["buttons"]["primary"],
            ).pack(side="right", padx=5)
            GButton(
                btn_frame,
                text="No",
                command=confirm_dialog.destroy,
                width=70,
                height=30,
                **self.colors["buttons"]["secondary"],
            ).pack(side="right")
            self.update_idletasks()
            confirm_dialog.update_idletasks()
            x = (
                self.winfo_rootx()
                + self.winfo_width() // 2
                - confirm_dialog.winfo_width() // 2
            )
            y = (
                self.winfo_rooty()
                + self.winfo_height() // 2
                - confirm_dialog.winfo_height() // 2
            )
            confirm_dialog.geometry(f"+{x}+{y}")
            confirm_dialog.wait_window()
            if confirmed:
                index = int(selected_item)
                del self.temp_filters[index]
                self._populate_tree()

        def select_all():
            """Mark all filter rules as active."""
            for item in self.temp_filters:
                item["active"] = True
            self._populate_tree()

        def deselect_all():
            """Mark all filter rules as inactive."""
            for item in self.temp_filters:
                item["active"] = False
            self._populate_tree()

        context_menu = tk.Menu(self, tearoff=0)
        item_id = self.filter_tree.identify_row(event.y)
        if item_id:
            self.filter_tree.selection_set(item_id)
            self.filter_tree.focus(item_id)
            context_menu.add_command(label="Insert Rule", command=insert_rule)
            context_menu.add_command(label="Edit Rule", command=edit_rule)
            context_menu.add_command(label="Remove Rule", command=remove_rule)
        else:
            context_menu.add_command(label="Insert Rule", command=insert_rule)
            context_menu.add_command(label="Edit Rule", state="disabled")
            context_menu.add_command(label="Remove Rule", state="disabled")
        context_menu.add_separator()
        context_menu.add_command(label="Select All", command=select_all)
        context_menu.add_command(label="Deselect All", command=deselect_all)
        context_menu.tk_popup(event.x_root, event.y_root)


# ============================================================================
# MAIN APPLICATION CLASS
# ============================================================================
class GSynchro:
    """Main application class for GSynchro file synchronization tool."""

    def __init__(self, root: tk.Tk):
        """Initialize the GSynchro application with root window."""
        self.root = root
        self.state_lock = threading.RLock()
        self.connection_manager = ConnectionManager(self._log, pool_size=4)

        self.folder_a = tk.StringVar()
        self.folder_b = tk.StringVar()
        self.folder_a_history = []
        self.folder_b_history = []

        self.remote_mode_a = tk.BooleanVar(value=False)
        self.remote_mode_b = tk.BooleanVar(value=False)

        self.tree_a: Optional[ttk.Treeview] = None
        self.tree_b: Optional[ttk.Treeview] = None

        self.files_a = {}
        self.files_b = {}
        self.filter_rules = []
        self.temp_files_to_clean = []

        self.options = {
            "font_family": DEFAULT_FONT_FAMILY,
            "font_size": DEFAULT_FONT_SIZE,
            "show_diff_only": False,
            "compare_method": "block",
        }

        self.comparer = Comparer(
            self._log, self.connection_manager, self.root, self.options, self.state_lock
        )

        self.remote_host_a = tk.StringVar()
        self.remote_user_a = tk.StringVar()
        self.remote_pass_a = tk.StringVar()
        self.remote_port_a = tk.StringVar(value="22")
        self.remote_host_b = tk.StringVar()
        self.remote_user_b = tk.StringVar()
        self.remote_pass_b = tk.StringVar()
        self.remote_port_b = tk.StringVar(value="22")

        default_widths = {
            "#0": 200,
            "sync": 50,
            "size": 80,
            "modified": 120,
            "status": 100,
        }
        self.column_widths_a = default_widths.copy()
        self.column_widths_b = default_widths.copy()

        self.hosts_a = []
        self.hosts_b = []

        self.sync_states = {}

        self._context_menu_tree: Optional[ttk.Treeview] = None
        self._context_menu_item_id: Optional[str] = None
        self.status_a = tk.StringVar()
        self.status_b = tk.StringVar()

        self._progress_lock = threading.Lock()

        self.ssh_entries_a = []
        self.ssh_entries_b = []
        self.test_btn_a = None
        self.test_btn_b = None

        self.colors = get_theme_colors()
        self._load_config()
        self._init_window()
        self._setup_ui()

        self.root.bind("<Escape>", self._on_escape_key)
        atexit.register(self._cleanup_temp_files)

    def _init_window(self):
        """Initialize main window properties and event handlers."""
        self.root.title("GSynchro - Synchronization Tool {}".format(APP_VERSION))
        self.root.minsize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _load_config(self):
        """Load configuration from config file if it exists."""
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
            if "WINDOW" in config and "geometry" in config["WINDOW"]:
                self.root.geometry(config["WINDOW"]["geometry"])
            if "FILTERS" in config and "rules" in config["FILTERS"]:
                self._load_filter_rules(config["FILTERS"]["rules"])
            if "OPTIONS" in config:
                self.options.update(config["OPTIONS"])
            if "COLUMN_WIDTHS_A" in config:
                widths_a = config["COLUMN_WIDTHS_A"]
                for col, width in widths_a.items():
                    self.column_widths_a[col] = int(width)
            if "COLUMN_WIDTHS_B" in config:
                widths_b = config["COLUMN_WIDTHS_B"]
                for col, width in widths_b.items():
                    self.column_widths_b[col] = int(width)
            if "FOLDER_A_HISTORY" in config:
                raw = config["FOLDER_A_HISTORY"]
                self.folder_a_history = self._normalize_history(raw)
                if self.folder_a_history:
                    first = self.folder_a_history[0]
                    self.folder_a.set(first["path"])
                    self._restore_ssh_config("A", first)
            if "FOLDER_B_HISTORY" in config:
                raw = config["FOLDER_B_HISTORY"]
                self.folder_b_history = self._normalize_history(raw)
                if self.folder_b_history:
                    first = self.folder_b_history[0]
                    self.folder_b.set(first["path"])
                    self._restore_ssh_config("B", first)
            if "HOSTS_A" in config:
                self.hosts_a = config["HOSTS_A"]
            if "HOSTS_B" in config:
                self.hosts_b = config["HOSTS_B"]
        except json.JSONDecodeError:
            self._log(f"Warning: Could not parse {CONFIG_FILE}. Using defaults.")

    def _normalize_history(self, raw_list: list) -> list:
        """Normalize history entries to consistent dictionary format."""
        normalized = []
        for entry in raw_list:
            if isinstance(entry, str):
                normalized.append(
                    {"path": entry, "host": "", "port": "", "username": ""}
                )
            elif isinstance(entry, dict):
                entry.setdefault("host", "")
                entry.setdefault("port", "")
                entry.setdefault("username", "")
                normalized.append(entry)
            else:
                self._log(f"Warning: Invalid history entry: {entry}. Skipping.")
        return normalized

    def _restore_ssh_config(self, panel: str, entry: dict):
        """Restore SSH configuration from history entry for specified panel."""
        host = entry.get("host", "")
        port = entry.get("port", "22")
        username = entry.get("username", "")
        if panel == "A":
            self.remote_host_a.set(host)
            self.remote_port_a.set(port)
            self.remote_user_a.set(username)
            self.remote_pass_a.set("")
            self.remote_mode_a.set(bool(host and username))
        else:
            self.remote_host_b.set(host)
            self.remote_port_b.set(port)
            self.remote_user_b.set(username)
            self.remote_pass_b.set("")
            self.remote_mode_b.set(bool(host and username))
        self._on_remote_toggle(panel)

    def _load_filter_rules(self, rules_data):
        """Load and validate filter rules from configuration data."""
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
        """Save current application configuration to config file."""
        current_folder_a = self.folder_a.get()
        if current_folder_a:
            entry_a = self._build_history_entry("A")
            self._add_to_history("A", entry_a)
        current_folder_b = self.folder_b.get()
        if current_folder_b:
            entry_b = self._build_history_entry("B")
            self._add_to_history("B", entry_b)

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

        if self.tree_a:
            try:
                for col in list(self.tree_a["columns"]) + ["#0"]:
                    width = self.tree_a.column(col, "width")
                    if width > 0:
                        self.column_widths_a[col] = width
            except Exception as e:
                self._log(f"Error capturing column widths from Panel A: {e}")
        if self.tree_b:
            try:
                for col in list(self.tree_b["columns"]) + ["#0"]:
                    width = self.tree_b.column(col, "width")
                    if width > 0:
                        self.column_widths_b[col] = width
            except Exception as e:
                self._log(f"Error capturing column widths from Panel B: {e}")

        config = {
            "WINDOW": {"geometry": self.root.geometry()},
            "HOSTS_A": self.hosts_a,
            "HOSTS_B": self.hosts_b,
            "FILTERS": {"rules": self.filter_rules},
            "OPTIONS": self.options,
            "COLUMN_WIDTHS_A": self.column_widths_a,
            "COLUMN_WIDTHS_B": self.column_widths_b,
            "FOLDER_A_HISTORY": self.folder_a_history,
            "FOLDER_B_HISTORY": self.folder_b_history,
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)

    def _build_history_entry(self, panel: str) -> dict:
        """Build a history entry dictionary for the specified panel."""
        if panel == "A":
            host = self.remote_host_a.get()
            port = self.remote_port_a.get() if self.remote_mode_a.get() else ""
            username = self.remote_user_a.get() if self.remote_mode_a.get() else ""
            path = self.folder_a.get()
        else:
            host = self.remote_host_b.get()
            port = self.remote_port_b.get() if self.remote_mode_b.get() else ""
            username = self.remote_user_b.get() if self.remote_mode_b.get() else ""
            path = self.folder_b.get()
        return {"path": path, "host": host, "port": port, "username": username}

    def _add_to_history(self, panel: str, entry: dict):
        """Add entry to panel history, removing duplicates and limiting size."""
        history = self.folder_a_history if panel == "A" else self.folder_b_history
        path = entry["path"]
        history[:] = [h for h in history if h["path"] != path]
        history.insert(0, entry)
        if len(history) > HISTORY_LENGTH:
            del history[HISTORY_LENGTH:]

    def _setup_ui(self):
        """Set up the main user interface components."""
        style = ttk.Style()
        style.configure(
            "flat.Horizontal.TProgressbar",
            troughcolor=self.colors["progress"]["trough"],
            background=self.colors["progress"]["background"],
            borderwidth=0,
            relief="flat",
        )
        style.configure(
            "TTreeview.Heading",
            font=(self.options["font_family"], self.options["font_size"], "bold"),
        )
        style.configure(
            "TTreeview", font=(self.options["font_family"], self.options["font_size"])
        )
        style.map("TTreeview")

        main_frame = self._create_main_frame()
        control_frame = self._create_control_frame(main_frame)
        panels_frame = self._create_panels_frame(main_frame)

        self._create_control_buttons(control_frame)
        self._create_panels(panels_frame)
        self._create_status_bar(main_frame)
        self._create_tree_context_menu()

        self.status_a.set("by Gino Bogo")

    def _create_main_frame(self) -> ttk.Frame:
        """Create and configure the main application frame."""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=tk.NSEW)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(1, weight=1)
        return main_frame

    def _create_control_frame(self, main_frame: ttk.Frame) -> ttk.Frame:
        """Create the control frame for buttons and inputs."""
        control_frame = ttk.Frame(main_frame)
        control_frame.grid(row=0, column=0, columnspan=3, sticky=tk.EW, pady=5)
        return control_frame

    def _create_control_buttons(self, control_frame: ttk.Frame):
        """Create the main control buttons (Compare, Sync, Options)."""
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
        """Create the paned window frame for the two panels."""
        panels_frame = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        panels_frame.grid(row=1, column=0, columnspan=3, sticky=tk.NSEW)
        return panels_frame

    def _create_panels(self, panels_frame: ttk.PanedWindow):
        """Create both panel A and panel B with their configurations."""
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
                "remote_mode_var": self.remote_mode_a,
                "panel_letter": "A",
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
                "remote_mode_var": self.remote_mode_b,
                "panel_letter": "B",
            },
        ]
        for config in panel_configs:
            self._create_panel(panels_frame, config)

    def _create_panel(self, parent: ttk.PanedWindow, panel_config: dict):
        """Create a single panel with folder browser, SSH config, and tree view."""
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
        remote_mode_var = panel_config["remote_mode_var"]
        panel_letter = panel_config["panel_letter"]
        btn_colors = self.colors["buttons"].get(
            button_color, self.colors["buttons"]["default"]
        )

        panel_frame = ttk.Frame(parent, padding=0)
        panel = ttk.LabelFrame(panel_frame, text=title, padding="5")
        panel.pack(fill=tk.BOTH, expand=True)
        panel.columnconfigure(0, weight=0)
        panel.columnconfigure(1, weight=1)
        panel.rowconfigure(6, weight=1)

        remote_check = ttk.Checkbutton(
            panel,
            text=" Remote ",
            variable=remote_mode_var,
            command=lambda: self._on_remote_toggle(panel_letter),
        )
        remote_check.grid(
            row=0, column=0, columnspan=2, padx=5, pady=(5, 0), sticky=tk.W
        )

        ttk.Label(panel, text="Host:").grid(
            row=1, column=0, padx=5, pady=5, sticky=tk.E
        )
        host_list = self.hosts_a if panel_letter == "A" else self.hosts_b
        host_values = [h.get("host", "") for h in host_list]
        host_combobox = ttk.Combobox(
            panel, textvariable=host_var, values=host_values, width=15
        )
        host_combobox.grid(row=1, column=1, padx=5, pady=5, sticky=tk.EW)
        host_combobox.bind(
            "<<ComboboxSelected>>",
            lambda e, pn=panel_letter: self._on_host_selected(pn),
        )

        ttk.Label(panel, text="Port:").grid(
            row=1, column=2, padx=5, pady=5, sticky=tk.E
        )
        port_entry = ttk.Entry(panel, textvariable=port_var, width=8)
        port_entry.grid(row=1, column=3, padx=5, pady=5, sticky=tk.EW)
        test_btn = GButton(
            panel,
            text="Test",
            command=lambda: self._test_ssh(title),
            width=70,
            height=30,
            **btn_colors,
        )
        test_btn.grid(row=1, column=4, padx=5, pady=5)
        if panel_letter == "A":
            self.test_btn_a = test_btn
        else:
            self.test_btn_b = test_btn

        ttk.Label(panel, text="Username:").grid(
            row=2, column=0, padx=5, pady=5, sticky=tk.E
        )
        user_entry = ttk.Entry(panel, textvariable=user_var, width=15)
        user_entry.grid(row=2, column=1, padx=5, pady=5, sticky=tk.EW)
        ttk.Label(panel, text="Password:").grid(
            row=2, column=2, padx=5, pady=5, sticky=tk.E
        )
        pass_entry = ttk.Entry(panel, textvariable=pass_var, show="*", width=15)
        pass_entry.grid(row=2, column=3, columnspan=2, padx=5, pady=5, sticky=tk.EW)

        ssh_entries = [host_combobox, port_entry, user_entry, pass_entry]
        if panel_letter == "A":
            self.ssh_entries_a = ssh_entries
        else:
            self.ssh_entries_b = ssh_entries

        ttk.Label(panel, text="Path:").grid(
            row=3, column=0, padx=5, pady=5, sticky=tk.E
        )
        path_combobox = ttk.Combobox(
            panel,
            textvariable=folder_var,
            values=[entry["path"] for entry in folder_history],
            width=20,
        )
        path_combobox.grid(row=3, column=1, columnspan=2, padx=5, pady=5, sticky=tk.EW)
        path_combobox.bind(
            "<<ComboboxSelected>>",
            lambda e, pn=panel_letter, cb=path_combobox: self._on_path_selected(pn, cb),
        )

        history_menu = tk.Menu(panel, tearoff=0)
        history_menu.add_command(
            label="Delete path",
            command=lambda pn=panel_letter: self._on_delete_history_item(
                pn, path_combobox
            ),
        )

        def show_history_menu(event):
            """Show history menu for path combobox."""
            history_menu.post(event.x_root, event.y_root)

        def close_menu(e):
            """Close the history menu."""
            history_menu.unpost()
            self.root.unbind("<Escape>")
            self.root.bind("<Escape>", close_menu)

        path_combobox.bind("<Button-3>", show_history_menu)

        def on_go():
            """Handle Go button click - populate panel with selected path."""
            panel_letter = title.split(" ")[1]
            folder_path = folder_var.get()
            if folder_path:
                self._save_current_ssh_to_history(panel_letter)
                self._populate_single_panel(panel_letter, folder_path)

        GButton(
            panel, text="Go", command=on_go, width=70, height=30, **btn_colors
        ).grid(row=3, column=3, padx=5, pady=5)
        GButton(
            panel,
            text="Browse",
            command=browse_command,
            width=70,
            height=30,
            **btn_colors,
        ).grid(row=3, column=4, padx=5, pady=5)

        column_widths = (
            self.column_widths_a if tree_attr == "tree_a" else self.column_widths_b
        )
        tree = self._create_tree_view(panel, column_widths)
        tree.grid(row=6, column=0, columnspan=5, pady=(10, 0), sticky=tk.NSEW)

        def apply_widths():
            """Apply saved column widths to tree view."""
            for col in list(tree["columns"]) + ["#0"]:
                width = column_widths.get(col)
                if width:
                    try:
                        tree.column(col, width=width)
                    except Exception as e:
                        self._log(f"Error applying column width for {col}: {e}")

        self.root.after(100, apply_widths)

        v_scrollbar = ttk.Scrollbar(panel, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=v_scrollbar.set)
        v_scrollbar.grid(row=6, column=5, pady=(10, 0), sticky=tk.NS)
        h_scrollbar = ttk.Scrollbar(panel, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(xscrollcommand=h_scrollbar.set)
        h_scrollbar.grid(row=7, column=0, columnspan=5, sticky=tk.EW)

        tree.bind("<Button-1>", self._on_tree_click)
        tree.bind("<Button-3>", self._on_tree_right_click)
        tree.bind("<Double-1>", self._on_tree_header_double_click)

        if tree_attr == "tree_a":
            self.tree_a = tree
        else:
            self.tree_b = tree

        parent.add(panel_frame, weight=1)
        self._on_remote_toggle(panel_letter)

    def _create_tree_view(
        self, parent: ttk.LabelFrame, column_widths: Optional[dict] = None
    ) -> ttk.Treeview:
        """Create a tree view for displaying file structure."""
        # Get display scaling factor
        scale_factor = GScaling.get_scale_factor(parent)

        if column_widths is None:
            column_widths = {
                "#0": 200,
                "sync": 50,
                "size": 80,
                "modified": 120,
                "status": 100,
            }

        # Scale column widths based on display DPI
        scaled_widths = {
            col: int(width * scale_factor) for col, width in column_widths.items()
        }

        tree = ttk.Treeview(
            parent,
            columns=("sync", "size", "modified", "status"),
            show="tree headings",
        )
        tree.heading("#0", text="Name")
        tree.column("#0", width=scaled_widths.get("#0", 200), anchor="w", stretch=False)
        tree.heading("sync", text="Sync")
        tree.column(
            "sync",
            width=scaled_widths.get("sync", 50),
            anchor="center",
            stretch=False,
        )
        tree.heading("size", text="Size")
        tree.column(
            "size", width=scaled_widths.get("size", 80), anchor="e", stretch=False
        )
        tree.heading("modified", text="Modified")
        tree.column(
            "modified",
            width=scaled_widths.get("modified", 120),
            anchor="center",
            stretch=False,
        )
        tree.heading("status", text="Status")
        tree.column(
            "status",
            width=scaled_widths.get("status", 100),
            anchor="center",
            stretch=False,
        )
        colors = self.colors["status"]
        for tag, color in colors.items():
            tree.tag_configure(tag, foreground=color)
        return tree

    def _create_status_bar(self, parent: ttk.Frame):
        """Create status bar with progress indicator."""
        status_frame = ttk.Frame(parent, relief="flat", padding="2")
        status_frame.grid(row=2, column=0, columnspan=3, sticky=tk.EW, pady=(5, 0))
        status_frame.columnconfigure(0, weight=1)
        status_frame.columnconfigure(1, weight=1)

        self.status_label_a = ttk.Label(
            status_frame, textvariable=self.status_a, width=80, anchor=tk.W
        )
        self.status_label_a.grid(row=0, column=0, sticky=tk.EW, padx=0)
        self.status_label_b = ttk.Label(
            status_frame, textvariable=self.status_b, width=80, anchor=tk.W
        )
        self.status_label_b.grid(row=0, column=1, sticky=tk.EW, padx=0)

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
            label="Delete", command=self._delete_selected_items
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
        self.tree_context_menu.add_separator()
        self.tree_context_menu.add_command(label="Expand All", command=self._expand_all)
        self.tree_context_menu.add_command(
            label="Collapse All", command=self._collapse_all
        )

    def _on_remote_toggle(self, panel: str):
        """Enable/disable SSH fields based on remote checkbox."""
        if panel == "A":
            enabled = self.remote_mode_a.get()
            entries = self.ssh_entries_a
            test_btn = self.test_btn_a
        else:
            enabled = self.remote_mode_b.get()
            entries = self.ssh_entries_b
            test_btn = self.test_btn_b

        state = "normal" if enabled else "disabled"
        for widget in entries:
            try:
                widget.config(state=state)
            except tk.TclError:
                pass
        if test_btn:
            try:
                test_btn.config(state=state)
            except tk.TclError:
                pass

    def _on_host_selected(self, panel_name: str):
        """Called when user selects a host from the combobox. Auto-fill port and username."""
        if panel_name == "A":
            host = self.remote_host_a.get()
            for h in self.hosts_a:
                if h.get("host") == host:
                    self.remote_port_a.set(h.get("port", self.remote_port_a.get()))
                    self.remote_user_a.set(h.get("username", self.remote_user_a.get()))
                    if host and h.get("username"):
                        self.remote_mode_a.set(True)
                        self._on_remote_toggle("A")
                    return
        else:
            host = self.remote_host_b.get()
            for h in self.hosts_b:
                if h.get("host") == host:
                    self.remote_port_b.set(h.get("port", self.remote_port_b.get()))
                    self.remote_user_b.set(h.get("username", self.remote_user_b.get()))
                    if host and h.get("username"):
                        self.remote_mode_b.set(True)
                        self._on_remote_toggle("B")
                    return

    def _update_host_history(
        self, panel_name: str, host: str, port: str, username: str
    ):
        """Update host history list for a panel (most-recent-first, deduped)."""
        if not host:
            return
        entry = {"host": host, "port": port or "22", "username": username or ""}
        if panel_name == "A":
            self.hosts_a = [h for h in self.hosts_a if h.get("host") != host]
            self.hosts_a.insert(0, entry)
            self.hosts_a = self.hosts_a[:HISTORY_LENGTH]
        else:
            self.hosts_b = [h for h in self.hosts_b if h.get("host") != host]
            self.hosts_b.insert(0, entry)
            self.hosts_b = self.hosts_b[:HISTORY_LENGTH]

    def _browse_panel_a(self):
        """Browse for local folder in panel A."""
        self._browse_panel("A")

    def _browse_panel_b(self):
        """Browse for local folder in panel B."""
        self._browse_panel("B")

    def _browse_panel(self, panel_name: str):
        """Browse for folder (local or remote) for specified panel."""
        if panel_name == "A":
            folder_var = self.folder_a
            folder_history = self.folder_a_history
        else:
            folder_var = self.folder_b
            folder_history = self.folder_b_history

        initial_path = folder_var.get()
        if not initial_path and folder_history:
            initial_path = folder_history[0]["path"]

        remote_mode = (
            self.remote_mode_a.get() if panel_name == "A" else self.remote_mode_b.get()
        )
        if remote_mode:
            selected_path = self._browse_remote(
                folder_var, f"Panel {panel_name}", initial_path
            )
            if selected_path:
                self._populate_single_panel(panel_name, selected_path)
        else:
            folder = filedialog.askdirectory(initialdir=initial_path)
            if folder:
                self._update_panel_history(panel_name, folder_var, folder, None)
                folder_var.set(folder)
                self._populate_single_panel(panel_name, folder)

    def _get_ssh_config_for_panel(self, panel: str) -> Optional[dict]:
        """Get SSH configuration for the specified panel."""
        if panel == "A":
            if not self.remote_mode_a.get():
                return None
            host = self.remote_host_a.get().strip()
            user = self.remote_user_a.get().strip()
            password = self.remote_pass_a.get()
            port_str = self.remote_port_a.get().strip()
            if not host or not user:
                return None
            try:
                port = int(port_str) if port_str else 22
            except ValueError:
                port = 22
            return {"host": host, "user": user, "password": password, "port": port}
        else:
            if not self.remote_mode_b.get():
                return None
            host = self.remote_host_b.get().strip()
            user = self.remote_user_b.get().strip()
            password = self.remote_pass_b.get()
            port_str = self.remote_port_b.get().strip()
            if not host or not user:
                return None
            try:
                port = int(port_str) if port_str else 22
            except ValueError:
                port = 22
            return {"host": host, "user": user, "password": password, "port": port}

    @contextmanager
    def _get_ssh_connection_for_panel(self, panel: str):
        """Context manager to get SSH connection for specified panel."""
        ssh_config = self._get_ssh_config_for_panel(panel)
        if ssh_config is None:
            yield None
            return
        with self.connection_manager.get_connection(
            ssh_config["host"],
            ssh_config["user"],
            ssh_config["password"],
            ssh_config["port"],
        ) as client:
            yield client

    def _test_ssh(self, panel_title: str):
        """Test SSH connection for the specified panel."""
        panel = panel_title.split(" ")[1]
        remote_mode = (
            self.remote_mode_a.get() if panel == "A" else self.remote_mode_b.get()
        )
        if not remote_mode:
            messagebox.showinfo(
                "Info", "SSH testing is only available when Remote mode is enabled."
            )
            return

        def test_thread():
            """Thread function to test SSH connection asynchronously."""
            try:
                ssh_config = self._get_ssh_config_for_panel(panel)
                if not ssh_config:
                    messagebox.showwarning("Warning", "Host and username are required.")
                    return
                self._log(f"Testing SSH {panel_title}...")
                with self.connection_manager.get_connection(
                    ssh_config["host"],
                    ssh_config["user"],
                    ssh_config["password"],
                    ssh_config["port"],
                ) as client:
                    if client is None:
                        raise ConnectionError("Failed to establish SSH connection.")
                    stdin, stdout, stderr = client.exec_command("echo ok")
                    stdout.channel.recv_exit_status()
                self._log(f"✓ SSH {panel_title} connected")
                messagebox.showinfo(
                    "Success", f"SSH connection established for {panel_title}!"
                )
            except Exception as e:
                self._log(f"✗ SSH connection failed for {panel_title}: {str(e)}")
                messagebox.showerror("Error", f"SSH connection failed: {str(e)}")

        threading.Thread(target=test_thread, daemon=True).start()

    def _on_path_selected(self, panel: str, combobox: ttk.Combobox):
        """Handle path selection from history combobox."""
        selected_path = combobox.get()
        history = self.folder_a_history if panel == "A" else self.folder_b_history
        entry = next((h for h in history if h["path"] == selected_path), None)
        if entry:
            self._restore_ssh_config(panel, entry)
            if panel == "A":
                self.folder_a.set(selected_path)
                self.remote_pass_a.set("")
            else:
                self.folder_b.set(selected_path)
                self.remote_pass_b.set("")

    def _save_current_ssh_to_history(self, panel: str):
        """Save current SSH configuration to panel history."""
        entry = self._build_history_entry(panel)
        self._add_to_history(panel, entry)
        if panel == "A":
            combobox = self._get_path_combobox("A")
            if combobox:
                combobox["values"] = [h["path"] for h in self.folder_a_history]
        else:
            combobox = self._get_path_combobox("B")
            if combobox:
                combobox["values"] = [h["path"] for h in self.folder_b_history]

    def _get_path_combobox(self, panel: str) -> Optional[ttk.Combobox]:
        """Get the path combobox widget for the specified panel."""
        return getattr(self, f"_path_combobox_{panel.lower()}", None)

    def _update_panel_history(
        self,
        panel_name: str,
        folder_var: tk.StringVar,
        new_path: str,
        ssh_config: Optional[dict],
    ):
        """Update panel history with new path and SSH config."""
        if not new_path or self._is_temporary_path(new_path):
            return
        if ssh_config is None:
            entry = {"path": new_path, "host": "", "port": "", "username": ""}
        else:
            entry = {
                "path": new_path,
                "host": ssh_config.get("host", ""),
                "port": str(ssh_config.get("port", 22)),
                "username": ssh_config.get("user", ""),
            }
        self._add_to_history(panel_name, entry)
        folder_var.set(new_path)
        self._save_config()

    def _delete_history_item(self, panel_name: str, item_to_delete: str) -> bool:
        """Delete a history item from the panel's history list."""
        history = self.folder_a_history if panel_name == "A" else self.folder_b_history
        for i, entry in enumerate(history):
            if entry["path"] == item_to_delete:
                del history[i]
                return True
        return False

    def _on_delete_history_item(self, panel_name: str, combobox: ttk.Combobox):
        """Handle deletion of a history item from the combobox."""
        current_value = combobox.get()
        if not current_value:
            return
        if messagebox.askyesno(
            "Confirm Deletion",
            f"Delete '{current_value}' from history?",
        ):
            deleted = self._delete_history_item(panel_name, current_value)
            if deleted:
                history = (
                    self.folder_a_history
                    if panel_name == "A"
                    else self.folder_b_history
                )
                combobox["values"] = [h["path"] for h in history]
                combobox.set("")
                combobox.update()
                combobox.update_idletasks()
            else:
                messagebox.showwarning(
                    "Not Found", f"'{current_value}' not found in history"
                )

    def _browse_remote(
        self, folder_var: tk.StringVar, panel_name: str, initial_path: str = ""
    ) -> Optional[str]:
        """Browse remote folder via SSH and return selected path."""
        panel = panel_name.split(" ")[1]
        try:
            with self._get_ssh_connection_for_panel(panel) as ssh_client:
                if ssh_client is None:
                    raise ConnectionError(
                        "Failed to establish SSH connection for remote browsing."
                    )
                current_path = initial_path or folder_var.get()
                if not current_path:
                    stdin, stdout, stderr = ssh_client.exec_command("pwd")
                    remote_path = stdout.read().decode().strip()
                    current_path = remote_path
                selected_path = self._show_remote_dialog(
                    ssh_client, folder_var, current_path, panel_name
                )
                if selected_path:
                    ssh_config = self._get_ssh_config_for_panel(panel)
                    self._update_panel_history(
                        panel, folder_var, selected_path, ssh_config
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
        """Show remote folder browsing dialog via SSH."""
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Browse Remote Folder - {panel_name}")
        dialog.geometry("500x400")
        dialog.minsize(500, 400)
        dialog.transient(self.root)
        dialog.grab_set()

        main_dialog_frame = ttk.Frame(dialog, padding="10")
        main_dialog_frame.pack(fill=tk.BOTH, expand=True)
        result = tk.StringVar()

        path_frame = ttk.Frame(main_dialog_frame)
        path_frame.pack(fill=tk.X, pady=(0, 5))
        path_var = tk.StringVar(value=current_path)
        ttk.Label(path_frame, text="Current Path:").pack(side=tk.LEFT)
        path_entry = ttk.Entry(path_frame, textvariable=path_var)
        path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        def go_to_path(event=None):
            """Navigate to the specified path in the remote dialog."""
            load_folders(path_var.get())

        GButton(
            path_frame,
            text="Go",
            command=go_to_path,
            width=70,
            height=30,
            **self.colors["buttons"]["default"],
        ).pack(side=tk.LEFT, padx=(5, 0))
        path_entry.bind("<Return>", go_to_path)

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
            """Load folders from remote path into listbox."""
            try:
                listbox.delete(0, tk.END)
                path_var.set(path)
                if path != "/":
                    listbox.insert(tk.END, "..")
                command = f"find {_posix_quote(path)} -maxdepth 1 -mindepth 1 -type d"
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
            """Handle double-click on listbox item to navigate."""
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
            """Select current path and close dialog."""
            result.set(path_var.get())
            dialog.destroy()

        def on_cancel():
            """Close dialog without selection."""
            dialog.destroy()

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

        listbox.bind("<Double-Button-1>", on_select)
        load_folders(current_path)
        self._center_dialog(dialog)
        self.root.wait_window(dialog)
        return result.get()

    def _populate_single_panel(
        self,
        panel: str,
        folder_path: str,
        active_rules: Optional[list] = None,
    ) -> threading.Thread:
        """Populate a single panel with files from the specified path."""

        def populate_thread_func():
            """Thread function to populate panel with file scan results."""
            try:
                self.root.after(0, self._start_progress, panel)
                ssh_config = self._get_ssh_config_for_panel(panel)
                if active_rules is None:
                    rules = self._get_active_filters()
                else:
                    rules = active_rules
                files = self._scan_folder(folder_path, ssh_config, panel, rules)
                with self.state_lock:
                    target_files_dict = self.files_a if panel == "A" else self.files_b
                    target_files_dict.clear()
                    target_files_dict.update(files)
                self.root.after(0, lambda: self._update_status(panel, files))

                tree_structure = self._build_tree_structure(files)
                tree = getattr(self, f"tree_{panel.lower()}")

                def populate_tree():
                    """Populate tree view with scanned file structure."""
                    if tree:
                        self._batch_populate_tree(tree, tree_structure, rules)

                self.root.after(0, populate_tree)
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
        ssh_config: Optional[dict],
        panel_name: str,
        rules: Optional[list] = None,
    ) -> dict:
        """Scan folder locally or via SSH and return file dictionary."""
        if rules is None:
            rules = []
        if ssh_config is not None:
            self._log(f"SSH scan panel {panel_name}")
            with self.connection_manager.get_connection(
                ssh_config["host"],
                ssh_config["user"],
                ssh_config["password"],
                ssh_config["port"],
            ) as ssh_client:
                if ssh_client is None:
                    self._log(f"Failed to acquire SSH client for panel {panel_name}")
                    return {}
                files = self._scan_remote(folder_path, ssh_client, rules)
                num_dirs = sum(1 for f in files.values() if f.get("type") == "dir")
                num_files = sum(1 for f in files.values() if f.get("type") == "file")
                self._log(
                    f"Found {num_dirs} folders and {num_files} files in panel {panel_name}"
                )
                return files
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
        """Scan local folder and return file dictionary with metadata."""
        files = {}
        if rules is None:
            rules = []
        try:
            for root, dirs, filenames in os.walk(
                folder_path, topdown=True, followlinks=True
            ):
                excluded_dirs = set()
                for d in dirs:
                    dir_rel_path = os.path.relpath(
                        os.path.join(root, d), folder_path
                    ).replace(os.sep, "/")
                    for pattern in rules:
                        if pattern.endswith("/") and fnmatch.fnmatch(
                            dir_rel_path + "/", pattern
                        ):
                            excluded_dirs.add(d)
                        elif fnmatch.fnmatch(dir_rel_path, pattern):
                            excluded_dirs.add(d)
                        elif not pattern.endswith("/") and fnmatch.fnmatch(d, pattern):
                            excluded_dirs.add(d)
                dirs[:] = [d for d in dirs if d not in excluded_dirs]

                for dirname in dirs:
                    full_path = os.path.join(root, dirname)
                    rel_path = os.path.relpath(full_path, folder_path).replace(
                        os.sep, "/"
                    )
                    if dirname not in excluded_dirs:
                        files[rel_path] = {
                            "type": "dir",
                            "full_path": full_path,
                        }

                for filename in filenames:
                    full_path = os.path.join(root, filename)
                    rel_path = os.path.relpath(full_path, folder_path).replace(
                        os.sep, "/"
                    )
                    if any(fnmatch.fnmatch(rel_path, r) for r in rules):
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
        """Scan remote folder via SSH and return file dictionary."""
        files = {}
        if rules is None:
            rules = []
        qpath = _posix_quote(folder_path)
        self._log("Remote scan: using two-pass find + stat (robust method).")
        fallback_cmd = (
            rf"{{ find {qpath} -mindepth 1 -type f"
            rf" -exec stat -c 'f|%n|%s|%Y' {{}} \; ;"
            rf" find {qpath} -mindepth 1 -type d"
            rf" -exec stat -c 'd|%n' {{}} \; ; }} 2>/dev/null"
        )
        _, out, _ = ssh_client.exec_command(fallback_cmd)
        raw_lines = out.readlines()

        for line in raw_lines:
            line = line.strip()
            if not line:
                continue
            try:
                parts = line.split("|", 3)
                if len(parts) < 2:
                    self._log(f"Warning: Skipping malformed line: {line}")
                    continue
                type_char = parts[0]
                filepath = parts[1]
                size = parts[2] if len(parts) > 2 else "0"
                mtime = parts[3] if len(parts) > 3 else "0"

                if type_char == "d":
                    filetype = "directory"
                else:
                    filetype = "regular file"

                if not filepath.startswith(folder_path):
                    continue
                rel_path = filepath[len(folder_path) :].lstrip("/")

                if any(fnmatch.fnmatch(rel_path, r) for r in rules) or any(
                    fnmatch.fnmatch(part, r)
                    for r in rules
                    for part in rel_path.split("/")
                ):
                    continue

                if filetype == "directory":
                    files[rel_path] = {"type": "dir", "full_path": filepath}
                else:
                    try:
                        size_int = int(size)
                    except ValueError:
                        size_int = 0
                    try:
                        mtime_float = float(mtime)
                    except ValueError:
                        mtime_float = 0.0
                    files[rel_path] = {
                        "size": size_int,
                        "modified": mtime_float,
                        "full_path": filepath,
                        "type": "file",
                    }
            except Exception as e:
                self._log(f"Warning: Could not parse remote scan line '{line}': {e}")
        self._log(f"Remote folder scan ended for {folder_path}")
        return files

    def _build_tree_structure(self, files: dict) -> dict:
        """Build hierarchical tree structure from flat file dictionary."""
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
        """Batch populate tree view with file structure."""
        if not tree:
            return
        if structure:
            tree.column("#0", stretch=False)
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
            """Recursively insert items into tree view from data structure."""
            items = sorted(data.items())
            for name, content in items:
                if name == ".":
                    continue
                if any(
                    fnmatch.fnmatch(
                        os.path.join(current_path_prefix, name).replace(os.sep, "/"),
                        pattern,
                    )
                    for pattern in filter_rules_for_insertion
                ):
                    continue
                if isinstance(content, dict) and "size" not in content:
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
        font_family = self.options["font_family"]
        font_size = self.options["font_size"]
        tree.tag_configure("custom_font", font=(font_family, font_size))

    def _build_tree_map(
        self, tree: Optional[ttk.Treeview], parent_item: str = "", path: str = ""
    ) -> dict:
        """Build a mapping of paths to tree item IDs."""
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
        """Update a tree item with comparison status and color."""
        if tree is None:
            return
        current_values = tree.item(item_id, "values")
        with self.state_lock:
            check_char = (
                CHECKED_CHAR
                if self.sync_states.get(rel_path, False)
                else UNCHECKED_CHAR
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

    def _validate_ssh_fields(self) -> list:
        """Return a list of human-readable error strings for any incomplete SSH panel.

        Host, Username, and Password are required (SSH auth will fail without any
        of them). Port is optional and defaults to 22 if left blank, so it is not
        validated here.
        """
        missing = []
        for panel, mode_var, host_var, user_var, pass_var in (
            (
                "A",
                self.remote_mode_a,
                self.remote_host_a,
                self.remote_user_a,
                self.remote_pass_a,
            ),
            (
                "B",
                self.remote_mode_b,
                self.remote_host_b,
                self.remote_user_b,
                self.remote_pass_b,
            ),
        ):
            if mode_var.get():
                if not host_var.get().strip():
                    missing.append(f"Panel {panel}: Host is required")
                if not user_var.get().strip():
                    missing.append(f"Panel {panel}: Username is required")
                if not pass_var.get():
                    missing.append(f"Panel {panel}: Password is required")
        return missing

    def compare_folders(self):
        """Compare files in both panels and display results."""
        folder_a_path = self.folder_a.get()
        folder_b_path = self.folder_b.get()
        if not folder_a_path or not folder_b_path:
            messagebox.showerror("Error", "Please select both folders to compare")
            return

        missing_ssh_fields = self._validate_ssh_fields()
        if missing_ssh_fields:
            messagebox.showerror(
                "Missing SSH Configuration",
                "Please fill in all required SSH fields:\n"
                + "\n".join(missing_ssh_fields),
            )
            return

        def compare_thread():
            """Thread function to perform folder comparison asynchronously."""
            self._log("Starting folder comparison...")
            try:
                self.root.after(0, self._start_progress, None, 0, "Scanning folders...")
                ssh_config_a = self._get_ssh_config_for_panel("A")
                ssh_config_b = self._get_ssh_config_for_panel("B")
                rules = self._get_active_filters()

                with ThreadPoolExecutor(max_workers=2) as executor:
                    future_a = executor.submit(
                        self._scan_folder, folder_a_path, ssh_config_a, "A", rules
                    )
                    future_b = executor.submit(
                        self._scan_folder, folder_b_path, ssh_config_b, "B", rules
                    )
                    files_a_result = future_a.result()
                    files_b_result = future_b.result()

                with self.state_lock:
                    self.files_a = files_a_result
                    self.files_b = files_b_result

                total_items = len(set(self.files_a.keys()) | set(self.files_b.keys()))
                self.root.after(
                    0, self._start_progress, None, total_items, "Comparing files..."
                )

                item_statuses, stats = self._run_comparison_logic(
                    ssh_config_a, ssh_config_b, self.files_a, self.files_b
                )

                def final_ui_update():
                    """Update UI with comparison results."""
                    tree_structure_a = self._build_tree_structure(self.files_a)
                    tree_structure_b = self._build_tree_structure(self.files_b)
                    if self.tree_a:
                        self._batch_populate_tree(self.tree_a, tree_structure_a, rules)
                    if self.tree_b:
                        self._batch_populate_tree(self.tree_b, tree_structure_b, rules)

                    fresh_tree_a_map = self._build_tree_map(self.tree_a)
                    fresh_tree_b_map = self._build_tree_map(self.tree_b)

                    self._apply_comparison_to_ui(
                        item_statuses, stats, fresh_tree_a_map, fresh_tree_b_map
                    )

                self.root.after(0, final_ui_update)
            except Exception as e:
                self._log(f"Error during comparison: {str(e)}")
            finally:
                self.root.after(0, self._stop_progress)

        threading.Thread(target=compare_thread, daemon=True).start()

    def _run_comparison_logic(
        self,
        ssh_config_a: Optional[dict],
        ssh_config_b: Optional[dict],
        files_a: dict,
        files_b: dict,
    ) -> tuple:
        """Run comparison logic and return item statuses and statistics."""
        all_paths = set(files_a.keys()) | set(files_b.keys())
        self._log("Parallel comparison (using per-panel SSH configs)")
        item_statuses, stats, dirty_folders = self._calculate_item_statuses_parallel(
            all_paths,
            files_a,
            files_b,
            ssh_config_a,
            ssh_config_b,
            max_workers=os.cpu_count() or 4,
        )
        self._propagate_dirty_folders(item_statuses, dirty_folders)
        return item_statuses, stats

    def _propagate_dirty_folders(self, item_statuses: dict, dirty_folders: set):
        """Mark parent folders as different when child files differ."""
        parents_to_mark_different = set()
        for path in dirty_folders:
            parents_to_mark_different.add(path)
            current_path = os.path.dirname(path)
            while current_path and current_path != ".":
                parents_to_mark_different.add(current_path)
                parent = os.path.dirname(current_path)
                current_path = parent
        if dirty_folders:
            parents_to_mark_different.add(".")

        for path in parents_to_mark_different:
            if item_statuses.get(path, (None,))[0] not in ("Only in A", "Only in B"):
                item_statuses[path] = ("Different", "magenta")
            with self.state_lock:
                self.sync_states[path] = True

    def _calculate_item_statuses_parallel(
        self,
        all_visible_paths: set,
        files_a: dict,
        files_b: dict,
        ssh_config_a: Optional[dict],
        ssh_config_b: Optional[dict],
        max_workers: int = 4,
    ) -> tuple:
        """Calculate item statuses using parallel processing."""
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

        file_paths = []
        dir_paths = []
        for rel_path in all_visible_paths:
            file_a_info = files_a.get(rel_path)
            file_b_info = files_b.get(rel_path)
            is_file_a = file_a_info and file_a_info.get("type") == "file"
            is_dir_a = file_a_info and file_a_info.get("type") == "dir"
            is_file_b = file_b_info and file_b_info.get("type") == "file"
            is_dir_b = file_b_info and file_b_info.get("type") == "dir"

            if (is_file_a and is_dir_b) or (is_dir_a and is_file_b):
                item_statuses[rel_path] = ("Conflict", "black")
                stats["conflicts"] += 1
                with self.state_lock:
                    self.sync_states[rel_path] = True
                dirty_folders.add(os.path.dirname(rel_path))
            elif is_file_a or is_file_b:
                file_paths.append(rel_path)
            else:
                dir_paths.append(rel_path)

        self._log(f"Processing {len(file_paths)} files, {len(dir_paths)} dirs")

        def compare_single_file(rel_path: str) -> tuple:
            """Compare a single file between panels and return status tuple."""
            file_a_info = files_a.get(rel_path)
            file_b_info = files_b.get(rel_path)
            use_ssh_a = ssh_config_a is not None
            use_ssh_b = ssh_config_b is not None
            ssh_a = None
            ssh_b = None
            try:
                if use_ssh_a:
                    with self.connection_manager.get_connection(
                        ssh_config_a["host"],
                        ssh_config_a["user"],
                        ssh_config_a["password"],
                        ssh_config_a["port"],
                    ) as client:
                        ssh_a = client
                        if use_ssh_b:
                            with self.connection_manager.get_connection(
                                ssh_config_b["host"],
                                ssh_config_b["user"],
                                ssh_config_b["password"],
                                ssh_config_b["port"],
                            ) as client_b:
                                ssh_b = client_b
                                status, status_color = self.comparer._compare_files(
                                    file_a_info,
                                    file_b_info,
                                    use_ssh_a,
                                    use_ssh_b,
                                    ssh_a,
                                    ssh_b,
                                )
                        else:
                            status, status_color = self.comparer._compare_files(
                                file_a_info,
                                file_b_info,
                                use_ssh_a,
                                use_ssh_b,
                                ssh_a,
                                None,
                            )
                else:
                    if use_ssh_b:
                        with self.connection_manager.get_connection(
                            ssh_config_b["host"],
                            ssh_config_b["user"],
                            ssh_config_b["password"],
                            ssh_config_b["port"],
                        ) as client_b:
                            ssh_b = client_b
                            status, status_color = self.comparer._compare_files(
                                file_a_info,
                                file_b_info,
                                use_ssh_a,
                                use_ssh_b,
                                None,
                                ssh_b,
                            )
                    else:
                        status, status_color = self.comparer._compare_files(
                            file_a_info,
                            file_b_info,
                            False,
                            False,
                            None,
                            None,
                        )
            except Exception as e:
                self._log(f"Error comparing {rel_path}: {e}")
                status, status_color = "Error", "black"
            return rel_path, status, status_color

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_path = {
                executor.submit(compare_single_file, rel_path): rel_path
                for rel_path in file_paths
            }
            for future in as_completed(future_to_path):
                rel_path, status, status_color = future.result()
                item_statuses[rel_path] = (status, status_color)
                if status == "Identical":
                    stats["identical"] += 1
                    with self.state_lock:
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
                    with self.state_lock:
                        self.sync_states[rel_path] = True
                self.root.after(0, self._update_progress, 1)

        for rel_path in dir_paths:
            file_a_info = files_a.get(rel_path)
            file_b_info = files_b.get(rel_path)
            is_dir_in_a = file_a_info and file_a_info.get("type") == "dir"
            is_dir_in_b = file_b_info and file_b_info.get("type") == "dir"
            if is_dir_in_a and not is_dir_in_b:
                item_statuses[rel_path] = ("Only in A", "blue")
                stats["only_a"] += 1
                with self.state_lock:
                    self.sync_states[rel_path] = True
                dirty_folders.add(os.path.dirname(rel_path))
            elif is_dir_in_b and not is_dir_in_a:
                item_statuses[rel_path] = ("Only in B", "red")
                stats["only_b"] += 1
                with self.state_lock:
                    self.sync_states[rel_path] = True
                dirty_folders.add(os.path.dirname(rel_path))

        for rel_path in sorted(all_visible_paths):
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
        """Apply comparison results to the UI tree views."""
        show_diff_only = self.options.get("show_diff_only", False)
        for rel_path, (status, status_color) in item_statuses.items():
            self.root.after(0, self._update_progress, 1)
            if show_diff_only and status == "Identical":
                if rel_path in tree_a_map and self.tree_a:
                    try:
                        self.tree_a.delete(tree_a_map[rel_path])
                    except tk.TclError:
                        pass
                if rel_path in tree_b_map and self.tree_b:
                    try:
                        self.tree_b.delete(tree_b_map[rel_path])
                    except tk.TclError:
                        pass
                continue

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

    def synchronize(self, direction: str):
        """Synchronize files from source to target panel."""

        def sync_thread():
            """Thread function to perform synchronization asynchronously."""
            self._log(f"Starting synchronization: {direction}")
            if direction == "a_to_b":
                source_path = self.folder_a.get()
                target_path = self.folder_b.get()
                with self.state_lock:
                    source_files_dict = self.files_a.copy()
                    source_ssh_config = self._get_ssh_config_for_panel("A")
                    target_ssh_config = self._get_ssh_config_for_panel("B")
            else:
                source_path = self.folder_b.get()
                target_path = self.folder_a.get()
                with self.state_lock:
                    source_files_dict = self.files_b.copy()
                    source_ssh_config = self._get_ssh_config_for_panel("B")
                    target_ssh_config = self._get_ssh_config_for_panel("A")

            if not source_path or not target_path:
                messagebox.showerror(
                    "Error", "Source and target folder paths must be set."
                )
                return

            try:
                files_to_copy = self._get_files_to_copy(source_files_dict)
                with self.state_lock:
                    target_files_dict = (
                        self.files_b if direction == "a_to_b" else self.files_a
                    ).copy()

                if not files_to_copy:
                    self._log("No files selected for synchronization.")
                    messagebox.showinfo(
                        "Sync",
                        "No files are checked for synchronization or folders are already in sync.",
                    )
                    return

                self.root.after(
                    0,
                    self._start_progress,
                    None,
                    len(files_to_copy),
                    "Synchronizing...",
                )

                self._perform_sync(
                    files_to_copy,
                    source_files_dict,
                    target_path,
                    source_ssh_config,
                    target_ssh_config,
                    target_files_dict,
                    direction,
                )

                self._log("Synchronization completed. Refreshing comparison...")
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

    def _get_files_to_copy(self, source_files_dict: dict) -> list:
        """Get list of files marked for synchronization from source."""
        files_to_sync = set()

        def _norm(p: str) -> str:
            """Normalize path separators to forward slashes."""
            return p.replace(os.sep, "/")

        with self.state_lock:
            sync_states_snapshot = self.sync_states.copy()
            for rel_path, is_checked in sync_states_snapshot.items():
                if not is_checked:
                    continue
                source_item = source_files_dict.get(rel_path)
                if not source_item:
                    continue
                if source_item.get("type") == "file":
                    files_to_sync.add(rel_path)
                elif source_item.get("type") == "dir":
                    dir_prefix = _norm(rel_path.rstrip(os.sep)) + "/"
                    for file_path, file_info in source_files_dict.items():
                        if file_info.get("type") != "file":
                            continue
                        if _norm(file_path).startswith(
                            dir_prefix
                        ) and sync_states_snapshot.get(file_path, False):
                            files_to_sync.add(file_path)
        return sorted(files_to_sync)

    def _perform_sync(
        self,
        files_to_copy: list,
        source_files_dict: dict,
        target_path: str,
        source_ssh_config: Optional[dict],
        target_ssh_config: Optional[dict],
        target_files_dict: dict,
        direction: str,
    ):
        """Perform synchronization using appropriate method based on SSH configs."""
        if source_ssh_config is not None and target_ssh_config is not None:
            self._sync_remote_to_remote(
                files_to_copy,
                source_files_dict,
                target_path,
                source_ssh_config,
                target_ssh_config,
                target_files_dict,
            )
        elif source_ssh_config is not None:
            self._sync_remote_to_local(
                files_to_copy,
                source_files_dict,
                target_path,
                source_ssh_config,
                target_files_dict,
            )
        elif target_ssh_config is not None:
            self._sync_local_to_remote(
                files_to_copy,
                source_files_dict,
                target_path,
                target_ssh_config,
                target_files_dict,
            )
        else:
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
        """Synchronize files from local source to local target."""
        self._log(f"Syncing local files to {target_path}")
        for rel_path in files_to_copy:
            source_file = source_files_dict[rel_path]["full_path"]
            target_file = os.path.join(target_path, rel_path.replace("/", os.sep))
            target_dir = os.path.dirname(target_file)
            os.makedirs(target_dir, exist_ok=True)

            if os.path.exists(target_file) and not os.access(target_file, os.W_OK):
                if os.name == "posix":
                    current_mode = os.stat(target_file).st_mode
                    os.chmod(target_file, current_mode | stat.S_IWUSR)
                elif os.name == "nt":
                    os.chmod(target_file, stat.S_IWRITE)
                else:
                    raise NotImplementedError(f"Unsupported OS: {os.name}")

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
        target_ssh_config: dict,
        target_files_dict: dict,
    ):
        """Synchronize files from local source to remote target via SCP."""
        self._log(f"Syncing local files to remote {remote_path}")
        with self.connection_manager.get_connection(
            target_ssh_config["host"],
            target_ssh_config["user"],
            target_ssh_config["password"],
            target_ssh_config["port"],
        ) as ssh_client:
            transport = ssh_client.get_transport()
            if not transport:
                raise ConnectionError("SSH client transport is not available.")
            with SCPClient(transport) as scp:
                for rel_path in files_to_copy:
                    local_file = source_files_dict[rel_path]["full_path"]
                    remote_file = _posix_join(remote_path, rel_path)
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
        source_ssh_config: dict,
        target_files_dict: dict,
    ):
        """Synchronize files from remote source to local target via SCP."""
        self._log(f"Syncing remote files to local {local_path}")
        with self.connection_manager.get_connection(
            source_ssh_config["host"],
            source_ssh_config["user"],
            source_ssh_config["password"],
            source_ssh_config["port"],
        ) as ssh_client:
            transport = ssh_client.get_transport()
            if not transport:
                raise ConnectionError("SSH client transport is not available.")
            with SCPClient(transport) as scp:
                for rel_path in files_to_copy:
                    remote_file = source_files_dict[rel_path]["full_path"]
                    local_file = os.path.join(local_path, rel_path.replace("/", os.sep))
                    local_dir = os.path.dirname(local_file)
                    os.makedirs(local_dir, exist_ok=True)

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
        source_ssh_config: dict,
        target_ssh_config: dict,
        target_files_dict: dict,
    ):
        """Synchronize files from remote source to remote target via SSH."""
        self._log(f"Syncing remote files to remote {target_path}")
        with self.connection_manager.get_connection(
            source_ssh_config["host"],
            source_ssh_config["user"],
            source_ssh_config["password"],
            source_ssh_config["port"],
        ) as source_ssh:
            with self.connection_manager.get_connection(
                target_ssh_config["host"],
                target_ssh_config["user"],
                target_ssh_config["password"],
                target_ssh_config["port"],
            ) as target_ssh:
                source_transport = source_ssh.get_transport()
                target_transport = target_ssh.get_transport()
                if not source_transport or not target_transport:
                    raise ConnectionError("SSH transport not available.")

                for rel_path in files_to_copy:
                    source_file_path = source_files_dict[rel_path]["full_path"]
                    target_file_path = _posix_join(target_path, rel_path)
                    target_dir = posixpath.dirname(target_file_path)
                    target_ssh.exec_command(f"mkdir -p {_posix_quote(target_dir)}")

                    with SCPClient(source_transport) as scp_source:
                        with SCPClient(target_transport) as scp_target:
                            self._log(f"Copying remote-to-remote: {rel_path}")
                            temp_f = tempfile.NamedTemporaryFile(delete=False)
                            temp_name = temp_f.name
                            try:
                                temp_f.close()
                                scp_source.get(source_file_path, temp_name)
                                target_item = target_files_dict.get(rel_path)
                                if target_item and target_item.get("type") == "dir":
                                    target_ssh.exec_command(
                                        f"rm -rf {_posix_quote(target_file_path)}"
                                    )
                                scp_target.put(temp_name, target_file_path)
                            finally:
                                try:
                                    os.remove(temp_name)
                                except Exception:
                                    self._log(
                                        f"Warning: could not remove temp file {temp_name}"
                                    )
                    self.root.after(0, self._update_progress)

    def _show_filters_dialog(self):
        """Show dialog for editing filter rules."""
        with self.state_lock:
            temp_filters = [dict(item) for item in self.filter_rules]

        dialog = tk.Toplevel(self.root)
        dialog.title("Edit Filters")
        dialog.geometry("400x400")
        dialog.minsize(300, 300)
        dialog.transient(self.root)
        dialog.grab_set()

        style = ttk.Style()
        dialog_bg = style.lookup("TFrame", "background")
        dialog.configure(bg=dialog_bg)

        context_menu = tk.Menu(dialog, tearoff=0)
        dialog.rowconfigure(0, weight=1)
        dialog.columnconfigure(0, weight=1)

        tree_frame, filter_tree = self._create_filter_tree(dialog)
        tree_frame.grid(row=0, column=0, padx=10, pady=10, sticky=tk.NSEW)

        def populate_tree():
            """Populate filter tree with current filter rules."""
            for item in filter_tree.get_children():
                filter_tree.delete(item)
            for i, item in enumerate(temp_filters):
                check_char = (
                    CHECKED_CHAR if item.get("active", True) else UNCHECKED_CHAR
                )
                filter_tree.insert("", "end", iid=i, values=(check_char, item["rule"]))

        def insert_rule():
            """Insert a new filter rule into the list."""
            new_rule = _ask_string_dialog(
                dialog, "Insert Rule", "Enter new filter pattern:", colors=self.colors
            )
            if new_rule and new_rule.strip():
                temp_filters.append({"rule": new_rule.strip(), "active": True})
                temp_filters.sort(key=lambda item: item["rule"])
                populate_tree()

        def edit_rule():
            """Edit the selected filter rule."""
            selected_item = filter_tree.focus()
            if not selected_item:
                return
            index = int(selected_item)
            current_rule = temp_filters[index]["rule"]
            edited_rule = _ask_string_dialog(
                dialog,
                "Edit Rule",
                "Edit filter pattern:",
                initial=current_rule,
                colors=self.colors,
            )
            if edited_rule and edited_rule.strip():
                temp_filters[index]["rule"] = edited_rule.strip()
                temp_filters.sort(key=lambda item: item["rule"])
                populate_tree()

        def remove_rule():
            """Remove the selected filter rule after confirmation."""
            selected_item = filter_tree.focus()
            if selected_item:
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
                    """Handle yes button in confirmation dialog."""
                    nonlocal confirmed
                    confirmed = True
                    confirm_dialog.destroy()

                btn_frame = ttk.Frame(confirm_dialog, padding=10)
                btn_frame.pack(fill="x")
                GButton(
                    btn_frame,
                    text="Yes",
                    command=on_yes,
                    width=70,
                    height=30,
                    **self.colors["buttons"]["primary"],
                ).pack(side="right", padx=5)
                GButton(
                    btn_frame,
                    text="No",
                    command=confirm_dialog.destroy,
                    width=70,
                    height=30,
                    **self.colors["buttons"]["default"],
                ).pack(side="right")
                confirm_dialog.wait_window()
                if confirmed:
                    index = int(selected_item)
                    del temp_filters[index]
                    populate_tree()

        def select_all():
            """Mark all filter rules as active."""
            for item in temp_filters:
                item["active"] = True
            populate_tree()

        def deselect_all():
            """Mark all filter rules as inactive."""
            for item in temp_filters:
                item["active"] = False
            populate_tree()

        context_menu.add_command(label="Insert Rule", command=insert_rule)
        context_menu.add_command(label="Edit Rule", command=edit_rule)
        context_menu.add_command(label="Remove Rule", command=remove_rule)
        context_menu.add_separator()
        context_menu.add_command(label="Select All", command=select_all)
        context_menu.add_command(label="Deselect All", command=deselect_all)

        def on_tree_click(event: tk.Event):
            """Handle tree click to toggle filter rule active state."""
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
            """Show context menu for filter tree operations."""
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

        filter_tree.bind("<Button-1>", on_tree_click)
        filter_tree.bind("<Button-3>", show_context_menu)
        dialog.bind("<Escape>", context_menu.unpost)
        populate_tree()

        def apply_filters():
            """Apply active filter rules and rescan folders."""
            active_rules = [
                item["rule"] for item in temp_filters if item.get("active", True)
            ]
            self._log(f"Applying active filters: {active_rules}")
            with self.state_lock:
                self.files_a.clear()
                self.files_b.clear()
                self._update_status("A", self.files_a)
                self._update_status("B", self.files_b)
                if self.tree_a:
                    self._batch_populate_tree(self.tree_a, {})
                if self.tree_b:
                    self._batch_populate_tree(self.tree_b, {})

        def run_scans_and_compare():
            """Run folder scans with active filters and compare results."""
            active_rules = [
                item["rule"] for item in temp_filters if item.get("active", True)
            ]
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
            for t in scan_threads:
                t.join()
            self.root.after(0, self.compare_folders)

        def save_and_close():
            """Save filter rules and close dialog."""
            with self.state_lock:
                self.filter_rules = temp_filters
                self.filter_rules.sort(key=lambda item: item["rule"])
            apply_filters()
            dialog.destroy()

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

        self._center_dialog(dialog)
        self.root.wait_window(dialog)

    def _show_options_dialog(self):
        """Show the options dialog for configuring application settings."""
        OptionsDialog(self.root, self)

    def _update_tree_fonts(self):
        """Update tree view fonts based on current options."""
        font_family = self.options["font_family"]
        font_size = self.options["font_size"]
        style = ttk.Style()
        style.configure("TTreeview", font=(font_family, font_size))
        style.configure("TTreeview.Heading", font=(font_family, font_size, "bold"))
        if self.tree_a:
            self.tree_a.tag_configure("custom_font", font=(font_family, font_size))
        if self.tree_b:
            self.tree_b.tag_configure("custom_font", font=(font_family, font_size))
        self._save_config()

    def _refresh_tree_views_after_font_change(self):
        """Refresh tree views after font change to apply new font settings."""
        if self.tree_a and self.folder_a.get():
            folder = self.folder_a.get()
            self._populate_single_panel(
                "A", folder, active_rules=self._get_active_filters()
            )
        if self.tree_b and self.folder_b.get():
            folder = self.folder_b.get()
            self._populate_single_panel(
                "B", folder, active_rules=self._get_active_filters()
            )
        if self.files_a and self.files_b:
            self.compare_folders()

    def _create_filter_tree(self, parent: Union[tk.Toplevel, tk.Widget]) -> tuple:
        """Create a tree view widget for displaying filter rules."""
        tree_frame = ttk.Frame(parent)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        filter_tree = ttk.Treeview(
            tree_frame, columns=("check", "rule"), show="headings"
        )
        filter_tree.heading("check", text="")

        # Get display scaling factor
        scale_factor = GScaling.get_scale_factor(parent)

        # Scale column widths based on display DPI
        check_width = int(40 * scale_factor)
        filter_tree.column("check", width=check_width, anchor="center", stretch=False)
        filter_tree.heading("rule", text="Filter Rule")
        filter_tree.column("rule", anchor="w", stretch=True)

        # Scale row height based on display DPI (default is ~20-24px)
        row_height = int(24 * scale_factor)
        style = ttk.Style()
        style.configure("FilterTree.Treeview", rowheight=row_height)
        filter_tree.configure(style="FilterTree.Treeview")

        filter_tree.grid(row=0, column=0, sticky=tk.NSEW)
        scrollbar = ttk.Scrollbar(tree_frame, command=filter_tree.yview)
        scrollbar.grid(row=0, column=1, sticky=tk.NS)
        filter_tree.config(yscrollcommand=scrollbar.set)
        return tree_frame, filter_tree

    def _get_active_filters(self) -> list:
        """Get list of active filter rules."""
        with self.state_lock:
            return [
                item["rule"]
                for item in self.filter_rules
                if isinstance(item, dict) and item.get("active", True)
            ]

    def _on_tree_click(self, event: tk.Event):
        """Handle tree click to toggle sync state for files."""
        widget = event.widget
        if not isinstance(widget, ttk.Treeview):
            return
        tree = cast(ttk.Treeview, widget)
        region = tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        column = tree.identify
        column = tree.identify_column(event.x)
        if column != "#1":  # 'sync' column
            return
        item_id = tree.identify_row(event.y)
        if not item_id:
            return
        rel_path = self._get_relative_path(tree, item_id)
        if rel_path is None:
            return
        # Determine the new state and start the recursive toggle.
        with self.state_lock:
            new_state = not self.sync_states.get(rel_path, False)
            self._toggle_sync_state_recursive(tree, item_id, new_state, rel_path)

    def _toggle_sync_state_recursive(
        self, tree: ttk.Treeview, item_id: str, new_state: bool, current_path: str
    ):
        """Recursively toggle the sync state for an item and its descendants."""
        with self.state_lock:
            self.sync_states[current_path] = new_state
            char = CHECKED_CHAR if new_state else UNCHECKED_CHAR
            current_values = list(tree.item(item_id, "values"))
            current_values[0] = char
            tree.item(item_id, values=tuple(current_values))
            # Recursively apply to children.
            for child_id in tree.get_children(item_id):
                child_text = tree.item(child_id, "text")
                child_path = _posix_join(current_path, child_text)
                self._toggle_sync_state_recursive(tree, child_id, new_state, child_path)

    def _on_tree_right_click(self, event: tk.Event):
        """Show context menu on right-click."""
        widget = event.widget
        if not isinstance(widget, ttk.Treeview):
            return
        tree = cast(ttk.Treeview, widget)
        item_id = tree.identify_row(event.y)
        self._context_menu_tree = tree
        self._context_menu_item_id = item_id
        if item_id:
            if item_id not in tree.selection():
                tree.selection_set(item_id)
                tree.focus(item_id)
        else:
            tree.selection_remove(tree.selection())
            tree.set("")
        selected_items = tree.selection()
        if len(selected_items) == 1:
            item_id = selected_items[0]
            rel_path = self._get_relative_path(tree, item_id)
            if not rel_path:
                return
            files_dict = self.files_a if tree is self.tree_a else self.files_b
            item_info = files_dict.get(rel_path)
        else:
            item_info = None
            rel_path = None
        if item_info and item_info.get("type") == "file":
            self.tree_context_menu.entryconfig("Open...", state="normal")
            self.tree_context_menu.entryconfig("Open Folder", state="normal")
        else:
            self.tree_context_menu.entryconfig("Open...", state="disabled")
            self.tree_context_menu.entryconfig(
                "Open Folder", state="normal" if item_id else "disabled"
            )
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
            self.tree_context_menu.entryconfig("Sync  ▶", state="disabled")
            self.tree_context_menu.entryconfig("◀  Sync", state="disabled")
        if item_id:
            self.tree_context_menu.entryconfig("Delete", state="normal")
        else:
            self.tree_context_menu.entryconfig("Delete", state="disabled")
        if self.sync_states:
            self.tree_context_menu.entryconfig("Select All", state="normal")
            self.tree_context_menu.entryconfig("Deselect All", state="normal")
        else:
            self.tree_context_menu.entryconfig("Select All", state="disabled")
            self.tree_context_menu.entryconfig("Deselect All", state="disabled")
        if tree.get_children():
            self.tree_context_menu.entryconfig("Expand All", state="normal")
            self.tree_context_menu.entryconfig("Collapse All", state="normal")
        else:
            self.tree_context_menu.entryconfig("Expand All", state="disabled")
            self.tree_context_menu.entryconfig("Collapse All", state="disabled")
        selected_a = self.tree_a.selection() if self.tree_a else ()
        selected_b = self.tree_b.selection() if self.tree_b else ()
        if not tree.get_children():
            return
        self.tree_context_menu.tk_popup(event.x_root, event.y_root)
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
        """Adjust the width of a single column to fit its content."""
        if not tree:
            return
        try:
            font_family = self.options["font_family"]
            font_size = self.options["font_size"]
            font = tkfont.Font(family=font_family, size=font_size)
            max_width = font.measure(tree.heading(column_id, "text"))

            def find_max_width_recursive(item_id=""):
                """Recursively find maximum width for column content."""
                nonlocal max_width
                for child_id in tree.get_children(item_id):
                    if column_id == "#0":
                        cell_value = tree.item(child_id, "text")
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

    def _sync_items(self, rel_paths: list[str], direction: str):
        """Handle the synchronization of multiple files or directories."""

        def sync_thread():
            """Thread function to sync selected items asynchronously."""
            try:
                if direction == "a_to_b":
                    with self.state_lock:
                        source_files_dict = self.files_a.copy()
                        target_path = self.folder_b.get()
                        source_ssh_config = self._get_ssh_config_for_panel("A")
                        target_ssh_config = self._get_ssh_config_for_panel("B")
                else:
                    with self.state_lock:
                        source_files_dict = self.files_b.copy()
                        target_path = self.folder_a.get()
                        source_ssh_config = self._get_ssh_config_for_panel("B")
                        target_ssh_config = self._get_ssh_config_for_panel("A")
                files_to_copy = []
                for rel_path in rel_paths:
                    source_item = source_files_dict.get(rel_path)
                    if not source_item:
                        continue
                    if source_item.get("type") == "file":
                        files_to_copy.append(rel_path)
                    else:
                        dir_prefix = rel_path.rstrip(os.sep).replace(os.sep, "/") + "/"
                        for p, info in source_files_dict.items():
                            if info.get("type") != "file":
                                continue
                            if p.replace(os.sep, "/").startswith(dir_prefix):
                                files_to_copy.append(p)
                files_to_copy = sorted(list(set(files_to_copy)))
                with self.state_lock:
                    target_files_dict = (
                        self.files_b if direction == "a_to_b" else self.files_a
                    ).copy()
                self.root.after(
                    0,
                    self._start_progress,
                    None,
                    len(files_to_copy),
                    f"Syncing {len(files_to_copy)} items...",
                )
                self._perform_sync(
                    files_to_copy,
                    source_files_dict,
                    target_path,
                    source_ssh_config,
                    target_ssh_config,
                    target_files_dict,
                    direction,
                )
                self._log("Successfully synced items. Refreshing view...")
                self.root.after(0, self.compare_folders)
            except Exception as e:
                self._log(f"Error syncing items: {e}")
                messagebox.showerror("Sync Error", f"Failed to sync items: {e}")
            finally:
                self.root.after(0, self._stop_progress)

        threading.Thread(target=sync_thread, daemon=True).start()

    def _select_all(self):
        """Select all different/new items."""
        tree = getattr(self, "_context_menu_tree", None)
        if not tree:
            tree = self.root.focus_get()
        if not isinstance(tree, ttk.Treeview) or tree not in (self.tree_a, self.tree_b):
            return
        diff_statuses = {"Different", "Only in A", "Only in B"}

        def traverse_and_select(item_id=""):
            """Recursively traverse tree and select items with different status."""
            for child_id in tree.get_children(item_id):
                status_values = tree.item(child_id, "values")
                status = status_values[3] if len(status_values) > 3 else ""
                if status in diff_statuses:
                    rel_path = self._get_relative_path(tree, child_id)
                    if rel_path is not None:
                        with self.state_lock:
                            self.sync_states[rel_path] = True
                        current_values = list(status_values)
                        current_values[0] = CHECKED_CHAR
                        tree.item(child_id, values=tuple(current_values))
                if tree.get_children(child_id):
                    traverse_and_select(child_id)

        traverse_and_select()

    def _deselect_all(self):
        """Deselect all items in the tree."""
        tree = getattr(self, "_context_menu_tree", None)
        if not tree:
            tree = self.root.focus_get()
        if not isinstance(tree, ttk.Treeview) or tree not in (self.tree_a, self.tree_b):
            return

        def traverse_and_deselect(item_id=""):
            """Recursively traverse tree and deselect all items."""
            for child_id in tree.get_children(item_id):
                rel_path = self._get_relative_path(tree, child_id)
                if rel_path is not None:
                    with self.state_lock:
                        if rel_path in self.sync_states:
                            self.sync_states[rel_path] = False
                    current_values = list(tree.item(child_id, "values"))
                    current_values[0] = UNCHECKED_CHAR
                    tree.item(child_id, values=tuple(current_values))
                if tree.get_children(child_id):
                    traverse_and_deselect(child_id)

        traverse_and_deselect()

    def _expand_all(self):
        """Expand all items in the tree."""
        tree = getattr(self, "_context_menu_tree", None)
        if not tree:
            tree = self.root.focus_get()
        if not isinstance(tree, ttk.Treeview) or tree not in (self.tree_a, self.tree_b):
            return

        def expand_recursive(item_id):
            """Recursively expand all tree items."""
            tree.item(item_id, open=True)
            for child in tree.get_children(item_id):
                expand_recursive(child)

        for item in tree.get_children():
            expand_recursive(item)

    def _collapse_all(self):
        """Collapse all items in the tree."""
        tree = getattr(self, "_context_menu_tree", None)
        if not tree:
            tree = self.root.focus_get()
        if not isinstance(tree, ttk.Treeview) or tree not in (self.tree_a, self.tree_b):
            return

        def collapse_recursive(item_id):
            """Recursively collapse all tree items."""
            tree.item(item_id, open=False)
            for child in tree.get_children(item_id):
                collapse_recursive(child)

        for item in tree.get_children():
            collapse_recursive(item)

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
        path_a = self._get_full_path_for_item(self.tree_a, selected_a[0], "A")
        path_b = self._get_full_path_for_item(self.tree_b, selected_b[0], "B")
        if not path_a or not path_b:
            messagebox.showerror(
                "Error", "Could not determine file paths for comparison."
            )
            return
        rel_path_a = self._get_relative_path(self.tree_a, selected_a[0])
        rel_path_b = self._get_relative_path(self.tree_b, selected_b[0])
        is_file_a = self.files_a.get(rel_path_a, {}).get("type") == "file"
        is_file_b = self.files_b.get(rel_path_b, {}).get("type") == "file"
        if not (is_file_a and is_file_b):
            messagebox.showwarning(
                "Selection Error", "Please select files, not directories, to compare."
            )
            return
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
            self._clear_context_menu_state()
            return
        try:
            local_path = self._get_full_path_for_item(tree, item_id)
            if not local_path:
                return
            if sys.platform == "win32":
                os.startfile(local_path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", local_path])
            else:
                process = subprocess.Popen(
                    ["xdg-open", local_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                stdout, stderr = process.communicate()
                if process.returncode != 0:
                    messagebox.showwarning(
                        "Warning", f"Could not open file: {stderr.decode().strip()}"
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
            folder_path = (
                item_info.get("full_path")
                if item_info.get("type") == "dir"
                else os.path.dirname(item_info.get("full_path", ""))
            )
            if not folder_path:
                return
            if sys.platform == "win32":
                os.startfile(folder_path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder_path])
            else:
                process = subprocess.Popen(
                    ["xdg-open", folder_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                stdout, stderr = process.communicate()
                if process.returncode != 0:
                    messagebox.showwarning(
                        "Warning", f"Could not open folder: {stderr.decode().strip()}"
                    )
        except Exception as e:
            messagebox.showerror("Error", f"Could not open folder: {e}")
        finally:
            self._clear_context_menu_state()

    def _delete_selected_items(self):
        """Delete the selected files or directories."""
        tree = self._context_menu_tree
        if tree is None:
            self._clear_context_menu_state()
            return
        selected_items = tree.selection()
        if not selected_items:
            return
        count = len(selected_items)
        msg = (
            f"Are you sure you want to permanently delete these {count} items?"
            if count > 1
            else "Are you sure you want to permanently delete the selected item?"
        )
        if not messagebox.askyesno("Confirm Delete", msg):
            return
        panel = "A" if tree is self.tree_a else "B"
        ssh_config = self._get_ssh_config_for_panel(panel)
        files_dict = self.files_a if panel == "A" else self.files_b
        base_folder = self.folder_a.get() if panel == "A" else self.folder_b.get()
        items_to_delete = []
        for item_id in selected_items:
            rel_path = self._get_relative_path(tree, item_id)
            if not rel_path:
                continue
            item_info = files_dict.get(rel_path)
            full_path = item_info.get("full_path") if item_info else None
            if not full_path:
                if ssh_config is not None:
                    full_path = _posix_join(base_folder, rel_path)
                else:
                    full_path = os.path.join(base_folder, rel_path.replace("/", os.sep))
            is_dir = False
            if item_info:
                is_dir = item_info.get("type") == "dir"
            elif ssh_config is None and os.path.isdir(full_path):
                is_dir = True
            items_to_delete.append((full_path, is_dir))

        def delete_and_refresh():
            """Delete items and refresh the panel view."""
            try:
                if ssh_config is not None:
                    with self.connection_manager.get_connection(
                        ssh_config["host"],
                        ssh_config["user"],
                        ssh_config["password"],
                        ssh_config["port"],
                    ) as ssh_client:
                        for full_path, _ in items_to_delete:
                            self._log(f"Deleting item: {full_path}")
                            command = f"rm -rf {_posix_quote(full_path)}"
                            stdin, stdout, stderr = ssh_client.exec_command(command)
                            error = stderr.read().decode()
                            if error:
                                self._log(f"Error deleting {full_path}: {error}")
                else:
                    for full_path, is_dir in items_to_delete:
                        self._log(f"Deleting item: {full_path}")
                        if is_dir:
                            if os.path.exists(full_path):
                                shutil.rmtree(full_path)
                        else:
                            if os.path.exists(full_path):
                                os.remove(full_path)
                self._log(f"Successfully deleted. Refreshing panel {panel}.")
                self._populate_single_panel(
                    panel, self.folder_a.get() if panel == "A" else self.folder_b.get()
                )
            except Exception as e:
                messagebox.showerror("Error", f"Failed to delete items: {e}")
            finally:
                self._clear_context_menu_state()

        threading.Thread(target=delete_and_refresh, daemon=True).start()

    # ==========================================================================
    # UTILITY METHODS
    # ==========================================================================
    def _on_escape_key(self, event=None):
        """Handle Escape key press to clear selection and focus from trees."""
        self._clear_context_menu_state()
        self.tree_context_menu.unpost()
        widget = self.root.focus_get()
        if isinstance(widget, ttk.Treeview) and widget in (self.tree_a, self.tree_b):
            selection = widget.selection()
            if selection:
                widget.selection_remove(selection)
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
            except OSError as e:
                self._log(f"Error cleaning up temporary file {temp_file_path}: {e}")

    def _is_temporary_path(self, path: str) -> bool:
        """Check if a path is a temporary file or directory."""
        if not path:
            return False
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
        if "tmp" in path_normalized and (
            path_normalized.startswith("/tmp/")
            or path_normalized.startswith("\\tmp\\")
            or "tmp" in os.path.basename(path_normalized)
        ):
            return True
        return False

    def _get_relative_path(
        self, tree: Optional[ttk.Treeview], item_id: str
    ) -> Optional[str]:
        """Construct relative path for item."""
        if tree is None or item_id is None:
            return None
        path_parts = []
        while item_id:
            text = tree.item(item_id, "text")
            path_parts.insert(0, text)
            item_id = tree.parent(item_id)
        if path_parts:
            return "/".join(path_parts)
        return None

    def _get_full_path_for_item(
        self, tree: Optional[ttk.Treeview], item_id: str, panel: Optional[str] = None
    ) -> Optional[str]:
        """Get the full, possibly temporary, path for a tree item."""
        if tree is None:
            return None
        rel_path = self._get_relative_path(tree, item_id)
        if not rel_path:
            return None
        if panel is None:
            panel = "A" if tree is self.tree_a else "B"
        ssh_config = self._get_ssh_config_for_panel(panel)
        files_dict = self.files_a if panel == "A" else self.files_b
        full_path = files_dict.get(rel_path, {}).get("full_path")
        if not full_path:
            return None
        if ssh_config is not None:
            try:
                with self.connection_manager.get_connection(
                    ssh_config["host"],
                    ssh_config["user"],
                    ssh_config["password"],
                    ssh_config["port"],
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

    def _update_status(self, panel: str, files: dict):
        """Update the status bar text."""
        num_dirs = sum(1 for f in files.values() if f.get("type") == "dir")
        num_files = sum(1 for f in files.values() if f.get("type") == "file")
        total_size = sum(f.get("size", 0) for f in files.values())
        status_text = f"Folders: {num_dirs}, Files: {num_files}, Size: {self._format_size(total_size)}"
        if panel == "A":
            self.status_a.set(status_text)
        else:
            self.status_b.set(status_text)

    def _start_progress(self, panel=None, max_value=0, text=""):
        """Show the progress bar."""
        if self.status_label_a:
            self.status_label_a.grid_remove()
        if self.status_label_b:
            self.status_label_b.grid_remove()
        self.progress_bar.grid()
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
        """Update the progress bar."""
        with self._progress_lock:
            self.progress_bar.step(step)

    def _stop_progress(self):
        """Hide the progress bar."""
        self.progress_bar.stop()
        self.progress_bar.grid_remove()
        if self.status_label_a:
            self.status_label_a.grid()

    def _log(self, message: str):
        """Log message to console."""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def _format_size(self, size_bytes: Union[int, float]) -> str:
        """Format file size to be readable."""
        for unit in [" B", "KB", "MB", "GB", "TB"]:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} PB"

    def _format_time(self, timestamp: float) -> str:
        """Format timestamp to a date string."""
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

    def _center_dialog(
        self,
        dialog: tk.Toplevel,
        relative_to: Optional[Union[tk.Widget, tk.Toplevel]] = None,
    ):
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
