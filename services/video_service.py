# services/video_service.py
# Version 1.0.0 dated 2025-11-09
# Business logic layer for video operations

from typing import Optional, List, Dict, Any
from pathlib import Path
from logging_config import get_logger

logger = get_logger(__name__)


class VideoService:
    """
    Business logic layer for video operations (Schema v3.2.0).

    Coordinates between VideoRepository (data access), VideoMetadataService (metadata extraction),
    and VideoThumbnailService (thumbnail generation).

    This service provides high-level video operations with business logic,
    error handling, and coordination between multiple services.
    """

    def __init__(self):
        """Initialize VideoService with repository and helper services."""
        from repository.video_repository import VideoRepository

        self._video_repo = VideoRepository()
        self.logger = logger

    # ========================================================================
    # VIDEO CRUD OPERATIONS
    # ========================================================================

    def get_video_by_path(self, path: str, project_id: int) -> Optional[Dict[str, Any]]:
        """
        Get video metadata by file path.

        Args:
            path: Video file path
            project_id: Project ID

        Returns:
            Video metadata dict, or None if not found

        Example:
            >>> service.get_video_by_path("/videos/clip.mp4", project_id=1)
            {'id': 1, 'path': '/videos/clip.mp4', 'duration_seconds': 45.2, ...}
        """
        try:
            return self._video_repo.get_by_path(path, project_id)
        except Exception as e:
            self.logger.error(f"Failed to get video by path {path}: {e}")
            return None

    def get_videos_by_project(self, project_id: int) -> List[Dict[str, Any]]:
        """
        Get all videos in a project.

        Args:
            project_id: Project ID

        Returns:
            List of video metadata dicts

        Example:
            >>> service.get_videos_by_project(project_id=1)
            [{'id': 1, 'path': '/vid1.mp4', ...}, {'id': 2, 'path': '/vid2.mp4', ...}]
        """
        try:
            return self._video_repo.get_by_project(project_id)
        except Exception as e:
            self.logger.error(f"Failed to get videos for project {project_id}: {e}")
            return []

    def get_videos_by_folder(self, folder_id: int, project_id: int) -> List[Dict[str, Any]]:
        """
        Get all videos in a folder.

        Args:
            folder_id: Folder ID
            project_id: Project ID

        Returns:
            List of video metadata dicts

        Example:
            >>> service.get_videos_by_folder(folder_id=5, project_id=1)
            [{'id': 1, 'path': '/videos/clip.mp4', ...}]
        """
        try:
            return self._video_repo.get_by_folder(folder_id, project_id)
        except Exception as e:
            self.logger.error(f"Failed to get videos for folder {folder_id}: {e}")
            return []

    def create_video(self, path: str, folder_id: int, project_id: int, **metadata) -> Optional[int]:
        """
        Create a new video metadata entry.

        Args:
            path: Video file path
            folder_id: Folder ID
            project_id: Project ID
            **metadata: Optional metadata fields

        Returns:
            Video ID, or None if creation failed

        Example:
            >>> service.create_video("/videos/clip.mp4", folder_id=5, project_id=1,
            ...                      size_kb=102400, duration_seconds=45.2)
            123
        """
        try:
            video_id = self._video_repo.create(path, folder_id, project_id, **metadata)
            self.logger.info(f"Created video {path} (id={video_id})")
            return video_id
        except Exception as e:
            self.logger.error(f"Failed to create video {path}: {e}")
            return None

    def update_video(self, video_id: int, **metadata) -> bool:
        """
        Update video metadata fields.

        Args:
            video_id: Video ID
            **metadata: Fields to update

        Returns:
            True if updated, False if failed

        Example:
            >>> service.update_video(123, duration_seconds=45.2, fps=30.0)
            True
        """
        try:
            success = self._video_repo.update(video_id, **metadata)
            if success:
                self.logger.info(f"Updated video {video_id}: {list(metadata.keys())}")
            return success
        except Exception as e:
            self.logger.error(f"Failed to update video {video_id}: {e}")
            return False

    def delete_video(self, video_id: int) -> bool:
        """
        Delete a video (CASCADE removes associations).

        Args:
            video_id: Video ID

        Returns:
            True if deleted, False if failed

        Example:
            >>> service.delete_video(123)
            True
        """
        try:
            success = self._video_repo.delete(video_id)
            if success:
                self.logger.info(f"Deleted video {video_id}")
            return success
        except Exception as e:
            self.logger.error(f"Failed to delete video {video_id}: {e}")
            return False

    def index_video(self, path: str, project_id: int, folder_id: int = None,
                   size_kb: float = None, modified: str = None) -> Optional[int]:
        """
        Index a video file during scanning.

        Creates a video metadata entry with 'pending' status for background processing.
        This method is called by PhotoScanService during repository scans.

        Args:
            path: Video file path
            project_id: Project ID
            folder_id: Folder ID (optional)
            size_kb: File size in KB (optional)
            modified: Modified timestamp (optional)

        Returns:
            Video ID, or None if indexing failed

        Example:
            >>> service.index_video("/videos/clip.mp4", project_id=1, folder_id=5,
            ...                     size_kb=102400, modified="2025-01-01 12:00:00")
            123
        """
        try:
            # Check if video already exists
            existing = self.get_video_by_path(path, project_id)
            if existing:
                self.logger.debug(f"Video already indexed: {path}")
                return existing.get('id')

            # Create new video entry with pending status
            video_id = self._video_repo.create(
                path=path,
                folder_id=folder_id,
                project_id=project_id,
                size_kb=size_kb,
                modified=modified,
                metadata_status='pending',
                thumbnail_status='pending'
            )

            if video_id:
                self.logger.info(f"Indexed video {path} (id={video_id}, status=pending)")
            return video_id

        except Exception as e:
            self.logger.error(f"Failed to index video {path}: {e}")
            return None

    # ========================================================================
    # BULK OPERATIONS
    # ========================================================================

    def bulk_create_videos(self, video_paths: List[str], folder_id: int, project_id: int) -> int:
        """
        Bulk create video metadata entries.

        Args:
            video_paths: List of video file paths
            folder_id: Folder ID
            project_id: Project ID

        Returns:
            Number of videos created

        Example:
            >>> paths = ['/vid1.mp4', '/vid2.mp4', '/vid3.mp4']
            >>> service.bulk_create_videos(paths, folder_id=5, project_id=1)
            3
        """
        if not video_paths:
            return 0

        rows = []
        for path in video_paths:
            # Check if file exists
            if not Path(path).exists():
                self.logger.warning(f"Video file not found: {path}")
                continue

            # Get file size
            try:
                size_kb = Path(path).stat().st_size / 1024
            except Exception as e:
                self.logger.warning(f"Failed to get size for {path}: {e}")
                size_kb = None

            rows.append({
                'path': path,
                'folder_id': folder_id,
                'size_kb': size_kb,
                'metadata_status': 'pending',
                'thumbnail_status': 'pending'
            })

        try:
            count = self._video_repo.bulk_upsert(rows, project_id)
            self.logger.info(f"Bulk created {count} videos for project {project_id}")
            return count
        except Exception as e:
            self.logger.error(f"Failed to bulk create videos: {e}")
            return 0

    def get_unprocessed_videos(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get videos that need metadata extraction.

        Args:
            limit: Maximum number of videos to return

        Returns:
            List of video metadata dicts with pending status

        Example:
            >>> service.get_unprocessed_videos(limit=50)
            [{'id': 1, 'path': '/vid1.mp4', 'metadata_status': 'pending', ...}]
        """
        try:
            return self._video_repo.get_unprocessed_videos(limit)
        except Exception as e:
            self.logger.error(f"Failed to get unprocessed videos: {e}")
            return []

    # ========================================================================
    # PROJECT-VIDEO ASSOCIATIONS
    # ========================================================================

    def add_to_branch(self, project_id: int, branch_key: str, video_path: str, label: str = None) -> bool:
        """
        Add video to a project branch.

        Args:
            project_id: Project ID
            branch_key: Branch key (e.g., 'all', date, folder name)
            video_path: Video file path
            label: Optional label

        Returns:
            True if added, False if already exists

        Example:
            >>> service.add_to_branch(project_id=1, branch_key='all', video_path='/vid1.mp4')
            True
        """
        try:
            success = self._video_repo.add_to_project_branch(project_id, branch_key, video_path, label)
            if success:
                self.logger.debug(f"Added video to branch {project_id}/{branch_key}")
            return success
        except Exception as e:
            self.logger.error(f"Failed to add video to branch: {e}")
            return False

    def get_videos_by_branch(self, project_id: int, branch_key: str) -> List[str]:
        """
        Get all video paths in a project branch.

        Args:
            project_id: Project ID
            branch_key: Branch key

        Returns:
            List of video file paths

        Example:
            >>> service.get_videos_by_branch(project_id=1, branch_key='all')
            ['/vid1.mp4', '/vid2.mp4', '/vid3.mp4']
        """
        try:
            return self._video_repo.get_videos_by_branch(project_id, branch_key)
        except Exception as e:
            self.logger.error(f"Failed to get videos for branch {project_id}/{branch_key}: {e}")
            return []

    # ========================================================================
    # VIDEO TAGGING
    # ========================================================================

    def add_tag_to_video(self, video_id: int, tag_id: int) -> bool:
        """
        Add a tag to a video.

        Args:
            video_id: Video ID
            tag_id: Tag ID

        Returns:
            True if added, False if already existed

        Example:
            >>> service.add_tag_to_video(video_id=123, tag_id=5)
            True
        """
        try:
            success = self._video_repo.add_tag(video_id, tag_id)
            if success:
                self.logger.info(f"Tagged video {video_id} with tag {tag_id}")
            return success
        except Exception as e:
            self.logger.error(f"Failed to tag video {video_id}: {e}")
            return False

    def remove_tag_from_video(self, video_id: int, tag_id: int) -> bool:
        """
        Remove a tag from a video.

        Args:
            video_id: Video ID
            tag_id: Tag ID

        Returns:
            True if removed, False if didn't exist

        Example:
            >>> service.remove_tag_from_video(video_id=123, tag_id=5)
            True
        """
        try:
            success = self._video_repo.remove_tag(video_id, tag_id)
            if success:
                self.logger.info(f"Removed tag {tag_id} from video {video_id}")
            return success
        except Exception as e:
            self.logger.error(f"Failed to remove tag from video {video_id}: {e}")
            return False

    def get_tags_for_video(self, video_id: int) -> List[Dict[str, Any]]:
        """
        Get all tags for a video.

        Args:
            video_id: Video ID

        Returns:
            List of tag dicts with 'id' and 'name'

        Example:
            >>> service.get_tags_for_video(video_id=123)
            [{'id': 1, 'name': 'vacation'}, {'id': 2, 'name': 'family'}]
        """
        try:
            return self._video_repo.get_tags_for_video(video_id)
        except Exception as e:
            self.logger.error(f"Failed to get tags for video {video_id}: {e}")
            return []

    def get_videos_by_tag(self, tag_id: int) -> List[int]:
        """
        Get all video IDs that have a specific tag.

        Args:
            tag_id: Tag ID

        Returns:
            List of video IDs

        Example:
            >>> service.get_videos_by_tag(tag_id=5)
            [123, 124, 125]
        """
        try:
            return self._video_repo.get_videos_by_tag(tag_id)
        except Exception as e:
            self.logger.error(f"Failed to get videos for tag {tag_id}: {e}")
            return []

    # ========================================================================
    # UTILITY METHODS
    # ========================================================================

    def is_video_file(self, path: str) -> bool:
        """
        Check if a file is a supported video format.

        Args:
            path: File path

        Returns:
            True if file is a supported video format

        Example:
            >>> service.is_video_file('/videos/clip.mp4')
            True
            >>> service.is_video_file('/photos/image.jpg')
            False
        """
        VIDEO_EXTENSIONS = {
            '.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm',
            '.m4v', '.mpg', '.mpeg', '.3gp', '.ogv', '.ts', '.mts'
        }

        ext = Path(path).suffix.lower()
        return ext in VIDEO_EXTENSIONS

    def get_video_info(self, video_id: int) -> Optional[Dict[str, Any]]:
        """
        Get complete video information including metadata and tags.

        Args:
            video_id: Video ID

        Returns:
            Dict with video metadata and tags, or None if not found

        Example:
            >>> service.get_video_info(video_id=123)
            {
                'id': 123,
                'path': '/vid1.mp4',
                'duration_seconds': 45.2,
                'width': 1920,
                'height': 1080,
                'tags': [{'id': 1, 'name': 'vacation'}]
            }
        """
        try:
            # Get video metadata (we need project_id, but we can get it from the video record)
            # For now, we'll need to modify this to work properly
            # This is a simplified version

            # We'll need to update this method once we have a way to get video by ID
            # For now, return None
            self.logger.warning("get_video_info not fully implemented yet")
            return None
        except Exception as e:
            self.logger.error(f"Failed to get video info for {video_id}: {e}")
            return None


# ========================================================================
# SINGLETON PATTERN
# ========================================================================

_video_service_instance = None


def get_video_service() -> VideoService:
    """
    Get singleton VideoService instance.

    Returns:
        VideoService instance

    Example:
        >>> from services.video_service import get_video_service
        >>> video_service = get_video_service()
        >>> videos = video_service.get_videos_by_project(project_id=1)
    """
    global _video_service_instance
    if _video_service_instance is None:
        _video_service_instance = VideoService()
    return _video_service_instance
