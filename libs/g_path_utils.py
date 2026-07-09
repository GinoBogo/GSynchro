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
    """Return a POSIX-shell-quoted version of `path` for safe exec_command use."""
    return shlex.quote(path)


def posix_join(*parts: str) -> str:
    """Join path components using POSIX semantics for remote path construction."""
    return posixpath.join(*parts)
