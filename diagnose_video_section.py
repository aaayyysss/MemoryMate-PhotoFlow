#!/usr/bin/env python3
"""
Deep diagnostic script to audit why video section is not appearing.
This will check every step of the video loading process.
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 80)
print("VIDEO SECTION DIAGNOSTIC AUDIT")
print("=" * 80)

# Step 1: Check database file exists
print("\n1. DATABASE FILE CHECK")
print("-" * 80)
from db_config import get_db_path
db_path = get_db_path()
print(f"Database path: {db_path}")
print(f"Exists: {os.path.exists(db_path)}")
if os.path.exists(db_path):
    print(f"Size: {os.path.getsize(db_path)} bytes")

# Step 2: Check if video_metadata table exists
print("\n2. VIDEO_METADATA TABLE CHECK")
print("-" * 80)
try:
    from repository.video_repository import VideoRepository
    repo = VideoRepository()
    print("✅ VideoRepository imported successfully")

    # Check table exists by trying a query
    try:
        with repo.connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='video_metadata'")
            result = cur.fetchone()
            if result:
                print("✅ video_metadata table exists")

                # Check schema
                cur.execute("PRAGMA table_info(video_metadata)")
                columns = cur.fetchall()
                print(f"   Columns: {len(columns)}")
                for col in columns:
                    print(f"     - {col[1]} ({col[2]})")
            else:
                print("❌ video_metadata table DOES NOT EXIST")
    except Exception as e:
        print(f"❌ Error checking table: {e}")

except Exception as e:
    print(f"❌ Failed to import VideoRepository: {e}")
    import traceback
    traceback.print_exc()

# Step 3: Check for videos in database
print("\n3. VIDEOS IN DATABASE CHECK")
print("-" * 80)
try:
    from repository.video_repository import VideoRepository
    repo = VideoRepository()

    # Check each project
    for project_id in [1, 2, 3]:
        try:
            videos = repo.get_by_project(project_id)
            print(f"Project {project_id}: {len(videos)} videos")
            if videos:
                print(f"  Sample video: {videos[0]['path']}")
                print(f"  Sample video has created_date: {videos[0].get('created_date')}")
        except Exception as e:
            print(f"Project {project_id}: Error - {e}")

except Exception as e:
    print(f"❌ Failed: {e}")

# Step 4: Check VideoService
print("\n4. VIDEO SERVICE CHECK")
print("-" * 80)
try:
    from services.video_service import VideoService
    service = VideoService()
    print("✅ VideoService imported successfully")

    for project_id in [1, 2, 3]:
        try:
            videos = service.get_videos_by_project(project_id)
            print(f"Project {project_id}: {len(videos)} videos via service")
        except Exception as e:
            print(f"Project {project_id}: Error - {e}")
            import traceback
            traceback.print_exc()

except Exception as e:
    print(f"❌ Failed to import VideoService: {e}")
    import traceback
    traceback.print_exc()

# Step 5: Check sidebar code
print("\n5. SIDEBAR VIDEO SECTION CODE CHECK")
print("-" * 80)
try:
    with open('sidebar_qt.py', 'r', encoding='utf-8') as f:
        content = f.read()

    # Find the video section code
    if '🎬 Videos' in content:
        print("✅ Found '🎬 Videos' section in sidebar_qt.py")

        # Find the condition
        if 'if videos:' in content:
            print("✅ Found 'if videos:' condition")
        else:
            print("❌ Missing 'if videos:' condition")

        # Check for diagnostic logging
        if '[Sidebar] Loading videos for project_id=' in content:
            print("✅ Diagnostic logging present")
        else:
            print("⚠️  Diagnostic logging missing (old code?)")

    else:
        print("❌ '🎬 Videos' section NOT FOUND in sidebar_qt.py")

except Exception as e:
    print(f"❌ Failed to read sidebar_qt.py: {e}")

# Step 6: Simulate sidebar video loading
print("\n6. SIMULATE SIDEBAR VIDEO LOADING")
print("-" * 80)
try:
    from services.video_service import VideoService

    # Try with project_id = 1
    project_id = 1
    print(f"Simulating: Loading videos for project_id={project_id}")

    video_service = VideoService()
    videos = video_service.get_videos_by_project(project_id) if project_id else []
    total_videos = len(videos)

    print(f"Result: Found {total_videos} videos")

    if videos:
        print("✅ CONDITION MET: Video section SHOULD appear")
        print(f"   First 3 videos:")
        for v in videos[:3]:
            print(f"     - {v.get('path', 'N/A')}")
    else:
        print("❌ CONDITION NOT MET: Video section will NOT appear")
        print("   Reason: get_videos_by_project() returned empty list")

except Exception as e:
    print(f"❌ Simulation failed: {e}")
    import traceback
    traceback.print_exc()

# Step 7: Check app_services for default project
print("\n7. DEFAULT PROJECT CHECK")
print("-" * 80)
try:
    from app_services import get_default_project_id
    default_project = get_default_project_id()
    print(f"Default project_id: {default_project}")

    if default_project:
        from services.video_service import VideoService
        service = VideoService()
        videos = service.get_videos_by_project(default_project)
        print(f"Videos in default project: {len(videos)}")
    else:
        print("⚠️  No default project set")

except Exception as e:
    print(f"❌ Failed: {e}")

# Step 8: Final diagnosis
print("\n" + "=" * 80)
print("DIAGNOSIS SUMMARY")
print("=" * 80)

try:
    from repository.video_repository import VideoRepository
    from services.video_service import VideoService

    repo = VideoRepository()
    service = VideoService()

    # Count total videos across all projects
    total = 0
    for pid in [1, 2, 3]:
        try:
            videos = repo.get_by_project(pid)
            total += len(videos)
        except:
            pass

    print(f"\n📊 Total videos in database: {total}")

    if total == 0:
        print("\n❌ ROOT CAUSE: No videos in database")
        print("   SOLUTION: Scan video folders to index videos")
        print("   Steps:")
        print("     1. Open app")
        print("     2. File → Scan for Media")
        print("     3. Select video folder (e.g., D:\\my phone\\videos\\)")
        print("     4. Wait for scan to complete")
        print("     5. Video section should appear")
    else:
        print("\n⚠️  UNEXPECTED: Videos exist in database but section not showing")
        print("   Possible causes:")
        print("     1. Sidebar using wrong project_id")
        print("     2. Code not updated (need to pull latest)")
        print("     3. App not restarted after update")
        print("     4. Exception being silently caught")
        print("\n   Check app log for:")
        print("     - '[Sidebar] Loading videos for project_id=X'")
        print("     - '[Sidebar] Found X videos in project Y'")

except Exception as e:
    print(f"\n❌ Diagnostic failed: {e}")

print("\n" + "=" * 80)
print("Run this script to diagnose the video section issue")
print("=" * 80)
