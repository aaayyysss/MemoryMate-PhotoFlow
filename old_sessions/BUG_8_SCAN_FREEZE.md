# BUG #8: App Freeze at 3% During Photo Scan

**Priority**: 🔴 CRITICAL
**Severity**: Application completely freezes, requires force quit
**Introduced By**: BUG #7 fix (commit 7cac5f7)

## Problem Description

App freezes with black screen at 3% progress during photo scanning. No error messages, complete UI freeze.

## Root Cause

**BUG #7 changed** `extract_basic_metadata()` to `extract_metadata()` to get created_* fields.

**The Problem**:
1. `extract_metadata()` is much heavier - calls `Image.open()` which can HANG indefinitely on corrupted files
2. When `Image.open()` hangs, calling `future.cancel()` does NOT kill the thread
3. Hung threads stay in ThreadPoolExecutor, eating up workers
4. With max_workers=8, after 8 files hang, ALL workers are blocked → DEADLOCK
5. Main thread waits forever for executor results → UI FREEZE

**ThreadPoolExecutor Limitation**:
- `future.cancel()` only prevents getting results
- Does NOT kill the running thread
- Thread continues consuming resources in background
- After enough hangs, thread pool is exhausted → freeze

## Code Analysis

**services/photo_scan_service.py line 459:**
```python
# BUG #7 changed this line:
future = executor.submit(self.metadata_service.extract_metadata, str(file_path))
# From:
future = executor.submit(self.metadata_service.extract_basic_metadata, str(file_path))
```

**Difference**:
- `extract_basic_metadata()`: Fast, simple, ~20ms per file
- `extract_metadata()`: Heavy, full EXIF, ~100-500ms per file, CAN HANG

**services/metadata_service.py line 110:**
```python
with Image.open(file_path) as img:  # <-- CAN HANG INDEFINITELY
    self._extract_dimensions(img, metadata)
    self._extract_exif(img, metadata)
```

## Solution

**Compute created_* fields from date_taken AFTER extraction** instead of during scan.

This avoids the heavy `extract_metadata()` call during scanning while still populating the fields.

### Implementation

1. **Keep using fast `extract_basic_metadata()` during scan**
2. **Add `_compute_created_fields_from_date()` helper** to compute created_* from date_taken
3. **Update scan service** to compute these fields inline without calling extract_metadata()

