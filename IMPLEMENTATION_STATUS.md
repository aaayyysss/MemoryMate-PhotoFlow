# Schema Redesign Implementation Status

**Date**: 2025-11-07
**Branch**: claude/schema-redesign-project-id-011CVBwMhXv8zQbxYfGHpKnZ
**Status**: 60% Complete (Repository layer done, service layer remaining)

---

## ✅ COMPLETED (3/3 commits)

### 1. Schema v3.0.0 (Commit 2d9d3e4)
- ✅ Updated repository/schema.py to v3.0.0
- ✅ Added project_id column to photo_folders
- ✅ Added project_id column to photo_metadata
- ✅ Created migration script (migrations/migration_v3_project_id.sql)
- ✅ Added indexes for project_id
- ✅ Updated UNIQUE constraints to (path, project_id)

### 2. Repository Layer (Commit 785a091)
- ✅ folder_repository.py - All methods updated with project_id
- ✅ photo_repository.py - All methods updated with project_id
- ✅ UNIQUE conflict resolution updated to (path, project_id)

---

## 🔨 REMAINING WORK

### 3. Service Layer (NOT STARTED)

**File**: services/photo_scan_service.py

**Changes Needed**:

```python
# Add project_id parameter to scan_repository()
def scan_repository(self,
                   root_folder: str,
                   project_id: int,  # NEW PARAMETER
                   incremental: bool = True,
                   ...):

# Update _ensure_folder_hierarchy() to pass project_id
def _ensure_folder_hierarchy(self, folder_path: Path, root_path: Path, project_id: int):
    root_id = self.folder_repo.ensure_folder(
        path=str(root_path),
        name=root_path.name,
        parent_id=None,
        project_id=project_id  # PASS project_id
    )

    # ... rest of hierarchy creation with project_id

# Update _write_batch() to pass project_id
def _write_batch(self, rows: List[Tuple], project_id: int):
    affected = self.photo_repo.bulk_upsert(rows, project_id)  # PASS project_id
```

**Estimated Time**: 15 minutes

---

### 4. UI Layer (NOT STARTED)

**File**: main_window_qt.py

**Changes Needed**:

```python
# In ScanController._cleanup() around line 310
# Already gets project_id - just pass it to scan:

current_project_id = self.main.grid.project_id
# ... validation ...

# When calling scan service, pass project_id:
# (No direct call to scan_repository in main_window_qt.py - handled by ScanWorkerAdapter)
```

**File**: services/scan_worker_adapter.py

**Changes Needed**:

```python
# Add project_id parameter to __init__
def __init__(self,
             folder: str,
             project_id: int,  # NEW PARAMETER
             incremental: bool,
             settings: Dict[str, Any],
             db_writer: Optional[Any] = None):
    self.project_id = project_id
    # ...

# Pass project_id to scan_repository()
def run(self):
    result: ScanResult = self.service.scan_repository(
        root_folder=self.folder,
        project_id=self.project_id,  # PASS project_id
        incremental=self.incremental,
        ...
    )
```

**Estimated Time**: 10 minutes

---

### 5. Reference DB Updates (MAJOR WORK)

**File**: reference_db.py

This file has MANY methods that query photo_folders and photo_metadata. Each needs updating:

**Folder Methods** (~30 methods):
- `get_all_folders()` - Add WHERE project_id = ?
- `get_child_folders()` - Add WHERE project_id = ?
- `get_folder_by_path()` - Add WHERE project_id = ?
- etc.

**Photo Methods** (~20 methods):
- `get_all_photos()` - Add WHERE project_id = ?
- `get_photos_by_folder()` - Add WHERE project_id = ?
- etc.

**Estimated Time**: 60-90 minutes (many methods)

**RECOMMENDATION**: Since reference_db.py is being phased out in favor of repository layer, we might be able to skip most of these updates if the UI doesn't call them directly. Need to check call sites.

---

## 🧪 TESTING PLAN

### Test 1: Fresh Database
```bash
# Delete old database
rm reference_data.db

# Start app
python main_qt.py

# Create project P01
# Scan photos
# Expected: Photos appear in P01
```

### Test 2: Multiple Projects
```bash
# Create P01, scan Folder A
# Create P02, scan Folder A again

# Switch to P01
# Expected: Shows P01's scan of Folder A

# Switch to P02
# Expected: Shows P02's scan of Folder A (separate data)
```

### Test 3: View Toggling
```bash
# In P01 with photos
# Toggle List → Tabs → List multiple times
# Expected: No crashes, stable behavior
```

---

## 📊 COMPLETION ESTIMATE

- ✅ Schema & Migration: **100% Complete**
- ✅ Repository Layer: **100% Complete**
- ⚠️ Service Layer: **0% Complete** (15 min remaining)
- ⚠️ UI Layer: **0% Complete** (10 min remaining)
- ⚠️ reference_db.py: **0% Complete** (60-90 min OR skip if unused)

**Total Remaining**: 25 minutes (if we skip reference_db.py) OR 85-115 minutes (if we update it)

---

## 🎯 RECOMMENDATION

### Option A: Minimal Viable (25 min)
1. Update photo_scan_service.py (15 min)
2. Update scan_worker_adapter.py (10 min)
3. Test with fresh database
4. Skip reference_db.py updates (only update if UI calls fail)

### Option B: Complete (85-115 min)
1. Do Option A
2. Update all reference_db.py methods (60-90 min)
3. Comprehensive testing

**My Recommendation**: **Option A** - Get scanning working, then fix reference_db.py methods as needed based on runtime errors.

---

## 🚀 NEXT STEPS

To complete this redesign:

1. Update services/photo_scan_service.py
2. Update services/scan_worker_adapter.py
3. Test with fresh database
4. Fix any reference_db.py methods that fail at runtime
5. Test thoroughly
6. Merge to main

---

## 📝 NOTES FOR CONTINUATION

- Current branch has clean foundation (schema + repositories)
- Service layer is straightforward (just pass project_id through)
- reference_db.py might have many unused methods - fix on demand
- Testing will reveal which methods actually need updating

---

**Status**: Ready for service layer implementation
**Blocked By**: None
**Risk Level**: Low (can rollback to baseline if needed)
