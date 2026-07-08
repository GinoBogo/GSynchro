#!/usr/bin/env python3
"""
GScaling - Display Scaling Detection Utility

Provides platform-specific display DPI scaling factor detection for GUI applications.
Supports Linux (X11 Xft.dpi) and Windows (Tkinter winfo_fpixels).

 Author: Gino Bogo
License: MIT
Version: 1.0
"""

import platform
import tkinter as tk
from typing import Optional


class GScaling:
    """Utility class for detecting display scaling factor."""

    _scale_factor: Optional[float] = None

    @classmethod
    def get_scale_factor(cls, master: Optional[tk.Misc] = None) -> float:
        """Get display scaling factor using platform-specific methods.

        Args:
            master: Optional parent widget for root window detection.

        Returns:
            Scale factor (e.g., 1.0 for 100%, 1.33 for 133%, 1.5 for 150%).
        """
        if cls._scale_factor is not None:
            return cls._scale_factor

        scale_factor = 1.0

        if platform.system() == "Linux":
            # Check X11 Xft.dpi (Linux X11)
            try:
                import subprocess

                result = subprocess.run(
                    ["xrdb", "-query"], capture_output=True, text=True, timeout=1
                )
                if result.returncode == 0:
                    for line in result.stdout.split("\n"):
                        if line.startswith("Xft.dpi:"):
                            try:
                                xft_dpi = float(line.split(":")[1].strip())
                                scale_factor = xft_dpi / 96.0
                                break
                            except (ValueError, IndexError):
                                pass
            except Exception:
                pass

        elif platform.system() == "Windows":
            # Try Tkinter's winfo_fpixels on Windows (more reliable)
            try:
                root = None
                if master is not None:
                    root = master.winfo_toplevel()
                if root is None:
                    try:
                        root = tk._default_root
                    except Exception:
                        pass

                if root is not None:
                    dpi = root.winfo_fpixels("1i")
                    detected_scale = dpi / 96.0
                    if detected_scale > 1.05 or detected_scale < 0.95:
                        scale_factor = detected_scale
            except Exception:
                pass

        cls._scale_factor = scale_factor
        return cls._scale_factor

    @classmethod
    def reset_cache(cls) -> None:
        """Reset the cached scale factor. Useful for testing or dynamic DPI changes."""
        cls._scale_factor = None
