# services/thumbnail_service.py
# Version 01.01.00.00 dated 20251105
# Unified thumbnail caching service with L1 (memory) + L2 (database) cache
# Enhanced TIFF support and Qt message suppression for unsupported formats

import os
import io
import time
from collections import OrderedDict
from typing import Optional, Dict, Any
from pathlib import Path

from PIL import Image
from PySide6.QtGui import QPixmap, QImage, QImageReader
from PySide6.QtCore import Qt, qInstallMessageHandler, QtMsgType

from logging_config import get_logger
from thumb_cache_db import ThumbCacheDB, get_cache

logger = get_logger(__name__)

# Global flag to track if message handler is installed
_qt_message_handler_installed = False

# Formats that should always use PIL (not Qt) due to compatibility issues
PIL_PREFERRED_FORMATS = {
    '.tif', '.tiff',  # TIFF with various compressions (JPEG, LZW, etc.)
    '.tga',           # TGA files
    '.psd',           # Photoshop files
    '.ico',           # Icons with multiple sizes
    '.bmp',           # Some BMP variants
}

def _qt_message_handler(msg_type, context, message):
    """
    Custom Qt message handler to suppress known TIFF compression warnings.

    This suppresses repetitive Qt warnings about unsupported TIFF compression
    methods (like JPEG compression in TIFF), since we handle these with PIL fallback.
    """
    # Suppress TIFF-related warnings that we handle with PIL
    if 'qt.imageformats.tiff' in message.lower():
        if any(x in message for x in [
            'JPEG compression support is not configured',
            'Sorry, requested compression method is not configured',
            'LZW compression support is not configured',
            'Deflate compression support is not configured'
        ]):
            # Silently ignore these - we handle them with PIL
            return

    # For other Qt messages, log them appropriately
    if msg_type == QtMsgType.QtDebugMsg:
        logger.debug(f"Qt: {message}")
    elif msg_type == QtMsgType.QtWarningMsg:
        # Don't spam warnings for image format issues
        if 'imageformat' not in message.lower():
            logger.warning(f"Qt: {message}")
    elif msg_type == QtMsgType.QtCriticalMsg:
        logger.error(f"Qt Critical: {message}")
    elif msg_type == QtMsgType.QtFatalMsg:
        logger.critical(f"Qt Fatal: {message}")

def install_qt_message_handler():
    """
    Install custom Qt message handler to suppress TIFF warnings.

    Call this once at application startup to prevent spam from
    unsupported TIFF compression methods.
    """
    global _qt_message_handler_installed
    if not _qt_message_handler_installed:
        qInstallMessageHandler(_qt_message_handler)
        _qt_message_handler_installed = True
        logger.info("Installed Qt message handler to suppress TIFF warnings")


class LRUCache:
    """
    Least Recently Used cache with size limit.

    Maintains an OrderedDict to track access order and evicts
    the least recently used items when capacity is exceeded.
    """

    def __init__(self, capacity: int = 500):
        """
        Initialize LRU cache.

        Args:
            capacity: Maximum number of entries before eviction
        """
        self.capacity = capacity
        self.cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self.hits = 0
        self.misses = 0
        logger.info(f"LRUCache initialized with capacity={capacity}")

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Get value for key, moving it to end (most recent).

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found
        """
        if key not in self.cache:
            self.misses += 1
            return None

        # Move to end (mark as recently used)
        self.cache.move_to_end(key)
        self.hits += 1
        return self.cache[key]

    def put(self, key: str, value: Dict[str, Any]):
        """
        Put value in cache, evicting oldest if at capacity.

        Args:
            key: Cache key
            value: Value to cache
        """
        if key in self.cache:
            # Update existing entry and move to end
            self.cache.move_to_end(key)
        else:
            # Add new entry
            if len(self.cache) >= self.capacity:
                # Evict oldest (first) entry
                evicted_key, _ = self.cache.popitem(last=False)
                logger.debug(f"LRU evicted: {evicted_key}")

        self.cache[key] = value

    def invalidate(self, key: str) -> bool:
        """
        Remove entry from cache.

        Args:
            key: Cache key to remove

        Returns:
            True if entry was removed
        """
        if key in self.cache:
            del self.cache[key]
            return True
        return False

    def clear(self):
        """Clear all entries from cache."""
        self.cache.clear()
        self.hits = 0
        self.misses = 0
        logger.info("LRUCache cleared")

    def size(self) -> int:
        """Return current number of entries."""
        return len(self.cache)

    def hit_rate(self) -> float:
        """Calculate cache hit rate."""
        total = self.hits + self.misses
        if total == 0:
            return 0.0
        return self.hits / total


class ThumbnailService:
    """
    Unified thumbnail caching service with two-tier caching:

    - L1 Cache: LRU-limited memory cache (fast, limited size)
    - L2 Cache: Database cache (persistent, larger, auto-purged)

    This replaces the previous fragmented caching with:
    - Unbounded memory dict in app_services._thumbnail_cache
    - Disk files in .thumb_cache/ directory
    - Database BLOBs in thumbnails_cache.db

    Benefits:
    - Unified invalidation
    - Memory usage control via LRU eviction
    - Eliminates duplicate storage (disk + database)
    - Unified statistics and monitoring
    """

    def __init__(self,
                 l1_capacity: int = 500,
                 db_cache: Optional[ThumbCacheDB] = None,
                 default_timeout: float = 5.0):
        """
        Initialize thumbnail service.

        Args:
            l1_capacity: Maximum entries in memory cache
            db_cache: Optional database cache instance (uses global if None)
            default_timeout: Default decode timeout in seconds
        """
        # Install Qt message handler to suppress TIFF warnings
        install_qt_message_handler()

        self.l1_cache = LRUCache(capacity=l1_capacity)
        self.l2_cache = db_cache or get_cache()
        self.default_timeout = default_timeout
        logger.info(f"ThumbnailService initialized (L1 capacity={l1_capacity}, timeout={default_timeout}s)")

    def _normalize_path(self, path: str) -> str:
        """
        Normalize path for consistent cache keys.

        Args:
            path: File path

        Returns:
            Normalized path
        """
        try:
            return os.path.normcase(os.path.abspath(os.path.normpath(str(path).strip())))
        except Exception:
            return str(path).strip().lower()

    def _get_mtime(self, path: str) -> Optional[float]:
        """
        Get file modification time safely.

        Args:
            path: File path

        Returns:
            Modification time or None if file doesn't exist
        """
        try:
            return os.path.getmtime(path)
        except Exception:
            return None

    def _is_cache_valid(self, cached_entry: Dict[str, Any], current_mtime: float) -> bool:
        """
        Check if cached entry is still valid.

        Args:
            cached_entry: Cache entry with 'mtime' field
            current_mtime: Current file modification time

        Returns:
            True if cache entry is still valid
        """
        if not cached_entry or current_mtime is None:
            return False

        cached_mtime = cached_entry.get("mtime", 0)
        # Allow small float comparison tolerance
        return abs(cached_mtime - current_mtime) < 0.1

    def get_thumbnail(self,
                     path: str,
                     height: int,
                     timeout: Optional[float] = None) -> QPixmap:
        """
        Get thumbnail from cache or generate it.

        Cache lookup order:
        1. L1 (memory) cache
        2. L2 (database) cache
        3. Generate from image file

        Args:
            path: Image file path
            height: Target thumbnail height in pixels
            timeout: Optional decode timeout (uses default if None)

        Returns:
            QPixmap thumbnail (may be null on error)
        """
        if not path:
            return QPixmap()

        norm_path = self._normalize_path(path)
        current_mtime = self._get_mtime(path)

        if current_mtime is None:
            logger.warning(f"File not found: {path}")
            return QPixmap()

        timeout = timeout or self.default_timeout

        # 1. Check L1 (memory) cache
        l1_entry = self.l1_cache.get(norm_path)
        if l1_entry and self._is_cache_valid(l1_entry, current_mtime):
            logger.debug(f"L1 hit: {path}")
            return l1_entry["pixmap"]

        # 2. Check L2 (database) cache
        l2_pixmap = self.l2_cache.get_cached_thumbnail(path, current_mtime, height * 2)
        if l2_pixmap and not l2_pixmap.isNull():
            logger.debug(f"L2 hit: {path}")
            # Store in L1 for faster subsequent access
            self.l1_cache.put(norm_path, {"pixmap": l2_pixmap, "mtime": current_mtime})
            return l2_pixmap

        # 3. Generate thumbnail
        logger.debug(f"Cache miss, generating: {path}")
        pixmap = self._generate_thumbnail(path, height, timeout)

        if pixmap and not pixmap.isNull():
            # Store in both caches
            self.l1_cache.put(norm_path, {"pixmap": pixmap, "mtime": current_mtime})
            self.l2_cache.store_thumbnail(path, current_mtime, pixmap)

        return pixmap

    def _generate_thumbnail(self, path: str, height: int, timeout: float) -> QPixmap:
        """
        Generate thumbnail from image file.

        Handles:
        - PIL-preferred formats (TIFF, TGA, PSD, etc.) - always use PIL
        - Qt-native formats (JPEG, PNG, WebP) - use Qt for speed
        - EXIF auto-rotation
        - Decode timeout protection
        - Automatic fallback to PIL on Qt failures

        Args:
            path: Image file path
            height: Target height in pixels
            timeout: Maximum decode time in seconds

        Returns:
            Generated QPixmap thumbnail
        """
        ext = os.path.splitext(path)[1].lower()

        # Use PIL directly for formats known to have Qt compatibility issues
        if ext in PIL_PREFERRED_FORMATS:
            logger.debug(f"Using PIL for {ext} format: {path}")
            return self._generate_thumbnail_pil(path, height, timeout)

        # Try Qt's fast QImageReader for common formats
        try:
            start = time.time()
            reader = QImageReader(path)
            reader.setAutoTransform(True)  # Handle EXIF rotation

            # Check timeout
            if time.time() - start > timeout:
                logger.warning(f"Decode timeout: {path}")
                return QPixmap()

            img = reader.read()
            if img.isNull():
                # Qt couldn't read it, fallback to PIL
                logger.debug(f"Qt returned null image for {path}, trying PIL")
                return self._generate_thumbnail_pil(path, height, timeout)

            if height > 0:
                img = img.scaledToHeight(height, Qt.SmoothTransformation)

            return QPixmap.fromImage(img)

        except Exception as e:
            logger.debug(f"QImageReader failed for {path}: {e}, trying PIL fallback")
            return self._generate_thumbnail_pil(path, height, timeout)

    def _generate_thumbnail_pil(self, path: str, height: int, timeout: float) -> QPixmap:
        """
        Generate thumbnail using PIL (fallback for TIFF and unsupported formats).

        Handles:
        - All TIFF compression types (JPEG, LZW, Deflate, PackBits, None)
        - CMYK and other color modes (converts to RGB)
        - Multi-page images (uses first page)
        - Transparency (preserves alpha channel)

        Args:
            path: Image file path
            height: Target height in pixels
            timeout: Maximum decode time in seconds

        Returns:
            Generated QPixmap thumbnail
        """
        try:
            start = time.time()

            with Image.open(path) as img:
                # Verify image loaded successfully
                if img is None:
                    logger.warning(f"PIL returned None for: {path}")
                    return QPixmap()

                # Try to load image data (forces actual file read)
                try:
                    img.load()
                except Exception as e:
                    logger.warning(f"PIL failed to load image data for {path}: {e}")
                    return QPixmap()

                # For multi-page images (TIFF, ICO), try to use first page
                try:
                    if hasattr(img, 'n_frames') and img.n_frames > 1:
                        img.seek(0)  # Go to first frame
                except Exception as e:
                    # Some images report n_frames but can't seek - just use current frame
                    logger.debug(f"Could not seek to first frame for {path}: {e}")

                # Calculate target dimensions
                if not hasattr(img, 'height') or not hasattr(img, 'width'):
                    logger.warning(f"Image missing dimensions: {path}")
                    return QPixmap()

                if img.height == 0 or img.width == 0:
                    logger.warning(f"Invalid image dimensions: {path}")
                    return QPixmap()

                ratio = height / float(img.height)
                target_w = int(img.width * ratio)

                # Check timeout
                if time.time() - start > timeout:
                    logger.warning(f"PIL decode timeout: {path}")
                    return QPixmap()

                # Handle various color modes
                try:
                    if img.mode == 'CMYK':
                        # Convert CMYK to RGB
                        img = img.convert('RGB')
                    elif img.mode in ('P', 'PA'):
                        # Convert palette mode with/without alpha
                        img = img.convert('RGBA' if 'transparency' in img.info else 'RGB')
                    elif img.mode in ('L', 'LA'):
                        # Convert grayscale to RGB
                        img = img.convert('RGBA' if img.mode == 'LA' else 'RGB')
                    elif img.mode not in ("RGB", "RGBA"):
                        # Convert any other mode to RGB
                        img = img.convert("RGB")
                except Exception as e:
                    logger.warning(f"Color mode conversion failed for {path}: {e}")
                    # Try to continue with original mode
                    pass

                # Resize
                try:
                    img.thumbnail((target_w, height), Image.Resampling.LANCZOS)
                except Exception as e:
                    logger.warning(f"Thumbnail resize failed for {path}: {e}")
                    return QPixmap()

                # Convert to QPixmap
                try:
                    buf = io.BytesIO()
                    # Use PNG to preserve alpha channel if present
                    save_format = "PNG" if img.mode == "RGBA" else "PNG"
                    img.save(buf, format=save_format, optimize=False)
                    qimg = QImage.fromData(buf.getvalue())

                    if qimg.isNull():
                        logger.warning(f"Failed to convert PIL image to QImage: {path}")
                        return QPixmap()

                    return QPixmap.fromImage(qimg)
                except Exception as e:
                    logger.warning(f"Failed to convert PIL image to QPixmap for {path}: {e}")
                    return QPixmap()

        except OSError as e:
            # Handle PIL-specific errors (corrupt files, unsupported formats, etc.)
            logger.warning(f"PIL could not open image {path}: {e}")
            return QPixmap()
        except Exception as e:
            logger.error(f"PIL thumbnail generation failed for {path}: {e}", exc_info=True)
            return QPixmap()

    def invalidate(self, path: str):
        """
        Invalidate cached thumbnail for a file.

        Removes from both L1 (memory) and L2 (database) caches.
        Call this when a file is modified or deleted.

        Args:
            path: File path to invalidate
        """
        norm_path = self._normalize_path(path)

        # Remove from L1
        l1_removed = self.l1_cache.invalidate(norm_path)

        # Remove from L2
        self.l2_cache.invalidate(path)

        logger.info(f"Invalidated thumbnail: {path} (L1={'yes' if l1_removed else 'no'})")

    def clear_all(self):
        """
        Clear all caches (L1 and L2).

        WARNING: This removes all cached thumbnails.
        """
        self.l1_cache.clear()
        # L2 cache doesn't have a clear method, but we can purge stale entries
        self.l2_cache.purge_stale(max_age_days=0)  # Purge everything
        logger.info("All thumbnail caches cleared")

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get unified cache statistics.

        Returns:
            Dictionary with L1 and L2 cache stats
        """
        l1_stats = {
            "size": self.l1_cache.size(),
            "capacity": self.l1_cache.capacity,
            "hits": self.l1_cache.hits,
            "misses": self.l1_cache.misses,
            "hit_rate": round(self.l1_cache.hit_rate() * 100, 2),
        }

        l2_stats = self.l2_cache.get_stats()
        l2_metrics = self.l2_cache.get_metrics()

        return {
            "l1_memory_cache": l1_stats,
            "l2_database_cache": {
                **l2_stats,
                **l2_metrics
            }
        }


# Global singleton instance
_thumbnail_service: Optional[ThumbnailService] = None


def get_thumbnail_service(l1_capacity: int = 500) -> ThumbnailService:
    """
    Get global ThumbnailService singleton.

    Args:
        l1_capacity: L1 cache capacity (only used on first call)

    Returns:
        Global ThumbnailService instance
    """
    global _thumbnail_service

    if _thumbnail_service is None:
        _thumbnail_service = ThumbnailService(l1_capacity=l1_capacity)
        logger.info("Global ThumbnailService created")

    return _thumbnail_service
