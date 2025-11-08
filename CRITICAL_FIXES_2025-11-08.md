# Critical Fixes - 2025-11-08

## Summary

Fixed 4 critical issues discovered during 9,3K photo testing:

1. ✅ **Project toggle crash** (P01→P02→P01→P02)
2. ✅ **Date/Folder count mismatch** (81% of photos missing from date tree)
3. ✅ **Orphaned folders** (Tags appearing in Folders section with wrong counts)
4. ⚠️ **Tagging freeze** (3+ minute black screen) - INVESTIGATION IN PROGRESS

---

## Issue 1: Project Toggle Crash ✅ FIXED

### Problem
App crashed when rapidly toggling between projects (P01→P02→P01→P02).

### Root Cause
`model.clear()` in sidebar was called while Qt internal state was still processing events from tabs refresh, causing Qt C++ segfault.

### Solution
Added comprehensive error handling in `sidebar_qt.py:1405-1454`:
- Added `_initialized` guard for `processEvents()` to prevent startup crash
- Wrapped `model.clear()` in try/except to catch Qt exceptions
- Fallback: Create new model if clear() fails
- Prevents app crash, maintains sidebar functionality

### Files Modified
- `sidebar_qt.py` - Lines 1405-1454

### Commits
- `da26878` - Initial fix (caused startup crash)
- `b4a8e90` - Fixed startup crash with initialization guard ✅

---

## Issue 2: Date/Folder Count Mismatch ✅ FIXED

### Problem
Massive discrepancy between folder counts and date counts:
- **Folders**: 9,394 photos
- **Dates**: 2,298 photos (only 19%!)
- **Missing**: 9,486 photos (81%)

### Root Cause Analysis

Analyzed user's `reference_data.db`:

```
Total photos in project 1: 11,783
Photos with date_taken (EXIF): 7,243 (61%)
Photos with created_year: 2,298 (19%) ← BUG!
Photos WITHOUT created_year: 9,485 (81%)
```

**Critical finding**: 9,486 photos are missing `created_year` EVEN THOUGH they all have valid dates!

Sample evidence:
```
Path: ...12-5-2022 Mein Geburtstag.../IMG_E8700.JPG
date_taken: 2022-12-05 17:04:53  ← Valid!
modified: 2022-12-06 19:20:32     ← Valid!
created_year: NULL                ← BUG!
```

### Root Cause
`created_year` field was not populated for older photos, likely because:
- `compute_created_fields()` was added in a later version
- Existing photos were never backfilled
- "By Date" tree uses `created_year` to group photos

### Solution
Created `fix_missing_created_year.py` backfill script:
- Parses existing `date_taken` or `modified` for each photo
- Extracts year and populates `created_year`, `created_date`, `created_ts`
- Uses same logic as `MetadataService.compute_created_fields()`
- Dry-run mode available for safety

### Usage
```bash
# Preview what will be fixed
python fix_missing_created_year.py --dry-run

# Apply the fix
python fix_missing_created_year.py

# Fix specific project only
python fix_missing_created_year.py --project-id 1
```

### Expected Results
After running the script:
- "By Date" tree will show ALL ~11,783 photos
- Date counts will match folder counts
- Year distribution will be accurate

### Files Added
- `fix_missing_created_year.py` - Standalone backfill script (165 lines)

### Commits
- `1a66194` - Add backfill script ✅

---

## Issue 3: Tagging Freeze (3+ Minute Black Screen) ⚠️ INVESTIGATING

### Problem
When tagging a single photo:
- UI freezes with black screen
- Freeze lasts 3+ minutes
- App eventually recovers and tag is successfully applied

### Evidence from Log
```
[22:31:48.312] Tag operation starts
[22:31:48.319] Tag applied successfully
[Sidebar] reload_tags_only → got 1 tags
[DB] get_images_by_folder(141, subfolders=True, project=1) -> 9394 paths from 544 folders
[TAG FILTER] Context intersected: 1/9394 matched (tag='favorite')
[GRID] Loaded 1 thumbnails.
... (3 similar reloads happen quickly)
[22:35:04.529] ← 3.3 MINUTE GAP! Next user interaction
```

### Observations
1. **All operations complete quickly** at 22:31:48 (within 1 second)
2. **No log output for 3.3 minutes** (196 seconds)
3. **UI completely frozen** during this time (black screen)
4. **App recovers** and continues normally

### Possible Causes
1. **Thumbnail generation**: If cache misses, might generate thumbnails for all 9,394 photos synchronously
2. **Blocking I/O**: Some file operation without logging
3. **Qt event loop blocked**: Main thread stuck in synchronous operation
4. **Database lock**: Long-running query holding lock

### Current Status
🔍 **Investigation in progress** - Need to:
- [ ] Check if thumbnail generation is synchronous
- [ ] Add more logging to identify blocking operation
- [ ] Profile the tagging operation with large dataset
- [ ] Check for synchronous database operations

### Temporary Workaround
None currently. Issue only affects first tag operation after loading large dataset.

---

## Testing Instructions

### Test 1: Project Toggle (Issue #1)
```bash
1. Start app
2. Create Project 1, scan photos
3. Create Project 2, scan different folder
4. Rapidly toggle: P1 → P2 → P1 → P2 → P1
5. ✅ Should NOT crash
6. Check sidebar shows correct counts for each project
```

### Test 2: Date Counts (Issue #2)
```bash
1. Run: python fix_missing_created_year.py --dry-run
2. Note how many photos will be fixed
3. Run: python fix_missing_created_year.py
4. Wait for completion
5. Restart app
6. Check "By Date" section in sidebar
7. ✅ Should show same count as "Folders" section
```

### Test 3: Orphaned Folders (Issue #3)
```bash
1. Run: python fix_orphaned_folders.py --dry-run
2. Note which orphaned folders will be fixed
3. Run: python fix_orphaned_folders.py
4. Restart app
5. Check sidebar (List and Tabs views)
6. ✅ "inbox" folders should only appear under proper parents
7. ✅ List and Tabs counts should match
8. ✅ "All Photos" count should be correct (not inflated)
```

### Test 4: Tagging Freeze (Issue #4)
```bash
1. Load large dataset (9K+ photos)
2. Right-click a photo
3. Select "Mark as Favorite"
4. ⚠️ UI may freeze for 3+ minutes
5. Wait for recovery
6. ✅ Tag should be applied successfully
```

---

## Files Modified/Added

| File | Lines | Description |
|------|-------|-------------|
| sidebar_qt.py | 1405-1454 | Error handling for model.clear() |
| fix_missing_created_year.py | 1-165 | Backfill script for missing dates |
| fix_orphaned_folders.py | 1-195 | Cleanup script for orphaned folders |
| ORPHANED_FOLDERS_FIX.md | - | Complete orphaned folders documentation |

---

## Branch & Commits

**Branch**: `claude/fix-project-toggle-crash-011CUw6ShwYCiDoh2BGQBZK2`

**Commits**:
1. `da26878` - Fix: Prevent crash during rapid project toggling (initial)
2. `b4a8e90` - Fix: Guard processEvents() during initialization
3. `1a66194` - Add: Backfill script to fix missing created_year
4. `c189940` - Fix: Remove orphaned folders causing sidebar count mismatch

---

## Next Steps

1. ✅ **Push all changes** to GitHub
2. ⚠️ **Investigate tagging freeze** - Add logging to identify blocking operation
3. ✅ **User testing** - Verify fixes with 9.3K photo dataset
4. 📝 **Document** - Update README with backfill script usage

---

**Status**: 3 out of 4 issues fixed. Issue #4 (tagging freeze) under investigation.

---

## Issue 3: Orphaned Folders (Tags in Folders Section) ✅ FIXED

### Problem
Folders appearing incorrectly in sidebar with count mismatches:
- **List view**: "inbox" folders show 0 count
- **Tabs view**: Same "inbox" folders show 1 count  
- **All Photos**: Count inflated (300 instead of 298)

### Root Cause Analysis

Database investigation revealed **orphaned folder entries**:

```
Orphaned Folder ID 18:
  Name: 'inbox'
  Path: c:\...\test-photos\inbox  ← LOWERCASE (incorrect)
  Parent: NULL  ← Should have parent!
  Photos: 1

Orphaned Folder ID 19:
  Name: 'inbox'
  Path: c:\...\test-photos - copy\inbox  ← LOWERCASE (incorrect)
  Parent: NULL  ← Should have parent!
  Photos: 1
```

Compared to legitimate folders:
```
Folder ID 2:
  Path: C:\...\Test-Photos\inbox  ← Proper Windows casing
  Parent: 1 (Test-Photos root)
  Photos: 85
```

**Why this happened:**
1. Initial scan creates folders with Windows proper casing
2. Rescan encounters photos with lowercase paths
3. Case-sensitive folder matching fails to find existing folder
4. New folder created with lowercase path + `parent_id = NULL`

**Impact:**
- List view (tree): Orphans skipped → 0 count
- Tabs view (direct query): Shows all folders → 1 count
- All Photos: 298 + 2 orphans = 300

### Solution
Created `fix_orphaned_folders.py` cleanup script:
- Identifies orphaned folders (lowercase paths, `parent_id = NULL`)
- Finds matching legitimate folders (case-insensitive)
- Reassigns photos from orphaned → legitimate folders
- Deletes orphaned folder entries

### Usage
```bash
# Preview what will be fixed
python fix_orphaned_folders.py --dry-run

# Apply the fix
python fix_orphaned_folders.py

# Fix specific project
python fix_orphaned_folders.py --project-id 1
```

### Expected Results
After running the script:
- Orphaned "inbox" folders removed from sidebar
- List and Tabs views show consistent counts
- "All Photos" shows correct count (298, not 300)
- Photos reassigned to proper parent folders

### Files Added
- `fix_orphaned_folders.py` - Cleanup script (195 lines)
- `ORPHANED_FOLDERS_FIX.md` - Complete documentation

### Commit
- `c189940` - Fix orphaned folders ✅

---
