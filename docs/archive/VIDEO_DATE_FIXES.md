# Video Date & Scan Cancellation Issues - Analysis & Fix Plan

## Issues Identified

### 1. VideoService.filter_by_date() Missing Month Parameter ❌
**Problem**: The method signature lacks `month` parameter support:
```python
def filter_by_date(self, videos, start_date=None, end_date=None, year=None, use_modified=False):
```

But somewhere the code is calling it with `month=11` which causes:
```
TypeError: filter_by_date() got an unexpected keyword argument 'month'
```

### 2. Video Date Hierarchy Not Built in Sidebar ❌
**Problem**: Sidebar only shows years for videos (lines 1972-1992), not months/days like photos have.

Photos have:
- 📅 By Date
  - 2024 (year)
    - January (month)
      - 2024-01-15 (day)

Videos only have:
- 📅 By Date
  - 2024 (year)
  - 2023 (year)

No month/day drill-down!

### 3. Videos Not Showing Dates Until Restart ❌
**Problem**: Video `created_date` and `created_year` fields aren't being populated during scan.

Looking at `photo_scan_service.py` line 753:
```python
video_service.index_video(
    path=str(video_path),
    project_id=project_id,
    folder_id=folder_id,
    size_kb=size_kb,
    modified=modified  # <-- Only passes modified, no created_date!
)
```

Video metadata extraction (date_taken) happens in BACKGROUND workers AFTER scan completes!
So videos have no dates until workers finish → restart needed to see dates in tree.

### 4. Scan Cancellation Still Not Responsive Enough ❌
User reports cancellation still not working. Need to check:
- Are there more long-running operations without cancellation checks?
- Is the ThreadPoolExecutor actually cancelling futures?

## Root Causes

1. **VideoService API incompleteness**: Missing month parameter for filtering
2. **Sidebar incomplete video support**: No month/day hierarchy like photos
3. **Scan timing issue**: Video dates populated async AFTER scan, tree built BEFORE dates exist
4. **Architecture mismatch**: Photos get dates during scan, videos get dates from background workers

## Proposed Solutions

### Fix 1: Add Month Parameter to VideoService.filter_by_date()
```python
def filter_by_date(self, videos: List[Dict[str, Any]],
                  start_date: str = None,
                  end_date: str = None,
                  year: int = None,
                  month: int = None,  # <-- ADD THIS
                  use_modified: bool = False) -> List[Dict[str, Any]]:
    """
    Filter videos by date taken or modified.

    Args:
        year: Year filter (2024)
        month: Month filter (1-12) - requires year parameter
        start_date/end_date: Date range (YYYY-MM-DD)
    """
    # If both year and month provided, convert to date range
    if year is not None and month is not None:
        start_date = f"{year}-{month:02d}-01"
        # Calculate last day of month
        if month == 12:
            end_date = f"{year}-12-31"
        else:
            import calendar
            last_day = calendar.monthrange(year, month)[1]
            end_date = f"{year}-{month:02d}-{last_day}"
    elif year is not None:
        start_date = f"{year}-01-01"
        end_date = f"{year}-12-31"

    # ... rest of existing logic
```

### Fix 2: Build Video Date Hierarchy with Months/Days
In `sidebar_qt.py` around line 1972, expand to include months like photos:

```python
# Get video date hierarchy (year -> month -> days)
video_date_hier = self.db.get_video_date_hierarchy(self.project_id)

for year in sorted(video_date_hier.keys(), reverse=True):
    year_item = QStandardItem(str(year))
    year_item.setEditable(False)
    year_item.setData("videos_year", Qt.UserRole)
    year_item.setData(year, Qt.UserRole + 1)

    # Count videos for year
    year_count = self.db.count_videos_for_year(year, self.project_id)
    year_cnt = QStandardItem(str(year_count))
    year_cnt.setEditable(False)
    year_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    year_cnt.setForeground(QColor("#888888"))
    date_parent.appendRow([year_item, year_cnt])

    # Add months under year
    months_data = video_date_hier[year]
    for month in sorted(months_data.keys()):
        month_item = QStandardItem(MONTH_NAMES[int(month)])
        month_item.setEditable(False)
        month_item.setData("videos_month", Qt.UserRole)
        month_item.setData(f"{year}-{month}", Qt.UserRole + 1)

        month_count = self.db.count_videos_for_month(year, month, self.project_id)
        month_cnt = QStandardItem(str(month_count))
        month_cnt.setEditable(False)
        month_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        month_cnt.setForeground(QColor("#888888"))
        year_item.appendRow([month_item, month_cnt])
```

### Fix 3: Add videos_month Click Handler
In `_tree_item_clicked` method around line 1540, add:

```python
elif mode == "videos_month" and value:
    _clear_tag_if_needed()
    print(f"[Sidebar] Filtering videos by month: {value}")
    try:
        from services.video_service import VideoService
        video_service = VideoService()
        videos = video_service.get_videos_by_project(self.project_id) if self.project_id else []

        # Parse year-month from value (format: "2024-11")
        year, month = value.split("-")
        filtered = video_service.filter_by_date(videos, year=int(year), month=int(month))

        paths = [v['path'] for v in filtered]

        print(f"[Sidebar] Showing {len(filtered)} videos from {value}")
        if hasattr(mw, "grid") and hasattr(mw.grid, "load_custom_paths"):
            mw.grid.model.clear()
            mw.grid.load_custom_paths(paths, content_type="videos")
            mw.statusBar().showMessage(f"📅 Showing {len(filtered)} videos from {value}")
    except Exception as e:
        print(f"[Sidebar] Failed to filter videos by month: {e}")
```

### Fix 4: Populate Video created_date During Scan (CRITICAL)
**Option A: Quick Extract During Scan** (Recommended)
In `photo_scan_service.py` around line 740-760, extract basic video date:

```python
try:
    # Quick date extraction for immediate tree population
    # Full metadata extracted by background workers later
    from services.video_metadata_service import VideoMetadataService
    meta_service = VideoMetadataService()

    # Quick ffprobe call for just creation_time (timeout: 2s)
    video_date = meta_service.extract_date_only(str(video_path), timeout=2.0)

    # Compute created_* fields like photos do
    if video_date:
        created_ts, created_date, created_year = compute_created_fields(video_date, modified)
    else:
        created_ts, created_date, created_year = compute_created_fields(None, modified)

    # Index video WITH dates
    video_service.index_video(
        path=str(video_path),
        project_id=project_id,
        folder_id=folder_id,
        size_kb=size_kb,
        modified=modified,
        created_ts=created_ts,  # <-- ADD
        created_date=created_date,  # <-- ADD
        created_year=created_year  # <-- ADD
    )
except Exception as e:
    logger.warning(f"Failed to extract video date: {e}")
    # Fall back to using modified date
    created_ts, created_date, created_year = compute_created_fields(None, modified)
    video_service.index_video(...)
```

**Option B: Use Modified Date as Fallback** (Simpler)
Always populate created_* from modified date during scan:

```python
# Always populate date fields from modified time
created_ts, created_date, created_year = compute_created_fields(None, modified)

video_service.index_video(
    path=str(video_path),
    project_id=project_id,
    folder_id=folder_id,
    size_kb=size_kb,
    modified=modified,
    created_ts=created_ts,
    created_date=created_date,
    created_year=created_year
)
```

Then background workers can UPDATE with proper date_taken when extracted.

### Fix 5: Improve Scan Cancellation (Check More Locations)
Add cancellation checks in:
1. Video processing loop (already has one, but check it's working)
2. Before launching background workers
3. During executor.submit() calls

## Implementation Priority

1. **HIGH**: Fix VideoService.filter_by_date() to accept month parameter
2. **HIGH**: Populate video created_date during scan (Option B - use modified)
3. **MEDIUM**: Build month/day hierarchy for videos in sidebar
4. **MEDIUM**: Add videos_month click handler
5. **LOW**: Verify scan cancellation works with new checks

## Testing Plan

1. Start scan of large directory with videos
2. Click cancel during scan → should stop within 5s
3. Check video dates in sidebar → should show immediately after scan
4. Click on video year → should show videos from that year
5. Click on video month → should show videos from that month (after fix)
6. Restart app → video dates should remain visible

---

**Files to Modify**:
1. `services/video_service.py` - Add month parameter
2. `sidebar_qt.py` - Build month hierarchy, add click handler
3. `services/photo_scan_service.py` - Populate video dates during scan
4. `services/video_service.py` - Update index_video signature
