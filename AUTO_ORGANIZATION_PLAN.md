# Implementation Plan: Auto-Organization of Imported Files

## Overview

After importing files from MTP devices, automatically populate ALL sections of MemoryMate:
- ✅ **Folders**: Add "Device_Imports" with subfolders (A54 von Ammar → Camera → 2025-11-19)
- ✅ **Dates**: Parse EXIF dates, add to "By Dates" tree (2024-10, 2024-08, etc.)
- ✅ **Videos**: Detect .mp4 files, add to Videos section
- ✅ **Faces**: Queue for face detection automatically

---

## Current Problem

**After importing 27 photos from Samsung A54:**
- ✅ Files appear in "All Photos" (works!)
- ❌ Files do NOT appear in "Folders" section
- ❌ Files do NOT appear in "By Dates" section
- ❌ Videos do NOT appear in "Videos" section
- ❌ Files are NOT queued for face detection

**User has to manually scan the repository to populate these sections.**

---

## Solution: Post-Import Auto-Organization

After successful import, automatically:
1. **Parse EXIF metadata** from imported files (dates, GPS, camera info)
2. **Create folder hierarchy** in database (Device_Imports → Device Name → Folder → Date)
3. **Add to date hierarchy** based on EXIF date or file modified date
4. **Detect videos** and add to videos table
5. **Queue for face detection** (background, non-blocking)
6. **Refresh sidebar** to show new counts

---

## Architecture

### Phase 1: EXIF Parsing (NEW)

**File**: `services/exif_parser.py` (NEW)

```python
class EXIFParser:
    """Parse EXIF metadata from photos and videos"""

    def parse_image(self, file_path: str) -> dict:
        """
        Extract EXIF metadata from image file.

        Returns:
            {
                'datetime_original': datetime,  # When photo was taken
                'datetime_digitized': datetime, # When photo was scanned/imported
                'gps_latitude': float,
                'gps_longitude': float,
                'camera_make': str,
                'camera_model': str,
                'width': int,
                'height': int,
                'orientation': int,
                'flash': bool,
            }
        """
        from PIL import Image
        from PIL.ExifTags import TAGS, GPSTAGS

        # Read EXIF data
        # Convert to datetime objects
        # Return structured dict

    def parse_video(self, file_path: str) -> dict:
        """
        Extract metadata from video file.

        Returns:
            {
                'datetime_original': datetime,
                'duration': float,  # seconds
                'width': int,
                'height': int,
                'codec': str,
                'fps': float,
            }
        """
        # Use FFmpeg or mediainfo
        # Extract creation date
        # Return structured dict

    def get_capture_date(self, file_path: str) -> datetime:
        """
        Get the best available capture date for a file.

        Priority:
        1. EXIF DateTimeOriginal
        2. EXIF DateTimeDigitized
        3. File modified time
        4. File created time

        Returns:
            datetime object (never None)
        """
```

### Phase 2: Folder Organization (ENHANCE EXISTING)

**File**: `services/mtp_import_adapter.py` (MODIFY)

Add to `import_selected_files()` method after copying files:

```python
def import_selected_files(self, selected_files, mtp_path, ...):
    # ... existing copy logic ...

    # NEW: Auto-organization after successful import
    if imported_paths:
        print(f"[MTPAdapter] Auto-organizing {len(imported_paths)} imported files...")
        self._organize_imported_files(
            imported_paths=imported_paths,
            device_name=device_name,
            folder_name=folder_name,
            device_id=device_id
        )

    return imported_paths

def _organize_imported_files(self, imported_paths, device_name, folder_name, device_id):
    """
    Organize imported files into Folders, Dates, Videos, and queue for Faces.

    Steps:
    1. Parse EXIF dates from all files
    2. Create folder hierarchy in database
    3. Add files to date hierarchy
    4. Detect and register videos
    5. Queue for face detection
    """
    from services.exif_parser import EXIFParser
    from pathlib import Path
    from datetime import datetime

    parser = EXIFParser()

    # Group files by capture date
    files_by_date = {}  # {date_str: [file_paths]}
    video_files = []

    for file_path in imported_paths:
        # Check if video
        if self._is_video_file(file_path):
            video_files.append(file_path)

        # Parse capture date
        try:
            capture_date = parser.get_capture_date(file_path)
            date_key = capture_date.strftime("%Y-%m-%d")

            if date_key not in files_by_date:
                files_by_date[date_key] = []
            files_by_date[date_key].append(file_path)
        except Exception as e:
            print(f"[MTPAdapter] Error parsing date for {file_path}: {e}")
            # Fallback to "Unknown Date"
            if "unknown" not in files_by_date:
                files_by_date["unknown"] = []
            files_by_date["unknown"].append(file_path)

    # 1. Create folder hierarchy
    self._create_folder_hierarchy(device_name, folder_name, files_by_date)

    # 2. Add files to date hierarchy
    self._add_to_date_hierarchy(files_by_date)

    # 3. Register videos
    if video_files:
        self._register_videos(video_files)

    # 4. Queue for face detection
    self._queue_face_detection(imported_paths)
```

### Phase 3: Folder Hierarchy (NEW DATABASE STRUCTURE)

**Folder Structure in Database:**

```
Folders
└── Device_Imports (folder_id=1001)
    └── A54 von Ammar (folder_id=1002)
        ├── Camera (folder_id=1003)
        │   ├── 2024-10-15 (folder_id=1004) → [IMG_001.jpg, IMG_002.jpg]
        │   └── 2024-08-22 (folder_id=1005) → [IMG_003.jpg]
        └── Screenshots (folder_id=1006)
            └── 2024-11-19 (folder_id=1007) → [Screenshot_001.png]
```

**Implementation:**

```python
def _create_folder_hierarchy(self, device_name, folder_name, files_by_date):
    """
    Create folder hierarchy: Device_Imports → Device → Folder → Dates

    Args:
        device_name: "A54 von Ammar"
        folder_name: "Camera" or "Screenshots"
        files_by_date: {"2024-10-15": [paths], "2024-08-22": [paths]}
    """

    # 1. Get or create "Device_Imports" root folder
    device_imports_id = self._get_or_create_folder(
        name="Device_Imports",
        parent_id=None,
        project_id=self.project_id
    )

    # 2. Get or create device folder (e.g., "A54 von Ammar")
    device_folder_id = self._get_or_create_folder(
        name=device_name,
        parent_id=device_imports_id,
        project_id=self.project_id
    )

    # 3. Get or create source folder (e.g., "Camera")
    source_folder_id = self._get_or_create_folder(
        name=folder_name,
        parent_id=device_folder_id,
        project_id=self.project_id
    )

    # 4. Create date subfolders and add files
    for date_str, file_paths in files_by_date.items():
        date_folder_id = self._get_or_create_folder(
            name=date_str,  # "2024-10-15"
            parent_id=source_folder_id,
            project_id=self.project_id
        )

        # Add files to this date folder
        for file_path in file_paths:
            self._link_file_to_folder(
                file_path=file_path,
                folder_id=date_folder_id,
                project_id=self.project_id
            )

def _get_or_create_folder(self, name, parent_id, project_id):
    """Get existing folder or create new one"""
    # Check if folder exists
    existing = self.db.get_folder_by_name_and_parent(name, parent_id, project_id)
    if existing:
        return existing['id']

    # Create new folder
    folder_id = self.db.create_folder(
        name=name,
        parent_id=parent_id,
        project_id=project_id
    )
    return folder_id

def _link_file_to_folder(self, file_path, folder_id, project_id):
    """Link imported file to folder in database"""
    # Check if file already in folder
    existing = self.db.get_file_folder_link(file_path, folder_id, project_id)
    if existing:
        return  # Already linked

    # Create link
    self.db.add_file_to_folder(
        file_path=file_path,
        folder_id=folder_id,
        project_id=project_id
    )
```

### Phase 4: Date Hierarchy (ENHANCE EXISTING)

**Add to "By Dates" tree based on EXIF dates:**

```python
def _add_to_date_hierarchy(self, files_by_date):
    """
    Add imported files to date hierarchy.

    Args:
        files_by_date: {"2024-10-15": [paths], "2024-08-22": [paths]}
    """
    for date_str, file_paths in files_by_date.items():
        if date_str == "unknown":
            continue  # Skip unknown dates

        # Parse date
        from datetime import datetime
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        except:
            continue

        # Add each file to date hierarchy
        for file_path in file_paths:
            self.db.add_file_to_date(
                file_path=file_path,
                capture_date=date_obj,
                project_id=self.project_id
            )
```

### Phase 5: Video Detection (NEW)

**File**: `services/video_service.py` (ENHANCE)

```python
def register_imported_videos(self, video_paths, project_id):
    """
    Register imported video files.

    Extracts metadata:
    - Duration
    - Resolution (width x height)
    - Codec
    - FPS
    - File size
    """
    from services.exif_parser import EXIFParser

    parser = EXIFParser()

    for video_path in video_paths:
        # Check if already registered
        existing = self.get_video_by_path(video_path, project_id)
        if existing:
            continue

        # Parse video metadata
        metadata = parser.parse_video(video_path)

        # Add to videos table
        self.db.add_video(
            path=video_path,
            project_id=project_id,
            duration=metadata.get('duration'),
            width=metadata.get('width'),
            height=metadata.get('height'),
            codec=metadata.get('codec'),
            fps=metadata.get('fps'),
            capture_date=metadata.get('datetime_original')
        )
```

### Phase 6: Face Detection Queue (NEW)

**File**: `services/face_detection_queue.py` (NEW)

```python
class FaceDetectionQueue:
    """Background queue for face detection on imported files"""

    def __init__(self, db):
        self.db = db
        self.queue = []
        self.processing = False

    def add_files(self, file_paths, project_id):
        """Add files to face detection queue"""
        for file_path in file_paths:
            # Skip videos (face detection on photos only)
            if self._is_video(file_path):
                continue

            # Check if already detected
            if self.db.has_face_detection(file_path, project_id):
                continue

            # Add to queue
            self.queue.append({
                'file_path': file_path,
                'project_id': project_id,
                'added_at': datetime.now()
            })

        print(f"[FaceQueue] Added {len(file_paths)} files to queue")

        # Start processing if not already running
        if not self.processing:
            self._start_processing()

    def _start_processing(self):
        """Process queue in background thread"""
        import threading

        def process_queue():
            self.processing = True
            print(f"[FaceQueue] Processing {len(self.queue)} files...")

            while self.queue:
                item = self.queue.pop(0)

                try:
                    # Run face detection
                    faces = self._detect_faces(item['file_path'])

                    # Save to database
                    self.db.save_face_detection(
                        file_path=item['file_path'],
                        project_id=item['project_id'],
                        faces=faces
                    )

                    print(f"[FaceQueue] ✓ Detected {len(faces)} face(s) in {item['file_path']}")

                except Exception as e:
                    print(f"[FaceQueue] ✗ Error detecting faces: {e}")

            self.processing = False
            print(f"[FaceQueue] Queue complete")

        thread = threading.Thread(target=process_queue, daemon=True)
        thread.start()

    def _detect_faces(self, file_path):
        """Run face detection on single file"""
        # Use existing face detection logic
        from services.face_detection import detect_faces_in_image
        return detect_faces_in_image(file_path)
```

### Phase 7: Sidebar Refresh (ENHANCE EXISTING)

**File**: `sidebar_qt.py` (MODIFY)

After import completes, trigger sidebar refresh:

```python
# In _on_item_clicked(), after import success:

if imported_paths:
    print(f"[Sidebar] Import successful: {len(imported_paths)} files")

    # Load imported files into grid
    mw.grid.model.clear()
    mw.grid.load_custom_paths(imported_paths, content_type="mixed")

    # NEW: Refresh sidebar to show updated counts
    print(f"[Sidebar] Refreshing sidebar sections...")

    # Force refresh Folders tab
    self._tab_populated.discard("folders")
    self._load_folders_if_selected()

    # Force refresh Dates tab
    self._tab_populated.discard("dates")
    self._load_dates_if_selected()

    # Force refresh Videos tab (if videos were imported)
    if any(self._is_video(p) for p in imported_paths):
        # Trigger video section refresh
        pass

    # Update "All Photos" count
    self._update_all_photos_count()

    print(f"[Sidebar] ✓ Sidebar refreshed")
```

---

## Database Changes

### New Methods Needed in `ReferenceDB`

```python
class ReferenceDB:

    # Folder management
    def get_folder_by_name_and_parent(self, name, parent_id, project_id):
        """Find folder by name and parent"""

    def create_folder(self, name, parent_id, project_id):
        """Create new folder in hierarchy"""

    def add_file_to_folder(self, file_path, folder_id, project_id):
        """Link file to folder"""

    # Date management
    def add_file_to_date(self, file_path, capture_date, project_id):
        """Add file to date hierarchy"""

    # Video management
    def add_video(self, path, project_id, duration, width, height, codec, fps, capture_date):
        """Register video in videos table"""

    def get_video_by_path(self, path, project_id):
        """Check if video already registered"""

    # Face detection
    def has_face_detection(self, file_path, project_id):
        """Check if file has face detection results"""

    def save_face_detection(self, file_path, project_id, faces):
        """Save face detection results"""
```

---

## Implementation Steps

### Step 1: Create EXIF Parser ✅
1. Create `services/exif_parser.py`
2. Implement `parse_image()` using PIL/Pillow
3. Implement `parse_video()` using ffmpeg-python
4. Implement `get_capture_date()` with fallbacks
5. Test with sample files (JPEG, HEIC, MP4)

### Step 2: Database Methods ✅
1. Add folder management methods to `ReferenceDB`
2. Add date hierarchy methods
3. Add video registration methods
4. Add face detection tracking methods
5. Test with sample data

### Step 3: Folder Hierarchy ✅
1. Implement `_get_or_create_folder()`
2. Implement `_create_folder_hierarchy()`
3. Implement `_link_file_to_folder()`
4. Test folder creation
5. Verify in sidebar "Folders" tab

### Step 4: Date Organization ✅
1. Implement `_add_to_date_hierarchy()`
2. Parse EXIF dates for all imported files
3. Group by year/month/day
4. Add to database
5. Verify in sidebar "By Dates" tab

### Step 5: Video Detection ✅
1. Implement `_is_video_file()` helper
2. Implement `_register_videos()`
3. Extract video metadata
4. Add to videos table
5. Verify in sidebar "Videos" tab

### Step 6: Face Detection Queue ✅
1. Create `services/face_detection_queue.py`
2. Implement background queue
3. Implement `_queue_face_detection()`
4. Run detection in background thread
5. Verify in sidebar "People" tab

### Step 7: Integration ✅
1. Add `_organize_imported_files()` to `MTPImportAdapter`
2. Call after successful import
3. Add sidebar refresh logic
4. Test end-to-end with real device
5. Verify all sections populated

### Step 8: User Feedback ✅
1. Add progress messages during organization
2. Show success message with breakdown:
   - "Organized 27 files into 3 folders"
   - "Parsed dates for 25 files"
   - "Registered 2 videos"
   - "Queued 27 files for face detection"
3. Update status bar
4. Highlight new folders in sidebar

---

## Testing Plan

### Test Case 1: Basic Import
**Setup**: Samsung A54, Camera folder with 15 photos
**Steps**:
1. Import 15 photos from Camera
2. Check "Folders" → Should see "Device_Imports → A54 von Ammar → Camera → [dates]"
3. Check "By Dates" → Should see photos grouped by EXIF date
4. Check "All Photos" → Should see all 15 photos

**Expected**:
- ✅ Folders created automatically
- ✅ Dates parsed from EXIF
- ✅ Photos grouped by capture date
- ✅ All sections populated

### Test Case 2: Mixed Content
**Setup**: Samsung A54, Screenshots folder with 10 photos + 2 videos
**Steps**:
1. Import 12 files from Screenshots
2. Check "Folders" → Should see Screenshots subfolder
3. Check "Videos" → Should see 2 videos
4. Check "By Dates" → Should see all 12 files

**Expected**:
- ✅ Videos detected and registered
- ✅ Videos appear in Videos section
- ✅ Photos and videos both organized

### Test Case 3: No EXIF Dates
**Setup**: Screenshots without EXIF (PNG files)
**Steps**:
1. Import 5 screenshots
2. Check organization

**Expected**:
- ✅ Falls back to file modified date
- ✅ Still organized into folders
- ✅ Dates from filesystem metadata

### Test Case 4: Face Detection
**Setup**: Camera photos with faces
**Steps**:
1. Import 10 photos with people
2. Wait 30 seconds
3. Check "People" tab

**Expected**:
- ✅ Face detection queued
- ✅ Runs in background
- ✅ Faces detected and saved
- ✅ People tab populated

### Test Case 5: Duplicate Handling
**Setup**: Re-import same files
**Steps**:
1. Import Camera folder
2. Import Camera folder again
3. Check organization

**Expected**:
- ✅ Duplicates skipped (Phase 1 feature)
- ✅ Folders not duplicated
- ✅ No duplicate entries in dates

---

## Performance Considerations

### EXIF Parsing
- **Time**: ~5-10ms per photo (PIL is fast)
- **Batch**: Can parse 100 photos in ~0.5-1 second
- **Async**: Run in background thread, non-blocking

### Database Operations
- **Folder creation**: ~1-2ms per folder
- **File linking**: ~1ms per file
- **Bulk insert**: Use transactions, batch operations

### Face Detection
- **Time**: ~500ms - 2s per photo (depends on resolution)
- **Queue**: Process in background, don't block UI
- **Priority**: Process recently imported files first

### Total Time Estimate
For 27 files:
- EXIF parsing: ~0.27 seconds
- Folder creation: ~0.01 seconds
- Database inserts: ~0.03 seconds
- **Total blocking time: ~0.3 seconds** ✅ Acceptable!
- Face detection: ~13-54 seconds (background) ✅ Non-blocking!

---

## Dependencies

### Python Packages (Add to requirements.txt)
```
Pillow>=10.0.0          # EXIF parsing for images
pillow-heif>=0.13.0     # HEIC/HEIF support
ffmpeg-python>=0.2.0    # Video metadata extraction
```

### System Dependencies
- FFmpeg (for video metadata)
- Already installed on most systems
- Windows: Can bundle ffmpeg.exe
- Linux: `apt install ffmpeg`

---

## Rollout Plan

### Phase 1: Core Auto-Organization (Priority 1)
**Timeline**: 2-3 hours
- EXIF parser
- Folder hierarchy
- Date organization
- Basic integration

**Deliverable**: Imported files appear in Folders and Dates sections

### Phase 2: Video Support (Priority 2)
**Timeline**: 1-2 hours
- Video detection
- Metadata extraction
- Videos section integration

**Deliverable**: Videos automatically registered and organized

### Phase 3: Face Detection (Priority 3)
**Timeline**: 1-2 hours
- Face detection queue
- Background processing
- People section integration

**Deliverable**: Faces detected automatically in background

### Phase 4: Polish & Testing (Priority 4)
**Timeline**: 1-2 hours
- User feedback messages
- Progress indicators
- Error handling
- End-to-end testing

**Deliverable**: Production-ready feature

**Total Timeline**: 5-9 hours of development

---

## Success Criteria

✅ **Auto-organization is successful if:**

1. **Folders Section Populated**
   - "Device_Imports" folder created
   - Device subfolders created (e.g., "A54 von Ammar")
   - Source subfolders created (e.g., "Camera", "Screenshots")
   - Date subfolders created (e.g., "2024-10-15")
   - Files linked to appropriate folders

2. **Dates Section Populated**
   - EXIF dates parsed successfully
   - Files grouped by capture date
   - Year/Month/Day hierarchy populated
   - Fallback to file dates for files without EXIF

3. **Videos Section Populated**
   - Videos detected automatically
   - Metadata extracted (duration, resolution, codec)
   - Videos table populated
   - Videos appear in Videos section

4. **Faces Section Populated**
   - Files queued for face detection
   - Detection runs in background
   - Faces detected and saved
   - People tab shows detected faces

5. **User Experience**
   - No manual scanning required
   - Fast response (<1 second blocking)
   - Clear feedback messages
   - Sidebar refreshes automatically

6. **No Regressions**
   - Existing import functionality works
   - Duplicate detection still works
   - Performance acceptable
   - No crashes or errors

---

## Next Steps

After creating this plan:
1. Get user approval on approach
2. Implement Phase 1 (Core Auto-Organization)
3. Test with Samsung A54
4. Iterate based on feedback
5. Implement remaining phases

**Ready to start implementation!** 🚀
