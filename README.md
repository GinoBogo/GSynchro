# GSynchro

GSynchro is a graphical user interface (GUI) tool designed for comparing and synchronizing files and directories between two locations. It provides a clear side-by-side view to visualize differences and enables flexible synchronization operations. It is particularly useful for developers, system administrators, or anyone needing to maintain consistency between local and remote file sets.

## Key Features

*   **Side-by-Side Comparison**: Visually compare the contents of two directories in a hierarchical tree view.
*   **Local & Remote Support**: Synchronize between:
    *   Local folder to local folder.
    *   Local folder to a remote folder over SSH.
    *   Remote folder to a local folder over SSH.
    *   Remote folder to another remote folder over SSH.
*   **SSH Integration**: Built-in support for SSH connections using `paramiko` and `scp` for secure remote operations. It includes an SSH connection tester and a remote directory browser.
*   **Detailed Status**: Files are marked with clear statuses after comparison:
    *   `Identical`: Files are the same.
    *   `Different`: Files have the same name but different content (based on size and MD5 hash).
    *   `Only in A` / `Only in B`: File exists only in one of the two locations.
    *   `Contains differences`: A directory contains items that are not identical.
*   **Flexible Synchronization**:
    *   Synchronize from left to right (`A` -> `B`) or right to left (`B` -> `A`).
    *   Selectively include or exclude individual files and directories from a sync operation using checkboxes.
*   **Powerful Filtering**: Define custom filter rules (with wildcard support) to exclude files and directories (like `.git`, `__pycache__`, `*.log`) from scans and comparisons.
*   **File Operations**:
    *   Open files directly from the application (downloads remote files to a temporary location first).
    *   Delete files and directories from both local and remote locations.
*   **Persistent Configuration**: Automatically saves your settings (SSH details, folder history, filter rules, and window size) to a `g_synchro.json` file for convenience.
*   **Cross-Platform**: Built with Python's standard `tkinter` library, making it compatible with Windows, macOS, and Linux.
