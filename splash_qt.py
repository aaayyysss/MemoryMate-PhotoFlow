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
        Perform startup steps — can be expanded later.
        This runs BEFORE MainWindow is shown.
        """
        from reference_db import ReferenceDB
        from thumb_cache_db import get_cache

        try:
            # STEP 1 — DB initialization
            self.progress.emit(10, "Opening database…")
            db = ReferenceDB()
            time.sleep(0.1)  # simulate a brief pause

            # STEP 2 — Ensure indexes and created_* fields
            self.progress.emit(30, "Ensuring database indexes…")
            if hasattr(db, "ensure_created_date_fields"):
                db.ensure_created_date_fields()
            if hasattr(db, "optimize_indexes"):
                db.optimize_indexes()
            
            if self._cancel:
                return

            # STEP 3 — Backfill created_* if needed
            self.progress.emit(50, "Verifying timestamps…")
            try:
                updated = db.single_pass_backfill_created_fields()
                if updated:
                    print(f"[Startup] Backfilled {updated} rows.")
            except Exception as e:
                print(f"[Startup] Backfill skipped: {e}")
            if self._cancel:
                return

            # STEP 4 — Cache cleanup / check
            self.progress.emit(70, "Checking thumbnail cache…")
            cache = get_cache()
            stats = cache.get_stats()
            print(f"[Cache] {stats}")
            if self.settings.get("cache_auto_cleanup", True):
                # optional cleanup policy
                cache.purge_stale(max_age_days=7)
            if self._cancel:
                return

            # STEP 5 — Folder tree preload
            self.progress.emit(90, "Preloading folder tree…")
            if self._cancel: return
            # not strictly required to fetch fully here — just force index creation
            # Sidebar reload happens after MainWindow is shown.

            # Done
            self.progress.emit(100, "Startup complete")
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
