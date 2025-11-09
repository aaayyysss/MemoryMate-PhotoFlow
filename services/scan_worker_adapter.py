# services/scan_worker_adapter.py
# Version 01.00.00.00 dated 20251102
# Qt adapter for PhotoScanService - bridges service layer with MainWindow

from PySide6.QtCore import QObject, Signal
from typing import Optional, Dict, Any

from .photo_scan_service import PhotoScanService, ScanResult, ScanProgress
from logging_config import get_logger

logger = get_logger(__name__)


class ScanWorkerAdapter(QObject):
    """
    Qt-compatible adapter for PhotoScanService.

    This adapter maintains the same interface as the old ScanWorker class,
    making it easy to integrate into MainWindow without major changes.

    Signals:
        progress(int, str): Progress percent and message
        finished(int, int): Folders found and photos indexed
        error(str): Error message
    """

    progress = Signal(int, str)          # percent, message
    finished = Signal(int, int, int)     # folders, photos, videos
    error = Signal(str)

    def __init__(self,
                 folder: str,
                 project_id: int,
                 incremental: bool,
                 settings: Dict[str, Any],
                 db_writer: Optional[Any] = None,
                 on_video_metadata_finished: Optional[Any] = None):
        """
        Initialize adapter.

        Args:
            folder: Root folder to scan
            project_id: Project ID to associate scanned photos with
            incremental: Enable incremental scanning
            settings: Application settings dict
            db_writer: Optional DBWriter (not used - kept for API compatibility)
            on_video_metadata_finished: Optional callback for when video metadata extraction finishes
        """
        super().__init__()

        self.folder = folder
        self.project_id = project_id
        self.incremental = incremental
        self.settings = settings
        self.db_writer = db_writer  # Kept for compatibility, but not used
        self.on_video_metadata_finished = on_video_metadata_finished

        # Create service instance
        self.service = PhotoScanService(
            batch_size=settings.get("scan_batch_size", 200),
            stat_timeout=settings.get("stat_timeout_secs", 3.0)
        )

        self._interrupted = False
        self._skipped_count = 0
        self._photos_indexed = 0

    def stop(self):
        """Request scan cancellation."""
        self._interrupted = True
        self.service.cancel()
        logger.info("Scan stop requested via adapter")

    def run(self):
        """
        Execute the scan. Called from QThread.

        Emits:
            progress: During scanning
            finished: On successful completion
            error: On failure
        """
        print(f"[ScanWorkerAdapter] run() method called!")
        print(f"[ScanWorkerAdapter] folder={self.folder}, incremental={self.incremental}")
        try:
            logger.info(f"ScanWorkerAdapter starting scan of {self.folder}")
            print(f"[ScanWorkerAdapter] Starting scan...")

            # Extract settings
            skip_unchanged = self.settings.get("skip_unchanged_photos", True)
            extract_exif = self.settings.get("use_exif_for_date", True)
            ignore_folders = set(self.settings.get("ignore_folders", []))

            # Define progress callback
            def on_progress(prog: ScanProgress):
                """Forward progress to Qt signal."""
                try:
                    self.progress.emit(prog.percent, prog.message)
                    self._photos_indexed = prog.current
                except Exception as e:
                    logger.warning(f"Failed to emit progress: {e}")

            # Run the scan
            result: ScanResult = self.service.scan_repository(
                root_folder=self.folder,
                project_id=self.project_id,
                incremental=self.incremental,
                skip_unchanged=skip_unchanged,
                extract_exif_date=extract_exif,
                ignore_folders=ignore_folders if ignore_folders else None,
                progress_callback=on_progress,
                on_video_metadata_finished=self.on_video_metadata_finished
            )

            # Update statistics
            self._skipped_count = result.photos_skipped
            self._photos_indexed = result.photos_indexed

            # Emit completion
            logger.info(
                f"Scan completed: {result.photos_indexed} photos, "
                f"{result.videos_indexed} videos, "
                f"{result.folders_found} folders in {result.duration_seconds:.1f}s"
            )

            try:
                self.finished.emit(result.folders_found, result.photos_indexed, result.videos_indexed)
            except Exception as e:
                logger.warning(f"Failed to emit finished signal: {e}")

        except Exception as e:
            error_msg = f"Scan failed: {e}"
            logger.error(error_msg, exc_info=True)

            try:
                self.error.emit(error_msg)
            except Exception:
                pass


# Backward compatibility alias
ScanWorker = ScanWorkerAdapter
