"""
Face Detection Configuration
Manages settings for face detection, recognition, and clustering.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional


class FaceDetectionConfig:
    """Configuration for face detection and recognition."""

    DEFAULT_CONFIG = {
        # Backend selection
        "backend": "insightface",  # Options: "insightface" (recommended, uses buffalo_l + OnnxRuntime)
        "enabled": False,  # Face detection disabled by default

        # Detection parameters
        "detection_model": "hog",  # face_recognition: "hog" (fast) or "cnn" (accurate)
        "upsample_times": 1,  # Number of times to upsample image for detection
        "min_face_size": 20,  # Minimum face size in pixels
        "confidence_threshold": 0.6,  # Minimum confidence for face detection

        # InsightFace specific
        "insightface_model": "buffalo_l",  # Model: buffalo_s, buffalo_l, antelopev2
        "insightface_det_size": (640, 640),  # Detection size for InsightFace

        # Clustering parameters
        "clustering_enabled": True,
        "clustering_eps": 0.42,  # DBSCAN epsilon (distance threshold)
        "clustering_min_samples": 3,  # Minimum faces to form a cluster
        "auto_cluster_after_scan": True,

        # Performance
        "batch_size": 50,  # Number of images to process before committing to DB
        "max_workers": 4,  # Max parallel face detection workers
        "skip_detected": True,  # Skip images that already have faces detected

        # Storage
        "save_face_crops": True,
        "crop_size": 160,  # Face crop size in pixels
        "crop_quality": 95,  # JPEG quality for face crops
        "face_cache_dir": ".face_cache",  # Directory for face crops

        # UI preferences
        "show_face_boxes": True,
        "show_confidence": False,
        "default_view": "grid",  # "grid" or "list"
        "thumbnail_size": 128,

        # Privacy
        "anonymize_untagged": False,
        "require_confirmation": True,  # Confirm before starting face detection
    }

    def __init__(self, config_path: Optional[str] = None):
        """Initialize configuration.

        Args:
            config_path: Path to configuration file. If None, uses default location.
        """
        if config_path is None:
            config_dir = Path.home() / ".memorymate"
            config_dir.mkdir(exist_ok=True)
            config_path = config_dir / "face_detection_config.json"

        self.config_path = Path(config_path)
        self.config = self.DEFAULT_CONFIG.copy()
        self.load()

    def load(self) -> None:
        """Load configuration from file."""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    loaded = json.load(f)
                    self.config.update(loaded)
                print(f"[FaceConfig] Loaded from {self.config_path}")
            except Exception as e:
                print(f"[FaceConfig] Failed to load config: {e}")
                # Keep defaults

    def save(self) -> None:
        """Save configuration to file."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, 'w') as f:
                json.dump(self.config, f, indent=2)
            print(f"[FaceConfig] Saved to {self.config_path}")
        except Exception as e:
            print(f"[FaceConfig] Failed to save config: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value.

        Args:
            key: Configuration key
            default: Default value if key not found

        Returns:
            Configuration value
        """
        return self.config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set configuration value.

        Args:
            key: Configuration key
            value: Value to set
        """
        self.config[key] = value
        self.save()

    def is_enabled(self) -> bool:
        """Check if face detection is enabled."""
        return self.config.get("enabled", False)

    def get_backend(self) -> str:
        """Get selected backend."""
        return self.config.get("backend", "insightface")

    def get_clustering_params(self) -> Dict[str, Any]:
        """Get clustering parameters."""
        return {
            "eps": self.config.get("clustering_eps", 0.42),
            "min_samples": self.config.get("clustering_min_samples", 3),
        }

    def get_face_cache_dir(self) -> Path:
        """Get face cache directory path."""
        cache_dir = Path(self.config.get("face_cache_dir", ".face_cache"))
        cache_dir.mkdir(exist_ok=True)
        return cache_dir

    def reset_to_defaults(self) -> None:
        """Reset configuration to defaults."""
        self.config = self.DEFAULT_CONFIG.copy()
        self.save()


# Global configuration instance
_config: Optional[FaceDetectionConfig] = None


def get_face_config() -> FaceDetectionConfig:
    """Get global face detection configuration instance."""
    global _config
    if _config is None:
        _config = FaceDetectionConfig()
    return _config


def reload_config() -> None:
    """Reload configuration from disk."""
    global _config
    _config = FaceDetectionConfig()
