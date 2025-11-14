# Video Counts & Branches - Root Cause Analysis & Complete Fix

## Problem Summary

Videos are indexed with dates but **NOT showing in sidebar date tree** and **counts are zero**.

---

## Root Causes Identified

### 1. ❌ Videos Not Added to Branches System
**File**: `reference_db.py`
**Problem**: `build_date_branches()` only processes photos, NOT videos

```python
# Line 2271: build_date_branches() - PHOTOS ONLY
def build_date_branches(self, project_id: int):
    # Populates project_images table with photos
    cur.execute("SELECT path FROM photo_metadata WHERE project_id = ?", (project_id,))
    # ...
    cur.execute("INSERT OR IGNORE INTO project_images ...")
```

**Missing**: `build_video_date_branches()` to populate `project_videos` table

**Impact**: Videos have dates, but sidebar can't find them in branches

---

### 2. ❌ Video Branches Never Built After Scan
**File**: `main_window_qt.py:387`
**Problem**: Only calls `build_date_branches()` for photos after scan

```python
# Line 387: PHOTOS ONLY
branch_count = db.build_date_branches(current_project_id)
```

**Missing**: Call to build video branches after video indexing

**Impact**: `project_videos` table empty, sidebar shows zero videos

---

### 3. ❌ Sidebar Shows Photo Counts Only
**File**: `sidebar_qt.py:1972`
**Problem**: Counts only query photos, not videos

```python
# Line 1972: Gets video years but...
video_years = self.db.list_video_years_with_counts(self.project_id)
```

This queries `video_metadata.created_year` correctly, BUT:
- The counts come from videos in database
- However, when user CLICKS on a year, it tries to load from `project_videos`
- `project_videos` is empty because branches were never built!

**Impact**: Counts might show, but clicking fails OR counts are zero

---

### 4. ❌ Date Click Handlers Load from Branches (Empty for Videos)
**File**: `sidebar_qt.py:1517-1540`
**Problem**: When user clicks video year, it should load videos from that year

**Current flow**:
1. User clicks "2024" under Videos
2. Handler calls `VideoService.filter_by_date(videos, year=2024)`
3. This filters ALL videos in project by year
4. BUT if videos weren't added to branches, might load wrong data

**Actually this part might work**, but branches are needed for consistency

---

## Architecture Gap

### Photos (WORKING):
1. Scan → photos indexed with created_date/created_year ✓
2. `build_date_branches()` called after scan ✓
3. Populates `project_images` table with photo paths ✓
4. Sidebar queries created_year → finds photos in branches ✓
5. User clicks year → loads from project_images ✓

### Videos (BROKEN):
1. Scan → videos indexed with created_date/created_year ✓ (my fix)
2. **NO build_video_date_branches() called** ❌
3. **project_videos table stays empty** ❌
4. Sidebar queries created_year → finds videos in database ✓
5. **User clicks year → tries to load from project_videos (empty!)** ❌

---

## Complete Fix Required

### Fix 1: Create build_video_date_branches() Method
**File**: `reference_db.py`
**Location**: After `build_date_branches()` (around line 2380)

```python
def build_video_date_branches(self, project_id: int):
    """
    Build branches for each created_date value in video_metadata.
    Similar to build_date_branches() but for videos.

    Populates project_videos table with video paths organized by date.

    Args:
        project_id: The project ID to associate videos with
    """
    print(f"[build_video_date_branches] Using project_id={project_id}")

    with self._connect() as conn:
        cur = conn.cursor()

        # Verify project exists
        cur.execute("SELECT id FROM projects WHERE id = ?", (project_id,))
        if not cur.fetchone():
            print(f"[build_video_date_branches] ERROR: Project {project_id} not found!")
            return 0

        # Get all videos with dates
        cur.execute("""
            SELECT path FROM video_metadata
            WHERE project_id = ? AND created_date IS NOT NULL
        """, (project_id,))
        all_video_paths = [r[0] for r in cur.fetchall()]
        print(f"[build_video_date_branches] Found {len(all_video_paths)} videos with dates")

        # Ensure 'all' branch exists for videos
        cur.execute("""
            INSERT OR IGNORE INTO branches (project_id, branch_key, display_name)
            VALUES (?,?,?)
        """, (project_id, "videos:all", "🎬 All Videos"))

        # Insert all videos into 'all' branch
        for video_path in all_video_paths:
            cur.execute("""
                INSERT OR IGNORE INTO project_videos (project_id, branch_key, video_path)
                VALUES (?,?,?)
            """, (project_id, "videos:all", video_path))

        # Get unique dates from video_metadata
        cur.execute("""
            SELECT DISTINCT created_date
            FROM video_metadata
            WHERE project_id = ? AND created_date IS NOT NULL
            ORDER BY created_date DESC
        """, (project_id,))
        dates = [r[0] for r in cur.fetchall()]
        print(f"[build_video_date_branches] Found {len(dates)} unique video dates")

        # Create branch for each date
        n_total = 0
        for date_str in dates:
            branch_key = f"videos:by_date:{date_str}"

            # Ensure branch exists
            cur.execute("""
                INSERT OR IGNORE INTO branches (project_id, branch_key, display_name)
                VALUES (?,?,?)
            """, (project_id, branch_key, f"📹 {date_str}"))

            # Get videos for this date
            cur.execute("""
                SELECT path FROM video_metadata
                WHERE project_id = ? AND created_date = ?
            """, (project_id, date_str))
            video_paths = [r[0] for r in cur.fetchall()]

            # Insert videos into branch
            for video_path in video_paths:
                cur.execute("""
                    INSERT OR IGNORE INTO project_videos (project_id, branch_key, video_path)
                    VALUES (?,?,?)
                """, (project_id, branch_key, video_path))

            n_total += len(video_paths)

        print(f"[build_video_date_branches] Added {n_total} videos to date branches")
        return n_total
```

### Fix 2: Call build_video_date_branches() After Scan
**File**: `main_window_qt.py`
**Location**: Line 387 (after `build_date_branches()`)

```python
# EXISTING (line 387):
branch_count = db.build_date_branches(current_project_id)

# ADD THIS (new line ~388):
video_branch_count = db.build_video_date_branches(current_project_id)
print(f"[MainWindow] Built {branch_count} photo branches, {video_branch_count} video branches")
```

### Fix 3: Ensure Sidebar Loads Videos from Branches
**File**: `sidebar_qt.py`
**Location**: Around line 1517-1540 (video year click handler)

**Check current implementation**: Does it load from branches or filter all videos?

If it loads from branches, update to use video branches:
```python
elif mode == "videos_year" and value:
    # Load videos from branch instead of filtering all
    videos = self.db.get_videos_by_branch(self.project_id, f"videos:by_date:{value}")
    paths = [v for v in videos]  # Already paths from branch
```

---

## Testing Checklist

After implementing fixes:

- [ ] Scan directory with videos
- [ ] Check main_window_qt.py calls build_video_date_branches()
- [ ] Check project_videos table has entries:
  ```sql
  SELECT COUNT(*) FROM project_videos WHERE project_id = 1;
  ```
- [ ] Check sidebar shows video years with counts > 0
- [ ] Click on video year → should show videos
- [ ] Check branches table has video branches:
  ```sql
  SELECT * FROM branches WHERE branch_key LIKE 'videos:%';
  ```

---

## Implementation Order

1. **Add build_video_date_branches() to reference_db.py** (30 lines)
2. **Call it in main_window_qt.py after scan** (1 line)
3. **Test video counts show in sidebar**
4. **Verify clicking works**

---

**Status**: Design complete, ready to implement
**Estimated Time**: 20 minutes
**Risk**: LOW (follows existing photo pattern)
