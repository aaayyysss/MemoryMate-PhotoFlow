"""
People Manager Dialog
Enterprise-grade face management UI inspired by Google Photos, Apple Photos, and Microsoft Photos.

Features:
- Grid view with face thumbnails
- Name labeling and editing
- Merge/split clusters
- Add/remove faces from clusters
- Search by person name
- Face count badges
- Representative face selection
"""

import os
from pathlib import Path
from typing import List, Dict, Any, Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QLineEdit, QScrollArea, QWidget, QFrame,
    QMessageBox, QInputDialog, QMenu, QSizePolicy, QToolBar,
    QComboBox, QSpinBox
)
from PySide6.QtCore import Qt, Signal, QSize, QTimer
from PySide6.QtGui import QPixmap, QImage, QAction, QIcon

from reference_db import ReferenceDB


class FaceClusterCard(QFrame):
    """Card widget displaying a face cluster (person)."""

    clicked = Signal(str)  # Emits branch_key
    renamed = Signal(str, str)  # Emits (branch_key, new_name)
    merge_requested = Signal(str)  # Emits branch_key
    delete_requested = Signal(str)  # Emits branch_key

    def __init__(self, cluster_data: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.cluster_data = cluster_data
        self.branch_key = cluster_data["branch_key"]

        self.setup_ui()
        self.setFrameStyle(QFrame.Box | QFrame.Raised)
        self.setLineWidth(1)
        self.setCursor(Qt.PointingHandCursor)

    def setup_ui(self):
        """Setup the card UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # Face thumbnail
        self.thumbnail_label = QLabel()
        self.thumbnail_label.setFixedSize(128, 128)
        self.thumbnail_label.setScaledContents(True)
        self.thumbnail_label.setStyleSheet("QLabel { background-color: #f0f0f0; border: 1px solid #ccc; }")

        # Load thumbnail
        self.load_thumbnail()

        layout.addWidget(self.thumbnail_label, alignment=Qt.AlignCenter)

        # Name label (editable on double-click)
        name = self.cluster_data.get("display_name", "Unknown")
        self.name_label = QLabel(name)
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setWordWrap(True)
        self.name_label.setStyleSheet("QLabel { font-weight: bold; }")
        layout.addWidget(self.name_label)

        # Count badge
        count = self.cluster_data.get("member_count", 0)
        self.count_label = QLabel(f"{count} photo{'s' if count != 1 else ''}")
        self.count_label.setAlignment(Qt.AlignCenter)
        self.count_label.setStyleSheet("QLabel { color: #666; font-size: 11px; }")
        layout.addWidget(self.count_label)

        # Make card slightly elevated on hover
        self.setStyleSheet("""
            FaceClusterCard {
                background-color: white;
                border-radius: 8px;
            }
            FaceClusterCard:hover {
                background-color: #f8f8f8;
                border: 2px solid #4CAF50;
            }
        """)

    def load_thumbnail(self):
        """Load face thumbnail from crop path or representative."""
        rep_path = self.cluster_data.get("rep_path")

        if rep_path and os.path.exists(rep_path):
            pixmap = QPixmap(rep_path)
            if not pixmap.isNull():
                pixmap = pixmap.scaled(
                    128, 128,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
                self.thumbnail_label.setPixmap(pixmap)
                return

        # Use PNG blob if available
        rep_thumb_png = self.cluster_data.get("rep_thumb_png")
        if rep_thumb_png:
            image = QImage.fromData(rep_thumb_png)
            if not image.isNull():
                pixmap = QPixmap.fromImage(image)
                pixmap = pixmap.scaled(
                    128, 128,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
                self.thumbnail_label.setPixmap(pixmap)
                return

        # Fallback: show placeholder
        self.thumbnail_label.setText("No\nImage")
        self.thumbnail_label.setAlignment(Qt.AlignCenter)

    def mousePressEvent(self, event):
        """Handle mouse click."""
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.branch_key)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        """Handle double-click to rename."""
        if event.button() == Qt.LeftButton:
            self.rename_person()
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        """Show context menu on right-click."""
        menu = QMenu(self)

        rename_action = QAction("✏️ Rename", self)
        rename_action.triggered.connect(self.rename_person)
        menu.addAction(rename_action)

        merge_action = QAction("🔗 Merge with...", self)
        merge_action.triggered.connect(lambda: self.merge_requested.emit(self.branch_key))
        menu.addAction(merge_action)

        menu.addSeparator()

        delete_action = QAction("🗑️ Delete", self)
        delete_action.triggered.connect(lambda: self.delete_requested.emit(self.branch_key))
        menu.addAction(delete_action)

        menu.exec(event.globalPos())

    def rename_person(self):
        """Rename this person."""
        current_name = self.cluster_data.get("display_name", "")

        new_name, ok = QInputDialog.getText(
            self,
            "Rename Person",
            "Enter person's name:",
            text=current_name
        )

        if ok and new_name and new_name != current_name:
            self.name_label.setText(new_name)
            self.cluster_data["display_name"] = new_name
            self.renamed.emit(self.branch_key, new_name)


class PeopleManagerDialog(QDialog):
    """Main dialog for managing face clusters (people)."""

    def __init__(self, project_id: int, parent=None):
        super().__init__(parent)
        self.project_id = project_id
        self.db = ReferenceDB()
        self.clusters: List[Dict[str, Any]] = []
        self.filtered_clusters: List[Dict[str, Any]] = []
        self.cards: Dict[str, FaceClusterCard] = {}

        self.setWindowTitle(f"People - Project {project_id}")
        self.resize(900, 700)

        self.setup_ui()
        self.load_clusters()

    def setup_ui(self):
        """Setup the user interface."""
        layout = QVBoxLayout(self)

        # Toolbar
        toolbar = self.create_toolbar()
        layout.addWidget(toolbar)

        # Search bar
        search_layout = QHBoxLayout()

        search_label = QLabel("🔍 Search:")
        search_layout.addWidget(search_label)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search people by name...")
        self.search_input.textChanged.connect(self.filter_clusters)
        search_layout.addWidget(self.search_input)

        # Sort dropdown
        sort_label = QLabel("Sort by:")
        search_layout.addWidget(sort_label)

        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Most photos", "Name (A-Z)", "Recently added"])
        self.sort_combo.currentTextChanged.connect(self.sort_clusters)
        search_layout.addWidget(self.sort_combo)

        layout.addLayout(search_layout)

        # Scroll area with grid of face cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setSpacing(12)
        self.grid_layout.setContentsMargins(12, 12, 12, 12)

        scroll.setWidget(self.grid_widget)
        layout.addWidget(scroll)

        # Status bar
        status_layout = QHBoxLayout()

        self.status_label = QLabel()
        status_layout.addWidget(self.status_label)

        status_layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        status_layout.addWidget(close_btn)

        layout.addLayout(status_layout)

    def create_toolbar(self) -> QToolBar:
        """Create toolbar with actions."""
        toolbar = QToolBar()
        toolbar.setIconSize(QSize(24, 24))

        # Refresh action
        refresh_action = QAction("🔄 Refresh", self)
        refresh_action.triggered.connect(self.load_clusters)
        toolbar.addAction(refresh_action)

        toolbar.addSeparator()

        # Run face detection action
        detect_action = QAction("🔍 Detect Faces", self)
        detect_action.triggered.connect(self.run_face_detection)
        toolbar.addAction(detect_action)

        # Recluster action
        cluster_action = QAction("🔗 Recluster", self)
        cluster_action.triggered.connect(self.recluster_faces)
        toolbar.addAction(cluster_action)

        toolbar.addSeparator()

        # Settings action
        settings_action = QAction("⚙️ Settings", self)
        settings_action.triggered.connect(self.open_settings)
        toolbar.addAction(settings_action)

        return toolbar

    def load_clusters(self):
        """Load face clusters from database."""
        try:
            self.clusters = self.db.get_face_clusters(self.project_id)
            self.filtered_clusters = self.clusters.copy()
            self.sort_clusters()
            self.update_grid()
            self.update_status()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load face clusters:\n{str(e)}")

    def update_grid(self):
        """Update the grid with face cards."""
        # Clear existing cards
        for i in reversed(range(self.grid_layout.count())):
            widget = self.grid_layout.itemAt(i).widget()
            if widget:
                widget.deleteLater()

        self.cards.clear()

        # Add cards in grid
        cols = 4  # Number of columns
        for i, cluster in enumerate(self.filtered_clusters):
            row = i // cols
            col = i % cols

            card = FaceClusterCard(cluster, self)
            card.clicked.connect(self.on_cluster_clicked)
            card.renamed.connect(self.on_cluster_renamed)
            card.merge_requested.connect(self.on_merge_requested)
            card.delete_requested.connect(self.on_delete_requested)

            self.grid_layout.addWidget(card, row, col)
            self.cards[cluster["branch_key"]] = card

        # Update layout
        self.grid_widget.updateGeometry()

    def filter_clusters(self, query: str):
        """Filter clusters by search query."""
        query = query.lower().strip()

        if not query:
            self.filtered_clusters = self.clusters.copy()
        else:
            self.filtered_clusters = [
                c for c in self.clusters
                if query in c.get("display_name", "").lower()
            ]

        self.update_grid()
        self.update_status()

    def sort_clusters(self):
        """Sort clusters based on selected criterion."""
        sort_by = self.sort_combo.currentText()

        if sort_by == "Most photos":
            self.filtered_clusters.sort(key=lambda c: c.get("member_count", 0), reverse=True)
        elif sort_by == "Name (A-Z)":
            self.filtered_clusters.sort(key=lambda c: c.get("display_name", "").lower())
        elif sort_by == "Recently added":
            # Assuming branch_key contains timestamp info or use id
            self.filtered_clusters.sort(key=lambda c: c.get("branch_key", ""), reverse=True)

        self.update_grid()

    def update_status(self):
        """Update status label."""
        total = len(self.clusters)
        shown = len(self.filtered_clusters)

        if shown == total:
            self.status_label.setText(f"👥 {total} people")
        else:
            self.status_label.setText(f"👥 Showing {shown} of {total} people")

    def on_cluster_clicked(self, branch_key: str):
        """Handle cluster card click."""
        # Show photos for this person
        try:
            paths = self.db.get_paths_for_cluster(self.project_id, branch_key)

            if paths:
                # Load photos in main grid
                if self.parent() and hasattr(self.parent(), "grid"):
                    grid = self.parent().grid
                    grid.model.clear()
                    grid.load_custom_paths(paths, content_type="photos")

                    # Update status
                    cluster_name = next(
                        (c["display_name"] for c in self.clusters if c["branch_key"] == branch_key),
                        "Unknown"
                    )
                    self.parent().statusBar().showMessage(f"👤 Showing {len(paths)} photos of {cluster_name}")

                # Close dialog to show photos
                self.accept()
            else:
                QMessageBox.information(self, "No Photos", f"No photos found for this person.")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load photos:\n{str(e)}")

    def on_cluster_renamed(self, branch_key: str, new_name: str):
        """Handle cluster rename."""
        try:
            # Update database
            with self.db._connect() as conn:
                cur = conn.cursor()
                cur.execute("""
                    UPDATE face_branch_reps
                    SET label = ?
                    WHERE project_id = ? AND branch_key = ?
                """, (new_name, self.project_id, branch_key))

                # Also update branches table
                cur.execute("""
                    UPDATE branches
                    SET display_name = ?
                    WHERE project_id = ? AND branch_key = ?
                """, (new_name, self.project_id, branch_key))

                conn.commit()

            # Update local data
            for cluster in self.clusters:
                if cluster["branch_key"] == branch_key:
                    cluster["display_name"] = new_name
                    break

            print(f"[PeopleManager] Renamed {branch_key} to '{new_name}'")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to rename person:\n{str(e)}")

    def on_merge_requested(self, source_branch_key: str):
        """Handle merge request."""
        # Get list of other clusters
        other_clusters = [
            c for c in self.clusters
            if c["branch_key"] != source_branch_key
        ]

        if not other_clusters:
            QMessageBox.information(self, "Merge", "No other people to merge with.")
            return

        # Show selection dialog
        names = [c.get("display_name", c["branch_key"]) for c in other_clusters]
        target_name, ok = QInputDialog.getItem(
            self,
            "Merge People",
            "Select person to merge with:",
            names,
            editable=False
        )

        if ok and target_name:
            # Find target cluster
            target_cluster = next(
                (c for c in other_clusters if c.get("display_name") == target_name),
                None
            )

            if target_cluster:
                self.merge_clusters(source_branch_key, target_cluster["branch_key"])

    def merge_clusters(self, source_key: str, target_key: str):
        """Merge two face clusters."""
        try:
            # Confirm merge
            source_name = next((c["display_name"] for c in self.clusters if c["branch_key"] == source_key), "Unknown")
            target_name = next((c["display_name"] for c in self.clusters if c["branch_key"] == target_key), "Unknown")

            reply = QMessageBox.question(
                self,
                "Confirm Merge",
                f"Merge '{source_name}' into '{target_name}'?\n\n"
                f"This will move all faces from '{source_name}' to '{target_name}'.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if reply != QMessageBox.Yes:
                return

            # Perform merge in database
            moved = self.db.merge_face_branches(self.project_id, source_key, target_key, keep_label=target_name)

            # Reload clusters
            self.load_clusters()

            QMessageBox.information(self, "Merge Complete", f"Merged {moved} faces into '{target_name}'.")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to merge clusters:\n{str(e)}")

    def on_delete_requested(self, branch_key: str):
        """Handle delete request."""
        cluster_name = next((c["display_name"] for c in self.clusters if c["branch_key"] == branch_key), "Unknown")

        reply = QMessageBox.question(
            self,
            "Delete Person",
            f"Delete '{cluster_name}'?\n\n"
            f"This will remove all face crops and clustering data for this person.\n"
            f"Original photos will not be affected.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            try:
                self.db.delete_branch(self.project_id, branch_key)
                self.load_clusters()
                QMessageBox.information(self, "Deleted", f"Deleted '{cluster_name}'.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete person:\n{str(e)}")

    def run_face_detection(self):
        """Run face detection worker."""
        try:
            from config.face_detection_config import get_face_config
            from workers.face_detection_worker import FaceDetectionWorker

            config = get_face_config()

            if not config.is_enabled():
                reply = QMessageBox.question(
                    self,
                    "Face Detection Disabled",
                    "Face detection is currently disabled.\n\nWould you like to enable it and continue?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )

                if reply == QMessageBox.Yes:
                    config.set("enabled", True)
                else:
                    return

            # Run worker
            worker = FaceDetectionWorker(self.project_id)
            stats = worker.run()

            QMessageBox.information(
                self,
                "Face Detection Complete",
                f"Detection completed!\n\n"
                f"• Images processed: {stats['processed_images']}\n"
                f"• Faces detected: {stats['total_faces']}\n"
                f"• Time: {stats['elapsed_time']:.1f}s"
            )

            # Reload clusters
            self.load_clusters()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Face detection failed:\n{str(e)}")

    def recluster_faces(self):
        """Re-run face clustering."""
        try:
            from config.face_detection_config import get_face_config
            from workers.face_cluster_worker import cluster_faces

            config = get_face_config()
            params = config.get_clustering_params()

            # Show progress
            QMessageBox.information(
                self,
                "Reclustering",
                f"Reclustering faces...\n\n"
                f"This may take a few moments.\n\n"
                f"Parameters:\n"
                f"• Epsilon: {params['eps']}\n"
                f"• Min samples: {params['min_samples']}"
            )

            # Run clustering
            cluster_faces(self.project_id, eps=params["eps"], min_samples=params["min_samples"])

            # Reload clusters
            self.load_clusters()

            QMessageBox.information(self, "Reclustering Complete", "Face clustering has been updated.")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Reclustering failed:\n{str(e)}")

    def open_settings(self):
        """Open face detection settings."""
        try:
            from ui.face_settings_dialog import FaceSettingsDialog

            dialog = FaceSettingsDialog(self)
            if dialog.exec():
                # Reload if settings changed
                self.load_clusters()

        except ImportError:
            QMessageBox.warning(self, "Settings", "Face settings dialog not available.")
