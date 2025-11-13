# Schema Redesign Plan - Project Isolation

**Date**: 2025-11-07
**Goal**: Add `project_id` as first-class column to core tables for clean project isolation
**Baseline**: Commit 4091048 (stable version before patching)

---

## Problem Statement

**Current Architecture** (Bad):
- `photo_folders` and `photo_metadata` are **global** tables
- Project association happens through `project_images` **junction table**
- Queries require complex JOINs to filter by project
- Easy to forget project_id filter → data leakage between projects
- Race conditions with Qt models during async operations
- **Result**: Crashes, data leakage, complex code

**Target Architecture** (Good):
- `photo_folders` and `photo_metadata` have **project_id column**
- Project ownership is clear at the row level
- Simple WHERE project_id = ? filtering
- Impossible to accidentally mix projects
- **Result**: Clean code, no leakage, better performance

---

## Schema Changes

### 1. Add project_id to photo_folders

**Before**:
```sql
CREATE TABLE photo_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    path TEXT UNIQUE NOT NULL,
    parent_id INTEGER NULL,
    FOREIGN KEY(parent_id) REFERENCES photo_folders(id)
);
```

**After**:
```sql
CREATE TABLE photo_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    path TEXT NOT NULL,  -- Remove UNIQUE (same folder path in different projects)
    parent_id INTEGER NULL,
    project_id INTEGER NOT NULL,  -- NEW: Direct project ownership
    FOREIGN KEY(parent_id) REFERENCES photo_folders(id),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(path, project_id)  -- NEW: Unique per project
);

CREATE INDEX idx_photo_folders_project ON photo_folders(project_id);
```

**Rationale**:
- Same physical folder can be scanned into different projects
- Each project has its own folder hierarchy
- Cascade delete: Deleting project removes its folders

### 2. Add project_id to photo_metadata

**Before**:
```sql
CREATE TABLE photo_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    folder_id INTEGER NOT NULL,
    size_kb REAL,
    -- ... other fields ...
    FOREIGN KEY(folder_id) REFERENCES photo_folders(id)
);
```

**After**:
```sql
CREATE TABLE photo_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,  -- Remove UNIQUE (same photo in different projects)
    folder_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,  -- NEW: Direct project ownership
    size_kb REAL,
    -- ... other fields ...
    FOREIGN KEY(folder_id) REFERENCES photo_folders(id),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(path, project_id)  -- NEW: Unique per project
);

CREATE INDEX idx_photo_metadata_project ON photo_metadata(project_id);
```

**Rationale**:
- Same photo file can be indexed in multiple projects
- Each project has independent metadata
- Cascade delete: Deleting project removes its photos

### 3. Keep project_images for Branch Associations

**No changes** to `project_images` table - it continues to serve as:
- Branch membership tracking (which photos are in which branch)
- Date-based filtering (date branches)
- Tag-based filtering (tag branches)

**Purpose**:
- `photo_metadata.project_id` = "This photo belongs to this project"
- `project_images` = "This photo is in this branch within the project"

---

## Query Changes (Examples)

### Get All Folders

**Before (Complex JOIN)**:
```python
def get_all_folders(self, project_id):
    cur.execute("""
        SELECT DISTINCT pf.id, pf.parent_id, pf.path, pf.name
        FROM photo_folders pf
        INNER JOIN photo_metadata pm ON pf.id = pm.folder_id
        INNER JOIN project_images pi ON pm.path = pi.image_path
        WHERE pi.project_id = ?
    """, (project_id,))
```

**After (Simple WHERE)**:
```python
def get_all_folders(self, project_id):
    cur.execute("""
        SELECT id, parent_id, path, name
        FROM photo_folders
        WHERE project_id = ?
        ORDER BY name
    """, (project_id,))
```

### Get Child Folders

**Before (Complex JOIN + Recursive)**:
```python
def get_child_folders(self, parent_id, project_id):
    # Complex JOIN with photo_metadata and project_images...
```

**After (Simple WHERE)**:
```python
def get_child_folders(self, parent_id, project_id):
    cur.execute("""
        SELECT id, name, path
        FROM photo_folders
        WHERE parent_id = ? AND project_id = ?
        ORDER BY name
    """, (parent_id, project_id))
```

### Get Image Count

**Before (Complex JOIN)**:
```python
def get_image_count_recursive(self, folder_id, project_id):
    # WITH RECURSIVE + JOIN project_images...
```

**After (Simple WHERE + Recursive)**:
```python
def get_image_count_recursive(self, folder_id, project_id):
    cur.execute("""
        WITH RECURSIVE subfolders(id) AS (
            SELECT id FROM photo_folders
            WHERE id = ? AND project_id = ?
            UNION ALL
            SELECT f.id FROM photo_folders f
            JOIN subfolders s ON f.parent_id = s.id
            WHERE f.project_id = ?
        )
        SELECT COUNT(*) FROM photo_metadata
        WHERE folder_id IN (SELECT id FROM subfolders)
          AND project_id = ?
    """, (folder_id, project_id, project_id, project_id))
```

**Still recursive, but no JOIN with project_images!**

---

## Migration Strategy

### Phase 1: Schema Migration (Destructive)

**For Development/Testing** (User has no production data):

```sql
-- Step 1: Backup old schema
ALTER TABLE photo_folders RENAME TO photo_folders_old;
ALTER TABLE photo_metadata RENAME TO photo_metadata_old;

-- Step 2: Create new schema
CREATE TABLE photo_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    parent_id INTEGER NULL,
    project_id INTEGER NOT NULL,
    FOREIGN KEY(parent_id) REFERENCES photo_folders(id),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(path, project_id)
);

CREATE TABLE photo_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    folder_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    size_kb REAL,
    modified TEXT,
    width INTEGER,
    height INTEGER,
    embedding BLOB,
    date_taken TEXT,
    tags TEXT,
    updated_at TEXT,
    metadata_status TEXT DEFAULT 'pending',
    metadata_fail_count INTEGER DEFAULT 0,
    created_ts INTEGER,
    created_date TEXT,
    created_year INTEGER,
    FOREIGN KEY(folder_id) REFERENCES photo_folders(id),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(path, project_id)
);

-- Step 3: Migrate data (populate project_id from project_images)
INSERT INTO photo_folders (id, name, path, parent_id, project_id)
SELECT DISTINCT
    pf.id,
    pf.name,
    pf.path,
    pf.parent_id,
    COALESCE(
        (SELECT DISTINCT pi.project_id
         FROM photo_metadata pm
         JOIN project_images pi ON pm.path = pi.image_path
         WHERE pm.folder_id = pf.id
         LIMIT 1),
        1  -- Default to project 1 if no association
    ) as project_id
FROM photo_folders_old pf;

INSERT INTO photo_metadata (
    id, path, folder_id, project_id, size_kb, modified,
    width, height, embedding, date_taken, tags, updated_at,
    metadata_status, metadata_fail_count, created_ts, created_date, created_year
)
SELECT
    pm.id,
    pm.path,
    pm.folder_id,
    COALESCE(pi.project_id, 1) as project_id,
    pm.size_kb,
    pm.modified,
    pm.width,
    pm.height,
    pm.embedding,
    pm.date_taken,
    pm.tags,
    pm.updated_at,
    pm.metadata_status,
    pm.metadata_fail_count,
    pm.created_ts,
    pm.created_date,
    pm.created_year
FROM photo_metadata_old pm
LEFT JOIN project_images pi ON pm.path = pi.image_path;

-- Step 4: Create indexes
CREATE INDEX idx_photo_folders_project ON photo_folders(project_id);
CREATE INDEX idx_photo_metadata_project ON photo_metadata(project_id);

-- Step 5: Drop old tables
DROP TABLE photo_folders_old;
DROP TABLE photo_metadata_old;

-- Step 6: Update schema version
INSERT INTO schema_version (version, description)
VALUES ('3.0.0', 'Added project_id to photo_folders and photo_metadata for clean project isolation');
```

### Phase 2: Code Updates

**Files to Update**:

1. **repository/schema.py**
   - Update SCHEMA_SQL with new table definitions
   - Bump SCHEMA_VERSION to "3.0.0"
   - Add migration to MIGRATIONS dict

2. **reference_db.py** (Multiple methods)
   - `get_all_folders()` - Remove JOIN, add WHERE project_id
   - `get_child_folders()` - Remove JOIN, add WHERE project_id
   - `get_image_count_recursive()` - Remove project_images JOIN
   - `insert_photo()` - Add project_id parameter
   - `bulk_upsert()` - Add project_id to inserts
   - Any folder creation methods - Add project_id parameter

3. **services/photo_scan_service.py**
   - `_process_file()` - Pass project_id to folder creation
   - `_ensure_folder_hierarchy()` - Accept and use project_id
   - `_write_batch()` - Include project_id in photo rows

4. **repository/photo_repository.py**
   - `upsert()` - Add project_id parameter
   - `bulk_upsert()` - Add project_id to batch inserts

5. **repository/folder_repository.py**
   - `ensure_folder()` - Add project_id parameter
   - `get_folder_by_path()` - Add project_id to WHERE clause

---

## Testing Plan

### Test 1: Fresh Database
```bash
# 1. Delete old database
rm reference_data.db

# 2. Start app
python main_qt.py

# 3. Create project P01
# 4. Scan photos
# 5. Verify: Photos appear in P01

# 6. Create project P02
# 7. Scan same photos
# 8. Verify: Photos appear in P02, separate from P01

# 9. Switch between P01 and P02
# 10. Verify: Each shows only its own photos
```

### Test 2: View Toggling
```bash
# 1. In P01 with photos
# 2. Toggle List → Tabs → List
# 3. Verify: No crashes

# 4. Toggle multiple times
# 5. Verify: Always stable
```

### Test 3: Multiple Projects
```bash
# 1. Create P01, scan Folder A
# 2. Create P02, scan Folder B
# 3. Create P03, scan Folder A again

# 4. Switch to P01
# 5. Verify: Shows Folder A photos from P01 only

# 6. Switch to P02
# 7. Verify: Shows Folder B photos only

# 8. Switch to P03
# 9. Verify: Shows Folder A photos from P03 (separate from P01)
```

---

## Benefits After Redesign

### Code Simplicity
- ✅ **90% fewer JOINs** - Most queries are simple WHERE clauses
- ✅ **Clearer intent** - project_id visible in every query
- ✅ **Easier to review** - No complex JOIN logic to understand

### Performance
- ✅ **Faster queries** - Indexed project_id instead of JOIN
- ✅ **Better query plans** - SQLite can optimize simple WHERE
- ✅ **Reduced memory** - No large JOIN intermediates

### Reliability
- ✅ **Impossible to leak data** - Can't forget WHERE clause
- ✅ **Cascade deletes work** - Delete project removes all data
- ✅ **No threading issues** - Simpler code = fewer race conditions

### Maintainability
- ✅ **Self-documenting** - project_id shows ownership
- ✅ **Easier to test** - Clear project boundaries
- ✅ **Simpler debugging** - Can inspect project_id directly

---

## Rollback Plan

If something goes wrong:

```bash
# Rollback to baseline
git checkout claude/debug-project-crashes-architecture-011CUtbAQwXPFye7fhFiZJna

# Or rollback just database
cp reference_data.db.backup reference_data.db
```

---

## Timeline

**Phase 1**: Schema Migration Script (30 min)
- Create migration.sql
- Test on dummy database

**Phase 2**: Update repository/schema.py (15 min)
- Update SCHEMA_SQL
- Bump version to 3.0.0

**Phase 3**: Update reference_db.py (60 min)
- Update ~10 methods
- Remove complex JOINs
- Add project_id parameters

**Phase 4**: Update services/ (30 min)
- Update photo_scan_service.py
- Update folder creation

**Phase 5**: Testing (30 min)
- Test fresh database
- Test view toggling
- Test multiple projects

**Total**: ~3 hours

---

## Next Steps

1. ✅ Create schema_migration_v3.sql
2. Update repository/schema.py
3. Create migration runner script
4. Update reference_db.py methods
5. Update service layer
6. Test thoroughly

---

**Status**: Ready to implement
**Risk**: Low (working on separate branch, can rollback)
**Payoff**: High (clean architecture, stable codebase)
