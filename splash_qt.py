# splash_qt.py
# Version 1.1 dated 20251020
#— Startup splash screen and background initialization
# ---------------------------------------------

import time
from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QApplication,
    QPushButton, QHBoxLayout
)

# ===============================================
# 🧠 Worker: does DB / cache / index init in background
# ===============================================
class StartupWorker(QThread):
    progress = Signal(int, str)   # percent, message
    finished = Signal(bool)       # success/failure

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        """
        Perform early startup steps BEFORE MainWindow is created.
        Covers: database, cache, translations, services initialization.
        """
        from reference_db import ReferenceDB
        from thumb_cache_db import get_cache

        try:
            # STEP 1 — Initial setup (5%)
            self.progress.emit(5, "Initializing application…")
            time.sleep(0.05)
            if self._cancel:
                return

            # STEP 2 — DB initialization (15%)
            self.progress.emit(15, "Opening database…")
            db = ReferenceDB()
            time.sleep(0.05)
            if self._cancel:
                return

            # STEP 3 — Verify database schema (30%)
            self.progress.emit(30, "Verifying database schema…")
            # NOTE: Schema creation and migrations are now handled automatically
            # by repository.DatabaseConnection during ReferenceDB initialization.
            print("[Startup] Database schema initialized successfully")

            # Optimize indexes if method exists (optional performance tuning)
            if hasattr(db, "optimize_indexes"):
                db.optimize_indexes()

            if self._cancel:
                return

            # STEP 4 — Backfill created_* if needed (45%)
            self.progress.emit(45, "Verifying timestamps…")
            try:
                updated = db.single_pass_backfill_created_fields()
                if updated:
                    print(f"[Startup] Backfilled {updated} rows.")
            except Exception as e:
                print(f"[Startup] Backfill skipped: {e}")
            if self._cancel:
                return

            # STEP 5 — Cache initialization (55%)
            self.progress.emit(55, "Initializing thumbnail cache…")
            cache = get_cache()
            stats = cache.get_stats()
            print(f"[Cache] {stats}")
            if self.settings.get("cache_auto_cleanup", True):
                cache.purge_stale(max_age_days=7)
            if self._cancel:
                return

            # STEP 6 — Initialize SearchService (65%)
            self.progress.emit(65, "Initializing search service…")
            try:
                from services import SearchService
                search_service = SearchService()
                print("[Startup] SearchService initialized")
            except Exception as e:
                print(f"[Startup] SearchService initialization failed: {e}")
            if self._cancel:
                return

            # STEP 7 — Initialize ThumbnailService (75%)
            self.progress.emit(75, "Initializing thumbnail service…")
            try:
                from services import get_thumbnail_service
                thumb_service = get_thumbnail_service()
                print("[Startup] ThumbnailService initialized")
            except Exception as e:
                print(f"[Startup] ThumbnailService initialization failed: {e}")
            if self._cancel:
                return

            # Done with background initialization
            # MainWindow creation happens next (on main thread)
            self.progress.emit(80, "Preparing main window…")
            self.finished.emit(True)

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.finished.emit(False)

# ===============================================
# 🌅 Splash Screen UI
# ===============================================
class SplashScreen(QDialog):
    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Dialog)
        self.setModal(True)
        self.setWindowTitle("MemoryMate PhotoFlow— Loading…")
        self.setFixedSize(400, 250)
        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e1e;
                border-radius: 8px;
            }
            QLabel {
                color: #ffffff;
                font-size: 12pt;
            }
            QPushButton {
                background-color: #444;
                color: white;
                border: none;
                padding: 6px 12px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #666;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # Optional logo
        logo = QLabel()
        pixmap = QPixmap("MemoryMate-PhotoFlow-logo.png")  # optional logo file
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaled(120, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            logo.setAlignment(Qt.AlignCenter)
            layout.addWidget(logo)

        self.status_label = QLabel("Starting up…")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        layout.addWidget(self.progress_bar)
        
        # Cancel button row
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        btn_row.addWidget(self.cancel_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)


    def update_progress(self, percent: int, message: str):
        self.progress_bar.setValue(percent)
        self.status_label.setText(message)
        QApplication.processEvents()
