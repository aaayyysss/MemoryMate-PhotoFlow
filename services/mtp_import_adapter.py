"""
MTP Import Adapter - Bridge between MTP devices and import workflow

Adapts MTP device access to work with the DeviceImportService infrastructure.
Since MTP devices require special Shell COM API access, this adapter:

1. Enumerates files from MTP device (via Shell COM API)
2. Creates DeviceMediaFile objects compatible with import dialog
3. Copies selected files from MTP to library during import
4. Tracks device ID and folder for proper organization

Usage:
    adapter = MTPImportAdapter(db, project_id)
    media_files = adapter.enumerate_mtp_folder(mtp_path, device_name, folder_name)
    # Show in import dialog
    imported_paths = adapter.import_selected_files(selected_files, import_options)
"""

import os
import tempfile
from pathlib import Path
from typing import List, Optional, Dict
from datetime import datetime
from dataclasses import dataclass

from services.device_import_service import DeviceMediaFile


@dataclass
class MTPFileInfo:
    """Information about a file on MTP device"""
    mtp_path: str           # Shell namespace path
    filename: str           # Original filename
    size_bytes: int         # File size
    modified_date: datetime # Last modified date
    is_folder: bool = False


class MTPImportAdapter:
    """Adapter for importing from MTP devices using Shell COM API"""

    MEDIA_EXTENSIONS = {
        '.jpg', '.jpeg', '.png', '.gif', '.heic', '.heif',
        '.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v', '.3gp'
    }

    def __init__(self, db, project_id: int):
        """
        Initialize MTP import adapter.

        Args:
            db: ReferenceDB instance
            project_id: Target project ID
        """
        self.db = db
        self.project_id = project_id

    def enumerate_mtp_folder(
        self,
        mtp_path: str,
        device_name: str,
        folder_name: str,
        max_files: int = 500
    ) -> List[DeviceMediaFile]:
        """
        Enumerate files in MTP device folder without copying.

        This creates DeviceMediaFile objects that can be shown in import dialog.
        Files are NOT copied yet - just enumerated for preview.

        Args:
            mtp_path: Shell namespace path to device folder
            device_name: Device name (e.g., "A54 von Ammar")
            folder_name: Folder name (e.g., "Camera")
            max_files: Maximum files to enumerate

        Returns:
            List of DeviceMediaFile objects for import dialog
        """
        print(f"[MTPAdapter] Enumerating MTP folder: {folder_name} on {device_name}")
        print(f"[MTPAdapter] Path: {mtp_path}")

        try:
            # Import COM libraries
            import win32com.client
            import pythoncom

            # Initialize COM in this thread
            pythoncom.CoInitialize()

            try:
                # Navigate to device folder using "This PC" approach
                shell = win32com.client.Dispatch("Shell.Application")
                computer = shell.Namespace(17)  # This PC

                if not computer:
                    raise Exception("Cannot access 'This PC' namespace")

                # Find device and navigate to folder
                folder = self._navigate_to_mtp_folder(shell, computer, mtp_path)

                if not folder:
                    raise Exception(f"Cannot access folder: {mtp_path}")

                print(f"[MTPAdapter] Successfully accessed folder")

                # Enumerate files
                media_files = []
                file_count = 0

                items = folder.Items()
                for item in items:
                    if file_count >= max_files:
                        print(f"[MTPAdapter] Reached max files limit ({max_files})")
                        break

                    if not item.IsFolder:
                        filename = item.Name
                        name_lower = filename.lower()

                        # Check if it's a media file
                        if any(name_lower.endswith(ext) for ext in self.MEDIA_EXTENSIONS):
                            # Get file info
                            try:
                                size = item.Size if hasattr(item, 'Size') else 0
                                modified = item.ModifyDate if hasattr(item, 'ModifyDate') else datetime.now()
                                if isinstance(modified, str):
                                    try:
                                        modified = datetime.fromisoformat(modified)
                                    except:
                                        modified = datetime.now()

                                # Create temp path for thumbnail (not actual file yet)
                                # We'll use item.Path as identifier
                                temp_path = f"mtp://{device_name}/{folder_name}/{filename}"

                                # Create DeviceMediaFile
                                media_file = DeviceMediaFile(
                                    path=temp_path,  # Virtual path for now
                                    filename=filename,
                                    size_bytes=size,
                                    modified_date=modified,
                                    already_imported=False,
                                    device_folder=folder_name
                                )

                                # Store actual MTP path for later import
                                media_file.mtp_item_path = item.Path  # Custom attribute

                                media_files.append(media_file)
                                file_count += 1

                                if file_count % 10 == 0:
                                    print(f"[MTPAdapter] Enumerated {file_count} files...")

                            except Exception as e:
                                print(f"[MTPAdapter] Error getting info for {filename}: {e}")
                                continue

                print(f"[MTPAdapter] ✓ Enumerated {len(media_files)} media files")
                return media_files

            finally:
                pythoncom.CoUninitialize()

        except Exception as e:
            print(f"[MTPAdapter] ERROR enumerating MTP folder: {e}")
            import traceback
            traceback.print_exc()
            return []

    def _navigate_to_mtp_folder(self, shell, computer, target_path: str):
        """
        Navigate from 'This PC' to target MTP folder.

        Args:
            shell: Shell.Application COM object
            computer: Computer folder namespace
            target_path: Target MTP path

        Returns:
            Folder object or None
        """
        try:
            # Find device
            device_folder = None
            storage_folder = None

            for item in computer.Items():
                if item.IsFolder and not item.IsFileSystem:
                    if item.Path and item.Path in target_path:
                        device_folder = shell.Namespace(item.Path)

                        if device_folder:
                            # Find storage location
                            storage_items = device_folder.Items()
                            for storage_item in storage_items:
                                if storage_item.IsFolder:
                                    if storage_item.Path and storage_item.Path in target_path:
                                        storage_folder = storage_item.GetFolder
                                        break
                            if storage_folder:
                                break

            if not storage_folder:
                return None

            # Navigate through subfolders
            folder = storage_folder

            if "}" in target_path:
                path_parts = target_path.split("}")
                if len(path_parts) > 1:
                    subfolder_path = path_parts[-1].strip("\\")
                    if subfolder_path:
                        subfolders = [p for p in subfolder_path.split("\\") if p]

                        for subfolder_name in subfolders:
                            found = False
                            items = folder.Items()
                            for item in items:
                                if item.IsFolder and item.Name == subfolder_name:
                                    folder = item.GetFolder
                                    found = True
                                    break

                            if not found:
                                return None

            return folder

        except Exception as e:
            print(f"[MTPAdapter] Error navigating to folder: {e}")
            return None

    def import_selected_files(
        self,
        mtp_path: str,
        selected_files: List[DeviceMediaFile],
        device_name: str,
        folder_name: str,
        import_date: Optional[datetime] = None
    ) -> List[str]:
        """
        Import selected files from MTP device to library.

        Copies files from device to proper library structure and adds to database.

        Args:
            mtp_path: MTP folder path to import from
            selected_files: List of DeviceMediaFile objects to import
            device_name: Device name for organization
            folder_name: Folder name for organization
            import_date: Import date (defaults to now)

        Returns:
            List of imported file paths in library
        """
        if not import_date:
            import_date = datetime.now()

        import_date_str = import_date.strftime("%Y-%m-%d")

        # Prepare destination directory
        # Structure: Device_Imports/{Device}/{Folder}/{Date}/
        # Use current working directory as base (aligns with existing scan repository pattern)
        cwd = Path.cwd()
        dest_base = cwd / "Device_Imports"
        device_safe = self._sanitize_filename(device_name)
        folder_safe = self._sanitize_filename(folder_name)

        dest_folder = dest_base / device_safe / folder_safe / import_date_str
        dest_folder.mkdir(parents=True, exist_ok=True)

        print(f"[MTPAdapter] Importing to: {dest_folder}")

        imported_paths = []

        try:
            # Import COM libraries
            import win32com.client
            import pythoncom

            # Initialize COM
            pythoncom.CoInitialize()

            try:
                shell = win32com.client.Dispatch("Shell.Application")
                dest_namespace = shell.Namespace(str(dest_folder))

                if not dest_namespace:
                    raise Exception(f"Cannot access destination folder: {dest_folder}")

                # Navigate to source MTP folder (same way as enumeration)
                # We need to navigate once, then find items by filename
                computer = shell.Namespace(17)  # This PC
                if not computer:
                    raise Exception("Cannot access 'This PC' namespace")

                # Navigate to MTP folder using the original mtp_path
                # We'll extract it from the first file's path
                if not selected_files:
                    print(f"[MTPAdapter] No files to import")
                    return imported_paths

                # Navigate to MTP folder using the same method as enumeration
                # This ensures we can access the files consistently
                source_folder = self._navigate_to_mtp_folder(shell, computer, mtp_path)

                if not source_folder:
                    raise Exception("Cannot navigate to source MTP folder during import")

                print(f"[MTPAdapter] Successfully accessed source folder for import")

                # Get all items from source folder once
                source_items_dict = {}
                for item in source_folder.Items():
                    if not item.IsFolder:
                        source_items_dict[item.Name] = item

                # Import each file
                for idx, media_file in enumerate(selected_files, 1):
                    print(f"[MTPAdapter] Importing {idx}/{len(selected_files)}: {media_file.filename}")

                    try:
                        # Find source item by filename
                        source_item = source_items_dict.get(media_file.filename)

                        if source_item:
                            # Copy file
                            dest_namespace.CopyHere(source_item, 4 | 16)

                            # Wait for copy to complete
                            expected_path = dest_folder / media_file.filename
                            import time
                            max_wait = 30
                            waited = 0

                            while waited < max_wait:
                                if expected_path.exists():
                                    print(f"[MTPAdapter] ✓ Copied {media_file.filename}")
                                    imported_paths.append(str(expected_path))

                                    # Add to database
                                    self._add_to_database(
                                        expected_path,
                                        device_name,
                                        folder_name,
                                        import_date
                                    )
                                    break

                                time.sleep(0.1)
                                waited += 0.1
                            else:
                                print(f"[MTPAdapter] ✗ Timeout importing {media_file.filename}")
                        else:
                            print(f"[MTPAdapter] ✗ Cannot find source item: {media_file.filename}")

                    except Exception as e:
                        print(f"[MTPAdapter] ✗ Error importing {media_file.filename}: {e}")
                        continue

                print(f"[MTPAdapter] ✓ Import complete: {len(imported_paths)}/{len(selected_files)} files")
                return imported_paths

            finally:
                pythoncom.CoUninitialize()

        except Exception as e:
            print(f"[MTPAdapter] ERROR during import: {e}")
            import traceback
            traceback.print_exc()
            return imported_paths

    def _add_to_database(
        self,
        file_path: Path,
        device_name: str,
        folder_name: str,
        import_date: datetime
    ):
        """
        Add imported file to database.

        Args:
            file_path: Path to imported file
            device_name: Source device name
            folder_name: Source folder name
            import_date: Import date
        """
        try:
            # Create branch_key for device folder organization
            # Format: "device_folder:Camera [A54 von Ammar]"
            branch_key = f"device_folder:{folder_name} [{device_name}]"

            # Add to project_images table (existing schema)
            self.db.execute("""
                INSERT INTO project_images (
                    project_id, branch_key, image_path, label
                ) VALUES (?, ?, ?, ?)
            """, (
                self.project_id,
                branch_key,
                str(file_path),
                None  # No label for device imports
            ))

            self.db.commit()
            print(f"[MTPAdapter] ✓ Added to database: {file_path.name} (branch: {branch_key})")

        except Exception as e:
            print(f"[MTPAdapter] ✗ Error adding to database: {e}")
            import traceback
            traceback.print_exc()

    def _sanitize_filename(self, name: str) -> str:
        """Sanitize filename for filesystem compatibility"""
        # Remove or replace invalid characters
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, '_')
        return name.strip()
