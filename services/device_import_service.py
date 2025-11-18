"""
Device Import Service - Import photos/videos from mobile devices

Phase 2: Incremental Sync Support
- Scan device and track files in database
- Detect new files since last import
- Track import sessions and history
- Preserve device folder structure (Camera/Screenshots)
- Detect deleted files on device

Usage:
    service = DeviceImportService(db, project_id, device_id="android:ABC123")

    # Get only new files since last import
    new_files = service.scan_incremental("/path/to/device/DCIM")

    # Import with session tracking
    session_id = service.start_import_session()
    stats = service.import_files(new_files)
    service.complete_import_session(session_id, stats)
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
    device_folder: Optional[str] = None   # Device folder (Camera/Screenshots/etc)
    import_status: str = "new"            # new/imported/skipped/modified


class DeviceImportService:
    """Service for importing media from mobile devices"""

    MEDIA_EXTENSIONS = {
        '.jpg', '.jpeg', '.png', '.gif', '.heic', '.heif',
        '.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'
    }

    def __init__(self, db, project_id: int, device_id: Optional[str] = None):
        """
        Initialize import service.

        Args:
            db: ReferenceDB instance
            project_id: Target project ID
            device_id: Device identifier for tracking (Phase 2)
        """
        self.db = db
        self.project_id = project_id
        self.device_id = device_id
        self.current_session_id = None  # Set when import session starts

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

    def scan_with_tracking(
        self,
        folder_path: str,
        root_path: str,
        max_depth: int = 3
    ) -> List[DeviceMediaFile]:
        """
        Scan device folder and track all files in device_files table (Phase 2).

        Args:
            folder_path: Folder to scan
            root_path: Device root path (for extracting device folder)
            max_depth: Maximum recursion depth

        Returns:
            List of DeviceMediaFile with tracking info
        """
        if not self.device_id:
            # Fall back to basic scan if no device_id
            return self.scan_device_folder(folder_path, max_depth)

        media_files = []
        folder = Path(folder_path)
        root = Path(root_path)

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
                            device_path = str(item)
                            file_hash = self._calculate_hash(device_path)

                            # Extract device folder (Camera/Screenshots/etc)
                            device_folder = self._extract_device_folder(device_path, str(root))

                            # Check if already tracked in database
                            import_status, already_imported = self._check_file_status(
                                device_path, file_hash
                            )

                            media_file = DeviceMediaFile(
                                path=device_path,
                                filename=item.name,
                                size_bytes=stat.st_size,
                                modified_date=datetime.fromtimestamp(stat.st_mtime),
                                file_hash=file_hash,
                                device_folder=device_folder,
                                import_status=import_status,
                                already_imported=already_imported
                            )

                            media_files.append(media_file)

                            # Track file in database
                            if self.device_id:
                                try:
                                    self.db.track_device_file(
                                        device_id=self.device_id,
                                        device_path=device_path,
                                        device_folder=device_folder,
                                        file_hash=file_hash,
                                        file_size=stat.st_size,
                                        file_mtime=datetime.fromtimestamp(stat.st_mtime).isoformat()
                                    )
                                except Exception as e:
                                    print(f"[DeviceImport] Failed to track file: {e}")

                    elif item.is_dir() and not item.name.startswith('.'):
                        # Recurse into subdirectories
                        scan_recursive(item, depth + 1)

            except (PermissionError, OSError) as e:
                print(f"[DeviceImport] Cannot access {current_folder}: {e}")

        scan_recursive(folder)
        return media_files

    def scan_incremental(self, folder_path: str, root_path: str, max_depth: int = 3) -> List[DeviceMediaFile]:
        """
        Scan device and return ONLY new files since last import (Phase 2).

        Args:
            folder_path: Folder to scan
            root_path: Device root path
            max_depth: Maximum recursion depth

        Returns:
            List of NEW DeviceMediaFile only
        """
        # Scan with tracking
        all_files = self.scan_with_tracking(folder_path, root_path, max_depth)

        # Filter to only new files
        new_files = [f for f in all_files if f.import_status == "new"]

        print(f"[DeviceImport] Incremental scan: {len(new_files)} new / {len(all_files)} total")
        return new_files

    def start_import_session(self, import_type: str = "manual") -> int:
        """
        Start a new import session (Phase 2).

        Args:
            import_type: Type of import ("manual", "auto", "incremental")

        Returns:
            Session ID
        """
        if not self.device_id:
            raise ValueError("device_id required for session tracking")

        session_id = self.db.create_import_session(
            device_id=self.device_id,
            project_id=self.project_id,
            import_type=import_type
        )
        self.current_session_id = session_id
        print(f"[DeviceImport] Started import session {session_id}")
        return session_id

    def complete_import_session(self, session_id: int, stats: Dict[str, any]):
        """
        Complete import session with statistics (Phase 2).

        Args:
            session_id: Session ID
            stats: Import statistics dict
        """
        photos_imported = stats.get('imported', 0)
        duplicates_skipped = stats.get('skipped', 0)
        bytes_imported = stats.get('bytes_imported', 0)

        error_message = None
        if stats.get('failed', 0) > 0:
            error_message = "; ".join(stats.get('errors', []))

        self.db.complete_import_session(
            session_id=session_id,
            photos_imported=photos_imported,
            videos_imported=0,  # TODO: Separate video tracking
            duplicates_skipped=duplicates_skipped,
            bytes_imported=bytes_imported,
            duration_seconds=None,
            error_message=error_message
        )

        self.current_session_id = None
        print(f"[DeviceImport] Completed session {session_id}: {photos_imported} imported, {duplicates_skipped} skipped")

    def _extract_device_folder(self, device_path: str, root_path: str) -> str:
        """
        Extract device folder name from path (Camera/Screenshots/WhatsApp/etc).

        Args:
            device_path: Full path on device
            root_path: Device root path

        Returns:
            Folder name or "Unknown"
        """
        try:
            rel_path = Path(device_path).relative_to(Path(root_path))
            parts = rel_path.parts

            # Look for meaningful folder names
            folder_indicators = [
                "Camera", "Screenshots", "Screen", "WhatsApp", "Instagram",
                "Telegram", "Download", "Pictures", "Photos", "DCIM"
            ]

            for part in parts:
                for indicator in folder_indicators:
                    if indicator.lower() in part.lower():
                        return part

            # Fallback: Use first folder after DCIM
            if "DCIM" in parts:
                dcim_idx = parts.index("DCIM")
                if dcim_idx + 1 < len(parts):
                    return parts[dcim_idx + 1]

            # Last resort: Use first folder
            if len(parts) > 1:
                return parts[0]

            return "Unknown"

        except Exception:
            return "Unknown"

    def _check_file_status(self, device_path: str, file_hash: str) -> tuple[str, bool]:
        """
        Check if file has been imported before (Phase 2).

        Args:
            device_path: Path on device
            file_hash: SHA256 hash

        Returns:
            Tuple of (import_status, already_imported)
        """
        if not self.device_id:
            # Fall back to basic hash check
            return ("new", self._is_already_imported(file_hash))

        try:
            # Check device_files table
            with self.db._connect() as conn:
                cur = conn.execute("""
                    SELECT import_status, local_photo_id
                    FROM device_files
                    WHERE device_id = ? AND device_path = ?
                """, (self.device_id, device_path))
                row = cur.fetchone()

                if row:
                    status = row[0]
                    local_photo_id = row[1]
                    already_imported = (local_photo_id is not None)
                    return (status, already_imported)

                # Not tracked yet - check by hash
                already_imported = self._is_already_imported(file_hash)
                return ("new", already_imported)

        except Exception as e:
            print(f"[DeviceImport] Error checking file status: {e}")
            return ("new", False)

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
            'bytes_imported': 0,  # Phase 2: Track bytes
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

                # Track bytes imported (Phase 2)
                stats['bytes_imported'] += media_file.size_bytes

                # Register in database
                local_photo_id = self._register_imported_file(
                    str(dest_path),
                    media_file.file_hash,
                    destination_folder_id,
                    device_path=media_file.path,
                    device_folder=media_file.device_folder
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
        folder_id: Optional[int],
        device_path: Optional[str] = None,
        device_folder: Optional[str] = None
    ) -> Optional[int]:
        """
        Register imported file in database (Phase 2: Enhanced).

        Args:
            file_path: Path to imported file
            file_hash: SHA256 hash
            folder_id: Destination folder ID
            device_path: Original path on device (Phase 2)
            device_folder: Device folder name (Phase 2)

        Returns:
            Photo ID if successfully registered
        """
        try:
            # Use existing add_project_image method
            if hasattr(self.db, 'add_project_image'):
                self.db.add_project_image(
                    project_id=self.project_id,
                    image_path=file_path,
                    folder_id=folder_id
                )

            # Get the photo_id for the just-inserted photo
            local_photo_id = None
            try:
                with self.db._connect() as conn:
                    # Update hash and device info (Phase 2)
                    conn.execute("""
                        UPDATE photo_metadata
                        SET file_hash = ?,
                            device_id = ?,
                            device_path = ?,
                            device_folder = ?,
                            import_session_id = ?
                        WHERE project_id = ? AND path = ?
                    """, (file_hash, self.device_id, device_path, device_folder,
                          self.current_session_id, self.project_id, file_path))

                    # Get the photo_id
                    cur = conn.execute("""
                        SELECT id FROM photo_metadata
                        WHERE project_id = ? AND path = ?
                    """, (self.project_id, file_path))
                    row = cur.fetchone()
                    if row:
                        local_photo_id = row[0]

                    conn.commit()

                    # Update device_files table (Phase 2)
                    if self.device_id and device_path and local_photo_id:
                        self.db.track_device_file(
                            device_id=self.device_id,
                            device_path=device_path,
                            device_folder=device_folder or "Unknown",
                            file_hash=file_hash,
                            file_size=0,  # Already tracked during scan
                            file_mtime="",
                            import_session_id=self.current_session_id,
                            local_photo_id=local_photo_id
                        )

            except Exception as e:
                print(f"[DeviceImport] Warning: Could not update device tracking: {e}")

            return local_photo_id

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
