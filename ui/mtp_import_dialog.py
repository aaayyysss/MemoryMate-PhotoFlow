"""
MTP Import Dialog - Simplified import dialog for MTP devices

Phase 1: Simple import workflow
- Show device and folder information
- Import all files or cancel
- Progress indication
- Import to library structure

Usage:
    dialog = MTPImportDialog(
        device_name="A54 von Ammar",
        folder_name="Camera",
        mtp_path="::{...}\\DCIM\\Camera",
        db=db,
        project_id=project_id,
        parent=parent
    )
    if dialog.exec():
        # Files imported successfully
        imported_paths = dialog.imported_paths
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QMessageBox, QCheckBox
)
from PySide6.QtCore import Qt, QThread, Signal
from pathlib import Path
from typing import List
from datetime import datetime


class MTPImportWorker(QThread):
    """Worker thread for importing files from MTP device"""

    progress = Signal(int, int, str)  # current, total, filename
    finished = Signal(list)            # imported paths
    error = Signal(str)                # error message

    def __init__(self, mtp_adapter, mtp_path, device_name, folder_name):
        super().__init__()
        self.mtp_adapter = mtp_adapter
        self.mtp_path = mtp_path
        self.device_name = device_name
        self.folder_name = folder_name
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            print(f"[MTPImportWorker] Starting import from {self.device_name} → {self.folder_name}")

            # Step 1: Enumerate files
            self.progress.emit(0, 100, "Scanning device...")
            media_files = self.mtp_adapter.enumerate_mtp_folder(
                self.mtp_path,
                self.device_name,
                self.folder_name,
                max_files=500
            )

            if not media_files:
                self.error.emit("No media files found in folder")
                return

            if self._cancelled:
                return

            print(f"[MTPImportWorker] Found {len(media_files)} files to import")

            # Step 2: Import files
            imported_paths = self.mtp_adapter.import_selected_files(
                media_files,
                self.device_name,
                self.folder_name,
                import_date=datetime.now()
            )

            if self._cancelled:
                return

            print(f"[MTPImportWorker] ✓ Import complete: {len(imported_paths)} files")
            self.finished.emit(imported_paths)

        except Exception as e:
            print(f"[MTPImportWorker] ERROR: {e}")
            import traceback
            traceback.print_exc()
            self.error.emit(str(e))


class MTPImportDialog(QDialog):
    """Simple import dialog for MTP devices (Phase 1)"""

    def __init__(
        self,
        device_name: str,
        folder_name: str,
        mtp_path: str,
        db,
        project_id: int,
        parent=None
    ):
        super().__init__(parent)
        self.device_name = device_name
        self.folder_name = folder_name
        self.mtp_path = mtp_path
        self.db = db
        self.project_id = project_id
        self.imported_paths = []

        self.setWindowTitle(f"Import from {device_name}")
        self.setMinimumSize(500, 300)
        self.setModal(True)

        self._init_ui()

    def _init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(20)

        # Header
        header = QLabel(f"<h2>📱 Import from Device</h2>")
        layout.addWidget(header)

        # Device info
        info_layout = QVBoxLayout()
        info_layout.setSpacing(8)

        device_label = QLabel(f"<b>Device:</b> {self.device_name}")
        folder_label = QLabel(f"<b>Folder:</b> {self.folder_name}")

        info_layout.addWidget(device_label)
        info_layout.addWidget(folder_label)
        layout.addLayout(info_layout)

        # Description
        desc = QLabel(
            "Photos and videos will be imported to your library.\n"
            f"They will be organized in: Imported_Devices/{self.device_name}/{self.folder_name}/"
        )
        desc.setStyleSheet("color: #666; font-size: 12px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Options
        options_layout = QVBoxLayout()
        options_layout.setSpacing(4)

        self.skip_duplicates_checkbox = QCheckBox("Skip files already in library")
        self.skip_duplicates_checkbox.setChecked(True)
        self.skip_duplicates_checkbox.setToolTip("Skip files with same name and date")

        self.face_detection_checkbox = QCheckBox("Run face detection after import")
        self.face_detection_checkbox.setChecked(True)

        options_layout.addWidget(self.skip_duplicates_checkbox)
        options_layout.addWidget(self.face_detection_checkbox)
        layout.addLayout(options_layout)

        layout.addStretch()

        # Progress bar (hidden initially)
        self.progress_bar = QProgressBar()
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        # Progress label
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("font-size: 11px; color: #666;")
        self.progress_label.hide()
        layout.addWidget(self.progress_label)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.import_btn = QPushButton("Import All Files")
        self.import_btn.setDefault(True)
        self.import_btn.setStyleSheet("""
            QPushButton {
                background-color: #0078d4;
                color: white;
                padding: 8px 24px;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #005a9e;
            }
            QPushButton:disabled {
                background-color: #ccc;
            }
        """)
        self.import_btn.clicked.connect(self._start_import)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)

        button_layout.addWidget(self.import_btn)
        button_layout.addWidget(self.cancel_btn)

        layout.addLayout(button_layout)

    def _start_import(self):
        """Start import process"""
        # Confirm import
        reply = QMessageBox.question(
            self,
            "Confirm Import",
            f"Import all photos and videos from {self.folder_name}?\n\n"
            "This may take several minutes depending on the number of files.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )

        if reply != QMessageBox.Yes:
            return

        # Disable UI
        self.import_btn.setEnabled(False)
        self.cancel_btn.setText("Cancel")

        # Show progress
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.progress_label.show()
        self.progress_label.setText("Starting import...")

        # Create MTP adapter
        from services.mtp_import_adapter import MTPImportAdapter
        adapter = MTPImportAdapter(self.db, self.project_id)

        # Create and start worker
        self.worker = MTPImportWorker(
            adapter,
            self.mtp_path,
            self.device_name,
            self.folder_name
        )

        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)

        self.worker.start()

    def _on_progress(self, current: int, total: int, message: str):
        """Handle progress update"""
        if total > 0:
            percent = int((current / total) * 100)
            self.progress_bar.setValue(percent)
        self.progress_label.setText(message)

    def _on_finished(self, imported_paths: List[str]):
        """Handle import completion"""
        self.progress_bar.hide()
        self.progress_label.hide()

        self.imported_paths = imported_paths

        if imported_paths:
            QMessageBox.information(
                self,
                "Import Complete",
                f"Successfully imported {len(imported_paths)} file(s)!\n\n"
                "Photos will now appear in all relevant branches:\n"
                "• All Photos\n"
                "• By Dates\n"
                f"• Folders → {self.folder_name} [{self.device_name}]"
            )
            self.accept()
        else:
            QMessageBox.warning(
                self,
                "No Files Imported",
                "No files were imported. They may already be in your library."
            )
            self.reject()

    def _on_error(self, error_msg: str):
        """Handle import error"""
        self.progress_bar.hide()
        self.progress_label.hide()

        QMessageBox.critical(
            self,
            "Import Error",
            f"Import failed:\n\n{error_msg}"
        )

        # Re-enable UI
        self.import_btn.setEnabled(True)
        self.cancel_btn.setText("Cancel")

    def closeEvent(self, event):
        """Handle dialog close"""
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(2000)
        event.accept()
