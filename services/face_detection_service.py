# face_detection_service.py
# Phase 5: Face Detection Service using InsightFace
# Detects faces in photos and generates 512-dimensional embeddings
# Uses InsightFace with buffalo_l model and OnnxRuntime backend
# ------------------------------------------------------

import os
import numpy as np
from typing import List, Tuple, Optional
from PIL import Image
import logging
import cv2

logger = logging.getLogger(__name__)

# Lazy import InsightFace (only load when needed)
_insightface_app = None
_providers_used = None


def _detect_available_providers():
    """
    Detect available ONNX Runtime providers (GPU/CPU).

    Returns automatic GPU detection based on proof of concept from OldPy/photo_sorter.py

    Returns:
        tuple: (providers_list, hardware_type)
            - providers_list: List of provider names for ONNXRuntime
            - hardware_type: 'GPU' or 'CPU'
    """
    try:
        import onnxruntime as ort
        available_providers = ort.get_available_providers()

        # Prefer GPU (CUDA), fallback to CPU
        if 'CUDAExecutionProvider' in available_providers:
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
            hardware_type = 'GPU'
            logger.info("🚀 CUDA (GPU) available - Using GPU acceleration for face detection")
        else:
            providers = ['CPUExecutionProvider']
            hardware_type = 'CPU'
            logger.info("💻 Using CPU for face detection (CUDA not available)")

        return providers, hardware_type

    except ImportError:
        logger.warning("ONNXRuntime not found, defaulting to CPU")
        return ['CPUExecutionProvider'], 'CPU'


def _get_insightface_app():
    """
    Lazy load InsightFace application with automatic GPU/CPU detection.

    Uses the proven pattern from OldPy/photo_sorter.py proof of concept:
    - Automatic provider detection (GPU if available, CPU fallback)
    - Model caching to avoid reloading
    - Proper error handling
    - Version compatibility (works with old and new InsightFace versions)
    - PyInstaller bundle support (looks for bundled models)
    """
    global _insightface_app, _providers_used
    if _insightface_app is None:
        try:
            import sys
            from insightface.app import FaceAnalysis
            import inspect

            # Detect best available providers
            providers, hardware_type = _detect_available_providers()
            _providers_used = providers

            # Determine model root path - PRIORITY ORDER:
            # 1. Custom path from settings (for offline use)
            # 2. PyInstaller bundle (sys._MEIPASS/insightface/)
            # 3. App directory (./ with models/buffalo_l/ inside)
            # 4. User home directory (~/.insightface/)
            app_models_dir = None

            # Check for custom path from settings first (offline use)
            try:
                from settings_manager_qt import SettingsManager
                settings = SettingsManager()
                custom_model_path = settings.get_setting('insightface_model_path', '')
                if custom_model_path:
                    # Check if this is the buffalo_l directory itself
                    if os.path.exists(custom_model_path):
                        # Try as buffalo_l directory
                        test_file = os.path.join(custom_model_path, 'det_10g.onnx')
                        if os.path.exists(test_file):
                            # This is the buffalo_l directory, parent is the root
                            app_models_dir = os.path.dirname(custom_model_path)
                            logger.info(f"🎯 Using custom model path: {custom_model_path}")
                            logger.info(f"   Model root set to: {app_models_dir}")
                        else:
                            # Try as parent directory with models/buffalo_l/
                            buffalo_path = os.path.join(custom_model_path, 'models', 'buffalo_l')
                            if os.path.exists(buffalo_path):
                                app_models_dir = custom_model_path
                                logger.info(f"🎯 Using custom model path: {buffalo_path}")
                                logger.info(f"   Model root set to: {app_models_dir}")
                            else:
                                logger.warning(f"⚠️ Custom model path configured but buffalo_l not found at {custom_model_path}")
                                logger.warning(f"   Expected: {custom_model_path}/det_10g.onnx or {buffalo_path}/det_10g.onnx")
            except Exception as e:
                logger.debug(f"Could not check custom model path from settings: {e}")

            # Check for PyInstaller bundle if no custom path
            if not app_models_dir and getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
                # Running in PyInstaller bundle
                bundle_dir = sys._MEIPASS
                pyinstaller_models = os.path.join(bundle_dir, 'insightface')
                # Verify the complete model path exists
                buffalo_path = os.path.join(pyinstaller_models, 'models', 'buffalo_l')
                if os.path.exists(buffalo_path):
                    app_models_dir = pyinstaller_models
                    logger.info(f"🎁 Running from PyInstaller bundle, using bundled models: {app_models_dir}")
                else:
                    logger.warning(f"⚠ PyInstaller bundle detected but buffalo_l not found at {buffalo_path}")

            # Check app directory if not in bundle
            # InsightFace expects: root/models/buffalo_l/
            # So if we have ./models/buffalo_l/, root should be ./
            if not app_models_dir:
                app_root = os.path.dirname(os.path.dirname(__file__))
                buffalo_path = os.path.join(app_root, 'models', 'buffalo_l')
                if os.path.exists(buffalo_path):
                    app_models_dir = app_root  # Point to app root, NOT models dir
                    logger.info(f"📁 Using local bundled models: {buffalo_path}")
                    logger.info(f"   Model root set to: {app_models_dir}")

            # Fallback to user home directory
            # InsightFace default: ~/.insightface/models/buffalo_l/
            if not app_models_dir:
                user_home = os.path.expanduser('~/.insightface')
                buffalo_path = os.path.join(user_home, 'models', 'buffalo_l')
                if os.path.exists(buffalo_path):
                    app_models_dir = user_home
                    logger.info(f"🏠 Using user home models: {buffalo_path}")
                else:
                    # Set anyway for auto-download
                    app_models_dir = user_home
                    logger.info(f"🏠 Will use user home for models (will download if needed): {user_home}")

            # Initialize InsightFace with buffalo_l model
            # Handle version compatibility: check which parameters are supported
            init_params = {'name': 'buffalo_l'}
            sig = inspect.signature(FaceAnalysis.__init__)

            # Check if 'providers' parameter is supported (newer versions)
            # ONNX Runtime 1.9+ requires explicit providers, but older InsightFace versions
            # don't support passing providers during __init__
            providers_supported = 'providers' in sig.parameters
            if providers_supported:
                # Newer version: pass providers during initialization
                init_params['providers'] = providers
                logger.info(f"✓ Using execution providers (via __init__): {providers}")
            else:
                # Older version: will pass providers during prepare() instead
                logger.info(f"✓ Using execution providers (via prepare()): {providers}")
                logger.debug("InsightFace version doesn't support 'providers' in __init__, will use in prepare()")

            # Check if 'allowed_modules' parameter is supported
            if 'allowed_modules' in sig.parameters:
                # Newer version: restrict to detection and recognition only
                init_params['allowed_modules'] = ['detection', 'recognition']
                logger.debug("Using InsightFace with allowed_modules (newer version)")
            else:
                # Older version: parameter not supported
                logger.debug("Using InsightFace without allowed_modules (older version)")

            # Check if 'root' parameter is supported (for custom model directory)
            if 'root' in sig.parameters:
                init_params['root'] = app_models_dir
                logger.info(f"✓ Setting model root to: {app_models_dir}")
                logger.info(f"   InsightFace will look for: {app_models_dir}/models/buffalo_l/")

            _insightface_app = FaceAnalysis(**init_params)

            # Prepare with context:
            # ctx_id=0 for GPU, ctx_id=-1 for CPU
            ctx_id = 0 if hardware_type == 'GPU' else -1

            # For older versions that don't support providers in __init__,
            # check if prepare() supports providers parameter
            prepare_params = {'ctx_id': ctx_id, 'det_size': (640, 640)}
            if not providers_supported:
                # Try to pass providers to prepare() for older versions
                try:
                    prepare_sig = inspect.signature(_insightface_app.prepare)
                    if 'providers' in prepare_sig.parameters:
                        prepare_params['providers'] = providers
                        logger.debug("Passing providers to prepare() for older InsightFace version")
                except Exception:
                    pass

            try:
                _insightface_app.prepare(**prepare_params)
                logger.info(f"✅ InsightFace (buffalo_l) loaded successfully with {hardware_type} acceleration")
            except Exception as prepare_error:
                # If prepare fails, might be missing models - try to download
                logger.warning(f"Model preparation failed: {prepare_error}")
                logger.info("Attempting to download models automatically...")

                # Try preparing again (this will trigger auto-download)
                _insightface_app.prepare(**prepare_params)
                logger.info(f"✅ Models downloaded and loaded with {hardware_type} acceleration")

        except ImportError as e:
            logger.error(f"❌ InsightFace library not installed: {e}")
            logger.error("Install with: pip install insightface onnxruntime")
            raise ImportError(
                "InsightFace library required for face detection. "
                "Install with: pip install insightface onnxruntime"
            ) from e
        except Exception as e:
            logger.error(f"❌ Failed to initialize InsightFace: {e}")
            logger.error(f"Error details: {type(e).__name__}: {str(e)}")
            raise
    return _insightface_app


def get_hardware_info():
    """
    Get information about the hardware being used for face detection.

    Returns:
        dict: Hardware information
            - 'type': 'GPU' or 'CPU'
            - 'providers': List of ONNXRuntime providers
            - 'cuda_available': bool
    """
    providers, hardware_type = _detect_available_providers()

    try:
        import onnxruntime as ort
        available = ort.get_available_providers()
        cuda_available = 'CUDAExecutionProvider' in available
    except:
        cuda_available = False

    return {
        'type': hardware_type,
        'providers': providers,
        'cuda_available': cuda_available
    }


class FaceDetectionService:
    """
    Service for detecting faces and generating embeddings using InsightFace.

    Uses InsightFace library which provides:
    - Face detection via RetinaFace (accurate, fast)
    - 512-dimensional face embeddings via ArcFace ResNet
    - High accuracy face recognition
    - OnnxRuntime backend for CPU/GPU inference

    Model: buffalo_l (large model, high accuracy)
    - Detection: RetinaFace
    - Recognition: ArcFace (ResNet100)
    - Embedding dimension: 512 (vs 128 for dlib)
    - Backend: OnnxRuntime

    Usage:
        service = FaceDetectionService()
        faces = service.detect_faces("photo.jpg")
        for face in faces:
            print(f"Found face at {face['bbox']} with confidence {face['confidence']}")
            print(f"Embedding shape: {face['embedding'].shape}")  # (512,)
    """

    @staticmethod
    def check_backend_availability() -> dict:
        """
        Check availability of face detection backends WITHOUT initializing them.

        This method checks if the required libraries can be imported
        without triggering expensive model downloads or initializations.

        Returns:
            Dictionary mapping backend name to availability status:
            {
                "insightface": bool,  # True if insightface and onnxruntime are available
                "face_recognition": False  # No longer supported
            }
        """
        availability = {
            "insightface": False,
            "face_recognition": False  # Deprecated, not supported
        }

        # Check InsightFace availability
        try:
            import insightface  # Just check if module exists
            import onnxruntime  # Check OnnxRuntime too
            availability["insightface"] = True
        except ImportError:
            pass

        return availability

    def __init__(self, model: str = "buffalo_l"):
        """
        Initialize face detection service.

        Args:
            model: Detection model to use (buffalo_l, buffalo_s, antelopev2)
                   - "buffalo_l" (recommended, high accuracy)
                   - "buffalo_s" (smaller, faster, lower accuracy)
                   - "antelopev2" (latest model)
        """
        self.model = model
        self.app = _get_insightface_app()
        logger.info(f"[FaceDetection] Initialized InsightFace with model={model}")

    def is_available(self) -> bool:
        """
        Check if the service is available and ready to use.

        Returns:
            True if InsightFace is initialized and ready, False otherwise
        """
        try:
            return self.app is not None
        except Exception:
            return False

    def detect_faces(self, image_path: str) -> List[dict]:
        """
        Detect all faces in an image and generate embeddings.

        Args:
            image_path: Path to image file

        Returns:
            List of face dictionaries with:
            {
                'bbox': [x1, y1, x2, y2],  # Face bounding box
                'bbox_x': int,  # X coordinate (top-left)
                'bbox_y': int,  # Y coordinate (top-left)
                'bbox_w': int,  # Width
                'bbox_h': int,  # Height
                'embedding': np.array (512,),  # Face embedding vector (ArcFace)
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

            # Load image using OpenCV (InsightFace expects BGR format)
            try:
                img = cv2.imread(image_path)
                if img is None:
                    logger.warning(f"Failed to load image {image_path}")
                    return []
            except Exception as e:
                logger.warning(f"Failed to load image {image_path}: {e}")
                return []

            # Detect faces and extract embeddings
            # Returns list of Face objects with bbox, embedding, det_score, etc.
            detected_faces = self.app.get(img)

            if not detected_faces:
                logger.debug(f"No faces found in {image_path}")
                return []

            # Convert InsightFace results to our format
            faces = []
            for face in detected_faces:
                # Get bounding box: [x1, y1, x2, y2]
                bbox = face.bbox.astype(int)
                x1, y1, x2, y2 = bbox

                # Calculate dimensions
                bbox_x = int(x1)
                bbox_y = int(y1)
                bbox_w = int(x2 - x1)
                bbox_h = int(y2 - y1)

                # Get confidence score from detection
                confidence = float(face.det_score)

                # Get embedding (512-dimensional ArcFace embedding)
                embedding = face.normed_embedding  # Already normalized to unit length

                faces.append({
                    'bbox': bbox.tolist(),
                    'bbox_x': bbox_x,
                    'bbox_y': bbox_y,
                    'bbox_w': bbox_w,
                    'bbox_h': bbox_h,
                    'embedding': embedding,
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

            # Resize to standard size for consistency (160x160 for better quality)
            face_img = face_img.resize((160, 160), Image.Resampling.LANCZOS)

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

def get_face_detection_service(model: str = "buffalo_l") -> FaceDetectionService:
    """Get or create singleton FaceDetectionService instance."""
    global _face_detection_service
    if _face_detection_service is None:
        _face_detection_service = FaceDetectionService(model=model)
    return _face_detection_service


def create_face_detection_service(config: dict) -> Optional[FaceDetectionService]:
    """
    Create a new FaceDetectionService instance from configuration.

    This function creates a fresh instance (not singleton) for testing purposes.

    Args:
        config: Configuration dictionary with keys:
            - backend: "insightface" (only supported backend)
            - insightface_model: Model name ("buffalo_l", "buffalo_s", "antelopev2")

    Returns:
        FaceDetectionService instance or None if backend not supported/available

    Example:
        config = {"backend": "insightface", "insightface_model": "buffalo_l"}
        service = create_face_detection_service(config)
    """
    backend = config.get("backend", "insightface")

    if backend != "insightface":
        logger.warning(f"Unsupported backend: {backend}. Only 'insightface' is supported.")
        return None

    # Check if InsightFace is available
    availability = FaceDetectionService.check_backend_availability()
    if not availability.get("insightface", False):
        logger.error("InsightFace backend not available. Install with: pip install insightface onnxruntime")
        return None

    # Get model name from config
    model = config.get("insightface_model", "buffalo_l")

    try:
        return FaceDetectionService(model=model)
    except Exception as e:
        logger.error(f"Failed to create FaceDetectionService: {e}")
        return None
