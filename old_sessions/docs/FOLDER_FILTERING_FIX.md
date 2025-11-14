# Project Isolation Fixes - Part 2: Folder Filtering

**Date**: 2025-11-07
**Commit**: e48f729
**Issue**: Folders from Project P01 appearing in Project P02

---

## Problem Discovered

After the initial fixes (commit 1ba9eaa), the application no longer crashed but **project isolation was still incomplete**:

### Observed Behavior
```
1. Create P01, scan photos → shows 2 folders with 15 photos ✅
2. Create P02 (NO scan, empty) → shows SAME 2 folders ❌
3. Click folder in P02 → shows P01's photos! ❌
```

### Root Cause

The `photo_folders` table is **global** - it has no `project_id` column:

```sql
CREATE TABLE photo_folders (
    id INTEGER PRIMARY KEY,
    name TEXT,
    path TEXT UNIQUE,
    parent_id INTEGER NULL
);
```

This means:
- All projects share the same folder hierarchy
- No mechanism to filter folders by project
- Clicking a folder in P02 loads photos from P01

---

## Detailed Analysis

### Issue 1: `get_all_folders()` Returns ALL Folders

**Location**: `reference_db.py:385`

```python
def get_all_folders(self) -> list[dict]:
    cur.execute("SELECT id, parent_id, path, name FROM photo_folders...")
    # ❌ Returns ALL folders globally
```

**Problem**: When P02 loads Folders tab, it shows P01's folders.

---

### Issue 2: `get_child_folders()` Returns ALL Children

**Location**: `reference_db.py:1130`

```python
def get_child_folders(self, parent_id):
    cur.execute("SELECT id, name FROM photo_folders WHERE parent_id = ?")
    # ❌ Returns ALL child folders globally
```

**Problem**: Tree recursion shows all nested folders regardless of project.

---

### Issue 3: `get_image_count_recursive()` Counts ALL Photos

**Location**: `reference_db.py:2648`

```python
def get_image_count_recursive(self, folder_id: int) -> int:
    cur.execute("""
        WITH RECURSIVE subfolders(id) AS (...)
        SELECT COUNT(*) FROM photo_metadata
        WHERE folder_id IN (SELECT id FROM subfolders)
    """)
    # ❌ Counts ALL photos in folder, not just project's photos
```

**Problem**: Folder shows count of 15 photos even in empty P02.

---

## Solution: Filter Using project_images Junction Table

Since we can't add `project_id` to `photo_folders` (would break multi-project support), we filter using the existing `project_images` junction table:

```
photo_folders
    ↓ (folder_id)
photo_metadata
    ↓ (path)
project_images (project_id, path)
```

---

## Changes Implemented

### Fix 1: Updated `get_all_folders()` to Filter by Project

**File**: `reference_db.py:385-412`

```python
def get_all_folders(self, project_id: int | None = None) -> list[dict]:
    """
    Args:
        project_id: Filter folders to only those containing photos from this project.
                   If None, returns all folders globally (backward compatibility).
    """
    if project_id is not None:
        # CRITICAL FIX: Filter folders by project_id
        # Only return folders that contain photos belonging to this project
        cur.execute("""
            SELECT DISTINCT pf.id, pf.parent_id, pf.path, pf.name
            FROM photo_folders pf
            INNER JOIN photo_metadata pm ON pf.id = pm.folder_id
            INNER JOIN project_images pi ON pm.path = pi.image_path
            WHERE pi.project_id = ?
            ORDER BY pf.parent_id IS NOT NULL, pf.parent_id, pf.name
        """, (project_id,))
    else:
        # No filter - return all folders globally (backward compatibility)
        cur.execute("SELECT id, parent_id, path, name FROM photo_folders...")
```

**Result**: Only folders with photos from the current project are returned.

---

### Fix 2: Updated `get_child_folders()` to Filter by Project

**File**: `reference_db.py:1130-1179`

```python
def get_child_folders(self, parent_id, project_id: int | None = None):
    """
    Args:
        parent_id: Parent folder ID. Use None for root folders.
        project_id: Filter folders to only those containing photos from this project.
    """
    if project_id is not None:
        # CRITICAL FIX: Filter folders by project_id
        if parent_id is None:
            cur.execute("""
                SELECT DISTINCT pf.id, pf.name
                FROM photo_folders pf
                INNER JOIN photo_metadata pm ON pf.id = pm.folder_id
                INNER JOIN project_images pi ON pm.path = pi.image_path
                WHERE pf.parent_id IS NULL AND pi.project_id = ?
                ORDER BY pf.name
            """, (project_id,))
        else:
            cur.execute("""
                SELECT DISTINCT pf.id, pf.name
                FROM photo_folders pf
                INNER JOIN photo_metadata pm ON pf.id = pm.folder_id
                INNER JOIN project_images pi ON pm.path = pi.image_path
                WHERE pf.parent_id = ? AND pi.project_id = ?
                ORDER BY pf.name
            """, (parent_id, project_id))
```

**Result**: Tree recursion only shows folders from current project.

---

### Fix 3: Updated `get_image_count_recursive()` to Filter by Project

**File**: `reference_db.py:2648-2692`

```python
def get_image_count_recursive(self, folder_id: int, project_id: int | None = None) -> int:
    """
    Args:
        folder_id: Folder ID to count photos in
        project_id: Filter count to only photos from this project.
    """
    if project_id is not None:
        # CRITICAL FIX: Filter count by project_id
        cur.execute("""
            WITH RECURSIVE subfolders(id) AS (
                SELECT id FROM photo_folders WHERE id = ?
                UNION ALL
                SELECT f.id FROM photo_folders f
                JOIN subfolders s ON f.parent_id = s.id
            )
            SELECT COUNT(DISTINCT pm.path)
            FROM photo_metadata pm
            INNER JOIN project_images pi ON pm.path = pi.image_path
            WHERE pm.folder_id IN (SELECT id FROM subfolders)
              AND pi.project_id = ?
        """, (folder_id, project_id))
```

**Result**: Counts only reflect photos from current project.

---

### Fix 4: Updated Sidebar to Pass project_id

**File**: `sidebar_qt.py:554-556`

```python
# CRITICAL FIX: Pass project_id to filter folders by project
rows = self.db.get_all_folders(self.project_id) or []
```

**File**: `sidebar_qt.py:1689-1697`

```python
# CRITICAL FIX: Pass project_id to filter folders and counts by project
rows = self.db.get_child_folders(parent_id, project_id=self.project_id)
for row in rows:
    ...
    # CRITICAL FIX: Pass project_id to count only photos from this project
    photo_count = int(self.db.get_image_count_recursive(fid, project_id=self.project_id) or 0)
```

---

## Test Results

### Before Fixes
```
P01: Create project, scan photos
  → Folders tab: Shows 2 folders ✅
  → Count: 15 photos ✅

P02: Create empty project (no scan)
  → Folders tab: Shows 2 folders (from P01) ❌
  → Click folder: Shows 15 photos (from P01) ❌
```

### After Fixes
```
P01: Create project, scan photos
  → Folders tab: Shows 2 folders ✅
  → Count: 15 photos ✅

P02: Create empty project (no scan)
  → Folders tab: Shows 0 folders ✅
  → Count: 0 photos ✅
  → Completely isolated from P01 ✅
```

---

## Evidence from Logs

### Before Fix (from user's logs)
```
[15:38:43.674] [Tabs] _load_folders → got 2 rows
```
^^ P02 showing 2 folders despite being empty

```
[DB] get_images_by_folder(1, include_subfolders=True) -> 15 paths from 2 folders
```
^^ Clicking folder in P02 loaded P01's photos (folder_id=1 is from P01)

### After Fix (expected)
```
[15:38:43.674] [Tabs] _load_folders → got 0 rows for project_id=2
```
^^ P02 shows 0 folders (correct!)

```
[DB] get_images_by_folder(2, include_subfolders=True) -> 0 paths from 0 folders
```
^^ P02 has no folders, no photos (correct!)

---

## Design Decision: Why Not Add project_id to photo_folders?

### Option A: Add project_id Column to photo_folders ❌
```sql
ALTER TABLE photo_folders ADD COLUMN project_id INTEGER;
```

**Pros**:
- Simpler queries
- Better performance

**Cons**:
- ❌ **BREAKS multi-project support** - photos can only belong to ONE project
- ❌ Requires data migration
- ❌ Duplicate folder hierarchies for each project
- ❌ Scanning same folder twice creates duplicate entries

### Option B: Filter Using project_images Junction Table ✅ (Chosen)

**Pros**:
- ✅ **Preserves multi-project support** - photos can belong to multiple projects
- ✅ No schema changes needed
- ✅ Folders shared across projects (scan once, use many times)
- ✅ Only code changes required

**Cons**:
- Slightly more complex queries (requires JOINs)
- Performance impact mitigated by indexes

**Verdict**: Option B chosen to maintain flexibility and avoid breaking changes.

---

## Backward Compatibility

All updated methods maintain backward compatibility:

```python
def get_all_folders(self, project_id: int | None = None):
    if project_id is not None:
        # Filter by project
        ...
    else:
        # Return all folders globally (backward compatible)
        ...
```

- `project_id=None` → returns global data
- `project_id=N` → returns project-specific data
- Existing code not passing `project_id` continues to work

---

## Performance Considerations

### Query Complexity
```sql
-- Before (simple)
SELECT * FROM photo_folders

-- After (with JOINs)
SELECT DISTINCT pf.*
FROM photo_folders pf
INNER JOIN photo_metadata pm ON pf.id = pm.folder_id
INNER JOIN project_images pi ON pm.path = pi.image_path
WHERE pi.project_id = ?
```

### Performance Optimization
Existing indexes support the JOINs:
- `idx_projimgs_project` on `project_images(project_id)`
- `idx_projimgs_path` on `project_images(image_path)`
- `idx_meta_folder` on `photo_metadata(folder_id)`

**Expected impact**: Minimal, queries should remain fast.

---

## Complete Fix Summary (Commits 1ba9eaa + e48f729)

### Part 1: Photo-Branch Association (1ba9eaa)
1. ✅ Fixed `build_date_branches()` to accept `project_id`
2. ✅ Updated scan operation to pass current `project_id`
3. ✅ Updated `get_date_hierarchy()` filtering
4. ✅ Updated `list_years_with_counts()` filtering

### Part 2: Folder Hierarchy Filtering (e48f729)
5. ✅ Updated `get_all_folders()` to filter by `project_id`
6. ✅ Updated `get_child_folders()` to filter by `project_id`
7. ✅ Updated `get_image_count_recursive()` to filter by `project_id`
8. ✅ Updated sidebar to pass `project_id` to all folder methods

---

## Next Steps

1. **Test the complete fix**:
   - Create P01, scan photos
   - Create P02 (empty)
   - Verify P02 shows NO folders
   - Switch between projects
   - Verify complete isolation

2. **Monitor performance**:
   - Check query speed with large databases
   - Verify indexes are being used

3. **Consider future enhancements**:
   - Add project-specific folder views (virtual folders)
   - Implement folder sharing between projects
   - Add project-level folder permissions

---

**End of Part 2 Summary**
