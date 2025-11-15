# 🐛 Debug Session Status - Project Handling Issue

**Session ID:** claude/debug-issue-011CUstrEnRPeyq1j7XfX7h1
**Date:** 2025-11-07
**Type:** Critical Bug Investigation & Fix
**Status:** 🟡 PARTIAL FIX - New crash scenario discovered

---

## 🎯 Original Issue

**Problem:** App crashed immediately after completing photo scan on fresh database
**Reporter:** User via log analysis
**Symptom:** Hard crash (no Python exception) when sidebar attempted to reload after scan completion

---

## 🔍 Root Cause Analysis

### Database Architecture Issue

The application uses a **hybrid database architecture** with a critical design flaw:

#### Global Tables (No project_id column):
- `photo_metadata` - ALL photos from ALL projects
- `photo_folders` - ALL folders from ALL projects

#### Junction Table (With project_id):
- `project_images` - Links photos to specific projects via `project_id`

### The Critical Flaw

Sidebar query methods were querying **GLOBAL tables WITHOUT project_id filtering**:
- `get_quick_date_counts()` - Quick date ranges (Today, This Week, etc.)
- `get_date_hierarchy()` - Year/Month/Day hierarchy
- `count_for_year()`, `count_for_month()`, `count_for_day()` - Photo counts
- `_count_between_meta_dates()`, `_count_recent_updated()` - Internal helpers

**Impact:** Sidebar displayed counts for ALL photos across ALL projects, causing:
1. Data inconsistency when UI tried to load photos not in current project
2. Hard crash when accessing `project_images` junction table
3. Incorrect photo counts in sidebar

---

## ✅ Solution Implemented (Commit: a16dc04)

### Changes Made

#### 1. Updated `reference_db.py` - Added project_id filtering

All count methods now accept optional `project_id` parameter:

```python
# Before
def get_quick_date_counts(self) -> list[dict]:
    # Queried ALL photos globally
    cnt = self._count_between_meta_dates(conn, start, end)

# After
def get_quick_date_counts(self, project_id: int | None = None) -> list[dict]:
    # Filters by project_id when provided
    cnt = self._count_between_meta_dates(conn, start, end, project_id)
```

**Methods Updated:**
- `get_quick_date_counts(project_id=None)`
- `_count_between_meta_dates(conn, start, end, project_id=None)`
- `_count_recent_updated(conn, start_ts, project_id=None)`
- `get_date_hierarchy(project_id=None)`
- `count_for_year(year, project_id=None)`
- `count_for_month(year, month, project_id=None)`
- `count_for_day(day, project_id=None)`

**Query Pattern:**
```sql
-- When project_id is provided
SELECT COUNT(DISTINCT pm.path)
FROM photo_metadata pm
INNER JOIN project_images pi ON pm.path = pi.image_path
WHERE pi.project_id = ?
  AND <date/filter conditions>

-- When project_id is None (backward compatibility)
SELECT COUNT(*)
FROM photo_metadata
WHERE <date/filter conditions>
```

#### 2. Updated `sidebar_qt.py` - Pass project_id to all queries

**Locations Updated:**
- `_build_tree_model()` method (line 1388)
- `_build_by_date_section()` method (lines 1692, 1713, 1729, 1750)
- Tabs controller date method (line 752)

```python
# Before
quick_rows = self.db.get_quick_date_counts()
hier = self.db.get_date_hierarchy()
y_count = self.db.count_for_year(year)

# After
quick_rows = self.db.get_quick_date_counts(project_id=self.project_id)
hier = self.db.get_date_hierarchy(project_id=self.project_id)
y_count = self.db.count_for_year(year, project_id=self.project_id)
```

### Files Modified
- `reference_db.py` - 7 methods updated (150 insertions, 49 deletions)
- `sidebar_qt.py` - 6 call sites updated

---

## 🧪 Testing Results

### ✅ Scenario 1: Fresh Database + First Scan
**Status:** FIXED ✅
**Test:** Create project on fresh DB → Run scan → Sidebar loads
**Result:** No crash, sidebar displays correctly

### 🔴 Scenario 2: Create New Project After First Scan
**Status:** NEW CRASH DISCOVERED 🔴
**Test:** Complete first scan → Create new project → App crashes
**Log Evidence:**
```
[05:41:43.409] [INFO] Reloading sidebar after date branches built...
[SidebarQt] reload() called, display_mode=list
[SidebarQt] Calling _build_tree_model() instead of tabs refresh
[Sidebar] starting async count population for 1 branch targets
...
[05:42:05.381] [Tabs] _clear_tab idx=3
PS C:\Users\ASUS\...\MemoryMate-PhotoFlow-main-10>
```

**Analysis:**
- First scan completes successfully (298 photos indexed)
- Photos display in grid correctly
- User creates new project
- Sidebar attempts to reload
- App crashes (returns to PowerShell prompt)
- No Python exception logged

---

## 🔍 New Issue Analysis

### Crash Timeline
1. ✅ Project 1 created successfully
2. ✅ First scan completed (298 photos)
3. ✅ Date branches built
4. ✅ Sidebar reloaded
5. ✅ Photos displayed in grid
6. ❌ User creates Project 2
7. ❌ Sidebar reload triggered
8. 🔴 **CRASH** - App exits

### Suspected Root Causes

#### Hypothesis 1: Project Switching Logic
- New project may have `None` or invalid `project_id`
- Sidebar tries to query with bad project_id
- Database query fails or returns unexpected results

#### Hypothesis 2: Database State Inconsistency
- `project_images` table may not be populated for new project
- Queries return empty results causing UI assertion failures
- Qt crashes when model receives invalid data

#### Hypothesis 3: Resource Cleanup Issue
- Previous project's resources not cleaned up
- New project initialization conflicts with old state
- Memory corruption or Qt object lifecycle issue

#### Hypothesis 4: Async Worker Thread Issue
- Sidebar count workers still running from previous project
- New project triggers conflicting worker generation
- Thread race condition causes crash

---

## 🔧 Next Steps for Debugging

### 1. Add Comprehensive Logging
```python
# In sidebar_qt.py set_project()
def set_project(self, project_id: int):
    print(f"[SidebarQt] set_project called: old={self.project_id}, new={project_id}")
    self.project_id = project_id
    print(f"[SidebarQt] Calling reload() with project_id={self.project_id}")
    self.reload()
```

### 2. Add Database Query Validation
```python
# In reference_db.py
def get_quick_date_counts(self, project_id: int | None = None):
    print(f"[DB] get_quick_date_counts: project_id={project_id}")
    if project_id is not None:
        # Verify project exists
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM projects WHERE id = ?", (project_id,))
            if cur.fetchone()[0] == 0:
                print(f"[DB] WARNING: project_id={project_id} not found!")
                return []
```

### 3. Add Worker Cancellation Logging
```python
# In sidebar_qt.py
def _async_populate_counts(self):
    gen = self._list_worker_gen
    print(f"[Sidebar] Starting count worker gen={gen}, project_id={self.project_id}")
```

### 4. Add Project Creation Logging
```python
# In main_window_qt.py breadcrumb project creation
db = ReferenceDB()
print(f"[Breadcrumb] Creating new project: name={project_name}")
proj_id = db.create_project(project_name.strip(), folder="", mode="scan")
print(f"[Breadcrumb] Created project_id={proj_id}")
```

### 5. Catch Qt Exceptions
```python
# Add to main_qt.py
import sys
import traceback

def exception_hook(exctype, value, tb):
    print("=" * 80)
    print("UNHANDLED EXCEPTION:")
    print("=" * 80)
    traceback.print_exception(exctype, value, tb)
    print("=" * 80)
    sys.__excepthook__(exctype, value, tb)

sys.excepthook = exception_hook
```

---

## 📊 Impact Assessment

### What's Fixed ✅
- ✅ No crash after initial scan on fresh database
- ✅ Sidebar correctly filters by project_id
- ✅ Photo counts accurate for current project
- ✅ Multi-project data isolation

### What's Broken 🔴
- 🔴 Crash when creating new project after first scan
- 🔴 Project switching may have initialization issues
- 🔴 Unknown crash location (no Python traceback)

---

## 🎯 Recommended Next Session Plan

### Priority 1: Identify Exact Crash Location
1. Add logging to all project switching code paths
2. Add logging to sidebar reload methods
3. Add logging to database queries with project_id
4. Add Qt exception hook to catch C++ crashes

### Priority 2: Test Project Lifecycle
1. Test: Create project 1 → Scan → Switch to project 2 (empty)
2. Test: Create project 1 → Scan → Create project 2 → Switch back to project 1
3. Test: Create multiple projects without scanning
4. Test: Delete project after scan

### Priority 3: Review Project Initialization
1. Audit `create_project()` method in `reference_db.py`
2. Audit `set_project()` method in `sidebar_qt.py`
3. Audit `set_project()` method in `grid_qt.py`
4. Audit breadcrumb project creation logic

---

## 💾 Commit History

| Commit | Description | Status |
|--------|-------------|--------|
| a16dc04 | Fix: Critical crash after scan on fresh DB - project_id filtering missing | ✅ Pushed |

**Branch:** claude/debug-issue-011CUstrEnRPeyq1j7XfX7h1
**Files Changed:** 2 (reference_db.py, sidebar_qt.py)
**Lines Changed:** +150, -49

---

## 📝 Notes for Next Session

1. **User is taking a break** - will resume debugging later
2. **Original crash is FIXED** - scan completion works now
3. **New crash discovered** - creating new project after first scan
4. **No Python traceback** - likely Qt/C++ level crash or silent exit
5. **Log shows clean exit** - no exception, just returns to prompt
6. **Need more logging** - add comprehensive debug logging before next test

---

## 🔗 Related Issues

- Original Issue: "app crashed after finish scan after creating the project on fresh db" ✅ FIXED
- New Issue: "app crash is persistent after creating a new project after a first foto scan" 🔴 ACTIVE

---

**Status Updated:** 2025-11-07 12:16:05 (user time)
**Current Status:** ✅ ROOT CAUSE IDENTIFIED - Implementing comprehensive fix

---

## 🆕 New Crash Analysis (Session 2)

### Crash Pattern Discovery

Analysis of two fresh crash logs revealed a **critical Qt widget lifecycle issue**:

**Common Pattern:**
- Both crashes occurred during tab/list mode switching
- Both crashes were silent (no Python traceback)
- Crashes happened when tab workers completed AFTER tabs were hidden
- Last log entry: `_load_tags → got 0 rows` (worker thread)
- No `_finish_tags` logs (signal handler never ran or crashed before logging)

### Root Cause #2: Race Condition in Widget Lifecycle

The application has a **race condition** between:
1. **Tab workers** completing asynchronously and emitting signals
2. **Mode switching** hiding tabs and invalidating UI state
3. **reload()** calling `refresh_all()` on hidden tabs

**Crash Sequence:**
1. User switches from tabs to list mode
2. `hide_tabs()` bumps worker generations and hides tabs
3. `processEvents()` processes pending signal deliveries
4. **BUG:** `reload()` can still call `refresh_all()` on hidden tabs
5. Worker signals delivered to finish handlers
6. **Finish handlers access deleted/invalid widgets**
7. **Segmentation fault** (Qt C++ crash, no Python traceback)

### Key Issues Found

#### Issue 1: Missing Null Checks in _clear_tab()
```python
# BEFORE (sidebar_qt.py:332-343)
def _clear_tab(self, idx):
    tab = self.tab_widget.widget(idx)
    if not tab: return
    v = tab.layout()  # ⚠️ Could be None
    for i in reversed(range(v.count())):
        w = v.itemAt(i).widget()  # ⚠️ itemAt(i) could be None
```

**Problem:** No null checks for layout or layout items, causing crashes when widgets are being deleted.

#### Issue 2: Missing Null Checks in _finish_tags()
```python
# BEFORE (sidebar_qt.py:804-805)
tab = self.tab_widget.widget(idx)
tab.layout().addWidget(QLabel("<b>Tags</b>"))  # ⚠️ No null check
```

**Problem:** Direct widget access without checking if tab/layout exists.

#### Issue 3: reload() Refreshes Hidden Tabs
```python
# BEFORE (sidebar_qt.py:2077-2081)
def reload(self):
    mode = self._effective_display_mode()
    if mode == "tabs":
        self.tabs_controller.refresh_all(force=True)  # ⚠️ Even if hidden!
```

**Problem:** `reload()` checks settings mode but doesn't check if tabs are actually visible, causing refresh of hidden tabs.

---

## ✅ Solution Implemented (This Session)

### Changes Made

#### 1. Enhanced _clear_tab() with Safety Checks
**File:** `sidebar_qt.py` (lines 332-356)

Added comprehensive null checks and exception handling:
```python
def _clear_tab(self, idx):
    self._dbg(f"_clear_tab idx={idx}")
    self._cancel_timeout(idx)

    tab = self.tab_widget.widget(idx)
    if not tab:
        self._dbg(f"_clear_tab idx={idx} - tab is None, skipping")
        return

    v = tab.layout()
    if not v:
        self._dbg(f"_clear_tab idx={idx} - layout is None, skipping")
        return

    try:
        for i in reversed(range(v.count())):
            item = v.itemAt(i)
            if not item:
                continue
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
    except Exception as e:
        self._dbg(f"_clear_tab idx={idx} - Exception: {e}")
        traceback.print_exc()
```

#### 2. Enhanced _finish_tags() with Safety Checks
**File:** `sidebar_qt.py` (lines 810-826)

Added null checks before widget access:
```python
def _finish_tags(self, idx, rows, started, gen):
    self._dbg(f"_finish_tags called: idx={idx}, gen={gen}, rows_count={len(rows) if rows else 0}")
    if self._is_stale("tags", gen):
        return

    tab = self.tab_widget.widget(idx)
    if not tab:
        self._dbg(f"_finish_tags - tab is None, aborting")
        return

    layout = tab.layout()
    if not layout:
        self._dbg(f"_finish_tags - layout is None, aborting")
        return

    layout.addWidget(QLabel("<b>Tags</b>"))
```

#### 3. Fixed reload() to Check Tab Visibility
**File:** `sidebar_qt.py` (lines 2113-2149)

Added visibility check to prevent refreshing hidden tabs:
```python
def reload(self):
    mode = self._effective_display_mode()
    tabs_visible = self.tabs_controller.isVisible()
    print(f"[SidebarQt] reload() called, mode={mode}, tabs_visible={tabs_visible}")

    # CRITICAL FIX: Only refresh tabs if actually visible
    if mode == "tabs" and tabs_visible:
        self.tabs_controller.refresh_all(force=True)
    elif mode == "tabs" and not tabs_visible:
        print(f"[SidebarQt] WARNING: mode=tabs but tabs not visible, skipping")
    else:
        self._build_tree_model()
```

#### 4. Added Global Exception Hook
**File:** `main_qt.py` (lines 45-56)

Installed Python exception hook to catch unhandled exceptions:
```python
import traceback
def exception_hook(exctype, value, tb):
    print("=" * 80)
    print("UNHANDLED EXCEPTION CAUGHT:")
    print("=" * 80)
    traceback.print_exception(exctype, value, tb)
    logger.error("Unhandled exception", exc_info=(exctype, value, tb))

sys.excepthook = exception_hook
```

#### 5. Added Comprehensive Logging

Added detailed logging throughout:
- Mode switching operations
- Tab visibility state
- Widget access operations
- Error conditions

**Benefits:**
- Easier debugging of future issues
- Clear audit trail of operations
- Early detection of invalid states

### Files Modified
- `sidebar_qt.py` - 100+ lines modified (safety checks, logging)
- `main_qt.py` - 12 lines added (exception hook)

---

## 🧪 Expected Results

### What Should Be Fixed ✅
- ✅ No crash when tabs receive worker signals after being hidden
- ✅ No crash from accessing null widgets/layouts
- ✅ No crash when reload() called after mode switch
- ✅ Better error messages for debugging
- ✅ Graceful handling of widget lifecycle edge cases

### What to Test
1. **Scenario 1:** Fresh DB → Scan → Switch modes repeatedly
2. **Scenario 2:** Scan → Create new project → Switch modes
3. **Scenario 3:** Rapid mode switching during tab loading
4. **Scenario 4:** Reload during mode transitions

---

## 📝 Next Steps

1. **User Testing:** User should test with provided crash scenario
2. **Monitor Logs:** Check for new WARNING/ERROR messages
3. **Verify Fix:** Confirm crashes no longer occur
4. **Performance:** Ensure no performance degradation

---

**Status Updated:** 2025-11-07 (current session)
**Next Action:** Commit changes and push for user testing
**Fix Status:** 🟢 COMPREHENSIVE FIX IMPLEMENTED
