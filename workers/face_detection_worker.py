# face_detection_worker.py
# Version 01.00.00.01 (Phase 7.0 – People / Face Detection)
# InsightFace-based face detection and embedding generation
# Based on successful proof of concept from OldPy/photo_sorter.py
# ------------------------------------------------------

import os
import sys
import sqlite3
import numpy as np
import cv2
from pathlib import Path
from typing import List, Tuple, Optional, Dict
from reference_db import ReferenceDB
from logging_config import get_logger

logger = get_logger(__name__)

# Global model cache - load once per process (from proof of concept)
_MODEL_CACHE = {
    "app": None,              # cached FaceAnalysis instance
    "model_dir": None,        # model directory used
    "providers": None,        # ORT providers used
}


def get_buffalo_model(model_dir: Optional[str] = None):
    """
    Get or create cached InsightFace FaceAnalysis instance.
    Uses automatic provider detection (GPU if available, otherwise CPU).

    Based on proof of concept implementation that successfully handled:
    - Model caching to avoid reloading
    - Automatic CPU/GPU detection
    - Offline mode support

    Args:
        model_dir: Optional directory for InsightFace models (defaults to ~/.insightface/models)

    Returns:
        FaceAnalysis instance or None if models not available
    """
    try:
        from insightface.app import FaceAnalysis
    except ImportError:
        logger.error("InsightFace not installed. Install with: pip install insightface")
        return None

    # Use default model directory if not specified
    if model_dir is None:
        model_dir = os.path.expanduser("~/.insightface/models")

    # Return cached instance if same configuration
    if (_MODEL_CACHE["app"] is not None and
        _MODEL_CACHE["model_dir"] == model_dir):
        logger.debug("Using cached FaceAnalysis instance")
        return _MODEL_CACHE["app"]

    # Detect available providers (GPU if available, otherwise CPU)
    try:
        import onnxruntime as ort
        available_providers = ort.get_available_providers()

        # Prefer GPU, fallback to CPU
        if 'CUDAExecutionProvider' in available_providers:
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
            logger.info("Using CUDA (GPU) for face detection")
        else:
            providers = ['CPUExecutionProvider']
            logger.info("Using CPU for face detection")

    except ImportError:
        providers = ['CPUExecutionProvider']
        logger.warning("ONNXRuntime not found, defaulting to CPU")

    # Check if buffalo_l model exists
    buffalo_path = os.path.join(model_dir, "buffalo_l")
    if not os.path.exists(buffalo_path):
        logger.error(f"Buffalo_l model not found at {buffalo_path}")
        logger.error("Download models with: python -m insightface.model_zoo")
        return None

    try:
        # Initialize FaceAnalysis
        # NOTE: In newer InsightFace versions, providers are set via allowed_modules
        # The proof of concept used a different parameter structure
        app = FaceAnalysis(
            name='buffalo_l',
            root=model_dir,
            allowed_modules=['detection', 'recognition']
        )

        # Prepare with context (this is where providers are actually used)
        app.prepare(ctx_id=0 if 'CUDAExecutionProvider' in providers else -1,
                   det_size=(640, 640))

        # Cache the instance
        _MODEL_CACHE["app"] = app
        _MODEL_CACHE["model_dir"] = model_dir
        _MODEL_CACHE["providers"] = providers

        logger.info(f"Loaded InsightFace buffalo_l model from {model_dir}")
        return app

    except Exception as e:
        logger.error(f"Failed to initialize FaceAnalysis: {e}")
        return None


def detect_faces_in_image(image_path: str, app=None) -> List[Tuple[np.ndarray, np.ndarray, float]]:
    """
    Detect faces in an image and return face crops with embeddings.

    Args:
        image_path: Path to image file
        app: Optional FaceAnalysis instance (will create if not provided)

    Returns:
        List of tuples (face_crop, embedding, confidence)
        Returns empty list if no faces found or error
    """
    if app is None:
        app = get_buffalo_model()
        if app is None:
            return []

    try:
        # Read image
        img = cv2.imread(image_path)
        if img is None:
            logger.warning(f"Failed to read image: {image_path}")
            return []

        # Detect faces
        faces = app.get(img)

        if not faces:
            logger.debug(f"No faces detected in {image_path}")
            return []

        results = []
        for face in faces:
            # Get bounding box
            bbox = face.bbox.astype(int)
            x1, y1, x2, y2 = bbox

            # Extract face crop
            face_crop = img[y1:y2, x1:x2]

            # Get embedding
            embedding = face.embedding

            # Get detection confidence
            confidence = float(face.det_score) if hasattr(face, 'det_score') else 1.0

            results.append((face_crop, embedding, confidence))

        logger.info(f"Detected {len(results)} face(s) in {image_path}")
        return results

    except Exception as e:
        logger.error(f"Error detecting faces in {image_path}: {e}")
        return []


def process_photos_for_project(project_id: int,
                               photo_paths: List[str],
                               face_crops_dir: str,
                               progress_callback=None):
    """
    Process a list of photos for face detection and save results to database.

    Args:
        project_id: Project ID
        photo_paths: List of photo file paths
        face_crops_dir: Directory to save face crop thumbnails
        progress_callback: Optional callback function(current, total)

    Returns:
        Dict with statistics: {
            'photos_processed': int,
            'faces_detected': int,
            'photos_with_faces': int,
            'errors': int
        }
    """
    # Initialize model once
    app = get_buffalo_model()
    if app is None:
        logger.error("Cannot initialize face detection model")
        return {
            'photos_processed': 0,
            'faces_detected': 0,
            'photos_with_faces': 0,
            'errors': len(photo_paths)
        }

    # Create face crops directory
    os.makedirs(face_crops_dir, exist_ok=True)

    # Initialize database
    db = ReferenceDB()
    conn = db._connect()
    cur = conn.cursor()

    stats = {
        'photos_processed': 0,
        'faces_detected': 0,
        'photos_with_faces': 0,
        'errors': 0
    }

    total = len(photo_paths)

    for idx, photo_path in enumerate(photo_paths):
        try:
            # Detect faces
            faces = detect_faces_in_image(photo_path, app)

            if faces:
                stats['photos_with_faces'] += 1

                # Save each face
                for face_idx, (face_crop, embedding, confidence) in enumerate(faces):
                    # Generate unique crop filename
                    photo_name = Path(photo_path).stem
                    crop_filename = f"{photo_name}_face{face_idx}_{confidence:.3f}.jpg"
                    crop_path = os.path.join(face_crops_dir, crop_filename)

                    # Save face crop
                    cv2.imwrite(crop_path, face_crop)

                    # Convert embedding to blob
                    embedding_blob = embedding.astype(np.float32).tobytes()

                    # Insert into database
                    # Use a temporary branch key until clustering is done
                    branch_key = "unclustered"

                    cur.execute("""
                        INSERT OR IGNORE INTO face_crops
                        (project_id, branch_key, image_path, crop_path, embedding, is_representative)
                        VALUES (?, ?, ?, ?, ?, 0)
                    """, (project_id, branch_key, photo_path, crop_path, embedding_blob))

                    stats['faces_detected'] += 1

            stats['photos_processed'] += 1

            # Progress callback
            if progress_callback:
                progress_callback(idx + 1, total)

        except Exception as e:
            logger.error(f"Error processing {photo_path}: {e}")
            stats['errors'] += 1

    # Commit all changes
    conn.commit()
    conn.close()

    logger.info(f"Face detection complete: {stats}")
    return stats


if __name__ == "__main__":
    """
    Command-line interface for face detection worker.

    Usage: python face_detection_worker.py <project_id> <photo_list_file>

    Where photo_list_file contains one photo path per line.
    """
    if len(sys.argv) < 3:
        print("Usage: python face_detection_worker.py <project_id> <photo_list_file>")
        sys.exit(1)

    project_id = int(sys.argv[1])
    photo_list_file = sys.argv[2]

    # Read photo paths
    with open(photo_list_file, 'r') as f:
        photo_paths = [line.strip() for line in f if line.strip()]

    # Setup face crops directory
    db = ReferenceDB()
    conn = db._connect()
    cur = conn.cursor()
    cur.execute("SELECT folder FROM projects WHERE id=?", (project_id,))
    result = cur.fetchone()
    conn.close()

    if not result:
        print(f"Project {project_id} not found")
        sys.exit(1)

    project_folder = result[0]
    face_crops_dir = os.path.join(project_folder, ".memorymate", "face_crops")

    # Process photos
    def progress(current, total):
        pct = (current / total) * 100
        print(f"Progress: {current}/{total} ({pct:.1f}%)")

    stats = process_photos_for_project(
        project_id=project_id,
        photo_paths=photo_paths,
        face_crops_dir=face_crops_dir,
        progress_callback=progress
    )

    print(f"\nFace Detection Complete!")
    print(f"Photos processed: {stats['photos_processed']}")
    print(f"Photos with faces: {stats['photos_with_faces']}")
    print(f"Total faces detected: {stats['faces_detected']}")
    print(f"Errors: {stats['errors']}")
