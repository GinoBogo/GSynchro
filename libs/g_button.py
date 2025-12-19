#!/usr/bin/env python3
"""
GButton - Enhanced Custom Rounded Button for Tkinter

A custom button widget that renders a flat, rectangular button with slight
rounded corners using a Canvas. Supports hover effects, custom colors, images,
and tooltips.

 Author: Gino Bogo
License: MIT
Version: 2.0
"""

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Any, Optional, Callable, Tuple


class GButton(tk.Canvas):
    """A customizable rectangular button widget with slight rounded corners."""

    def __init__(
        self,
        master: Optional[tk.Misc] = None,
        text: str = "Button",
        command: Optional[Callable] = None,
        width: int = 100,
        height: int = 34,
        corner_radius: int = 8,
        bg: str = "#007AFF",  # Default blue color
        fg: str = "white",
        hover_bg: Optional[str] = None,
        pressed_bg: Optional[str] = None,
        disabled_bg: str = "#E0E0E0",
        disabled_fg: str = "#A0A0A0",
        border_color: Optional[str] = None,
        font: Optional[Tuple[str, int]] = None,
        image: Optional[tk.PhotoImage] = None,
        image_position: str = "left",
        tooltip_text: Optional[str] = None,
        state: str = "normal",
        canvas_bg: Optional[str] = None,
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
            font: Font tuple (family, size).
            image: Optional image to display on button.
            image_position: Position of image relative to text.
            tooltip_text: Text to show in tooltip on hover.
            state: Initial state ("normal" or "disabled").
            canvas_bg: Background color of the canvas (outside the button).
            **kwargs: Additional arguments for tk.Canvas.
        """
        # Set cursor based on state
        if "cursor" not in kwargs:
            kwargs["cursor"] = "arrow" if state == "disabled" else "hand2"

        # Set focus behavior
        if "takefocus" not in kwargs:
            kwargs["takefocus"] = True

        # Set highlight thickness
        if "highlightthickness" not in kwargs:
            kwargs["highlightthickness"] = 0

        # Set border width
        if "borderwidth" not in kwargs:
            kwargs["borderwidth"] = 0

        # Try to get parent background color for canvas
        if canvas_bg is None:
            canvas_bg = self._get_parent_background(master)

        if canvas_bg and "bg" not in kwargs:
            kwargs["bg"] = canvas_bg
        elif "bg" not in kwargs:
            # Default to light gray if no parent background found
            kwargs["bg"] = "#f0f0f0"

        # Initialize Canvas
        super().__init__(
            master,
            width=width,
            height=height,
            **kwargs,
        )

        # Set default hover/pressed colors if not provided
        final_hover_bg = (
            hover_bg if hover_bg is not None else self._darken_color(bg, 0.8)
        )
        final_pressed_bg = (
            pressed_bg if pressed_bg is not None else self._darken_color(bg, 0.6)
        )

        # Core properties
        self.command = command
        self.text = text
        self.corner_radius = max(0, min(corner_radius, min(width, height) // 2))
        self.bg_color = bg
        self.fg_color = fg
        self.hover_bg = final_hover_bg
        self.pressed_bg = final_pressed_bg
        self.disabled_bg = disabled_bg
        self.disabled_fg = disabled_fg
        self.border_color = border_color
        self.image = image
        self.image_position = image_position
        self.tooltip_text = tooltip_text
        self._state = state
        self._focused = False
        self._tooltip_id = None

        # Performance caching
        self._last_signature = None
        self._width = width
        self._height = height

        # Font handling
        if font:
            self._font = tkfont.Font(family=font[0], size=font[1])
        else:
            self._font = tkfont.Font(family="Segoe UI", size=10, weight="normal")

        # Bind events
        self._bind_events()

        # Initial draw
        self._draw()

    def _get_parent_background(self, master: Optional[tk.Misc]) -> Optional[str]:
        """Get parent widget background color.

        Returns:
            Background color string if found, None otherwise.
        """
        if master is None:
            return None

        bg = None
        try:
            # Try to get parent's background
            bg = master.cget("background")
        except (tk.TclError, AttributeError):
            pass

        if not bg:
            try:
                style = ttk.Style()
                bg = style.lookup(master.winfo_class(), "background")
            except Exception:
                pass

        return bg

    def _bind_events(self) -> None:
        """Bind all necessary events to the button."""
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Configure>", self._on_configure)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<KeyPress-Return>", self._on_key_press)
        self.bind("<KeyPress-space>", self._on_key_press)

    def _draw(self) -> None:
        """Draw the button with performance caching."""
        # Create signature for caching
        current_signature = (
            self._state,
            self._focused,
            self.text,
            self._width,
            self._height,
            id(self.image) if self.image else None,
            self.image_position,
        )

        # Skip redraw if nothing changed
        if self._last_signature == current_signature:
            return

        self._last_signature = current_signature

        # Clear canvas
        self.delete("all")

        # Determine colors based on state
        fill_color, text_color, outline_color = self._get_state_colors()

        # Draw rounded rectangle with visible border
        if self.corner_radius == 0:
            # Draw regular rectangle for no radius
            self.create_rectangle(
                2,  # Start 2px from edge for border visibility
                2,
                self._width - 2,  # End 2px from edge
                self._height - 2,
                fill=fill_color,
                outline=outline_color,
                width=2,  # Thicker border for visibility
            )
        else:
            # Draw rounded rectangle using the specified method
            # Use offset for border visibility
            offset = 2
            self._draw_rounded_rect(
                offset,
                offset,
                self._width - offset,
                self._height - offset,
                self.corner_radius,
                fill=fill_color,
                outline=outline_color,
                width=2,  # Thicker border
            )

        # Draw focus indicator
        if self._focused:
            self._draw_focus_indicator()

        # Draw content (image and/or text)
        self._draw_content(text_color)

    def _draw_rounded_rect(
        self, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs
    ) -> int:
        """Draw a rounded rectangle using a smoothed polygon.

        Returns:
            Canvas item ID of the created polygon.
        """
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

    def _get_state_colors(self) -> Tuple[str, str, str]:
        """Get colors based on current state.

        Returns:
            Tuple of (fill_color, text_color, outline_color)
        """
        if self._state == "disabled":
            fill_color = self.disabled_bg
            text_color = self.disabled_fg
        elif self._state == "pressed":
            fill_color = self.pressed_bg
            text_color = self.fg_color
        elif self._state == "hover":
            fill_color = self.hover_bg
            text_color = self.fg_color
        else:
            fill_color = self.bg_color
            text_color = self.fg_color

        # Determine border color - make it more contrasting
        if self.border_color:
            outline_color = self.border_color
        else:
            # Use a more contrasting version of fill color for border
            if self._is_light_color(fill_color):
                outline_color = self._darken_color(fill_color, 0.7)  # Darker
            else:
                outline_color = self._lighten_color(fill_color, 1.3)  # Lighter

        return fill_color, text_color, outline_color

    def _draw_focus_indicator(self) -> None:
        """Draw focus indicator around the button."""
        offset = 4  # More offset for focus indicator
        radius = max(0, self.corner_radius - 2)

        if radius == 0:
            # Draw rectangular focus indicator
            self.create_rectangle(
                offset,
                offset,
                self._width - offset,
                self._height - offset,
                fill="",
                outline=self.fg_color,
                width=2,  # Thicker focus indicator
                dash=(3, 2),
            )
        else:
            # Draw rounded focus indicator using the same method
            self._draw_rounded_rect(
                offset,
                offset,
                self._width - offset,
                self._height - offset,
                radius,
                fill="",
                outline=self.fg_color,
                width=2,  # Thicker focus indicator
                dash=(3, 2),
            )

    def _draw_content(self, text_color: str) -> None:
        """Draw image and/or text on the button."""
        if self.image:
            # Calculate positions for image and text
            image_pos, text_pos = self._calculate_layout()

            # Draw image
            self.create_image(
                image_pos[0], image_pos[1], image=self.image, anchor="center"
            )

            # Draw text
            self.create_text(
                text_pos[0],
                text_pos[1],
                text=self.text,
                fill=text_color,
                font=self._font,
                anchor="center",
            )
        else:
            # Draw text only
            self.create_text(
                self._width / 2,
                self._height / 2,
                text=self.text,
                fill=text_color,
                font=self._font,
                anchor="center",
            )

    def _calculate_layout(self) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        """Calculate positions for image and text.

        Returns:
            Tuple of ((image_x, image_y), (text_x, text_y))
        """
        if not self.image:
            return (0, 0), (self._width / 2, self._height / 2)

        # Get image dimensions
        img_width = self.image.width()
        img_height = self.image.height()

        # Calculate spacing
        spacing = 8

        if self.image_position == "left":
            image_x = img_width // 2 + spacing
            text_x = image_x + img_width // 2 + spacing
            image_y = text_y = self._height / 2

        elif self.image_position == "right":
            text_width = self._font.measure(self.text)
            text_x = self._width - text_width // 2 - img_width - spacing * 2
            image_x = text_x + text_width // 2 + spacing + img_width // 2
            image_y = text_y = self._height / 2

        elif self.image_position == "top":
            image_x = text_x = self._width / 2
            image_y = (self._height - img_height) // 3
            text_y = self._height * 2 / 3

        elif self.image_position == "bottom":
            image_x = text_x = self._width / 2
            text_y = self._height / 3
            image_y = self._height * 2 / 3 - img_height // 2

        else:  # center
            image_x = text_x = self._width / 2
            image_y = text_y = self._height / 2

        return (image_x, image_y), (text_x, text_y)

    def _darken_color(self, color: str, factor: float = 0.7) -> str:
        """Darken a color by a specified factor.

        Args:
            color: Hex color string to darken.
            factor: Darkening factor (0.0 to 1.0).

        Returns:
            Darkened hex color string.
        """
        try:
            r, g, b = self.winfo_rgb(color)
            r = int((r / 65535) * 255 * factor)
            g = int((g / 65535) * 255 * factor)
            b = int((b / 65535) * 255 * factor)
            return f"#{max(0, min(255, r)):02x}{max(0, min(255, g)):02x}{max(0, min(255, b)):02x}"
        except Exception:
            return color

    def _lighten_color(self, color: str, factor: float = 1.3) -> str:
        """Lighten a color by a specified factor.

        Args:
            color: Hex color string to lighten.
            factor: Lightening factor (1.0+).

        Returns:
            Lightened hex color string.
        """
        try:
            r, g, b = self.winfo_rgb(color)
            r = int((r / 65535) * 255 * factor)
            g = int((g / 65535) * 255 * factor)
            b = int((b / 65535) * 255 * factor)
            return f"#{max(0, min(255, r)):02x}{max(0, min(255, g)):02x}{max(0, min(255, b)):02x}"
        except Exception:
            return color

    def _is_light_color(self, color: str) -> bool:
        """Check if a color is light based on luminance.

        Args:
            color: Hex color string to check.

        Returns:
            True if the color is light, False otherwise.
        """
        try:
            r, g, b = self.winfo_rgb(color)
            luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 65535
            return luminance > 0.5
        except Exception:
            return True

    # Event Handlers
    def _on_press(self, event: tk.Event) -> None:
        """Handle mouse press event."""
        if self._state != "disabled":
            self._state = "pressed"
            self._draw()

    def _on_release(self, event: tk.Event) -> None:
        """Handle mouse release event."""
        if self._state != "disabled":
            # Check if release is within bounds
            if 0 <= event.x <= self._width and 0 <= event.y <= self._height:
                if self.command:
                    self.command()

                # Return to hover state if mouse is still inside
                if self.winfo_exists():
                    self._state = "hover"
            else:
                self._state = "normal"
            self._draw()

    def _on_enter(self, event: tk.Event) -> None:
        """Handle mouse enter event."""
        if self._state != "disabled" and self._state != "pressed":
            self._state = "hover"
            self._draw()

            # Schedule tooltip if configured
            if self.tooltip_text and self._state != "disabled":
                self._tooltip_id = self.after(1000, self._show_tooltip)

    def _on_leave(self, event: tk.Event) -> None:
        """Handle mouse leave event."""
        if self._state != "disabled":
            self._state = "normal"
            self._draw()

        # Cancel tooltip
        if self._tooltip_id:
            self.after_cancel(self._tooltip_id)
            self._tooltip_id = None

    def _on_configure(self, event: tk.Event) -> None:
        """Handle widget resize events."""
        self._width = event.width
        self._height = event.height
        self.corner_radius = min(
            self.corner_radius, min(self._width, self._height) // 2
        )
        self._draw()

    def _on_focus_in(self, event: tk.Event) -> None:
        """Handle focus gain event."""
        self._focused = True
        self._draw()

    def _on_focus_out(self, event: tk.Event) -> None:
        """Handle focus loss event."""
        self._focused = False
        self._draw()

    def _on_key_press(self, event: tk.Event) -> None:
        """Handle keyboard press events (Return/Space)."""
        if self._state != "disabled":
            self._state = "pressed"
            self._draw()
            self.after(100, self._trigger_command)

    def _trigger_command(self) -> None:
        """Trigger the button command after keyboard press."""
        if not self.winfo_exists():
            return

        if self.command:
            self.command()

        if self.winfo_exists():
            self._state = "normal"
            self._draw()

    def _show_tooltip(self) -> None:
        """Display tooltip window."""
        if not self.tooltip_text or not self.winfo_exists():
            return

        # Create tooltip window
        tooltip = tk.Toplevel(self)
        tooltip.wm_overrideredirect(True)
        tooltip.wm_attributes("-topmost", True)

        # Get button position
        x = self.winfo_rootx() + self.winfo_width() // 2
        y = self.winfo_rooty() + self.winfo_height() + 5

        # Create tooltip label
        label = tk.Label(
            tooltip,
            text=self.tooltip_text,
            bg="#FFFFE0",
            fg="black",
            padx=6,
            pady=3,
            relief="solid",
            borderwidth=1,
            font=("Segoe UI", 9),
        )
        label.pack()

        # Position tooltip
        tooltip.geometry(f"+{x}+{y}")

        # Destroy tooltip after delay
        tooltip.after(3000, tooltip.destroy)

    # Public Methods
    def configure(self, cnf: Any = None, **kwargs: Any) -> Any:
        """Update button configuration.

        Returns:
            Configuration result from parent class.
        """
        if isinstance(cnf, str):
            return super().configure(cnf, **kwargs)

        if isinstance(cnf, dict):
            kwargs = {**cnf, **kwargs}
            cnf = None

        # Handle custom properties
        custom_props = {
            "text": "text",
            "bg": "bg_color",
            "fg": "fg_color",
            "hover_bg": "hover_bg",
            "pressed_bg": "pressed_bg",
            "disabled_bg": "disabled_bg",
            "disabled_fg": "disabled_fg",
            "command": "command",
            "border_color": "border_color",
            "corner_radius": "corner_radius",
            "image": "image",
            "image_position": "image_position",
            "tooltip_text": "tooltip_text",
        }

        for kwarg, attr in custom_props.items():
            if kwarg in kwargs:
                setattr(self, attr, kwargs.pop(kwarg))

        # Handle state specially
        if "state" in kwargs:
            state = kwargs.pop("state")
            if state != self._state:
                self._state = state
                if state == "disabled":
                    kwargs["cursor"] = "arrow"
                elif "cursor" not in kwargs:
                    kwargs["cursor"] = "hand2"

        # Handle size changes
        if "width" in kwargs:
            self._width = int(kwargs["width"])
        if "height" in kwargs:
            self._height = int(kwargs["height"])

        # Handle canvas_bg
        if "canvas_bg" in kwargs:
            kwargs["bg"] = kwargs.pop("canvas_bg")

        # Pass remaining kwargs to parent configure
        result = super().configure(cnf, **kwargs)
        self._draw()
        return result

    def cget(self, key: str) -> Any:
        """Get configuration value.

        Returns:
            Configuration value for the specified key.
        """
        if key == "text":
            return self.text
        elif key == "state":
            return self._state
        elif key == "command":
            return self.command
        elif key == "bg":
            return self.bg_color
        elif key == "fg":
            return self.fg_color
        elif key == "image":
            return self.image
        elif key == "font":
            return self._font
        elif key in [
            "hover_bg",
            "pressed_bg",
            "disabled_bg",
            "disabled_fg",
            "border_color",
            "corner_radius",
            "image_position",
            "tooltip_text",
        ]:
            return getattr(self, key)

        return super().cget(key)


# Demonstration of GButton functionality
if __name__ == "__main__":
    root = tk.Tk()
    root.title("GButton Demonstration")
    root.geometry("400x450")

    # Get the system background color
    root_bg = root.cget("bg")

    def on_click() -> None:
        """Callback function for button clicks."""
        print("Button clicked!")

    def toggle_state() -> None:
        """Toggle button state between normal and disabled."""
        current = btn2.cget("state")
        new_state = "disabled" if current == "normal" else "normal"
        btn2.configure(state=new_state)

    # Create a sample icon image with specific exception handling
    icon_img = None
    try:
        icon_img = tk.PhotoImage(width=16, height=16)
        # Draw a simple square icon
        for i in range(4, 12):
            for j in range(4, 12):
                icon_img.put("#FFFFFF", (i, j))
    except (tk.TclError, RuntimeError) as e:
        # Handle specific image creation errors
        print(f"Note: Could not create icon image: {e}")
        icon_img = None

    # Application title
    title_label = tk.Label(
        root, text="GButton Demonstration", font=("Segoe UI", 14, "bold"), bg=root_bg
    )
    title_label.pack(pady=10)

    # Button demonstration with various configurations
    btn1 = GButton(
        root,
        text="Rectangular Button (radius=0)",
        command=on_click,
        bg="#007AFF",
        hover_bg="#0051A8",
        width=220,
        height=36,
        corner_radius=0,
        tooltip_text="Button with sharp rectangular corners",
    )
    btn1.pack(pady=6)

    btn2 = GButton(
        root,
        text="Slightly Rounded (radius=4)",
        command=toggle_state,
        bg="#4CAF50",
        hover_bg="#45a049",
        width=220,
        height=36,
        corner_radius=4,
        tooltip_text="Button with slight corner rounding",
    )
    btn2.pack(pady=6)

    btn3 = GButton(
        root,
        text="Standard Rounding (radius=8)",
        command=on_click,
        bg="#FF9800",
        hover_bg="#F57C00",
        width=220,
        height=36,
        corner_radius=8,
        tooltip_text="Button with standard corner radius",
    )
    btn3.pack(pady=6)

    btn4 = GButton(
        root,
        text="More Rounding (radius=12)",
        command=on_click,
        bg="#9C27B0",
        hover_bg="#7B1FA2",
        width=220,
        height=36,
        corner_radius=12,
        tooltip_text="Button with pronounced corner rounding",
    )
    btn4.pack(pady=6)

    btn5 = GButton(
        root,
        text="Button with Icon (radius=8)",
        command=on_click,
        bg="#2196F3",
        hover_bg="#1976D2",
        width=220,
        height=36,
        corner_radius=8,
        image=icon_img,
        image_position="left",
        tooltip_text="Button with left-aligned icon",
    )
    btn5.pack(pady=6)

    btn6 = GButton(
        root,
        text="Disabled State (radius=8)",
        command=on_click,
        bg="#607D8B",
        hover_bg="#455A64",
        disabled_bg="#E0E0E0",
        disabled_fg="#9E9E9E",
        state="disabled",
        width=220,
        height=36,
        corner_radius=8,
        tooltip_text="Button in disabled state",
    )
    btn6.pack(pady=6)

    root.mainloop()
