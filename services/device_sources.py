"""
Mobile Device Detection Service

Detects mounted mobile devices (Android, iPhone) by scanning for DCIM folders
and provides device information for direct access browsing.

Usage:
    scanner = DeviceScanner()
    devices = scanner.scan_devices()
    for device in devices:
        print(f"{device.label}: {device.folders}")
"""

import os
import platform
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass


@dataclass
class DeviceFolder:
    """Represents a folder on a mobile device"""
    name: str           # Display name (e.g., "Camera", "Screenshots")
    path: str           # Full filesystem path
    photo_count: int    # Estimated photo/video count (0 if not counted yet)


@dataclass
class MobileDevice:
    """Represents a detected mobile device"""
    label: str                  # Human-readable label (e.g., "Samsung Galaxy S22")
    root_path: str              # Mount point or root path
    device_type: str            # "android" or "ios"
    folders: List[DeviceFolder] # DCIM folders and other media folders


class DeviceScanner:
    """Cross-platform mobile device scanner"""

    # Common DCIM folder patterns for Android
    ANDROID_PATTERNS = [
        "DCIM/Camera",
        "DCIM",
        "DCIM/.thumbnails",
        "Internal Storage/DCIM",
        "Internal Storage/DCIM/Camera",
        "Pictures",
        "Pictures/Screenshots",
        "Pictures/WhatsApp",
        "Camera",
        "Photos",
        "Download",
        "100MEDIA",
        "PRIVATE/AVCHD",
    ]

    # Common folder patterns for iOS
    IOS_PATTERNS = [
        "DCIM",
        "DCIM/100APPLE",
        "DCIM/101APPLE",
        "DCIM/102APPLE",
        "DCIM/103APPLE",
        "Photos",
    ]

    # SD Card / Camera patterns
    CAMERA_PATTERNS = [
        "DCIM",
        "DCIM/100CANON",
        "DCIM/100NIKON",
        "DCIM/100SONY",
        "DCIM/100OLYMP",
        "100MEDIA",
        "PRIVATE/AVCHD",
    ]

    # Supported media extensions
    MEDIA_EXTENSIONS = {
        '.jpg', '.jpeg', '.png', '.gif', '.heic', '.heif',
        '.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'
    }

    def __init__(self):
        """Initialize device scanner"""
        self.system = platform.system()

    def scan_devices(self) -> List[MobileDevice]:
        """
        Scan for mounted mobile devices across all platforms.

        Returns:
            List of MobileDevice objects representing detected devices
        """
        devices = []

        if self.system == "Windows":
            devices.extend(self._scan_windows())
        elif self.system == "Darwin":  # macOS
            devices.extend(self._scan_macos())
        elif self.system == "Linux":
            devices.extend(self._scan_linux())

        return devices

    def _scan_windows(self) -> List[MobileDevice]:
        """Scan Windows drives D: through Z: for mobile devices"""
        devices = []

        for drive_letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            drive_path = f"{drive_letter}:\\"
            if not os.path.exists(drive_path):
                continue

            device = self._check_device_at_path(drive_path)
            if device:
                devices.append(device)

        return devices

    def _scan_macos(self) -> List[MobileDevice]:
        """Scan macOS /Volumes for mobile devices"""
        devices = []
        volumes_path = Path("/Volumes")

        if not volumes_path.exists():
            return devices

        for volume in volumes_path.iterdir():
            if not volume.is_dir():
                continue
            # Skip system volume (Macintosh HD)
            if volume.name in ("Macintosh HD", "System"):
                continue

            device = self._check_device_at_path(str(volume))
            if device:
                devices.append(device)

        return devices

    def _scan_linux(self) -> List[MobileDevice]:
        """Scan Linux mount points for mobile devices"""
        devices = []

        # Common mount locations
        mount_bases = [
            "/media",
            "/mnt",
            "/run/media",
        ]

        # Add current user's media directory
        user = os.getenv("USER")
        if user:
            mount_bases.extend([
                f"/media/{user}",
                f"/run/media/{user}",
            ])

        for base in mount_bases:
            base_path = Path(base)
            if not base_path.exists():
                continue

            # Check each subdirectory
            try:
                for mount_point in base_path.iterdir():
                    if not mount_point.is_dir():
                        continue

                    device = self._check_device_at_path(str(mount_point))
                    if device:
                        devices.append(device)
            except (PermissionError, OSError):
                continue

        return devices

    def _check_device_at_path(self, root_path: str) -> Optional[MobileDevice]:
        """
        Check if a path contains a mobile device by looking for DCIM folder
        or other common camera/media folder structures.

        Args:
            root_path: Path to check (drive, volume, or mount point)

        Returns:
            MobileDevice if detected, None otherwise
        """
        root = Path(root_path)

        # Primary check: DCIM folder (standard for cameras and phones)
        has_dcim = (root / "DCIM").exists() and (root / "DCIM").is_dir()

        # Alternate checks for devices without standard DCIM structure
        alternate_indicators = [
            "Internal Storage/DCIM",  # Some Android devices
            "Camera",                  # Some cameras
            "Pictures",                # Alternative structure
            "Photos",                  # Alternative structure
            "100MEDIA",                # Some cameras
            "PRIVATE/AVCHD",          # Video cameras
        ]

        has_alternate = False
        for alt_path in alternate_indicators:
            if (root / alt_path).exists() and (root / alt_path).is_dir():
                has_alternate = True
                break

        # Must have either DCIM or alternate structure
        if not has_dcim and not has_alternate:
            return None

        # Determine device type
        device_type = self._detect_device_type(root_path)

        # Get device label (volume name or directory name)
        label = self._get_device_label(root_path)

        # Scan for media folders
        folders = self._scan_media_folders(root_path, device_type)

        if not folders:
            # No media folders found, skip this device
            return None

        return MobileDevice(
            label=label,
            root_path=root_path,
            device_type=device_type,
            folders=folders
        )

    def _detect_device_type(self, root_path: str) -> str:
        """
        Detect if device is Android, iOS, or camera/SD card based on folder structure.

        Args:
            root_path: Device root path

        Returns:
            "android", "ios", or "camera"
        """
        root = Path(root_path)
        dcim = root / "DCIM"

        # iOS devices have DCIM/100APPLE, 101APPLE patterns
        if dcim.exists():
            try:
                for folder in dcim.iterdir():
                    if folder.is_dir() and "APPLE" in folder.name.upper():
                        return "ios"
            except (PermissionError, OSError):
                pass

        # Check for camera-specific patterns (Canon, Nikon, Sony, etc.)
        camera_markers = [
            "DCIM/100CANON",
            "DCIM/100NIKON",
            "DCIM/100SONY",
            "DCIM/100OLYMP",
            "DCIM/100PANA",
            "DCIM/100FUJI",
            "100MEDIA",
            "PRIVATE/AVCHD",
        ]
        for marker in camera_markers:
            if (root / marker).exists():
                return "camera"

        # Check for Android-specific folders
        android_markers = [
            "Android",
            "Internal Storage",
            "Pictures/Screenshots",
            "Pictures/WhatsApp",
        ]
        for marker in android_markers:
            if (root / marker).exists():
                return "android"

        # If has only DCIM and nothing else specific, likely a camera/SD card
        if dcim.exists():
            # Check if it's a simple structure (just DCIM, no other phone folders)
            try:
                root_contents = [item.name for item in root.iterdir() if item.is_dir()]
                phone_folders = ["Android", "Music", "Movies", "Downloads", "Documents"]
                has_phone_folders = any(pf in root_contents for pf in phone_folders)

                if not has_phone_folders and "DCIM" in root_contents:
                    return "camera"
            except (PermissionError, OSError):
                pass

        # Default to android (more common for phones)
        return "android"

    def _get_device_label(self, root_path: str) -> str:
        """
        Get human-readable device label.

        Args:
            root_path: Device root path

        Returns:
            Device label (e.g., "Samsung Galaxy S22", "iPhone 14 Pro", "SD Card")
        """
        # Extract volume/directory name
        path = Path(root_path)
        base_name = path.name

        # Add device emoji based on type
        device_type = self._detect_device_type(root_path)

        emoji_map = {
            "android": "🤖",
            "ios": "🍎",
            "camera": "📷",
        }
        emoji = emoji_map.get(device_type, "📱")

        # Clean up common prefixes and improve labels
        if base_name.upper() in ("DCIM", "CAMERA", "PHONE"):
            if device_type == "camera":
                base_name = "SD Card / Camera"
            elif device_type == "ios":
                base_name = "iPhone"
            else:
                base_name = "Android Device"
        elif base_name.upper() in ("NO NAME", "UNTITLED", ""):
            if device_type == "camera":
                base_name = "SD Card"
            else:
                base_name = "Mobile Device"

        return f"{emoji} {base_name}"

    def _scan_media_folders(self, root_path: str, device_type: str) -> List[DeviceFolder]:
        """
        Scan device for media folders containing photos/videos.

        Args:
            root_path: Device root path
            device_type: "android", "ios", or "camera"

        Returns:
            List of DeviceFolder objects
        """
        folders = []
        root = Path(root_path)

        # Use appropriate patterns based on device type
        if device_type == "camera":
            patterns = self.CAMERA_PATTERNS
        elif device_type == "ios":
            patterns = self.IOS_PATTERNS
        else:  # android
            patterns = self.ANDROID_PATTERNS

        # Scan each pattern
        for pattern in patterns:
            folder_path = root / pattern
            if not folder_path.exists() or not folder_path.is_dir():
                continue

            # Quick count of media files (don't recurse deeply for performance)
            count = self._quick_count_media(folder_path)

            if count > 0:
                # Get display name (last part of path)
                display_name = self._get_folder_display_name(pattern)

                # Skip if display name is None (hidden folders)
                if display_name is None:
                    continue

                folders.append(DeviceFolder(
                    name=display_name,
                    path=str(folder_path),
                    photo_count=count
                ))

        return folders

    def _get_folder_display_name(self, pattern: str) -> str:
        """
        Convert folder pattern to display name.

        Args:
            pattern: Folder pattern (e.g., "DCIM/Camera", "DCIM/100CANON")

        Returns:
            Display name (e.g., "Camera", "Canon Photos") or None to skip
        """
        parts = pattern.split('/')
        name = parts[-1]

        # Skip hidden folders
        if name.startswith('.'):
            return None

        # Special cases for common folders
        if name == "DCIM":
            return "Camera Roll"

        if "APPLE" in name.upper():
            return "Camera Roll"

        # Camera-specific folders
        camera_brands = {
            "CANON": "Canon Photos",
            "NIKON": "Nikon Photos",
            "SONY": "Sony Photos",
            "OLYMP": "Olympus Photos",
            "PANA": "Panasonic Photos",
            "FUJI": "Fujifilm Photos",
        }

        for brand, display in camera_brands.items():
            if brand in name.upper():
                return display

        # Generic media folders
        if "100MEDIA" in name or "MEDIA" in name:
            return "Media"

        if "AVCHD" in name:
            return "Videos"

        # Clean up "Internal Storage" prefix
        if "Internal Storage" in pattern:
            return name

        return name

    def _quick_count_media(self, folder_path: Path, max_depth: int = 2) -> int:
        """
        Quick count of media files in folder (non-recursive for performance).

        Args:
            folder_path: Path to scan
            max_depth: Maximum recursion depth (default 2)

        Returns:
            Estimated count of media files
        """
        count = 0

        try:
            # Non-recursive: just count files in this directory
            for item in folder_path.iterdir():
                if item.is_file():
                    if item.suffix.lower() in self.MEDIA_EXTENSIONS:
                        count += 1
                elif item.is_dir() and max_depth > 0 and not item.name.startswith('.'):
                    # Recurse into subdirectories (limited depth)
                    count += self._quick_count_media(item, max_depth - 1)
        except (PermissionError, OSError):
            # Skip folders we can't read
            pass

        return count


# Convenience function
def scan_mobile_devices() -> List[MobileDevice]:
    """
    Scan for all mounted mobile devices.

    Returns:
        List of MobileDevice objects
    """
    scanner = DeviceScanner()
    return scanner.scan_devices()
