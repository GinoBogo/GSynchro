import os
import sys
import tempfile
import tkinter as tk
from unittest.mock import patch

import pytest
from termcolor import cprint

from g_compare import GCompare

# Add project root to sys.path to import g_compare from the parent directory
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)


@pytest.fixture
def base_test_files():
    """Set up the test environment and yield app components."""
    root = tk.Tk()
    root.withdraw()  # Hide the main window during tests

    # =======================================================================
    # Create temporary files for testing
    # =======================================================================

    temp_dir = tempfile.TemporaryDirectory()
    file_a_path = os.path.join(temp_dir.name, "file_a.txt")
    file_b_path = os.path.join(temp_dir.name, "file_b.txt")
    file_c_path = os.path.join(temp_dir.name, "file_c.txt")

    with open(file_a_path, "w", encoding="utf-8") as f:
        f.write("line 1\nline 2\nline 3\n")
    with open(file_b_path, "w", encoding="utf-8") as f:
        f.write("line 1\nline two\nline 3\n")
    with open(file_c_path, "w", encoding="utf-8") as f:
        f.write("line 1\nline 2\nline 3\n")

    files = {
        "a": file_a_path,
        "b": file_b_path,
        "c": file_c_path,
    }
    yield files

    # Teardown
    temp_dir.cleanup()
    root.destroy()


@pytest.fixture
def app_components(base_test_files):
    """Fixture for app with default sys.argv (no command-line files)."""
    root = tk.Tk()
    root.withdraw()  # Hide the main window during tests
    with patch.object(sys, "argv", ["g_compare.py"]):
        app = GCompare(root)
    yield app, root, base_test_files
    root.destroy()


class TestGCompare:
    """Test suite for the GCompare application."""

    # ==========================================================================
    # INITIALIZATION & FILE LOADING TESTS
    # ==========================================================================

    def test_initialization(self, app_components):
        """Test if the application initializes correctly."""
        cprint(f"\n--- {self.test_initialization.__doc__}", "yellow")

        app, _, _ = app_components
        assert isinstance(app, GCompare)
        assert app.root.title() == "GCompare - File Comparison Tool"
        assert app.status_a.get() == "by Gino Bogo"

    def test_load_file_a(self, app_components):
        """Test loading a file into panel A."""
        cprint(f"\n--- {self.test_load_file_a.__doc__}", "yellow")

        app, root, files = app_components
        app._load_file_a(files["a"])
        root.update()  # Process events
        content = app.file_view_a.get("1.0", tk.END)
        assert content.strip() == "line 1\nline 2\nline 3"
        assert "lines, 21 characters" in app.status_a.get()

    def test_load_file_b(self, app_components):
        """Test loading a file into panel B."""
        cprint(f"\n--- {self.test_load_file_b.__doc__}", "yellow")

        app, root, files = app_components
        app._load_file_b(files["b"])
        root.update()
        content = app.file_view_b.get("1.0", tk.END)
        assert content.strip() == "line 1\nline two\nline 3"
        assert "lines, 23 characters" in app.status_b.get()

    def test_load_from_cli_one_arg(self, base_test_files):
        """Test loading a file from command line with one argument."""
        cprint(f"\n--- {self.test_load_from_cli_one_arg.__doc__}", "yellow")
        root = tk.Tk()
        root.withdraw()
        files = base_test_files
        with (
            patch.object(GCompare, "_load_file_a") as mock_load_a,
            patch.object(GCompare, "_load_file_b") as mock_load_b,
        ):
            with patch.object(sys, "argv", ["g_compare.py", files["a"]]):
                GCompare(root)
            mock_load_a.assert_called_once_with(files["a"])
            mock_load_b.assert_not_called()
        root.destroy()

    def test_load_from_cli_two_args(self, base_test_files):
        """Test loading files from command line with two arguments."""
        cprint(f"\n--- {self.test_load_from_cli_two_args.__doc__}", "yellow")
        root = tk.Tk()
        root.withdraw()
        files = base_test_files
        with (
            patch.object(GCompare, "_load_file_a") as mock_load_a,
            patch.object(GCompare, "_load_file_b") as mock_load_b,
        ):
            with patch.object(sys, "argv", ["g_compare.py", files["a"], files["b"]]):
                GCompare(root)
            mock_load_a.assert_called_once_with(files["a"])
            mock_load_b.assert_called_once_with(files["b"])
        root.destroy()

    # ==========================================================================
    # COMPARISON TESTS
    # ==========================================================================

    def test_compare_identical_files(self, app_components):
        """Test comparing two identical files."""
        cprint(f"\n--- {self.test_compare_identical_files.__doc__}", "yellow")

        app, root, files = app_components
        app._load_file_a(files["a"])
        app._load_file_b(files["c"])
        root.update()

        app._compare_files()
        root.update()

        assert app.status_a.get() == "Lines removed: 0"
        assert app.status_b.get() == "Lines added: 0"

        # Check that no 'difference' tags were applied
        assert len(app.file_view_a.tag_ranges("difference")) == 0
        assert len(app.file_view_b.tag_ranges("difference")) == 0

    def test_compare_different_files(self, app_components):
        """Test comparing two different files."""
        cprint(f"\n--- {self.test_compare_different_files.__doc__}", "yellow")

        app, root, files = app_components
        app._load_file_a(files["a"])
        app._load_file_b(files["b"])
        root.update()

        app._compare_files()
        root.update()

        assert app.status_a.get() == "Lines removed: 1"
        assert app.status_b.get() == "Lines added: 1"

        # Check that 'difference' tags were applied
        assert len(app.file_view_a.tag_ranges("difference")) > 0
        assert len(app.file_view_b.tag_ranges("difference")) > 0

        # Check the correct lines are tagged
        tagged_a = app.file_view_a.tag_ranges("difference")
        assert str(tagged_a[0]) == "2.0"  # Start of line 2

        tagged_b = app.file_view_b.tag_ranges("difference")
        assert str(tagged_b[0]) == "2.0"  # Start of line 2

    # ==========================================================================
    # EDITING & SAVING TESTS
    # ==========================================================================

    def test_dirty_state_on_edit(self, app_components):
        """Test if the panel title indicates unsaved changes."""
        cprint(f"\n--- {self.test_dirty_state_on_edit.__doc__}", "yellow")

        app, root, files = app_components
        app._load_file_a(files["a"])
        root.update()

        # Initial state should not be dirty
        assert app.panel_a.cget("text") == "File A"

        # Simulate user typing
        app.file_view_a.insert("1.0", "new text")
        root.update()

        # State should now be dirty
        assert app.panel_a.cget("text") == "File A*"

    @patch("tkinter.messagebox.askyesno", return_value=True)
    @patch("tkinter.messagebox.showinfo")
    def test_save_resets_dirty_state(
        self, mock_showinfo, mock_askyesno, app_components
    ):
        """Test that saving the file resets the dirty state indicator."""
        cprint(f"\n--- {self.test_save_resets_dirty_state.__doc__}", "yellow")

        app, root, files = app_components
        app._load_file_a(files["a"])
        root.update()

        # Make the file dirty
        app.file_view_a.insert("1.0", "new text")
        root.update()
        assert app.panel_a.cget("text") == "File A*"

        # Save the file
        app._save_file_a()
        root.update()

        # Check that the dirty indicator is gone
        assert app.panel_a.cget("text") == "File A"
        mock_askyesno.assert_called_once()
        mock_showinfo.assert_called_once()
