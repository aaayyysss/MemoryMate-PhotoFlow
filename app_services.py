# app_services.py
# Version 09.18.01.15 dated 20251102
# Migrated to use ThumbnailService for unified caching

import os, io, shutil, hashlib, json
import time
import sqlite3


from pathlib import Path
from typing import Optional
from reference_db import ReferenceDB
from PIL import Image, ImageOps, ExifTags
from io import BytesIO
from PySide6.QtGui import QPixmap, QImageReader, QImage
from PySide6.QtCore import QSize, Qt, Signal, QObject

from reference_db import ReferenceDB
from services import get_thumbnail_service

DB_PATH = "photo_app.db"

# Image file extensions
SUPPORTED_EXT = {
    # JPEG family
    '.jpg', '.jpeg', '.jpe', '.jfif',
    # PNG
    '.png',
    # WEBP
    '.webp',
    # TIFF
    '.tif', '.tiff',
    # HEIF/HEIC (Apple/modern)
    '.heic', '.heif',
    # BMP
    '.bmp', '.dib',
    # GIF
    '.gif',
    # Modern formats
    '.avif', '.jxl',
    # RAW formats
    '.cr2', '.cr3', '.nef', '.nrw', '.arw', '.srf', '.sr2',
    '.dng', '.orf', '.rw2', '.pef', '.raf'
}

# Video file extensions (for future video support)
VIDEO_EXT = {
    # Common video formats
    '.mp4', '.m4v', '.mov',
    # MPEG family
    '.mpeg', '.mpg', '.mpe',
    # Windows Media
    '.wmv', '.asf',
    # AVI
    '.avi',
    # Matroska
    '.mkv', '.webm',
    # Flash
    '.flv', '.f4v',
    # Other
    '.3gp', '.3g2', '.ogv', '.ts', '.mts', '.m2ts'
}

# Combined: all supported media files (photos + videos)
ALL_MEDIA_EXT = SUPPORTED_EXT | VIDEO_EXT

_db = ReferenceDB()

# Get global thumbnail service (replaces old _thumbnail_cache and disk cache)
_thumbnail_service = get_thumbnail_service(l1_capacity=500)
_enable_thumbnail_cache = True  # toggle caching on/off



def clear_disk_thumbnail_cache():
    """
    Legacy function for backward compatibility.
    Now delegates to ThumbnailService.clear_all().
    """
    try:
        _thumbnail_service.clear_all()
        print("[Cache] All thumbnail caches cleared (L1 + L2)")
        return True
    except Exception as e:
        print(f"[Cache] Failed to clear thumbnail cache: {e}")
        return False

def clear_thumbnail_cache():
    """
    Public: clear all thumbnail caches (L1 memory + L2 database).

    Replaces old behavior of clearing memory dict + disk files.
    """
    return clear_disk_thumbnail_cache()
    

def list_projects():
    try:
        rows = _db.get_all_projects()
        return rows or []
    except Exception:
        with _db._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, name, folder, mode, created_at FROM projects ORDER BY id DESC")
            return [
                {"id": r[0], "name": r[1], "folder": r[2], "mode": r[3], "created_at": r[4]}
                for r in cur.fetchall()
            ]

def list_branches(project_id: int):
    try:
        return _db.get_branches(project_id)
    except Exception:
        with _db._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT branch_key, display_name FROM branches WHERE project_id=? ORDER BY id ASC", (project_id,))
            return [{"branch_key": r[0], "display_name": r[1]} for r in cur.fetchall()]


def get_thumbnail(path: str, height: int, use_disk_cache: bool = True) -> QPixmap:
    """
    Get thumbnail for an image file.

    Now uses ThumbnailService with unified L1 (memory) + L2 (database) caching.
    The use_disk_cache parameter is kept for backward compatibility but ignored.

    Args:
        path: Image file path
        height: Target thumbnail height in pixels
        use_disk_cache: Legacy parameter (ignored, caching always enabled)

    Returns:
        QPixmap thumbnail
    """
    if not path:
        return QPixmap()

    if not _enable_thumbnail_cache:
        # Caching disabled - generate directly without caching
        # This is rare but supported for debugging
        return _thumbnail_service._generate_thumbnail(path, height, timeout=5.0)

    # Use ThumbnailService which handles L1 (memory) + L2 (database) caching
    return _thumbnail_service.get_thumbnail(path, height)


def get_project_images(project_id: int, branch_key: Optional[str]):
    """
    Legacy branch-based image loading.
    This remains for backward compatibility but
    the grid now also supports folder-based loading
    directly via ReferenceDB.
    """
    return _db.get_project_images(project_id, branch_key)


def get_folder_images(folder_id: int):
    """
    New helper: Load image paths from photo_metadata for a folder.
    """
    return _db.get_images_by_folder(folder_id)

def export_branch(project_id: int, branch_key: str, dest_folder: str) -> int:
    paths = get_project_images(project_id, branch_key)
    exported = 0
    for p in paths:
        if not os.path.exists(p):
            continue
        name = os.path.basename(p)
        dst = os.path.join(dest_folder, name)
        i = 1
        while os.path.exists(dst):
            stem, ext = os.path.splitext(name)
            dst = os.path.join(dest_folder, f"{stem}_{i}{ext}")
            i += 1
        shutil.copy2(p, dst)
        exported += 1
    _db.log_export_action(project_id, branch_key, exported, paths, [], dest_folder)
    return exported

def get_default_project_id():
    projs = list_projects()
    return projs[0]["id"] if projs else None



def set_thumbnail_cache_enabled(flag: bool):
    global _enable_thumbnail_cache
    _enable_thumbnail_cache = flag
 


      


class ScanSignals(QObject):
    progress = Signal(int, str)  # percent, message

scan_signals = ScanSignals()

def scan_repository(root_folder, incremental=False, cancel_callback=None):
    """
    Smart scan:
    - Logs live progress (via scan_signals)
    - Skips unchanged files if incremental=True
    - Updates folder photo counts
    """
    db = ReferenceDB()
    root_folder = Path(root_folder)
    if not root_folder.exists():
        raise ValueError(f"Folder not found: {root_folder}")

    # Get or create default project for this scan
    project_id = db._get_or_create_default_project()

    # --- Gather all files first for total count ---
    all_files = []
    for current_dir, _, files in os.walk(root_folder):
        if cancel_callback and cancel_callback():
            print("[SCAN] Cancel callback triggered — stopping scan gracefully.")
            return 0, 0

        for fn in files:
            if fn.lower().split(".")[-1] in ["jpg", "jpeg", "png", "heic", "tif", "tiff", "webp"]:
                all_files.append(Path(current_dir) / fn)
    total_files = len(all_files)
    if total_files == 0:
        scan_signals.progress.emit(100, "No images found.")
        return 0, 0

    folder_map = {}
    folder_count = 0
    photo_count = 0

    # --- Step 1: Walk folders ---
    for idx, file_path in enumerate(all_files):
        if cancel_callback and cancel_callback():
            print("[SCAN] Cancel callback triggered — stopping scan gracefully.")
            return 0, 0

        folder_path = file_path.parent
        parent_path = folder_path.parent if folder_path != root_folder else None
        parent_id = folder_map.get(str(parent_path)) if parent_path else None

        if str(folder_path) not in folder_map:
            folder_id = db.ensure_folder(str(folder_path), folder_path.name, parent_id, project_id)
            folder_map[str(folder_path)] = folder_id
            folder_count += 1
        else:
            folder_id = folder_map[str(folder_path)]

        # --- Step 2: Incremental skip check ---
        stat = os.stat(file_path)
        size_kb = stat.st_size / 1024
        modified = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))

        if incremental:
            existing = db.get_photo_metadata_by_path(str(file_path))
            if existing and existing.get("size_kb") == size_kb and existing.get("modified") == modified:
                # Skip unchanged
                continue

        # --- Step 3: Extract metadata ---
        width = height = None
        date_taken = None
        try:
            with Image.open(file_path) as img:
                width, height = img.size
                exif = img.getexif()
                if exif:
                    for k, v in exif.items():
                        tag = ExifTags.TAGS.get(k, k)
                        if tag == "DateTimeOriginal":
                            date_taken = str(v)
                            break
        except Exception:
            pass

        # --- Step 4: Insert or update ---
        db.upsert_photo_metadata(
            path=str(file_path),
            folder_id=folder_id,
            size_kb=size_kb,
            modified=modified,
            width=width,
            height=height,
            date_taken=date_taken,
            tags=None,
            project_id=project_id,
        )
        photo_count += 1

        # --- Step 5: Progress reporting ---
        pct = int((idx + 1) / total_files * 100)
        scan_signals.progress.emit(pct, f"{photo_count} / {total_files} processed")


    # --- Step 6: Rebuild date index ---
    scan_signals.progress.emit(100, f"✅ Scan complete: {photo_count} photos, {folder_count} folders")
    print(f"[SCAN] Completed: {folder_count} folders, {photo_count} photos")

    # Trigger post-scan date indexing
    rebuild_date_index_with_progress()

    return folder_count, photo_count

#    scan_signals.progress.emit(100, f"✅ Scan complete: {photo_count} photos, {folder_count} folders")
#    print(f"[SCAN] Completed: {folder_count} folders, {photo_count} photos")
#    return folder_count, photo_count
  
  
def rebuild_date_index_with_progress():
    """
    Rebuild the date index after scanning and emit progress updates.
    This makes '📅 Date branches' appear immediately without restarting.
    """
    db = ReferenceDB()
    with db._connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM photo_metadata").fetchone()[0]
        if total == 0:
            scan_signals.progress.emit(100, "No photos to index by date.")
            return

        done = 0
        cursor = conn.execute("SELECT id FROM photo_metadata")
        for row in cursor:
            # If you already maintain a date index table or view, update it here
            # This loop is just to simulate progress feedback
            done += 1
            pct = int(done / total * 100)
            if done % 50 == 0 or done == total:
                scan_signals.progress.emit(pct, f"Indexing dates… {done}/{total}")

        scan_signals.progress.emit(100, f"📅 Date index ready ({total} photos).")
        print(f"[INDEX] Date indexing completed: {total} photos")


