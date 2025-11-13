# Fix: Empty Video Date Branches

**Issue:** Video date branches in sidebar showing empty (no years/dates)
**Cause:** Existing videos don't have `created_date`, `created_year`, `created_ts` fields populated
**Solution:** Run backfill script to populate date fields from video metadata

---

## Quick Fix

Run this command to populate video dates for your project:

```bash
python backfill_video_dates.py --project-id 1
```

Replace `1` with your actual project ID if different.

---

## What's Happening?

1. **The Problem:**
   - Video date branches query `created_year` from the `video_metadata` table
   - Existing videos (scanned before Nov 2025) don't have these fields populated
   - The sidebar query returns empty results → no date branches shown

2. **The Fix:**
   - The backfill script re-extracts metadata from your video files
   - Populates 4 date fields:
     - `date_taken` - Full date/time from video metadata
     - `created_date` - Date only (YYYY-MM-DD)
     - `created_year` - Year only (e.g., 2024)
     - `created_ts` - Unix timestamp
   - These enable efficient date filtering in the sidebar

3. **Already Fixed for New Videos:**
   - Videos scanned AFTER this fix (Nov 2025) get dates automatically
   - Only existing/old videos need backfill

---

## Step-by-Step Instructions

### 1. Check Your Project ID

If you don't know your project ID:

```bash
# View all projects
sqlite3 reference_data.db "SELECT id, name FROM projects;"
```

Or check in the application (usually displayed in titlebar or project selector).

### 2. Run Backfill (Dry Run First)

Test what will happen without making changes:

```bash
python backfill_video_dates.py --project-id 1 --dry-run
```

You'll see:
- How many videos need date updates
- What dates will be extracted
- No actual changes made yet

### 3. Run Backfill (For Real)

Once you're satisfied, run without `--dry-run`:

```bash
python backfill_video_dates.py --project-id 1
```

This will:
- ✓ Extract dates from video files using ffprobe
- ✓ Update database with date fields
- ✓ Show progress for each video
- ✓ Display summary of results

### 4. Restart Application

After backfill completes:
1. Close the MemoryMate application
2. Restart it
3. Check the video section in sidebar
4. Date branches should now show with years and counts!

---

## Backfill Output Example

```
================================================================================
VIDEO DATE BACKFILL SCRIPT
================================================================================

Processing videos for project_id=1
Found 127 total videos

Found 127 videos missing date_taken

Processing videos...
--------------------------------------------------------------------------------
[1/127] Processing: vacation_2023.mp4
  → date_taken: 2023-07-15 14:32:10
  → created_date: 2023-07-15
  → created_year: 2023
  → created_ts: 1689429130
  ✓ Updated video_id=1

[2/127] Processing: birthday_party.MOV
  → date_taken: 2024-03-20 18:45:22
  → created_date: 2024-03-20
  → created_year: 2024
  → created_ts: 1710959122
  ✓ Updated video_id=2

... [125 more videos]

================================================================================
SUMMARY
================================================================================
Total videos:          127
Missing dates:         127
Successfully updated:  125
Failed:                2
Skipped (not found):   0

✓ Video dates backfilled successfully!
```

---

## Troubleshooting

### "File not found" errors
- Video files may have been moved/deleted
- Script skips missing files automatically
- Check paths if many failures

### "Failed to extract date_taken" errors
- Some videos don't have embedded date metadata
- Transcoded/downloaded videos often lack dates
- These videos will remain without dates (expected)
- They won't appear in date branches

### Script hangs/slow
- ffprobe can take 1-5 seconds per video
- 100 videos = ~3-8 minutes
- Be patient, watch progress output
- Script shows [current/total] for each video

### Still no dates after backfill
1. **Check database:**
   ```bash
   sqlite3 reference_data.db "SELECT COUNT(*) as with_year FROM video_metadata WHERE created_year IS NOT NULL;"
   ```
   Should show count > 0

2. **Check specific video:**
   ```bash
   sqlite3 reference_data.db "SELECT path, created_year, created_date FROM video_metadata WHERE id = 1;"
   ```
   Should show populated fields

3. **Restart application** - sidebar caches data on startup

---

## Alternative: Rescan Videos

Instead of backfill, you can rescan your video collection:

1. Open MemoryMate application
2. Go to project settings or scan dialog
3. Rescan your video folders
4. New scan will populate dates automatically

**Note:** Rescanning may take longer than backfill if you have many videos.

---

## What Changed?

**Previously (Buggy):**
- VideoMetadataWorker only saved `date_taken`
- Didn't populate `created_date`, `created_year`, `created_ts`
- Date hierarchy queries returned empty (field was NULL)

**Now (Fixed):**
- VideoMetadataWorker populates ALL date fields automatically
- New videos get dates on first scan
- Backfill script fixes existing videos

**Commits:**
- BUG FIX #6: Video date fields population
- Workers/video_metadata_worker.py: Lines 136-163
- backfill_video_dates.py: Updated to include `created_ts`

---

## Technical Details

### Database Schema

```sql
CREATE TABLE video_metadata (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    project_id INTEGER,
    date_taken TEXT,           -- Full datetime: '2024-03-20 18:45:22'
    created_date TEXT,          -- Date only: '2024-03-20'
    created_year INTEGER,       -- Year only: 2024
    created_ts INTEGER,         -- Unix timestamp: 1710959122
    -- ... other fields ...
);
```

### Query Used by Sidebar

```python
def list_video_years_with_counts(self, project_id):
    """Get years with video counts for sidebar."""
    cur.execute("""
        SELECT created_year, COUNT(*)
        FROM video_metadata
        WHERE project_id = ?
          AND created_year IS NOT NULL
        GROUP BY created_year
        ORDER BY created_year DESC
    """, (project_id,))
    return cur.fetchall()
```

If `created_year` is NULL for all videos → returns empty list → sidebar shows no years.

---

## Prevention

**For future videos**, this is already handled!

When you scan new videos:
1. VideoMetadataService extracts `date_taken` from ffprobe
2. VideoMetadataWorker computes `created_date`, `created_year`, `created_ts`
3. All fields saved to database
4. Date branches populate automatically

Only videos scanned BEFORE Nov 2025 need backfill.

---

## Need Help?

If backfill script fails or you need assistance:

1. Check the log output for specific errors
2. Try dry-run mode first to see what will happen
3. Verify ffprobe is installed and working:
   ```bash
   ffprobe -version
   ```
4. Check database file exists and is writable
5. Make sure no other application is accessing the database

---

**Created:** 2025-11-11
**Issue:** Empty video date branches
**Status:** Fix available - run backfill script
**Affected:** Videos scanned before Nov 2025
