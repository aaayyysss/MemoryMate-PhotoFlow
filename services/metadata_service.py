# services/metadata_service.py
# Version 01.00.00.00 dated 20251102
# Metadata extraction service - EXIF, dimensions, date parsing

import os
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from datetime import datetime
from dataclasses import dataclass

from PIL import Image, ExifTags
from logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class ImageMetadata:
    """
    Structured container for image metadata.

    All fields are optional since metadata extraction can fail or be incomplete.
    """
    # File information
    path: str
    file_size_bytes: Optional[int] = None
    file_size_kb: Optional[float] = None
    modified_time: Optional[str] = None  # ISO format: "2024-11-02 12:34:56"

    # Image dimensions
    width: Optional[int] = None
    height: Optional[int] = None

    # EXIF data
    date_taken: Optional[str] = None  # From EXIF DateTimeOriginal
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None
    iso: Optional[int] = None
    focal_length: Optional[float] = None
    aperture: Optional[float] = None
    shutter_speed: Optional[str] = None
    orientation: Optional[int] = None

    # Computed fields
    created_timestamp: Optional[int] = None  # Unix timestamp
    created_date: Optional[str] = None  # "YYYY-MM-DD"
    created_year: Optional[int] = None

    # Status
    success: bool = False
    error_message: Optional[str] = None


class MetadataService:
    """
    Service for extracting metadata from image files.

    Responsibilities:
    - Extract EXIF data (date, camera info, settings)
    - Get image dimensions
    - Parse and normalize dates from various formats
    - Handle file system metadata (size, modified time)
    - Error handling for corrupted or unsupported files

    Does NOT handle:
    - Face detection (separate service)
    - Thumbnail generation (ThumbnailService)
    - Database operations (use repositories)
    """

    # Supported EXIF date formats
    EXIF_DATE_FORMATS = [
        "%Y:%m:%d %H:%M:%S",     # EXIF standard: "2024:10:15 12:34:56"
        "%Y-%m-%d %H:%M:%S",     # ISO format
        "%Y/%m/%d %H:%M:%S",     # Slash format
        "%d.%m.%Y %H:%M:%S",     # European format
        "%Y-%m-%d",              # Date only
    ]

    def __init__(self,
                 extract_camera_info: bool = False,
                 extract_shooting_params: bool = False):
        """
        Initialize metadata service.

        Args:
            extract_camera_info: Extract camera make/model
            extract_shooting_params: Extract ISO, aperture, etc.
        """
        self.extract_camera_info = extract_camera_info
        self.extract_shooting_params = extract_shooting_params

    def extract_metadata(self, file_path: str) -> ImageMetadata:
        """
        Extract all available metadata from an image file.

        Args:
            file_path: Path to image file

        Returns:
            ImageMetadata with all extracted information
        """
        metadata = ImageMetadata(path=file_path)

        try:
            # Step 1: File system metadata
            self._extract_file_metadata(file_path, metadata)

            # Step 2: Open image and extract dimensions + EXIF
            with Image.open(file_path) as img:
                self._extract_dimensions(img, metadata)
                self._extract_exif(img, metadata)

            # Step 3: Compute derived fields
            self._compute_created_fields(metadata)

            metadata.success = True
            logger.debug(f"Successfully extracted metadata from {file_path}")

        except FileNotFoundError:
            metadata.error_message = "File not found"
            logger.debug(f"File not found: {file_path}")
        except Exception as e:
            metadata.error_message = str(e)
            logger.debug(f"Failed to extract metadata from {file_path}: {e}")

        return metadata

    def extract_basic_metadata(self, file_path: str) -> Tuple[Optional[int], Optional[int], Optional[str]]:
        """
        Fast extraction of just dimensions and date (for scanning).

        Args:
            file_path: Path to image file

        Returns:
            Tuple of (width, height, date_taken) or (None, None, None) on failure
        """
        try:
            with Image.open(file_path) as img:
                width, height = img.size
                date_taken = self._get_exif_date(img)
                return (int(width), int(height), date_taken)
        except Exception as e:
            logger.debug(f"Failed basic metadata extraction for {file_path}: {e}")
            return (None, None, None)

    def _extract_file_metadata(self, file_path: str, metadata: ImageMetadata):
        """Extract file system metadata (size, modified time)."""
        try:
            stat_result = os.stat(file_path)

            metadata.file_size_bytes = stat_result.st_size
            metadata.file_size_kb = stat_result.st_size / 1024.0

            # Format modified time as ISO string
            mtime = datetime.fromtimestamp(stat_result.st_mtime)
            metadata.modified_time = mtime.strftime("%Y-%m-%d %H:%M:%S")

        except Exception as e:
            logger.warning(f"Could not extract file metadata for {file_path}: {e}")

    def _extract_dimensions(self, img: Image.Image, metadata: ImageMetadata):
        """Extract image dimensions."""
        try:
            width, height = img.size
            metadata.width = int(width)
            metadata.height = int(height)
        except Exception as e:
            logger.warning(f"Could not extract dimensions: {e}")

    def _extract_exif(self, img: Image.Image, metadata: ImageMetadata):
        """Extract EXIF data from image."""
        try:
            exif = img.getexif()
            if not exif:
                return

            # Map numeric tags to names
            exif_dict = {
                ExifTags.TAGS.get(key, key): value
                for key, value in exif.items()
            }

            # Extract date (highest priority)
            metadata.date_taken = self._extract_exif_date(exif_dict)

            # Extract orientation
            if 'Orientation' in exif_dict:
                metadata.orientation = int(exif_dict['Orientation'])

            # Extract camera info (optional)
            if self.extract_camera_info:
                metadata.camera_make = exif_dict.get('Make')
                metadata.camera_model = exif_dict.get('Model')

            # Extract shooting parameters (optional)
            if self.extract_shooting_params:
                if 'ISOSpeedRatings' in exif_dict:
                    metadata.iso = int(exif_dict['ISOSpeedRatings'])

                if 'FocalLength' in exif_dict:
                    focal = exif_dict['FocalLength']
                    # Handle tuple format (numerator, denominator)
                    if isinstance(focal, tuple) and len(focal) == 2:
                        metadata.focal_length = float(focal[0]) / float(focal[1])
                    else:
                        metadata.focal_length = float(focal)

                if 'FNumber' in exif_dict:
                    fnum = exif_dict['FNumber']
                    if isinstance(fnum, tuple) and len(fnum) == 2:
                        metadata.aperture = float(fnum[0]) / float(fnum[1])
                    else:
                        metadata.aperture = float(fnum)

                if 'ExposureTime' in exif_dict:
                    exp = exif_dict['ExposureTime']
                    if isinstance(exp, tuple) and len(exp) == 2:
                        metadata.shutter_speed = f"{exp[0]}/{exp[1]}"
                    else:
                        metadata.shutter_speed = str(exp)

        except Exception as e:
            logger.debug(f"EXIF extraction failed: {e}")

    def _get_exif_date(self, img: Image.Image) -> Optional[str]:
        """
        Fast EXIF date extraction (used by extract_basic_metadata).

        Returns normalized date string or None.
        Works with all image formats including TIFF, JPEG, PNG, HEIC, etc.
        """
        try:
            # Use getexif() instead of deprecated _getexif()
            # getexif() works with all formats including TIFF
            exif = img.getexif()
            if not exif:
                return None

            # Try to get date from common tags
            for key, value in exif.items():
                tag = ExifTags.TAGS.get(key, key)
                if tag in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
                    return self._normalize_exif_date(str(value))

            return None

        except AttributeError:
            # Fallback for very old PIL/Pillow versions or exotic formats
            logger.debug(f"Image type {img.format} does not support EXIF extraction")
            return None
        except Exception as e:
            logger.debug(f"EXIF date extraction error: {e}")
            return None

    def _extract_exif_date(self, exif_dict: Dict[str, Any]) -> Optional[str]:
        """
        Extract date from EXIF dictionary with priority order.

        Priority:
        1. DateTimeOriginal (when photo was taken)
        2. DateTimeDigitized (when photo was scanned/digitized)
        3. DateTime (file modification in camera)

        Returns normalized date string or None.
        """
        for tag in ('DateTimeOriginal', 'DateTimeDigitized', 'DateTime'):
            if tag in exif_dict:
                date_str = str(exif_dict[tag])
                normalized = self._normalize_exif_date(date_str)
                if normalized:
                    return normalized

        return None

    def _normalize_exif_date(self, date_str: str) -> Optional[str]:
        """
        Normalize EXIF date to standard format.

        EXIF dates use colons: "2024:10:15 12:34:56"
        We normalize to:      "2024-10-15 12:34:56"

        Args:
            date_str: Raw EXIF date string

        Returns:
            Normalized date string or None if invalid
        """
        if not date_str:
            return None

        try:
            # Split date and time
            parts = date_str.split(" ", 1)
            if not parts:
                return None

            # Replace colons in date part with hyphens
            date_part = parts[0].replace(":", "-", 2)
            time_part = parts[1] if len(parts) > 1 else ""

            result = date_part
            if time_part:
                result = f"{date_part} {time_part}"

            # Validate by parsing
            self.parse_date(result)

            return result.strip()

        except Exception:
            return None

    def _compute_created_fields(self, metadata: ImageMetadata):
        """
        Compute derived fields from date_taken or modified_time.

        Sets:
        - created_timestamp (Unix timestamp)
        - created_date (YYYY-MM-DD)
        - created_year (integer)
        """
        # Try date_taken first, fallback to modified_time
        date_str = metadata.date_taken or metadata.modified_time

        if not date_str:
            return

        try:
            dt = self.parse_date(date_str)
            if dt:
                metadata.created_timestamp = int(dt.timestamp())
                metadata.created_date = dt.strftime("%Y-%m-%d")
                metadata.created_year = dt.year
        except Exception as e:
            logger.debug(f"Failed to compute created fields: {e}")

    def parse_date(self, date_str: str) -> Optional[datetime]:
        """
        Parse date string using multiple format attempts.

        Args:
            date_str: Date string in various possible formats

        Returns:
            datetime object or None if parsing fails
        """
        if not date_str:
            return None

        # Try known formats
        for fmt in self.EXIF_DATE_FORMATS:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue

        # Last resort: try ISO format parsing
        try:
            return datetime.fromisoformat(date_str)
        except Exception:
            return None

    def compute_created_fields_from_dates(self,
                                         date_taken: Optional[str],
                                         modified: Optional[str]) -> Tuple[Optional[int], Optional[str], Optional[int]]:
        """
        Legacy helper for backward compatibility with db_writer.

        Args:
            date_taken: EXIF date string
            modified: File modified time string

        Returns:
            Tuple of (timestamp, date_string, year)
        """
        date_str = date_taken or modified

        if not date_str:
            return (None, None, None)

        try:
            dt = self.parse_date(date_str)
            if dt:
                return (
                    int(dt.timestamp()),
                    dt.strftime("%Y-%m-%d"),
                    dt.year
                )
        except Exception:
            pass

        return (None, None, None)

    @staticmethod
    def is_image_file(file_path: str) -> bool:
        """
        Check if file is a supported image format.

        Args:
            file_path: Path to file

        Returns:
            True if supported image format
        """
        ext = Path(file_path).suffix.lower()
        return ext in {'.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff', '.heic', '.heif'}


# Example usage:
"""
from services import MetadataService

# Basic usage
service = MetadataService()
metadata = service.extract_metadata("/path/to/photo.jpg")

if metadata.success:
    print(f"Dimensions: {metadata.width}x{metadata.height}")
    print(f"Date taken: {metadata.date_taken}")
    print(f"Camera: {metadata.camera_make} {metadata.camera_model}")

# Fast extraction (for scanning)
width, height, date = service.extract_basic_metadata("/path/to/photo.jpg")

# With extended info
service = MetadataService(
    extract_camera_info=True,
    extract_shooting_params=True
)
metadata = service.extract_metadata("/path/to/photo.jpg")
print(f"ISO: {metadata.iso}, Aperture: f/{metadata.aperture}")
"""
