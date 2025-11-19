"""
EXIF Parser - Extract metadata from photos and videos

Parses EXIF data to get capture dates, camera info, GPS, etc.
Used for auto-organizing imported files by date.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional, Dict
import os


class EXIFParser:
    """Parse EXIF metadata from photos and videos"""

    def __init__(self):
        """Initialize EXIF parser"""
        pass

    def get_capture_date(self, file_path: str) -> datetime:
        """
        Get the best available capture date for a file.

        Priority:
        1. EXIF DateTimeOriginal (when photo was taken)
        2. EXIF DateTimeDigitized (when photo was scanned/imported)
        3. EXIF DateTime (file modification in camera)
        4. File modified time
        5. File created time

        Args:
            file_path: Path to image or video file

        Returns:
            datetime object (never None, always returns something)
        """
        file_path_obj = Path(file_path)

        # Try EXIF for images
        if self._is_image(file_path):
            exif_date = self._get_exif_date(file_path)
            if exif_date:
                return exif_date

        # Try video metadata
        elif self._is_video(file_path):
            video_date = self._get_video_date(file_path)
            if video_date:
                return video_date

        # Fallback to file system dates
        return self._get_file_date(file_path_obj)

    def _is_image(self, file_path: str) -> bool:
        """Check if file is an image"""
        ext = Path(file_path).suffix.lower()
        return ext in ['.jpg', '.jpeg', '.png', '.gif', '.heic', '.heif', '.bmp', '.tiff', '.webp']

    def _is_video(self, file_path: str) -> bool:
        """Check if file is a video"""
        ext = Path(file_path).suffix.lower()
        return ext in ['.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v', '.3gp', '.flv']

    def _get_exif_date(self, file_path: str) -> Optional[datetime]:
        """
        Extract EXIF date from image file.

        Returns:
            datetime object if EXIF date found, None otherwise
        """
        try:
            from PIL import Image
            from PIL.ExifTags import TAGS

            print(f"[EXIFParser] Parsing EXIF from: {Path(file_path).name}")

            with Image.open(file_path) as img:
                # Get EXIF data
                exif_data = img._getexif()

                if not exif_data:
                    print(f"[EXIFParser]   No EXIF data found")
                    return None

                # Look for date tags in priority order
                date_tags = [
                    36867,  # DateTimeOriginal (when photo was taken)
                    36868,  # DateTimeDigitized (when photo was scanned)
                    306,    # DateTime (file modification in camera)
                ]

                for tag_id in date_tags:
                    if tag_id in exif_data:
                        date_str = exif_data[tag_id]

                        # Parse EXIF date format: "YYYY:MM:DD HH:MM:SS"
                        try:
                            dt = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                            tag_name = TAGS.get(tag_id, tag_id)
                            print(f"[EXIFParser]   ✓ Found {tag_name}: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
                            return dt
                        except ValueError:
                            continue

                print(f"[EXIFParser]   No valid date tags found in EXIF")
                return None

        except ImportError:
            print(f"[EXIFParser]   ✗ PIL not available, cannot parse EXIF")
            return None
        except Exception as e:
            print(f"[EXIFParser]   ✗ Error parsing EXIF: {e}")
            return None

    def _get_video_date(self, file_path: str) -> Optional[datetime]:
        """
        Extract creation date from video file metadata.

        Returns:
            datetime object if video date found, None otherwise
        """
        try:
            # Try using ffprobe (part of FFmpeg) to get video metadata
            import subprocess
            import json

            print(f"[EXIFParser] Parsing video metadata from: {Path(file_path).name}")

            # Run ffprobe to get video metadata
            cmd = [
                'ffprobe',
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                '-show_streams',
                file_path
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

            if result.returncode == 0:
                metadata = json.loads(result.stdout)

                # Try to get creation_time from format tags
                if 'format' in metadata and 'tags' in metadata['format']:
                    tags = metadata['format']['tags']

                    # Different video formats use different tag names
                    for tag_name in ['creation_time', 'date', 'DATE', 'creation_date']:
                        if tag_name in tags:
                            date_str = tags[tag_name]

                            # Parse ISO format: "2024-10-15T14:30:00.000000Z"
                            try:
                                # Remove microseconds and timezone
                                date_str = date_str.split('.')[0].replace('Z', '')
                                dt = datetime.fromisoformat(date_str)
                                print(f"[EXIFParser]   ✓ Found {tag_name}: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
                                return dt
                            except ValueError:
                                continue

                print(f"[EXIFParser]   No creation_time found in video metadata")
                return None

            else:
                print(f"[EXIFParser]   ✗ ffprobe failed (not installed or error)")
                return None

        except FileNotFoundError:
            # ffprobe not installed, fall back to file dates
            print(f"[EXIFParser]   ✗ ffprobe not found (FFmpeg not installed)")
            return None
        except subprocess.TimeoutExpired:
            print(f"[EXIFParser]   ✗ ffprobe timeout")
            return None
        except Exception as e:
            print(f"[EXIFParser]   ✗ Error parsing video metadata: {e}")
            return None

    def _get_file_date(self, file_path: Path) -> datetime:
        """
        Get file system date (modified or created time).

        Returns:
            datetime object (never None)
        """
        try:
            # Try modified time first (more accurate for photos copied from camera)
            mtime = file_path.stat().st_mtime
            dt = datetime.fromtimestamp(mtime)
            print(f"[EXIFParser]   Using file modified time: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
            return dt

        except Exception as e:
            # Last resort: use current time
            print(f"[EXIFParser]   ✗ Error getting file date, using current time: {e}")
            return datetime.now()

    def parse_image_full(self, file_path: str) -> Dict:
        """
        Extract full EXIF metadata from image (for future use).

        Returns dict with:
            - datetime_original: When photo was taken
            - camera_make: Camera manufacturer
            - camera_model: Camera model
            - width: Image width
            - height: Image height
            - orientation: EXIF orientation
            - gps_latitude: GPS latitude (if available)
            - gps_longitude: GPS longitude (if available)
        """
        metadata = {
            'datetime_original': None,
            'camera_make': None,
            'camera_model': None,
            'width': None,
            'height': None,
            'orientation': None,
            'gps_latitude': None,
            'gps_longitude': None,
        }

        try:
            from PIL import Image
            from PIL.ExifTags import TAGS, GPSTAGS

            with Image.open(file_path) as img:
                # Get basic image info
                metadata['width'] = img.width
                metadata['height'] = img.height

                # Get EXIF data
                exif_data = img._getexif()
                if not exif_data:
                    return metadata

                # Extract common EXIF tags
                for tag_id, value in exif_data.items():
                    tag_name = TAGS.get(tag_id, tag_id)

                    if tag_name == 'DateTimeOriginal':
                        try:
                            metadata['datetime_original'] = datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
                        except:
                            pass
                    elif tag_name == 'Make':
                        metadata['camera_make'] = value
                    elif tag_name == 'Model':
                        metadata['camera_model'] = value
                    elif tag_name == 'Orientation':
                        metadata['orientation'] = value
                    elif tag_name == 'GPSInfo':
                        # Parse GPS data
                        gps_data = {}
                        for gps_tag_id in value:
                            gps_tag_name = GPSTAGS.get(gps_tag_id, gps_tag_id)
                            gps_data[gps_tag_name] = value[gps_tag_id]

                        # Convert GPS to decimal degrees
                        if 'GPSLatitude' in gps_data and 'GPSLongitude' in gps_data:
                            metadata['gps_latitude'] = self._convert_gps_to_decimal(
                                gps_data['GPSLatitude'],
                                gps_data.get('GPSLatitudeRef', 'N')
                            )
                            metadata['gps_longitude'] = self._convert_gps_to_decimal(
                                gps_data['GPSLongitude'],
                                gps_data.get('GPSLongitudeRef', 'E')
                            )

        except Exception as e:
            print(f"[EXIFParser] Error parsing full EXIF: {e}")

        return metadata

    def _convert_gps_to_decimal(self, gps_coord, ref):
        """Convert GPS coordinates from degrees/minutes/seconds to decimal"""
        try:
            degrees = float(gps_coord[0])
            minutes = float(gps_coord[1])
            seconds = float(gps_coord[2])

            decimal = degrees + (minutes / 60.0) + (seconds / 3600.0)

            if ref in ['S', 'W']:
                decimal = -decimal

            return decimal
        except:
            return None
