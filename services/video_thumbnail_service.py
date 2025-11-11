# services/video_thumbnail_service.py
# Version 1.0.0 dated 2025-11-09
# Video thumbnail generation using ffmpeg

import subprocess
from typing import Optional
from pathlib import Path
from logging_config import get_logger

logger = get_logger(__name__)


class VideoThumbnailService:
    """
    Service for generating video thumbnails using ffmpeg.

    Extracts a single frame from the video to use as a thumbnail.
    Default: Extract frame at 10% of video duration (or 1 second if duration unknown).

    Thumbnail format: JPEG (for compatibility and file size)
    """

    def __init__(self, thumbnail_dir: str = ".thumb_cache"):
        """
        Initialize VideoThumbnailService.

        Args:
            thumbnail_dir: Directory to store video thumbnails (default: .thumb_cache)
        """
        self.logger = logger
        self.thumbnail_dir = Path(thumbnail_dir)
        self._ffmpeg_path = self._get_ffmpeg_path()
        self._ffmpeg_available = self._check_ffmpeg()

        # Create thumbnail directory if it doesn't exist
        try:
            self.thumbnail_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.logger.error(f"Failed to create thumbnail directory {thumbnail_dir}: {e}")

    def _get_ffmpeg_path(self) -> str:
        """
        Get ffmpeg path from settings or default to 'ffmpeg'.

        Checks for custom ffprobe path in settings and looks for ffmpeg in same directory.

        Returns:
            Path to ffmpeg executable
        """
        try:
            from settings_manager_qt import SettingsManager
            import os
            settings = SettingsManager()
            ffprobe_path = settings.get_setting('ffprobe_path', '')
            if ffprobe_path:
                # If custom ffprobe path is set, try to find ffmpeg in same directory
                ffprobe_dir = Path(ffprobe_path).parent
                potential_ffmpeg = ffprobe_dir / 'ffmpeg.exe' if os.name == 'nt' else ffprobe_dir / 'ffmpeg'
                if potential_ffmpeg.exists():
                    self.logger.info(f"Using ffmpeg from same directory as ffprobe: {potential_ffmpeg}")
                    return str(potential_ffmpeg)
        except Exception as e:
            self.logger.debug(f"Could not load ffmpeg path from settings: {e}")
        return 'ffmpeg'  # Default to system PATH

    def _check_ffmpeg(self) -> bool:
        """
        Check if ffmpeg is available at the configured path.

        Returns:
            True if ffmpeg is available, False otherwise
        """
        try:
            result = subprocess.run(
                [self._ffmpeg_path, '-version'],
                capture_output=True,
                text=True,
                timeout=5
            )
            available = result.returncode == 0
            if available:
                if self._ffmpeg_path != 'ffmpeg':
                    self.logger.info(f"ffmpeg detected at '{self._ffmpeg_path}' - video thumbnail generation enabled")
                else:
                    self.logger.info("ffmpeg detected - video thumbnail generation enabled")
            else:
                self.logger.warning("ffmpeg not available - video thumbnail generation disabled")
            return available
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self.logger.warning("ffmpeg not found - video thumbnail generation disabled")
            return False

    def generate_thumbnail(
        self,
        video_path: str,
        output_path: Optional[str] = None,
        timestamp: Optional[float] = None,
        width: int = 320,
        height: int = 240
    ) -> Optional[str]:
        """
        Generate a thumbnail for a video file.

        Args:
            video_path: Path to video file
            output_path: Optional output path (default: auto-generated in thumbnail_dir)
            timestamp: Optional timestamp in seconds to extract frame from (default: 10% of duration or 1 second)
            width: Thumbnail width in pixels (default: 320)
            height: Thumbnail height in pixels (default: 240)

        Returns:
            Path to generated thumbnail, or None if failed

        Example:
            >>> service.generate_thumbnail('/videos/clip.mp4')
            '.thumb_cache/clip_mp4_thumb.jpg'

            >>> service.generate_thumbnail('/videos/clip.mp4', timestamp=5.0, width=640, height=480)
            '.thumb_cache/clip_mp4_thumb.jpg'
        """
        if not self._ffmpeg_available:
            self.logger.warning(f"Cannot generate thumbnail for {video_path} (ffmpeg not available)")
            return None

        # Generate output path if not provided
        if output_path is None:
            video_name = Path(video_path).stem
            video_ext = Path(video_path).suffix.replace('.', '_')
            output_path = self.thumbnail_dir / f"{video_name}{video_ext}_thumb.jpg"
        else:
            output_path = Path(output_path)

        # Determine timestamp to extract frame from
        if timestamp is None:
            # Try to get 10% of video duration
            timestamp = self._get_default_timestamp(video_path)

        try:
            # Use ffmpeg to extract frame
            cmd = [
                self._ffmpeg_path,  # Use configured ffmpeg path
                '-y',  # Overwrite output file
                '-ss', str(timestamp),  # Seek to timestamp
                '-i', video_path,  # Input file
                '-vframes', '1',  # Extract 1 frame
                '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease',  # Scale
                '-q:v', '2',  # Quality (2 = high quality for JPEG)
                str(output_path)
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                self.logger.error(f"ffmpeg failed for {video_path}: {result.stderr}")
                return None

            # Verify thumbnail was created
            if output_path.exists() and output_path.stat().st_size > 0:
                self.logger.info(f"Generated thumbnail for {video_path} at {output_path}")
                return str(output_path)
            else:
                self.logger.error(f"Thumbnail file not created or empty: {output_path}")
                return None

        except subprocess.TimeoutExpired:
            self.logger.error(f"ffmpeg timeout for {video_path}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error generating thumbnail for {video_path}: {e}")
            return None

    def _get_default_timestamp(self, video_path: str) -> float:
        """
        Get default timestamp for thumbnail extraction (10% of duration or 1 second).

        Args:
            video_path: Path to video file

        Returns:
            Timestamp in seconds
        """
        try:
            # Try to get video duration using ffprobe
            cmd = [
                'ffprobe',
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                video_path
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                duration = float(result.stdout.strip())
                # Extract frame at 10% of duration, or at least 1 second in
                return max(1.0, duration * 0.1)

        except (ValueError, subprocess.TimeoutExpired, Exception) as e:
            self.logger.debug(f"Failed to get duration for {video_path}: {e}")

        # Default: 1 second
        return 1.0

    def get_thumbnail_path(self, video_path: str) -> Path:
        """
        Get the expected thumbnail path for a video (without generating it).

        Args:
            video_path: Path to video file

        Returns:
            Expected thumbnail path

        Example:
            >>> service.get_thumbnail_path('/videos/clip.mp4')
            PosixPath('.thumb_cache/clip_mp4_thumb.jpg')
        """
        video_name = Path(video_path).stem
        video_ext = Path(video_path).suffix.replace('.', '_')
        return self.thumbnail_dir / f"{video_name}{video_ext}_thumb.jpg"

    def thumbnail_exists(self, video_path: str) -> bool:
        """
        Check if a thumbnail already exists for a video.

        Args:
            video_path: Path to video file

        Returns:
            True if thumbnail exists, False otherwise

        Example:
            >>> service.thumbnail_exists('/videos/clip.mp4')
            False
            >>> service.generate_thumbnail('/videos/clip.mp4')
            >>> service.thumbnail_exists('/videos/clip.mp4')
            True
        """
        thumb_path = self.get_thumbnail_path(video_path)
        return thumb_path.exists() and thumb_path.stat().st_size > 0

    def delete_thumbnail(self, video_path: str) -> bool:
        """
        Delete the thumbnail for a video.

        Args:
            video_path: Path to video file

        Returns:
            True if deleted, False if didn't exist

        Example:
            >>> service.delete_thumbnail('/videos/clip.mp4')
            True
        """
        thumb_path = self.get_thumbnail_path(video_path)
        try:
            if thumb_path.exists():
                thumb_path.unlink()
                self.logger.info(f"Deleted thumbnail: {thumb_path}")
                return True
            return False
        except Exception as e:
            self.logger.error(f"Failed to delete thumbnail {thumb_path}: {e}")
            return False

    def is_ffmpeg_available(self) -> bool:
        """
        Check if ffmpeg is available.

        Returns:
            True if ffmpeg is available, False otherwise

        Example:
            >>> service.is_ffmpeg_available()
            True
        """
        return self._ffmpeg_available

    def generate_thumbnails_batch(
        self,
        video_paths: list[str],
        width: int = 320,
        height: int = 240
    ) -> dict[str, Optional[str]]:
        """
        Generate thumbnails for multiple videos.

        Args:
            video_paths: List of video file paths
            width: Thumbnail width in pixels
            height: Thumbnail height in pixels

        Returns:
            Dict mapping video_path to thumbnail_path (None if failed)

        Example:
            >>> paths = ['/vid1.mp4', '/vid2.mp4', '/vid3.mp4']
            >>> results = service.generate_thumbnails_batch(paths)
            >>> results
            {
                '/vid1.mp4': '.thumb_cache/vid1_mp4_thumb.jpg',
                '/vid2.mp4': '.thumb_cache/vid2_mp4_thumb.jpg',
                '/vid3.mp4': None  # Failed
            }
        """
        results = {}

        for video_path in video_paths:
            # Skip if thumbnail already exists
            if self.thumbnail_exists(video_path):
                results[video_path] = str(self.get_thumbnail_path(video_path))
                continue

            # Generate thumbnail
            thumb_path = self.generate_thumbnail(video_path, width=width, height=height)
            results[video_path] = thumb_path

        succeeded = sum(1 for v in results.values() if v is not None)
        self.logger.info(f"Batch thumbnail generation: {succeeded}/{len(video_paths)} succeeded")

        return results


# ========================================================================
# SINGLETON PATTERN
# ========================================================================

_video_thumbnail_service_instance = None


def get_video_thumbnail_service() -> VideoThumbnailService:
    """
    Get singleton VideoThumbnailService instance.

    Returns:
        VideoThumbnailService instance

    Example:
        >>> from services.video_thumbnail_service import get_video_thumbnail_service
        >>> service = get_video_thumbnail_service()
        >>> thumb_path = service.generate_thumbnail('/videos/clip.mp4')
    """
    global _video_thumbnail_service_instance
    if _video_thumbnail_service_instance is None:
        _video_thumbnail_service_instance = VideoThumbnailService()
    return _video_thumbnail_service_instance
