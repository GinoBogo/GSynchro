#!/usr/bin/env python3
"""
File comparison utilities for GSynchro.

Handles the logic for comparing file and directory structures
using various methods (MD5 hash, block comparison).

 Author: Gino Bogo
License: MIT
Version: 1.0
"""

# Standard library imports.
import hashlib
from contextlib import contextmanager
from typing import Optional, Iterator

# Third-party library imports.
import paramiko

# Personal library imports.
from libs.g_path_utils import posix_quote


CHUNK_SIZE = 4096


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
                command = cmd_template.format(posix_quote(file_info["full_path"]))
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
