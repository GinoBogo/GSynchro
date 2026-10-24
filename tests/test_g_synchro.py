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

import g_synchro
from g_synchro import GSynchro, OptionsDialog


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

    # Conflict (file vs. directory)
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

    # Correctly unpack the three return values from the parallel status calculation.
    item_statuses, stats, dirty_folders = app._calculate_item_statuses_parallel(
        all_paths, app.files_a, app.files_b, None, None
    )

    # Mimic the application's logic by propagating the "dirty" status to parent folders.
    app._propagate_dirty_folders(item_statuses, dirty_folders)

    return {k.replace(os.sep, "/"): v for k, v in item_statuses.items()}


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
        assert actual_statuses.get("identical.txt") == ("Identical", "green")
        assert actual_statuses.get("different.txt") == ("Different", "orange")
        # After propagation, the root folder should be marked as different.
        assert actual_statuses.get(".") == ("Different", "magenta")

    def test_unique_files_and_directories(self, comparison_test_environment):
        """Test items that exist only in one panel."""
        cprint(f"\n--- {self.test_unique_files_and_directories.__doc__}", "yellow")
        app, panel_a_dir, panel_b_dir = comparison_test_environment
        actual_statuses = _run_comparison(app, panel_a_dir, panel_b_dir)
        assert actual_statuses.get("only_in_a.txt") == ("Only in A", "blue")
        assert actual_statuses.get("only_in_b.txt") == ("Only in B", "red")
        assert actual_statuses.get("subdir") == ("Only in A", "blue")
        assert actual_statuses.get("subdir/subfile.txt") == (
            "Only in A",
            "blue",
        )
        assert actual_statuses.get("subdir_b") == ("Only in B", "red")
        assert actual_statuses.get(".") == ("Different", "magenta")

    def test_deeply_nested_structure(self, comparison_test_environment):
        """Test deeply nested unique items."""
        cprint(f"\n--- {self.test_deeply_nested_structure.__doc__}", "yellow")
        app, panel_a_dir, panel_b_dir = comparison_test_environment
        actual_statuses = _run_comparison(app, panel_a_dir, panel_b_dir)
        assert actual_statuses.get("deep/a/deep_file.txt") == (
            "Only in A",
            "blue",
        )
        # The unique directory itself should be "Only in A".
        assert actual_statuses.get("deep/a") == ("Only in A", "blue")
        # Its parent, however, contains differences and should be marked as such.
        assert actual_statuses.get("deep") == ("Only in A", "blue")

    def test_shared_directory_with_differences(self, comparison_test_environment):
        """Test a directory that exists in both panels but has different content."""
        cprint(f"\n--- {self.test_shared_directory_with_differences.__doc__}", "yellow")
        app, panel_a_dir, panel_b_dir = comparison_test_environment
        actual_statuses = _run_comparison(app, panel_a_dir, panel_b_dir)
        assert actual_statuses.get("shared_dir") == ("Different", "magenta")
        assert actual_statuses.get("shared_dir/a_only.txt") == (
            "Only in A",
            "blue",
        )
        assert actual_statuses.get("shared_dir/b_only.txt") == (
            "Only in B",
            "red",
        )

    def test_type_conflict(self, comparison_test_environment):
        """Test a path that is a file in one panel and a directory in the other."""
        cprint(f"\n--- {self.test_type_conflict.__doc__}", "yellow")
        app, panel_a_dir, panel_b_dir = comparison_test_environment
        actual_statuses = _run_comparison(app, panel_a_dir, panel_b_dir)
        assert actual_statuses.get("conflict") == ("Conflict", "black")


class TestSync:
    """Test suite for synchronization functionality."""

    def test_sync_conflict_resolution(self, comparison_test_environment):
        """Test that syncing a file over a conflicting directory resolves the conflict."""
        cprint(f"\n--- {self.test_sync_conflict_resolution.__doc__}", "yellow")
        app, panel_a_dir, panel_b_dir = comparison_test_environment

        # Run comparison to set up sync states
        _run_comparison(app, panel_a_dir, panel_b_dir)

        # The conflict is: directory in A, file in B
        # Syncing A to B should replace the file in B with the directory from A
        # But since sync only copies files, and "conflict" is the directory in A, but wait

        # Actually, the conflict is "conflict": dir in A, file in B
        # But sync_states for "conflict" is True, but since it's a dir, _get_files_to_copy doesn't include it
        # So no files are synced for "conflict"

        # To test, I need a file in one panel and dir in the other with the same name as a file.

        # The fixture has (panel_a_dir / "conflict").mkdir()  # dir in A
        # (panel_b_dir / "conflict").write_text("I am a file")  # file in B

        # But to sync a file over the dir, I need a file in A with the same name as the dir in B.

        # The fixture doesn't have that. The "conflict" is dir in A, file in B.

        # To test syncing a file over a dir, I need to create a file in A named "conflict", but then it's not a dir.

        # Perhaps modify the fixture for this test.

        # Let's create a file in A and dir in B with the same name.

        # For this test, let's create a specific setup.

        # Add to the fixture or create a new one.

        # For simplicity, let's modify the test to create the conflict.

        # In the test, before running comparison, create a file in A and dir in B.

        # But the fixture already has the conflict as dir in A, file in B.

        # To test file over dir, I need file in A, dir in B.

        # So, let's change the fixture for this test.

        # But since it's a fixture, perhaps create a new fixture or modify in the test.

        # Let's remove the existing conflict and create a new one.

        # In the test:

        # Remove the existing conflict
        if (panel_a_dir / "conflict").exists():
            shutil.rmtree(panel_a_dir / "conflict")
        if (panel_b_dir / "conflict").exists():
            if (panel_b_dir / "conflict").is_file():
                (panel_b_dir / "conflict").unlink()
            else:
                shutil.rmtree(panel_b_dir / "conflict")

        # Create file in A, dir in B
        (panel_a_dir / "conflict").write_text("File from A")
        (panel_b_dir / "conflict").mkdir()

        # Now run comparison
        actual_statuses = _run_comparison(app, panel_a_dir, panel_b_dir)
        assert actual_statuses.get("conflict") == ("Conflict", "black")

        # Now sync A to B
        # Since it's threaded, perhaps call the sync method directly.

        # To test, I can call app._sync_local_to_local directly.

        # Get files_to_copy
        files_to_copy, dirs_to_create = app._get_files_to_copy(app.files_a)
        assert "conflict" in files_to_copy  # since it's a file in A

        # Call _sync_local_to_local
        app._sync_local_to_local(
            files_to_copy, app.files_a, str(panel_b_dir), app.files_b, dirs_to_create
        )

        # Check that the dir in B is replaced by the file from A
        assert (panel_b_dir / "conflict").is_file()
        assert (panel_b_dir / "conflict").read_text() == "File from A"




class TestCopyAndEmptyDirs:
    """Test suite for copying and synchronizing empty directories."""

    def test_get_copy_items_includes_empty_directory(
        self, comparison_test_environment
    ):
        """_get_copy_items should emit an empty directory with is_dir=True."""
        app, panel_a_dir, panel_b_dir = comparison_test_environment
        empty_dir = panel_b_dir / "emptydir"
        empty_dir.mkdir()
        app.files_b = app._scan_local(str(panel_b_dir))
        items = app._get_copy_items(["emptydir"], app.files_b)
        assert len(items) == 1
        assert items[0]["dest_filename"] == "emptydir"
        assert items[0]["is_dir"] is True
        assert items[0]["source_full_path"] == str(empty_dir)

    def test_get_files_to_copy_includes_empty_directory(
        self, comparison_test_environment
    ):
        """_get_files_to_copy should include an empty checked directory in dirs_to_sync."""
        app, panel_a_dir, panel_b_dir = comparison_test_environment
        empty_dir = panel_b_dir / "emptydir"
        empty_dir.mkdir()
        app.files_b = app._scan_local(str(panel_b_dir))
        with app.state_lock:
            app.sync_states = {"emptydir": True}
        files_to_copy, dirs_to_create = app._get_files_to_copy(app.files_b)
        assert files_to_copy == []
        assert dirs_to_create == ["emptydir"]

    def test_sync_local_to_local_creates_empty_directory(
        self, comparison_test_environment
    ):
        """_sync_local_to_local should create an empty directory at the target."""
        app, panel_a_dir, panel_b_dir = comparison_test_environment
        app._sync_local_to_local(
            [],  # files_to_copy
            app.files_b,  # source_files_dict
            str(panel_a_dir),  # target_path
            app.files_a,  # target_files_dict
            ("new_empty_dir",),  # dirs_to_create
        )
        assert (panel_a_dir / "new_empty_dir").is_dir()

    def test_sync_nothing_checked_returns_empty(
        self, comparison_test_environment
    ):
        """With nothing checked, _get_files_to_copy returns empty lists."""
        app, panel_a_dir, panel_b_dir = comparison_test_environment
        app.files_a = app._scan_local(str(panel_a_dir))
        app.files_b = app._scan_local(str(panel_b_dir))
        with app.state_lock:
            app.sync_states = {}
        files_to_copy, dirs_to_create = app._get_files_to_copy(app.files_a)
        assert files_to_copy == []
        assert dirs_to_create == []

class TestUIComparisonDisplay:
    """Test suite for UI display after comparison."""

    def test_only_in_b_item_not_in_panel_a(self, comparison_test_environment):
        """Verify that an item 'Only in B' does not appear in Panel A's UI."""
        cprint(f"\n--- {self.test_only_in_b_item_not_in_panel_a.__doc__}", "yellow")
        app, panel_a_dir, panel_b_dir = comparison_test_environment
        root = app.root

        # 1. Scan folders and populate UI trees
        app.files_a = app._scan_local(str(panel_a_dir))
        app.files_b = app._scan_local(str(panel_b_dir))

        tree_structure_a = app._build_tree_structure(app.files_a)
        tree_structure_b = app._build_tree_structure(app.files_b)

        app._batch_populate_tree(app.tree_a, tree_structure_a)
        app._batch_populate_tree(app.tree_b, tree_structure_b)
        root.update()

        # 2. Run comparison logic to get statuses
        all_paths = set(app.files_a.keys()) | set(app.files_b.keys())
        item_statuses, stats, dirty_folders = app._calculate_item_statuses_parallel(
            all_paths, app.files_a, app.files_b, None, None
        )

        # Propagate dirty status to parent directories
        app._propagate_dirty_folders(item_statuses, dirty_folders)

        # 3. Build tree maps and apply results to UI
        tree_a_map = app._build_tree_map(app.tree_a)
        tree_b_map = app._build_tree_map(app.tree_b)
        app._apply_comparison_to_ui(item_statuses, stats, tree_a_map, tree_b_map)
        root.update()

        # 4. Assertions
        # Check Panel B for 'only_in_b.txt'
        item_id_b = tree_b_map.get("only_in_b.txt")
        assert item_id_b is not None, "'only_in_b.txt' should exist in Panel B map"
        values_b = app.tree_b.item(item_id_b, "values")
        status_b = values_b[3]
        assert status_b == "Only in B", "Status in Panel B should be 'Only in B'"

        # Check Panel A to ensure 'only_in_b.txt' was NOT added
        item_id_a = tree_a_map.get("only_in_b.txt")
        assert item_id_a is None, (
            "Placeholder for 'only_in_b.txt' should NOT exist in Panel A map"
        )

        # Verify no item with that text was added to tree_a
        found_in_a = False
        for item in app.tree_a.get_children():
            if app.tree_a.item(item, "text") == "only_in_b.txt":
                found_in_a = True
                break
        assert not found_in_a, (
            "Item 'only_in_b.txt' should not be present in Panel A's tree view"
        )

    def test_only_in_a_item_not_in_panel_b(self, comparison_test_environment):
        """Verify that an item 'Only in A' does not appear in Panel B's UI."""
        cprint(f"\n--- {self.test_only_in_a_item_not_in_panel_b.__doc__}", "yellow")
        app, panel_a_dir, panel_b_dir = comparison_test_environment
        root = app.root

        # 1. Scan folders and populate UI trees
        app.files_a = app._scan_local(str(panel_a_dir))
        app.files_b = app._scan_local(str(panel_b_dir))

        tree_structure_a = app._build_tree_structure(app.files_a)
        tree_structure_b = app._build_tree_structure(app.files_b)

        app._batch_populate_tree(app.tree_a, tree_structure_a)
        app._batch_populate_tree(app.tree_b, tree_structure_b)
        root.update()

        # 2. Run comparison logic to get statuses
        all_paths = set(app.files_a.keys()) | set(app.files_b.keys())
        item_statuses, stats, dirty_folders = app._calculate_item_statuses_parallel(
            all_paths, app.files_a, app.files_b, None, None
        )

        # Propagate dirty status to parent directories
        app._propagate_dirty_folders(item_statuses, dirty_folders)

        # 3. Build tree maps and apply results to UI
        tree_a_map = app._build_tree_map(app.tree_a)
        tree_b_map = app._build_tree_map(app.tree_b)
        app._apply_comparison_to_ui(item_statuses, stats, tree_a_map, tree_b_map)
        root.update()

        # 4. Assertions
        # Check Panel A for 'only_in_a.txt'
        item_id_a = tree_a_map.get("only_in_a.txt")
        assert item_id_a is not None, "'only_in_a.txt' should exist in Panel A map"
        values_a = app.tree_a.item(item_id_a, "values")
        status_a = values_a[3]
        assert status_a == "Only in A", "Status in Panel A should be 'Only in A'"

        # Check Panel B to ensure 'only_in_a.txt' was NOT added
        item_id_b = tree_b_map.get("only_in_a.txt")
        assert item_id_b is None, (
            "Placeholder for 'only_in_a.txt' should NOT exist in Panel B map"
        )


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


class TestFilterProjects:
    """Test suite for project-selectable filter lists."""

    @pytest.fixture
    def app(self, monkeypatch, tmp_path):
        # Isolate from any real g_synchro.json in the workspace.
        cfg = tmp_path / "g_synchro.json"
        monkeypatch.setattr(g_synchro, "CONFIG_FILE", str(cfg))
        root = tk.Tk()
        root.withdraw()
        app = GSynchro(root)
        yield app
        root.destroy()

    def test_default_project_exists(self, app):
        assert "Default" in app.filter_projects
        assert app.active_filter_project == "Default"
        assert app.filter_rules == []

    def test_set_active_project_reloads_rules(self, app):
        app.filter_projects["Default"] = [{"rule": "*.log", "active": True}]
        app.filter_projects["Python"] = [{"rule": "*.pyc", "active": True}]
        app._set_active_project("Python")
        assert app.active_filter_project == "Python"
        assert app.filter_rules == [{"rule": "*.pyc", "active": True}]
        assert app._get_active_filters() == ["*.pyc"]

    def test_set_active_project_unknown_falls_back_to_default(self, app):
        app.filter_projects["Default"] = [{"rule": "x", "active": True}]
        app._set_active_project("DoesNotExist")
        assert app.active_filter_project == "Default"
        assert app._get_active_filters() == ["x"]

    def test_legacy_rules_migrate_to_default_project(self, app, monkeypatch, tmp_path):
        cfg = tmp_path / "migrate.json"
        cfg.write_text(
            '{"FILTERS": {"rules": [{"rule": "*.log", "active": true}]}}'
        )
        monkeypatch.setattr(g_synchro, "CONFIG_FILE", str(cfg))
        root = tk.Tk()
        root.withdraw()
        migrated = GSynchro(root)
        root.destroy()
        assert migrated.filter_projects == {
            "Default": [{"rule": "*.log", "active": True}]
        }
        assert migrated.active_filter_project == "Default"
        assert migrated._get_active_filters() == ["*.log"]

    def test_unknown_active_project_falls_back(self, app, monkeypatch, tmp_path):
        cfg = tmp_path / "g_synchro.json"
        cfg.write_text(
            '{"FILTERS": {"projects": {"Default": []}, "active_project": "Missing"}}'
        )
        monkeypatch.setattr(g_synchro, "CONFIG_FILE", str(cfg))
        root = tk.Tk()
        root.withdraw()
        recovered = GSynchro(root)
        root.destroy()
        assert recovered.active_filter_project == "Default"

    def test_options_dialog_project_combo_lists_projects(self, app, monkeypatch):
        app.filter_projects = {
            "Default": [{"rule": "*.log", "active": True}],
            "Python": [{"rule": "*.pyc", "active": True}],
        }
        app.active_filter_project = "Python"
        app.filter_rules = [{"rule": "*.pyc", "active": True}]

        dialog = OptionsDialog(app.root, app)
        assert set(dialog.project_combo["values"]) == {"Default", "Python"}
        assert dialog.project_var.get() == "Python"
        dialog.destroy()


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
        assert actual_statuses.get("symlink_to_file.txt") == ("Different", "orange")

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
        assert actual_statuses.get("symlink_to_dir") == ("Different", "magenta")

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
        assert actual_statuses.get("shared_dir_identical") == ("Identical", "green")
