# Video Infrastructure Design

**Author**: Claude AI Assistant
**Date**: 2025-11-09
**Version**: 1.0.0

---

## Executive Summary

This document outlines the complete architecture for video support in MemoryMate-PhotoFlow, designed to mirror industry leaders (Google Photos, Apple Photos, Microsoft Photos) while maintaining clean separation from photo handling.

### Key Principles

1. **Complete Separation**: Videos have their own repository, services, workers, and UI sections
2. **Industry-Standard Practices**: Frame extraction for thumbnails, metadata parsing, smooth playback
3. **Performance First**: Async processing, caching, lazy loading
4. **Crash Prevention**: Defensive coding, proper thread handling, resource cleanup

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     PRESENTATION LAYER                       │
├───────────────┬──────────────────┬──────────────────────────┤
│  List View    │   Tabs View      │    Grid View             │
│  📹 Videos    │   📹 Videos      │    Video Thumbnails      │
│  (sidebar)    │   (sidebar)      │    (main panel)          │
└───────────────┴──────────────────┴──────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│                      SERVICE LAYER                           │
├───────────────┬──────────────────┬──────────────────────────┤
│ VideoService  │ VideoThumbnail   │  VideoMetadata          │
│               │ Service          │  Service                │
└───────────────┴──────────────────┴──────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│                    REPOSITORY LAYER                          │
├───────────────┬──────────────────┬──────────────────────────┤
│ VideoRepo     │ VideoFolderRepo  │  VideoTagRepo           │
└───────────────┴──────────────────┴──────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│                      DATABASE LAYER                          │
│  video_metadata │ video_folders │ video_tags │ project_videos│
└─────────────────────────────────────────────────────────────┘
```

---

## Database Schema

### New Tables

#### `video_metadata`
```sql
CREATE TABLE video_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    folder_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,

    -- File metadata
    size_kb REAL,
    modified TEXT,
    duration_seconds REAL,

    -- Video specs
    width INTEGER,
    height INTEGER,
    fps REAL,
    codec TEXT,
    bitrate INTEGER,

    -- Timestamps
    date_taken TEXT,
    created_ts INTEGER,
    created_date TEXT,
    created_year INTEGER,
    updated_at TEXT,

    -- Processing status
    metadata_status TEXT DEFAULT 'pending',
    metadata_fail_count INTEGER DEFAULT 0,
    thumbnail_status TEXT DEFAULT 'pending',

    FOREIGN KEY (folder_id) REFERENCES photo_folders(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(path, project_id)
);

CREATE INDEX idx_video_metadata_project ON video_metadata(project_id);
CREATE INDEX idx_video_metadata_folder ON video_metadata(folder_id);
CREATE INDEX idx_video_metadata_date ON video_metadata(date_taken);
CREATE INDEX idx_video_metadata_year ON video_metadata(created_year);
```

#### `project_videos` (mirrors `project_images`)
```sql
CREATE TABLE project_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    branch_key TEXT,
    video_path TEXT NOT NULL,
    label TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, branch_key, video_path)
);

CREATE INDEX idx_project_videos_project ON project_videos(project_id);
CREATE INDEX idx_project_videos_branch ON project_videos(project_id, branch_key);
```

#### `video_tags` (mirrors `photo_tags`)
```sql
CREATE TABLE video_tags (
    video_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY (video_id, tag_id),
    FOREIGN KEY (video_id) REFERENCES video_metadata(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

CREATE INDEX idx_video_tags_video ON video_tags(video_id);
CREATE INDEX idx_video_tags_tag ON video_tags(tag_id);
```

---

## Component Design

### 1. VideoRepository (`repository/video_repository.py`)

**Purpose**: Database access layer for videos

**Key Methods**:
```python
class VideoRepository(BaseRepository):
    def get_by_path(project_id, path) -> Optional[Dict]
    def get_by_folder(folder_id, project_id) -> List[Dict]
    def get_by_project(project_id) -> List[Dict]
    def upsert(path, folder_id, project_id, **metadata) -> int
    def bulk_upsert(rows, project_id) -> int
    def update_metadata(video_id, duration, width, height, codec, ...)
    def get_unprocessed_videos(limit=100) -> List[Dict]
```

**Crash Prevention**:
- Path normalization (lowercase on Windows)
- Transaction handling with rollback
- Proper foreign key constraints

---

### 2. VideoService (`services/video_service.py`)

**Purpose**: Business logic layer for video operations

**Key Methods**:
```python
class VideoService:
    def add_video(path, project_id) -> int
    def get_videos_by_folder(folder_id, project_id) -> List[str]
    def get_videos_by_branch(branch_key, project_id) -> List[str]
    def get_videos_by_tag(tag_name, project_id) -> List[str]
    def tag_video(path, tag_name, project_id) -> bool
    def untag_video(path, tag_name, project_id) -> bool
```

---

### 3. VideoMetadataService (`services/video_metadata_service.py`)

**Purpose**: Extract video metadata using ffmpeg/ffprobe

**Key Methods**:
```python
class VideoMetadataService:
    def extract_metadata(path) -> VideoMetadata
    def get_duration(path) -> float
    def get_dimensions(path) -> Tuple[int, int]
    def get_codec_info(path) -> Dict
    def extract_creation_date(path) -> Optional[str]
```

**Technology Stack**:
- **Primary**: `ffprobe` (from ffmpeg suite)
- **Fallback**: `opencv-python` for basic info
- **Metadata**: Parse QuickTime atoms, MP4 metadata

**Example ffprobe Usage**:
```python
import subprocess
import json

def extract_metadata(path):
    cmd = [
        'ffprobe',
        '-v', 'quiet',
        '-print_format', 'json',
        '-show_format',
        '-show_streams',
        path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    data = json.loads(result.stdout)
    return {
        'duration': float(data['format']['duration']),
        'width': data['streams'][0]['width'],
        'height': data['streams'][0]['height'],
        'codec': data['streams'][0]['codec_name'],
        'fps': eval(data['streams'][0]['r_frame_rate']),
        'bitrate': int(data['format']['bit_rate'])
    }
```

---

### 4. VideoThumbnailService (`services/video_thumbnail_service.py`)

**Purpose**: Generate and cache video thumbnails

**Approach (Industry Standard)**:
```
1. Extract frame at 10% of duration (skip intro/black frames)
2. Resize to thumbnail size (e.g., 300x300)
3. Cache in database (same as photo thumbnails)
4. Use placeholder icon while generating
```

**Implementation**:
```python
import subprocess
from PIL import Image
import io

def generate_thumbnail(video_path, output_size=300):
    """Extract frame at 10% duration, resize to thumbnail"""

    # Get video duration
    duration = get_duration(video_path)
    timestamp = duration * 0.1  # 10% into video

    # Extract frame using ffmpeg
    cmd = [
        'ffmpeg',
        '-ss', str(timestamp),  # Seek to timestamp
        '-i', video_path,
        '-vframes', '1',  # Extract 1 frame
        '-f', 'image2pipe',  # Output to pipe
        '-vcodec', 'png',
        'pipe:1'
    ]

    result = subprocess.run(cmd, capture_output=True, timeout=10)

    # Resize with PIL
    img = Image.open(io.BytesIO(result.stdout))
    img.thumbnail((output_size, output_size), Image.Resampling.LANCZOS)

    # Return as bytes for caching
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    return buffer.getvalue()
```

**Fallback Strategy**:
1. Try ffmpeg extraction
2. If fails, use OpenCV
3. If fails, show video icon placeholder

---

### 5. VideoScanService (`services/video_scan_service.py`)

**Purpose**: Scan directories for videos, index metadata

**Workflow**:
```
1. Walk directory tree
2. Filter by video extensions (.mp4, .mov, .avi, etc.)
3. For each video:
   a. Create video_metadata entry
   b. Add to project_videos (all branch)
   c. Extract metadata (async worker)
   d. Generate thumbnail (async worker)
4. Emit progress signals for UI updates
```

**Key Features**:
- Incremental scanning (skip unchanged files)
- Batch processing (200 videos at a time)
- Cancellation support
- Progress reporting

---

### 6. VideoWorker (`workers/video_worker.py`)

**Purpose**: Background processing for videos

**Worker Types**:

#### MetadataExtractorWorker
```python
class MetadataExtractorWorker(QRunnable):
    """Extract video metadata in background thread"""

    def run(self):
        for video_path in self.queue:
            try:
                metadata = extract_metadata(video_path)
                video_repo.update_metadata(video_id, **metadata)
                self.progress.emit(video_path, 'success')
            except Exception as e:
                self.error.emit(video_path, str(e))
```

#### ThumbnailGeneratorWorker
```python
class ThumbnailGeneratorWorker(QRunnable):
    """Generate video thumbnails in background thread"""

    def run(self):
        for video_path in self.queue:
            try:
                thumbnail = generate_thumbnail(video_path)
                cache_service.store(video_path, thumbnail)
                self.progress.emit(video_path, 'success')
            except Exception as e:
                self.error.emit(video_path, str(e))
```

---

## UI Integration

### Sidebar List View

```
📁 Folders (298)
📅 By Date (298)
🏷️  Tags (0)
📹 Videos (45)  ← NEW SECTION
  └─ By Duration
      ├─ < 1 min (12)
      ├─ 1-5 min (25)
      └─ > 5 min (8)
  └─ By Resolution
      ├─ 4K (5)
      ├─ 1080p (30)
      └─ 720p (10)
```

### Sidebar Tabs View

```
[All] [Folders] [Dates] [Tags] [Videos] [Quick]
                                   ↑
                              NEW TAB
```

Videos tab shows:
- All videos in project
- Grouped by folder
- Video duration badges
- Resolution indicators
- Format icons (MP4, MOV, AVI)

### Grid View

```
┌─────────┬─────────┬─────────┐
│  📹 5:23│  📹 2:15│  📹 1:45│
│  [thumb]│  [thumb]│  [thumb]│
│  Video1 │  Video2 │  Video3 │
│  4K·MP4 │ 1080p·  │ 720p·   │
└─────────┴─────────┴─────────┘
```

Features:
- Duration badge in corner
- Resolution/format labels
- Play icon overlay
- Scrubbing support (hover to preview different timestamps)

---

## Performance Optimizations

### 1. Lazy Metadata Extraction

```python
# Don't extract metadata during scan (too slow)
# Queue videos for background processing

def scan_videos(folder):
    for video in find_videos(folder):
        # Quick: just create entry
        video_repo.create(path, size, modified)

        # Queue for background:
        metadata_queue.put(video.path)
        thumbnail_queue.put(video.path)
```

### 2. Thumbnail Caching

```sql
-- Reuse existing thumbnail_cache table
INSERT INTO thumbnail_cache (file_path, thumbnail_data, size, created_at)
VALUES (?, ?, 300, CURRENT_TIMESTAMP)
```

### 3. Progress Streaming

```python
# Emit progress every N videos
for i, video in enumerate(videos):
    process(video)
    if i % 10 == 0:
        progress_signal.emit(i, total)
```

---

## Crash Prevention Checklist

### Memory Leaks
- ✅ Close ffmpeg subprocesses properly
- ✅ Release PIL Image objects
- ✅ Clear thumbnail cache periodically
- ✅ Use context managers for file handles

### Thread Safety
- ✅ Never update Qt UI from worker threads
- ✅ Use QTimer.singleShot for UI updates
- ✅ Check generation numbers for stale workers
- ✅ Check model identity before updating

### Resource Limits
- ✅ Limit concurrent workers (max 4)
- ✅ Timeout ffmpeg operations (10 seconds)
- ✅ Skip videos > 2GB for metadata extraction
- ✅ Clear worker queues on cancellation

### Error Handling
- ✅ Try/except around all ffmpeg calls
- ✅ Fallback to OpenCV if ffmpeg fails
- ✅ Show placeholder if thumbnail fails
- ✅ Log errors without crashing

---

## Testing Strategy

### Unit Tests
```python
def test_video_metadata_extraction():
    """Test ffprobe metadata parsing"""

def test_thumbnail_generation():
    """Test frame extraction and resizing"""

def test_video_repository():
    """Test CRUD operations"""
```

### Integration Tests
```python
def test_video_scan():
    """Test scanning directory with mixed media"""

def test_video_tagging():
    """Test tagging videos across projects"""

def test_video_filtering():
    """Test filtering by duration, resolution"""
```

### Performance Tests
```python
def test_scan_1000_videos():
    """Ensure scan completes in reasonable time"""

def test_thumbnail_generation_batch():
    """Test generating 100 thumbnails"""
```

---

## Migration Path

### Phase 1: Schema (Immediate)
- Add video tables to schema.py
- Create migration script
- Test with existing database

### Phase 2: Repository (Week 1)
- Implement VideoRepository
- Add tests
- Integrate with existing code

### Phase 3: Services (Week 2)
- VideoService, VideoMetadataService
- VideoThumbnailService
- Add tests

### Phase 4: UI (Week 3)
- Add Videos section to sidebar
- Update grid view for video display
- Video player panel

### Phase 5: Workers (Week 4)
- Background metadata extraction
- Background thumbnail generation
- Progress reporting

---

## Dependencies

### Required
- `ffmpeg` / `ffprobe` (system install)
- `Pillow` (already installed)

### Optional
- `opencv-python` (fallback for metadata)
- `pymediainfo` (alternative to ffprobe)

### Installation
```bash
# Windows (Chocolatey)
choco install ffmpeg

# macOS (Homebrew)
brew install ffmpeg

# Linux (apt)
sudo apt install ffmpeg
```

---

## Conclusion

This design provides a complete, industry-standard video infrastructure that:

1. **Separates Concerns**: Videos have their own layers, not mixed with photos
2. **Scales Well**: Async processing, caching, lazy loading
3. **Prevents Crashes**: Defensive coding, proper thread handling, resource cleanup
4. **Matches Industry**: Similar to Google Photos, Apple Photos, Microsoft Photos

Implementation can proceed incrementally, with each phase tested independently.
