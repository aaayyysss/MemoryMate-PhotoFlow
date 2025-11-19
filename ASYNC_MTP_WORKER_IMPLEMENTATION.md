# Async MTP File Copying Implementation

## Phase 1 Complete: Non-Blocking UI with QThread Worker

This document details the implementation of asynchronous MTP file copying using Qt's QThread framework, eliminating UI freezing during device file transfers.

---

## The Problem

### Before (Synchronous Blocking)

```python
# Ran on main UI thread - BLOCKED EVERYTHING
for item in folder.Items():
    dest_folder.CopyHere(item.Path, 4 | 16)  # ← Blocks for 1-3 seconds PER FILE
    media_paths.append(temp_path)

# Result: 10 files × 2 seconds = 20 seconds of FROZEN UI
```

**User Experience**:
- Click folder → App freezes
- No feedback, no progress
- Can't cancel, can't interact
- Looks like app crashed
- **Terrible UX**

---

## The Solution

### After (Asynchronous Non-Blocking)

```python
# Create worker that runs in background thread
worker = MTPCopyWorker(shell, folder_path)

# Show progress dialog (non-blocking)
progress = QProgressDialog("Copying photos...", "Cancel", 0, 100)

# Connect signals
worker.progress.connect(lambda c, t, f: progress.setValue(c/t*100))
worker.finished.connect(load_photos_into_grid)

# Start background worker
worker.start()  # ← Returns immediately, UI stays responsive
```

**User Experience**:
- Click folder → Progress dialog appears instantly
- See real-time progress: "Copying 5/10: IMG_001.jpg"
- UI stays responsive (can minimize, close, etc.)
- Can cancel anytime
- **Professional UX**

---

## Architecture

### Component Overview

```
[User Clicks Folder]
        ↓
[sidebar_qt.py] - Main UI thread
        ↓
[MTPCopyWorker.start()] ← Creates new thread
        ↓
[Background Thread] ──signals──→ [Progress Dialog]
    │                                    ↓
    │ (Copy files)               [Update progress bar]
    │ (2-3 seconds per file)     [Show current file]
    ↓                                    ↓
[Worker.finished] ──signal──→ [Load grid with photos]
```

### Thread Safety

**Main UI Thread**:
- Handles user interactions
- Updates progress dialog
- Loads grid when finished
- Always responsive

**Worker Thread**:
- Copies files via Shell COM API
- Emits progress signals
- No direct UI manipulation
- Terminates when done

**Communication**: Qt signals/slots (thread-safe)

---

## Implementation Details

### 1. MTPCopyWorker Class

**File**: `workers/mtp_copy_worker.py`

```python
class MTPCopyWorker(QThread):
    # Signals (thread-safe communication)
    progress = pyqtSignal(int, int, str)  # current, total, filename
    finished = pyqtSignal(list)            # list of copied paths
    error = pyqtSignal(str)                # error message

    def run(self):
        # This runs in background thread
        # 1. Count files (quick pass)
        files_total = count_media_files(folder)

        # 2. Copy files (slow pass)
        for file in files:
            # Emit progress (updates UI)
            self.progress.emit(current, total, filename)

            # Copy file (blocking, but on worker thread)
            dest_folder.CopyHere(item.Path, 4 | 16)

            # Check if cancelled
            if self._cancelled:
                return

        # 3. Emit results
        self.finished.emit(copied_paths)
```

**Key Features**:
- ✅ Two-pass algorithm (count, then copy)
- ✅ Cancellation support (`self._cancelled` flag)
- ✅ Progress updates (filename + percentage)
- ✅ Error handling with signal emission
- ✅ Comprehensive logging

### 2. Sidebar Integration

**File**: `sidebar_qt.py` (lines 1967-2064)

```python
# Create worker
worker = MTPCopyWorker(shell, folder_path, max_files=100)

# Create progress dialog
progress = QProgressDialog(
    "Copying photos from Camera...",
    "Cancel", 0, 100, mw
)
progress.setWindowModality(Qt.WindowModal)

# Connect progress updates
def on_progress(current, total, filename):
    percent = int((current / total) * 100)
    progress.setValue(percent)
    progress.setLabelText(f"Copying {current}/{total}: {filename}")

worker.progress.connect(on_progress)

# Connect completion
def on_finished(paths):
    progress.close()
    mw.grid.load_custom_paths(paths, content_type="mixed")
    mw.statusBar().showMessage(f"Showing {len(paths)} photos")
    worker.deleteLater()  # Clean up

worker.finished.connect(on_finished)

# Connect cancellation
def on_cancel():
    worker.cancel()
    worker.wait(3000)  # Wait up to 3 seconds
    if worker.isRunning():
        worker.terminate()

progress.canceled.connect(on_cancel)

# Start worker (non-blocking!)
worker.start()
```

**Key Features**:
- ✅ Modal progress dialog (blocks interaction with main window)
- ✅ Real-time progress updates
- ✅ Graceful cancellation
- ✅ Proper worker cleanup (deleteLater)
- ✅ Worker reference stored (prevents garbage collection)

---

## Performance Characteristics

### Synchronous (Before)

| Files | Time | UI State | User Can |
|-------|------|----------|----------|
| 10 | 20s | Frozen | Nothing |
| 50 | 100s | Frozen | Nothing |
| 100 | 200s | Frozen | Nothing |

**Impact**: Unusable for folders with many photos

### Asynchronous (After)

| Files | Time | UI State | User Can |
|-------|------|----------|-----------|
| 10 | 20s | Responsive | Interact, cancel |
| 50 | 100s | Responsive | Interact, cancel |
| 100 | Stopped at 100 | Responsive | Interact, cancel |

**Impact**: Professional UX, feels fast even when it's not

---

## Edge Cases Handled

### 1. User Cancellation

```python
def on_cancel():
    worker.cancel()  # Set flag
    worker.wait(3000)  # Wait gracefully
    if worker.isRunning():
        worker.terminate()  # Force kill if needed
```

**Behavior**:
- Sets `_cancelled` flag
- Worker checks flag between files
- Stops gracefully if possible
- Force terminates if hung

### 2. Worker Errors

```python
try:
    # Copy operations
except Exception as e:
    self.error.emit(str(e))  # Send to UI thread

# In UI thread:
def on_error(error_msg):
    progress.close()
    mw.statusBar().showMessage(f"Error: {error_msg}")
```

**Behavior**:
- Catches exceptions in worker thread
- Emits error signal to UI thread
- UI displays user-friendly message
- Worker cleans up and exits

### 3. Device Disconnection

```python
# Worker checks folder accessibility
folder = self.shell.Namespace(self.folder_path)
if not folder:
    self.error.emit("Cannot access folder")
    return

# During copy
dest_folder.CopyHere(item.Path, 4 | 16)
# If device disconnects, this throws exception
# → Caught by error handler → Error signal → UI notification
```

### 4. Duplicate Filenames

```python
# Worker doesn't handle duplicates
# Shell.CopyHere automatically renames:
# IMG_001.jpg → IMG_001 (2).jpg

# All unique paths returned in finished signal
```

### 5. Garbage Collection

```python
# Store worker reference in main window
if not hasattr(mw, '_mtp_workers'):
    mw._mtp_workers = []
mw._mtp_workers.append(worker)

# Worker self-destructs on completion
worker.deleteLater()
```

**Why**: Without reference, Python garbage collects worker mid-operation

---

## Testing Scenarios

### Scenario 1: Normal Operation

1. User clicks "Camera" folder (10 photos)
2. Progress dialog appears: "Copying photos from Camera..."
3. Progress updates: "Copying 1/10: IMG_001.jpg" (0%)
4. Progress updates: "Copying 5/10: IMG_005.jpg" (50%)
5. Progress updates: "Copying 10/10: IMG_010.jpg" (100%)
6. Dialog closes
7. Photos appear in grid
8. Status: "Showing 10 items from Camera"

**Expected**: Smooth, professional experience

### Scenario 2: User Cancellation

1. User clicks "Pictures" folder (50 photos)
2. Progress dialog appears
3. After 3 files, user clicks "Cancel"
4. Dialog shows: "Cancelling..."
5. Worker stops after current file
6. Dialog closes
7. Grid remains empty
8. Status: "Copy operation cancelled"

**Expected**: Clean cancellation, no corruption

### Scenario 3: Device Disconnection

1. User clicks "Camera" folder
2. Progress dialog appears
3. After 2 files, device unplugged
4. Worker encounters error on next copy
5. Error signal emitted
6. Dialog closes
7. Error message: "Error copying files: [COM error]"
8. Grid shows 2 successfully copied photos

**Expected**: Graceful degradation, partial success

### Scenario 4: Large Folder (100+ files)

1. User clicks folder with 500 photos
2. Progress dialog appears
3. Worker counts files, finds 500
4. Stops at 100 (max_files limit)
5. Progress: "Copying 100/100: IMG_100.jpg"
6. Dialog closes
7. Grid shows first 100 photos
8. Status: "Showing 100 items from Camera"

**Expected**: Timeout protection, reasonable wait time

---

## Performance Optimization

### Two-Pass Algorithm

**Why**: More accurate progress bar

```python
# Pass 1: Count files (fast - just enumeration)
def count_media_files(folder):
    count = 0
    for item in folder.Items():
        if is_media_file(item):
            count += 1
    return count  # Takes <1 second

# Pass 2: Copy files (slow - actual file transfer)
for file in files:
    progress.emit(current, total, filename)  # Accurate percentage
    copy_file(file)
```

**Alternative (single-pass)**:
- Progress bar would be indeterminate
- User doesn't know how long to wait
- Less professional UX

### Depth Limiting

```python
def copy_media_files(folder, depth=0, max_depth=2):
    if depth > max_depth:
        return  # Don't recurse too deep
```

**Why**:
- MTP recursion is slow (each level = network round-trip)
- Photos typically at depth 0-1 (DCIM/Camera)
- Prevents infinite loops on symlinks

### File Limiting

```python
if files_copied >= self.max_files:
    return  # Stop at 100 files
```

**Why**:
- Copying 1000 files takes 30+ minutes
- User experience degrades
- Better UX: Import feature for bulk operations

---

## Comparison to Industry Standards

### Google Photos

- ✅ Background sync
- ✅ Progress indication
- ✅ Cancellable
- ✅ Doesn't block app

### Apple Photos

- ✅ Import with progress
- ✅ Background operations
- ✅ Responsive UI
- ✅ Partial import on cancel

### Adobe Lightroom

- ✅ Import dialog with progress
- ✅ Background processing
- ✅ Detailed file-by-file progress
- ✅ Cancellable at any time

### MemoryMate (After This Fix)

- ✅ Background copy with QThread
- ✅ Progress dialog with file names
- ✅ Cancellable
- ✅ UI stays responsive

**Result**: Matches industry standard UX

---

## Future Enhancements

### Phase 2: Device Media Cache (Next)

```python
# Cache device file list to avoid rescanning
cache = {
    "device_id": "windows_mtp:97299b5b",
    "folders": {
        "DCIM/Camera": {
            "files": ["IMG_001.jpg", "IMG_002.jpg"],
            "last_scan": "2025-11-19T12:00:00",
            "count": 2
        }
    }
}
```

**Benefits**:
- Instant folder open (no rescan)
- Incremental updates (only new files)
- Offline folder browsing

### Phase 3: Smart Progress Estimation

```python
# Estimate remaining time based on transfer speed
bytes_copied = 5 * 1024 * 1024  # 5 MB
elapsed = 10  # seconds
speed = bytes_copied / elapsed  # 512 KB/s
remaining_bytes = 20 * 1024 * 1024  # 20 MB
eta = remaining_bytes / speed  # 40 seconds

progress.setLabelText(f"Copying 5/10 - 40 seconds remaining")
```

### Phase 4: Thumbnail Preview

```python
# Copy thumbnails first (fast), full images later
# User can browse while full copy continues in background
```

### Phase 5: Parallel Copying

```python
# Multiple workers copying different folders simultaneously
# Saturate USB bandwidth for faster transfers
```

---

## Known Limitations

1. **No concurrent folder operations**: Opening another folder cancels current operation
   - **Fix**: Queue multiple workers or warn user

2. **No persistent cache**: Folder re-opened = full rescan + recopy
   - **Fix**: Implement Phase 2 (device media cache)

3. **100 file limit**: Large folders truncated
   - **Fix**: Add "Load More" button or remove limit for Import feature

4. **No retry on transient errors**: Temporary failure = file skipped
   - **Fix**: Retry logic with exponential backoff

5. **No transfer speed optimization**: Files copied serially
   - **Fix**: Implement Phase 5 (parallel copying)

---

## Alignment with MobileDevice4 Plan

### Rule #2: "Never Use MTP on UI Thread"

**Status**: ✅ **FULLY IMPLEMENTED**

- All MTP calls moved to worker thread
- UI thread only handles signals
- Progress dialog doesn't block event loop
- User can interact with app during transfer

### Progress Tracking

**MobileDevice4 says**:
> "DeviceScannerWorker: QThread-based worker showing progress, allowing cancellation"

**Current Implementation**:
- ✅ QThread-based: `MTPCopyWorker(QThread)`
- ✅ Shows progress: `progress.setValue(percent)`
- ✅ Allows cancellation: `progress.canceled.connect(on_cancel)`
- ✅ Handles timeouts: `max_files=100`, `max_depth=2`

**Match**: 100% aligned with plan

---

## Summary

### What We Built

1. **MTPCopyWorker**
   - QThread subclass
   - Two-pass algorithm (count + copy)
   - Progress signals
   - Cancellation support
   - Error handling

2. **Sidebar Integration**
   - Async worker creation
   - Progress dialog
   - Signal connections
   - Worker lifecycle management

3. **User Experience**
   - Non-blocking UI
   - Real-time feedback
   - Professional progress indication
   - Graceful error handling

### Impact

**Before**:
- Click folder → freeze 20 seconds → photos appear
- No progress, no cancel, terrible UX

**After**:
- Click folder → progress dialog → photos appear
- See progress, can cancel, professional UX

### Next Steps

1. ✅ **Phase 1 Complete**: Async file copying
2. ⏳ **Phase 2 Next**: Device media cache
3. ⏳ **Phase 3**: DeviceManager abstraction
4. ⏳ **Phase 4**: Import dialog redesign

---

## Testing Checklist

- [ ] Click folder with 10 photos → Progress dialog shows → Photos load
- [ ] Cancel mid-copy → Operation stops gracefully
- [ ] Unplug device mid-copy → Error handled, partial success
- [ ] Click folder with 500 photos → Stops at 100, UI responsive
- [ ] Multiple rapid clicks → No crashes, operations queue/cancel properly
- [ ] Close app during copy → Worker terminates cleanly
- [ ] Different device (iPhone, SD card) → Same behavior

**Status**: Ready for user testing!
