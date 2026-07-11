#!/usr/bin/env python3
"""
File comparison utilities for GSynchro.

Handles the logic for comparing file and directory structures
using various methods (MD5 hash, block comparison).

 Author: Gino Bogo
License: MIT
Version: 1.1
"""

# Standard library imports.
import hashlib
from contextlib import contextmanager
from typing import Optional, Iterator, BinaryIO

# Third-party library imports.
import paramiko

# Personal library imports.
from libs.g_path_utils import posix_quote


DEFAULT_CHUNK_SIZE = 4096


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
        # Cache SFTP sessions per SSH client to avoid repeated setup/teardown.
        self._sftp_sessions: dict = {}

    def _validate_file_info(self, file_info: dict, context: str) -> None:
        """
        Validate that a file_info dictionary contains required keys.

        Args:
            file_info (dict): Dictionary to validate.
            context (str): Context string for error messages.

        Raises:
            KeyError: If required keys are missing.
        """
        if not isinstance(file_info, dict):
            raise TypeError(f"{context}: expected dict, got {type(file_info).__name__}")
        if "full_path" not in file_info:
            raise KeyError(f"{context}: missing required key 'full_path'")
        if "type" not in file_info:
            raise KeyError(f"{context}: missing required key 'type'")

    def _get_sftp(self, ssh_client: paramiko.SSHClient) -> paramiko.SFTPClient:
        """
        Return a cached SFTP session for the given SSH client.

        Creates and caches the SFTP session on first use. Reuses it for
        subsequent calls, eliminating repeated SFTP subsystem handshakes.

        Args:
            ssh_client (paramiko.SSHClient): SSH client for remote access.

        Returns:
            paramiko.SFTPClient: Cached SFTP client.

        Raises:
            ConnectionError: If SSH client is not connected or transport is inactive.
        """
        if not ssh_client:
            raise ConnectionError("SSH client is not connected.")

        client_id = id(ssh_client)
        if client_id not in self._sftp_sessions:
            transport = ssh_client.get_transport()
            if not transport or not transport.is_active():
                raise ConnectionError("SSH client transport is not active.")
            self._sftp_sessions[client_id] = ssh_client.open_sftp()

        return self._sftp_sessions[client_id]

    def close_sftp_sessions(self) -> None:
        """
        Close all cached SFTP sessions.

        Call this when comparison operations are complete to release
        server-side resources cleanly.
        """
        for sftp in self._sftp_sessions.values():
            try:
                sftp.close()
            except Exception as e:
                self.log(f"Warning: error closing SFTP session: {e}")
        self._sftp_sessions.clear()

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
                               "Only in A", "Only in B", or "Error"
                - color (str): Color code for UI display ("black", "orange",
                              "green", "blue", "red", or "gray")
        """
        if file_a and file_b:
            try:
                self._validate_file_info(file_a, "file_a")
                self._validate_file_info(file_b, "file_b")
            except (TypeError, KeyError) as e:
                self.log(f"Error: invalid file info: {e}")
                return "Error", "gray"

            is_a_file = file_a.get("type") == "file"
            is_b_file = file_b.get("type") == "file"
            if is_a_file and not is_b_file:
                return "Conflict", "black"
            if not is_a_file and is_b_file:
                return "Conflict", "black"
            if file_a.get("size") != file_b.get("size"):
                return "Different", "orange"

            if "size" in file_a and "size" in file_b:
                with self.state_lock:
                    # Snapshot the compare method to avoid race conditions.
                    compare_method = self.options.get("compare_method", "block")
                    chunk_size = self.options.get("chunk_size", DEFAULT_CHUNK_SIZE)

                    if compare_method == "md5":
                        try:
                            hash_a = self._get_md5_hash(file_a, use_ssh_a, ssh_client_a)
                            hash_b = self._get_md5_hash(file_b, use_ssh_b, ssh_client_b)
                            if hash_a != hash_b:
                                return "Different", "orange"
                        except (
                            ConnectionError,
                            IOError,
                            FileNotFoundError,
                            OSError,
                        ) as e:
                            self.log(f"Error during MD5 comparison: {e}")
                            return "Error", "gray"
                        except Exception as e:
                            self.log(f"Unexpected error during MD5 comparison: {e}")
                            return "Error", "gray"
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
                                    file_a_handle, file_b_handle, chunk_size
                                ):
                                    return "Different", "orange"
                        except (
                            ConnectionError,
                            IOError,
                            FileNotFoundError,
                            OSError,
                        ) as e:
                            self.log(f"Error during block file comparison: {e}")
                            return "Error", "gray"
                        except Exception as e:
                            self.log(
                                f"Unexpected error during block file comparison: {e}"
                            )
                            return "Error", "gray"
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
        (md5sum or md5) to calculate the hash efficiently. Falls back to SFTP-based
        local hashing if remote commands are unavailable.

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
        self._validate_file_info(file_info, "_get_md5_hash")

        if use_ssh:
            if not ssh_client:
                raise ConnectionError("SSH client not connected for MD5 calculation.")

            # Try remote command execution first (most efficient for large files).
            for cmd_template in ["md5sum {}", "md5 -q {}"]:
                command = cmd_template.format(posix_quote(file_info["full_path"]))
                stdin, stdout, stderr = ssh_client.exec_command(command)
                exit_status = stdout.channel.recv_exit_status()
                if exit_status == 0:
                    output = stdout.read().decode().strip()
                    return output.split()[0]

            # Fallback: download via cached SFTP and hash locally.
            self.log(
                f"Remote md5sum/md5 unavailable for {file_info['full_path']}. "
                "Falling back to SFTP-based local hashing."
            )
            try:
                sftp = self._get_sftp(ssh_client)
                hasher = hashlib.md5()
                chunk_size = self.options.get("chunk_size", DEFAULT_CHUNK_SIZE)
                with sftp.open(file_info["full_path"], "rb") as remote_f:
                    while chunk := remote_f.read(chunk_size):
                        hasher.update(chunk)
                return hasher.hexdigest()
            except Exception as e:
                error_msg = (
                    f"Could not calculate MD5 for {file_info['full_path']} via SSH: {e}"
                )
                self.log(error_msg)
                raise IOError(error_msg) from e
        else:
            hasher = hashlib.md5()
            chunk_size = self.options.get("chunk_size", DEFAULT_CHUNK_SIZE)
            try:
                with open(file_info["full_path"], "rb") as f:
                    while chunk := f.read(chunk_size):
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
    ) -> Iterator[BinaryIO]:
        """
        Context manager to open file handle for reading, local or via SSH.

        Uses a cached SFTP session for remote files to avoid repeated
        SFTP subsystem setup/teardown overhead.

        Args:
            file_info (dict): Dictionary containing file information including 'full_path'.
            use_ssh (bool): Whether to access the file via SSH.
            ssh_client (paramiko.SSHClient or None): SSH client for remote access.

        Yields:
            BinaryIO: A file handle that can be read from.

        Raises:
            ConnectionError: If SSH is required but client is not connected or transport is inactive.
        """
        self._validate_file_info(file_info, "_open_file_handle")

        if use_ssh:
            sftp = self._get_sftp(ssh_client)
            file_handle = sftp.open(file_info["full_path"], "rb")
            try:
                yield file_handle
            finally:
                file_handle.close()
                # NOTE: SFTP session is NOT closed here; it is reused via _get_sftp().
        else:
            with open(file_info["full_path"], "rb") as file_handle:
                yield file_handle

    def _are_chunks_identical(
        self,
        file_a_handle: BinaryIO,
        file_b_handle: BinaryIO,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> bool:
        """
        Compare two file handles chunk by chunk to determine if files are identical.

        This method reads both files in chunks and compares them sequentially.
        It stops as soon as a difference is found, making it efficient for large files
        where only the beginning differs.

        Args:
            file_a_handle (BinaryIO): File handle for the first file.
            file_b_handle (BinaryIO): File handle for the second file.
            chunk_size (int): Number of bytes to read per chunk. Defaults to 4096.

        Returns:
            bool: True if files are identical, False otherwise.
        """
        while True:
            chunk_a = file_a_handle.read(chunk_size)
            chunk_b = file_b_handle.read(chunk_size)
            if chunk_a != chunk_b:
                return False
            if not chunk_a:
                return True
