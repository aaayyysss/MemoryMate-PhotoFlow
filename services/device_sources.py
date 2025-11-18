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
        "DCIM/.thumbnails",
        "Pictures",
        "Pictures/Screenshots",
        "Pictures/WhatsApp",
        "Download",
    ]

    # Common folder patterns for iOS
    IOS_PATTERNS = [
        "DCIM",
        "DCIM/100APPLE",
        "DCIM/101APPLE",
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
        Check if a path contains a mobile device by looking for DCIM folder.

        Args:
            root_path: Path to check (drive, volume, or mount point)

        Returns:
            MobileDevice if detected, None otherwise
        """
        dcim_path = Path(root_path) / "DCIM"

        # Must have DCIM folder to be considered a camera device
        if not dcim_path.exists() or not dcim_path.is_dir():
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
        Detect if device is Android or iOS based on folder structure.

        Args:
            root_path: Device root path

        Returns:
            "android" or "ios"
        """
        root = Path(root_path)

        # iOS devices have DCIM/100APPLE, 101APPLE patterns
        dcim = root / "DCIM"
        if dcim.exists():
            for folder in dcim.iterdir():
                if folder.is_dir() and "APPLE" in folder.name.upper():
                    return "ios"

        # Check for Android-specific folders
        android_markers = [
            "Android",
            "Pictures/Screenshots",
            "Pictures/WhatsApp",
        ]
        for marker in android_markers:
            if (root / marker).exists():
                return "android"

        # Default to android (more common)
        return "android"

    def _get_device_label(self, root_path: str) -> str:
        """
        Get human-readable device label.

        Args:
            root_path: Device root path

        Returns:
            Device label (e.g., "Samsung Galaxy S22", "iPhone 14 Pro")
        """
        # Extract volume/directory name
        path = Path(root_path)
        base_name = path.name

        # Add device emoji based on type
        device_type = self._detect_device_type(root_path)
        emoji = "📱"

        # Clean up common prefixes
        if base_name.upper() in ("DCIM", "CAMERA", "PHONE"):
            base_name = "Mobile Device"

        return f"{emoji} {base_name}"

    def _scan_media_folders(self, root_path: str, device_type: str) -> List[DeviceFolder]:
        """
        Scan device for media folders containing photos/videos.

        Args:
            root_path: Device root path
            device_type: "android" or "ios"

        Returns:
            List of DeviceFolder objects
        """
        folders = []
        root = Path(root_path)

        # Use appropriate patterns based on device type
        patterns = self.ANDROID_PATTERNS if device_type == "android" else self.IOS_PATTERNS

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
            pattern: Folder pattern (e.g., "DCIM/Camera")

        Returns:
            Display name (e.g., "Camera")
        """
        parts = pattern.split('/')
        name = parts[-1]

        # Special cases
        if name == "DCIM":
            return "Camera Roll"
        if "APPLE" in name:
            return "Camera Roll"
        if name.startswith('.'):
            return None  # Skip hidden folders

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
