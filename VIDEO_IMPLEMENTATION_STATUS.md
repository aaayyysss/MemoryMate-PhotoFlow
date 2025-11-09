# Video Support Implementation Status

## Phase 3: Business Logic Layer ✅ COMPLETE
**Status**: Implemented in previous commits

- ✅ VideoMetadataService for extracting video metadata (duration, codecs, etc.)
- ✅ VideoThumbnailService for generating video thumbnails
- ✅ VideoService for business logic coordination
- ✅ VideoRepository for database operations
- ✅ Schema v3.2.0 with video_metadata, project_videos, video_tags tables

## Phase 4: UI Integration ⏳ IN PROGRESS

### Phase 4.1: Videos Tab in Sidebar ✅ COMPLETE
**Commit**: 5531eb7

- ✅ Added `selectVideos` signal to SidebarTabs
- ✅ Added "Videos" tab with 🎬 icon
- ✅ Implemented `_load_videos()` - loads videos via VideoService in background thread
- ✅ Implemented `_finish_videos()` - displays video list with file names
- ✅ Double-click on video emits selectVideos signal
- ✅ Shows video count and load time

### Phase 4.2: Main Window Integration ✅ COMPLETE
**Commit**: 5531eb7

- ✅ Added `on_videos_selected()` to SidebarController
- ✅ Connected `sidebar.selectVideos` signal to controller
- ✅ Wired up video selection flow

### Phase 4.3: Grid View Updates ✅ COMPLETE
**Commit**: bbf6fe2
**Status**: Implemented

**Changes Made in `thumbnail_grid_qt.py`**:

✅ **Implemented Features**:

1. **set_videos() method** - Shows all videos for current project
   - Sets navigation_mode to "videos"
   - Calls reload() to fetch and display videos

2. **Video mode in reload()** - Loads videos via VideoService
   - Added "videos" case to load video paths
   - Updates status bar with video count
   - Handles video context label ("Videos: — → N video(s) shown")

3. **Video file detection** - Helper functions added:
   - `is_video_file(path)` - Checks common video extensions
   - `format_duration(seconds)` - Formats as MM:SS or H:MM:SS

4. **Duration badge rendering** in CenteredThumbnailDelegate:
   - Detects video files automatically
   - Renders 50x20px badge in bottom-right corner
   - Black background (180 alpha), white bold text
   - Shows duration (e.g., "2:35") or 🎬 icon if unavailable

5. **Video metadata loading** in _load_paths():
   - Fetches duration from video_metadata table
   - Stores in UserRole + 3 for delegate access

6. **ReferenceDB.get_video_by_path()** - Database access:
   - Queries video_metadata table by path and project_id
   - Returns complete metadata including duration_seconds

### Phase 4.4: Video Player Panel ⏳ TODO
**Status**: Not started

**Required Changes**:

1. Create `VideoPlayerPanel` widget (new file: `video_player_qt.py`):
   - Use QMediaPlayer for playback
   - Add play/pause, seek, volume controls
   - Show video metadata (duration, resolution, codec)
   - Position: dock panel or overlay

2. Integrate in main_window_qt.py:
   - Add video player dock/panel
   - Connect double-click on video thumbnail to open player
   - Handle video playback lifecycle

## Phase 5: Background Workers ⏳ TODO
**Status**: Not started

### Phase 5.1: MetadataExtractorWorker ⏳ TODO
**Location**: `workers/video_metadata_worker.py`

**Functionality**:
- Async extraction of video metadata (duration, resolution, codecs)
- Progress reporting via signals
- Updates video_metadata table
- Cancellation support

**Integration**:
- Launch from scan operation
- Process videos with pending metadata_status
- Report progress to UI

### Phase 5.2: ThumbnailGeneratorWorker ⏳ TODO
**Location**: `workers/video_thumbnail_worker.py`

**Functionality**:
- Async generation of video thumbnails
- Extract frame at 10% duration
- Store in thumbnail cache/database
- Progress reporting
- Cancellation support

**Integration**:
- Launch from scan operation or on-demand
- Process videos with pending thumbnail_status
- Update UI when complete

### Phase 5.3: Progress Reporting ⏳ TODO
**Changes Required**:

1. Add progress signals:
   ```python
   metadata_progress = Signal(int, int)  # (current, total)
   thumbnail_progress = Signal(int, int)  # (current, total)
   ```

2. Add progress UI:
   - Progress bar in main window
   - Status text (e.g., "Extracting metadata: 45/100 videos")
   - Cancel button

3. Worker cancellation:
   - Set cancel flag
   - Workers check flag periodically
   - Clean up partial work

## Installation Requirements

### Required Packages:
```bash
# For video thumbnail generation
pip install opencv-python  # or opencv-python-headless

# For video playback (Qt Multimedia)
pip install PySide6-Multimedia

# Already installed (from Phase 3):
# - ffmpeg-python (for metadata extraction)
```

### System Dependencies:
```bash
# Ubuntu/Debian
sudo apt-get install ffmpeg libavcodec-dev libavformat-dev

# macOS
brew install ffmpeg

# Windows
# Download ffmpeg from https://ffmpeg.org/download.html
# Add to PATH
```

## Testing Checklist

### Phase 4.1-4.2 (Current):
- [x] Videos tab appears in sidebar
- [x] Videos tab loads without errors
- [x] Double-clicking video item triggers selectVideos signal
- [x] No Python syntax errors

### Phase 4.3 (Next):
- [ ] Grid displays video thumbnails
- [ ] Duration badges appear on video thumbnails
- [ ] Video thumbnails are distinct from photos
- [ ] Clicking video thumbnail selects it

### Phase 4.4:
- [ ] Video player opens on double-click
- [ ] Video plays correctly
- [ ] Controls work (play/pause, seek, volume)
- [ ] Player closes cleanly

### Phase 5:
- [ ] Metadata worker extracts duration/resolution
- [ ] Thumbnail worker generates thumbnails
- [ ] Progress bars update correctly
- [ ] Cancel buttons work
- [ ] No UI freezing during background work

## Next Steps

1. **Immediate** (Phase 4.3):
   - Implement `set_videos()` in thumbnail_grid_qt.py
   - Add duration badge rendering
   - Test video thumbnail display

2. **Short-term** (Phase 4.4):
   - Create video player panel
   - Wire up double-click to player
   - Test video playback

3. **Medium-term** (Phase 5):
   - Implement background workers
   - Add progress reporting UI
   - Test with large video collections

## Architecture Notes

**Video Flow**:
```
Scan Operation
    ↓
VideoService.index_video()
    ↓
video_metadata table (pending status)
    ↓
Background Workers:
  - MetadataExtractorWorker → update metadata
  - ThumbnailGeneratorWorker → generate thumbnails
    ↓
UI displays videos with thumbnails and duration
```

**Data Layer**:
- `video_metadata` table: stores all video metadata
- `project_videos` table: associates videos with projects/branches
- `video_tags` table: video tagging support

**Service Layer**:
- VideoMetadataService: extracts metadata via ffmpeg
- VideoThumbnailService: generates thumbnails via OpenCV
- VideoService: coordinates operations

**UI Layer**:
- Sidebar Videos tab: lists all videos
- Grid view: displays video thumbnails with badges
- Video player: playback interface

## Known Limitations

1. **No video scanning yet**: The scan operation only processes photos. Need to:
   - Update PhotoScanService to detect video files
   - Call VideoService.index_video() for videos
   - Handle mixed photo/video folders

2. **No video thumbnail caching**: Need to:
   - Store thumbnails in thumbnail cache database
   - Use same caching strategy as photos

3. **No video metadata extraction**: Background workers not implemented yet

## Files Modified

### Phase 3 (Previous):
- `repository/schema.py` - Added video tables
- `repository/video_repository.py` - Video CRUD operations
- `services/video_service.py` - Business logic
- `services/video_metadata_service.py` - Metadata extraction
- `services/video_thumbnail_service.py` - Thumbnail generation

### Phase 4.1-4.2 (Current):
- `sidebar_qt.py` - Videos tab
- `main_window_qt.py` - Controller wiring

### Phase 4.3-4.4 (TODO):
- `thumbnail_grid_qt.py` - Video display
- `video_player_qt.py` - New file for player

### Phase 5 (TODO):
- `workers/video_metadata_worker.py` - New file
- `workers/video_thumbnail_worker.py` - New file
- `main_window_qt.py` - Progress UI
