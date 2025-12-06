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
    dir_a = test_dir / "folder_a"
    dir_b = test_dir / "folder_b"

    # Create test directories
    dir_a.mkdir(exist_ok=True)
    dir_b.mkdir(exist_ok=True)

    # =======================================================================
    # Create test files and directories
    # =======================================================================

    # Identical file (same content in both folders)
    (dir_a / "identical.txt").write_text("This file is the same.")
    (dir_b / "identical.txt").write_text("This file is the same.")

    # Different file (same name, different content)
    (dir_a / "different.txt").write_text("Content for folder A.")
    (dir_b / "different.txt").write_text("Content for folder B.")

    # File only in folder A
    (dir_a / "only_in_a.txt").write_text("This file is only in A.")

    # File only in folder B
    (dir_b / "only_in_b.txt").write_text("This file is only in B.")

    # Subdirectory structure (different in each folder)
    (dir_a / "subdir").mkdir(exist_ok=True)
    (dir_b / "subdir_b").mkdir(exist_ok=True)
    (dir_a / "subdir" / "subfile.txt").write_text("Subfile content.")

    # Shared directory with different content
    (dir_a / "shared_dir").mkdir(exist_ok=True)
    (dir_b / "shared_dir").mkdir(exist_ok=True)
    (dir_a / "shared_dir" / "a_only.txt").write_text("a")
    (dir_b / "shared_dir" / "b_only.txt").write_text("b")

    # Deeper nested structure
    (dir_a / "deep" / "a").mkdir(parents=True, exist_ok=True)
    (dir_a / "deep" / "a" / "deep_file.txt").write_text("deep")

    # Type conflict (file vs. directory)
    (dir_a / "conflict").mkdir(exist_ok=True)
    (dir_b / "conflict").write_text("I am a file")

    # Initialize the application
    app = GSynchro(root)
    app.folder_a.set(str(dir_a))
    app.folder_b.set(str(dir_b))

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
        assert actual_statuses.get("only_in_a.txt") == "Only in A"
        assert actual_statuses.get("only_in_b.txt") == "Only in B"
        assert actual_statuses.get("subdir") == "Only in A"
        assert actual_statuses.get(os.path.join("subdir", "subfile.txt")) == "Only in A"
        assert actual_statuses.get("subdir_b") == "Only in B"

    def test_deeply_nested_structure(self, comparison_test_environment):
        """Test deeply nested unique items."""
        cprint(f"\n--- {self.test_deeply_nested_structure.__doc__}", "yellow")
        app, dir_a, dir_b = comparison_test_environment
        actual_statuses = self._run_comparison(app, dir_a, dir_b)
        assert actual_statuses.get("deep") == "Only in A"
        assert (
            actual_statuses.get(os.path.join("deep", "a", "deep_file.txt"))
            == "Only in A"
        )

    def test_shared_directory_with_differences(self, comparison_test_environment):
        """Test a directory that exists in both panels but has different content."""
        cprint(f"\n--- {self.test_shared_directory_with_differences.__doc__}", "yellow")
        app, dir_a, dir_b = comparison_test_environment
        actual_statuses = self._run_comparison(app, dir_a, dir_b)
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

    # Nested folder inside a filtered directory
    (dir_a / "__pycache__" / "folder_1").mkdir()
    (dir_a / "__pycache__" / "folder_1" / "another.pyc").write_text("nested cache")

    # Another directory to be filtered
    (dir_a / "build").mkdir()
    (dir_a / "build" / "app.exe").write_text("executable")

    # File to be excluded by its full name
    (dir_a / "important_doc.txt").write_text("This document is important.")

    # Files to be excluded by multiple wildcard patterns
    (dir_a / "temp.tmp").write_text("Temporary file.")
    (dir_a / "backup.bak").write_text("Backup file.")
    (dir_a / "keep.txt").write_text(
        "Keep this file."
    )  # Should not be filtered by new rules

    # Nested directory to be excluded with its contents
    (dir_a / "data" / "sensitive").mkdir(parents=True, exist_ok=True)
    (dir_a / "data" / "sensitive" / "private.txt").write_text("Private data.")
    (dir_a / "data" / "public.txt").write_text("Public data.")  # Should not be filtered

    # Files to be excluded by pattern within a specific directory
    (dir_a / "logs").mkdir(exist_ok=True)
    (dir_a / "logs" / "app.log").write_text("App log.")
    (dir_a / "logs" / "error.log").write_text("Error log.")
    (dir_a / "logs" / "info.txt").write_text(
        "Info text."
    )  # Should not be filtered by *.log

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

    def test_nested_folder_in_filtered_directory(self, filtering_test_environment):
        """Test that a nested folder inside a filtered directory is also excluded."""
        cprint(f"\n--- {self.test_nested_folder_in_filtered_directory.__doc__}", "cyan")
        app, dir_a = filtering_test_environment
        rules = ["__pycache__/"]
        scanned_files = app._scan_local(dir_a, rules=rules)
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


class TestSymbolicLinks:
    """Test suite for symbolic link handling."""

    @pytest.mark.skipif(
        sys.platform == "win32", reason="Symbolic links require admin on Windows"
    )
    def test_symlink_to_file_comparison(self, comparison_test_environment):
        """Test comparison of a symlink to a file vs. a regular file."""
        cprint(f"\n--- {self.test_symlink_to_file_comparison.__doc__}", "magenta")
        app, dir_a, dir_b = comparison_test_environment

        # Create a file in dir_a and a symlink to it in dir_b
        (dir_a / "target_file.txt").write_text("This is the target.")
        os.symlink(dir_a / "target_file.txt", dir_b / "symlink_to_file.txt")

        # Create a regular file in dir_a with the same name as the symlink
        (dir_a / "symlink_to_file.txt").write_text("This is a regular file.")

        # Run comparison
        actual_statuses = self._run_comparison(app, dir_a, dir_b)

        # The symlink in B points to a file that is different from the regular file in A
        assert actual_statuses.get("symlink_to_file.txt") == "Different"

    @pytest.mark.skipif(
        sys.platform == "win32", reason="Symbolic links require admin on Windows"
    )
    def test_symlink_to_directory_comparison(self, comparison_test_environment):
        """Test comparison of a symlink to a directory vs. a regular directory."""
        cprint(f"\n--- {self.test_symlink_to_directory_comparison.__doc__}", "magenta")
        app, dir_a, dir_b = comparison_test_environment

        # Create a directory in dir_a and a symlink to it in dir_b
        (dir_a / "target_dir").mkdir()
        (dir_a / "target_dir" / "file.txt").write_text("content")
        os.symlink(
            dir_a / "target_dir", dir_b / "symlink_to_dir", target_is_directory=True
        )

        # Create a regular directory in dir_a with the same name as the symlink
        (dir_a / "symlink_to_dir").mkdir()
        (dir_a / "symlink_to_dir" / "another_file.txt").write_text("different content")

        # Run comparison
        actual_statuses = self._run_comparison(app, dir_a, dir_b)

        # The symlinked directory in B has different content than the regular directory in A
        assert actual_statuses.get("symlink_to_dir") == "Has differences"

    @pytest.mark.skipif(
        sys.platform == "win32", reason="Symbolic links require admin on Windows"
    )
    def test_symlink_pointing_to_identical_directory(self, comparison_test_environment):
        """Test a symlink in B pointing to the identical directory in A."""
        cprint(
            f"\n--- {self.test_symlink_pointing_to_identical_directory.__doc__}",
            "magenta",
        )
        app, dir_a, dir_b = comparison_test_environment

        # Remove the pre-existing 'shared_dir' in dir_b created by the fixture
        # to allow creating a symlink with the same name.
        shutil.rmtree(dir_b / "shared_dir")
        (dir_b / "shared_dir").unlink(
            missing_ok=True
        )  # Ensure it's gone if it was a file

        # In B, create a symlink named 'shared_dir' pointing to the 'shared_dir' in A
        os.symlink(
            str(dir_a / "shared_dir"),
            str(dir_b / "shared_dir"),
            target_is_directory=True,
        )

        actual_statuses = self._run_comparison(app, dir_a, dir_b)

        assert actual_statuses.get("shared_dir") == "Identical"

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
