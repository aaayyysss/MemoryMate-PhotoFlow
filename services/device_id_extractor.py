"""
Device ID Extraction Service

Extracts unique, persistent device identifiers for mobile devices,
cameras, USB drives, and SD cards across Windows, macOS, and Linux.

This enables the app to recognize when the same device is reconnected.
"""

import os
import platform
import subprocess
import uuid
from pathlib import Path
from typing import Optional, Tuple
from dataclasses import dataclass


@dataclass
class DeviceIdentifier:
    """Represents a unique device identifier"""
    device_id: str          # Unique persistent ID
    device_name: str        # Human-readable name
    device_type: str        # "android", "ios", "camera", "usb", "sd_card"
    serial_number: Optional[str] = None  # Physical serial (if available)
    volume_guid: Optional[str] = None    # Volume GUID (Windows only)
    mount_point: str = ""   # Current mount path


class DeviceIDExtractor:
    """
    Extracts unique device IDs from various storage devices.

    Strategy:
    1. Android (MTP): Use USB serial number via mtp-detect/libmtp
    2. iOS: Use device UUID via idevice_id
    3. USB/SD Cards: Use volume UUID or generate from serial + label
    4. Fallback: Hash of mount point + volume label
    """

    def __init__(self):
        self.system = platform.system()

    def extract_device_id(self, root_path: str, device_type: str) -> DeviceIdentifier:
        """
        Extract unique device ID from mount point.

        Args:
            root_path: Device mount point (e.g., "/media/user/phone")
            device_type: Device type hint ("android", "ios", "camera", etc.)

        Returns:
            DeviceIdentifier with unique ID and metadata
        """
        # Normalize path
        root_path = os.path.abspath(root_path)

        # Try type-specific extraction
        if device_type == "android":
            return self._extract_android_id(root_path)
        elif device_type == "ios":
            return self._extract_ios_id(root_path)
        else:
            # Generic USB/SD card/camera
            return self._extract_volume_id(root_path, device_type)

    def _extract_android_id(self, root_path: str) -> DeviceIdentifier:
        """
        Extract Android device ID via MTP.

        Uses:
        - Linux: mtp-detect to get USB serial
        - Windows: WMI to query device serial
        - macOS: Android File Transfer detection
        """
        serial = None
        device_name = Path(root_path).name or "Android Device"

        if self.system == "Linux":
            serial = self._get_mtp_serial_linux(root_path)
        elif self.system == "Windows":
            serial = self._get_mtp_serial_windows(root_path)
        elif self.system == "Darwin":
            serial = self._get_mtp_serial_macos(root_path)

        if serial:
            device_id = f"android:{serial}"
        else:
            # Fallback: Hash mount path + timestamp (not ideal but works)
            device_id = f"android:unknown:{self._hash_path(root_path)}"

        return DeviceIdentifier(
            device_id=device_id,
            device_name=device_name,
            device_type="android",
            serial_number=serial,
            mount_point=root_path
        )

    def _extract_ios_id(self, root_path: str) -> DeviceIdentifier:
        """
        Extract iOS device UUID.

        Uses:
        - Linux/macOS: idevice_id from libimobiledevice
        - Windows: iTunes device enumeration
        """
        device_uuid = None
        device_name = Path(root_path).name or "iPhone"

        if self.system in ["Linux", "Darwin"]:
            device_uuid = self._get_ios_uuid_unix(root_path)
        elif self.system == "Windows":
            device_uuid = self._get_ios_uuid_windows(root_path)

        if device_uuid:
            device_id = f"ios:{device_uuid}"
        else:
            # Fallback
            device_id = f"ios:unknown:{self._hash_path(root_path)}"

        return DeviceIdentifier(
            device_id=device_id,
            device_name=device_name,
            device_type="ios",
            serial_number=device_uuid,
            mount_point=root_path
        )

    def _extract_volume_id(self, root_path: str, device_type: str) -> DeviceIdentifier:
        """
        Extract volume UUID for USB drives, SD cards, cameras.

        Uses:
        - Linux: blkid to get UUID
        - macOS: diskutil to get UUID
        - Windows: wmic to get VolumeSerialNumber
        """
        volume_uuid = None
        volume_label = Path(root_path).name or "Storage Device"

        if self.system == "Linux":
            volume_uuid = self._get_volume_uuid_linux(root_path)
        elif self.system == "Darwin":
            volume_uuid = self._get_volume_uuid_macos(root_path)
        elif self.system == "Windows":
            volume_uuid = self._get_volume_uuid_windows(root_path)

        if volume_uuid:
            device_id = f"{device_type}:{volume_uuid}"
        else:
            # Fallback: Use volume label + hash
            device_id = f"{device_type}:{self._hash_path(root_path)}"

        return DeviceIdentifier(
            device_id=device_id,
            device_name=volume_label,
            device_type=device_type,
            volume_guid=volume_uuid,
            mount_point=root_path
        )

    # ======================================================================
    # Platform-specific device ID extraction methods
    # ======================================================================

    def _get_mtp_serial_linux(self, root_path: str) -> Optional[str]:
        """Get Android MTP device serial on Linux via mtp-detect."""
        try:
            # Run mtp-detect to list MTP devices
            result = subprocess.run(
                ["mtp-detect"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                # Parse output for serial number
                for line in result.stdout.splitlines():
                    if "Serial number:" in line:
                        serial = line.split(":", 1)[1].strip()
                        if serial and serial != "0":
                            return serial
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as e:
            print(f"[DeviceID] MTP detection failed on Linux: {e}")

        return None

    def _get_mtp_serial_windows(self, root_path: str) -> Optional[str]:
        """Get Android MTP device serial on Windows via WMI."""
        # Note: Windows MTP devices don't appear as drive letters by default
        # They appear in "This PC" but require Windows Portable Devices API
        # For now, fall back to hash-based ID
        return None

    def _get_mtp_serial_macos(self, root_path: str) -> Optional[str]:
        """Get Android MTP device serial on macOS."""
        # macOS requires Android File Transfer app for MTP
        # No native command-line tools available
        return None

    def _get_ios_uuid_unix(self, root_path: str) -> Optional[str]:
        """Get iOS device UUID on Linux/macOS via idevice_id."""
        try:
            # List all connected iOS devices
            result = subprocess.run(
                ["idevice_id", "-l"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                # Get first device UUID
                lines = result.stdout.strip().splitlines()
                if lines:
                    return lines[0].strip()
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as e:
            print(f"[DeviceID] iOS detection failed: {e}")

        return None

    def _get_ios_uuid_windows(self, root_path: str) -> Optional[str]:
        """Get iOS device UUID on Windows."""
        # Windows: iTunes creates device identifiers
        # Would need to query iTunes COM interface or registry
        # For now, fall back to hash-based ID
        return None

    def _get_volume_uuid_linux(self, root_path: str) -> Optional[str]:
        """Get volume UUID on Linux via blkid."""
        try:
            # Find device for mount point
            result = subprocess.run(
                ["findmnt", "-n", "-o", "SOURCE", root_path],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                device = result.stdout.strip()

                # Get UUID for device
                result2 = subprocess.run(
                    ["blkid", "-s", "UUID", "-o", "value", device],
                    capture_output=True,
                    text=True,
                    timeout=5
                )

                if result2.returncode == 0:
                    uuid_val = result2.stdout.strip()
                    if uuid_val:
                        return uuid_val
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as e:
            print(f"[DeviceID] Linux volume UUID extraction failed: {e}")

        return None

    def _get_volume_uuid_macos(self, root_path: str) -> Optional[str]:
        """Get volume UUID on macOS via diskutil."""
        try:
            result = subprocess.run(
                ["diskutil", "info", root_path],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                # Parse diskutil output
                for line in result.stdout.splitlines():
                    if "Volume UUID:" in line:
                        uuid_val = line.split(":", 1)[1].strip()
                        if uuid_val:
                            return uuid_val
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as e:
            print(f"[DeviceID] macOS volume UUID extraction failed: {e}")

        return None

    def _get_volume_uuid_windows(self, root_path: str) -> Optional[str]:
        """Get volume serial number on Windows via wmic."""
        try:
            # Extract drive letter (e.g., "E:" from "E:\\")
            drive_letter = Path(root_path).anchor.rstrip("\\")

            result = subprocess.run(
                ["wmic", "volume", "where", f"DriveLetter='{drive_letter}'", "get", "DeviceID"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                lines = result.stdout.strip().splitlines()
                if len(lines) > 1:
                    device_id = lines[1].strip()
                    if device_id:
                        return device_id
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as e:
            print(f"[DeviceID] Windows volume ID extraction failed: {e}")

        return None

    def _hash_path(self, path: str) -> str:
        """Generate deterministic hash from path (last resort fallback)."""
        # Use volume label + path hash
        # This is NOT persistent across remounts but better than nothing
        path_hash = abs(hash(path)) % 100000
        return f"{path_hash:05d}"


# Convenience function
def get_device_id(root_path: str, device_type: str) -> DeviceIdentifier:
    """
    Extract device ID from mount point.

    Args:
        root_path: Device mount path
        device_type: Type hint ("android", "ios", "camera", etc.)

    Returns:
        DeviceIdentifier with unique ID

    Example:
        >>> device = get_device_id("/media/user/Galaxy_S22", "android")
        >>> print(device.device_id)
        "android:ABC123XYZ"
    """
    extractor = DeviceIDExtractor()
    return extractor.extract_device_id(root_path, device_type)


if __name__ == "__main__":
    # Test device ID extraction
    import sys

    if len(sys.argv) < 2:
        print("Usage: python device_id_extractor.py <mount_path> [device_type]")
        print("Example: python device_id_extractor.py /media/user/phone android")
        sys.exit(1)

    mount_path = sys.argv[1]
    dev_type = sys.argv[2] if len(sys.argv) > 2 else "usb"

    device = get_device_id(mount_path, dev_type)

    print(f"Device ID: {device.device_id}")
    print(f"Device Name: {device.device_name}")
    print(f"Device Type: {device.device_type}")
    print(f"Serial Number: {device.serial_number}")
    print(f"Volume GUID: {device.volume_guid}")
    print(f"Mount Point: {device.mount_point}")
