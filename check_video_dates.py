#!/usr/bin/env python3
"""Check video dates in database to diagnose date handling issues."""

import sqlite3
from collections import Counter
from datetime import datetime

def check_video_dates():
    """Check video dates in the database."""
    conn = sqlite3.connect('/home/user/MemoryMate-PhotoFlow/media.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print("=" * 80)
    print("VIDEO DATE AUDIT")
    print("=" * 80)

    # Get total videos
    cur.execute("SELECT COUNT(*) FROM video_metadata WHERE project_id = 1")
    total = cur.fetchone()[0]
    print(f"\n1. Total videos in project 1: {total}")

    # Get videos with dates
    cur.execute("SELECT COUNT(*) FROM video_metadata WHERE project_id = 1 AND created_date IS NOT NULL")
    with_dates = cur.fetchone()[0]
    print(f"2. Videos with created_date: {with_dates} ({100*with_dates/total if total > 0 else 0:.1f}%)")

    # Get unique years
    cur.execute("SELECT DISTINCT created_year FROM video_metadata WHERE project_id = 1 AND created_year IS NOT NULL ORDER BY created_year")
    years = [row[0] for row in cur.fetchall()]
    print(f"3. Unique years in videos: {years}")
    print(f"   Count: {len(years)} years")

    # Get year distribution
    cur.execute("""
        SELECT created_year, COUNT(*) as count
        FROM video_metadata
        WHERE project_id = 1 AND created_year IS NOT NULL
        GROUP BY created_year
        ORDER BY created_year DESC
    """)
    year_dist = cur.fetchall()
    print(f"\n4. Year distribution:")
    for year, count in year_dist:
        print(f"   {year}: {count} videos")

    # Get month distribution
    cur.execute("""
        SELECT SUBSTR(created_date, 1, 7) as year_month, COUNT(*) as count
        FROM video_metadata
        WHERE project_id = 1 AND created_date IS NOT NULL
        GROUP BY year_month
        ORDER BY year_month DESC
    """)
    month_dist = cur.fetchall()
    print(f"\n5. Month distribution: {len(month_dist)} unique months")
    for ym, count in month_dist[:10]:  # Show first 10
        print(f"   {ym}: {count} videos")
    if len(month_dist) > 10:
        print(f"   ... and {len(month_dist) - 10} more months")

    # Check if created_date matches modified
    cur.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN SUBSTR(created_date, 1, 10) = SUBSTR(modified, 1, 10) THEN 1 ELSE 0 END) as matching,
            SUM(CASE WHEN created_date IS NOT NULL AND modified IS NOT NULL THEN 1 ELSE 0 END) as both_present
        FROM video_metadata
        WHERE project_id = 1
    """)
    row = cur.fetchone()
    print(f"\n6. Date source analysis:")
    print(f"   Videos with both created_date and modified: {row['both_present']}")
    print(f"   Videos where created_date matches modified date: {row['matching']}")
    print(f"   Likely using file modified date: {row['matching']} / {row['both_present']} ({100*row['matching']/row['both_present'] if row['both_present'] > 0 else 0:.1f}%)")

    # Sample some video dates
    cur.execute("""
        SELECT path, created_date, created_year, modified, date_taken
        FROM video_metadata
        WHERE project_id = 1
        LIMIT 5
    """)
    print(f"\n7. Sample video dates:")
    for row in cur.fetchall():
        path = row['path'].split('/')[-1] if row['path'] else 'N/A'
        print(f"   {path}")
        print(f"      created_date: {row['created_date']}")
        print(f"      created_year: {row['created_year']}")
        print(f"      modified:     {row['modified']}")
        print(f"      date_taken:   {row['date_taken']}")

    # Check for photo date bleeding
    print(f"\n8. Checking for photo/video data mixing...")
    cur.execute("SELECT COUNT(*) FROM photo_metadata WHERE project_id = 1")
    photo_count = cur.fetchone()[0]
    print(f"   Total photos in project 1: {photo_count}")

    cur.execute("SELECT DISTINCT created_year FROM photo_metadata WHERE project_id = 1 AND created_year IS NOT NULL ORDER BY created_year")
    photo_years = [row[0] for row in cur.fetchall()]
    print(f"   Photo years: {photo_years}")

    # Check for overlap
    video_years_set = set(years)
    photo_years_set = set(photo_years)
    overlap = video_years_set & photo_years_set
    video_only = video_years_set - photo_years_set
    photo_only = photo_years_set - video_years_set

    print(f"\n9. Year overlap analysis:")
    print(f"   Years in both photos and videos: {sorted(overlap)}")
    print(f"   Years only in videos: {sorted(video_only)}")
    print(f"   Years only in photos: {sorted(photo_only)}")

    # Check project_videos table
    cur.execute("SELECT COUNT(*) FROM project_videos WHERE project_id = 1")
    pv_count = cur.fetchone()[0]
    print(f"\n10. Branch integration:")
    print(f"   Entries in project_videos table: {pv_count}")

    if pv_count == 0:
        print(f"   ⚠️  WARNING: project_videos is empty!")
        print(f"   Videos not integrated into branches system!")
        print(f"   Need to run build_video_date_branches()")

    conn.close()

    print("\n" + "=" * 80)

if __name__ == '__main__':
    check_video_dates()
