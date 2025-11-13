"""
Face Detection Worker
Detects faces in images, extracts embeddings, and saves face crops to database.
"""

import os
import sys
import time
import sqlite3
import numpy as np
from pathlib import Path
from typing import List, Dict, Any, Optional
from PIL import Image
import traceback

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reference_db import ReferenceDB
from services.face_detection_service import create_face_detection_service
from config.face_detection_config import get_face_config
from workers.progress_writer import write_status


class FaceDetectionWorker:
    """Worker for detecting faces in a photo collection."""

    def __init__(self, project_id: int):
        """Initialize face detection worker.

        Args:
            project_id: Project ID to process
        """
        self.project_id = project_id
        self.db = ReferenceDB()
        self.config = get_face_config()

        # Initialize face detection service
        self.face_service = create_face_detection_service()
        if self.face_service is None or not self.face_service.is_available():
            raise RuntimeError("Face detection service not available")

        # Get configuration
        self.min_face_size = self.config.get("min_face_size", 20)
        self.confidence_threshold = self.config.get("confidence_threshold", 0.6)
        self.batch_size = self.config.get("batch_size", 50)
        self.skip_detected = self.config.get("skip_detected", True)
        self.save_crops = self.config.get("save_face_crops", True)
        self.crop_size = self.config.get("crop_size", 160)
        self.crop_quality = self.config.get("crop_quality", 95)

        # Setup face cache directory
        self.face_cache_dir = self.config.get_face_cache_dir()
        self.face_cache_dir.mkdir(parents=True, exist_ok=True)

        print(f"[FaceWorker] Initialized for project {project_id}")
        print(f"[FaceWorker] Backend: {self.config.get_backend()}")
        print(f"[FaceWorker] Face cache: {self.face_cache_dir}")

    def get_images_to_process(self) -> List[Dict[str, Any]]:
        """Get list of images that need face detection.

        Returns:
            List of image dictionaries with 'id' and 'path'
        """
        with self.db._connect() as conn:
            cur = conn.cursor()

            if self.skip_detected:
                # Only get images without face detection
                cur.execute("""
                    SELECT pm.id, pm.path
                    FROM photo_metadata pm
                    WHERE pm.project_id = ?
                      AND NOT EXISTS (
                          SELECT 1 FROM face_crops fc
                          WHERE fc.image_path = pm.path
                            AND fc.project_id = pm.project_id
                      )
                    ORDER BY pm.id
                """, (self.project_id,))
            else:
                # Get all images in project
                cur.execute("""
                    SELECT id, path
                    FROM photo_metadata
                    WHERE project_id = ?
                    ORDER BY id
                """, (self.project_id,))

            rows = cur.fetchall()
            return [{"id": r[0], "path": r[1]} for r in rows]

    def save_face_crop(
        self,
        image_path: str,
        bbox: tuple,
        face_id: int
    ) -> Optional[str]:
        """Save a face crop to disk.

        Args:
            image_path: Path to original image
            bbox: Face bounding box (top, right, bottom, left)
            face_id: Unique face ID for filename

        Returns:
            Path to saved crop or None if failed
        """
        if not self.save_crops:
            return None

        try:
            # Load original image
            image = Image.open(image_path)

            # Extract bounding box
            top, right, bottom, left = bbox

            # Add padding (10%)
            width = right - left
            height = bottom - top
            padding_x = int(width * 0.1)
            padding_y = int(height * 0.1)

            # Apply padding with bounds checking
            img_width, img_height = image.size
            left = max(0, left - padding_x)
            top = max(0, top - padding_y)
            right = min(img_width, right + padding_x)
            bottom = min(img_height, bottom + padding_y)

            # Crop face
            face_crop = image.crop((left, top, right, bottom))

            # Resize to standard size
            face_crop = face_crop.resize((self.crop_size, self.crop_size), Image.Resampling.LANCZOS)

            # Generate filename
            crop_filename = f"face_{self.project_id}_{face_id:08d}.jpg"
            crop_path = self.face_cache_dir / crop_filename

            # Save crop
            face_crop.save(crop_path, "JPEG", quality=self.crop_quality)

            return str(crop_path)

        except Exception as e:
            print(f"[FaceWorker] Failed to save crop: {e}")
            return None

    def process_image(self, image_path: str) -> List[Dict[str, Any]]:
        """Process a single image for face detection.

        Args:
            image_path: Path to image

        Returns:
            List of detected faces with embeddings
        """
        try:
            # Detect faces
            faces = self.face_service.detect_faces(
                image_path,
                min_size=self.min_face_size,
                confidence_threshold=self.confidence_threshold
            )

            return faces

        except Exception as e:
            print(f"[FaceWorker] Error processing {image_path}: {e}")
            traceback.print_exc()
            return []

    def save_faces_to_db(
        self,
        image_path: str,
        faces: List[Dict[str, Any]],
        conn: sqlite3.Connection
    ) -> int:
        """Save detected faces to database.

        Args:
            image_path: Path to original image
            faces: List of detected faces
            conn: Database connection

        Returns:
            Number of faces saved
        """
        if not faces:
            return 0

        cur = conn.cursor()
        saved_count = 0

        for face in faces:
            try:
                # Generate unique face ID
                cur.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM face_crops")
                face_id = cur.fetchone()[0]

                # Save face crop
                crop_path = self.save_face_crop(image_path, face["bbox"], face_id)

                # Convert embedding to bytes
                embedding_bytes = face["embedding"].tobytes()

                # Insert into face_crops table
                cur.execute("""
                    INSERT INTO face_crops (
                        project_id,
                        branch_key,
                        image_path,
                        crop_path,
                        embedding,
                        confidence,
                        bbox_top,
                        bbox_right,
                        bbox_bottom,
                        bbox_left,
                        is_representative
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """, (
                    self.project_id,
                    "unassigned",  # Will be assigned during clustering
                    image_path,
                    crop_path or "",
                    embedding_bytes,
                    face["confidence"],
                    face["bbox"][0],  # top
                    face["bbox"][1],  # right
                    face["bbox"][2],  # bottom
                    face["bbox"][3],  # left
                ))

                saved_count += 1

            except Exception as e:
                print(f"[FaceWorker] Failed to save face from {image_path}: {e}")
                traceback.print_exc()

        return saved_count

    def run(self, status_path: Optional[str] = None) -> Dict[str, Any]:
        """Run face detection on all images in project.

        Args:
            status_path: Optional path to status file for progress reporting

        Returns:
            Dictionary with statistics
        """
        start_time = time.time()

        # Setup status reporting
        if status_path is None:
            status_dir = Path("status")
            status_dir.mkdir(exist_ok=True)
            status_path = status_dir / "face_detection_status.json"

        log_path = str(status_path).replace(".json", ".log")

        def log(message: str):
            """Log message to file and console."""
            print(f"[FaceWorker] {message}")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] {message}\n")

        # Get images to process
        log("Getting images to process...")
        images = self.get_images_to_process()
        total_images = len(images)

        if total_images == 0:
            log("No images to process")
            return {"total_images": 0, "total_faces": 0, "elapsed_time": 0}

        log(f"Found {total_images} images to process")
        write_status(status_path, "initializing", 0, total_images)

        # Process images in batches
        total_faces = 0
        processed_images = 0
        images_with_faces = 0

        conn = self.db._connect()

        try:
            for i in range(0, total_images, self.batch_size):
                batch = images[i:i + self.batch_size]
                batch_faces = 0

                for img in batch:
                    image_path = img["path"]

                    # Check if file exists
                    if not os.path.exists(image_path):
                        processed_images += 1
                        continue

                    # Detect faces
                    faces = self.process_image(image_path)

                    # Save to database
                    if faces:
                        saved = self.save_faces_to_db(image_path, faces, conn)
                        batch_faces += saved
                        if saved > 0:
                            images_with_faces += 1

                    processed_images += 1

                    # Update progress
                    if processed_images % 10 == 0:
                        pct = (processed_images / total_images) * 100
                        log(f"Progress: {processed_images}/{total_images} ({pct:.1f}%) - {total_faces} faces")
                        write_status(status_path, "detecting", processed_images, total_images)

                # Commit batch
                conn.commit()
                total_faces += batch_faces
                log(f"Batch {i//self.batch_size + 1}: {batch_faces} faces detected")

        finally:
            conn.close()

        # Final statistics
        elapsed = time.time() - start_time
        log("=" * 50)
        log(f"Face detection completed!")
        log(f"  Total images: {total_images}")
        log(f"  Images processed: {processed_images}")
        log(f"  Images with faces: {images_with_faces}")
        log(f"  Total faces detected: {total_faces}")
        log(f"  Elapsed time: {elapsed:.1f}s")
        log(f"  Average: {elapsed/processed_images:.2f}s per image" if processed_images > 0 else "N/A")
        log("=" * 50)

        write_status(status_path, "done", total_images, total_images)

        return {
            "total_images": total_images,
            "processed_images": processed_images,
            "images_with_faces": images_with_faces,
            "total_faces": total_faces,
            "elapsed_time": elapsed,
        }


def main():
    """Main entry point for face detection worker."""
    if len(sys.argv) < 2:
        print("Usage: python face_detection_worker.py <project_id>")
        sys.exit(1)

    project_id = int(sys.argv[1])

    # Check configuration
    config = get_face_config()
    if not config.is_enabled():
        print("[FaceWorker] Face detection is disabled in configuration")
        print("[FaceWorker] Enable it in settings to use this feature")
        sys.exit(1)

    # Check backend availability
    from services.face_detection_service import FaceDetectionService
    availability = FaceDetectionService.check_backend_availability()
    backend = config.get_backend()

    if not availability.get(backend, False):
        print(f"[FaceWorker] Backend '{backend}' is not available")
        print(f"[FaceWorker] Available backends: {[k for k, v in availability.items() if v]}")
        if backend == "face_recognition":
            print("[FaceWorker] Install with: pip install face-recognition")
        elif backend == "insightface":
            print("[FaceWorker] Install with: pip install insightface onnxruntime")
        sys.exit(1)

    # Run worker
    try:
        worker = FaceDetectionWorker(project_id)
        stats = worker.run()

        print("\n✅ Face detection completed successfully!")
        print(f"   Detected {stats['total_faces']} faces in {stats['images_with_faces']} images")

    except Exception as e:
        print(f"\n❌ Face detection failed: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
