"""
Face Detection Service - Abstraction Layer
Supports multiple backends: face_recognition and InsightFace
"""

import numpy as np
from typing import List, Tuple, Optional, Dict, Any
from abc import ABC, abstractmethod
from pathlib import Path
import traceback


class FaceDetectionBackend(ABC):
    """Abstract base class for face detection backends."""

    @abstractmethod
    def detect_faces(self, image_path: str) -> List[Dict[str, Any]]:
        """Detect faces in an image.

        Args:
            image_path: Path to image file

        Returns:
            List of face dictionaries with keys:
                - bbox: (top, right, bottom, left) coordinates
                - embedding: Face embedding vector
                - confidence: Detection confidence (0-1)
                - landmarks: Optional facial landmarks
        """
        pass

    @abstractmethod
    def get_embedding_size(self) -> int:
        """Get size of embedding vector."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if backend is available and initialized."""
        pass


class FaceRecognitionBackend(FaceDetectionBackend):
    """Backend using face_recognition library (dlib)."""

    def __init__(self, model: str = "hog", upsample_times: int = 1):
        """Initialize face_recognition backend.

        Args:
            model: Detection model - "hog" (fast, CPU) or "cnn" (accurate, GPU)
            upsample_times: How many times to upsample image for detection
        """
        self.model = model
        self.upsample_times = upsample_times
        self._available = False

        try:
            import face_recognition
            self.face_recognition = face_recognition
            self._available = True
            print(f"[FaceRecognition] Backend initialized (model={model})")
        except ImportError:
            print("[FaceRecognition] Library not installed. Run: pip install face-recognition")

    def detect_faces(self, image_path: str) -> List[Dict[str, Any]]:
        """Detect faces using face_recognition library."""
        if not self._available:
            return []

        try:
            # Load image
            image = self.face_recognition.load_image_file(image_path)

            # Detect face locations
            face_locations = self.face_recognition.face_locations(
                image,
                number_of_times_to_upsample=self.upsample_times,
                model=self.model
            )

            if not face_locations:
                return []

            # Get face encodings (128-D embeddings)
            face_encodings = self.face_recognition.face_encodings(
                image,
                known_face_locations=face_locations
            )

            # Get landmarks
            face_landmarks = self.face_recognition.face_landmarks(
                image,
                face_locations=face_locations
            )

            # Format results
            faces = []
            for i, (location, encoding) in enumerate(zip(face_locations, face_encodings)):
                top, right, bottom, left = location

                face_dict = {
                    "bbox": (top, right, bottom, left),
                    "embedding": encoding.astype(np.float32),
                    "confidence": 1.0,  # face_recognition doesn't provide confidence
                    "landmarks": face_landmarks[i] if i < len(face_landmarks) else None,
                    "area": (bottom - top) * (right - left)
                }
                faces.append(face_dict)

            return faces

        except Exception as e:
            print(f"[FaceRecognition] Detection failed for {image_path}: {e}")
            traceback.print_exc()
            return []

    def get_embedding_size(self) -> int:
        """face_recognition uses 128-D embeddings."""
        return 128

    def is_available(self) -> bool:
        """Check if face_recognition is available."""
        return self._available


class InsightFaceBackend(FaceDetectionBackend):
    """Backend using InsightFace library."""

    def __init__(self, model_name: str = "buffalo_l", det_size: Tuple[int, int] = (640, 640)):
        """Initialize InsightFace backend.

        Args:
            model_name: Model name - "buffalo_s", "buffalo_l", "antelopev2"
            det_size: Detection input size (width, height)
        """
        self.model_name = model_name
        self.det_size = det_size
        self._available = False
        self.app = None

        try:
            import insightface
            from insightface.app import FaceAnalysis

            # Initialize face analysis app
            self.app = FaceAnalysis(
                name=model_name,
                providers=['CPUExecutionProvider']  # Use CPU by default
            )
            self.app.prepare(ctx_id=0, det_size=det_size)
            self._available = True
            print(f"[InsightFace] Backend initialized (model={model_name})")

        except ImportError:
            print("[InsightFace] Library not installed. Run: pip install insightface onnxruntime")
        except Exception as e:
            print(f"[InsightFace] Initialization failed: {e}")

    def detect_faces(self, image_path: str) -> List[Dict[str, Any]]:
        """Detect faces using InsightFace."""
        if not self._available or self.app is None:
            return []

        try:
            import cv2

            # Load image
            image = cv2.imread(str(image_path))
            if image is None:
                return []

            # Detect faces
            faces_data = self.app.get(image)

            if not faces_data:
                return []

            # Format results
            faces = []
            for face in faces_data:
                bbox = face.bbox.astype(int)  # [x1, y1, x2, y2]
                # Convert to (top, right, bottom, left) format
                top, right, bottom, left = bbox[1], bbox[2], bbox[3], bbox[0]

                face_dict = {
                    "bbox": (top, right, bottom, left),
                    "embedding": face.embedding.astype(np.float32),  # 512-D embedding
                    "confidence": float(face.det_score),
                    "landmarks": face.landmark_2d_106 if hasattr(face, 'landmark_2d_106') else None,
                    "area": (bottom - top) * (right - left),
                    "age": face.age if hasattr(face, 'age') else None,
                    "gender": face.gender if hasattr(face, 'gender') else None,
                }
                faces.append(face_dict)

            return faces

        except Exception as e:
            print(f"[InsightFace] Detection failed for {image_path}: {e}")
            traceback.print_exc()
            return []

    def get_embedding_size(self) -> int:
        """InsightFace uses 512-D embeddings."""
        return 512

    def is_available(self) -> bool:
        """Check if InsightFace is available."""
        return self._available


class FaceDetectionService:
    """Main service for face detection with backend selection."""

    def __init__(self, backend_name: str = "face_recognition", **kwargs):
        """Initialize face detection service.

        Args:
            backend_name: Backend to use - "face_recognition" or "insightface"
            **kwargs: Backend-specific parameters
        """
        self.backend_name = backend_name
        self.backend: Optional[FaceDetectionBackend] = None

        # Initialize backend
        if backend_name == "face_recognition":
            model = kwargs.get("model", "hog")
            upsample = kwargs.get("upsample_times", 1)
            self.backend = FaceRecognitionBackend(model=model, upsample_times=upsample)

        elif backend_name == "insightface":
            model = kwargs.get("model_name", "buffalo_l")
            det_size = kwargs.get("det_size", (640, 640))
            self.backend = InsightFaceBackend(model_name=model, det_size=det_size)

        else:
            raise ValueError(f"Unknown backend: {backend_name}")

        if not self.backend.is_available():
            raise RuntimeError(f"Backend '{backend_name}' is not available")

        print(f"[FaceDetection] Service initialized with backend: {backend_name}")

    def detect_faces(
        self,
        image_path: str,
        min_size: int = 20,
        confidence_threshold: float = 0.6
    ) -> List[Dict[str, Any]]:
        """Detect faces in an image.

        Args:
            image_path: Path to image file
            min_size: Minimum face size in pixels
            confidence_threshold: Minimum detection confidence

        Returns:
            List of face dictionaries
        """
        if self.backend is None:
            return []

        faces = self.backend.detect_faces(image_path)

        # Filter by size and confidence
        filtered_faces = []
        for face in faces:
            # Check minimum size
            if face["area"] < min_size * min_size:
                continue

            # Check confidence threshold
            if face["confidence"] < confidence_threshold:
                continue

            filtered_faces.append(face)

        return filtered_faces

    def get_embedding_size(self) -> int:
        """Get embedding size for current backend."""
        if self.backend is None:
            return 0
        return self.backend.get_embedding_size()

    def is_available(self) -> bool:
        """Check if service is available."""
        return self.backend is not None and self.backend.is_available()

    @staticmethod
    def check_backend_availability() -> Dict[str, bool]:
        """Check which backends are available.

        Returns:
            Dictionary with backend availability status
        """
        availability = {}

        # Check face_recognition
        try:
            import face_recognition
            availability["face_recognition"] = True
        except ImportError:
            availability["face_recognition"] = False

        # Check insightface
        try:
            import insightface
            availability["insightface"] = True
        except ImportError:
            availability["insightface"] = False

        return availability


def create_face_detection_service(config: Optional[Dict[str, Any]] = None) -> Optional[FaceDetectionService]:
    """Factory function to create face detection service from config.

    Args:
        config: Configuration dictionary. If None, uses default config.

    Returns:
        FaceDetectionService instance or None if backend unavailable
    """
    if config is None:
        from config.face_detection_config import get_face_config
        cfg = get_face_config()
        backend = cfg.get_backend()
        config = cfg.config
    else:
        backend = config.get("backend", "face_recognition")

    try:
        if backend == "face_recognition":
            return FaceDetectionService(
                backend_name="face_recognition",
                model=config.get("detection_model", "hog"),
                upsample_times=config.get("upsample_times", 1)
            )
        elif backend == "insightface":
            return FaceDetectionService(
                backend_name="insightface",
                model_name=config.get("insightface_model", "buffalo_l"),
                det_size=tuple(config.get("insightface_det_size", (640, 640)))
            )
        else:
            print(f"[FaceDetection] Unknown backend: {backend}")
            return None

    except Exception as e:
        print(f"[FaceDetection] Failed to create service: {e}")
        return None
