# face_detection_service.py
# Phase 5: Face Detection Service
# Detects faces in photos and generates 128-dimensional embeddings
# Uses face_recognition library (dlib-based)
# ------------------------------------------------------

import os
import numpy as np
from typing import List, Tuple, Optional
from PIL import Image
import logging

logger = logging.getLogger(__name__)

# Lazy import face_recognition (only load when needed)
_face_recognition = None

def _get_face_recognition():
    """Lazy load face_recognition library."""
    global _face_recognition
    if _face_recognition is None:
        try:
            import face_recognition
            _face_recognition = face_recognition
            logger.info("✅ face_recognition library loaded successfully")
        except ImportError as e:
            logger.error(f"❌ face_recognition library not installed: {e}")
            logger.error("Install with: pip install face_recognition")
            raise ImportError(
                "face_recognition library required for face detection. "
                "Install with: pip install face_recognition"
            ) from e
    return _face_recognition


class FaceDetectionService:
    """
    Service for detecting faces and generating embeddings.

    Uses face_recognition library which provides:
    - Face detection via HOG or CNN
    - 128-dimensional face embeddings via dlib's ResNet model
    - High accuracy face recognition

    Usage:
        service = FaceDetectionService()
        faces = service.detect_faces("photo.jpg")
        for face in faces:
            print(f"Found face at {face['bbox']} with confidence {face['confidence']}")
            print(f"Embedding shape: {face['embedding'].shape}")
    """

    def __init__(self, model: str = "hog"):
        """
        Initialize face detection service.

        Args:
            model: Detection model to use
                   - "hog" (faster, CPU-friendly, good for most cases)
                   - "cnn" (slower, GPU-optimized, more accurate)
        """
        self.model = model
        self.fr = _get_face_recognition()
        logger.info(f"[FaceDetection] Initialized with model={model}")

    def detect_faces(self, image_path: str) -> List[dict]:
        """
        Detect all faces in an image and generate embeddings.

        Args:
            image_path: Path to image file

        Returns:
            List of face dictionaries with:
            {
                'bbox': (top, right, bottom, left),  # Face bounding box
                'bbox_x': int,  # X coordinate
                'bbox_y': int,  # Y coordinate
                'bbox_w': int,  # Width
                'bbox_h': int,  # Height
                'embedding': np.array (128,),  # Face embedding vector
                'confidence': float  # Detection confidence (0-1)
            }

        Example:
            faces = service.detect_faces("photo.jpg")
            print(f"Found {len(faces)} faces")
        """
        try:
            # Check if file exists
            if not os.path.exists(image_path):
                logger.warning(f"Image not found: {image_path}")
                return []

            # Load image using face_recognition (handles various formats)
            try:
                image = self.fr.load_image_file(image_path)
            except Exception as e:
                logger.warning(f"Failed to load image {image_path}: {e}")
                return []

            # Detect face locations
            # Returns: [(top, right, bottom, left), ...]
            face_locations = self.fr.face_locations(image, model=self.model)

            if not face_locations:
                logger.debug(f"No faces found in {image_path}")
                return []

            # Generate embeddings for all detected faces
            # Returns: [array(128,), array(128,), ...]
            face_encodings = self.fr.face_encodings(image, face_locations)

            # Combine locations and embeddings
            faces = []
            for location, encoding in zip(face_locations, face_encodings):
                top, right, bottom, left = location

                # Calculate bounding box dimensions
                bbox_x = left
                bbox_y = top
                bbox_w = right - left
                bbox_h = bottom - top

                # Estimate confidence (face_recognition doesn't provide confidence scores)
                # We use a heuristic based on face size (larger faces = more confident)
                face_area = bbox_w * bbox_h
                image_area = image.shape[0] * image.shape[1]
                confidence = min(0.95, 0.7 + (face_area / image_area) * 0.25)

                faces.append({
                    'bbox': location,
                    'bbox_x': bbox_x,
                    'bbox_y': bbox_y,
                    'bbox_w': bbox_w,
                    'bbox_h': bbox_h,
                    'embedding': encoding,
                    'confidence': confidence
                })

            logger.info(f"[FaceDetection] Found {len(faces)} faces in {os.path.basename(image_path)}")
            return faces

        except Exception as e:
            logger.error(f"Error detecting faces in {image_path}: {e}")
            return []

    def save_face_crop(self, image_path: str, face: dict, output_path: str) -> bool:
        """
        Save a cropped face image to disk.

        Args:
            image_path: Original image path
            face: Face dictionary with 'bbox' key
            output_path: Path to save cropped face

        Returns:
            True if successful, False otherwise
        """
        try:
            # Load original image
            img = Image.open(image_path)

            # Extract bounding box
            bbox_x = face['bbox_x']
            bbox_y = face['bbox_y']
            bbox_w = face['bbox_w']
            bbox_h = face['bbox_h']

            # Add padding (10% on each side)
            padding = int(min(bbox_w, bbox_h) * 0.1)
            x1 = max(0, bbox_x - padding)
            y1 = max(0, bbox_y - padding)
            x2 = min(img.width, bbox_x + bbox_w + padding)
            y2 = min(img.height, bbox_y + bbox_h + padding)

            # Crop face
            face_img = img.crop((x1, y1, x2, y2))

            # Resize to standard size for consistency (128x128)
            face_img = face_img.resize((128, 128), Image.Resampling.LANCZOS)

            # Ensure directory exists
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            # Save
            face_img.save(output_path, quality=95)
            logger.debug(f"Saved face crop to {output_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to save face crop: {e}")
            return False

    def batch_detect_faces(self, image_paths: List[str],
                          max_workers: int = 4) -> dict:
        """
        Detect faces in multiple images (parallel processing).

        Args:
            image_paths: List of image paths
            max_workers: Number of parallel workers

        Returns:
            Dictionary mapping image_path -> list of faces

        Example:
            results = service.batch_detect_faces(["img1.jpg", "img2.jpg"])
            for path, faces in results.items():
                print(f"{path}: {len(faces)} faces")
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = {}
        total = len(image_paths)

        logger.info(f"[FaceDetection] Processing {total} images with {max_workers} workers")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all detection tasks
            futures = {executor.submit(self.detect_faces, path): path
                      for path in image_paths}

            # Collect results as they complete
            processed = 0
            for future in as_completed(futures):
                path = futures[future]
                try:
                    faces = future.result()
                    results[path] = faces
                    processed += 1

                    if processed % 10 == 0:
                        logger.info(f"[FaceDetection] Progress: {processed}/{total} images")

                except Exception as e:
                    logger.error(f"Error processing {path}: {e}")
                    results[path] = []

        logger.info(f"[FaceDetection] Batch complete: {processed}/{total} images processed")
        return results


# Singleton instance
_face_detection_service = None

def get_face_detection_service(model: str = "hog") -> FaceDetectionService:
    """Get or create singleton FaceDetectionService instance."""
    global _face_detection_service
    if _face_detection_service is None:
        _face_detection_service = FaceDetectionService(model=model)
    return _face_detection_service
