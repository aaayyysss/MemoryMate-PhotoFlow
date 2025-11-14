# face_detection_worker.py
# Phase 5: Face Detection Worker
# Background worker for detecting faces and generating embeddings
# Populates face_crops table with detected faces
# ------------------------------------------------------

import os
import time
import numpy as np
from typing import Optional
from PySide6.QtCore import QRunnable, QObject, Signal, Slot
import logging

from reference_db import ReferenceDB
from services.face_detection_service import get_face_detection_service

logger = logging.getLogger(__name__)


class FaceDetectionSignals(QObject):
    """
    Signals for face detection worker progress reporting.
    """
    # progress(current, total, message)
    progress = Signal(int, int, str)

    # face_detected(image_path, face_count)
    face_detected = Signal(str, int)

    # finished(success_count, failed_count, total_faces)
    finished = Signal(int, int, int)

    # error(image_path, error_message)
    error = Signal(str, str)


class FaceDetectionWorker(QRunnable):
    """
    Background worker for detecting faces in photos.

    Processes all photos in a project, detects faces, generates embeddings,
    and saves results to face_crops table.

    Usage:
        worker = FaceDetectionWorker(project_id=1)
        worker.signals.progress.connect(on_progress)
        worker.signals.finished.connect(on_finished)
        QThreadPool.globalInstance().start(worker)

    Performance:
        - Uses HOG model (fast, CPU-friendly)
        - Processes ~1-2 photos/second
        - Parallel processing NOT recommended (CPU-intensive)
        - For 1000 photos: ~10-15 minutes

    Features:
        - Skips photos already processed
        - Saves face crops to .memorymate/faces/
        - Generates 128-dim embeddings
        - Error handling and progress reporting
    """

    def __init__(self, project_id: int, model: str = "hog",
                 skip_processed: bool = True, max_faces_per_photo: int = 10):
        """
        Initialize face detection worker.

        Args:
            project_id: Project ID to process photos for
            model: Detection model ("hog" or "cnn")
            skip_processed: Skip photos already in face_crops table
            max_faces_per_photo: Maximum faces to detect per photo (prevent memory issues)
        """
        super().__init__()
        self.project_id = project_id
        self.model = model
        self.skip_processed = skip_processed
        self.max_faces_per_photo = max_faces_per_photo
        self.signals = FaceDetectionSignals()
        self.cancelled = False

        # Statistics
        self._stats = {
            'photos_processed': 0,
            'photos_skipped': 0,
            'photos_failed': 0,
            'faces_detected': 0
        }

    def cancel(self):
        """Cancel the detection process."""
        self.cancelled = True
        logger.info("[FaceDetectionWorker] Cancellation requested")

    @Slot()
    def run(self):
        """Main worker execution."""
        logger.info(f"[FaceDetectionWorker] Starting face detection for project {self.project_id}")
        start_time = time.time()

        try:
            # Initialize services
            db = ReferenceDB()
            face_service = get_face_detection_service(model=self.model)

            # Get all photos for this project
            photos = self._get_photos_to_process(db)
            total_photos = len(photos)

            if total_photos == 0:
                logger.info("[FaceDetectionWorker] No photos to process")
                self.signals.finished.emit(0, 0, 0)
                return

            logger.info(f"[FaceDetectionWorker] Processing {total_photos} photos")

            # Create face crops directory
            face_crops_dir = os.path.join(os.getcwd(), ".memorymate", "faces")
            os.makedirs(face_crops_dir, exist_ok=True)

            # Process each photo
            for idx, photo in enumerate(photos, 1):
                if self.cancelled:
                    logger.info("[FaceDetectionWorker] Cancelled by user")
                    break

                photo_path = photo['path']

                # Emit progress
                self.signals.progress.emit(
                    idx, total_photos,
                    f"Detecting faces: {os.path.basename(photo_path)}"
                )

                # Detect faces
                try:
                    faces = face_service.detect_faces(photo_path)

                    if not faces:
                        self._stats['photos_processed'] += 1
                        continue

                    # Limit faces per photo (prevent memory issues with large group photos)
                    if len(faces) > self.max_faces_per_photo:
                        logger.warning(
                            f"[FaceDetectionWorker] {photo_path} has {len(faces)} faces, "
                            f"keeping largest {self.max_faces_per_photo}"
                        )
                        # Sort by face size (bbox_w * bbox_h) and keep largest
                        faces = sorted(faces, key=lambda f: f['bbox_w'] * f['bbox_h'], reverse=True)
                        faces = faces[:self.max_faces_per_photo]

                    # Save faces to database
                    for face_idx, face in enumerate(faces):
                        self._save_face(db, photo_path, face, face_idx, face_crops_dir)

                    self._stats['photos_processed'] += 1
                    self._stats['faces_detected'] += len(faces)

                    # Emit face detected signal
                    self.signals.face_detected.emit(photo_path, len(faces))

                    logger.info(f"[FaceDetectionWorker] ✓ {photo_path}: {len(faces)} faces")

                except Exception as e:
                    self._stats['photos_failed'] += 1
                    error_msg = str(e)
                    logger.error(f"[FaceDetectionWorker] ✗ {photo_path}: {error_msg}")
                    self.signals.error.emit(photo_path, error_msg)

            # Finalize
            duration = time.time() - start_time
            logger.info(
                f"[FaceDetectionWorker] Complete in {duration:.1f}s: "
                f"{self._stats['photos_processed']} processed, "
                f"{self._stats['faces_detected']} faces detected, "
                f"{self._stats['photos_failed']} failed"
            )

            self.signals.finished.emit(
                self._stats['photos_processed'],
                self._stats['photos_failed'],
                self._stats['faces_detected']
            )

        except Exception as e:
            logger.error(f"[FaceDetectionWorker] Fatal error: {e}", exc_info=True)
            self.signals.finished.emit(0, 0, 0)

    def _get_photos_to_process(self, db: ReferenceDB) -> list:
        """
        Get list of photos to process.

        Returns photos that haven't been processed yet (if skip_processed=True).
        """
        with db._connect() as conn:
            cur = conn.cursor()

            if self.skip_processed:
                # Get photos not in face_crops table
                cur.execute("""
                    SELECT DISTINCT pm.path, pm.project_id
                    FROM photo_metadata pm
                    WHERE pm.project_id = ?
                      AND pm.path NOT IN (
                          SELECT DISTINCT image_path FROM face_crops WHERE project_id = ?
                      )
                    ORDER BY pm.path
                """, (self.project_id, self.project_id))
            else:
                # Get all photos
                cur.execute("""
                    SELECT path, project_id
                    FROM photo_metadata
                    WHERE project_id = ?
                    ORDER BY path
                """, (self.project_id,))

            return [{'path': row[0], 'project_id': row[1]} for row in cur.fetchall()]

    def _save_face(self, db: ReferenceDB, image_path: str, face: dict,
                   face_idx: int, face_crops_dir: str):
        """
        Save detected face to database and disk.

        Args:
            db: Database instance
            image_path: Original photo path
            face: Face dictionary with bbox and embedding
            face_idx: Face index in photo (for naming)
            face_crops_dir: Directory to save face crops
        """
        try:
            # Generate crop filename
            image_basename = os.path.splitext(os.path.basename(image_path))[0]
            crop_filename = f"{image_basename}_face{face_idx}.jpg"
            crop_path = os.path.join(face_crops_dir, crop_filename)

            # Save face crop to disk
            face_service = get_face_detection_service()
            if not face_service.save_face_crop(image_path, face, crop_path):
                logger.warning(f"Failed to save face crop: {crop_path}")
                return

            # Convert embedding to bytes for storage
            embedding_bytes = face['embedding'].astype(np.float32).tobytes()

            # Insert into database
            with db._connect() as conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT OR REPLACE INTO face_crops (
                        project_id, image_path, crop_path, embedding,
                        bbox_x, bbox_y, bbox_w, bbox_h, confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    self.project_id,
                    image_path,
                    crop_path,
                    embedding_bytes,
                    face['bbox_x'],
                    face['bbox_y'],
                    face['bbox_w'],
                    face['bbox_h'],
                    face['confidence']
                ))
                conn.commit()

        except Exception as e:
            logger.error(f"Failed to save face: {e}")


# Standalone script support
if __name__ == "__main__":
    import sys
    from PySide6.QtCore import QCoreApplication, QThreadPool

    if len(sys.argv) < 2:
        print("Usage: python face_detection_worker.py <project_id>")
        sys.exit(1)

    project_id = int(sys.argv[1])

    app = QCoreApplication(sys.argv)

    def on_progress(current, total, message):
        print(f"[{current}/{total}] {message}")

    def on_finished(success, failed, total_faces):
        print(f"\nFinished: {success} photos processed, {failed} failed, {total_faces} faces detected")
        app.quit()

    worker = FaceDetectionWorker(project_id=project_id)
    worker.signals.progress.connect(on_progress)
    worker.signals.finished.connect(on_finished)

    QThreadPool.globalInstance().start(worker)

    sys.exit(app.exec())
