# Mobile Device Support - User Guide

Complete guide for handling Samsung Android and iPhone iOS devices in MemoryMate-PhotoFlow.

## Overview

The mobile device support provides **Photos-app-style** workflow for accessing and importing photos/videos from your phone:

- **Auto-detection**: Devices appear automatically in sidebar when connected
- **Browse mode**: View photos without importing (read-only access)
- **Smart import**: Import with duplicate detection (skip already imported photos)
- **Thumbnail preview**: See photos before importing
- **Selective import**: Choose which photos to import

---

## Setup & Configuration

### 1. Connect Your Device

#### For Samsung/Android:
1. Connect phone via USB cable
2. On phone: Enable "File Transfer" / "MTP" mode
   - Swipe down notification → Tap "USB charging" → Select "File transfer"
3. Device will mount and appear in sidebar automatically

#### For iPhone/iOS:
1. Connect iPhone via USB cable
2. On iPhone: Tap "Trust This Computer" when prompted
3. Device will mount (requires iTunes/Apple Mobile Device Support on Windows)
4. Device appears in sidebar automatically

### 2. No Additional Configuration Needed

The app automatically:
- Scans for DCIM folders (camera photos)
- Detects device type (Android vs iOS)
- Enumerates media folders (Camera, Screenshots, WhatsApp, etc.)
- Counts photos/videos in each folder

---

## Usage - Two Modes

### Mode 1: Browse (View Only)

**Quick browsing without importing:**

1. Navigate to sidebar → **📱 Mobile Devices**
2. Click on a device folder (e.g., "Camera")
3. Photos load in grid view (read-only from device)
4. Double-click photo to view in preview panel
5. No files copied to your computer

**When to use:** Quick preview, checking which photos are on device

**Limitations:** Cannot tag, cannot edit metadata, cannot face detect (files not in project)

---

### Mode 2: Import (Photos App Style)

**Import photos to your project:**

1. Navigate to sidebar → **📱 Mobile Devices**
2. **Right-click** on device folder → **📥 Import from this folder...**
3. Import dialog opens showing thumbnails:
   - ✅ **Green checkboxes**: New photos (auto-selected)
   - 🔘 **Grey photos with badge**: Already imported (auto-skipped)
4. Review selection:
   - Click **"Select All New"** - Import all new photos
   - Click **"Deselect All"** - Clear selection
   - Manually check/uncheck individual photos
5. Click **"Import X Selected"**
6. Progress bar shows import status
7. Photos copied to project directory with timestamp folder

**When to use:** Keep photos permanently, tag them, detect faces, organize in albums

**Benefits:**
- Duplicate detection (won't import same photo twice)
- Photos become part of your project
- Can tag, organize, search, detect faces
- Photos remain even after disconnecting device

---

## Import Dialog Features

### Smart Duplicate Detection

The app uses **SHA256 hash** to detect duplicates:

```
First import:  Photo123.jpg → Hash: abc123... → Imported ✓
Second import: Photo123.jpg → Hash: abc123... → Already imported (skipped)
```

**How it works:**
1. Calculates hash of device photo
2. Checks if hash exists in project database
3. Marks as "Already Imported" with badge
4. Auto-deselects imported photos
5. Only new photos selected by default

### Thumbnail Grid

**Layout:**
- 5 thumbnails per row (120x120 pixels)
- Checkbox + filename below each thumbnail
- Hover: Blue border highlights selectable photos
- Badge: "✓ Imported" shows on duplicates

**Interactions:**
- Click checkbox: Toggle selection
- Click thumbnail: No action (use checkbox)
- Hover over filename: Shows full filename in tooltip

### Import Statistics

After import completes, dialog shows:

```
Import completed!

Imported: 45
Skipped: 12  (already in library)
Failed: 0

[OK]
```

**What happens to imported photos:**
1. Copied to: `<project_folder>/imported_YYYYMMDD_HHMMSS/`
2. Registered in database with hash
3. Available in grid view immediately
4. Sidebar/grid refreshes automatically

---

## Device Tree Structure

Sidebar shows devices with folder hierarchy:

```
📱 Mobile Devices (3,963)
  ├─ 📱 Samsung Galaxy S22 (3,963)
  │  ├─ • Camera (2,423)
  │  ├─ • Screenshots (320)
  │  └─ • WhatsApp Images (1,220)
  └─ 📱 iPhone 14 Pro (3,540)
     └─ • Camera Roll (3,540)
```

**Counts:** Shows total photos in each folder (scanned on mount)

---

## Context Menu Options

### On Device Folder:
```
Right-click "Camera" folder:
  📥 Import from this folder...  ← Opens import dialog
  👁️ Browse (view only)          ← Browse without importing
  🔄 Refresh device              ← Re-scan device
```

### On Device Root:
```
Right-click "Samsung Galaxy S22":
  📱 Scan device for photos...   ← Import from entire device
  🔄 Refresh device list         ← Re-detect all devices
```

---

## Supported File Types

### Photos:
- `.jpg`, `.jpeg` - JPEG images
- `.png` - PNG images
- `.gif` - GIF images
- `.heic`, `.heif` - High Efficiency Image Format (iPhone)

### Videos:
- `.mp4` - MPEG-4 video
- `.mov` - QuickTime video (iPhone)
- `.avi` - AVI video
- `.mkv` - Matroska video
- `.webm`, `.m4v` - Web/mobile video

---

## Platform-Specific Notes

### Windows
- Android: Enable "File Transfer" mode on phone
- iPhone: Requires iTunes or Apple Mobile Device Support
- Devices mount as drives (D:, E:, F:, etc.)

### macOS
- Android: Enable "File Transfer" mode on phone
- iPhone: Works natively (no additional software needed)
- Devices mount in /Volumes/

### Linux
- Android: Enable "File Transfer" mode on phone
  - May require `mtp-tools` package: `sudo apt install mtp-tools`
- iPhone: Requires `libimobiledevice`: `sudo apt install libimobiledevice-utils`
- Devices mount in /media/<user>/ or /run/media/<user>/

---

## Troubleshooting

### Device Not Showing

**Check:**
1. USB cable properly connected
2. Phone unlocked
3. "File Transfer" / "MTP" mode enabled (Android)
4. "Trust This Computer" tapped (iPhone)
5. Device appears in file manager (Windows Explorer, Finder, Nautilus)

**Fix:** Disconnect and reconnect device, check USB mode

---

### Import Dialog Shows 0 Files

**Check:**
1. DCIM folder exists on device
2. Folder has photos (not empty)
3. Photos are supported format (JPG, PNG, HEIC, MP4, etc.)

**Fix:** Take a photo with phone camera, try again

---

### All Photos Show "Already Imported"

**Meaning:** All photos are already in your project (duplicates)

**To verify:**
- Open project folder → `imported_YYYYMMDD_HHMMSS/`
- Photos are already there from previous import

**Not a bug:** This is working correctly! Duplicate detection prevents re-importing same photos.

---

### Import Fails / Errors

**Common causes:**
1. Device disconnected during import
2. Phone locked/screen off
3. Insufficient disk space
4. Permission errors

**Fix:**
- Keep phone unlocked during import
- Check disk space: `df -h` (Linux/macOS) or `dir` (Windows)
- Reconnect device and retry

---

## Keyboard Shortcuts

(Currently none - click-based workflow)

Future enhancement: Space = toggle selection, Ctrl+A = select all

---

## Performance Tips

### For Large Imports (1000+ photos):

1. **Import in batches**: Import folder by folder (Camera, Screenshots, etc.)
2. **Selective import**: Deselect old photos you don't need
3. **Keep phone unlocked**: Prevents connection timeout
4. **Close other apps**: Free up memory for thumbnail generation

### For Faster Scanning:

- Device scan is automatic and optimized
- Max depth: 3 levels (prevents scanning entire phone)
- Thumbnail generation: On-demand in import dialog

---

## Advanced: Manual Import

If automatic detection fails, you can manually browse device:

**Linux/macOS:**
```bash
# Find mount point
mount | grep mtp    # Android
mount | grep mobile # iPhone

# Example: /run/media/user/Samsung-S22/DCIM/Camera
```

Then in app:
1. Browse grid → Open folder manually
2. Copy paths to import dialog (future enhancement)

---

## Database Schema

### How Duplicates Are Tracked

```sql
-- photo_metadata table
CREATE TABLE photo_metadata (
    ...
    file_hash TEXT,  -- SHA256 hash for dedup
    ...
);

CREATE INDEX idx_photo_metadata_hash ON photo_metadata(file_hash);
```

**Import process:**
1. Calculate hash: `SHA256(file_bytes)`
2. Query: `SELECT COUNT(*) WHERE file_hash = ?`
3. If count > 0: Mark as "Already Imported"
4. If count = 0: Copy file and insert with hash

---

## FAQ

**Q: Do I need to import photos to use them?**
A: No, you can browse directly from device. But import is required for:
   - Face detection
   - Tagging
   - Organizing into albums
   - Keeping photos after disconnecting device

**Q: What happens to imported photos?**
A: They're copied to `<project_folder>/imported_YYYYMMDD_HHMMSS/` and registered in database. Original photos remain on device.

**Q: Can I import from SD card?**
A: Yes! If SD card has DCIM folder, it will be detected as a device.

**Q: Can I import from multiple devices at once?**
A: Yes, each device appears separately. Import from each one individually.

**Q: Does import preserve EXIF data?**
A: Yes, `shutil.copy2()` preserves all metadata including EXIF, timestamps, etc.

**Q: What if I import the same photo twice?**
A: Duplicate detection prevents this. Second import shows "Already Imported" badge.

**Q: Can I delete photos from device after import?**
A: Not from within app. Use your phone's file manager or Photos app to delete.

**Q: Will this work with digital cameras?**
A: Yes! Any device with DCIM folder (SD cards, DSLRs, action cameras) will be detected.

---

## Comparison to macOS Photos App

| Feature | Photos App | MemoryMate |
|---------|-----------|------------|
| Auto-detect devices | ✅ | ✅ |
| Thumbnail preview | ✅ | ✅ |
| Duplicate detection | ✅ | ✅ (SHA256 hash) |
| Select/deselect | ✅ | ✅ |
| Import progress | ✅ | ✅ |
| Browse without import | ❌ | ✅ (unique feature!) |
| Context menu | ❌ | ✅ |
| Face detection after import | ✅ | ✅ |
| iCloud sync | ✅ | ❌ (future) |

---

## Changelog

### v4.0.0 (Current)
- ✅ Device auto-detection (Android, iPhone, SD cards)
- ✅ Photos-app-style import dialog
- ✅ Smart duplicate detection (SHA256)
- ✅ Thumbnail previews in import dialog
- ✅ Selective import with checkboxes
- ✅ Browse mode (view without importing)
- ✅ Context menu integration
- ✅ Progress bar with status
- ✅ Database migration for file_hash

### Future Enhancements:
- ⏳ Live device monitoring (detect connection/disconnection)
- ⏳ Import queue (schedule imports)
- ⏳ Smart albums (auto-organize by date/location)
- ⏳ Cloud sync (Google Photos, iCloud)

---

## Support

For issues or feature requests:
- GitHub: https://github.com/aaayyysss/MemoryMate-PhotoFlow/issues
- Check debug log: `Debug-Log` file in project root

---

**Happy importing! 📱➡️💻**
