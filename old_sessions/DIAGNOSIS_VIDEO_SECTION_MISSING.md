# Diagnosis: Video Section Missing from Sidebar

## Date: 2025-11-12
## Issue: Video section not appearing in sidebar despite video files present

---

## 🔍 ANALYSIS OF DEBUG LOG

### What the Log Shows

1. **FFmpeg/FFprobe Available**: ✅
   ```
   ✅ FFmpeg and FFprobe detected - full video support enabled
   ```

2. **Video Playback Working**: ✅
   ```
   [LightboxDialog] Loading video: d:\my phone\videos\نورا رحال، طاهر مامللي،، حبذا(480p).mp4
   [LightboxDialog] Video playback started
   [LightboxDialog] Video duration: 2:18
   ```

3. **Sidebar Built**: ✅
   ```
   [Sidebar] Creating fresh model (avoiding Qt segfault)
   [Sidebar] Loaded 31 folder counts in batch
   [Sidebar] starting async count population for 1 branch targets
   ```

4. **Video Section Message**: ❌ MISSING
   ```
   Expected: "[Sidebar] Added 🎬 Videos section with 97 videos and filters."
   Actual: NO MESSAGE AT ALL
   ```

5. **VideoDateHierarchy**: ❌ MISSING
   ```
   Expected: "[VideoDateHierarchy] Building: X years, Y months, Z videos"
   Actual: NO MESSAGE AT ALL
   ```

---

## 🚨 ROOT CAUSE

### The Issue

The video section code exists in `sidebar_qt.py:1708-2003` but **line 1723 condition fails**:

```python
videos = video_service.get_videos_by_project(self.project_id)  # Returns []
if videos:  # ❌ FALSE - section not shown
    # Build video section...
```

### Why Videos List is Empty

The query `SELECT * FROM video_metadata WHERE project_id = ?` returns **no rows**.

This means one of:

1. **Videos Not Scanned Yet**
   - User has video files on disk
   - But they haven't run a scan to index them into database
   - `video_metadata` table exists but is empty

2. **Database Was Reset**
   - Earlier session showed "[VideoDateHierarchy] Building: 6 years, 31 months, 97 videos"
   - But current database has no videos
   - Possible causes:
     - Database file deleted/reset
     - Using different database file (wrong path)
     - Schema migration cleared data

3. **Wrong Project ID**
   - Videos indexed under different project_id
   - Current project_id has no videos
   - Unlikely if user only has one project

---

## 🔧 DIAGNOSTIC IMPROVEMENTS MADE

### Added Detailed Logging

**File**: `sidebar_qt.py:1712-1715`

```python
print(f"[Sidebar] Loading videos for project_id={self.project_id}")
videos = video_service.get_videos_by_project(self.project_id) if self.project_id else []
total_videos = len(videos)
print(f"[Sidebar] Found {total_videos} videos in project {self.project_id}")
```

**What This Will Show**:
- Confirms video loading is attempted
- Shows project_id being queried
- Shows how many videos found (0 = explains missing section)
- Helps diagnose if exception is thrown

### Added Exception Traceback

**File**: `sidebar_qt.py:1717-1719`

```python
except Exception as e:
    print(f"[Sidebar] Failed to load videos: {e}")
    import traceback
    traceback.print_exc()
```

**What This Will Show**:
- Full traceback if VideoService import fails
- Full traceback if database query fails
- Helps diagnose unexpected errors

---

## 🧪 HOW TO DIAGNOSE (For User)

### Step 1: Check New Log Output

After pulling latest code, restart app and check log for:

```
[Sidebar] Loading videos for project_id=1
[Sidebar] Found 0 videos in project 1    ← If 0, videos not indexed
```

### Step 2: Check Database Directly

**Create diagnostic script** `check_video_count.py`:

```python
#!/usr/bin/env python3
"""Check if videos exist in database."""

from repository.video_repository import VideoRepository

repo = VideoRepository()

# Check total videos
all_videos = repo.get_by_project(project_id=1)
print(f"Total videos in project 1: {len(all_videos)}")

if all_videos:
    print("\nSample videos:")
    for v in all_videos[:5]:
        print(f"  - {v['path']}")
        print(f"    created_date: {v.get('created_date')}")
        print(f"    duration: {v.get('duration_seconds')}s")
else:
    print("\n⚠️  No videos found in database!")
    print("Videos need to be scanned first.")
    print("\nTo scan:")
    print("1. Open app")
    print("2. File → Scan for Media")
    print("3. Select folder with videos")
    print("4. Wait for scan to complete")
```

**Run**:
```bash
python check_video_count.py
```

### Step 3: Check Database File Exists

```bash
# Check if database exists
ls -lh reference_data.db

# Check file size (should be > 0)
# If 0 bytes, database is empty/corrupted
```

### Step 4: Check Which Database is Being Used

**Add logging to app startup**:

```python
# main_qt.py or main_window_qt.py
from db_config import get_db_path
print(f"[Startup] Using database: {get_db_path()}")
print(f"[Startup] Database exists: {os.path.exists(get_db_path())}")
print(f"[Startup] Database size: {os.path.getsize(get_db_path()) if os.path.exists(get_db_path()) else 0} bytes")
```

---

## 💡 LIKELY SOLUTIONS

### Solution 1: Scan Videos (Most Likely)

If `check_video_count.py` shows 0 videos:

1. **Open App**
2. **File → Scan for Media** (or equivalent menu)
3. **Select folder** containing videos (e.g., `D:\my phone\videos\`)
4. **Wait for scan** to complete
5. **Restart app** or refresh sidebar
6. **Check sidebar** → should now show 🎬 Videos section

### Solution 2: Re-scan if Database Was Reset

If videos were indexed before but aren't now:

1. Check if `reference_data.db` was deleted/reset
2. Re-scan all media folders
3. Videos will be re-indexed

### Solution 3: Check Database Path

If using custom database location:

1. Verify `db_config.py` points to correct file
2. Check if multiple database files exist
3. Ensure app is using the correct one

### Solution 4: Run Migration (If Schema Issue)

If `video_metadata` table doesn't exist:

```bash
python migrate_add_video_tables.py
```

---

## 📊 EXPECTED BEHAVIOR AFTER FIX

### Successful Video Section Display

```
[Sidebar] Loading videos for project_id=1
[Sidebar] Found 97 videos in project 1
[VideoDateHierarchy] Building: 2 years, 8 months, 97 videos    ← After date fix
[Sidebar] Added 🎬 Videos section with 97 videos and filters.
```

### Sidebar Structure

```
🌿 Branches
📅 Quick Dates
📁 Folders
  ├─ D:\my phone\videos\ (97)
  ├─ D:\my phone\videos\deutsch lernen\ (45)
  └─ ...
📅 By Date
  ├─ 2024 (52)
  └─ 2023 (45)
🏷️ Tags
🎬 Videos ← APPEARS!
  ├─ All Videos (97)
  ├─ ⏱️ By Duration
  │  ├─ Short < 30s (12)
  │  ├─ Medium 30s-5min (65)
  │  └─ Long > 5min (20)
  ├─ 📺 By Resolution
  │  ├─ SD < 720p (50)
  │  ├─ HD 720p (30)
  │  ├─ Full HD 1080p (15)
  │  └─ 4K 2160p+ (2)
  ├─ 🎞️ By Codec
  │  ├─ H.264 / AVC (80)
  │  ├─ H.265 / HEVC (15)
  │  └─ VP9 (2)
  ├─ 📦 By File Size
  │  ├─ Small < 100MB (20)
  │  ├─ Medium 100MB-1GB (50)
  │  ├─ Large 1GB-5GB (25)
  │  └─ XLarge > 5GB (2)
  └─ 📅 By Date
     ├─ 2024 (52)
     └─ 2023 (45)
👥 People
```

---

## 🔑 KEY POINTS

1. **Video section only shows if videos are indexed in database**
   - Not based on filesystem scan
   - Requires explicit media scan operation

2. **Earlier logs showed videos existed**
   - "[VideoDateHierarchy] Building: 6 years, 31 months, 97 videos"
   - Database may have been reset since then

3. **Video playback works independently**
   - Opening video files directly works
   - But sidebar requires database indexing

4. **Diagnostic logging added**
   - Will show project_id being queried
   - Will show video count returned
   - Will show full tracebacks on errors

---

## 📁 Files Changed

1. **sidebar_qt.py** (+4 lines)
   - Added diagnostic print statements
   - Added full exception traceback
   - Lines 1712, 1715, 1717-1719

---

## ✅ NEXT STEPS

1. **User pulls latest code**
2. **User runs app and checks log** for new diagnostic messages
3. **User runs `check_video_count.py`** to verify database state
4. **User scans videos** if database is empty
5. **Video section should appear** after scan completes

---

**Status**: Diagnostic improvements committed, awaiting user feedback
**Branch**: `claude/fix-database-path-inconsistency-011CV2nF45M3FtC6baF2SqaH`
**Commit**: Pending
