#!/usr/bin/env python3
"""
GTheme - Centralized Color Definitions

Defines the color palette used across the GSynchro and GCompare applications.

 Author: Gino Bogo
License: MIT
Version: 1.0
"""


def get_theme_colors():
    """Return a dictionary containing all color definitions used in the application."""
    return {
        "buttons": {
            "lightgreen": {
                "bg": "#90EE90",
                "hover_bg": "#B6FFB6",
                "pressed_bg": "#7CCD7C",
                "fg": "black",
            },
            "lightblue": {
                "bg": "#87CEFA",
                "hover_bg": "#ADD8E6",
                "pressed_bg": "#7EC0EE",
                "fg": "black",
            },
            "secondary": {
                "bg": "#E6E6FA",
                "hover_bg": "#F3F3FC",
                "pressed_bg": "#CBCBE8",
                "fg": "black",
            },
            "default": {
                "bg": "#E1E1E1",
                "hover_bg": "#F0F0F0",
                "pressed_bg": "#D0D0D0",
                "fg": "black",
            },
            "primary": {
                "bg": "#007AFF",
                "hover_bg": "#0051A8",
                "pressed_bg": "#003366",
                "fg": "white",
            },
            "lightgray": {
                "bg": "#F8F9FA",
                "hover_bg": "#E9ECEF",
                "pressed_bg": "#DEE2E6",
                "fg": "#495057",
            },
            "lightgold": {
                "bg": "#EEE8AA",
                "hover_bg": "#F5F0C6",
                "pressed_bg": "#CDC673",
                "fg": "black",
            },
            "orange": {
                "bg": "#FFCC80",
                "hover_bg": "#FFE0B2",
                "pressed_bg": "#FFB74D",
                "fg": "black",
            },
            "bluegrey": {
                "bg": "#CFD8DC",
                "hover_bg": "#ECEFF1",
                "pressed_bg": "#B0BEC5",
                "fg": "black",
            },
        },
        "status": {
            "green": "green",
            "orange": "orange",
            "blue": "blue",
            "red": "red",
            "magenta": "magenta",
            "black": "black",
        },
        "diff": {
            "removed": "lightcoral",
            "removed_empty": "yellow",
            "added": "lightblue",
            "added_empty": "yellow",
            "marker_fill": "#808080",
            "marker_outline": "black",
            "canvas_bg": "#FFFFFF",
        },
        "progress": {
            "trough": "#E0E0E0",
            "background": "dodgerblue",
        },
    }
