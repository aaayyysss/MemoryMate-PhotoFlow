# Video Date Workflow Audit - Complete Analysis

## Current State (BROKEN)

### Scan Phase (photo_scan_service.py:728-791)
```python
# Line 763-769: Videos indexed WITHOUT date fields
video_service.index_video(
    path=str(video_path),
    project_id=project_id,
    folder_id=folder_id,
    size_kb=size_kb,
    modified=modified  # ← Only modified, NO created_date/created_year!
)
```

**Problem**: Videos have NULL created_date/created_year after scan

### Background Worker Phase (video_metadata_worker.py:136-163)
```python
# Lines 150-159: Worker computes created_* from date_taken
if date_taken:
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    update_data['created_ts'] = int(dt.timestamp())
    update_data['created_date'] = date_str
    update_data['created_year'] = dt.year
```

**Problem**: This happens AFTER scan completes, AFTER sidebar builds tree

### Sidebar Building (sidebar_qt.py:1972-1992)
```python
# Line 1972: Gets video years from database
video_years = self.db.list_video_years_with_counts(self.project_id)

# But created_year is NULL for all videos!
# Result: video_years = [] (empty list)
```

**Problem**: Tree built before workers populate dates → no video years shown

---

## Timeline of Events (Current Flow)

1. **T+0s**: User starts scan
2. **T+60s**: Scan completes, videos indexed with NULL created_date/created_year
3. **T+60s**: Sidebar builds tree, queries created_year → finds nothing
4. **T+60s**: Background workers START (in separate thread)
5. **T+120s**: Workers finish extracting metadata, populate created_date/created_year
6. **T+120s**: User sees nothing in video date tree 😞
7. **User restarts app**
8. **After restart**: Sidebar queries created_year → finds dates ✓
9. **User sees video dates** ✓ (but required restart!)

---

## Root Cause Analysis

### Issue 1: Videos Indexed Without Dates
**File**: `services/photo_scan_service.py:763-769`
**Problem**: `index_video()` doesn't populate created_* fields during scan

**Why**: Video date extraction requires slow ffprobe (~2s per video), would block scan

**Impact**: Sidebar tree has no dates to show

### Issue 2: VideoService.index_video() Doesn't Accept Date Parameters
**File**: `services/video_service.py:169-216`
**Problem**: Method signature lacks created_ts/created_date/created_year parameters

**Current Signature**:
```python
def index_video(self, path, project_id, folder_id=None, size_kb=None, modified=None)
```

**Missing**: created_ts, created_date, created_year

### Issue 3: No Fallback to Modified Date
**File**: `services/photo_scan_service.py:760`
**Problem**: Modified date is captured but never used to compute created_* fields

**Photos do this**: Lines 576-589 compute created_* from date_taken OR leave NULL
**Videos should do**: Compute created_* from modified as fallback

### Issue 4: Sidebar Not Rebuilding After Workers Finish
**File**: `sidebar_qt.py:1972-1992`
**Problem**: Sidebar builds tree once after scan, never rebuilds when workers finish

**Current**: Build tree → workers populate dates → stale tree
**Needed**: Build tree → workers populate dates → rebuild tree OR use modified date

---

## Comparison: Photos vs Videos

| Aspect | Photos | Videos |
|--------|--------|--------|
| **Date extraction** | Fast EXIF (~0.01s) | Slow ffprobe (~2s) |
| **During scan** | Extract date_taken, compute created_* | Only capture modified |
| **created_* fields** | Populated immediately | NULL until workers finish |
| **Sidebar shows dates** | Yes, immediately | No, needs restart |
| **Background processing** | None needed for dates | Required for all metadata |

---

## Solution Design

### Option A: Quick Video Date Extraction (NOT RECOMMENDED)
Run ffprobe with short timeout during scan:
- **Pros**: Get real date_taken immediately
- **Cons**: Slows down scan significantly (2s × videos), might timeout

### Option B: Use Modified Date as Fallback ✅ (RECOMMENDED)
Populate created_* from modified date during scan, let workers update later:
- **Pros**: Fast, reliable, dates show immediately
- **Cons**: Initial dates might be file modified, not video creation (updated by workers)

**This is the best solution**: Users see dates immediately, accurate dates populated later.

---

## Implementation Plan

### Step 1: Add Helper Function
**File**: `services/photo_scan_service.py`
**Location**: Around line 460 (before _process_file)

```python
def _compute_created_fields(date_str: str = None, modified: str = None) -> tuple:
    """
    Compute created_ts, created_date, created_year from date or modified time.

    Args:
        date_str: Date string in YYYY-MM-DD or YYYY-MM-DD HH:MM:SS format
        modified: Modified timestamp in YYYY-MM-DD HH:MM:SS format (fallback)

    Returns:
        Tuple of (created_ts, created_date, created_year) or (None, None, None)
    """
    from datetime import datetime

    # Try parsing date_str first
    date_to_parse = date_str if date_str else modified

    if not date_to_parse:
        return (None, None, None)

    try:
        # Extract YYYY-MM-DD part
        date_only = date_to_parse.split(' ')[0]
        dt = datetime.strptime(date_only, '%Y-%m-%d')

        return (
            int(dt.timestamp()),  # created_ts
            date_only,             # created_date (YYYY-MM-DD)
            dt.year                # created_year
        )
    except (ValueError, AttributeError, IndexError) as e:
        logger.debug(f"Failed to parse date '{date_to_parse}': {e}")
        return (None, None, None)
```

### Step 2: Update Video Indexing
**File**: `services/photo_scan_service.py`
**Location**: Lines 757-770 (_process_videos method)

```python
# Get file stats
stat = os.stat(video_path)
size_kb = stat.st_size / 1024
modified = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))

# CRITICAL FIX: Compute created_* from modified date for immediate sidebar display
# Background workers will UPDATE these with proper date_taken from video metadata
created_ts, created_date, created_year = self._compute_created_fields(None, modified)

# Index video WITH date fields (using modified as fallback)
video_service.index_video(
    path=str(video_path),
    project_id=project_id,
    folder_id=folder_id,
    size_kb=size_kb,
    modified=modified,
    created_ts=created_ts,      # ← ADD
    created_date=created_date,  # ← ADD
    created_year=created_year   # ← ADD
)
```

### Step 3: Update VideoService.index_video()
**File**: `services/video_service.py`
**Location**: Lines 169-216

```python
def index_video(self, path: str, project_id: int, folder_id: int = None,
               size_kb: float = None, modified: str = None,
               created_ts: int = None,        # ← ADD
               created_date: str = None,      # ← ADD
               created_year: int = None) -> Optional[int]:  # ← ADD
    """
    Index a video file during scanning.

    Args:
        path: Video file path
        project_id: Project ID
        folder_id: Folder ID (optional)
        size_kb: File size in KB (optional)
        modified: Modified timestamp (optional)
        created_ts: Created timestamp (optional, for immediate date hierarchy)
        created_date: Created date YYYY-MM-DD (optional, for immediate date hierarchy)
        created_year: Created year (optional, for immediate date hierarchy)
    """
    # ... existing code ...

    # Create new video entry with pending status AND date fields
    video_id = self._video_repo.create(
        path=path,
        folder_id=folder_id,
        project_id=project_id,
        size_kb=size_kb,
        modified=modified,
        created_ts=created_ts,          # ← ADD
        created_date=created_date,      # ← ADD
        created_year=created_year,      # ← ADD
        metadata_status='pending',
        thumbnail_status='pending'
    )
```

### Step 4: Verify VideoRepository.create() Accepts Parameters
**File**: `repository/video_repository.py`
**Check**: Lines 115-170 (create method)

Should already accept **kwargs, so created_ts/created_date/created_year will be passed through.

### Step 5: Verify Background Worker Still Works
**File**: `workers/video_metadata_worker.py`
**Check**: Lines 136-163 (update logic)

Worker should still UPDATE with proper date_taken:
- Initial scan: created_* from modified date
- Worker updates: created_* from date_taken (overwrite modified date)

This is CORRECT behavior - fallback is replaced with accurate data.

---

## Expected Results After Fix

### Timeline (New Flow)

1. **T+0s**: User starts scan
2. **T+60s**: Scan completes, videos indexed with created_date/created_year FROM MODIFIED DATE
3. **T+60s**: Sidebar builds tree, queries created_year → finds dates ✓
4. **T+60s**: User sees video dates immediately in tree ✓
5. **T+60s**: Background workers START (in separate thread)
6. **T+120s**: Workers finish, UPDATE created_date/created_year with proper date_taken
7. **User can browse videos by date immediately, no restart needed** ✓

### What Users Will See

1. **During scan**: "Indexing 1000 videos..."
2. **After scan**: Video tree shows years/months immediately (using modified date)
3. **Background workers run**: "Extracting metadata for 1000 videos..."
4. **After workers**: Dates might shift slightly if date_taken differs from modified

### Date Accuracy

- **Initial**: Based on file modified time (usually accurate)
- **After workers**: Based on video metadata creation_time (more accurate)
- **Fallback chain**: date_taken → modified → NULL

---

## Testing Checklist

- [ ] Scan directory with videos
- [ ] Check sidebar IMMEDIATELY after scan → video dates visible
- [ ] Click on video year → shows videos from that year
- [ ] Click on video month → shows videos from that month
- [ ] Wait for workers to finish
- [ ] Dates should still be visible (might update slightly)
- [ ] Restart app → dates persist
- [ ] No errors in logs

---

## Files to Modify

1. **services/photo_scan_service.py**
   - Add `_compute_created_fields()` helper (new function ~20 lines)
   - Update `_process_videos()` to call helper and pass dates (~5 lines changed)

2. **services/video_service.py**
   - Update `index_video()` signature to accept created_* parameters (~3 lines)
   - Pass created_* to repository create() call (~3 lines)

3. **repository/video_repository.py**
   - Verify `create()` accepts **kwargs (should already work, no changes needed)

4. **workers/video_metadata_worker.py**
   - No changes needed, already updates created_* fields

---

## Estimated Time: 15 minutes

- Step 1: 5 min (add helper function)
- Step 2: 3 min (update video indexing)
- Step 3: 3 min (update VideoService)
- Step 4: 2 min (verify repository)
- Step 5: 2 min (test)

**Total**: ~15 minutes for complete fix

---

**Status**: Design complete, ready to implement
**Priority**: HIGH (user-reported, blocking feature)
**Risk**: LOW (fallback pattern, background workers still update)
