"""
Modern Preferences Dialog with Left Sidebar Navigation

Features:
- Apple/VS Code style left sidebar navigation
- 6 organized sections (General, Appearance, Scanning, Face Detection, Video, Advanced)
- Full i18n translation support
- Responsive layout with minimum 900x600 size
- Top-right Save/Cancel buttons
- Dark mode adaptive styling
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QStackedWidget, QWidget, QLabel, QCheckBox, QComboBox, QLineEdit,
    QTextEdit, QPushButton, QSpinBox, QFormLayout, QGroupBox, QMessageBox,
    QDialogButtonBox, QScrollArea, QFileDialog
)
from PySide6.QtCore import Qt, QSize, QProcess
from PySide6.QtGui import QGuiApplication
import sys
from pathlib import Path

from translation_manager import get_translation_manager, tr
from config.face_detection_config import get_face_config


class PreferencesDialog(QDialog):
    """Modern preferences dialog with sidebar navigation and i18n support."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.face_config = get_face_config()
        self.tm = get_translation_manager()

        # Load current language from settings
        current_lang = self.settings.get("language", "en")
        self.tm.set_language(current_lang)

        self.setWindowTitle(tr("preferences.title"))
        self.setMinimumSize(900, 600)

        # Track original settings for change detection
        self.original_settings = self._capture_settings()

        self._setup_ui()
        self._load_settings()
        self._apply_styling()

    def _setup_ui(self):
        """Create the main UI layout with sidebar navigation."""
        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Left sidebar navigation
        self.sidebar = QListWidget()
        self.sidebar.setMaximumWidth(180)
        self.sidebar.setSpacing(2)
        self.sidebar.setFocusPolicy(Qt.NoFocus)
        self.sidebar.currentRowChanged.connect(self._on_sidebar_changed)

        # Add navigation items
        nav_items = [
            ("preferences.nav.general", "⚙️"),
            ("preferences.nav.appearance", "🎨"),
            ("preferences.nav.scanning", "📁"),
            ("preferences.nav.face_detection", "👤"),
            ("preferences.nav.video", "🎬"),
            ("preferences.nav.advanced", "🔧")
        ]

        for key, icon in nav_items:
            item = QListWidgetItem(f"{icon}  {tr(key)}")
            item.setSizeHint(QSize(160, 40))
            self.sidebar.addItem(item)

        self.sidebar.setCurrentRow(0)

        main_layout.addWidget(self.sidebar)

        # Right side: content area with top button bar
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(20, 10, 20, 10)
        right_layout.setSpacing(10)

        # Top button bar (Save/Cancel)
        button_bar = QHBoxLayout()
        button_bar.addStretch()

        self.btn_cancel = QPushButton(tr("common.cancel"))
        self.btn_cancel.clicked.connect(self._on_cancel)

        self.btn_save = QPushButton(tr("common.save"))
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self._on_save)

        button_bar.addWidget(self.btn_cancel)
        button_bar.addWidget(self.btn_save)

        right_layout.addLayout(button_bar)

        # Stacked widget for content panels
        self.content_stack = QStackedWidget()

        # Create all content panels
        self.content_stack.addWidget(self._create_general_panel())
        self.content_stack.addWidget(self._create_appearance_panel())
        self.content_stack.addWidget(self._create_scanning_panel())
        self.content_stack.addWidget(self._create_face_detection_panel())
        self.content_stack.addWidget(self._create_video_panel())
        self.content_stack.addWidget(self._create_advanced_panel())

        right_layout.addWidget(self.content_stack)

        main_layout.addWidget(right_widget, 1)

    def _create_scrollable_panel(self, content_widget: QWidget) -> QScrollArea:
        """Wrap content in a scrollable area."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content_widget)
        return scroll

    def _create_general_panel(self) -> QWidget:
        """Create General Settings panel."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignTop)
        layout.setSpacing(15)

        # Title
        title = QLabel(tr("preferences.general.title"))
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        layout.addWidget(title)

        # Skip unchanged photos
        self.chk_skip = QCheckBox(tr("preferences.general.skip_unchanged"))
        self.chk_skip.setToolTip(tr("preferences.general.skip_unchanged_hint"))
        layout.addWidget(self.chk_skip)

        # Use EXIF dates
        self.chk_exif = QCheckBox(tr("preferences.general.use_exif_dates"))
        self.chk_exif.setToolTip(tr("preferences.general.use_exif_dates_hint"))
        layout.addWidget(self.chk_exif)

        layout.addStretch()

        return self._create_scrollable_panel(widget)

    def _create_appearance_panel(self) -> QWidget:
        """Create Appearance panel."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignTop)
        layout.setSpacing(15)

        # Title
        title = QLabel(tr("preferences.appearance.title"))
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        layout.addWidget(title)

        # Dark mode
        self.chk_dark = QCheckBox(tr("preferences.appearance.dark_mode"))
        self.chk_dark.setToolTip(tr("preferences.appearance.dark_mode_hint"))
        layout.addWidget(self.chk_dark)

        # Language selector
        lang_group = QGroupBox()
        lang_layout = QFormLayout(lang_group)
        lang_layout.setSpacing(10)

        self.cmb_language = QComboBox()
        self.cmb_language.setToolTip(tr("preferences.appearance.language_hint"))

        # Populate available languages
        for lang_code, lang_name in self.tm.get_available_languages():
            self.cmb_language.addItem(lang_name, lang_code)

        # Set current language
        current_index = self.cmb_language.findData(self.tm.current_language)
        if current_index >= 0:
            self.cmb_language.setCurrentIndex(current_index)

        lang_layout.addRow(tr("preferences.appearance.language") + ":", self.cmb_language)
        layout.addWidget(lang_group)

        # Thumbnail cache
        cache_group = QGroupBox(tr("preferences.cache.title"))
        cache_layout = QVBoxLayout(cache_group)
        cache_layout.setSpacing(10)

        self.chk_cache = QCheckBox(tr("preferences.cache.enabled"))
        self.chk_cache.setToolTip(tr("preferences.cache.enabled_hint"))
        cache_layout.addWidget(self.chk_cache)

        cache_size_layout = QFormLayout()
        self.cmb_cache_size = QComboBox()
        self.cmb_cache_size.setEditable(True)
        self.cmb_cache_size.setToolTip(tr("preferences.cache.size_mb_hint"))
        for size in ["100", "250", "500", "1000", "2000"]:
            self.cmb_cache_size.addItem(size)

        cache_size_layout.addRow(tr("preferences.cache.size_mb") + ":", self.cmb_cache_size)
        cache_layout.addLayout(cache_size_layout)

        self.chk_cache_cleanup = QCheckBox(tr("preferences.cache.auto_cleanup"))
        self.chk_cache_cleanup.setToolTip(tr("preferences.cache.auto_cleanup_hint"))
        cache_layout.addWidget(self.chk_cache_cleanup)

        layout.addWidget(cache_group)

        layout.addStretch()

        return self._create_scrollable_panel(widget)

    def _create_scanning_panel(self) -> QWidget:
        """Create Scanning Settings panel."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignTop)
        layout.setSpacing(15)

        # Title
        title = QLabel(tr("preferences.scanning.title"))
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        layout.addWidget(title)

        # Ignore folders
        ignore_group = QGroupBox(tr("preferences.scanning.ignore_folders"))
        ignore_layout = QVBoxLayout(ignore_group)

        hint_label = QLabel(tr("preferences.scanning.ignore_folders_hint"))
        hint_label.setStyleSheet("color: gray; font-size: 9pt;")
        ignore_layout.addWidget(hint_label)

        self.txt_ignore_folders = QTextEdit()
        self.txt_ignore_folders.setPlaceholderText(tr("preferences.scanning.ignore_folders_placeholder"))
        self.txt_ignore_folders.setMaximumHeight(150)
        ignore_layout.addWidget(self.txt_ignore_folders)

        layout.addWidget(ignore_group)

        layout.addStretch()

        return self._create_scrollable_panel(widget)

    def _create_face_detection_panel(self) -> QWidget:
        """Create Face Detection panel."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignTop)
        layout.setSpacing(15)

        # Title
        title = QLabel(tr("preferences.face_detection.title"))
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        layout.addWidget(title)

        # InsightFace Model Selection
        model_group = QGroupBox("InsightFace Model")
        model_layout = QFormLayout(model_group)
        model_layout.setSpacing(10)

        self.cmb_insightface_model = QComboBox()
        self.cmb_insightface_model.addItem("buffalo_s (Fast, smaller memory)", "buffalo_s")
        self.cmb_insightface_model.addItem("buffalo_l (Balanced, recommended)", "buffalo_l")
        self.cmb_insightface_model.addItem("antelopev2 (Most accurate)", "antelopev2")
        self.cmb_insightface_model.setToolTip(
            "Choose the face detection model:\n"
            "• buffalo_s: Faster, uses less memory\n"
            "• buffalo_l: Best balance (recommended)\n"
            "• antelopev2: Most accurate but slower"
        )
        model_layout.addRow("Model:", self.cmb_insightface_model)

        layout.addWidget(model_group)

        # Detection Settings
        detection_group = QGroupBox("Detection Settings")
        detection_layout = QFormLayout(detection_group)
        detection_layout.setSpacing(10)

        self.spin_min_face_size = QSpinBox()
        self.spin_min_face_size.setRange(10, 100)
        self.spin_min_face_size.setSuffix(" px")
        self.spin_min_face_size.setToolTip("Minimum face size in pixels (smaller = detect smaller/distant faces)")
        detection_layout.addRow("Min Face Size:", self.spin_min_face_size)

        self.spin_confidence = QSpinBox()
        self.spin_confidence.setRange(30, 95)
        self.spin_confidence.setSuffix(" %")
        self.spin_confidence.setToolTip("Minimum confidence threshold (higher = fewer false positives)")
        detection_layout.addRow("Confidence:", self.spin_confidence)

        layout.addWidget(detection_group)

        # Clustering Settings
        cluster_group = QGroupBox("Face Clustering")
        cluster_layout = QFormLayout(cluster_group)
        cluster_layout.setSpacing(10)

        self.spin_cluster_eps = QSpinBox()
        self.spin_cluster_eps.setRange(20, 60)
        self.spin_cluster_eps.setSuffix(" %")
        self.spin_cluster_eps.setToolTip(
            "Clustering threshold (lower = stricter grouping):\n"
            "• 30-35%: Recommended (prevents grouping different people)\n"
            "• <30%: Very strict (may split same person)\n"
            "• >40%: Loose (may group different people)"
        )
        cluster_layout.addRow("Threshold (eps):", self.spin_cluster_eps)

        self.spin_min_samples = QSpinBox()
        self.spin_min_samples.setRange(1, 10)
        self.spin_min_samples.setToolTip("Minimum faces needed to form a cluster")
        cluster_layout.addRow("Min Samples:", self.spin_min_samples)

        self.chk_auto_cluster = QCheckBox("Auto-cluster after face detection scan")
        self.chk_auto_cluster.setToolTip("Automatically group faces after detection completes")
        cluster_layout.addRow("", self.chk_auto_cluster)

        layout.addWidget(cluster_group)

        # Performance Settings
        perf_group = QGroupBox("Performance")
        perf_layout = QFormLayout(perf_group)
        perf_layout.setSpacing(10)

        self.spin_max_workers = QSpinBox()
        self.spin_max_workers.setRange(1, 16)
        self.spin_max_workers.setToolTip("Number of parallel face detection workers")
        perf_layout.addRow("Max Workers:", self.spin_max_workers)

        self.spin_batch_size = QSpinBox()
        self.spin_batch_size.setRange(10, 200)
        self.spin_batch_size.setToolTip("Number of images to process before saving to database")
        perf_layout.addRow("Batch Size:", self.spin_batch_size)

        layout.addWidget(perf_group)

        layout.addStretch()

        return self._create_scrollable_panel(widget)

    def _create_video_panel(self) -> QWidget:
        """Create Video Settings panel."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignTop)
        layout.setSpacing(15)

        # Title
        title = QLabel(tr("preferences.video.title"))
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        layout.addWidget(title)

        # FFprobe path
        ffprobe_group = QGroupBox(tr("preferences.video.ffprobe_path"))
        ffprobe_layout = QVBoxLayout(ffprobe_group)

        hint_label = QLabel(tr("preferences.video.ffprobe_path_hint"))
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet("color: gray; font-size: 9pt; padding-bottom: 5px;")
        ffprobe_layout.addWidget(hint_label)

        path_layout = QHBoxLayout()
        self.txt_ffprobe_path = QLineEdit()
        self.txt_ffprobe_path.setPlaceholderText(tr("preferences.video.ffprobe_path_placeholder"))
        path_layout.addWidget(self.txt_ffprobe_path, 1)

        btn_browse = QPushButton(tr("common.browse"))
        btn_browse.clicked.connect(self._browse_ffprobe)
        path_layout.addWidget(btn_browse)

        btn_test = QPushButton(tr("common.test"))
        btn_test.clicked.connect(self._test_ffprobe)
        path_layout.addWidget(btn_test)

        ffprobe_layout.addLayout(path_layout)

        # Help note
        note_label = QLabel(tr("preferences.video.ffmpeg_note"))
        note_label.setWordWrap(True)
        note_label.setStyleSheet("font-size: 10pt; color: #666; padding: 8px; background: #f0f0f0; border-radius: 4px;")
        ffprobe_layout.addWidget(note_label)

        layout.addWidget(ffprobe_group)

        layout.addStretch()

        return self._create_scrollable_panel(widget)

    def _create_advanced_panel(self) -> QWidget:
        """Create Advanced Settings panel."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignTop)
        layout.setSpacing(15)

        # Title
        title = QLabel(tr("preferences.developer.title"))
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        layout.addWidget(title)

        # Diagnostics
        diag_group = QGroupBox(tr("preferences.diagnostics.title"))
        diag_layout = QVBoxLayout(diag_group)

        self.chk_decoder_warnings = QCheckBox(tr("preferences.diagnostics.decoder_warnings"))
        self.chk_decoder_warnings.setToolTip(tr("preferences.diagnostics.decoder_warnings_hint"))
        diag_layout.addWidget(self.chk_decoder_warnings)

        layout.addWidget(diag_group)

        # Developer tools
        dev_group = QGroupBox(tr("preferences.developer.title"))
        dev_layout = QVBoxLayout(dev_group)

        self.chk_db_debug = QCheckBox(tr("preferences.developer.db_debug"))
        self.chk_db_debug.setToolTip(tr("preferences.developer.db_debug_hint"))
        dev_layout.addWidget(self.chk_db_debug)

        self.chk_sql_echo = QCheckBox(tr("preferences.developer.sql_queries"))
        self.chk_sql_echo.setToolTip(tr("preferences.developer.sql_queries_hint"))
        dev_layout.addWidget(self.chk_sql_echo)

        layout.addWidget(dev_group)

        # Metadata extraction
        meta_group = QGroupBox(tr("preferences.metadata.title"))
        meta_layout = QFormLayout(meta_group)
        meta_layout.setSpacing(10)

        self.spin_workers = QComboBox()
        self.spin_workers.setEditable(True)
        self.spin_workers.setToolTip(tr("preferences.metadata.workers_hint"))
        for workers in ["2", "4", "6", "8", "12"]:
            self.spin_workers.addItem(workers)
        meta_layout.addRow(tr("preferences.metadata.workers") + ":", self.spin_workers)

        self.txt_meta_timeout = QComboBox()
        self.txt_meta_timeout.setEditable(True)
        self.txt_meta_timeout.setToolTip(tr("preferences.metadata.timeout_hint"))
        for timeout in ["4.0", "6.0", "8.0", "12.0"]:
            self.txt_meta_timeout.addItem(timeout)
        meta_layout.addRow(tr("preferences.metadata.timeout") + ":", self.txt_meta_timeout)

        self.txt_meta_batch = QComboBox()
        self.txt_meta_batch.setEditable(True)
        self.txt_meta_batch.setToolTip(tr("preferences.metadata.batch_size_hint"))
        for batch in ["50", "100", "200", "500"]:
            self.txt_meta_batch.addItem(batch)
        meta_layout.addRow(tr("preferences.metadata.batch_size") + ":", self.txt_meta_batch)

        self.chk_meta_auto = QCheckBox(tr("preferences.metadata.auto_run"))
        self.chk_meta_auto.setToolTip(tr("preferences.metadata.auto_run_hint"))
        meta_layout.addRow("", self.chk_meta_auto)

        layout.addWidget(meta_group)

        layout.addStretch()

        return self._create_scrollable_panel(widget)

    def _apply_styling(self):
        """Apply dark/light mode adaptive styling."""
        is_dark = self.settings.get("dark_mode", False)

        if is_dark:
            sidebar_bg = "#2b2b2b"
            sidebar_item_bg = "#3c3c3c"
            sidebar_selected = "#4a90e2"
            content_bg = "#1e1e1e"
            text_color = "#e0e0e0"
        else:
            sidebar_bg = "#f5f5f5"
            sidebar_item_bg = "#ffffff"
            sidebar_selected = "#0078d4"
            content_bg = "#ffffff"
            text_color = "#000000"

        self.sidebar.setStyleSheet(f"""
            QListWidget {{
                background: {sidebar_bg};
                border: none;
                border-right: 1px solid #ccc;
                outline: none;
            }}
            QListWidget::item {{
                background: {sidebar_item_bg};
                color: {text_color};
                padding: 8px;
                margin: 2px 4px;
                border-radius: 4px;
            }}
            QListWidget::item:selected {{
                background: {sidebar_selected};
                color: white;
            }}
            QListWidget::item:hover:!selected {{
                background: {sidebar_item_bg if is_dark else '#e8e8e8'};
            }}
        """)

        self.setStyleSheet(f"""
            QDialog {{
                background: {content_bg};
            }}
            QGroupBox {{
                font-weight: bold;
                border: 1px solid #ccc;
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 10px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }}
        """)

    def _on_sidebar_changed(self, index: int):
        """Handle sidebar navigation changes."""
        self.content_stack.setCurrentIndex(index)

    def _load_settings(self):
        """Load current settings into UI controls."""
        # General
        self.chk_skip.setChecked(self.settings.get("skip_unchanged_photos", False))
        self.chk_exif.setChecked(self.settings.get("use_exif_for_date", True))

        # Appearance
        self.chk_dark.setChecked(self.settings.get("dark_mode", False))
        self.chk_cache.setChecked(self.settings.get("thumbnail_cache_enabled", True))
        self.cmb_cache_size.setCurrentText(str(self.settings.get("cache_size_mb", 500)))
        self.chk_cache_cleanup.setChecked(self.settings.get("cache_auto_cleanup", True))

        # Scanning
        ignore_folders = self.settings.get("ignore_folders", [])
        self.txt_ignore_folders.setPlainText("\n".join(ignore_folders))

        # Face Detection
        model = self.face_config.get("insightface_model", "buffalo_l")
        index = self.cmb_insightface_model.findData(model)
        if index >= 0:
            self.cmb_insightface_model.setCurrentIndex(index)

        self.spin_min_face_size.setValue(self.face_config.get("min_face_size", 20))
        self.spin_confidence.setValue(int(self.face_config.get("confidence_threshold", 0.6) * 100))
        self.spin_cluster_eps.setValue(int(self.face_config.get("clustering_eps", 0.35) * 100))
        self.spin_min_samples.setValue(self.face_config.get("clustering_min_samples", 2))
        self.chk_auto_cluster.setChecked(self.face_config.get("auto_cluster_after_scan", True))
        self.spin_max_workers.setValue(self.face_config.get("max_workers", 4))
        self.spin_batch_size.setValue(self.face_config.get("batch_size", 50))

        # Video
        self.txt_ffprobe_path.setText(self.settings.get("ffprobe_path", ""))

        # Advanced
        self.chk_decoder_warnings.setChecked(self.settings.get("show_decoder_warnings", False))
        self.chk_db_debug.setChecked(self.settings.get("db_debug_logging", False))
        self.chk_sql_echo.setChecked(self.settings.get("show_sql_queries", False))
        self.spin_workers.setCurrentText(str(self.settings.get("meta_workers", 4)))
        self.txt_meta_timeout.setCurrentText(str(self.settings.get("meta_timeout_secs", 8.0)))
        self.txt_meta_batch.setCurrentText(str(self.settings.get("meta_batch", 200)))
        self.chk_meta_auto.setChecked(self.settings.get("auto_run_backfill_after_scan", False))

    def _capture_settings(self) -> dict:
        """Capture current settings for change detection."""
        return {
            "skip_unchanged_photos": self.settings.get("skip_unchanged_photos", False),
            "use_exif_for_date": self.settings.get("use_exif_for_date", True),
            "dark_mode": self.settings.get("dark_mode", False),
            "language": self.settings.get("language", "en"),
            "thumbnail_cache_enabled": self.settings.get("thumbnail_cache_enabled", True),
            "cache_size_mb": self.settings.get("cache_size_mb", 500),
            "cache_auto_cleanup": self.settings.get("cache_auto_cleanup", True),
            "ignore_folders": self.settings.get("ignore_folders", []),
            "insightface_model": self.face_config.get("insightface_model", "buffalo_l"),
            "min_face_size": self.face_config.get("min_face_size", 20),
            "confidence_threshold": self.face_config.get("confidence_threshold", 0.6),
            "clustering_eps": self.face_config.get("clustering_eps", 0.35),
            "clustering_min_samples": self.face_config.get("clustering_min_samples", 2),
            "auto_cluster_after_scan": self.face_config.get("auto_cluster_after_scan", True),
            "face_max_workers": self.face_config.get("max_workers", 4),
            "face_batch_size": self.face_config.get("batch_size", 50),
            "ffprobe_path": self.settings.get("ffprobe_path", ""),
            "show_decoder_warnings": self.settings.get("show_decoder_warnings", False),
            "db_debug_logging": self.settings.get("db_debug_logging", False),
            "show_sql_queries": self.settings.get("show_sql_queries", False),
            "meta_workers": self.settings.get("meta_workers", 4),
            "meta_timeout_secs": self.settings.get("meta_timeout_secs", 8.0),
            "meta_batch": self.settings.get("meta_batch", 200),
            "auto_run_backfill_after_scan": self.settings.get("auto_run_backfill_after_scan", False),
        }

    def _on_cancel(self):
        """Handle cancel button - check for unsaved changes."""
        if self._has_changes():
            reply = QMessageBox.question(
                self,
                tr("preferences.unsaved_changes"),
                tr("preferences.unsaved_changes_message"),
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
            )

            if reply == QMessageBox.Yes:
                self._on_save()
            elif reply == QMessageBox.No:
                self.reject()
            # Cancel = do nothing
        else:
            self.reject()

    def _on_save(self):
        """Save all settings and close dialog."""
        # General
        self.settings.set("skip_unchanged_photos", self.chk_skip.isChecked())
        self.settings.set("use_exif_for_date", self.chk_exif.isChecked())

        # Appearance
        self.settings.set("dark_mode", self.chk_dark.isChecked())
        self.settings.set("thumbnail_cache_enabled", self.chk_cache.isChecked())

        try:
            cache_size = int(self.cmb_cache_size.currentText())
        except ValueError:
            cache_size = 500
        self.settings.set("cache_size_mb", cache_size)

        self.settings.set("cache_auto_cleanup", self.chk_cache_cleanup.isChecked())

        # Language
        selected_lang = self.cmb_language.currentData()
        old_lang = self.settings.get("language", "en")
        if selected_lang != old_lang:
            self.settings.set("language", selected_lang)
            QMessageBox.information(
                self,
                tr("preferences.appearance.restart_required"),
                tr("preferences.appearance.restart_required_message")
            )

        # Scanning
        ignore_list = [x.strip() for x in self.txt_ignore_folders.toPlainText().splitlines() if x.strip()]
        self.settings.set("ignore_folders", ignore_list)

        # Face Detection
        self.face_config.set("insightface_model", self.cmb_insightface_model.currentData())
        self.face_config.set("min_face_size", self.spin_min_face_size.value())
        self.face_config.set("confidence_threshold", self.spin_confidence.value() / 100.0)
        self.face_config.set("clustering_eps", self.spin_cluster_eps.value() / 100.0)
        self.face_config.set("clustering_min_samples", self.spin_min_samples.value())
        self.face_config.set("auto_cluster_after_scan", self.chk_auto_cluster.isChecked())
        self.face_config.set("max_workers", self.spin_max_workers.value())
        self.face_config.set("batch_size", self.spin_batch_size.value())
        print(f"✅ Face detection settings saved: model={self.cmb_insightface_model.currentData()}, "
              f"eps={self.spin_cluster_eps.value()}%, min_samples={self.spin_min_samples.value()}")

        # Video
        ffprobe_path = self.txt_ffprobe_path.text().strip()
        old_ffprobe_path = self.settings.get("ffprobe_path", "")
        self.settings.set("ffprobe_path", ffprobe_path)

        if ffprobe_path != old_ffprobe_path:
            # Clear FFmpeg check flag
            flag_file = Path('.ffmpeg_check_done')
            if flag_file.exists():
                try:
                    flag_file.unlink()
                    print(tr("preferences.video.ffmpeg_path_changed"))
                except Exception as e:
                    print(f"⚠️ Failed to clear FFmpeg check flag: {e}")

            path_display = ffprobe_path if ffprobe_path else tr("preferences.video.ffmpeg_path_system")
            print(tr("preferences.video.ffmpeg_path_configured", path=path_display))

            # Offer to restart
            reply = QMessageBox.question(
                self,
                tr("preferences.video.restart_required"),
                tr("preferences.video.restart_required_message"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )

            if reply == QMessageBox.Yes:
                self.accept()
                print("🔄 Restarting application...")
                QProcess.startDetached(sys.executable, sys.argv)
                QGuiApplication.quit()
                return

        # Advanced
        self.settings.set("show_decoder_warnings", self.chk_decoder_warnings.isChecked())

        if self.settings.get("show_decoder_warnings", False):
            QMessageBox.information(
                self,
                tr("preferences.diagnostics.restart_required"),
                tr("preferences.diagnostics.restart_required_message")
            )
        else:
            QMessageBox.information(
                self,
                tr("preferences.diagnostics.restart_recommended"),
                tr("preferences.diagnostics.restart_recommended_message")
            )

        self.settings.set("db_debug_logging", self.chk_db_debug.isChecked())
        self.settings.set("show_sql_queries", self.chk_sql_echo.isChecked())

        if self.chk_db_debug.isChecked():
            print(tr("preferences.developer.developer_mode_enabled"))

        # Metadata
        self.settings.set("meta_workers", int(self.spin_workers.currentText()))
        self.settings.set("meta_timeout_secs", float(self.txt_meta_timeout.currentText()))
        self.settings.set("meta_batch", int(self.txt_meta_batch.currentText()))
        self.settings.set("auto_run_backfill_after_scan", self.chk_meta_auto.isChecked())

        self.accept()

    def _has_changes(self) -> bool:
        """Check if any settings have been modified."""
        current = {
            "skip_unchanged_photos": self.chk_skip.isChecked(),
            "use_exif_for_date": self.chk_exif.isChecked(),
            "dark_mode": self.chk_dark.isChecked(),
            "language": self.cmb_language.currentData(),
            "thumbnail_cache_enabled": self.chk_cache.isChecked(),
            "cache_size_mb": int(self.cmb_cache_size.currentText()) if self.cmb_cache_size.currentText().isdigit() else 500,
            "cache_auto_cleanup": self.chk_cache_cleanup.isChecked(),
            "ignore_folders": [x.strip() for x in self.txt_ignore_folders.toPlainText().splitlines() if x.strip()],
            "insightface_model": self.cmb_insightface_model.currentData(),
            "min_face_size": self.spin_min_face_size.value(),
            "confidence_threshold": self.spin_confidence.value() / 100.0,
            "clustering_eps": self.spin_cluster_eps.value() / 100.0,
            "clustering_min_samples": self.spin_min_samples.value(),
            "auto_cluster_after_scan": self.chk_auto_cluster.isChecked(),
            "face_max_workers": self.spin_max_workers.value(),
            "face_batch_size": self.spin_batch_size.value(),
            "ffprobe_path": self.txt_ffprobe_path.text().strip(),
            "show_decoder_warnings": self.chk_decoder_warnings.isChecked(),
            "db_debug_logging": self.chk_db_debug.isChecked(),
            "show_sql_queries": self.chk_sql_echo.isChecked(),
            "meta_workers": int(self.spin_workers.currentText()) if self.spin_workers.currentText().isdigit() else 4,
            "meta_timeout_secs": float(self.txt_meta_timeout.currentText()) if self.txt_meta_timeout.currentText().replace('.', '').isdigit() else 8.0,
            "meta_batch": int(self.txt_meta_batch.currentText()) if self.txt_meta_batch.currentText().isdigit() else 200,
            "auto_run_backfill_after_scan": self.chk_meta_auto.isChecked(),
        }

        return current != self.original_settings

    def _browse_ffprobe(self):
        """Browse for ffprobe executable."""
        import platform

        if platform.system() == "Windows":
            filter_str = "Executable Files (*.exe);;All Files (*.*)"
        else:
            filter_str = "All Files (*)"

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select FFprobe Executable",
            "",
            filter_str
        )

        if path:
            self.txt_ffprobe_path.setText(path)

    def _test_ffprobe(self):
        """Test ffprobe executable."""
        import subprocess

        path = self.txt_ffprobe_path.text().strip()
        if not path:
            path = "ffprobe"  # Test system PATH

        try:
            result = subprocess.run(
                [path, '-version'],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                version_line = result.stdout.split('\n')[0] if result.stdout else 'Version info unavailable'
                QMessageBox.information(
                    self,
                    tr("preferences.video.ffprobe_test_success"),
                    tr("preferences.video.ffprobe_test_success_message", version=version_line)
                )
            else:
                QMessageBox.warning(
                    self,
                    tr("preferences.video.ffprobe_test_failed"),
                    tr("preferences.video.ffprobe_test_failed_message",
                       code=result.returncode, error=result.stderr)
                )
        except FileNotFoundError:
            QMessageBox.critical(
                self,
                tr("preferences.video.ffprobe_not_found"),
                tr("preferences.video.ffprobe_not_found_message", path=path)
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                tr("preferences.video.ffprobe_test_error"),
                tr("preferences.video.ffprobe_test_error_message", error=str(e))
            )
