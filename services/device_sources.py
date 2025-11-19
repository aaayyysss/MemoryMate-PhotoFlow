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
    is_mtp: bool = False                 # True if accessed via MTP/PTP (Windows portable device)


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

                filesystem_items_to_check = []  # Track filesystem items that might be devices

                for item in items:
                    # DEBUG: Log all items to diagnose detection issues
                    try:
                        item_name = item.Name
                        is_folder = item.IsFolder
                        is_filesystem = item.IsFileSystem
                        print(f"[DeviceScanner]       → Item: '{item_name}' | IsFolder={is_folder} | IsFileSystem={is_filesystem}")
                    except Exception as e:
                        print(f"[DeviceScanner]       → Item inspection error: {e}")
                        continue

                    # Check if it's a portable device (primary method)
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
                                        # Use the FolderItem directly instead of path string
                                        device = self._check_portable_storage(shell, storage, device_name)
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

    def _check_portable_storage(self, shell, storage_item, device_name: str) -> Optional[MobileDevice]:
        """
        Check a portable device storage location using FolderItem COM object.

        Args:
            shell: Shell.Application COM object
            storage_item: FolderItem COM object for storage location
            device_name: Name of the parent device (e.g., "Galaxy A23")

        Returns:
            MobileDevice if DCIM found, None otherwise
        """
        try:
            storage_name = storage_item.Name
            print(f"[DeviceScanner]             Checking storage via COM: {storage_name}")

            # Get folder from storage item
            storage_folder = storage_item.GetFolder
            if not storage_folder:
                print(f"[DeviceScanner]             ✗ Cannot access storage folder")
                return None

            # PERFORMANCE FIX: Don't enumerate all items (could be 1000+)
            # Instead, try to navigate directly to known media folders
            print(f"[DeviceScanner]             Attempting direct navigation to media folders...")

            has_dcim = False
            dcim_item = None

            # Try to access DCIM directly (much faster than enumerating all items)
            try:
                # Try ParseName to get DCIM folder directly
                items = storage_folder.Items()

                # Limit search to first 100 items max (timeout protection)
                max_check = 100
                checked = 0

                for item in items:
                    checked += 1
                    if item.IsFolder:
                        folder_name = item.Name

                        # Found DCIM at root level
                        if folder_name == "DCIM":
                            has_dcim = True
                            dcim_item = item
                            print(f"[DeviceScanner]             ✓ Found DCIM folder at root")
                            break

                        # Check common storage subdirectories (Internal shared storage, etc.)
                        # Only check folders that are likely to contain DCIM
                        folder_name_lower = folder_name.lower()
                        if any(name in folder_name_lower for name in [
                            "internal", "storage", "phone", "card", "sdcard", "shared"
                        ]):
                            print(f"[DeviceScanner]               Quick-checking: {folder_name}")
                            try:
                                subfolder = item.GetFolder
                                if subfolder:
                                    # Try to find DCIM in this subfolder (don't enumerate all)
                                    subitems = subfolder.Items()
                                    for subitem in subitems:
                                        if subitem.IsFolder and subitem.Name == "DCIM":
                                            has_dcim = True
                                            dcim_item = subitem
                                            print(f"[DeviceScanner]               ✓ Found DCIM in: {folder_name}/DCIM")
                                            break
                                if has_dcim:
                                    break
                            except Exception as e:
                                # Skip this subfolder if we can't access it
                                continue

                    # Timeout protection: Stop after checking max_check items
                    if checked >= max_check:
                        print(f"[DeviceScanner]             Stopped after checking {checked} items (timeout protection)")
                        break

            except Exception as e:
                print(f"[DeviceScanner]             ERROR during DCIM search: {e}")

            if not has_dcim:
                print(f"[DeviceScanner]             REJECTED: No DCIM found")
                return None

            # Device detected! Build device label
            device_label = f"{device_name} - {storage_name}"
            print(f"[DeviceScanner]             Device label: {device_label}")

            # Detect device type
            device_type = "android"
            device_name_lower = device_name.lower()
            if "iphone" in device_name_lower or "ipad" in device_name_lower:
                device_type = "ios"

            print(f"[DeviceScanner]             Device type: {device_type}")

            # Scan for media folders using COM
            print(f"[DeviceScanner]             Scanning for media folders...")
            folders_list = self._scan_portable_storage_folders(shell, storage_item, device_type)
            print(f"[DeviceScanner]             Found {len(folders_list)} media folder(s)")

            if not folders_list:
                print(f"[DeviceScanner]             REJECTED: No media folders with photos")
                return None

            # Create device ID
            storage_path = storage_item.Path
            device_id = f"windows_mtp:{hash(storage_path) & 0xFFFFFFFF:08x}"
            print(f"[DeviceScanner]             Device ID: {device_id}")

            # Register device
            if self.db and self.register_devices:
                try:
                    self.db.register_device(
                        device_id=device_id,
                        device_name=device_label,
                        device_type=device_type,
                        serial_number=None,
                        volume_guid=None
                    )
                    print(f"[DeviceScanner]             ✓ Registered in database")
                except Exception as e:
                    print(f"[DeviceScanner]             WARNING: Database registration failed: {e}")

            print(f"[DeviceScanner]             ✓✓✓ DEVICE ACCEPTED: {device_label} ({device_type})")

            return MobileDevice(
                label=device_label,
                root_path=storage_path,
                folders=folders_list,
                device_id=device_id,
                device_type=device_type,
                is_mtp=True
            )

        except Exception as e:
            print(f"[DeviceScanner]             ✗ Storage check failed: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _scan_portable_storage_folders(self, shell, storage_item, device_type: str) -> List[DeviceFolder]:
        """
        Scan portable storage for media folders using FolderItem COM object.

        PERFORMANCE: Only scans critical media folders (DCIM/Pictures),
        not the entire device tree. Full scan happens when user opens folder.

        Args:
            shell: Shell.Application COM object
            storage_item: FolderItem COM object for storage location
            device_type: Device type (android, ios, camera)

        Returns:
            List of DeviceFolder objects
        """
        folders = []

        try:
            # CRITICAL FIX: Only scan essential media folders during detection
            # Full pattern scanning is too slow over MTP (100+ patterns × 100+ files = freeze)
            # Professional apps (Lightroom, Photos) only check DCIM and Pictures initially

            if device_type == "ios":
                # iOS: Only check DCIM and its immediate subfolders
                essential_patterns = ["DCIM", "DCIM/100APPLE", "DCIM/101APPLE"]
            elif device_type == "camera":
                # Cameras: Check DCIM only
                essential_patterns = ["DCIM", "DCIM/100CANON", "DCIM/100NIKON"]
            else:  # android
                # Android: Comprehensive folder patterns (Option A implementation)
                # Based on Google Photos behavior - check all common media folder locations
                essential_patterns = [
                    # Primary camera folders
                    "DCIM/Camera",
                    "DCIM",
                    "Camera",

                    # User photo folders
                    "Pictures",
                    "Photos",

                    # Screenshots (common locations)
                    "DCIM/Screenshots",
                    "Pictures/Screenshots",
                    "Screenshots",

                    # WhatsApp media
                    "WhatsApp/Media/WhatsApp Images",
                    "WhatsApp/Media/WhatsApp Video",

                    # Telegram media
                    "Telegram/Telegram Images",
                    "Telegram/Telegram Video",

                    # Instagram
                    "Pictures/Instagram",
                    "Instagram",

                    # Downloads
                    "Download",
                    "Downloads",

                    # Movies/Video folders
                    "Movies",
                    "DCIM/Video",

                    # Social media apps
                    "Snapchat/Media",
                    "TikTok",

                    # Samsung-specific structures
                    "Internal shared storage/DCIM/Camera",
                    "Internal shared storage/DCIM",
                    "Internal shared storage/Pictures",

                    # Cloud sync folders
                    "Google Photos",
                    "OneDrive/Pictures",

                    # Other messaging apps
                    "Facebook/Media",
                    "Messenger/Media",
                    "Signal/Media"
                ]

            print(f"[DeviceScanner]               Quick scan: checking {len(essential_patterns)} essential folders only")

            # Get storage folder
            storage_folder = storage_item.GetFolder
            if not storage_folder:
                return folders

            # For each essential pattern, try to navigate to it
            for pattern in essential_patterns:
                try:
                    # Navigate through pattern parts
                    parts = pattern.split('/')
                    current_folder = storage_folder

                    # Navigate to the pattern location with timeout
                    for part in parts:
                        items = current_folder.Items()
                        found = False

                        # Limit item check to prevent hanging
                        checked = 0
                        max_items_to_check = 50  # Don't iterate through more than 50 items

                        for item in items:
                            checked += 1
                            if item.IsFolder and item.Name == part:
                                current_folder = item.GetFolder
                                found = True
                                break

                            # Timeout protection
                            if checked >= max_items_to_check:
                                print(f"[DeviceScanner]                 Stopped searching for '{part}' after {checked} items")
                                break

                        if not found:
                            # This pattern doesn't exist
                            current_folder = None
                            break

                    if current_folder:
                        # Found the folder! Quick check for media files (don't count all)
                        items = current_folder.Items()
                        media_count = 0
                        has_media = False

                        # Quick scan: Only check first 10 items to see if folder has media
                        # Reduced from 20 for better performance
                        checked = 0
                        max_quick_check = 10

                        for item in items:
                            if not item.IsFolder:
                                checked += 1
                                name_lower = item.Name.lower()
                                if any(name_lower.endswith(ext) for ext in [
                                    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.heic',
                                    '.mp4', '.mov', '.avi', '.mkv', '.m4v', '.3gp'
                                ]):
                                    media_count += 1
                                    has_media = True

                                # Stop after checking enough files to confirm media exists
                                if checked >= max_quick_check:
                                    break

                        if has_media:
                            display_name = self._get_folder_display_name(pattern)
                            if display_name:
                                # Build full path for this folder
                                pattern_windows = pattern.replace('/', '\\')
                                full_path = f"{storage_item.Path}\\{pattern_windows}"

                                # Use media_count from quick scan as approximate indicator
                                # This shows users there ARE files, without doing expensive full enumeration
                                # The actual count will be determined when folder is opened/imported
                                print(f"[DeviceScanner]                 ✓ {display_name}: found {media_count}+ media files (quick scan)")
                                folders.append(DeviceFolder(
                                    name=display_name,
                                    path=full_path,
                                    photo_count=media_count  # Approximate count from quick scan
                                ))

                except Exception as e:
                    # Pattern doesn't exist or can't be accessed
                    continue

        except Exception as e:
            print(f"[DeviceScanner]               ERROR scanning folders: {e}")

        return folders

    def _scan_windows_portable_wmic(self) -> List[MobileDevice]:
        """
        Fallback: Use PowerShell to enumerate portable devices.
        This works on all Windows systems without requiring pywin32.
        """
        devices = []
        print(f"[DeviceScanner]     Using PowerShell fallback to enumerate portable devices...")

        try:
            import subprocess

            # PowerShell script to enumerate Shell namespace for portable devices
            ps_script = """
            $shell = New-Object -ComObject Shell.Application
            $computer = $shell.Namespace(17)  # 17 = This PC

            foreach ($item in $computer.Items()) {
                if ($item.IsFolder -and !$item.IsFileSystem) {
                    Write-Host "PORTABLE_DEVICE:$($item.Name):$($item.Path)"

                    # Try to enumerate storage locations inside the device
                    try {
                        $deviceFolder = $shell.Namespace($item.Path)
                        if ($deviceFolder) {
                            foreach ($storage in $deviceFolder.Items()) {
                                if ($storage.IsFolder) {
                                    Write-Host "STORAGE:$($storage.Name):$($storage.Path)"
                                }
                            }
                        }
                    } catch {
                        # Ignore errors accessing device contents
                    }
                }
            }
            """

            # Run PowerShell script
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                print(f"[DeviceScanner]     PowerShell enumeration successful")

                # Parse PowerShell output
                for line in result.stdout.strip().split('\n'):
                    line = line.strip()

                    if line.startswith("PORTABLE_DEVICE:"):
                        parts = line.split(':', 2)
                        if len(parts) >= 3:
                            device_name = parts[1]
                            device_path = parts[2]
                            print(f"[DeviceScanner]       • Portable device found: {device_name}")
                            print(f"[DeviceScanner]         Path: {device_path}")

                    elif line.startswith("STORAGE:"):
                        parts = line.split(':', 2)
                        if len(parts) >= 3:
                            storage_name = parts[1]
                            storage_path = parts[2]
                            print(f"[DeviceScanner]           • Storage: {storage_name}")
                            print(f"[DeviceScanner]             Path: {storage_path}")

                            # Check if this storage location has DCIM
                            try:
                                device = self._check_device_at_path(storage_path)
                                if device:
                                    print(f"[DeviceScanner]             ✓ Device detected!")
                                    devices.append(device)
                                else:
                                    print(f"[DeviceScanner]             ✗ No DCIM found")
                            except Exception as e:
                                print(f"[DeviceScanner]             ERROR checking path: {e}")

            else:
                print(f"[DeviceScanner]     ✗ PowerShell failed (return code: {result.returncode})")
                if result.stderr:
                    print(f"[DeviceScanner]     Error: {result.stderr[:200]}")

        except FileNotFoundError:
            print(f"[DeviceScanner]     ✗ PowerShell not found")
        except subprocess.TimeoutExpired:
            print(f"[DeviceScanner]     ✗ PowerShell enumeration timed out")
        except Exception as e:
            print(f"[DeviceScanner]     ✗ PowerShell fallback failed: {e}")
            import traceback
            traceback.print_exc()

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

    def _check_shell_namespace_device(self, shell_path: str) -> Optional[MobileDevice]:
        """
        Check Windows Shell namespace path for mobile device using COM API.

        Shell namespace paths (starting with ::) cannot be accessed with normal
        file system APIs. Must use Shell.Application COM interface.

        Args:
            shell_path: Shell namespace path (e.g., ::{20D04FE0...}\\\\?\\usb#...)

        Returns:
            MobileDevice if detected, None otherwise
        """
        print(f"[DeviceScanner]           Checking Shell namespace path via COM...")

        try:
            import win32com.client

            shell = win32com.client.Dispatch("Shell.Application")
            folder = shell.Namespace(shell_path)

            if not folder:
                print(f"[DeviceScanner]           ✗ Cannot access Shell namespace")
                return None

            # Get folder items
            items = folder.Items()
            print(f"[DeviceScanner]           Found {items.Count} items in device storage")

            # List folder names
            folder_names = []
            for item in items:
                if item.IsFolder:
                    folder_names.append(item.Name)

            print(f"[DeviceScanner]           Folders found: {folder_names}")

            # Check for DCIM folder
            has_dcim = "DCIM" in folder_names
            print(f"[DeviceScanner]           Has DCIM folder: {has_dcim}")

            # Check subdirectories for DCIM (Internal shared storage, etc.)
            has_dcim_in_subdir = False
            if not has_dcim:
                print(f"[DeviceScanner]           Checking subdirectories for DCIM...")
                for item in items:
                    if item.IsFolder:
                        subdir_name = item.Name
                        subdir_name_lower = subdir_name.lower()

                        # Check if this looks like a storage subdirectory
                        if any(name in subdir_name_lower for name in [
                            "internal", "storage", "phone", "card", "sdcard", "shared"
                        ]):
                            print(f"[DeviceScanner]             Checking subdirectory: {subdir_name}")
                            try:
                                # Access subdirectory
                                subfolder = shell.Namespace(item.Path)
                                if subfolder:
                                    subitems = subfolder.Items()
                                    subfolders = [si.Name for si in subitems if si.IsFolder]
                                    if "DCIM" in subfolders:
                                        has_dcim_in_subdir = True
                                        print(f"[DeviceScanner]             ✓ Found DCIM in: {subdir_name}/DCIM")
                                        break
                            except Exception as e:
                                print(f"[DeviceScanner]             ERROR accessing {subdir_name}: {e}")

            if has_dcim_in_subdir:
                print(f"[DeviceScanner]           Has DCIM in subdirectory: True")

            if not has_dcim and not has_dcim_in_subdir:
                print(f"[DeviceScanner]           REJECTED: No DCIM found in Shell namespace")
                return None

            # Device detected! Extract device label from path
            # Shell path format: ::{GUID}\?\usb#vid_04e8&pid_6860#...
            device_label = "Mobile Device"
            try:
                # Try to get friendly name from folder
                device_label = folder.Title or folder.Self.Name or "Mobile Device"
            except:
                # Fallback: parse from path
                if "samsung" in shell_path.lower():
                    device_label = "Samsung Device"
                elif "iphone" in shell_path.lower() or "apple" in shell_path.lower():
                    device_label = "iPhone"

            print(f"[DeviceScanner]           Device label: {device_label}")

            # Detect device type
            device_type = "android"
            if "iphone" in shell_path.lower() or "apple" in shell_path.lower():
                device_type = "ios"

            print(f"[DeviceScanner]           Device type: {device_type}")

            # Build device folders list using COM
            print(f"[DeviceScanner]           Scanning for media folders via COM...")
            folders_list = self._scan_shell_namespace_folders(shell, shell_path, device_type)
            print(f"[DeviceScanner]           Found {len(folders_list)} media folder(s)")

            if not folders_list:
                print(f"[DeviceScanner]           REJECTED: No media folders found")
                return None

            # Extract device ID from Shell path
            device_id = f"windows_shell:{hash(shell_path) & 0xFFFFFFFF:08x}"
            print(f"[DeviceScanner]           Device ID: {device_id}")

            # Register device if database available
            if self.db and self.register_devices:
                try:
                    self.db.register_device(
                        device_id=device_id,
                        device_name=device_label,
                        device_type=device_type,
                        serial_number=None,
                        volume_guid=None
                    )
                    print(f"[DeviceScanner]           ✓ Registered in database")
                except Exception as e:
                    print(f"[DeviceScanner]           WARNING: Database registration failed: {e}")

            print(f"[DeviceScanner]           ✓✓✓ DEVICE ACCEPTED: {device_label} ({device_type})")

            return MobileDevice(
                label=device_label,
                root_path=shell_path,
                folders=folders_list,
                device_id=device_id,
                device_type=device_type,
                is_mtp=True
            )

        except ImportError:
            print(f"[DeviceScanner]           ✗ win32com not available for Shell namespace access")
            return None
        except Exception as e:
            print(f"[DeviceScanner]           ✗ Shell namespace check failed: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _scan_shell_namespace_folders(self, shell, root_path: str, device_type: str) -> List[DeviceFolder]:
        """
        Scan Shell namespace path for media folders using COM API.

        Args:
            shell: Shell.Application COM object
            root_path: Shell namespace path
            device_type: Device type (android, ios, camera)

        Returns:
            List of DeviceFolder objects
        """
        folders = []

        # Use appropriate patterns based on device type
        if device_type == "camera":
            patterns = self.CAMERA_PATTERNS
        elif device_type == "ios":
            patterns = self.IOS_PATTERNS
        else:  # android
            patterns = self.ANDROID_PATTERNS

        print(f"[DeviceScanner]             Using {len(patterns)} folder patterns for {device_type}")

        # For each pattern, try to access via Shell namespace
        for pattern in patterns:
            try:
                # Build full path
                pattern_windows = pattern.replace('/', '\\')
                full_path = f"{root_path}\\{pattern_windows}"

                # Try to access this folder
                folder_obj = shell.Namespace(full_path)
                if folder_obj:
                    # Folder exists! Count media files
                    items = folder_obj.Items()
                    media_count = 0

                    # Quick count of image/video files
                    for item in items:
                        if not item.IsFolder:
                            name_lower = item.Name.lower()
                            if any(name_lower.endswith(ext) for ext in [
                                '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.heic',
                                '.mp4', '.mov', '.avi', '.mkv', '.m4v', '.3gp'
                            ]):
                                media_count += 1

                    if media_count > 0:
                        display_name = self._get_folder_display_name(pattern)
                        if display_name:
                            print(f"[DeviceScanner]               ✓ Found folder: {display_name} ({media_count} files)")
                            folders.append(DeviceFolder(
                                name=display_name,
                                path=full_path,
                                photo_count=media_count
                            ))
            except Exception as e:
                # Folder doesn't exist or can't be accessed
                continue

        return folders

    def _check_device_at_path(self, root_path: str) -> Optional[MobileDevice]:
        """
        Check if a path contains a mobile device by looking for DCIM folder
        or other common camera/media folder structures.

        Handles both regular file system paths and Windows Shell namespace paths.

        Args:
            root_path: Path to check (drive, volume, mount point, or Shell namespace)

        Returns:
            MobileDevice if detected, None otherwise
        """
        print(f"[DeviceScanner]           Checking path: {root_path}")

        # Check if this is a Windows Shell namespace path (starts with ::)
        is_shell_path = root_path.startswith("::")

        if is_shell_path:
            print(f"[DeviceScanner]           Detected Shell namespace path")
            return self._check_shell_namespace_device(root_path)

        # Regular file system path handling
        root = Path(root_path)

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
        # Also handles cases like D:\My Phone\Samsung A23\DCIM
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
                        "internal", "storage", "phone", "card", "sdcard", "shared", "samsung",
                        "galaxy", "android", "device", "mobile", "iphone", "apple"
                    ]):
                        print(f"[DeviceScanner]             Checking subdirectory: {subdir.name}")

                        # Check for DCIM at this level
                        if (subdir / "DCIM").exists() and (subdir / "DCIM").is_dir():
                            has_dcim_in_subdir = True
                            print(f"[DeviceScanner]             ✓ Found DCIM in: {subdir.name}/DCIM")
                            break

                        # Check one level deeper (for nested device folders)
                        try:
                            for nested in subdir.iterdir():
                                if not nested.is_dir():
                                    continue
                                print(f"[DeviceScanner]               Checking nested: {subdir.name}/{nested.name}")
                                if (nested / "DCIM").exists() and (nested / "DCIM").is_dir():
                                    has_dcim_in_subdir = True
                                    print(f"[DeviceScanner]               ✓ Found DCIM in: {subdir.name}/{nested.name}/DCIM")
                                    # Update root to point to the actual device folder
                                    # This is hacky but necessary for nested structures
                                    root = nested
                                    break
                            if has_dcim_in_subdir:
                                break
                        except (PermissionError, OSError):
                            pass

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

        # Option A: Friendly names for comprehensive Android folder patterns
        # Map full patterns to user-friendly display names
        pattern_names = {
            # Primary camera
            "DCIM/Camera": "Camera",
            "DCIM": "Camera Roll",
            "Camera": "Camera",

            # User photos
            "Pictures": "Pictures",
            "Photos": "Photos",

            # Screenshots
            "DCIM/Screenshots": "Screenshots",
            "Pictures/Screenshots": "Screenshots",
            "Screenshots": "Screenshots",

            # WhatsApp
            "WhatsApp/Media/WhatsApp Images": "WhatsApp Images",
            "WhatsApp/Media/WhatsApp Video": "WhatsApp Videos",

            # Telegram
            "Telegram/Telegram Images": "Telegram Images",
            "Telegram/Telegram Video": "Telegram Videos",

            # Instagram
            "Pictures/Instagram": "Instagram",
            "Instagram": "Instagram",

            # Downloads
            "Download": "Downloads",
            "Downloads": "Downloads",

            # Movies/Videos
            "Movies": "Movies",
            "DCIM/Video": "Videos",

            # Social media
            "Snapchat/Media": "Snapchat",
            "TikTok": "TikTok",

            # Samsung structures
            "Internal shared storage/DCIM/Camera": "Camera",
            "Internal shared storage/DCIM": "Camera Roll",
            "Internal shared storage/Pictures": "Pictures",

            # Cloud sync
            "Google Photos": "Google Photos",
            "OneDrive/Pictures": "OneDrive",

            # Messaging apps
            "Facebook/Media": "Facebook",
            "Messenger/Media": "Messenger",
            "Signal/Media": "Signal",
        }

        # Check if exact pattern match exists
        if pattern in pattern_names:
            return pattern_names[pattern]

        # iOS/Apple devices
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

        # Default: return folder name
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
