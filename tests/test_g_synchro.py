import os
import shutil
import sys
import tempfile
import tkinter as tk
from pathlib import Path

import pytest
from termcolor import cprint

# Add project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from g_synchro import GSynchro


@pytest.fixture
def comparison_test_environment():
    """Set up test environment for comparison tests."""
    root = tk.Tk()
    root.withdraw()  # Hide the main window during tests

    # Create a temporary directory to hold test folders
    test_dir = Path(tempfile.mkdtemp())
    panel_a_dir = test_dir / "panel_a"
    panel_b_dir = test_dir / "panel_b"

    # Create test directories
    panel_a_dir.mkdir(exist_ok=True)
    panel_b_dir.mkdir(exist_ok=True)

    # =======================================================================
    # Create test files and directories
    # =======================================================================

    # Identical file (same content in both panels)
    (panel_a_dir / "identical.txt").write_text("This file is the same.")
    (panel_b_dir / "identical.txt").write_text("This file is the same.")

    # Different file (same name, different content)
    (panel_a_dir / "different.txt").write_text("Content for panel A.")
    (panel_b_dir / "different.txt").write_text("Content for panel B.")

    # File only in panel A
    (panel_a_dir / "only_in_a.txt").write_text("This file is only in A.")

    # File only in panel B
    (panel_b_dir / "only_in_b.txt").write_text("This file is only in B.")

    # Subdirectory structure (different in each panel)
    (panel_a_dir / "subdir").mkdir(exist_ok=True)
    (panel_b_dir / "subdir_b").mkdir(exist_ok=True)
    (panel_a_dir / "subdir" / "subfile.txt").write_text("Subfile content.")

    # Shared directory with different content
    (panel_a_dir / "shared_dir").mkdir(exist_ok=True)
    (panel_b_dir / "shared_dir").mkdir(exist_ok=True)
    (panel_a_dir / "shared_dir" / "a_only.txt").write_text("a")
    (panel_b_dir / "shared_dir" / "b_only.txt").write_text("b")

    # Deeper nested structure
    (panel_a_dir / "deep" / "a").mkdir(parents=True, exist_ok=True)
    (panel_a_dir / "deep" / "a" / "deep_file.txt").write_text("deep")

    # Type conflict (file vs. directory)
    (panel_a_dir / "conflict").mkdir(exist_ok=True)
    (panel_b_dir / "conflict").write_text("I am a file")

    # Initialize the application
    app = GSynchro(root)
    app.folder_a.set(str(panel_a_dir))
    app.folder_b.set(str(panel_b_dir))

    yield app, panel_a_dir, panel_b_dir

    # Teardown
    shutil.rmtree(test_dir)
    root.destroy()


def _run_comparison(app, panel_a_dir, panel_b_dir):
    """Helper method to scan panels and run comparison logic.

    Args:
        app: GSynchro application instance
        panel_a_dir: Path to panel A directory
        panel_b_dir: Path to panel B directory

    Returns:
        Dictionary of path to status mappings
    """
    app.files_a = app._scan_local(panel_a_dir)
    app.files_b = app._scan_local(panel_b_dir)

    all_paths = set(app.files_a.keys()) | set(app.files_b.keys())

    item_statuses, stats = app._calculate_item_statuses(
        all_paths, app.files_a, app.files_b, False, False, None, None
    )

    return {k.replace("/", os.sep): v[0] for k, v in item_statuses.items()}


class TestComparePanels:
    """Test suite for panel comparison functionality."""

    # ===========================================================================
    # Comparison Test Methods
    # ===========================================================================

    def test_identical_and_different_files(self, comparison_test_environment):
        """Test comparison of identical and different files."""
        cprint(f"\n--- {self.test_identical_and_different_files.__doc__}", "yellow")
        app, panel_a_dir, panel_b_dir = comparison_test_environment
        actual_statuses = _run_comparison(app, panel_a_dir, panel_b_dir)
        assert actual_statuses.get("identical.txt") == "Identical"
        assert actual_statuses.get("different.txt") == "Different"

    def test_unique_files_and_directories(self, comparison_test_environment):
        """Test items that exist only in one panel."""
        cprint(f"\n--- {self.test_unique_files_and_directories.__doc__}", "yellow")
        app, panel_a_dir, panel_b_dir = comparison_test_environment
        actual_statuses = _run_comparison(app, panel_a_dir, panel_b_dir)
        assert actual_statuses.get("only_in_a.txt") == "Only in A"
        assert actual_statuses.get("only_in_b.txt") == "Only in B"
        assert actual_statuses.get("subdir") == "Only in A"
        assert actual_statuses.get(os.path.join("subdir", "subfile.txt")) == "Only in A"
        assert actual_statuses.get("subdir_b") == "Only in B"

    def test_deeply_nested_structure(self, comparison_test_environment):
        """Test deeply nested unique items."""
        cprint(f"\n--- {self.test_deeply_nested_structure.__doc__}", "yellow")
        app, panel_a_dir, panel_b_dir = comparison_test_environment
        actual_statuses = _run_comparison(app, panel_a_dir, panel_b_dir)
        assert actual_statuses.get("deep") == "Only in A"
        assert (
            actual_statuses.get(os.path.join("deep", "a", "deep_file.txt"))
            == "Only in A"
        )

    def test_shared_directory_with_differences(self, comparison_test_environment):
        """Test a directory that exists in both panels but has different content."""
        cprint(f"\n--- {self.test_shared_directory_with_differences.__doc__}", "yellow")
        app, panel_a_dir, panel_b_dir = comparison_test_environment
        actual_statuses = _run_comparison(app, panel_a_dir, panel_b_dir)
        assert actual_statuses.get("shared_dir") == "Has differences"
        assert (
            actual_statuses.get(os.path.join("shared_dir", "a_only.txt")) == "Only in A"
        )
        assert (
            actual_statuses.get(os.path.join("shared_dir", "b_only.txt")) == "Only in B"
        )

    def test_type_conflict(self, comparison_test_environment):
        """Test a path that is a file in one panel and a directory in the other."""
        cprint(f"\n--- {self.test_type_conflict.__doc__}", "yellow")
        app, panel_a_dir, panel_b_dir = comparison_test_environment
        actual_statuses = _run_comparison(app, panel_a_dir, panel_b_dir)
        assert actual_statuses.get("conflict") == "Type conflict"


@pytest.fixture
def filtering_test_environment():
    """Set up test environment for filtering tests."""
    root = tk.Tk()
    root.withdraw()
    app = GSynchro(root)

    test_dir = Path(tempfile.mkdtemp())
    panel_dir = test_dir / "panel"
    panel_dir.mkdir(exist_ok=True)

    # =======================================================================
    # Create test structure for filtering
    # =======================================================================

    # Regular files
    (panel_dir / "file.txt").write_text("keep")
    (panel_dir / "file.log").write_text("filter")

    # Directory to be filtered
    (panel_dir / "__pycache__").mkdir()
    (panel_dir / "__pycache__" / "cache.pyc").write_text("cache")

    # Nested folder inside a filtered directory
    (panel_dir / "__pycache__" / "folder_1").mkdir()
    (panel_dir / "__pycache__" / "folder_1" / "another.pyc").write_text("nested cache")

    # Another directory to be filtered
    (panel_dir / "build").mkdir()
    (panel_dir / "build" / "app.exe").write_text("executable")

    # File to be excluded by its full name
    (panel_dir / "important_doc.txt").write_text("This document is important.")

    # Files to be excluded by multiple wildcard patterns
    (panel_dir / "temp.tmp").write_text("Temporary file.")
    (panel_dir / "backup.bak").write_text("Backup file.")
    (panel_dir / "keep.txt").write_text(
        "Keep this file."
    )  # Should not be filtered by new rules

    # Nested directory to be excluded with its contents
    (panel_dir / "data" / "sensitive").mkdir(parents=True, exist_ok=True)
    (panel_dir / "data" / "sensitive" / "private.txt").write_text("Private data.")
    (panel_dir / "data" / "public.txt").write_text(
        "Public data."
    )  # Should not be filtered

    # Files to be excluded by pattern within a specific directory
    (panel_dir / "logs").mkdir(exist_ok=True)
    (panel_dir / "logs" / "app.log").write_text("App log.")
    (panel_dir / "logs" / "error.log").write_text("Error log.")
    (panel_dir / "logs" / "info.txt").write_text(
        "Info text."
    )  # Should not be filtered by *.log

    # File named similarly to a directory pattern to test rule specificity
    (panel_dir / "my_dir").write_text("I am a file named my_dir")
    (panel_dir / "my_dir_folder").mkdir(exist_ok=True)
    (panel_dir / "my_dir_folder" / "nested.txt").write_text("Nested file.")

    yield app, panel_dir

    # Teardown
    shutil.rmtree(test_dir)
    root.destroy()


class TestFiltering:
    """Test suite for filtering functionality."""

    # ===========================================================================
    # Filtering Test Methods
    # ===========================================================================

    def test_file_and_directory_filters(self, filtering_test_environment):
        """Test that filter rules exclude specified files and directories."""
        cprint(f"\n--- {self.test_file_and_directory_filters.__doc__}", "cyan")
        app, panel_dir = filtering_test_environment
        # Define filter rules to apply
        rules = ["*.log", "__pycache__", "build/"]

        # Scan the panel with the filter rules
        scanned_files = app._scan_local(panel_dir, rules=rules)

        # Get the relative paths of the scanned items
        actual_paths = {path.replace(os.sep, "/") for path in scanned_files.keys()}

        # Assert that filtered items are not present
        assert "file.txt" in actual_paths
        assert "file.log" not in actual_paths
        assert "__pycache__" not in actual_paths
        assert "__pycache__/cache.pyc" not in actual_paths
        assert "build" not in actual_paths
        assert "build/app.exe" not in actual_paths

    def test_nested_folder_in_filtered_directory(self, filtering_test_environment):
        """Test that a nested folder inside a filtered directory is also excluded."""
        cprint(f"\n--- {self.test_nested_folder_in_filtered_directory.__doc__}", "cyan")
        app, panel_dir = filtering_test_environment
        rules = ["__pycache__/"]
        scanned_files = app._scan_local(panel_dir, rules=rules)
        actual_paths = {path.replace(os.sep, "/") for path in scanned_files.keys()}

        # Assert that the entire __pycache__ directory and its contents are excluded
        assert "__pycache__" not in actual_paths
        assert "__pycache__/folder_1" not in actual_paths
        assert "__pycache__/folder_1/another.pyc" not in actual_paths

        # Ensure new files are not accidentally filtered by old rules
        assert "important_doc.txt" in actual_paths
        assert "temp.tmp" in actual_paths
        assert "backup.bak" in actual_paths
        assert "keep.txt" in actual_paths
        assert "data/public.txt" in actual_paths
        assert "logs/info.txt" in actual_paths
        assert "my_dir" in actual_paths
        assert "my_dir_folder/nested.txt" in actual_paths

    def test_exclude_specific_file_by_name(self, filtering_test_environment):
        """Test excluding a specific file by its full name."""
        cprint(f"\n--- {self.test_exclude_specific_file_by_name.__doc__}", "cyan")
        app, panel_dir = filtering_test_environment
        rules = ["important_doc.txt"]
        scanned_files = app._scan_local(panel_dir, rules=rules)
        actual_paths = {path.replace(os.sep, "/") for path in scanned_files.keys()}

        assert "important_doc.txt" not in actual_paths
        assert "file.txt" in actual_paths  # Ensure other files are still present

    def test_exclude_multiple_file_patterns(self, filtering_test_environment):
        """Test excluding files using multiple wildcard patterns."""
        cprint(f"\n--- {self.test_exclude_multiple_file_patterns.__doc__}", "cyan")
        app, panel_dir = filtering_test_environment
        rules = ["*.tmp", "*.bak"]
        scanned_files = app._scan_local(panel_dir, rules=rules)
        actual_paths = {path.replace(os.sep, "/") for path in scanned_files.keys()}

        assert "temp.tmp" not in actual_paths
        assert "backup.bak" not in actual_paths
        assert "keep.txt" in actual_paths  # Should not be filtered
        assert "file.txt" in actual_paths  # Ensure other files are still present

    def test_exclude_nested_directory_and_contents(self, filtering_test_environment):
        """Test excluding a nested directory and all its contents."""
        cprint(
            f"\n--- {self.test_exclude_nested_directory_and_contents.__doc__}", "cyan"
        )
        app, panel_dir = filtering_test_environment
        rules = ["data/sensitive/"]  # Note the trailing slash for directory
        scanned_files = app._scan_local(panel_dir, rules=rules)
        actual_paths = {path.replace(os.sep, "/") for path in scanned_files.keys()}

        assert "data/sensitive" not in actual_paths
        assert "data/sensitive/private.txt" not in actual_paths
        assert "data/public.txt" in actual_paths  # Should not be filtered
        assert "file.txt" in actual_paths  # Ensure other files are still present

    def test_exclude_files_by_pattern_in_specific_directory(
        self, filtering_test_environment
    ):
        """Test excluding files matching a pattern within a specific directory."""
        cprint(
            f"\n--- {self.test_exclude_files_by_pattern_in_specific_directory.__doc__}",
            "cyan",
        )
        app, panel_dir = filtering_test_environment
        rules = ["logs/*.log"]
        scanned_files = app._scan_local(panel_dir, rules=rules)
        actual_paths = {path.replace(os.sep, "/") for path in scanned_files.keys()}

        assert "logs/app.log" not in actual_paths
        assert "logs/error.log" not in actual_paths
        assert "logs/info.txt" in actual_paths  # Should not be filtered
        assert "file.txt" in actual_paths  # Ensure other files are still present

    def test_file_named_like_directory_pattern(self, filtering_test_environment):
        """Test that a file named like a directory pattern is not excluded if rule is for directory."""
        cprint(f"\n--- {self.test_file_named_like_directory_pattern.__doc__}", "cyan")
        app, panel_dir = filtering_test_environment
        rules = [
            "my_dir_folder/"
        ]  # This rule should only exclude the folder and its contents
        scanned_files = app._scan_local(panel_dir, rules=rules)
        actual_paths = {path.replace(os.sep, "/") for path in scanned_files.keys()}

        assert (
            "my_dir" in actual_paths
        )  # This is a file, should not be excluded by "my_dir_folder/"
        assert "my_dir_folder" not in actual_paths
        assert "my_dir_folder/nested.txt" not in actual_paths
        assert "file.txt" in actual_paths  # Ensure other files are still present


class TestSymbolicLinks:
    """Test suite for symbolic link handling."""

    @pytest.mark.skipif(
        sys.platform == "win32", reason="Symbolic links require admin on Windows"
    )
    def test_symlink_to_file_comparison(self, comparison_test_environment):
        """Test comparison of a symlink to a file vs. a regular file."""
        cprint(f"\n--- {self.test_symlink_to_file_comparison.__doc__}", "magenta")
        app, panel_a_dir, panel_b_dir = comparison_test_environment

        # Create a file in panel A and a symlink to it in panel B
        (panel_a_dir / "target_file.txt").write_text("This is the target.")
        os.symlink(panel_a_dir / "target_file.txt", panel_b_dir / "symlink_to_file.txt")

        # Create a regular file in panel A with the same name as the symlink
        (panel_a_dir / "symlink_to_file.txt").write_text("This is a regular file.")

        # Run comparison
        actual_statuses = _run_comparison(app, panel_a_dir, panel_b_dir)

        # The symlink in panel B points to a file that is different from the regular file in panel A
        assert actual_statuses.get("symlink_to_file.txt") == "Different"

    @pytest.mark.skipif(
        sys.platform == "win32", reason="Symbolic links require admin on Windows"
    )
    def test_symlink_to_directory_comparison(self, comparison_test_environment):
        """Test comparison of a symlink to a directory vs. a regular directory."""
        cprint(f"\n--- {self.test_symlink_to_directory_comparison.__doc__}", "magenta")
        app, panel_a_dir, panel_b_dir = comparison_test_environment

        # Create a directory in panel A and a symlink to it in panel B
        (panel_a_dir / "target_dir").mkdir()
        (panel_a_dir / "target_dir" / "file.txt").write_text("content")
        os.symlink(
            panel_a_dir / "target_dir",
            panel_b_dir / "symlink_to_dir",
            target_is_directory=True,
        )

        # Create a regular directory in panel A with the same name as the symlink
        (panel_a_dir / "symlink_to_dir").mkdir()
        (panel_a_dir / "symlink_to_dir" / "another_file.txt").write_text(
            "different content"
        )

        # Run comparison
        actual_statuses = _run_comparison(app, panel_a_dir, panel_b_dir)

        # The symlinked directory in panel B has different content than the regular directory in panel A
        assert actual_statuses.get("symlink_to_dir") == "Has differences"

    @pytest.mark.skipif(
        sys.platform == "win32", reason="Symbolic links require admin on Windows"
    )
    def test_symlink_pointing_to_identical_directory(self, comparison_test_environment):
        """Test a symlink in panel B pointing to the identical directory in panel A."""
        cprint(
            f"\n--- {self.test_symlink_pointing_to_identical_directory.__doc__}",
            "magenta",
        )
        app, panel_a_dir, panel_b_dir = comparison_test_environment

        # Create a separate target directory in panel A that doesn't exist in panel B
        target_dir = panel_a_dir / "symlink_target_dir"
        target_dir.mkdir(exist_ok=True)
        (target_dir / "file1.txt").write_text("File 1 content")
        (target_dir / "file2.txt").write_text("File 2 content")

        # Create the same structure in panel A as a regular directory
        (panel_a_dir / "shared_dir_identical").mkdir(exist_ok=True)
        (panel_a_dir / "shared_dir_identical" / "file1.txt").write_text(
            "File 1 content"
        )
        (panel_a_dir / "shared_dir_identical" / "file2.txt").write_text(
            "File 2 content"
        )

        # Create symlink in panel B pointing to the target directory
        os.symlink(
            str(target_dir),
            str(panel_b_dir / "shared_dir_identical"),
            target_is_directory=True,
        )

        actual_statuses = _run_comparison(app, panel_a_dir, panel_b_dir)

        # Check that the symlink and directory are considered identical
        assert actual_statuses.get("shared_dir_identical") == "Identical"
