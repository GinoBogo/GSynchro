"""Tests for GMessagebox yes/no cancellation behavior."""

import sys
from pathlib import Path
from typing import ClassVar

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from libs.g_messagebox import GMessagebox


class FakeParent:
    """Minimal widget stand-in used as a parent during tests."""

    def winfo_rootx(self):
        return 100

    def winfo_rooty(self):
        return 100

    def winfo_width(self):
        return 200

    def winfo_height(self):
        return 150


class FakeDialog:
    """In-memory stand-in for tk.Toplevel used to simulate modal dialogs."""

    _next_wait_action = "protocol"

    def __init__(self, parent):
        self.protocol_callbacks = {}
        self.destroyed = False
        self.wait_action = self._next_wait_action
        FakeDialog._next_wait_action = "protocol"

    def title(self, title):
        self.title_value = title

    def transient(self, parent):
        self.transient_parent = parent

    def grab_set(self):
        self.grab_set_called = True

    def resizable(self, *args, **kwargs):
        self.resizable_value = args

    def configure(self, **kwargs):
        self.bg = kwargs.get("bg")

    def update_idletasks(self):
        self.update_idletasks_called = True

    def winfo_rootx(self):
        return 100

    def winfo_rooty(self):
        return 100

    def winfo_width(self):
        return 200

    def winfo_height(self):
        return 150

    def geometry(self, geometry):
        self.geometry_value = geometry

    def protocol(self, name, callback):
        self.protocol_callbacks[name] = callback

    def destroy(self):
        self.destroyed = True

    def wait_window(self):
        if self.wait_action == "protocol":
            callback = self.protocol_callbacks.get("WM_DELETE_WINDOW")
            if callback:
                callback()
        else:
            commands = FakeGButton.last_commands
            if self.wait_action == "yes" and commands:
                commands[-1]()
            elif self.wait_action == "no" and len(commands) >= 2:
                commands[-2]()


class FakeWidget:
    def __init__(self, *args, **kwargs):
        pass

    def pack(self, *args, **kwargs):
        pass


class FakeStyle:
    def lookup(self, *args, **kwargs):
        return "#ffffff"


class FakeGButton:
    last_commands: ClassVar[list] = []

    def __init__(
        self, master, text=None, command=None, width=None, height=None, **kwargs
    ):
        self.command = command
        FakeGButton.last_commands.append(command)

    def pack(self, *args, **kwargs):
        pass


def setup_fakes(monkeypatch, wait_action="protocol"):
    module = sys.modules["libs.g_messagebox"]
    FakeDialog._next_wait_action = wait_action
    FakeGButton.last_commands.clear()

    monkeypatch.setattr(module.tk, "Toplevel", FakeDialog)
    monkeypatch.setattr(module.ttk, "Style", FakeStyle)
    monkeypatch.setattr(module.ttk, "Frame", FakeWidget)
    monkeypatch.setattr(module.ttk, "Label", FakeWidget)
    monkeypatch.setattr(module, "GButton", FakeGButton)
    monkeypatch.setattr(module.tk, "_default_root", FakeParent(), raising=False)
    monkeypatch.setattr(module.GScaling, "get_scale_factor", lambda parent=None: 1.0)
    monkeypatch.setattr(
        module.GScaling, "scale_pixels", lambda value, master=None: value
    )


def test_window_close_returns_false(monkeypatch):
    setup_fakes(monkeypatch, "protocol")
    assert GMessagebox.askyesno("Confirm Delete", "Are you sure?") is False


def test_missing_root_raises_clear_error(monkeypatch):
    module = sys.modules["libs.g_messagebox"]
    monkeypatch.setattr(module.tk, "_default_root", None, raising=False)
    with pytest.raises(RuntimeError, match="requires a parent widget"):
        GMessagebox.showinfo("Info", "message")


def test_yes_returns_true(monkeypatch):
    setup_fakes(monkeypatch, "yes")
    assert GMessagebox.askyesno("Confirm", "Proceed?") is True


def test_no_returns_false(monkeypatch):
    setup_fakes(monkeypatch, "no")
    assert GMessagebox.askyesno("Confirm", "Proceed?") is False
