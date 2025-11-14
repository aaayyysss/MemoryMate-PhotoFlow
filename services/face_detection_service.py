# services/face_detection_service.py
# Version 01.00.00.01 (Phase 7.0 – People / Face Detection)
# Service layer for face detection integration
# Based on successful proof of concept architecture
# ------------------------------------------------------

"""
Face Detection Service

This service provides face detection and embedding generation for the MemoryMate app.
It integrates the InsightFace-based face detection worker with the existing architecture.

Key Features:
- Automatic face detection in photos
- Face embedding generation for clustering
- Integration with existing scan workflow
- Performance tracking and benchmarking
- Graceful fallback when models unavailable

Based on:
- Proof of concept from OldPy/photo_sorter.py
- Service layer architecture from existing services
"""

import os
import time
from typing import List, Dict, Optional, Callable
from pathlib import Path

from logging_config import get_logger
from reference_db import ReferenceDB

logger = get_logger(__name__)


class FaceDetectionService:
    """
    Service for detecting faces in photos and generating embeddings.

    This service wraps the face detection worker and provides:
    - High-level interface for face detection
    - Integration with existing database layer
    - Performance tracking
    - Error handling and logging
    """

    def __init__(self, db: Optional[ReferenceDB] = None):
        """
        Initialize face detection service.

        Args:
            db: Optional ReferenceDB instance (creates new if not provided)
        """
        self.db = db or ReferenceDB()
        self.logger = get_logger(__name__)

    def is_available(self) -> bool:
        """
        Check if face detection is available (InsightFace installed and models present).

        Returns:
            bool: True if face detection is available
        """
        try:
            from workers.face_detection_worker import get_buffalo_model
            model = get_buffalo_model()
            return model is not None
        except ImportError:
            return False

    def get_model_status(self) -> Dict[str, any]:
        """
        Get detailed status of face detection model.

        Returns:
            Dict with keys:
            - 'available': bool
            - 'model_dir': str or None
            - 'providers': list or None
            - 'error': str or None
        """
        try:
            from workers.face_detection_worker import get_buffalo_model, _MODEL_CACHE

            model = get_buffalo_model()

            if model is None:
                return {
                    'available': False,
                    'model_dir': None,
                    'providers': None,
                    'error': 'Model not available or not installed'
                }

            return {
                'available': True,
                'model_dir': _MODEL_CACHE.get('model_dir'),
                'providers': _MODEL_CACHE.get('providers'),
                'error': None
            }

        except ImportError as e:
            return {
                'available': False,
                'model_dir': None,
                'providers': None,
                'error': f'InsightFace not installed: {e}'
            }
        except Exception as e:
            return {
                'available': False,
                'model_dir': None,
                'providers': None,
                'error': str(e)
            }

    def detect_faces_batch(self,
                          project_id: int,
                          photo_paths: List[str],
                          progress_callback: Optional[Callable[[int, int, Dict], None]] = None) -> Dict:
        """
        Detect faces in a batch of photos.

        Args:
            project_id: Project ID
            photo_paths: List of photo file paths
            progress_callback: Optional callback(current, total, stats)

        Returns:
            Dict with statistics:
            {
                'photos_processed': int,
                'faces_detected': int,
                'photos_with_faces': int,
                'errors': int,
                'duration_seconds': float,
                'faces_per_second': float
            }
        """
        if not self.is_available():
            self.logger.error("Face detection not available")
            return {
                'photos_processed': 0,
                'faces_detected': 0,
                'photos_with_faces': 0,
                'errors': len(photo_paths),
                'duration_seconds': 0,
                'faces_per_second': 0,
                'error': 'Face detection not available'
            }

        # Get project folder for face crops
        conn = self.db._connect()
        cur = conn.cursor()
        cur.execute("SELECT folder FROM projects WHERE id=?", (project_id,))
        result = cur.fetchone()
        conn.close()

        if not result:
            self.logger.error(f"Project {project_id} not found")
            return {
                'photos_processed': 0,
                'faces_detected': 0,
                'photos_with_faces': 0,
                'errors': len(photo_paths),
                'duration_seconds': 0,
                'faces_per_second': 0,
                'error': f'Project {project_id} not found'
            }

        project_folder = result[0]
        face_crops_dir = os.path.join(project_folder, ".memorymate", "face_crops")

        # Import worker
        from workers.face_detection_worker import process_photos_for_project

        # Track performance
        start_time = time.time()

        # Process with optional progress callback
        def wrapped_progress(current, total):
            # Get intermediate stats (we'll calculate on the fly)
            if progress_callback:
                progress_callback(current, total, {})

        stats = process_photos_for_project(
            project_id=project_id,
            photo_paths=photo_paths,
            face_crops_dir=face_crops_dir,
            progress_callback=wrapped_progress if progress_callback else None
        )

        # Add performance metrics
        duration = time.time() - start_time
        stats['duration_seconds'] = duration

        if duration > 0 and stats['faces_detected'] > 0:
            stats['faces_per_second'] = stats['faces_detected'] / duration
        else:
            stats['faces_per_second'] = 0

        self.logger.info(f"Face detection complete: {stats}")
        return stats

    def detect_faces_single(self, project_id: int, photo_path: str) -> Dict:
        """
        Detect faces in a single photo.

        Args:
            project_id: Project ID
            photo_path: Path to photo file

        Returns:
            Dict with statistics (same as detect_faces_batch)
        """
        return self.detect_faces_batch(project_id, [photo_path])

    def get_face_count(self, project_id: int) -> int:
        """
        Get total number of detected faces for a project.

        Args:
            project_id: Project ID

        Returns:
            int: Number of face crops in database
        """
        conn = self.db._connect()
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM face_crops
            WHERE project_id = ?
        """, (project_id,))
        result = cur.fetchone()
        conn.close()
        return result[0] if result else 0

    def clear_faces(self, project_id: int):
        """
        Clear all face detection data for a project.

        This removes:
        - Face crop records from database
        - Face crop files from disk
        - Cluster data

        Args:
            project_id: Project ID
        """
        conn = self.db._connect()
        cur = conn.cursor()

        # Get all face crop paths before deletion
        cur.execute("""
            SELECT crop_path FROM face_crops
            WHERE project_id = ?
        """, (project_id,))
        crop_paths = [row[0] for row in cur.fetchall()]

        # Delete from database
        cur.execute("DELETE FROM face_crops WHERE project_id = ?", (project_id,))
        cur.execute("DELETE FROM face_branch_reps WHERE project_id = ?", (project_id,))
        cur.execute("DELETE FROM branches WHERE project_id = ? AND type = 'face'", (project_id,))

        conn.commit()
        conn.close()

        # Delete crop files from disk
        deleted = 0
        for crop_path in crop_paths:
            try:
                if os.path.exists(crop_path):
                    os.remove(crop_path)
                    deleted += 1
            except Exception as e:
                self.logger.warning(f"Failed to delete {crop_path}: {e}")

        self.logger.info(f"Cleared {len(crop_paths)} face crops from project {project_id} ({deleted} files deleted)")
