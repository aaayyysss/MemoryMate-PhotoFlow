# sidebar_qt.py
# Version 09.18.01.13 dated 20251031
# Tab-based sidebar with per-tab status labels, improved timeout handling,
# and dynamic branch/folder/date/tag loading.

from PySide6.QtWidgets import (
    QWidget, QTreeView, QMenu, QFileDialog,
    QVBoxLayout, QMessageBox, QTreeWidgetItem, QTreeWidget,
    QHeaderView, QHBoxLayout, QPushButton, QLabel, QTabWidget, QListWidget, QListWidgetItem, QProgressBar,
    QTableWidget, QTableWidgetItem, QScrollArea
)
from PySide6.QtCore import Qt, QPoint, Signal, QTimer
from PySide6.QtGui import (
    QStandardItemModel, QStandardItem,
    QFont, QColor, QIcon,
    QTransform, QPainter, QPixmap
)

from app_services import list_branches, export_branch
from reference_db import ReferenceDB
from services.tag_service import get_tag_service

import threading
import traceback
import time
import re

from datetime import datetime


# SettingsManager is used to persist sidebar display preference
try:
    from settings_manager_qt import SettingsManager
except Exception:
    SettingsManager = None



from PySide6.QtCore import Signal, QObject


# === Phase 3: Drag & Drop Support ===
class DroppableTreeView(QTreeView):
    """
    Custom QTreeView that accepts photo drops for folder assignment.
    Emits photoDropped signal with (folder_id, photo_paths) when photos are dropped.
    """
    photoDropped = Signal(int, list)  # (folder_id, list of photo paths)
    tagDropped = Signal(str, list)    # (tag_name, list of photo paths)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def dragEnterEvent(self, event):
        """Accept drag events if they contain photo paths."""
        if event.mimeData().hasUrls() or event.mimeData().hasFormat('application/x-photo-paths'):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        """Update drop indicator as drag moves over items."""
        if event.mimeData().hasUrls() or event.mimeData().hasFormat('application/x-photo-paths'):
            # Find the item under the cursor
            index = self.indexAt(event.position().toPoint())
            if index.isValid():
                self.setCurrentIndex(index)
                event.acceptProposedAction()
            else:
                event.ignore()
        else:
            event.ignore()

    def dropEvent(self, event):
        """Handle photo drop onto folder/tag."""
        if not (event.mimeData().hasUrls() or event.mimeData().hasFormat('application/x-photo-paths')):
            event.ignore()
            return

        # Get the item where photos were dropped
        index = self.indexAt(event.position().toPoint())
        if not index.isValid():
            event.ignore()
            return

        # Extract photo paths from MIME data
        paths = []
        if event.mimeData().hasFormat('application/x-photo-paths'):
            paths_data = event.mimeData().data('application/x-photo-paths')
            paths_text = bytes(paths_data).decode('utf-8')
            paths = [p.strip() for p in paths_text.split('\n') if p.strip()]
        elif event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls()]

        if not paths:
            event.ignore()
            return

        # Get the folder/branch ID from the item
        item = self.model().itemFromIndex(index)
        if not item:
            event.ignore()
            return

        # Check item type and emit appropriate signal
        folder_id = item.data(Qt.UserRole)
        branch_key = item.data(Qt.UserRole + 1)

        if folder_id is not None:
            # Dropped on folder - emit photoDropped signal
            print(f"[DragDrop] Dropped {len(paths)} photo(s) on folder ID: {folder_id}")
            self.photoDropped.emit(folder_id, paths)
            event.acceptProposedAction()
        elif branch_key is not None:
            # Dropped on branch/tag - emit tagDropped signal
            print(f"[DragDrop] Dropped {len(paths)} photo(s) on branch: {branch_key}")
            self.tagDropped.emit(branch_key, paths)
            event.acceptProposedAction()
        else:
            event.ignore()


# =====================================================================
# 1️: SidebarTabs — full tabs-based controller (new)
# ====================================================================

class SidebarTabs(QWidget):
    # Signals to parent (SidebarQt/MainWindow) so the grid can change context
    selectBranch = Signal(str)     # branch_key    e.g. "all" or "face_john"
    selectFolder = Signal(int)     # folder_id
    selectDate   = Signal(str)     # e.g. "2025-10" or "2025"
    selectTag    = Signal(str)     # tag name

    # Signals for async worker completion
    # ▼ add with your other Signals
    _finishBranchesSig = Signal(int, list, float, int)  # (idx, rows, started, gen)
    _finishFoldersSig  = Signal(int, list, float, int)
    _finishDatesSig    = Signal(int, object, float, int)  # object to accept dict or list
    _finishTagsSig     = Signal(int, list, float, int)
    _finishPeopleSig   = Signal(int, list, float, int)  # 👥 NEW
    _finishQuickSig    = Signal(int, list, float, int)  # Quick dates

    
    def __init__(self, project_id: int | None, parent=None):
        super().__init__(parent)
        self._dbg("__init__ started")
        self.db = ReferenceDB()
        self.project_id = project_id

        # internal state (lives here now)
        self._tab_populated: set[str] = set()
        self._tab_loading: set[str]   = set()
        self._tab_timers: dict[int, QTimer] = {}
        self._tab_status_labels: dict[int, QLabel] = {}
        self._count_targets: list[tuple] = []               # optional future use
        self._tab_indexes: dict[str, int] = {}              # "branches"/"folders"/"dates"/"tags"/"quick" -> tab index
        # ▼ add near your state vars
        self._tab_gen: dict[str, int] = {"branches":0, "folders":0, "dates":0, "tags":0, "quick":0}
        # Guard against concurrent refresh_all calls
        self._refreshing_all = False

        # UI
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.tab_widget = QTabWidget()
        v.addWidget(self.tab_widget, 1)

        # connections - Use Qt.QueuedConnection to ensure slots run in main thread
        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        self._finishBranchesSig.connect(self._finish_branches, Qt.QueuedConnection)
        self._finishFoldersSig.connect(self._finish_folders, Qt.QueuedConnection)
        self._finishDatesSig.connect(self._finish_dates, Qt.QueuedConnection)
        self._finishTagsSig.connect(self._finish_tags, Qt.QueuedConnection)
        self._finishPeopleSig.connect(self._finish_people, Qt.QueuedConnection)
        self._finishQuickSig.connect(self._finish_quick, Qt.QueuedConnection)

        # initial build – do not populate yet
        self._build_tabs()
        self._dbg("__init__ completed")

    # === helper for consistent debug output ===
    def _bump_gen(self, tab_type:str) -> int:
        g = (self._tab_gen.get(tab_type, 0) + 1) % 1_000_000
        self._tab_gen[tab_type] = g
        return g

    def _is_stale(self, tab_type:str, gen:int) -> bool:
        return gen != self._tab_gen.get(tab_type, -1)
        
    
    def _dbg(self, msg):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"[{ts}] [Tabs] {msg}")

    # ---------- public API ----------
    def set_project(self, project_id: int | None):
        self.project_id = project_id
        self.refresh_all(force=True)

    def refresh_all(self, force=False):
        """Repopulate tabs (typically after scans or project switch)."""
        self._dbg(f"refresh_all(force={force}) called")

        # Guard against concurrent refresh_all calls
        if self._refreshing_all:
            self._dbg("refresh_all blocked - already refreshing")
            return

        try:
            self._refreshing_all = True
            for key in ("branches", "folders", "dates", "tags", "quick"):
                idx = self._tab_indexes.get(key)
                self._dbg(f"refresh_all: key={key}, idx={idx}, force={force}")
                if idx is not None:
                    self._populate_tab(key, idx, force=force)
            self._dbg(f"refresh_all(force={force}) completed")
        finally:
            self._refreshing_all = False

    def refresh_tab(self, tab_name: str):
        """Refresh a single tab (e.g., 'tags', 'folders', 'dates')."""
        self._dbg(f"refresh_tab({tab_name}) called")
        idx = self._tab_indexes.get(tab_name)
        if idx is not None:
            self._populate_tab(tab_name, idx, force=True)
            self._dbg(f"refresh_tab({tab_name}) completed")
        else:
            self._dbg(f"refresh_tab({tab_name}) - tab not found")

    def show_tabs(self): self.show()
    def hide_tabs(self):
        """Hide tabs and cancel any pending workers"""
        self._dbg("hide_tabs() called - canceling pending workers")
        # Bump all generations to invalidate any in-flight workers
        for key in self._tab_gen.keys():
            self._bump_gen(key)
        # Clear loading state
        self._tab_loading.clear()
        # Cancel all timers
        for idx, timer in list(self._tab_timers.items()):
            try:
                timer.stop()
            except:
                pass
        self._tab_timers.clear()
        self._tab_status_labels.clear()
        self.hide()

    # ---------- internal ----------
    def _build_tabs(self):
        self._dbg("_build_tabs → building tab widgets")
        self.tab_widget.clear()
        self._tab_indexes.clear()

        for tab_type, label in [
            ("branches", "Branches"),
            ("folders",  "Folders"),
            ("dates",    "By Date"),
            ("tags",     "Tags"),
            ("people",   "People"),          # 👥 NEW
            ("quick",    "Quick Dates"),
        ]:

            w = QWidget()
            w.setProperty("tab_type", tab_type)
            v = QVBoxLayout(w)
            v.setContentsMargins(6, 6, 6, 6)
            v.addWidget(QLabel(f"Loading {label}…"))
            idx = self.tab_widget.addTab(w, label)
            self._tab_indexes[tab_type] = idx

        self._tab_loading.clear()
        self._tab_populated.clear()
        QTimer.singleShot(0, lambda: self._on_tab_changed(self.tab_widget.currentIndex()))
        self._dbg(f"_build_tabs → added {len(self._tab_indexes)} tabs")

    def _on_tab_changed(self, idx: int):
        self._dbg(f"_on_tab_changed(idx={idx})")
        if idx < 0:
            return
        w = self.tab_widget.widget(idx)
        tab_type = w.property("tab_type") if w else None
        if not tab_type:
            return
        self._start_timeout(idx, tab_type)
        self._populate_tab(tab_type, idx)
        self._dbg(f"_on_tab_changed → tab_type={tab_type}")

    def _start_timeout(self, idx, tab_type, ms=120000):
        self._dbg(f"_start_timeout idx={idx} type={tab_type}")

        t = self._tab_timers.get(idx)
        if t:
            try: t.stop()
            except: pass
        timer = QTimer(self); timer.setSingleShot(True)

        def on_to():
            self._dbg(f"⚠️ timeout reached for tab={tab_type}")

            if tab_type in self._tab_loading:
                self._tab_loading.discard(tab_type)
                self._clear_tab(idx)
                self._set_tab_empty(idx, "No items (timeout)")
            self._tab_timers.pop(idx, None)
            self._tab_status_labels.pop(idx, None)

        timer.timeout.connect(on_to)
        timer.start(ms)
        self._tab_timers[idx] = timer

    def _cancel_timeout(self, idx):
        t = self._tab_timers.pop(idx, None)
        if t:
            try: t.stop()
            except: pass

    def _show_loading(self, idx, label="Loading…"):
        self._dbg(f"_show_loading idx={idx} label='{label}'")

        self._clear_tab(idx)
        tab = self.tab_widget.widget(idx)
        v = tab.layout()
        title = QLabel(f"<b>{label}</b>")
        pb = QProgressBar(); pb.setRange(0,0)
        st = QLabel(""); st.setStyleSheet("color:#666; font-size:11px;")
        v.addWidget(title); v.addWidget(pb); v.addWidget(st)
        self._tab_status_labels[idx] = st

    def _clear_tab(self, idx):
        self._dbg(f"_clear_tab idx={idx}")
        self._cancel_timeout(idx)

        tab = self.tab_widget.widget(idx)
        if not tab:
            self._dbg(f"_clear_tab idx={idx} - tab is None, skipping")
            return
        v = tab.layout()
        if not v:
            self._dbg(f"_clear_tab idx={idx} - layout is None, skipping")
            return
        try:
            for i in reversed(range(v.count())):
                item = v.itemAt(i)
                if not item:
                    continue
                w = item.widget()
                if w:
                    w.setParent(None)
                    w.deleteLater()
        except Exception as e:
            self._dbg(f"_clear_tab idx={idx} - Exception during widget cleanup: {e}")
            import traceback
            traceback.print_exc()

    def _set_tab_empty(self, idx, msg="No items"):
        tab = self.tab_widget.widget(idx)
        if not tab: return
        v = tab.layout()
        v.addWidget(QLabel(f"<b>{msg}</b>"))

    def _wrap_in_scroll_area(self, widget):
        """Wrap a widget in a QScrollArea for vertical scrolling support"""
        scroll = QScrollArea()
        scroll.setWidget(widget)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        return scroll

    # ---------- collapse/expand support ----------

    def toggle_collapse_expand(self):
        """Toggle collapse/expand all for tree widgets in current tab"""
        try:
            current_idx = self.tab_widget.currentIndex()
            tab = self.tab_widget.widget(current_idx)
            if not tab:
                return

            # Find QTreeWidget in current tab
            tree = None
            for i in range(tab.layout().count()):
                widget = tab.layout().itemAt(i).widget()
                if isinstance(widget, QTreeWidget):
                    tree = widget
                    break

            if not tree:
                return

            # Check if any items are expanded
            any_expanded = False
            for i in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(i)
                if item.isExpanded():
                    any_expanded = True
                    break

            # Toggle: collapse all if any expanded, else expand all
            if any_expanded:
                tree.collapseAll()
            else:
                tree.expandAll()

        except Exception as e:
            print(f"[SidebarTabs] toggle_collapse_expand failed: {e}")

    # ---------- population dispatcher ----------

    def _populate_tab(self, tab_type: str, idx: int, force=False):
        self._dbg(f"_populate_tab({tab_type}, idx={idx}, force={force})")
        self._dbg(f"  populated={tab_type in self._tab_populated}, loading={tab_type in self._tab_loading}")

        # Force refresh: clear both populated and loading states
        if force:
            if tab_type in self._tab_populated:
                self._dbg(f"  Force refresh: removing {tab_type} from populated set")
                self._tab_populated.discard(tab_type)
            if tab_type in self._tab_loading:
                self._dbg(f"  Force refresh: removing {tab_type} from loading set (canceling in-progress)")
                self._tab_loading.discard(tab_type)
                # Bump generation to invalidate any in-progress workers
                self._bump_gen(tab_type)

        if tab_type in self._tab_populated or tab_type in self._tab_loading:
            self._dbg(f"  Skipping {tab_type}: already populated or loading")
            if tab_type == "branches":
                self._set_branch_context_from_list(idx)
            return

        self._dbg(f"  Starting load for {tab_type}")
        self._tab_loading.add(tab_type)
        gen = self._bump_gen(tab_type)

        if tab_type == "branches":
            self._show_loading(idx, "Loading Branches…")
            self._load_branches(idx, gen)
        elif tab_type == "folders":
            self._show_loading(idx, "Loading Folders…")
            self._load_folders(idx, gen)
        elif tab_type == "dates":
            self._show_loading(idx, "Loading Dates…")
            self._load_dates(idx, gen)
        elif tab_type == "tags":
            self._show_loading(idx, "Loading Tags…")
            self._load_tags(idx, gen)
        elif tab_type == "people":
            self._show_loading(idx, "Loading People…")
            self._load_people(idx, gen)

        elif tab_type == "quick":
            self._show_loading(idx, "Loading Quick Dates…")
            self._load_quick(idx, gen)

    # ---------- branches ----------
    def _load_branches(self, idx:int, gen:int):
        started = time.time()
        def work():
            try:
                rows = []
                if self.project_id:
                    rows = self.db.get_branches(self.project_id) or []
            except Exception:
                traceback.print_exc()
                rows = []
            self._finishBranchesSig.emit(idx, rows, started, gen)
        threading.Thread(target=work, daemon=True).start()

    # ---------- BRANCHES ----------
    def _finish_branches(self, idx:int, rows:list, started:float, gen:int):
        if self._is_stale("branches", gen):
            self._dbg(f"_finish_branches (stale gen={gen}) — ignoring")
            return
        self._cancel_timeout(idx)
        self._clear_tab(idx)

        # normalize to [(key, name, count)]
        norm = []
        for r in (rows or []):
            count = None
            if isinstance(r, (tuple, list)) and len(r) >= 2:
                key, name = r[0], r[1]
                count = r[2] if len(r) >= 3 else None
            elif isinstance(r, dict):
                key  = r.get("branch_key") or r.get("key") or r.get("id") or r.get("name")
                name = r.get("display_name") or r.get("label") or r.get("name") or str(key)
                count = r.get("count")
            else:
                key = name = str(r)
            if key is None:
                continue
            norm.append((str(key), str(name), count))

        tab = self.tab_widget.widget(idx)
        tab.layout().addWidget(QLabel("<b>Branches</b>"))

        # Create 2-column table: Branch/Folder | Photos
        table = QTableWidget()
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Branch/Folder", "Photos"])
        table.setRowCount(len(norm))
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)

        for row, (key, name, count) in enumerate(norm):
            # Column 0: Branch name
            item_name = QTableWidgetItem(name)
            item_name.setData(Qt.UserRole, key)
            table.setItem(row, 0, item_name)

            # Column 1: Count (right-aligned, light grey like List view)
            count_str = str(count) if count is not None else "0"
            item_count = QTableWidgetItem(count_str)
            item_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item_count.setForeground(QColor("#BBBBBB"))
            table.setItem(row, 1, item_count)

        table.cellDoubleClicked.connect(lambda row, col: self.selectBranch.emit(table.item(row, 0).data(Qt.UserRole)))
        tab.layout().addWidget(self._wrap_in_scroll_area(table), 1)

        self._tab_populated.add("branches")
        self._tab_loading.discard("branches")
        st = self._tab_status_labels.get(idx)
        if st: st.setText(f"{len(norm)} item(s) • {time.time()-started:.2f}s")
        if norm:
            self.selectBranch.emit(norm[0][0])

    def _set_branch_context_from_list(self, idx):
        tab = self.tab_widget.widget(idx)
        if not tab: return
        try:
            # Find QTableWidget in tab layout
            table = next((tab.layout().itemAt(i).widget()
                          for i in range(tab.layout().count())
                          if isinstance(tab.layout().itemAt(i).widget(), QTableWidget)), None)
            if table and table.currentRow() >= 0:
                self.selectBranch.emit(table.item(table.currentRow(), 0).data(Qt.UserRole))
        except Exception:
            pass

    # ---------- folders ----------
    def _load_folders(self, idx:int, gen:int):
        started = time.time()
        def work():
            try:
                # CRITICAL FIX: Pass project_id to filter folders by project
                rows = self.db.get_all_folders(self.project_id) or []    # expect list[dict{id,path}] or tuples
                self._dbg(f"_load_folders → got {len(rows)} rows for project_id={self.project_id}")
            except Exception:
                traceback.print_exc()
                rows = []
            self._finishFoldersSig.emit(idx, rows, started, gen)
        threading.Thread(target=work, daemon=True).start()

    # ---------- FOLDERS ----------
    def _finish_folders(self, idx:int, rows:list, started:float, gen:int):
        if self._is_stale("folders", gen):
            self._dbg(f"_finish_folders (stale gen={gen}) — ignoring")
            return
        self._cancel_timeout(idx)
        self._clear_tab(idx)

        tab = self.tab_widget.widget(idx)
        tab.layout().addWidget(QLabel("<b>Folders</b>"))

        # Create tree widget matching List view's Folders-Branch appearance
        tree = QTreeWidget()
        tree.setHeaderLabels(["Folder", "Photos"])
        tree.setColumnCount(2)
        tree.setSelectionMode(QTreeWidget.SingleSelection)
        tree.setEditTriggers(QTreeWidget.NoEditTriggers)
        tree.setAlternatingRowColors(True)
        tree.header().setStretchLastSection(False)
        tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)

        # Build tree structure recursively using database hierarchy (like List view)
        try:
            self._add_folder_tree_items(tree, None)
        except Exception as e:
            print(f"[SidebarTabs] _finish_folders tree build failed: {e}")
            traceback.print_exc()

        if tree.topLevelItemCount() == 0:
            self._set_tab_empty(idx, "No folders found")
        else:
            # Connect double-click to emit folder selection
            tree.itemDoubleClicked.connect(
                lambda item, col: self.selectFolder.emit(item.data(0, Qt.UserRole)) if item.data(0, Qt.UserRole) else None
            )
            tab.layout().addWidget(self._wrap_in_scroll_area(tree), 1)

        self._tab_populated.add("folders")
        self._tab_loading.discard("folders")
        st = self._tab_status_labels.get(idx)
        folder_count = self._count_tree_folders(tree)
        if st: st.setText(f"{folder_count} folder(s) • {time.time()-started:.2f}s")

    def _add_folder_tree_items(self, parent_widget_or_item, parent_id=None):
        """Recursively add folder items to QTreeWidget (matches List view's _add_folder_items)"""
        try:
            rows = self.db.get_child_folders(parent_id, project_id=self.project_id)
        except Exception as e:
            print(f"[SidebarTabs] get_child_folders({parent_id}, project_id={self.project_id}) failed: {e}")
            return

        for row in rows:
            name = row["name"]
            fid = row["id"]

            # Get recursive photo count (includes subfolders)
            if hasattr(self.db, "get_image_count_recursive"):
                photo_count = int(self.db.get_image_count_recursive(fid) or 0)
            else:
                # Fallback to non-recursive count
                try:
                    folder_paths = self.db.get_images_by_folder(fid, project_id=self.project_id)
                    photo_count = len(folder_paths) if folder_paths else 0
                except Exception:
                    photo_count = 0

            # Create tree item with emoji prefix (matching List view)
            item = QTreeWidgetItem([f"📁 {name}", f"{photo_count:>5}"])
            item.setData(0, Qt.UserRole, int(fid))

            # Set count column formatting (right-aligned, grey color like List view)
            item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            item.setForeground(1, QColor("#888888"))

            # Add to parent
            if isinstance(parent_widget_or_item, QTreeWidget):
                parent_widget_or_item.addTopLevelItem(item)
            else:
                parent_widget_or_item.addChild(item)

            # Recursively add child folders
            self._add_folder_tree_items(item, fid)

    def _count_tree_folders(self, tree):
        """Count total folders in tree"""
        count = 0
        def count_recursive(parent_item):
            nonlocal count
            for i in range(parent_item.childCount()):
                count += 1
                count_recursive(parent_item.child(i))

        for i in range(tree.topLevelItemCount()):
            count += 1
            count_recursive(tree.topLevelItem(i))
        return count

    # ---------- dates ----------
    def _load_dates(self, idx:int, gen:int):
        started = time.time()
        def work():
            rows = []
            try:
                # Get hierarchical date data: {year: {month: [days]}}
                # CRITICAL FIX: Pass project_id to filter dates by project
                if hasattr(self.db, "get_date_hierarchy"):
                    hier = self.db.get_date_hierarchy(self.project_id) or {}
                    # Also get year counts - now filtered by project_id
                    year_counts = {}
                    if hasattr(self.db, "list_years_with_counts"):
                        year_list = self.db.list_years_with_counts(self.project_id) or []
                        year_counts = {str(y): c for y, c in year_list}
                    # Build result with hierarchy and counts
                    rows = {"hierarchy": hier, "year_counts": year_counts}
                else:
                    self._dbg("_load_dates → No date hierarchy method available")
                self._dbg(f"_load_dates → got hierarchy data for project_id={self.project_id}")
            except Exception:
                traceback.print_exc()
                rows = {}
            self._finishDatesSig.emit(idx, rows, started, gen)
        threading.Thread(target=work, daemon=True).start()

    # ---------- DATES ----------
    def _finish_dates(self, idx:int, rows:list|dict, started:float, gen:int):
        if gen is not None and self._is_stale("dates", gen):
            self._dbg(f"_finish_dates (stale gen={gen}) — ignoring")
            return
        self._cancel_timeout(idx)
        self._clear_tab(idx)

        tab = self.tab_widget.widget(idx)
        tab.layout().addWidget(QLabel("<b>Dates</b>"))

        # Extract hierarchy and counts from result
        if isinstance(rows, dict):
            hier = rows.get("hierarchy", {})
            year_counts = rows.get("year_counts", {})
        else:
            hier = {}
            year_counts = {}

        if not hier:
            self._set_tab_empty(idx, "No date index found")
        else:
            # Create tree widget: Years → Months → Days
            tree = QTreeWidget()
            tree.setHeaderLabels(["Year/Month/Day", "Photos"])
            tree.setColumnCount(2)
            tree.setSelectionMode(QTreeWidget.SingleSelection)
            tree.setEditTriggers(QTreeWidget.NoEditTriggers)
            tree.setAlternatingRowColors(True)
            tree.header().setStretchLastSection(False)
            tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
            tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)

            # Populate tree: Years (top level)
            for year in sorted(hier.keys(), reverse=True):
                # Get accurate year count from database
                year_count = 0
                try:
                    if hasattr(self.db, "count_for_year"):
                        year_count = self.db.count_for_year(year)
                    else:
                        year_count = year_counts.get(str(year), 0)
                except Exception:
                    year_count = year_counts.get(str(year), 0)

                year_item = QTreeWidgetItem([str(year), str(year_count)])
                year_item.setData(0, Qt.UserRole, str(year))
                tree.addTopLevelItem(year_item)

                # Months (children of year)
                months_dict = hier[year]
                month_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

                for month in sorted(months_dict.keys(), reverse=True):
                    days_list = months_dict[month]
                    month_num = int(month) if month.isdigit() else 0
                    month_label = month_names[month_num] if 0 < month_num <= 12 else month

                    # Get accurate month count from database (not just len(days_list))
                    month_count = 0
                    try:
                        if hasattr(self.db, "count_for_month"):
                            month_count = self.db.count_for_month(year, month)
                        else:
                            month_count = len(days_list)
                    except Exception:
                        month_count = len(days_list)

                    month_item = QTreeWidgetItem([f"{month_label} {year}", str(month_count)])
                    month_item.setData(0, Qt.UserRole, f"{year}-{month}")
                    year_item.addChild(month_item)

                    # Days (children of month) - WITH COUNTS
                    for day in sorted(days_list, reverse=True):
                        # Get day count from database
                        day_count = 0
                        try:
                            if hasattr(self.db, "count_for_day"):
                                day_count = self.db.count_for_day(day, project_id=self.project_id)
                            else:
                                # Fallback: count from get_images_by_date
                                day_paths = self.db.get_images_by_date(day) if hasattr(self.db, "get_images_by_date") else []
                                day_count = len(day_paths) if day_paths else 0
                        except Exception:
                            day_count = 0

                        day_item = QTreeWidgetItem([str(day), str(day_count) if day_count > 0 else ""])
                        day_item.setData(0, Qt.UserRole, str(day))
                        month_item.addChild(day_item)

            # Connect double-click to emit date selection
            tree.itemDoubleClicked.connect(lambda item, col: self.selectDate.emit(item.data(0, Qt.UserRole)))
            tab.layout().addWidget(self._wrap_in_scroll_area(tree), 1)

        self._tab_populated.add("dates")
        self._tab_loading.discard("dates")
        st = self._tab_status_labels.get(idx)
        if st:
            year_count = len(hier.keys()) if hier else 0
            st.setText(f"{year_count} year(s) • {time.time()-started:.2f}s")

    # ---------- tags ----------
    def _load_tags(self, idx:int, gen:int):
        """
        Load tags using TagService (service layer).

        ARCHITECTURE: UI Layer → TagService → TagRepository → Database
        """
        started = time.time()
        project_id = self.project_id  # Capture project_id before thread starts
        def work():
            rows = []
            try:
                # Use TagService for proper layered architecture
                tag_service = get_tag_service()
                rows = tag_service.get_all_tags_with_counts(project_id) or []  # list of (tag_name, count) tuples
                self._dbg(f"_load_tags → got {len(rows)} rows for project_id={project_id}")
            except Exception:
                traceback.print_exc()
                rows = []
            self._finishTagsSig.emit(idx, rows, started, gen)
        threading.Thread(target=work, daemon=True).start()

    # ---------- TAGS ----------
    def _finish_tags(self, idx:int, rows:list, started:float, gen:int):
        self._dbg(f"_finish_tags called: idx={idx}, gen={gen}, rows_count={len(rows) if rows else 0}")
        if self._is_stale("tags", gen):
            self._dbg(f"_finish_tags (stale gen={gen}) — ignoring")
            return
        self._cancel_timeout(idx)
        self._clear_tab(idx)

        tab = self.tab_widget.widget(idx)
        if not tab:
            self._dbg(f"_finish_tags - tab is None at idx={idx}, aborting")
            return
        layout = tab.layout()
        if not layout:
            self._dbg(f"_finish_tags - layout is None at idx={idx}, aborting")
            return
        layout.addWidget(QLabel("<b>Tags</b>"))

        # Process rows which can be: tuples (tag, count), dicts, or strings
        tag_items = []  # list of (tag_name, count)
        for r in (rows or []):
            if isinstance(r, tuple) and len(r) == 2:
                # Format: (tag_name, count) from get_all_tags_with_counts()
                tag_name, count = r
                tag_items.append((tag_name, count))
            elif isinstance(r, dict):
                # Format: dict with 'tag'/'name'/'label' key
                tag_name = r.get("tag") or r.get("name") or r.get("label")
                count = r.get("count", 0)
                if tag_name:
                    tag_items.append((tag_name, count))
            else:
                # Format: plain string
                tag_name = str(r)
                if tag_name:
                    tag_items.append((tag_name, 0))

        if not tag_items:
            self._set_tab_empty(idx, "No tags found")
        else:
            # Create 2-column table: Tag | Photos
            table = QTableWidget()
            table.setColumnCount(2)
            table.setHorizontalHeaderLabels(["Tag", "Photos"])
            table.setRowCount(len(tag_items))
            table.setSelectionBehavior(QTableWidget.SelectRows)
            table.setSelectionMode(QTableWidget.SingleSelection)
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            table.verticalHeader().setVisible(False)
            table.horizontalHeader().setStretchLastSection(False)
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)

            for row, (tag_name, count) in enumerate(tag_items):
                # Column 0: Tag name (no emoji prefix to match List view)
                item_name = QTableWidgetItem(tag_name)
                item_name.setData(Qt.UserRole, tag_name)
                table.setItem(row, 0, item_name)

                # Column 1: Count (right-aligned, grey color like List view)
                count_str = str(count) if count else ""
                item_count = QTableWidgetItem(count_str)
                item_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                item_count.setForeground(QColor("#888888"))
                table.setItem(row, 1, item_count)

            table.cellDoubleClicked.connect(lambda row, col: self.selectTag.emit(table.item(row, 0).data(Qt.UserRole)))
            if tab.layout():
                tab.layout().addWidget(self._wrap_in_scroll_area(table), 1)
            else:
                self._dbg(f"_finish_tags - layout is None when adding table, aborting")

        self._tab_populated.add("tags")
        self._tab_loading.discard("tags")
        st = self._tab_status_labels.get(idx)
        if st: st.setText(f"{len(tag_items)} item(s) • {time.time()-started:.2f}s")
    # ---------- quick ----------
    def _load_quick(self, idx:int, gen:int):
        started = time.time()
        def work():
            rows = []
            try:
                if hasattr(self.db, "get_quick_date_counts"):
                    rows = self.db.get_quick_date_counts() or []
                else:
                    # Fallback: simple list without counts
                    rows = [
                        {"key": "today", "label": "Today", "count": 0},
                        {"key": "this-week", "label": "This Week", "count": 0},
                        {"key": "this-month", "label": "This Month", "count": 0}
                    ]
                self._dbg(f"_load_quick → got {len(rows)} rows")
            except Exception:
                traceback.print_exc()
                rows = []
            # Emit using same signature as other tabs
            self._finishQuickSig.emit(idx, rows, started, gen)
        threading.Thread(target=work, daemon=True).start()

    # ---------- QUICK ----------
    def _finish_quick(self, idx:int, rows:list, started:float|None=None, gen:int|None=None):
        if gen is not None and self._is_stale("quick", gen):
            self._dbg(f"_finish_quick (stale gen={gen}) — ignoring")
            return
        self._cancel_timeout(idx)
        self._clear_tab(idx)

        tab = self.tab_widget.widget(idx)
        tab.layout().addWidget(QLabel("<b>Quick Dates</b>"))

        # Normalize rows to (key, label, count)
        quick_items = []
        for r in (rows or []):
            if isinstance(r, dict):
                key = r.get("key", "")
                label = r.get("label", "")
                count = r.get("count", 0)
                # Strip "date:" prefix from key if present
                if key.startswith("date:"):
                    key = key[5:]
                quick_items.append((key, label, count))
            elif isinstance(r, (tuple, list)) and len(r) >= 2:
                key, label = r[0], r[1]
                count = r[2] if len(r) >= 3 else 0
                quick_items.append((key, label, count))

        if not quick_items:
            self._set_tab_empty(idx, "No quick dates")
        else:
            # Create 2-column table: Period | Photos
            table = QTableWidget()
            table.setColumnCount(2)
            table.setHorizontalHeaderLabels(["Period", "Photos"])
            table.setRowCount(len(quick_items))
            table.setSelectionBehavior(QTableWidget.SelectRows)
            table.setSelectionMode(QTableWidget.SingleSelection)
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            table.verticalHeader().setVisible(False)
            table.horizontalHeader().setStretchLastSection(False)
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)

            for row, (key, label, count) in enumerate(quick_items):
                # Column 0: Period label
                item_name = QTableWidgetItem(label)
                item_name.setData(Qt.UserRole, key)
                table.setItem(row, 0, item_name)

                # Column 1: Count (right-aligned, light grey like List view)
                item_count = QTableWidgetItem(str(count))
                item_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                item_count.setForeground(QColor("#BBBBBB"))
                table.setItem(row, 1, item_count)

            table.cellDoubleClicked.connect(lambda row, col: self.selectDate.emit(table.item(row, 0).data(Qt.UserRole)))
            tab.layout().addWidget(self._wrap_in_scroll_area(table), 1)

        self._tab_populated.add("quick")
        self._tab_loading.discard("quick")

    # ---------- people ----------
    def _load_people(self, idx: int, gen: int):
        started = time.time()
        def work():
            try:
                rows = []
                if self.project_id and hasattr(self.db, "get_face_clusters"):
                    rows = self.db.get_face_clusters(self.project_id) or []
                self._dbg(f"_load_people → got {len(rows)} clusters")
            except Exception:
                traceback.print_exc()
                rows = []
            self._finishPeopleSig.emit(idx, rows, started, gen)
        threading.Thread(target=work, daemon=True).start()

    # ---------- PEOPLE ----------
    def _finish_people(self, idx: int, rows: list, started: float, gen: int):
        if self._is_stale("people", gen):
            self._dbg(f"_finish_people (stale gen={gen}) — ignoring")
            return
        self._cancel_timeout(idx)
        self._clear_tab(idx)

        tab = self.tab_widget.widget(idx)
        layout = tab.layout()

        # === Header row with label + 🔁 Re-Cluster ===
        header = QWidget()
        hbox = QHBoxLayout(header)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(8)

        lbl = QLabel("<b>👥 People / Face Clusters</b>")
        btn_recluster = QPushButton("🔁 Re-Cluster")
        btn_recluster.setFixedHeight(24)
        btn_recluster.setToolTip("Run face clustering again in background")
        btn_recluster.setStyleSheet("QPushButton{padding:3px 8px;}")
        hbox.addWidget(lbl)
        hbox.addStretch(1)
        hbox.addWidget(btn_recluster)
        layout.addWidget(header)

        # === Launch clustering worker ===
        def on_recluster():
            try:
                from subprocess import Popen
                import sys, os
                script = os.path.join(os.path.dirname(__file__), "workers", "face_cluster_worker.py")
                print(f"[People] launching recluster worker → {script}")
                # Reuse the same detached helper pattern
                if hasattr(self.parent(), "_launch_detached"):
                    self.parent()._launch_detached(script)
                else:
                    Popen([sys.executable, script], close_fds=True)
            except Exception as e:
                import traceback; traceback.print_exc()
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.critical(self, "Re-Cluster Failed", str(e))

        btn_recluster.clicked.connect(on_recluster)

        # === Populate cluster list ===
        if not rows:
            self._set_tab_empty(idx, "No face clusters found")
            self._tab_populated.add("people")
            self._tab_loading.discard("people")
            return

        # Create 2-column table: Person | Photos
        table = QTableWidget()
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Person", "Photos"])
        table.setRowCount(len(rows))
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)

        for row_idx, row in enumerate(rows):
            name = row.get("display_name") or row.get("branch_key")
            count = row.get("member_count", 0)
            rep = row.get("rep_path", "")

            # Column 0: Person name
            item_name = QTableWidgetItem(str(name))
            item_name.setData(Qt.UserRole, f"facecluster:{row['branch_key']}")
            if rep:
                item_name.setToolTip(rep)
            table.setItem(row_idx, 0, item_name)

            # Column 1: Count (right-aligned, grey like List view)
            item_count = QTableWidgetItem(str(count))
            item_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item_count.setForeground(QColor("#888888"))
            table.setItem(row_idx, 1, item_count)

        table.cellDoubleClicked.connect(
            lambda row, col: self.selectBranch.emit(table.item(row, 0).data(Qt.UserRole))
        )
        layout.addWidget(self._wrap_in_scroll_area(table), 1)

        self._tab_populated.add("people")
        self._tab_loading.discard("people")
        st = self._tab_status_labels.get(idx)
        if st:
            st.setText(f"{len(rows)} cluster(s) • {time.time()-started:.2f}s")

# =====================================================================
# 2️ SidebarQt — main sidebar container with toggle
# =====================================================================

class SidebarQt(QWidget):
    folderSelected = Signal(int)

    def __init__(self, project_id=None):
        super().__init__()
        self.db = ReferenceDB()
        self.project_id = project_id

        # settings
        self.settings = SettingsManager() if SettingsManager else None

        # UI state
        self._reload_block = False
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.timeout.connect(self._do_reload_throttled)

        # Worker generation for list mode (to cancel stale workers)
        self._list_worker_gen = 0

        # Refresh guard to prevent concurrent reloads
        self._refreshing = False

        # Initialization flag to prevent processEvents() during startup
        self._initialized = False

        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(60)
        self._spin_timer.timeout.connect(self._tick_spinner)
        self._spin_angle = 0
        self._base_pm = self._make_reload_pixmap(18, 18)

        
        # Header
        header_bar = QWidget()
        header_layout = QHBoxLayout(header_bar)
        header_layout.setContentsMargins(2, 2, 2, 2)
        header_layout.setSpacing(4)

        title_lbl = QLabel("📁 Sidebar")
        title_lbl.setStyleSheet("font-weight: bold; padding-left: 4px;")
        header_layout.addWidget(title_lbl)
        header_layout.addStretch(1)

        # Mode toggle
        self.btn_mode_toggle = QPushButton("")
        self.btn_mode_toggle.setCheckable(True)
        current_mode = self.settings.get("sidebar_mode", "list") if self.settings else "list"
        self.btn_mode_toggle.setChecked(current_mode.lower() == "tabs")
        self._update_mode_toggle_text()
        self.btn_mode_toggle.setToolTip("Toggle Sidebar Mode: List / Tabs")
        self.btn_mode_toggle.clicked.connect(self._on_mode_toggled)
        header_layout.addWidget(self.btn_mode_toggle)

        # Refresh
        self.btn_refresh = QPushButton("")
        self.btn_refresh.setFixedSize(28, 24)
        self.btn_refresh.setIcon(QIcon(self._base_pm))
        self.btn_refresh.setIconSize(self._base_pm.size())
        self.btn_refresh.setToolTip("Reload folder tree from database")
        header_layout.addWidget(self.btn_refresh)
        self.btn_refresh.clicked.connect(self._on_refresh_clicked)

        # collapse/expand
        self.btn_collapse = QPushButton("⇵")
        self.btn_collapse.setFixedSize(28, 24)
        self.btn_collapse.setToolTip("Collapse/Expand main sections")
        header_layout.addWidget(self.btn_collapse)
        self.btn_collapse.clicked.connect(self._on_collapse_clicked)

        # Tree (list mode) - Phase 3: Use DroppableTreeView for drag & drop support
        self.tree = DroppableTreeView(self)
        self.tree.setAlternatingRowColors(True)
        self.tree.setEditTriggers(QTreeView.NoEditTriggers)
        self.tree.setSelectionBehavior(QTreeView.SelectRows)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(False)
        self.model = QStandardItemModel(self.tree)
        self.model.setHorizontalHeaderLabels(["Folder / Branch", "Photos"])
        self.tree.setModel(self.model)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_menu)

        # Phase 3: Connect drag & drop signals
        self.tree.photoDropped.connect(self._on_photos_dropped_to_folder)
        self.tree.tagDropped.connect(self._on_photos_dropped_to_tag)


        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)
        layout.addWidget(header_bar)
        layout.addWidget(self.tree, 1)
        
        # Tabs controller (new owner of tab UI)
        self.tabs_controller = SidebarTabs(project_id=self.project_id, parent=self)
        self.tabs_controller.hide()            # start hidden if default is list
        layout.addWidget(self.tabs_controller, 1)

        # Connect SidebarTabs signals to your grid helpers
        self.tabs_controller.selectBranch.connect(lambda key: self._set_grid_context("branch", key))
        self.tabs_controller.selectFolder.connect(lambda folder_id: self._set_grid_context("folder", folder_id))
        self.tabs_controller.selectDate.connect(lambda key: self._set_grid_context("date", key))
        self.tabs_controller.selectTag.connect(
            lambda name: self.window()._apply_tag_filter(name) if hasattr(self.window(), "_apply_tag_filter") else None
        )        
        
        
        # Build the tree (counts update async)
        self._build_tree_model()

        # Click handlers
        self.tree.clicked.connect(self._on_item_clicked)

        # Start with persisted mode
        try:
            if current_mode.lower() == "tabs":
                self.switch_display_mode("tabs")
            else:
                self.switch_display_mode("list")
        except Exception:
            self.switch_display_mode("list")

        # Apply fold state if persisted
        try:
            folded = bool(self.settings.get("sidebar_folded", False)) if self.settings else False
            if folded:
                self.collapse_all()
        except Exception:
            pass

        # Mark initialization as complete - safe to call processEvents() now
        self._initialized = True

    # ---- header helpers ----


    def _find_model_item_by_key(self, key, role=Qt.UserRole+1):
        """Return (QStandardItem for column0, QStandardItem for column1) where column0.data(role)==key, or (None,None)."""
        def recurse(parent):
            for r in range(parent.rowCount()):
                n0 = parent.child(r, 0)
                n1 = parent.child(r, 1)
                if n0 and n0.data(role) == key:
                    return n0, (n1 if n1 else None)
                # search recursively
                res = recurse(n0)
                if res != (None, None):
                    return res
            return (None, None)
        # top-level roots
        for top in range(self.model.rowCount()):
            root = self.model.item(top, 0)
            # check children of root
            res = recurse(root)
            if res != (None, None):
                return res
        return (None, None)

    def _update_mode_toggle_text(self):
        self.btn_mode_toggle.setText("Tabs" if self.btn_mode_toggle.isChecked() else "List")

    def _on_mode_toggled(self, checked):
        self._update_mode_toggle_text()
        mode = "tabs" if checked else "list"
        try:
            if self.settings:
                self.settings.set("sidebar_mode", mode)
        except Exception:
            pass
        self.switch_display_mode(mode)

    def _on_refresh_clicked(self):
        self._start_spinner()
        self.reload()
        QTimer.singleShot(150, self._stop_spinner)

    def _on_collapse_clicked(self):
        try:
            mode = self._effective_display_mode()
            if mode == "tabs":
                # Collapse/expand trees in active tab
                if hasattr(self, "tabs_controller"):
                    self.tabs_controller.toggle_collapse_expand()
            else:
                any_expanded = False
                for r in range(self.model.rowCount()):
                    idx = self.model.index(r, 0)
                    if self.tree.isExpanded(idx):
                        any_expanded = True
                        break
                if any_expanded:
                    self.collapse_all()
                else:
                    self.expand_all()
        except Exception as e:
            print(f"[Sidebar] collapse action failed: {e}")


    def _get_photo_count(self, folder_id: int) -> int:
        try:
            if hasattr(self.db, "count_for_folder"):
                return int(self.db.count_for_folder(folder_id, project_id=self.project_id) or 0)
            if hasattr(self.db, "get_folder_photo_count"):
                return int(self.db.get_folder_photo_count(folder_id, project_id=self.project_id) or 0)
            with self.db._connect() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM photo_metadata WHERE folder_id=?", (folder_id,))
                val = cur.fetchone()
                return int(val[0]) if val else 0
        except Exception:
            return 0

    # ---- click handling ----
    def _on_item_clicked(self, index):
        if not index.isValid():
            return
        index = index.sibling(index.row(), 0)
        item = self.model.itemFromIndex(index)
        if not item:
            return
        mode = item.data(Qt.UserRole)
        value = item.data(Qt.UserRole + 1)
        mw = self.window()
        
        if not hasattr(mw, "grid"):
            return

        def _clear_tag_if_needed():
            if mode in ("folder", "branch", "date") and hasattr(mw, "_clear_tag_filter"):
                mw._clear_tag_filter()

        if mode == "folder" and value:
            _clear_tag_if_needed()
            mw.grid.set_context("folder", value)
            
        elif mode == "branch" and value:
            _clear_tag_if_needed()
            val_str = str(value)
            if val_str.startswith("date:"):
                mw.grid.set_context("date", val_str.replace("date:", ""))
            elif val_str.startswith("facecluster:"):
                branch_key = val_str.split(":", 1)[1]
                mw.grid.set_context("people", branch_key)

            else:
                mw.grid.set_context("branch", val_str)
        
        elif mode == "people" and value:
            # Load all images belonging to this face cluster
            try:
                paths = self.db.get_paths_for_cluster(self.project_id, value)
                if hasattr(mw, "grid") and hasattr(mw.grid, "display_thumbnails"):
                    mw.grid.display_thumbnails(paths)
                else:
                    print(f"[Sidebar] Unable to display thumbnails for people cluster {value}")
            except Exception as e:
                print(f"[Sidebar] Failed to open people cluster {value}: {e}")
                        
                
        elif mode == "date" and value:
            _clear_tag_if_needed()
            mw.grid.set_context("date", value)
        elif mode == "tag" and value:
            if hasattr(mw, "_apply_tag_filter"):
                mw._apply_tag_filter(value)

        # Video handlers
        elif mode == "videos" and value == "all":
            # Show all videos for current project
            print(f"[Sidebar] Displaying all videos for project {self.project_id}")
            try:
                from services.video_service import VideoService
                video_service = VideoService()
                videos = video_service.get_videos_by_project(self.project_id) if self.project_id else []
                paths = [v['path'] for v in videos]
                if hasattr(mw, "grid") and hasattr(mw.grid, "load_custom_paths"):
                    mw.grid.load_custom_paths(paths)
                    mw.statusBar().showMessage(f"🎬 Showing {len(videos)} videos")
                else:
                    print(f"[Sidebar] Unable to display videos - grid.load_custom_paths not found")
            except Exception as e:
                print(f"[Sidebar] Failed to load all videos: {e}")

        elif mode == "videos_duration" and value:
            # Filter videos by duration
            print(f"[Sidebar] Filtering videos by duration: {value}")
            try:
                from services.video_service import VideoService
                video_service = VideoService()
                videos = video_service.get_videos_by_project(self.project_id) if self.project_id else []

                if value == "short":
                    filtered = video_service.filter_by_duration(videos, max_seconds=30)
                    label = "short (< 30s)"
                elif value == "medium":
                    filtered = video_service.filter_by_duration(videos, min_seconds=30, max_seconds=300)
                    label = "medium (30s - 5min)"
                elif value == "long":
                    filtered = video_service.filter_by_duration(videos, min_seconds=300)
                    label = "long (> 5min)"
                else:
                    filtered = videos
                    label = value

                paths = [v['path'] for v in filtered]
                print(f"[Sidebar] Showing {len(filtered)} {label} videos")
                if hasattr(mw, "grid") and hasattr(mw.grid, "load_custom_paths"):
                    mw.grid.load_custom_paths(paths)
                    mw.statusBar().showMessage(f"🎬 Showing {len(filtered)} {label} videos")
                else:
                    print(f"[Sidebar] Unable to display filtered videos - grid.load_custom_paths not found")
            except Exception as e:
                print(f"[Sidebar] Failed to filter videos by duration: {e}")

        elif mode == "videos_resolution" and value:
            # Filter videos by resolution
            print(f"[Sidebar] Filtering videos by resolution: {value}")
            try:
                from services.video_service import VideoService
                video_service = VideoService()
                videos = video_service.get_videos_by_project(self.project_id) if self.project_id else []
                filtered = video_service.filter_by_resolution(videos, quality=value)
                paths = [v['path'] for v in filtered]

                quality_labels = {'sd': 'SD (< 720p)', 'hd': 'HD (720p)', 'fhd': 'Full HD (1080p)', '4k': '4K (2160p+)'}
                label = quality_labels.get(value, value)

                print(f"[Sidebar] Showing {len(filtered)} {label} videos")
                if hasattr(mw, "grid") and hasattr(mw.grid, "load_custom_paths"):
                    mw.grid.load_custom_paths(paths)
                    mw.statusBar().showMessage(f"🎬 Showing {len(filtered)} {label} videos")
                else:
                    print(f"[Sidebar] Unable to display filtered videos - grid.load_custom_paths not found")
            except Exception as e:
                print(f"[Sidebar] Failed to filter videos by resolution: {e}")

        elif mode == "videos_codec" and value:
            # Filter videos by codec (Option 7)
            print(f"[Sidebar] Filtering videos by codec: {value}")
            try:
                from services.video_service import VideoService
                video_service = VideoService()
                videos = video_service.get_videos_by_project(self.project_id) if self.project_id else []

                # Map codec keys to codec names
                codec_map = {
                    'h264': ['h264', 'avc'],
                    'hevc': ['hevc', 'h265'],
                    'vp9': ['vp9'],
                    'av1': ['av1'],
                    'mpeg4': ['mpeg4', 'xvid', 'divx']
                }

                codecs_to_filter = codec_map.get(value, [value])
                filtered = video_service.filter_by_codec(videos, codecs=codecs_to_filter)
                paths = [v['path'] for v in filtered]

                codec_labels = {
                    'h264': 'H.264 / AVC',
                    'hevc': 'H.265 / HEVC',
                    'vp9': 'VP9',
                    'av1': 'AV1',
                    'mpeg4': 'MPEG-4'
                }
                label = codec_labels.get(value, value.upper())

                print(f"[Sidebar] Showing {len(filtered)} {label} videos")
                if hasattr(mw, "grid") and hasattr(mw.grid, "load_custom_paths"):
                    mw.grid.load_custom_paths(paths)
                    mw.statusBar().showMessage(f"🎞️ Showing {len(filtered)} {label} videos")
                else:
                    print(f"[Sidebar] Unable to display filtered videos - grid.load_custom_paths not found")
            except Exception as e:
                print(f"[Sidebar] Failed to filter videos by codec: {e}")

        elif mode == "videos_size" and value:
            # Filter videos by file size (Option 7)
            print(f"[Sidebar] Filtering videos by file size: {value}")
            try:
                from services.video_service import VideoService
                video_service = VideoService()
                videos = video_service.get_videos_by_project(self.project_id) if self.project_id else []
                filtered = video_service.filter_by_file_size(videos, size_range=value)
                paths = [v['path'] for v in filtered]

                size_labels = {
                    'small': 'Small (< 100MB)',
                    'medium': 'Medium (100MB - 1GB)',
                    'large': 'Large (1GB - 5GB)',
                    'xlarge': 'XLarge (> 5GB)'
                }
                label = size_labels.get(value, value)

                print(f"[Sidebar] Showing {len(filtered)} {label} videos")
                if hasattr(mw, "grid") and hasattr(mw.grid, "load_custom_paths"):
                    mw.grid.load_custom_paths(paths)
                    mw.statusBar().showMessage(f"📦 Showing {len(filtered)} {label} videos")
                else:
                    print(f"[Sidebar] Unable to display filtered videos - grid.load_custom_paths not found")
            except Exception as e:
                print(f"[Sidebar] Failed to filter videos by file size: {e}")

        elif mode == "videos_year" and value:
            # Filter videos by year (Option 7)
            print(f"[Sidebar] Filtering videos by year: {value}")
            try:
                from services.video_service import VideoService
                video_service = VideoService()
                videos = video_service.get_videos_by_project(self.project_id) if self.project_id else []
                filtered = video_service.filter_by_date(videos, year=int(value))
                paths = [v['path'] for v in filtered]

                print(f"[Sidebar] Showing {len(filtered)} videos from {value}")
                if hasattr(mw, "grid") and hasattr(mw.grid, "load_custom_paths"):
                    mw.grid.load_custom_paths(paths)
                    mw.statusBar().showMessage(f"📅 Showing {len(filtered)} videos from {value}")
                else:
                    print(f"[Sidebar] Unable to display filtered videos - grid.load_custom_paths not found")
            except Exception as e:
                print(f"[Sidebar] Failed to filter videos by year: {e}")

        elif mode == "videos_search" and value == "search":
            # Search videos
            from PySide6.QtWidgets import QInputDialog
            query, ok = QInputDialog.getText(self, "Search Videos", "Enter search term (filename or tags):")
            if ok and query:
                print(f"[Sidebar] Searching videos for: {query}")
                try:
                    from services.video_service import VideoService
                    video_service = VideoService()
                    videos = video_service.get_videos_by_project(self.project_id) if self.project_id else []
                    filtered = video_service.search_videos(videos, query)
                    paths = [v['path'] for v in filtered]
                    if hasattr(mw, "grid") and hasattr(mw.grid, "load_custom_paths"):
                        mw.grid.load_custom_paths(paths)
                        mw.statusBar().showMessage(f"🔍 Found {len(filtered)} videos matching '{query}'")
                    else:
                        print(f"[Sidebar] Unable to display search results - grid.load_custom_paths not found")
                except Exception as e:
                    print(f"[Sidebar] Failed to search videos: {e}")

        def _reflow():
            try:
                g = mw.grid
                if hasattr(g, "_apply_zoom_geometry"):
                    g._apply_zoom_geometry()
                g.list_view.doItemsLayout()
                g.list_view.viewport().update()
            except Exception as e:
                print(f"[Sidebar] reflow failed: {e}")

        QTimer.singleShot(0, _reflow)


    # ---- tree mode builder ----
    def _build_tree_model(self):
        # Build tree synchronously for folders (counts populated right away),
        # and register branch targets for async fill to keep responsiveness.

        # CRITICAL: Prevent concurrent rebuilds that cause Qt crashes during rapid project switching
        # Similar to grid reload() guard pattern
        if getattr(self, '_rebuilding_tree', False):
            print("[Sidebar] _build_tree_model() blocked - already rebuilding (prevents concurrent rebuild crash)")
            return

        try:
            self._rebuilding_tree = True

            # CRITICAL FIX: Cancel any pending count workers before rebuilding
            self._list_worker_gen = (self._list_worker_gen + 1) % 1_000_000

            # CRITICAL FIX: Process pending deleteLater() and worker callbacks before rebuilding
            # This ensures:
            # 1. Old widgets from tabs are fully cleaned up
            # 2. Async count workers have checked their generation and aborted
            # 3. No pending model item updates are in the event queue
            # Without this, workers can access model items during clear() causing crashes
            if self._initialized:
                from PySide6.QtCore import QCoreApplication
                print("[Sidebar] Processing pending events before model clear")
                QCoreApplication.processEvents()
                # Process events twice to catch worker callbacks scheduled during first pass
                QCoreApplication.processEvents()
                print("[Sidebar] Pending events processed")

            # CRITICAL FIX: Detach model from view before clearing to prevent Qt segfault
            # Qt can crash if the view has active selections/iterators when model is cleared
            print("[Sidebar] Detaching old model from tree view")
            self.tree.setModel(None)

            # Clear selection to release any Qt internal references
            if hasattr(self.tree, 'selectionModel') and self.tree.selectionModel():
                try:
                    self.tree.selectionModel().clear()
                except Exception:
                    pass

            # CRITICAL FIX: Create a completely fresh model instead of clearing the old one
            # This is safer than model.clear() which can cause Qt C++ segfaults
            print("[Sidebar] Creating fresh model (avoiding Qt segfault)")
            old_model = self.model
            self.model = QStandardItemModel(self.tree)
            self.model.setHorizontalHeaderLabels(["Folder / Branch", "Photos"])

            # Schedule old model for deletion (let Qt clean it up safely)
            if old_model is not None:
                try:
                    old_model.deleteLater()
                except Exception as e:
                    print(f"[Sidebar] Warning: Could not schedule old model for deletion: {e}")

            # Attach the fresh model to the tree view
            print("[Sidebar] Attaching fresh model to tree view")
            self.tree.setModel(self.model)

            self._count_targets = []
            try:
                # Get total photo count for displaying on top-level sections
                total_photos = 0
                if self.project_id:
                    try:
                        # Get count from "all" branch
                        all_photos = self.db.get_project_images(self.project_id, branch_key='all')
                        total_photos = len(all_photos) if all_photos else 0
                    except Exception as e:
                        print(f"[Sidebar] Could not get total photo count: {e}")
                        total_photos = 0

                # Helper to create styled count item
                def _make_count_item(count_val):
                    item = QStandardItem(str(count_val) if count_val else "")
                    item.setEditable(False)
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    item.setForeground(QColor("#BBBBBB"))
                    return item

                branch_root = QStandardItem("🌿 Branches")
                branch_root.setEditable(False)
                branch_count_item = _make_count_item(total_photos)
                self.model.appendRow([branch_root, branch_count_item])
                branches = list_branches(self.project_id) if self.project_id else []
                for b in branches:
                    name_item = QStandardItem(b["display_name"])
                    count_item = QStandardItem("")
                    name_item.setEditable(False)
                    count_item.setEditable(False)
                    name_item.setData("branch", Qt.UserRole)
                    name_item.setData(b["branch_key"], Qt.UserRole + 1)
                    count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    count_item.setForeground(QColor("#BBBBBB"))
                    branch_root.appendRow([name_item, count_item])
                    # register branch for async counts
                    self._count_targets.append(("branch", b["branch_key"], name_item, count_item))

                quick_root = QStandardItem("📅 Quick Dates")
                quick_root.setEditable(False)
                quick_count_item = _make_count_item(total_photos)
                self.model.appendRow([quick_root, quick_count_item])
                try:
                    quick_rows = self.db.get_quick_date_counts(project_id=self.project_id)
                except Exception:
                    quick_rows = []
                for row in quick_rows:
                    name_item = QStandardItem(row["label"])
                    count_item = QStandardItem(str(row["count"]) if row and row.get("count") else "")
                    name_item.setEditable(False)
                    count_item.setEditable(False)
                    name_item.setData("branch", Qt.UserRole)
                    name_item.setData(row["key"], Qt.UserRole + 1)
                    count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    count_item.setForeground(QColor("#BBBBBB"))
                    quick_root.appendRow([name_item, count_item])

                # IMPORTANT FIX: use synchronous folder population as in the previous working version,
                # so folder counts are calculated and displayed immediately.
                folder_root = QStandardItem("📁 Folders")
                folder_root.setEditable(False)
                folder_count_item = _make_count_item(total_photos)
                self.model.appendRow([folder_root, folder_count_item])
                # synchronous (restores the previous working behavior)
                self._add_folder_items(folder_root, None)

                self._build_by_date_section()
                self._build_tag_section()

                # >>> NEW: 🎬 Videos section
                try:
                    from services.video_service import VideoService
                    video_service = VideoService()
                    videos = video_service.get_videos_by_project(self.project_id) if self.project_id else []
                    total_videos = len(videos)
                except Exception as e:
                    print("[Sidebar] Failed to load videos:", e)
                    total_videos = 0
                    videos = []

                if videos:
                    root_name_item = QStandardItem("🎬 Videos")
                    root_cnt_item = _make_count_item(total_videos)
                    root_name_item.setEditable(False)
                    root_cnt_item.setEditable(False)
                    self.model.appendRow([root_name_item, root_cnt_item])

                    # Add "All Videos" option
                    all_videos_item = QStandardItem("All Videos")
                    all_videos_item.setEditable(False)
                    all_videos_item.setData("videos", Qt.UserRole)
                    all_videos_item.setData("all", Qt.UserRole + 1)
                    all_count = QStandardItem(str(total_videos))
                    all_count.setEditable(False)
                    all_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    all_count.setForeground(QColor("#888888"))
                    root_name_item.appendRow([all_videos_item, all_count])

                    # 🎯 Filter by Duration
                    duration_parent = QStandardItem("⏱️ By Duration")
                    duration_parent.setEditable(False)
                    duration_count = QStandardItem("")
                    duration_count.setEditable(False)
                    root_name_item.appendRow([duration_parent, duration_count])

                    # Count videos by duration
                    short_videos = [v for v in videos if v.get('duration_seconds') and v['duration_seconds'] < 30]
                    medium_videos = [v for v in videos if v.get('duration_seconds') and 30 <= v['duration_seconds'] < 300]
                    long_videos = [v for v in videos if v.get('duration_seconds') and v['duration_seconds'] >= 300]

                    # Short videos (< 30s)
                    short_item = QStandardItem("Short (< 30s)")
                    short_item.setEditable(False)
                    short_item.setData("videos_duration", Qt.UserRole)
                    short_item.setData("short", Qt.UserRole + 1)
                    short_count = QStandardItem(str(len(short_videos)))
                    short_count.setEditable(False)
                    short_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    short_count.setForeground(QColor("#888888"))
                    duration_parent.appendRow([short_item, short_count])

                    # Medium videos (30s - 5min)
                    medium_item = QStandardItem("Medium (30s - 5min)")
                    medium_item.setEditable(False)
                    medium_item.setData("videos_duration", Qt.UserRole)
                    medium_item.setData("medium", Qt.UserRole + 1)
                    medium_count = QStandardItem(str(len(medium_videos)))
                    medium_count.setEditable(False)
                    medium_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    medium_count.setForeground(QColor("#888888"))
                    duration_parent.appendRow([medium_item, medium_count])

                    # Long videos (> 5min)
                    long_item = QStandardItem("Long (> 5min)")
                    long_item.setEditable(False)
                    long_item.setData("videos_duration", Qt.UserRole)
                    long_item.setData("long", Qt.UserRole + 1)
                    long_count = QStandardItem(str(len(long_videos)))
                    long_count.setEditable(False)
                    long_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    long_count.setForeground(QColor("#888888"))
                    duration_parent.appendRow([long_item, long_count])

                    # 📺 Filter by Resolution
                    res_parent = QStandardItem("📺 By Resolution")
                    res_parent.setEditable(False)
                    res_count = QStandardItem("")
                    res_count.setEditable(False)
                    root_name_item.appendRow([res_parent, res_count])

                    # Count videos by resolution
                    sd_videos = [v for v in videos if v.get('width') and v.get('height') and v['height'] < 720]
                    hd_videos = [v for v in videos if v.get('height') and 720 <= v['height'] < 1080]
                    fhd_videos = [v for v in videos if v.get('height') and 1080 <= v['height'] < 2160]
                    uhd_videos = [v for v in videos if v.get('height') and v['height'] >= 2160]

                    # SD videos (< 720p)
                    sd_item = QStandardItem("SD (< 720p)")
                    sd_item.setEditable(False)
                    sd_item.setData("videos_resolution", Qt.UserRole)
                    sd_item.setData("sd", Qt.UserRole + 1)
                    sd_cnt = QStandardItem(str(len(sd_videos)))
                    sd_cnt.setEditable(False)
                    sd_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    sd_cnt.setForeground(QColor("#888888"))
                    res_parent.appendRow([sd_item, sd_cnt])

                    # HD videos (720p)
                    hd_item = QStandardItem("HD (720p)")
                    hd_item.setEditable(False)
                    hd_item.setData("videos_resolution", Qt.UserRole)
                    hd_item.setData("hd", Qt.UserRole + 1)
                    hd_cnt = QStandardItem(str(len(hd_videos)))
                    hd_cnt.setEditable(False)
                    hd_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    hd_cnt.setForeground(QColor("#888888"))
                    res_parent.appendRow([hd_item, hd_cnt])

                    # Full HD videos (1080p)
                    fhd_item = QStandardItem("Full HD (1080p)")
                    fhd_item.setEditable(False)
                    fhd_item.setData("videos_resolution", Qt.UserRole)
                    fhd_item.setData("fhd", Qt.UserRole + 1)
                    fhd_cnt = QStandardItem(str(len(fhd_videos)))
                    fhd_cnt.setEditable(False)
                    fhd_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    fhd_cnt.setForeground(QColor("#888888"))
                    res_parent.appendRow([fhd_item, fhd_cnt])

                    # 4K videos (2160p+)
                    uhd_item = QStandardItem("4K (2160p+)")
                    uhd_item.setEditable(False)
                    uhd_item.setData("videos_resolution", Qt.UserRole)
                    uhd_item.setData("4k", Qt.UserRole + 1)
                    uhd_cnt = QStandardItem(str(len(uhd_videos)))
                    uhd_cnt.setEditable(False)
                    uhd_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    uhd_cnt.setForeground(QColor("#888888"))
                    res_parent.appendRow([uhd_item, uhd_cnt])

                    # 🎞️ Filter by Codec (Option 7)
                    codec_parent = QStandardItem("🎞️ By Codec")
                    codec_parent.setEditable(False)
                    codec_count = QStandardItem("")
                    codec_count.setEditable(False)
                    root_name_item.appendRow([codec_parent, codec_count])

                    # Count videos by codec
                    h264_videos = [v for v in videos if v.get('codec') and v['codec'].lower() in ['h264', 'avc']]
                    hevc_videos = [v for v in videos if v.get('codec') and v['codec'].lower() in ['hevc', 'h265']]
                    vp9_videos = [v for v in videos if v.get('codec') and v['codec'].lower() == 'vp9']
                    av1_videos = [v for v in videos if v.get('codec') and v['codec'].lower() == 'av1']
                    mpeg4_videos = [v for v in videos if v.get('codec') and v['codec'].lower() in ['mpeg4', 'xvid', 'divx']]

                    # H.264
                    h264_item = QStandardItem("H.264 / AVC")
                    h264_item.setEditable(False)
                    h264_item.setData("videos_codec", Qt.UserRole)
                    h264_item.setData("h264", Qt.UserRole + 1)
                    h264_cnt = QStandardItem(str(len(h264_videos)))
                    h264_cnt.setEditable(False)
                    h264_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    h264_cnt.setForeground(QColor("#888888"))
                    codec_parent.appendRow([h264_item, h264_cnt])

                    # H.265 / HEVC
                    hevc_item = QStandardItem("H.265 / HEVC")
                    hevc_item.setEditable(False)
                    hevc_item.setData("videos_codec", Qt.UserRole)
                    hevc_item.setData("hevc", Qt.UserRole + 1)
                    hevc_cnt = QStandardItem(str(len(hevc_videos)))
                    hevc_cnt.setEditable(False)
                    hevc_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    hevc_cnt.setForeground(QColor("#888888"))
                    codec_parent.appendRow([hevc_item, hevc_cnt])

                    # VP9
                    vp9_item = QStandardItem("VP9")
                    vp9_item.setEditable(False)
                    vp9_item.setData("videos_codec", Qt.UserRole)
                    vp9_item.setData("vp9", Qt.UserRole + 1)
                    vp9_cnt = QStandardItem(str(len(vp9_videos)))
                    vp9_cnt.setEditable(False)
                    vp9_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    vp9_cnt.setForeground(QColor("#888888"))
                    codec_parent.appendRow([vp9_item, vp9_cnt])

                    # AV1
                    av1_item = QStandardItem("AV1")
                    av1_item.setEditable(False)
                    av1_item.setData("videos_codec", Qt.UserRole)
                    av1_item.setData("av1", Qt.UserRole + 1)
                    av1_cnt = QStandardItem(str(len(av1_videos)))
                    av1_cnt.setEditable(False)
                    av1_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    av1_cnt.setForeground(QColor("#888888"))
                    codec_parent.appendRow([av1_item, av1_cnt])

                    # MPEG-4
                    mpeg4_item = QStandardItem("MPEG-4")
                    mpeg4_item.setEditable(False)
                    mpeg4_item.setData("videos_codec", Qt.UserRole)
                    mpeg4_item.setData("mpeg4", Qt.UserRole + 1)
                    mpeg4_cnt = QStandardItem(str(len(mpeg4_videos)))
                    mpeg4_cnt.setEditable(False)
                    mpeg4_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    mpeg4_cnt.setForeground(QColor("#888888"))
                    codec_parent.appendRow([mpeg4_item, mpeg4_cnt])

                    # 📦 Filter by File Size (Option 7)
                    size_parent = QStandardItem("📦 By File Size")
                    size_parent.setEditable(False)
                    size_count = QStandardItem("")
                    size_count.setEditable(False)
                    root_name_item.appendRow([size_parent, size_count])

                    # Count videos by file size
                    small_videos = [v for v in videos if v.get('size_kb') and v['size_kb'] / 1024 < 100]
                    medium_size_videos = [v for v in videos if v.get('size_kb') and 100 <= v['size_kb'] / 1024 < 1024]
                    large_videos = [v for v in videos if v.get('size_kb') and 1024 <= v['size_kb'] / 1024 < 5120]
                    xlarge_videos = [v for v in videos if v.get('size_kb') and v['size_kb'] / 1024 >= 5120]

                    # Small (< 100MB)
                    small_size_item = QStandardItem("Small (< 100MB)")
                    small_size_item.setEditable(False)
                    small_size_item.setData("videos_size", Qt.UserRole)
                    small_size_item.setData("small", Qt.UserRole + 1)
                    small_size_cnt = QStandardItem(str(len(small_videos)))
                    small_size_cnt.setEditable(False)
                    small_size_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    small_size_cnt.setForeground(QColor("#888888"))
                    size_parent.appendRow([small_size_item, small_size_cnt])

                    # Medium (100MB - 1GB)
                    medium_size_item = QStandardItem("Medium (100MB - 1GB)")
                    medium_size_item.setEditable(False)
                    medium_size_item.setData("videos_size", Qt.UserRole)
                    medium_size_item.setData("medium", Qt.UserRole + 1)
                    medium_size_cnt = QStandardItem(str(len(medium_size_videos)))
                    medium_size_cnt.setEditable(False)
                    medium_size_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    medium_size_cnt.setForeground(QColor("#888888"))
                    size_parent.appendRow([medium_size_item, medium_size_cnt])

                    # Large (1GB - 5GB)
                    large_size_item = QStandardItem("Large (1GB - 5GB)")
                    large_size_item.setEditable(False)
                    large_size_item.setData("videos_size", Qt.UserRole)
                    large_size_item.setData("large", Qt.UserRole + 1)
                    large_size_cnt = QStandardItem(str(len(large_videos)))
                    large_size_cnt.setEditable(False)
                    large_size_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    large_size_cnt.setForeground(QColor("#888888"))
                    size_parent.appendRow([large_size_item, large_size_cnt])

                    # XLarge (> 5GB)
                    xlarge_size_item = QStandardItem("XLarge (> 5GB)")
                    xlarge_size_item.setEditable(False)
                    xlarge_size_item.setData("videos_size", Qt.UserRole)
                    xlarge_size_item.setData("xlarge", Qt.UserRole + 1)
                    xlarge_size_cnt = QStandardItem(str(len(xlarge_videos)))
                    xlarge_size_cnt.setEditable(False)
                    xlarge_size_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    xlarge_size_cnt.setForeground(QColor("#888888"))
                    size_parent.appendRow([xlarge_size_item, xlarge_size_cnt])

                    # 📅 Filter by Date (Option 7)
                    from datetime import datetime
                    date_parent = QStandardItem("📅 By Date")
                    date_parent.setEditable(False)
                    date_count = QStandardItem("")
                    date_count.setEditable(False)
                    root_name_item.appendRow([date_parent, date_count])

                    # Count videos by year (last 5 years)
                    current_year = datetime.now().year
                    for year in range(current_year, current_year - 5, -1):
                        year_videos = []
                        for v in videos:
                            date_str = v.get('date_taken') or v.get('modified')
                            if date_str:
                                try:
                                    video_year = int(date_str.split('-')[0])
                                    if video_year == year:
                                        year_videos.append(v)
                                except (ValueError, IndexError):
                                    pass

                        year_item = QStandardItem(str(year))
                        year_item.setEditable(False)
                        year_item.setData("videos_year", Qt.UserRole)
                        year_item.setData(year, Qt.UserRole + 1)
                        year_cnt = QStandardItem(str(len(year_videos)))
                        year_cnt.setEditable(False)
                        year_cnt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                        year_cnt.setForeground(QColor("#888888"))
                        date_parent.appendRow([year_item, year_cnt])

                    # 🔍 Search Videos
                    search_item = QStandardItem("🔍 Search Videos...")
                    search_item.setEditable(False)
                    search_item.setData("videos_search", Qt.UserRole)
                    search_item.setData("search", Qt.UserRole + 1)
                    search_count = QStandardItem("")
                    search_count.setEditable(False)
                    root_name_item.appendRow([search_item, search_count])

                    print(f"[Sidebar] Added 🎬 Videos section with {total_videos} videos and filters.")
                # <<< NEW

                # >>> NEW: 👥 People / Face Albums section
                try:
                    clusters = self.db.get_face_clusters(self.project_id)
                except Exception as e:
                    print("[Sidebar] get_face_clusters failed:", e)
                    clusters = []

                if clusters:
                    root_name_item = QStandardItem("👥 People")
                    root_cnt_item = QStandardItem("")
                    root_name_item.setEditable(False)
                    root_cnt_item.setEditable(False)
                    self.model.appendRow([root_name_item, root_cnt_item])

                    for row in clusters:
                        name = row.get("display_name") or row.get("branch_key")
                        count = row.get("member_count", 0)
                        rep = row.get("rep_path", "")
                        label = f"{name} ({count})"

                        name_item = QStandardItem(label)
                        name_item.setEditable(False)
                        name_item.setData("people", Qt.UserRole)
                        name_item.setData(row["branch_key"], Qt.UserRole + 1)
                        name_item.setToolTip(rep)

                        count_item = QStandardItem(str(count))
                        count_item.setEditable(False)
                        count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                        count_item.setForeground(QColor("#888888"))

                        root_name_item.appendRow([name_item, count_item])

                    print(f"[Sidebar] Added 👥 People section with {len(clusters)} clusters.")
                # <<< NEW

                for r in range(self.model.rowCount()):
                    idx = self.model.index(r, 0)
                    self.tree.expand(idx)

                # Force column width recalculation after building tree
                QTimer.singleShot(0, self._recalculate_columns)
            except Exception as e:
                QMessageBox.warning(self, "Load Error", f"Failed to build navigation:\n{e}")

            # populate branch counts asynchronously while folder counts are already set
            if self._count_targets:
                print(f"[Sidebar] starting async count population for {len(self._count_targets)} branch targets")
                self._async_populate_counts()

        finally:
            # Always reset flag even if exception occurs
            self._rebuilding_tree = False

    def _add_folder_items_async(self, parent_item, parent_id=None):
        # kept for folder-tab lazy usage if desired, but not used for tree-mode counts
        rows = self.db.get_child_folders(parent_id, project_id=self.project_id)
        for row in rows:
            name = row["name"]
            fid = row["id"]
            name_item = QStandardItem(f"📁 {name}")
            count_item = QStandardItem("")
            name_item.setEditable(False)
            count_item.setEditable(False)
            name_item.setData("folder", Qt.UserRole)
            name_item.setData(fid, Qt.UserRole + 1)
            count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            count_item.setForeground(QColor("#888888"))
            parent_item.appendRow([name_item, count_item])
            # register for async, but tree-mode uses _add_folder_items synchronous
            self._count_targets.append(("folder", fid, name_item, count_item))
            self._add_folder_items_async(name_item, fid)


    def _apply_counts(self, results):  # with async_populate_counts_priorFix
        try:
            for name_item, count_item, cnt in results:
                try:
                    text = str(cnt) if cnt is not None else ""
                    if isinstance(count_item, QStandardItem):
                        try:
                            count_item.setText(text)
                        except Exception:
                            try:
                                idx = count_item.index()
                                if idx.isValid():
                                    self.model.setData(idx, text)
                            except Exception:
                                pass
                        continue
                    if count_item is not None and hasattr(count_item, "setText") and not isinstance(count_item, QStandardItem):
                        try:
                            count_item.setText(1, text)
                        except Exception:
                            pass
                        continue
                    if name_item is not None:
                        try:
                            if isinstance(name_item, QStandardItem):
                                idx = name_item.index()
                                if idx.isValid():
                                    sibling_idx = idx.sibling(idx.row(), 1)
                                    self.model.setData(sibling_idx, text)
                                    continue
                        except Exception:
                            pass
                        try:
                            if hasattr(name_item, "setText") and not isinstance(name_item, QStandardItem):
                                name_item.setText(1, text)
                                continue
                        except Exception:
                            pass
                except Exception:
                    pass
            try:
                self.tree.viewport().update()
            except Exception:
                pass

            # Recalculate columns after count updates
            QTimer.singleShot(0, self._recalculate_columns)

            print("[Sidebar][counts applied] updated UI with counts")
        except Exception:
            traceback.print_exc()


    def _async_populate_counts(self):
        targets = list(self._count_targets)
        if not targets:
            print("[Sidebar][counts] no targets to populate")
            return

        # Bump generation to invalidate any previous workers
        self._list_worker_gen = (self._list_worker_gen + 1) % 1_000_000
        current_gen = self._list_worker_gen

        # CRITICAL FIX: Extract only data (typ, key), NOT Qt objects, before passing to worker
        data_only = [(typ, key) for typ, key, name_item, count_item in targets]

        def worker():
            results = []
            try:
                print(f"[Sidebar][counts worker gen={current_gen}] running for {len(data_only)} targets...")
                # Work only with data, NO Qt objects in worker thread
                for typ, key in data_only:
                    try:
                        cnt = 0
                        if typ == "branch":
                            if hasattr(self.db, "count_images_by_branch"):
                                cnt = int(self.db.count_images_by_branch(self.project_id, key) or 0)
                            else:
                                rows = self.db.get_images_by_branch(self.project_id, key) or []
                                cnt = len(rows)

                        elif typ == "folder":
                            # Use recursive count including all subfolders
                            if hasattr(self.db, "get_image_count_recursive"):
                                cnt = int(self.db.get_image_count_recursive(key) or 0)
                            elif hasattr(self.db, "count_for_folder"):
                                cnt = int(self.db.count_for_folder(key, project_id=self.project_id) or 0)
                            else:
                                with self.db._connect() as conn:
                                    cur = conn.cursor()
                                    cur.execute("SELECT COUNT(*) FROM photo_metadata WHERE folder_id=?", (key,))
                                    v = cur.fetchone()
                                    cnt = int(v[0]) if v else 0

                        # IMPORTANT: Only pass data (typ, key, cnt), NOT Qt objects
                        results.append((typ, key, cnt))
                    except Exception:
                        traceback.print_exc()
                        results.append((typ, key, 0))
                print(f"[Sidebar][counts worker gen={current_gen}] finished scanning targets, scheduling UI update")
            except Exception:
                traceback.print_exc()
            # Schedule UI update in main thread with generation check
            QTimer.singleShot(0, lambda: self._apply_counts_defensive(results, current_gen))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_counts_defensive(self, results, gen=None):
        """
        Apply counts to UI by finding QStandardItems in model by key.
        This method runs in the MAIN THREAD (called via QTimer.singleShot).

        Args:
            results: List of (typ, key, cnt) tuples from worker thread
            gen: Generation number to check if results are stale
        """
        # Check if this worker is stale
        if gen is not None and gen != self._list_worker_gen:
            print(f"[Sidebar][counts] Ignoring stale worker results (gen={gen}, current={self._list_worker_gen})")
            return

        # CRITICAL SAFETY: Check if model is detached (being rebuilt)
        # If model is not attached to tree view, skip update to prevent crashes
        if self.tree.model() != self.model:
            print("[Sidebar][counts] Model is detached (rebuilding), skipping count update")
            return

        # Safety check: ensure model is valid before accessing
        if not self.model or self.model.rowCount() == 0:
            print("[Sidebar][counts] Model is empty or invalid, skipping count update")
            return

        try:
            for typ, key, cnt in results:
                text = str(cnt) if cnt is not None else ""

                # Find the model item by key and update count column
                try:
                    found_name, found_count = self._find_model_item_by_key(key)

                    # Try updating count_item directly
                    if found_count is not None:
                        try:
                            found_count.setText(text)
                            continue
                        except Exception:
                            # Fallback: try model-level setData on its index
                            try:
                                idx = found_count.index()
                                if idx.isValid():
                                    self.model.setData(idx, text)
                                    continue
                            except Exception:
                                pass

                    # Fallback: if only found_name is present, set its sibling column
                    if found_name is not None:
                        try:
                            idx = found_name.index()
                            if idx.isValid():
                                sib = idx.sibling(idx.row(), 1)
                                self.model.setData(sib, text)
                                continue
                        except Exception:
                            pass

                except Exception:
                    traceback.print_exc()

            # Refresh view to show updated counts
            try:
                self.tree.viewport().update()
            except Exception:
                pass

            # Recalculate columns after count updates
            QTimer.singleShot(0, self._recalculate_columns)

            print("[Sidebar][counts applied] updated UI with counts")
        except Exception:
            traceback.print_exc()

    def _add_folder_items(self, parent_item, parent_id=None):
        # CRITICAL FIX: Pass project_id to filter folders and counts by project
        try:
            rows = self.db.get_child_folders(parent_id, project_id=self.project_id)
        except Exception as e:
            print(f"[Sidebar] Error in get_child_folders: {e}")
            import traceback
            traceback.print_exc()
            rows = []

        for row in rows:
            try:
                name = row["name"]
                fid = row["id"]

                if hasattr(self.db, "get_image_count_recursive"):
                    # CRITICAL FIX: Pass project_id to count only photos from this project
                    try:
                        photo_count = int(self.db.get_image_count_recursive(fid, project_id=self.project_id) or 0)
                    except Exception as e:
                        print(f"[Sidebar] Error in get_image_count_recursive for folder {fid}: {e}")
                        photo_count = 0
                else:
                    photo_count = self._get_photo_count(fid)

                name_item = QStandardItem(f"📁 {name}")
                count_item = QStandardItem(str(photo_count))
                count_item.setText(f"{photo_count:>5}")
                name_item.setEditable(False)
                count_item.setEditable(False)
                name_item.setData("folder", Qt.UserRole)
                name_item.setData(fid, Qt.UserRole + 1)
                count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                count_item.setForeground(QColor("#888888"))
                parent_item.appendRow([name_item, count_item])

                # Recursive call with error handling
                self._add_folder_items(name_item, fid)
            except Exception as e:
                print(f"[Sidebar] Error adding folder item: {e}")
                import traceback
                traceback.print_exc()
                continue


    def _build_by_date_section(self):
        from PySide6.QtGui import QStandardItem, QColor
        from PySide6.QtCore import Qt
        try:
            hier = self.db.get_date_hierarchy(project_id=self.project_id)
        except Exception:
            return
        if not hier or not isinstance(hier, dict):
            return

        root_name_item = QStandardItem("📅 By Date")
        root_cnt_item = QStandardItem("")
        for it in (root_name_item, root_cnt_item):
            it.setEditable(False)
        self.model.appendRow([root_name_item, root_cnt_item])

        def _cnt_item(num):
            c = QStandardItem("" if not num else str(num))
            c.setEditable(False)
            c.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            c.setForeground(QColor("#888888"))
            return c

        for year in sorted(hier.keys(), key=lambda y: int(str(y))):
            try:
                y_count = self.db.count_for_year(year, project_id=self.project_id)
            except Exception:
                y_count = 0
            y_item = QStandardItem(str(year))
            y_item.setEditable(False)
            y_item.setData("branch", Qt.UserRole)
            y_item.setData(f"date:{year}", Qt.UserRole + 1)
            root_name_item.appendRow([y_item, _cnt_item(y_count)])

            months = hier.get(year, {})
            if not isinstance(months, dict):
                continue

            for month in sorted(months.keys(), key=lambda m: int(str(m))):
                m_label = f"{int(month):02d}"
                try:
                    m_count = self.db.count_for_month(year, month, project_id=self.project_id)
                except Exception:
                    m_count = 0
                m_item = QStandardItem(m_label)
                m_item.setEditable(False)
                m_item.setData("branch", Qt.UserRole)
                m_item.setData(f"date:{year}-{m_label}", Qt.UserRole + 1)
                y_item.appendRow([m_item, _cnt_item(m_count)])

                day_ymd_list = months.get(month, []) or []
                day_numbers = []
                for ymd in day_ymd_list:
                    try:
                        dd = str(ymd).split("-")[2]
                        day_numbers.append(int(dd))
                    except Exception:
                        pass
                for day in sorted(set(day_numbers)):
                    d_label = f"{int(day):02d}"
                    ymd = f"{year}-{m_label}-{d_label}"
                    try:
                        d_count = self.db.count_for_day(ymd, project_id=self.project_id)
                    except Exception:
                        d_count = 0
                    d_item = QStandardItem(d_label)
                    d_item.setEditable(False)
                    d_item.setData("branch", Qt.UserRole)
                    d_item.setData(f"date:{ymd}", Qt.UserRole + 1)
                    m_item.appendRow([d_item, _cnt_item(d_count)])

    def _build_tag_section(self):
        try:
            if hasattr(self.db, "get_all_tags_with_counts"):
                tag_rows = self.db.get_all_tags_with_counts()
            else:
                tag_rows = [(t, 0) for t in self.db.get_all_tags()]
        except Exception:
            tag_rows = []

        if not tag_rows:
            return

        root_name_item = QStandardItem("🏷️ Tags")
        root_count_item = QStandardItem("")
        root_name_item.setEditable(False)
        root_count_item.setEditable(False)
        self.model.appendRow([root_name_item, root_count_item])

        for tag_name, count in tag_rows:
            text = tag_name
            count_text = str(count) if count else ""

            name_item = QStandardItem(text)
            count_item = QStandardItem(count_text)
            name_item.setEditable(False)
            count_item.setEditable(False)
            count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            count_item.setForeground(QColor("#888888"))

            name_item.setData("tag", Qt.UserRole)
            name_item.setData(tag_name, Qt.UserRole + 1)

            root_name_item.appendRow([name_item, count_item])

    
    def reload_tags_only(self):
        """
        Reload tags in both list mode (tree) and tabs mode.

        ARCHITECTURE: UI Layer → TagService → TagRepository → Database
        """
        try:
            # Use TagService for proper layered architecture
            tag_service = get_tag_service()
            # CRITICAL: Pass project_id to filter tags by current project (Schema v3.0.0)
            tag_rows = tag_service.get_all_tags_with_counts(self.project_id)
            print(f"[Sidebar] reload_tags_only → got {len(tag_rows)} tags for project_id={self.project_id}")
        except Exception as e:
            print(f"[Sidebar] reload_tags_only skipped: {e}")
            return

        # Update tree view (list mode)
        tag_root = self._find_root_item("🏷️ Tags")
        if tag_root is None:
            tag_root = QStandardItem("🏷️ Tags")
            count_col = QStandardItem("")
            tag_root.setEditable(False)
            count_col.setEditable(False)
            self.model.appendRow([tag_root, count_col])

        while tag_root.rowCount() > 0:
            tag_root.removeRow(0)

        for tag_name, count in tag_rows:
            name_item = QStandardItem(tag_name)
            cnt_item = QStandardItem(str(count) if count else "")
            name_item.setEditable(False)
            cnt_item.setEditable(False)
            cnt_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            cnt_item.setForeground(QColor("#888888"))

            name_item.setData("tag", Qt.UserRole)
            name_item.setData(tag_name, Qt.UserRole + 1)

            tag_root.appendRow([name_item, cnt_item])

        self.tree.expand(self.model.indexFromItem(tag_root))
        self.tree.viewport().update()

        # Also refresh tabs mode if it's active
        if hasattr(self, 'tabs_controller') and self.tabs_controller:
            mode = self._effective_display_mode()
            if mode == "tabs":
                # Refresh just the tags tab
                try:
                    if hasattr(self.tabs_controller, 'refresh_tab'):
                        self.tabs_controller.refresh_tab("tags")
                    else:
                        # Fallback: refresh all tabs
                        self.tabs_controller.refresh_all(force=True)
                except Exception as e:
                    print(f"[Sidebar] Failed to refresh tags tab: {e}")


    def _on_folder_selected(self, folder_id: int):
        if hasattr(self, "on_folder_selected") and callable(self.on_folder_selected):
            self.on_folder_selected(folder_id)

    def set_project(self, project_id: int):
        self.project_id = project_id
        self.tabs_controller.set_project(project_id)   # <-- delegate
        self.reload()

    def _show_menu(self, pos: QPoint):
        index = self.tree.indexAt(pos)
        if not index.isValid():
            return
        index = index.sibling(index.row(), 0)
        item = self.model.itemFromIndex(index)
        if not item:
            return

        mode = item.data(Qt.UserRole)
        value = item.data(Qt.UserRole + 1)
        label = item.text().strip()
        db = self.db
        menu = QMenu(self)

        if mode == "tag" and isinstance(value, str):
            tag_name = value
            act_filter = menu.addAction(f"Filter by tag: {tag_name}")
            menu.addSeparator()
            act_rename = menu.addAction("✏️ Rename Tag…")
            act_delete = menu.addAction("🗑 Delete Tag")

            chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if chosen is act_filter:
                if hasattr(self.parent(), "_apply_tag_filter"):
                    self.parent()._apply_tag_filter(tag_name)
            elif chosen is act_rename:
                from PySide6.QtWidgets import QInputDialog
                new_name, ok = QInputDialog.getText(self, "Rename Tag", "New name:", text=tag_name)
                if ok and new_name.strip() and new_name.strip() != tag_name:
                    try:
                        if hasattr(db, "rename_tag"):
                            db.rename_tag(tag_name, new_name.strip())
                        self.reload_tags_only()
                    except Exception as e:
                        QMessageBox.critical(self, "Rename Failed", str(e))
            elif chosen is act_delete:
                ret = QMessageBox.question(self, "Delete Tag",
                                           f"Delete tag '{tag_name}'?\nThis will unassign it from all photos.",
                                           QMessageBox.Yes | QMessageBox.No)
                if ret == QMessageBox.Yes:
                    try:
                        if hasattr(db, "delete_tag"):
                            db.delete_tag(tag_name)
                        self.reload_tags_only()
                    except Exception as e:
                        QMessageBox.critical(self, "Delete Failed", str(e))
            return

        if label.startswith("🏷️ Tags"):
            act_new = menu.addAction("➕ New Tag…")
            chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if chosen is act_new:
                from PySide6.QtWidgets import QInputDialog
                name, ok = QInputDialog.getText(self, "New Tag", "Tag name:")
                if ok and name.strip():
                    try:
                        if hasattr(db, "ensure_tag"):
                            db.ensure_tag(name.strip())
                        self.reload_tags_only()
                    except Exception as e:
                        QMessageBox.critical(self, "Create Failed", str(e))
            return

        act_export = menu.addAction("📁 Export Photos to Folder…")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is act_export:
            self._do_export(item.data(Qt.UserRole + 1))

    def _do_export(self, branch_key: str):
        dest = QFileDialog.getExistingDirectory(self, f"Export branch: {branch_key}")
        if not dest:
            return
        try:
            count = export_branch(self.project_id, branch_key, dest)
            QMessageBox.information(self, "Export Completed",
                                    f"Exported {count} photos from '{branch_key}' to:\n{dest}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))

    def _find_root_item(self, title: str):
        for row in range(self.model.rowCount()):
            it = self.model.item(row, 0)
            if not it:
                continue
            txt = it.text().strip()
            if txt.startswith(title):
                return it
        return None


    def collapse_all(self):
        try:
            self.tree.collapseAll()
            # Force column width recalculation after collapse
            QTimer.singleShot(0, self._recalculate_columns)
            try:
                if self.settings:
                    self.settings.set("sidebar_folded", True)
            except Exception:
                pass
        except Exception:
            pass

    def expand_all(self):
        try:
            for r in range(self.model.rowCount()):
                idx = self.model.index(r, 0)
                self.tree.expand(idx)
            # Force column width recalculation after expand
            QTimer.singleShot(0, self._recalculate_columns)
            try:
                if self.settings:
                    self.settings.set("sidebar_folded", False)
            except Exception:
                pass
        except Exception:
            pass

    def _recalculate_columns(self):
        """Force tree view to recalculate column widths"""
        try:
            header = self.tree.header()
            # Recalculate column 1 (counts) to fit content
            header.resizeSection(1, header.sectionSizeHint(1))
            # Force viewport update to ensure column 0 (names) uses remaining space
            self.tree.viewport().update()
            self.tree.scheduleDelayedItemsLayout()
        except Exception as e:
            print(f"[Sidebar] _recalculate_columns failed: {e}")

    def toggle_fold(self, folded: bool):
        if folded:
            self.collapse_all()
        else:
            self.expand_all()

    def _effective_display_mode(self):
        try:
            if self.settings:
                mode = str(self.settings.get("sidebar_mode", "list")).lower()
                if mode in ("tabs", "list"):
                    return mode
        except Exception:
            pass
        return "list"

    def switch_display_mode(self, mode: str):
        mode = (mode or "list").lower()
        if mode not in ("list", "tabs"):
            mode = "list"
        try:
            if self.settings:
                self.settings.set("sidebar_mode", mode)
        except Exception:
            pass

        print(f"[SidebarQt] switch_display_mode({mode}) - canceling old workers")

        # CRITICAL: Process pending events before mode switch
        # This ensures all pending widget deletions are completed
        # Only process events after initialization is complete
        if self._initialized:
            from PySide6.QtCore import QCoreApplication
            QCoreApplication.processEvents()

        if mode == "tabs":
            # Cancel list mode workers by bumping generation
            self._list_worker_gen = (self._list_worker_gen + 1) % 1_000_000
            print(f"[SidebarQt] Canceled list workers (new gen={self._list_worker_gen})")

            print("[SidebarQt] Hiding tree view")
            self.tree.hide()
            print("[SidebarQt] Showing tabs controller")
            self.tabs_controller.show_tabs()
            # Force refresh tabs when switching to tabs mode (ensures fresh data after scans)
            print("[SidebarQt] Calling tabs_controller.refresh_all(force=True) after mode switch")
            try:
                self.tabs_controller.refresh_all(force=True)
                print("[SidebarQt] tabs_controller.refresh_all() completed after mode switch")
            except Exception as e:
                print(f"[SidebarQt] ERROR in tabs_controller.refresh_all() after mode switch: {e}")
                import traceback
                traceback.print_exc()
        else:
            # Cancel tab workers via hide_tabs() which bumps their generations
            print("[SidebarQt] Hiding tabs controller")
            self.tabs_controller.hide_tabs()
            print("[SidebarQt] Canceled tab workers via hide_tabs()")

            # Process events again after hiding tabs to clear tab widgets
            # Only after initialization is complete
            if self._initialized:
                print("[SidebarQt] Processing pending events after hide_tabs()")
                from PySide6.QtCore import QCoreApplication
                QCoreApplication.processEvents()
                print("[SidebarQt] Finished processing events")

            # CRITICAL FIX: Clear tree view selection before showing to prevent stale Qt references
            print("[SidebarQt] Clearing tree view selection before rebuild")
            try:
                if hasattr(self.tree, 'selectionModel') and self.tree.selectionModel():
                    self.tree.selectionModel().clear()
                # Clear any expand/collapse state that might hold stale references
                self.tree.collapseAll()
            except Exception as e:
                print(f"[SidebarQt] Warning: Could not clear tree selection: {e}")

            print("[SidebarQt] Showing tree view")
            self.tree.show()
            print("[SidebarQt] Calling _build_tree_model()")
            try:
                self._build_tree_model()
                print("[SidebarQt] _build_tree_model() completed")
            except Exception as e:
                print(f"[SidebarQt] ERROR in _build_tree_model(): {e}")
                import traceback
                traceback.print_exc()

        try:
            self.btn_mode_toggle.setChecked(mode == "tabs")
            self._update_mode_toggle_text()
        except Exception:
            pass


    def reload_throttled(self, delay_ms: int = 800):
        if self._reload_block:
            return
        self._reload_block = True
        if not self._reload_timer.isActive():
            self._reload_timer.start(delay_ms)

    def _do_reload_throttled(self):
        try:
            self.reload()
        finally:
            self._reload_block = False

    def reload(self):
        # Guard against concurrent reloads
        if self._refreshing:
            print("[SidebarQt] reload() blocked - already refreshing")
            return

        try:
            self._refreshing = True
            mode = self._effective_display_mode()
            tabs_visible = self.tabs_controller.isVisible()
            print(f"[SidebarQt] reload() called, display_mode={mode}, tabs_visible={tabs_visible}")

            # CRITICAL FIX: Only refresh tabs if they're actually visible
            # This prevents crashes when reload() is called after switching to list mode
            # but before settings are fully updated
            if mode == "tabs" and tabs_visible:
                print(f"[SidebarQt] Calling tabs_controller.refresh_all(force=True)")
                try:
                    self.tabs_controller.refresh_all(force=True)
                    print(f"[SidebarQt] tabs_controller.refresh_all() completed")
                except Exception as e:
                    print(f"[SidebarQt] ERROR in tabs_controller.refresh_all(): {e}")
                    import traceback
                    traceback.print_exc()
            elif mode == "tabs" and not tabs_visible:
                print(f"[SidebarQt] WARNING: mode=tabs but tabs not visible, skipping refresh")
            else:
                print(f"[SidebarQt] Calling _build_tree_model() instead of tabs refresh")
                try:
                    self._build_tree_model()
                except Exception as e:
                    print(f"[SidebarQt] ERROR in _build_tree_model(): {e}")
                    import traceback
                    traceback.print_exc()
        finally:
            # Always reset flag, even if error occurs
            self._refreshing = False
        

    def _start_spinner(self):
        if not self._spin_timer.isActive():
            self._spin_angle = 0
            self._spin_timer.start()

    def _stop_spinner(self):
        if self._spin_timer.isActive():
            self._spin_timer.stop()
        self.btn_refresh.setIcon(QIcon(self._base_pm))

    def _tick_spinner(self):
        self._spin_angle = (self._spin_angle + 30) % 360
        pm = self._rotate_pixmap(self._base_pm, self._spin_angle)
        self.btn_refresh.setIcon(QIcon(pm))

    def _make_reload_pixmap(self, w: int, h: int) -> QPixmap:
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        font = p.font()
        font.setPointSize(int(h * 0.9))
        p.setFont(font)
        p.setPen(Qt.darkGray)
        p.drawText(pm.rect(), Qt.AlignCenter, "↻")
        p.end()
        return pm

    def _rotate_pixmap(self, pm: QPixmap, angle: int) -> QPixmap:
        if pm.isNull():
            return pm
        tr = QTransform()
        tr.rotate(angle)
        rotated = pm.transformed(tr, Qt.SmoothTransformation)
        final_pm = QPixmap(pm.size())
        final_pm.fill(Qt.transparent)
        p = QPainter(final_pm)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        x = (final_pm.width() - rotated.width()) // 2
        y = (final_pm.height() - rotated.height()) // 2
        p.drawPixmap(x, y, rotated)
        p.end()
        return final_pm
            
    def auto_refresh_sidebar_tabs(self):
        # Thin delegate to the new tabs widget
        self.tabs_controller.refresh_all(force=True)
  

    def _set_grid_context(self, mode: str, value):
        mw = self.window()
        if not hasattr(mw, "grid"):
            return

        # clear tag filter when switching main contexts
        if mode in ("folder", "branch", "date") and hasattr(mw, "_clear_tag_filter"):
            mw._clear_tag_filter()

        if mode == "branch" and isinstance(value, str) and value.startswith("date:"):
            mw.grid.set_context("date", value.replace("date:", ""))
        else:
            mw.grid.set_context(mode, value)

        # nudge layout
        def _reflow():
            try:
                g = mw.grid
                if hasattr(g, "_apply_zoom_geometry"):
                    g._apply_zoom_geometry()
                g.list_view.doItemsLayout()
                g.list_view.viewport().update()
            except Exception as e:
                print(f"[Sidebar] reflow failed: {e}")
        QTimer.singleShot(0, _reflow)


    # === Phase 3: Drag & Drop Handlers ===

    def _on_photos_dropped_to_folder(self, folder_id: int, photo_paths: list):
        """
        Handle photos dropped onto a folder in the sidebar tree.
        Updates the folder_id for all dropped photos in the database.
        """
        try:
            print(f"[DragDrop] Moving {len(photo_paths)} photo(s) to folder ID: {folder_id}")

            # Update folder_id for each photo in the database
            db = self.db if hasattr(self, 'db') else ReferenceDB()
            updated_count = 0

            for path in photo_paths:
                try:
                    db.set_folder_for_image(path, folder_id)
                    updated_count += 1
                except Exception as e:
                    print(f"[DragDrop] Failed to update folder for {path}: {e}")

            # Show success message
            QMessageBox.information(
                self,
                "Photos Moved",
                f"Successfully moved {updated_count} photo(s) to the selected folder."
            )

            # Refresh sidebar and grid to reflect changes
            if hasattr(self, '_do_reload_throttled'):
                self._do_reload_throttled()

            # Notify main window to refresh grid
            if hasattr(self.parent(), 'grid'):
                self.parent().grid.reload()

            print(f"[DragDrop] Successfully updated {updated_count}/{len(photo_paths)} photo(s)")

        except Exception as e:
            print(f"[DragDrop] Error moving photos to folder: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to move photos to folder:\n{str(e)}"
            )

    def _on_photos_dropped_to_tag(self, branch_key: str, photo_paths: list):
        """
        Handle photos dropped onto a tag/branch in the sidebar tree.
        Applies the tag to all dropped photos.
        """
        try:
            print(f"[DragDrop] Adding tag '{branch_key}' to {len(photo_paths)} photo(s)")

            # Determine tag name from branch key
            tag_name = None
            if branch_key == "favorite":
                tag_name = "favorite"
            elif branch_key.startswith("face_"):
                tag_name = "face"
            else:
                # For other branches, use the branch key as tag name
                tag_name = branch_key

            if not tag_name:
                print(f"[DragDrop] Unknown branch key: {branch_key}")
                return

            # Apply tag to each photo
            db = self.db if hasattr(self, 'db') else ReferenceDB()
            tag_service = get_tag_service()
            tagged_count = 0

            for path in photo_paths:
                try:
                    # Add tag to photo
                    tag_service.add_tag(path, tag_name)
                    tagged_count += 1
                except Exception as e:
                    print(f"[DragDrop] Failed to tag {path}: {e}")

            # Show success message
            QMessageBox.information(
                self,
                "Photos Tagged",
                f"Successfully tagged {tagged_count} photo(s) with '{tag_name}'."
            )

            # Refresh sidebar and grid to reflect changes
            if hasattr(self, '_do_reload_throttled'):
                self._do_reload_throttled()

            # Notify main window to refresh grid
            if hasattr(self.parent(), 'grid'):
                self.parent().grid.reload()

            print(f"[DragDrop] Successfully tagged {tagged_count}/{len(photo_paths)} photo(s)")

        except Exception as e:
            print(f"[DragDrop] Error tagging photos: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to tag photos:\n{str(e)}"
            )

    def _launch_detached(self, script_path: str):
        """Launch a script in a detached subprocess (used for heavy workers)."""
        try:
            import subprocess, sys
            subprocess.Popen([sys.executable, script_path],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL,
                             close_fds=True)
            print(f"[Sidebar] Detached worker launched: {script_path}")
        except Exception as e:
            print(f"[Sidebar] Failed to launch worker: {e}")
