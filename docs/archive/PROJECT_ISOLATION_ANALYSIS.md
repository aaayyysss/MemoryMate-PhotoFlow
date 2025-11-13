# Project Isolation Analysis - Crash Investigation

## Executive Summary

The application crashes when:
1. Creating projects and toggling between List/Tabs views (Crash 1)
2. Creating multiple projects and switching between them (Crash 2)
3. Scanning photos in a project and toggling views (Crash 3)

**Root Cause**: The database architecture lacks proper project isolation. Photos scanned in Project P02 are incorrectly associated with Project P01 (project_id=1), causing data inconsistencies and UI crashes.

---

## Critical Issues Identified

### 1. **CRITICAL: `build_date_branches()` Always Uses Project ID 1**

**Location**: `reference_db.py:1976-1982`

```python
# get project (default first)
cur.execute("SELECT id FROM projects ORDER BY id LIMIT 1")
row = cur.fetchone()
if not row:
    print("[build_date_branches] No projects found!")
    return 0
project_id = row[0]  # ❌ ALWAYS uses first project!
print(f"[build_date_branches] Using project_id={project_id}")
```

**Problem**:
- This function ALWAYS queries the first project (ORDER BY id LIMIT 1)
- Completely ignores which project is currently active
- Called from `main_window_qt.py:297` without any project context

**Impact**:
- User scans photos in Project P02 (project_id=2)
- `build_date_branches()` runs but uses project_id=1
- All photos get associated with project_id=1 in `project_images` table
- When querying project_id=2, no photos are found → empty grid → potential crashes

**Evidence from logs**:
```
[get_images_by_branch] project_id=2, branch_key='all'
[get_images_by_branch] Found 0 photos
...
[build_date_branches] Using project_id=1
[build_date_branches] Populating 'all' branch with 15 photos
```

---

### 2. **Database Queries Not Filtering by Project ID**

#### Issue 2a: `get_date_hierarchy()` Not Using Project ID

**Location**: `sidebar_qt.py:669`

```python
hier = self.db.get_date_hierarchy() or {}  # ❌ No project_id passed!
```

**Problem**:
- `get_date_hierarchy()` supports a `project_id` parameter (reference_db.py:2068)
- But sidebar doesn't pass `self.project_id` to the method
- Returns ALL photos globally instead of project-specific photos

**Fix Needed**:
```python
hier = self.db.get_date_hierarchy(self.project_id) or {}
```

#### Issue 2b: `list_years_with_counts()` Likely Missing Project Filter

**Location**: `sidebar_qt.py:673`

```python
year_list = self.db.list_years_with_counts() or []
```

**Need to verify**: Does this method support project_id filtering?

#### Issue 2c: `count_for_year()`, `count_for_month()`, `count_for_day()`

**Need to verify**: Do these counting methods filter by project_id?

---

### 3. **Scan Operation Doesn't Associate Photos with Projects**

**Current flow**:
1. User scans folder → photos added to `photo_metadata` table (global)
2. Photos added to `photo_folders` table (global hierarchy)
3. `build_date_branches()` called → associates photos with project_id=1 only
4. No mechanism to scan photos FOR a specific project

**Problem**:
- `photo_metadata` table has NO `project_id` column
- Photos can belong to multiple projects via `project_images` junction table
- But scan doesn't create these associations for the current project

---

### 4. **Project Switching Issues**

**Location**: `main_window_qt.py:3176-3194`

When switching projects:
```python
def _on_project_changed_by_id(self, project_id: int):
    # Updates UI components
    if hasattr(self, "sidebar") and self.sidebar:
        self.sidebar.set_project(project_id)
    if hasattr(self, "grid") and self.grid:
        self.grid.project_id = project_id
        self.grid.set_branch("all")  # Reset to show all photos
```

**Missing**:
- No cleanup of old state before switching
- Rapid switching causes race conditions with async workers
- Multiple refresh cycles trigger worker cancellations ("stale gen")

---

### 5. **Race Conditions from Rapid UI Refresh Cycles**

**Evidence from logs**:
```
[13:54:05.578] [Tabs] Starting load for branches
[13:54:05.587] [Tabs] Starting load for tags
[13:54:05.603] [Tabs] _clear_tab idx=2
...
[13:55:41.036] [Tabs] _finish_branches (stale gen=5) — ignoring
[13:55:41.162] [Tabs] _finish_dates (stale gen=3) — ignoring
```

**Problem**:
- Toggling between List/Tabs modes triggers `refresh_all(force=True)`
- Each refresh cancels previous workers mid-flight
- Worker completions arrive after cancellation → "stale gen" messages
- Potential for UI state corruption

---

## Database Architecture Review

### Current Schema (from `repository/schema.py`)

```
photo_metadata (global photo library)
  ├─ id (PRIMARY KEY)
  ├─ path (UNIQUE)
  ├─ folder_id → photo_folders.id
  ├─ date_taken, width, height, tags, etc.
  └─ created_date, created_year (for date browsing)

photo_folders (hierarchical folder structure)
  ├─ id (PRIMARY KEY)
  ├─ path (UNIQUE)
  ├─ name
  └─ parent_id → photo_folders.id (nullable)

projects (top-level organizational unit)
  ├─ id (PRIMARY KEY)
  ├─ name
  ├─ folder (scan path)
  └─ mode

project_images (many-to-many: projects/branches → photos)
  ├─ id (PRIMARY KEY)
  ├─ project_id → projects.id
  ├─ branch_key (e.g., "all", "by_date:2024-11-07")
  └─ image_path (refers to photo_metadata.path)

branches (sub-groups within projects)
  ├─ id (PRIMARY KEY)
  ├─ project_id → projects.id
  ├─ branch_key
  └─ display_name
```

### Design Intent

The architecture supports:
- ✅ Global photo library (`photo_metadata`)
- ✅ Photos can belong to multiple projects
- ✅ Project-specific views via `project_images` junction table

### What's Broken

- ❌ `build_date_branches()` doesn't receive current project context
- ❌ Scan operation doesn't create `project_images` associations
- ❌ UI queries don't consistently filter by `project_id`
- ❌ `photo_folders` are global, not project-specific

---

## Recommended Fixes

### Fix 1: Pass Current Project to `build_date_branches()`

**Change 1a**: Update function signature in `reference_db.py`

```python
# Before
def build_date_branches(self):
    """Build branches for each date_taken value..."""
    # get project (default first)
    cur.execute("SELECT id FROM projects ORDER BY id LIMIT 1")
    ...

# After
def build_date_branches(self, project_id: int):
    """
    Build branches for each date_taken value in photo_metadata.

    Args:
        project_id: The project ID to associate photos with
    """
    print(f"[build_date_branches] Using project_id={project_id}")
    ...
```

**Change 1b**: Update caller in `main_window_qt.py:297`

```python
# Before
branch_count = db.build_date_branches()

# After
branch_count = db.build_date_branches(self.grid.project_id)
```

---

### Fix 2: Add Project Context to Scan Operation

**Option A: Simple - Pass project_id to build_date_branches**

Already covered in Fix 1. This associates scanned photos with the correct project.

**Option B: Enhanced - Make scan operation project-aware**

Add project_id parameter to scan service:

```python
class PhotoScanService:
    def scan_repository(self,
                       root_folder: str,
                       project_id: int,  # New parameter
                       ...):
        # After scanning, associate photos with project
        self._associate_photos_with_project(project_id)
```

---

### Fix 3: Pass Project ID to Database Queries

**Change 3a**: Update sidebar date hierarchy query

```python
# sidebar_qt.py:669
# Before
hier = self.db.get_date_hierarchy() or {}

# After
hier = self.db.get_date_hierarchy(self.project_id) or {}
```

**Change 3b**: Update year/month/day count queries

Need to verify if these methods support project_id:
- `list_years_with_counts()`
- `count_for_year(year)`
- `count_for_month(year, month)`
- `count_for_day(day)`

If not, add `project_id` parameter to each.

---

### Fix 4: Improve Project Switching

**Change 4a**: Add cleanup before switching

```python
def _on_project_changed_by_id(self, project_id: int):
    """Switch to a different project with proper cleanup."""
    try:
        # 1. Cancel any running workers
        if hasattr(self, "sidebar") and self.sidebar:
            self.sidebar.cancel_workers()

        # 2. Clear grid
        if hasattr(self, "grid") and self.grid:
            self.grid.clear()

        # 3. Update project context
        if hasattr(self, "sidebar") and self.sidebar:
            self.sidebar.set_project(project_id)

        if hasattr(self, "grid") and self.grid:
            self.grid.project_id = project_id
            self.grid.set_branch("all")

        # 4. Refresh UI
        QTimer.singleShot(100, self._update_breadcrumb)

        print(f"[MainWindow] Switched to project ID: {project_id}")
    except Exception as e:
        print(f"[MainWindow] Error switching project: {e}")
```

---

### Fix 5: Reduce UI Refresh Cycles

**Problem**: Toggling List/Tabs triggers excessive refreshes

**Solution**: Debounce refresh calls

```python
class SidebarQt:
    def __init__(self):
        ...
        self._refresh_timer = QTimer()
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._do_refresh)

    def request_refresh(self):
        """Debounced refresh - waits 100ms before executing."""
        self._refresh_timer.start(100)

    def _do_refresh(self):
        """Actual refresh implementation."""
        self.tabs_controller.refresh_all(force=True)
```

---

## Proposed Database Schema Changes

### Option 1: Add project_id to photo_metadata (Breaking Change)

```sql
ALTER TABLE photo_metadata ADD COLUMN project_id INTEGER REFERENCES projects(id);
CREATE INDEX idx_photo_project ON photo_metadata(project_id);
```

**Pros**:
- Simpler queries
- Better performance
- Clearer ownership model

**Cons**:
- ❌ Photos can only belong to ONE project
- ❌ Breaks existing multi-project use case
- ❌ Requires data migration

**Verdict**: ❌ **NOT RECOMMENDED** - loses flexibility

---

### Option 2: Keep Current Schema, Fix Queries (Recommended)

Keep the current `project_images` junction table design but fix all queries to use it properly.

**Pros**:
- ✅ No schema changes needed
- ✅ Photos can belong to multiple projects
- ✅ Maintains flexibility
- ✅ Only requires code changes

**Cons**:
- Queries are slightly more complex (need JOIN)
- Must ensure all queries filter correctly

**Verdict**: ✅ **RECOMMENDED**

---

## Implementation Plan

### Phase 1: Critical Fixes (Stops Crashes)

1. ✅ Fix `build_date_branches()` to accept and use `project_id` parameter
2. ✅ Update `main_window_qt.py` to pass current project_id
3. ✅ Update sidebar queries to filter by project_id

### Phase 2: Project Switching Improvements

4. Add cleanup logic before project switches
5. Add debouncing to UI refresh cycles
6. Improve error handling in project operations

### Phase 3: Testing

7. Test Crash 1 scenario: Create project, toggle List/Tabs
8. Test Crash 2 scenario: Create multiple projects, switch between them
9. Test Crash 3 scenario: Scan photos, toggle views

### Phase 4: Long-term Improvements

10. Add project_id validation to all database operations
11. Add comprehensive logging for project operations
12. Consider adding project context manager for state consistency

---

## Test Scenarios

### Scenario 1: Single Project Workflow
```
1. Create Project P01
2. Scan photos → 15 photos found
3. Verify: Grid shows 15 photos
4. Verify: Dates tab shows hierarchy
5. Toggle to List view
6. Toggle back to Tabs view
7. Result: No crash, data persists
```

### Scenario 2: Multi-Project Workflow
```
1. Create Project P01
2. Scan photos → 15 photos in P01
3. Create Project P02
4. Switch to P02
5. Verify: Grid shows 0 photos (P02 is empty)
6. Scan different folder → 20 photos in P02
7. Verify: Grid shows 20 photos
8. Switch back to P01
9. Verify: Grid shows 15 photos (original P01 data)
10. Result: Projects properly isolated
```

### Scenario 3: Project Deletion
```
1. Create Project P01 with photos
2. Delete P01
3. Verify: project_images entries cascade delete (ON DELETE CASCADE)
4. Verify: photo_metadata entries remain (global library)
5. Result: Project deleted cleanly
```

---

## Files to Modify

### Priority 1 (Stops Crashes)
- [x] `reference_db.py` - Fix `build_date_branches()` signature and implementation
- [x] `main_window_qt.py` - Pass project_id when calling `build_date_branches()`
- [x] `sidebar_qt.py` - Pass project_id to `get_date_hierarchy()` and count methods

### Priority 2 (Improves Stability)
- [ ] `main_window_qt.py` - Improve `_on_project_changed_by_id()` cleanup
- [ ] `sidebar_qt.py` - Add refresh debouncing
- [ ] `thumbnail_grid_qt.py` - Add project validation

### Priority 3 (Testing)
- [ ] Add integration tests for project switching
- [ ] Add tests for scan + project association
- [ ] Add tests for multi-project isolation

---

## Next Steps

1. **Review this analysis** with the team/user
2. **Implement Priority 1 fixes** (critical path)
3. **Test crash scenarios** to verify fixes
4. **Implement Priority 2 fixes** (stability improvements)
5. **Add comprehensive tests** to prevent regression

---

## Appendix: Query Audit Checklist

Methods that MUST filter by project_id:

- [x] `build_date_branches()` - ❌ Uses wrong project_id
- [ ] `get_date_hierarchy()` - ✅ Supports project_id but not used by caller
- [ ] `list_years_with_counts()` - ❓ Need to verify
- [ ] `count_for_year()` - ❓ Need to verify
- [ ] `count_for_month()` - ❓ Need to verify
- [ ] `count_for_day()` - ❓ Need to verify
- [ ] `get_images_by_branch()` - ✅ Already filters by project_id
- [ ] `get_project_images()` - ✅ Already filters by project_id
- [ ] `get_face_clusters()` - ❓ Need to verify

Methods that DON'T need project filtering (global):

- [x] `get_all_folders()` - Folder hierarchy is global
- [x] `get_child_folders()` - Folder hierarchy is global
- [x] `get_image_count_recursive()` - Counts from photo_metadata (global)

---

**End of Analysis**
