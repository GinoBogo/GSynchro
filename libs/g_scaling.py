#!/usr/bin/env python3
"""
GScaling - Display Scaling Detection Utility

Provides platform-specific display DPI scaling factor detection for GUI applications.

Detection strategy per platform:
  - Windows : Process DPI-awareness enabling + per-monitor/system DPI query via
              ctypes (GetDpiForWindow / GetDpiForSystem), falling back to
              Tk's winfo_fpixels.
  - Linux   : Environment variables (GDK_SCALE, QT_SCALE_FACTOR), GNOME gsettings
              integer scaling, X11 Xft.dpi (via xrdb), falling back to
              Tk's winfo_fpixels. Works best-effort under both X11 and Wayland.
  - macOS   : system_profiler Retina detection, falling back to the documented
              default of 1.0 (Tk/Cocoa already renders Retina displays natively
              at the point level, so 1.0 is a safe assumption for widget layout).

The module never raises: any detection failure is caught, logged at DEBUG level,
and the chain simply moves on to the next method, ultimately defaulting to 1.0
("no scaling detected") if nothing else succeeds.

 Author: Gino Bogo
License: MIT
Version: 2.0
"""

import logging
import os
import platform
import subprocess
import tkinter as tk
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_DPI = 96.0
SUBPROCESS_TIMEOUT = 1.0


class GScaling:
    """Utility class for detecting display scaling factor.

    Backward compatible with the original API (get_scale_factor, reset_cache).
    Adds: enable_windows_dpi_awareness(), get_detection_info(), and a more
    robust, multi-method detection chain per platform.
    """

    _scale_factor: Optional[float] = None
    _detection_method: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    @classmethod
    def enable_windows_dpi_awareness(cls) -> bool:
        """Declare this process as DPI-aware on Windows.

        MUST be called as early as possible, before any Tkinter root window
        is created (ideally the first lines of your main script). Without
        this, Windows will virtualize/bitmap-stretch the app on HiDPI
        displays and DPI queries will report a misleading 96 DPI regardless
        of the real scale factor.

        Safe to call on any platform: it is a no-op (returns False) outside
        of Windows, and never raises.

        Returns:
            True if DPI awareness was successfully set, False otherwise.
        """
        if platform.system() != "Windows":
            return False

        try:
            import ctypes

            # Try Per-Monitor v2 awareness first (Windows 10 1703+).
            try:
                DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
                if ctypes.windll.user32.SetProcessDpiAwarenessContext(
                    DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
                ):
                    logger.debug(
                        "DPI awareness set via SetProcessDpiAwarenessContext (per-monitor v2)"
                    )
                    return True
            except (AttributeError, OSError):
                pass

            # Fall back to Per-Monitor v1 (Windows 8.1+).
            try:
                result = ctypes.windll.shcore.SetProcessDpiAwareness(2)
                if result == 0:  # S_OK
                    logger.debug(
                        "DPI awareness set via SetProcessDpiAwareness(2) (per-monitor v1)"
                    )
                    return True
            except (AttributeError, OSError):
                pass

            # Fall back to system-DPI-aware (Vista+).
            try:
                if ctypes.windll.user32.SetProcessDPIAware():
                    logger.debug(
                        "DPI awareness set via SetProcessDPIAware (system-wide)"
                    )
                    return True
            except (AttributeError, OSError):
                pass

        except Exception as e:
            logger.debug("Failed to enable Windows DPI awareness: %s", e)

        return False

    @classmethod
    def get_scale_factor(
        cls, master: Optional[tk.Misc] = None, force_refresh: bool = False
    ) -> float:
        """Get display scaling factor using platform-specific methods.

        Args:
            master: Optional parent widget, used for per-window/per-monitor
                detection where supported (e.g. Windows GetDpiForWindow).
            force_refresh: If True, ignore any cached value and re-detect.
                Useful after a monitor change or when the window has been
                moved to a different display.

        Returns:
            Scale factor (e.g., 1.0 for 100%, 1.33 for 133%, 1.5 for 150%).
            Always returns a positive float; falls back to 1.0 if detection
            fails on every method attempted.
        """
        if cls._scale_factor is not None and not force_refresh:
            return cls._scale_factor

        system = platform.system()
        detected: Optional[Tuple[float, str]] = None

        if system == "Windows":
            detected = cls._detect_windows(master)
        elif system == "Linux":
            detected = cls._detect_linux(master)
        elif system == "Darwin":
            detected = cls._detect_macos(master)

        if detected is None:
            scale_factor, method = 1.0, "default (no detection method succeeded)"
        else:
            scale_factor, method = detected

        # Sanity-guard against garbage/absurd values from a flaky detection method.
        if not (0.5 <= scale_factor <= 4.0):
            logger.debug(
                "Detected scale factor %.3f via %s is out of sane bounds, resetting to 1.0",
                scale_factor,
                method,
            )
            scale_factor, method = 1.0, "default (out-of-range value discarded)"

        cls._scale_factor = scale_factor
        cls._detection_method = method
        logger.debug("Scale factor resolved to %.3f via %s", scale_factor, method)
        return cls._scale_factor

    @classmethod
    def get_detection_info(cls) -> dict:
        """Return debugging info about the last detection: value + method used.

        Calls get_scale_factor() first if it hasn't been resolved yet.
        """
        if cls._scale_factor is None:
            cls.get_scale_factor()
        return {"scale_factor": cls._scale_factor, "method": cls._detection_method}

    @classmethod
    def scale_pixels(cls, value: int, master: Optional[tk.Misc] = None) -> int:
        """Scale a pixel value by the display scaling factor.

        Args:
            value: Pixel value to scale (e.g., padding, spacing, line width).
            master: Optional parent widget for scale factor detection.

        Returns:
            Scaled pixel value as integer.

        Example:
            scaled_padding = GScaling.scale_pixels(10)  # Returns 13 at 133% scaling
        """
        scale_factor = cls.get_scale_factor(master)
        return int(value * scale_factor)

    @classmethod
    def reset_cache(cls) -> None:
        """Reset the cached scale factor. Useful for testing or dynamic DPI changes
        (e.g. after a WM_DPICHANGED event on Windows, or moving the window to a
        monitor with a different scale)."""
        cls._scale_factor = None
        cls._detection_method = None

    # ------------------------------------------------------------------ #
    # Windows detection
    # ------------------------------------------------------------------ #

    @staticmethod
    def _detect_windows(master: Optional[tk.Misc]) -> Optional[Tuple[float, str]]:
        # Attempt 1: per-window DPI via ctypes (accurate per-monitor value,
        # requires DPI awareness to have been enabled beforehand and the
        # window to already be realized/mapped).
        try:
            import ctypes

            root = master.winfo_toplevel() if master is not None else tk._default_root
            if root is not None:
                root.update_idletasks()
                hwnd = root.winfo_id()
                try:
                    dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
                    if dpi:
                        return (dpi / DEFAULT_DPI, "Windows GetDpiForWindow")
                except (AttributeError, OSError):
                    pass

                try:
                    dpi = ctypes.windll.user32.GetDpiForSystem()
                    if dpi:
                        return (dpi / DEFAULT_DPI, "Windows GetDpiForSystem")
                except (AttributeError, OSError):
                    pass
        except Exception as e:
            logger.debug("Windows ctypes DPI query failed: %s", e)

        # Attempt 2: Tk's own reported DPI. Only trust this if the process
        # has already been made DPI-aware, otherwise Windows silently
        # virtualizes the app and this returns a misleading 96 DPI (1.0).
        try:
            root = master.winfo_toplevel() if master is not None else tk._default_root
            if root is not None:
                dpi = root.winfo_fpixels("1i")
                detected_scale = dpi / DEFAULT_DPI
                if detected_scale > 1.05 or detected_scale < 0.95:
                    return (detected_scale, "Tk winfo_fpixels")
        except Exception as e:
            logger.debug("Tk winfo_fpixels query failed on Windows: %s", e)

        return None

    # ------------------------------------------------------------------ #
    # Linux detection
    # ------------------------------------------------------------------ #

    @staticmethod
    def _detect_linux(master: Optional[tk.Misc]) -> Optional[Tuple[float, str]]:
        session_type = os.environ.get("XDG_SESSION_TYPE", "unknown")
        logger.debug("Linux session type: %s", session_type)

        # Attempt 1: GDK_SCALE env var (integer scaling, GTK apps/Wayland-friendly).
        gdk_scale = os.environ.get("GDK_SCALE")
        if gdk_scale:
            try:
                return (float(gdk_scale), "env GDK_SCALE")
            except ValueError:
                pass

        # Attempt 2: QT_SCALE_FACTOR env var (Qt apps, supports fractional values).
        qt_scale = os.environ.get("QT_SCALE_FACTOR")
        if qt_scale:
            try:
                return (float(qt_scale), "env QT_SCALE_FACTOR")
            except ValueError:
                pass

        # Attempt 3: GNOME integer scaling via gsettings (works under both
        # X11 and Wayland GNOME sessions).
        try:
            result = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "scaling-factor"],
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT,
            )
            if result.returncode == 0:
                # Output looks like "uint32 2" (0 means "not set / auto").
                parts = result.stdout.strip().split()
                if parts:
                    value = int(parts[-1])
                    if value > 0:
                        return (float(value), "gsettings scaling-factor")
        except (
            subprocess.SubprocessError,
            OSError,
            ValueError,
            FileNotFoundError,
        ) as e:
            logger.debug("gsettings scaling-factor query failed: %s", e)

        # Attempt 4: X11 Xft.dpi via xrdb (X11 only; will not work under
        # native Wayland, but many Wayland sessions still run XWayland with
        # a populated resource database as a compatibility shim).
        try:
            result = subprocess.run(
                ["xrdb", "-query"],
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT,
            )
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if line.startswith("Xft.dpi:"):
                        try:
                            xft_dpi = float(line.split(":")[1].strip())
                            return (xft_dpi / DEFAULT_DPI, "X11 Xft.dpi (xrdb)")
                        except (ValueError, IndexError):
                            pass
        except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
            logger.debug("xrdb query failed: %s", e)

        # Attempt 5: universal fallback via Tk's own DPI report.
        try:
            root = master.winfo_toplevel() if master is not None else tk._default_root
            if root is not None:
                dpi = root.winfo_fpixels("1i")
                detected_scale = dpi / DEFAULT_DPI
                if detected_scale > 1.05 or detected_scale < 0.95:
                    return (detected_scale, "Tk winfo_fpixels")
        except Exception as e:
            logger.debug("Tk winfo_fpixels query failed on Linux: %s", e)

        return None

    # ------------------------------------------------------------------ #
    # macOS detection
    # ------------------------------------------------------------------ #

    @staticmethod
    def _detect_macos(master: Optional[tk.Misc]) -> Optional[Tuple[float, str]]:
        # Tk/Cocoa already renders Retina displays natively at the point
        # level, so widget layout generally does not need adjustment.
        # This best-effort check is only useful if the caller needs to know
        # the actual backing pixel ratio (e.g. for scaling raster image
        # assets manually).
        try:
            result = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True,
                text=True,
                timeout=3.0,
            )
            if result.returncode == 0:
                if "Retina" in result.stdout:
                    return (2.0, "system_profiler Retina detection")
                return (1.0, "system_profiler (non-Retina)")
        except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
            logger.debug("system_profiler query failed: %s", e)

        # Default: assume Tk already handles Retina rendering transparently.
        return (1.0, "macOS default (Tk/Cocoa handles Retina natively)")
