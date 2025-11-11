# Project Isolation Fixes - Implementation Summary

**Date**: 2025-11-07
**Branch**: `claude/debug-project-crashes-architecture-011CUtbAQwXPFye7fhFiZJna`
**Issue**: Application crashes when creating projects, toggling views, and scanning photos

---

## 🎯 Problem Summary

The application crashed in 3 scenarios:
1. **Crash 1**: Creating a project (P01) and toggling between List and Tabs views
2. **Crash 2**: Creating multiple projects (P01, P02) and switching between them
3. **Crash 3**: Scanning photos in P02, which showed 0 photos after scan completion

**Root Cause**: Database architecture lacked proper project isolation. All scanned photos were incorrectly associated with project_id=1 regardless of which project was active.

---

## 🔧 Changes Implemented

### 1. Fixed `build_date_branches()` to Accept Project ID Parameter

**File**: `reference_db.py:1964-1985`

**Before**:
```python
def build_date_branches(self):
    """Build branches for each date_taken value..."""
    # get project (default first)
    cur.execute("SELECT id FROM projects ORDER BY id LIMIT 1")
    row = cur.fetchone()
    if not row:
        print("[build_date_branches] No projects found!")
        return 0
    project_id = row[0]  # ❌ ALWAYS uses first project!
    print(f"[build_date_branches] Using project_id={project_id}")
```

**After**:
```python
def build_date_branches(self, project_id: int):
    """
    Build branches for each date_taken value in photo_metadata.

    Args:
        project_id: The project ID to associate photos with

    NOTE: Uses date_taken field (populated during scan) instead of created_date.
    Also populates the 'all' branch with all photos.
    """
    print(f"[build_date_branches] Using project_id={project_id}")

    with self._connect() as conn:
        cur = conn.cursor()

        # Verify project exists
        cur.execute("SELECT id FROM projects WHERE id = ?", (project_id,))
        row = cur.fetchone()
        if not row:
            print(f"[build_date_branches] ERROR: Project {project_id} not found!")
            return 0
```

**Impact**: Photos are now associated with the correct project when scanned.

---

### 2. Updated Scan Cleanup to Pass Current Project ID

**File**: `main_window_qt.py:292-312`

**Before**:
```python
# Build date branches after scan completes
try:
    self.logger.info("Building date branches...")
    from reference_db import ReferenceDB
    db = ReferenceDB()
    branch_count = db.build_date_branches()  # ❌ No project_id passed!
    self.logger.info(f"Created {branch_count} date branch entries")
```

**After**:
```python
# Build date branches after scan completes
try:
    self.logger.info("Building date branches...")
    from reference_db import ReferenceDB
    from app_services import get_default_project_id
    db = ReferenceDB()

    # CRITICAL FIX: Get current project_id to associate scanned photos with correct project
    # Without this, all photos go to project_id=1 regardless of which project is active
    current_project_id = self.main.grid.project_id
    if current_project_id is None:
        self.logger.warning("Grid project_id is None, using default project")
        current_project_id = get_default_project_id()

    if current_project_id is None:
        self.logger.error("No project found! Cannot build date branches.")
        raise ValueError("No project available to associate scanned photos")

    self.logger.info(f"Building date branches for project_id={current_project_id}")
    branch_count = db.build_date_branches(current_project_id)
    self.logger.info(f"Created {branch_count} date branch entries for project {current_project_id}")
```

**Impact**: Scan operation now associates photos with the active project instead of defaulting to project_id=1.

---

### 3. Updated Sidebar to Filter Dates by Project ID

**File**: `sidebar_qt.py:665-679`

**Before**:
```python
try:
    # Get hierarchical date data: {year: {month: [days]}}
    # Note: Database methods don't require project_id - they query all photos
    if hasattr(self.db, "get_date_hierarchy"):
        hier = self.db.get_date_hierarchy() or {}  # ❌ No project_id!
        # Also get year counts
        year_counts = {}
        if hasattr(self.db, "list_years_with_counts"):
            year_list = self.db.list_years_with_counts() or []  # ❌ No project_id!
            year_counts = {str(y): c for y, c in year_list}
        # Build result with hierarchy and counts
        rows = {"hierarchy": hier, "year_counts": year_counts}
```

**After**:
```python
try:
    # Get hierarchical date data: {year: {month: [days]}}
    # CRITICAL FIX: Pass project_id to filter dates by project
    if hasattr(self.db, "get_date_hierarchy"):
        hier = self.db.get_date_hierarchy(self.project_id) or {}
        # Also get year counts - now filtered by project_id
        year_counts = {}
        if hasattr(self.db, "list_years_with_counts"):
            year_list = self.db.list_years_with_counts(self.project_id) or []
            year_counts = {str(y): c for y, c in year_list}
        # Build result with hierarchy and counts
        rows = {"hierarchy": hier, "year_counts": year_counts}
    else:
        self._dbg("_load_dates → No date hierarchy method available")
    self._dbg(f"_load_dates → got hierarchy data for project_id={self.project_id}")
```

**Impact**: Dates tab now shows only photos from the current project instead of all photos globally.

---

### 4. Added Project ID Filtering to `list_years_with_counts()`

**File**: `reference_db.py:1704-1738`

**Before**:
```python
def list_years_with_counts(self) -> list[tuple[int, int]]:
    """[(year, count)] newest first. Returns [] if migration not yet run."""
    if not self._has_created_columns():
        return []
    with self._connect() as conn:
        cur = conn.execute("""
            SELECT created_year, COUNT(*)
            FROM photo_metadata
            WHERE created_year IS NOT NULL
            GROUP BY created_year
            ORDER BY created_year DESC
        """)
        return cur.fetchall()
```

**After**:
```python
def list_years_with_counts(self, project_id: int | None = None) -> list[tuple[int, int]]:
    """
    Get list of years with photo counts.

    Args:
        project_id: Filter by project_id if provided, otherwise use all photos globally

    Returns:
        [(year, count)] newest first. Returns [] if migration not yet run.
    """
    if not self._has_created_columns():
        return []
    with self._connect() as conn:
        cur = conn.cursor()
        if project_id is not None:
            # Filter by project_id using project_images junction table
            cur.execute("""
                SELECT pm.created_year, COUNT(DISTINCT pm.path)
                FROM photo_metadata pm
                INNER JOIN project_images pi ON pm.path = pi.image_path
                WHERE pi.project_id = ?
                  AND pm.created_year IS NOT NULL
                GROUP BY pm.created_year
                ORDER BY pm.created_year DESC
            """, (project_id,))
        else:
            # No project filter - use all photos globally
            cur.execute("""
                SELECT created_year, COUNT(*)
                FROM photo_metadata
                WHERE created_year IS NOT NULL
                GROUP BY created_year
                ORDER BY created_year DESC
            """)
        return cur.fetchall()
```

**Impact**: Year counts now reflect the current project instead of global counts.

---

## ✅ Database Query Audit Results

### Methods Already Supporting Project ID Filtering

These methods already had `project_id` parameter support:

- ✅ `get_date_hierarchy(project_id: int | None = None)` - reference_db.py:2068
- ✅ `count_for_year(year, project_id: int | None = None)` - reference_db.py:2109
- ✅ `count_for_month(year, month, project_id: int | None = None)` - reference_db.py:2138
- ✅ `count_for_day(day, project_id: int | None = None)` - reference_db.py:2170
- ✅ `get_images_by_branch(project_id, branch_key)` - reference_db.py:1231

### Methods Updated to Support Project ID Filtering

- ✅ `list_years_with_counts(project_id: int | None = None)` - Now filters by project

### Methods NOT Requiring Project Filtering (Global)

These methods operate on global data and don't need project filtering:

- ✅ `get_all_folders()` - Folder hierarchy is global
- ✅ `get_child_folders(parent_id)` - Folder hierarchy is global
- ✅ `get_image_count_recursive(folder_id)` - Counts from photo_metadata (global)

---

## 🧪 Testing Scenarios

### Scenario 1: Single Project + Toggle Views ✅ Expected to Pass

```
1. Start application
2. Create Project P01
3. Toggle between List and Tabs views multiple times
4. Expected: No crash, views switch smoothly
```

### Scenario 2: Multi-Project Switching ✅ Expected to Pass

```
1. Create Project P01
2. Scan photos → 15 photos in P01
3. Create Project P02
4. Switch to P02 → should show 0 photos
5. Scan different folder in P02 → 20 photos
6. Switch back to P01 → should show 15 photos
7. Expected: Each project shows its own photos
```

### Scenario 3: Scan + Toggle Views ✅ Expected to Pass

```
1. Create Project P02
2. Scan photos → 15 photos
3. Toggle to Tabs view
4. Toggle back to List view
5. Expected: Dates section appears, no crash
```

---

## 📋 Files Modified

### Critical Fixes
1. **reference_db.py** - Fixed `build_date_branches()` signature and added project ID validation
2. **main_window_qt.py** - Updated scan cleanup to pass current project_id
3. **sidebar_qt.py** - Updated date loading to filter by project_id
4. **reference_db.py** - Added project_id filtering to `list_years_with_counts()`

### Supporting Documentation
5. **PROJECT_ISOLATION_ANALYSIS.md** - Comprehensive root cause analysis
6. **FIXES_IMPLEMENTATION_SUMMARY.md** - This file

---

## 🔄 Database Architecture (Unchanged)

The fix maintains the existing flexible schema design:

```
photo_metadata (global photo library)
  ↓
project_images (junction table)
  ├─ project_id → projects.id
  ├─ branch_key (e.g., "all", "by_date:2024-11-07")
  └─ image_path → photo_metadata.path
```

**Benefits**:
- ✅ Photos can belong to multiple projects
- ✅ No schema migration required
- ✅ Flexible multi-project support
- ✅ Only required code changes

---

## 🚀 Next Steps

1. **Test the fixes** using the crash scenarios
2. **Commit changes** to feature branch
3. **Create pull request** for review
4. **Monitor for regressions** in project switching behavior

---

## 📝 Known Limitations

1. **Folder hierarchy is global** - All projects share the same folder tree
   - This is by design to support photos belonging to multiple projects
   - Future enhancement: Add project-specific folder views

2. **Year counts may be inaccurate** if photos span multiple projects
   - Fixed by passing `project_id` to `list_years_with_counts()`

3. **Performance** - Queries now JOIN with `project_images` table
   - Should be fast due to indexes on `project_id` and `image_path`
   - Monitor for slow queries on large databases

---

## 🎉 Expected Results

After these fixes:

1. ✅ Projects are properly isolated - P01 photos don't appear in P02
2. ✅ Scanning photos associates them with the active project
3. ✅ Dates tab shows only photos from the current project
4. ✅ No more crashes when toggling views or switching projects
5. ✅ Database queries consistently filter by `project_id`

---

**End of Implementation Summary**
