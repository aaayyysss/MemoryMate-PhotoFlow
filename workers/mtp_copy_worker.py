"""
MTP File Copy Worker

QThread-based worker for copying files from MTP devices to local cache.
Prevents UI freezing during file transfer operations.
"""

from PySide6.QtCore import QThread, Signal
import os
import tempfile


class MTPCopyWorker(QThread):
    """
    Background worker for copying files from MTP device via Shell COM API.

    Signals:
        progress(int, int, str): Emits (current_file, total_files, filename)
        finished(list): Emits list of successfully copied file paths
        error(str): Emits error message if operation fails
    """

    # Signals
    progress = Signal(int, int, str)  # current, total, filename
    finished = Signal(list)            # list of copied file paths
    error = Signal(str)                # error message

    def __init__(self, folder_path, max_files=100, max_depth=2):
        """
        Initialize MTP copy worker.

        Args:
            folder_path: Shell namespace path to MTP folder
            max_files: Maximum files to copy (timeout protection)
            max_depth: Maximum recursion depth
        """
        super().__init__()
        self.folder_path = folder_path
        self.max_files = max_files
        self.max_depth = max_depth
        self._cancelled = False

        # Media extensions to copy
        self.media_extensions = {
            '.jpg', '.jpeg', '.png', '.gif', '.heic', '.heif',
            '.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'
        }

    def cancel(self):
        """Cancel the copy operation."""
        self._cancelled = True

    def run(self):
        """Execute file copying in background thread."""
        try:
            print(f"[MTPCopyWorker] Starting background copy from: {self.folder_path}")

            # Import COM libraries in worker thread
            import win32com.client
            import pythoncom

            # CRITICAL: Initialize COM in this thread with apartment model
            # COM objects must be initialized in the thread where they're used
            print(f"[MTPCopyWorker] Initializing COM in worker thread...")
            pythoncom.CoInitialize()

            try:
                # Create Shell.Application in THIS thread (not main UI thread)
                # COM objects are apartment-threaded and cannot be shared across threads
                print(f"[MTPCopyWorker] Creating Shell.Application in worker thread...")
                shell = win32com.client.Dispatch("Shell.Application")

                # Create temp directory
                temp_dir = os.path.join(tempfile.gettempdir(), "memorymate_device_cache")
                os.makedirs(temp_dir, exist_ok=True)

                # Clear old temp files
                try:
                    for old_file in os.listdir(temp_dir):
                        if self._cancelled:
                            return
                        try:
                            os.remove(os.path.join(temp_dir, old_file))
                        except:
                            pass
                except:
                    pass

                print(f"[MTPCopyWorker] Temp cache directory: {temp_dir}")

                # Get folder to copy from
                folder = shell.Namespace(self.folder_path)
                if not folder:
                    self.error.emit(f"Cannot access folder: {self.folder_path}")
                    return

                # Copy files
                media_paths = []
                files_copied = 0
                files_total = 0

                # First pass: count files
                def count_media_files(com_folder, depth=0):
                    nonlocal files_total
                    if depth > self.max_depth or self._cancelled:
                        return

                    try:
                        items = com_folder.Items()
                        for item in items:
                            if self._cancelled:
                                return

                            if files_total >= self.max_files:
                                return

                            if item.IsFolder and depth < self.max_depth:
                                if not item.Name.startswith('.'):
                                    try:
                                        subfolder = shell.Namespace(item.Path)
                                        if subfolder:
                                            count_media_files(subfolder, depth + 1)
                                    except:
                                        pass
                            else:
                                name_lower = item.Name.lower()
                                if any(name_lower.endswith(ext) for ext in self.media_extensions):
                                    files_total += 1
                    except:
                        pass

                count_media_files(folder)

                if self._cancelled:
                    return

                print(f"[MTPCopyWorker] Found {files_total} media files to copy")

                # Second pass: copy files
                def copy_media_files(com_folder, depth=0):
                    nonlocal files_copied, media_paths

                    if depth > self.max_depth or self._cancelled:
                        return

                    if files_copied >= self.max_files:
                        return

                    try:
                        items = com_folder.Items()
                        for item in items:
                            if self._cancelled:
                                print(f"[MTPCopyWorker] Cancelled by user")
                                return

                            if files_copied >= self.max_files:
                                return

                            if item.IsFolder and depth < self.max_depth:
                                if not item.Name.startswith('.'):
                                    try:
                                        subfolder = shell.Namespace(item.Path)
                                        if subfolder:
                                            copy_media_files(subfolder, depth + 1)
                                    except:
                                        pass
                            else:
                                name_lower = item.Name.lower()
                                if any(name_lower.endswith(ext) for ext in self.media_extensions):
                                    try:
                                        # Emit progress
                                        files_copied += 1
                                        self.progress.emit(files_copied, files_total, item.Name)

                                        # Copy file
                                        dest_folder = shell.Namespace(temp_dir)
                                        if dest_folder:
                                            print(f"[MTPCopyWorker] Copying {files_copied}/{files_total}: {item.Name}")

                                            # Copy with flags: 4 = no progress UI, 16 = yes to all
                                            dest_folder.CopyHere(item.Path, 4 | 16)

                                            # Verify copy succeeded
                                            expected_path = os.path.join(temp_dir, item.Name)
                                            if os.path.exists(expected_path):
                                                media_paths.append(expected_path)
                                                print(f"[MTPCopyWorker] ✓ Copied successfully: {item.Name}")
                                            else:
                                                print(f"[MTPCopyWorker] ✗ Copy failed (not found): {item.Name}")

                                    except Exception as e:
                                        print(f"[MTPCopyWorker] ✗ Copy failed for {item.Name}: {e}")

                    except Exception as e:
                        print(f"[MTPCopyWorker] Error at depth {depth}: {e}")

                # Execute copy
                copy_media_files(folder)

                if self._cancelled:
                    print(f"[MTPCopyWorker] Operation cancelled, copied {files_copied}/{files_total} files")
                    return

                print(f"[MTPCopyWorker] Copy complete: {len(media_paths)} files copied successfully")

                # Emit results
                self.finished.emit(media_paths)

            finally:
                # CRITICAL: Uninitialize COM when done
                print(f"[MTPCopyWorker] Uninitializing COM in worker thread...")
                pythoncom.CoUninitialize()

        except Exception as e:
            import traceback
            print(f"[MTPCopyWorker] FATAL ERROR: {e}")
            traceback.print_exc()
            self.error.emit(str(e))
