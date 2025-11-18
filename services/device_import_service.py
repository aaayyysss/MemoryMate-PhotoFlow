"""
Device Import Service - Import photos/videos from mobile devices

Provides Photos-app-like import workflow:
- Scan device for media files
- Show thumbnails for selection
- Import selected files to project
- Skip duplicates (hash-based detection)
- Background import with progress

Usage:
    service = DeviceImportService(db, project_id)
    files = service.scan_device_folder("/path/to/device/DCIM")
    service.import_files(files, destination_folder)
"""

import os
import shutil
import hashlib
from pathlib import Path
from typing import List, Dict, Optional, Callable
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool


@dataclass
class DeviceMediaFile:
    """Represents a media file on device"""
    path: str                    # Full path on device
    filename: str                # Original filename
    size_bytes: int              # File size
    modified_date: datetime      # Last modified date
    thumbnail_path: Optional[str] = None  # Thumbnail preview
    already_imported: bool = False        # Already in library
    file_hash: Optional[str] = None       # SHA256 hash for dedup


class DeviceImportService:
    """Service for importing media from mobile devices"""

    MEDIA_EXTENSIONS = {
        '.jpg', '.jpeg', '.png', '.gif', '.heic', '.heif',
        '.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'
    }

    def __init__(self, db, project_id: int):
        """
        Initialize import service.

        Args:
            db: ReferenceDB instance
            project_id: Target project ID
        """
        self.db = db
        self.project_id = project_id

    def scan_device_folder(self, folder_path: str, max_depth: int = 3) -> List[DeviceMediaFile]:
        """
        Scan device folder for media files.

        Args:
            folder_path: Device folder to scan
            max_depth: Maximum recursion depth

        Returns:
            List of DeviceMediaFile objects
        """
        media_files = []
        folder = Path(folder_path)

        if not folder.exists():
            return media_files

        def scan_recursive(current_folder: Path, depth: int = 0):
            if depth > max_depth:
                return

            try:
                for item in current_folder.iterdir():
                    if item.is_file():
                        if item.suffix.lower() in self.MEDIA_EXTENSIONS:
                            # Create media file entry
                            stat = item.stat()
                            media_file = DeviceMediaFile(
                                path=str(item),
                                filename=item.name,
                                size_bytes=stat.st_size,
                                modified_date=datetime.fromtimestamp(stat.st_mtime)
                            )

                            # Check if already imported (by hash)
                            media_file.file_hash = self._calculate_hash(str(item))
                            media_file.already_imported = self._is_already_imported(media_file.file_hash)

                            media_files.append(media_file)

                    elif item.is_dir() and not item.name.startswith('.'):
                        # Recurse into subdirectories
                        scan_recursive(item, depth + 1)

            except (PermissionError, OSError) as e:
                print(f"[DeviceImport] Cannot access {current_folder}: {e}")

        scan_recursive(folder)
        return media_files

    def _calculate_hash(self, file_path: str, chunk_size: int = 8192) -> str:
        """
        Calculate SHA256 hash of file for duplicate detection.

        Args:
            file_path: Path to file
            chunk_size: Read chunk size

        Returns:
            SHA256 hexdigest
        """
        try:
            sha256 = hashlib.sha256()
            with open(file_path, 'rb') as f:
                while chunk := f.read(chunk_size):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except Exception as e:
            print(f"[DeviceImport] Hash calculation failed for {file_path}: {e}")
            return ""

    def _is_already_imported(self, file_hash: str) -> bool:
        """
        Check if file with this hash is already in project.

        Args:
            file_hash: SHA256 hash

        Returns:
            True if already imported
        """
        if not file_hash:
            return False

        try:
            with self.db._connect() as conn:
                cur = conn.execute("""
                    SELECT COUNT(*) FROM photo_metadata
                    WHERE project_id = ? AND file_hash = ?
                """, (self.project_id, file_hash))
                count = cur.fetchone()[0]
                return count > 0
        except Exception:
            # If file_hash column doesn't exist, can't check
            return False

    def import_files(
        self,
        files: List[DeviceMediaFile],
        destination_folder_id: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> Dict[str, any]:
        """
        Import files from device to project.

        Args:
            files: List of DeviceMediaFile to import
            destination_folder_id: Target folder ID (None for root)
            progress_callback: Callback(current, total, filename)

        Returns:
            Dict with import statistics
        """
        stats = {
            'total': len(files),
            'imported': 0,
            'skipped': 0,
            'failed': 0,
            'errors': []
        }

        # Get project directory
        project_dir = self._get_project_directory()
        if not project_dir:
            stats['errors'].append("Could not determine project directory")
            return stats

        # Create import subdirectory with timestamp
        import_dir = Path(project_dir) / f"imported_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        import_dir.mkdir(parents=True, exist_ok=True)

        for idx, media_file in enumerate(files, 1):
            if progress_callback:
                progress_callback(idx, len(files), media_file.filename)

            # Skip if already imported
            if media_file.already_imported:
                stats['skipped'] += 1
                continue

            try:
                # Copy file to import directory
                source_path = Path(media_file.path)
                dest_path = import_dir / media_file.filename

                # Handle duplicate filenames
                counter = 1
                while dest_path.exists():
                    stem = source_path.stem
                    suffix = source_path.suffix
                    dest_path = import_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

                shutil.copy2(source_path, dest_path)

                # Register in database
                self._register_imported_file(
                    str(dest_path),
                    media_file.file_hash,
                    destination_folder_id
                )

                stats['imported'] += 1

            except Exception as e:
                error_msg = f"Failed to import {media_file.filename}: {e}"
                print(f"[DeviceImport] {error_msg}")
                stats['errors'].append(error_msg)
                stats['failed'] += 1

        return stats

    def _get_project_directory(self) -> Optional[str]:
        """
        Get project directory path from database.

        Returns:
            Project directory path or None
        """
        try:
            with self.db._connect() as conn:
                cur = conn.execute("""
                    SELECT root_folder FROM projects WHERE id = ?
                """, (self.project_id,))
                row = cur.fetchone()
                return row[0] if row else None
        except Exception as e:
            print(f"[DeviceImport] Could not get project directory: {e}")
            return None

    def _register_imported_file(
        self,
        file_path: str,
        file_hash: str,
        folder_id: Optional[int]
    ):
        """
        Register imported file in database.

        Args:
            file_path: Path to imported file
            file_hash: SHA256 hash
            folder_id: Destination folder ID
        """
        try:
            # Use existing add_project_image method
            if hasattr(self.db, 'add_project_image'):
                self.db.add_project_image(
                    project_id=self.project_id,
                    image_path=file_path,
                    folder_id=folder_id
                )

            # Store hash for future dedup (if column exists)
            try:
                with self.db._connect() as conn:
                    conn.execute("""
                        UPDATE photo_metadata
                        SET file_hash = ?
                        WHERE project_id = ? AND file_path = ?
                    """, (file_hash, self.project_id, file_path))
                    conn.commit()
            except Exception:
                # file_hash column doesn't exist, skip
                pass

        except Exception as e:
            print(f"[DeviceImport] Failed to register {file_path}: {e}")
            raise


class DeviceImportWorker(QRunnable):
    """Background worker for device imports"""

    class Signals(QObject):
        """Worker signals"""
        progress = Signal(int, int, str)  # current, total, filename
        finished = Signal(dict)            # statistics
        error = Signal(str)                # error message

    def __init__(
        self,
        import_service: DeviceImportService,
        files: List[DeviceMediaFile],
        destination_folder_id: Optional[int] = None
    ):
        super().__init__()
        self.import_service = import_service
        self.files = files
        self.destination_folder_id = destination_folder_id
        self.signals = self.Signals()

    def run(self):
        """Run import in background thread"""
        try:
            stats = self.import_service.import_files(
                self.files,
                self.destination_folder_id,
                progress_callback=self._on_progress
            )
            self.signals.finished.emit(stats)
        except Exception as e:
            self.signals.error.emit(str(e))

    def _on_progress(self, current: int, total: int, filename: str):
        """Emit progress signal"""
        self.signals.progress.emit(current, total, filename)
