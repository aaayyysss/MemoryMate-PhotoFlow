# LIGHTBOX EDIT MODE AUDIT REPORT
**Date:** 2025-11-13  
**Component:** MemoryMate-PhotoFlow Preview Panel (Lightbox Dialog)  
**File:** preview_panel_qt.py  
**Issue:** Photo not displaying in edit mode  

---

## EXECUTIVE SUMMARY

**STATUS:** 🔴 **CRITICAL BUG FOUND**

The photo does not display when entering edit mode due to a **fundamental widget hierarchy violation**. The canvas widget is being reparented between two pages of a QStackedWidget, but Qt's visibility rules prevent a widget from being visible when its original parent is hidden.

**Root Cause:** Architectural design flaw in widget reparenting logic  
**Impact:** Complete loss of edit functionality - users cannot see the photo they're editing  
**Severity:** Critical (P0)  
**Lines Affected:** 2322-2326, 2362-2363, 1105-1129  

---

## DETAILED FINDINGS

### 1. CRITICAL: Canvas Not Visible in Edit Mode

**Location:** `/home/user/MemoryMate-PhotoFlow/preview_panel_qt.py:2285-2344`

**Error:** Photo canvas disappears when entering edit mode

**Root Cause:**

The application uses a two-level stacked widget architecture:

```
Main Stack (self.stack):
├─ [0] viewer_page
│   └─ content_stack (QStackedWidget)
│       └─ [0] canvas (_ImageCanvas) ← CANVAS ORIGINAL LOCATION
└─ [1] editor_page
    └─ edit_canvas_container ← CANVAS INTENDED LOCATION
```

When `_enter_edit_mode()` executes (line 2322):

```python
# Line 2322: Switch to editor page
self.stack.setCurrentIndex(1)

# Lines 2323-2326: Try to reparent canvas
if hasattr(self, "edit_canvas_container"):
    container_layout = self.edit_canvas_container.layout()
    if self.canvas.parent() is not self.edit_canvas_container:
        container_layout.addWidget(self.canvas)
```

**The Qt Visibility Rule Violation:**

1. The canvas's original parent is `content_stack` (a QStackedWidget in viewer_page)
2. `addWidget(self.canvas)` attempts to reparent canvas to `edit_canvas_container`
3. **BUT:** In Qt's widget hierarchy, when a parent widget is hidden, ALL its children are hidden
4. When `self.stack.setCurrentIndex(1)` executes, viewer_page (index 0) becomes HIDDEN
5. Since `content_stack` is in viewer_page, it becomes HIDDEN
6. Since canvas was originally a child of `content_stack`, Qt still considers it part of that hierarchy
7. **Result:** Canvas remains HIDDEN even though it's been added to the edit_canvas_container layout

**Technical Details:**

The `QLayout::addWidget()` method does reparent widgets, but the timing and hierarchy cause the issue:
- The canvas is added to `content_stack` at line 542 during initialization
- When reparenting via `addWidget()`, Qt updates the widget's parent pointer
- However, the canvas retains visibility state inherited from its original parent tree
- Since `content_stack` is hidden when viewer_page is not current, the canvas inherits this hidden state

**Impact:**
- Users click "Edit Photo" button
- UI switches to edit mode with all controls visible
- **Photo canvas is completely blank/black**
- Users cannot see what they're editing
- All edit controls (brightness, contrast, etc.) are non-functional without visual feedback

**Evidence:**
Lines 2287-2298 show diagnostic logging that was added to debug this exact issue:
```python
print(f"[EditMode] 🔍 DIAGNOSTIC: Entering edit mode, _orig_pil={'exists' if self._orig_pil else 'None'}")
```

The presence of these diagnostics confirms this bug has been encountered before.

---

### 2. CRITICAL: Incorrect Return-to-Viewer Logic

**Location:** `/home/user/MemoryMate-PhotoFlow/preview_panel_qt.py:2346-2366`

**Error:** Canvas is placed in wrong location when returning from edit mode

**Code:**
```python
def _return_to_viewer(self):
    # ... confirmation dialog ...
    
    viewer_page = self.stack.widget(0)
    viewer_layout = viewer_page.layout()
    center_widget = viewer_layout.itemAt(1).widget()  # Get "center" QWidget
    if center_widget:
        canvas_row = center_widget.layout()  # Get hbox layout
    
    if canvas_row and self.canvas.parent() is not center_widget:
        canvas_row.insertWidget(0, self.canvas, 1)  # ← BUG HERE
```

**Root Cause:**

The code tries to insert the canvas at position 0 in the `hbox` layout. However:

**Current hierarchy (from lines 526-589):**
```
center (QWidget)
└── hbox (QHBoxLayout)
    ├── [0] content_stack (QStackedWidget) ← Position 0
    │   └── [0] canvas ← Canvas should be INSIDE content_stack
    └── [1] _meta_placeholder
```

**What the buggy code does:**
```
center (QWidget)
└── hbox (QHBoxLayout)
    ├── [0] canvas ← WRONG! Canvas placed at position 0
    ├── [1] content_stack ← Pushed to position 1
    └── [2] _meta_placeholder
```

**Impact:**
- Canvas is placed as a sibling of content_stack instead of inside it
- Breaks the video/photo switching logic (content_stack manages photo vs video display)
- Navigation between photos and videos will fail after exiting edit mode
- Potential crashes when switching media types

---

### 3. ARCHITECTURAL: Widget Lifecycle Violation

**Location:** `/home/user/MemoryMate-PhotoFlow/preview_panel_qt.py:532-542, 1105-1129`

**Error:** Attempting to share a widget between two QStackedWidget pages

**Root Cause:**

The architecture violates Qt's widget lifecycle rules:

**Qt Widget Rules:**
1. A widget can only have ONE parent at a time
2. A widget's visibility is determined by its parent chain
3. When a QStackedWidget changes pages, non-current pages and ALL their children are hidden
4. Reparenting a widget doesn't break the original visibility state inheritance immediately

**Current Design Flaw:**
The code tries to "share" the canvas between viewer and editor pages by reparenting it back and forth. This is fundamentally incompatible with Qt's visibility model.

**Impact:**
- Brittle architecture that breaks under normal use
- Difficult to debug (visibility issues are not obvious)
- Maintenance nightmare (developers will keep "fixing" symptoms)
- Risk of memory leaks (parent/child relationships unclear)

---

## SIGNAL/SLOT CONNECTION ANALYSIS

**Status:** ✅ Connections are correct

Verified connections:
- Line 1146: `btn_edit.clicked.connect(self._enter_edit_mode)` ✓
- Line 539: `canvas.scaleChanged.connect(self._on_canvas_scale_changed)` ✓
- Line 2342-2343: Canvas pixmap is set in edit mode ✓

The signal/slot connections are working correctly. The issue is purely architectural.

---

## WIDGET INITIALIZATION ANALYSIS

**Status:** ✅ Widgets properly initialized

Verified initialization:
- Line 535: Canvas created ✓
- Line 542: Canvas added to content_stack ✓
- Lines 1105-1129: Edit canvas container created ✓
- Line 1146: Edit button created and connected ✓

All widgets are properly initialized. The issue is in the mode-switching logic, not initialization.

---

## THE PROPER FIX

### Solution: Rearchitect to avoid reparenting

**Option 1: Move content_stack to be shared (RECOMMENDED)**

```python
# In __init__, restructure the hierarchy:

# Create content_stack ONCE at dialog level, not inside viewer page
self.content_stack = QStackedWidget()
self.canvas = LightboxDialog._ImageCanvas(self)
self.canvas.scaleChanged.connect(self._on_canvas_scale_changed)
self.content_stack.addWidget(self.canvas)

# In VIEWER page: Reference content_stack
def _build_viewer_page(self):
    # ... top bar ...
    
    # Use the shared content_stack
    hbox.addWidget(self.content_stack, 1)
    
    # ... rest of viewer ...

# In EDITOR page: Reference the SAME content_stack
def _build_edit_page(self):
    # ... toolbars ...
    
    # Use the shared content_stack (not a container!)
    content_row_layout.addWidget(self.content_stack, 1)
    
    # ... right panel ...
```

**Changes required:**

1. **Move content_stack creation** (line 532-558):
   - Move from inside viewer page to dialog level (before stack creation)
   - Make it an instance variable available to both pages

2. **Update viewer page** (line 589):
   ```python
   # OLD:
   hbox.addWidget(self.content_stack, 1)
   
   # NEW: Create a placeholder and add content_stack to it
   canvas_placeholder = QWidget()
   canvas_layout = QVBoxLayout(canvas_placeholder)
   canvas_layout.setContentsMargins(0,0,0,0)
   # content_stack will be moved here in viewer mode
   hbox.addWidget(canvas_placeholder, 1)
   ```

3. **Update editor page** (line 1120):
   ```python
   # OLD:
   content_row_layout.addWidget(self.edit_canvas_container, 1)
   
   # NEW: Add content_stack directly
   content_row_layout.addWidget(self.content_stack, 1)
   ```

4. **Update _enter_edit_mode** (lines 2322-2327):
   ```python
   # OLD:
   self.stack.setCurrentIndex(1)
   if hasattr(self, "edit_canvas_container"):
       container_layout = self.edit_canvas_container.layout()
       if self.canvas.parent() is not self.edit_canvas_container:
           container_layout.addWidget(self.canvas)
   
   # NEW:
   self.stack.setCurrentIndex(1)
   # Move content_stack to editor page
   if self.content_stack.parent() is not self.editor_page:
       editor_layout = self.editor_page.layout()
       content_row = editor_layout.itemAt(2).widget()  # content_row
       row_layout = content_row.layout()
       # Remove from current location
       old_parent_layout = self.content_stack.parent().layout()
       if old_parent_layout:
           old_parent_layout.removeWidget(self.content_stack)
       # Add to editor location
       row_layout.insertWidget(0, self.content_stack, 1)
   ```

5. **Update _return_to_viewer** (lines 2362-2363):
   ```python
   # OLD:
   if canvas_row and self.canvas.parent() is not center_widget:
       canvas_row.insertWidget(0, self.canvas, 1)
   
   # NEW:
   # Move content_stack back to viewer page
   if canvas_row and self.content_stack.parent() is not center_widget:
       # Remove from editor page
       editor_layout = self.editor_page.layout()
       content_row = editor_layout.itemAt(2).widget()
       row_layout = content_row.layout()
       row_layout.removeWidget(self.content_stack)
       # Add back to viewer
       canvas_row.insertWidget(0, self.content_stack, 1)
   ```

**Why this works:**
- content_stack is reparented, not canvas
- When content_stack is moved to editor_page, it becomes visible (because editor_page is current)
- Canvas stays inside content_stack, so it inherits visibility from content_stack
- No violation of Qt widget hierarchy rules

---

## DIFF-STYLE CODE CHANGES

### Change 1: Move content_stack to dialog level

**File:** `/home/user/MemoryMate-PhotoFlow/preview_panel_qt.py`  
**Lines:** 509-542

```diff
         # UI skeleton
         self.stack = QStackedWidget(self)
         outer = QVBoxLayout(self)
         outer.setContentsMargins(0,0,0,0)
         outer.addWidget(self.stack)
 
+        # === PHASE 1: Content stack for unified photo/video display ===
+        # Created at dialog level so it can be shared between viewer and editor pages
+        self.content_stack = QStackedWidget()
+
+        # Page 0: Image canvas (for photos)
+        self.canvas = LightboxDialog._ImageCanvas(self)
+        self.canvas.setCursor(Qt.OpenHandCursor)
+        try:
+            self.canvas.scaleChanged.connect(self._on_canvas_scale_changed)
+        except Exception:
+            pass
+        self.content_stack.addWidget(self.canvas)  # index 0
+
+        # Page 1: Video player (for videos)
+        video_container = QWidget()
+        video_layout = QVBoxLayout(video_container)
+        video_layout.setContentsMargins(0, 0, 0, 0)
+        video_layout.setSpacing(0)
+
+        self.video_widget = QVideoWidget()
+        self.video_widget.setMinimumHeight(300)
+        self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
+        self.video_widget.setStyleSheet("background-color: black;")
+        self.video_widget.installEventFilter(self)
+        video_layout.addWidget(self.video_widget)
+
+        self.content_stack.addWidget(video_container)  # index 1
+
+        # Initialize media player for video playback
+        self.media_player = QMediaPlayer()
+        self.audio_output = QAudioOutput()
+        self.media_player.setAudioOutput(self.audio_output)
+        self.media_player.setVideoOutput(self.video_widget)
+
+        # Connect signals for video playback
+        try:
+            self.media_player.playbackStateChanged.connect(self._on_video_playback_state_changed)
+            self.media_player.errorOccurred.connect(self._on_video_error)
+            self.media_player.positionChanged.connect(self._on_video_position_changed)
+            self.media_player.durationChanged.connect(self._on_video_duration_changed)
+            print("[LightboxDialog] Video player signals connected successfully")
+        except Exception as e:
+            print(f"[LightboxDialog] WARNING: Failed to connect video signals: {e}")
+
+        self._video_duration = 0
+        self._timeline_seeking = False
+        self._current_media_type = "photo"
+        self._is_video = False
+        self._is_fullscreen = False
+        self._pre_fullscreen_geometry = None
+        self._pre_fullscreen_state = None
+        self.canvas.installEventFilter(self)
+
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
 
-        # === PHASE 1: Content stack for unified photo/video display ===
-        self.content_stack = QStackedWidget()
-
-        # Page 0: Image canvas (for photos)
-        self.canvas = LightboxDialog._ImageCanvas(self)
-        self.canvas.setCursor(Qt.OpenHandCursor)
-        # keep viewer & editor zoom controls synchronized whenever canvas scale changes
-        try:
-            self.canvas.scaleChanged.connect(self._on_canvas_scale_changed)
-        except Exception:
-            pass
-        self.content_stack.addWidget(self.canvas)  # index 0
-
-        # Page 1: Video player (for videos)
-        video_container = QWidget()
-        video_layout = QVBoxLayout(video_container)
-        video_layout.setContentsMargins(0, 0, 0, 0)
-        video_layout.setSpacing(0)
-
-        # Video widget with proper sizing
-        self.video_widget = QVideoWidget()
-        self.video_widget.setMinimumHeight(300)
-        self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
-        self.video_widget.setStyleSheet("background-color: black;")
-        self.video_widget.installEventFilter(self)  # PHASE 2: Capture double-click for fullscreen
-        video_layout.addWidget(self.video_widget)
-
-        self.content_stack.addWidget(video_container)  # index 1
-
-        # Initialize media player for video playback
-        self.media_player = QMediaPlayer()
-        self.audio_output = QAudioOutput()
-        self.media_player.setAudioOutput(self.audio_output)
-        self.media_player.setVideoOutput(self.video_widget)
-
-        # Connect signals for video playback
-        try:
-            self.media_player.playbackStateChanged.connect(self._on_video_playback_state_changed)
-            self.media_player.errorOccurred.connect(self._on_video_error)
-            self.media_player.positionChanged.connect(self._on_video_position_changed)
-            self.media_player.durationChanged.connect(self._on_video_duration_changed)
-            print("[LightboxDialog] Video player signals connected successfully")
-        except Exception as e:
-            print(f"[LightboxDialog] WARNING: Failed to connect video signals: {e}")
-
-        # Video position update timer
-        self._video_duration = 0
-        self._timeline_seeking = False  # Track if user is dragging timeline
-
-        # Track current media type
-        self._current_media_type = "photo"  # "photo" or "video"
-        self._is_video = False
-
-        # === PHASE 2: Fullscreen mode support ===
-        self._is_fullscreen = False
-        self._pre_fullscreen_geometry = None
-        self._pre_fullscreen_state = None
-
+        # Add content_stack to viewer page
         hbox.addWidget(self.content_stack, 1)
 
-        # install event filter for canvas (if needed downstream)
-        self.canvas.installEventFilter(self)
-        
         # meta placeholder (exists independently from editor panel)
         self._meta_placeholder = QWidget()
         self._meta_placeholder.setFixedWidth(0)
         hbox.addWidget(self._meta_placeholder, 0)
```

### Change 2: Update _build_edit_page to use content_stack

**File:** `/home/user/MemoryMate-PhotoFlow/preview_panel_qt.py`  
**Lines:** 1103-1132

```diff
         row2.addStretch(1)
         layout.addLayout(row2)
 
-        # === Canvas container (shared) + right-side placeholder for editor/filter panels ===
-        # Host both canvas container and right placeholder side-by-side so right panel never underlaps canvas.
-        self.edit_canvas_container = QWidget()
-        edit_lay = QVBoxLayout(self.edit_canvas_container)
-        edit_lay.setContentsMargins(0, 0, 0, 0)
-        edit_lay.setSpacing(0)
-        
-        # create a placeholder widget on the right where the adjustments / filters panel will be attached
+        # === Canvas content (shared via content_stack) + right-side placeholder ===
         self._editor_right_placeholder = QWidget()
         self._editor_right_placeholder.setFixedWidth(0)  # hidden by default
         self._editor_right_placeholder.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
         
-        # host both in a horizontal row so the right panel sits to the right of the canvas
+        # Host content_stack and right placeholder side-by-side
         content_row = QWidget()
         content_row_layout = QHBoxLayout(content_row)
         content_row_layout.setContentsMargins(0, 0, 0, 0)
         content_row_layout.setSpacing(16)
-        content_row_layout.addWidget(self.edit_canvas_container, 1)
+        # Note: content_stack will be reparented here when entering edit mode
+        # Create placeholder for it
+        self._edit_canvas_placeholder = QWidget()
+        self._edit_canvas_placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
+        edit_placeholder_layout = QVBoxLayout(self._edit_canvas_placeholder)
+        edit_placeholder_layout.setContentsMargins(8, 8, 8, 8)
+        edit_placeholder_layout.setSpacing(0)
+        content_row_layout.addWidget(self._edit_canvas_placeholder, 1)
         content_row_layout.addWidget(self._editor_right_placeholder, 0)
 
         layout.addWidget(content_row, 1)
-        
-        # Create edit_canvas_container inner layout now (this is where the canvas will be reparented on mode switch)
-        inner = QVBoxLayout()
-        inner.setContentsMargins(8, 8, 8, 8)   # add a small right margin so canvas content doesn't touch placeholder
-        inner.setSpacing(0)
-        self.edit_canvas_container.setLayout(inner)
-    
                
         return page
```

### Change 3: Fix _enter_edit_mode to reparent content_stack

**File:** `/home/user/MemoryMate-PhotoFlow/preview_panel_qt.py`  
**Lines:** 2285-2344

```diff
     def _enter_edit_mode(self):
         """Switch UI to edit mode (reuse main canvas). Prepare edit staging but do not auto-open the panel."""
         print(f"[EditMode] 🔍 DIAGNOSTIC: Entering edit mode, _orig_pil={'exists' if self._orig_pil else 'None'}")
 
         # CRITICAL FIX: If _orig_pil is None, try to reload from current path
         if not self._orig_pil and hasattr(self, 'current_path') and self.current_path:
             print(f"[EditMode] 🔄 DIAGNOSTIC: _orig_pil is None, attempting to reload from {self.current_path}")
             try:
                 img = Image.open(self.current_path)
                 img = ImageOps.exif_transpose(img).convert("RGBA")
                 self._orig_pil = img.copy()
                 print(f"[EditMode] ✅ DIAGNOSTIC: Successfully reloaded image, size: {self._orig_pil.size}")
             except Exception as e:
                 print(f"[EditMode] ❌ DIAGNOSTIC: Failed to reload image: {e}")
                 QMessageBox.warning(self, "Edit Error", f"Cannot enter edit mode: Image not loaded.\n\nError: {e}")
                 return
 
         # Prepare edit staging
         if self._orig_pil:
             self._edit_base_pil = self._orig_pil.copy()
             print(f"[EditMode] ✅ DIAGNOSTIC: Created _edit_base_pil copy")
         else:
             print(f"[EditMode] ❌ DIAGNOSTIC: Cannot enter edit mode - no image loaded")
             QMessageBox.warning(self, "Edit Error", "Cannot enter edit mode: No image loaded.")
             return
         self._working_pil = self._edit_base_pil.copy() if self._edit_base_pil else None
 
         # Reset adjustments values & sliders
         for k in self.adjustments.keys():
             self.adjustments[k] = 0
             slider = getattr(self, f"slider_{k}", None)
             if slider:
                 slider.blockSignals(True)
                 slider.setValue(0)
                 slider.blockSignals(False)
 
-        # Show editor view and move canvas into editor container
+        # Show editor view and move content_stack to editor page
         self.stack.setCurrentIndex(1)
-        if hasattr(self, "edit_canvas_container"):
-            container_layout = self.edit_canvas_container.layout()
-            if self.canvas.parent() is not self.edit_canvas_container:
-                container_layout.addWidget(self.canvas)
+        
+        # FIX: Reparent content_stack (not canvas) to edit page
+        if hasattr(self, "_edit_canvas_placeholder"):
+            placeholder_layout = self._edit_canvas_placeholder.layout()
+            # Remove from viewer page if it's there
+            viewer_page = self.stack.widget(0)
+            if viewer_page and self.content_stack.parent() == viewer_page:
+                viewer_layout = viewer_page.layout()
+                if viewer_layout:
+                    center_widget = viewer_layout.itemAt(1).widget()
+                    if center_widget:
+                        center_layout = center_widget.layout()
+                        if center_layout:
+                            center_layout.removeWidget(self.content_stack)
+            # Add to editor page
+            if self.content_stack.parent() != self._edit_canvas_placeholder:
+                placeholder_layout.addWidget(self.content_stack)
+                print("[EditMode] ✅ Reparented content_stack to editor page")
+        
         self.canvas.reset_view()
 
         # Make Save & Cancel visible in toolbar row (they are already created in page)
         if hasattr(self, "btn_save"):
             self.btn_save.show()
         if hasattr(self, "btn_cancel"):
             self.btn_cancel.show()
 
-        # Ensure placeholder exists but keep it collapsed (panel shown via Adjustments button)
-        if not hasattr(self, "_editor_right_placeholder"):
-            # placeholder will have been created by _build_edit_page; if not, nothing to do
-            pass
-
         # Render the working base as the initial preview
         if self._edit_base_pil:
             pm = self._pil_to_qpixmap(self._edit_base_pil)
             self.canvas.set_pixmap(pm)
             self._update_info(pm)
```

### Change 4: Fix _return_to_viewer to reparent content_stack back

**File:** `/home/user/MemoryMate-PhotoFlow/preview_panel_qt.py`  
**Lines:** 2346-2366

```diff
     def _return_to_viewer(self):
         reply = QMessageBox.question(self, "Return to viewer", "Do you want to save changes before returning?",
                                      QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
         if reply == QMessageBox.Cancel:
             return
         if reply == QMessageBox.Yes:
             QMessageBox.information(self, "Saved", "Changes saved.")
-        # remove right editor panel (optional) - keep visible but no harm
+        
+        # FIX: Reparent content_stack back to viewer page
         viewer_page = self.stack.widget(0)
         viewer_layout = viewer_page.layout()
         center_widget = viewer_layout.itemAt(1).widget() if viewer_layout.count() > 1 else None
         if center_widget:
-            canvas_row = center_widget.layout()
+            center_layout = center_widget.layout()
         else:
-            canvas_row = None
+            center_layout = None
 
-        if canvas_row and self.canvas.parent() is not center_widget:
-            canvas_row.insertWidget(0, self.canvas, 1)
+        if center_layout:
+            # Remove from editor page
+            if hasattr(self, "_edit_canvas_placeholder"):
+                edit_layout = self._edit_canvas_placeholder.layout()
+                if edit_layout:
+                    edit_layout.removeWidget(self.content_stack)
+            # Add back to viewer page
+            if self.content_stack.parent() != center_widget:
+                center_layout.insertWidget(0, self.content_stack, 1)
+                print("[EditMode] ✅ Reparented content_stack back to viewer page")
 
         self.stack.setCurrentIndex(0)
         self.canvas.reset_view()
```

---

## TESTING CHECKLIST

After applying the fix, test these scenarios:

### Basic Edit Mode
- [ ] Load a photo in viewer mode
- [ ] Click "Edit Photo" button
- [ ] **VERIFY:** Photo displays in edit mode
- [ ] **VERIFY:** Edit controls (zoom, adjustments) are visible
- [ ] Adjust brightness slider
- [ ] **VERIFY:** Photo updates in real-time
- [ ] Click "Back" to return to viewer
- [ ] **VERIFY:** Photo displays in viewer mode
- [ ] **VERIFY:** Edits are discarded

### Navigation in Edit Mode
- [ ] Load multiple photos
- [ ] Enter edit mode on photo 1
- [ ] Navigate to next photo
- [ ] **VERIFY:** Photo 2 displays correctly
- [ ] **VERIFY:** Edit state is maintained
- [ ] Navigate to previous photo
- [ ] **VERIFY:** Photo 1 displays correctly

### Video Compatibility
- [ ] Load a video in viewer mode
- [ ] **VERIFY:** Video plays correctly
- [ ] Navigate to a photo
- [ ] **VERIFY:** Photo displays correctly
- [ ] Enter edit mode
- [ ] **VERIFY:** Photo displays in edit mode
- [ ] Exit edit mode
- [ ] Navigate back to video
- [ ] **VERIFY:** Video plays correctly

### Edge Cases
- [ ] Enter edit mode with no photo loaded
- [ ] **VERIFY:** Error message displays
- [ ] Load corrupted image
- [ ] Attempt to enter edit mode
- [ ] **VERIFY:** Graceful error handling
- [ ] Rapid mode switching (viewer → edit → viewer)
- [ ] **VERIFY:** No crashes or visual glitches

---

## SUMMARY

**Total Issues Found:** 3 critical bugs

| Issue | Severity | Lines | Status |
|-------|----------|-------|--------|
| Canvas not visible in edit mode | P0 Critical | 2322-2326 | Fix provided |
| Wrong canvas placement on return | P0 Critical | 2362-2363 | Fix provided |
| Widget lifecycle violation | P1 High | 532-542, 1105-1129 | Fix provided |

**Estimated Fix Time:** 30-45 minutes  
**Testing Time:** 15-20 minutes  
**Total Time:** ~1 hour

**Risk Level:** Low (architectural improvement, no data loss risk)

---

## CONCLUSION

The root cause is a fundamental architectural flaw: attempting to share a widget between two QStackedWidget pages by reparenting it back and forth. This violates Qt's visibility model where hidden parents cause all children to be hidden.

The proper solution is to reparent the content_stack (the container) rather than the canvas itself. This way, when content_stack is moved to the editor page, it becomes visible, and the canvas (its child) inherits that visibility.

The fix is surgical, well-defined, and addresses the root cause rather than adding workarounds.

**Recommendation:** Implement the fix immediately as this is a P0 bug blocking core functionality.
