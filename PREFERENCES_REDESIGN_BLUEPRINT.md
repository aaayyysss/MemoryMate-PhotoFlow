# Preferences Dialog Redesign Blueprint
## Professional Left Sidebar Navigation (Option C)

---

## Current Issues
- ❌ Dialog too long vertically
- ❌ OK/Cancel buttons hidden beneath window
- ❌ No scrolling support
- ❌ Difficult to navigate through many settings

---

## New Design: Left Sidebar Navigation

### Visual Layout

```
┌─────────────────────────────────────────────────────────────┐
│  Preferences                              [Save] [Cancel]    │
├──────────┬──────────────────────────────────────────────────┤
│          │                                                   │
│ General  │  General Settings                                │
│          │  ─────────────────────────────────────────────   │
│ Face     │                                                   │
│ Detection│  ☐ Auto-refresh thumbnails                       │
│          │  Refresh interval: [____5____] seconds           │
│ Video    │                                                   │
│ Settings │  Default view mode:                              │
│          │  ○ Grid  ⦿ List  ○ Timeline                     │
│ FFmpeg   │                                                   │
│          │  Theme:                                           │
│ Advanced │  [Light     ▼]                                   │
│          │                                                   │
│ About    │  Startup:                                        │
│          │  ☑ Restore last project                          │
│          │  ☐ Show welcome screen                           │
│          │                                                   │
│          │  ─────────────────────────────────────────────   │
│          │                                                   │
└──────────┴──────────────────────────────────────────────────┘
```

---

## Implementation Structure

### File Location
`main_window_qt.py` - Line 875 (PreferencesDialog class)

### New Class Structure

```python
class PreferencesDialog(QDialog):
    """
    Modern preferences dialog with left sidebar navigation.

    Features:
    - Left sidebar for section navigation
    - Scrollable content area
    - Save/Cancel buttons at top-right
    - Responsive layout
    - Translation-ready
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumSize(900, 600)  # Reasonable minimum

        # Main layout
        main_layout = QHBoxLayout(self)

        # Left sidebar
        sidebar = self._create_sidebar()
        main_layout.addWidget(sidebar)

        # Right content area
        content_area = self._create_content_area()
        main_layout.addLayout(content_area)

        # Load current settings
        self._load_settings()

    def _create_sidebar(self):
        """Create left navigation sidebar."""
        sidebar = QFrame()
        sidebar.setFrameShape(QFrame.StyledPanel)
        sidebar.setMaximumWidth(180)
        sidebar.setStyleSheet("""
            QFrame {
                background-color: #f5f5f5;
                border-right: 1px solid #ddd;
            }
            QPushButton {
                text-align: left;
                padding: 10px 15px;
                border: none;
                background: transparent;
            }
            QPushButton:hover {
                background-color: #e0e0e0;
            }
            QPushButton:checked {
                background-color: #d0d0d0;
                font-weight: bold;
                border-left: 3px solid #007bff;
            }
        """)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Navigation buttons (checkable for selection highlight)
        self.nav_buttons = {}
        sections = [
            ("general", "⚙️ General"),
            ("face", "👤 Face Detection"),
            ("video", "🎬 Video Settings"),
            ("ffmpeg", "🎞️ FFmpeg"),
            ("advanced", "🔧 Advanced"),
            ("about", "ℹ️ About")
        ]

        for key, label in sections:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, k=key: self._switch_section(k))
            layout.addWidget(btn)
            self.nav_buttons[key] = btn

        # Select first section by default
        self.nav_buttons["general"].setChecked(True)

        layout.addStretch()
        return sidebar

    def _create_content_area(self):
        """Create right content area with scroll support."""
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(20, 10, 20, 20)

        # Top bar with title and buttons
        top_bar = QHBoxLayout()

        self.section_title = QLabel("General Settings")
        self.section_title.setStyleSheet("font-size: 18px; font-weight: bold;")
        top_bar.addWidget(self.section_title)

        top_bar.addStretch()

        # Save and Cancel buttons (top-right)
        btn_save = QPushButton("💾 Save")
        btn_save.clicked.connect(self.accept)
        btn_save.setStyleSheet("padding: 5px 15px;")

        btn_cancel = QPushButton("❌ Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_cancel.setStyleSheet("padding: 5px 15px;")

        top_bar.addWidget(btn_save)
        top_bar.addWidget(btn_cancel)

        content_layout.addLayout(top_bar)

        # Add separator line
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        content_layout.addWidget(line)

        # Scrollable content area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        # Stack widget for switching between sections
        self.content_stack = QStackedWidget()

        # Create section widgets
        self.content_stack.addWidget(self._create_general_section())
        self.content_stack.addWidget(self._create_face_section())
        self.content_stack.addWidget(self._create_video_section())
        self.content_stack.addWidget(self._create_ffmpeg_section())
        self.content_stack.addWidget(self._create_advanced_section())
        self.content_stack.addWidget(self._create_about_section())

        scroll.setWidget(self.content_stack)
        content_layout.addWidget(scroll)

        return content_layout

    def _switch_section(self, section_key):
        """Switch to selected section."""
        # Update button states
        for key, btn in self.nav_buttons.items():
            btn.setChecked(key == section_key)

        # Update content
        section_index = {
            "general": 0,
            "face": 1,
            "video": 2,
            "ffmpeg": 3,
            "advanced": 4,
            "about": 5
        }

        self.content_stack.setCurrentIndex(section_index[section_key])

        # Update title
        titles = {
            "general": "General Settings",
            "face": "Face Detection Settings",
            "video": "Video Settings",
            "ffmpeg": "FFmpeg Configuration",
            "advanced": "Advanced Settings",
            "about": "About MemoryMate PhotoFlow"
        }
        self.section_title.setText(titles[section_key])
```

---

## Section Content Structure

### Each section should follow this pattern:

```python
def _create_general_section(self):
    """Create General settings section."""
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setAlignment(Qt.AlignTop)

    # Group 1: Interface
    group = QGroupBox("Interface")
    group_layout = QFormLayout()

    # Setting 1
    self.auto_refresh = QCheckBox("Auto-refresh thumbnails")
    group_layout.addRow("", self.auto_refresh)

    # Setting 2
    self.refresh_interval = QSpinBox()
    self.refresh_interval.setRange(1, 60)
    group_layout.addRow("Refresh interval (seconds):", self.refresh_interval)

    # Setting 3
    self.default_view = QComboBox()
    self.default_view.addItems(["Grid", "List", "Timeline"])
    group_layout.addRow("Default view mode:", self.default_view)

    group.setLayout(group_layout)
    layout.addWidget(group)

    # Group 2: Startup
    group2 = QGroupBox("Startup")
    group2_layout = QVBoxLayout()

    self.restore_project = QCheckBox("Restore last project on startup")
    group2_layout.addWidget(self.restore_project)

    self.show_welcome = QCheckBox("Show welcome screen")
    group2_layout.addWidget(self.show_welcome)

    group2.setLayout(group2_layout)
    layout.addWidget(group2)

    layout.addStretch()
    return widget
```

---

## Migration Strategy

### Phase 1: Create New Dialog Class
1. Create `PreferencesDialogV2` in `ui/preferences_dialog.py`
2. Implement with new structure
3. Test alongside old dialog

### Phase 2: Move Settings
1. General → Basic app settings
2. Face Detection → Model paths, clustering params
3. Video → Filtering, display options
4. FFmpeg → Path configuration, auto-detect
5. Advanced → Performance, cache, debug

### Phase 3: Replace Old Dialog
1. Update `main_window_qt.py:3629` (`_open_preferences()`)
2. Switch from `PreferencesDialog` to `PreferencesDialogV2`
3. Remove old dialog code

---

## Benefits

✅ **Always Visible Buttons**: Save/Cancel at top-right, never hidden
✅ **Scalable**: Easy to add new sections
✅ **Scrollable**: Each section scrolls independently
✅ **Professional**: Clean, modern appearance
✅ **Organized**: Logical grouping of settings
✅ **Responsive**: Minimum size ensures usability
✅ **Translation-Ready**: All strings can be externalized

---

## Estimated Implementation Time

- Basic structure: 2-3 hours
- All sections: 4-6 hours
- Testing & polish: 2 hours
- **Total: 8-11 hours**

---

## Next Steps

1. Create `ui/preferences_dialog.py`
2. Implement `PreferencesDialogV2` class
3. Port settings from old dialog section by section
4. Add translation support
5. Test thoroughly
6. Replace old dialog in main window

---

## Translation Integration

Once translation system is in place, update all labels:

```python
from utils.translation_manager import get_translator

t = get_translator()

# Instead of:
QPushButton("Save")

# Use:
QPushButton(t.get('preferences.save'))
```

This ensures the preferences dialog is fully internationalized.
