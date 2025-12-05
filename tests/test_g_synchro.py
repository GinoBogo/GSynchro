import os
import sys
import shutil
import tempfile
import tkinter as tk
import pytest

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
        print(f"\n--- {self.test_identical_and_different_files.__doc__} ---")
        app, dir_a, dir_b = comparison_test_environment
        actual_statuses = self._run_comparison(app, dir_a, dir_b)
        assert actual_statuses.get("identical.txt") == "Identical"
        assert actual_statuses.get("different.txt") == "Different"

    def test_unique_files_and_folders(self, comparison_test_environment):
        """Test items that exist only in one folder."""
        print(f"\n--- {self.test_unique_files_and_folders.__doc__} ---")
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
        print(f"\n--- {self.test_deeply_nested_structure.__doc__} ---")
        app, dir_a, dir_b = comparison_test_environment
        actual_statuses = self._run_comparison(app, dir_a, dir_b)
        assert actual_statuses.get("deep") == "Only in Folder A"
        assert (
            actual_statuses.get(os.path.join("deep", "a", "deep_file.txt"))
            == "Only in Folder A"
        )

    def test_shared_directory_with_differences(self, comparison_test_environment):
        """Test a directory that exists in both panels but has different content."""
        print(f"\n--- {self.test_shared_directory_with_differences.__doc__} ---")
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
        print(f"\n--- {self.test_type_conflict.__doc__} ---")
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

    test_dir = tempfile.mkdtemp()
    dir_a = os.path.join(test_dir, "folder_a")
    os.makedirs(dir_a, exist_ok=True)

    # =======================================================================
    # Create test structure for filtering
    # =======================================================================

    # Regular files
    with open(os.path.join(dir_a, "file.txt"), "w") as f:
        f.write("keep")
    with open(os.path.join(dir_a, "file.log"), "w") as f:
        f.write("filter")

    # Directory to be filtered
    os.makedirs(os.path.join(dir_a, "__pycache__"))
    with open(os.path.join(dir_a, "__pycache__", "cache.pyc"), "w") as f:
        f.write("cache")

    # Another directory to be filtered
    os.makedirs(os.path.join(dir_a, "build"))
    with open(os.path.join(dir_a, "build", "app.exe"), "w") as f:
        f.write("executable")

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
        print(f"\n--- {self.test_file_and_directory_filters.__doc__} ---")
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
