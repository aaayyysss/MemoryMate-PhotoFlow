# services/photo_scan_service.py
# Version 01.00.01.00 dated 20251102
# Photo scanning service - Uses MetadataService for extraction

import os
import platform
import time
from pathlib import Path
from typing import Optional, List, Tuple, Callable, Dict, Any, Set
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass

from repository import PhotoRepository, FolderRepository, ProjectRepository, DatabaseConnection
from logging_config import get_logger
from .metadata_service import MetadataService

logger = get_logger(__name__)


@dataclass
class ScanResult:
    """Results from a photo repository scan."""
    folders_found: int
    photos_indexed: int
    photos_skipped: int
    photos_failed: int
    videos_indexed: int  # 🎬 NEW: video count
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

    Metadata Extraction Approach:
    - Uses MetadataService.extract_basic_metadata() for ALL photos (BUG FIX #8)
    - This avoids hangs from corrupted/malformed images
    - created_ts/created_date/created_year are computed inline from date_taken
    - Consistent across entire service - do not mix with extract_metadata()
    """

    # Supported image extensions
    # Common formats
    IMAGE_EXTENSIONS = {
        # JPEG family
        '.jpg', '.jpeg', '.jpe', '.jfif',
        # PNG
        '.png',
        # WEBP
        '.webp',
        # TIFF
        '.tif', '.tiff',
        # HEIF/HEIC (Apple/modern)
        '.heic', '.heif',  # ✅ iPhone photos, Live Photos (still image part)
        # BMP
        '.bmp', '.dib',
        # GIF
        '.gif',
        # Modern formats
        '.avif',  # AV1 Image File
        '.jxl',   # JPEG XL
        # RAW formats (may require extra plugins)
        '.cr2', '.cr3',  # Canon RAW
        '.nef', '.nrw',  # Nikon RAW
        '.arw', '.srf', '.sr2',  # Sony RAW
        '.dng',  # Adobe Digital Negative (includes Apple ProRAW)
        '.orf',  # Olympus RAW
        '.rw2',  # Panasonic RAW
        '.pef',  # Pentax RAW
        '.raf',  # Fujifilm RAW
    }

    # Video file extensions
    VIDEO_EXTENSIONS = {
        # Apple/iPhone formats
        '.mov',   # ✅ QuickTime, Live Photos (video part), Cinematic mode, ProRes
        '.m4v',   # ✅ iTunes video, iPhone recordings
        # Common video formats
        '.mp4',   # MPEG-4
        # MPEG family
        '.mpeg', '.mpg', '.mpe',
        # Windows Media
        '.wmv', '.asf',
        # AVI
        '.avi',
        # Matroska
        '.mkv', '.webm',
        # Flash
        '.flv', '.f4v',
        # Mobile/Other
        '.3gp', '.3g2',  # Mobile phones
        '.ogv',          # Ogg Video
        '.ts', '.mts', '.m2ts'  # MPEG transport stream
    }

    # Combined: all supported media files (photos + videos)
    SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

    # Default ignore patterns (OS-specific to avoid irrelevant exclusions)
    # Common folders to ignore across all platforms
    _COMMON_IGNORE_FOLDERS = {
        "__pycache__", "node_modules", ".git", ".svn", ".hg",
        "venv", ".venv", "env", ".env"
    }

    # Platform-specific ignore folders
    if platform.system() == "Windows":
        DEFAULT_IGNORE_FOLDERS = _COMMON_IGNORE_FOLDERS | {
            "AppData", "Program Files", "Program Files (x86)", "Windows",
            "$Recycle.Bin", "System Volume Information", "Temp", "Cache",
            "Microsoft", "Installer", "Recovery", "Logs",
            "ThumbCache", "ActionCenterCache"
        }
    elif platform.system() == "Darwin":  # macOS
        DEFAULT_IGNORE_FOLDERS = _COMMON_IGNORE_FOLDERS | {
            "Library", ".Trash", "Caches", "Logs",
            "Application Support"
        }
    else:  # Linux and others
        DEFAULT_IGNORE_FOLDERS = _COMMON_IGNORE_FOLDERS | {
            ".cache", ".local/share/Trash", "tmp"
        }

    def __init__(self,
                 photo_repo: Optional[PhotoRepository] = None,
                 folder_repo: Optional[FolderRepository] = None,
                 project_repo: Optional[ProjectRepository] = None,
                 metadata_service: Optional[MetadataService] = None,
                 batch_size: int = 200,
                 stat_timeout: float = 3.0):
        """
        Initialize scan service.

        Args:
            photo_repo: Photo repository (creates default if None)
            folder_repo: Folder repository (creates default if None)
            project_repo: Project repository (creates default if None)
            metadata_service: Metadata extraction service (creates default if None)
            batch_size: Number of photos to batch before writing (default: 200)
                       NOTE: Could be made configurable via SettingsManager in the future
            stat_timeout: Timeout for os.stat calls in seconds (default: 3.0)
                         NOTE: Could be made configurable via SettingsManager in the future
        """
        self.photo_repo = photo_repo or PhotoRepository()
        self.folder_repo = folder_repo or FolderRepository()
        self.project_repo = project_repo or ProjectRepository()
        self.metadata_service = metadata_service or MetadataService()

        self.batch_size = batch_size
        self.stat_timeout = stat_timeout

        self._cancelled = False
        self._stats = {
            'photos_indexed': 0,
            'photos_skipped': 0,
            'photos_failed': 0,
            'folders_found': 0
        }

        # Video workers (initialized when videos are processed)
        self.video_metadata_worker = None
        self.video_thumbnail_worker = None

    def scan_repository(self,
                       root_folder: str,
                       project_id: int,
                       incremental: bool = True,
                       skip_unchanged: bool = True,
                       extract_exif_date: bool = True,
                       ignore_folders: Optional[Set[str]] = None,
                       progress_callback: Optional[Callable[[ScanProgress], None]] = None,
                       on_video_metadata_finished: Optional[Callable[[int, int], None]] = None) -> ScanResult:
        """
        Scan a photo repository and index all photos.

        Args:
            root_folder: Root folder to scan
            project_id: Project ID to associate scanned photos with
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
        self._stats = {'photos_indexed': 0, 'photos_skipped': 0, 'photos_failed': 0, 'videos_indexed': 0, 'folders_found': 0}

        root_path = Path(root_folder).resolve()
        if not root_path.exists():
            raise ValueError(f"Root folder does not exist: {root_folder}")

        logger.info(f"Starting scan: {root_folder} (incremental={incremental})")

        try:
            # Step 1: Discover all media files (photos + videos)
            # Priority: explicit parameter > settings > platform-specific defaults
            if ignore_folders is not None:
                ignore_set = ignore_folders
            else:
                # Check settings for custom exclusions
                ignore_set = self._get_ignore_folders_from_settings()

            all_files = self._discover_files(root_path, ignore_set)
            all_videos = self._discover_videos(root_path, ignore_set)

            total_files = len(all_files)
            total_videos = len(all_videos)

            logger.info(f"Discovered {total_files} candidate image files and {total_videos} video files")

            if total_files == 0 and total_videos == 0:
                logger.warning("No media files found")
                return ScanResult(0, 0, 0, 0, 0, time.time() - start_time)

            # Step 2: Load existing metadata for incremental scan
            existing_metadata = {}
            if skip_unchanged:
                existing_metadata = self._load_existing_metadata()
                logger.debug(f"Loaded {len(existing_metadata)} existing file records")

            # Step 3: Process files in batches
            batch_rows = []
            folders_seen: Set[str] = set()

            # CRITICAL FIX: Increase thread pool size to prevent deadlock
            # With max_workers=4, if 4 files timeout simultaneously, the pool deadlocks
            # Using 8 workers provides headroom for timeouts while maintaining reasonable concurrency
            executor = ThreadPoolExecutor(max_workers=8)

            try:
                for i, file_path in enumerate(all_files, 1):
                    if self._cancelled:
                        logger.info("Scan cancelled by user")
                        break

                    # Process file
                    row = self._process_file(
                        file_path=file_path,
                        root_path=root_path,
                        project_id=project_id,
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
                        self._write_batch(batch_rows, project_id)
                        batch_rows.clear()

                    # Report progress (check cancellation here too for responsiveness)
                    if progress_callback and (i % 10 == 0 or i == total_files):
                        # RESPONSIVE CANCEL: Check during progress reporting
                        if self._cancelled:
                            logger.info("Scan cancelled during progress reporting")
                            break

                        progress = ScanProgress(
                            current=i,
                            total=total_files,
                            percent=int((i / total_files) * 100),
                            message=f"Indexed {self._stats['photos_indexed']}/{total_files} photos",
                            current_file=str(file_path)
                        )
                        progress_callback(progress)

                # Final batch flush
                if batch_rows and not self._cancelled:
                    self._write_batch(batch_rows, project_id)

            finally:
                # Properly shutdown executor to prevent Qt timer warnings
                # Don't wait if cancelled to exit quickly
                try:
                    executor.shutdown(wait=not self._cancelled, cancel_futures=True)
                except Exception as e:
                    logger.debug(f"Executor shutdown error (ignored): {e}")

            # Step 4: Process videos
            if total_videos > 0 and not self._cancelled:
                logger.info(f"Processing {total_videos} videos...")
                self._process_videos(all_videos, root_path, project_id, folders_seen, progress_callback)

            # Step 5: Create default project and branch if needed
            self._ensure_default_project(root_folder)

            # Step 6: Launch background workers for video processing
            if self._stats['videos_indexed'] > 0:
                self.video_metadata_worker, self.video_thumbnail_worker = self._launch_video_workers(
                    project_id,
                    on_metadata_finished_callback=on_video_metadata_finished
                )

            # Finalize
            duration = time.time() - start_time
            self._stats['folders_found'] = len(folders_seen)

            logger.info(
                f"Scan complete: {self._stats['photos_indexed']} photos indexed, "
                f"{self._stats['videos_indexed']} videos indexed, "
                f"{self._stats['photos_skipped']} skipped, "
                f"{self._stats['photos_failed']} failed in {duration:.1f}s"
            )

            return ScanResult(
                folders_found=self._stats['folders_found'],
                photos_indexed=self._stats['photos_indexed'],
                photos_skipped=self._stats['photos_skipped'],
                photos_failed=self._stats['photos_failed'],
                videos_indexed=self._stats['videos_indexed'],
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
            # Check cancellation during discovery (responsive cancel)
            if self._cancelled:
                logger.info("File discovery cancelled by user")
                return image_files

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

    def _discover_videos(self, root_path: Path, ignore_folders: Set[str]) -> List[Path]:
        """
        Discover all video files in directory tree.

        Args:
            root_path: Root directory
            ignore_folders: Folder names to skip

        Returns:
            List of video file paths
        """
        video_files = []

        for dirpath, dirnames, filenames in os.walk(root_path):
            # Check cancellation during discovery (responsive cancel)
            if self._cancelled:
                logger.info("Video discovery cancelled by user")
                return video_files

            # Filter ignored directories in-place
            dirnames[:] = [
                d for d in dirnames
                if d not in ignore_folders and not d.startswith(".")
            ]

            for filename in filenames:
                ext = Path(filename).suffix.lower()
                if ext in self.VIDEO_EXTENSIONS:
                    video_files.append(Path(dirpath) / filename)

        return video_files

    def _get_ignore_folders_from_settings(self) -> Set[str]:
        """
        Get ignore folders from settings, with fallback to platform-specific defaults.

        Returns:
            Set of folder names to ignore during scanning

        Priority:
            1. Custom exclusions from settings (if non-empty)
            2. Platform-specific defaults (DEFAULT_IGNORE_FOLDERS)
        """
        try:
            from settings_manager_qt import SettingsManager
            settings = SettingsManager()
            custom_exclusions = settings.get("scan_exclude_folders", [])

            if custom_exclusions:
                # User has configured custom exclusions - use them
                logger.info(f"Using custom scan exclusions from settings: {len(custom_exclusions)} folders")
                return set(custom_exclusions)
            else:
                # No custom exclusions - use platform-specific defaults
                logger.debug(f"Using platform-specific default exclusions ({platform.system()})")
                return self.DEFAULT_IGNORE_FOLDERS
        except Exception as e:
            logger.warning(f"Could not load scan exclusions from settings: {e}")
            logger.debug("Falling back to platform-specific default exclusions")
            return self.DEFAULT_IGNORE_FOLDERS

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

    def _compute_created_fields(self, date_str: str = None, modified: str = None) -> tuple:
        """
        Compute created_ts, created_date, created_year from date or modified time.

        This helper is used for both photos and videos to ensure consistent date handling.

        Args:
            date_str: Date string in YYYY-MM-DD or YYYY-MM-DD HH:MM:SS format
            modified: Modified timestamp in YYYY-MM-DD HH:MM:SS format (fallback)

        Returns:
            Tuple of (created_ts, created_date, created_year) or (None, None, None)

        Example:
            >>> _compute_created_fields("2024-11-12", None)
            (1699747200, "2024-11-12", 2024)

            >>> _compute_created_fields(None, "2024-11-12 15:30:00")
            (1699747200, "2024-11-12", 2024)
        """
        from datetime import datetime

        # Try parsing date_str first, fall back to modified
        date_to_parse = date_str if date_str else modified

        if not date_to_parse:
            return (None, None, None)

        try:
            # Extract YYYY-MM-DD part
            date_only = date_to_parse.split(' ')[0]
            dt = datetime.strptime(date_only, '%Y-%m-%d')

            return (
                int(dt.timestamp()),  # created_ts
                date_only,             # created_date (YYYY-MM-DD)
                dt.year                # created_year
            )
        except (ValueError, AttributeError, IndexError) as e:
            logger.debug(f"Failed to parse date '{date_to_parse}': {e}")
            return (None, None, None)

    def _process_file(self,
                     file_path: Path,
                     root_path: Path,
                     project_id: int,
                     existing_metadata: Dict[str, str],
                     skip_unchanged: bool,
                     extract_exif_date: bool,
                     executor: ThreadPoolExecutor) -> Optional[Tuple]:
        """
        Process a single image file.

        Returns:
            Tuple for database insert, or None if skipped/failed
        """
        # RESPONSIVE CANCEL: Check before processing each file
        if self._cancelled:
            return None

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

        # RESPONSIVE CANCEL: Check before expensive metadata extraction
        if self._cancelled:
            return None

        # Step 4: Extract dimensions and EXIF date using MetadataService
        # CRITICAL FIX: Wrap metadata extraction with timeout to prevent hangs
        # PIL/Pillow can hang on corrupted images, malformed TIFF/EXIF, or files with infinite loops
        # BUG FIX #8: Use fast extract_basic_metadata() to avoid hangs, compute created_* inline
        width = height = date_taken = None
        created_ts = created_date = created_year = None
        metadata_timeout = 5.0  # 5 seconds per image

        if extract_exif_date:
            # Use fast basic metadata extraction (BUG FIX #8: Reverted from extract_metadata)
            try:
                logger.debug(f"[Scan] Processing metadata for: {path_str}")
                future = executor.submit(self.metadata_service.extract_basic_metadata, str(file_path))
                width, height, date_taken = future.result(timeout=metadata_timeout)
                logger.debug(f"[Scan] Metadata extracted successfully for: {path_str}")
            except FuturesTimeoutError:
                logger.warning(f"Metadata extraction timeout for {path_str} (5s limit) - continuing without metadata")
                # Continue without dimensions/EXIF - photo will still be indexed
                try:
                    future.cancel()
                except Exception:
                    pass
            except Exception as e:
                logger.debug(f"Could not extract image metadata from {path_str}: {e}")
                # Continue without dimensions/EXIF
        else:
            # Just get dimensions without EXIF (with timeout)
            try:
                future = executor.submit(self.metadata_service.extract_basic_metadata, str(file_path))
                width, height, _ = future.result(timeout=metadata_timeout)
            except FuturesTimeoutError:
                logger.warning(f"Dimension extraction timeout for {path_str} (5s limit)")
                try:
                    future.cancel()
                except Exception:
                    pass
            except Exception as e:
                logger.debug(f"Could not extract dimensions from {path_str}: {e}")

        # BUG FIX #7 + #8: Compute created_* fields from date_taken inline (no heavy extract_metadata call)
        if date_taken:
            try:
                from datetime import datetime
                # Parse date_taken (format: 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD')
                date_str = date_taken.split(' ')[0]  # Extract YYYY-MM-DD part
                dt = datetime.strptime(date_str, '%Y-%m-%d')
                created_ts = int(dt.timestamp())
                created_date = date_str  # YYYY-MM-DD
                created_year = dt.year
            except (ValueError, AttributeError, IndexError) as e:
                # If date parsing fails, these fields will remain NULL
                logger.debug(f"[Scan] Failed to parse date_taken '{date_taken}': {e}")

        # Step 5: Ensure folder hierarchy exists
        try:
            folder_id = self._ensure_folder_hierarchy(file_path.parent, root_path, project_id)
        except Exception as e:
            logger.error(f"Failed to create folder hierarchy for {path_str}: {e}")
            self._stats['photos_failed'] += 1
            return None

        # Success
        self._stats['photos_indexed'] += 1

        # Return row tuple for batch insert
        # BUG FIX #7: Include created_ts, created_date, created_year for date hierarchy
        # (path, folder_id, size_kb, modified, width, height, date_taken, tags,
        #  created_ts, created_date, created_year)
        return (path_str, folder_id, size_kb, mtime, width, height, date_taken, None,
                created_ts, created_date, created_year)

    def _ensure_folder_hierarchy(self, folder_path: Path, root_path: Path, project_id: int) -> int:
        """
        Ensure folder and all parent folders exist in database.

        Args:
            folder_path: Current folder path
            root_path: Repository root path
            project_id: Project ID for folder ownership

        Returns:
            Folder ID
        """
        # Ensure root folder exists
        root_id = self.folder_repo.ensure_folder(
            path=str(root_path),
            name=root_path.name,
            parent_id=None,
            project_id=project_id
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
                    parent_id=current_parent_id,
                    project_id=project_id
                )

            return current_parent_id

        except ValueError:
            # folder_path not under root_path (shouldn't happen)
            logger.warning(f"Folder {folder_path} is not under root {root_path}")
            return self.folder_repo.ensure_folder(
                path=str(folder_path),
                name=folder_path.name,
                parent_id=root_id,
                project_id=project_id
            )

    def _write_batch(self, rows: List[Tuple], project_id: int):
        """
        Write a batch of photo rows to database.

        Args:
            rows: List of tuples (path, folder_id, size_kb, modified, width, height, date_taken, tags,
                                   created_ts, created_date, created_year)
            project_id: Project ID for photo ownership
        """
        if not rows:
            return

        # RESPONSIVE CANCEL: Check before database write
        if self._cancelled:
            logger.info("Batch write skipped due to cancellation")
            return

        try:
            affected = self.photo_repo.bulk_upsert(rows, project_id)
            logger.debug(f"Wrote batch of {affected} photos to database")
        except Exception as e:
            logger.error(f"Failed to write batch: {e}", exc_info=True)
            # Try individual writes as fallback
            for row in rows:
                try:
                    # BUG FIX #7: Unpack row with created_* fields
                    path, folder_id, size_kb, modified, width, height, date_taken, tags, created_ts, created_date, created_year = row
                    self.photo_repo.upsert(path, folder_id, project_id, size_kb, modified, width, height,
                                          date_taken, tags, created_ts, created_date, created_year)
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

    def _process_videos(self, video_files: List[Path], root_path: Path, project_id: int,
                       folders_seen: Set[str], progress_callback: Optional[Callable] = None):
        """
        Process discovered video files and index them.

        Args:
            video_files: List of video file paths
            root_path: Root directory of scan
            project_id: Project ID
            folders_seen: Set of folder paths already seen
            progress_callback: Optional progress callback
        """
        try:
            from services.video_service import VideoService
            video_service = VideoService()

            for i, video_path in enumerate(video_files, 1):
                if self._cancelled:
                    logger.info("Video processing cancelled by user")
                    break

                try:
                    # Track folder
                    folder_path = os.path.dirname(str(video_path))
                    folders_seen.add(folder_path)

                    # Ensure folder exists and get folder_id (PROPER FIX)
                    folder_id = self._ensure_folder_hierarchy(video_path.parent, root_path, project_id)

                    # Get file stats
                    stat = os.stat(video_path)
                    size_kb = stat.st_size / 1024
                    modified = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))

                    # CRITICAL FIX: Compute created_* fields from modified date for immediate sidebar display
                    # Background workers will UPDATE these with proper date_taken from video metadata
                    created_ts, created_date, created_year = self._compute_created_fields(None, modified)

                    # Index video WITH date fields (using modified as fallback until workers extract date_taken)
                    video_service.index_video(
                        path=str(video_path),
                        project_id=project_id,
                        folder_id=folder_id,
                        size_kb=size_kb,
                        modified=modified,
                        created_ts=created_ts,
                        created_date=created_date,
                        created_year=created_year
                    )
                    self._stats['videos_indexed'] += 1

                except Exception as e:
                    logger.warning(f"Failed to index video {video_path}: {e}")

                # Report progress
                if progress_callback and (i % 10 == 0 or i == len(video_files)):
                    progress = ScanProgress(
                        current=i,
                        total=len(video_files),
                        percent=int((i / len(video_files)) * 100),
                        message=f"Indexed {self._stats['videos_indexed']}/{len(video_files)} videos",
                        current_file=str(video_path)
                    )
                    progress_callback(progress)

            logger.info(f"Indexed {self._stats['videos_indexed']} videos (metadata extraction pending)")

        except ImportError:
            logger.warning("VideoService not available, skipping video indexing")
        except Exception as e:
            logger.error(f"Error processing videos: {e}", exc_info=True)

    def _launch_video_workers(self, project_id: int, on_metadata_finished_callback=None):
        """
        Launch background workers for video metadata extraction and thumbnail generation.

        Args:
            project_id: Project ID for which to process videos
            on_metadata_finished_callback: Optional callback(success, failed) to call when metadata extraction finishes

        Returns:
            Tuple of (metadata_worker, thumbnail_worker) or (None, None) if failed
        """
        try:
            from PySide6.QtCore import QThreadPool
            from workers.video_metadata_worker import VideoMetadataWorker
            from workers.video_thumbnail_worker import VideoThumbnailWorker

            logger.info(f"Launching background workers for {self._stats['videos_indexed']} videos...")

            # Launch metadata extraction worker
            metadata_worker = VideoMetadataWorker(project_id=project_id)

            # Connect progress signals for UI feedback
            metadata_worker.signals.progress.connect(
                lambda curr, total, path: logger.info(f"[Metadata] Processing {curr}/{total}: {path}")
            )
            metadata_worker.signals.finished.connect(
                lambda success, failed: logger.info(f"[Metadata] Complete: {success} successful, {failed} failed")
            )

            # CRITICAL: Connect callback BEFORE starting worker to avoid race condition
            if on_metadata_finished_callback:
                metadata_worker.signals.finished.connect(on_metadata_finished_callback)
                logger.info("Connected metadata finished callback for sidebar refresh")

            QThreadPool.globalInstance().start(metadata_worker)
            logger.info("✓ Video metadata extraction worker started")

            # Launch thumbnail generation worker
            thumbnail_worker = VideoThumbnailWorker(project_id=project_id, thumbnail_height=200)

            # Connect progress signals for UI feedback
            thumbnail_worker.signals.progress.connect(
                lambda curr, total, path: logger.info(f"[Thumbnails] Generating {curr}/{total}: {path}")
            )
            thumbnail_worker.signals.finished.connect(
                lambda success, failed: logger.info(f"[Thumbnails] Complete: {success} successful, {failed} failed")
            )

            QThreadPool.globalInstance().start(thumbnail_worker)
            logger.info("✓ Video thumbnail generation worker started")

            # Store worker count for status
            logger.info(f"🎬 Processing {self._stats['videos_indexed']} videos in background (check logs for progress)")

            # Return workers so callers can connect to their signals
            return metadata_worker, thumbnail_worker

        except ImportError as e:
            logger.warning(f"Video workers not available: {e}")
            return None, None
        except Exception as e:
            logger.error(f"Error launching video workers: {e}", exc_info=True)
            return None, None
