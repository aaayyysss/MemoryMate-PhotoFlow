#!/usr/bin/env python3
"""Check what tables exist in the database."""

import sqlite3

def check_tables():
    """Check tables in the database."""
    conn = sqlite3.connect('/home/user/MemoryMate-PhotoFlow/media.db')
    cur = conn.cursor()

    print("=" * 80)
    print("DATABASE TABLES")
    print("=" * 80)

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cur.fetchall()]

    print(f"\nFound {len(tables)} tables:")
    for table in tables:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        count = cur.fetchone()[0]
        print(f"  - {table}: {count} rows")

    # Check for video-related tables
    print("\nVideo-related tables:")
    video_tables = [t for t in tables if 'video' in t.lower()]
    if video_tables:
        for table in video_tables:
            print(f"\nTable: {table}")
            cur.execute(f"PRAGMA table_info({table})")
            columns = cur.fetchall()
            print(f"  Columns:")
            for col in columns:
                print(f"    - {col[1]} ({col[2]})")
    else:
        print("  No video tables found!")

    conn.close()

if __name__ == '__main__':
    check_tables()
