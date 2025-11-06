# sidebar_qt.py
# Version 09.18.01.13 dated 20251031
# Tab-based sidebar with per-tab status labels, improved timeout handling,
# and dynamic branch/folder/date/tag loading.

from PySide6.QtWidgets import (
    QWidget, QTreeView, QMenu, QFileDialog,
    QVBoxLayout, QMessageBox, QTreeWidgetItem, QTreeWidget,
    QHeaderView, QHBoxLayout, QPushButton, QLabel, QTabWidget, QListWidget, QListWidgetItem, QProgressBar,
    QTableWidget, QTableWidgetItem
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

class _ThreadProxy(QObject):
    done = Signal(object, int, list, float)



# =====================================================================
# 1️: SidebarTabs — full tabs-based controller (new)
# ====================================================================

class SidebarTabs(QWidget):
    # Signals to parent (SidebarQt/MainWindow) so the grid can change context
    selectBranch = Signal(str)     # branch_key    e.g. "all" or "face_john"
    selectFolder = Signal(int)     # folder_id
    selectDate   = Signal(str)     # e.g. "2025-10" or "2025"
    selectTag    = Signal(str)     # tag name

    # inside class SidebarTabs
 
#    _finishQuickSig    = Signal(int, list, float)
    
    # ▼ add with your other Signals
    _finishBranchesSig = Signal(int, list, float, int)  # (idx, rows, started, gen)
    _finishFoldersSig  = Signal(int, list, float, int)
    _finishDatesSig    = Signal(int, object, float, int)  # object to accept dict or list
    _finishTagsSig     = Signal(int, list, float, int)
    _finishPeopleSig   = Signal(int, list, float, int)  # 👥 NEW

    
    def __init__(self, project_id: int | None, parent=None):
        super().__init__(parent)
        self._dbg("__init__ started")
        self.db = ReferenceDB()
        self.project_id = project_id

        # at init:
        self._thread_proxy = _ThreadProxy()
        self._thread_proxy.done.connect(lambda self_, idx, rows, started:
            self._finish_branches(idx, rows, started)
        )

        # internal state (lives here now)
        self._tab_populated: set[str] = set()
        self._tab_loading: set[str]   = set()
        self._tab_timers: dict[int, QTimer] = {}
        self._tab_status_labels: dict[int, QLabel] = {}
        self._count_targets: list[tuple] = []               # optional future use
        self._tab_indexes: dict[str, int] = {}              # "branches"/"folders"/"dates"/"tags"/"quick" -> tab index
        # ▼ add near your state vars
        self._tab_gen: dict[str, int] = {"branches":0, "folders":0, "dates":0, "tags":0, "quick":0}

        # UI
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.tab_widget = QTabWidget()
        v.addWidget(self.tab_widget, 1)

        # connections
        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        self._finishBranchesSig.connect(self._finish_branches)
        self._finishFoldersSig.connect(self._finish_folders)
        self._finishDatesSig.connect(self._finish_dates)
        self._finishTagsSig.connect(self._finish_tags)
        self._finishPeopleSig.connect(self._finish_people)

#        self._finishQuickSig.connect(self._finish_quick)


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
        for key in ("branches", "folders", "dates", "tags", "quick"):
            idx = self._tab_indexes.get(key)
            self._dbg(f"refresh_all: key={key}, idx={idx}, force={force}")
            if idx is not None:
                self._populate_tab(key, idx, force=force)
        self._dbg(f"refresh_all(force={force}) completed")

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
    def hide_tabs(self): self.hide()

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
        if not tab: return
        v = tab.layout()
        for i in reversed(range(v.count())):
            w = v.itemAt(i).widget()
            if w: w.setParent(None)

    def _set_tab_empty(self, idx, msg="No items"):
        tab = self.tab_widget.widget(idx)
        if not tab: return
        v = tab.layout()
        v.addWidget(QLabel(f"<b>{msg}</b>"))

    # ---------- population dispatcher ----------

    def _populate_tab(self, tab_type: str, idx: int, force=False):
        self._dbg(f"_populate_tab({tab_type}, idx={idx}, force={force})")
        self._dbg(f"  populated={tab_type in self._tab_populated}, loading={tab_type in self._tab_loading}")

        if force and tab_type in self._tab_populated:
            self._dbg(f"  Force refresh: removing {tab_type} from populated set")
            self._tab_populated.discard(tab_type)

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

            # Column 1: Count
            count_str = str(count) if count is not None else "0"
            item_count = QTableWidgetItem(count_str)
            item_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row, 1, item_count)

        table.cellDoubleClicked.connect(lambda row, col: self.selectBranch.emit(table.item(row, 0).data(Qt.UserRole)))
        tab.layout().addWidget(table, 1)

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
                rows = self.db.get_all_folders() or []    # expect list[dict{id,path}] or tuples
                self._dbg(f"_load_folders → got {len(rows)} rows")
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

        # Parse folder data
        folders = []
        for r in (rows or []):
            fid, path = None, None
            if isinstance(r, dict):
                fid = r.get("id")
                path = r.get("path") or r.get("name") or (f"Folder {fid}" if fid is not None else None)
            elif isinstance(r, (list, tuple)) and len(r) >= 2:
                fid, path = r[0], r[1]
            elif isinstance(r, str):
                path = r
            if path:
                folders.append({"id": fid, "path": str(path)})

        if not folders:
            self._set_tab_empty(idx, "No folders found")
        else:
            # Create tree widget for hierarchical folder display
            tree = QTreeWidget()
            tree.setHeaderLabels(["Folder", "Photos"])
            tree.setColumnCount(2)
            tree.setSelectionMode(QTreeWidget.SingleSelection)
            tree.setEditTriggers(QTreeWidget.NoEditTriggers)
            tree.setAlternatingRowColors(True)
            tree.header().setStretchLastSection(False)
            tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
            tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)

            # Build tree structure from folder paths
            path_to_item = {}  # Track created tree items by normalized path

            import os
            for folder in folders:
                fid = folder["id"]
                path = folder["path"]

                # Normalize path separators
                path = path.replace("\\", "/")

                # Get folder count from database if available
                count = 0
                try:
                    if fid and hasattr(self.db, "get_images_by_folder"):
                        folder_paths = self.db.get_images_by_folder(fid)
                        count = len(folder_paths) if folder_paths else 0
                except Exception:
                    pass

                # Split path into components
                parts = [p for p in path.split("/") if p]

                # Build tree hierarchy
                parent_item = None
                current_path = ""

                for i, part in enumerate(parts):
                    current_path = "/".join(parts[:i+1])

                    if current_path not in path_to_item:
                        # Create new tree item
                        is_leaf = (i == len(parts) - 1)  # Last component
                        count_str = str(count) if is_leaf else ""

                        item = QTreeWidgetItem([part, count_str])
                        if is_leaf and fid is not None:
                            item.setData(0, Qt.UserRole, int(fid))

                        if parent_item:
                            parent_item.addChild(item)
                        else:
                            tree.addTopLevelItem(item)

                        path_to_item[current_path] = item

                    parent_item = path_to_item[current_path]

            # Connect double-click to emit folder selection
            tree.itemDoubleClicked.connect(
                lambda item, col: self.selectFolder.emit(item.data(0, Qt.UserRole)) if item.data(0, Qt.UserRole) else None
            )
            tab.layout().addWidget(tree, 1)

        self._tab_populated.add("folders")
        self._tab_loading.discard("folders")
        st = self._tab_status_labels.get(idx)
        if st: st.setText(f"{len(folders)} folder(s) • {time.time()-started:.2f}s")

    # ---------- dates ----------
    def _load_dates(self, idx:int, gen:int):
        started = time.time()
        def work():
            rows = []
            try:
                if self.project_id:
                    # Get hierarchical date data: {year: {month: [days]}}
                    if hasattr(self.db, "get_date_hierarchy"):
                        hier = self.db.get_date_hierarchy() or {}
                        # Also get year counts
                        year_counts = {}
                        if hasattr(self.db, "list_years_with_counts"):
                            year_list = self.db.list_years_with_counts() or []
                            year_counts = {str(y): c for y, c in year_list}
                        # Build result with hierarchy and counts
                        rows = {"hierarchy": hier, "year_counts": year_counts}
                    else:
                        self._dbg("_load_dates → No date hierarchy method available")
                self._dbg(f"_load_dates → got hierarchy data")
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
                    month_count = len(days_list)
                    month_item = QTreeWidgetItem([f"{month_label} {year}", str(month_count)])
                    month_item.setData(0, Qt.UserRole, f"{year}-{month}")
                    year_item.addChild(month_item)

                    # Days (children of month)
                    for day in sorted(days_list, reverse=True):
                        day_item = QTreeWidgetItem([str(day), ""])  # No count for individual days
                        day_item.setData(0, Qt.UserRole, str(day))
                        month_item.addChild(day_item)

            # Connect double-click to emit date selection
            tree.itemDoubleClicked.connect(lambda item, col: self.selectDate.emit(item.data(0, Qt.UserRole)))
            tab.layout().addWidget(tree, 1)

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
        def work():
            rows = []
            try:
                # Use TagService for proper layered architecture
                tag_service = get_tag_service()
                rows = tag_service.get_all_tags_with_counts() or []  # list of (tag_name, count) tuples
                self._dbg(f"_load_tags → got {len(rows)} rows")
            except Exception:
                traceback.print_exc()
                rows = []
            self._finishTagsSig.emit(idx, rows, started, gen)
        threading.Thread(target=work, daemon=True).start()

    # ---------- TAGS ----------
    def _finish_tags(self, idx:int, rows:list, started:float, gen:int):
        if self._is_stale("tags", gen):
            self._dbg(f"_finish_tags (stale gen={gen}) — ignoring")
            return
        self._cancel_timeout(idx)
        self._clear_tab(idx)

        tab = self.tab_widget.widget(idx)
        tab.layout().addWidget(QLabel("<b>Tags</b>"))

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
                # Column 0: Tag name
                item_name = QTableWidgetItem(tag_name)
                item_name.setData(Qt.UserRole, tag_name)
                table.setItem(row, 0, item_name)

                # Column 1: Count
                item_count = QTableWidgetItem(str(count))
                item_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(row, 1, item_count)

            table.cellDoubleClicked.connect(lambda row, col: self.selectTag.emit(table.item(row, 0).data(Qt.UserRole)))
            tab.layout().addWidget(table, 1)

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
            self._finishQuickSig.emit(idx, rows, started, gen) if hasattr(self, "_finishQuickSig") else self._finish_quick(idx, rows, started, gen)
        threading.Thread(target=work, daemon=True).start()

    # ---------- QUICK ----------
    def _finish_quick(self, idx:int, rows:list, started:float|None=None, gen:int|None=None):
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

                # Column 1: Count
                item_count = QTableWidgetItem(str(count))
                item_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(row, 1, item_count)

            table.cellDoubleClicked.connect(lambda row, col: self.selectDate.emit(table.item(row, 0).data(Qt.UserRole)))
            tab.layout().addWidget(table, 1)

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

            # Column 1: Count
            item_count = QTableWidgetItem(str(count))
            item_count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row_idx, 1, item_count)

        table.cellDoubleClicked.connect(
            lambda row, col: self.selectBranch.emit(table.item(row, 0).data(Qt.UserRole))
        )
        layout.addWidget(table, 1)

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

        # Tree (list mode)
        self.tree = QTreeView(self)
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
                if self.tabs_controller.isVisible():
                    self.tabs_controller.hide_tabs()
                else:
                    self.tabs_controller.show_tabs()
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
                return int(self.db.count_for_folder(folder_id) or 0)
            if hasattr(self.db, "get_folder_photo_count"):
                return int(self.db.get_folder_photo_count(folder_id) or 0)
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
        self.model.removeRows(0, self.model.rowCount())
        self._count_targets = []
        try:
            branch_root = QStandardItem("🌿 Branches")
            branch_root.setEditable(False)
            self.model.appendRow([branch_root, QStandardItem("")])
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
            self.model.appendRow([quick_root, QStandardItem("")])
            try:
                quick_rows = self.db.get_quick_date_counts()
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
            self.model.appendRow([folder_root, QStandardItem("")])
            # synchronous (restores the previous working behavior)
            self._add_folder_items(folder_root, None)

            self._build_by_date_section()
            self._build_tag_section()
            
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
            

            for r in range(self.model.rowCount()):
                idx = self.model.index(r, 0)
                self.tree.expand(idx)
        except Exception as e:
            QMessageBox.warning(self, "Load Error", f"Failed to build navigation:\n{e}")

        # populate branch counts asynchronously while folder counts are already set
        if self._count_targets:
            print(f"[Sidebar] starting async count population for {len(self._count_targets)} branch targets")
            self._async_populate_counts()

    def _add_folder_items_async(self, parent_item, parent_id=None):
        # kept for folder-tab lazy usage if desired, but not used for tree-mode counts
        rows = self.db.get_child_folders(parent_id)
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
            
            print("[Sidebar][counts applied] updated UI with counts")
        except Exception:
            traceback.print_exc()


    def _async_populate_counts(self):
        targets = list(self._count_targets)
        if not targets:
            print("[Sidebar][counts] no targets to populate")
            return

        def worker():
            results = []
            try:
                print(f"[Sidebar][counts worker] running for {len(targets)} targets...")
                for typ, key, name_item, count_item in targets:
                    try:
                        cnt = 0
                        if typ == "branch":
                            if hasattr(self.db, "count_images_by_branch"):
                                cnt = int(self.db.count_images_by_branch(self.project_id, key) or 0)
                            else:
                                rows = self.db.get_images_by_branch(self.project_id, key) or []
                                cnt = len(rows)
#                        elif typ == "folder":
#                            if hasattr(self.db, "count_for_folder"):
#                                cnt = int(self.db.count_for_folder(key) or 0)
#                            else:
#                                with self.db._connect() as conn:
#                                    cur = conn.cursor()
#                                    cur.execute("SELECT COUNT(*) FROM photo_metadata WHERE folder_id=?", (key,))
#                                    v = cur.fetchone()
#                                    cnt = int(v[0]) if v else 0


                        elif typ == "folder":
                            # 🆕 Use recursive count including all subfolders
                            if hasattr(self.db, "get_image_count_recursive"):
                                cnt = int(self.db.get_image_count_recursive(key) or 0)
                            elif hasattr(self.db, "count_for_folder"):
                                cnt = int(self.db.count_for_folder(key) or 0)
                            else:
                                with self.db._connect() as conn:
                                    cur = conn.cursor()
                                    cur.execute("SELECT COUNT(*) FROM photo_metadata WHERE folder_id=?", (key,))
                                    v = cur.fetchone()
                                    cnt = int(v[0]) if v else 0

                        results.append((typ, key, name_item, count_item, cnt))
                    except Exception:
                        traceback.print_exc()
                        results.append((typ, key, name_item, count_item, 0))
                print("[Sidebar][counts worker] finished scanning targets, scheduling UI update")
            except Exception:
                traceback.print_exc()
            QTimer.singleShot(0, lambda: self._apply_counts_defensive(results))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_counts_defensive(self, results):
        """
        Apply counts to UI defensively:
        - If name_item/count_item provided, update them.
        - If missing, try to find QStandardItems in the model by payload key and update.
        """
        try:
            for typ, key, name_item, count_item, cnt in results:
                text = str(cnt) if cnt is not None else ""
                # If we have a QStandardItem count_item directly, set it.
                if isinstance(count_item, QStandardItem):
                    try:
                        count_item.setText(text)
                        continue
                    except Exception:
                        pass
                # If count_item is a QTreeWidgetItem node (folder-tab), update its second column
                if count_item is not None and hasattr(count_item, "setText") and not isinstance(count_item, QStandardItem):
                    try:
                        count_item.setText(1, text)
                        continue
                    except Exception:
                        pass
                # If we have a name_item QStandardItem, set sibling column
                if isinstance(name_item, QStandardItem):
                    try:
                        idx = name_item.index()
                        if idx.isValid():
                            sib = idx.sibling(idx.row(), 1)
                            self.model.setData(sib, text)
                            continue
                    except Exception:
                        pass
                # Defensive fallback: search the model by key (branch or folder id)
                try:
                    found_name, found_count = self._find_model_item_by_key(key)
                    if found_count is not None:
                        try:
                            found_count.setText(text)
                            continue
                        except Exception:
                            # try model-level setData on its index
                            try:
                                idx = found_count.index()
                                if idx.isValid():
                                    self.model.setData(idx, text)
                                    continue
                            except Exception:
                                pass
                    # If only found_name is present, set its sibling
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
                    pass
            # refresh views
            try:
                self.tree.viewport().update()
            except Exception:
                pass
            
            print("[Sidebar][counts applied] updated UI with counts")
        except Exception:
            traceback.print_exc()

    def _add_folder_items(self, parent_item, parent_id=None):
        rows = self.db.get_child_folders(parent_id)
        for row in rows:
            name = row["name"]
            fid = row["id"]
#            photo_count = self._get_photo_count(fid)

            if hasattr(self.db, "get_image_count_recursive"):
                photo_count = int(self.db.get_image_count_recursive(fid) or 0)
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
            self._add_folder_items(name_item, fid)


    def _build_by_date_section(self):
        from PySide6.QtGui import QStandardItem, QColor
        from PySide6.QtCore import Qt
        try:
            hier = self.db.get_date_hierarchy()
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
                y_count = self.db.count_for_year(year)
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
                    m_count = self.db.count_for_month(year, month)
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
                        d_count = self.db.count_for_day(ymd)
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
            tag_rows = tag_service.get_all_tags_with_counts()
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
            try:
                if self.settings:
                    self.settings.set("sidebar_folded", False)
            except Exception:
                pass
        except Exception:
            pass

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

        if mode == "tabs":
            self.tree.hide()
            self.tabs_controller.show_tabs()
            # Ensure tabs are current; SidebarTabs handles its own population
            self.tabs_controller.refresh_all(force=False)
        else:
            self.tabs_controller.hide_tabs()
            self.tree.show()
            self._build_tree_model()

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
        mode = self._effective_display_mode()
        print(f"[SidebarQt] reload() called, display_mode={mode}")
        if mode == "tabs":
            print(f"[SidebarQt] Calling tabs_controller.refresh_all(force=True)")
            self.tabs_controller.refresh_all(force=True)
            print(f"[SidebarQt] tabs_controller.refresh_all() completed")
        else:
            print(f"[SidebarQt] Calling _build_tree_model() instead of tabs refresh")
            self._build_tree_model()
        

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
