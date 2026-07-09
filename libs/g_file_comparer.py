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
        """
        Initialize Comparer with logger, connection manager, and UI components.

        Args:
            logger_func (callable): Function to use for logging messages.
            connection_manager: Manager for SSH connections.
            root_widget: Root widget for UI updates.
            options (dict): Configuration options for comparison.
            state_lock: Thread lock for synchronizing state access.
        """
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
        """
        Compare two files and return status and color tuple.

        This method performs a comprehensive comparison of two files, checking
        their types, sizes, and content using either MD5 hash or block-by-block
        comparison based on the configured method.

        Args:
            file_a (dict or None): Information about file A, or None if doesn't exist.
            file_b (dict or None): Information about file B, or None if doesn't exist.
            use_ssh_a (bool): Whether file A is accessed via SSH.
            use_ssh_b (bool): Whether file B is accessed via SSH.
            ssh_client_a (paramiko.SSHClient or None): SSH client for file A.
            ssh_client_b (paramiko.SSHClient or None): SSH client for file B.

        Returns:
            tuple: A tuple containing:
                - status (str): One of "Conflict", "Different", "Identical",
                               "Only in A", or "Only in B"
                - color (str): Color code for UI display ("black", "orange",
                              "green", "blue", or "red")
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
        """
        Calculate MD5 hash of a file, locally or via SSH.

        This method computes the MD5 hash of a file either locally or on a remote
        system via SSH. For remote files, it attempts to use system commands
        (md5sum or md5) to calculate the hash efficiently.

        Args:
            file_info (dict): Dictionary containing file information including 'full_path'.
            use_ssh (bool): Whether to access the file via SSH.
            ssh_client (paramiko.SSHClient or None): SSH client for remote access.

        Returns:
            str: The MD5 hash of the file as a hexadecimal string.

        Raises:
            ConnectionError: If SSH is required but client is not connected.
            IOError: If unable to calculate MD5 hash on remote system.
            FileNotFoundError: If the local file does not exist.
        """
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
        """
        Context manager to open file handle for reading, local or via SSH.

        This context manager provides a unified interface for opening file handles
        whether the file is local or remote via SSH. It ensures proper resource
        cleanup regardless of how the file is accessed.

        Args:
            file_info (dict): Dictionary containing file information including 'full_path'.
            use_ssh (bool): Whether to access the file via SSH.
            ssh_client (paramiko.SSHClient or None): SSH client for remote access.

        Yields:
            file-like object: A file handle that can be read from.

        Raises:
            ConnectionError: If SSH is required but client is not connected or transport is inactive.
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
        """
        Compare two file handles chunk by chunk to determine if files are identical.

        This method reads both files in chunks and compares them sequentially.
        It stops as soon as a difference is found, making it efficient for large files
        where only the beginning differs.

        Args:
            file_a_handle: File handle for the first file (must support read() method).
            file_b_handle: File handle for the second file (must support read() method).

        Returns:
            bool: True if files are identical, False otherwise.
        """
        while True:
            chunk_a = file_a_handle.read(CHUNK_SIZE)
            chunk_b = file_b_handle.read(CHUNK_SIZE)
            if chunk_a != chunk_b:
                return False
            if not chunk_a:
                return True
