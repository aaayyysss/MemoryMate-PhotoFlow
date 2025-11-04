# main_window_qt.py
# Version 09.19.00.00 dated 20251102
# Removed old embedded ScanWorker class (316 LOC) - using services/photo_scan_service.py
# Current LOC: ~2,540 (down from original 2,855)

# [ Tree View  ]
#     │
#     ▼
#  photo_folders  ⇨  loads folder → retrieves photos
#     │
#     ▼
#  photo_metadata → thumbnail grid, details panel
#
#
# 🧭 3. Suggested Database Schema
#  🗃️ photo_folders
#  id	parent_id	path	name
#  1	NULL	/repo/2022	2022
#  2	1	/repo/2022/family	family
#  3	1	/repo/2022/vacation	vacation
#  🗃️ photo_metadata
#  path	folder_id	size_kb	modified	width	height	embedding	date_taken	tags
#  /repo/2022/family/img1.jpg	2	3200	2024-12-05 12:34	4000	3000	… blob …	2024-05-04	family,baby
#
#  👉 folder_id gives fast tree navigation
#  👉 tags, embedding, and date_taken allow smart sorting later.
#
#  🧭 2. Recommended Directory Scanning Strategy
#  👉 We don't want to re-scan the entire repository every time.
#  👉 We should scan once and index the structure in the database.
#
#  🧰 Step-by-step:
#  Recursive scan using os.walk() or pathlib.Path.rglob('*')
#
#  For each file:
#  Check file type (image formats: .jpg, .png, .heic, .webp, .tif, etc.)
#  Get basic metadata (size, modified date, dimensions if needed)
#  Save to photo_metadata table
#  For each folder:
#  Save a reference to photo_folders table (for tree view)
#  Mark parent–child relationship for UI navigation
#  Store a hash or last_modified so we can incrementally update later.

from splash_qt import SplashScreen, StartupWorker
import os, traceback, time as _time, logging
from thumb_cache_db import get_cache

from db_writer import DBWriter
from typing import Iterable, Optional, Dict, Tuple

# ✅ NEW: Import service-based ScanWorker
from services.scan_worker_adapter import ScanWorkerAdapter as ScanWorker

# Add imports near top if not present:

from PySide6.QtCore import Qt, QThread, QSize, QThreadPool, Signal, QObject, QRunnable, QEvent, QTimer

from PySide6.QtGui import QPixmap, QImage, QImageReader, QAction, QIcon, QTransform, QPalette, QColor, QGuiApplication

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, 
    QHBoxLayout, QVBoxLayout, QLabel, 
    QComboBox, QSizePolicy, QToolBar, QMessageBox,
    QDialog, QPushButton, QFileDialog, QScrollArea,
    QCheckBox, QComboBox as QSortComboBox,
    QProgressDialog, QApplication, QStyle,
    QDialogButtonBox, QMenu, QGroupBox, QFrame,
    QSlider, QFormLayout, QTextEdit
)


from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from collections import deque


from PIL import Image, ImageEnhance, ImageQt, ImageOps, ExifTags
# Optional HEIF/HEIC support (non-fatal)
try:
    import pillow_heif  # if installed, Pillow can open HEIC/HEIF
    print("[Startup] pillow_heif available — HEIC/HEIF support enabled.")
except Exception:
    # fine if missing; HEIC files will be skipped unless plugin installed
    pass

from sidebar_qt import SidebarQt

from thumbnail_grid_qt import ThumbnailGridQt

from app_services import (
    list_projects, get_default_project_id, 
    scan_signals, scan_repository, 
    clear_thumbnail_cache
)

from reference_db import ReferenceDB
from reference_db import (
    # NOTE: ensure_created_date_fields no longer needed - handled by migration system
    count_missing_created_fields,
    single_pass_backfill_created_fields,
)

from settings_manager_qt import SettingsManager
# --- Apply decoder warning policy early ---
from settings_manager_qt import apply_decoder_warning_policy
apply_decoder_warning_policy()

from preview_panel_qt import LightboxDialog

# --- Backfill / process management imports ---
import subprocess, shlex, sys
from pathlib import Path
from threading import Thread, Event

# When double-clicking a thumbnail:
def _on_thumb_double_click(self, path):
    dlg = LightboxDialog(path, self)
    dlg.exec()


# --- Simple file logger for debugging frozen builds ---
def safe_log(msg: str):
    """Append a message to app_log.txt (UTF-8)."""
    try:
        log_path = os.path.join(os.getcwd(), "app_log.txt")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception as e:
        # In worst case, just print if file writing fails
        print(f"[LOGGING ERROR] {e}: {msg}")

# Small helper: clamp integer percent
def _clamp_pct(v):
    try:
        return max(0, min(100, int(v or 0)))
    except Exception:
        return 0



# ---------------------------
# Note: Old embedded ScanWorker class removed - now using services/photo_scan_service.py


# === Phase 2 Controllers (in-file, no new modules) ===========================

class ScanController:
    """
    Wraps scan orchestration: start, cancel, cleanup, progress wiring.
    Keeps MainWindow slimmer.
    """
    def __init__(self, main):
        self.main = main
        self.thread = None
        self.worker = None
        self.db_writer = None
        self.cancel_requested = False
        self.logger = logging.getLogger(__name__)

    def start_scan(self, folder, incremental: bool):
        """Entry point called from MainWindow toolbar action."""
        self.cancel_requested = False
        self.main.statusBar().showMessage(f"📸 Scanning repository: {folder} (incremental={incremental})")
        self.main._committed_total = 0

        # Progress dialog
        self.main._scan_progress = QProgressDialog("Preparing scan...", "Cancel", 0, 100, self.main)
        self.main._scan_progress.setWindowTitle("Scanning Photos")
        self.main._scan_progress.setWindowModality(Qt.WindowModal)
        self.main._scan_progress.setAutoClose(False)
        self.main._scan_progress.setAutoReset(False)
        self.main._scan_progress.show()

        # DB writer
        # NOTE: Schema creation handled automatically by repository layer
        from db_writer import DBWriter
        self.db_writer = DBWriter(batch_size=200, poll_interval_ms=150)
        self.db_writer.error.connect(lambda msg: print(f"[DBWriter] {msg}"))
        self.db_writer.committed.connect(self._on_committed)
        self.db_writer.start()

        # CRITICAL: Initialize database schema before starting scan
        # This ensures the repository layer has created all necessary tables
        try:
            from repository.base_repository import DatabaseConnection
            db_conn = DatabaseConnection("reference_data.db", auto_init=True)
            print("[Schema] Database schema initialized successfully")
        except Exception as e:
            print(f"[Schema] ERROR: Failed to initialize database schema: {e}")
            import traceback
            traceback.print_exc()
            self.main.statusBar().showMessage(f"❌ Database initialization failed: {e}")
            return

        # Scan worker
        try:
            print(f"[ScanController] Creating ScanWorker for folder: {folder}")
            from services.scan_worker_adapter import ScanWorkerAdapter as ScanWorker
            print(f"[ScanController] ScanWorker imported successfully")

            self.thread = QThread(self.main)
            print(f"[ScanController] QThread created")

            self.worker = ScanWorker(folder, incremental, self.main.settings, db_writer=self.db_writer)
            print(f"[ScanController] ScanWorker instance created")

            self.worker.moveToThread(self.thread)
            print(f"[ScanController] Worker moved to thread")

            self.worker.progress.connect(self._on_progress)
            self.worker.finished.connect(self._on_finished)
            self.worker.error.connect(self._on_error)
            self.thread.started.connect(lambda: print("[ScanController] QThread STARTED!"))
            self.thread.started.connect(self.worker.run)
            self.worker.finished.connect(lambda f, p: self.thread.quit())
            self.thread.finished.connect(self._cleanup)
            print(f"[ScanController] Signals connected")

            # Start scan thread immediately - DBWriter is already running from line 178
            print("[ScanController] Starting scan thread...")
            self.thread.start()
            print("[ScanController] thread.start() called")

            self.main.act_cancel_scan.setEnabled(True)
        except Exception as e:
            print(f"[ScanController] CRITICAL ERROR creating scan worker: {e}")
            import traceback
            traceback.print_exc()
            self.main.statusBar().showMessage(f"❌ Failed to create scan worker: {e}")
            QMessageBox.critical(self.main, "Scan Error", f"Failed to create scan worker:\n\n{e}")
            return

    def cancel(self):
        """Cancel triggered from toolbar."""
        self.cancel_requested = True
        if self.worker:
            try:
                self.worker.stop()
            except Exception:
                pass
        self.main.statusBar().showMessage("🛑 Scan cancellation requested…")
        self.main.act_cancel_scan.setEnabled(False)

    def _on_committed(self, n: int):
        self.main._committed_total += n
        try:
            if self.main._scan_progress:
                cur = self.main._scan_progress.labelText() or ""
                self.main._scan_progress.setLabelText(f"{cur}\nCommitted: {self.main._committed_total} rows")
        except Exception:
            pass

    def _on_progress(self, pct: int, msg: str):
        if not self.main._scan_progress:
            return
        pct_i = max(0, min(100, int(pct or 0)))
        self.main._scan_progress.setValue(pct_i)
        if msg:
            label = f"{msg}\nCommitted: {self.main._committed_total}"
            self.main._scan_progress.setLabelText(label)
        QApplication.processEvents()
        if self.main._scan_progress.wasCanceled():
            self.cancel()

    def _on_finished(self, folders, photos):
        print(f"[ScanController] scan finished: {folders} folders, {photos} photos")
        self.main._scan_result = (folders, photos)

    def _on_error(self, err_text: str):
        try:
            QMessageBox.critical(self.main, "Scan Error", err_text)
        except Exception:
            print(f"[ScanController] {err_text}")
        if self.thread and self.thread.isRunning():
            self.thread.quit()

    def _cleanup(self):
        print("[ScanController] cleanup after scan")
        try:
            self.main.act_cancel_scan.setEnabled(False)
            if self.main._scan_progress:
                self.main._scan_progress.setValue(100)
                self.main._scan_progress.close()
            if self.db_writer:
                self.db_writer.shutdown(wait=True)
        except Exception as e:
            self.logger.error(f"Cleanup error: {e}", exc_info=True)

        # Build date branches after scan completes
        try:
            self.logger.info("Building date branches...")
            from reference_db import ReferenceDB
            db = ReferenceDB()
            branch_count = db.build_date_branches()
            self.logger.info(f"Created {branch_count} date branch entries")

            # CRITICAL: Backfill created_date field immediately after scan
            # This populates created_date from date_taken so get_date_hierarchy() works
            # Without this, the "By Date" section won't appear until app restart
            self.logger.info("Backfilling created_date fields...")
            backfilled = db.single_pass_backfill_created_fields()
            if backfilled:
                self.logger.info(f"Backfilled {backfilled} rows with created_date")

            # CRITICAL: Update sidebar project_id if it was None (fresh database)
            # The scan creates the first project, so we need to tell the sidebar about it
            sidebar_was_updated = False
            if self.main.sidebar.project_id is None:
                self.logger.info("Sidebar project_id was None, updating to default project")
                from app_services import get_default_project_id
                default_pid = get_default_project_id()
                self.logger.debug(f"Setting sidebar project_id to {default_pid}")
                self.main.sidebar.set_project(default_pid)
                if hasattr(self.main.sidebar, "tabs_controller"):
                    self.main.sidebar.tabs_controller.project_id = default_pid
                sidebar_was_updated = True

            # CRITICAL: Also update grid's project_id if it was None
            if self.main.grid.project_id is None:
                self.logger.info("Grid project_id was None, updating to default project")
                from app_services import get_default_project_id
                default_pid = get_default_project_id()
                self.logger.debug(f"Setting grid project_id to {default_pid}")
                self.main.grid.project_id = default_pid
        except Exception as e:
            self.logger.error(f"Error building date branches: {e}", exc_info=True)

        # Sidebar & grid refresh
        # Note: If we just called set_project(), it already triggered a refresh
        # so we need to wait a moment for it to complete before forcing another refresh
        try:
            if sidebar_was_updated:
                # Give the async refresh from set_project() time to complete
                import time
                time.sleep(0.5)

            self.logger.info("Reloading sidebar after date branches built...")
            # Always refresh tabs, regardless of current display mode
            if hasattr(self.main.sidebar, "tabs_controller"):
                self.main.sidebar.tabs_controller.refresh_all(force=True)
                self.logger.debug("Tabs refresh completed")
            # Also reload the sidebar (tree view if in list mode)
            if hasattr(self.main.sidebar, "reload"):
                self.main.sidebar.reload()
                self.logger.debug("Sidebar reload completed")
        except Exception as e:
            self.logger.error(f"Error reloading sidebar: {e}", exc_info=True)
        try:
            if hasattr(self.main.grid, "reload"):
                self.main.grid.reload()
        except Exception:
            pass
        # reload thumbnails after scan
        if self.main.thumbnails and hasattr(self.main.grid, "get_visible_paths"):
            self.main.thumbnails.load_thumbnails(self.main.grid.get_visible_paths())

        # summary
        f, p = self.main._scan_result
        QMessageBox.information(self.main, "Scan Complete", f"Indexed {p} photos in {f} folders.\nCommitted: {self.main._committed_total} rows.")


class SidebarController:
    """Encapsulates folder/branch event handling & thumbnail refresh."""
    def __init__(self, main):
        self.main = main

    def on_folder_selected(self, folder_id: int):
        self.main.grid.set_folder(folder_id)
        
        if getattr(self.main, "active_tag_filter", "all") != "all":
            self.main._apply_tag_filter(self.main.active_tag_filter)
            
        if self.main.thumbnails and hasattr(self.main.grid, "get_visible_paths"):
            self.main.thumbnails.clear()
            self.main.thumbnails.load_thumbnails(self.main.grid.get_visible_paths())

    def on_branch_selected(self, branch_key: str):
        self.main.grid.set_branch(branch_key)
        
        if getattr(self.main, "active_tag_filter", "all") != "all":
            self.main._apply_tag_filter(self.main.active_tag_filter)
            
        if hasattr(self.main.grid, "_on_slider_changed"):
            self.main.grid._on_slider_changed(self.main.grid.zoom_slider.value())
            
        if hasattr(self.main.grid, "list_view"):
            self.main.grid.list_view.scrollToTop()
            
        if self.main.thumbnails and hasattr(self.main.grid, "get_visible_paths"):
            self.main.thumbnails.clear()
            self.main.thumbnails.load_thumbnails(self.main.grid.get_visible_paths())


class ProjectController:
    """Owns project switching & persistence logic."""
    def __init__(self, main):
        self.main = main

    def on_project_changed(self, idx: int):
        pid = self.main.project_combo.itemData(idx)
        if pid is None:
            return
        if self.main.thumbnails:
            self.main.thumbnails.clear()
        self.main.sidebar.set_project(pid)
        self.main.grid.set_project(pid)


#======================================

# --- Thumbnail management (self-contained, in-file) --------------------------


class _ThumbLoaded(QObject):
    loaded = Signal(str, int, QPixmap)  # path, size, pixmap

class _ThumbTask(QRunnable):
    """Worker: decode + scale a single image to 'size' and emit a pixmap."""
    def __init__(self, path: str, size: int, emitter: _ThumbLoaded):
        super().__init__()
        self.path = str(path)
        self.size = int(size)
        self.emitter = emitter
        self.setAutoDelete(True)

    def run(self):
        pm = None
        # Fast path: Qt decoder with scaled decode and EXIF auto-transform.
        try:
            reader = QImageReader(self.path)
            reader.setAutoTransform(True)
            reader.setScaledSize(QSize(self.size, self.size))
            img = reader.read()
            if img and not img.isNull():
                pm = QPixmap.fromImage(img)
        except Exception:
            pm = None

        # Fallback: Pillow if available.
        if pm is None:
            try:
                from PIL import Image, ImageQt
                im = Image.open(self.path)
                im.thumbnail((self.size, self.size), Image.LANCZOS)
                if im.mode not in ("RGBA", "LA"):
                    im = im.convert("RGBA")
                pm = QPixmap.fromImage(ImageQt.ImageQt(im))
            except Exception:
                pm = None

        # Last resort: gray placeholder.
        if pm is None:
            pm = QPixmap(self.size, self.size)
            pm.fill(Qt.lightGray)

        self.emitter.loaded.emit(self.path, self.size, pm)


class ThumbnailManager(QObject):
    """
    Orchestrates thumbnail loading, caching, zoom, and delivery to the grid.
    - grid: an object that can accept thumbnails via set_thumbnail(path, pixmap)
            or update_item_thumbnail(path, pixmap).
    - cache: optional dict-like; if None, an internal cache is used.
    - log: callable like gui_log.debug/info/warn (optional).
    """
    def __init__(self, grid, cache: Optional[Dict[Tuple[str, int], QPixmap]], log=None, initial_size: int = 160):
        super().__init__()
        self._grid = grid
        self._log = log
        self._size = max(24, int(initial_size))
        self._cache: Dict[Tuple[str, int], QPixmap] = cache if cache is not None else {}
        self._emitter = _ThumbLoaded()
        self._emitter.loaded.connect(self._on_loaded)
        self._pool = QThreadPool.globalInstance()

        # detect grid API once (no hard dependency on exact class)
        self._apply_thumb = None
        if hasattr(self._grid, "set_thumbnail"):
            self._apply_thumb = self._grid.set_thumbnail
        elif hasattr(self._grid, "update_item_thumbnail"):
            self._apply_thumb = self._grid.update_item_thumbnail

    # ---------- public API ----------
    def load_thumbnails(self, image_paths: Iterable[str]) -> None:
        size = self._size
        for p in image_paths:
            key = (p, size)
            if key in self._cache:
                # Cache hit → push to grid immediately.
                self._deliver_to_grid(p, self._cache[key])
                continue
            # Submit async decode.
            self._pool.start(_ThumbTask(p, size, self._emitter))

    def update_zoom(self, factor: float) -> None:
        """
        'factor' can be a multiplier (e.g., 1.25) or an absolute int if >= 24.
        Re-renders visible thumbs at the new size (cache is keyed by size).
        """
        if isinstance(factor, (int, float)) and factor >= 24:
            new_size = int(factor)
        else:
            new_size = int(self._size * float(factor))
        new_size = max(24, min(1024, new_size))
        if new_size == self._size:
            return
        self._size = new_size
        if self._log:
            try:
                self._log.debug(f"[Thumbs] Zoom set → {self._size}px")
            except Exception:
                pass
        # Ask grid for visible paths if it can provide them, else do nothing.
        paths = None
        if hasattr(self._grid, "visible_paths"):
            try:
                paths = list(self._grid.visible_paths())
            except Exception:
                paths = None
        if paths:
            self.load_thumbnails(paths)

    def clear(self) -> None:
        """Optional: clear only current-size entries to free memory."""
        to_del = [k for k in self._cache.keys() if isinstance(k, tuple) and len(k) == 2 and k[1] == self._size]
        for k in to_del:
            self._cache.pop(k, None)

    # ---------- internal ----------
    def _on_loaded(self, path: str, size: int, pm: QPixmap) -> None:
        # store in cache and deliver
        self._cache[(path, size)] = pm
        if size == self._size:
            self._deliver_to_grid(path, pm)

    def _deliver_to_grid(self, path: str, pm: QPixmap) -> None:
        if self._apply_thumb:
            try:
                self._apply_thumb(path, pm)
            except Exception:
                pass

    def shutdown_threads(self):
        """Gracefully shutdown thumbnail pool (for app close)."""
        try:
            if hasattr(self, "_pool") and self._pool:
                self._pool.waitForDone(1000)
                print("[ThumbnailManager] Thread pool shut down.")
        except Exception as e:
            print(f"[ThumbnailManager] shutdown error: {e}")



# === Phase 3 UIBuilder =======================================================

class UIBuilder:
    """
    Helper for building toolbars, menus, and controls with less boilerplate.
    Used by MainWindow during __init__ to reduce clutter.
    """
    def __init__(self, main):
        self.main = main
        self.tb = None

    def make_toolbar(self, name="Tools"):
        tb = QToolBar(name, self.main)
        self.main.addToolBar(tb)
        self.tb = tb
        return tb

    def action(self, text, icon=None, shortcut=None, tooltip=None, checkable=False, handler=None):
        act = QAction(text, self.main)
        if icon:
            act.setIcon(QIcon.fromTheme(icon))
        if shortcut:
            try:
                act.setShortcut(shortcut)
            except Exception:
                pass
        if tooltip:
            act.setToolTip(tooltip)
        act.setCheckable(checkable)
        if handler:
            act.triggered.connect(handler)
        if self.tb:
            self.tb.addAction(act)
        return act

    def separator(self):
        if self.tb:
            self.tb.addSeparator()

    def menu(self, title, icon=None):
        m = self.main.menuBar().addMenu(title)
        return m

    def menu_action(self, menu, text, shortcut=None, tooltip=None, checkable=False, handler=None):
        act = QAction(text, self.main)
        if shortcut:
            try:
                act.setShortcut(shortcut)
            except Exception:
                pass
        if tooltip:
            act.setToolTip(tooltip)
        act.setCheckable(checkable)
        if handler:
            act.triggered.connect(handler)
        menu.addAction(act)
        return act

    def combo_sort(self, label_text, options, on_change):
        self.tb.addWidget(QLabel(label_text))
        combo = QSortComboBox()
        combo.addItems(options)
        combo.currentIndexChanged.connect(lambda *_: on_change())
        self.tb.addWidget(combo)
        return combo

    def checkbox(self, text, checked=True):
        chk = QCheckBox(text)
        chk.setChecked(checked)
        if self.tb:
            self.tb.addWidget(chk)
        return chk

# ======================================================

class PreferencesDialog(QDialog):
    def __init__(self, settings: SettingsManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Application Preferences")
        self.setMinimumWidth(380)
        self.settings = settings

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title = QLabel("⚙️ General Settings")
        title.setStyleSheet("font-weight: bold; font-size: 11pt; color: #004A7F;")
        layout.addWidget(title)

        # ✅ Preferences toggles
        self.chk_skip = QCheckBox("Skip reindexing unchanged photos")
        self.chk_skip.setChecked(settings.get("skip_unchanged_photos", True))
        layout.addWidget(self.chk_skip)

        self.chk_exif = QCheckBox("Use EXIF date for 'Date Taken'")
        self.chk_exif.setChecked(settings.get("use_exif_for_date", True))
        layout.addWidget(self.chk_exif)

        self.chk_dark = QCheckBox("Enable Dark Mode (experimental)")
        self.chk_dark.setChecked(settings.get("dark_mode", False))
        layout.addWidget(self.chk_dark)

        self.chk_cache = QCheckBox("Enable Thumbnail Cache")
        self.chk_cache.setChecked(settings.get("thumbnail_cache_enabled", True))
        layout.addWidget(self.chk_cache)
        
        self.chk_cache_cleanup = QCheckBox("Auto cleanup old thumbnail cache on startup")
        self.chk_cache_cleanup.setChecked(settings.get("cache_auto_cleanup", True))
        layout.addWidget(self.chk_cache_cleanup)

        # === 📂 Scanning Ignore Folders ===
        ignore_group = QGroupBox("Scanning — Ignored Folders")
        ignore_layout = QVBoxLayout(ignore_group)
        self.txt_ignore_folders = QTextEdit()
        self.txt_ignore_folders.setPlaceholderText("One folder name per line (case-sensitive)...")
        current_ignore = settings.get("ignore_folders", [
            "AppData", "Program Files", "Program Files (x86)", "Windows",
            "$Recycle.Bin", "System Volume Information", "__pycache__",
            "node_modules", "Temp", "Cache", "Microsoft", "Installer",
            "Recovery", "Logs", "ThumbCache", "ActionCenterCache"
        ])
        self.txt_ignore_folders.setText("\n".join(current_ignore))
        ignore_layout.addWidget(self.txt_ignore_folders)
        layout.addWidget(ignore_group)


        # 🧠 Diagnostics & Logging
        diag_group = QWidget()
        diag_layout = QVBoxLayout(diag_group)
        diag_layout.setContentsMargins(0, 0, 0, 0)
        diag_layout.setSpacing(4)

        self.chk_decoder_warnings = QCheckBox("Show decoder/image warnings (PNG, TIFF, ICC, etc.)")
        self.chk_decoder_warnings.setToolTip(
            "If enabled, Qt and Pillow will show decoding warnings.\n"
            "Useful for developers or debugging broken images.\n"
            "Requires app restart to take effect."
        )
        self.chk_decoder_warnings.setChecked(settings.get("show_decoder_warnings", False))
        diag_layout.addWidget(self.chk_decoder_warnings)
        layout.addWidget(diag_group)

        # === 🧑‍💻 Developer Info (collapsible advanced diagnostics – adaptive style) ===
        from PySide6.QtGui import QPalette, QColor

        # Detect current theme
        is_dark = bool(self.settings.get("dark_mode", False))

        # Define color palette dynamically
        if is_dark:
            bg_main = "#2b2b2b"
            bg_highlight = "#383838"
            border_color = "#555"
            text_color = "#dcdcdc"
            accent_color = "#4aa3ff"
        else:
            bg_main = "#f7f7f7"
            bg_highlight = "#e7f2ff"
            border_color = "#ccc"
            text_color = "#004A7F"
            accent_color = "#0078d4"

        # --- Header toggle button ---
        dev_header = QPushButton("Developer Info ▼")
        dev_header.setCheckable(True)
        dev_header.setChecked(False)
        dev_header.setCursor(Qt.PointingHandCursor)
        dev_header.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg_main};
                color: {text_color};
                font-weight: 600;
                border: 1px solid {border_color};
                border-radius: 5px;
                padding: 6px 10px;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: {bg_highlight};
                border-color: {accent_color};
            }}
            QPushButton:checked {{
                background-color: {accent_color};
                color: white;
            }}
        """)

        # --- Developer panel (collapsible area) ---
        self.dev_panel = QFrame()
        self.dev_panel.setFrameShape(QFrame.StyledPanel)
        self.dev_panel.setStyleSheet(f"""
            QFrame {{
                background-color: {bg_main};
                border: 1px solid {border_color};
                border-radius: 5px;
            }}
            QCheckBox {{
                color: {text_color};
                spacing: 6px;
            }}
            QCheckBox::indicator:checked {{
                background-color: {accent_color};
                border: 1px solid {accent_color};
            }}
            QCheckBox::indicator:unchecked {{
                background-color: transparent;
                border: 1px solid {border_color};
            }}
        """)

        dev_layout = QVBoxLayout(self.dev_panel)
        dev_layout.setContentsMargins(12, 8, 12, 8)
        dev_layout.setSpacing(6)

        # --- Developer toggles ---
        self.chk_db_debug = QCheckBox("Enable DB debug logging (verbose SQL output)")
        self.chk_db_debug.setToolTip("If enabled, shows detailed SQL queries and DB operations in console/logs.")
        self.chk_sql_echo = QCheckBox("Show SQL queries in real time")
        self.chk_sql_echo.setToolTip("Displays executed SQL statements for debugging.")

        self.chk_db_debug.setChecked(settings.get("db_debug_logging", False))
        self.chk_sql_echo.setChecked(settings.get("show_sql_queries", False))

        dev_layout.addWidget(self.chk_db_debug)
        dev_layout.addWidget(self.chk_sql_echo)

        # Hide by default
        self.dev_panel.setVisible(False)

        # --- Expand / collapse behavior ---
        def _toggle_dev_panel():
            visible = dev_header.isChecked()
            self.dev_panel.setVisible(visible)
            dev_header.setText("Developer Info ▲" if visible else "Developer Info ▼")

        dev_header.toggled.connect(_toggle_dev_panel)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet(f"color: {border_color}; margin: 6px 0;")
        layout.addWidget(divider)

        # Add to layout
        layout.addWidget(dev_header)
        layout.addWidget(self.dev_panel)


        # === 🧠 Developer Diagnostics: Cache Stats + Footer Indicator ===

        # ---------------------------------------------------------
        # Collapsible diagnostics group
        # ---------------------------------------------------------
        self.dev_cache_group = QGroupBox("🧠 Diagnostics – Cache Statistics")
        self.dev_cache_group.setCheckable(True)
        self.dev_cache_group.setChecked(False)
        self.dev_cache_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                margin-top: 8px;
                border: 1px solid palette(mid);
                border-radius: 6px;
                padding: 6px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 2px 4px;
                color: palette(text);
            }
        """)

        cache_layout = QVBoxLayout(self.dev_cache_group)
        cache_layout.setContentsMargins(10, 4, 10, 4)

        info_lbl = QLabel("View persistent thumbnail-cache statistics and purge if necessary.")
        info_lbl.setWordWrap(True)
        cache_layout.addWidget(info_lbl)

        # --- Buttons row ---
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        btn_stats = QPushButton("📊  Show Cache Stats")
        btn_purge = QPushButton("🗑️  Purge Old Entries")
        btn_row.addWidget(btn_stats)
        btn_row.addWidget(btn_purge)
        cache_layout.addLayout(btn_row)

        def on_show_stats():
            stats = get_cache().get_stats()
            if "error" in stats:
                QMessageBox.warning(self, "Thumbnail Cache Stats", f"Error: {stats['error']}")
                return
            msg = (f"Entries: {stats['entries']}\n"
                   f"Size: {stats['size_mb']} MB\n"
                   f"Last Updated: {stats['last_updated']}\n"
                   f"Path: {stats['path']}")
            QMessageBox.information(self, "Thumbnail Cache Stats", msg)

        def on_purge():
            cache = get_cache()
            cache.purge_stale(max_age_days=7)
            QMessageBox.information(self, "Purge Complete", "Old thumbnails (older than 7 days) have been purged.")
            update_cache_footer()  # refresh footer indicator

        btn_stats.clicked.connect(on_show_stats)
        btn_purge.clicked.connect(on_purge)

        # ✅ FIX: use main layout instead of non-existent self.dev_layout
        layout.addWidget(self.dev_cache_group)

        # ---------------------------------------------------------
        # Footer: tiny live size indicator
        # ---------------------------------------------------------
        footer_frame = QFrame()
        footer_layout = QHBoxLayout(footer_frame)
        footer_layout.setContentsMargins(4, 4, 4, 4)

        footer_label = QLabel()
        footer_label.setStyleSheet("""
            QLabel {
                font-size: 11px;
                color: palette(mid);
            }
        """)
        footer_layout.addWidget(footer_label)
        footer_layout.addStretch(1)

        # ✅ FIX: also add to main layout
        layout.addWidget(footer_frame)

        def update_cache_footer():
            """Update the small live size indicator."""
            try:
                stats = get_cache().get_stats()
                if "error" in stats:
                    footer_label.setText("Cache: unavailable")
                else:
                    footer_label.setText(f"Cache: {stats['size_mb']} MB • {stats['entries']} items")
            except Exception as e:
                footer_label.setText(f"Cache: error ({e})")

        update_cache_footer()
        self.dev_cache_group.toggled.connect(lambda _: update_cache_footer())


        # ✅ Cache size
        row_cache = QWidget()
        row_layout = QHBoxLayout(row_cache)
        row_layout.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel("Max Cache Size (MB):")
        self.txt_cache_size = QComboBox()
        self.txt_cache_size.setEditable(True)
        for val in [100, 250, 500, 1000, 2000]:
            self.txt_cache_size.addItem(str(val))
        current_cache = str(settings.get("cache_size_mb", 500))
        if current_cache not in [str(x) for x in [100, 250, 500, 1000, 2000]]:
            self.txt_cache_size.insertItem(0, current_cache)
        self.txt_cache_size.setCurrentText(current_cache)
        row_layout.addWidget(lbl)
        row_layout.addWidget(self.txt_cache_size)
        layout.addWidget(row_cache)

        # --- Metadata Backfill preferences (persistent via SettingsManager) ---
        meta_group = QGroupBox("Metadata Backfill (worker pool)")
        meta_layout = QFormLayout(meta_group)
        self.spin_workers = QComboBox()
        for v in [1, 2, 4, 6, 8, 12]:
            self.spin_workers.addItem(str(v))
        self.spin_workers.setCurrentText(str(self.settings.get("meta_workers", 4)))
        meta_layout.addRow("Workers:", self.spin_workers)

        self.txt_meta_timeout = QComboBox()
        self.txt_meta_timeout.setEditable(True)
        for v in ["4.0", "6.0", "8.0", "12.0"]:
            self.txt_meta_timeout.addItem(v)
        self.txt_meta_timeout.setCurrentText(str(self.settings.get("meta_timeout_secs", 8.0)))
        meta_layout.addRow("Per-file timeout (s):", self.txt_meta_timeout)

        self.txt_meta_batch = QComboBox()
        self.txt_meta_batch.setEditable(True)
        for v in ["50", "100", "200", "500"]:
            self.txt_meta_batch.addItem(v)
        self.txt_meta_batch.setCurrentText(str(self.settings.get("meta_batch", 200)))
        meta_layout.addRow("DB update batch:", self.txt_meta_batch)

        self.chk_meta_auto = QCheckBox("Auto-run after scan")
        self.chk_meta_auto.setChecked(self.settings.get("auto_run_backfill_after_scan", False))
        meta_layout.addRow(self.chk_meta_auto)

        layout.addWidget(meta_group)

        # --- Buttons ---
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(buttons)
        buttons.accepted.connect(self._apply_and_close)
        buttons.rejected.connect(self.reject)

    def _apply_and_close(self):
        """Save settings persistently."""
        self.settings.set("skip_unchanged_photos", self.chk_skip.isChecked())
        self.settings.set("use_exif_for_date", self.chk_exif.isChecked())
        self.settings.set("dark_mode", self.chk_dark.isChecked())
        self.settings.set("thumbnail_cache_enabled", self.chk_cache.isChecked())
        try:
            cache_size = int(self.txt_cache_size.currentText())
        except ValueError:
            cache_size = 500
        self.settings.set("cache_size_mb", cache_size)
        self.settings.set("show_decoder_warnings", self.chk_decoder_warnings.isChecked())
        if self.settings.get("show_decoder_warnings", False):
            QMessageBox.information(
                self,
                "Restart Required",
                "Decoder warnings have been enabled.\nPlease restart the app to apply changes."
            )
        else:
            QMessageBox.information(
                self,
                "Restart Recommended",
                "Decoder warning silencing will apply fully after restarting the app."
            )

        self.settings.set("db_debug_logging", self.chk_db_debug.isChecked())
        self.settings.set("show_sql_queries", self.chk_sql_echo.isChecked())
        if self.chk_db_debug.isChecked():
            print("🧩 Developer mode: DB debug logging enabled.")
        self.settings.set("cache_auto_cleanup", self.chk_cache_cleanup.isChecked())

        # Save metadata backfill prefs
        self.settings.set("meta_workers", int(self.spin_workers.currentText()))
        self.settings.set("meta_timeout_secs", float(self.txt_meta_timeout.currentText()))
        self.settings.set("meta_batch", int(self.txt_meta_batch.currentText()))
        self.settings.set("auto_run_backfill_after_scan", bool(self.chk_meta_auto.isChecked()))

        # Save ignore folders
        ignore_list = [x.strip() for x in self.txt_ignore_folders.toPlainText().splitlines() if x.strip()]
        self.settings.set("ignore_folders", ignore_list)


        self.accept()


# Thumbnail task/emitter used by ThumbnailGridQt
class ThumbnailResult(QObject):
    # emitted on GUI thread: (path, QPixmap)
    ready = Signal(str, QPixmap)

class ThumbnailTask(QRunnable):
    def __init__(self, path: str, size: int, emitter: ThumbnailResult):
        super().__init__()
        self.path = str(path)
        self.size = int(size)
        self.emitter = emitter
        self.setAutoDelete(True)

    def run(self):
        # Try using QImageReader for efficient scaled decode (works on GUI-thread types but we run in worker thread)
        try:
            reader = QImageReader(self.path)
            reader.setAutoTransform(True)
            # Ask the reader to give a scaled image matching thumbnail size
            reader.setScaledSize(QSize(self.size, self.size))
            img = reader.read()
            if not img.isNull():
                pix = QPixmap.fromImage(img)
                self.emitter.ready.emit(self.path, pix)
                return
        except Exception:
            pass

        # Fallback to Pillow in worker thread
        try:
            from PIL import Image, ImageOps, ImageQt
            im = Image.open(self.path)
            im.thumbnail((self.size, self.size), Image.LANCZOS)
            if im.mode != "RGBA":
                im = im.convert("RGBA")
            qim = ImageQt.ImageQt(im)
            pix = QPixmap.fromImage(qim)
            self.emitter.ready.emit(self.path, pix)
            return
        except Exception:
            pass

        # Final fallback: empty placeholder
        try:
            empty = QPixmap(self.size, self.size)
            empty.fill(Qt.lightGray)
            self.emitter.ready.emit(self.path, empty)
        except Exception:
            pass

# Shared pool & emitter to reuse across the app
_thumbnail_result_emitter = ThumbnailResult()
_thumbnail_thread_pool = QThreadPool.globalInstance()


class DetailsPanel(QWidget):
    """
    Rich metadata panel:
    - Preview (auto-rotated)
    - DB row (photo_metadata)
    - Filesystem stats
    - EXIF (camera, lens, ISO, shutter, aperture, focal length, date taken, GPS)
    - 'Copy' button to copy all metadata as plain text
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(320)

        # Widgets
        self.thumb = QLabel(alignment=Qt.AlignCenter)
        self.thumb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.meta = QLabel(alignment=Qt.AlignTop)
        self.meta.setWordWrap(True)

        self.btn_copy = QPushButton("Copy")
        self.btn_copy.setToolTip("Copy all metadata as plain text")
        self.btn_copy.clicked.connect(self._copy_all)

        # Style
        self.meta.setStyleSheet("""
            QLabel {
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 10pt;
                color: #333;
            }
            table {
                border-collapse: collapse;
            }
            td {
                padding: 2px 6px;
                vertical-align: top;
            }
            b {
                color: #004A7F;
            }
            .hdr {
                color: #004A7F; font-weight: 600; margin-top: 6px;
            }
        """)

        # Layout
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        lay.addWidget(self.thumb, 3)
        lay.addWidget(self.meta, 2)
        lay.addWidget(self.btn_copy, 0, alignment=Qt.AlignRight)

        # Internal: last plain-text metadata
        self._last_plaintext = ""

    def clear(self):
        self.thumb.clear()
        self.meta.setText("")
        self._last_plaintext = ""

    # ---------- Public API ----------
    def update_path(self, path: str):
        """Update preview and metadata for selected image."""
        if not path:
            self.clear()
            return

        import os, time
        base = os.path.basename(path)

        # --- Preview (safe; QImageReader honors EXIF orientation) ---
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        img = reader.read()
        if not img.isNull():
            pm = QPixmap.fromImage(img).scaledToWidth(260, Qt.SmoothTransformation)
            self.thumb.setPixmap(pm)
        else:
            self.thumb.setText("(no preview)")

        # --- Collect data from DB + FS + EXIF ---
        from reference_db import ReferenceDB
        db = ReferenceDB()
        row = db.get_photo_metadata_by_path(path) or {}

        # filesystem
        try:
            import os, time
            st = os.stat(path)
            fs_size_kb = f"{st.st_size/1024:,.1f} KB"
#            fs_mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
            fs_mtime = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(st.st_mtime))
        except Exception:
            fs_size_kb, fs_mtime = "-", "-"

        # >>> FIX: merge extra metadata (dimensions, safe size, modified)
        extra = self._file_metadata_info(path)
        if extra:
            if extra.get("width") and extra.get("height"):
                row["width"] = extra["width"]
                row["height"] = extra["height"]
            if fs_size_kb in ("-", None):
                fs_size_kb = f"{extra.get('size_kb', 0):,.1f} KB"
            if fs_mtime in ("-", None):
                fs_mtime = extra.get("modified", "-")
        # <<< FIX

        # EXIF
        exif = self._read_exif(path)  # dict with safe keys

        # prefer DB date_taken, then EXIF, then fs modified
        date_taken = row.get("date_taken") or exif.get("Date Taken") or fs_mtime

        # --- Build HTML table (compact) ---
        def r(k, v):
            v = "-" if v in (None, "", []) else v
            return f"<tr><td><b>{k}</b></td><td>{v}</td></tr>"


        db_rows = []
        for k in ("folder_id", "width", "height", "size_kb", "modified", "tags"):
            if k in row:
                db_rows.append(r(k, row.get(k)))
        # >>> FIX (nice to have): show a humanized DB size if available
        try:
            sk = row.get("size_kb")
            if sk not in (None, "", "-"):
                skf = float(sk)
                if skf >= 1024:
                    db_rows.append(r("size_db_pretty", f"{skf/1024:,.2f} MB"))
                else:
                    db_rows.append(r("size_db_pretty", f"{skf:,.1f} KB"))
        except Exception:
            pass

        exif_rows = []
        for key in ("Camera", "Lens", "ISO", "Shutter", "Aperture", "Focal Length",
                    "Orientation", "Date Taken", "GPS"):
            if exif.get(key) not in (None, "", []):
                exif_rows.append(r(key, exif[key]))

        fs_rows = [
            r("File", base),
            r("Path", path),
            r("Size (FS)", fs_size_kb),
            r("Modified (FS)", fs_mtime),
#            r("Date Taken (final)", date_taken),
            r("Dimensions", f"{row.get('width','-')} × {row.get('height','-')}"),
            r("Date Taken (final)", date_taken),            
        ]

        sections = []
        if db_rows:
            sections.append(f"<div class='hdr'>Database</div><table>{''.join(db_rows)}</table>")
        if exif_rows:
            sections.append(f"<div class='hdr'>EXIF</div><table>{''.join(exif_rows)}</table>")
        sections.append(f"<div class='hdr'>File</div><table>{''.join(fs_rows)}</table>")

        html = f"<b>{base}</b><br>{''.join(sections)}"
        self.meta.setText(html)

        # Prepare plain text for copy
        self._last_plaintext = self._to_plaintext(base, row, fs_size_kb, fs_mtime, date_taken, exif)

    # ---------- Helpers ----------

    def _file_metadata_info(self, path: str) -> dict:
        """Return file size, modification time, and image dimensions safely."""
        info = {"size_kb": None, "width": None, "height": None, "modified": None}
        try:
            if not path or not os.path.exists(path):
                return info
            st = os.stat(path)
#            info["size_kb"] = round(st.st_size / 1024.0, 3)
#            info["modified"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))

            info["size_kb"] = round(st.st_size / 1024.0, 3)
            info["modified"] = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(st.st_mtime))
            # Use QImageReader for dimensions only (fast, no full decode)
            reader = QImageReader(path)
            sz = reader.size()
            if sz and sz.width() > 0 and sz.height() > 0:
                info["width"], info["height"] = sz.width(), sz.height()
        except Exception as e:
            print(f"[DetailsPanel] metadata info failed for {path}: {e}")
        return info

    def _copy_all(self):
        if not self._last_plaintext:
            return
        QApplication.clipboard().setText(self._last_plaintext)

    def _to_plaintext(self, base, row, fs_size_kb, fs_mtime, date_taken, exif):
        # minimal, readable copy
        lines = [f"File: {base}"]
        if row:
            lines.append("[Database]")
            for k in ("folder_id", "width", "height", "size_kb", "modified", "tags"):
                if k in row:
                    lines.append(f"  {k}: {row.get(k)}")
        if exif:
            lines.append("[EXIF]")
            for k in ("Camera", "Lens", "ISO", "Shutter", "Aperture",
                      "Focal Length", "Orientation", "Date Taken", "GPS"):
                v = exif.get(k)
                if v not in (None, "", []):
                    lines.append(f"  {k}: {v}")
        lines.append("[File]")
        lines.append(f"  Size (FS): {fs_size_kb}")
        lines.append(f"  Modified (FS): {fs_mtime}")
        lines.append(f"  Date Taken (final): {date_taken}")
        return "\n".join(lines)

    def _read_exif(self, path: str) -> dict:
        """
        Extract a curated set of EXIF fields. Safe for images without EXIF.
        Returns: {
          "Camera": "...", "Lens": "...", "ISO": 100,
          "Shutter": "1/125 s", "Aperture": "f/2.8",
          "Focal Length": "50 mm", "Orientation": "Rotate 90 CW",
          "Date Taken": "YYYY-MM-DD HH:MM:SS",
          "GPS": "12.3456, -98.7654"
        }
        """
        result = {}
        try:
            from PIL import Image, ExifTags
            # Try enabling HEIC/HEIF support if Pillow plugin is present.
            try:
                import pillow_heif  # noqa
                # pillow_heif.register_heif_opener()  # recent versions auto-register
            except Exception:
                pass

            with Image.open(path) as im:
                exif = im.getexif()
                if not exif:
                    return result
                # Build reverse tag map
                TAGS = ExifTags.TAGS
                # Simple fields
                def get_by_name(name):
                    for k, v in exif.items():
                        if TAGS.get(k) == name:
                            return v
                    return None

                make = get_by_name("Make")
                model = get_by_name("Model")
                lens = get_by_name("LensModel") or get_by_name("LensMake")
                iso = get_by_name("ISOSpeedRatings") or get_by_name("PhotographicSensitivity")
                dt = (get_by_name("DateTimeOriginal") or
                      get_by_name("DateTimeDigitized") or
                      get_by_name("DateTime"))
                orientation = get_by_name("Orientation")
                focal = get_by_name("FocalLength")
                fnum = get_by_name("FNumber")
                exposure = get_by_name("ExposureTime")

                # Camera
                cam = None
                if make and model:
                    cam = f"{str(make).strip()} {str(model).strip()}".strip()
                elif model:
                    cam = str(model).strip()
                elif make:
                    cam = str(make).strip()
                if cam:
                    result["Camera"] = cam
                if lens:
                    result["Lens"] = str(lens)

                # ISO
                if isinstance(iso, (list, tuple)) and iso:
                    iso = iso[0]
                if iso:
                    result["ISO"] = int(iso) if str(iso).isdigit() else str(iso)

                # Aperture
                if fnum:
                    result["Aperture"] = self._format_fnumber(fnum)

                # Focal length
                if focal:
                    result["Focal Length"] = self._format_rational_mm(focal)

                # Shutter
                if exposure:
                    result["Shutter"] = self._format_exposure(exposure)

                # Orientation (human text)
                if orientation:
                    orient_map = {
                        1: "Normal",
                        3: "Rotate 180",
                        6: "Rotate 90 CW",
                        8: "Rotate 90 CCW",
                    }
                    result["Orientation"] = orient_map.get(int(orientation), str(orientation))

                # Date Taken
                if dt:
                    result["Date Taken"] = str(dt).replace(":", "-", 2)  # "YYYY:MM:DD ..." -> "YYYY-MM-DD ..."

                # GPS
                gps_ifd = None
                for k, v in exif.items():
                    if TAGS.get(k) == "GPSInfo":
                        gps_ifd = v
                        break
                if gps_ifd:
                    gps = self._extract_gps(gps_ifd)
                    if gps:
                        result["GPS"] = gps
        except Exception:
            # Silent: keep panel resilient
            pass
        return result

    # ---------- EXIF format helpers ----------
    def _format_fnumber(self, fnum):
        # fnum can be Rational, (num, den), or float
        try:
            v = self._to_float(fnum)
            return f"f/{v:.1f}"
        except Exception:
            return str(fnum)

    def _format_rational_mm(self, value):
        try:
            v = self._to_float(value)
            return f"{v:.0f} mm"
        except Exception:
            return str(value)

    def _format_exposure(self, exp):
        """
        exp may be a float seconds (e.g., 0.008) or a fraction (num, den).
        Render as a nice '1/125 s' or '0.5 s'.
        """
        try:
            v = self._to_float(exp)
            if v <= 0:
                return str(exp)
            if v < 1:
                # show as 1/x
                denom = round(1.0 / v)
                # avoid things like 1/1
                if denom > 1:
                    return f"1/{denom} s"
            # >= 1s
            if v.is_integer():
                return f"{int(v)} s"
            return f"{v:.2f} s"
        except Exception:
            return str(exp)

    def _to_float(self, val):
        # Rational or tuple
        if isinstance(val, tuple) and len(val) == 2:
            num, den = val
            return float(num) / float(den) if den else float(num)
        # PIL may expose its own Rational type
        try:
            return float(val)
        except Exception:
            # Some EXIF types (e.g., IFDRational) have .numerator/.denominator
            num = getattr(val, "numerator", None)
            den = getattr(val, "denominator", None)
            if num is not None and den not in (None, 0):
                return float(num) / float(den)
            raise

    def _extract_gps(self, gps_ifd) -> str | None:
        """
        gps_ifd is a dict keyed by numeric GPS tags. Convert to "lat, lon".
        """
        try:
            from PIL.ExifTags import GPSTAGS
            # Map numeric keys to names
            gps = {GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}
            lat = self._gps_to_deg(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef"))
            lon = self._gps_to_deg(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef"))
            if lat is not None and lon is not None:
                return f"{lat:.6f}, {lon:.6f}"
        except Exception:
            pass
        return None

    def _gps_to_deg(self, value, ref):
        """
        value: [(deg_num,deg_den),(min_num,min_den),(sec_num,sec_den)] or rationals
        ref: 'N'/'S' or 'E'/'W'
        """
        if not value or not ref:
            return None
        try:
            def rf(x):
                return self._to_float(x)
            d = rf(value[0]); m = rf(value[1]); s = rf(value[2])
            deg = d + (m / 60.0) + (s / 3600.0)
            if str(ref).upper() in ("S", "W"):
                deg = -deg
            return deg
        except Exception:
            return None


class BackfillStatusPanel(QWidget):
    """
    Simple panel that shows last lines of app_log.txt related to backfill and offers
    quick start/foreground-run buttons.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.setMaximumHeight(240)
        layout = QVBoxLayout(self)
        lbl = QLabel("<b>Metadata Backfill Status</b>")
        layout.addWidget(lbl)
        self.txt = QLabel("(no log yet)")
        self.txt.setWordWrap(True)
        self.txt.setStyleSheet("font-family: monospace;")
        layout.addWidget(self.txt, 1)
        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("Start (background)")
        self.btn_fore = QPushButton("Run (foreground)")
        self.btn_stop = QPushButton("Stop (not implemented)")
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_fore)
        btn_row.addWidget(self.btn_stop)
        layout.addLayout(btn_row)
        self.btn_start.clicked.connect(self._on_start_background)
        self.btn_fore.clicked.connect(self._on_run_foreground)
        self.btn_stop.clicked.connect(self._on_stop)
        self._tail_log()

    def _get_config(self):
        """
        Safely obtain the settings object:
         - Prefer the top-level MainWindow (self.window()) if it exposes .settings
         - Fallback to module SettingsManager()
        """
        try:
            top = self.window()
            if top is not None and hasattr(top, "settings"):
                return top.settings
        except Exception:
            pass
        # Fallback: import SettingsManager and return a new instance (read-only defaults)
        try:
            from settings_manager_qt import SettingsManager
            return SettingsManager()
        except Exception:
            return None

    def _launch_detached(self):
        script = Path(__file__).resolve().parent / "workers" / "meta_backfill_pool.py"
        if not script.exists():
            QMessageBox.warning(self, "Backfill Script Missing", f"{script} not found.")
            return

        settings = self._get_config()
        workers = settings.get("meta_workers", 4) if settings else 4
        timeout = settings.get("meta_timeout_secs", 8.0) if settings else 8.0
        batch = settings.get("meta_batch", 200) if settings else 200

        args = [sys.executable, str(script),
                "--workers", str(int(workers)),
                "--timeout", str(float(timeout)),
                "--batch", str(int(batch))]
        kwargs = {"close_fds": True}

        # On Windows, try to suppress console windows:
        if os.name == "nt":
            # Prefer pythonw.exe if available (no console)
            pythonw = None
            try:
                import shutil
                pythonw = shutil.which("pythonw")
            except Exception:
                pythonw = None

            if pythonw:
                args[0] = pythonw
            else:
                # If pythonw not available, use CREATE_NO_WINDOW and startupinfo to hide window
                try:
                    kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                except Exception:
                    kwargs["creationflags"] = 0x08000000

                try:
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    si.wShowWindow = subprocess.SW_HIDE
                    kwargs["startupinfo"] = si
                except Exception:
                    pass
        else:
            # For POSIX, detach the child so it doesn't hold terminal (start new session)
            kwargs["start_new_session"] = True

        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
            QMessageBox.information(self, "Backfill", "Persistent backfill started in background.")
        except Exception as e:
            QMessageBox.critical(self, "Backfill Error", str(e))

    def _on_start_background(self):
        # Quick guard: spawn detached in main thread (fire-and-forget)
        try:
            self._launch_detached()
        except Exception as e:
            QMessageBox.critical(self, "Backfill Error", str(e))

    def _on_run_foreground(self):
        """
        Run backfill in a background Python thread (foreground process run),
        so the GUI thread doesn't block. Use the same config resolution helper.
        """
        def run():
            script = Path(__file__).resolve().parent / "workers" / "meta_backfill_pool.py"
            settings = self._get_config()
            workers = settings.get("meta_workers", 4) if settings else 4
            timeout = settings.get("meta_timeout_secs", 8.0) if settings else 8.0
            batch = settings.get("meta_batch", 200) if settings else 200

            cmd = [sys.executable, str(script),
                   "--workers", str(int(workers)),
                   "--timeout", str(float(timeout)),
                   "--batch", str(int(batch))]
            try:
                subprocess.run(cmd)
                QMessageBox.information(self, "Backfill", "Foreground backfill finished.")
            except Exception as e:
                QMessageBox.critical(self, "Backfill", str(e))

        Thread(target=run, daemon=True).start()

    def _on_stop(self):
        QMessageBox.information(self, "Stop", "Stopping detached backfill requires PID-tracking or OS tools. Not implemented in GUI.")

    def _tail_log(self):
        try:
            p = Path.cwd() / "app_log.txt"
            if not p.exists():
                self.txt.setText("(no log yet)")
                return
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()[-12:]
            filtered = [l for l in lines if "meta_backfill" in l or "worker" in l or "supervisor" in l]
            if not filtered:
                filtered = lines[-6:]
            self.txt.setText("\n".join(filtered[-6:]))
        except Exception as e:
            self.txt.setText(str(e))



class MainWindow(QMainWindow):
    PROGRESS_DIALOG_THRESHOLD = 10  # 👈 only show dialog if photo count >= X
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MemoryMate - PhotoFlow")
        
        # keep rest of initializer logic, but ensure some attributes exist
        # keep rest of initializer logic, but ensure some attributes exist
        self.settings = SettingsManager()
        self._committed_total = 0
        self._scan_result = (0, 0)  # folders, photos
        
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)
        QApplication.instance().setAttribute(Qt.AA_SynthesizeMouseForUnhandledTouchEvents, True)
        QApplication.instance().setAttribute(Qt.AA_SynthesizeTouchForUnhandledMouseEvents, True)
        QApplication.instance().setAttribute(Qt.AA_CompressTabletEvents, False)

        # Get the available geometry of the primary screen
        screen = QGuiApplication.primaryScreen().availableGeometry()

        # Set the main window to fit the available screen (without hiding under taskbar)

        # Optional: add a margin if you want some breathing room
        margin = 50
        self.setGeometry(screen.adjusted(margin, margin, -margin, -margin))

        # Move to top-left of available area  
        self.move(screen.center() - self.rect().center())

        
        if not self.settings.get("show_decoder_warnings", False):
            print("🔇 Qt/Pillow decoder warnings silenced (per user settings).")
        else:
            print("⚠️ Decoder warnings ENABLED (verbose mode).")
        
        self.active_tag_filter = "all"

        # === Toolbar & Menus via UIBuilder ===
        ui = UIBuilder(self)
        tb = ui.make_toolbar("Tools")
        # Defer connecting handlers until grid exists
        act_select_all = ui.action("Select All")
        act_clear_sel = ui.action("Clear")
        act_open = ui.action("Open")
        act_delete = ui.action("Delete")
        ui.separator()

        folded = bool(self.settings.get("sidebar_folded", False))

        # Create action early — connect it later when sidebar exists
        self.act_fold_unfold = ui.action(
            "Fold/Unfold Sidebar",
            shortcut="Ctrl+Shift+F",
            tooltip="Toggle collapse/expand of the sidebar (Ctrl+Shift+F)",
            checkable=True
        )
        self.act_fold_unfold.setChecked(folded)
        ui.separator()

        # === Menu Bar ===
        menu_bar = self.menuBar()
        menu_settings = menu_bar.addMenu("⚙️ Settings")

        act_preferences = QAction("Preferences…", self)
        act_preferences.setShortcut("Ctrl+,")
        act_preferences.setIcon(QIcon.fromTheme("preferences-system"))
        menu_settings.addAction(act_preferences)
        act_preferences.triggered.connect(self._open_preferences)


        # ... after act_preferences created ...
        act_toggle_sidebar_mode = QAction("Toggle Sidebar Mode (List/Tabs)", self)
        act_toggle_sidebar_mode.setShortcut("Ctrl+Alt+S")
        act_toggle_sidebar_mode.setToolTip("Toggle Sidebar between List and Tabs (Ctrl+Alt+S)")
        menu_settings.addAction(act_toggle_sidebar_mode)

        menu_settings.addSeparator()
        menu_settings.addAction("About Photo App", lambda: QMessageBox.information(self, "About", "MemoryMate PhotoFlow  (Alpha)\n© 2025"))

        # === Database Menu ===
        menu_db = menu_bar.addMenu("🗄️ Database")

        act_db_fresh = QAction("Fresh Start (delete DB)…", self)
        act_db_check = QAction("Self-Check / Report…", self)
        act_db_rebuild_dates = QAction("Rebuild Date Index", self)  # safe no-op if not implemented
        act_migrate = QAction("Data Migration… (created_ts / year / date)", self)
        
        menu_db.addAction(act_db_fresh)
        menu_db.addAction(act_db_check)
        menu_db.addSeparator()
        menu_db.addAction(act_db_rebuild_dates)
        menu_db.addAction(act_migrate)
                
        # Metadata Backfill submenu (use menu_bar which is already defined above)
        meta_menu = menu_bar.addMenu("🔍 Metadata Backfill")
        act_meta_start = meta_menu.addAction("Start Persistent Backfill (background)")
        act_meta_single = meta_menu.addAction("Run Backfill (foreground)")
        act_meta_auto = meta_menu.addAction("Auto-run after scan")
        act_meta_auto.setCheckable(True)
        act_meta_auto.setChecked(self.settings.get("auto_run_backfill_after_scan", False))

        act_meta_start.triggered.connect(lambda: self.backfill_panel._on_start_background())
        act_meta_single.triggered.connect(lambda: self.backfill_panel._on_run_foreground())
        act_meta_auto.toggled.connect(lambda v: self.settings.set("auto_run_backfill_after_scan", bool(v)))
 
        # === 🧠 Tag Filters ===
        tool_tag_bar = ui.menu("🧠 Tags")
        self.btn_all = ui.menu_action(tool_tag_bar, "All", checkable=True, handler=lambda: self._apply_tag_filter("all"))
        self.btn_all.setChecked(True)
        self.btn_fav = ui.menu_action(tool_tag_bar, "⭐ Favorites", checkable=True, handler=lambda: self._apply_tag_filter("favorite"))
        self.btn_faces = ui.menu_action(tool_tag_bar, "🧍 Faces", checkable=True, handler=lambda: self._apply_tag_filter("face")) 

        tools_menu = self.menuBar().addMenu("🧰 Tools")

        act_clear_cache = tools_menu.addAction("🧹 Clear Thumbnail Cache…")
        act_clear_cache.triggered.connect(self._on_clear_thumbnail_cache)

        act_migrate.triggered.connect(self._run_date_migration)
        act_db_fresh.triggered.connect(self._db_fresh_start)
        act_db_check.triggered.connect(self._db_self_check)
        act_db_rebuild_dates.triggered.connect(self._db_rebuild_date_index)
        
        act_optimize = QAction("Optimize Indexes (date/updated)", self)
        menu_db.addAction(act_optimize)

        def _optimize_db():
            try:
                ReferenceDB().optimize_indexes()
                QMessageBox.information(self, "Database", "Indexes created/verified successfully.")
            except Exception as e:
                QMessageBox.critical(self, "Database Error", str(e))

        act_optimize.triggered.connect(_optimize_db)    

        # 📂 Scan Repository Action
        act_scan_repo = tb.addAction("📂 Scan Repository…")

        # Toggle action: show/hide the BackfillStatusPanel
        self.act_toggle_backfill = QAction(QIcon.fromTheme("view-sidebar"), "Backfill Panel", self)
        self.act_toggle_backfill.setCheckable(True)
        
        # read persisted preference (fallback True)
        _visible_default = bool(self.settings.get("show_backfill_panel", True))
        
        self.act_toggle_backfill.setChecked(_visible_default)
        self.act_toggle_backfill.setToolTip("Show / hide Metadata Backfill status panel")
        
        tb.addAction(self.act_toggle_backfill)

        # Connect toggle signal to a handler that safely creates/shows/hides the panel and persists the choice
        self.act_toggle_backfill.toggled.connect(self._on_toggle_backfill_panel)

        # Ensure panel initial visibility matches saved preference (if panel already created)
        try:
            if hasattr(self, "backfill_panel") and self.backfill_panel is not None:
                self.backfill_panel.setVisible(_visible_default)
        except Exception:
            pass

        # 🛑 Cancel Scan button (programmatic cancel trigger)
        self.act_cancel_scan = ui.action(
            "🛑 Cancel Scan",
            icon="process-stop",
            tooltip="Abort an ongoing repository scan immediately",
            handler=self._on_cancel_scan_clicked
        )
        self.act_cancel_scan.setEnabled(False)

        # Incremental vs full scan
        self.chk_incremental = ui.checkbox("Incremental", checked=True)
        ui.separator()

        # 🔽 Sorting and filtering controls
        self.sort_combo = ui.combo_sort("Sort:", ["Filename", "Date", "Size"], self._apply_sort_filter)
        self.sort_order_combo = QSortComboBox()
        self.sort_order_combo.addItems(["Ascending", "Descending"])
        self.sort_order_combo.currentIndexChanged.connect(lambda *_: self._apply_sort_filter())
        tb.addWidget(self.sort_order_combo)

        # --- Central container
        container = QWidget()
        self.setCentralWidget(container)
        main_layout = QVBoxLayout(container)

        # Backfill status panel
        try:
            self.backfill_panel = BackfillStatusPanel(self)
            main_layout.addWidget(self.backfill_panel)
        except Exception:
            pass
            
        # --- Top bar
        topbar = QWidget()
        top_layout = QHBoxLayout(topbar)
        top_layout.setContentsMargins(6, 6, 6, 6)

        self.project_combo = QComboBox()
        self._projects = list_projects()
        for p in self._projects:
            self.project_combo.addItem(f"{p['id']} — {p['name']} ({p['mode']})", p["id"])
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)

        top_layout.addWidget(QLabel("Project:"))
        top_layout.addWidget(self.project_combo, 1)
        main_layout.addWidget(topbar)

        # --- Main layout (Sidebar + Grid + Details)
        self.splitter = QSplitter(Qt.Horizontal)

        default_pid = get_default_project_id()
        if default_pid is None and self._projects:
            default_pid = self._projects[0]["id"]

        self.sidebar = SidebarQt(project_id=default_pid)
        

        # === Lazy wiring for sidebar actions (now sidebar exists) ===
        def _on_fold_toggle(checked):
            try:
                if hasattr(self, "sidebar") and self.sidebar:
                    self.sidebar.toggle_fold(bool(checked))
                self.settings.set("sidebar_folded", bool(checked))
            except Exception as e:
                print(f"[MainWindow] fold toggle failed: {e}")

        self.act_fold_unfold.toggled.connect(_on_fold_toggle)
        try:
            self.sidebar.toggle_fold(folded)
        except Exception:
            pass

        def _on_toggle_sidebar_mode():
            try:
                current = self.sidebar._effective_display_mode()
                new_mode = "tabs" if current == "list" else "list"
                self.sidebar.switch_display_mode(new_mode)
                self.settings.set("sidebar_mode", new_mode)
            except Exception as e:
                print(f"[MainWindow] toggle sidebar mode failed: {e}")

        act_toggle_sidebar_mode.triggered.connect(_on_toggle_sidebar_mode)

        # === Controllers ===
        self.scan_controller = ScanController(self)
        self.sidebar_controller = SidebarController(self)
        self.project_controller = ProjectController(self)

        self.sidebar.on_branch_selected = self.sidebar_controller.on_branch_selected
        self.sidebar.folderSelected.connect(self.sidebar_controller.on_folder_selected)
        
        self.splitter.addWidget(self.sidebar)

        self.grid = ThumbnailGridQt(project_id=default_pid)
        self.splitter.addWidget(self.grid)

        # 🔗 Now that grid exists — connect toolbar actions safely
        act_select_all.triggered.connect(self.grid.list_view.selectAll)
        act_clear_sel.triggered.connect(self.grid.list_view.clearSelection)
        act_open.triggered.connect(lambda: self._open_lightbox_from_selection())
        act_delete.triggered.connect(lambda: self._request_delete_from_selection())

       # === Thumbnail Manager wiring ===
        self.thumb_cache = {}
        try:
            self.thumbnails = ThumbnailManager(
                grid=self.grid,
                cache=self.thumb_cache,
                log=getattr(self, "gui_log", None),
                initial_size=160
            )
        except Exception as e:
            print("[MainWindow] ⚠️ ThumbnailManager init failed:", e)
            self.thumbnails = None

        # Hook zoom slider to ThumbnailManager if available
        if hasattr(self.grid, "zoom_slider"):
            self.grid.zoom_slider.valueChanged.connect(
                lambda val: self.thumbnails.update_zoom(val) if self.thumbnails else None
            )

        # --- Details panel on the right
        self.details = DetailsPanel(self)
        self.splitter.addWidget(self.details)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)

        main_layout.addWidget(self.splitter, 1)

        if default_pid is not None:
            idx = self.project_combo.findData(default_pid)
            if idx >= 0:
                self.project_combo.setCurrentIndex(idx)

        # --- Wire toolbar actions
        act_select_all.triggered.connect(self.grid.list_view.selectAll)
        act_clear_sel.triggered.connect(self.grid.list_view.clearSelection)
        act_open.triggered.connect(lambda: self._open_lightbox_from_selection())
        act_delete.triggered.connect(lambda: self._request_delete_from_selection())

        act_scan_repo.triggered.connect(self._on_scan_repository)

        self.sort_combo.currentIndexChanged.connect(lambda *_: self._apply_sort_filter())
        self.sort_order_combo.currentIndexChanged.connect(lambda *_: self._apply_sort_filter())

        # --- Grid signals
        self.grid.selectionChanged.connect(
            lambda n: self.statusBar().showMessage(f"{n} selected")
        )
        self.grid.openRequested.connect(lambda p: self._open_lightbox(p))
        self.grid.deleteRequested.connect(lambda paths: self._confirm_delete(paths))

        # --- Auto-update details panel on selection change
        def _update_details_from_selection():
            paths = self.grid.get_selected_paths()
            self.details.update_path(paths[-1] if paths else None)
            
        self.grid.selectionChanged.connect(lambda *_: _update_details_from_selection())
        
        # === Initialize periodic progress pollers (for detached workers) ===
        try:
            self.app_root = os.getcwd()  # base path for 'status/' folder
            os.makedirs(os.path.join(self.app_root, "status"), exist_ok=True)
            self._init_progress_pollers()
        except Exception as e:
            print(f"[MainWindow] ⚠️ Progress pollers init failed: {e}")

        # === Initialize database schema at startup ===
        try:
            from repository.base_repository import DatabaseConnection
            db_conn = DatabaseConnection("reference_data.db", auto_init=True)
            print("[Startup] Database schema initialized successfully")
        except Exception as e:
            print(f"[Startup] ⚠️ Database initialization failed: {e}")
            import traceback
            traceback.print_exc()


# =========================
    def _init_db_and_sidebar(self):
        """
        Initialize database schema, ensure created_* date fields, backfill if needed,
        optimize indexes, and reload the sidebar date tree.

        Runs on app startup to make sure the date navigation works immediately.
        """
        from reference_db import ReferenceDB
        self.db = ReferenceDB()

        # NOTE: Schema creation and migrations are now handled automatically
        # by repository layer during ReferenceDB initialization.
        # created_* columns are added via migration system (v1.5.0 migration).

        # 🕰 Backfill if needed (populate data in existing columns)
        try:
            updated_rows = self.db.single_pass_backfill_created_fields()
            if updated_rows:
                print(f"[DB] Backfilled {updated_rows} legacy rows with created_* fields.")
        except Exception as e:
            print(f"[DB] Backfill failed (possibly empty DB): {e}")

        # ⚡ Optimize indexes (important for large photo libraries)
        try:
            self.db.optimize_indexes()
        except Exception as e:
            print(f"[DB] optimize_indexes failed: {e}")

        # 🌳 Reload sidebar date tree
        try:
            self.sidebar.reload_date_tree()
            print("[Sidebar] Date tree reloaded.")
        except Exception as e:
            print(f"[Sidebar] Failed to reload date tree: {e}")
  
    # ============================================================
    # 🏷️ Tag filter handler
    # ============================================================
    def _apply_tag_filter(self, tag: str | None):
        """
        Apply or clear a tag filter without changing navigation context.
        """
        if not hasattr(self, "grid"):
            return

        if tag in (None, "", "all"):
            self.grid.apply_tag_filter(None)
            self.statusBar().showMessage("Showing all photos")
            print("[TAG FILTER] Cleared.")
        else:
            self.grid.apply_tag_filter(tag)
            msg = f"Filtered by tag '{tag}'"
            self.statusBar().showMessage(msg)
            print(f"[TAG FILTER] Applied: {tag}")


    def _clear_tag_filter(self):
        """Clear active tag overlay and restore previous grid navigation mode."""
        if getattr(self, "active_tag_filter", None):
            print("[TAG FILTER] Cleared by navigation.")
        self.active_tag_filter = None
        self.grid.active_tag_filter = None

        # ✅ Restore the last navigation mode and key
        prev_mode = getattr(self, "last_nav_mode", "branch")
        if self.grid.load_mode == "tag":
            self.grid.load_mode = prev_mode

            # Restore keys based on last mode
            if prev_mode == "branch":
                self.grid.branch_key = getattr(self, "last_nav_key", None)
            elif prev_mode == "folder":
                self.grid.current_folder_id = getattr(self, "last_nav_key", None)
            elif prev_mode == "date":
                self.grid.date_key = getattr(self, "last_nav_key", None)

        # Force refresh
        if hasattr(self.grid, "reload"):
            self.grid.reload()


    def _on_clear_thumbnail_cache(self):
        if QMessageBox.question(
            self,
            "Clear Thumbnail Cache",
            "This will delete all generated thumbnail cache files.\n"
            "They will be rebuilt automatically as needed.\n\nProceed?",
            QMessageBox.Yes | QMessageBox.No
        ) == QMessageBox.Yes:
            ok = clear_thumbnail_cache()
            if ok:
                QMessageBox.information(self, "Cache Cleared", "Thumbnail cache has been cleared.")
            else:
                QMessageBox.warning(self, "Cache", "No cache folder found or could not clear.")


    def _open_preferences(self):
        dlg = PreferencesDialog(self.settings, self)
        if dlg.exec() == QDialog.Accepted:
            # Apply live UI updates if needed
            if self.settings.get("dark_mode", False):
                self._apply_dark_mode()
            else:
                self._apply_light_mode()


    def _apply_dark_mode(self):
        """Switch the app palette to dark mode (safe for PySide6)."""
        app = QApplication.instance()
        dark = QPalette()
        dark.setColor(QPalette.Window, QColor(30, 30, 30))
        dark.setColor(QPalette.WindowText, Qt.white)
        dark.setColor(QPalette.Base, QColor(25, 25, 25))
        dark.setColor(QPalette.AlternateBase, QColor(35, 35, 35))
        dark.setColor(QPalette.ToolTipBase, Qt.white)
        dark.setColor(QPalette.ToolTipText, Qt.white)
        dark.setColor(QPalette.Text, Qt.white)
        dark.setColor(QPalette.Button, QColor(45, 45, 45))
        dark.setColor(QPalette.ButtonText, Qt.white)
        dark.setColor(QPalette.BrightText, Qt.red)
        dark.setColor(QPalette.Link, QColor(42, 130, 218))
        dark.setColor(QPalette.Highlight, QColor(42, 130, 218))
        dark.setColor(QPalette.HighlightedText, Qt.black)
        app.setPalette(dark)


    def _run_date_migration(self):
        """
        Manual, idempotent migration:
          1) Ensure created_* columns + indexes
          2) Backfill in chunks with progress dialog
        """
        # Step 1: Get database reference
        # NOTE: Schema columns are automatically created via migration system
        db = ReferenceDB()

        # Step 2: how much to do? (check for data to backfill)
        total = db.count_missing_created_fields()
        if total == 0:
            QMessageBox.information(self, "Migration", "Nothing to migrate — fields already populated.")
            return

        progress = QProgressDialog("Backfilling created_* fields…", "Cancel", 0, total, self)
        progress.setWindowTitle("Database Migration")
        progress.setWindowModality(Qt.WindowModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()

        processed = 0
        CHUNK = 1000
        while processed < total:
            if progress.wasCanceled():
                break
            n = db.single_pass_backfill_created_fields(CHUNK)
            if n <= 0:
                break
            processed += n
            progress.setValue(min(processed, total))
            QApplication.processEvents()

        progress.setValue(total)
        progress.close()
        # Optional: refresh sidebar, in case you already render date branches
        try:
            self.sidebar.reload()
        except Exception:
            pass
        QMessageBox.information(self, "Migration", f"Completed. Updated ~{processed} rows.")


    def _db_fresh_start(self):
        """
        Delete/backup the current DB file and recreate an empty schema.
        Then clear UI and let the user run a new scan.
        """
        ret = QMessageBox.warning(
            self,
            "Fresh Start (delete DB)",
            "This will erase the current database (a backup .bak_YYYYMMDD_HHMMSS will be created if possible).\n\n"
            "Are you sure you want to continue?",
            QMessageBox.Yes | QMessageBox.No
        )
        if ret != QMessageBox.Yes:
            return

        try:
            # --- 🛑 Stop anything that might hold the DB ---
            if hasattr(self, "thumb_grid"):
                self.thumb_grid.shutdown_threads()  # stop background loaders

            if hasattr(self, "sidebar"):
                self.sidebar.db = None  # release ReferenceDB handle

            # --- 🧹 Reset DB ---
            db = ReferenceDB()
            db.fresh_reset()

            # --- 🧼 Clear UI ---
            if hasattr(self, "grid"):
                self.grid.clear()

            if hasattr(self, "sidebar"):
                # rebind fresh DB after reset
                self.sidebar.db = ReferenceDB()
                self.sidebar.reload()

            QMessageBox.information(
                self,
                "Database Reset",
                "✅ A fresh empty database was created.\n\nNow run: 📂 Scan Repository…"
            )

        except Exception as e:
            QMessageBox.critical(self, "Reset Failed", f"❌ {e}")


    def _db_self_check(self):
        """
        Run integrity PRAGMA and show basic counts to confirm a healthy scan.
        """
        try:
            db = ReferenceDB()
            rep = db.integrity_report()
            counts = rep.get("counts", {})
            ok = rep.get("ok", False)
            errors = rep.get("errors", [])
            msg = []
            msg.append("Integrity: " + ("OK ✅" if ok else "FAIL ❌"))
            msg.append("")
            msg.append("Counts:")
            msg.append(f"  • photo_folders:   {counts.get('photo_folders', 0)}")
            msg.append(f"  • photo_metadata:  {counts.get('photo_metadata', 0)}")
            msg.append(f"  • projects:        {counts.get('projects', 0)}")
            msg.append(f"  • branches:        {counts.get('branches', 0)}")
            msg.append(f"  • project_images:  {counts.get('project_images', 0)}")
            if errors:
                msg.append("")
                msg.append("Warnings/Errors:")
                for e in errors:
                    msg.append(f"  - {e}")
            QMessageBox.information(self, "DB Self-Check", "\n".join(msg))
        except Exception as e:
            QMessageBox.critical(self, "Self-Check Failed", str(e))


    def _db_rebuild_date_index(self):
        """
        Optional hook: if you implemented a date index builder (e.g., build_date_index_by_day),
        call it here. Otherwise, show a friendly message.
        """
        try:
            db = ReferenceDB()
            # If you already added: db.build_date_index_by_day()
            if hasattr(db, "build_date_index_by_day"):
                n = db.build_date_index_by_day()  # hypothetical return: rows added/updated
                QMessageBox.information(self, "Date Index", f"Date index rebuilt.\nUpdated rows: {n}")
            else:
                QMessageBox.information(
                    self, "Date Index",
                    "Date index not implemented yet in ReferenceDB.\n"
                    "You can safely ignore this or wire it later."
                )
        except Exception as e:
            QMessageBox.critical(self, "Date Index Failed", str(e))


    def _exif_to_dict(self, path: str) -> dict:
        """Read EXIF using PIL. Return a flat dict of tag_name -> value (best-effort)."""
        try:
            from PIL import Image, ExifTags
            with Image.open(path) as img:
                raw = img.getexif() or {}
                tagmap = {ExifTags.TAGS.get(k, k): raw.get(k) for k in raw.keys()}
                # Flatten MakerNote garbage
                if "MakerNote" in tagmap:
                    tagmap.pop("MakerNote", None)
                return tagmap
        except Exception:
            return {}

    def _fmt_rational(self, v):
        """Handle PIL rational/tuples (e.g., (1, 125)) → float or friendly str."""
        try:
            # fractions
            if hasattr(v, "numerator") and hasattr(v, "denominator"):
                return float(v.numerator) / float(v.denominator) if v.denominator else 0.0
            # (num, den) tuple
            if isinstance(v, (tuple, list)) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
                return float(v[0]) / float(v[1]) if v[1] else 0.0
            return v
        except Exception:
            return v

    def _parse_exif_exposure(self, exif: dict) -> dict:
        """Return prettified ISO / shutter / aperture / focal."""
        out = {}
        # ISO
        iso = exif.get("ISOSpeedRatings") or exif.get("PhotographicSensitivity")
        if isinstance(iso, (list, tuple)):
            iso = iso[0] if iso else None
        if iso:
            out["ISO"] = f"{iso}"
        # Shutter
        shutter = exif.get("ExposureTime")
        if shutter:
            r = self._fmt_rational(shutter)
            if isinstance(r, (int, float)) and r > 0:
                # pretty as 1/xxx for small values
                out["Shutter"] = f"1/{int(round(1 / r))}" if r < 1 else f"{r:.2f}s"
            else:
                out["Shutter"] = f"{shutter}"
        # Aperture
        fnum = exif.get("FNumber")
        if fnum:
            f = self._fmt_rational(fnum)
            out["Aperture"] = f"f/{f:.1f}" if isinstance(f, (int, float)) else f"f/{f}"
        # Focal length
        fl = exif.get("FocalLength")
        if fl:
            f = self._fmt_rational(fl)
            out["Focal Length"] = f"{f:.0f} mm" if isinstance(f, (int, float)) else f"{f} mm"
        return out

    def _gps_to_degrees(self, gps):
        """Convert GPS IFD to (lat, lon) in decimal degrees if possible."""
        try:
            def _conv(ref, vals):
                def _rat(x):
                    if hasattr(x, "numerator") and hasattr(x, "denominator"):
                        return float(x.numerator) / float(x.denominator) if x.denominator else 0.0
                    if isinstance(x, (tuple, list)) and len(x) == 2:
                        return float(x[0]) / float(x[1]) if x[1] else 0.0
                    return float(x)
                d, m, s = (_rat(vals[0]), _rat(vals[1]), _rat(vals[2]))
                deg = d + m/60.0 + s/3600.0
                if ref in ("S", "W"):
                    deg = -deg
                return deg

            lat = lon = None
            gps_lat = gps.get("GPSLatitude")
            gps_lat_ref = gps.get("GPSLatitudeRef")
            gps_lon = gps.get("GPSLongitude")
            gps_lon_ref = gps.get("GPSLongitudeRef")
            if gps_lat and gps_lat_ref and gps_lon and gps_lon_ref:
                lat = _conv(gps_lat_ref, gps_lat)
                lon = _conv(gps_lon_ref, gps_lon)
            return (lat, lon)
        except Exception:
            return (None, None)

    def _build_meta_table(self, rows: list[tuple[str, str]]) -> str:
        safe = []
        for k, v in rows:
            val = "-" if v is None or v == "" else str(v)
            safe.append(f"<tr><td><b>{k}</b></td><td>{val}</td></tr>")
        return "<table cellspacing='2' cellpadding='2'>" + "".join(safe) + "</table>"


    def _apply_light_mode(self):
        """Revert to the default (light) palette."""
        app = QApplication.instance()
        app.setPalette(QApplication.style().standardPalette())


    def _on_toggle_backfill_panel(self, visible: bool):
        """
        Handler for the toolbar toggle action. Ensures the BackfillStatusPanel exists,
        inserts it into the main layout if necessary, toggles visibility and persists the choice.
        """
        try:
            # Ensure we have a BackfillStatusPanel instance (lazy-create if necessary)
            if not hasattr(self, "backfill_panel") or self.backfill_panel is None:
                try:
                    # Try to create and insert at the top of the central layout
                    self.backfill_panel = BackfillStatusPanel(self)
                    # Insert at top if possible
                    try:
                        container = self.centralWidget()
                        if container is not None:
                            layout = container.layout()
                            if layout is not None:
                                layout.insertWidget(0, self.backfill_panel)
                    except Exception:
                        # If insertion fails, fallback to adding to layout end (best-effort)
                        try:
                            main_layout = getattr(self, "main_layout", None)
                            if main_layout is not None and hasattr(main_layout, "addWidget"):
                                main_layout.addWidget(self.backfill_panel)
                        except Exception:
                            pass
                except Exception as e:
                    print(f"[MainWindow] Failed to create BackfillStatusPanel dynamically: {e}")
                    return

            # Apply requested visibility
            try:
                self.backfill_panel.setVisible(bool(visible))
            except Exception as e:
                print(f"[MainWindow] Could not set BackfillStatusPanel visibility: {e}")

            # Persist the choice
            try:
                self.settings.set("show_backfill_panel", bool(visible))
            except Exception:
                pass

        except Exception as e:
            print(f"[MainWindow] _on_toggle_backfill_panel error: {e}")

    def _on_project_changed(self, idx: int):
        self.project_controller.on_project_changed(idx)
        
    def _on_folder_selected(self, folder_id: int):
        # DELEGATED to SidebarController (legacy stub kept for compatibility)
        self.sidebar_controller.on_folder_selected(folder_id)


    def _on_branch_selected(self, branch_key: str):
        # DELEGATED to SidebarController (legacy stub kept for compatibility)
        self.sidebar_controller.on_branch_selected(branch_key)


    def _on_scan_repository(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Photo Repository")
        if not folder:
            return
        incremental = self.chk_incremental.isChecked()
        self.scan_controller.start_scan(folder, incremental)

    # 👇 Optional: keep cancel button behavior simple
    def _on_cancel_scan_clicked(self):
        self.scan_controller.cancel()

    def _apply_sort_filter(self):
        """
        Apply sorting/filtering to the grid based on toolbar combo boxes.
        """
        sort_field = self.sort_combo.currentText().lower()
        descending = self.sort_order_combo.currentText() == "Descending"
        self.grid.apply_sorting(sort_field, descending)

    def _open_lightbox_from_selection(self):
        """Open the last selected image in lightbox."""
        paths = self.grid.get_selected_paths()
        print(f"[MAIN_open_lightbox_from_selection] paths: {paths}")
        if paths:
            self._open_lightbox(paths[-1])

    def _request_delete_from_selection(self):
        """Trigger delete for currently selected images."""
        paths = self.grid.get_selected_paths()
        if paths:
            self._confirm_delete(paths)

    def _confirm_delete(self, paths: list[str]):
        """Simple delete confirmation dialog (you can plug real logic later)."""
        if not paths:
            return
        ret = QMessageBox.question(
            self,
            "Delete",
            f"Delete {len(paths)} photo(s)?",
            QMessageBox.Yes | QMessageBox.No
        )
        if ret == QMessageBox.Yes:
            # TODO: your real delete logic here
            print("🗑 Deleting:", paths)
            self.grid.reload()

    def _open_lightbox(self, path: str):
        """
        Open the LightboxDialog for the clicked path, and pass the full list of
        paths based on the current navigation context (folder / branch / date).
        """
        if not path:
            return

        from reference_db import ReferenceDB
        db = ReferenceDB()

        # 1) Build the "context paths" in this priority:
        #    folder -> branch -> date -> visible paths -> just [path]
        paths = []
        context = "unknown"

        # Folder context
        folder_id = getattr(self.grid, "current_folder_id", None)
        print(f"[open_lightbox] self.grid={self.grid}")
        print(f"[open_lightbox] folder_id={folder_id}")
        if folder_id is not None:
            try:
                paths = db.get_images_by_folder(folder_id)
                context = f"folder({folder_id})"
                
            except Exception as e:
                print(f"[open_lightbox] folder fetch failed: {e}")
        
        
        # Branch context (if no folder result)
        print(f"[open_lightbox] paths={paths}")
        branch_key = getattr(self.grid, "branch_key", None)
        if not paths and branch_key:
            try:
                paths = db.get_images_by_branch(self.grid.project_id, branch_key)
                context = f"branch({branch_key})"
            except Exception as e:
                print(f"[open_lightbox] branch fetch failed: {e}")
        print(f"[open_lightbox] branch_key={branch_key}")
        
        # Date context (if no folder/branch result)
        date_key = getattr(self.grid, "date_key", None)
        if not paths and date_key:           
            try:
                if len(date_key) == 4 and date_key.isdigit():
                    paths = db.get_images_by_year(int(date_key))
                    context = f"year({date_key})"
                elif len(date_key) == 7 and date_key[4] == "-" and date_key[5:7].isdigit():
                    # format "YYYY-MM"
                    paths = db.get_images_by_month_str(date_key)
                    context = f"month({date_key})"
                elif len(date_key) == 10 and date_key[4] == "-" and date_key[7] == "-":
                    # format "YYYY-MM-DD"
                    paths = db.get_images_by_date(date_key)
                    context = f"day({date_key})"
            except Exception as e:
                print(f"[open_lightbox] date fetch failed: {e}")
        print(f"[open_lightbox] date_key={date_key}")
        print(f"[open_lightbox] paths={paths}")
        
        # --- Fallback 1: what's visible on the grid right now?
        if not paths and hasattr(self.grid, "get_visible_paths"):
            try:
                paths = self.grid.get_visible_paths()
                context = "visible_paths"
            except Exception as e:
                print(f"[open_lightbox] get_visible_paths() failed: {e}")

        # --- Fallback 2: internal loaded model (even if view isn't built yet)
        if not paths and hasattr(self.grid, "get_all_paths"):
            try:
                paths = self.grid.get_all_paths()
                context = "all_paths"
            except Exception as e:
                print(f"[open_lightbox] get_all_paths() failed: {e}")

        # --- Final fallback: this single image only
        if not paths:
            paths = [path]
            context = "single"

        # Locate index of the clicked photo in `paths`
        try:
            idx = paths.index(path)
        except ValueError:
            # Different path normalization? try os.path.normcase/normpath
            try:
                norm = lambda p: os.path.normcase(os.path.normpath(p))
                idx = [norm(p) for p in paths].index(norm(path))
            except Exception:
                idx = 0

        print(f"[open_lightbox] context={context}, total={len(paths)}")
        print(f"[open_lightbox] paths={paths}")
        print(f"[open_lightbox] path={path}")

        # 3) Launch dialog
        dlg = LightboxDialog(path, self)
        dlg.set_image_list(paths, idx)   # <-- THIS ENABLES next/prev
        dlg.resize(900, 700)
        dlg.exec()


    def resizeEvent(self, event):
        """
        Clamp the main window to the primary screen available geometry so that layout
        changes (triggered by sidebar clicks or widget reflows) won't push parts of the
        window off-screen (bottom clipped).
        """
        try:
            super().resizeEvent(event)
        except Exception:
            pass

        try:
            avail = QGuiApplication.primaryScreen().availableGeometry()
            geo = self.geometry()
            tx = geo.x()
            ty = geo.y()
            tw = geo.width()
            th = geo.height()

            # Ensure window does not extend below the available bottom
            if geo.bottom() > avail.bottom():
                ty = max(avail.top(), avail.bottom() - th)
            # Ensure window does not extend above top
            if ty < avail.top():
                ty = avail.top()
            # Ensure window does not extend right of available area
            if geo.right() > avail.right():
                tx = max(avail.left(), avail.right() - tw)
            if tx < avail.left():
                tx = avail.left()

            # move if changed
            if (tx, ty) != (geo.x(), geo.y()):
                try:
                    self.move(tx, ty)
                except Exception:
                    pass
        except Exception:
            pass


    def closeEvent(self, event):
        """Ensure thumbnail threads and caches are closed before app exit."""
        try:
            if hasattr(self, "grid") and hasattr(self.grid, "shutdown_threads"):
                self.grid.shutdown_threads()
                print("[Shutdown] Grid threads shut down.")
        except Exception as e:
            print(f"[Shutdown] Grid thread error: {e}")

        try:
            if hasattr(self, "thumbnails") and hasattr(self.thumbnails, "shutdown_threads"):
                self.thumbnails.shutdown_threads()
                print("[Shutdown] ThumbnailManager threads shut down.")
        except Exception as e:
            print(f"[Shutdown] ThumbnailManager shutdown error: {e}")

        try:
            if hasattr(self, "thumb_cache"):
                self.thumb_cache.clear()
                print("[Shutdown] Thumbnail cache cleared.")
        except Exception as e:
            print(f"[Shutdown] Thumb cache clear error: {e}")

        super().closeEvent(event)


    def _init_progress_pollers(self):
        self.cluster_timer = QTimer(self)
        self.cluster_timer.timeout.connect(self._poll_cluster_status)
        self.cluster_timer.start(2000)  # every 2 seconds

        self.backfill_timer = QTimer(self)
        self.backfill_timer.timeout.connect(self._poll_backfill_status)
        self.backfill_timer.start(2000)

    def _poll_cluster_status(self):
        path = os.path.join(self.app_root, "status", "cluster_status.json")
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            phase = data.get("phase")
            pct = data.get("percent", 0)
            
#            self.status_bar.showMessage(f"👥 Clustering {pct:.1f}% ({phase})")
            self.statusBar().showMessage(f"👥 Clustering {pct:.1f}% ({phase})")

            if phase == "done":
                self.status_bar.showMessage("✅ Clustering complete")
                os.remove(path)
        except Exception as e:
            print(f"[Status] cluster poll failed: {e}")

    def _poll_backfill_status(self):
        path = os.path.join(self.app_root, "status", "backfill_status.json")
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            pct = data.get("percent", 0)
            
#            self.status_bar.showMessage(f"📸 Backfill {pct:.1f}%")
            phase = data.get("phase", "")
            self.statusBar().showMessage(f"📸 Backfill {pct:.1f}% ({phase})")
#            self.statusBar().showMessage(f"👥 Clustering {pct:.1f}% ({phase})")

            if data.get("phase") == "done":
#                self.status_bar.showMessage("✅ Metadata backfill complete")
                self.statusBar().showMessage("✅ Metadata backfill complete")                
                os.remove(path)
        except Exception as e:
            print(f"[Status] backfill poll failed: {e}")
