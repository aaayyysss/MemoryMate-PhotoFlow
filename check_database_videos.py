#!/usr/bin/env python3
"""
Simple check: Are videos in the database?
This is THE reason video section doesn't appear.
"""

import sqlite3
import os
from pathlib import Path

# Find database file
possible_paths = [
    'reference_data.db',
    '../reference_data.db',
    'media.db',
    '../media.db',
]

db_path = None
for path in possible_paths:
    if os.path.exists(path):
        db_path = path
        break

if not db_path:
    print("❌ Database file not found!")
    print(f"   Tried: {possible_paths}")
    print("\nSearching current directory...")
    for f in Path('.').glob('*.db'):
        print(f"   Found: {f}")
        db_path = str(f)
        break

if not db_path:
    print("\n❌ No database file found. Cannot check videos.")
    exit(1)

print("=" * 80)
print("VIDEO DATABASE CHECK")
print("=" * 80)
print(f"\nDatabase: {db_path}")
print(f"Size: {os.path.getsize(db_path):,} bytes")

# Connect and check
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Check if video_metadata table exists
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='video_metadata'")
if not cur.fetchone():
    print("\n❌ video_metadata table doesn't exist!")
    print("   Need to run schema migration")
    exit(1)

print("\n✅ video_metadata table exists")

# Count videos by project
print("\n" + "=" * 80)
print("VIDEOS BY PROJECT")
print("=" * 80)

cur.execute("""
    SELECT
        project_id,
        COUNT(*) as video_count,
        COUNT(CASE WHEN created_date IS NOT NULL THEN 1 END) as with_dates
    FROM video_metadata
    GROUP BY project_id
    ORDER BY project_id
""")

projects = cur.fetchall()

if not projects:
    print("\n❌ NO VIDEOS IN DATABASE")
    print("\nThis is why video section doesn't appear!")
    print("\nThe condition is:")
    print("   videos = get_videos_by_project(project_id)")
    print("   if videos:  # ← This is False because list is empty")
    print("       # Show video section")
    print("\nSOLUTION:")
    print("   1. Open app")
    print("   2. File → Scan for Media (or equivalent)")
    print("   3. Select your videos folder (e.g., D:\\my phone\\videos\\)")
    print("   4. Wait for scan to complete")
    print("   5. Video section will appear!")
    print("\nOnce videos are scanned:")
    print("   - Video section will show with all filters")
    print("   - Date tree will include videos with photos")
    print("   - Counts will be accurate (photos + videos)")
else:
    total = 0
    for row in projects:
        total += row['video_count']
        print(f"\nProject {row['project_id']}:")
        print(f"  Total videos: {row['video_count']}")
        print(f"  With dates: {row['with_dates']}")

    print(f"\n✅ TOTAL VIDEOS: {total}")
    print("\nVideos exist in database!")
    print("\nIf video section still doesn't appear, check:")
    print("  1. App using correct project_id?")
    print("  2. Code updated? (git pull)")
    print("  3. App restarted after update?")
    print("  4. Check app log for:")
    print("     - '[Sidebar] Loading videos for project_id=X'")
    print("     - '[Sidebar] Found X videos in project Y'")

# Sample some videos
print("\n" + "=" * 80)
print("SAMPLE VIDEOS")
print("=" * 80)

cur.execute("""
    SELECT path, created_date, created_year, modified, date_taken
    FROM video_metadata
    LIMIT 5
""")

samples = cur.fetchall()
if samples:
    for i, row in enumerate(samples, 1):
        print(f"\n{i}. {Path(row['path']).name}")
        print(f"   created_date: {row['created_date']}")
        print(f"   created_year: {row['created_year']}")
        print(f"   date_taken: {row['date_taken']}")
        print(f"   modified: {row['modified']}")
else:
    print("\nNo videos to sample")

conn.close()

print("\n" + "=" * 80)
