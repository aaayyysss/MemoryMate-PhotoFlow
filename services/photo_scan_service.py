# services/photo_scan_service.py
# Version 01.00.00.00 dated 20251102
# Photo scanning service - Extracts logic from MainWindow.ScanWorker

import os
import time
from pathlib import Path
from typing import Optional, List, Tuple, Callable, Dict, Any, Set
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass

from PIL import Image, ExifTags

from repository import PhotoRepository, FolderRepository, ProjectRepository, DatabaseConnection
from logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class ScanResult:
    """Results from a photo repository scan."""
    folders_found: int
    photos_indexed: int
    photos_skipped: int
    photos_failed: int
    duration_seconds: float
    interrupted: bool = False


@dataclass
class ScanProgress:
    """Progress information during scanning."""
    current: int
    total: int
    percent: int
    message: str
    current_file: Optional[str] = None


class PhotoScanService:
    """
    Service for scanning photo repositories and indexing metadata.

    Responsibilities:
    - File system traversal with ignore patterns
    - Basic metadata extraction (size, dimensions, EXIF date)
    - Folder hierarchy management
    - Batched database writes
    - Progress reporting
    - Cancellation support
    - Incremental scanning (skip unchanged files)

    Does NOT handle:
    - Advanced EXIF parsing (use MetadataService)
    - Thumbnail generation (use ThumbnailService)
    - Face detection (separate service)
    """

    # Supported image extensions
    SUPPORTED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff', '.heic', '.heif'}

    # Default ignore patterns
    DEFAULT_IGNORE_FOLDERS = {
        "AppData", "Program Files", "Program Files (x86)", "Windows",
        "$Recycle.Bin", "System Volume Information", "__pycache__",
        "node_modules", "Temp", "Cache", "Microsoft", "Installer",
        "Recovery", "Logs", "ThumbCache", "ActionCenterCache"
    }

    def __init__(self,
                 photo_repo: Optional[PhotoRepository] = None,
                 folder_repo: Optional[FolderRepository] = None,
                 project_repo: Optional[ProjectRepository] = None,
                 batch_size: int = 200,
                 stat_timeout: float = 3.0):
        """
        Initialize scan service.

        Args:
            photo_repo: Photo repository (creates default if None)
            folder_repo: Folder repository (creates default if None)
            project_repo: Project repository (creates default if None)
            batch_size: Number of photos to batch before writing
            stat_timeout: Timeout for os.stat calls (seconds)
        """
        self.photo_repo = photo_repo or PhotoRepository()
        self.folder_repo = folder_repo or FolderRepository()
        self.project_repo = project_repo or ProjectRepository()

        self.batch_size = batch_size
        self.stat_timeout = stat_timeout

        self._cancelled = False
        self._stats = {
            'photos_indexed': 0,
            'photos_skipped': 0,
            'photos_failed': 0,
            'folders_found': 0
        }

    def scan_repository(self,
                       root_folder: str,
                       incremental: bool = True,
                       skip_unchanged: bool = True,
                       extract_exif_date: bool = True,
                       ignore_folders: Optional[Set[str]] = None,
                       progress_callback: Optional[Callable[[ScanProgress], None]] = None) -> ScanResult:
        """
        Scan a photo repository and index all photos.

        Args:
            root_folder: Root folder to scan
            incremental: If True, skip files that haven't changed
            skip_unchanged: Skip files with matching mtime
            extract_exif_date: Extract EXIF DateTimeOriginal
            ignore_folders: Folders to skip (uses defaults if None)
            progress_callback: Optional callback for progress updates

        Returns:
            ScanResult with statistics

        Raises:
            ValueError: If root_folder doesn't exist
            Exception: For other errors (with logging)
        """
        start_time = time.time()
        self._cancelled = False
        self._stats = {'photos_indexed': 0, 'photos_skipped': 0, 'photos_failed': 0, 'folders_found': 0}

        root_path = Path(root_folder).resolve()
        if not root_path.exists():
            raise ValueError(f"Root folder does not exist: {root_folder}")

        logger.info(f"Starting scan: {root_folder} (incremental={incremental})")

        try:
            # Step 1: Discover all image files
            all_files = self._discover_files(root_path, ignore_folders or self.DEFAULT_IGNORE_FOLDERS)
            total_files = len(all_files)

            logger.info(f"Discovered {total_files} candidate image files")

            if total_files == 0:
                logger.warning("No image files found")
                return ScanResult(0, 0, 0, 0, time.time() - start_time)

            # Step 2: Load existing metadata for incremental scan
            existing_metadata = {}
            if skip_unchanged:
                existing_metadata = self._load_existing_metadata()
                logger.debug(f"Loaded {len(existing_metadata)} existing file records")

            # Step 3: Process files in batches
            batch_rows = []
            folders_seen: Set[str] = set()

            executor = ThreadPoolExecutor(max_workers=4)

            try:
                for i, file_path in enumerate(all_files, 1):
                    if self._cancelled:
                        logger.info("Scan cancelled by user")
                        break

                    # Process file
                    row = self._process_file(
                        file_path=file_path,
                        root_path=root_path,
                        existing_metadata=existing_metadata,
                        skip_unchanged=skip_unchanged,
                        extract_exif_date=extract_exif_date,
                        executor=executor
                    )

                    if row is None:
                        # Skipped or failed
                        continue

                    # Track folder
                    folder_path = os.path.dirname(str(file_path))
                    folders_seen.add(folder_path)

                    batch_rows.append(row)

                    # Flush batch if needed
                    if len(batch_rows) >= self.batch_size:
                        self._write_batch(batch_rows)
                        batch_rows.clear()

                    # Report progress
                    if progress_callback and (i % 10 == 0 or i == total_files):
                        progress = ScanProgress(
                            current=i,
                            total=total_files,
                            percent=int((i / total_files) * 100),
                            message=f"Indexed {self._stats['photos_indexed']}/{total_files} photos",
                            current_file=str(file_path)
                        )
                        progress_callback(progress)

                # Final batch flush
                if batch_rows:
                    self._write_batch(batch_rows)

            finally:
                executor.shutdown(wait=False)

            # Step 4: Create default project and branch if needed
            self._ensure_default_project(root_folder)

            # Finalize
            duration = time.time() - start_time
            self._stats['folders_found'] = len(folders_seen)

            logger.info(
                f"Scan complete: {self._stats['photos_indexed']} indexed, "
                f"{self._stats['photos_skipped']} skipped, "
                f"{self._stats['photos_failed']} failed in {duration:.1f}s"
            )

            return ScanResult(
                folders_found=self._stats['folders_found'],
                photos_indexed=self._stats['photos_indexed'],
                photos_skipped=self._stats['photos_skipped'],
                photos_failed=self._stats['photos_failed'],
                duration_seconds=duration,
                interrupted=self._cancelled
            )

        except Exception as e:
            logger.error(f"Scan failed: {e}", exc_info=True)
            raise

    def cancel(self):
        """Request cancellation of current scan."""
        self._cancelled = True
        logger.info("Scan cancellation requested")

    def _discover_files(self, root_path: Path, ignore_folders: Set[str]) -> List[Path]:
        """
        Discover all image files in directory tree.

        Args:
            root_path: Root directory
            ignore_folders: Folder names to skip

        Returns:
            List of image file paths
        """
        image_files = []

        for dirpath, dirnames, filenames in os.walk(root_path):
            # Filter ignored directories in-place
            dirnames[:] = [
                d for d in dirnames
                if d not in ignore_folders and not d.startswith(".")
            ]

            for filename in filenames:
                ext = Path(filename).suffix.lower()
                if ext in self.SUPPORTED_EXTENSIONS:
                    image_files.append(Path(dirpath) / filename)

        return image_files

    def _load_existing_metadata(self) -> Dict[str, str]:
        """
        Load existing file metadata for incremental scanning.

        Returns:
            Dictionary mapping path -> mtime string
        """
        try:
            # Use repository to get all photos
            with self.photo_repo.connection(read_only=True) as conn:
                cur = conn.cursor()
                cur.execute("SELECT path, modified FROM photo_metadata")
                return {row['path']: row['modified'] for row in cur.fetchall()}
        except Exception as e:
            logger.warning(f"Could not load existing metadata: {e}")
            return {}

    def _process_file(self,
                     file_path: Path,
                     root_path: Path,
                     existing_metadata: Dict[str, str],
                     skip_unchanged: bool,
                     extract_exif_date: bool,
                     executor: ThreadPoolExecutor) -> Optional[Tuple]:
        """
        Process a single image file.

        Returns:
            Tuple for database insert, or None if skipped/failed
        """
        path_str = str(file_path)

        # Step 1: Get file stats with timeout protection
        try:
            future = executor.submit(os.stat, path_str)
            stat_result = future.result(timeout=self.stat_timeout)
        except FuturesTimeoutError:
            logger.warning(f"os.stat timeout for {path_str}")
            self._stats['photos_failed'] += 1
            try:
                future.cancel()
            except Exception:
                pass
            return None
        except FileNotFoundError:
            logger.debug(f"File not found: {path_str}")
            self._stats['photos_failed'] += 1
            return None
        except Exception as e:
            logger.warning(f"os.stat failed for {path_str}: {e}")
            self._stats['photos_failed'] += 1
            return None

        # Step 2: Extract basic metadata from stat
        try:
            mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat_result.st_mtime))
            size_kb = stat_result.st_size / 1024.0
        except Exception as e:
            logger.error(f"Failed to process stat result for {path_str}: {e}")
            self._stats['photos_failed'] += 1
            return None

        # Step 3: Skip if unchanged (incremental scan)
        if skip_unchanged and existing_metadata.get(path_str) == mtime:
            self._stats['photos_skipped'] += 1
            return None

        # Step 4: Extract dimensions and EXIF date
        width = height = date_taken = None

        try:
            with Image.open(file_path) as img:
                width, height = img.size

                if extract_exif_date:
                    try:
                        exif = img._getexif()
                        if exif:
                            for key, value in exif.items():
                                tag = ExifTags.TAGS.get(key, key)
                                if tag in ("DateTimeOriginal", "DateTime"):
                                    date_taken = str(value)
                                    break
                    except Exception:
                        pass  # EXIF extraction is optional

        except Exception as e:
            logger.debug(f"Could not extract image metadata from {path_str}: {e}")
            # Continue without dimensions/EXIF

        # Step 5: Ensure folder hierarchy exists
        try:
            folder_id = self._ensure_folder_hierarchy(file_path.parent, root_path)
        except Exception as e:
            logger.error(f"Failed to create folder hierarchy for {path_str}: {e}")
            self._stats['photos_failed'] += 1
            return None

        # Success
        self._stats['photos_indexed'] += 1

        # Return row tuple for batch insert
        # (path, folder_id, size_kb, modified, width, height, date_taken, tags)
        return (path_str, folder_id, size_kb, mtime, width, height, date_taken, None)

    def _ensure_folder_hierarchy(self, folder_path: Path, root_path: Path) -> int:
        """
        Ensure folder and all parent folders exist in database.

        Args:
            folder_path: Current folder path
            root_path: Repository root path

        Returns:
            Folder ID
        """
        # Ensure root folder exists
        root_id = self.folder_repo.ensure_folder(
            path=str(root_path),
            name=root_path.name,
            parent_id=None
        )

        # If folder is root, return root_id
        if folder_path == root_path:
            return root_id

        # Build parent chain
        try:
            rel_path = folder_path.relative_to(root_path)
            parts = list(rel_path.parts)

            current_parent_id = root_id
            current_path = root_path

            for part in parts:
                current_path = current_path / part
                current_parent_id = self.folder_repo.ensure_folder(
                    path=str(current_path),
                    name=part,
                    parent_id=current_parent_id
                )

            return current_parent_id

        except ValueError:
            # folder_path not under root_path (shouldn't happen)
            logger.warning(f"Folder {folder_path} is not under root {root_path}")
            return self.folder_repo.ensure_folder(
                path=str(folder_path),
                name=folder_path.name,
                parent_id=root_id
            )

    def _write_batch(self, rows: List[Tuple]):
        """
        Write a batch of photo rows to database.

        Args:
            rows: List of tuples (path, folder_id, size_kb, modified, width, height, date_taken, tags)
        """
        if not rows:
            return

        try:
            affected = self.photo_repo.bulk_upsert(rows)
            logger.debug(f"Wrote batch of {affected} photos to database")
        except Exception as e:
            logger.error(f"Failed to write batch: {e}", exc_info=True)
            # Try individual writes as fallback
            for row in rows:
                try:
                    self.photo_repo.upsert(*row)
                except Exception as e2:
                    logger.error(f"Failed to write individual photo {row[0]}: {e2}")

    def _ensure_default_project(self, root_folder: str):
        """
        Ensure a default project exists and has an 'all' branch.

        Args:
            root_folder: Repository root folder
        """
        try:
            projects = self.project_repo.find_all(limit=1)

            if not projects:
                # Create default project
                project_id = self.project_repo.create(
                    name="Default Project",
                    folder=root_folder,
                    mode="date"
                )
                logger.info(f"Created default project (id={project_id})")
            else:
                project_id = projects[0]['id']

            # Ensure 'all' branch exists
            self.project_repo.ensure_branch(
                project_id=project_id,
                branch_key="all",
                display_name="📁 All Photos"
            )

            # Add all photos to 'all' branch
            # TODO: This should be done more efficiently
            logger.debug(f"Project {project_id} ready with 'all' branch")

        except Exception as e:
            logger.warning(f"Could not create default project: {e}")
