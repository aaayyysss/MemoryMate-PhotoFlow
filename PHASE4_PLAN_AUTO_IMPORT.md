# Phase 4: Auto-Import Workflows - Implementation Plan
**Date:** 2025-11-18
**Status:** Planning
**Branch:** `claude/add-mobile-device-support-015nbJPrbBVS98KbQaL31rpw`

---

## 📋 Overview

Phase 4 adds **automated import workflows** to reduce manual work when connecting devices. Users can set devices to auto-import, monitor for new devices, and get notified when imports complete.

---

## 🎯 Goals

1. **Reduce Manual Steps**: Users shouldn't need to manually import every time
2. **User Control**: Auto-import should be opt-in per device
3. **Non-Blocking**: Imports run in background without freezing UI
4. **Awareness**: Users know when imports happen via notifications
5. **Flexibility**: Support both automatic and manual workflows

---

## 📦 Feature Breakdown

### **Phase 4A: Device Auto-Import Preferences** ⭐ Priority 1
**What:** Per-device setting to enable/disable auto-import

**Implementation:**
1. Add `auto_import` column to `mobile_devices` table (default: FALSE)
2. Add `auto_import_folder` column to specify which folder to auto-import from
3. Add UI in device context menu: "Enable Auto-Import" / "Disable Auto-Import"
4. Store user preference in database

**User Experience:**
- Right-click device in sidebar → "Enable Auto-Import from Camera"
- Next time device connects, auto-import triggered for that folder
- User can disable per device: "Disable Auto-Import"

**Database Schema:**
```sql
ALTER TABLE mobile_devices ADD COLUMN auto_import BOOLEAN DEFAULT 0;
ALTER TABLE mobile_devices ADD COLUMN auto_import_folder TEXT DEFAULT NULL;
ALTER TABLE mobile_devices ADD COLUMN last_auto_import TIMESTAMP DEFAULT NULL;
```

---

### **Phase 4B: Manual "Import New" Button** ⭐ Priority 1
**What:** Quick button in sidebar to import new files without opening dialog

**Implementation:**
1. Add "Import New Files" button to device context menu
2. Runs incremental import in background (only new files)
3. Shows progress notification
4. Skips duplicates by default

**User Experience:**
- Right-click device → "Import New Files"
- Progress bar appears in sidebar
- Toast notification: "Imported 15 new photos from iPhone"
- No dialog shown (uses smart defaults)

**Benefits:**
- Faster than opening full dialog
- Uses Phase 2 incremental scan (only new files)
- Uses Phase 3 duplicate detection (skips cross-device dups)

---

### **Phase 4C: Background Import with Progress** ⭐ Priority 2
**What:** Import runs in background thread with real-time progress

**Implementation:**
1. Enhance existing `DeviceImportWorker` for non-blocking import
2. Add progress bar widget to sidebar (per device)
3. Update sidebar in real-time during import
4. Allow user to cancel long imports

**User Experience:**
- Import starts → Progress bar appears below device
- "Importing... 15/342 files (4%)"
- User can continue working in app
- When done: "✓ Imported 15 files"

**Technical:**
- Already have `DeviceImportWorker` (Qt QRunnable)
- Just need to add progress signals to sidebar
- Update device_files table as import progresses

---

### **Phase 4D: Toast Notifications** ⭐ Priority 2
**What:** Non-intrusive notifications for import completion

**Implementation:**
1. Create simple `ToastNotification` widget (Qt QWidget)
2. Slide in from bottom-right corner
3. Auto-dismiss after 5 seconds
4. Click to view import details

**User Experience:**
- Import completes → Toast appears
- "✓ Imported 15 photos from iPhone"
- Click → Opens project folder or device history
- Fades out after 5s

**Design:**
```
╔══════════════════════════════════╗
║ ✓ Import Complete               ║
║ 15 photos from iPhone 14 Pro    ║
║ Skipped 3 duplicates             ║
╚══════════════════════════════════╝
```

---

### **Phase 4E: Device Connection Monitoring** ⚠️ Priority 3 (Optional)
**What:** Automatically detect when devices are connected

**Implementation:**
- **Linux**: Use `pyudev` to monitor udev events
- **Windows**: Use `wmi` to monitor USB device changes
- **macOS**: Use `pyobjc` for IOKit notifications

**Challenges:**
- OS-specific code (requires platform detection)
- Additional dependencies (`pyudev`, `wmi`, `pyobjc`)
- Permissions (may require root/admin on some OSes)
- Testing complexity (need multiple devices/OSes)

**Decision:** **DEFER to Phase 5** (too complex for Phase 4)

**Alternative:** Manual "Refresh Devices" button in sidebar
- User clicks to re-scan for devices
- Much simpler, no OS dependencies
- Still useful for detecting newly connected devices

---

## 🗺️ Implementation Roadmap

### **Milestone 1: Device Preferences** (1-2 hours)
- [ ] Add `auto_import` columns to mobile_devices table
- [ ] Create migration script for schema update
- [ ] Add database methods: `set_device_auto_import()`, `get_device_auto_import()`
- [ ] Update device context menu in sidebar
- [ ] Add "Enable Auto-Import" / "Disable Auto-Import" menu items
- [ ] Show auto-import status in device tooltip

### **Milestone 2: Quick Import Button** (1-2 hours)
- [ ] Add "Import New Files" to device context menu
- [ ] Implement background import without dialog
- [ ] Use smart defaults (skip duplicates, new only, Camera folder)
- [ ] Show progress in sidebar
- [ ] Update device history after import

### **Milestone 3: Progress Indicators** (1 hour)
- [ ] Create progress bar widget for sidebar
- [ ] Connect import worker signals to progress bar
- [ ] Show "Importing... X/Y files" text
- [ ] Add cancel button to progress bar
- [ ] Clean up progress bar when done

### **Milestone 4: Notifications** (1 hour)
- [ ] Create `ToastNotification` widget
- [ ] Position in bottom-right corner
- [ ] Auto-dismiss after 5 seconds
- [ ] Click handler to show details
- [ ] Queue multiple notifications (if needed)

### **Milestone 5: Polish & Testing** (1 hour)
- [ ] Test with multiple devices
- [ ] Test auto-import workflow end-to-end
- [ ] Test progress bar with large imports
- [ ] Test notifications
- [ ] Update documentation

---

## 🎨 UI Mockups

### **Device Context Menu (Phase 4A & 4B):**
```
┌─────────────────────────────────┐
│ iPhone 14 Pro                   │
│ 🟢 Last import: 2 hours ago     │
├─────────────────────────────────┤
│ ▶ Import from this folder...    │ (existing)
│ ⚡ Import New Files (Quick)     │ (NEW - Phase 4B)
│ ──────────────────────────────  │
│ ✓ Enable Auto-Import            │ (NEW - Phase 4A, checked if enabled)
│   └─ Auto-import folder: Camera │ (sub-menu)
│ ──────────────────────────────  │
│ 📊 View Import History          │
│ 🔄 Refresh Device               │ (NEW - manual re-scan)
└─────────────────────────────────┘
```

### **Sidebar Progress Indicator (Phase 4C):**
```
📱 Devices
  📱 iPhone 14 Pro 🟢
    ├─ 📁 Camera
    ├─ 📁 Screenshots
    └─ [▓▓▓▓▓░░░░░] Importing 15/342 (4%)  [✕ Cancel]
```

### **Toast Notification (Phase 4D):**
```
Bottom-right corner:
┌──────────────────────────────────────┐
│ ✓ Import Complete                    │
│ 15 photos from iPhone 14 Pro         │
│ Skipped 3 cross-device duplicates    │
│                             [Dismiss] │
└──────────────────────────────────────┘
```

---

## 🔧 Technical Implementation

### **Database Schema Changes:**

```sql
-- Add auto-import preferences to mobile_devices
ALTER TABLE mobile_devices ADD COLUMN auto_import BOOLEAN DEFAULT 0;
ALTER TABLE mobile_devices ADD COLUMN auto_import_folder TEXT DEFAULT NULL;
ALTER TABLE mobile_devices ADD COLUMN last_auto_import TIMESTAMP DEFAULT NULL;
ALTER TABLE mobile_devices ADD COLUMN auto_import_enabled_date TIMESTAMP DEFAULT NULL;

-- Index for quick auto-import device lookup
CREATE INDEX IF NOT EXISTS idx_mobile_devices_auto_import
ON mobile_devices(auto_import) WHERE auto_import = 1;
```

### **New Database Methods (reference_db.py):**

```python
def set_device_auto_import(self, device_id: str, enabled: bool, folder: str = None):
    """Enable or disable auto-import for device"""
    with self._connect() as conn:
        if enabled:
            conn.execute("""
                UPDATE mobile_devices
                SET auto_import = 1,
                    auto_import_folder = ?,
                    auto_import_enabled_date = CURRENT_TIMESTAMP
                WHERE device_id = ?
            """, (folder, device_id))
        else:
            conn.execute("""
                UPDATE mobile_devices
                SET auto_import = 0,
                    auto_import_folder = NULL
                WHERE device_id = ?
            """, (device_id,))

def get_device_auto_import_status(self, device_id: str) -> dict:
    """Get auto-import settings for device"""
    with self._connect() as conn:
        cur = conn.execute("""
            SELECT auto_import, auto_import_folder, last_auto_import
            FROM mobile_devices
            WHERE device_id = ?
        """, (device_id,))
        row = cur.fetchone()
        if row:
            return {
                'enabled': bool(row[0]),
                'folder': row[1],
                'last_import': row[2]
            }
    return {'enabled': False, 'folder': None, 'last_import': None}

def get_auto_import_devices(self) -> list[dict]:
    """Get all devices with auto-import enabled"""
    with self._connect() as conn:
        cur = conn.execute("""
            SELECT device_id, device_name, auto_import_folder, mount_point
            FROM mobile_devices
            WHERE auto_import = 1
            ORDER BY device_name
        """)
        return [
            {
                'device_id': row[0],
                'device_name': row[1],
                'auto_import_folder': row[2],
                'mount_point': row[3]
            }
            for row in cur.fetchall()
        ]
```

### **Quick Import Function (services/device_import_service.py):**

```python
def quick_import_new_files(
    self,
    device_folder_path: str,
    root_path: str,
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> dict:
    """
    Quick import: Import only new files with smart defaults.

    Smart defaults:
    - Incremental scan (new files only)
    - Skip cross-device duplicates
    - Import to root folder
    - Create import session

    Returns:
        Stats dict with imported/skipped/failed counts
    """
    # Start session
    session_id = self.start_import_session(import_type="auto")

    # Scan for new files only
    new_files = self.scan_incremental(device_folder_path, root_path)

    # Filter out cross-device duplicates
    files_to_import = [
        f for f in new_files
        if not f.is_cross_device_duplicate
    ]

    # Import files
    stats = self.import_files(
        files_to_import,
        destination_folder_id=None,
        progress_callback=progress_callback
    )

    # Complete session
    self.complete_import_session(session_id, stats)

    return stats
```

### **Toast Notification Widget (ui/toast_notification.py):**

```python
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QRect
from PySide6.QtGui import QFont

class ToastNotification(QWidget):
    """Non-intrusive notification widget"""

    def __init__(self, title: str, message: str, parent=None, duration: int = 5000):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.duration = duration

        # Setup UI
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)

        title_label = QLabel(title)
        title_label.setFont(QFont("", 11, QFont.Bold))

        message_label = QLabel(message)
        message_label.setWordWrap(True)

        layout.addWidget(title_label)
        layout.addWidget(message_label)

        # Styling
        self.setStyleSheet("""
            QWidget {
                background-color: #2d2d2d;
                color: white;
                border-radius: 8px;
                border: 1px solid #444;
            }
        """)

        # Auto-dismiss timer
        QTimer.singleShot(duration, self.fade_out)

    def show_notification(self):
        """Show notification with slide-in animation"""
        # Position in bottom-right corner
        screen = self.screen().geometry()
        self.move(screen.width() - self.width() - 20, screen.height() - self.height() - 20)

        # Fade in
        self.setWindowOpacity(0)
        self.show()

        # Animate opacity
        animation = QPropertyAnimation(self, b"windowOpacity")
        animation.setDuration(300)
        animation.setStartValue(0)
        animation.setEndValue(1)
        animation.start()

    def fade_out(self):
        """Fade out and close"""
        animation = QPropertyAnimation(self, b"windowOpacity")
        animation.setDuration(300)
        animation.setStartValue(1)
        animation.setEndValue(0)
        animation.finished.connect(self.close)
        animation.start()
```

---

## 📊 User Workflows

### **Workflow 1: Enable Auto-Import**

1. User right-clicks "iPhone 14 Pro" in sidebar
2. Selects "Enable Auto-Import"
3. Sub-menu appears: "Camera", "Screenshots", "All Folders"
4. User selects "Camera"
5. Database updated: `auto_import=1, auto_import_folder="Camera"`
6. Tooltip now shows: "Auto-import: Camera ✓"

### **Workflow 2: Quick Import**

1. User connects iPhone (has new photos)
2. Right-clicks "iPhone 14 Pro" → "Import New Files"
3. Background import starts:
   - Scans Camera folder incrementally
   - Finds 15 new files
   - Skips 3 cross-device duplicates
   - Imports 12 files
4. Progress bar shows: "Importing 1/12... 2/12... 3/12..."
5. Toast notification: "✓ Imported 12 photos from iPhone"
6. Device history updates with new session

### **Workflow 3: Auto-Import (Future)**

1. User enables auto-import for device
2. Device connects (detected by OS monitor)
3. App automatically triggers quick import
4. Import runs in background
5. Toast notification when complete
6. User continues working, no interruption

---

## 🧪 Testing Plan

### **Test 1: Enable/Disable Auto-Import**
- Enable auto-import for device
- Verify database updated correctly
- Verify tooltip shows auto-import status
- Disable auto-import
- Verify database cleared

### **Test 2: Quick Import with New Files**
- Connect device with 20 new photos
- Click "Import New Files"
- Verify only new files imported
- Verify cross-device duplicates skipped
- Verify import session created

### **Test 3: Quick Import with No New Files**
- Connect device with no new files
- Click "Import New Files"
- Verify notification: "No new files to import"
- Verify no import session created

### **Test 4: Progress Bar Display**
- Start import with 100+ files
- Verify progress bar shows in sidebar
- Verify progress updates in real-time
- Verify cancel button works

### **Test 5: Toast Notifications**
- Complete import
- Verify toast appears in bottom-right
- Verify toast auto-dismisses after 5s
- Verify multiple toasts queue correctly

---

## ⚖️ Decisions

### **Decision 1: Auto-Import Trigger**
**Options:**
- A) OS-level device detection (complex, OS-specific)
- B) Manual "Import New Files" button (simple, user-initiated)
- C) Periodic polling for new devices (battery drain)

**Decision:** Start with **B (Manual button)** in Phase 4
- Defer A to Phase 5 (too complex)
- Avoid C (bad UX, battery impact)

### **Decision 2: Auto-Import Defaults**
**Question:** What to import by default in quick import?

**Decision:**
- Only new files (Phase 2 incremental)
- Skip cross-device duplicates (Phase 3)
- Import to root folder
- Use Camera folder if available, else prompt

### **Decision 3: Notification Style**
**Options:**
- A) OS native notifications (platform-specific)
- B) In-app toast widgets (cross-platform)
- C) Status bar messages (less visible)

**Decision:** **B (Toast widgets)** for Phase 4
- Cross-platform
- No external dependencies
- More control over styling
- Can defer to OS notifications later

---

## 📝 Phase 4 Success Criteria

- [  ] Users can enable/disable auto-import per device
- [  ] Users can specify which folder to auto-import from
- [  ] "Import New Files" button works in device context menu
- [  ] Quick import uses smart defaults (new only, skip dups)
- [  ] Progress bar shows during import in sidebar
- [  ] Toast notifications appear on import completion
- [  ] All imports tracked in import sessions
- [  ] Device history updates correctly
- [  ] No UI freezing during imports
- [  ] User can cancel long-running imports

---

## 🎉 Summary

Phase 4 focuses on **reducing manual work** while maintaining **user control**:

1. **Device Preferences**: Enable auto-import per device
2. **Quick Import**: One-click import with smart defaults
3. **Background Import**: Non-blocking with progress
4. **Notifications**: Keep users informed without interruption

This brings MemoryMate-PhotoFlow closer to professional app behavior while being practical to implement.

**Next Phase (5):** Advanced features like OS-level device detection, import presets, and device groups.
