import os
import sys
import shutil
import tempfile
import tkinter as tk
import unittest

# Add project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from g_synchro import GSynchro


class TestCompareFolders(unittest.TestCase):
    def setUp(self):
        """Set up test environment before each test."""
        self.root = tk.Tk()
        self.root.withdraw()  # Hide the main window during tests

        # Create a temporary directory to hold our test folders
        self.test_dir = tempfile.mkdtemp()
        self.dir_a = os.path.join(self.test_dir, "folder_a")
        self.dir_b = os.path.join(self.test_dir, "folder_b")

        # Create test directories
        os.makedirs(self.dir_a, exist_ok=True)
        os.makedirs(self.dir_b, exist_ok=True)

        # --- Create test files and folders ---

        # 1. Identical file
        with open(os.path.join(self.dir_a, "identical.txt"), "w") as f:
            f.write("This file is the same.")
        with open(os.path.join(self.dir_b, "identical.txt"), "w") as f:
            f.write("This file is the same.")

        # 2. Different file
        with open(os.path.join(self.dir_a, "different.txt"), "w") as f:
            f.write("Content for folder A.")
        with open(os.path.join(self.dir_b, "different.txt"), "w") as f:
            f.write("Content for folder B.")

        # 3. File only in A
        with open(os.path.join(self.dir_a, "only_in_a.txt"), "w") as f:
            f.write("This file is only in A.")

        # 4. File only in B
        with open(os.path.join(self.dir_b, "only_in_b.txt"), "w") as f:
            f.write("This file is only in B.")

        # 5. Subdirectory structure
        os.makedirs(os.path.join(self.dir_a, "subdir"), exist_ok=True)
        os.makedirs(os.path.join(self.dir_b, "subdir_b"), exist_ok=True)
        with open(os.path.join(self.dir_a, "subdir", "subfile.txt"), "w") as f:
            f.write("Subfile content.")

        # 6. Shared directory with different content
        os.makedirs(os.path.join(self.dir_a, "shared_dir"), exist_ok=True)
        os.makedirs(os.path.join(self.dir_b, "shared_dir"), exist_ok=True)
        with open(os.path.join(self.dir_a, "shared_dir", "a_only.txt"), "w") as f:
            f.write("a")
        with open(os.path.join(self.dir_b, "shared_dir", "b_only.txt"), "w") as f:
            f.write("b")

        # 7. Deeper nested structure
        os.makedirs(os.path.join(self.dir_a, "deep", "a"), exist_ok=True)
        with open(os.path.join(self.dir_a, "deep", "a", "deep_file.txt"), "w") as f:
            f.write("deep")

        # 8. Type conflict (file vs. directory)
        os.makedirs(os.path.join(self.dir_a, "conflict"), exist_ok=True)
        with open(os.path.join(self.dir_b, "conflict"), "w") as f:
            f.write("I am a file")

        # Initialize the application
        self.app = GSynchro(self.root)
        self.app.folder_a.set(self.dir_a)
        self.app.folder_b.set(self.dir_b)

    def tearDown(self):
        """Clean up test environment after each test."""
        shutil.rmtree(self.test_dir)
        self.root.destroy()

    def _run_comparison(self):
        """Helper method to scan folders and run comparison logic."""
        self.app.files_a = self.app._scan_local(self.dir_a)
        self.app.files_b = self.app._scan_local(self.dir_b)

        all_paths = set(self.app.files_a.keys()) | set(self.app.files_b.keys())

        item_statuses, stats = self.app._calculate_item_statuses(
            all_paths, self.app.files_a, self.app.files_b, False, False
        )

        return {k.replace("/", os.sep): v[0] for k, v in item_statuses.items()}

    def test_identical_and_different_files(self):
        """Test comparison of identical and different files."""
        actual_statuses = self._run_comparison()
        self.assertEqual(actual_statuses.get("identical.txt"), "Identical")
        self.assertEqual(actual_statuses.get("different.txt"), "Different")

    def test_unique_files_and_folders(self):
        """Test items that exist only in one folder."""
        actual_statuses = self._run_comparison()
        self.assertEqual(actual_statuses.get("only_in_a.txt"), "Only in Folder A")
        self.assertEqual(actual_statuses.get("only_in_b.txt"), "Only in Folder B")
        self.assertEqual(actual_statuses.get("subdir"), "Only in Folder A")
        self.assertEqual(
            actual_statuses.get(os.path.join("subdir", "subfile.txt")),
            "Only in Folder A",
        )
        self.assertEqual(actual_statuses.get("subdir_b"), "Only in Folder B")

    def test_deeply_nested_structure(self):
        """Test deeply nested unique items."""
        actual_statuses = self._run_comparison()
        self.assertEqual(actual_statuses.get("deep"), "Only in Folder A")
        self.assertEqual(
            actual_statuses.get(os.path.join("deep", "a", "deep_file.txt")),
            "Only in Folder A",
        )

    def test_shared_directory_with_differences(self):
        """Test a directory that exists in both panels but has different content."""
        actual_statuses = self._run_comparison()
        self.assertEqual(actual_statuses.get("shared_dir"), "Contains differences")
        self.assertEqual(
            actual_statuses.get(os.path.join("shared_dir", "a_only.txt")),
            "Only in Folder A",
        )
        self.assertEqual(
            actual_statuses.get(os.path.join("shared_dir", "b_only.txt")),
            "Only in Folder B",
        )

    def test_type_conflict(self):
        """Test a path that is a file in one panel and a directory in the other."""
        actual_statuses = self._run_comparison()
        self.assertEqual(actual_statuses.get("conflict"), "Type conflict")


class TestFiltering(unittest.TestCase):
    def setUp(self):
        """Set up test environment for filtering tests."""
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = GSynchro(self.root)

        self.test_dir = tempfile.mkdtemp()
        self.dir_a = os.path.join(self.test_dir, "folder_a")
        os.makedirs(self.dir_a, exist_ok=True)

        # Create a structure for testing filters
        with open(os.path.join(self.dir_a, "file.txt"), "w") as f:
            f.write("keep")
        with open(os.path.join(self.dir_a, "file.log"), "w") as f:
            f.write("filter")

        os.makedirs(os.path.join(self.dir_a, "__pycache__"))
        with open(os.path.join(self.dir_a, "__pycache__", "cache.pyc"), "w") as f:
            f.write("cache")

        os.makedirs(os.path.join(self.dir_a, "build"))
        with open(os.path.join(self.dir_a, "build", "app.exe"), "w") as f:
            f.write("executable")

    def tearDown(self):
        """Clean up test environment after each test."""
        shutil.rmtree(self.test_dir)
        self.root.destroy()

    def test_file_and_directory_filters(self):
        """Test that filter rules exclude specified files and directories."""
        # Define filter rules to apply
        rules = ["*.log", "__pycache__", "build/"]

        # Scan the folder with the filter rules
        scanned_files = self.app._scan_local(self.dir_a, rules=rules)

        # Get the relative paths of the scanned items
        actual_paths = {path.replace(os.sep, "/") for path in scanned_files.keys()}

        # Assert that filtered items are not present
        self.assertIn("file.txt", actual_paths)
        self.assertNotIn("file.log", actual_paths)
        self.assertNotIn("__pycache__", actual_paths)
        self.assertNotIn("__pycache__/cache.pyc", actual_paths)
        self.assertNotIn("build", actual_paths)
        self.assertNotIn("build/app.exe", actual_paths)


if __name__ == "__main__":

    class PolishTestResult(unittest.TextTestResult):
        """A test result class that prints [S], [F], [E] for results."""

        def startTest(self, test):
            """Called before each test is run."""
            super().startTest(test)
            self.stream.write(f"\nRunning: {test.shortDescription()}\n")

        def addSuccess(self, test):
            super(unittest.TextTestResult, self).addSuccess(test)
            if self.dots:
                self.stream.write("[SUCCESS]")
                self.stream.flush()

        def addFailure(self, test, err):
            super(unittest.TextTestResult, self).addFailure(test, err)
            if self.dots:
                self.stream.write("[FAILURE]")
                self.stream.flush()

        def addError(self, test, err):
            super(unittest.TextTestResult, self).addError(test, err)
            if self.dots:
                self.stream.write("[ERROR]")
                self.stream.flush()

    # Load tests and run with the custom runner
    compare_suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestCompareFolders)
    filter_suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestFiltering)
    all_tests = unittest.TestSuite([compare_suite, filter_suite])
    runner = unittest.TextTestRunner(resultclass=PolishTestResult, stream=sys.stdout)  # type: ignore
    runner.run(all_tests)
