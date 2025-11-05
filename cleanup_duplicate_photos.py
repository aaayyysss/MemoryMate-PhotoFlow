#!/usr/bin/env python3
"""
Cleanup script to remove duplicate photo entries from photo_metadata table.

This script removes duplicates that were created due to path format differences
(e.g., 'C:\\path\\photo.jpg' vs 'C:/path/photo.jpg').

Usage:
    python cleanup_duplicate_photos.py
"""

import sys
from repository.photo_repository import PhotoRepository
from logging_config import get_logger

logger = get_logger(__name__)


def main():
    """Run the duplicate cleanup."""
    print("=" * 70)
    print("Photo Metadata Duplicate Cleanup Tool")
    print("=" * 70)
    print()
    print("This tool will remove duplicate photo entries from the database.")
    print("Duplicates are caused by path format differences (e.g., backslash vs forward slash).")
    print()

    # Ask for confirmation
    response = input("Do you want to proceed? (yes/no): ").strip().lower()
    if response not in ('yes', 'y'):
        print("Cleanup cancelled.")
        return 0

    print()
    print("Starting cleanup...")
    print()

    try:
        # Create repository and run cleanup
        photo_repo = PhotoRepository()

        # Get stats before cleanup
        stats_before = photo_repo.get_statistics()
        total_before = stats_before['total_photos']

        print(f"Photos in database before cleanup: {total_before}")

        # Run cleanup
        deleted_count = photo_repo.cleanup_duplicate_paths()

        # Get stats after cleanup
        stats_after = photo_repo.get_statistics()
        total_after = stats_after['total_photos']

        print()
        print("=" * 70)
        print("Cleanup Complete!")
        print("=" * 70)
        print(f"Photos before cleanup: {total_before}")
        print(f"Photos after cleanup:  {total_after}")
        print(f"Duplicates removed:    {deleted_count}")
        print()

        if deleted_count > 0:
            print("✓ Successfully removed duplicate entries.")
            print()
            print("NOTE: The grid will now show the correct number of photos.")
        else:
            print("✓ No duplicates found - database is clean!")

        return 0

    except Exception as e:
        logger.error(f"Cleanup failed: {e}", exc_info=True)
        print()
        print("✗ Cleanup failed!")
        print(f"Error: {e}")
        print()
        print("Check the logs for more details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
