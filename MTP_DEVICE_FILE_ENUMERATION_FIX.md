# MTP Device File Enumeration Fix

## Problem
Mobile devices (Samsung Galaxy A23) were detected successfully and showed folders in sidebar, but displayed "Camera (0 files)" and "Pictures (0 files)" instead of showing the actual photo count.

## Root Cause
Two separate issues were identified:

### Issue 1: Photo Count Display (FIXED)
In `services/device_sources.py`, the `_scan_portable_storage_folders()` method was:
1. Performing a quick scan and finding media files (incrementing `media_count`)
2. BUT then hardcoding `estimated_count = 0` instead of using the discovered count
3. This made the sidebar display "Camera (0 files)" even when photos existed

**Fix**: Changed line 511 to use `photo_count=media_count` instead of `photo_count=0`
- Now shows approximate count from quick scan (e.g., "Camera (10 files)")
- Indicates files ARE present without expensive full enumeration
- Actual count determined when folder is opened/imported

### Issue 2: Folder Click Handler (FIXED)
In `sidebar_qt.py`, the `_on_item_clicked()` method's device_folder handler was:
1. Receiving Windows Shell namespace paths (e.g., `::{GUID}\\...` for MTP devices)
2. Trying to use regular Python `Path()` operations on these COM object paths
3. `Path(shell_path).exists()` always returned False for Shell namespace paths
4. Result: "Device folder not accessible" error

**Fix**: Updated the device_folder click handler (lines 1959-2091) to:
- Detect Shell namespace paths (start with `::`)
- Use Windows Shell COM API for MTP device enumeration
- Keep regular Path operations for Linux/Mac file system mounts
- Enumerate media files recursively via COM for MTP devices
- Show helpful message directing users to Import feature

## Changes Made

### File 1: `services/device_sources.py`
- Line 507: Updated log message to show actual media count
- Line 511: Changed from `photo_count=0` to `photo_count=media_count`

### File 2: `sidebar_qt.py`
- Lines 1959-2091: Completely rewrote device_folder click handler
- Added Shell namespace path detection
- Added COM API-based enumeration for MTP devices
- Maintained backward compatibility for regular file system paths

## Testing
User reported:
- ✅ Device detection works in seconds (not 30+)
- ✅ Appears in sidebar correctly
- ✅ Shows Camera and Pictures folders
- ✅ No freeze/hang issues

Expected improvements with this fix:
- ✅ Sidebar now shows approximate file counts (e.g., "Camera (10 files)")
- ✅ Clicking folders enumerates files via COM API
- ✅ Users get helpful feedback about files found

## Technical Notes

### Windows MTP Architecture
- MTP devices use Shell namespace paths, not regular file system paths
- Format: `::{20D04FE0-3AEA-1069-A2D8-08002B30309D}\\\\?\\usb#...`
- Must use `Shell.Application` COM interface to access
- Cannot use Python's `Path.exists()`, `Path.iterdir()`, etc.

### Performance Optimization
- Quick scan checks only first 10 files per folder during detection
- Prevents freeze during device scan (100+ patterns × 100+ files)
- Full enumeration happens when user clicks folder
- Professional apps (Lightroom, Photos) use similar approach

## Next Steps
Potential future enhancements:
1. Implement actual MTP file preview in grid (currently shows message)
2. Cache MTP file thumbnails for faster browsing
3. Add progress indicator for large folder enumeration
4. Optimize COM enumeration performance further
