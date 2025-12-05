import os
import sys
import shutil
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
    test_dir = tempfile.mkdtemp()
    dir_a = os.path.join(test_dir, "folder_a")
    dir_b = os.path.join(test_dir, "folder_b")

    # Create test directories
    os.makedirs(dir_a, exist_ok=True)
    os.makedirs(dir_b, exist_ok=True)

    # =======================================================================
    # Create test files and directories
    # =======================================================================

    # Identical file (same content in both folders)
    with open(os.path.join(dir_a, "identical.txt"), "w") as f:
        f.write("This file is the same.")
    with open(os.path.join(dir_b, "identical.txt"), "w") as f:
        f.write("This file is the same.")

    # Different file (same name, different content)
    with open(os.path.join(dir_a, "different.txt"), "w") as f:
        f.write("Content for folder A.")
    with open(os.path.join(dir_b, "different.txt"), "w") as f:
        f.write("Content for folder B.")

    # File only in folder A
    with open(os.path.join(dir_a, "only_in_a.txt"), "w") as f:
        f.write("This file is only in A.")

    # File only in folder B
    with open(os.path.join(dir_b, "only_in_b.txt"), "w") as f:
        f.write("This file is only in B.")

    # Subdirectory structure (different in each folder)
    os.makedirs(os.path.join(dir_a, "subdir"), exist_ok=True)
    os.makedirs(os.path.join(dir_b, "subdir_b"), exist_ok=True)
    with open(os.path.join(dir_a, "subdir", "subfile.txt"), "w") as f:
        f.write("Subfile content.")

    # Shared directory with different content
    os.makedirs(os.path.join(dir_a, "shared_dir"), exist_ok=True)
    os.makedirs(os.path.join(dir_b, "shared_dir"), exist_ok=True)
    with open(os.path.join(dir_a, "shared_dir", "a_only.txt"), "w") as f:
        f.write("a")
    with open(os.path.join(dir_b, "shared_dir", "b_only.txt"), "w") as f:
        f.write("b")

    # Deeper nested structure
    os.makedirs(os.path.join(dir_a, "deep", "a"), exist_ok=True)
    with open(os.path.join(dir_a, "deep", "a", "deep_file.txt"), "w") as f:
        f.write("deep")

    # Type conflict (file vs. directory)
    os.makedirs(os.path.join(dir_a, "conflict"), exist_ok=True)
    with open(os.path.join(dir_b, "conflict"), "w") as f:
        f.write("I am a file")

    # Initialize the application
    app = GSynchro(root)
    app.folder_a.set(dir_a)
    app.folder_b.set(dir_b)

    yield app, dir_a, dir_b

    # Teardown
    shutil.rmtree(test_dir)
    root.destroy()


class TestCompareFolders:
    """Test suite for folder comparison functionality."""

    # ===========================================================================
    # Comparison Test Methods
    # ===========================================================================

    def test_identical_and_different_files(self, comparison_test_environment):
        """Test comparison of identical and different files."""
        cprint(f"\n--- {self.test_identical_and_different_files.__doc__}", "yellow")
        app, dir_a, dir_b = comparison_test_environment
        actual_statuses = self._run_comparison(app, dir_a, dir_b)
        assert actual_statuses.get("identical.txt") == "Identical"
        assert actual_statuses.get("different.txt") == "Different"

    def test_unique_files_and_folders(self, comparison_test_environment):
        """Test items that exist only in one folder."""
        cprint(f"\n--- {self.test_unique_files_and_folders.__doc__}", "yellow")
        app, dir_a, dir_b = comparison_test_environment
        actual_statuses = self._run_comparison(app, dir_a, dir_b)
        assert actual_statuses.get("only_in_a.txt") == "Only in Folder A"
        assert actual_statuses.get("only_in_b.txt") == "Only in Folder B"
        assert actual_statuses.get("subdir") == "Only in Folder A"
        assert (
            actual_statuses.get(os.path.join("subdir", "subfile.txt"))
            == "Only in Folder A"
        )
        assert actual_statuses.get("subdir_b") == "Only in Folder B"

    def test_deeply_nested_structure(self, comparison_test_environment):
        """Test deeply nested unique items."""
        cprint(f"\n--- {self.test_deeply_nested_structure.__doc__}", "yellow")
        app, dir_a, dir_b = comparison_test_environment
        actual_statuses = self._run_comparison(app, dir_a, dir_b)
        assert actual_statuses.get("deep") == "Only in Folder A"
        assert (
            actual_statuses.get(os.path.join("deep", "a", "deep_file.txt"))
            == "Only in Folder A"
        )

    def test_shared_directory_with_differences(self, comparison_test_environment):
        """Test a directory that exists in both panels but has different content."""
        cprint(f"\n--- {self.test_shared_directory_with_differences.__doc__}", "yellow")
        app, dir_a, dir_b = comparison_test_environment
        actual_statuses = self._run_comparison(app, dir_a, dir_b)
        assert actual_statuses.get("shared_dir") == "Contains differences"
        assert (
            actual_statuses.get(os.path.join("shared_dir", "a_only.txt"))
            == "Only in Folder A"
        )
        assert (
            actual_statuses.get(os.path.join("shared_dir", "b_only.txt"))
            == "Only in Folder B"
        )

    def test_type_conflict(self, comparison_test_environment):
        """Test a path that is a file in one panel and a directory in the other."""
        cprint(f"\n--- {self.test_type_conflict.__doc__}", "yellow")
        app, dir_a, dir_b = comparison_test_environment
        actual_statuses = self._run_comparison(app, dir_a, dir_b)
        assert actual_statuses.get("conflict") == "Type conflict"

    # ===========================================================================
    # Helper Methods
    # ===========================================================================

    def _run_comparison(self, app, dir_a, dir_b):
        """Helper method to scan folders and run comparison logic."""
        app.files_a = app._scan_local(dir_a)
        app.files_b = app._scan_local(dir_b)

        all_paths = set(app.files_a.keys()) | set(app.files_b.keys())

        item_statuses, stats = app._calculate_item_statuses(
            all_paths, app.files_a, app.files_b, False, False
        )

        return {k.replace("/", os.sep): v[0] for k, v in item_statuses.items()}


@pytest.fixture
def filtering_test_environment():
    """Set up test environment for filtering tests."""
    root = tk.Tk()
    root.withdraw()
    app = GSynchro(root)

    test_dir = Path(tempfile.mkdtemp())
    dir_a = test_dir / "folder_a"
    dir_a.mkdir(exist_ok=True)

    # =======================================================================
    # Create test structure for filtering
    # =======================================================================

    # Regular files
    (dir_a / "file.txt").write_text("keep")
    (dir_a / "file.log").write_text("filter")

    # Directory to be filtered
    (dir_a / "__pycache__").mkdir()
    (dir_a / "__pycache__" / "cache.pyc").write_text("cache")

    # Another directory to be filtered
    (dir_a / "build").mkdir()
    (dir_a / "build" / "app.exe").write_text("executable")

    # File to be excluded by its full name
    (dir_a / "important_doc.txt").write_text("This document is important.")

    # Files to be excluded by multiple wildcard patterns
    (dir_a / "temp.tmp").write_text("Temporary file.")
    (dir_a / "backup.bak").write_text("Backup file.")
    (dir_a / "keep.txt").write_text("Keep this file.")  # Should not be filtered by new rules

    # Nested directory to be excluded with its contents
    (dir_a / "data" / "sensitive").mkdir(parents=True, exist_ok=True)
    (dir_a / "data" / "sensitive" / "private.txt").write_text("Private data.")
    (dir_a / "data" / "public.txt").write_text("Public data.")  # Should not be filtered

    # Files to be excluded by pattern within a specific directory
    (dir_a / "logs").mkdir(exist_ok=True)
    (dir_a / "logs" / "app.log").write_text("App log.")
    (dir_a / "logs" / "error.log").write_text("Error log.")
    (dir_a / "logs" / "info.txt").write_text("Info text.")  # Should not be filtered by *.log

    # File named similarly to a directory pattern to test rule specificity
    (dir_a / "my_dir").write_text("I am a file named my_dir")
    (dir_a / "my_dir_folder").mkdir(exist_ok=True)
    (dir_a / "my_dir_folder" / "nested.txt").write_text("Nested file.")

    yield app, dir_a

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
        app, dir_a = filtering_test_environment
        # Define filter rules to apply
        rules = ["*.log", "__pycache__", "build/"]

        # Scan the folder with the filter rules
        scanned_files = app._scan_local(dir_a, rules=rules)

        # Get the relative paths of the scanned items
        actual_paths = {path.replace(os.sep, "/") for path in scanned_files.keys()}

        # Assert that filtered items are not present
        assert "file.txt" in actual_paths
        assert "file.log" not in actual_paths
        assert "__pycache__" not in actual_paths
        assert "__pycache__/cache.pyc" not in actual_paths
        assert "build" not in actual_paths
        assert "build/app.exe" not in actual_paths

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
        app, dir_a = filtering_test_environment
        rules = ["important_doc.txt"]
        scanned_files = app._scan_local(dir_a, rules=rules)
        actual_paths = {path.replace(os.sep, "/") for path in scanned_files.keys()}

        assert "important_doc.txt" not in actual_paths
        assert "file.txt" in actual_paths  # Ensure other files are still present

    def test_exclude_multiple_file_patterns(self, filtering_test_environment):
        """Test excluding files using multiple wildcard patterns."""
        cprint(f"\n--- {self.test_exclude_multiple_file_patterns.__doc__}", "cyan")
        app, dir_a = filtering_test_environment
        rules = ["*.tmp", "*.bak"]
        scanned_files = app._scan_local(dir_a, rules=rules)
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
        app, dir_a = filtering_test_environment
        rules = ["data/sensitive/"]  # Note the trailing slash for directory
        scanned_files = app._scan_local(dir_a, rules=rules)
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
        app, dir_a = filtering_test_environment
        rules = ["logs/*.log"]
        scanned_files = app._scan_local(dir_a, rules=rules)
        actual_paths = {path.replace(os.sep, "/") for path in scanned_files.keys()}

        assert "logs/app.log" not in actual_paths
        assert "logs/error.log" not in actual_paths
        assert "logs/info.txt" in actual_paths  # Should not be filtered
        assert "file.txt" in actual_paths  # Ensure other files are still present

    def test_file_named_like_directory_pattern(self, filtering_test_environment):
        """Test that a file named like a directory pattern is not excluded if rule is for directory."""
        cprint(f"\n--- {self.test_file_named_like_directory_pattern.__doc__}", "cyan")
        app, dir_a = filtering_test_environment
        rules = [
            "my_dir_folder/"
        ]  # This rule should only exclude the folder and its contents
        scanned_files = app._scan_local(dir_a, rules=rules)
        actual_paths = {path.replace(os.sep, "/") for path in scanned_files.keys()}

        assert (
            "my_dir" in actual_paths
        )  # This is a file, should not be excluded by "my_dir_folder/"
        assert "my_dir_folder" not in actual_paths
        assert "my_dir_folder/nested.txt" not in actual_paths
        assert "file.txt" in actual_paths  # Ensure other files are still present
