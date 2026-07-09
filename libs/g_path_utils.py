#!/usr/bin/env python3
"""
Path utilities for GSynchro.

Provides utility functions for handling POSIX paths and shell quoting
for safe remote command execution.

 Author: Gino Bogo
License: MIT
Version: 1.0
"""

# Standard library imports.
import posixpath
import shlex


def posix_quote(path: str) -> str:
    """
    Return a POSIX-shell-quoted version of `path` for safe exec_command use.

    This function ensures that file paths with special characters (spaces,
    parentheses, etc.) are properly escaped for safe use in shell commands
    when executing commands on remote systems via SSH.

    The function uses shlex.quote() which wraps the path in single quotes
    and escapes any embedded single quotes, making it safe for shell execution.

    Args:
        path (str): The file path to be shell-quoted.

    Returns:
        str: A shell-escaped version of the input path.

    Example:
        >>> posix_quote("/path/with spaces/file.txt")
        "'/path/with spaces/file.txt'"

        >>> posix_quote("/path/with'quote/file.txt")
        "'/path/with'\"'\"'quote/file.txt'"

    Note:
        This function is specifically designed for POSIX-compliant systems
        and should be used when executing commands on remote Unix-like systems.
    """
    return shlex.quote(path)


def posix_join(*parts: str) -> str:
    """
    Join path components using POSIX semantics for remote path construction.

    This function combines multiple path components into a single path using
    POSIX path separators ('/'), which is appropriate for Unix-like systems.
    It handles empty parts and normalizes the resulting path.

    This is particularly useful when constructing paths for remote systems
    where POSIX path semantics are expected, regardless of the local system's
    path conventions (e.g., Windows vs. Unix).

    Args:
        *parts (str): Variable number of path components to join.

    Returns:
        str: A path constructed by joining the components with '/' separators.

    Example:
        >>> posix_join("/home", "user", "documents")
        '/home/user/documents'

        >>> posix_join("home/user", "documents", "file.txt")
        'home/user/documents/file.txt'

        >>> posix_join("/home/user/", "/documents/")
        '/home/user/documents'

    Note:
        This function uses posixpath.join() internally, which automatically
        normalizes the path by removing duplicate separators and handling
        parent directory references ('..').
    """
    return posixpath.join(*parts)
