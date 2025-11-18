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
    device_type: str            # "android", "ios", "camera", "usb", "sd_card"
    folders: List[DeviceFolder] # DCIM folders and other media folders
    device_id: Optional[str] = None      # Unique persistent device ID
    serial_number: Optional[str] = None  # Physical serial number
    volume_guid: Optional[str] = None    # Volume GUID (Windows)


class DeviceScanner:
    """
    Cross-platform mobile device scanner with persistent device tracking.

    Detects mobile devices and optionally registers them in the database
    for import history tracking.
    """

    # Common DCIM folder patterns for Android
    ANDROID_PATTERNS = [
        "DCIM/Camera",
        "DCIM",
        "DCIM/.thumbnails",
        "Internal Storage/DCIM",
        "Internal Storage/DCIM/Camera",
        # MTP mount subdirectories (common on GVFS mounts)
        "Internal shared storage/DCIM",
        "Internal shared storage/DCIM/Camera",
        "Phone storage/DCIM",
        "Phone storage/DCIM/Camera",
        "Card/DCIM",
        "Card/DCIM/Camera",
        "SD card/DCIM",
        "SD card/DCIM/Camera",
        # Other common Android folders
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
        # AFC/MTP subdirectories (if iOS device is mounted via third-party tools)
        "Internal Storage/DCIM",
        "Internal Storage/DCIM/100APPLE",
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

    def __init__(self, db=None, register_devices: bool = True):
        """
        Initialize device scanner.

        Args:
            db: ReferenceDB instance for device registration (optional)
            register_devices: Whether to register detected devices in database
        """
        self.system = platform.system()
        self.db = db
        self.register_devices = register_devices

    def scan_devices(self) -> List[MobileDevice]:
        """
        Scan for mounted mobile devices across all platforms.

        Automatically registers devices in database if db was provided.

        Returns:
            List of MobileDevice objects representing detected devices
        """
        print(f"\n[DeviceScanner] ===== Starting device scan =====")
        print(f"[DeviceScanner] Platform: {self.system}")
        print(f"[DeviceScanner] Database registration: {'enabled' if self.db and self.register_devices else 'disabled'}")

        devices = []

        if self.system == "Windows":
            print(f"[DeviceScanner] Scanning Windows drives...")
            devices.extend(self._scan_windows())
        elif self.system == "Darwin":  # macOS
            print(f"[DeviceScanner] Scanning macOS volumes...")
            devices.extend(self._scan_macos())
        elif self.system == "Linux":
            print(f"[DeviceScanner] Scanning Linux mount points...")
            devices.extend(self._scan_linux())
        else:
            print(f"[DeviceScanner] WARNING: Unknown platform '{self.system}'")

        print(f"[DeviceScanner] ===== Scan complete: {len(devices)} device(s) found =====\n")
        return devices

    def _scan_windows(self) -> List[MobileDevice]:
        """
        Scan Windows for mobile devices.

        Checks both:
        1. Drive letters (D: through Z:) - for SD cards, cameras mounted as drives
        2. Portable devices (MTP) - for Android/iOS phones under "This PC"
        """
        devices = []

        # Method 1: Scan drive letters (for SD cards, cameras)
        print(f"[DeviceScanner]   Checking drive letters D:-Z:...")
        for drive_letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            drive_path = f"{drive_letter}:\\"
            if not os.path.exists(drive_path):
                continue

            print(f"[DeviceScanner]     Drive {drive_letter}: exists, checking...")
            device = self._check_device_at_path(drive_path)
            if device:
                print(f"[DeviceScanner]       ✓ Device found on drive {drive_letter}:")
                devices.append(device)
            else:
                print(f"[DeviceScanner]       ✗ No device on drive {drive_letter}:")

        # Method 2: Scan portable devices (MTP/PTP - Android/iOS phones)
        print(f"[DeviceScanner]   Checking portable devices (MTP/PTP)...")
        portable_devices = self._scan_windows_portable_devices()
        devices.extend(portable_devices)

        return devices

    def _scan_windows_portable_devices(self) -> List[MobileDevice]:
        """
        Detect Windows MTP/PTP portable devices (Android/iOS phones).

        Uses win32com.shell to enumerate devices under "This PC" namespace.
        Falls back to wmic if COM is not available.
        """
        devices = []

        # Try using win32com.shell (more reliable)
        try:
            print(f"[DeviceScanner]     Attempting Shell COM enumeration...")
            import win32com.client

            shell = win32com.client.Dispatch("Shell.Application")
            # Namespace 17 = "This PC" / "Computer"
            computer_folder = shell.Namespace(17)

            if computer_folder:
                items = computer_folder.Items()
                print(f"[DeviceScanner]     Found {items.Count} items under 'This PC'")

                for item in items:
                    # Check if it's a portable device
                    # Portable devices have IsFileSystem=False and IsFolder=True
                    if item.IsFolder and not item.IsFileSystem:
                        device_name = item.Name
                        print(f"[DeviceScanner]       • Portable device found: {device_name}")

                        # Try to access the device folder
                        try:
                            device_folder = shell.Namespace(item.Path)
                            if device_folder:
                                # Enumerate storage locations (Phone, Card, etc.)
                                storage_items = device_folder.Items()
                                print(f"[DeviceScanner]         Storage locations: {storage_items.Count}")

                                for storage in storage_items:
                                    if storage.IsFolder:
                                        storage_name = storage.Name
                                        storage_path = storage.Path
                                        print(f"[DeviceScanner]           • Storage: {storage_name}")
                                        print(f"[DeviceScanner]             Path: {storage_path}")

                                        # Check if this storage location has DCIM
                                        device = self._check_device_at_path(storage_path)
                                        if device:
                                            print(f"[DeviceScanner]             ✓ Device detected!")
                                            devices.append(device)
                                        else:
                                            print(f"[DeviceScanner]             ✗ No DCIM found")
                        except Exception as e:
                            print(f"[DeviceScanner]         ERROR accessing {device_name}: {e}")
            else:
                print(f"[DeviceScanner]     ✗ Could not access 'This PC' namespace")

        except ImportError:
            print(f"[DeviceScanner]     ✗ win32com not available, trying fallback...")
            # Fallback: Use wmic to list portable devices
            devices.extend(self._scan_windows_portable_wmic())
        except Exception as e:
            print(f"[DeviceScanner]     ✗ Shell COM enumeration failed: {e}")
            # Fallback: Use wmic
            devices.extend(self._scan_windows_portable_wmic())

        return devices

    def _scan_windows_portable_wmic(self) -> List[MobileDevice]:
        """
        Fallback: Use wmic to detect portable devices.
        This is less reliable but works without win32com.
        """
        devices = []
        print(f"[DeviceScanner]     Using wmic fallback...")

        try:
            import subprocess
            result = subprocess.run(
                ["wmic", "logicaldisk", "get", "caption,volumename,drivetype"],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                print(f"[DeviceScanner]     wmic output received")
                # Parse output for portable devices (DriveType=2 is removable)
                lines = result.stdout.strip().split('\n')
                for line in lines[1:]:  # Skip header
                    if line.strip():
                        parts = line.split()
                        if len(parts) >= 2:
                            drive = parts[0]
                            if os.path.exists(drive):
                                print(f"[DeviceScanner]       Checking {drive}...")
                                device = self._check_device_at_path(drive)
                                if device:
                                    devices.append(device)
            else:
                print(f"[DeviceScanner]     ✗ wmic failed")
        except Exception as e:
            print(f"[DeviceScanner]     ✗ wmic fallback failed: {e}")

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
        print(f"[DeviceScanner] Current user: {user}")
        if user:
            mount_bases.extend([
                f"/media/{user}",
                f"/run/media/{user}",
            ])

        print(f"[DeviceScanner] Checking mount locations: {mount_bases}")

        for base in mount_bases:
            base_path = Path(base)
            if not base_path.exists():
                print(f"[DeviceScanner]   ✗ {base} - does not exist")
                continue

            print(f"[DeviceScanner]   ✓ {base} - exists")

            # Check each subdirectory
            try:
                mount_points = list(base_path.iterdir())
                print(f"[DeviceScanner]     Found {len(mount_points)} mount points")

                for mount_point in mount_points:
                    if not mount_point.is_dir():
                        print(f"[DeviceScanner]       • {mount_point.name} - skipping (not a directory)")
                        continue

                    print(f"[DeviceScanner]       • {mount_point.name} - checking...")
                    device = self._check_device_at_path(str(mount_point))
                    if device:
                        print(f"[DeviceScanner]         ✓ Device detected: {device.label}")
                        devices.append(device)
                    else:
                        print(f"[DeviceScanner]         ✗ No device detected")
            except (PermissionError, OSError) as e:
                print(f"[DeviceScanner]   ✗ {base} - permission denied: {e}")
                continue

        # ================================================================
        # GVFS MTP mount detection (used by most Linux file managers)
        # ================================================================
        print(f"[DeviceScanner] Checking GVFS MTP mounts...")
        devices.extend(self._scan_gvfs_mtp())

        return devices

    def _scan_gvfs_mtp(self) -> List[MobileDevice]:
        """
        Scan GVFS MTP mounts for mobile devices.

        GVFS (GNOME Virtual File System) is used by most Linux file managers
        to mount MTP devices at paths like:
        - /run/user/<uid>/gvfs/mtp:host=...
        - ~/.gvfs/mtp:host=... (older systems)
        """
        devices = []
        gvfs_paths = []

        # Modern GVFS location
        uid = os.getuid()
        modern_gvfs = Path(f"/run/user/{uid}/gvfs")
        if modern_gvfs.exists():
            gvfs_paths.append(modern_gvfs)
            print(f"[DeviceScanner]   ✓ Found modern GVFS: {modern_gvfs}")
        else:
            print(f"[DeviceScanner]   ✗ Modern GVFS not found: {modern_gvfs}")

        # Legacy GVFS location
        home = os.path.expanduser("~")
        legacy_gvfs = Path(f"{home}/.gvfs")
        if legacy_gvfs.exists():
            gvfs_paths.append(legacy_gvfs)
            print(f"[DeviceScanner]   ✓ Found legacy GVFS: {legacy_gvfs}")
        else:
            print(f"[DeviceScanner]   ✗ Legacy GVFS not found: {legacy_gvfs}")

        if not gvfs_paths:
            print(f"[DeviceScanner]   No GVFS mount points found")
            return devices

        # Scan each GVFS location for MTP mounts
        for gvfs_base in gvfs_paths:
            try:
                mounts = list(gvfs_base.iterdir())
                print(f"[DeviceScanner]   Found {len(mounts)} GVFS mount(s)")

                for mount in mounts:
                    if not mount.is_dir():
                        continue

                    # Look for MTP mounts (mtp:host=..., gphoto2:host=..., afc:host=...)
                    mount_name = mount.name
                    if any(prefix in mount_name.lower() for prefix in ["mtp:", "gphoto2:", "afc:"]):
                        print(f"[DeviceScanner]     • Found MTP/PTP mount: {mount_name}")

                        # Check if this is a mobile device
                        device = self._check_device_at_path(str(mount))
                        if device:
                            print(f"[DeviceScanner]       ✓ Device detected: {device.label}")
                            devices.append(device)
                        else:
                            print(f"[DeviceScanner]       ✗ No device detected")
                    else:
                        print(f"[DeviceScanner]     • Skipping non-MTP mount: {mount_name}")

            except (PermissionError, OSError) as e:
                print(f"[DeviceScanner]   ✗ Cannot access GVFS {gvfs_base}: {e}")
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
        print(f"[DeviceScanner]           Checking path: {root_path}")

        # List directory contents for debugging
        try:
            contents = [item.name for item in root.iterdir() if item.is_dir()]
            print(f"[DeviceScanner]           Directories found: {contents}")
        except (PermissionError, OSError) as e:
            print(f"[DeviceScanner]           ERROR: Cannot list directory: {e}")
            return None

        # Primary check: DCIM folder (standard for cameras and phones)
        has_dcim = (root / "DCIM").exists() and (root / "DCIM").is_dir()
        print(f"[DeviceScanner]           Has DCIM folder: {has_dcim}")

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
        found_alternate = None
        for alt_path in alternate_indicators:
            if (root / alt_path).exists() and (root / alt_path).is_dir():
                has_alternate = True
                found_alternate = alt_path
                break

        if has_alternate:
            print(f"[DeviceScanner]           Has alternate structure: {found_alternate}")
        else:
            print(f"[DeviceScanner]           No alternate structure found")

        # MTP/GVFS-specific check: Look for DCIM in subdirectories
        # This handles Android phones mounted via MTP where structure is:
        # mtp:host=Phone/ → Internal shared storage/ → DCIM/
        has_dcim_in_subdir = False
        if not has_dcim and not has_alternate:
            print(f"[DeviceScanner]           Checking subdirectories for DCIM (MTP mounts)...")
            try:
                for subdir in root.iterdir():
                    if not subdir.is_dir():
                        continue

                    # Common MTP subdirectory names
                    subdir_name_lower = subdir.name.lower()
                    if any(name in subdir_name_lower for name in [
                        "internal", "storage", "phone", "card", "sdcard", "shared"
                    ]):
                        print(f"[DeviceScanner]             Checking subdirectory: {subdir.name}")
                        if (subdir / "DCIM").exists() and (subdir / "DCIM").is_dir():
                            has_dcim_in_subdir = True
                            print(f"[DeviceScanner]             ✓ Found DCIM in: {subdir.name}/DCIM")
                            break
            except (PermissionError, OSError) as e:
                print(f"[DeviceScanner]           Cannot scan subdirectories: {e}")

        if has_dcim_in_subdir:
            print(f"[DeviceScanner]           Has DCIM in subdirectory: True")

        # Must have either DCIM or alternate structure
        if not has_dcim and not has_alternate and not has_dcim_in_subdir:
            print(f"[DeviceScanner]           REJECTED: No DCIM or alternate structure")
            return None

        # Determine device type
        device_type = self._detect_device_type(root_path)
        print(f"[DeviceScanner]           Device type: {device_type}")

        # Get device label (volume name or directory name)
        label = self._get_device_label(root_path)
        print(f"[DeviceScanner]           Device label: {label}")

        # Scan for media folders
        print(f"[DeviceScanner]           Scanning for media folders...")
        folders = self._scan_media_folders(root_path, device_type)
        print(f"[DeviceScanner]           Found {len(folders)} media folder(s)")

        if not folders:
            # No media folders found, skip this device
            print(f"[DeviceScanner]           REJECTED: No media folders with photos/videos")
            return None

        # Extract unique device ID (Phase 1: Device Tracking)
        device_id = None
        serial_number = None
        volume_guid = None

        print(f"[DeviceScanner]           Extracting device ID...")
        try:
            from services.device_id_extractor import get_device_id
            device_identifier = get_device_id(root_path, device_type)

            device_id = device_identifier.device_id
            serial_number = device_identifier.serial_number
            volume_guid = device_identifier.volume_guid

            print(f"[DeviceScanner]           Device ID: {device_id}")
            print(f"[DeviceScanner]           Serial: {serial_number}")
            print(f"[DeviceScanner]           Volume GUID: {volume_guid}")

            # Register device in database if db provided
            if self.db and self.register_devices and device_id:
                try:
                    self.db.register_device(
                        device_id=device_id,
                        device_name=label,
                        device_type=device_type,
                        serial_number=serial_number,
                        volume_guid=volume_guid,
                        mount_point=root_path
                    )
                    print(f"[DeviceScanner]           ✓ Registered in database")
                except Exception as e:
                    print(f"[DeviceScanner]           WARNING: Failed to register device in DB: {e}")

        except Exception as e:
            # Device ID extraction failed - not critical, continue without ID
            print(f"[DeviceScanner]           WARNING: Device ID extraction failed: {e}")
            import traceback
            traceback.print_exc()

        print(f"[DeviceScanner]           ✓✓✓ DEVICE ACCEPTED: {label} ({device_type})")
        return MobileDevice(
            label=label,
            root_path=root_path,
            device_type=device_type,
            folders=folders,
            device_id=device_id,
            serial_number=serial_number,
            volume_guid=volume_guid
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
def scan_mobile_devices(db=None, register_devices: bool = True) -> List[MobileDevice]:
    """
    Scan for all mounted mobile devices.

    Args:
        db: ReferenceDB instance for device registration (optional)
        register_devices: Whether to register detected devices in database

    Returns:
        List of MobileDevice objects with device IDs

    Example:
        >>> from reference_db import ReferenceDB
        >>> db = ReferenceDB()
        >>> devices = scan_mobile_devices(db=db)
        >>> for device in devices:
        ...     print(f"{device.label}: {device.device_id}")
    """
    scanner = DeviceScanner(db=db, register_devices=register_devices)
    return scanner.scan_devices()
