# preview_panel_qt.py
# Version 09.16.01.11 dated 2025.10.22
# 
# one file for all photos preview / editing UI
#
# Photo-App/
# ├─ main_window_qt.py
# ├─ thumbnail_grid_qt.py
# ├─ thumb_cache_db.py
# ├─ settings_manager_qt.py
# ├─ preview_panel_qt.py   👈 NEW (LightboxDialog + CollapsiblePanel + #  LabeledSlider)
#  └─ assets/
#     └─ icons/
#
# Final Layout Hierarchy Recap
# QStackedWidget (mode_stack)
# ├─ viewer_page
# │  ├─ top_bar_viewer (arrows, edit, rotate, menu)
# │  ├─ image_area (shared)
# │  ├─ bottom_bar_viewer (zoom, info, tag_box, info button)
# │  └─ right_info_panel
# └─ editor_page
#   ├─ top_bar_editor_row1 (back)
#   ├─ top_bar_editor_row2 (save, save as, cancel)
#   ├─ image_area (shared)
#   ├─ bottom_bar_editor (context control slider)
#   └─ right_editor_panel (tool palette)
#

import os, time, subprocess

from PIL import Image, ImageOps, ImageEnhance, ImageFilter, ImageQt,ExifTags
from datetime import datetime

from PySide6.QtCore import (
    Qt, QParallelAnimationGroup, QPropertyAnimation, 
    QEasingCurve, QEvent, QTimer, QSize, 
    Signal, QRect, QPoint, QPointF, QUrl
)


from PySide6.QtWidgets import (
    QApplication, QDialog, QLabel, QVBoxLayout, QHBoxLayout, QStackedWidget, QGridLayout,
    QScrollArea, QSlider, QToolButton, QWidget, QPushButton, QMenu, QComboBox, QGraphicsDropShadowEffect,
    QTextEdit, QGraphicsOpacityEffect, QFileDialog, QMessageBox, QRubberBand, QFrame, QStyle, QSizePolicy, QLineEdit
)

from PySide6.QtGui import (
    QStandardItemModel, QStandardItem, QPixmap, QImage,
    QPainter, QPen, QBrush, QColor, QFont, QAction, QCursor,
    QIcon, QTransform, QDesktopServices
) 

from PySide6.QtSvg import QSvgRenderer

from reference_db import ReferenceDB




# ================================================================
# Collapsible Panel
# ================================================================
class CollapsiblePanel(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.toggle_button = QToolButton(text=title, checkable=True, checked=True)
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(Qt.DownArrow)
        self.toggle_button.clicked.connect(self._toggle)

        self.content_area = QWidget()
        self.content_area.setMaximumHeight(0)
        self.content_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.toggle_animation = QParallelAnimationGroup(self)
        self.content_anim = QPropertyAnimation(self.content_area, b"maximumHeight")
        self.content_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.content_anim.setDuration(200)
        self.toggle_animation.addAnimation(self.content_anim)

        self._content_layout = QVBoxLayout(self.content_area)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(6)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toggle_button)
        layout.addWidget(self.content_area)

    def addWidget(self, widget):
        self._content_layout.addWidget(widget)
        self.content_area.setMaximumHeight(self._content_layout.sizeHint().height())

    def _toggle(self):
        checked = self.toggle_button.isChecked()
        self.toggle_button.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        target_height = self._content_layout.sizeHint().height() if checked else 0
        self.content_anim.setStartValue(self.content_area.maximumHeight())
        self.content_anim.setEndValue(target_height)
        self.toggle_animation.start()


# ================================================================
# Labeled Slider (Icon + Label + Slider + % Value)
# ================================================================
class LabeledSlider(QWidget):
    valueChanged = Signal(int)

    def __init__(self, icon_path: str, label_text: str, min_val=-100, max_val=100, parent=None):
        super().__init__(parent)

        # --- Icon + label
        icon_label = QLabel()
        if icon_path:
            icon_label.setPixmap(QIcon(icon_path).pixmap(16, 16))
        self.text_label = QLabel(label_text)
        self.text_label.setMinimumWidth(70)

        # --- Slider and value
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(min_val, max_val)
        self.slider.setValue(0)

        self.value_label = QLabel("0")
        # make the value label a bit wider so it remains visible
        self.value_label.setFixedWidth(52)
        self.value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        # --- Layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(icon_label)
        layout.addWidget(self.text_label)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.value_label)

        # Signal: slider → valueChanged proxy
        self.slider.valueChanged.connect(self._update_value_label)
        self.slider.valueChanged.connect(self.valueChanged.emit)

    def _update_value_label(self, val: int):
        self.value_label.setText(str(val))

    def value(self) -> int:
        return self.slider.value()

    def setValue(self, v: int):
        self.slider.setValue(v)

    def connect_value_changed(self, fn: callable):
        if callable(fn):
            self.slider.valueChanged.connect(fn)
        else:
            raise TypeError(f"LabeledSlider.connect_value_changed expected a callable, got {type(fn)}")

# ================================================================
# LightboxDialog
# ================================================================                
class LightboxDialog(QDialog):
    """
    Minimal, reliable photo viewer built from scratch with a simple editor:
    - Central canvas
    - Right-side editor panel (Light / Color)
    - Live preview using Pillow
    """

    # ---------- Icon helper ----------
    def _icon(self, name: str) -> QIcon:
        base_dir = os.path.join(os.path.dirname(__file__), "assets", "icons")
        path = os.path.join(base_dir, f"{name}.svg")
        theme = getattr(self, "_theme", "light")

        if os.path.exists(path):
            pm = QPixmap(48, 48)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            renderer = QSvgRenderer(path)
            renderer.render(p)
            tint = QColor("#000000" if theme == "light" else "#ffffff")
            p.setCompositionMode(QPainter.CompositionMode_SourceIn)
            p.fillRect(pm.rect(), tint)
            p.end()
            return QIcon(pm)

        emoji_map = {
            "edit": "🖉", "rotate": "↻", "info": "ℹ️", "next": "▶", "prev": "◀",
            "save": "💾", "crop": "✂️", "reset": "⟳", "zoom_in": "➕",
            "zoom_out": "➖", "cancel": "✖", "brightness": "☀️", "contrast": "⚖️",
            "copy": "📋"
        }
        ch = emoji_map.get(name, "⬜")
        pm = QPixmap(48, 48)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        font = QFont("Segoe UI Emoji", 28)
        p.setFont(font)
        p.drawText(pm.rect(), Qt.AlignCenter, ch)
        p.end()
        return QIcon(pm)

    # ---------- Theme ----------
    def _apply_theme(self, theme: str = "light"):
        self._theme = theme
        if theme == "dark":
            bg = "#161616"; fg = "#ffffff"; panel = "#1f1f1f"; accent_start = "#0a84ff"
            button_border = "#2f2f2f"; hover_bg = "#1a73e8"; disabled = "#555555"
        else:
            bg = "#ffffff"; fg = "#000000"; panel = "#ffffff"; accent_start = "#0078d4"
            button_border = "#cccccc"; hover_bg = "#cde6ff"; disabled = "#bbbbbb"

        self._palette_tokens = {
            "bg": bg, "fg": fg, "panel": panel,
            "accent_start": accent_start, "button_border": button_border,
            "hover_bg": hover_bg, "disabled": disabled
        }
        pal = self.palette()
        pal.setColor(self.backgroundRole(), QColor(bg))
        pal.setColor(self.foregroundRole(), QColor(fg))
        self.setPalette(pal)
        self._toolbar_style = f"""
        QToolButton, QPushButton {{
            color: {fg};
            background-color: {panel};
            border: 1px solid {button_border};
            border-radius: 8px;
            padding: 6px 8px;
        }}
        QToolButton:hover, QPushButton:hover {{
            background-color: {hover_bg};
        }}
        """

    def _button_style(self) -> str:
        t = getattr(self, "_palette_tokens", {"fg":"#000","accent_start":"#0078d4","button_border":"#ccc","hover_bg":"#cde6ff","disabled":"#bbb"})
        return f"""
        QToolButton, QPushButton {{
            color: {t['fg']};
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {t['accent_start']},stop:1 #999);
            border: 1px solid {t['button_border']};
            border-radius: 8px;
            padding: 8px 14px;
        }}
        QToolButton:hover, QPushButton:hover {{
            background: {t['hover_bg']};
        }}
        QToolButton:disabled, QPushButton:disabled {{
            background: {t['disabled']};
            color: #888;
        }}
        """

    # ---------- Inner canvas ----------
    class _ImageCanvas(QWidget):
        # emit whenever absolute scale changes
        scaleChanged = Signal(float)
        
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setAttribute(Qt.WA_OpaquePaintEvent, True)
            self.setMouseTracking(True)
            self._pixmap = None
            self._img_size = QSize(0,0)
            self._scale = 1.0
            self._fit_scale = 1.0
            self._offset = QPointF(0,0)
            self._dragging = False
            self._drag_start_pos = QPoint()
            self._drag_start_offset = QPointF(0,0)
            self._bg = QColor(16,16,16)
            # crop
            self._crop_mode = False
            self._crop_rect = None
            self._crop_dragging = False
            self._crop_start = QPoint()

        def set_pixmap(self, pm: QPixmap):
            self._pixmap = pm
            self._img_size = pm.size()
            self._offset = QPointF(0,0)
            self._recompute_fit_scale()
            self._scale = self._fit_scale
            # notify about the new scale
            try:
                self.scaleChanged.emit(self._scale)
            except Exception:
                pass            
            self.update()

        def reset_view(self):
            self._offset = QPointF(0,0)
            self._recompute_fit_scale()
            self._scale = self._fit_scale
            try:
                self.scaleChanged.emit(self._scale)
            except Exception:
                pass            
            self.update()
 
        def zoom_to(self, scale: float, anchor_px: QPointF = None):
            """Set absolute scale (not delta). Enforce min = fit. Keep anchor under cursor."""            
            if self._pixmap is None:
                return
            new_scale = max(scale, self._fit_scale * 0.25)
            
            if anchor_px is None:
                anchor_px = QPointF(self.width() / 2.0, self.height() / 2.0)

            img_w, img_h = self._img_size.width(), self._img_size.height()
            if img_w == 0 or img_h == 0:
                return

            ax = (anchor_px.x() - self._offset.x()) / self._scale
            ay = (anchor_px.y() - self._offset.y()) / self._scale

            # Update scale
            self._scale = new_scale
            
            # Recompute offset so the same image point stays under the anchor
            self._offset.setX(anchor_px.x() - ax * self._scale)
            self._offset.setY(anchor_px.y() - ay * self._scale)

            self._clamp_offset()
            # notify subscriber(s) about scale change
            try:
                self.scaleChanged.emit(self._scale)
            except Exception:
                pass            
            self.update()


        def relative_zoom(self, factor: float, anchor_px: QPointF):
            self.zoom_to(self._scale * factor, anchor_px)

        def set_scale_from_slider(self, slider_value: int):
            """
            Slider contract:
              0  -> fit scale
              1..200 -> fit * (1 + value/100 * 4)  (up to 5x fit)
              negative not used (kept simple)
            """            
            if self._pixmap is None:
                return
            if slider_value <= 0:
                self.zoom_to(self._fit_scale)
            else:
                # max ~5x the fit scale
                mult = 1.0 + (slider_value / 100.0) * 4.0
                self.zoom_to(self._fit_scale * mult)


        def paintEvent(self, ev):
            p = QPainter(self)
            p.fillRect(self.rect(), self._bg)
            if self._pixmap and not self._pixmap.isNull():
                p.translate(self._offset)
                p.scale(self._scale, self._scale)
                p.drawPixmap(0,0,self._pixmap.width(), self._pixmap.height(), self._pixmap)
            if self._crop_mode and self._crop_rect:
                p.resetTransform()
                p.setRenderHint(QPainter.Antialiasing)
                p.setPen(QPen(Qt.yellow,2,Qt.DashLine))
                p.drawRect(self._crop_rect)
                p.fillRect(self._crop_rect, QColor(255,255,0,50))
            p.end()

        def resizeEvent(self, ev):
            super().resizeEvent(ev)
            prev_fit = self._fit_scale
            self._recompute_fit_scale()
            if self._scale <= prev_fit + 1e-6:
                self._scale = self._fit_scale
                self._offset = QPointF((self.width()-self._img_size.width()*self._scale)/2.0,
                                       (self.height()-self._img_size.height()*self._scale)/2.0)
            self._clamp_offset()
            self.update()

        def mousePressEvent(self, ev):
            if self._crop_mode:
                if ev.button() == Qt.LeftButton:
                    self._crop_dragging = True
                    self._crop_start = ev.pos()
                    self._crop_rect = QRect(self._crop_start, QSize())
                    self.update()
                return
            if ev.button() == Qt.LeftButton and self._pixmap:
                self._dragging = True
                self._drag_start_pos = ev.pos()
                self._drag_start_offset = QPointF(self._offset)
                self.setCursor(Qt.ClosedHandCursor)

        def mouseMoveEvent(self, ev):
            if self._crop_mode and self._crop_dragging:
                self._crop_rect = QRect(self._crop_start, ev.pos()).normalized()
                self.update()
                return
            if self._dragging and self._pixmap:
                delta = ev.pos() - self._drag_start_pos
                self._offset = QPointF(self._drag_start_offset.x()+delta.x(), self._drag_start_offset.y()+delta.y())
                self._clamp_offset()
                self.update()

        def mouseReleaseEvent(self, ev):
            if self._crop_mode and ev.button() == Qt.LeftButton:
                self._crop_dragging = False
                self.update()
                return
            if ev.button() == Qt.LeftButton and self._dragging:
                self._dragging = False
                self.setCursor(Qt.OpenHandCursor)

        def wheelEvent(self, ev):
            if not self._pixmap:
                return
            steps = ev.angleDelta().y()/120.0
            if steps == 0:
                return
            factor = 1.15 ** steps
            self.relative_zoom(factor, QPointF(ev.position()))
            ev.accept()

        def _recompute_fit_scale(self):
            if not self._pixmap or self._img_size.isEmpty() or self.width()<2 or self.height()<2:
                self._fit_scale = 1.0
                return
            vw, vh = float(self.width()), float(self.height())
            iw, ih = float(self._img_size.width()), float(self._img_size.height())
            self._fit_scale = min(vw/iw, vh/ih)
            self._offset = QPointF((vw - iw*self._fit_scale)/2.0, (vh - ih*self._fit_scale)/2.0)

        def _clamp_offset(self):
            if not self._pixmap:
                return
            vw, vh = float(self.width()), float(self.height())
            iw, ih = float(self._img_size.width())*self._scale, float(self._img_size.height())*self._scale
            if iw <= vw:
                self._offset.setX((vw - iw)/2.0)
            else:
                min_x = vw - iw; max_x = 0.0
                self._offset.setX(min(max(self._offset.x(), min_x), max_x))
            if ih <= vh:
                self._offset.setY((vh - ih)/2.0)
            else:
                min_y = vh - ih; max_y = 0.0
                self._offset.setY(min(max(self._offset.y(), min_y), max_y))

        def enter_crop_mode(self):
            self._crop_mode = True
            self._crop_rect = None
            self.setCursor(Qt.CrossCursor)
            self.update()

        def exit_crop_mode(self):
            self._crop_mode = False
            self._crop_rect = None
            self._crop_dragging = False
            self.setCursor(Qt.OpenHandCursor)
            self.update()

    # ---------- Dialog ----------
    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Photo Viewer")
        self.resize(1200,800)

        self._path = image_path
        self._image_list = []
        self._current_index = 0

        # default light theme (metadata requested black text)
        self._apply_theme("light")
        
        # --- Edit staging state (non-destructive editing) ---
        # _orig_pil is set by _load_image; during edit we create _edit_base_pil
        # which is the editable base (original copy preserved in _orig_pil).
        # _working_pil is the last processed PIL image (preview).
        self._orig_pil = None
        self._edit_base_pil = None
        self._working_pil = None
        self._is_dirty = False  # true if there are unapplied edits

        # metadata cache + preload control
        self._meta_cache = {}
        self._meta_preloader = None
        self._preload_stop = False
        
        # processing state
        self._orig_pil = None      # original PIL Image (RGBA)
        self._working_pil = None   # last processed PIL Image
        self.adjustments = {       # slider values -100..100
            "brightness": 0,
            "exposure": 0,
            "contrast": 0,
            "highlights": 0,
            "shadows": 0,
            "vignette": 0,
            "saturation": 0,
            "warmth": 0
        }
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(50)
        self._apply_timer.timeout.connect(self._apply_adjustments)

        # UI skeleton
        self.stack = QStackedWidget(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0,0,0,0)
        outer.addWidget(self.stack)

        # === Viewer page ===
        viewer = QWidget()
        vbox = QVBoxLayout(viewer)
        vbox.setContentsMargins(8,8,8,8)
        vbox.setSpacing(8)

        # top bar
        self._top = self._build_top_bar()
        vbox.addWidget(self._top, 0)

        # central area
        center = QWidget()
        hbox = QHBoxLayout(center)
        hbox.setContentsMargins(0,0,0,0)
        hbox.setSpacing(0)

        self.canvas = LightboxDialog._ImageCanvas(self)
        self.canvas.setCursor(Qt.OpenHandCursor)
        # keep viewer & editor zoom controls synchronized whenever canvas scale changes
        try:
            self.canvas.scaleChanged.connect(self._on_canvas_scale_changed)
        except Exception:
            pass        
        
        hbox.addWidget(self.canvas, 1)

        # install event filter for canvas (if needed downstream)
        self.canvas.installEventFilter(self)
        
        # meta placeholder (exists independently from editor panel)
        self._meta_placeholder = QWidget()
        self._meta_placeholder.setFixedWidth(0)
        hbox.addWidget(self._meta_placeholder, 0)

        vbox.addWidget(center, 1)

        # bottom bar
        self._bottom = self._build_bottom_bar()
        vbox.addWidget(self._bottom, 0)

        self.stack.addWidget(viewer)

        # editor page
        self.editor_page = self._build_edit_page()
        self.stack.addWidget(self.editor_page)

        # right editor panel (created but not added until edit mode)
        self.right_editor_panel = self._build_right_editor_panel()

        # load image file
        if image_path:
            self._load_image(image_path)

        self._init_navigation_arrows()
        self.stack.setCurrentIndex(0)

    # -------------------------------
    # Build Right-side editor panel
    # -------------------------------
    def _build_right_editor_panel(self) -> QWidget:
        """Construct right-hand editor panel for Light & Color adjustments (collapsible)."""
        panel = QWidget()
        panel.setFixedWidth(380)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12,12,12,12)
        layout.setSpacing(12)

        # Light section
        light_group = CollapsiblePanel("Light")
        # brightness, exposure, contrast, highlights, shadows, vignette
        sliders = [
            ("brightness", "Brightness", "brightness", -100, 100),
            ("exposure", "Exposure", "brightness", -100, 100),
            ("contrast", "Contrast", "contrast", -100, 100),
            ("highlights", "Highlights", "brightness", -100, 100),
            ("shadows", "Shadows", "brightness", -100, 100),
            ("vignette", "Vignette", "crop", -100, 100)
        ]
        for key, label, icon, lo, hi in sliders:
            s = LabeledSlider("", label, lo, hi)
            s.text_label.setText(label)
            s.valueChanged.connect(lambda v, k=key: self._on_adjustment_changed(k, v))
            light_group.addWidget(s)
            # store widget for reset/sync
            setattr(self, f"slider_{key}", s)

        layout.addWidget(light_group)

        # Color section
        color_group = CollapsiblePanel("Color")
        sliders_c = [
            ("saturation", "Saturation", "brightness", -100, 100),
            ("warmth", "Warmth", "brightness", -100, 100)
        ]
        for key,label,icon,lo,hi in sliders_c:
            s = LabeledSlider("", label, lo, hi)
            s.text_label.setText(label)
            s.valueChanged.connect(lambda v, k=key: self._on_adjustment_changed(k, v))
            color_group.addWidget(s)
            setattr(self, f"slider_{key}", s)

        layout.addWidget(color_group)

        # Reset button
        btn_reset = QPushButton("Reset")
        btn_reset.setStyleSheet(self._button_style())
        btn_reset.clicked.connect(self._reset_adjustments)
        layout.addWidget(btn_reset)

        layout.addStretch(1)
        return panel

    def _build_filters_panel(self) -> QWidget:
        """Build a scrollable filters panel with thumbnails and preset filter actions."""
        t = getattr(self, "_palette_tokens", {"panel": "#ffffff", "fg": "#000000", "button_border": "#ddd"})
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)

        # Auto Enhance button
        auto_btn = QPushButton("Auto Enhance")
        auto_btn.setStyleSheet(self._button_style())
        auto_btn.clicked.connect(lambda: self._apply_filter_preset("Auto Enhance"))
        v.addWidget(auto_btn)

        # Grid of presets (thumbnail buttons)
        presets = [
            ("Original", {}),
            ("Punch", {"contrast": 25, "saturation": 20}),
            ("Golden", {"warmth": 30, "saturation": 10}),
            ("Radiate", {"highlights": 20, "contrast": 15}),
            ("Warm Contrast", {"warmth": 20, "contrast": 15}),
            ("Calm", {"saturation": -10, "contrast": -5}),
            ("Cool Light", {"warmth": -15}),
            ("Vivid Cool", {"saturation": 30, "contrast": 20, "warmth": -10}),
            ("Dramatic Cool", {"contrast": 35, "saturation": 10, "warmth": -20}),
            ("B&W", {"saturation": -100}),
            ("B&W Cool", {"saturation": -100, "contrast": 20}),
            ("Film", {"contrast": 10, "saturation": -5, "vignette": 10}),
        ]

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        thumb_size = QSize(96, 72)
        for i, (name, adj) in enumerate(presets):
            btn = QPushButton(name)
            btn.setFixedSize(thumb_size.width() + 24, thumb_size.height() + 24)
            # attach closure with preset data
            btn.clicked.connect(lambda _, n=name, a=adj: self._apply_filter_preset(n, preset_adjustments=a))
            grid.addWidget(btn, i // 2, i % 2)

        v.addLayout(grid)
        v.addStretch(1)

        scroll.setWidget(container)
        w = QWidget()
        w_layout = QVBoxLayout(w)
        w_layout.setContentsMargins(0, 0, 0, 0)
        w_layout.addWidget(scroll)
        w.setMinimumWidth(360)
        return w

    def _on_adjustment_changed(self, key: str, val: int):
        """Store slider value and schedule re-apply of adjustments (debounced)."""
        self.adjustments[key] = int(val)
        # schedule apply (debounced)
        self._apply_timer.start()

    def _reset_adjustments(self):
        for k in self.adjustments.keys():
            self.adjustments[k] = 0
            slider = getattr(self, f"slider_{k}", None)
            if slider:
                slider.setValue(0)
        # restore original
        self._apply_adjustments()

    # -------------------------------
    # Image processing (Pillow)
    # -------------------------------
    
    def _toggle_right_editor_panel(self):
        """Show or hide the right-side adjustments panel inside the editor placeholder; hides filters if open."""
        try:
            if not hasattr(self, "_editor_right_placeholder"):
                return
            ph = self._editor_right_placeholder
            layout = ph.layout()
            visible = ph.width() > 0 and getattr(self, "_current_right_panel", None) == "adjustments"

            if visible:
                # hide
                if layout:
                    while layout.count():
                        item = layout.takeAt(0)
                        w = item.widget()
                        if w:
                            w.setParent(None)
                ph.setFixedWidth(0)
                self._current_right_panel = None
                if hasattr(self, "btn_adjust") and getattr(self.btn_adjust, "setChecked", None):
                    self.btn_adjust.setChecked(False)
            else:
                # if filters currently open, close them first
                if getattr(self, "_current_right_panel", None) == "filters":
                    self._toggle_filters_panel()

                if layout is None:
                    layout = QVBoxLayout(ph)
                    layout.setContentsMargins(0, 0, 0, 0)
                    layout.setSpacing(0)

                while layout.count():
                    item = layout.takeAt(0)
                    if item.widget():
                        item.widget().setParent(None)

                # attach the panel widget (use existing instance)
                if not hasattr(self, "right_editor_panel") or self.right_editor_panel is None:
                    self.right_editor_panel = self._build_right_editor_panel()
                layout.addWidget(self.right_editor_panel)
                ph.setFixedWidth(self.right_editor_panel.minimumWidth() or 360)
                self._current_right_panel = "adjustments"
                if hasattr(self, "btn_adjust") and getattr(self.btn_adjust, "setChecked", None):
                    self.btn_adjust.setChecked(True)
        except Exception as e:
            print(f"[toggle_right_editor_panel] error: {e}")    

    def _apply_adjustments(self):
        """Apply adjustments to the current edit-base (non-destructive) and update canvas preview."""
        src = self._edit_base_pil or self._orig_pil
        if src is None:
            return
        img = src.copy().convert("RGBA")

        # apply exposure -> brightness -> contrast -> highlights/shadows -> saturation -> warmth -> vignette
        exp = self.adjustments.get("exposure", 0)
        if exp != 0:
            exp_factor = 1.0 + (exp / 100.0) * 0.5
            img = ImageEnhance.Brightness(img).enhance(exp_factor)

        bri = self.adjustments.get("brightness", 0)
        if bri != 0:
            bri_factor = 1.0 + (bri / 100.0) * 1.0
            img = ImageEnhance.Brightness(img).enhance(bri_factor)

        ctr = self.adjustments.get("contrast", 0)
        if ctr != 0:
            ctr_factor = 1.0 + (ctr / 100.0) * 1.2
            img = ImageEnhance.Contrast(img).enhance(ctr_factor)

        # Highlights/shadows (approx)
        try:
            luma = img.convert("L")
            highlights_val = self.adjustments.get("highlights", 0)
            if highlights_val != 0:
                def high_map(v):
                    return max(0, min(255, int((v - 128) * 2))) if v > 128 else 0
                mask_h = luma.point(high_map)
                h_factor = 1.0 + (highlights_val / 100.0) * 0.8
                bright_img = ImageEnhance.Brightness(img).enhance(h_factor)
                img = Image.composite(bright_img, img, mask_h)
            shadows_val = self.adjustments.get("shadows", 0)
            if shadows_val != 0:
                def low_map(v):
                    return max(0, min(255, int((128 - v) * 2))) if v < 128 else 0
                mask_s = luma.point(low_map)
                s_factor = 1.0 + (shadows_val / 100.0) * 0.6
                dark_img = ImageEnhance.Brightness(img).enhance(s_factor)
                img = Image.composite(dark_img, img, mask_s)
        except Exception:
            pass

        sat = self.adjustments.get("saturation", 0)
        if sat != 0:
            sat_factor = 1.0 + (sat / 100.0) * 1.5
            img = ImageEnhance.Color(img).enhance(sat_factor)

        warmth = self.adjustments.get("warmth", 0)
        if warmth != 0:
            w = warmth / 100.0
            r_mult = 1.0 + (0.6 * w)
            b_mult = 1.0 - (0.6 * w)
            try:
                r, g, b, a = img.split()
                r = r.point(lambda v: max(0, min(255, int(v * r_mult))))
                b = b.point(lambda v: max(0, min(255, int(v * b_mult))))
                img = Image.merge("RGBA", (r, g, b, a))
            except Exception:
                pass

        vign = self.adjustments.get("vignette", 0)
        if vign != 0:
            strength = abs(vign) / 100.0
            w, h = img.size
            mask = Image.new("L", (w, h), 0)
            cx = w/2.0; cy = h/2.0
            maxrad = ((cx*cx + cy*cy) ** 0.5)
            pix = mask.load()
            for yi in range(h):
                for xi in range(w):
                    dx = xi - cx; dy = yi - cy
                    d = (dx*dx + dy*dy) ** 0.5
                    v = int(255 * min(1.0, max(0.0, (d - (maxrad * (1.0 - 0.6*strength))) / (maxrad * (0.6*strength + 1e-6)))))
                    pix[xi, yi] = v
            dark = Image.new("RGBA", img.size, (0,0,0,int(255 * 0.5 * strength)))
            img = Image.composite(dark, img, mask.convert("L").point(lambda v: v))

        # store working preview and flag dirty if different from edit base
        self._working_pil = img
        # mark as dirty if any adjustment non-zero OR if edit_base differs from orig
        self._is_dirty = any(v != 0 for v in self.adjustments.values()) or (self._edit_base_pil is not None and getattr(self._edit_base_pil, "mode", None) is not None and self._edit_base_pil.tobytes() != self._orig_pil.tobytes() if self._orig_pil is not None else False)

        # update Save action enablement
        if hasattr(self, "_save_action_overwrite") and self._save_action_overwrite:
            self._save_action_overwrite.setEnabled(self._is_dirty and bool(self._path))

        # update canvas preview
        try:
            pm = self._pil_to_qpixmap(self._working_pil)
            self.canvas.set_pixmap(pm)
            self._update_info(pm)
        except Exception as e:
            print("[_apply_adjustments] error:", e)

    def _apply_filter_preset(self, name: str, preset_adjustments: dict = None):
        """Apply a filter preset: set adjustments and re-run pipeline. Name visible in status/log."""
        # For Auto Enhance do a simple heuristic: small contrast + brightness + saturation bump
        if name == "Auto Enhance":
            presets = {"contrast": 10, "brightness": 5, "saturation": 8}
            self.adjustments.update({k: presets.get(k, 0) for k in self.adjustments.keys()})
        elif preset_adjustments is None:
            # "Original" -> reset adjustments to 0
            for k in self.adjustments.keys():
                self.adjustments[k] = 0
        else:
            # update only keys provided
            for k in self.adjustments.keys():
                self.adjustments[k] = int(preset_adjustments.get(k, 0))

        # reflect on sliders if available
        for k, v in self.adjustments.items():
            slider = getattr(self, f"slider_{k}", None)
            if slider:
                slider.blockSignals(True)
                slider.setValue(int(v))
                slider.blockSignals(False)

        # schedule reapply
        self._apply_timer.start()
        # mark dirty
        self._is_dirty = True
        if hasattr(self, "_save_action_overwrite") and self._save_action_overwrite:
            self._save_action_overwrite.setEnabled(bool(self._path))

    def _toggle_filters_panel(self):
        """Show/hide filters panel in the right placeholder; ensure adjustments panel hidden when filters shown."""
        # if right panel placeholder missing, do nothing
        if not hasattr(self, "_editor_right_placeholder"):
            return
        ph = self._editor_right_placeholder
        layout = ph.layout()
        visible = ph.width() > 0 and getattr(self, "_current_right_panel", None) == "filters"

        if visible:
            # hide
            if layout:
                while layout.count():
                    item = layout.takeAt(0)
                    w = item.widget()
                    if w:
                        w.setParent(None)
            ph.setFixedWidth(0)
            self._current_right_panel = None
            if hasattr(self, "btn_filter") and getattr(self.btn_filter, "setChecked", None):
                self.btn_filter.setChecked(False)
        else:
            # ensure adjustments panel is hidden first
            if getattr(self, "_current_right_panel", None) == "adjustments":
                self._toggle_right_editor_panel()
            # mount filters
            if layout is None:
                layout = QVBoxLayout(ph)
                layout.setContentsMargins(0, 0, 0, 0)
                layout.setSpacing(0)
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().setParent(None)
            # create panel if needed
            if not hasattr(self, "right_filters_panel") or self.right_filters_panel is None:
                self.right_filters_panel = self._build_filters_panel()
            layout.addWidget(self.right_filters_panel)
            ph.setFixedWidth(self.right_filters_panel.minimumWidth() or 360)
            self._current_right_panel = "filters"
            if hasattr(self, "btn_filter") and getattr(self.btn_filter, "setChecked", None):
                self.btn_filter.setChecked(True)
            
    # -------------------------------
    # Top/bottom/editor builders (unchanged style but integrated)
    # -------------------------------

    def _build_edit_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10,10,10,10)
        layout.setSpacing(8)

        # === Row 1: Back + Filename ===
        row1 = QHBoxLayout()
        self.btn_back = QToolButton()
        self.btn_back.setIcon(self._icon("prev"))
        self.btn_back.setText("Back")
        self.btn_back.setToolTip("Return to viewer")
        self.btn_back.setStyleSheet(self._button_style())
        self.btn_back.clicked.connect(self._return_to_viewer)

        self.lbl_edit_title = QLabel(os.path.basename(self._path) if self._path else "")
        self.lbl_edit_title.setStyleSheet("color:#333;font-weight:bold;")
        row1.addWidget(self.btn_back)
        row1.addWidget(self.lbl_edit_title)
        row1.addStretch(1)
        layout.addLayout(row1)
        
        # === Row 2: Edit toolbar ===
        row2 = QHBoxLayout()
        
        def make_btn(txt, tip, icon_name=None) -> QToolButton:
            b = QToolButton()
            b.setText(txt)
            b.setToolTip(tip)
            b.setStyleSheet(self._button_style())
            if icon_name:
                b.setIcon(self._icon(icon_name))
            return b

        self.btn_zoom_in = make_btn("+","Zoom in")
        self.btn_zoom_out = make_btn("−","Zoom out")

        # wire the editor toolbar zoom buttons to the shared nudge (adjusts the shared slider)
        self.btn_zoom_in.clicked.connect(lambda: (self._nudge_zoom(+1), self._sync_zoom_controls()))
        self.btn_zoom_out.clicked.connect(lambda: (self._nudge_zoom(-1), self._sync_zoom_controls()))        

        self.edit_zoom_field = QLineEdit("100%")
        self.edit_zoom_field.setFixedWidth(60)
        self.edit_zoom_field.setAlignment(Qt.AlignCenter)
        self.edit_zoom_field.setStyleSheet("color:#000;background:#eee;border:1px solid #ccc;")

        self.btn_reset = make_btn("","Reset","reset")

        # Adjustments toggle (replaces brightness quick button)
        self.btn_adjust = make_btn("", "Adjustments", "brightness")
        self.btn_adjust.setToolTip("Open adjustments panel")
        self.btn_adjust.setCheckable(True)
        self.btn_adjust.clicked.connect(self._toggle_right_editor_panel)
                
        # Filters toggle (replaces contrast quick button)
        self.btn_filter = make_btn("", "Filters", "contrast")
        self.btn_filter.setToolTip("Open filters panel")
        self.btn_filter.setCheckable(True)
        self.btn_filter.clicked.connect(self._toggle_filters_panel)
    
        self.btn_crop = make_btn("","Crop","crop")
        self.btn_crop.setCheckable(True)
        self.btn_crop.toggled.connect(self._toggle_crop_mode)

        # Crop apply/cancel buttons (initially hidden)
        self.btn_crop_apply = make_btn("Apply","Apply crop")
        self.btn_crop_cancel = make_btn("Cancel","Cancel crop")
        self.btn_crop_apply.hide()
        self.btn_crop_cancel.hide()
        self.btn_crop_apply.clicked.connect(self._apply_crop)
        self.btn_crop_cancel.clicked.connect(lambda: (self.canvas.exit_crop_mode(), self._show_crop_controls(False)))

        self.btn_rotate = make_btn("","Rotate clockwise","rotate")
#        self.btn_brightness = make_btn("", "Brightness", "brightness")  # kept for compatibility
#        self.btn_contrast = make_btn("", "Contrast", "contrast")        


        # Save menu (updated elsewhere to provide Save as copy / Save / Copy to clipboard)
        self.btn_save = make_btn("Save options", "Save options", "save")
        self.btn_save.setPopupMode(QToolButton.InstantPopup)
        # menu wiring is done during page construction in previous code
        # Cancel button will both cancel edits and return to viewer
        menu = QMenu(self.btn_save)

        act_save_copy = QAction("Save as copy...", self)
        act_save_over = QAction("Save", self)
        act_copy_clip = QAction("Copy to clipboard", self)

        act_save_copy.triggered.connect(self._save_as_copy)
        act_save_over.triggered.connect(self._save_overwrite)
        act_copy_clip.triggered.connect(self._copy_working_to_clipboard)

        # "Save" should only be enabled when we have an existing path and a dirty working image
        act_save_over.setEnabled(False)

        menu.addAction(act_save_copy)
        menu.addAction(act_save_over)
        menu.addAction(act_copy_clip)
        self.btn_save.setMenu(menu)
        self._save_action_overwrite = act_save_over  # keep reference to enable/disable based on state


        self.btn_cancel = make_btn("","Cancel","cancel")
#        self.btn_cancel.clicked.connect(self._return_to_viewer)
        # Cancel: discard edits and return
        self.btn_cancel.clicked.connect(lambda: (self._cancel_edits(), self._return_to_viewer()))

        # Assemble toolbar
        for w in [
            self.btn_zoom_in,
            self.btn_zoom_out,
            self.edit_zoom_field,
            self.btn_reset,
            self.btn_adjust,
            self.btn_filter,
            self.btn_crop,
            self.btn_crop_apply,
            self.btn_crop_cancel,
            self.btn_rotate,
#            self.btn_brightness,
#            self.btn_contrast,
            self.btn_save,
            self.btn_cancel
        ]:
            row2.addWidget(w)
        row2.addStretch(1)
        layout.addLayout(row2)

        # === Canvas container (shared) + right-side placeholder for editor/filter panels ===
        # Host both canvas container and right placeholder side-by-side so right panel never underlaps canvas.
        self.edit_canvas_container = QWidget()
        edit_lay = QVBoxLayout(self.edit_canvas_container)
        edit_lay.setContentsMargins(0, 0, 0, 0)
        edit_lay.setSpacing(0)
        
        # create a placeholder widget on the right where the adjustments / filters panel will be attached
        self._editor_right_placeholder = QWidget()
        self._editor_right_placeholder.setFixedWidth(0)  # hidden by default
        self._editor_right_placeholder.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        
        # host both in a horizontal row so the right panel sits to the right of the canvas
        content_row = QWidget()
        content_row_layout = QHBoxLayout(content_row)
        content_row_layout.setContentsMargins(0, 0, 0, 0)
        content_row_layout.setSpacing(16)
        content_row_layout.addWidget(self.edit_canvas_container, 1)
        content_row_layout.addWidget(self._editor_right_placeholder, 0)

        layout.addWidget(content_row, 1)
        
        # Create edit_canvas_container inner layout now (this is where the canvas will be reparented on mode switch)
        inner = QVBoxLayout()
        inner.setContentsMargins(8, 8, 8, 8)   # add a small right margin so canvas content doesn't touch placeholder
        inner.setSpacing(0)
        self.edit_canvas_container.setLayout(inner)
    
               
        return page


    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(40)
        h = QHBoxLayout(bar)
        h.setContentsMargins(8,0,8,0)
        h.setSpacing(10)

        self.btn_edit = QPushButton("Edit Photo")
        self.btn_edit.setIcon(self._icon("edit"))
        self.btn_edit.setToolTip("Edit photo")
        self.btn_edit.setStyleSheet(self._button_style())
        self.btn_edit.clicked.connect(self._enter_edit_mode)

        self.btn_rotate = QToolButton()
        self.btn_rotate.setIcon(self._icon("rotate"))
        self.btn_rotate.setToolTip("Rotate clockwise")
        self.btn_rotate.setStyleSheet(self._button_style())
        self.btn_rotate.clicked.connect(self._rotate_image)

        self.btn_more = QToolButton()
        self.btn_more.setText("...")
        self.btn_more.setToolTip("More actions")
        self.btn_more.setStyleSheet(self._button_style())
        self.btn_more.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.btn_more)
        act_save = QAction("💾 Save As...", self)
        act_copy = QAction("📋 Copy to Clipboard", self)
        act_open = QAction("📂 Open in Explorer", self)
        menu.addActions([act_save, act_copy, act_open])
        act_save.triggered.connect(self._save_as)
        act_copy.triggered.connect(self._copy_to_clipboard)
        act_open.triggered.connect(self._open_in_explorer)
        self.btn_more.setMenu(menu)

        self.title_label = QLabel(os.path.basename(self._path) if self._path else "")
        self.title_label.setStyleSheet("color:#000;font-size:14px;")

        h.addWidget(self.btn_edit)
        h.addWidget(self.btn_rotate)
        h.addWidget(self.btn_more)
        h.addStretch(1)
        h.addWidget(self.title_label)
        h.addStretch(2)

        bar.setStyleSheet(self._button_style() + """
            QWidget {
                background-color: rgba(240,240,240,220);
                border-radius: 8px;
            }
        """)
        bar.setGraphicsEffect(QGraphicsDropShadowEffect(blurRadius=12, offset=QPointF(0,2)))
        return bar

    def _build_bottom_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(46)
        h = QHBoxLayout(bar)
        h.setContentsMargins(0,0,0,0)
        h.setSpacing(6)

        self.btn_zoom_minus = QToolButton()
        self.btn_zoom_minus.setText("−")
        self.btn_zoom_minus.setStyleSheet("color:#000; font-size:16px; font-weight:bold;")
        self.btn_zoom_minus.setToolTip("Zoom out")
        self.btn_zoom_minus.clicked.connect(lambda: self._nudge_zoom(-1))

        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(-100,200)
        self.zoom_slider.setValue(0)
        self.zoom_slider.setFixedWidth(120)
        self.zoom_slider.valueChanged.connect(self._on_slider_changed)

        self.btn_zoom_plus = QToolButton()
        self.btn_zoom_plus.setText("+")
        self.btn_zoom_plus.setStyleSheet("color:#000; font-size:16px; font-weight:bold;")
        self.btn_zoom_plus.setToolTip("Zoom in")
        self.btn_zoom_plus.clicked.connect(lambda: self._nudge_zoom(+1))

        self.zoom_combo = QComboBox()
        self.zoom_combo.setEditable(True)
        self.zoom_combo.setFixedWidth(80)
        for z in [10,25,50,75,100,200,300,400,500,600,700,800]:
            self.zoom_combo.addItem(f"{z}%")
        self.zoom_combo.setCurrentText("100%")
        self.zoom_combo.editTextChanged.connect(self._on_zoom_combo_changed)
        self.zoom_combo.activated.connect(lambda _: self._on_zoom_combo_changed())

        self.info_label = QLabel()
        self.info_label.setStyleSheet("color:#000;font-size:12px;")
        self.info_label.setText("🖼️ — × —   💾 — KB")

        self.btn_info_toggle = QToolButton()
        self.btn_info_toggle.setIcon(self._icon("info"))
        self.btn_info_toggle.setToolTip("Show detailed info")
        self.btn_info_toggle.setCheckable(True)
        self.btn_info_toggle.toggled.connect(self._toggle_metadata_panel)
        self.btn_info_toggle.setStyleSheet(self._button_style())

        h.addWidget(self.btn_zoom_minus)
        h.addWidget(self.zoom_slider)
        h.addWidget(self.btn_zoom_plus)
        h.addWidget(self.zoom_combo)
        h.addStretch(1)
        h.addWidget(self.info_label, 0)
        h.addWidget(self.btn_info_toggle, 0)

        bar.setStyleSheet(self._button_style() + """
            QWidget {
                background-color: rgba(240,240,240,220);
                border-radius: 8px;
            }
        """)
        bar.setGraphicsEffect(QGraphicsDropShadowEffect(blurRadius=12, offset=QPointF(0,2)))
        return bar

    # ---------- Metadata panel (polished) ----------
    def _build_metadata_panel(self, data_dict: dict) -> QWidget:
        """
        Metadata panel:
         - black text (as requested)
         - file path wraps over multiple lines
         - copy button on right side for path-like values
        """
        t = getattr(self, "_palette_tokens", {
            "panel": "#ffffff", "fg": "#000000",
            "accent_start": "#0078d4", "button_border": "#dddddd"
        })

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        container = QWidget()
        container.setObjectName("metaContainer")
        vlayout = QVBoxLayout(container)
        vlayout.setContentsMargins(12, 12, 12, 12)
        vlayout.setSpacing(10)

        container.setStyleSheet(f"""
            QWidget#metaContainer {{ background-color: {t['panel']}; }}
            QLabel.metaTitle {{ color: {t['fg']}; font-weight: 700; font-size: 14px; }}
            QLabel.metaKey {{ color: {t['fg']}; font-weight: 600; font-size: 12px; }}
            QLabel.metaVal {{ color: {t['fg']}; font-size: 12px; }}
            QToolButton.metaCopy {{ border: none; padding: 4px; }}
        """)

        # Header
        header = QWidget()
        hh = QHBoxLayout(header)
        hh.setContentsMargins(0, 0, 0, 0)
        hh.setSpacing(8)
        title = QLabel("Info")
        title.setObjectName("metaTitle")
        title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        close_btn = QToolButton()
        close_btn.setAutoRaise(True)
        close_btn.setToolTip("Close")
        try:
            close_btn.setIcon(self._icon("cancel"))
        except Exception:
            close_btn.setText("✖")
        close_btn.clicked.connect(lambda: self.btn_info_toggle.setChecked(False))
        hh.addWidget(title)
        hh.addStretch(1)
        hh.addWidget(close_btn)
        vlayout.addWidget(header)

        # Thumbnail + filename
        top_row = QWidget()
        tr = QHBoxLayout(top_row)
        tr.setContentsMargins(0, 0, 0, 0)
        tr.setSpacing(8)
        thumb_lbl = QLabel()
        thumb_lbl.setFixedSize(72, 72)
        thumb_lbl.setStyleSheet("background: rgba(0,0,0,0.03); border-radius:4px;")
        thumb_lbl.setAlignment(Qt.AlignCenter)
        try:
            if getattr(self.canvas, "_pixmap", None):
                pm = self.canvas._pixmap
                if not pm.isNull():
                    tpm = pm.scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    thumb_lbl.setPixmap(tpm)
        except Exception:
            pass
        name_val = data_dict.get("File", os.path.basename(self._path) if self._path else "")
        name_widget = QWidget()
        nw = QVBoxLayout(name_widget)
        nw.setContentsMargins(0, 0, 0, 0)
        nw.setSpacing(4)
        name_lbl = QLabel(str(name_val))
        name_lbl.setObjectName("metaKey")
        name_lbl.setWordWrap(False)
        name_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        desc_field = QLineEdit()
        desc_field.setPlaceholderText("Add a description")
        desc_field.setFixedHeight(28)
        desc_field.setStyleSheet(f"color: {t['fg']}; background: rgba(0,0,0,0.02); border: 1px solid rgba(0,0,0,0.06); padding-left:6px;")
        nw.addWidget(name_lbl)
        nw.addWidget(desc_field)
        tr.addWidget(thumb_lbl)
        tr.addWidget(name_widget, 1)
        vlayout.addWidget(top_row)

        # Grid rows
        grid = QGridLayout()
        grid.setColumnStretch(0, 0)
        grid.setColumnMinimumWidth(0, 28)
        grid.setColumnMinimumWidth(1, 120)
        grid.setColumnStretch(2, 1)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(12)
        grid.setContentsMargins(0, 8, 0, 0)

        icons = {
            "File": "📄", "Folder": "📁", "Size": "📏", "Modified": "🕓",
            "Created": "🗓️", "Captured": "🕰️", "Camera Make": "🏭",
            "Camera Model": "📷", "Lens": "🔍", "Aperture": "ƒ",
            "Exposure": "⏱", "ISO": "ISO", "Focal Length": "🔭",
            "Software": "💻", "Tags": "🏷️", "Database": "💾", "Info": "•"
        }

        row = 0
        for key, val in data_dict.items():
            if key == "File":
                continue
            icon_text = icons.get(key, "•")
            icon_lbl = QLabel(icon_text)
            icon_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            icon_lbl.setFixedWidth(28)
            key_lbl = QLabel(f"{key}")
            key_lbl.setObjectName("metaKey")
            key_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            val_widget = QWidget()
            vbox = QHBoxLayout(val_widget)
            vbox.setContentsMargins(0, 0, 0, 0)
            vbox.setSpacing(6)
            val_lbl = QLabel(str(val))
            val_lbl.setObjectName("metaVal")
            val_lbl.setWordWrap(True)  # enable wrapping for long paths
            val_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            val_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            vbox.addWidget(val_lbl, 1)
            val_str = str(val)
            if "\\" in val_str or "/" in val_str or ":" in val_str:
                vbox.addStretch(1)
                copy_btn = QToolButton()
                copy_btn.setAutoRaise(True)
                copy_btn.setObjectName("metaCopy")
                copy_btn.setToolTip("Copy to clipboard")
                try:
                    copy_btn.setIcon(self._icon("copy"))
                except Exception:
                    copy_btn.setText("📋")
                copy_btn.clicked.connect(lambda _, s=val_str: QApplication.clipboard().setText(s))
                vbox.addWidget(copy_btn)
            grid.addWidget(icon_lbl, row, 0, Qt.AlignTop)
            grid.addWidget(key_lbl, row, 1, Qt.AlignTop)
            grid.addWidget(val_widget, row, 2, Qt.AlignTop)
            row += 1

        vlayout.addLayout(grid)
        vlayout.addStretch(1)

        scroll.setWidget(container)
        scroll.setMinimumWidth(360)
        scroll.setStyleSheet(f"QScrollArea {{ background-color: {t['panel']}; border-left: 1px solid {t['button_border']}; }}")

        return scroll

    def _toggle_metadata_panel(self, show: bool):
        """Toggle right metadata panel (polished)."""
        if show:
            meta_dict = self._parse_metadata_to_dict(self._get_metadata_text())
            panel = self._build_metadata_panel(meta_dict)
            panel.setMinimumWidth(360)
            panel.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
            self._meta_panel = panel

            layout = self._meta_placeholder.layout()
            if layout is None:
                layout = QVBoxLayout(self._meta_placeholder)
                layout.setContentsMargins(0, 0, 0, 0)
                layout.setSpacing(0)

            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().setParent(None)

            layout.addWidget(panel)
            self._meta_placeholder.setFixedWidth(360)
            self.btn_info_toggle.setToolTip("Hide detailed info")
        else:
            if hasattr(self, "_meta_placeholder"):
                self._meta_placeholder.setFixedWidth(0)
            self.btn_info_toggle.setToolTip("Show detailed info")

    def _get_metadata_text(self):
        if self._path in self._meta_cache:
            return self._meta_cache[self._path]
        text = self._load_metadata(self._path)
        self._meta_cache[self._path] = text
        self._preload_metadata_async()
        return text

    def _parse_metadata_to_dict(self, text: str) -> dict:
        meta = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("⚠️"):
                continue
            line = line.lstrip("•").strip()
            if ":" in line:
                key, val = line.split(":", 1)
                key, val = key.strip(), val.strip()
            else:
                key, val = "Info", line
            key = (
                key.replace("📄", "File")
                   .replace("📁", "Folder")
                   .replace("📏", "Size")
                   .replace("🕓", "Modified")
                   .replace("🗓️", "Created")
                   .replace("🕰️", "Captured")
                   .replace("🏭", "Camera Make")
                   .replace("📷", "Camera Model")
                   .replace("🔍", "Lens")
                   .replace("ƒ", "Aperture")
                   .replace("⏱", "Exposure")
                   .replace("ISO", "ISO")
                   .replace("🔭", "Focal Length")
                   .replace("💻", "Software")
                   .replace("💾", "Database")
                   .replace("🏷️", "Tags")
                   .strip()
            )
            words = key.split()
            key = " ".join(sorted(set(words), key=words.index))
            meta[key] = val
        return meta

    def _load_metadata(self, path: str) -> str:
        lines = []
        base = os.path.basename(path) if path else ""
        folder = os.path.dirname(path) if path else ""
        lines.append(f"📄 {base}")
        lines.append(f"📁 {folder}")
        lines.append("")
        try:
            st = os.stat(path)
            dt_mod = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            dt_crt = datetime.fromtimestamp(st.st_ctime).strftime("%Y-%m-%d %H:%M:%S")
            lines += [
                f"📏 File size: {st.st_size/1024:.1f} KB",
                f"🕓 Modified: {dt_mod}",
                f"🗓️ Created: {dt_crt}"
            ]
        except Exception as e:
            lines.append(f"⚠️ File info error: {e}")
        lines.append("")
        try:
            img = Image.open(path)
            exif = img._getexif() or {}
            if exif:
                exif_tags = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
                wanted = [
                    ("DateTimeOriginal", "🕰️ Captured"),
                    ("Make", "🏭 Camera Make"),
                    ("Model", "📷 Camera Model"),
                    ("LensModel", "🔍 Lens"),
                    ("FNumber", "ƒ Aperture"),
                    ("ExposureTime", "⏱ Exposure"),
                    ("ISOSpeedRatings", "ISO"),
                    ("FocalLength", "🔭 Focal Length"),
                    ("Software", "💻 Processed by"),
                ]
                for key, label in wanted:
                    if key in exif_tags:
                        lines.append(f"{label}: {exif_tags[key]}")
            else:
                lines.append("⚠️ No EXIF metadata found.")
        except Exception:
            lines.append("⚠️ Failed to read EXIF info.")
        lines.append("")
        try:
            db = getattr(self, "_shared_db_instance", None)
            if not db and hasattr(self.parent(), "reference_db"):
                db = self.parent().reference_db
            if db:
                record = db.get_photo_metadata_by_path(path)
                if record:
                    lines.append("💾 Database Metadata:")
                    for k, v in record.items():
                        lines.append(f"   • {k}: {v}")
                else:
                    lines.append("⚠️ No record found in database.")
        except Exception as e:
            lines.append(f"⚠️ DB error: {e}")

        return "\n".join(lines)

    def _preload_metadata_async(self):
        if self._meta_preloader and self._meta_preloader.is_alive():
            return
        from threading import Thread
        def worker():
            try:
                radius = 5
                total = len(self._image_list)
                for offset in range(-radius, radius + 1):
                    if self._preload_stop:
                        break
                    idx = self._current_index + offset
                    if idx < 0 or idx >= total:
                        continue
                    path = self._image_list[idx]
                    if path not in self._meta_cache:
                        text = self._load_metadata(path)
                        self._meta_cache[path] = text
                if len(self._meta_cache) > 2000:
                    to_remove = list(self._meta_cache.keys())[:200]
                    for k in to_remove:
                        self._meta_cache.pop(k, None)
            except Exception as e:
                print(f"[Preload] Error: {e}")
        self._meta_preloader = Thread(target=worker, daemon=True)
        self._meta_preloader.start()

    # -------------------------------
    # Load image and store original PIL image
    # -------------------------------
    def _load_image(self, path: str):
        try:
            img = Image.open(path)
            img = ImageOps.exif_transpose(img).convert("RGBA")
            self._orig_pil = img.copy()
            qimg = ImageQt.ImageQt(img)
            pm = QPixmap.fromImage(qimg)
            self.canvas.set_pixmap(pm)
            self._update_info(pm)
        except Exception as e:
            QMessageBox.warning(self, "Load failed", f"Couldn't load image: {e}")

    def _pil_to_qpixmap(self, pil_img):
        """Convert a Pillow Image (RGBA) to QPixmap safely."""
        try:
            qimg = ImageQt.ImageQt(pil_img.convert("RGBA"))
            return QPixmap.fromImage(qimg)
        except Exception as e:
            print("[_pil_to_qpixmap] conversion failed:", e)
            return QPixmap()
            
    # ---------- Fit / Zoom ----------
    def _on_zoom_combo_changed(self, *_):
        text = self.zoom_combo.currentText().strip().replace("%", "")
        try:
            pct = max(1, min(800, int(text)))
        except ValueError:
            return
        new_scale = self.canvas._fit_scale * (pct / 100.0)
        self.canvas.zoom_to(new_scale)
        self._sync_zoom_controls()

    def _fit_to_window(self):
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(0)
        self.zoom_slider.blockSignals(False)
        self.canvas.reset_view()

    def _on_slider_changed(self, v: int):
        if not self.canvas._pixmap:
            return
        if v == 0:
            self.canvas.zoom_to(self.canvas._fit_scale)
        elif v > 0:
            mult = 1.0 + (v / 200.0) * 4.0
            self.canvas.zoom_to(self.canvas._fit_scale * mult)
        else:
            mult = 1.0 + (v / 100.0) * 0.9
            self.canvas.zoom_to(self.canvas._fit_scale * mult)
        self._sync_zoom_controls()

    def _nudge_zoom(self, direction: int):
        val = self.zoom_slider.value()
        step = 10 * direction
        val = max(self.zoom_slider.minimum(), min(self.zoom_slider.maximum(), val + step))
        self.zoom_slider.setValue(val)
        self._sync_zoom_controls()

    def _sync_zoom_controls(self):
        if not self.canvas._pixmap:
            return
        zoom_pct = int(round(self.canvas._scale / self.canvas._fit_scale * 100))
        zoom_pct = max(1, min(zoom_pct, 800))
        self.zoom_combo.blockSignals(True)
        self.zoom_combo.setCurrentText(f"{zoom_pct}%")
        self.zoom_combo.blockSignals(False)
        rel = self.canvas._scale / self.canvas._fit_scale
        if rel >= 1.0:
            slider_val = int((rel - 1.0) / 4.0 * 200.0)
        else:
            slider_val = int((rel - 1.0) / 0.9 * 100.0)
        slider_val = max(-100, min(200, slider_val))
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(slider_val)
        self.zoom_slider.blockSignals(False)

    def _rotate_image(self):
        """Rotate image clockwise and refresh all metadata + info."""
        if not self.canvas._pixmap:
            return
        tr = QTransform().rotate(90)
        pm = self.canvas._pixmap.transformed(tr)
        self.canvas.set_pixmap(pm)
        self._update_info(pm)
        self._update_titles_and_meta()  # 🔄 refresh title + meta
        self.canvas.update()
        self._refresh_metadata_panel()

    # -------------------------------
    # Navigation arrows (simplified)
    # -------------------------------
    def _init_navigation_arrows(self):
        self.btn_prev = QToolButton(self)
        self.btn_next = QToolButton(self)
        for b,name,tip in [(self.btn_prev,"prev","Previous photo"),(self.btn_next,"next","Next photo")]:
            b.setAutoRaise(True)
            b.setIcon(self._icon(name))
            b.setIconSize(QSize(40,40))
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tip)
            b.setStyleSheet("""
                QToolButton { color: black; background-color: rgba(0,0,0,0.06); border-radius:20px; border:1px solid rgba(0,0,0,0.06); }
                QToolButton:hover { background-color: rgba(0,0,0,0.12); }
            """)
        self.btn_prev.clicked.connect(self._go_prev)
        self.btn_next.clicked.connect(self._go_next)
        QTimer.singleShot(0, self._position_nav_buttons)

    def _position_nav_buttons(self):
        if not hasattr(self, "btn_prev") or not hasattr(self, "canvas"):
            return
        if self.canvas.width()==0 or self.canvas.height()==0:
            QTimer.singleShot(50, self._position_nav_buttons)
            return
        try:
            canvas_tl = self.canvas.mapTo(self, QPoint(0,0))
        except Exception:
            canvas_tl = QPoint(8, self._top.height()+8)
        cw = self.canvas.width(); ch = self.canvas.height()
        btn_w = self.btn_prev.width() or 48; btn_h = self.btn_prev.height() or 48
        y = canvas_tl.y() + (ch//2) - (btn_h//2)
        left_x = canvas_tl.x() + 12
        right_x = canvas_tl.x() + cw - btn_w - 12
        if left_x < 0: left_x = 12
        if right_x + btn_w > self.width(): right_x = max(12, self.width()-btn_w-12)
        self.btn_prev.move(left_x, max(8,y))
        self.btn_next.move(right_x, max(8,y))
        self.btn_prev.show(); self.btn_next.show()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._position_nav_buttons()

    # -------------------------------
    # Enter edit mode -> attach right panel
    # -------------------------------
    def _enter_edit_mode(self):
        """Switch UI to edit mode (reuse main canvas). Prepare edit staging but do not auto-open the panel."""
        # Prepare edit staging
        if self._orig_pil:
            self._edit_base_pil = self._orig_pil.copy()
        else:
            self._edit_base_pil = None
        self._working_pil = self._edit_base_pil.copy() if self._edit_base_pil else None

        # Reset adjustments values & sliders
        for k in self.adjustments.keys():
            self.adjustments[k] = 0
            slider = getattr(self, f"slider_{k}", None)
            if slider:
                slider.blockSignals(True)
                slider.setValue(0)
                slider.blockSignals(False)

        # Show editor view and move canvas into editor container
        self.stack.setCurrentIndex(1)
        if hasattr(self, "edit_canvas_container"):
            container_layout = self.edit_canvas_container.layout()
            if self.canvas.parent() is not self.edit_canvas_container:
                container_layout.addWidget(self.canvas)
        self.canvas.reset_view()

        # Make Save & Cancel visible in toolbar row (they are already created in page)
        if hasattr(self, "btn_save"):
            self.btn_save.show()
        if hasattr(self, "btn_cancel"):
            self.btn_cancel.show()

        # Ensure placeholder exists but keep it collapsed (panel shown via Adjustments button)
        if not hasattr(self, "_editor_right_placeholder"):
            # placeholder will have been created by _build_edit_page; if not, nothing to do
            pass

        # Render the working base as the initial preview
        if self._edit_base_pil:
            pm = self._pil_to_qpixmap(self._edit_base_pil)
            self.canvas.set_pixmap(pm)
            self._update_info(pm)

    def _return_to_viewer(self):
        reply = QMessageBox.question(self, "Return to viewer", "Do you want to save changes before returning?",
                                     QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        if reply == QMessageBox.Cancel:
            return
        if reply == QMessageBox.Yes:
            QMessageBox.information(self, "Saved", "Changes saved.")
        # remove right editor panel (optional) - keep visible but no harm
        viewer_page = self.stack.widget(0)
        viewer_layout = viewer_page.layout()
        center_widget = viewer_layout.itemAt(1).widget() if viewer_layout.count() > 1 else None
        if center_widget:
            canvas_row = center_widget.layout()
        else:
            canvas_row = None

        if canvas_row and self.canvas.parent() is not center_widget:
            canvas_row.insertWidget(0, self.canvas, 1)

        self.stack.setCurrentIndex(0)
        self.canvas.reset_view()

    # ---------- Navigation / list ----------
    def set_image_list(self, image_list, start_index=0):
        self._image_list = list(image_list or [])
        self._current_index = max(0, min(start_index, len(self._image_list) - 1))
        if self._image_list:
            self._path = self._image_list[self._current_index]
            self._load_image(self._path)

    def _update_titles_and_meta(self):
        base = os.path.basename(self._path) if self._path else ""
        self.setWindowTitle(f"Photo Viewer – {base}")
        if hasattr(self, "title_label"):
            self.title_label.setText(base)
        if hasattr(self, "lbl_edit_title"):
            self.lbl_edit_title.setText(base)
        if hasattr(self, "canvas") and self.canvas._pixmap:
            self._update_info(self.canvas._pixmap)
        if hasattr(self, "_meta_panel") and self._meta_panel.isVisible():
            meta_dict = self._parse_metadata_to_dict(self._get_metadata_text())
            new_panel = self._build_metadata_panel(meta_dict)
            layout = self._meta_placeholder.layout()
            if layout:
                while layout.count():
                    item = layout.takeAt(0)
                    if item.widget():
                        item.widget().setParent(None)
                layout.addWidget(new_panel)
                self._meta_panel = new_panel
        if self.stack.currentIndex() == 1 and hasattr(self, "edit_canvas_container"):
            parent_layout = self.edit_canvas_container.layout()
            if self.canvas.parent() is not self.edit_canvas_container:
                parent_layout.addWidget(self.canvas)
            self.canvas.reset_view()

    def _go_prev(self):
        if self._image_list and self._current_index > 0:
            self._current_index -= 1
            self._path = self._image_list[self._current_index]
            self._load_image(self._path)
            self._update_titles_and_meta()
            self._fit_to_window()
            self._refresh_metadata_panel()

    def _go_next(self):
        if self._image_list and self._current_index < len(self._image_list) - 1:
            self._current_index += 1
            self._path = self._image_list[self._current_index]
            self._load_image(self._path)
            self._update_titles_and_meta()
            self._fit_to_window()
            self._refresh_metadata_panel()
    
    # ---------- Utilities ----------
    def _update_info(self, pm: QPixmap):
        kb = round(pm.width() * pm.height() * 4 / 1024)
        self.info_label.setText(f"🖼️ {pm.width()}×{pm.height()}   💾 {kb:,} KB")

    def _toggle_crop_mode(self, enabled):
        if not hasattr(self, "canvas"):
            return
        if enabled:
            self.canvas.enter_crop_mode()
            self._show_crop_controls(True)
        else:
            self.canvas.exit_crop_mode()
            self._show_crop_controls(False)

    # -------------------------------
    # Crop functions (use canvas methods)
    # -------------------------------
    def get_crop_box(self):
        rect = getattr(self.canvas, "_crop_rect", None)
        if not rect or not getattr(self.canvas, "_pixmap", None): return None
        left = rect.left(); top = rect.top(); w = rect.width(); h = rect.height()
        ox = self.canvas._offset.x(); oy = self.canvas._offset.y(); scale = self.canvas._scale
        rx = (left - ox) / scale; ry = (top - oy) / scale
        rw = w / scale; rh = h / scale
        iw = max(1, self.canvas._img_size.width()); ih = max(1, self.canvas._img_size.height())
        x = int(max(0, min(iw-1, round(rx))))
        y = int(max(0, min(ih-1, round(ry))))
        w2 = int(max(1, min(iw-x, round(rw))))
        h2 = int(max(1, min(ih-y, round(rh))))
        return (x,y,w2,h2)

    def _apply_crop(self):
        """Apply crop to the edit base (staged); do NOT write file. Mark dirty."""
        if not hasattr(self.canvas, "_crop_rect") or not self.canvas._crop_rect:
            QMessageBox.warning(self, "Crop", "No crop area selected.")
            return
        if self._edit_base_pil is None:
            QMessageBox.warning(self, "Crop", "No image loaded for editing.")
            return
        box = self.get_crop_box()
        if not box:
            QMessageBox.warning(self, "Crop", "Invalid crop selection.")
            return
        try:
            x, y, w, h = box
            cropped = self._edit_base_pil.crop((x, y, x + w, y + h))
            self._edit_base_pil = cropped
            # after changing base, reapply adjustments to produce preview
            self._apply_adjustments()
            self._is_dirty = True
            # keep crop UI state: exit crop mode and hide buttons
            if hasattr(self.canvas, "exit_crop_mode"):
                self.canvas.exit_crop_mode()
            self._show_crop_controls(False)
        except Exception as e:
            QMessageBox.warning(self, "Crop failed", str(e))

    def _show_crop_controls(self, show: bool):
        if show:
            if hasattr(self, "btn_crop_apply") and hasattr(self, "btn_crop_cancel"):
                self.btn_crop_apply.show(); self.btn_crop_cancel.show()
        else:
            if hasattr(self, "btn_crop_apply"): self.btn_crop_apply.hide()
            if hasattr(self, "btn_crop_cancel"): self.btn_crop_cancel.hide()
            if hasattr(self, "btn_crop") and hasattr(self.btn_crop, "setChecked"):
                self.btn_crop.setChecked(False)

    # -------------------------------
    # Save / other helpers
    # -------------------------------
    def _save_as(self):
        if not self._path: return
        dest, _ = QFileDialog.getSaveFileName(self, "Save As", self._path, "Images (*.png *.jpg *.jpeg *.bmp)")
        if not dest: return
        try:
            img = Image.open(self._path)
            img.save(dest)
            self._path = dest
            QMessageBox.information(self, "Saved", f"Photo saved as:\n{dest}")
            self._update_titles_and_meta()
            self._refresh_metadata_panel()
        except Exception as e:
            QMessageBox.warning(self, "Save failed", f"Error: {e}")

    def _copy_to_clipboard(self):
        if self.canvas._pixmap:
            QApplication.clipboard().setPixmap(self.canvas._pixmap)
            QMessageBox.information(self, "Copied", "Photo copied to clipboard.")

    def _save_as_copy(self):
        """Save working image as a new file (Save as copy)."""
        if not self._working_pil:
            QMessageBox.information(self, "Save as copy", "Nothing to save.")
            return
        dest, _ = QFileDialog.getSaveFileName(self, "Save As Copy", self._path or "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if not dest:
            return
        try:
            self._working_pil.save(dest)
            QMessageBox.information(self, "Saved", f"Saved copy to:\n{dest}")
            # optional: keep edit open; do not overwrite original
            self._is_dirty = False
            if hasattr(self, "_save_action_overwrite") and self._save_action_overwrite:
                self._save_action_overwrite.setEnabled(False)
        except Exception as e:
            QMessageBox.warning(self, "Save failed", f"Error: {e}")

    def _save_overwrite(self):
        """Overwrite the original file with the working image (make edits permanent)."""
        if not self._path:
            QMessageBox.warning(self, "Save", "No original path to save to.")
            return
        if not self._working_pil:
            QMessageBox.information(self, "Save", "Nothing to save.")
            return
        try:
            self._working_pil.save(self._path)
            # update original and edit base to reflect saved file
            self._orig_pil = self._working_pil.copy()
            self._edit_base_pil = self._orig_pil.copy()
            self._is_dirty = False
            if hasattr(self, "_save_action_overwrite") and self._save_action_overwrite:
                self._save_action_overwrite.setEnabled(False)
            QMessageBox.information(self, "Saved", f"Saved changes to:\n{self._path}")
        except Exception as e:
            QMessageBox.warning(self, "Save failed", f"Error: {e}")

    def _copy_working_to_clipboard(self):
        """Copy the working preview to clipboard as a pixmap."""
        if self._working_pil is None:
            QMessageBox.information(self, "Copy", "Nothing to copy.")
            return
        try:
            pm = self._pil_to_qpixmap(self._working_pil)
            QApplication.clipboard().setPixmap(pm)
            QMessageBox.information(self, "Copied", "Working image copied to clipboard.")
        except Exception as e:
            QMessageBox.warning(self, "Copy failed", f"Error: {e}")

    def _cancel_edits(self):
        """Reset staged edits and restore original image in viewer/editor."""
        # discard staged images
        self._edit_base_pil = None
        self._working_pil = None
        # reset adjustments and sliders
        for k in self.adjustments.keys():
            self.adjustments[k] = 0
            slider = getattr(self, f"slider_{k}", None)
            if slider:
                slider.blockSignals(True)
                slider.setValue(0)
                slider.blockSignals(False)
        # restore original image on canvas
        if self._orig_pil:
            pm = self._pil_to_qpixmap(self._orig_pil)
            self.canvas.set_pixmap(pm)
            self._update_info(pm)
        # reset dirty flag and save action
        self._is_dirty = False
        if hasattr(self, "_save_action_overwrite") and self._save_action_overwrite:
            self._save_action_overwrite.setEnabled(False)
        # hide crop controls and uncheck crop toggle
        self._show_crop_controls(False)
        if hasattr(self, "btn_crop") and getattr(self.btn_crop, "setChecked", None):
            self.btn_crop.setChecked(False)
    
    def _open_in_explorer(self):
        if self._path and os.path.exists(self._path):
            subprocess.run(["explorer", "/select,", os.path.normpath(self._path)])

    def closeEvent(self, ev):
        self._preload_stop = True
        super().closeEvent(ev)

    # -------------------------------
    # Metadata helpers (unchanged)
    # -------------------------------
    def _refresh_metadata_panel(self):
        if hasattr(self, "_meta_panel") and self._meta_panel.isVisible():
            try:
                meta_dict = self._parse_metadata_to_dict(self._get_metadata_text())
                new_panel = self._build_metadata_panel(meta_dict)
                layout = self._meta_placeholder.layout()
                if layout:
                    while layout.count():
                        item = layout.takeAt(0)
                        if item.widget(): item.widget().setParent(None)
                    layout.addWidget(new_panel)
                self._meta_panel = new_panel
            except Exception as e:
                print(f"[Metadata Refresh Error] {e}")
                
    def _on_canvas_scale_changed(self, new_scale: float):
        """
        Called when the canvas scale changes (wheel / programmatic).
        Ensure the zoom slider and combo reflect the new scale.
        """
        try:
            # _sync_zoom_controls reads current canvas._scale, so just call it
            self._sync_zoom_controls()
        except Exception:
            pass