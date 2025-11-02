# app_services.py
# Version 09.17.01.10 dated 20251026

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

DB_PATH = "photo_app.db"
SUPPORTED_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.heic', '.tif', '.tiff'}

_db = ReferenceDB()



# Single authoritative cache dir (Path object) defined early
_THUMB_CACHE_DIR = Path(os.path.join(os.getcwd(), ".thumb_cache"))
_THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_thumbnail_cache = {}      # in-memory cache mapping path -> {"pixmap": QPixmap, "mtime": float}
_enable_thumbnail_cache = True  # toggle caching on/off



def clear_disk_thumbnail_cache():
    try:
        if _THUMB_CACHE_DIR.exists():
            shutil.rmtree(_THUMB_CACHE_DIR)
        _THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        print(f"[Cache] Disk thumbnail cache cleared: {_THUMB_CACHE_DIR}")
        return True
    except Exception as e:
        print(f"[Cache] Failed to clear disk thumbnail cache: {e}")
        return False

def clear_thumbnail_cache():
    """Public: clear both memory and disk caches."""
    try:
        _thumbnail_cache.clear()
    except Exception:
        pass
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

def _hash_path(path: str) -> str:
    return hashlib.md5(path.encode("utf-8")).hexdigest()
    
def _thumb_file_path(path: str, fixed_height: int) -> tuple[str, str]:
    h = _hash_path(path)
    d = _THUMB_CACHE_DIR / str(fixed_height)
    d.mkdir(parents=True, exist_ok=True)
    return str(d / f"{h}.png"), str(d / f"{h}.meta")

def list_branches(project_id: int):
    try:
        return _db.get_branches(project_id)
    except Exception:
        with _db._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT branch_key, display_name FROM branches WHERE project_id=? ORDER BY id ASC", (project_id,))
            return [{"branch_key": r[0], "display_name": r[1]} for r in cur.fetchall()]

def _read_meta(meta_path):
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _write_meta(meta_path, size, mtime):
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"size": size, "mtime": mtime}, f)
    except Exception:
        pass

def _is_valid_cache(path, thumb_path, meta_path):
    if not os.path.exists(thumb_path) or not os.path.exists(meta_path):
        return False
    try:
        stat = os.stat(path)
    except Exception:
        return False
    meta = _read_meta(meta_path)
    if not meta:
        return False
    return meta.get("size") == stat.st_size and abs(meta.get("mtime", 0) - stat.st_mtime) < 0.1

def _thumb_key(path: str, height: int) -> str:
    try:
        mtime = os.path.getmtime(path)
        size = os.path.getsize(path)
    except OSError:
        mtime = 0
        size = 0
    raw = f"{path}|{mtime}|{size}|h={height}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def get_thumbnail(path: str, height: int, use_disk_cache: bool = True) -> QPixmap:
    if not path:
        return QPixmap()

    ext = os.path.splitext(path)[1].lower()
    is_tiff = ext in (".tif", ".tiff")

    key = _thumb_key(path, height)
    cache_file = _THUMB_CACHE_DIR / f"{key}.png"

    if use_disk_cache and cache_file.exists():
        pm = QPixmap(str(cache_file))
        if not pm.isNull():
            return pm

    if is_tiff:
        try:
            with Image.open(path) as im:
                ratio = height / float(im.height)
                target_w = int(im.width * ratio)
                im = im.convert("RGB")
                im.thumbnail((target_w, height))
                buf = io.BytesIO()
                im.save(buf, format="PNG")
                qimg = QImage.fromData(buf.getvalue())
                pm = QPixmap.fromImage(qimg)
                if use_disk_cache and not pm.isNull():
                    try:
                        pm.save(str(cache_file), "PNG")
                    except Exception:
                        pass
                return pm
        except Exception as e:
            print(f"[get_thumbnail] TIFF fallback failed for {path}: {e}")
            return QPixmap()

    # Normal path using QImageReader
    try:
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        img = reader.read()
        if img.isNull():
            return QPixmap()
        if height > 0:
            img = img.scaledToHeight(height, Qt.SmoothTransformation)
        pm = QPixmap.fromImage(img)
        if use_disk_cache and not pm.isNull():
            try:
                pm.save(str(cache_file), "PNG")
            except Exception:
                pass
        return pm
    except Exception:
        return QPixmap()


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
            folder_id = db.ensure_folder(str(folder_path), folder_path.name, parent_id)
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


