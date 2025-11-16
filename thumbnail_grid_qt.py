# thumbnail_grid_qt.py
# Version 09.18.01.13 dated 20251101
#
# Updated: optimized thumbnail worker integration, reuse of thread pool,
# shared placeholders, pass shared cache into workers, respect thumbnail_workers setting,
# and safer worker token handling to avoid stale emissions.
#
# Previous behavior preserved; changes are focused on performance and memory.


import io
import os
import re
import sys
import time
import uuid
import hashlib
import collections
from typing import Optional

# === Global Decoder Warning Policy ===
from settings_manager_qt import SettingsManager
from thumb_cache_db import get_cache
from services import get_thumbnail_service

# create module-level settings instance (used in __init__ safely)
settings = SettingsManager()
if not settings.get("show_decoder_warnings", False):
    # Silence Qt and Pillow warnings
    os.environ["QT_LOGGING_RULES"] = "qt.gui.imageio.warning=false"
    import warnings
    warnings.filterwarnings("ignore", category=UserWarning)
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    print("🔇 Decoder warnings suppressed per settings.")
else:
    print("⚠️ Decoder warnings enabled (developer mode).")




from PySide6.QtWidgets import (
    QWidget, QListView,
    QVBoxLayout, QMessageBox,
    QHBoxLayout, QSlider,
    QPushButton, QStyledItemDelegate,
    QStyle, QMenu, QAbstractItemView, QStyleOptionViewItem
)

from PySide6.QtCore import (
    Qt,
    QRect,
    QSize,
    QThreadPool,
    QRunnable,
    Signal,
    QObject,
    QEvent, QPropertyAnimation,
    QEasingCurve,
    QPoint, QModelIndex, QTimer, QItemSelectionModel, QMimeData
)

 

from PySide6.QtGui import (
    QStandardItemModel, 
    QStandardItem,
    QPixmap,
    QImage,
    QPainter,
    QPen,
    QBrush,
    QColor,
    QFont,
    QAction,
    QCursor,
    QIcon, QImageReader
) 

    
from reference_db import ReferenceDB
from app_services import (
    get_project_images,
    get_thumbnail
)
from services.tag_service import get_tag_service

from PIL import Image



def make_placeholder_pixmap(size=QSize(160, 160), text="😊"):
    """
    Create a transparent placeholder so thumbnails with different aspect
    ratios won't show large opaque blocks. Draw a soft rounded rect and center the icon.
    Ensures QPainter is properly ended to avoid leaving paint device active.
    """
    pm = QPixmap(size)
    pm.fill(Qt.transparent)
    p = QPainter()
    try:
        p.begin(pm)
        # use Antialiasing + TextAntialiasing + SmoothPixmapTransform for high-quality output
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        rect = pm.rect().adjusted(4, 4, -4, -4)
        bg = QColor("#F3F4F6")
        border = QColor("#E0E0E0")
        p.setBrush(bg)
        p.setPen(border)
        p.drawRoundedRect(rect, 10, 10)

        font = QFont()
        font.setPointSize(int(max(10, size.height() * 0.28)))
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor("#9AA0A6"))
        p.drawText(pm.rect(), Qt.AlignCenter, text)
    finally:
        try:
            p.end()
        except Exception:
            pass
    return pm

def _pil_to_qimage(pil_img):
    if pil_img.mode != "RGB":
        pil_img = pil_img.convert("RGB")
    data = pil_img.tobytes("raw", "RGB")
    qimg = QImage(data, pil_img.width, pil_img.height, QImage.Format_RGB888)
    return qimg

# === Enhanced safe thumbnail loader ===


def load_thumbnail_safe(path: str, height: int, cache: dict, timeout: float, placeholder: QPixmap):
    """
    Safe loader with ThumbnailService.

    NOTE: The 'cache' parameter is kept for backward compatibility but is now unused
    as ThumbnailService manages its own L1+L2 caching internally.

    Args:
        path: Image file path
        height: Target thumbnail height
        cache: Legacy parameter (unused, kept for compatibility)
        timeout: Decode timeout in seconds
        placeholder: Fallback pixmap on error

    Returns:
        QPixmap thumbnail
    """
    try:
        # Use ThumbnailService which handles all caching internally
        thumb_service = get_thumbnail_service()
        pm = thumb_service.get_thumbnail(path, height, timeout=timeout)

        if pm and not pm.isNull():
            return pm

        return placeholder

    except Exception as e:
        print(f"[ThumbnailSafe] Failed to load {path}: {e}")
        return placeholder

# --- Worker signal bridge ---
def get_thumbnail_safe(path, height, use_disk_cache=True):
    pm = get_thumbnail(path, height, use_disk_cache=True)
    if pm and not pm.isNull():
        return pm

    # --- fallback for TIFF with unsupported compression ---
    if path.lower().endswith((".tif", ".tiff")):
        try:
            with Image.open(path) as im:
                im.thumbnail((height * 2, height), Image.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, format="PNG")
                qimg = QImage.fromData(buf.getvalue())
                return QPixmap.fromImage(qimg)
        except Exception as e:
            print(f"[TIFF fallback] Could not read {path}: {e}")

    return pm

# --- Worker signal bridge ---
class ThumbSignal(QObject):
    preview = Signal(str, QPixmap, int)  # quick low-res
    loaded = Signal(str, QPixmap, int)  # path, pixmap, row index


# --- Worker for background thumbnail loading ---

class ThumbWorker(QRunnable):
    def __init__(self, real_path, norm_path, height, row, signal_obj, cache, reload_token, placeholder):
        super().__init__()
        # real_path = on-disk path to open; norm_path = unified key used in model/cache
        self.real_path = str(real_path)
        self.norm_path = str(norm_path)
        self.height = int(height)
        self.row = int(row)
        self.signals = signal_obj
        self.cache = cache
        self.reload_token = reload_token
        self.placeholder = placeholder

    def run(self):
        try:
            quick_h = max(64, min(128, max(32, self.height // 2)))
            pm_preview = None
            try:
                # Try QImageReader fast scaled read first
                try:
#                    reader = QImageReader(self.path)
                    reader = QImageReader(self.real_path)
                    reader.setAutoTransform(True)
                    reader.setScaledSize(QSize(quick_h, quick_h))
                    img = reader.read()
                    if img is not None and not img.isNull():
                        pm_preview = QPixmap.fromImage(img)
                except Exception:
                    pm_preview = None
                if pm_preview is None:
#                    pm_preview = load_thumbnail_safe(self.path, quick_h, self.cache, timeout=2.0, placeholder=self.placeholder)
                    pm_preview = load_thumbnail_safe(self.real_path, quick_h, self.cache, timeout=2.0, placeholder=self.placeholder)
            except Exception as e:
#                print(f"[ThumbWorker] preview failed {self.path}: {e}")
                print(f"[ThumbWorker] preview failed {self.real_path}: {e}")
                pm_preview = self.placeholder

            try:
#                self.signals.preview.emit(self.path, pm_preview, self.row)
                # emit with normalized key so the grid can always match the item
                self.signals.preview.emit(self.norm_path, pm_preview, self.row)
            except Exception:
                return

            # full
            try:
#                pm_full = get_thumbnail(self.path, self.height, use_disk_cache=True)
                pm_full = get_thumbnail(self.real_path, self.height, use_disk_cache=True)
                if pm_full is None or pm_full.isNull():
#                    pm_full = load_thumbnail_safe(self.path, self.height, self.cache, timeout=5.0, placeholder=self.placeholder)
                    pm_full = load_thumbnail_safe(self.real_path, self.height, self.cache, timeout=5.0, placeholder=self.placeholder)
            except Exception:
                pm_full = self.placeholder

            try:
#                self.signals.loaded.emit(self.path, pm_full, self.row)
                self.signals.loaded.emit(self.norm_path, pm_full, self.row)
            except Exception:
                return

        except Exception as e:
            print(f"[ThumbWorker] Error for {self.path}: {e}")


def is_video_file(path: str) -> bool:
    """Check if file is a video based on extension."""
    if not path:
        return False
    ext = os.path.splitext(path)[1].lower()
    video_exts = {'.mp4', '.m4v', '.mov', '.mpeg', '.mpg', '.mpe', '.wmv',
                  '.asf', '.avi', '.mkv', '.webm', '.flv', '.f4v', '.3gp',
                  '.3g2', '.ogv', '.ts', '.mts', '.m2ts'}
    return ext in video_exts


def format_duration(seconds: float) -> str:
    """Format duration in seconds to MM:SS or H:MM:SS format."""
    if seconds is None or seconds < 0:
        return "0:00"

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"


class CenteredThumbnailDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)



    def paint(self, painter: QPainter, option, index):
        # ✅ Get icon/pixmap data properly first
        icon_data = index.data(Qt.DecorationRole)
        rect = option.rect

        # ✅ Guard against invalid or zero rect sizes (e.g., before layout settles)
        cell_h = rect.height()
        if cell_h <= 6:
            QStyledItemDelegate.paint(self, painter, option, index)
            return

        target_h = cell_h - 6

        # 🟡 Selection border
        if option.state & QStyle.State_Selected:
            painter.save()
            pen = QPen(QColor(30, 144, 255))
            pen.setWidth(3)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 6, 6)
            painter.restore()

        # 🖼 Draw scaled thumbnail with fixed height and aspect ratio
        pm = None
        if isinstance(icon_data, QIcon):
            pm = icon_data.pixmap(QSize(int(target_h * 2), target_h))
        elif isinstance(icon_data, QPixmap):
            pm = icon_data

        # === Tag overlay ===
        tags = index.data(Qt.UserRole + 2) or []
        if tags:
            painter.save()
            overlay_rect = QRect(rect.right() - 26, rect.top() + 4, 22, 22)
            if "favorite" in tags:
                painter.fillRect(rect, QColor(0, 0, 0, 30))  # subtle dark overlay
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(255, 215, 0, 220))
                painter.drawEllipse(overlay_rect)
                painter.setPen(QPen(Qt.black))
                painter.drawText(overlay_rect, Qt.AlignCenter, "★")
            elif "face" in tags:
                painter.setBrush(QColor(70, 130, 180, 200))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(overlay_rect)
                painter.setPen(QPen(Qt.white))
                painter.drawText(overlay_rect, Qt.AlignCenter, "👤")
            painter.restore()


        if pm and not pm.isNull():
            orig_w = pm.width()
            orig_h = pm.height()
            if orig_h > 0:
                scale = target_h / orig_h
                target_w = int(orig_w * scale)
                scaled = pm.scaled(target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                x = rect.x() + (rect.width() - scaled.width()) // 2
                y = rect.y() + (rect.height() - scaled.height()) // 2
                painter.drawPixmap(QRect(x, y, scaled.width(), scaled.height()), scaled)

                # 🎬 Video duration badge (Phase 4.3)
                file_path = index.data(Qt.UserRole)
                if file_path and is_video_file(file_path):
                    # Get duration from video metadata (stored in UserRole + 3)
                    duration_seconds = index.data(Qt.UserRole + 3)

                    # Draw semi-transparent background for badge
                    painter.save()
                    badge_width = 50
                    badge_height = 20
                    badge_rect = QRect(
                        x + scaled.width() - badge_width - 4,
                        y + scaled.height() - badge_height - 4,
                        badge_width,
                        badge_height
                    )

                    # Draw rounded background
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor(0, 0, 0, 180))
                    painter.drawRoundedRect(badge_rect, 3, 3)

                    # Draw duration text or play icon
                    painter.setPen(QPen(Qt.white))
                    font = QFont()
                    font.setPointSize(9)
                    font.setBold(True)
                    painter.setFont(font)

                    if duration_seconds and duration_seconds > 0:
                        duration_text = format_duration(duration_seconds)
                    else:
                        duration_text = "🎬"  # Show play icon if no duration

                    painter.drawText(badge_rect, Qt.AlignCenter, duration_text)
                    painter.restore()

                    # 🎯 Phase 3: Status indicators for video processing (top-left corner)
                    metadata_status = index.data(Qt.UserRole + 7)
                    thumbnail_status = index.data(Qt.UserRole + 8)

                    # Only show indicators if status is not 'ok' (show pending/error states)
                    if metadata_status and metadata_status != 'ok':
                        painter.save()
                        # Metadata status indicator (left side)
                        status_size = 18
                        status_x = x + 4
                        status_y = y + 4

                        # Color and icon based on status
                        if metadata_status == 'pending':
                            status_color = QColor(255, 165, 0, 200)  # Orange
                            status_icon = "⏳"
                        elif metadata_status == 'error':
                            status_color = QColor(255, 0, 0, 200)  # Red
                            status_icon = "❌"
                        else:  # unknown
                            status_color = QColor(128, 128, 128, 200)  # Gray
                            status_icon = "❓"

                        # Draw background circle
                        painter.setPen(Qt.NoPen)
                        painter.setBrush(status_color)
                        painter.drawEllipse(status_x, status_y, status_size, status_size)

                        # Draw icon
                        painter.setPen(QPen(Qt.white))
                        icon_font = QFont()
                        icon_font.setPointSize(10)
                        painter.setFont(icon_font)
                        painter.drawText(QRect(status_x, status_y, status_size, status_size),
                                       Qt.AlignCenter, status_icon)
                        painter.restore()

                    if thumbnail_status and thumbnail_status != 'ok':
                        painter.save()
                        # Thumbnail status indicator (right side of metadata indicator)
                        status_size = 18
                        status_x = x + 4 + 20  # Offset from metadata indicator
                        status_y = y + 4

                        # Color and icon based on status
                        if thumbnail_status == 'pending':
                            status_color = QColor(255, 165, 0, 200)  # Orange
                            status_icon = "🖼"
                        elif thumbnail_status == 'error':
                            status_color = QColor(255, 0, 0, 200)  # Red
                            status_icon = "🚫"
                        else:  # unknown
                            status_color = QColor(128, 128, 128, 200)  # Gray
                            status_icon = "❓"

                        # Draw background circle
                        painter.setPen(Qt.NoPen)
                        painter.setBrush(status_color)
                        painter.drawEllipse(status_x, status_y, status_size, status_size)

                        # Draw icon
                        painter.setPen(QPen(Qt.white))
                        icon_font = QFont()
                        icon_font.setPointSize(10)
                        painter.setFont(icon_font)
                        painter.drawText(QRect(status_x, status_y, status_size, status_size),
                                       Qt.AlignCenter, status_icon)
                        painter.restore()


        # 🟢 Focus glow
        if option.state & QStyle.State_HasFocus:
            painter.save()
            pen = QPen(QColor(30, 144, 255, 160))
            pen.setWidth(4)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            focus_rect = rect.adjusted(2, 2, -2, -2)
            painter.drawRoundedRect(focus_rect, 6, 6)
            painter.restore()
 

# === Phase 3: Drag & Drop Support ===
class DraggableThumbnailModel(QStandardItemModel):
    """
    Custom model that provides photo paths as MIME data for drag and drop.
    Enables dragging photos from the grid to sidebar folders/tags.
    """
    def mimeTypes(self):
        """Return list of MIME types this model supports for drag operations."""
        return ['text/uri-list', 'application/x-photo-paths']

    def mimeData(self, indexes):
        """
        Create MIME data from selected items.
        Extracts photo paths from Qt.UserRole and provides them in two formats:
        - text/uri-list: Standard file URIs for external apps
        - application/x-photo-paths: Custom format with newline-separated paths
        """
        mime_data = QMimeData()

        # Get unique photo paths from selected indexes
        paths = []
        for index in indexes:
            if index.isValid():
                path = index.data(Qt.UserRole)
                if path and path not in paths:
                    paths.append(path)

        if not paths:
            return mime_data

        # Format 1: text/uri-list (standard file URLs)
        from PySide6.QtCore import QUrl
        urls = [QUrl.fromLocalFile(str(p)) for p in paths]
        mime_data.setUrls(urls)

        # Format 2: application/x-photo-paths (custom format with paths separated by newlines)
        paths_text = '\n'.join(str(p) for p in paths)
        mime_data.setData('application/x-photo-paths', paths_text.encode('utf-8'))

        print(f"[DragDrop] Created MIME data for {len(paths)} photo(s)")
        return mime_data


class ThumbnailGridQt(QWidget):
    # inside class ThumbnailGridQt(QWidget):
    selectionChanged = Signal(int)# count of selected items
    deleteRequested = Signal(list)# list[str] paths to delete
    openRequested = Signal(str)#path to open (double-click/lightbox)
    gridReloaded = Signal()  # Phase 2.3: emitted after grid data is reloaded
        
    def __init__(self, project_id=None):
        super().__init__()        
        self.settings = settings  # use module-level settings instance
        
        self.db = ReferenceDB()  # new
        self.load_mode = "branch"  # or "folder" or "date"          
        self.project_id = project_id

        self.thumb_height = 160  # 👈 default thumbnail height
        
       # ✅ Unified navigation state
        self.navigation_mode = None        # 'folder', 'date', 'branch'
        self.navigation_key = None         # id or key (folder_id, date_key, etc.) depending on mode
        self.active_tag_filter = None      # current tag string: 'favorite', 'face', etc. or None

        # legacy vars for backward compatibility
        self.load_mode = None
        self.current_folder_id = None
        self.date_key = None               # 'YYYY' or 'YYYY-MM-DD'
        self.branch_key = None
        
        # --- Thumbnail pipeline safety ---
        self._reload_token = uuid.uuid4()
        # NOTE: _thumb_cache kept for backward compatibility but no longer used
        # ThumbnailService manages its own L1+L2 cache internally
        self._thumb_cache = {}        # Deprecated: use ThumbnailService instead
        self._thumbnail_service = get_thumbnail_service()
        self._decode_timeout = 5.0    # seconds for watchdog
        # shared placeholder pixmap (reuse to avoid many allocations)
        self._placeholder_pixmap = make_placeholder_pixmap(QSize(self.thumb_height, self.thumb_height))
        self._current_reload_token = self._reload_token  # initialize for safety


        # --- Thumbnail grid spacing (consistent with zoom system)
        # Use settings-based padding for consistent spacing across all modes
        self.thumb_spacing = self.settings.get("thumb_padding", 8)
        self.cell_width_factor = 1.25
        
        # Use the global thread pool for better reuse across grid instances.
        self.thread_pool = QThreadPool.globalInstance()
        # Respect user setting for worker count
        try:
            workers = int(self.settings.get("thumbnail_workers", 4))
        except Exception:
            workers = 4
        
        # set a reasonable cap
        workers = max(1, min(workers, 8))
        self.thread_pool.setMaxThreadCount(workers)

        self.thumb_signal = ThumbSignal()
        self.thumb_signal.preview.connect(self._on_thumb_loaded)  # show asap
        self.thumb_signal.loaded.connect(self._on_thumb_loaded)   # then refine
        self._paths = []
        
        # prefetch radius (number of items ahead/behind), configurable
        try:
            self._prefetch_radius = int(self.settings.get("thumbnail_prefetch", 8))
        except Exception:
            self._prefetch_radius = 8

        # --- Toolbar (Face Grouping + Zoom controls)
        # Phase 8: Face grouping buttons (moved from People tab for global access)
        self.btn_detect_and_group = QPushButton("⚡ Detect & Group Faces")
        self.btn_detect_and_group.setToolTip("Automatically detect faces and group them into person albums")
        self.btn_detect_and_group.setStyleSheet("QPushButton{padding:5px 12px; font-weight:bold;}")
        # Handler will be connected from main_window_qt.py after grid is created

        self.btn_recluster = QPushButton("🔁 Re-Cluster")
        self.btn_recluster.setToolTip("Re-group detected faces (without re-detecting)")
        self.btn_recluster.setStyleSheet("QPushButton{padding:5px 12px;}")
        # Handler will be connected from main_window_qt.py after grid is created

        # Zoom controls
        self.zoom_out_btn = QPushButton("-")
        self.zoom_out_btn.setFixedWidth(30)
        self.zoom_out_btn.clicked.connect(self.zoom_out)

        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setFixedWidth(30)
        self.zoom_in_btn.clicked.connect(self.zoom_in)

        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(0, 100)  # min and max height
        self.zoom_slider.setValue(50)

        self.zoom_slider.sliderPressed.connect(self._on_slider_pressed)
        self.zoom_slider.sliderReleased.connect(self._on_slider_released)
        self.zoom_slider.valueChanged.connect(self._on_slider_value_changed)

        # --- List view ---
        self.list_view = QListView()
        self.list_view.setViewMode(QListView.IconMode)
        self.list_view.setResizeMode(QListView.Adjust)
        self.list_view.setMovement(QListView.Static)
        self.list_view.setSelectionMode(QListView.ExtendedSelection)
        self.list_view.setWrapping(True)        
        self.list_view.setSpacing(self.thumb_spacing)
        self.list_view.setUniformItemSizes(False)

        # ✅ Enable touch gestures after list_view is created
        self.list_view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list_view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        
        self.list_view.viewport().setAttribute(Qt.WA_AcceptTouchEvents, True)
        self.grabGesture(Qt.PinchGesture)

        # === Phase 3: Drag & Drop Support ===
        self.list_view.setDragEnabled(True)
        self.list_view.setDragDropMode(QAbstractItemView.DragOnly)
        self.list_view.setDefaultDropAction(Qt.CopyAction)

        # Delegates
        self.delegate = CenteredThumbnailDelegate(self.list_view)
        self.list_view.setItemDelegate(self.delegate)

        # Phase 3: Use draggable model for drag & drop support
        self.model = DraggableThumbnailModel(self.list_view)
        self.list_view.setModel(self.model)

        # --- Context menu ---
        self.list_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_view.customContextMenuRequested.connect(self._on_context_menu)
        
        # selection behavior & key handling
        self.list_view.setSelectionBehavior(QListView.SelectItems)
        self.list_view.setSelectionRectVisible(True)
        self.list_view.installEventFilter(self)  # capture keyboard in the view

        # notify selection count
        self.list_view.selectionModel().selectionChanged.connect(
            lambda *_: self.selectionChanged.emit(len(self.get_selected_paths()))
        )

        # double-click = open in lightbox
        self.list_view.doubleClicked.connect(self._on_double_clicked)
        
        # --- 📸 Initialize new zoom system here ---
        self._init_zoom()  # 👈 important!

        # Toolbar
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setSpacing(10)

        # Face grouping buttons (left side)
        toolbar_layout.addWidget(self.btn_detect_and_group)
        toolbar_layout.addWidget(self.btn_recluster)
        toolbar_layout.addSpacing(20)  # Add space between face buttons and zoom controls

        # Zoom controls (right side)
        toolbar_layout.addStretch()  # Push zoom controls to the right
        toolbar_layout.addWidget(self.zoom_out_btn)
        toolbar_layout.addWidget(self.zoom_slider)
        toolbar_layout.addWidget(self.zoom_in_btn)

        # --- Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(toolbar_layout)   # 👈 add toolbar on top
        layout.addWidget(self.list_view)

        # debounce timer for requests
        self._rv_timer = QTimer(self)
        self._rv_timer.setSingleShot(True)
        self._rv_timer.timeout.connect(self.request_visible_thumbnails)
        

        # 🔁 Hook scrollbars AFTER timer exists (debounced incremental scheduling)
        def _on_scroll():
            self._rv_timer.start(50)
        self.list_view.verticalScrollBar().valueChanged.connect(_on_scroll)
        self.list_view.horizontalScrollBar().valueChanged.connect(_on_scroll)
        
# ===================================================
    # --- Normalization helper used everywhere (model key, cache key, worker emits) ---
    def _norm_path(self, p: str) -> str:
        try:
            return os.path.normcase(os.path.abspath(os.path.normpath(str(p).strip())))
        except Exception:
            return str(p).strip().lower()


    def request_visible_thumbnails(self):
        """
        Compute visible rows in the list_view and submit workers only for those,
        plus a small prefetch radius. Prevents scheduling workers for the entire dataset.

        Uses scrollbar position for reliable viewport calculation in IconMode.
        """
        try:
            viewport = self.list_view.viewport()
            rect = viewport.rect()
            if rect.isNull() or self.model.rowCount() == 0:
                # reschedule if viewport not yet fully laid out
                QTimer.singleShot(50, self.request_visible_thumbnails)
                return

            # CRITICAL FIX: Use scrollbar position instead of indexAt() for reliability
            # indexAt() doesn't work well in IconMode when scrolled
            scrollbar = self.list_view.verticalScrollBar()
            scroll_value = scrollbar.value()
            scroll_max = scrollbar.maximum()

            # Calculate approximate start position based on scroll percentage
            if scroll_max > 0:
                scroll_fraction = scroll_value / scroll_max
                approx_start = int(scroll_fraction * self.model.rowCount())
            else:
                approx_start = 0

            # Try indexAt() first, but fall back to scroll-based calculation
            top_index = self.list_view.indexAt(QPoint(rect.left(), rect.top()))
            if top_index.isValid() and top_index.row() > approx_start - 50:
                # indexAt() is working and gives reasonable result
                start = top_index.row()
            else:
                # indexAt() failed or unreliable, use scroll-based estimate
                start = max(0, approx_start - 20)  # Start a bit before scroll position
                print(f"[GRID] Using scroll-based start position: {start} (scroll: {scroll_value}/{scroll_max})")

            # Calculate end position
            bottom_index = self.list_view.indexAt(QPoint(rect.left(), rect.bottom() - 1))

            if bottom_index.isValid() and bottom_index.row() > start:
                end = bottom_index.row()
            else:
                # Calculate based on grid layout
                first_item = self.model.item(max(0, start))
                if first_item:
                    item_width = first_item.sizeHint().width() + self.thumb_spacing
                    item_height = first_item.sizeHint().height() + self.thumb_spacing
                    if item_width > 0 and item_height > 0:
                        items_per_row = max(1, rect.width() // item_width)
                        visible_rows = (rect.height() // item_height) + 2  # +2 for partial rows
                        visible_items = visible_rows * items_per_row
                        end = min(self.model.rowCount() - 1, start + visible_items)
                    else:
                        end = min(self.model.rowCount() - 1, start + 150)
                else:
                    end = min(self.model.rowCount() - 1, start + 150)

            # Expand range by prefetch radius
            start = max(0, start - self._prefetch_radius)
            end = min(self.model.rowCount() - 1, end + self._prefetch_radius)

            # If near bottom, load all remaining
            remaining = self.model.rowCount() - end - 1
            if remaining > 0 and remaining < 100:
                end = self.model.rowCount() - 1
                print(f"[GRID] Near bottom, loading all remaining {remaining} items")

            print(f"[GRID] Loading viewport range: {start}-{end} of {self.model.rowCount()}")

            token = self._reload_token
            loaded_count = 0
            for row in range(start, end + 1):
                item = self.model.item(row)
                if not item:
                    continue

                npath = item.data(Qt.UserRole)        # normalized key
                rpath = item.data(Qt.UserRole + 6)    # real path
                if not npath or not rpath:
                    continue

                # avoid resubmitting while already scheduled
                if item.data(Qt.UserRole + 5):
                    continue

                # schedule worker
                item.setData(True, Qt.UserRole + 5)  # mark scheduled
                thumb_h = int(self._thumb_base * self._zoom_factor)

                w = ThumbWorker(rpath, npath, thumb_h, row, self.thumb_signal,
                                self._thumb_cache, token, self._placeholder_pixmap)

                self.thread_pool.start(w)
                loaded_count += 1

            if loaded_count > 0:
                print(f"[GRID] Queued {loaded_count} new thumbnail workers")

        except Exception as e:
            print(f"[GRID] request_visible_thumbnails error: {e}")
            import traceback
            traceback.print_exc()

    def event(self, ev):
        if ev.type() == QEvent.Gesture:
            gesture = ev.gesture(Qt.PinchGesture)
            if gesture is not None:
                # NOTE: No need to cast — gesture has scaleFactor()
                scale = gesture.scaleFactor()
                self._apply_pinch_zoom(scale)
                return True
        return super().event(ev)

    def _apply_pinch_zoom(self, scale):
        # You can map scale to your zoom slider or directly adjust thumb size
        new_val = max(50, min(400, self.zoom_slider.value() * scale))
        self.zoom_slider.setValue(int(new_val))

    def _normalize_date_key(self, val: str) -> str | None:
        """
        Normalize a 'date' payload to one of:
          • 'YYYY'
          • 'YYYY-MM'  (zero-padded month)
          • 'YYYY-MM-DD'
        Returns None if not a recognized format.
        """
        s = (val or "").strip()

        # Year
        if re.fullmatch(r"\d{4}", s):
            return s

        # Year-Month (allow 1 or 2 digits for month)
        m = re.match(r"^(\d{4})-(\d{1,2})$", s)
        if m:
            y, mo = m.groups()
            try:
                mo_i = int(mo)
                if 1 <= mo_i <= 12:
                    return f"{y}-{mo_i:02d}"
            except Exception:
                pass
            return None

        # Day
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return s

        return None


    def load_custom_paths(self, paths, content_type="auto"):
        """
        Directly load an arbitrary list of image/video paths (used by tag filters and video filters).

        Args:
            paths: List of file paths to display
            content_type: "auto" (detect from paths), "photos", or "videos"
        """
        import os

        # Auto-detect content type if not specified
        if content_type == "auto" and paths:
            # Check first few paths to determine content type
            sample_size = min(10, len(paths))
            video_count = sum(1 for p in paths[:sample_size] if is_video_file(p))
            if video_count > sample_size / 2:
                content_type = "videos"
            else:
                content_type = "photos"

        # Set appropriate mode based on content type
        if content_type == "videos":
            self.load_mode = "videos"
        else:
            self.load_mode = "tag"

        self.branch_key = None
        self.current_folder_id = None
        self.date_key = None
        
        def norm(p):
            return os.path.normcase(os.path.normpath(p.strip()))

        self.model.clear()

        # ✅ normalize paths to match how they're stored in DB
        self._paths = [norm(p) for p in (paths or [])]

        # tag map (for overlays) - only for photos
        tag_map = {}
        if content_type != "videos":
            try:
                tag_map = self.db.get_tags_for_paths(self._paths)
            except Exception as e:
                print(f"[GRID] Warning: Could not fetch tags: {e}")

        # Use current reload token snapshot so workers can be tied to this load
        token = self._reload_token

        for i, p in enumerate(self._paths):
            item = QStandardItem()
            item.setEditable(False)
            item.setData(p, Qt.UserRole)
            item.setData(tag_map.get(p, []), Qt.UserRole + 2)  # 🏷️ store tags for paint()

            # --- Set placeholder size based on default aspect ratio
            aspect_ratio = 1.5
            item.setData(aspect_ratio, Qt.UserRole + 1)
            item.setSizeHint(self._thumb_size_for_aspect(aspect_ratio))

            self.model.appendRow(item)
            thumb_h = int(self._thumb_base * self._zoom_factor)
            # ThumbWorker signature: real_path, norm_path, height, row, signal_obj, cache, reload_token, placeholder
            worker = ThumbWorker(p, p, thumb_h, i, self.thumb_signal, self._thumb_cache, token, self._placeholder_pixmap)

            self.thread_pool.start(worker)

        self._apply_zoom_geometry()
        self.list_view.doItemsLayout()
        self.list_view.viewport().update()

        mode_label = "videos" if content_type == "videos" else "tag"
        print(f"[GRID] Loaded {len(self._paths)} thumbnails in {mode_label}-mode.")


    def shutdown_threads(self):
        """Stop accepting new tasks and wait for current ones to finish."""
        if self.thread_pool:
            # global threadpool has no waitForDone in some contexts; try to be graceful
            try:
                self.thread_pool.waitForDone(2000)  # wait max 2 seconds
            except Exception:
                pass


    def apply_sorting(self, field: str, descending: bool = False):
        """
        Sort current _paths list and rebuild model.
        """
        if not self._paths:
            return
        reverse = descending
        if field == "filename":
            self._paths.sort(key=lambda p: str(p).lower(), reverse=reverse)
        elif field == "date":
            import os
            self._paths.sort(key=lambda p: os.path.getmtime(p), reverse=reverse)
        elif field == "size":
            import os
            self._paths.sort(key=lambda p: os.path.getsize(p), reverse=reverse)

        # rebuild
        self.model.clear()
        token = self._reload_token
        for i, p in enumerate(self._paths):
            item = QStandardItem()
            item.setEditable(False)
            item.setData(p, Qt.UserRole)
            item.setSizeHint(QSize(self.thumb_height, self.thumb_height + self.thumb_spacing))
            self.model.appendRow(item)
            worker = ThumbWorker(p, self.thumb_height, i, self.thumb_signal, self._thumb_cache, token, self._placeholder_pixmap)
            self.thread_pool.start(worker)


    def _on_thumb_loaded(self, path: str, pixmap: QPixmap, row: int):
        """Called asynchronously when a thumbnail has been loaded."""
        # --- Token safety check ---
        if getattr(self, "_current_reload_token", None) != self._reload_token:
            print(f"[GRID] Discarded stale thumbnail: {path}")
            return

#        item = None
#        try:
#            item = self.model.item(row)
#        except Exception:
#            item = None
#
#        if not item or item.data(Qt.UserRole) != path:
#            # The model may have changed (re-ordered); attempt to find by path instead
#            found = None
#            for r in range(self.model.rowCount()):
#                it = self.model.item(r)
#                if it and it.data(Qt.UserRole) == path:
#                    found = it
#                    break
#            item = found


        # path here is ALWAYS normalized; match by key
        item = self.model.item(row) if (0 <= row < self.model.rowCount()) else None
        if (not item) or (item.data(Qt.UserRole) != path):
            item = None
            for r in range(self.model.rowCount()):
                it = self.model.item(r)
                if it and it.data(Qt.UserRole) == path:
                    item = it
                    row = r
                    break
        if not item:
            return

        # 🧠 Use cached pixmap if invalid
        if pixmap is None or pixmap.isNull():
#            pm = load_thumbnail_safe(
#                path, int(self._thumb_base * self._zoom_factor),
#                self._thumb_cache, self._decode_timeout, self._placeholder_pixmap
#            )

            real_path = item.data(Qt.UserRole + 6) or path
            pm = load_thumbnail_safe(real_path,
                                     int(self._thumb_base * self._zoom_factor),
                                     self._thumb_cache, self._decode_timeout, self._placeholder_pixmap)

        else:
            pm = pixmap

        # 🧮 Update metadata and UI
        aspect_ratio = pm.width() / pm.height() if pm and pm.height() > 0 else 1.5
        item.setData(aspect_ratio, Qt.UserRole + 1)
        item.setSizeHint(self._thumb_size_for_aspect(aspect_ratio))
        item.setIcon(QIcon(pm))
        
        item.setData(False, Qt.UserRole + 5)   # allow future requeue   
        
        # allow future rescheduling after zoom/scroll
        item.setData(False, Qt.UserRole + 5)

        # NOTE: ThumbnailService handles all cache updates internally
        # No need to manually update memory cache here

        # ✅ Redraw the updated thumbnail cell
        try:
            rect = self.list_view.visualRect(self.model.indexFromItem(item))
            self.list_view.viewport().update(rect)
        except Exception:
            self.list_view.viewport().update()


    def clear(self):
        self.model.clear()
        self._paths.clear()
        self.branch_key = None


    def get_selected_paths(self):
        selection = self.list_view.selectionModel().selectedIndexes()
        return [i.data(Qt.UserRole) for i in selection]


    def _on_context_menu(self, pos: QPoint):
        idx = self.list_view.indexAt(pos)
        paths = self.get_selected_paths()
        if not idx.isValid() and not paths:
            return
        if not paths and idx.isValid():
            paths = [idx.data(Qt.UserRole)]

        db = self.db

        # Build dynamic tag info
        all_tags = []
        try:
            if hasattr(db, "get_all_tags"):
                all_tags = db.get_all_tags()
        except Exception:
            pass

        # tags present across selection (use TagService for consistency)
        present_map = {}
        try:
            from services.tag_service import get_tag_service
            tag_service = get_tag_service()
            present_map = tag_service.get_tags_for_paths(paths, self.project_id)
            print(f"[ContextMenu] Got tags for {len(paths)} path(s): {present_map}")
        except Exception as e:
            print(f"[ContextMenu] Error getting tags: {e}")
            present_map = {}
        present_tags = set()
        for tlist in present_map.values():
            present_tags.update([t.strip() for t in tlist if t.strip()])

        print(f"[ContextMenu] present_tags = {present_tags}")

        # Menu
        m = QMenu(self)
        act_open = m.addAction("Open")
        act_reveal = m.addAction("Reveal in Explorer")
        m.addSeparator()

        # Single unified Tags submenu with toggle behavior
        tag_menu = m.addMenu("🏷️ Tags")

        # Quick presets (favorite and face) - always shown
        act_fav = tag_menu.addAction("⭐ Favorite")
        act_fav.setCheckable(True)
        if "favorite" in present_tags:
            act_fav.setChecked(True)

        act_face = tag_menu.addAction("🧍 Face")
        act_face.setCheckable(True)
        if "face" in present_tags:
            act_face.setChecked(True)

        tag_menu.addSeparator()

        # All existing tags with checkmarks for present ones
        toggle_actions = {}
        for t in sorted(all_tags):
            # Skip favorite and face since they're already in quick presets
            if t.lower() in ["favorite", "face"]:
                continue

            act = tag_menu.addAction(t)
            act.setCheckable(True)
            if t in present_tags:
                act.setChecked(True)
            toggle_actions[act] = t

        tag_menu.addSeparator()
        act_new_tag = tag_menu.addAction("➕ New Tag…")

        # Clear All Tags - top level for visibility
        act_clear_all = m.addAction("❌ Clear All Tags")
        if not present_tags:
            act_clear_all.setEnabled(False)  # Disable if no tags present

        m.addSeparator()
        act_export = m.addAction("Export…")
        act_delete = m.addAction("🗑 Delete")

        chosen = m.exec(self.list_view.viewport().mapToGlobal(pos))
        if not chosen:
            return

        # Actions
        if chosen is act_open:
            self.openRequested.emit(paths[-1])

        elif chosen is act_reveal:
            try:
                import os
                for p in paths[:1]:
                    os.startfile(p)
            except Exception:
                pass

        elif chosen is act_export:
            self.deleteRequested.emit([])

        elif chosen is act_delete:
            self.deleteRequested.emit(paths)

        elif chosen is act_fav:
            # Check if any photos are selected
            if not paths:
                QMessageBox.information(
                    self,
                    "No Photos Selected",
                    "Please select one or more photos before toggling a tag."
                )
                return

            # TOGGLE: Remove if present, add if absent
            tag_service = get_tag_service()
            if "favorite" in present_tags:
                # Remove from all selected photos
                for p in paths:
                    tag_service.remove_tag(p, "favorite", self.project_id)
                print(f"[Tag] Removed 'favorite' → {len(paths)} photo(s)")
            else:
                # Add to all selected photos
                count = tag_service.assign_tags_bulk(paths, "favorite", self.project_id)
                print(f"[Tag] Added 'favorite' → {count} photo(s)")

            # CRITICAL: Wrap post-tag operations in try/except to prevent crashes
            try:
                self._refresh_tags_for_paths(paths)
            except Exception as e:
                print(f"[Tag] Warning: Failed to refresh tag overlays: {e}")

            # 🪄 Refresh sidebar tags
            try:
                mw = self.window()
                if hasattr(mw, "sidebar"):
                    if hasattr(mw.sidebar, "reload_tags_only"):
                        mw.sidebar.reload_tags_only()
                    else:
                        mw.sidebar.reload()
            except Exception as e:
                print(f"[Tag] Warning: Failed to reload sidebar tags: {e}")

            # 🔄 Reload grid if we removed the active tag filter
            if "favorite" in present_tags:
                active_tag = getattr(self, "context", {}).get("tag_filter")
                if active_tag and active_tag.lower() == "favorite":
                    print(f"[Tag] Reloading grid - removed tag matches active filter 'favorite'")
                    try:
                        self.reload()
                    except Exception as e:
                        print(f"[Tag] Warning: Failed to reload grid: {e}")
                        # Clear the tag filter to prevent showing stale data
                        if hasattr(self, "context") and "tag_filter" in self.context:
                            self.context["tag_filter"] = None
                            self.reload()  # Try again without filter

        elif chosen is act_face:
            # Check if any photos are selected
            if not paths:
                QMessageBox.information(
                    self,
                    "No Photos Selected",
                    "Please select one or more photos before toggling a tag."
                )
                return

            # TOGGLE: Remove if present, add if absent
            tag_service = get_tag_service()
            if "face" in present_tags:
                # Remove from all selected photos
                for p in paths:
                    tag_service.remove_tag(p, "face", self.project_id)
                print(f"[Tag] Removed 'face' → {len(paths)} photo(s)")
            else:
                # Add to all selected photos
                count = tag_service.assign_tags_bulk(paths, "face", self.project_id)
                print(f"[Tag] Added 'face' → {count} photo(s)")

            # CRITICAL: Wrap post-tag operations in try/except to prevent crashes
            try:
                self._refresh_tags_for_paths(paths)
            except Exception as e:
                print(f"[Tag] Warning: Failed to refresh tag overlays: {e}")

            # 🪄 Refresh sidebar tags
            try:
                mw = self.window()
                if hasattr(mw, "sidebar"):
                    if hasattr(mw.sidebar, "reload_tags_only"):
                        mw.sidebar.reload_tags_only()
                    else:
                        mw.sidebar.reload()
            except Exception as e:
                print(f"[Tag] Warning: Failed to reload sidebar tags: {e}")

            # 🔄 Reload grid if we removed the active tag filter
            if "face" in present_tags:
                active_tag = getattr(self, "context", {}).get("tag_filter")
                if active_tag and active_tag.lower() == "face":
                    print(f"[Tag] Reloading grid - removed tag matches active filter 'face'")
                    try:
                        self.reload()
                    except Exception as e:
                        print(f"[Tag] Warning: Failed to reload grid: {e}")
                        # Clear the tag filter to prevent showing stale data
                        if hasattr(self, "context") and "tag_filter" in self.context:
                            self.context["tag_filter"] = None
                            self.reload()  # Try again without filter

        elif chosen in toggle_actions:
            # Check if any photos are selected
            if not paths:
                QMessageBox.information(
                    self,
                    "No Photos Selected",
                    "Please select one or more photos before toggling a tag."
                )
                return

            # TOGGLE: Remove if present, add if absent
            tagname = toggle_actions[chosen]
            tag_service = get_tag_service()

            if tagname in present_tags:
                # Remove from all selected photos
                for p in paths:
                    tag_service.remove_tag(p, tagname, self.project_id)
                print(f"[Tag] Removed '{tagname}' → {len(paths)} photo(s)")
            else:
                # Add to all selected photos
                count = tag_service.assign_tags_bulk(paths, tagname, self.project_id)
                print(f"[Tag] Added '{tagname}' → {count} photo(s)")

            # CRITICAL: Wrap post-tag operations in try/except to prevent crashes
            try:
                self._refresh_tags_for_paths(paths)
            except Exception as e:
                print(f"[Tag] Warning: Failed to refresh tag overlays: {e}")

            # 🪄 Refresh sidebar tags
            try:
                mw = self.window()
                if hasattr(mw, "sidebar"):
                    if hasattr(mw.sidebar, "reload_tags_only"):
                        mw.sidebar.reload_tags_only()
                    else:
                        mw.sidebar.reload()
            except Exception as e:
                print(f"[Tag] Warning: Failed to reload sidebar tags: {e}")

            # 🔄 Reload grid if we removed the active tag filter
            if tagname in present_tags:
                active_tag = getattr(self, "context", {}).get("tag_filter")
                if active_tag and active_tag.lower() == tagname.lower():
                    print(f"[Tag] Reloading grid - removed tag matches active filter '{active_tag}'")
                    try:
                        self.reload()
                    except Exception as e:
                        print(f"[Tag] Warning: Failed to reload grid: {e}")
                        # Clear the tag filter to prevent showing stale data
                        if hasattr(self, "context") and "tag_filter" in self.context:
                            self.context["tag_filter"] = None
                            self.reload()  # Try again without filter

        elif chosen is act_new_tag:
            # Check if any photos are selected
            if not paths:
                QMessageBox.information(
                    self,
                    "No Photos Selected",
                    "Please select one or more photos before creating and assigning a tag."
                )
                return

            # ARCHITECTURE: UI Layer → TagService → TagRepository → Database
            from PySide6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(self, "New Tag", "Tag name:")
            if ok and name.strip():
                tname = name.strip()
                tag_service = get_tag_service()
                # Ensure tag exists and assign to photos (Schema v3.1.0)
                tag_service.ensure_tag_exists(tname, self.project_id)
                count = tag_service.assign_tags_bulk(paths, tname, self.project_id)
                print(f"[Tag] Created and assigned '{tname}' → {count} photo(s)")
                self._refresh_tags_for_paths(paths)

                # 🪄 Refresh sidebar tags
                mw = self.window()
                if hasattr(mw, "sidebar"):
                    if hasattr(mw.sidebar, "reload_tags_only"):
                        mw.sidebar.reload_tags_only()
                    else:
                        mw.sidebar.reload()

        elif chosen is act_clear_all:
            # ARCHITECTURE: UI Layer → TagService → TagRepository → Database
            # Remove all present tags from selection
            tag_service = get_tag_service()
            for p in paths:
                for t in list(present_tags):
                    tag_service.remove_tag(p, t, self.project_id)
            print(f"[Tag] Cleared all tags → {len(paths)} photo(s)")
            self._refresh_tags_for_paths(paths)

            # 🪄 Refresh sidebar tags
            mw = self.window()
            if hasattr(mw, "sidebar"):
                if hasattr(mw.sidebar, "reload_tags_only"):
                    mw.sidebar.reload_tags_only()
                else:
                    mw.sidebar.reload()

            # 🔄 Reload grid if viewing a tag branch that was just cleared
            active_tag = getattr(self, "context", {}).get("tag_filter")
            if active_tag and active_tag.lower() in [t.lower() for t in present_tags]:
                print(f"[Tag] Reloading grid - cleared tags include active filter '{active_tag}'")
                self.reload()


    def _refresh_tags_for_paths(self, paths: list[str]):
        """
        Refresh tag overlay (Qt.UserRole+2) for given paths only.
        Avoids full grid reload and keeps UI snappy.

        ARCHITECTURE: UI Layer → TagService → TagRepository → Database
        """
        if not paths:
            return
        try:
            # Use TagService for proper layered architecture
            tag_service = get_tag_service()
            tags_map = tag_service.get_tags_for_paths(paths, self.project_id)
            print(f"[TagCache] Refreshing tags for {len(paths)} paths, got {len(tags_map)} entries")
        except Exception as e:
            print(f"[TagCache] ❌ Failed to fetch tags: {e}")
            return

        # normalize to the same format used in load()
        updated_count = 0
        for row in range(self.model.rowCount()):
            item = self.model.item(row)
            if not item:
                continue
            p = item.data(Qt.UserRole)
            if p in tags_map:
                new_tags = tags_map.get(p, [])
                item.setData(new_tags, Qt.UserRole + 2)
                updated_count += 1

        if updated_count > 0:
            print(f"[TagCache] Updated {updated_count} items, repaint complete")

        # repaint only
        self.list_view.viewport().update()

    # ==========================================================
    # 📸 Zoom Handling with Fixed Height & Aspect Ratio
    # ==========================================================
    def _init_zoom(self):
        """Initialize zoom state and event handling."""
        self._thumb_base = 120
        self._zoom_factor = 1.0
        self._min_zoom = 0.5
        self._max_zoom = 3.0

        from settings_manager_qt import SettingsManager
        self.settings = SettingsManager()
        self._cell_padding = self.settings.get("thumb_padding", 8)

        self.list_view.setViewMode(QListView.IconMode)
        self.list_view.setResizeMode(QListView.Adjust)
        self.list_view.setSpacing(self._cell_padding)
        self.list_view.setUniformItemSizes(False)  # allow dynamic width
        self.list_view.setMovement(QListView.Static)
        self.list_view.setWrapping(True)

        if hasattr(self, "zoom_slider"):
            self.zoom_slider.setMinimum(0)
            self.zoom_slider.setMaximum(100)
            self.zoom_slider.setValue(50)
            self.zoom_slider.valueChanged.connect(self._on_slider_changed)

        self.list_view.viewport().installEventFilter(self)

    def _thumb_size_for_aspect(self, aspect_ratio: float) -> QSize:
        """
        Compute size for a given aspect ratio based on current zoom factor.
        Height is fixed, width varies.
        """
        thumb_h = int(self._thumb_base * self._zoom_factor)
        if aspect_ratio <= 0:
            aspect_ratio = 1.5  # fallback default
        thumb_w = int(thumb_h * aspect_ratio)
        return QSize(thumb_w, thumb_h)

    def _set_zoom_factor(self, factor: float):
        """Clamp and apply zoom factor, update all items."""
        factor = max(self._min_zoom, min(self._max_zoom, factor))
        self._zoom_factor = factor
        self._apply_zoom_geometry()

    def _apply_zoom_geometry(self):
        """
        Recalculate grid sizes for all items based on current zoom and
        their stored aspect ratios.
        """
        for i in range(self.model.rowCount()):
            idx = self.model.index(i, 0)
            aspect_ratio = idx.data(Qt.UserRole + 1) or 1.5
            size = self._thumb_size_for_aspect(aspect_ratio)
            self.model.setData(idx, size, Qt.SizeHintRole)

        self.list_view.setSpacing(self._cell_padding)
        self.list_view.updateGeometry()
        self.list_view.repaint()


    def _animate_zoom_to(self, target_factor: float, duration: int = 200):
        """Smoothly animate zoom factor between current and target value."""
        target_factor = max(self._min_zoom, min(self._max_zoom, target_factor))

        # Kill existing animation if still running
        if hasattr(self, "_zoom_anim") and self._zoom_anim is not None:
            self._zoom_anim.stop()

        # PropertyAnimation on a dynamic property
        self.setProperty("_zoom_factor_prop", self._zoom_factor)
        self._zoom_anim = QPropertyAnimation(self, b"_zoom_factor_prop", self)
        self._zoom_anim.setDuration(duration)
        self._zoom_anim.setStartValue(self._zoom_factor)
        self._zoom_anim.setEndValue(target_factor)
        self._zoom_anim.setEasingCurve(QEasingCurve.InOutQuad)

#        self._zoom_anim.valueChanged.connect(lambda val: self._set_zoom_factor(float(val)))
#        self._zoom_anim.start()

        def _on_zoom_anim_val(val):
            self._set_zoom_factor(float(val))
            # sync slider position (inverse mapping)
            norm = (float(val) - self._min_zoom) / (self._max_zoom - self._min_zoom)
            self.zoom_slider.blockSignals(True)
            self.zoom_slider.setValue(int(norm * 100))
            self.zoom_slider.blockSignals(False)

        self._zoom_anim.valueChanged.connect(_on_zoom_anim_val)
        # Phase 2.3: Emit gridReloaded when zoom animation finishes (for status bar update)
        self._zoom_anim.finished.connect(lambda: self.gridReloaded.emit())
        self._zoom_anim.start()


    def zoom_in(self):
        self._animate_zoom_to(self._zoom_factor * 1.1)

    def zoom_out(self):
        self._animate_zoom_to(self._zoom_factor / 1.1)

    def _on_slider_changed(self, value: int):
        """Animate slider-driven zoom as well."""
        norm = value / 100.0
        new_factor = self._min_zoom + (self._max_zoom - self._min_zoom) * norm
        self._animate_zoom_to(new_factor)

    def _on_slider_pressed(self):
        # Stop any running animation while dragging
        if hasattr(self, "_zoom_anim") and self._zoom_anim is not None:
            self._zoom_anim.stop()
        self._is_slider_dragging = True

    def _on_slider_value_changed(self, value: int):
        # Live preview during drag — immediate resize without animation
        if getattr(self, "_is_slider_dragging", False):
            norm = value / 100.0
            new_factor = self._min_zoom + (self._max_zoom - self._min_zoom) * norm
            self._set_zoom_factor(new_factor)

    def _on_slider_released(self):
        self._is_slider_dragging = False
        value = self.zoom_slider.value()
        norm = value / 100.0
        new_factor = self._min_zoom + (self._max_zoom - self._min_zoom) * norm
        # ✨ smooth animate to final position for polish
        self._animate_zoom_to(new_factor)


    def _on_double_clicked(self, index):
        path = index.data(Qt.UserRole)
        print(f"[ThumbnailGridQt_on_double_clicked] index: {index.data}")
        if path:
            self.openRequested.emit(path)

    def eventFilter(self, obj, event):
        """
        Phase 2.1: Unified event filter for keyboard shortcuts and mouse events.

        Handles:
        - Ctrl+Wheel: Zoom in/out
        - Arrow keys: Navigate grid
        - Ctrl+A: Select all
        - Escape: Clear selection
        - Space/Enter: Open lightbox
        - Delete: Delete selected
        """
        # Ctrl+Wheel zoom (merged from previous eventFilter)
        if event.type() == QEvent.Wheel and (event.modifiers() & Qt.ControlModifier):
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            return True

        # Keyboard shortcuts
        if obj is self.list_view and event.type() == QEvent.KeyPress:
            key = event.key()
            mods = event.modifiers()

            # Ctrl+A -> select all
            if key == Qt.Key_A and (mods & Qt.ControlModifier):
                self.list_view.selectAll()
                return True

            # Esc -> clear selection
            if key == Qt.Key_Escape:
                self.list_view.clearSelection()
                return True

            # Delete -> request deletion of selected paths
            if key in (Qt.Key_Delete, Qt.Key_Backspace):
                paths = self.get_selected_paths()
                if paths:
                    self.deleteRequested.emit(paths)
                return True

            # Space or Enter -> open lightbox for current/selected item
            if key in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
                current = self.list_view.currentIndex()
                if current.isValid():
                    path = current.data(Qt.UserRole)
                    if path:
                        self.openRequested.emit(path)
                return True

            # Arrow key navigation (Up/Down/Left/Right)
            if key in (Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right):
                return self._handle_arrow_navigation(key, mods)

        return super().eventFilter(obj, event)

    def _handle_arrow_navigation(self, key, mods):
        """
        Phase 2.1: Handle arrow key navigation in grid view.

        Moves selection up/down/left/right based on grid layout.
        Supports Shift for range selection.
        """
        current_index = self.list_view.currentIndex()
        if not current_index.isValid():
            # No current selection - select first item
            if self.model.rowCount() > 0:
                first_index = self.model.index(0, 0)
                self.list_view.setCurrentIndex(first_index)
                if not (mods & Qt.ShiftModifier):
                    self.list_view.selectionModel().select(first_index, QItemSelectionModel.ClearAndSelect)
            return True

        current_row = current_index.row()
        total_rows = self.model.rowCount()

        # Calculate items per row based on current grid layout
        viewport_width = self.list_view.viewport().width()
        item_width = self.delegate.sizeHint(QStyleOptionViewItem(), current_index).width()
        spacing = self.list_view.spacing()
        items_per_row = max(1, viewport_width // (item_width + spacing))

        # Determine target row based on key
        target_row = current_row
        if key == Qt.Key_Left:
            target_row = max(0, current_row - 1)
        elif key == Qt.Key_Right:
            target_row = min(total_rows - 1, current_row + 1)
        elif key == Qt.Key_Up:
            target_row = max(0, current_row - items_per_row)
        elif key == Qt.Key_Down:
            target_row = min(total_rows - 1, current_row + items_per_row)

        # Apply navigation
        if target_row != current_row:
            target_index = self.model.index(target_row, 0)
            self.list_view.setCurrentIndex(target_index)

            # Handle selection based on modifiers
            if mods & Qt.ShiftModifier:
                # Shift: Extend selection (range select)
                self.list_view.selectionModel().select(target_index, QItemSelectionModel.Select)
            else:
                # No modifier: Move selection (clear previous)
                self.list_view.selectionModel().select(target_index, QItemSelectionModel.ClearAndSelect)

            # Ensure target item is visible
            self.list_view.scrollTo(target_index, QAbstractItemView.EnsureVisible)

        return True

    def get_selected_paths(self):
        selection = self.list_view.selectionModel().selectedIndexes()
        return [i.data(Qt.UserRole) for i in selection if i.isValid()]


    def set_project(self, project_id: int):
        self.project_id = project_id
        self.clear()


# ============================================================
    # 🧭 Navigation handlers
    # ============================================================
    def set_folder(self, folder_id: int):
        """Called when a folder node is clicked."""
        self.navigation_mode = "folder"
        self.navigation_key = folder_id
        self.active_tag_filter = None

        self.load_mode = "folder"
        self.current_folder_id = folder_id
        self.reload()
        
        self._apply_zoom_geometry()
        

    def set_branch(self, branch_key: str):
        """Called when a branch node is clicked."""
        self.navigation_mode = "branch"
        self.navigation_key = branch_key
        self.active_tag_filter = None

        self.load_mode = "branch"
        self.branch_key = branch_key
        self.reload()

    def set_date(self, date_key: str):
        """Called when a date node (YYYY / YYYY-MM / YYYY-MM-DD) is clicked."""
        self.navigation_mode = "date"
        self.navigation_key = date_key
        self.active_tag_filter = None

        self.load_mode = "date"
        self.date_key = date_key
        self.reload()

    def set_videos(self):
        """
        Called when Videos tab is selected - show all videos for current project.
        🎬 Phase 4.3: Video support
        """
        self.navigation_mode = "videos"
        self.navigation_key = None
        self.active_tag_filter = None

        self.load_mode = "videos"
        self.reload()

    def load_paths(self, paths: list[str]):
        """
        Load arbitrary list of photo paths (e.g., from search results).

        This is used for search results or custom photo collections that
        don't fit into the folder/branch/date navigation paradigm.

        Args:
            paths: List of photo file paths to display
        """
        self.navigation_mode = "custom"
        self.navigation_key = None
        self.active_tag_filter = None
        self.load_mode = "custom"

        # Store paths and reload
        self._paths = list(paths)
        print(f"[GRID] Loading {len(self._paths)} custom paths (e.g., search results)")

        # Clear and reload grid
        self.model.clear()
        self._reload_token = uuid.uuid4()  # Generate new UUID token
        self._current_reload_token = self._reload_token
        token = self._reload_token

        # Get tags for all paths
        tag_map = {}
        try:
            if hasattr(self.db, 'get_tags_for_paths'):
                tag_map = self.db.get_tags_for_paths(self._paths)
        except Exception as e:
            print(f"[GRID] Warning: Could not fetch tags: {e}")

        # Load thumbnails
        for i, p in enumerate(self._paths):
            item = QStandardItem()
            item.setData(p, Qt.UserRole)  # normalized path
            item.setData(p, Qt.UserRole + 6)  # real path
            item.setData(tag_map.get(p, []), Qt.UserRole + 2)  # tags

            # Set placeholder size
            aspect_ratio = 1.5
            item.setData(aspect_ratio, Qt.UserRole + 1)
            item.setSizeHint(self._thumb_size_for_aspect(aspect_ratio))

            self.model.appendRow(item)

        # Trigger thumbnail loading
        self._apply_zoom_geometry()
        self.list_view.doItemsLayout()
        self.list_view.viewport().update()

        # Request visible thumbnails
        if hasattr(self, 'request_visible_thumbnails'):
            QTimer.singleShot(100, self.request_visible_thumbnails)

        print(f"[GRID] Loaded {len(self._paths)} thumbnails in custom mode")

        # Phase 2.3: Emit signal for status bar update
        self.gridReloaded.emit()

    def reload_priortoContext_driven(self):
        """
        Load image paths based on current load_mode and refresh thumbnail grid.
        Prevents duplicate reloads for the same context.
        """
#        # --- Prevent duplicate reloads ---
        
        self.model.clear()
        self._paths.clear()

        # ✅ Handle tag overlay mode explicitly
        if getattr(self, "load_mode", None) == "tag":
            if getattr(self, "active_tag_filter", None):
                print(f"[GRID] Reload requested under tag filter '{self.active_tag_filter}' – skipping DB context reload.")
                # keep showing current filtered paths
                self._apply_zoom_layout()
                self.list_view.doItemsLayout()
                self.list_view.viewport().update()
                return
            else:
                # tag cleared → fall back to previous context
                print("[GRID] Tag filter cleared – restoring previous context.")
                self.load_mode = getattr(self, "last_nav_mode", "branch")

        # --- Load from DB
           
        if self.load_mode == "branch":
            if not self.branch_key:
                return
            # Support virtual date branches ("date:")
            if self.branch_key.startswith("date:"):
                paths = self.db.get_images_for_quick_key(self.branch_key)
            else:
                if not self.project_id:
                    return
                paths = self.db.get_images_by_branch(self.project_id, self.branch_key)
                                    
        elif self.load_mode == "folder":
            if not self.current_folder_id:
                return
            paths = self.db.get_images_by_folder(self.current_folder_id, project_id=self.project_id)

        elif self.load_mode == "date":
            if not self.date_key:
                return
            dk = self.date_key  # already normalized to YYYY / YYYY-MM / YYYY-MM-DD
            if len(dk) == 4 and dk.isdigit():
                paths = self.db.get_images_by_year(int(dk), self.project_id)

            elif len(dk) == 7 and dk[4] == "-" and dk[5:7].isdigit():
                year, month = dk.split("-", 1)
                paths = self.db.get_images_by_month(year, month, self.project_id)
                # fallback: if no results, maybe dates have timestamps—try prefix search
                if not paths:
                    paths = self.db.get_images_for_quick_key(f"date:{dk}")

            elif len(dk) == 10 and dk[4] == "-" and dk[7] == "-":
                paths = self.db.get_images_by_date(dk, self.project_id)
            else:
                # fallback for quick keys (rare)
                paths = self.db.get_images_for_quick_key(f"date:{dk}")
                        
        else:
            return

        # Normalize to list[str]
        self._paths = [
            r[0] if isinstance(r, (tuple, list)) else
            r.get("path") if isinstance(r, dict) and "path" in r else
            str(r)
            for r in paths
        ]

        tag_map = self.db.get_tags_for_paths(self._paths)

        # --- Build items
        token = self._reload_token
        for i, p in enumerate(self._paths):
            item = QStandardItem()
            item.setEditable(False)
            item.setData(p, Qt.UserRole)
            item.setData(tag_map.get(p, []), Qt.UserRole + 2)  # store tags list
            
            # initial placeholder size
            item.setSizeHint(QSize(self.thumb_height * 2, self.thumb_height))
            self.model.appendRow(item)

            worker = ThumbWorker(p, self.thumb_height, i, self.thumb_signal, self._thumb_cache, token, self._placeholder_pixmap)
            self.thread_pool.start(worker)

        # --- Trigger UI update
        self._apply_zoom_layout()
        self.list_view.doItemsLayout()
        self.list_view.viewport().update()
        print(f"[GRID] Reloaded {len(self._paths)} thumbnails in {self.load_mode}-mode.")
 
    # ============================================================
    # 🌍 Context-driven navigation & reload (Enhanced with user feedback)
    # ============================================================
    def set_context(self, mode: str, key: str | int | None):
        """
        Sets navigation context (folder, branch, or date) and triggers reload.
        Clears any active tag overlay.
        """
        self.context = getattr(self, "context", {
            "mode": None, "key": None, "tag_filter": None
        })
        self.context["mode"] = mode
        self.context["key"] = key
        self.context["tag_filter"] = None
        self.reload()

    # ============================================================
    def apply_tag_filter(self, tag: str | None):
        """
        Overlay a tag filter on top of the current navigation context.
        Passing None or 'all' clears the filter.
        """
        if not hasattr(self, "context"):
            self.context = {"mode": None, "key": None, "tag_filter": None}
        self.context["tag_filter"] = tag if tag not in (None, "", "all") else None
        self.reload()

    # ============================================================
    def reload(self):
        """
        Centralized reload logic combining navigation context + optional tag overlay.
        Includes user feedback via status bar and detailed console logs.
        """
        # CRITICAL: Prevent concurrent reloads that cause crashes
        # Similar to sidebar._refreshing flag pattern
        if getattr(self, '_reloading', False):
            print("[GRID] reload() blocked - already reloading (prevents concurrent reload crash)")
            return

        try:
            self._reloading = True

            import os
            from PySide6.QtCore import QSize
            from PySide6.QtGui import QStandardItem

            db = self.db
            ctx = getattr(self, "context", {"mode": None, "key": None, "tag_filter": None})
            mode, key, tag = ctx["mode"], ctx["key"], ctx["tag_filter"]

            # CRITICAL FIX: Update load_mode to match context mode
            # This ensures grid state stays synchronized when switching between photo/video navigation
            if mode in ("folder", "branch", "date", "videos", "tag", "people"):
                self.load_mode = mode
            elif mode is None and tag:
                # Tag filter without specific navigation context
                self.load_mode = "tag"

            # --- 1️+2️: Determine base photo paths by navigation mode AND tag filter ---
            # CRITICAL FIX: Use efficient database queries instead of in-memory intersection
            # OLD (SLOW): Load all 2856 photos → filter in memory → UI freeze
            # NEW (FAST): SQL JOIN returns only matching photos → instant response

            if tag:
                # Tag filter is active - use efficient JOIN queries
                if mode == "folder" and key:
                    paths = db.get_images_by_folder_and_tag(self.project_id, key, tag, include_subfolders=True)
                    print(f"[TAG FILTER] Folder {key} + tag '{tag}' → {len(paths)} photos (efficient query)")
                elif mode == "branch" and key:
                    paths = db.get_images_by_branch_and_tag(self.project_id, key, tag)
                    print(f"[TAG FILTER] Branch {key} + tag '{tag}' → {len(paths)} photos (efficient query)")
                elif mode == "people" and key:
                    # 👥 Face cluster + tag filter
                    paths = db.get_images_by_branch_and_tag(self.project_id, key, tag)
                    print(f"[TAG FILTER] People {key} + tag '{tag}' → {len(paths)} photos (efficient query)")
                elif mode == "date" and key:
                    dk = str(key)
                    paths = db.get_images_by_date_and_tag(self.project_id, dk, tag)
                    print(f"[TAG FILTER] Date {dk} + tag '{tag}' → {len(paths)} photos (efficient query)")
                else:
                    # No navigation context - show all tagged photos
                    paths = db.get_image_paths_for_tag(tag, self.project_id)
                    self.context["mode"] = "tag"
                    print(f"[TAG FILTER] Showing all tagged photos for '{tag}' ({len(paths)})")

            else:
                # No tag filter - use normal navigation queries
                if mode == "folder" and key:
                    paths = db.get_images_by_folder(key, project_id=self.project_id)
                elif mode == "branch" and key:
                    paths = db.get_images_by_branch(self.project_id, key)
                elif mode == "people" and key:
                    # 👥 Face cluster navigation - load photos containing faces from this cluster
                    paths = db.get_images_by_branch(self.project_id, key)
                    print(f"[GRID] Loaded {len(paths)} photos for face cluster {key}")
                elif mode == "date" and key:
                    dk = str(key)
                    if len(dk) == 4 and dk.isdigit():
                        paths = db.get_images_by_year(int(dk), self.project_id)
                    elif len(dk) == 7 and dk[4] == "-" and dk[5:7].isdigit():
                        paths = db.get_images_by_month_str(dk, self.project_id)
                    elif len(dk) == 10 and dk[4] == "-" and dk[7] == "-":
                        paths = db.get_images_by_date(dk, self.project_id)
                    else:
                        # fallback for quick keys (e.g. date:this-week)
                        paths = db.get_images_for_quick_key(f"date:{dk}", self.project_id)
                elif mode == "videos":
                    # 🎬 Phase 4.3: Load all videos for project
                    try:
                        from services.video_service import VideoService
                        video_service = VideoService()
                        videos = video_service.get_videos_by_project(self.project_id)
                        paths = [v['path'] for v in videos]
                        print(f"[GRID] Loaded {len(paths)} videos for project {self.project_id}")
                    except Exception as e:
                        print(f"[GRID] Failed to load videos: {e}")
                        paths = []
                else:
                    paths = []

            final_count = len(paths)
            base_count = final_count  # For status message compatibility

            # --- 3️: Render grid ---
            self._load_paths(paths)

            # --- 4️: User feedback ---
            context_label = {
                "folder": "Folder",
                "branch": "Branch",
                "date": "Date",
                "tag": "Tag",
                "videos": "Videos",
                "people": "People"
            }.get(mode or "unknown", "Unknown")

            tag_label = f" [Tag: {tag}]" if tag else ""
            media_label = "video(s)" if mode == "videos" else "photo(s)"
            status_msg = (
                f"{context_label}: {key or '—'} → "
                f"{final_count} {media_label} shown"
                f"{' (filtered)' if tag else ''}"
            )

            # Status bar update (if parent has one)
            mw = self.window()
            if hasattr(mw, "statusBar"):
                try:
                    mw.statusBar().showMessage(status_msg)
                except Exception:
                    pass

            # Detailed console log
            if tag:
                print(f"[GRID] Reloaded {final_count}/{base_count} thumbnails in {mode}-mode (tag={tag})")
            else:
                print(f"[GRID] Reloaded {final_count} thumbnails in {mode}-mode (base={base_count})")

            # Phase 2.3: Emit signal for status bar update
            self.gridReloaded.emit()

        finally:
            # Always reset flag even if exception occurs
            self._reloading = False

    # ============================================================
    def _load_paths(self, paths: list[str]):
        """
        Build and render thumbnail items from the given path list.
        """
        from PySide6.QtCore import QSize, Qt
        from PySide6.QtGui import QStandardItem, QPixmap, QIcon


        self._reload_token = uuid.uuid4()
        self._current_reload_token = self._reload_token

        self.model.clear()
        self._paths = [str(p) for p in paths]
        tag_map = self.db.get_tags_for_paths(self._paths)

        # 📏 Default aspect ratio for placeholders
        default_aspect = 1.5
        placeholder_size = self._thumb_size_for_aspect(default_aspect)

        # optional placeholder pixmap (scale shared placeholder if needed)
        placeholder_pix = self._placeholder_pixmap
        if placeholder_pix.size() != placeholder_size:
            try:
                placeholder_pix = self._placeholder_pixmap.scaled(placeholder_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            except Exception:
                placeholder_pix = QPixmap(placeholder_size)
                placeholder_pix.fill(Qt.transparent)
        
        token = self._reload_token
        for i, p in enumerate(self._paths):
            item = QStandardItem()
            item.setEditable(False)

            # normalize once and keep both
            np = self._norm_path(p)

            # 🧾 store data
            item.setData(np, Qt.UserRole)           # normalized model key
            item.setData(p,  Qt.UserRole + 6)       # real on-disk path (for stat/open)

            item.setData(tag_map.get(p, []), Qt.UserRole + 2)
            item.setData(default_aspect, Qt.UserRole + 1)
            item.setData(False, Qt.UserRole + 5)   # not scheduled yet

            # 🎬 Phase 4.3: Store video metadata if this is a video file
            if is_video_file(p):
                try:
                    # Try to get metadata from video_metadata table
                    video_meta = self.db.get_video_by_path(p, self.project_id)
                    if video_meta:
                        # Duration for badge
                        if 'duration_seconds' in video_meta:
                            item.setData(video_meta['duration_seconds'], Qt.UserRole + 3)

                        # Phase 3: Status indicators for UI feedback
                        metadata_status = video_meta.get('metadata_status', 'pending')
                        thumbnail_status = video_meta.get('thumbnail_status', 'pending')
                        item.setData(metadata_status, Qt.UserRole + 7)
                        item.setData(thumbnail_status, Qt.UserRole + 8)

                        # Phase 4: Rich tooltip with video metadata details
                        tooltip_parts = [f"🎬 <b>{os.path.basename(p)}</b>"]

                        # Duration
                        if video_meta.get('duration_seconds'):
                            duration_str = format_duration(video_meta['duration_seconds'])
                            tooltip_parts.append(f"⏱️ Duration: {duration_str}")

                        # Resolution
                        width = video_meta.get('width')
                        height = video_meta.get('height')
                        if width and height:
                            # Determine quality label
                            if height >= 2160:
                                quality = "4K UHD"
                            elif height >= 1080:
                                quality = "Full HD"
                            elif height >= 720:
                                quality = "HD"
                            else:
                                quality = "SD"
                            tooltip_parts.append(f"📺 Resolution: {width}x{height} ({quality})")

                        # Frame rate
                        if video_meta.get('fps'):
                            tooltip_parts.append(f"🎞️ Frame Rate: {video_meta['fps']:.1f} fps")

                        # Codec
                        if video_meta.get('codec'):
                            tooltip_parts.append(f"🎞️ Codec: {video_meta['codec']}")

                        # Bitrate
                        if video_meta.get('bitrate'):
                            bitrate_mbps = video_meta['bitrate'] / 1_000_000
                            tooltip_parts.append(f"📊 Bitrate: {bitrate_mbps:.1f} Mbps")

                        # File size
                        if video_meta.get('size_kb'):
                            size_mb = video_meta['size_kb'] / 1024
                            if size_mb >= 1024:
                                size_str = f"{size_mb / 1024:.1f} GB"
                            else:
                                size_str = f"{size_mb:.1f} MB"
                            tooltip_parts.append(f"📦 Size: {size_str}")

                        # Date taken
                        if video_meta.get('date_taken'):
                            tooltip_parts.append(f"📅 Date: {video_meta['date_taken']}")

                        # Processing status
                        if metadata_status != 'ok' or thumbnail_status != 'ok':
                            status_parts = []
                            if metadata_status != 'ok':
                                status_parts.append(f"Metadata: {metadata_status}")
                            if thumbnail_status != 'ok':
                                status_parts.append(f"Thumbnail: {thumbnail_status}")
                            tooltip_parts.append(f"⚠️ Status: {', '.join(status_parts)}")

                        item.setToolTip("<br>".join(tooltip_parts))
                    else:
                        # No video record found
                        item.setData(None, Qt.UserRole + 3)
                        item.setData('unknown', Qt.UserRole + 7)
                        item.setData('unknown', Qt.UserRole + 8)
                        item.setToolTip(f"🎬 {os.path.basename(p)}<br>⚠️ No metadata available")
                except Exception as e:
                    # If query fails, set defaults
                    item.setData(None, Qt.UserRole + 3)
                    item.setData('error', Qt.UserRole + 7)
                    item.setData('error', Qt.UserRole + 8)
                    item.setToolTip(f"🎬 {os.path.basename(p)}<br>❌ Error loading metadata: {str(e)}")

            # 🖼 initial placeholder size & icon
            item.setSizeHint(placeholder_size)
            item.setIcon(QIcon(placeholder_pix))

            self.model.appendRow(item)

            # ⚡ PERFORMANCE FIX: Don't start workers for all photos upfront!
            # Let request_visible_thumbnails() handle viewport-based loading
            # This prevents flooding the thread pool with 10,000+ workers

            # OLD CODE (removed):
            # thumb_h = int(self._thumb_base * self._zoom_factor)
            # np = self._norm_path(p)
            # self.thread_pool.start(
            #     ThumbWorker(p, np, thumb_h, i, self.thumb_signal, self._thumb_cache, token, placeholder_pix)
            # )


        # 🧭 Apply current zoom layout to placeholder sizes
        self._apply_zoom_geometry()
        self.list_view.doItemsLayout()
        self.list_view.viewport().update()
        print(f"[GRID] Loaded {len(self._paths)} thumbnails.")

        # kick the incremental scheduler
        QTimer.singleShot(0, self.request_visible_thumbnails)

        # === 🔥 Optional next-folder/date prefetch ===
        try:
            # Find next sibling in sidebar (if available)
            if hasattr(self.window(), "sidebar") and hasattr(self.window().sidebar, "get_next_branch_paths"):
                next_paths = self.window().sidebar.get_next_branch_paths(self.navigation_mode, self.navigation_key)
                if next_paths:
                    self.preload_cache_warmup(next_paths[:50])  # prefetch only first 50
        except Exception as e:
            print(f"[WarmUp] Prefetch skipped: {e}")        
        
    # ============================================================
    # ⚙️  Optional Cache Warm-Up Prefetcher
    # ============================================================
    def preload_cache_warmup(self, next_paths: list[str]):
        """
        Prefetch thumbnails for the next folder/date in background.
        Does not display them, only decodes + stores in cache.
        """
        if not next_paths:
            return

        print(f"[WarmUp] Starting prefetch for {len(next_paths)} upcoming images...")

        # Avoid blocking UI
        from PySide6.QtCore import QRunnable, Slot

        class WarmupWorker(QRunnable):
            def __init__(self, paths, thumb_base, zoom_factor, cache, decode_timeout, placeholder):
                super().__init__()
                self.paths = paths
                self.thumb_base = thumb_base
                self.zoom_factor = zoom_factor
                self.cache = cache
                self.decode_timeout = decode_timeout
                self.placeholder = placeholder

            @Slot()
            def run(self):
                from thumb_cache_db import get_cache
                cache_db = get_cache()
                height = int(self.thumb_base * self.zoom_factor)
                count = 0

                for path in self.paths:
                    try:
                        if not os.path.exists(path):
                            continue
                        st = os.stat(path)
                        mtime = st.st_mtime

                        # skip if already cached
                        if path in self.cache and abs(self.cache[path]["mtime"] - mtime) < 0.1:
                            continue
                        if cache_db.has_entry(path, mtime):
                            continue

                        # decode quietly
                        pm = load_thumbnail_safe(path, height, self.cache, self.decode_timeout, self.placeholder)
                        if pm and not pm.isNull():
                            count += 1

                    except Exception as e:
                        print(f"[WarmUp] Skip {path}: {e}")

                print(f"[WarmUp] Prefetch complete: {count}/{len(self.paths)} thumbnails cached.")

        worker = WarmupWorker(
            next_paths, self._thumb_base, self._zoom_factor,
            self._thumb_cache, self._decode_timeout, self._placeholder_pixmap
        )
        self.thread_pool.start(worker)


    def _load_paths_later(self, paths: list[str]):
        """
        Build and render thumbnail items with tag overlay badges (⭐ 🧍 etc.)
        and dynamic placeholder sizing (fixed height, variable width).
        """
        from PySide6.QtCore import QSize, Qt
        from PySide6.QtGui import QStandardItem, QPixmap, QIcon, QPainter, QColor, QFont
        import os

        self.model.clear()
        self._paths = [str(p) for p in paths]
        tag_map = self.db.get_tags_for_paths(self._paths)

        default_aspect = 1.5
        placeholder_size = self._thumb_size_for_aspect(default_aspect)
        placeholder_pix = self._placeholder_pixmap
        if placeholder_pix.size() != placeholder_size:
            try:
                placeholder_pix = self._placeholder_pixmap.scaled(placeholder_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            except Exception:
                placeholder_pix = QPixmap(placeholder_size)
                placeholder_pix.fill(Qt.transparent)

        active_tag = self.context.get("tag_filter") if isinstance(self.context, dict) else None

        token = self._reload_token
        for i, p in enumerate(self._paths):
            item = QStandardItem()
            item.setEditable(False)
            item.setData(p, Qt.UserRole)
            item.setData(tag_map.get(p, []), Qt.UserRole + 2)
            item.setData(default_aspect, Qt.UserRole + 1)

            # --- Tag badge overlay on placeholder
            pix_with_badge = QPixmap(placeholder_pix)
            if active_tag:
                painter = QPainter(pix_with_badge)
                painter.setRenderHint(QPainter.Antialiasing)
                badge_color = QColor(255, 215, 0, 180)
                badge_icon = "⭐"

                if "face" in active_tag.lower():
                    badge_color = QColor(70, 130, 180, 180)
                    badge_icon = "🧍"
                elif "fav" in active_tag.lower():
                    badge_color = QColor(255, 215, 0, 180)
                    badge_icon = "⭐"
                else:
                    badge_color = QColor(144, 238, 144, 180)
                    badge_icon = "🏷"

                r = 22
                painter.setBrush(badge_color)
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(
                    placeholder_pix.width() - r - 6,
                    placeholder_pix.height() - r - 6,
                    r, r
                )

                font = QFont("Segoe UI Emoji", 14, QFont.Bold)
                painter.setFont(font)
                painter.setPen(Qt.white)
                painter.drawText(
                    QRect(placeholder_pix.width() - r - 6, placeholder_pix.height() - r - 6, r, r),
                    Qt.AlignCenter, badge_icon
                )
                painter.end()

            item.setIcon(QIcon(pix_with_badge))
            item.setSizeHint(placeholder_size)
            self.model.appendRow(item)

            # ⚡ PERFORMANCE FIX: Don't start workers for all photos upfront!
            # Let request_visible_thumbnails() handle viewport-based loading
            # (Same fix as in _load_paths method)

        self._apply_zoom_geometry()
        self.list_view.doItemsLayout()
        self.list_view.viewport().update()
        print(f"[GRID] Loaded {len(self._paths)} thumbnails with tag badges.")

        # Trigger viewport-based loading
        QTimer.singleShot(0, self.request_visible_thumbnails)

    # --- ADD inside class ThumbnailGridQt (near other public helpers) ---

    def get_visible_paths(self) -> list[str]:
        """
        Return the paths that are currently in the view/model, in order.
        This reflects any sorting and filtering that has been applied.
        """
        out = []
        for row in range(self.model.rowCount()):
            item = self.model.item(row)
            if item:
                p = item.data(Qt.UserRole)
                if p:
                    out.append(p)
        return out

    def get_all_paths(self) -> list[str]:
        """
        Return the internal list of paths as last loaded by reload().
        Useful when the view/model hasn't been populated yet.
        """
        return list(getattr(self, "_paths", []))


    # >>> FIX: Add size and dimension calculation for metadata panel
    def _file_metadata_info(self, path: str) -> dict:
        """
        Return a metadata dict with file size, width, height and mtime.
        Uses cached thumbnails where possible for performance.
        """
        info = {"size_kb": None, "width": None, "height": None, "modified": None}
        try:
            if not path or not os.path.exists(path):
                return info
            st = os.stat(path)
            info["size_kb"] = round(st.st_size / 1024.0, 3)
            info["modified"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))

            # Try cached thumbnail for dimensions first
            pm_entry = self._thumb_cache.get(self._norm_path(path))
            if pm_entry and pm_entry.get("pixmap"):
                pm = pm_entry["pixmap"]
                info["width"], info["height"] = pm.width(), pm.height()
            else:
                # Fallback to reading image header only (fast)
                reader = QImageReader(path)
                sz = reader.size()
                if sz and sz.width() > 0 and sz.height() > 0:
                    info["width"], info["height"] = sz.width(), sz.height()
        except Exception as e:
            print(f"[MetaInfo] Could not extract info for {path}: {e}")
        return info
    # <<< FIX
