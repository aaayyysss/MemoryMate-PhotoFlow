# workers/video_metadata_worker.py
# Version 1.0.0 dated 2025-11-09
# Background worker for extracting video metadata

import sys
import os
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtCore import QObject, Signal, QRunnable, Slot
from services.video_metadata_service import VideoMetadataService
from repository.video_repository import VideoRepository
from logging_config import get_logger

logger = get_logger(__name__)


class VideoMetadataWorkerSignals(QObject):
    """
    Signals for video metadata extraction worker.

    Signals:
        progress: (current, total, video_path) - Progress update
        finished: (success_count, failed_count) - Completion signal
        error: (video_path, error_message) - Error signal for individual video
    """
    progress = Signal(int, int, str)  # current, total, video_path
    finished = Signal(int, int)        # success_count, failed_count
    error = Signal(str, str)           # video_path, error_message


class VideoMetadataWorker(QRunnable):
    """
    Background worker for extracting video metadata.

    Extracts metadata (duration, resolution, codecs, etc.) for videos
    with pending metadata_status. Runs in background thread pool.

    Usage:
        worker = VideoMetadataWorker(project_id=1)
        worker.signals.progress.connect(on_progress)
        worker.signals.finished.connect(on_finished)
        QThreadPool.globalInstance().start(worker)
    """

    def __init__(self, project_id: int, video_paths: list = None):
        """
        Initialize metadata extraction worker.

        Args:
            project_id: Project ID to process videos for
            video_paths: Optional list of specific video paths to process.
                        If None, processes all videos with pending metadata.
        """
        super().__init__()
        self.project_id = project_id
        self.video_paths = video_paths
        self.signals = VideoMetadataWorkerSignals()
        self.cancelled = False

        self.metadata_service = VideoMetadataService()
        self.video_repo = VideoRepository()

    def cancel(self):
        """Request cancellation of worker."""
        self.cancelled = True
        logger.info("[VideoMetadataWorker] Cancellation requested")

    @Slot()
    def run(self):
        """
        Extract metadata for pending videos.

        Processes videos in project with metadata_status = 'pending' or 'error'.
        Updates video_metadata table with extracted information.
        """
        logger.info(f"[VideoMetadataWorker] Starting for project_id={self.project_id}")

        success_count = 0
        failed_count = 0

        try:
            # Get list of videos to process
            if self.video_paths:
                # Process specific videos
                videos_to_process = []
                for path in self.video_paths:
                    video = self.video_repo.get_by_path(path, self.project_id)
                    if video:
                        videos_to_process.append(video)
            else:
                # Get all videos with pending metadata
                all_videos = self.video_repo.get_by_project(self.project_id)
                videos_to_process = [
                    v for v in all_videos
                    if v.get('metadata_status') in ('pending', 'error', None)
                ]

            total = len(videos_to_process)
            logger.info(f"[VideoMetadataWorker] Found {total} videos to process")

            if total == 0:
                self.signals.finished.emit(0, 0)
                return

            # Process each video
            for idx, video in enumerate(videos_to_process):
                if self.cancelled:
                    logger.info("[VideoMetadataWorker] Cancelled, stopping")
                    break

                video_path = video['path']
                current = idx + 1

                # Emit progress
                self.signals.progress.emit(current, total, video_path)

                # Check if file exists
                if not os.path.exists(video_path):
                    logger.warning(f"[VideoMetadataWorker] File not found: {video_path}")
                    self.signals.error.emit(video_path, "File not found")
                    failed_count += 1
                    continue

                # Extract metadata
                try:
                    metadata = self.metadata_service.extract_metadata(video_path)

                    if metadata:
                        # Update database
                        video_id = video['id']
                        self.video_repo.update(
                            video_id=video_id,
                            duration_seconds=metadata.get('duration_seconds'),  # Fixed: was 'duration'
                            width=metadata.get('width'),
                            height=metadata.get('height'),
                            fps=metadata.get('fps'),
                            codec=metadata.get('codec'),
                            bitrate=metadata.get('bitrate'),
                            date_taken=metadata.get('date_taken'),  # CRITICAL FIX: Save date_taken for date filtering
                            metadata_status='ok'
                        )

                        success_count += 1
                        logger.info(f"[VideoMetadataWorker] ✓ {video_path}: {metadata.get('duration_seconds', 0):.1f}s")

                    else:
                        # Metadata extraction failed
                        self.video_repo.update(
                            video_id=video['id'],
                            metadata_status='error'
                        )
                        failed_count += 1
                        error_msg = "Failed to extract metadata"
                        self.signals.error.emit(video_path, error_msg)
                        logger.error(f"[VideoMetadataWorker] ✗ {video_path}: {error_msg}")

                except Exception as e:
                    # Error during extraction
                    error_msg = str(e)
                    logger.error(f"[VideoMetadataWorker] Error processing {video_path}: {error_msg}")

                    try:
                        self.video_repo.update(
                            video_id=video['id'],
                            metadata_status='error'
                        )
                    except Exception:
                        pass

                    self.signals.error.emit(video_path, error_msg)
                    failed_count += 1

        except Exception as e:
            logger.error(f"[VideoMetadataWorker] Fatal error: {e}")
            import traceback
            traceback.print_exc()

        finally:
            # Emit completion signal
            self.signals.finished.emit(success_count, failed_count)
            logger.info(
                f"[VideoMetadataWorker] Finished: {success_count} success, "
                f"{failed_count} failed"
            )


def main():
    """
    Standalone entry point for running worker as separate process.

    Usage:
        python workers/video_metadata_worker.py <project_id>
    """
    import sys
    from PySide6.QtCore import QCoreApplication, QThreadPool

    if len(sys.argv) < 2:
        print("Usage: python video_metadata_worker.py <project_id>")
        sys.exit(1)

    project_id = int(sys.argv[1])

    app = QCoreApplication(sys.argv)

    # Create and run worker
    worker = VideoMetadataWorker(project_id=project_id)

    def on_progress(current, total, path):
        print(f"[{current}/{total}] Processing: {os.path.basename(path)}")

    def on_finished(success, failed):
        print(f"\n✓ Completed: {success} success, {failed} failed")
        app.quit()

    def on_error(path, error):
        print(f"✗ Error: {os.path.basename(path)} - {error}")

    worker.signals.progress.connect(on_progress)
    worker.signals.finished.connect(on_finished)
    worker.signals.error.connect(on_error)

    # Start worker
    QThreadPool.globalInstance().start(worker)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
