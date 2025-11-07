# Schema v3.0.0 Audit Report
**Date:** 2025-11-07
**Branch:** main
**Status:** ⚠️ HYBRID IMPLEMENTATION - Needs Fixes

---

## Executive Summary

The main branch has a **HYBRID implementation** problem:
- ✅ **Schema v3.0.0** is correctly defined with `project_id` columns
- ❌ **Query logic** still uses v2.0.0 junction table approach
- ❌ **Missing project_id parameter** in several critical methods

This causes:
1. Folders not filtering correctly by project
2. Complex JOINs when simple WHERE clauses would work
3. Performance issues (unnecessary joins through project_images)

---

## Schema Status: ✅ CORRECT

### repository/schema.py

**Version:** 3.0.0 ✅
**Line 22:** `SCHEMA_VERSION = "3.0.0"` ✅

**photo_folders table (Lines 142-151):**
```sql
CREATE TABLE IF NOT EXISTS photo_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    parent_id INTEGER NULL,
    project_id INTEGER NOT NULL,  ✅ CORRECT
    FOREIGN KEY(parent_id) REFERENCES photo_folders(id),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,  ✅ CORRECT
    UNIQUE(path, project_id)  ✅ CORRECT
);
```

**photo_metadata table (Lines 154-175):**
```sql
CREATE TABLE IF NOT EXISTS photo_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    folder_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,  ✅ CORRECT
    ...
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,  ✅ CORRECT
    UNIQUE(path, project_id)  ✅ CORRECT
);
```

**Indexes (Lines 219, 224):**
```sql
CREATE INDEX IF NOT EXISTS idx_photo_folders_project ON photo_folders(project_id);  ✅ CORRECT
CREATE INDEX IF NOT EXISTS idx_photo_metadata_project ON photo_metadata(project_id);  ✅ CORRECT
```

**Result:** Schema definition is 100% correct for v3.0.0.

---

## Query Logic Status: ❌ INCORRECT

### reference_db.py - Critical Issues

#### 🔴 Issue 1: get_all_folders() - Lines 385-412

**Current Implementation (WRONG):**
```python
if project_id is not None:
    cur.execute("""
        SELECT DISTINCT pf.id, pf.parent_id, pf.path, pf.name
        FROM photo_folders pf
        INNER JOIN photo_metadata pm ON pf.id = pm.folder_id
        INNER JOIN project_images pi ON pm.path = pi.image_path  ❌ WRONG!
        WHERE pi.project_id = ?
        ...
    """, (project_id,))
```

**Problem:**
- Uses v2.0.0 junction table logic
- Complex 3-table JOIN when simple WHERE clause would work
- Performance issue: joins through project_images table

**Should Be:**
```python
if project_id is not None:
    cur.execute("""
        SELECT id, parent_id, path, name
        FROM photo_folders
        WHERE project_id = ?  ✅ CORRECT
        ORDER BY parent_id IS NOT NULL, parent_id, name
    """, (project_id,))
```

---

#### 🔴 Issue 2: get_child_folders() - Lines 1130-1179

**Current Implementation (WRONG):**
```python
if project_id is not None:
    if parent_id is None:
        cur.execute("""
            SELECT DISTINCT pf.id, pf.name
            FROM photo_folders pf
            INNER JOIN photo_metadata pm ON pf.id = pm.folder_id
            INNER JOIN project_images pi ON pm.path = pi.image_path  ❌ WRONG!
            WHERE pf.parent_id IS NULL AND pi.project_id = ?
            ...
        """, (project_id,))
```

**Should Be:**
```python
if project_id is not None:
    if parent_id is None:
        cur.execute("""
            SELECT id, name
            FROM photo_folders
            WHERE parent_id IS NULL AND project_id = ?  ✅ CORRECT
            ORDER BY name
        """, (project_id,))
```

---

#### 🔴 Issue 3: get_images_by_folder() - Lines 1204-1236

**Current Implementation (MISSING project_id):**
```python
def get_images_by_folder(self, folder_id: int, include_subfolders: bool = True):
    ...
    query = f"SELECT path FROM photo_metadata WHERE folder_id IN ({placeholders}) ORDER BY path"
    # ❌ NO project_id filtering!
```

**Problem:**
- Returns ALL photos from folder regardless of project
- No project_id parameter accepted
- Will show photos from wrong projects

**Should Be:**
```python
def get_images_by_folder(self, folder_id: int, include_subfolders: bool = True, project_id: int | None = None):
    ...
    if project_id is not None:
        query = f"SELECT path FROM photo_metadata WHERE folder_id IN ({placeholders}) AND project_id = ? ORDER BY path"
        cur.execute(query, folder_ids + [project_id])  ✅ CORRECT
```

---

#### 🔴 Issue 4: Other Methods Using project_images JOIN

**Total instances:** 11 locations use `INNER JOIN project_images`

All need to be changed to direct `WHERE project_id = ?` filtering:

1. Line 404: get_all_folders()
2. Line 1151: get_child_folders() (parent IS NULL case)
3. Line 1160: get_child_folders() (parent = ? case)
4. Line 1770: (need to check method name)
5. Line 1886: (need to check method name)
6. Line 1926: (need to check method name)
7. Line 2157: (need to check method name)
8. Line 2194: (need to check method name)
9. Line 2226: (need to check method name)
10. Line 2254: (need to check method name)
11. Line 2674: (need to check method name)

---

## Methods Needing project_id Parameter

### reference_db.py

1. ❌ **get_images_by_folder()** - Line 1204
   - Missing `project_id` parameter
   - Current: Returns ALL photos in folder
   - Needed: Filter by project_id

2. ❌ **count_photos_in_folder()** - Line 1238
   - Missing `project_id` parameter
   - Current: Counts ALL photos in folder
   - Needed: Count only project's photos

3. ❌ **get_folder_photo_count()** - Line 1254
   - Missing `project_id` parameter
   - Current: Counts ALL photos in folder
   - Needed: Count only project's photos

4. ❌ **get_descendant_folder_ids()** - (need to find)
   - May need project_id parameter
   - Used by get_images_by_folder

---

## UI Layer Status

### sidebar_qt.py - Needs Audit

**Questions to Answer:**
1. Does `_load_folders()` pass `project_id` to `get_all_folders()`?
2. Does `_add_folder_items()` pass `project_id` to `get_child_folders()`?
3. Does grid loading pass `project_id` to `get_images_by_folder()`?
4. Are photo counts using project-filtered queries?

---

## Fix Priority

### Critical (Must Fix for v3.0.0)

1. **get_all_folders()** - Replace 3-table JOIN with direct WHERE
2. **get_child_folders()** - Replace 3-table JOIN with direct WHERE
3. **get_images_by_folder()** - Add project_id parameter and filtering
4. **All 11 instances of project_images JOIN** - Replace with direct project_id filtering

### High Priority

5. **count_photos_in_folder()** - Add project_id parameter
6. **get_folder_photo_count()** - Add project_id parameter
7. **get_descendant_folder_ids()** - Add project_id parameter if needed
8. **sidebar_qt.py** - Ensure all calls pass project_id

### Medium Priority

9. Audit remaining methods for project_id filtering
10. Check repository layer (photo_repository.py, folder_repository.py)
11. Check service layer (photo_scan_service.py)

---

## Expected Performance Improvement

After fixing to use direct `WHERE project_id = ?`:

- **Query complexity:** O(1) lookup vs O(n) join
- **Index usage:** Direct index on photo_folders.project_id
- **Speed improvement:** 10-100x faster for large databases
- **Code clarity:** Simple WHERE clause vs complex 3-table JOIN

---

## Recommendation

**Action Plan:**
1. Fix all methods in reference_db.py (11 locations)
2. Add project_id parameters to methods that need it
3. Audit sidebar_qt.py and other UI components
4. Test with multi-project database
5. Commit all changes as "Implement true Schema v3.0.0 with direct project_id filtering"
6. Push to main branch

**Risk:** Low - All changes are straightforward replacements
**Testing:** Create test with 2 projects, verify isolation
**Rollback:** Keep backup of current main branch

---

**End of Audit Report**
