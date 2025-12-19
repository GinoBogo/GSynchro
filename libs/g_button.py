#!/usr/bin/env python3
"""
GButton - Enhanced Custom Rounded Button for Tkinter

A custom button widget that renders a flat, rectangular button with slight
rounded corners using a Canvas. Supports hover effects, custom colors, images,
and tooltips.

 Author: Gino Bogo
License: MIT
Version: 2.1
"""

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Any, Optional, Callable, Tuple, Dict


class GButton(tk.Canvas):
    """A customizable rectangular button widget with slight rounded corners."""

    # Class-level shared resources
    _shared_fonts: Dict[Any, tkfont.Font] = {}
    _color_op_cache: Dict[str, str] = {}

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
        if "cursor" not in kwargs:
            kwargs["cursor"] = "arrow" if state == "disabled" else "hand2"

        if "takefocus" not in kwargs:
            kwargs["takefocus"] = True

        if "highlightthickness" not in kwargs:
            kwargs["highlightthickness"] = 0

        if "borderwidth" not in kwargs:
            kwargs["borderwidth"] = 0

        if canvas_bg is None:
            canvas_bg = self._get_parent_background(master)

        if canvas_bg and "bg" not in kwargs:
            kwargs["bg"] = canvas_bg
        elif "bg" not in kwargs:
            kwargs["bg"] = "#f0f0f0"

        super().__init__(
            master,
            width=width,
            height=height,
            **kwargs,
        )

        final_hover_bg = (
            hover_bg if hover_bg is not None else self._darken_color(bg, 0.8)
        )
        final_pressed_bg = (
            pressed_bg if pressed_bg is not None else self._darken_color(bg, 0.6)
        )

        self._text = text
        self.command = command
        self._corner_radius = max(0, min(corner_radius, min(width, height) // 2))
        self._bg_color = bg
        self._fg_color = fg
        self._hover_bg = final_hover_bg
        self._pressed_bg = final_pressed_bg
        self._disabled_bg = disabled_bg
        self._disabled_fg = disabled_fg
        self._border_color = border_color
        self._image_position = image_position
        self._tooltip_text = tooltip_text
        self._state = state
        self._focused = False

        self._image = None
        self._image_size = (0, 0)
        self._image_cache = None
        self._set_image(image)

        self._tooltip_id = None
        self._tooltip_window = None

        self._last_signature = None
        self._width = width
        self._height = height
        self._resize_timer = None

        self._font_key = font
        self._font = self._get_font(font)

        self._bind_events()
        self._draw()

    # Property getters and setters
    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, value):
        if self._text != value:
            self._text = value
            self._last_signature = None
            if self.winfo_exists():
                self._draw()

    @property
    def corner_radius(self):
        return self._corner_radius

    @corner_radius.setter
    def corner_radius(self, value):
        if self._corner_radius != value:
            self._corner_radius = max(
                0, min(value, min(self._width, self._height) // 2)
            )
            self._last_signature = None
            if self.winfo_exists():
                self._draw()

    @property
    def bg_color(self):
        return self._bg_color

    @bg_color.setter
    def bg_color(self, value):
        if self._bg_color != value:
            self._bg_color = value
            self._last_signature = None
            if self.winfo_exists():
                self._draw()

    @property
    def fg_color(self):
        return self._fg_color

    @fg_color.setter
    def fg_color(self, value):
        if self._fg_color != value:
            self._fg_color = value
            self._last_signature = None
            if self.winfo_exists():
                self._draw()

    @property
    def hover_bg(self):
        return self._hover_bg

    @hover_bg.setter
    def hover_bg(self, value):
        if self._hover_bg != value:
            self._hover_bg = value
            self._last_signature = None
            if self.winfo_exists():
                self._draw()

    @property
    def pressed_bg(self):
        return self._pressed_bg

    @pressed_bg.setter
    def pressed_bg(self, value):
        if self._pressed_bg != value:
            self._pressed_bg = value
            self._last_signature = None
            if self.winfo_exists():
                self._draw()

    @property
    def disabled_bg(self):
        return self._disabled_bg

    @disabled_bg.setter
    def disabled_bg(self, value):
        if self._disabled_bg != value:
            self._disabled_bg = value
            self._last_signature = None
            if self.winfo_exists():
                self._draw()

    @property
    def disabled_fg(self):
        return self._disabled_fg

    @disabled_fg.setter
    def disabled_fg(self, value):
        if self._disabled_fg != value:
            self._disabled_fg = value
            self._last_signature = None
            if self.winfo_exists():
                self._draw()

    @property
    def border_color(self):
        return self._border_color

    @border_color.setter
    def border_color(self, value):
        if self._border_color != value:
            self._border_color = value
            self._last_signature = None
            if self.winfo_exists():
                self._draw()

    @property
    def image(self):
        return self._image

    @image.setter
    def image(self, value):
        self._set_image(value)
        self._last_signature = None
        if self.winfo_exists():
            self._draw()

    @property
    def image_position(self):
        return self._image_position

    @image_position.setter
    def image_position(self, value):
        if self._image_position != value:
            self._image_position = value
            self._last_signature = None
            if self.winfo_exists():
                self._draw()

    @property
    def tooltip_text(self):
        return self._tooltip_text

    @tooltip_text.setter
    def tooltip_text(self, value):
        if self._tooltip_text != value:
            self._tooltip_text = value

    def _set_image(self, image):
        """Set image with validation and caching."""
        if image is None:
            self._image = None
            self._image_size = (0, 0)
            self._image_cache = None
            return

        if not isinstance(image, tk.PhotoImage):
            raise TypeError("Image must be a tk.PhotoImage instance")

        self._image = image
        self._image_size = (image.width(), image.height())

        if self._image_size[0] <= 32 and self._image_size[1] <= 32:
            try:
                self._image_cache = image.copy()
            except (tk.TclError, RuntimeError):
                self._image_cache = None

    def _get_parent_background(self, master: Optional[tk.Misc]) -> Optional[str]:
        """Get parent widget background color."""
        if master is None:
            return None

        bg = None
        try:
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

    def _get_font(self, font_spec):
        """Get or create a font with caching."""
        if font_spec is None:
            font_spec = ("Segoe UI", 10, "normal")

        key = tuple(font_spec) if isinstance(font_spec, (list, tuple)) else font_spec

        if key not in GButton._shared_fonts:
            if isinstance(font_spec, (list, tuple)):
                if len(font_spec) == 3:
                    GButton._shared_fonts[key] = tkfont.Font(
                        family=font_spec[0], size=font_spec[1], weight=font_spec[2]
                    )
                elif len(font_spec) == 2:
                    GButton._shared_fonts[key] = tkfont.Font(
                        family=font_spec[0], size=font_spec[1]
                    )
            else:
                GButton._shared_fonts[key] = tkfont.Font(font=font_spec)

        return GButton._shared_fonts[key]

    def _bind_events(self) -> None:
        """Bind all necessary events to the button."""
        events_to_bind = [
            ("<Button-1>", self._on_press),
            ("<ButtonRelease-1>", self._on_release),
            ("<Enter>", self._on_enter),
            ("<Leave>", self._on_leave),
            ("<Configure>", self._on_configure),
            ("<FocusIn>", self._on_focus_in),
            ("<FocusOut>", self._on_focus_out),
            ("<KeyPress-Return>", self._on_key_press),
            ("<KeyPress-space>", self._on_key_press),
        ]

        for event, handler in events_to_bind:
            self.bind(event, handler, add="+")

    def _draw(self) -> None:
        """Draw the button with performance caching."""
        current_signature = (
            self._state,
            self._focused,
            self.text,
            self._width,
            self._height,
            self.corner_radius,
            self.bg_color,
            self.fg_color,
            self.hover_bg,
            self.pressed_bg,
            self.disabled_bg,
            self.disabled_fg,
            self.border_color,
            id(self._image) if self._image else None,
            self._image_position,
        )

        if self._last_signature == current_signature:
            return

        self._last_signature = current_signature
        self.delete("all")

        if self._state == "disabled":
            fill_color = self._disabled_bg
            text_color = self._disabled_fg
        elif self._state == "pressed":
            fill_color = self._pressed_bg
            text_color = self._fg_color
        elif self._state == "hover":
            fill_color = self._hover_bg
            text_color = self._fg_color
        else:
            fill_color = self._bg_color
            text_color = self._fg_color

        # ORIGINAL BORDER LOGIC RESTORED
        if self._border_color:
            outline_color = self._border_color
        else:
            # Use a more contrasting version of fill color for border
            if self._is_light_color(fill_color):
                outline_color = self._darken_color(fill_color, 0.7)  # Darker
            else:
                outline_color = self._lighten_color(fill_color, 1.3)  # Lighter

        if self.corner_radius == 0:
            self.create_rectangle(
                2,
                2,
                self._width - 2,
                self._height - 2,
                fill=fill_color,
                outline=outline_color,
                width=2,
            )
        else:
            offset = 2
            self._draw_rounded_rect(
                offset,
                offset,
                self._width - offset,
                self._height - offset,
                self.corner_radius,
                fill=fill_color,
                outline=outline_color,
                width=2,
            )

        if self._focused:
            self._draw_focus_indicator()

        self._draw_content(text_color)

    def _draw_rounded_rect(
        self, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs
    ) -> int:
        """Draw a rounded rectangle using a smoothed polygon."""
        br_radius = radius + 1

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
            y2 - br_radius,
            x2,
            y2 - br_radius,
            x2,
            y2,
            x2 - br_radius,
            y2,
            x2 - br_radius,
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

    def _draw_focus_indicator(self) -> None:
        """Draw focus indicator around the button."""
        offset = 4
        radius = max(0, self.corner_radius - 2)

        if radius == 0:
            self.create_rectangle(
                offset,
                offset,
                self._width - offset,
                self._height - offset,
                fill="",
                outline=self._fg_color,
                width=2,
                dash=(3, 2),
            )
        else:
            self._draw_rounded_rect(
                offset,
                offset,
                self._width - offset,
                self._height - offset,
                radius,
                fill="",
                outline=self._fg_color,
                width=2,
                dash=(3, 2),
            )

    def _draw_content(self, text_color: str) -> None:
        """Draw image and/or text on the button."""
        if self._image:
            image_pos, text_pos = self._calculate_layout()
            image_to_use = self._image_cache if self._image_cache else self._image
            self.create_image(
                image_pos[0], image_pos[1], image=image_to_use, anchor="center"
            )
            self.create_text(
                text_pos[0],
                text_pos[1],
                text=self.text,
                fill=text_color,
                font=self._font,
                anchor="center",
            )
        else:
            self.create_text(
                self._width / 2,
                self._height / 2,
                text=self.text,
                fill=text_color,
                font=self._font,
                anchor="center",
            )

    def _calculate_layout(self) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        """Calculate positions for image and text."""
        if not self._image:
            return (0, 0), (self._width / 2, self._height / 2)

        img_width = self._image_size[0]
        img_height = self._image_size[1]
        text_width = self._font.measure(self.text)
        text_height = self._font.metrics("linespace")
        spacing = 8

        if self._image_position == "left":
            total_width = img_width + spacing + text_width
            start_x = (self._width - total_width) / 2
            image_x = start_x + img_width / 2
            text_x = start_x + img_width + spacing + text_width / 2
            image_y = text_y = self._height / 2

        elif self._image_position == "right":
            total_width = text_width + spacing + img_width
            start_x = (self._width - total_width) / 2
            text_x = start_x + text_width / 2
            image_x = start_x + text_width + spacing + img_width / 2
            image_y = text_y = self._height / 2

        elif self._image_position == "top":
            total_height = img_height + spacing + text_height
            start_y = (self._height - total_height) / 2
            image_x = text_x = self._width / 2
            image_y = start_y + img_height / 2
            text_y = start_y + img_height + spacing + text_height / 2

        elif self._image_position == "bottom":
            total_height = text_height + spacing + img_height
            start_y = (self._height - total_height) / 2
            image_x = text_x = self._width / 2
            text_y = start_y + text_height / 2
            image_y = start_y + text_height + spacing + img_height / 2

        else:  # center
            image_x = text_x = self._width / 2
            image_y = text_y = self._height / 2

        return (image_x, image_y), (text_x, text_y)

    def _darken_color(self, color: str, factor: float = 0.7) -> str:
        """Darken a color with caching."""
        cache_key = f"darken_{color}_{factor}"

        if cache_key in self._color_op_cache:
            return self._color_op_cache[cache_key]

        try:
            r, g, b = self.winfo_rgb(color)
            r = int((r / 65535) * 255 * factor)
            g = int((g / 65535) * 255 * factor)
            b = int((b / 65535) * 255 * factor)

            result = f"#{max(0, min(255, r)):02x}{max(0, min(255, g)):02x}{max(0, min(255, b)):02x}"
            self._color_op_cache[cache_key] = result
            return result
        except Exception:
            return color

    def _lighten_color(self, color: str, factor: float = 1.3) -> str:
        """Lighten a color with caching."""
        cache_key = f"lighten_{color}_{factor}"

        if cache_key in self._color_op_cache:
            return self._color_op_cache[cache_key]

        try:
            r, g, b = self.winfo_rgb(color)
            r = min(255, int((r / 65535) * 255 * factor))
            g = min(255, int((g / 65535) * 255 * factor))
            b = min(255, int((b / 65535) * 255 * factor))

            result = f"#{max(0, r):02x}{max(0, g):02x}{max(0, b):02x}"
            self._color_op_cache[cache_key] = result
            return result
        except Exception:
            return color

    def _is_light_color(self, color: str) -> bool:
        """Check if a color is light based on luminance.
        RESTORED TO ORIGINAL LOGIC
        """
        try:
            r, g, b = self.winfo_rgb(color)
            luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 65535
            return luminance > 0.5
        except Exception:
            return True

    # Event Handlers
    def _on_press(self, event: tk.Event) -> None:
        if self._state != "disabled":
            self._state = "pressed"
            self._draw()

    def _on_release(self, event: tk.Event) -> None:
        if self._state != "disabled":
            if 0 <= event.x <= self._width and 0 <= event.y <= self._height:
                if self.command:
                    self.command()

                if self.winfo_exists():
                    self._state = "hover"
            else:
                self._state = "normal"
            self._draw()

    def _on_enter(self, event: tk.Event) -> None:
        if self._state != "disabled" and self._state != "pressed":
            self._state = "hover"
            self._draw()

            if self._tooltip_text and self._state != "disabled":
                self._tooltip_id = self.after(1000, self._show_tooltip)

    def _on_leave(self, event: tk.Event) -> None:
        if self._state != "disabled":
            self._state = "normal"
            self._draw()

        if self._tooltip_id:
            self.after_cancel(self._tooltip_id)
            self._tooltip_id = None

        if self._tooltip_window and self._tooltip_window.winfo_exists():
            self._tooltip_window.destroy()
            self._tooltip_window = None

    def _on_configure(self, event: tk.Event) -> None:
        if self._resize_timer:
            self.after_cancel(self._resize_timer)

        self._resize_timer = self.after(
            50, self._handle_resize, event.width, event.height
        )

    def _handle_resize(self, width: int, height: int):
        if not self.winfo_exists():
            return
        self._resize_timer = None
        self._width = width
        self._height = height
        self.corner_radius = min(self._corner_radius, min(width, height) // 2)
        self._last_signature = None
        self._draw()

    def _on_focus_in(self, event: tk.Event) -> None:
        self._focused = True
        self._draw()

    def _on_focus_out(self, event: tk.Event) -> None:
        self._focused = False
        self._draw()

    def _on_key_press(self, event: tk.Event) -> None:
        if self._state != "disabled":
            self._state = "pressed"
            self._draw()
            self.after(100, self._trigger_command)

    def _trigger_command(self) -> None:
        if not self.winfo_exists():
            return

        if self.command:
            self.command()

        if self.winfo_exists():
            self._state = "normal"
            self._draw()

    def _show_tooltip(self) -> None:
        if not self._tooltip_text or not self.winfo_exists():
            return

        if self._tooltip_window and self._tooltip_window.winfo_exists():
            self._tooltip_window.destroy()

        self._tooltip_window = tk.Toplevel(self)
        self._tooltip_window.wm_overrideredirect(True)
        self._tooltip_window.wm_attributes("-topmost", True)

        x = self.winfo_rootx() + self.winfo_width() // 2
        y = self.winfo_rooty() + self.winfo_height() + 5

        label = tk.Label(
            self._tooltip_window,
            text=self._tooltip_text,
            bg="#FFFFE0",
            fg="black",
            padx=6,
            pady=3,
            relief="solid",
            borderwidth=1,
            font=("Segoe UI", 9),
        )
        label.pack()

        self._tooltip_window.geometry(f"+{x}+{y}")
        self._tooltip_window.after(3000, self._tooltip_window.destroy)

    # Public Methods
    def configure(self, cnf: Any = None, **kwargs: Any) -> Any:
        if isinstance(cnf, str):
            return super().configure(cnf, **kwargs)

        if isinstance(cnf, dict):
            kwargs = {**cnf, **kwargs}
            cnf = None

        property_map = {
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

        for kwarg, attr in property_map.items():
            if kwarg in kwargs:
                if kwarg == "command":
                    self.command = kwargs.pop(kwarg)
                else:
                    setattr(self, attr, kwargs.pop(kwarg))

        if "state" in kwargs:
            state = kwargs.pop("state")
            if state != self._state:
                self._state = state
                if state == "disabled":
                    kwargs["cursor"] = "arrow"
                elif "cursor" not in kwargs:
                    kwargs["cursor"] = "hand2"
                self._last_signature = None

        if "width" in kwargs:
            self._width = int(kwargs["width"])
            self._last_signature = None
        if "height" in kwargs:
            self._height = int(kwargs["height"])
            self._last_signature = None

        if "canvas_bg" in kwargs:
            kwargs["bg"] = kwargs.pop("canvas_bg")

        result = super().configure(cnf, **kwargs)
        self._draw()
        return result

    def cget(self, key: str) -> Any:
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


if __name__ == "__main__":
    root = tk.Tk()
    root.title("GButton Demonstration")
    root.geometry("400x450")

    root_bg = root.cget("bg")

    def on_click() -> None:
        print("Button clicked!")

    def toggle_state() -> None:
        current = btn2.cget("state")
        new_state = "disabled" if current == "normal" else "normal"
        btn2.configure(state=new_state)

    icon_img = None
    try:
        icon_img = tk.PhotoImage(width=16, height=16)
        for i in range(4, 12):
            for j in range(4, 12):
                icon_img.put("#FFFFFF", (i, j))
    except (tk.TclError, RuntimeError) as e:
        print(f"Note: Could not create icon image: {e}")
        icon_img = None

    title_label = tk.Label(
        root,
        text="GButton Demonstration",
        font=("Segoe UI", 14, "bold"),
        bg=root_bg,
    )
    title_label.pack(pady=10)

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
        text="Icon + Button (radius=8)",
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
