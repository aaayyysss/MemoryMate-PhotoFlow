# Video Date Backfill Guide

## Why Do I Need This?

If you scanned videos **before** the date filtering fix (commit `ae27baa`), those videos don't have `date_taken` populated in the database. This causes:
- ❌ All date filters showing count: 0
- ❌ No videos appearing when clicking year filters (2021-2025)

## Solution: Backfill Existing Videos

The `backfill_video_dates.py` script will:
1. Find all videos missing `date_taken`
2. Re-extract metadata (including dates)
3. Update the database with dates

## How to Run

### Step 1: Close the Photo App
Make sure the MemoryMate-PhotoFlow app is **completely closed** before running the backfill script.

### Step 2: Run Dry Run (Optional but Recommended)
See what would be updated without making changes:

```bash
cd /path/to/MemoryMate-PhotoFlow
python backfill_video_dates.py --dry-run
```

You should see output like:
```
Found 653 total videos
Found 653 videos missing date_taken

Processing videos...
[1/653] Processing: video1.mp4
  → date_taken: 2024-03-15 14:30:00
  → created_date: 2024-03-15
  → created_year: 2024
  [DRY RUN] Would update video_id=1
...
```

### Step 3: Run the Actual Backfill
Update the database:

```bash
python backfill_video_dates.py
```

Or specify a project:
```bash
python backfill_video_dates.py --project-id 1
```

### Step 4: Restart the App
Close and reopen MemoryMate-PhotoFlow. The date filters should now work!

## What Gets Updated

For each video, the script updates:
- **date_taken**: Full datetime (YYYY-MM-DD HH:MM:SS)
- **created_date**: Date only (YYYY-MM-DD)
- **created_year**: Year as integer (YYYY)

## Date Extraction Strategy

The script uses 5 fallback strategies (in order):
1. Video format-level `creation_time` tag
2. Video format-level `date` tag (lowercase)
3. Video format-level `DATE` tag (uppercase)
4. Video stream-level creation/date tags
5. **File modified time** (ultimate fallback - guarantees all videos get dates)

## Troubleshooting

### "File not found" errors
Some videos in the database may no longer exist on disk. These will be skipped.

### "Failed to extract date_taken"
Very rare. The script should always extract at least the file modified time.

### Script hangs on a video
Some corrupted videos may cause ffprobe to hang. Press Ctrl+C to stop, and the partially completed work will be saved.

### Still showing 0 videos after backfill
1. Make sure you closed and reopened the app
2. Check the log output - did all videos update successfully?
3. Try clicking "All Videos" to verify the videos exist

## Performance

- Processes ~10-20 videos per second
- 653 videos should take ~30-60 seconds
- Progress is shown in real-time

## Alternative: Rescan Project

Instead of using the backfill script, you can also rescan your project:
1. Open MemoryMate-PhotoFlow
2. Start a new scan of your video folder
3. The scan will update existing videos with the new date extraction logic

However, the backfill script is faster since it only updates the date fields.
