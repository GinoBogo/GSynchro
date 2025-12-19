#!/usr/bin/env python3
"""
GButton - Custom Rounded Button for Tkinter

A custom button widget that renders a flat, rounded button using a Canvas.
Supports hover effects, custom colors, and rounded corners.

Author: Gino Bogo
License: MIT Version: 1.0
"""

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Any, Union


class GButton(tk.Canvas):
    """A customizable rounded button widget."""

    def __init__(
        self,
        master=None,
        text="Button",
        command=None,
        width: Union[int, str] = 120,
        height: Union[int, str] = 40,
        corner_radius: Union[int, str] = 5,
        bg="#007AFF",
        fg="white",
        hover_bg="#0051A8",
        pressed_bg="#003366",
        disabled_bg="#E0E0E0",
        disabled_fg="#A0A0A0",
        border_color=None,
        font=None,
        **kwargs,
    ):
        """Initialize the GButton.

        Args:
            master: Parent widget.
            text: Button label text.
            command: Callback function when clicked.
            width: Width in pixels.
            height: Height in pixels.
            corner_radius: Radius of the corners.
            bg: Background color (normal state).
            fg: Text color.
            hover_bg: Background color when hovered.
            pressed_bg: Background color when pressed.
            disabled_bg: Background color when disabled.
            disabled_fg: Text color when disabled.
            border_color: Border color (optional).
            font: Font tuple or object for the text.
            **kwargs: Additional arguments for tk.Canvas.
        """
        # Handle canvas background to match parent if possible
        canvas_bg = kwargs.pop("canvas_bg", None)
        if not canvas_bg:
            try:
                if master:
                    canvas_bg = master.cget("background")
            except (tk.TclError, AttributeError):
                try:
                    if master:
                        style = ttk.Style()
                        canvas_bg = style.lookup(master.winfo_class(), "background")
                except Exception:
                    pass

        if not canvas_bg:
            canvas_bg = "#f0f0f0"  # Default fallback

        # Handle initial state
        self._state = kwargs.get("state", "normal")

        if "cursor" not in kwargs:
            kwargs["cursor"] = "arrow" if self._state == "disabled" else "hand2"

        if "takefocus" not in kwargs:
            kwargs["takefocus"] = True

        # Initialize Canvas
        super().__init__(
            master,
            width=width,
            height=height,
            bg=canvas_bg,
            highlightthickness=0,
            borderwidth=0,
            **kwargs,
        )

        self.command = command
        self.text = text
        self.corner_radius = int(corner_radius)
        self.bg_color = bg
        self.fg_color = fg
        self.hover_bg = hover_bg
        self.pressed_bg = pressed_bg
        self.disabled_bg = disabled_bg
        self.disabled_fg = disabled_fg
        self.border_color = border_color
        self._focused = False

        if font:
            self._font = font
        else:
            self._font = tkfont.Font(family="Helvetica", size=10)

        self._width = int(width)
        self._height = int(height)

        # Bind events
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Configure>", self._on_configure)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<Return>", self._on_key_press)
        self.bind("<space>", self._on_key_press)

        # Initial draw
        self._draw()

    def _draw(self):
        """Draw the button based on current state."""
        self.delete("all")

        # Determine color based on state
        if self._state == "disabled":
            fill_color = self.disabled_bg
            text_color = self.disabled_fg
            outline_color = self.disabled_bg
        elif self._state == "pressed":
            fill_color = self.pressed_bg
            text_color = self.fg_color
            outline_color = self._get_border_color(fill_color)
        elif self._state == "hover":
            fill_color = self.hover_bg
            text_color = self.fg_color
            outline_color = self._get_border_color(fill_color)
        else:
            fill_color = self.bg_color
            text_color = self.fg_color
            outline_color = self._get_border_color(fill_color)

        # Draw rounded rectangle
        self._draw_rounded_rect(
            2,
            2,
            self._width - 2,
            self._height - 2,
            self.corner_radius,
            fill=fill_color,
            outline=outline_color,
            width=1,
        )

        if self._focused:
            self._draw_rounded_rect(
                4,
                4,
                self._width - 4,
                self._height - 4,
                max(0, self.corner_radius - 2),
                fill="",
                outline=self.fg_color,
                width=1,
                dash=(2, 2),
            )

        # Draw text
        self.create_text(
            self._width / 2,
            self._height / 2 + 1,
            text=self.text,
            fill=text_color,
            font=self._font,
        )

    def _get_border_color(self, fill_color):
        """Determine border color based on fill color brightness."""
        if self.border_color:
            return self.border_color
        if self._is_light_color(fill_color):
            return self._darken_color(fill_color)
        return self._lighten_color(fill_color)

    def _darken_color(self, color, factor=0.85):
        """Darken a color by a factor."""
        try:
            r, g, b = self.winfo_rgb(color)
            r = int((r / 65535) * 255 * factor)
            g = int((g / 65535) * 255 * factor)
            b = int((b / 65535) * 255 * factor)
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return color

    def _lighten_color(self, color, factor=1.3):
        """Lighten a color by a factor."""
        try:
            r, g, b = self.winfo_rgb(color)
            r = int((r / 65535) * 255)
            g = int((g / 65535) * 255)
            b = int((b / 65535) * 255)

            r = min(255, int(r * factor))
            g = min(255, int(g * factor))
            b = min(255, int(b * factor))
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return color

    def _is_light_color(self, color):
        """Check if color is light based on luminance."""
        try:
            r, g, b = self.winfo_rgb(color)
            luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 65535
            return luminance > 0.5
        except Exception:
            return True

    def _draw_rounded_rect(self, x1, y1, x2, y2, radius, **kwargs):
        """Draw a rounded rectangle using a smoothed polygon."""
        points = [
            x1 + radius,
            y1,
            x1 + radius,
            y1,
            x2 - radius,
            y1,
            x2 - radius,
            y1,
            x2,
            y1,
            x2,
            y1 + radius,
            x2,
            y1 + radius,
            x2,
            y2 - radius,
            x2,
            y2 - radius,
            x2,
            y2,
            x2 - radius,
            y2,
            x2 - radius,
            y2,
            x1 + radius,
            y2,
            x1 + radius,
            y2,
            x1,
            y2,
            x1,
            y2 - radius,
            x1,
            y2 - radius,
            x1,
            y1 + radius,
            x1,
            y1 + radius,
            x1,
            y1,
        ]
        return self.create_polygon(points, smooth=True, **kwargs)

    def _on_press(self, event):
        if self._state != "disabled":
            self._state = "pressed"
            self._draw()

    def _on_release(self, event):
        if self._state != "disabled":
            # Check if release is within bounds to trigger command
            if 0 <= event.x <= self._width and 0 <= event.y <= self._height:
                if self.command:
                    self.command()

                # Check if widget still exists after command execution
                try:
                    if not self.winfo_exists():
                        return
                except tk.TclError:
                    return

                self._state = "hover"  # Return to hover state if still inside
            else:
                self._state = "normal"
            self._draw()

    def _on_enter(self, event):
        if self._state != "disabled" and self._state != "pressed":
            self._state = "hover"
            self._draw()

    def _on_leave(self, event):
        if self._state != "disabled":
            self._state = "normal"
            self._draw()

    def _on_configure(self, event):
        """Handle resize events."""
        self._width = event.width
        self._height = event.height
        self._draw()

    def _on_focus_in(self, event):
        self._focused = True
        self._draw()

    def _on_focus_out(self, event):
        self._focused = False
        self._draw()

    def _on_key_press(self, event):
        if self._state != "disabled":
            self._state = "pressed"
            self._draw()
            self.after(100, self._trigger_command)

    def _trigger_command(self):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return

        if self.command:
            self.command()

        try:
            if self.winfo_exists():
                self._state = "normal"
                self._draw()
        except tk.TclError:
            pass

    def configure(self, cnf: Any = None, **kwargs: Any) -> Any:
        """Update configuration."""
        if isinstance(cnf, str):
            return super().configure(cnf, **kwargs)

        if isinstance(cnf, dict):
            kwargs = {**cnf, **kwargs}
            cnf = None

        if "text" in kwargs:
            self.text = kwargs.pop("text")
        if "bg" in kwargs:
            self.bg_color = kwargs.pop("bg")
        if "fg" in kwargs:
            self.fg_color = kwargs.pop("fg")
        if "hover_bg" in kwargs:
            self.hover_bg = kwargs.pop("hover_bg")
        if "pressed_bg" in kwargs:
            self.pressed_bg = kwargs.pop("pressed_bg")
        if "disabled_bg" in kwargs:
            self.disabled_bg = kwargs.pop("disabled_bg")
        if "disabled_fg" in kwargs:
            self.disabled_fg = kwargs.pop("disabled_fg")
        if "command" in kwargs:
            self.command = kwargs.pop("command")
        if "border_color" in kwargs:
            self.border_color = kwargs.pop("border_color")
        if "corner_radius" in kwargs:
            self.corner_radius = int(kwargs.pop("corner_radius"))
        if "state" in kwargs:
            self._state = kwargs["state"]
            if self._state == "disabled":
                kwargs["cursor"] = "arrow"
            elif "cursor" not in kwargs:
                kwargs["cursor"] = "hand2"

        result = super().configure(cnf, **kwargs)
        self._draw()
        return result


if __name__ == "__main__":
    root = tk.Tk()
    root.title("GButton Test")
    root.geometry("300x200")

    def on_click():
        print("Button clicked!")

    btn = GButton(
        root,
        text="Click Me",
        command=on_click,
        bg="#4CAF50",
        hover_bg="#45a049",
        width=150,
        height=50,
    )
    btn.pack(pady=50)

    root.mainloop()
